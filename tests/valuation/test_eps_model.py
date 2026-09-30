from datetime import date, timedelta

import pytest

from quant.valuation import eps_model as em

S = ("downside", "base", "upside")


def quarter(i, revenue=100.0, op=20.0, start=date(2026, 7, 1)):
    begin = start + timedelta(days=91 * i)
    return {"id": f"Q{i}", "start": begin.isoformat(), "end": (begin + timedelta(days=90)).isoformat(),
            "revenue": {s: revenue for s in S}, "operating_profit": {s: op for s in S},
            "other_pretax": {s: 0.0 for s in S}, "tax_rate": {s: 0.2 for s in S},
            "other_after_tax": {s: 0.0 for s in S}, "special_expense": {s: 0.0 for s in S},
            "diluted_shares": {s: 10.0 for s in S}}


def model(n=8, **kw):
    return {"ticker": "SYN", "schema": 1, "basis": "gaap", "known_at": "2026-09-01T00:00:00+00:00",
            "origin": "seed:company_package@x", "reviewed": True, "last_reported_end": "2026-06-30",
            "sources": [], "notes": [], "history": [], "quarters": [quarter(i, **kw) for i in range(n)]}


def test_ntm_eps_and_special_addback():
    m = model()
    m["quarters"][0]["operating_profit"] = {s: 10.0 for s in S}
    m["quarters"][0]["special_expense"] = {s: 10.0 for s in S}   # 已在营业利润里, 无税盾回加
    n = em.ntm(m)
    # 净利 = 3×16 + 8 = 56; 回加 10 → 66 / 10 股
    assert abs(n["eps"]["base"] - 6.6) < 1e-9 and abs(n["special_addback_per_share"]["base"] - 1.0) < 1e-9
    assert n["first_quarter_end"] == m["quarters"][0]["end"] and n["earnings_basis"] == "gaap"
    assert em.ntm(model(n=3)) is None


def test_seed_reproduces_company_rows():
    rows = [{"id": f"Q{i}", "start": q["start"], "end": q["end"], "revenue": 100.0, "operating_profit": 20.0,
             "pretax": 22.0, "tax_expense": 4.4, "parent_income": 17.0, "gaap_diluted_shares": 10.0}
            for i, q in enumerate(model()["quarters"])]
    research = {"scenarios": {s: {"quarters": rows} for s in S}}
    package = {"known_at": "k", "scenarios": [{"id": s, "quarters": [
        {"id": "Q0", "cash": {"income_cash_timing": {"legal": {"expense": 5.0, "expense_is_in_ebit": True}}}}]}
        for s in S]}
    m = em.seed_from_research("SYN", research, package, known_at="now")
    assert all(abs(em.quarter_income(q, "base") - 17.0) < 1e-9 for q in m["quarters"])
    assert m["quarters"][0]["special_expense"]["base"] == 5.0 and m["last_reported_end"] == "2026-06-30"
    assert em.seed_from_research("SYN", {"scenarios": {"base": {"quarters": rows}}}, package, known_at="n") is None


def test_roll_pops_reported_quarter_scales_and_appends():
    m = model()
    first_end = m["quarters"][0]["end"]
    reported = [{"period_end": "2026-06-29", "revenue": 90.0},
                {"period_end": (date.fromisoformat(first_end) - timedelta(days=2)).isoformat(), "revenue": 110.0}]
    new, events = em.roll(m, reported, now="2026-10-01T00:00:00+00:00")
    assert [e["quarter"] for e in events] == ["Q0"] and abs(events[0]["ratio"] - 1.1) < 1e-9
    assert len(new["quarters"]) == 8 and new["quarters"][0]["id"] == "Q1"
    assert abs(new["quarters"][0]["revenue"]["base"] - 110.0) < 1e-9          # 超预期 10% 延续
    assert abs(new["quarters"][0]["operating_profit"]["base"] - 22.0) < 1e-9  # 利润率不变
    appended = new["quarters"][-1]
    assert appended["approx_dates"] and appended["start"] > new["quarters"][-2]["end"]
    assert new["reviewed"] is False and new["origin"].startswith("roll:") and new["history"]
    assert m["quarters"][0]["id"] == "Q0", "不改入参"
    same, none = em.roll(new, reported)
    assert none == [] and same["quarters"] == new["quarters"], "同一份财报不重复滚"


def test_roll_ignores_unmatched_dates_and_clamps_ratio():
    m = model()
    assert em.roll(m, [{"period_end": "2026-12-31", "revenue": 500.0}])[1] == []
    first_end = m["quarters"][0]["end"]
    _, events = em.roll(m, [{"period_end": first_end, "revenue": 1000.0}])
    assert events[0]["ratio"] == em.RATIO_CLAMP[1]


def test_set_quarter_and_review_require_primary_source():
    m = model()
    em.set_quarter(m, "Q1", revenue=[90, 100, 110], op_margin=[0.1, 0.2, 0.3])
    assert m["quarters"][1]["operating_profit"]["upside"] == pytest.approx(33.0)
    with pytest.raises(ValueError):
        em.set_quarter(m, "nope", revenue=[1, 2, 3])
    with pytest.raises(ValueError):
        em.review(m, sources=[], note="x")
    em.review(m, sources=["https://www.sec.gov/x"], note="guide", now="2026-10-02T00:00:00+00:00")
    assert m["reviewed"] and m["origin"] == "review:2026-10-02" and m["sources"] == ["https://www.sec.gov/x"]


def test_save_load_round_trip(tmp_path):
    path = tmp_path / "eps.json"
    em.save({"SYN": model()}, path)
    assert em.load(path)["SYN"]["ticker"] == "SYN"
    assert em.load(tmp_path / "missing.json") == {}


def test_research_target_uses_slim_model_and_flags_unreviewed():
    from tests.valuation.test_research_target import AS_OF, closes, quarters
    from quant.valuation import research_target as rt
    m = model(revenue=100.0, op=62.5)          # 净利 62.5×0.8 = 50/季, 四季 200 ÷ 10 股 = EPS 20
    m["reviewed"], m["origin"] = False, "roll:2026-09-27"
    r = rt.research_target("SYN", company_research=None, input_state="current", quarters=quarters([5.0] * 19),
                           price=400.0, closes=closes(lambda d: 400.0), as_of=AS_OF, eps_model=m)
    assert r["status"] == "ok" and abs(r["eps"]["eps"]["base"] - 20.0) < 1e-9
    assert "eps_model_unreviewed" in r["flags"] and r["input_state"] == "eps_model:roll:2026-09-27"
