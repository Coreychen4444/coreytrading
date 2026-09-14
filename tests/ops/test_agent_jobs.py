"""Production job boundaries on synthetic evidence; never invoke native models."""
from copy import deepcopy
from datetime import datetime, timezone
import json

import pytest

from quant.core.agent_contracts import canonical_hash, card_revision
from quant.core.io_json import read_json
from quant.ops import agent_jobs as jobs
from quant.ops.agent_health import acquire_job, release_job
from quant.ops.agent_snapshots import make_snapshot


def capture(**kwargs):
    card = {"agent": {"zones": {"stop_level": 90}}}
    data = {"ticker": "DEMO", "bucket": "watchlist", "card": card,
            "card_revision": card_revision(card), "repaint_targets": []}
    snap = make_snapshot(data)
    return {"snapshot": snap, "snapshots": {r: snap for r in ("macro", "technical", "risk", "valuation")},
            "freshness": {"market": {"DEMO": {"status": "current"}}},
            "summary": {"scope": kwargs.get("scope"), "repaint_targets": []}}


def record(*args, **kwargs):
    return {"run_id": "a" * 32, "status": "succeeded", "provider": "claude",
            "result": {"proposal": {"kind": "zone_update"}, "summary": "synthetic", "unknowns": []}}


def settings():
    routes = jobs.load_routes()
    routes["autopaint"].update(enabled=True, apply=True, review=True)
    return routes


def test_disabled_repaint_keeps_research_and_entire_pending_queue(tmp_path):
    routes = settings()
    routes["autopaint"].update(enabled=False, apply=False, review=False)
    targets = [{"ticker": "DEMO", "bucket": "watchlist"}]
    def frozen(**kwargs):
        out = capture(**kwargs)
        out["summary"]["repaint_targets"] = targets
        out["snapshot"]["data"]["repaint_targets"] = targets
        return out
    def forbidden(*args, **kwargs):
        raise AssertionError("disabled repaint must not dispatch")
    out = jobs.run_job("postclose", apply=True, routes=routes, run_dir=tmp_path,
                       control_dir=tmp_path / "control",
                       capture_fn=frozen, run_role_fn=record, card_fn=forbidden)
    assert set(out["reports"]) == {"technical", "risk"}
    assert out["cards"] == [] and out["pending_cards"] == targets
    assert out["status"] == "partial"
    assert out["repaint_disabled_reason"] == "autopaint_apply_disabled"


def test_card_research_honors_disabled_review(tmp_path):
    routes = settings()
    routes["autopaint"].update(apply=False, review=False)
    def worker(*args, **kwargs):
        assert kwargs["review"] is False
        return {**record(), "result": {"proposal": None}}
    out = jobs.run_card("DEMO", "watchlist", routes=routes, run_dir=tmp_path,
                        control_dir=tmp_path / "control", capture_fn=capture, run_role_fn=worker)
    assert out["reason"] == "model_abstained"


def test_card_business_lease_precedes_capture(tmp_path):
    control = tmp_path / "control"
    held = acquire_job(control, "technical:watchlist:DEMO", timeout_s=60)
    def unexpected(**kwargs):
        raise AssertionError("duplicate capture")
    out = jobs.run_card("DEMO", "watchlist", routes=settings(), run_dir=tmp_path,
                        control_dir=control, capture_fn=unexpected)
    assert out["status"] == "busy"
    assert not (tmp_path / "task-jobs").exists()
    release_job(control, held["lease_id"])


def test_card_current_bars_required_before_model(tmp_path):
    def stale(**kwargs):
        out = capture(**kwargs)
        out["freshness"]["market"]["DEMO"]["status"] = "stale"
        return out
    out = jobs.run_card("DEMO", "watchlist", apply=True, routes=settings(), run_dir=tmp_path,
                        control_dir=tmp_path / "control", capture_fn=stale,
                        run_role_fn=lambda *a, **k: (_ for _ in ()).throw(AssertionError("model called")))
    assert out["status"] == "needs_review"
    assert len(list((tmp_path / "task-jobs").glob("*.json"))) == 1


def test_apply_uses_host_submit_and_persists_before_image(tmp_path):
    calls = []
    def submit(run_id, *, apply, **kwargs):
        calls.append(apply)
        if apply:
            backup = read_json(tmp_path / "card-backups" / (run_id + ".json"))
            assert backup["revision"] == card_revision(backup["card"])
        return {"status": "applied" if apply else "preview", "can_apply": True}
    out = jobs.run_card("DEMO", "watchlist", apply=True, routes=settings(), run_dir=tmp_path,
                        control_dir=tmp_path / "control", capture_fn=capture,
                        run_role_fn=record, submit_fn=submit)
    assert out["status"] == "applied" and calls == [False, True]
    saved = read_json(next((tmp_path / "task-jobs").glob("*.json")))
    assert saved["status"] == "applied" and saved["finished_at"]


def test_abstention_is_visible_and_never_submits(tmp_path):
    def abstain(*a, **k):
        out = record()
        out["result"]["proposal"] = None
        return out
    out = jobs.run_card("DEMO", "watchlist", apply=True, routes=settings(), run_dir=tmp_path,
                        control_dir=tmp_path / "control", capture_fn=capture, run_role_fn=abstain,
                        submit_fn=lambda *a, **k: (_ for _ in ()).throw(AssertionError("submitted")))
    assert out["status"] == "needs_review" and out["reason"] == "model_abstained"


def test_production_job_persists_workflow_and_leaves_unknowns_visible(tmp_path):
    def workflow(*args, **kwargs):
        return {"status": "partial", "reports": {"risk": {"run_id": "a" * 32,
                "status": "failed", "result": {"summary": None, "unknowns": ["missing receipt"]}}},
                "total_calls": 4, "macro_research": {"initial": {"status": "unavailable"}, "followup": None}}
    out = jobs.run_job("weekly", run_dir=tmp_path, control_dir=tmp_path / "control",
                       capture_fn=capture, workflow_fn=workflow)
    assert out["status"] == "partial" and out["reports"]["risk"]["unknowns"]
    assert out["macro_research"]["initial"]["status"] == "unavailable"
    assert read_json(next((tmp_path / "task-jobs").glob("*.json")))["status"] == "partial"
    assert read_json(next((tmp_path / "workflows").glob("*.json")))["total_calls"] == 4


@pytest.mark.parametrize("queue_len, expected", [
    (0, 5), (3, 5), (5, 5), (6, 5), (10, 5),   # floor: at least five per round
    (11, 6), (12, 6), (13, 7), (17, 9),        # above: ceil(n/2) so two rounds drain it
])
def test_repaint_bound_is_at_least_five_and_drains_in_two_rounds(queue_len, expected):
    assert jobs.repaint_bound(queue_len) == expected
    assert queue_len <= expected * jobs.MAX_ROUNDS_TO_DRAIN


@pytest.mark.parametrize("queue_len, processed", [(7, 5), (12, 6)])
def test_repaint_bound_retains_pending_queue(tmp_path, queue_len, processed):
    def many(**kwargs):
        out = capture(**kwargs)
        targets = [{"ticker": "DEMO" + str(i), "bucket": "watchlist"} for i in range(queue_len)]
        out["snapshot"]["data"]["repaint_targets"] = targets
        out["summary"]["repaint_targets"] = targets
        return out
    calls = []
    def card(ticker, bucket, **kwargs):
        calls.append(ticker)
        return {"status": "applied"}
    out = jobs.run_job("postclose", apply=True, routes=settings(), run_dir=tmp_path,
                       control_dir=tmp_path / "control", capture_fn=many, run_role_fn=record, card_fn=card)
    assert calls == ["DEMO" + str(i) for i in range(processed)]
    assert out["repaint_bound"] == processed
    assert out["status"] == "partial" and len(out["pending_cards"]) == queue_len - processed


def test_scheduled_holiday_skips_before_capture_or_state(tmp_path):
    def unexpected(**kwargs):
        raise AssertionError("captured on Labor Day")
    out = jobs.run_job("briefing", scheduled=True, now=datetime(2026, 9, 7, 10, 30, tzinfo=timezone.utc),
                       run_dir=tmp_path, control_dir=tmp_path / "control", capture_fn=unexpected)
    assert out["status"] == "skipped"
    assert list(tmp_path.iterdir()) == []


def test_weekly_requests_only_trading_workflow(tmp_path):
    seen = []
    def workflow(*args, **kwargs):
        seen.append(kwargs['purpose'])
        return {'status': 'completed', 'reports': {}, 'total_calls': 0}
    out = jobs.run_job('weekly', run_dir=tmp_path, control_dir=tmp_path / 'control',
                       capture_fn=capture, workflow_fn=workflow)
    assert out['status'] == 'completed' and 'valuation_calculations' not in out
    assert seen == ['trading']


def test_daily_kinds_run_one_technical_model_and_mechanical_risk(tmp_path):
    """Briefing/postclose = technical model + code-rendered risk; no macro call."""
    roles = []
    def worker(role, task, snapshot, **kwargs):
        roles.append(role)
        return record()
    def forbidden(*args, **kwargs):
        raise AssertionError("daily kinds must not start the multi-role workflow")
    for kind in ('briefing', 'postclose'):
        out = jobs.run_job(kind, run_dir=tmp_path, control_dir=tmp_path / 'control',
                           capture_fn=capture, run_role_fn=worker, workflow_fn=forbidden)
        assert out['status'] == 'completed'
        assert set(out['reports']) == {'technical', 'risk'}
        assert out['reports']['technical']['reused'] is False
        risk = out['reports']['risk']
        assert risk['provider'] == 'host' and risk['model_call'] is False
        assert risk['kind'] == 'mechanical_risk_receipt' and '机械风控收据' in risk['summary']
    assert roles == ['technical', 'technical']


def test_same_session_and_cards_reuse_the_technical_run(tmp_path):
    """The next briefing must not resend the same completed session to the model."""
    card = {"agent": {"zones": {"stop_level": 90}}}
    view = make_snapshot({"scope": "postclose", "cards": {"DEMO": {"status": "captured", "card_revision": card_revision(card)}}})
    def frozen(**kwargs):
        out = capture(**kwargs)
        out["snapshots"]["technical"] = view
        out["summary"]["latest_completed_session"] = "2026-09-08"
        return out
    calls = []
    def worker(role, task, snapshot, **kwargs):
        calls.append(role)
        return {**record(), "snapshot": snapshot, "role": role}
    first = jobs.run_job('postclose', run_dir=tmp_path, control_dir=tmp_path / 'control',
                         capture_fn=frozen, run_role_fn=worker)
    assert first['reports']['technical']['reused'] is False
    # The runtime persists run records; emulate the one the fake worker produced.
    from quant.core.io_json import atomic_write_json
    atomic_write_json(tmp_path / "runs" / ("a" * 32 + ".json"),
                      {"run_id": "a" * 32, "status": "succeeded", "provider": "claude",
                       "snapshot": view, "result": {"summary": "synthetic", "unknowns": []}})
    second = jobs.run_job('briefing', run_dir=tmp_path, control_dir=tmp_path / 'control',
                          capture_fn=frozen, run_role_fn=worker)
    assert second['reports']['technical']['reused'] is True
    assert second['reports']['technical']['reused_from_run_id'] == "a" * 32
    assert calls == ['technical']
    # A changed card revision forces a fresh model run.
    changed = deepcopy(view["data"]); changed["cards"]["DEMO"]["card_revision"] = "0" * 64
    def moved(**kwargs):
        out = frozen(**kwargs)
        out["snapshots"]["technical"] = make_snapshot(changed)
        return out
    third = jobs.run_job('briefing', run_dir=tmp_path, control_dir=tmp_path / 'control',
                         capture_fn=moved, run_role_fn=worker)
    assert third['reports']['technical']['reused'] is False and calls == ['technical', 'technical']


def test_card_dispatch_uses_technical_view_and_archives_full_capture(tmp_path):
    c = capture(scope='card')
    full = deepcopy(c['snapshot']['data'])
    full['unrelated_financial_archive'] = 'shared-only'
    c['snapshot'] = make_snapshot(full)
    seen = []
    def worker(role, task, snapshot, **kwargs):
        seen.append(snapshot)
        assert 'unrelated_financial_archive' not in snapshot['data']
        assert snapshot['data']['card_revision'] == c['snapshot']['data']['card_revision']
        return {**record(), 'status': 'needs_review'}
    out = jobs.run_card('DEMO', 'watchlist', run_dir=tmp_path,
                        control_dir=tmp_path / 'control', capture_fn=lambda **kw: c,
                        run_role_fn=worker)
    assert seen == [c['snapshots']['technical']]
    assert read_json(out['capture_artifact']) == c['snapshot']


def test_receipt_stays_running_until_the_last_card_is_written(tmp_path):
    """A 'completed' receipt without finished_at mid-repaint misled the ladder task."""
    targets = [{"ticker": "DEMO", "bucket": "watchlist"}]
    def frozen(**kwargs):
        out = capture(**kwargs)
        out["summary"]["repaint_targets"] = targets
        out["snapshot"]["data"]["repaint_targets"] = targets
        return out
    seen = []
    def card(ticker, bucket, **kwargs):
        receipt = read_json(next((tmp_path / "task-jobs").glob("*.json")))
        seen.append((receipt["status"], receipt["stage"], receipt.get("research_status"), "finished_at" in receipt))
        return {"status": "applied"}
    out = jobs.run_job("postclose", apply=True, routes=settings(), run_dir=tmp_path,
                       control_dir=tmp_path / "control", capture_fn=frozen, run_role_fn=record, card_fn=card)
    assert seen == [("running", "reviewed_repaint", "completed", False)]
    assert out["status"] == "completed" and out["research_status"] == "completed"
    saved = read_json(next((tmp_path / "task-jobs").glob("*.json")))
    assert saved["status"] == "completed" and saved["finished_at"] and saved["stage"] == "finished"


def test_chart_is_no_longer_a_model_job(tmp_path):
    """chart_read.py is the deliverable; no model call, no packet."""
    from quant.ops import agent_desktop
    assert "chart" not in jobs.TASKS and "chart" not in agent_desktop.COMMANDS
    with pytest.raises(ValueError, match="unknown domain job kind"):
        jobs.run_job("chart", run_dir=tmp_path, control_dir=tmp_path / "control")
    # It stays a scheduled kind: the task still gates itself on the trading calendar.
    from quant.core.market_sessions import scheduled_session_due
    due = scheduled_session_due("chart", datetime(2026, 9, 9, 20, 40, tzinfo=timezone.utc))
    assert due["due"] is True and due["session"] == "2026-09-09"
