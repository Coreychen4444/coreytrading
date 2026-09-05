"""Production job boundaries on synthetic evidence; never invoke native models."""
from copy import deepcopy
from datetime import datetime, timezone
import json

from quant.core.agent_contracts import canonical_hash
from quant.core.io_json import read_json
from quant.ops import agent_jobs as jobs
from quant.ops.agent_health import acquire_job, release_job
from quant.ops.agent_snapshots import make_snapshot


def capture(**kwargs):
    card = {"agent": {"zones": {"stop_level": 90}}}
    data = {"ticker": "DEMO", "bucket": "watchlist", "card": card,
            "card_revision": canonical_hash(card), "repaint_targets": []}
    snap = make_snapshot(data)
    return {"snapshot": snap, "snapshots": {r: snap for r in ("macro", "technical", "risk", "valuation")},
            "freshness": {"market": {"DEMO": {"status": "current"}}},
            "summary": {"scope": kwargs.get("scope"), "repaint_targets": []}}


def record(*args, **kwargs):
    return {"run_id": "a" * 32, "status": "succeeded", "provider": "claude",
            "result": {"proposal": {"kind": "zone_update"}, "summary": "synthetic", "unknowns": []}}


def settings():
    routes = jobs.load_routes()
    routes["autopaint"]["apply"] = True
    return routes


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
            assert backup["revision"] == canonical_hash(backup["card"])
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
                "total_calls": 4}
    out = jobs.run_job("weekly", run_dir=tmp_path, control_dir=tmp_path / "control",
                       capture_fn=capture, workflow_fn=workflow)
    assert out["status"] == "partial" and out["reports"]["risk"]["unknowns"]
    assert read_json(next((tmp_path / "task-jobs").glob("*.json")))["status"] == "partial"
    assert read_json(next((tmp_path / "workflows").glob("*.json")))["total_calls"] == 4


def test_repaint_bound_retains_pending_queue(tmp_path):
    def many(**kwargs):
        out = capture(**kwargs)
        targets = [{"ticker": "DEMO" + str(i), "bucket": "watchlist"} for i in range(4)]
        out["snapshot"]["data"]["repaint_targets"] = targets
        out["summary"]["repaint_targets"] = targets
        return out
    calls = []
    def card(ticker, bucket, **kwargs):
        calls.append(ticker)
        return {"status": "applied"}
    out = jobs.run_job("postclose", apply=True, routes=settings(), run_dir=tmp_path,
                       control_dir=tmp_path / "control", capture_fn=many, run_role_fn=record, card_fn=card)
    assert calls == ["DEMO0", "DEMO1"]
    assert out["status"] == "partial" and len(out["pending_cards"]) == 2


def test_scheduled_holiday_skips_before_capture_or_state(tmp_path):
    def unexpected(**kwargs):
        raise AssertionError("captured on Labor Day")
    out = jobs.run_job("briefing", scheduled=True, now=datetime(2026, 9, 7, 10, 30, tzinfo=timezone.utc),
                       run_dir=tmp_path, control_dir=tmp_path / "control", capture_fn=unexpected)
    assert out["status"] == "skipped"
    assert list(tmp_path.iterdir()) == []
