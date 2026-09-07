"""Dated macro/company shocks over a frozen cash-flow baseline; 零授权效力.

Rate shocks use bp, business percentages use fractions, dollar effects use USD.
Policy rates require explicit transmission. No risk label becomes a coefficient.
"""
from __future__ import annotations

from copy import deepcopy
import math

from quant.core.agent_contracts import canonical_hash
from quant.core.valuation_contracts import DIRECT_FACTORS, validate_scenario, validate_transmissions
from quant.valuation import model


def sensitivities(horizon: int, evidence_ref: str) -> list[dict]:
    """Mechanical parallel-curve experiments, explicitly not policy forecasts."""
    return [{"id": "rf_" + ("down" if bp < 0 else "up") + "_25bp",
             "label": f"无风险曲线平行移动 {bp:+d}bp",
             "basis": "假设各预测年及终值的无风险率同幅变化；其余输入固定；不是政策降息预测",
             "evidence_refs": [evidence_ref],
             "shocks": [{"factor": "rf_bp", "delta": bp, "start_year": 1,
                         "end_year": horizon, "terminal": True}]} for bp in (-25, 25)]


def _expand(shocks, transmissions):
    expanded, missing = [], []
    direct = {s["factor"] for s in shocks if s["factor"] in DIRECT_FACTORS}
    for shock in shocks:
        if shock["factor"] in DIRECT_FACTORS:
            expanded.append(deepcopy(shock))
            continue
        links = [r for r in transmissions if r["factor"] == shock["factor"]]
        if not links:
            missing.append("unquantified_transmission:" + shock["factor"])
        for link in links:
            if link["driver"] in direct:
                missing.append("overlapping_direct_and_transmitted_channel:" + link["driver"])
                continue
            first = max(shock["start_year"], link.get("start_year", shock["start_year"]))
            last = min(shock["end_year"], link.get("end_year", shock["end_year"]))
            terminal = shock["terminal"] and link.get("terminal", True)
            if first <= last:
                expanded.append({**shock, "factor": link["driver"], "start_year": first, "end_year": last,
                                 "terminal": terminal, "delta": shock["delta"] * link["coefficient"]})
    return expanded, sorted(set(missing))


def _calculate(baseline, shocks):
    spec, drivers, capital = baseline["spec"], baseline["drivers"], baseline["cost_of_capital"]
    original = model.project({**spec, "year_effects": []})
    n = len(original)
    adjustments = [{} for _ in range(n + 1)]
    existing = [{"factor": {"revenue_base": "revenue_pct", "margin_start": "margin_pp",
                             "net_interest_after_tax": "net_interest_usd"}[e["driver"]],
                 "delta": e["delta"], "start_year": e["start_year"], "end_year": e["end_year"], "terminal": False}
                for e in spec.get("year_effects", [])]
    for shock in existing + shocks:
        indexes = list(range(shock["start_year"] - 1, shock["end_year"]))
        if shock["terminal"]:
            indexes.append(n)
        for index in indexes:
            factor = shock["factor"]
            adjustments[index][factor] = adjustments[index].get(factor, 0.0) + shock["delta"]

    def rates(change):
        rf = change.get("rf_bp", 0) / 10000
        erp = change.get("erp_bp", 0) / 10000
        spread = change.get("debt_spread_bp", 0) / 10000
        equity = spec["cost_of_equity"] + rf + capital["beta"] * erp
        debt_weight = capital["debt_weight"]
        wacc = (spec["wacc"] + (1 - debt_weight) * (rf + capital["beta"] * erp)
                + debt_weight * (rf + spread) * (1 - spec["tax_rate"]))
        return wacc, equity

    path, discount_rates, equity_rates = [], [], []
    revenue_prev = spec["revenue_base"]
    for row, change in zip(original, adjustments):
        revenue = row["revenue"] * (1 + change.get("revenue_pct", 0))
        margin = row["margin"] + change.get("margin_pp", 0)
        if revenue <= 0 or not -1 <= margin <= 1:
            raise ValueError("scenario operating path outside support")
        ebit = revenue * margin
        nopat = ebit * (1 - spec["tax_rate"]) if ebit > 0 else ebit
        reinvest = max(0.0, revenue - revenue_prev) / spec["sales_to_capital"] + change.get("reinvestment_usd", 0)
        cashflow = nopat - reinvest
        interest = spec["net_interest_after_tax"] + change.get("net_interest_usd", 0)
        path.append({**row, "revenue": revenue, "margin": margin, "nopat": nopat,
                     "reinvest": reinvest, "fcf": cashflow, "eps": (nopat + interest) / row["shares"]})
        revenue_prev = revenue
        wacc, equity = rates(change)
        discount_rates.append(wacc)
        equity_rates.append(equity)
    terminal = adjustments[-1]
    terminal_revenue = original[-1]["revenue"] * (1 + terminal.get("revenue_pct", 0))
    terminal_margin = original[-1]["margin"] + terminal.get("margin_pp", 0)
    if terminal_revenue <= 0 or not -1 <= terminal_margin <= 1:
        raise ValueError("terminal operating path outside support")
    terminal_ebit = terminal_revenue * terminal_margin
    terminal_nopat = terminal_ebit * (1 - spec["tax_rate"]) if terminal_ebit > 0 else terminal_ebit
    terminal_wacc, terminal_equity = rates(terminal)
    if terminal.get("reinvestment_usd"):
        # A fixed dollar perpetuity must be expressed as a sustainable return
        # on capital/growth assumption, not silently embedded in a growth rate.
        raise ValueError("terminal dollar reinvestment requires a sustainable capital model")
    dcf = model.dcf_value(path, wacc=spec["wacc"], wacc_path=discount_rates,
                           terminal_wacc=terminal_wacc, terminal_nopat=terminal_nopat,
                           terminal_growth=spec["terminal_growth"], ronic_terminal=spec["ronic_terminal"],
                           **{key: drivers[key] for key in ("cash_sti", "lt_investments", "debt", "shares")})
    # Year-one price values only later distributions, using the equity curve.
    payout_terminal = 1 - spec["terminal_growth"] / spec["ronic_terminal"]
    terminal_eps = (terminal_nopat + spec["net_interest_after_tax"] + terminal.get("net_interest_usd", 0)) / original[-1]["shares"]
    spread = terminal_equity - spec["terminal_growth"]
    price, accum = 0.0, 1.0
    if spread <= 0 or any(rate <= -1 for rate in equity_rates):
        raise ValueError("scenario equity curve outside support")
    for row, rate in zip(path[1:], equity_rates[1:]):
        accum *= 1 + rate
        payout = max(0.0, min(1.0, 1 - row["reinvest"] / row["nopat"])) if row["nopat"] > 0 else 0.0
        price += row["eps"] * payout / accum
    price += terminal_eps * (1 + spec["terminal_growth"]) * payout_terminal / spread / accum
    pe_multiplier = baseline.get("market_target_12m", {}).get("pe_multiplier", 1.0)
    pe_multiplier *= 1 + adjustments[0].get("pe_pct", 0)
    if pe_multiplier <= 0:
        raise ValueError("nonpositive multiple")
    eps = path[0]["eps"]
    price = price * pe_multiplier if price > 0 and eps > 0 and terminal_eps > 0 else None
    if not spec.get("net_interest_known", True):
        price, eps = None, None
    value = max(0.0, dcf["per_share"])
    if not math.isfinite(value) or (price is not None and not math.isfinite(price)):
        raise ValueError("nonfinite scenario result")
    return {"fair_value": value, "market_target_12m": price,
            "eps_12m": eps, "pe_12m": price / eps if price is not None else None,
            "path": path, "wacc_path": discount_rates, "terminal_wacc": terminal_wacc,
            "terminal_nopat": terminal_nopat}


def evaluate(baseline: dict, scenario: dict, *, transmissions=None, evidence_refs=None) -> dict:
    """No complete target for unmapped or duplicate channels; preserve diagnostics."""
    scenario = validate_scenario(scenario, horizon=len(baseline["base"]["path"]), evidence_refs=evidence_refs)
    links = validate_transmissions(transmissions or [])
    horizon = len(baseline["base"]["path"])
    if any(link.get("end_year", horizon) > horizon or
           link.get("terminal", False) and link.get("end_year", horizon) != horizon for link in links):
        raise ValueError("transmission horizon incompatible with company forecast")
    shocks, missing = _expand(scenario["shocks"], links)
    identity = canonical_hash({"spec": baseline["spec"], "drivers": baseline["drivers"],
                               "capital": baseline["cost_of_capital"], "model_version": baseline["model_version"]})
    result = {"scenario": scenario, "baseline_hash": identity, "transmissions": links,
              "status": "unknown", "fair_value": None, "market_target_12m": None,
              "missing": missing, "probability": None, "authority": "analysis_only"}
    try:
        reference = _calculate(baseline, [])
        changed = _calculate(baseline, shocks)
        contributions = []
        for factor in sorted({s["factor"] for s in shocks}):
            single = _calculate(baseline, [s for s in shocks if s["factor"] == factor])
            contributions.append({"factor": factor, "intrinsic_delta": single["fair_value"] - reference["fair_value"],
                                  "target_12m_delta": (single["market_target_12m"] - reference["market_target_12m"])
                                  if single["market_target_12m"] is not None and reference["market_target_12m"] is not None else None})
        delta = changed["fair_value"] - reference["fair_value"]
        result.update(status="partial" if missing else "computed", partial_calculation=changed,
                      reference=reference, attribution=contributions,
                      intrinsic_delta=delta if not missing else None,
                      interaction_delta=delta - sum(r["intrinsic_delta"] for r in contributions))
        if not missing:
            result.update(fair_value=changed["fair_value"], market_target_12m=changed["market_target_12m"])
    except (ValueError, OverflowError, ZeroDivisionError) as exc:
        result["missing"].append(str(exc))
    result["receipt_hash"] = canonical_hash(result)
    return result
