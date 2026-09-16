"""Offline tests for reviewed, bounded, revision-checked proposal submission."""
from copy import deepcopy
from datetime import datetime, timedelta
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
from pathlib import Path
import threading

import pytest

from quant.core import io_json, paths
from quant.core.agent_contracts import (CONTRACT_VERSION, RESULT_SCHEMA_VERSION,
                                        canonical_hash, card_revision)
from quant.ops import agent_submit as submit
from quant.ops.agent_snapshots import make_snapshot

NOW = datetime.fromisoformat("2026-09-05T04:30:00+00:00")


def bind_review(record):
    reviewed = deepcopy(record["result"])
    reviewed.update(proposal=None, review={"run_id": record["run_id"], "verdict": "approve",
                                          "reasons": ["Synthetic structure independently checked."]})
    record["review"] = {"run_id": "review-2", "role": "technical", "status": "succeeded", "provider": "codex",
                        "result": reviewed, "snapshot": deepcopy(record["snapshot"]),
                        "policy_hash": record["policy_hash"],
                        "reviewed_result_hash": canonical_hash(record["result"])}
    return record


def rebind_snapshot(record):
    snap = record["snapshot"]
    snap["input_hash"] = canonical_hash(snap["data"])
    record["result"]["input_hash"] = snap["input_hash"]
    return bind_review(record)


@pytest.fixture
def record(monkeypatch):
    monkeypatch.setattr(submit, "_now", lambda: NOW)
    card = {"instrument": {"type": "stock"}, "broker": None, "status": "watching",
            "agent": {"thesis": {"summary": "Synthetic thesis", "status": "intact", "conviction": "medium",
                                  "last_reviewed": "2026-09-04T21:00:00Z"},
                      "action": {"intent": "wait_for_zone", "reason": "synthetic"},
                      "current_regime": {"name": "R_hold", "cap_pct_nav": 0.4,
                                         "participation": "zone_eligible", "source": "auto",
                                         "since": "2026-09-04"},
                      "zones": {"accumulation_zone_1": [100, 105], "accumulation_zone_2": [95, 99],
                                "trim_zone": [120, 125], "no_chase_above": 110, "stop_level": 90,
                                "anchored_on": {"date": "2026-09-04", "basis": "prior evidence"}},
                      "next_regime": {"name": "R_next", "cap_pct_nav": 0.4,
                                      "entry_gate": {"level": 115, "days": 2, "volume_ratio_min": 1.2},
                                      "zones": {"accumulation_zone_1": [115, 118], "stop_level": 110}}}}
    data = {"ticker": "SYN", "bucket": "watchlist", "card": card, "card_revision": card_revision(card),
            "completed_bars": [{"time": "2026-09-04", "open": 106, "high": 111, "low": 105,
                                "close": 108, "volume": 1000}],
            "latest_completed_session": "2026-09-04", "level_map": {}, "technical": {},
            "macro": None, "valuation": None, "capture_warnings": ["macro_prior_unavailable"]}
    snapshot = make_snapshot(data, now=NOW)
    args = ["--zone1", "100", "105", "--zone2", "95", "99", "--trim", "120", "125",
            "--no-chase-above", "110", "--stop-level", "90", "--anchored-on", "2026-09-04",
            "--anchor-basis", "Synthetic pivots and supply cluster", "--reasoning-summary", "Reviewed synthetic map",
            "--valid-until", "2026-09-10"]
    result = {"schema_version": RESULT_SCHEMA_VERSION, "contract_version": CONTRACT_VERSION,
              "role": "technical", "snapshot_id": snapshot["snapshot_id"], "input_hash": snapshot["input_hash"],
              "generated_at": snapshot["as_of"], "expires_at": snapshot["expires_at"],
              "summary": "Synthetic proposed structure", "observations": [
                  {"id": "obs-1", "kind": "fact", "text": "Cached completed close is 108.",
                   "evidence_refs": ["completed_bars"]}],
              "hypotheses": [], "risks": [], "unknowns": ["Macro unavailable"],
              "proposal": {"kind": "zone_update", "zone_update_args": args}, "requests": [], "review": None,
              "authority": {"mode": "proposal_only", "may_write_state": False, "may_place_orders": False}}
    return bind_review({"run_id": "proposal-1", "role": "technical", "status": "needs_review", "provider": "claude",
                        "result": result, "snapshot": snapshot, "policy_hash": "f" * 64})


@pytest.fixture
def writer(record, monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("submit_test_zone_writer", paths.ZONE_WRITER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    pos, wl = tmp_path / "positions.json", tmp_path / "watchlist.json"
    pos.write_text(json.dumps({"tickers": {}}))
    wl.write_text(json.dumps({"tickers": {"SYN": deepcopy(record["snapshot"]["data"]["card"])}}))
    monkeypatch.setattr(mod, "POSITIONS_PATH", pos)
    monkeypatch.setattr(mod, "WATCHLIST_PATH", wl)
    monkeypatch.setattr(mod, "KLINE_CACHE_DIR", tmp_path / "klines")
    monkeypatch.setattr(mod, "VALUATION_INPUTS_PATH", tmp_path / "inputs.json")
    monkeypatch.setattr(io_json, "STATE_LOCK_PATH", tmp_path / ".state.lock")
    monkeypatch.setattr(paths, "ENV_PATH", tmp_path / "absent.env")
    monkeypatch.setattr(submit, "_load_writer", lambda: mod)
    return mod


def test_preview_never_imports_writer_or_reads_live(record, monkeypatch):
    # Warm the installed calendar dependency before denying file I/O; loading
    # its package metadata is distinct from reading live trading data.
    from quant.core.market_sessions import session_close
    session_close(NOW.date())
    def forbidden(*args, **kwargs):
        pytest.fail("dry run attempted I/O or writer import")
    monkeypatch.setattr(submit, "_load_writer", forbidden)
    monkeypatch.setattr(Path, "open", forbidden)
    before = deepcopy(record)
    receipt = submit.apply_proposal(record)
    assert receipt["status"] == "dry_run" and not receipt["applied"]
    assert receipt["writer_args"][:6] == ["--ticker", "SYN", "--bucket", "watchlist",
                                          "--expected-revision", record["snapshot"]["data"]["card_revision"]]
    assert receipt["review_run_id"] == "review-2"
    assert receipt["can_apply"] is True and receipt["blocked_reasons"] == []
    assert record == before


def test_unreviewed_apply_requires_explicit_host_opt_out(record, writer):
    record["review"] = None
    assert submit.apply_proposal(record)["review_required"] is True
    assert submit.apply_proposal(record)["can_apply"] is False
    assert submit.apply_proposal(record, require_review=False)["review_required"] is False
    with pytest.raises(submit.SubmitError, match="requires independent review"):
        submit.apply_proposal(record, apply=True)
    receipt = submit.apply_proposal(record, apply=True, require_review=False)
    assert receipt["applied"] and receipt["review_run_id"] is None
    assert receipt["prepared"]["review_policy"] == "not_required_by_host"
    target = json.loads(writer.WATCHLIST_PATH.read_text())["tickers"]["SYN"]
    assert receipt["after_revision"] == card_revision(target)


@pytest.mark.parametrize("failure", ["revision", "expired", "protected_flag", "stop_relaxation"])
def test_no_review_retains_write_guards(record, writer, monkeypatch, failure):
    record["review"] = None
    if failure == "revision":
        # 并发写必须是 writer 自己的地盘 (agent 块) —— broker 报价刷新不再算冲突,
        # 见 agent_contracts.card_revision。
        doc = json.loads(writer.WATCHLIST_PATH.read_text())
        doc["tickers"]["SYN"]["agent"]["key_risk"] = "concurrent redraw"
        writer.WATCHLIST_PATH.write_text(json.dumps(doc))
    elif failure == "expired":
        monkeypatch.setattr(submit, "_now", lambda: NOW + timedelta(hours=2))
    elif failure == "protected_flag":
        record["result"]["proposal"]["zone_update_args"] += ["--ticker", "OTHER"]
    else:
        record["result"]["proposal"]["zone_update_args"] += ["--stop-level-days", "99"]
    before = writer.WATCHLIST_PATH.read_bytes()
    with pytest.raises(submit.SubmitError):
        submit.apply_proposal(record, apply=True, require_review=False)
    assert writer.WATCHLIST_PATH.read_bytes() == before


@pytest.mark.parametrize("extra", [
    ["--ticker", "OTHER"], ["--bucket", "current"], ["--expected-revision", "f" * 64],
    ["--remove-from-other"], ["--role", "core_theme"], ["--instrument-type", "option"],
    ["--status", "closed"], ["--shell", "bash"], ["--output", "/tmp/state.json"],
    ["--print-json"], ["--zone", "1", "2"], ["--zone1=1", "2"], ["--"],
    ["--zone1", "1", "2"], ["positional"], ["--stop-level", "nan"],
    ["--methodology", "unreviewed"], ["--vehicle", "arbitrary"],
])
def test_worker_cannot_supply_identity_effects_or_ambiguous_args(record, extra):
    record["result"]["proposal"]["zone_update_args"] += extra
    with pytest.raises(submit.SubmitError):
        submit.apply_proposal(record)


@pytest.mark.parametrize("mutate", [
    lambda n: n.update(broker={"qty": 50}),
    lambda n: n.update(source="auto"),
    lambda n: n.update(current_regime={"cap_pct_nav": 1}),
    lambda n: n.update(cap_pct_nav=0.05),
    lambda n: n.update(cap_pct_nav=True),
    lambda n: n["zones"].update(lots=[]),
    lambda n: n["zones"].update(timeframe={"not": "text"}),
    lambda n: n["entry_gate"].update(command="anything"),
])
def test_nested_next_regime_cannot_smuggle_protected_state(record, mutate):
    nr = deepcopy(record["snapshot"]["data"]["card"]["agent"]["next_regime"])
    mutate(nr)
    record["result"]["proposal"]["zone_update_args"] += ["--next-regime", json.dumps(nr)]
    with pytest.raises(submit.SubmitError):
        submit.apply_proposal(record)


@pytest.mark.parametrize("optional", [{"cap_pct_nav": 0.4}, {"cap_pct_nav": 0},
                                     {"cap_pct_nav": 0.4, "zones": None}])
def test_future_gate_skeleton_applies_without_changing_current_authority(record, writer, optional):
    before = deepcopy(record["snapshot"]["data"]["card"]["agent"])
    future = {"name": "pending_after_repair", "entry_gate": {
        "level": 130, "days": 2, "volume_ratio_min": 1.2}, **optional}
    record["result"]["proposal"]["zone_update_args"] += ["--next-regime", json.dumps(future)]
    bind_review(record)
    receipt = submit.apply_proposal(record, apply=True)
    assert receipt["status"] == "applied"
    after = json.loads(writer.WATCHLIST_PATH.read_text())["tickers"]["SYN"]["agent"]
    assert after["current_regime"] == before["current_regime"]
    assert after["zones"]["stop_level"] == before["zones"]["stop_level"]
    assert after["next_regime"] == future


@pytest.mark.parametrize("optional", [{}, {"cap_pct_nav": None}, {"cap_pct_nav": None, "zones": None}])
def test_future_gate_skeleton_without_a_cap_is_rejected(record, writer, optional):
    """带 gate 却不说授予多少 = 写卡期沉默, 转移期全局失败。

    `next_regime.cap_pct_nav: null` 会在**转移发生的那一刻**才现形 ——
    `current_regime` 那边要求 numeric, 转移机上行分支原样搬, 于是整本 watchlist
    写入被拒, 同日达标的其他卡一起没落卡。空 cap 必须在提交边界就挡掉;
    「这道 gate 不授权」有合法写法: 显式 0。
    """
    future = {"name": "pending_after_repair", "entry_gate": {
        "level": 130, "days": 2, "volume_ratio_min": 1.2}, **optional}
    record["result"]["proposal"]["zone_update_args"] += ["--next-regime", json.dumps(future)]
    bind_review(record)
    with pytest.raises(submit.SubmitError, match="cap_pct_nav"):
        submit.apply_proposal(record, apply=True)


@pytest.mark.parametrize("mutate", [
    lambda r: r["review"].update(run_id=r["run_id"]),
    lambda r: r["review"].update(status="failed"),
    lambda r: r["review"].update(role="macro"),
    lambda r: r["review"].update(policy_hash="0" * 64),
    lambda r: r["review"].update(provider="claude"),
    lambda r: r["review"].update(provider=" CLAUDE "),
    lambda r: r["review"].pop("provider"),
    lambda r: r.pop("provider"),
    lambda r: r["review"].update(reviewed_result_hash="0" * 64),
    lambda r: r["review"]["result"]["review"].update(run_id="another-run"),
    lambda r: r["review"]["result"]["review"].update(verdict="reject"),
    lambda r: r["review"]["result"]["review"].update(verdict="needs_review"),
    lambda r: r["review"]["result"].update(snapshot_id="other-snapshot"),
    lambda r: r["review"]["snapshot"].update(snapshot_id="other-snapshot"),
])
def test_review_must_approve_exact_result_run_snapshot_and_policy(record, mutate):
    mutate(record)
    with pytest.raises(submit.SubmitError):
        submit.apply_proposal(record, apply=True)


def test_stale_result_is_rejected_before_writer(record, monkeypatch):
    monkeypatch.setattr(submit, "_now", lambda: datetime.fromisoformat(record["result"]["expires_at"]))
    with pytest.raises(submit.SubmitError, match="expired"):
        submit.apply_proposal(record, apply=True)


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(card_revision="0" * 64),
    lambda d: d.update(completed_bars=[]),
    lambda d: d.update(latest_completed_session="2026-09-03"),
    lambda d: d["completed_bars"][0].update(complete=False),
    lambda d: d.update(capture_warnings=["cached_bars_older_than_seven_days"]),
])
def test_invalid_snapshot_card_or_bars_cannot_apply(record, mutate):
    mutate(record["snapshot"]["data"])
    rebind_snapshot(record)
    with pytest.raises(submit.SubmitError):
        submit.apply_proposal(record, apply=True)


def test_yesterday_missing_is_stale_even_inside_seven_day_window(record):
    data = record["snapshot"]["data"]
    data["completed_bars"][0]["time"] = data["latest_completed_session"] = "2026-09-03"
    argv = record["result"]["proposal"]["zone_update_args"]
    argv[argv.index("--anchored-on") + 1] = "2026-09-03"
    rebind_snapshot(record)
    assert submit.apply_proposal(record)["can_apply"] is False
    with pytest.raises(submit.SubmitError, match="latest exchange close"):
        submit.apply_proposal(record, apply=True)


def test_anchor_must_be_the_completed_bar_date(record):
    argv = record["result"]["proposal"]["zone_update_args"]
    argv[argv.index("--anchored-on") + 1] = "2026-09-05"
    bind_review(record)
    with pytest.raises(submit.SubmitError, match="zone anchor"):
        submit.apply_proposal(record, apply=True)


def test_apply_uses_existing_writer_and_revision_check(record, writer):
    before_positions = writer.POSITIONS_PATH.read_bytes()
    receipt = submit.apply_proposal(record, apply=True)
    assert receipt["status"] == "applied" and receipt["applied"]
    assert writer.POSITIONS_PATH.read_bytes() == before_positions
    updated = writer.WATCHLIST_PATH.read_bytes()
    card = json.loads(updated)["tickers"]["SYN"]
    assert card["agent"]["zones"]["reasoning_summary"] == "Reviewed synthetic map"
    assert card["agent"]["current_regime"] == record["snapshot"]["data"]["card"]["agent"]["current_regime"]
    with pytest.raises(submit.SubmitError, match="revision conflict"):
        submit.apply_proposal(record, apply=True)
    assert writer.WATCHLIST_PATH.read_bytes() == updated


def test_live_card_change_after_snapshot_rejects_without_overwriting(record, writer):
    doc = json.loads(writer.WATCHLIST_PATH.read_text())
    doc["tickers"]["SYN"]["agent"]["key_risk"] = "new concurrent fact"
    writer.WATCHLIST_PATH.write_text(json.dumps(doc))
    before = writer.WATCHLIST_PATH.read_bytes()
    with pytest.raises(submit.SubmitError, match="revision conflict"):
        submit.apply_proposal(record, apply=True)
    assert writer.WATCHLIST_PATH.read_bytes() == before


@pytest.mark.parametrize("field,value", [("days", 1), ("volume_ratio_min", 1.0)])
def test_apply_cannot_weaken_gate_confirmation(record, writer, field, value):
    nr = deepcopy(record["snapshot"]["data"]["card"]["agent"]["next_regime"])
    nr["entry_gate"][field] = value
    record["result"]["proposal"]["zone_update_args"] += ["--next-regime", json.dumps(nr)]
    bind_review(record)
    before = writer.WATCHLIST_PATH.read_bytes()
    with pytest.raises(submit.SubmitError, match="relax existing gate"):
        submit.apply_proposal(record, apply=True)
    assert writer.WATCHLIST_PATH.read_bytes() == before


def test_apply_cannot_extend_stop_confirmation(record, writer):
    record["result"]["proposal"]["zone_update_args"] += ["--stop-level-days", "2"]
    bind_review(record)
    before = writer.WATCHLIST_PATH.read_bytes()
    with pytest.raises(submit.SubmitError, match="relax existing stop"):
        submit.apply_proposal(record, apply=True)
    assert writer.WATCHLIST_PATH.read_bytes() == before


def test_shell_metacharacters_are_inert_text_not_commands(record, writer):
    text = "literal $(touch forbidden); `echo ignored`"
    record["result"]["proposal"]["zone_update_args"] += ["--thesis", text]
    bind_review(record)
    submit.apply_proposal(record, apply=True)
    assert json.loads(writer.WATCHLIST_PATH.read_text())["tickers"]["SYN"]["agent"]["thesis"]["summary"] == text


def test_missing_volume_leg_cannot_remove_existing_requirement(record, writer):
    nr = deepcopy(record["snapshot"]["data"]["card"]["agent"]["next_regime"])
    del nr["entry_gate"]["volume_ratio_min"]
    record["result"]["proposal"]["zone_update_args"] += ["--next-regime", json.dumps(nr)]
    bind_review(record)
    with pytest.raises(submit.SubmitError, match="gate volume"):
        submit.apply_proposal(record, apply=True)


def test_existing_explicit_gate_exception_is_preserved(record, writer):
    agent = record["snapshot"]["data"]["card"]["agent"]
    agent["next_regime"]["entry_gate"].update(days=1, volume_ratio_min=1.0)
    writer.WATCHLIST_PATH.write_text(json.dumps({"tickers": {"SYN": record["snapshot"]["data"]["card"]}}))
    record["snapshot"]["data"]["card_revision"] = card_revision(record["snapshot"]["data"]["card"])
    nr = deepcopy(agent["next_regime"])
    record["result"]["proposal"]["zone_update_args"] += ["--next-regime", json.dumps(nr)]
    rebind_snapshot(record)
    assert submit.apply_proposal(record, apply=True)["applied"] is True


def test_errors_distinguish_prewrite_rejection_from_uncertain_writer_failure(record, writer, monkeypatch):
    record["review"] = None
    with pytest.raises(submit.SubmitError) as error:
        submit.apply_proposal(record, apply=True)
    assert error.value.side_effect_possible is False
    bind_review(record)
    monkeypatch.setattr(writer, "main", lambda argv, **kwargs: (_ for _ in ()).throw(RuntimeError("interrupted writer")))
    with pytest.raises(submit.SubmitError) as error:
        submit.apply_proposal(record, apply=True)
    assert error.value.side_effect_possible is True


def test_new_gate_uses_existing_policy_defaults(record, writer):
    record["snapshot"]["data"]["card"]["agent"].pop("next_regime")
    record["snapshot"]["data"]["card_revision"] = card_revision(record["snapshot"]["data"]["card"])
    writer.WATCHLIST_PATH.write_text(json.dumps({"tickers": {"SYN": record["snapshot"]["data"]["card"]}}))
    nr = {"name": "new", "cap_pct_nav": 0.4, "entry_gate": {"level": 115, "volume_ratio_min": 1.2},
          "zones": {"stop_level": 110, "accumulation_zone_1": [115, 118]}}
    record["result"]["proposal"]["zone_update_args"] += ["--next-regime", json.dumps(nr)]
    rebind_snapshot(record)
    assert submit.apply_proposal(record, apply=True)["applied"] is True


def test_loader_failure_is_a_prewrite_rejection(record, monkeypatch):
    monkeypatch.setattr(submit, "_load_writer", lambda: (_ for _ in ()).throw(ImportError("missing writer")))
    with pytest.raises(submit.SubmitError) as error:
        submit.apply_proposal(record, apply=True)
    assert error.value.side_effect_possible is False


def _recapture_time(record, now, *, session=None):
    """A new capture/research/review, never extending an old result's TTL."""
    data = record["snapshot"]["data"]
    argv = record["result"]["proposal"]["zone_update_args"]
    if session is not None:
        data["latest_completed_session"] = data["completed_bars"][0]["time"] = session
        argv[argv.index("--anchored-on") + 1] = session
        argv[argv.index("--valid-until") + 1] = (now.date() + timedelta(days=3)).isoformat()
    snapshot = make_snapshot(data, now=now)
    record["snapshot"] = snapshot
    record["result"].update(snapshot_id=snapshot["snapshot_id"], input_hash=snapshot["input_hash"],
                            generated_at=snapshot["as_of"], expires_at=snapshot["expires_at"])
    return bind_review(record)


def test_labor_day_accepts_friday_close_without_restamping_anchor_or_extending_ttl(record, writer, monkeypatch):
    holiday = datetime.fromisoformat("2026-09-07T21:30:00+00:00")
    _recapture_time(record, holiday)
    monkeypatch.setattr(submit, "_now", lambda: holiday)
    assert submit.apply_proposal(record)["can_apply"] is True
    assert submit.apply_proposal(record, apply=True)["applied"] is True
    updated = json.loads(writer.WATCHLIST_PATH.read_text())["tickers"]["SYN"]
    assert updated["agent"]["zones"]["anchored_on"]["date"] == "2026-09-04"
    assert datetime.fromisoformat(record["snapshot"]["expires_at"]) - holiday == timedelta(hours=1)


def test_holiday_does_not_revive_an_expired_snapshot_or_review(record, monkeypatch):
    monkeypatch.setattr(submit, "_now", lambda: datetime.fromisoformat("2026-09-07T21:30:00+00:00"))
    with pytest.raises(submit.SubmitError, match="expired") as error:
        submit.apply_proposal(record, apply=True)
    assert error.value.side_effect_possible is False


def test_halfday_can_apply_after_13_et_and_not_before(record, writer, monkeypatch):
    closed = datetime.fromisoformat("2026-11-27T18:00:00+00:00")
    before = closed - timedelta(seconds=1)
    _recapture_time(record, before, session="2026-11-27")
    monkeypatch.setattr(submit, "_now", lambda: before)
    with pytest.raises(submit.SubmitError, match="unfinished"):
        submit.apply_proposal(record, apply=True)
    _recapture_time(record, closed, session="2026-11-27")
    monkeypatch.setattr(submit, "_now", lambda: closed)
    assert submit.apply_proposal(record, apply=True)["applied"] is True


@pytest.mark.parametrize("expiration", ["snapshot", "review", "exchange_close"])
def test_waiting_for_state_lock_rechecks_expiry_before_any_write(record, writer, monkeypatch, expiration):
    clock = {"now": NOW}
    if expiration == "review":
        record["review"]["result"]["expires_at"] = (NOW + timedelta(seconds=10)).isoformat()
        after = NOW + timedelta(seconds=11)
    elif expiration == "exchange_close":
        # An unexpired Tuesday intraday proposal becomes stale at Tuesday close.
        clock["now"] = datetime.fromisoformat("2026-09-08T19:59:00+00:00")
        _recapture_time(record, clock["now"])
        after = clock["now"] + timedelta(minutes=1)
    else:
        after = datetime.fromisoformat(record["snapshot"]["expires_at"])
    monkeypatch.setattr(submit, "_now", lambda: clock["now"])
    attempted = threading.Event()
    real_lock = writer.state_lock

    @contextmanager
    def observed_lock():
        attempted.set()
        with real_lock():
            yield

    monkeypatch.setattr(writer, "state_lock", observed_lock)
    before = writer.WATCHLIST_PATH.read_bytes()
    with ThreadPoolExecutor(max_workers=1) as pool:
        with real_lock():
            future = pool.submit(submit.apply_proposal, record, apply=True)
            assert attempted.wait(5), "submission did not reach the real shared lock"
            assert not future.done(), "writer should be waiting for the held state lock"
            clock["now"] = after
        with pytest.raises(submit.SubmitError, match="expired|latest exchange close") as error:
            future.result(timeout=5)
    assert error.value.side_effect_possible is False
    assert writer.WATCHLIST_PATH.read_bytes() == before


def test_precommit_receipt_is_the_final_target_hash_and_precedes_any_write(record, writer):
    before = writer.WATCHLIST_PATH.read_bytes()
    prepared = []

    def persist_audit(receipt):
        assert writer.WATCHLIST_PATH.read_bytes() == before
        assert receipt["before_revision"] == record["snapshot"]["data"]["card_revision"]
        prepared.append(deepcopy(receipt))

    applied = submit.apply_proposal(record, apply=True, on_precommit=persist_audit)
    target = json.loads(writer.WATCHLIST_PATH.read_text())["tickers"]["SYN"]
    assert len(prepared) == 1
    assert applied["prepared"] == prepared[0]
    assert applied["after_revision"] == prepared[0]["after_revision"] == card_revision(target)
    assert applied["before_revision"] == card_revision(record["snapshot"]["data"]["card"])
    assert applied["before_revision"] != applied["after_revision"]


def test_precommit_audit_failure_and_expiry_never_write_a_card(record, writer, monkeypatch):
    before = writer.WATCHLIST_PATH.read_bytes()

    def failed(receipt):
        raise OSError("synthetic audit disk error")

    with pytest.raises(submit.SubmitError, match="audit could not") as error:
        submit.apply_proposal(record, apply=True, on_precommit=failed)
    assert error.value.side_effect_possible is False
    assert writer.WATCHLIST_PATH.read_bytes() == before

    def expired_during_audit(receipt):
        monkeypatch.setattr(submit, "_now", lambda: datetime.fromisoformat(record["snapshot"]["expires_at"]))

    with pytest.raises(submit.SubmitError, match="expired") as error:
        submit.apply_proposal(record, apply=True, on_precommit=expired_during_audit)
    assert error.value.side_effect_possible is False
    assert writer.WATCHLIST_PATH.read_bytes() == before


def test_preview_never_calls_precommit_audit(record):
    def forbidden(receipt):
        raise AssertionError("dry-run must not write a prepared audit")
    receipt = submit.apply_proposal(record, on_precommit=forbidden)
    assert receipt["after_revision"] is None


def test_reconciliation_only_classifies_exact_target_hashes():
    before, after, other = [canonical_hash({"synthetic": i}) for i in range(3)]
    prepared = {"before_revision": before, "after_revision": after}
    for actual, expected in ((after, "applied"), (before, "no_effect"), (other, "needs_review")):
        receipt = submit.reconcile_submission(prepared, actual)
        assert receipt["status"] == expected
        assert receipt["writes_performed"] is False and receipt["retry_authorized"] is False
    assert submit.reconcile_submission({"before_revision": before, "after_revision": before}, before)["status"] == "needs_review"
    with pytest.raises(submit.SubmitError):
        submit.reconcile_submission({"before_revision": before}, after)


# ── : 「消失」比「变松」更严重, 却不在旧判别式里 ──────────────
#
# 受控重画的提案带价位带、不带 --stop-level,
# writer 整块替换擦掉转移机刚写的 bfloor, 本守卫只比 days 于是放行, 回执 applied;
# regime_transition 下行分支 `if stop is not None` 整段不评估 = 破位不降级。

def _drop_stop(record):
    args = record["result"]["proposal"]["zone_update_args"]
    i = args.index("--stop-level")
    del args[i:i + 2]
    return bind_review(record)


def test_apply_rejects_a_repaint_that_drops_the_existing_stop(record, writer):
    _drop_stop(record)
    before = writer.WATCHLIST_PATH.read_bytes()
    with pytest.raises(submit.SubmitError, match="remove existing stop") as ei:
        submit.apply_proposal(record, apply=True)
    assert ei.value.side_effect_possible is False          # merge 之前拒, 不是 writer 失败
    assert writer.WATCHLIST_PATH.read_bytes() == before


def test_preview_blocks_stop_removal_before_the_run_is_accepted(record, writer):
    """Desktop 会话在提交预览就该看到, 不是 applied 之后读回卡片才发现。"""
    _drop_stop(record)
    receipt = submit.preview_candidate(record, require_review=False)
    assert receipt["can_apply"] is False
    assert any("remove existing stop" in r for r in receipt["blocked_reasons"])


def test_keeping_the_stop_means_restating_it(record, writer):
    """保持 = 传原值; writer 同值时自动带过 days / 来历, 守卫放行。"""
    data = record["snapshot"]["data"]
    data["card"]["agent"]["zones"]["stop_level_days"] = 1
    data["card"]["agent"]["zones"]["stop_derivation"] = "bfloor_buf: synthetic"
    data["card_revision"] = card_revision(data["card"])
    rebind_snapshot(record)
    writer.WATCHLIST_PATH.write_text(json.dumps({"tickers": {"SYN": deepcopy(record["snapshot"]["data"]["card"])}}))
    receipt = submit.apply_proposal(record, apply=True)
    assert receipt["status"] == "applied"
    after = json.loads(writer.WATCHLIST_PATH.read_text())["tickers"]["SYN"]["agent"]["zones"]
    assert after["stop_level"] == 90 and after["stop_level_days"] == 1
    assert after["stop_derivation"] == "bfloor_buf: synthetic"


def test_worker_cannot_use_the_clear_stop_escape_hatch(record):
    """--clear-stop 是人工 CLI 的显式删除通道, 不在受控路径白名单里。"""
    record["result"]["proposal"]["zone_update_args"] += ["--clear-stop"]
    bind_review(record)
    with pytest.raises(submit.SubmitError, match="unauthorized"):
        submit.apply_proposal(record, apply=False)


# ── thesis.frame: 受控路径与人工 CLI 走同一个判别式 ─────────────────────
def _synthetic_frame(**over):
    frame = {"as_of": "2026-09-04", "hypothesis": "Synthetic revenue rests on demand leg A",
             "drivers": [{"name": "A", "mechanism": "usage billing", "evidence": "待核实", "kpi": "A share"}],
             "bear_case": {"claim": "competitor parity", "rebuttal": "待核实",
                           "falsifier": "A revenue turns negative"},
             "valuation": "待核实",
             "catalyst": {"event": "next print", "date": "待核实", "baseline": "待核实", "watch": ["A share"]},
             "sources": ["synthetic fixture"]}
    frame.update(over)
    return frame


def test_worker_thesis_frame_applies_through_the_same_writer(record, writer, monkeypatch):
    monkeypatch.setattr(writer, "et_today", lambda: "2026-09-05")
    record["result"]["proposal"]["zone_update_args"] += ["--thesis-frame", json.dumps(_synthetic_frame())]
    bind_review(record)
    assert submit.preview_candidate(record)["can_apply"] is True
    assert submit.apply_proposal(record, apply=True)["status"] == "applied"
    thesis = json.loads(writer.WATCHLIST_PATH.read_text())["tickers"]["SYN"]["agent"]["thesis"]
    assert thesis["frame"] == _synthetic_frame()
    assert thesis["summary"] == _synthetic_frame()["hypothesis"]


@pytest.mark.parametrize("frame", [_synthetic_frame(rating="OW"), _synthetic_frame(drivers=[]),
                                   ["not", "an", "object"]])
def test_malformed_thesis_frame_is_rejected_at_the_boundary(record, frame):
    record["result"]["proposal"]["zone_update_args"] += ["--thesis-frame", json.dumps(frame)]
    bind_review(record)
    with pytest.raises(submit.SubmitError, match="thesis-frame"):
        submit.apply_proposal(record)


def test_preview_blocks_a_frame_that_drops_reached_watch_items(record, writer, monkeypatch):
    """预注册的看点到期后被静默换掉 = 事件后讲故事; Desktop 会话在 preview 就要看到。"""
    monkeypatch.setattr(writer, "et_today", lambda: "2026-09-05")
    data = record["snapshot"]["data"]
    prior = _synthetic_frame(catalyst={"event": "last print", "date": "2026-09-01",
                                       "baseline": "待核实", "watch": ["A share"]})
    data["card"]["agent"]["thesis"].update(summary=prior["hypothesis"], frame=prior)
    data["card_revision"] = card_revision(data["card"])
    record["result"]["proposal"]["zone_update_args"] += ["--thesis-frame", json.dumps(_synthetic_frame())]
    rebind_snapshot(record)
    receipt = submit.preview_candidate(record)
    assert receipt["can_apply"] is False
    assert any("review" in reason for reason in receipt["blocked_reasons"])
