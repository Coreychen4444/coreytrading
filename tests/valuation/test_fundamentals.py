"""fundamentals.py: 三表解析对齐 / capex coalesce / 缓存与新鲜度 / 单票失败不炸整轮。"""
import json
from datetime import datetime, timedelta, timezone

import pytest

from quant.valuation import fundamentals as F
from tests.valuation.conftest import FakeSource, make_reports


def test_parse_quarters_aligns_three_statements_ascending():
    inc, bal, cf = make_reports(n_q=8)
    rows = F.parse_quarters(inc, bal, cf)
    assert [r["period"] for r in rows] == [f"{2014 + i//4}/Q{i%4+1}" for i in range(8)]
    r = rows[-1]
    assert r["revenue"] > rows[0]["revenue"]
    assert r["cash_sti"] == 2e9 and r["lt_debt"] == 5e8 and r["total_equity"] == 3e9
    assert r["fcf"] is not None and r["da"] is not None
    assert r["period_end"].startswith("2015-")


def test_annual_reports_are_excluded_and_restatement_keeps_newest():
    inc, bal, cf = make_reports(n_q=4)
    annual = dict(inc[0], financial_type=7, period_text="2014/FY")
    restated = json.loads(json.dumps(inc[-1]))
    restated["date_time"] += 1
    for it in restated["item_list"]:
        if it["field_id"] == 8001:
            it["data"] = 123.0
    rows = F.parse_quarters(inc + [annual, restated], bal, cf)
    assert len(rows) == 4 and all(r["period"] != "2014/FY" for r in rows)
    assert rows[-1]["revenue"] == 123.0


def test_sdk_named_quarters_match_legacy_captures_and_exclude_cumulative_flows():
    inc, bal, cf = make_reports(n_q=8)
    expected = F.parse_quarters(inc, bal, cf)
    for reports in (inc, bal, cf):
        for report in reports:
            report["financial_type"] = f"Q{report['financial_type']}"
        for kind in ("ANNUAL", "Q6", "Q9", "QUARTERLY", True, 1.0, "1", None):
            reports.append(dict(reports[-1], financial_type=kind, period_text=f"bad-{kind}"))
    assert F.parse_quarters(inc, bal, cf) == expected


def test_capex_coalesce_prefers_direct_then_net_ppe():
    inc, bal, cf = make_reports(n_q=4)
    cf[-1]["item_list"] = [it for it in cf[-1]["item_list"] if it["field_id"] != 8044]   # 只有净 PPE
    cf[-2]["item_list"] = [it for it in cf[-2]["item_list"] if it["field_id"] not in (8044, 8046)]
    rows = F.parse_quarters(inc, bal, cf)
    assert rows[-1]["capex_basis"] == "net_ppe_purchase" and rows[-1]["capex"] is not None
    assert rows[-2]["capex_basis"] is None and rows[-2]["capex"] is None
    assert rows[0]["capex_basis"] == "capital_expenditure"


def test_missing_balance_sheet_quarter_yields_none_not_forward_fill():
    inc, bal, cf = make_reports(n_q=4)
    rows = F.parse_quarters(inc, bal[:-1], cf)
    assert rows[-1]["cash_sti"] is None and rows[-2]["cash_sti"] == 2e9


def test_parse_snapshot_guards_nan_and_maps_fields():
    s = F.parse_snapshot({"last_price": 10.0, "total_market_val": 1e9, "dividend_ratio_ttm": float("nan"),
                          "update_time": "2026-09-01 16:00:00", "outstanding_shares": 1e8})
    assert s["last_price"] == 10.0 and s["total_market_val"] == 1e9 and s["outstanding_shares"] == 1e8
    assert s["dividend_ratio_ttm"] is None and s["update_time"] == "2026-09-01T16:00:00-04:00"
    assert s["pe_ttm"] is None


def test_parse_valuation_and_consensus_handle_empty():
    assert F.parse_valuation({}) is None and F.parse_valuation(None) is None
    assert F.parse_consensus({"total": 0}) is None and F.parse_consensus(None) is None
    v = F.parse_valuation({"valuation_type": "PE", "trend": {"current_value": 1, "average_value": 2}})
    assert v["current"] == 1 and v["average"] == 2 and v["minus_1sd"] is None


def test_fetch_bundle_and_cache_roundtrip(tmp_path):
    src = FakeSource()
    b = F.fetch_bundle("syn", src, quarters=12)
    assert b["ticker"] == "SYN" and len(b["quarters"]) == 12
    assert b["snapshot"]["last_price"] == 50.0 and b["valuation_pe"]["average"] == 20.0 and b["consensus"]["total"] == 10
    assert {c[0] for c in src.calls} == {"statements", "snapshot", "valuation", "consensus"}
    p = F.save_bundle(b, tmp_path)
    assert p == tmp_path / "SYN.json"
    assert F.load_bundle("SYN", cache_dir=tmp_path)["quarters"][-1]["period"] == b["quarters"][-1]["period"]
    assert F.load_bundle("NOPE", cache_dir=tmp_path) is None


def test_bundle_freshness_gate(tmp_path):
    b = F.fetch_bundle("SYN", FakeSource(), quarters=8)
    b["fetched_at"] = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()
    F.save_bundle(b, tmp_path)
    assert F.load_bundle("SYN", cache_dir=tmp_path, max_age_hours=24) is None
    assert F.load_bundle("SYN", cache_dir=tmp_path, max_age_hours=48) is not None
    assert 29 < F.bundle_age_hours(b) < 31


def test_refresh_isolates_single_ticker_failure(tmp_path):
    src = FakeSource(fail={"BAD"})
    out = F.refresh(["SYN", "BAD"], quarters=8, source=src, cache_dir=tmp_path)
    assert out["refreshed"] == ["SYN"] and "BAD" in out["errors"] and "boom" in out["errors"]["BAD"]
    assert (tmp_path / "SYN.json").exists() and not (tmp_path / "BAD.json").exists()
    assert src.closed is False, "外部注入的 source 由调用方关闭"


def test_fetch_bundle_requires_income_statement():
    class Empty(FakeSource):
        def statements(self, code, statement_type, num):
            return []
    with pytest.raises(RuntimeError):
        F.fetch_bundle("SYN", Empty())


def test_capture_vintages_survive_refresh_and_do_not_leak_later_revisions(tmp_path):
    from copy import deepcopy
    first = F.fetch_bundle("SYN", FakeSource(), quarters=8)
    first["fetched_at"] = "2026-08-01T00:00:00Z"
    F.save_bundle(first, tmp_path)
    revised = deepcopy(first)
    revised["fetched_at"] = "2026-09-01T00:00:00Z"
    revised["quarters"][-1]["revenue"] *= 2
    F.save_bundle(revised, tmp_path)
    F.save_bundle(revised, tmp_path)
    assert len(list((tmp_path / "snapshots" / "SYN").glob("*.json"))) == 2
    assert F.load_vintage("SYN", as_of="2026-08-15T00:00:00Z", cache_dir=tmp_path) == first
    assert F.load_vintage("SYN", as_of="2026-07-15T00:00:00Z", cache_dir=tmp_path) is None
    assert F.load_bundle("SYN", cache_dir=tmp_path) == revised


def test_parser_rejects_boolean_infinite_fields_and_preserves_unverified_period_metadata():
    inc, bal, cf = make_reports(n_q=4)
    bal[-1]["item_list"] = [{"field_id": 8003, "data": True}, {"field_id": 8058, "data": float("inf")}]
    row = F.parse_quarters(inc, bal, cf)[-1]
    assert row["cash_sti"] is None and row["current_debt"] is None
    assert row["available_at"] is None and row["provider_period_timestamp"]
    assert "unverified" in row["period_end_basis"]
    assert F.parse_snapshot({"update_time": "2026-01-05 16:00:00"})["update_time"].endswith("-05:00")


def test_annotate_availability_uses_archive_then_calendar(tmp_path):
    """季度可知日 = 财报反应日。存档按 period_text；日历 last_print 只给最近一个未匹配季度。"""
    import json as _json
    from quant.valuation import fundamentals as F
    (tmp_path / "SYN.json").write_text(_json.dumps({"code": 0, "data": [
        {"period_text": "2026/Q3", "pub_trading_day_str": "2026-06-24", "trading_day_str": "2026-06-25"}]}))
    cal = {"tickers": {"SYN": {"last_print": {"pub_day": "2026-09-30", "reaction_day": "2026-10-01"}}}}
    dates = F.report_dates("SYN", deep_dir=tmp_path, calendar=cal)
    rows = [{"period": "2026/Q2", "period_end": "2026-02-25", "available_at": None},
            {"period": "2026/Q3", "period_end": "2026-05-27", "available_at": None},
            {"period": "2026/Q4", "period_end": "2026-09-02", "available_at": None}]
    F.annotate_availability(rows, dates, fetched_day="2026-10-10")
    assert [r["available_at"] for r in rows] == [None, "2026-06-25", "2026-10-01"]
    assert rows[2]["available_at_basis"] == "earnings_calendar:last_print"
    later = [{"period": "2026/Q4", "period_end": "2026-09-02", "available_at": None}]
    F.annotate_availability(later, dates, fetched_day="2026-09-30")       # 反应日在抓取日之后 → 不填
    assert later[0]["available_at"] is None
