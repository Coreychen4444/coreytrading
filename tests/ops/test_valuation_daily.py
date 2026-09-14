"""Dedicated scheduling boundaries, synthetic evidence and fake workers only."""
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import pytest

from quant.core.io_json import read_json
from quant.ops import valuation_daily as daily
from quant.ops.agent_health import acquire_job, release_job
from quant.ops.agent_snapshots import make_snapshot
from tests.valuation.test_assessment import data, STAMP


@pytest.fixture
def setup(tmp_path):
    events = []
    now = datetime.fromisoformat(STAMP.replace("Z", "+00:00"))
    frozen = make_snapshot(data(), now=now)

    def capture(**kwargs):
        events.append("capture")
        return {"snapshot": frozen, "snapshots": {r: frozen for r in
                ("macro", "technical", "risk", "valuation")}, "summary": {"scope": "portfolio"}}

    def refresh(tickers):
        events.append("refresh")
        assert tickers == ["SYN"]
        return {"errors": {}}

    def worker(role, task, snapshot, **kwargs):
        events.append(role)
        assert kwargs["review"] is False
        assert kwargs["control_dir"] == tmp_path / "control"
        assert role in ("macro", "valuation")
        return {"role": role, "status": "succeeded", "run_id": role,
                "result": {"summary": "synthetic", "requests": [], "valuation_scenarios": []}}

    return dict(root=tmp_path / "daily", run_dir=tmp_path / "agents",
                control_dir=tmp_path / "control", now=now,
                universe_fn=lambda: ["SYN"], refresh_fn=refresh, capture_fn=capture,
                routes={"version": 1, "limits": {"max_calls": 4}, "roles": {}},
                run_role_fn=worker), events


def test_independent_daily_sequence_and_archived_receipt(setup):
    kwargs, events = setup
    out = daily.run_daily(**kwargs)
    assert events == ["refresh", "capture", "macro", "valuation"]
    assert out["status"] == "completed"
    assert out["coverage"] == {"total": 1, "computed": 1, "publishable": 0, "company_research": 0}
    assert out["publication"]["status"] == "withheld"
    assert read_json(out["receipt"])["receipt_hash"] == out["receipt_hash"]
    assert read_json(out["workflow"])["risk_snapshot"] is None
    assert "暂不发布" in Path(out["report"]).read_text()
    assert "基准DCF $" not in Path(out["report"]).read_text()
    assert read_json(kwargs["root"] / "latest.json")["run_id"] == out["run_id"]


def test_duplicate_day_stops_before_data_and_models(setup):
    kwargs, events = setup
    first = daily.run_daily(**kwargs)
    events.clear()
    second = daily.run_daily(**kwargs)
    assert second["status"] == "skipped" and events == []
    assert second["artifact"] == first["artifact"]


def test_busy_stops_before_data_and_models(setup):
    kwargs, events = setup
    lease = acquire_job(kwargs["control_dir"], "valuation-daily", timeout_s=60)
    try:
        assert daily.run_daily(**kwargs)["status"] == "busy"
        assert events == [] and not kwargs["root"].exists()
    finally:
        release_job(kwargs["control_dir"], lease["lease_id"])


def test_failure_supersedes_current_pointer_without_old_price(setup):
    kwargs, events = setup
    good = daily.run_daily(**kwargs)

    def fail(**kwargs):
        raise RuntimeError("capture unavailable")

    out = daily.run_daily(**{**kwargs, "retry": True, "capture_fn": fail})
    latest = read_json(kwargs["root"] / "latest.json")
    assert out["status"] == latest["status"] == "failed"
    assert "receipt" not in latest and "report" not in latest
    assert Path(good["receipt"]).exists()
    events.clear()
    assert daily.run_daily(**kwargs)["status"] == "skipped"
    assert events == []


def test_refresh_error_is_partial_even_if_cached_research_succeeds(setup):
    kwargs, _ = setup
    kwargs["refresh_fn"] = lambda tickers: {"errors": {"fundamentals:SYN": "unavailable"}}
    out = daily.run_daily(**kwargs)
    assert out["status"] == "partial" and out["refresh"]["errors"]
    assert Path(out["report"]).exists()


def test_preview_has_no_writes_refresh_or_models(setup):
    kwargs, events = setup
    out = daily.run_daily(preview=True, **kwargs)
    assert out["status"] == "preview" and out["macro_research"] == "not_run"
    assert events == ["capture"]
    assert not kwargs["root"].exists() and not kwargs["control_dir"].exists()


def test_company_pilot_reaches_daily_archive_without_legacy_price(setup):
    from tests.valuation.test_company_model import packet, STAMP as COMPANY_STAMP
    kwargs, events = setup
    now = datetime.fromisoformat(COMPANY_STAMP)
    frozen = make_snapshot({'selected_tickers': ['SYN'], 'valuation': {'company_models': {'SYN': packet()}}}, now=now)
    kwargs['now'] = now
    kwargs['capture_fn'] = lambda **kw: {'snapshot': frozen, 'snapshots': {r: frozen for r in ('macro', 'technical', 'risk', 'valuation')}, 'summary': {}}
    out = daily.run_daily(**kwargs)
    assert out['status'] == 'completed', out
    assert out['coverage'] == {'total': 1, 'computed': 0, 'publishable': 0, 'company_research': 1}
    assert read_json(out['receipt'])['tickers']['SYN']['company_research']['observed']['status'] == 'reconciled'
    assert 'company-v5.2-research' in Path(out['report']).read_text()


def test_day_is_singapore_and_no_market_holiday_gate(setup):
    kwargs, _ = setup
    kwargs["now"] = datetime(2026, 9, 6, 23, tzinfo=timezone.utc)
    out = daily.run_daily(**kwargs)
    assert out["day_sgt"] == "2026-09-07" and out["status"] == "completed"


def test_invalid_calculation_hash_is_not_published(setup):
    kwargs, _ = setup
    kwargs["workflow_fn"] = lambda *a, **k: {"valuation_calculations": {
        "receipt_hash": "invalid", "authority": "analysis_only", "tickers": {}}}
    out = daily.run_daily(**kwargs)
    assert out["status"] == "failed" and "report" not in out


def test_empty_universe_is_explicit_failure(setup):
    kwargs, events = setup
    out = daily.run_daily(**{**kwargs, "universe_fn": lambda: []})
    assert out["status"] == "failed" and "empty_valuation_universe" in out["error"]
    assert events == []


def test_invalid_mode_never_runs(monkeypatch):
    monkeypatch.setattr(daily, "run_daily", lambda **k: pytest.fail("unexpected run"))
    with pytest.raises(SystemExit):
        daily.main(["--preview", "--retry"])
