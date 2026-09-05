"""A fully offline Claude proposal → Codex review → controlled card commit."""
from copy import deepcopy
from datetime import datetime, timedelta
import json

import pytest

from quant.core.agent_contracts import CONTRACT_VERSION, RESULT_SCHEMA_VERSION, canonical_hash
from quant.core.market_sessions import session_close
from quant.ops import agent_runtime as runtime, agent_submit as submit
from quant.ops.agent_snapshots import capture_card
from tests.ops.test_agent_submit import NOW, record, writer  # noqa: F401 — shared synthetic fixtures


class FrozenClock(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW.astimezone(tz) if tz is not None else NOW.replace(tzinfo=None)


def test_reviewed_runtime_commit_is_targeted_idempotent_and_revision_fenced(
        record, writer, monkeypatch, tmp_path):
    """Use real capture, contracts, run receipts, submit checks, merger and CAS writer."""
    monkeypatch.setattr(runtime, "datetime", FrozenClock)
    monkeypatch.setattr(runtime, "policy_context", lambda role: {
        "synthetic-policy": "Use frozen price evidence; existing deterministic rules retain authority."})
    routes = runtime.load_routes()
    routes["roles"]["technical"].update(provider="claude", model=None,
                                          reviewer={"provider": "codex", "model": None}, fallback=[])
    document = json.loads(writer.WATCHLIST_PATH.read_text())
    document["tickers"]["OTHER"] = deepcopy(document["tickers"]["SYN"])
    document["tickers"]["OTHER"]["agent"]["key_risk"] = "unrelated card must survive exactly"
    writer.WATCHLIST_PATH.write_text(json.dumps(document))
    writer.KLINE_CACHE_DIR.mkdir()
    dates, day = [], datetime.fromisoformat("2026-09-04").date()
    while len(dates) < 60:
        if session_close(day) is not None:
            dates.append(day.isoformat())
        day -= timedelta(days=1)
    bars = []
    for i, stamp in enumerate(reversed(dates)):
        close = 100 + (i % 5) * 2
        bars.append({"time": stamp, "open": close - 2, "low": close - 3,
                     "high": close + 3, "close": close, "volume": 1000 + i * 10})
    (writer.KLINE_CACHE_DIR / "SYN.jsonl").write_text(
        "\n".join(json.dumps(bar) for bar in bars) + "\n")
    snapshot = capture_card("SYN", "watchlist", now=NOW,
                            positions_path=writer.POSITIONS_PATH, watchlist_path=writer.WATCHLIST_PATH,
                            kline_dir=writer.KLINE_CACHE_DIR, macro_path=tmp_path / "absent-macro.json")
    assert snapshot["data"]["technical"] is not None
    assert snapshot["data"]["level_map"] is not None
    assert snapshot["data"]["card_revision"] == canonical_hash(document["tickers"]["SYN"])
    frozen_snapshot = deepcopy(snapshot)
    worker_args = deepcopy(record["result"]["proposal"]["zone_update_args"])
    calls = []

    def offline_runner(provider, prompt, schema, **kwargs):
        packet = json.loads(prompt.split("\n", 1)[1])
        frozen, target = packet["snapshot"], packet["review_target"]
        calls.append((provider, target["run_id"] if target else None))
        assert frozen == frozen_snapshot
        assert kwargs["cwd"].exists() and list(kwargs["cwd"].iterdir()) == []
        assert provider == ("codex" if target else "claude")
        payload = {"schema_version": RESULT_SCHEMA_VERSION, "contract_version": CONTRACT_VERSION,
                   "role": "technical", "snapshot_id": frozen["snapshot_id"], "input_hash": frozen["input_hash"],
                   "generated_at": frozen["as_of"], "expires_at": frozen["expires_at"],
                   "summary": "合成快照的独立复核" if target else "合成价位图提案",
                   "observations": [{"id": "close", "kind": "fact", "text": "合成已完成收盘价为108。",
                                     "evidence_refs": ["completed_bars"]}],
                   "hypotheses": [], "risks": [], "unknowns": ["宏观先验缺失"], "requests": [],
                   "proposal": None if target else {"kind": "zone_update", "zone_update_args": worker_args},
                   "review": {"run_id": target["run_id"], "verdict": "approve",
                              "reasons": ["按原始合成K线与价位图独立复核，维持既有规则。"]} if target else None,
                   "authority": {"mode": "proposal_only", "may_write_state": False, "may_place_orders": False}}
        return {"status": "succeeded", "payload": payload, "provider": provider,
                "model": "offline-fixture", "elapsed_s": 0, "usage": {}}

    runs = tmp_path / "runtime"
    first = runtime.run_role("technical", "合成提案A", snapshot, routes=routes, run_dir=runs,
                             runner=offline_runner, review=True, idempotency_key="A")
    # This independently reviewed proposal keeps the old revision until after A commits.
    stale = runtime.run_role("technical", "合成提案B", snapshot, routes=routes, run_dir=runs,
                             runner=offline_runner, review=True, idempotency_key="B")
    assert first["status"] == stale["status"] == "succeeded"
    assert first["provider"] == "claude" and first["review"]["provider"] == "codex"
    assert first["review"]["reviewed_result_hash"] == canonical_hash(first["result"])
    assert [provider for provider, _ in calls] == ["claude", "codex", "claude", "codex"]
    assert calls[1][1] == first["run_id"] and calls[3][1] == stale["run_id"]

    before_positions, before_watchlist = writer.POSITIONS_PATH.read_bytes(), writer.WATCHLIST_PATH.read_bytes()
    loaded = []
    monkeypatch.setattr(submit, "_load_writer", lambda: loaded.append(True) or writer)
    preview = runtime.submit_run(first["run_id"], run_dir=runs)
    assert preview["status"] == "dry_run" and preview["can_apply"] is True
    assert loaded == []
    assert (writer.POSITIONS_PATH.read_bytes(), writer.WATCHLIST_PATH.read_bytes()) == (
        before_positions, before_watchlist)

    receipt = runtime.submit_run(first["run_id"], apply=True, run_dir=runs)
    assert receipt["status"] == "applied" and receipt["applied"] is True
    after = writer.WATCHLIST_PATH.read_bytes()
    committed = json.loads(after)
    assert committed["tickers"]["OTHER"] == document["tickers"]["OTHER"]
    assert committed["tickers"]["SYN"] != document["tickers"]["SYN"]
    assert committed["tickers"]["SYN"]["agent"]["zones"]["reasoning_summary"] == "Reviewed synthetic map"
    assert writer.POSITIONS_PATH.read_bytes() == before_positions

    repeated = runtime.submit_run(first["run_id"], apply=True, run_dir=runs)
    assert repeated == receipt and len(loaded) == 1
    assert writer.WATCHLIST_PATH.read_bytes() == after
    assert len(calls) == 4, "submitting a completed run must never invoke models again"

    with pytest.raises(submit.SubmitError, match="revision conflict") as conflict:
        runtime.submit_run(stale["run_id"], apply=True, run_dir=runs)
    assert conflict.value.side_effect_possible is False
    stale_record = json.loads((runs / "runs" / f"{stale['run_id']}.json").read_text())
    assert stale_record["submission"]["status"] == "rejected"
    assert (writer.POSITIONS_PATH.read_bytes(), writer.WATCHLIST_PATH.read_bytes()) == (before_positions, after)
    assert snapshot == frozen_snapshot
