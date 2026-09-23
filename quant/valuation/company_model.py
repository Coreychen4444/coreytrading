"""Explicit company statements, quarterly forecasts and independent price cases; 零授权效力.

No provider access or trading writes. Amounts and shares are in millions of USD
and millions of shares, so their quotient is USD/share. No archetype, inferred
share count, automatic financing, tax benefit or PE is supplied by this engine.
Raw calculations are diagnostic research, not accepted market forecasts.
"""
from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import argparse
from copy import deepcopy
from datetime import date, datetime, timedelta
import math

from quant.core.agent_contracts import canonical_hash
from quant.core.io_json import atomic_write_json, read_json
from quant.valuation import company_cashflow, company_equity, company_multiples, company_eps

VERSION = "company-v5.4.1-research"


def number(value, name, *, minimum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name}: explicit finite number required")
    if minimum is not None and value < minimum:
        raise ValueError(f"{name}: below {minimum}")
    return float(value)


def stamp(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("timezone required")
    return result


def _sum(items, name):
    if not isinstance(items, dict):
        raise ValueError(f"{name}: explicit named components required (empty allowed)")
    return sum(number(v, f"{name}.{k}") for k, v in items.items())


def _evidence(block, sources, cutoff):
    if not block.get("basis") or not block.get("source_refs"):
        raise ValueError("basis and source_refs required")
    known = stamp(block["known_at"])
    if known > cutoff:
        raise ValueError("assumption or observation unavailable at cutoff")
    for ref in block["source_refs"]:
        source = sources[ref]
        if stamp(source["available_at"]) > known or stamp(source["captured_at"]) > cutoff:
            raise ValueError("source not available or captured by cutoff")
        digest = source.get("sha256", "")
        if (not source.get("url") or len(digest) != 64
                or any(c not in "0123456789abcdef" for c in digest)):
            raise ValueError("source URL and captured content hash required")


def consolidate(segments, required_segments):
    """Signed corporate/elimination rows; no summing Products and its children."""
    if set(segments) != set(required_segments) or len(required_segments) != len(set(required_segments)):
        raise ValueError("complete nonoverlapping segment set required")
    revenue = profit = 0.0
    for name, row in segments.items():
        revenue += number(row["revenue"], f"{name}.revenue")
        profit += number(row["operating_profit"], f"{name}.operating_profit")
    return {"revenue": revenue, "operating_profit": profit}


def earnings(segments, income, shares, required_segments):
    if not isinstance(income.get("adjusted_definition"), str) or not income["adjusted_definition"].strip():
        raise ValueError("explicit adjusted earnings definition required")
    totals = consolidate(segments, required_segments)
    if income.get("nonoperating_basis") == "combined_net":
        if any(k in income for k in ("interest_income", "interest_expense", "other_nonoperating")):
            raise ValueError("combined nonoperating income cannot also contain split components")
        if not income.get("nonoperating_definition"):
            raise ValueError("combined nonoperating definition required")
        nonoperating = number(income["net_nonoperating"], "net nonoperating")
    else:
        if income.get("nonoperating_basis", "split_components") != "split_components" or "net_nonoperating" in income:
            raise ValueError("explicit consistent nonoperating basis required")
        nonoperating = (number(income["interest_income"], "interest_income", minimum=0)
                        - number(income["interest_expense"], "interest_expense", minimum=0)
                        + _sum(income["other_nonoperating"], "other_nonoperating"))
    pretax = totals["operating_profit"] + nonoperating
    after_tax = income.get("after_tax_items")
    equity_income = discontinued = 0.0
    if after_tax is not None:
        if (set(after_tax) != {"basis", "excluded_from_pretax_and_tax", "equity_income", "discontinued_income"}
                or not after_tax["basis"] or after_tax["excluded_from_pretax_and_tax"] is not True):
            raise ValueError("after-tax items require explicit scope and exclusion from pretax/tax")
        equity_income = number(after_tax["equity_income"], "after-tax equity income")
        discontinued = number(after_tax["discontinued_income"], "after-tax discontinued income")
    continuing = pretax - number(income["tax_expense"], "tax_expense") + equity_income
    consolidated = continuing + discontinued
    parent_before_preferred = consolidated - number(income["noncontrolling_income"], "noncontrolling_income")
    preferred = income.get("preferred_dividends", 0)
    if "preferred_dividends" in income and not income.get("preferred_dividend_basis"):
        raise ValueError("preferred dividend numerator requires explicit basis")
    preferred = number(preferred, "preferred dividends", minimum=0)
    parent = parent_before_preferred - preferred
    adjusted = parent + _sum(income["parent_after_tax_adjustments"], "parent_after_tax_adjustments")
    basic = number(shares["basic_weighted"], "basic_weighted", minimum=1e-12)
    incremental = number(shares["incremental_dilutive"], "incremental_dilutive", minimum=0)
    closing = number(shares["closing"], "closing", minimum=1e-12)
    # Loss-period anti-dilution is not an estimate of profitable-period shares.
    gaap_denominator = basic if parent < 0 else basic + incremental
    adjusted_denominator = basic if adjusted < 0 else basic + incremental
    gaap_numerator, adjusted_numerator = parent, adjusted
    added_shares = addback = 0.0
    conversion = shares.get("preferred_if_converted")
    if conversion is not None:
        if (set(conversion) != {"basis", "shares", "dividend_addback"}
                or not conversion["basis"] or "preferred_dividends" not in income):
            raise ValueError("preferred if-converted requires separate shares and dividend basis")
        added_shares = number(conversion["shares"], "preferred conversion shares", minimum=1e-12)
        addback = number(conversion["dividend_addback"], "preferred dividend addback", minimum=0)
        if addback > preferred:
            raise ValueError("conversion addback exceeds preferred dividends")
        # This block is separate from ordinary incremental shares; include only
        # when dilutive for the selected numerator (GAAP and adjusted separately).
        if (parent + addback) / (gaap_denominator + added_shares) < parent / gaap_denominator:
            gaap_numerator += addback
            gaap_denominator += added_shares
        if (adjusted + addback) / (adjusted_denominator + added_shares) < adjusted / adjusted_denominator:
            adjusted_numerator += addback
            adjusted_denominator += added_shares
    eps_inputs = None
    if "eps_securities" in shares:
        if conversion is not None or income["parent_after_tax_adjustments"]:
            raise ValueError("securities EPS cannot mix legacy conversion or adjusted earnings; use an explicitly separate basis")
        if discontinued and income["noncontrolling_income"]:
            raise ValueError("securities EPS needs a separate discontinued NCI allocation")
        eps_inputs = {"income": parent, "control_income": parent - discontinued,
                      "basic_common": basic, "incremental": incremental,
                      "securities": deepcopy(shares["eps_securities"])}
        security_eps = company_eps.calculate(**eps_inputs)
        parent = adjusted = security_eps["basic_common_income"]
        gaap_numerator = adjusted_numerator = security_eps["diluted_income"]
        gaap_denominator = adjusted_denominator = security_eps["diluted_shares"]
        basic = security_eps["basic_shares"]
    return {**totals, "pretax": pretax, "consolidated_income": consolidated,
            "continuing_income": continuing, "after_tax_equity_income": equity_income,
            "discontinued_income": discontinued,
            "parent_income_before_preferred": parent_before_preferred,
            "preferred_dividends": preferred,
            "tax_expense": number(income["tax_expense"], "tax_expense"),
            "interest_income": income.get("interest_income"),
            "interest_expense": income.get("interest_expense"),
            "adjusted_definition": income["adjusted_definition"],
            "parent_income": parent, "adjusted_parent_income": adjusted,
            "gaap_eps_income": gaap_numerator, "adjusted_eps_income": adjusted_numerator,
            "preferred_conversion_shares": added_shares,
            "preferred_conversion_dividend_addback": addback,
            "gaap_eps": gaap_numerator / gaap_denominator, "adjusted_eps": adjusted_numerator / adjusted_denominator,
            "basic_weighted_shares": basic, "incremental_dilutive_shares": incremental,
            "gaap_diluted_shares": gaap_denominator, "adjusted_diluted_shares": adjusted_denominator,
            "closing_shares": closing,
            "securities_eps_inputs": eps_inputs,
            "securities_eps": security_eps if eps_inputs is not None else None}


def income_cash_timing(result, cash):
    """Replace selected accrual charges with cash once; declarations need source audit.

    Net tax payments may be negative (refund). Interest payments here exclude
    capitalized interest, which has its own existing cash field. This bridge
    does not estimate tax liabilities or accrued-interest balances.
    """
    block = cash.get("income_cash_timing")
    if block is None:
        return {"status": "not_separately_bridged", "adjustment": 0.0}
    allowed = {"basis", "other_entries_exclude_selected_timing_items",
               "tax_cash_paid_net", "interest_cash_paid", "interest_cash_received",
               "interest_cash_excludes_capitalized"}
    if set(block) - allowed or not block.get("basis"):
        raise ValueError("income cash timing: explicit supported basis required")
    if block.get("other_entries_exclude_selected_timing_items") is not True:
        raise ValueError("income cash timing must exclude selected items from WC and other adjustments")
    parts = {}
    if "tax_cash_paid_net" in block:
        parts["tax"] = result["tax_expense"] - number(block["tax_cash_paid_net"], "net tax cash")
    interest = {"interest_cash_paid", "interest_cash_received"}
    if interest & set(block):
        if not interest.issubset(block) or block.get("interest_cash_excludes_capitalized") is not True:
            raise ValueError("both interest cash sides excluding capitalized interest required")
        if result["interest_income"] is None or result["interest_expense"] is None:
            raise ValueError("split interest accruals required for interest cash timing")
        parts["interest"] = (result["interest_expense"] - result["interest_income"]
                             - number(block["interest_cash_paid"], "interest cash paid", minimum=0)
                             + number(block["interest_cash_received"], "interest cash received", minimum=0))
    if not parts:
        raise ValueError("at least one selected cash timing component required")
    return {"status": "explicit_conditional_bridge", "components": parts,
            "adjustment": sum(parts.values()), "basis": block["basis"]}


def liquidity(result, cash, opening_liquidity, opening_debt):
    """Cash pool roll-forward, with financing and partner flows explicit.

    Opening pool is cash plus explicitly liquid investments, excluding restricted
    cash. Transfers inside that pool must not appear as new external cash inflows.
    CFO starts with consolidated income, so partner distributions are separate.
    """
    positive = ("depreciation_amortization", "capex_investing", "capex_financing",
                "capital_grants", "partner_contributions", "partner_distributions",
                "debt_issued", "debt_repaid", "equity_cash_raised", "lease_principal",
                "common_dividends", "common_buybacks", "minimum_liquidity", "capitalized_interest_cash")
    x = {k: number(cash[k], k, minimum=0) for k in positive}
    timing = income_cash_timing(result, cash)
    ocf = (result["consolidated_income"] + x["depreciation_amortization"]
           + _sum(cash["noncash_adjustments"], "noncash_adjustments")
           + number(cash["working_capital_cash_change"], "working_capital_cash_change")
           - x["capitalized_interest_cash"] + timing["adjustment"])
    total_capex = x["capex_investing"] + x["capex_financing"]
    pre_financing = (ocf - total_capex + x["capital_grants"] - x["lease_principal"]
                     + _sum(cash["other_cash_flows"], "other_cash_flows"))
    ending = (opening_liquidity + pre_financing + x["partner_contributions"] - x["partner_distributions"]
              + x["debt_issued"] - x["debt_repaid"] + x["equity_cash_raised"]
              - x["common_dividends"] - x["common_buybacks"])
    debt_non_cash = number(cash["debt_non_cash_change"], "debt_non_cash_change")
    debt = opening_debt + x["debt_issued"] - x["debt_repaid"] + debt_non_cash
    if debt < 0:
        raise ValueError("debt repayment exceeds available principal")
    return {"operating_cash_flow": ocf, "total_capex": total_capex,
            "income_cash_timing": timing,
            "cash_before_external_financing": pre_financing,
            "ending_liquidity": ending, "ending_debt": debt, "debt_non_cash_change": debt_non_cash,
            "funding_gap": max(0.0, x["minimum_liquidity"] - ending)}


def reconcile(observed, required_segments):
    """Independent source totals compared with component arithmetic; no correction."""
    computed = earnings(observed["segments"], observed["income"], observed["shares"], required_segments)
    checks = []
    for field, expected in observed["reported"].items():
        if field not in computed:
            raise ValueError(f"unsupported reconciliation field: {field}")
        tolerance = .0051 if field.endswith("eps") else 1.0  # Published million-dollar/cent rounding.
        expected = number(expected, field)
        delta = computed[field] - expected
        checks.append({"field": field, "reported": expected, "calculated": computed[field],
                       "difference": delta, "passed": abs(delta) <= tolerance})
    required = {"revenue", "operating_profit", "pretax", "parent_income", "gaap_eps"}
    if not required.issubset(observed["reported"]):
        raise ValueError("income reconciliation anchors incomplete")
    for identity in observed.get("identities", []):
        calculated = _sum(identity["components"], identity["id"])
        expected = number(identity["reported"], identity["id"])
        checks.append({"field": identity["id"], "reported": expected, "calculated": calculated,
                       "difference": calculated - expected, "passed": abs(calculated - expected) <= 1.0})
    return {"status": "reconciled" if all(c["passed"] for c in checks) else "mismatch",
            "checks": checks, "computed": computed,
            "limitation": "arithmetic and selected disclosure reconciliation, not complete financial audit"}


def opening_balance(block, *, sources, cutoff):
    """Bind every scenario to one dated disclosed balance, excluding restricted cash.

    Reported debt is carrying value, not automatically principal, enterprise-value
    debt, or all senior claims. Those bridges require separate evidence.
    """
    _evidence(block, sources, cutoff)
    when = date.fromisoformat(block["date"])
    if when > cutoff.date() or when > stamp(block["known_at"]).date():
        raise ValueError("opening balance unavailable at cutoff")
    x = {k: number(block[k], k, minimum=0) for k in (
        "cash_and_equivalents", "liquid_investments", "restricted_cash_outside_pool",
        "current_debt", "noncurrent_debt", "closing_common_shares")}
    if x["closing_common_shares"] <= 0 or not block.get("debt_basis"):
        raise ValueError("positive closing shares and explicit debt basis required")
    computed = {"opening_liquidity": x["cash_and_equivalents"] + x["liquid_investments"],
                "opening_debt": x["current_debt"] + x["noncurrent_debt"],
                "opening_shares": x["closing_common_shares"],
                "cash_including_restricted": x["cash_and_equivalents"] + x["restricted_cash_outside_pool"]}
    if set(block["reported"]) != set(computed):
        raise ValueError("complete opening balance reconciliation anchors required")
    checks = []
    for key, value in computed.items():
        reported = number(block["reported"][key], key, minimum=0)
        checks.append({"field": key, "calculated": value, "reported": reported,
                       "difference": value - reported, "passed": abs(value - reported) <= 1.0})
    return {"date": str(when), "status": "reconciled" if all(c["passed"] for c in checks) else "mismatch",
            "computed": computed, "checks": checks, "restricted_cash_excluded": x["restricted_cash_outside_pool"],
            "debt_basis": block["debt_basis"],
            "limitation": "selected balance anchors; not an audit of liquidity accessibility or all equity claims"}


def earnings_window(quarters, ids, basis):
    if basis not in ("gaap", "adjusted"):
        raise ValueError("explicit GAAP or adjusted earnings basis required")
    selected = [q for q in quarters if q["id"] in ids]
    if len(selected) != 4 or len(ids) != 4 or len(set(ids)) != 4:
        raise ValueError("four explicit earnings quarters required")
    start = date.fromisoformat(selected[0]["start"])
    end = date.fromisoformat(selected[-1]["end"])
    if not 350 <= (end - start).days <= 380:
        raise ValueError("earnings window must span approximately one year")
    for left, right in zip(selected, selected[1:]):
        if date.fromisoformat(right["start"]) != date.fromisoformat(left["end"]) + timedelta(days=1):
            raise ValueError("earnings quarters must be consecutive")
    days = [(date.fromisoformat(q["end"]) - date.fromisoformat(q["start"])).days + 1 for q in selected]
    income_key = "adjusted_parent_income" if basis == "adjusted" else "parent_income"
    definitions = {q["adjusted_definition"] for q in selected}
    if basis == "adjusted" and len(definitions) != 1:
        raise ValueError("mixed adjusted earnings definitions")
    securities_inputs = [q.get("securities_eps_inputs") for q in selected]
    if any(x is not None for x in securities_inputs):
        if any(x is None for x in securities_inputs):
            raise ValueError("mixed securities EPS methods in earnings window")
        annual = company_eps.calculate(**company_eps.aggregate(securities_inputs, days))
        return {"start": str(start), "end": str(end), "earnings_basis": basis,
                "parent_income": annual["basic_common_income"],
                "adjusted_definition": next(iter(definitions)) if basis == "adjusted" else None,
                "weighted_diluted_shares": annual["diluted_shares"],
                "eps_income": annual["diluted_income"], "eps": annual["diluted_eps"],
                "securities_eps": annual, "revenue": sum(q["revenue"] for q in selected),
                "status": "research_projection", "validated": False}
    income = sum(q[income_key] for q in selected)
    denominator = sum((q["basic_weighted_shares"] + (q["incremental_dilutive_shares"] if income >= 0 else 0)) * d
                      for q, d in zip(selected, days)) / sum(days)
    eps_income = income
    conversion_shares = sum(q.get("preferred_conversion_shares", 0) * d
                            for q, d in zip(selected, days)) / sum(days)
    conversion_addback = sum(q.get("preferred_conversion_dividend_addback", 0) for q in selected)
    if conversion_shares and (income + conversion_addback) / (denominator + conversion_shares) < income / denominator:
        eps_income += conversion_addback
        denominator += conversion_shares
    return {"start": str(start), "end": str(end), "earnings_basis": basis, "parent_income": income,
            "adjusted_definition": next(iter(definitions)) if basis == "adjusted" else None,
            "weighted_diluted_shares": denominator, "eps_income": eps_income, "eps": eps_income / denominator,
            "revenue": sum(q["revenue"] for q in selected), "status": "research_projection", "validated": False}


def independent_multiple(case, quarters, *, cutoff, sources, funding_gap, ticker=None):
    """Use external, explicit PE only; never derive it from projected cash flows."""
    if case is None:
        return {"status": "unknown", "reason": "independent_multiple_evidence_missing"}
    _evidence(case, sources, cutoff)
    if case["method"] not in ("comparable_forward_pe", "historical_same_basis_pe", "analyst_sensitivity"):
        raise ValueError("independent PE method required; cashflow-derived PE forbidden")
    target = date.fromisoformat(case["target_date"])
    if not 330 <= (target - cutoff.date()).days <= 400:
        raise ValueError("target date must be approximately twelve months after cutoff")
    window = earnings_window(quarters, case["eps_quarters"], case["earnings_basis"])
    if case["earnings_basis"] == "adjusted" and case.get("adjusted_definition") != window["adjusted_definition"]:
        raise ValueError("PE and EPS adjusted earnings definitions must match")
    start, end = date.fromisoformat(window["start"]), date.fromisoformat(window["end"])
    if abs((start - target).days) > 45:
        raise ValueError("EPS window must be the forward year at target, not today's TTM/NTM")
    # Annual aggregate uses income / day-weighted annual diluted shares, not sum
    # of quarterly EPS when the denominator changes.
    eps = window["eps"]
    panel = None
    if case["method"] == "analyst_sensitivity":
        low, high = number(case["low"], "PE low", minimum=0), number(case["high"], "PE high", minimum=0)
    else:
        if "low" in case or "high" in case:
            raise ValueError("observed PE bounds must be computed from the panel, not supplied")
        if not case.get("panel"):
            return {"status": "unknown", "reason": "independent_multiple_panel_missing"}
        panel = company_multiples.calculate(case["panel"], method=case["method"], ticker=ticker,
                    earnings_definition="GAAP" if case["earnings_basis"] == "gaap" else case["adjusted_definition"],
                    sources=sources, as_of=cutoff.isoformat())
        if panel["status"] != "research_panel":
            return {"status": "unknown", "reason": panel["reason"], "panel": panel}
        low, high = panel["low"], panel["high"]
    if low <= 0 or high < low:
        raise ValueError("ordered positive PE range required")
    if case["method"] != "analyst_sensitivity" and not case.get("comparability_basis"):
        raise ValueError("peer/cycle horizon and accounting comparability required")
    out = {"status": "research_sensitivity", "target_date": str(target), "earnings_basis": case["earnings_basis"],
           "eps_window_start": str(start), "eps_window_end": str(end), "eps": eps, "pe_low": low, "pe_high": high,
           "low": None, "high": None, "method": case["method"], "basis": case["basis"],
           "independent_of_cashflow_path": True, "validated": False}
    if panel is not None:
        out["panel"] = panel
        out["multiple_at_target"] = "conditional reuse of observed panel; not a predicted market PE"
    if eps <= 0 or funding_gap:
        out.update(status="unknown", reason="nonpositive_eps" if eps <= 0 else "unfunded_operating_path")
    else:
        out.update(low=eps * low, high=eps * high)
    return out


def owner_value(case, *, cutoff, sources, funding_gap):
    """Separate per-share owner cash-flow DCF; no automatic terminal multiple.

    Each amount is explicitly net of debt service, partner claims and required
    reinvestment. Terminal common equity is net of all remaining claims. Thus no
    second cash/debt bridge is added. Future issuance reduces cash per share.
    """
    if case is None:
        return {"status": "unknown", "reason": "owner_cashflows_and_terminal_claims_missing"}
    _evidence(case, sources, cutoff)
    if funding_gap:
        return {"status": "unknown", "reason": "unfunded_operating_path"}
    if case.get("cashflow_basis") != "common_equity_after_financing_and_partner_claims":
        raise ValueError("explicit common-owner cashflow basis required")
    if not case.get("claims_basis"):
        raise ValueError("terminal claims reconciliation required")
    rate = number(case["cost_of_equity"], "cost_of_equity", minimum=0)
    if rate <= 0 or rate >= 1:
        raise ValueError("cost of equity must be a positive fraction below one")
    total = 0.0
    last = cutoff.date()
    for row in case["cashflows"]:
        when = date.fromisoformat(row["date"])
        if when <= last:
            raise ValueError("owner cashflows must be future and strictly ordered")
        shares = number(row["shares"], "owner shares", minimum=1e-12)
        amount = number(row["common_cash"], "common_cash")
        total += amount / shares / (1 + rate) ** ((when - cutoff.date()).days / 365.25)
        last = when
    terminal = case["terminal"]
    end = date.fromisoformat(terminal["date"])
    if end < last or end <= cutoff.date():
        raise ValueError("terminal cannot precede cashflows")
    terminal_pv = (number(terminal["common_equity_value"], "terminal common equity", minimum=0)
                   / number(terminal["shares"], "terminal shares", minimum=1e-12)
                   / (1 + rate) ** ((end - cutoff.date()).days / 365.25))
    return {"status": "research_sensitivity", "per_share": total + terminal_pv,
            "explicit_cashflow_pv": total, "terminal_pv": terminal_pv,
            "cost_of_equity": rate, "terminal_basis": case["claims_basis"], "validated": False}


def operating_value(case, quarters, *, sources, cutoff):
    if case is None:
        return {'status': 'unknown', 'reason': 'explicit_operating_cashflows_and_terminal_missing'}
    _evidence(case, sources, cutoff)
    if date.fromisoformat(case['date']) < cutoff.date():
        raise ValueError('operating value date cannot precede valuation cutoff')
    for row in case['periods'] + [case['terminal']]:
        _evidence({**case, 'basis': row['basis'], 'source_refs': row['source_refs']}, sources, cutoff)
    # Link overlapping full-quarter operating forecasts; longer-horizon values
    # must carry their own assumptions, never silently overwrite company EPS.
    for row in case['periods']:
        overlap = [q for q in quarters if q['start'] <= row['end'] and q['end'] >= row['start']]
        if overlap:
            if overlap[0]['start'] != row['start'] or overlap[-1]['end'] != row['end']:
                raise ValueError('DCF periods must align with complete company forecast quarters where overlapping')
            expected = sum(q['operating_profit'] for q in overlap)
            if abs(number(row['operating_profit'], 'DCF operating profit') - expected) > 1e-6:
                raise ValueError('DCF operating profit differs from company earnings path')
    return company_cashflow.value(case)


def business_equity(case, quarters, *, sources, cutoff, funding_gap, operating=None):
    if case is None:
        return {"status": "unknown", "reason": "business_values_and_equity_claims_missing"}
    _evidence(case, sources, cutoff)
    case = deepcopy(case)
    if case.get('use_consolidated_dcf'):
        if not operating or operating['status'] != 'research_sensitivity' or operating['date'] != case['date']:
            raise ValueError('matching dated consolidated DCF required')
        if case['required_business_ids'] != ['consolidated'] or case['businesses']:
            raise ValueError('consolidated DCF replaces all business values; separate segment values forbidden')
        case['businesses'] = [{'economic_id': 'consolidated', 'date': case['date'],
            'value': operating['operating_value'], 'basis': 'host-calculated consolidated DCF ' + operating['receipt_hash'],
            'source_refs': case['source_refs']}]
    matches = [q for q in quarters if q['end'] == case['date']]
    if len(matches) != 1:
        raise ValueError('equity bridge date must match an explicit forecast quarter end')
    if abs(number(case['outstanding_shares'], 'bridge shares') - matches[0]['closing_shares']) > 1e-6:
        raise ValueError('equity bridge shares must match forecast closing shares, excluding separately modelled claims')
    for row in case['businesses'] + case['nonoperating_assets'] + case['claims']:
        _evidence({**case, 'basis': row['basis'], 'source_refs': row['source_refs']}, sources, cutoff)
    result = company_equity.bridge(case)
    if funding_gap:
        result.update(status='unknown', per_share=None, reason='unfunded_operating_path')
        result['receipt_hash'] = canonical_hash({k: v for k, v in result.items() if k != 'receipt_hash'})
    return result


def build(package, *, as_of):
    cutoff = stamp(as_of)
    if package.get("schema_version") != "1.0" or package.get("currency") != "USD" or package.get("scale") != "millions":
        raise ValueError("schema 1.0, USD and millions for both money and shares required")
    if not package.get("ticker"):
        raise ValueError("ticker required")
    sources = package["sources"]
    _evidence(package, sources, cutoff)
    if cutoff >= stamp(package["valid_until"]):
        raise ValueError("company input expired")
    operating_hold = package.get("operating_path_hold")
    if operating_hold is not None:
        if (not isinstance(operating_hold, dict)
                or operating_hold.get("status") != "unestimable"
                or not operating_hold.get("reason")):
            raise ValueError("operating path hold requires unestimable status and reason")
        _evidence(operating_hold, sources, cutoff)
    required = package["segment_names"]
    observed = package["observed"]
    _evidence(observed, sources, cutoff)
    if date.fromisoformat(observed["end"]) > cutoff.date():
        raise ValueError("reported quarter after cutoff")
    audit = reconcile(observed, required)
    if not package.get("opening_balance"):
        raise ValueError("dated disclosed opening_balance required; arbitrary scenario openings forbidden")
    opening = opening_balance(package["opening_balance"], sources=sources, cutoff=cutoff)
    out = {"model_version": VERSION, "as_of": as_of, "ticker": package["ticker"],
           "authority": "analysis_only", "input_hash": canonical_hash(package),
           "publication": {"status": "withheld", "reason": "company_model_not_yet_accepted"},
           "observed": audit, "opening_balance": opening, "scenarios": {}, "status": "research_only",
           "accuracy": "not_established", "sources": sources,
           "estimability": {"operating_value": "unestimable" if operating_hold else "unknown",
                            "reason": operating_hold["reason"] if operating_hold else None}}
    if audit["status"] != "reconciled" or opening["status"] != "reconciled":
        out["status"] = "input_mismatch"
    else:
        for scenario in package["scenarios"]:
            name = scenario["id"]
            if name in out["scenarios"]:
                raise ValueError("duplicate scenario id")
            _evidence(scenario, sources, cutoff)
            if scenario["opening_date"] != opening["date"]:
                raise ValueError("scenario opening date must match disclosed balance date")
            for key in ("opening_liquidity", "opening_debt", "opening_shares"):
                if abs(number(scenario[key], key) - opening["computed"][key]) > 1e-6:
                    raise ValueError(f"scenario {key} differs from disclosed balance; explicit intervening bridge required")
            liquid = number(scenario["opening_liquidity"], "opening_liquidity", minimum=0)
            debt = number(scenario["opening_debt"], "opening_debt", minimum=0)
            prior_end, rows = date.fromisoformat(scenario["opening_date"]), []
            for q in scenario["quarters"]:
                start, end = date.fromisoformat(q["start"]), date.fromisoformat(q["end"])
                bridge = q.get("unreported_bridge")
                period_status = "future_projection"
                if not 75 <= (end - start).days <= 105:
                    raise ValueError("explicit future quarter required")
                if end <= cutoff.date():
                    if rows or bridge is None or bridge.get("reported_results_available") is not False:
                        raise ValueError("explicit future quarter or first ended-unreported bridge required")
                    _evidence(bridge, sources, cutoff)
                    if (cutoff >= stamp(bridge["expires_at"])
                            or stamp(bridge["expires_at"]) - stamp(bridge["known_at"]) > timedelta(days=7)):
                        raise ValueError("ended-unreported bridge freshness expired or exceeds seven days")
                    if end <= date.fromisoformat(observed["end"]):
                        raise ValueError("unreported bridge must follow last actual quarter")
                    period_status = "ended_unreported_estimate"
                elif bridge is not None:
                    raise ValueError("unreported bridge only for an ended quarter")
                if start != prior_end + timedelta(days=1):
                    raise ValueError("forecast quarters must be consecutive")
                if any(r["id"] == q["id"] for r in rows):
                    raise ValueError("duplicate quarter id")
                e = earnings(q["segments"], q["income"], q["shares"], required)
                cash = liquidity(e, q["cash"], liquid, debt)
                if q["cash"]["equity_cash_raised"] > 0 and q["shares"]["issued"] <= 0:
                    raise ValueError("equity financing requires explicit issuance")
                prev_shares = rows[-1]["closing_shares"] if rows else scenario["opening_shares"]
                expected_shares = (number(prev_shares, "opening_shares", minimum=1e-12)
                                   + number(q["shares"]["issued"], "issued", minimum=0)
                                   - number(q["shares"]["repurchased"], "repurchased", minimum=0))
                if abs(expected_shares - e["closing_shares"]) > 1e-6:
                    raise ValueError("share roll-forward mismatch")
                rows.append({"id": q["id"], "start": q["start"], "end": q["end"],
                             "period_status": period_status, **e, "cash": cash})
                liquid, debt, prior_end = cash["ending_liquidity"], cash["ending_debt"], end
            if not rows:
                raise ValueError("forecast quarters required")
            gap = max(r["cash"]["funding_gap"] for r in rows)
            operating = ({'status': 'unknown', 'reason': 'operating_path_unestimable: ' + operating_hold['reason']}
                         if operating_hold else operating_value(scenario.get('operating_dcf'), rows, sources=sources, cutoff=cutoff))
            out["scenarios"][name] = {"basis": scenario["basis"], "quarters": rows,
                "assumptions": scenario.get("assumptions", {}),
                "earnings_windows": {label: earnings_window(rows, window["quarters"], window["basis"])
                                     for label, window in scenario.get("earnings_windows", {}).items()},
                "maximum_funding_gap": gap,
                "operating_value": operating,
                "business_equity": ({'status': 'unknown', 'reason': 'operating_path_unestimable'}
                                    if operating_hold else business_equity(scenario.get("business_equity"), rows, sources=sources, cutoff=cutoff, funding_gap=gap, operating=operating)),
                "target_12m": independent_multiple(scenario.get("multiple"), rows, cutoff=cutoff, sources=sources, funding_gap=gap, ticker=package["ticker"]),
                "intrinsic": owner_value(scenario.get("owner_dcf"), cutoff=cutoff, sources=sources, funding_gap=gap)}
        base = out['scenarios'].get('base', {}).get('operating_value', {})
        if not operating_hold and base.get('status') == 'research_sensitivity':
            out['estimability'] = {'operating_value': 'conditional_research',
                                   'reason': 'not_common_equity_or_current_share_price'}
        elif not operating_hold:
            out['estimability']['reason'] = base.get('reason', 'base_operating_path_missing')
    out["receipt_hash"] = canonical_hash(out)
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--output", type=Path, required=True, help="new detached research artifact")
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error("output exists; preserve previous forecast")
    result = build(read_json(args.input), as_of=args.as_of)
    atomic_write_json(args.output, result)
    print(f"{result['ticker']} {result['status']} publication=withheld {args.output}")
    return int(result["status"] == "input_mismatch")


if __name__ == "__main__":
    raise SystemExit(main())
