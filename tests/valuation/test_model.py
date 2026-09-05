"""model.py: 驱动推导 / 路径 / DCF 闭式对账 / 合理 PE / 反向解 / 判定与置信 / β 回归 / 全流程。"""
import math

import pytest

from quant.valuation import archetypes as A
from quant.valuation import model as M
from tests.valuation.conftest import make_bundle, make_klines


def _params(name):
    p = dict(A.ARCHETYPES[name])
    p.pop("label")
    return p


# ── 驱动 ────────────────────────────────────────────────────────────

def test_ttm_requires_all_four_quarters():
    rows = [{"x": 1.0}] * 3 + [{"x": None}] + [{"x": 2.0}] * 4
    assert M.ttm_at(rows, 2, "x") is None
    assert M.ttm_at(rows, 3, "x") is None
    assert M.ttm_at(rows, 7, "x") == 8.0


def test_derive_drivers_recovers_synthetic_truth():
    b = make_bundle(g_annual=0.10, margin=0.30, tax=0.21, shares=100e6)
    d = M.derive_drivers(b, _params("platform"))
    q = b["quarters"]
    assert d["revenue_ttm"] == pytest.approx(sum(r["revenue"] for r in q[-4:]))
    assert d["growth_candidates"]["yoy"] == pytest.approx(0.10, abs=1e-6)
    assert d["growth_candidates"]["cagr3"] == pytest.approx(0.10, abs=1e-6)
    assert d["growth_candidates"]["cagr10"] == pytest.approx(0.10, abs=1e-6)
    assert d["opm_ttm"] == pytest.approx(0.30) and d["opm_median"] == pytest.approx(0.30)
    assert d["tax_rate"] == pytest.approx(0.21, abs=1e-6) and d["tax_basis"].startswith("TTM")
    assert d["shares"] == pytest.approx(100e6, rel=1e-6) and d["shares_basis"].startswith("近 4 季")
    assert d["net_cash_ex_leases"] == pytest.approx(2e9 - 5e8)
    assert d["oneoff_ttm"] == pytest.approx(0.0, abs=1e-3) and d["oneoff_share_of_pretax"] == pytest.approx(0.0, abs=1e-9)
    assert d["eps_core_ttm"] == pytest.approx(d["eps_reported_ttm"], rel=1e-9)
    assert M.S2C_CLAMP[0] <= d["sales_to_capital"] <= M.S2C_CLAMP[1]
    assert d["invested_capital"] == pytest.approx(3e9 + 5e8 - 2e9)
    assert d["buyback_yield"] > 0 and d["share_change_rate"] > 0
    assert d["flags"] == []


def test_goodwill_is_excluded_from_reinvestment_capital_but_kept_in_roic():
    plain = M.derive_drivers(make_bundle(), _params("platform"))
    gw = M.derive_drivers(make_bundle(equity=3e9 + 5e9, goodwill=5e9), _params("platform"))
    assert gw["invested_capital"] == pytest.approx(plain["invested_capital"] + 5e9)
    assert gw["invested_capital_ex_goodwill"] == pytest.approx(plain["invested_capital"])
    assert gw["sales_to_capital"] == pytest.approx(plain["sales_to_capital"])
    assert gw["roic_ttm"] < plain["roic_ttm"] and gw["roic_ex_goodwill"] == pytest.approx(plain["roic_ex_goodwill"])


def test_history_start_filters_and_too_short_history_raises():
    b = make_bundle(n_q=20)
    rows = M.filter_history(b["quarters"], "2017-01-01")
    assert all(r["period_end"] >= "2017-01-01" for r in rows) and len(rows) < 20
    with pytest.raises(ValueError):
        M.derive_drivers({"quarters": b["quarters"][:3], "snapshot": {}}, _params("platform"))


def test_oneoff_is_residual_of_pretax_over_operating_and_interest():
    b = make_bundle(n_q=8)
    for r in b["quarters"][-4:]:
        r["pretax"] += 5e8          # 税前多出 5 亿 = 非经营损益
        r["net_income"] += 5e8 * 0.79
    d = M.derive_drivers(b, _params("platform"))
    assert d["oneoff_ttm"] == pytest.approx(2e9)
    assert d["eps_core_ttm"] < d["eps_reported_ttm"]
    assert any("报表 EPS 失真" in f for f in d["flags"])


def test_tax_defaults_when_pretax_negative():
    b = make_bundle(margin=-0.2)
    d = M.derive_drivers(b, _params("ai_infra_capex"))
    assert d["tax_rate"] == M.DEFAULT_TAX and "税率用缺省" in d["flags"]


# ── 路径与引擎 ──────────────────────────────────────────────────────

def test_growth_and_margin_paths():
    lin = M.growth_path(0.20, 0.03, 10)
    assert lin[0] == pytest.approx(0.20) and lin[-1] == pytest.approx(0.03) and all(a >= b for a, b in zip(lin, lin[1:]))
    ex = M.growth_path(0.60, 0.03, 10, fade="exp", half_life=2.0)
    assert ex[0] == pytest.approx(0.60) and ex[2] == pytest.approx(0.03 + 0.57 * 0.5) and ex[-1] > 0.03
    assert M.growth_path(0.1, 0.03, 5, fade="flat") == [0.1] * 5
    assert M.growth_path(0.1, 0.03, 1) == [0.1]
    mp = M.margin_path(-0.1, 0.25, 6, 5)
    assert mp[0] == pytest.approx(-0.03) and mp[4] == pytest.approx(0.25) and mp[5] == pytest.approx(0.25)


def _flat_spec(**kw):
    s = {"revenue_base": 1e9, "growth_start": 0.0, "terminal_growth": 0.0, "horizon_years": 10, "fade": "linear",
         "half_life": None, "margin_start": 0.30, "target_margin": 0.30, "margin_fade_years": 1, "tax_rate": 0.21,
         "sales_to_capital": 2.0, "shares": 1e6, "share_change_rate": 0.0, "net_interest_after_tax": 0.0, "dps": 0.0,
         "wacc": 0.10, "cost_of_equity": 0.10, "ronic_terminal": 0.10}
    s.update(kw)
    return s


def test_project_compounds_and_reinvests():
    s = _flat_spec(growth_start=0.10, terminal_growth=0.10, sales_to_capital=2.0, share_change_rate=0.02)
    p = M.project(s)
    assert p[0]["revenue"] == pytest.approx(1.1e9) and p[1]["revenue"] == pytest.approx(1.21e9)
    assert p[0]["reinvest"] == pytest.approx(0.1e9 / 2.0)
    assert p[0]["fcf"] == pytest.approx(p[0]["nopat"] - p[0]["reinvest"])
    assert p[0]["nopat"] == pytest.approx(1.1e9 * 0.30 * 0.79)
    assert p[1]["shares"] == pytest.approx(1e6 * 0.98 ** 2)


def test_dcf_zero_growth_equals_perpetuity_closed_form():
    p = M.project(_flat_spec())
    nopat = 1e9 * 0.30 * 0.79
    r = M.dcf_value(p, wacc=0.10, terminal_growth=0.0, ronic_terminal=0.10, cash_sti=0, lt_investments=0, debt=0, shares=1e6)
    mid = 1.10 ** M.MIDYEAR
    assert r["ev"] == pytest.approx(nopat / 0.10 * mid, rel=1e-9)         # 中年折现 = 年末折现 × (1+r)^0.5
    assert r["per_share"] == pytest.approx(nopat / 0.10 * mid / 1e6, rel=1e-9)
    assert r["effective_duration"] == pytest.approx(10.0 - M.MIDYEAR, rel=0.02)   # 永续: 有效久期 ≈ 1/r − 0.5
    assert r["rate_sensitivity_100bp"] == pytest.approx((0.10 / 0.11) * (1.11 / 1.10) ** M.MIDYEAR - 1, rel=1e-6)
    assert 0 < r["tv_share"] < 1 and r["duration_years"] > 0


def test_equity_bridge_adds_cash_and_investments_subtracts_debt():
    p = M.project(_flat_spec())
    base = M.dcf_value(p, wacc=0.10, terminal_growth=0.0, ronic_terminal=0.10, cash_sti=0, lt_investments=0, debt=0, shares=1e6)
    br = M.dcf_value(p, wacc=0.10, terminal_growth=0.0, ronic_terminal=0.10, cash_sti=5e8, lt_investments=1e8, debt=2e8, shares=1e6)
    assert br["equity"] - base["equity"] == pytest.approx(4e8)


def test_justified_pe_formulas():
    assert M.justified_pe(0.03, 0.15, 0.10) == pytest.approx((1 - 0.2) * 1.03 / 0.07)
    assert M.justified_pe(0.10, 0.15, 0.10) is None
    p = M.project(_flat_spec())
    assert M.path_justified_pe(p, 3, cost_of_equity=0.10, terminal_growth=0.0, ronic_terminal=0.10) == pytest.approx(10.0, rel=1e-9)   # EPS 引擎仍年末折现


def test_eps_pe_value_uses_history_and_degenerate_band_clamp():
    p = M.project(_flat_spec(growth_start=0.05, terminal_growth=0.03))
    hist = {"average": 20.0, "minus_1sd": 16.0, "plus_1sd": 24.0}
    r = M.eps_pe_value(p, cost_of_equity=0.10, terminal_growth=0.03, ronic_terminal=0.15, pe_hist=hist)
    assert r["pe_T"] == 20.0 and r["pe_bull"] == 24.0 and r["pe_bear"] >= 16.0
    assert r["per_share"] == pytest.approx(r["eps_T"] * 20.0 / 1.1 ** 3)
    bad = {"average": 13.0, "minus_1sd": -27.0, "plus_1sd": 54.0}
    r2 = M.eps_pe_value(p, cost_of_equity=0.10, terminal_growth=0.03, ronic_terminal=0.15, pe_hist=bad)
    assert "退化" in r2["pe_basis"] and r2["pe_bull"] == pytest.approx(26.0)
    r3 = M.eps_pe_value(p, cost_of_equity=0.10, terminal_growth=0.03, ronic_terminal=0.15, pe_hist=None)
    assert "路径合理" in r3["pe_basis"] and r3["pe_T"] > 0
    neg = M.project(_flat_spec(margin_start=-0.3, target_margin=-0.3))
    assert M.eps_pe_value(neg, cost_of_equity=0.10, terminal_growth=0.03, ronic_terminal=0.15, pe_hist=hist) is None


def test_implied_growth_and_margin_recover_known_inputs():
    spec = _flat_spec(terminal_growth=0.03, ronic_terminal=0.15, sales_to_capital=3.0)

    def ps(s):
        return M.dcf_value(M.project(s), wacc=s["wacc"], terminal_growth=s["terminal_growth"],
                           ronic_terminal=s["ronic_terminal"], cash_sti=0, lt_investments=0, debt=0, shares=s["shares"])["per_share"]

    target = ps({**spec, "fade": "flat", "growth_start": 0.12})
    assert M.implied_growth(target, spec, ps) == pytest.approx(0.12, abs=1e-6)
    target_m = ps({**spec, "target_margin": 0.42})
    assert M.implied_margin(target_m, spec, ps) == pytest.approx(0.42, abs=1e-6)
    assert M.implied_param(1e12, spec, ps, "growth_start", -0.5, 3.0) is None


def test_overlay_applies_additive_and_multiplicative():
    s = _flat_spec(growth_start=0.10, revenue_base=100.0, target_margin=0.2)
    p = M.apply_overlay(s, [{"driver": "growth_start", "delta": 0.05}, {"driver": "revenue_base", "delta": 0.10},
                            {"driver": "wacc", "delta": -0.01}])
    assert p["growth_start"] == pytest.approx(0.15) and p["revenue_base"] == pytest.approx(110.0) and p["wacc"] == pytest.approx(0.09)
    assert s["growth_start"] == 0.10, "不改入参"


# ── 判定 / 置信 / β / 资本成本 ─────────────────────────────────────

@pytest.mark.parametrize("ratio, label", [(-0.30, "低估"), (-0.15, "偏低"), (0.0, "合理"), (0.15, "偏高"), (0.30, "高估")])
def test_verdict_bands(ratio, label):
    assert M.verdict(100.0 * (1 + ratio), 100.0)["label"] == label


def test_verdict_undefined_when_fair_nonpositive():
    assert M.verdict(100.0, 0.0)["label"] == "无法判定" and M.verdict(100.0, None)["ratio"] is None


def test_confidence_deductions():
    hi = M.confidence(2, tv_share=0.5, fcf_ttm=1.0, quarters=40, dcf_positive=True)
    assert hi["tier"] == "高" and hi["reasons"] == []
    lo = M.confidence(2, tv_share=0.9, fcf_ttm=-1.0, quarters=8, dcf_positive=False, opm_percentile=0.99)
    assert lo["tier"] == "低" and len(lo["reasons"]) == 5
    assert M.confidence(1, tv_share=0.5, fcf_ttm=1.0, quarters=40, dcf_positive=True, opm_percentile=0.97)["tier"] == "低"


def test_beta_regression_recovers_slope_and_needs_enough_overlap():
    tk, spy = make_klines(n=260, beta=1.5)
    b = M.beta_from_klines(tk, spy)
    assert b["raw"] == pytest.approx(1.5, abs=1e-9)
    assert b["adjusted"] == pytest.approx(0.67 * 1.5 + 0.33, abs=1e-9)
    assert b["n_obs"] == M.BETA_MAX_OBS
    assert M.beta_from_klines(tk[:100], spy) is None


def test_cost_of_capital_clamps_beta_and_floors_wacc():
    d = {"tax_rate": 0.21, "debt": 0.0, "net_interest_ttm": 1e6, "mcap": 1e10}
    c = M.cost_of_capital(d, rf=0.0475, erp=0.045, beta={"raw": 3.4, "adjusted": 2.6, "n_obs": 250, "window": "w"}, terminal_growth=0.03)
    assert c["beta"] == M.BETA_CLAMP[1] and "夹逼" in c["beta_basis"]
    assert c["cost_of_equity"] == pytest.approx(0.0475 + M.BETA_CLAMP[1] * 0.045) and c["wacc"] == pytest.approx(c["cost_of_equity"])
    mixed = M.cost_of_capital(d, rf=0.0475, erp=0.045, beta={"raw": 1.0, "adjusted": 1.0, "n_obs": 60, "window": "w"},
                              terminal_growth=0.03, industry_beta=1.52)
    assert mixed["beta"] == 1.52 and "交叉: 个股 5 年月度回归 1.00" in mixed["beta_basis"], "v3: 行业 β 为准, 回归只交叉"
    ind = M.cost_of_capital(d, rf=0.0475, erp=0.045, beta=None, terminal_growth=0.03, industry_beta=1.52)
    assert ind["beta"] == 1.52 and "行业 β" in ind["beta_basis"]
    big = M.cost_of_capital(d, rf=0.0475, erp=0.045, beta=None, terminal_growth=0.03, industry_beta=2.5)
    assert big["beta"] == M.BETA_CLAMP[1]
    c2 = M.cost_of_capital(d, rf=0.0475, erp=0.045, beta=None, overrides={"wacc": 0.09}, terminal_growth=0.03)
    assert c2["wacc"] == 0.09 and c2["wacc_basis"] == "override" and c2["beta_basis"].startswith("缺省")
    lev = M.cost_of_capital({"tax_rate": 0.21, "debt": 5e9, "net_interest_ttm": -2.5e8, "mcap": 5e9}, rf=0.04, erp=0.045,
                            beta={"raw": 1.0, "adjusted": 1.0, "n_obs": 250, "window": "w"})
    assert lev["cost_of_debt"] == pytest.approx(0.05) and lev["debt_weight"] == pytest.approx(0.5)
    assert lev["wacc"] < lev["cost_of_equity"]


# ── 全流程 ──────────────────────────────────────────────────────────

def _resolved(name, **entry):
    doc = {"tickers": {"SYN": {"archetype": name, "rationale": "合成", **entry}}}
    return A.resolve("SYN", doc)


def test_valuate_platform_end_to_end_is_internally_consistent():
    b = make_bundle(price=50.0)
    tk, spy = make_klines(beta=1.2)
    res = M.valuate(b, _resolved("platform"), rf=0.045, erp=0.045, beta=M.beta_from_klines(tk, spy))
    sc = res["scenarios"]
    assert res["fair_value"] > 0 and sc["bear"] <= sc["base"] <= sc["bull"]
    assert res["fair_value"] == pytest.approx(0.25 * sc["bear"] + 0.5 * sc["base"] + 0.25 * sc["bull"])
    assert res["verdict"]["label"] in ("低估", "偏低", "合理", "偏高", "高估")
    near = M.valuate(b, _resolved("platform"), rf=0.045, erp=0.045, beta=None, price=res["fair_value"] * 0.9)
    assert near["implied"]["cagr"] is not None and near["implied"]["target_margin"] is not None
    assert res["base"]["weights"] == {"fcf_dcf": 0.5, "street": 0.5}
    assert res["base"]["dcf"]["per_share"] > 0 and res["base"]["eps_pe"]["per_share"] > 0
    st = res["base"]["street"]
    assert st["target_avg"] == 60.0 and st["per_share"] == pytest.approx(60.0 / (1 + res["cost_of_capital"]["cost_of_equity"]))
    assert res["scenarios"]["bear"] <= res["scenarios"]["base"] <= res["scenarios"]["bull"]
    assert res["anchor"]["dcf_vs_street"] == pytest.approx(res["base"]["dcf"]["per_share"] / st["per_share"] - 1)
    assert res["growth_basis"].startswith("均(") and res["margin_basis"] == "维持第 1 年利润率"
    assert res["anchor"]["consensus_revenue"] is False and res["spec"]["growth_year1"] == pytest.approx(res["anchor"]["growth_1"])
    assert res["spec"]["horizon_years"] == 10 and len(res["base"]["path"]) == 10
    for k in ("revenue_ttm", "opm_ttm", "shares", "sales_to_capital"):
        assert k in res["drivers"]


def test_valuate_price_scales_verdict_monotonically():
    b = make_bundle(price=50.0)
    fair = M.valuate(b, _resolved("platform"), rf=0.045, erp=0.045, beta=None, price=50.0)["fair_value"]
    cheap = M.valuate(b, _resolved("platform"), rf=0.045, erp=0.045, beta=None, price=fair * 0.5)
    dear = M.valuate(b, _resolved("platform"), rf=0.045, erp=0.045, beta=None, price=fair * 2.0)
    assert cheap["verdict"]["label"] == "低估" and dear["verdict"]["label"] == "高估"
    assert cheap["fair_value"] == pytest.approx(dear["fair_value"]) == pytest.approx(fair)
    assert cheap["implied"]["cagr"] < dear["implied"]["cagr"]
    assert cheap["implied"]["target_margin"] < dear["implied"]["target_margin"]
    assert M.valuate(b, _resolved("platform"), rf=0.045, erp=0.045, beta=None, price=0.01)["implied"]["cagr"] is None


def test_cyclical_normalizes_to_median_and_flags_peak():
    n = 50
    margins = [0.10] * (n - 4) + [0.60] * 4          # 最后一年利润率飙到 60%
    b = make_bundle(margin_series=margins)
    res = M.valuate(b, _resolved("semi_cyclical"), rf=0.045, erp=0.045, beta=None)
    assert res["target_margin"] == pytest.approx(res["drivers"]["opm_median"])
    assert res["margin_basis"].startswith("近 20 季")
    assert any("周期峰值" in f for f in res["flags"])
    assert res["base"]["path"][5]["margin"] == pytest.approx(res["drivers"]["opm_median"])   # 5 年回归到位 (年 6 起)
    assert res["base"]["path"][0]["margin"] > res["base"]["path"][1]["margin"] > res["drivers"]["opm_median"]
    assert res["confidence"]["tier"] != "高"


def test_overrides_and_overlay_flow_into_spec():
    b = make_bundle()
    res = M.valuate(b, _resolved("platform", overrides={"target_margin": 0.4, "wacc": 0.08, "horizon_years": 6,
                                                        "probabilities": {"bear": 0.2, "base": 0.6, "bull": 0.2}},
                                news_overlay=[{"date": "2026-09-01", "source": "s", "driver": "growth_start", "delta": 0.02, "note": "n"}]),
                    rf=0.045, erp=0.045, beta=None)
    assert res["spec"]["target_margin"] == 0.4 and res["spec"]["wacc"] == 0.08 and res["spec"]["horizon_years"] == 6
    assert res["spec"]["growth_start"] == pytest.approx(res["growth_start"] + 0.02)
    assert res["scenarios"]["probabilities"] == {"bear": 0.2, "base": 0.6, "bull": 0.2}
    assert res["margin_basis"] == "override"


def test_not_applicable_is_skipped_without_math():
    res = M.valuate({"ticker": "NA1", "quarters": []}, _resolved("not_applicable"), rf=0.045, erp=0.045, beta=None)
    assert res["skipped"] and "not_applicable" in res["reason"]


def test_loss_making_infra_gets_low_confidence_not_a_crash():
    margins = [-0.8 + 0.06 * i for i in range(12)]
    b = make_bundle(n_q=12, start_year=2023, margin_series=margins, cash=1e9, debt=2e9, capex_ratio=0.9, price=100.0)
    res = M.valuate(b, _resolved("ai_infra_capex"), rf=0.045, erp=0.045, beta=None)
    assert res["base"]["weights"]["street"] == 0.5 and res["base"]["street"] is not None
    assert res["confidence"]["tier"] == "低"
    assert res["margin_basis"].startswith("原型缺省")
    assert math.isfinite(res["scenarios"]["base"])


# ── 增量新闻 overlay 四段应用 ────────────────────────────────────────

def _ov(driver, delta, ticker="SYN", channel=None, **kw):
    ch = channel or ("pe" if driver in A.PE_DRIVERS else "eps")
    return {"id": f"{driver}-{delta}", "date": "2026-09-01", "ticker": ticker, "channel": ch, "driver": driver,
            "delta": delta, "reliability": "filing", "source": "s", "note": "n", "resolved_until": "2026-12-01", **kw}


def test_market_stage_applies_explicit_rf_erp_without_implicit_macro_tilt():
    m = M.apply_overlays_market(0.045, 0.045, [_ov("rf", 0.002), _ov("erp", 0.003)], {"risk_level": "adverse", "risk_score": -1})
    assert m["rf"] == pytest.approx(0.047) and m["erp"] == pytest.approx(0.045 + 0.003)
    explicit = M.apply_overlays_market(0.045, 0.045, [_ov("erp", 0.01, ticker="MACRO")], {"risk_level": "adverse"})
    assert explicit["erp_tilt"] == 0.0 and explicit["erp"] == pytest.approx(0.055) and "仅研究上下文" in explicit["erp_tilt_basis"]
    none = M.apply_overlays_market(0.045, 0.045, [], None)
    assert none["erp"] == 0.045 and none["erp_tilt_basis"] == "无宏观状态"


@pytest.mark.parametrize("level,score", [("supportive", 2), ("neutral", 0), ("adverse", -1), ("crisis", -2), ("unknown", -2)])
def test_risk_labels_preserve_valuation_and_context_without_quantified_overlays(level, score):
    b = make_bundle(price=50.0)
    params = dict(rf=0.045, erp=0.045, beta=None)
    plain = M.valuate(b, _resolved("platform"), **params)
    context = {"risk_level": level, "risk_score": score, "run_id": "macro-label-only"}
    labelled = M.valuate(b, _resolved("platform"), macro_context=context, **params)
    assert labelled["fair_value"] == pytest.approx(plain["fair_value"])
    assert labelled["cost_of_capital"]["erp"] == pytest.approx(0.045)
    assert labelled["cost_of_capital"]["wacc"] == pytest.approx(plain["cost_of_capital"]["wacc"])
    assert labelled["scenarios"]["probabilities"] == plain["scenarios"]["probabilities"]
    assert labelled["news"]["macro_context"] == context
    assert labelled["news"]["erp_tilt"] == 0.0


def test_spec_stage_multiplicative_and_additive_drivers():
    s = _flat_spec(growth_start=0.10, terminal_growth=0.03)
    p = M.apply_overlays_spec(s, [_ov("revenue_base", 0.10), _ov("growth_start", 0.02), _ov("target_margin", -0.05),
                                  _ov("share_change_rate", 0.01), _ov("net_interest_after_tax", 1e6), _ov("wacc", 0.005),
                                  _ov("terminal_growth", 0.01), _ov("pe_multiplier", -0.1), _ov("scenario_tilt", 0.1)])
    assert p["revenue_base"] == pytest.approx(1.1e9) and p["growth_start"] == pytest.approx(0.12)
    assert p["target_margin"] == pytest.approx(0.25) and p["share_change_rate"] == pytest.approx(0.01)
    assert p["net_interest_after_tax"] == pytest.approx(1e6) and p["wacc"] == pytest.approx(0.105) and p["cost_of_equity"] == pytest.approx(0.105)
    assert p["terminal_growth"] == pytest.approx(0.04)
    assert "pe_multiplier" not in p and "scenario_tilt" not in p
    assert M.pe_multiplier_of([_ov("pe_multiplier", -0.1), _ov("pe_multiplier", 0.05)]) == pytest.approx(0.9 * 1.05)


def test_probability_tilt_moves_mass_and_respects_floor():
    base = {"bear": 0.25, "base": 0.5, "bull": 0.25}
    up = M.tilt_probabilities(base, [_ov("scenario_tilt", 0.10)])
    assert up["bear"] == pytest.approx(0.15) and up["bull"] == pytest.approx(0.35) and sum(up.values()) == pytest.approx(1)
    dn = M.tilt_probabilities(base, [_ov("scenario_tilt", -0.5)])
    assert dn["bull"] == pytest.approx(M.PROB_FLOOR) and dn["bear"] == pytest.approx(0.45)
    assert M.tilt_probabilities(base, []) == base


def test_valuate_carries_news_block_and_moves_fair_value():
    b = make_bundle(price=50.0)
    plain = M.valuate(b, _resolved("platform"), rf=0.045, erp=0.045, beta=None)
    bull = M.valuate(b, _resolved("platform"), rf=0.045, erp=0.045, beta=None,
                     overlays={"applied": [_ov("revenue_base", 0.10)], "ignored": [{"id": "x", "reason": "可靠度 rumor 不应用"}], "flags": ["f"]},
                     macro_context={"risk_level": "neutral", "risk_score": 0})
    assert bull["fair_value"] > plain["fair_value"]
    assert bull["news"]["applied"][0]["driver"] == "revenue_base" and bull["news"]["ignored"][0]["id"] == "x"
    assert "revenue_base" in bull["news"]["spec_changes"] and "f" in bull["flags"]
    bear = M.valuate(b, _resolved("platform"), rf=0.045, erp=0.045, beta=None,
                     overlays={"applied": [_ov("pe_multiplier", -0.2), _ov("erp", 0.01), _ov("scenario_tilt", -0.1)], "ignored": [], "flags": []},
                     macro_context={"risk_level": "adverse", "risk_score": -1})
    assert bear["fair_value"] < plain["fair_value"]
    assert bear["base"]["eps_pe"]["pe_mult"] == pytest.approx(0.8) and "overlay" in bear["base"]["eps_pe"]["pe_basis"]
    assert bear["cost_of_capital"]["erp"] == pytest.approx(0.045 + 0.01) and bear["cost_of_capital"]["erp_tilt"] == 0.0
    assert bear["scenarios"]["probabilities"]["bear"] == pytest.approx(0.35)
    assert plain["news"]["applied"] == [] and plain["cost_of_capital"]["erp_tilt"] == 0.0


# ── 第二轮: 收入趋势归一化 ────────────────────────────────────────

def test_fit_log_trend_recovers_constant_growth():
    vals = [100 * (1.10 ** (i / 4)) for i in range(40)]           # 每年 +10%
    tr = M.fit_log_trend(vals)
    assert tr["annual_growth"] == pytest.approx(0.10, abs=1e-9) and tr["r2"] == pytest.approx(1.0)
    assert tr["fitted_last"] == pytest.approx(vals[-1], rel=1e-9) and tr["n"] == 40
    assert M.fit_log_trend([1.0, 1.0]) is None and M.fit_log_trend([5.0] * 10)["annual_growth"] == pytest.approx(0.0)
    noisy = M.fit_log_trend([100, 300, 50, 400, 60, 500, 70, 900])
    assert 0 <= noisy["r2"] < 1


def test_project_revenue_level_fades_to_trend():
    s = _flat_spec(growth_start=0.0, terminal_growth=0.0, revenue_level_ratio=0.5, revenue_fade_years=2)
    p = M.project(s)
    assert p[0]["revenue"] == pytest.approx(1e9 * 0.5 ** 0.5) and p[1]["revenue"] == pytest.approx(0.5e9)
    assert p[2]["revenue"] == pytest.approx(0.5e9) and p[0]["level"] == pytest.approx(0.5 ** 0.5)
    assert all(r["reinvest"] == 0.0 for r in p), "收入回落不记负再投资"
    same = M.project(_flat_spec(growth_start=0.0, terminal_growth=0.0))
    assert same[0]["revenue"] == pytest.approx(1e9) and same[0]["level"] == 1.0


def test_resolve_revenue_base_gates_and_ratio():
    d = {"revenue_trend": {"n": 40, "r2": 0.8, "fitted_last": 8e9, "annual_growth": 0.1}, "revenue_vs_trend": 1.5}
    ratio, fade, basis, flags = M.resolve_revenue_base(d, {"revenue_method": "trend", "revenue_fade_years": 3}, {})
    assert ratio == pytest.approx(1 / 1.5) and fade == 3 and "趋势线" in basis and any("周期峰值收入" in f for f in flags)
    weak = dict(d, revenue_trend={"n": 40, "r2": 0.3, "fitted_last": 8e9, "annual_growth": 0.1})
    ratio2, fade2, basis2, flags2 = M.resolve_revenue_base(weak, {"revenue_method": "trend", "revenue_fade_years": 3}, {})
    assert ratio2 == 1.0 and fade2 == 1 and any("退回 TTM 实际" in f for f in flags2)
    ratio3, *_ = M.resolve_revenue_base(weak, {"revenue_method": "trend", "revenue_fade_years": 3}, {"trend_min_r2": 0.2})
    assert ratio3 == pytest.approx(1 / 1.5)
    ratio4, fade4, basis4, flags4 = M.resolve_revenue_base(d, {"revenue_method": "actual"}, {})
    assert ratio4 == 1.0 and basis4 == "TTM 实际" and flags4 == []
    trough = dict(d, revenue_vs_trend=0.4)
    r5, *_ = M.resolve_revenue_base(trough, {"revenue_method": "trend", "revenue_fade_years": 3}, {})
    assert r5 == M.REVENUE_RATIO_CLAMP[1]


def test_cyclical_uses_trend_slope_and_level_when_fit_is_good():
    b = make_bundle(g_annual=0.10, margin=0.20)
    for r in b["quarters"][-4:]:
        r["revenue"] *= 1.6                      # 最后一年收入飙到趋势线 1.6× (峰值)
        r["operating_profit"] = r["revenue"] * 0.5
    res = M.valuate(b, _resolved("semi_cyclical", overrides={"revenue_method": "trend"}), rf=0.045, erp=0.045, beta=None)
    assert res["growth_basis"].startswith("收入对数趋势斜率")
    assert res["spec"]["revenue_level_ratio"] < 0.8 and "趋势线" in res["revenue_basis"]
    assert any("周期峰值收入" in f for f in res["flags"])
    assert res["implied"]["revenue_level_ratio"] == res["spec"]["revenue_level_ratio"]
    plain = M.valuate(b, _resolved("semi_cyclical"), rf=0.045, erp=0.045, beta=None)   # 缺省 actual (回测未采纳 trend, 设计 §10.4)
    assert plain["spec"]["revenue_level_ratio"] == 1.0 and plain["fair_value"] > res["fair_value"]
    assert plain["growth_basis"].startswith("收入对数趋势斜率"), "增速仍用趋势斜率 (端点偏差), 只有收入水平不归一化"
    assert res["scenario_deltas"]["margin_fade_years"] == 2 and res["scenario_deltas"]["growth"] == 0.20


# ── v2: 一致预期锚定 / 月度 β / 中年折现 ─────────────────────────────

def test_resolve_base_prefers_consensus_then_runrate_then_ttm():
    b = make_bundle(g_annual=0.10, margin=0.30)
    d = M.derive_drivers(b, _params("platform"))
    fw = {"forward_revenue": d["revenue_ttm"] * 1.5, "forward_eps": d["eps_core_ttm"] * 2.0}
    a = M.resolve_base(d, _params("platform"), {}, fw)
    assert a["consensus_revenue"] and a["consensus_eps"] and a["growth_1"] == pytest.approx(0.5)
    assert a["margin_1"] > d["opm_ttm"] and "一致前瞻 EPS" in a["margin_basis"] and "一致前瞻收入" in a["revenue_basis"]
    assert a["growth_after"] <= _params("platform")["growth_cap_after"] and a["forward_pe"] == pytest.approx(50.0 / fw["forward_eps"])
    off = M.resolve_base(d, _params("platform"), {"use_consensus": False}, fw)
    assert not off["consensus_revenue"] and off["revenue_1"] == pytest.approx(d["revenue_ttm"] * (1 + off["growth_after"]))
    ramp = make_bundle(g_annual=0.10, margin=0.30)
    ramp["quarters"][-1]["revenue"] *= 1.6                 # 最近季陡峭爬坡
    ramp["quarters"][-1]["operating_profit"] = ramp["quarters"][-1]["revenue"] * 0.45
    dr = M.derive_drivers(ramp, _params("platform"))
    r = M.resolve_base(dr, _params("platform"), {}, None)
    assert dr["runrate_vs_ttm"] > M.RUNRATE_RAMP and "最近季年化" in r["revenue_basis"] and r["margin_1"] == pytest.approx(0.45)


def test_valuate_with_consensus_moves_value_and_records_anchor():
    b = make_bundle(g_annual=0.10, margin=0.30)
    plain = M.valuate(b, _resolved("platform"), rf=0.045, erp=0.045, beta=None)
    d = plain["drivers"]
    b2 = make_bundle(g_annual=0.10, margin=0.30, forward={"forward_revenue": d["revenue_ttm"] * 1.5, "forward_eps": d["eps_core_ttm"] * 1.8,
                                                           "forward_pe": None, "forward_ps": None, "as_of": "2026-09-01", "source": "t"})
    cons = M.valuate(b2, _resolved("platform"), rf=0.045, erp=0.045, beta=None)
    assert cons["fair_value"] > plain["fair_value"] * 1.2
    assert cons["anchor"]["consensus_revenue"] and cons["base"]["path"][0]["revenue"] == pytest.approx(d["revenue_ttm"] * 1.5)
    assert cons["anchor"]["forward"]["forward_eps"] == pytest.approx(d["eps_core_ttm"] * 1.8)
    assert cons["implied"]["cagr_years"] == 9 and "年2起" not in cons["anchor"]["growth_basis"]


def test_monthly_beta_and_midyear_discounting():
    import datetime as dt
    import math as _m
    import random
    rng = random.Random(1)
    d0 = dt.date(2019, 1, 1)
    pt, pm, a, b_ = {}, {}, 100.0, 100.0
    for i in range(6 * 365):
        r = rng.gauss(0.0003, 0.01)
        b_ *= _m.exp(r); a *= _m.exp(1.4 * r)
        day = (d0 + dt.timedelta(days=i)).isoformat()
        pt[day], pm[day] = a, b_
    bt = M.beta_from_monthly(pt, pm)
    assert bt["raw"] == pytest.approx(1.4, abs=1e-6) and bt["n_obs"] == 60 and "月度" in bt["window"]
    assert M.beta_from_monthly(pt, pm, as_of="2020-06-30") is None, "不足 36 个月"
    p = M.project(_flat_spec())
    nopat = 1e9 * 0.30 * 0.79
    r = M.dcf_value(p, wacc=0.10, terminal_growth=0.0, ronic_terminal=0.10, cash_sti=0, lt_investments=0, debt=0, shares=1e6)
    assert r["ev"] == pytest.approx(nopat / 0.10 * 1.10 ** M.MIDYEAR, rel=1e-9), "中年折现: 永续价值 × (1+r)^0.5"


def test_project_year1_anchor_then_fade():
    s = _flat_spec(growth_year1=0.50, growth_start=0.10, terminal_growth=0.02, horizon_years=5, margin_start=0.20, target_margin=0.40, margin_fade_years=3)
    p = M.project(s)
    assert p[0]["revenue"] == pytest.approx(1.5e9) and p[0]["growth"] == pytest.approx(0.5) and p[1]["growth"] == pytest.approx(0.10)
    assert p[-1]["growth"] == pytest.approx(0.02)
    # margin_fade_years=3: 年 1 锚定, 之后 3 年过渡, 年 4 到位
    assert p[0]["margin"] == pytest.approx(0.20) and p[1]["margin"] == pytest.approx(0.20 + 0.20 / 3)
    assert p[2]["margin"] == pytest.approx(0.20 + 0.40 / 3) and p[3]["margin"] == pytest.approx(0.40)


# ── v3: 引擎 B = 卖方一致目标价 ──────────────────────────────────

def test_street_value_discounts_targets_and_requires_coverage():
    c = {"average": 120.0, "lowest": 90.0, "highest": 150.0, "total": 10, "update_time_str": "2026-09-01"}
    s = M.street_value(c, cost_of_equity=0.10)
    assert s["per_share"] == pytest.approx(120 / 1.1) and s["per_share_bear"] == pytest.approx(90 / 1.1) and s["per_share_bull"] == pytest.approx(150 / 1.1)
    assert "10 家" in s["basis"]
    assert M.street_value({"average": 120.0, "total": 2}, cost_of_equity=0.10) is None
    assert M.street_value(None, cost_of_equity=0.10) is None and M.street_value({"average": 0, "total": 9}, cost_of_equity=0.1) is None


def test_no_street_coverage_falls_back_to_dcf_only_with_flag():
    b = make_bundle()
    b["consensus"] = None
    res = M.valuate(b, _resolved("platform"), rf=0.045, erp=0.045, beta=None)
    assert res["base"]["weights"] == {"fcf_dcf": 0.5, "street": 0.0} and res["base"]["street"] is None
    assert any("引擎 B 零权" in f for f in res["flags"])
    assert res["fair_value"] > 0
    both = M.valuate(make_bundle(), _resolved("platform"), rf=0.045, erp=0.045, beta=None)
    assert both["scenarios"]["bear"] != res["scenarios"]["bear"], "街上低/均/高进熊/基/牛"
