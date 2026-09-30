"""Dedicated scheduling boundaries, synthetic evidence and fake workers only."""
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import json
import pytest

from quant.core.io_json import read_json
from quant.ops import valuation_daily as daily
from quant.ops.agent_health import acquire_job, release_job
from quant.ops.agent_snapshots import make_snapshot
from tests.valuation.test_assessment import data, STAMP


@pytest.fixture(autouse=True)
def _isolated_state_lock(tmp_path, monkeypatch):
    """research_snapshot 取共享 state 锁; 离线测试不许在仓库 var/ 下留锁文件。"""
    from quant.core import io_json
    monkeypatch.setattr(io_json, "STATE_LOCK_PATH", tmp_path / ".state.lock")


@pytest.fixture
def setup(tmp_path):
    events = []
    now = datetime.fromisoformat(STAMP.replace("Z", "+00:00"))
    frozen = make_snapshot(data(), now=now)

    def capture(**kwargs):
        events.append("capture")
        return {"snapshot": frozen, "snapshots": {r: frozen for r in
                ("technical", "risk", "valuation")}, "summary": {"scope": "portfolio"}}

    def refresh(tickers):
        events.append("refresh")
        assert tickers == ["SYN"]
        return {"errors": {}}

    def worker(role, task, snapshot, **kwargs):
        events.append(role)
        assert kwargs["review"] is False
        assert kwargs["control_dir"] == tmp_path / "control"
        assert role == "valuation"                       # 没有宏观模型阶段
        return {"role": role, "status": "succeeded", "run_id": role,
                "result": {"summary": "synthetic", "requests": []}}

    return dict(root=tmp_path / "daily", run_dir=tmp_path / "agents",
                control_dir=tmp_path / "control", now=now,
                universe_fn=lambda: ["SYN"], refresh_fn=refresh, capture_fn=capture,
                routes={"version": 1, "limits": {"max_calls": 4}, "roles": {}},
                run_role_fn=worker), events


def test_independent_daily_sequence_and_archived_receipt(setup):
    kwargs, events = setup
    out = daily.run_daily(**kwargs)
    assert events == ["refresh", "capture", "valuation"]
    assert out["status"] == "completed"
    assert out["coverage"] == {"total": 1, "computed": 1, "publishable": 0,
                               "company_research": 0, "conditional_operating": 0,
                               "operating_path_unestimable": 0, "expired_inputs": 0, "unestimable": 0}
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
    assert out["status"] == "preview" and "macro_research" not in out
    assert events == ["capture"]
    assert not kwargs["root"].exists() and not kwargs["control_dir"].exists()


def test_company_pilot_reaches_daily_archive_without_legacy_price(setup):
    from quant.valuation.company_model import VERSION
    from tests.valuation.test_company_model import packet, STAMP as COMPANY_STAMP
    kwargs, events = setup
    now = datetime.fromisoformat(COMPANY_STAMP)
    frozen = make_snapshot({'selected_tickers': ['SYN'], 'valuation': {'company_models': {'SYN': packet()}}}, now=now)
    kwargs['now'] = now
    kwargs['capture_fn'] = lambda **kw: {'snapshot': frozen, 'snapshots': {r: frozen for r in ('technical', 'risk', 'valuation')}, 'summary': {}}
    out = daily.run_daily(**kwargs)
    assert out['status'] == 'completed', out
    assert out['coverage'] == {'total': 1, 'computed': 0, 'publishable': 0,
                               'company_research': 1, 'conditional_operating': 0,
                               'operating_path_unestimable': 0, 'expired_inputs': 0, 'unestimable': 0}
    assert read_json(out['receipt'])['tickers']['SYN']['company_research']['observed']['status'] == 'reconciled'
    assert VERSION in Path(out['report']).read_text()


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


def test_price_cache_status_distinguishes_current_from_missing_stale_or_future(tmp_path):
    import json
    path = tmp_path / "SYN.jsonl"
    check = lambda: daily.price_cache_status("SYN", expected_session="2026-09-25", cache_dir=tmp_path)
    assert check()["status"] == "unavailable"
    for day, value in (("2026-09-24", 20), ("2026-09-28", 20), ("2026-09-25", 0)):
        path.write_text(json.dumps({"time": day + " 00:00:00", "close": value}) + "\n")
        assert check()["status"] == "unavailable"
    path.write_text(json.dumps({"time": "2026-09-25 00:00:00", "close": 20}) + "\n")
    assert check()["status"] == "current"


def test_zero_new_bars_is_healthy_only_with_current_completed_close(monkeypatch):
    from quant.valuation import fundamentals
    from quant.data import kline_cache
    monkeypatch.setattr(fundamentals, "refresh", lambda t: {"refreshed": t, "errors": {}})
    monkeypatch.setattr(kline_cache, "update_ticker", lambda *a: 0)
    monkeypatch.setattr(daily, "price_cache_status", lambda ticker, **k: {
        "status": "current" if ticker == "CURRENT" else "unavailable", "cached_session": None})
    out = daily.refresh_inputs(["CURRENT", "STALE"])
    assert out["prices"] == {"CURRENT": 0, "STALE": 0}
    assert "prices:CURRENT" not in out["errors"]
    assert "prices:STALE" in out["errors"]


def test_exact_company_release_is_archived_separately_from_diagnostic_hold(setup):
    from tests.valuation.test_company_model import packet, STAMP as COMPANY_STAMP
    from tests.valuation.test_releases import fixture, select
    kwargs, _ = setup
    now = datetime.fromisoformat(COMPANY_STAMP)
    frozen = make_snapshot({'selected_tickers': ['SYN'], 'valuation': {'company_models': {'SYN': packet()}}}, now=now)
    kwargs['now'] = now
    kwargs['capture_fn'] = lambda **kw: {'snapshot': frozen, 'snapshots': {
        r: frozen for r in ('technical', 'risk', 'valuation')}, 'summary': {}}
    accepted = select(*fixture())
    calls = []
    def read_release(ticker, kind, **kw):
        calls.append((ticker, kind, kw['expected_input_hash']))
        return accepted if kind == 'fair_value' else {
            'status': 'withheld', 'ticker': ticker, 'kind': kind, 'reason': 'no_release'}
    out = daily.run_daily(**kwargs, release_fn=read_release)
    assert out['released_counts'] == {'fair_value': 1, 'market_target_12m': 0}
    assert all(h == read_json(out['receipt'])['tickers']['SYN']['company_research']['input_hash'] for _, _, h in calls)
    assert out['publication']['status'] == 'withheld'  # Raw DCF hold stays separate.
    assert read_json(out['publication_receipt'])['SYN']['fair_value']['release_id'] == 'one'
    text = Path(out['report']).read_text()
    assert '条件公允价值区间 $10.00–$30.00' in text
    assert '12个月市场预测区间' not in text


def test_research_forward_ledger_follows_explicit_root(setup):
    kwargs, _ = setup
    out = daily.run_daily(**kwargs)
    ledger = kwargs["root"] / "valuation_forward.jsonl"
    rows = [json.loads(line) for line in ledger.read_text().splitlines()]
    assert [r["ticker"] for r in rows] == ["SYN"] and rows[0]["run_id"] == out["run_id"]
    assert out["research_counts"]["total"] == 1


def test_research_snapshot_rolls_eps_models_and_preview_does_not_write(tmp_path):
    from quant.valuation import eps_model
    from tests.valuation.test_eps_model import model
    path = tmp_path / "eps_models.json"
    eps_model.save({"SYN": model()}, path)
    first_end = model()["quarters"][0]["end"]
    frozen = {"snapshot": {"data": {"valuation": {"fundamentals": {
        "SYN": {"quarters": [{"period_end": first_end, "revenue": 120.0}]}}}}}}
    receipt = {"as_of": "2026-10-01T00:00:00+00:00", "tickers": {}}
    _, text = daily.research_snapshot(receipt, frozen, eps_models_path=path, write=False)
    assert "SYN" in text and eps_model.load(path)["SYN"]["quarters"][0]["id"] == "Q0", "preview 不写盘"
    result, _ = daily.research_snapshot(receipt, frozen, eps_models_path=path)
    assert result["eps_model_rolls"]["SYN"][0]["quarter"] == "Q0"
    assert eps_model.load(path)["SYN"]["quarters"][0]["id"] == "Q1"
