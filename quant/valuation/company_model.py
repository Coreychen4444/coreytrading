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
from quant.valuation import company_amortization, company_cashflow, company_content_capital, company_contract_costs, company_debt_costs, company_equity, company_leases, company_multiples, company_eps, company_ppe_runoff, company_share_settlements

VERSION = "company-v5.4.24-research"


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


def content_capital(case, quarters, *, opening_date, equity_case, sources, cutoff):
    """Keep CFO content cash separate from DCF capital and payable changes."""
    _evidence(case, sources, cutoff)
    result = company_content_capital.project(case)
    native = result['native_period_count']
    if (case['date'] != opening_date or native != len(quarters)
            or [(r['id'], r['start'], r['end']) for r in result['periods'][:native]]
               != [(q['id'], q['start'], q['end']) for q in quarters]):
        raise ValueError('content capital opening date and native quarters must match company')
    claims = {c['economic_id']: c for c in (equity_case or {}).get('claims', [])}
    if claims.get(result['incremental_contract_claim_id'], {}).get('settlement') != 'unresolved':
        raise ValueError('content book/cash bridge cannot resolve incremental content contract economics')
    for row in case['periods']:
        _evidence({**case, **row}, sources, cutoff)
    for row, q in zip(result['periods'][:native], quarters):
        cash = q['cash']; key = result['content_noncash_key']
        if key not in cash['noncash_adjustments']:
            raise ValueError('native content amortization addback key missing')
        comparisons = [(cash['noncash_adjustments'][key], row['content_amortization']),
                       (cash['depreciation_amortization'], row['noncontent_DA']),
                       (cash['capex_investing']+cash['capex_financing'], row['noncontent_cash_capital']),
                       (cash['working_capital_cash_change'], row['native_working_capital_cash_change']),
                       (sum(number(s['operating_profit'], 'native segment operating profit')
                            for s in q['segments'].values()), row['operating_profit'])]
        if any(abs(number(actual, 'native content binding')-expected) > 1e-6 for actual, expected in comparisons):
            raise ValueError('native profit, content cash or capital differ from content ledger')
    return result


def ordinary_share_cash_settlements(case, quarters, *, opening_date, opening_shares, equity_case, sources, cutoff):
    """Bind a future flow proxy once; current employee claim values stay unknown."""
    _evidence(case, sources, cutoff)
    result = company_share_settlements.project(case)
    if (case['date'] != opening_date
            or abs(result['opening_common_shares'] - number(opening_shares, 'linked opening shares')) > 1e-6
            or [(r['id'], r['start'], r['end']) for r in result['periods']]
               != [(q['id'], q['start'], q['end']) for q in quarters]):
        raise ValueError('ordinary flow ledger must match opening shares and every native quarter')
    claims = {c['economic_id']: c for c in (equity_case or {}).get('claims', [])}
    if any(claims.get(c, {}).get('settlement') != 'unresolved'
           for c in result['unresolved_current_claim_ids']):
        raise ValueError('ordinary flow proxy cannot value or remove unresolved current claims')
    for original, row, q in zip(case['periods'], result['periods'], quarters):
        for block in [original, *original['issuances'], original['repurchase'], original['other_financing']]:
            _evidence({**case, **block}, sources, cutoff)
        shares, cash = q['shares'], q['cash']
        comparisons = [(shares['issued'], row['gross_issued']),
                       (shares['repurchased'], row['repurchased']),
                       (shares['closing'], row['closing_common_shares']),
                       (cash['equity_cash_raised'], row['issuance_cash_proceeds']),
                       (cash['common_buybacks'], row['repurchase_cash'])]
        key = row['other_financing']['native_cash_key']
        if key not in cash['other_cash_flows']:
            raise ValueError('ordinary flow unassigned financing cash key missing from native cash')
        comparisons.append((cash['other_cash_flows'][key], row['unassigned_financing_cash']))
        if any(abs(number(actual, 'native ordinary flow') - expected) > 1e-6 for actual, expected in comparisons):
            raise ValueError('native ordinary shares or cash differ from settlement ledger')
    return result


def cash_capital_budget(case, quarters, *, opening_date, sources, cutoff, operating_hold):
    """Bind cash purchases to declared annual budgets; no total-capital inference."""
    _evidence(case, sources, cutoff)
    if (case.get('currency') != 'USD' or case.get('scale') != 'millions'
            or case.get('metric') != 'cash_purchases_of_PPE'
            or case.get('scope') != 'cash_only_partial_capital_population'
            or not operating_hold):
        raise ValueError('cash-only capital budget requires explicit USD scope and unresolved operating path hold')
    budgets, previous, rows = {}, None, []
    for item in case['annual_budgets']:
        _evidence({**case, **item}, sources, cutoff)
        ident = item['id']
        start, end = date.fromisoformat(item['start']), date.fromisoformat(item['end'])
        if (ident in budgets or not 364 <= (end - start).days <= 370
                or (previous is not None and start != previous + timedelta(days=1))):
            raise ValueError('unique consecutive annual cash budget periods required')
        amount = number(item['amount'], 'annual cash budget', minimum=0)
        actual, actual_through = 0.0, None
        if item.get('reported_actual') is not None:
            observed = item['reported_actual']
            _evidence({**case, **observed}, sources, cutoff)
            actual_through = date.fromisoformat(observed['through'])
            if (date.fromisoformat(observed['start']) != start
                    or not start <= actual_through == date.fromisoformat(opening_date) <= end):
                raise ValueError('actual cash period must end at company opening date within budget year')
            actual = number(observed['amount'], 'reported actual cash purchases', minimum=0)
        else:
            if start <= date.fromisoformat(opening_date):
                raise ValueError('cash budget overlapping opening date requires reported actual scope')
        guidance = item.get('guidance_range')
        if guidance is not None:
            _evidence({**case, **guidance}, sources, cutoff)
            if guidance.get('metric_interpretation') != 'same_cash_metric_assumption':
                raise ValueError('issuer guidance requires explicit same cash metric interpretation')
            low = number(guidance['low'], 'guidance low', minimum=0)
            high = number(guidance['high'], 'guidance high', minimum=0)
            if not low <= amount <= high:
                raise ValueError('declared cash scenario outside same-scope guidance range')
        growth = item.get('growth_assumption')
        if growth is not None:
            _evidence({**case, **growth}, sources, cutoff)
            prev = budgets[growth['previous_budget_id']]
            if prev['end'] + timedelta(days=1) != start or growth.get('status') != 'scenario_assumption':
                raise ValueError('cash growth must be a dated scenario assumption from preceding budget')
            rate = number(growth['rate'], 'cash budget growth', minimum=-1)
            if abs(prev['amount'] * (1 + rate) - amount) > 1e-6:
                raise ValueError('annual cash budget differs from declared growth assumption')
        matched = [q for q in quarters if start <= date.fromisoformat(q['start']) <= date.fromisoformat(q['end']) <= end]
        if not matched:
            raise ValueError('annual cash budget must cover explicit forecast quarters')
        prior_end = actual_through or start - timedelta(days=1)
        for q in matched:
            if date.fromisoformat(q['start']) != prior_end + timedelta(days=1):
                raise ValueError('cash budget actual and forecast periods must cover consecutive dates')
            prior_end = date.fromisoformat(q['end'])
        projected = sum(number(q['cash']['capex_investing'], 'linked cash PPE purchases', minimum=0) for q in matched)
        remainder = number(item['unmodelled_remainder_cash'], 'cash after forecast horizon', minimum=0)
        if prior_end != date.fromisoformat(quarters[-1]['end']) and prior_end != end:
            raise ValueError('partial cash budget only allowed at forecast horizon')
        if prior_end == end and remainder != 0:
            raise ValueError('complete cash budget cannot contain unmodelled remainder')
        if prior_end < end and item.get('remainder_status') != 'outside_forecast_horizon_scenario_assumption':
            raise ValueError('partial-year cash remainder must be an explicit scenario assumption')
        if abs(actual + projected + remainder - amount) > 1e-6:
            raise ValueError('reported plus projected plus remaining cash must reconcile annual budget')
        budgets[ident] = {'start': start, 'end': end, 'amount': amount}
        previous = end
        rows.append({'id': ident, 'start': str(start), 'end': str(end), 'annual_cash_budget': amount,
                     'reported_actual_cash': actual, 'actual_through': str(actual_through) if actual_through else None,
                     'forecast_quarter_ids': [q['id'] for q in matched], 'projected_cash_in_forecast': projected,
                     'unmodelled_remainder_cash': remainder, 'guidance_range': deepcopy(guidance),
                     'growth_assumption': deepcopy(growth)})
    if not rows or sum(len(r['forecast_quarter_ids']) for r in rows) != len(quarters):
        raise ValueError('cash budget must cover every forecast quarter exactly once')
    result = {'status': 'conditional_research', 'metric': case['metric'], 'scope': case['scope'],
              'annual_budgets': rows, 'input_hash': canonical_hash(case),
              'source_completeness': 'not_established', 'publication': 'withheld',
              'limitations': ['Cash purchases only; supplier accruals, noncash financed assets, prepayments and acquisitions are not certified covered.',
                              'Guidance metric mapping, quarterly allocations and future annual growth are explicit assumptions.',
                              'Physical readiness, depreciation and capacity costs remain unresolved; no operating-value release.']}
    result['receipt_hash'] = canonical_hash(result)
    return result


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
               "interest_cash_excludes_capitalized", "operating_service_expense",
               "operating_service_cash_paid", "operating_service_expense_is_in_ebit",
               "legal_settlement", "tax_cash_basis"}
    if set(block) - allowed or not block.get("basis"):
        raise ValueError("income cash timing: explicit supported basis required")
    if block.get("other_entries_exclude_selected_timing_items") is not True:
        raise ValueError("income cash timing must exclude selected items from WC and other adjustments")
    parts = {}
    if "tax_cash_paid_net" in block:
        parts["tax"] = result["tax_expense"] - number(block["tax_cash_paid_net"], "net tax cash")
    tax_basis = block.get('tax_cash_basis')
    if 'tax_cash_basis' in block:
        if (tax_basis != 'modeled_provision_cash_equivalence'
                or 'tax' not in parts or abs(parts['tax']) > 1e-6):
            raise ValueError('tax cash equivalence requires cash equal to modeled provision')
    service = {"operating_service_expense", "operating_service_cash_paid"}
    if service & set(block):
        if not service.issubset(block) or block.get('operating_service_expense_is_in_ebit') is not True:
            raise ValueError('both selected service expense in EBIT and operating cash payment required')
        parts['operating_service'] = (number(block['operating_service_expense'],'selected service expense',minimum=0)
                                      - number(block['operating_service_cash_paid'],'selected service cash',minimum=0))
    legal = block.get('legal_settlement')
    if 'legal_settlement' in block:
        required = {'basis', 'expense', 'cash_paid', 'expense_is_in_ebit',
                    'operating_profit_before_expense', 'timing_key', 'scope'}
        if (not isinstance(legal, dict) or set(legal) != required or not legal.get('basis')
                or legal.get('scope') != 'selected_conditional_operating_settlement'
                or legal.get('expense_is_in_ebit') is not True
                or not isinstance(legal.get('timing_key'), str) or not legal['timing_key'].strip()):
            raise ValueError('explicit selected legal expense/payment and operating scope required')
        expense = number(legal['expense'], 'selected legal expense', minimum=0)
        paid = number(legal['cash_paid'], 'selected legal cash', minimum=0)
        before = number(legal['operating_profit_before_expense'], 'preselected legal EBIT')
        if abs(before - expense - result['operating_profit']) > 1e-6:
            raise ValueError('selected legal expense must reconcile EBIT')
        key = legal['timing_key']
        if key in cash['other_cash_flows'] or key in cash['noncash_adjustments']:
            raise ValueError('selected legal settlement must not repeat in other cash or noncash entries')
        parts['legal_settlement'] = expense - paid
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
    out = {"status": "explicit_conditional_bridge", "components": parts,
           "adjustment": sum(parts.values()), "basis": block["basis"]}
    if tax_basis is not None:
        out.update(tax_cash_basis=tax_basis, tax_provision=result['tax_expense'],
                   tax_cash_paid_net=block['tax_cash_paid_net'],
                   tax_payment_status='scenario_assumption_not_verified_payment')
    if legal is not None:
        out['selected_legal_settlement'] = {'expense': expense, 'cash_paid': paid,
            'scope': legal['scope'], 'basis': legal['basis'], 'timing_key': key,
            'source_completeness': 'not_established'}
    return out


def note_principal_cash(case, quarters, *, sources, cutoff):
    """Selected nominal maturity payments only, not debt valuation or coupon cash."""
    _evidence(case, sources, cutoff)
    if (case.get('currency') != 'USD' or case.get('scale') != 'millions'
            or case.get('scope') != 'selected_contractual_note_principal_only'
            or case.get('execution_assumption') != 'contractual_maturity_no_early_redemption_refinancing'
            or not case.get('payment_date_basis')):
        raise ValueError('explicit selected contractual nominal principal scope required')
    opening = date.fromisoformat(case['date'])
    end = date.fromisoformat(quarters[-1]['end'])
    nominal = number(case['selected_opening_nominal'], 'selected note nominal', minimum=1e-12)
    exclusions = case.get('unresolved_economics')
    if not isinstance(exclusions, list) or not exclusions:
        raise ValueError('unresolved debt book, coupon and financing economics must remain explicit')
    names = set()
    for item in exclusions:
        if (not isinstance(item, dict) or not item.get('economic_id') or item['economic_id'] in names
                or item.get('status') != 'unresolved'):
            raise ValueError('unique unresolved debt economics required')
        names.add(item['economic_id'])
        _evidence({**case, **item}, sources, cutoff)
    payments = case.get('payments')
    if not isinstance(payments, list) or not payments:
        raise ValueError('complete selected native principal maturity schedule required')
    parsed = []; names = set()
    for item in payments:
        if not isinstance(item, dict):
            raise ValueError('structured selected maturity payment required')
        _evidence({**case, **item}, sources, cutoff)
        due = date.fromisoformat(item['contractual_due'])
        if not item.get('economic_id') or item['economic_id'] in names or not opening < due <= end:
            raise ValueError('unique selected future maturity inside native horizon required')
        names.add(item['economic_id'])
        parsed.append((due, number(item['amount'], 'selected principal payment', minimum=1e-12)))
    if abs(sum(v for _, v in parsed) - nominal) > 1e-6:
        raise ValueError('selected maturity payments must exhaust selected nominal')
    periods = case.get('periods')
    if (not isinstance(periods, list) or len(periods) != len(quarters)
            or any(not isinstance(r, dict) for r in periods)
            or [(r['start'], r['end']) for r in periods] != [(q['start'], q['end']) for q in quarters]):
        raise ValueError('selected note principal periods must match all native quarters')
    remaining = nominal; rows = []
    for period, q in zip(periods, quarters):
        _evidence({**case, **period}, sources, cutoff)
        a, b = date.fromisoformat(q['start']), date.fromisoformat(q['end'])
        selected = sum(v for due, v in parsed if a <= due <= b)
        other = number(period['other_debt_principal_paid'], 'other principal cash', minimum=0)
        total = selected + other
        if abs(number(q['cash']['debt_repaid'], 'native debt principal cash', minimum=0) - total) > 1e-6:
            raise ValueError('native principal cash differs from selected maturity plus other payments')
        remaining -= selected
        rows.append({'start':q['start'], 'end':q['end'], 'selected_principal_paid':selected,
                     'other_principal_paid':other, 'total_debt_principal_paid':total,
                     'remaining_selected_nominal':remaining})
    if abs(remaining) > 1e-6:
        raise ValueError('native quarters do not cover selected maturities')
    out = {'status':'conditional_research', 'scope':case['scope'], 'date':case['date'],
           'selected_opening_nominal':nominal, 'selected_principal_paid_total':nominal,
           'payment_date_basis':case['payment_date_basis'], 'periods':rows,
           'unresolved_economics':deepcopy(exclusions), 'input_hash':canonical_hash(case),
           'source_completeness':'not_established', 'publication':'withheld',
           'limitations':['Contractual maturity cash under no-call/no-refinancing scenario, not proof of future transfers.',
               'Selected nominal is not net carrying debt or full fair value; discount amortization, coupons, credit and capital remain separately unresolved.']}
    out['receipt_hash'] = canonical_hash(out)
    return out


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
    obligation = cash.get('financing_obligation_cash')
    financing_net = 0.0
    if 'financing_obligation_cash' in cash:
        if (not isinstance(obligation, dict)
                or set(obligation) != {'basis', 'population_basis', 'cash_paid', 'cash_received',
                    'excluded_from_debt_rollforward', 'not_new_operating_capital',
                    'other_entries_exclude_this_population'}
                or not obligation.get('basis')
                or obligation.get('population_basis') != 'selected_obligations_outside_recourse_nonrecourse_debt'
                or obligation.get('excluded_from_debt_rollforward') is not True
                or obligation.get('not_new_operating_capital') is not True
                or obligation.get('other_entries_exclude_this_population') is not True):
            raise ValueError('explicit segregated financing-obligation cash population required')
        paid = number(obligation['cash_paid'], 'financing obligation cash paid', minimum=0)
        received = number(obligation['cash_received'], 'financing obligation cash received', minimum=0)
        financing_net = received - paid
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
              - x["common_dividends"] - x["common_buybacks"] + financing_net)
    debt_non_cash = number(cash["debt_non_cash_change"], "debt_non_cash_change")
    debt = opening_debt + x["debt_issued"] - x["debt_repaid"] + debt_non_cash
    if debt < 0:
        raise ValueError("debt repayment exceeds available principal")
    out = {"operating_cash_flow": ocf, "total_capex": total_capex,
            "income_cash_timing": timing,
            "cash_before_external_financing": pre_financing,
            "ending_liquidity": ending, "ending_debt": debt, "debt_non_cash_change": debt_non_cash,
            "funding_gap": max(0.0, x["minimum_liquidity"] - ending)}
    if obligation is not None:
        out['financing_obligation_cash'] = {'status': 'conditional_research',
            'population_basis': obligation['population_basis'], 'basis': obligation['basis'],
            'cash_paid': paid, 'cash_received': received, 'net_cash': financing_net,
            'debt_rollforward_effect': 0.0, 'new_operating_capital': 0.0,
            'book_rollforward_verified': False, 'publication': 'withheld', 'validated': False}
    return out


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


def operating_value(case, quarters, *, sources, cutoff, amortization=None, content=None):
    if case is None:
        return {'status': 'unknown', 'reason': 'explicit_operating_cashflows_and_terminal_missing'}
    _evidence(case, sources, cutoff)
    if date.fromisoformat(case['date']) < cutoff.date():
        raise ValueError('operating value date cannot precede valuation cutoff')
    for row in case['periods'] + [case['terminal']]:
        _evidence({**case, 'basis': row['basis'], 'source_refs': row['source_refs']}, sources, cutoff)
    if amortization is not None and amortization.get('require_exhaustion_before_terminal'):
        last = amortization['periods'][-1]
        if (last['end'] != case['terminal']['date']
                or last['remaining_existing'] != 0 or last['remaining_iprd'] != 0):
            raise ValueError('selected intangible exhaustion must precede the terminal at the matching final period')
    if content is not None:
        selected = [r for r in content['periods'] if r['end'] > case['date']]
        boundaries = {content['date'], *(r['end'] for r in content['periods'])}
        if (case['date'] not in boundaries
                or [(r['start'], r['end']) for r in selected]
                   != [(r['start'], r['end']) for r in case['periods']]
                or content['periods'][-1]['end'] != case['terminal']['date']):
            raise ValueError('content capital must cover every whole DCF period through terminal date')
        for row, linked in zip(case['periods'], selected):
            for field, target in (('operating_profit', 'operating_profit'),
                                  ('depreciation_amortization', 'dcf_depreciation_amortization'),
                                  ('gross_capital_investment', 'dcf_gross_capital_investment'),
                                  ('working_capital_increase', 'dcf_working_capital_increase')):
                if abs(number(row[field], 'DCF content binding')-linked[target]) > 1e-6:
                    raise ValueError('DCF economics differ from content capital ledger: ' + field)
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
        if amortization is not None:
            matches = [a for a in amortization['periods'] if a['start'] == row['start'] and a['end'] == row['end']]
            if len(matches) != 1:
                raise ValueError('DCF period must match one acquired intangible ledger period')
            linked = matches[0]
            for field, target in (('operating_profit', 'operating_profit'),
                                  ('depreciation_amortization', 'dcf_depreciation_amortization' if 'operating_capital' in linked else 'total_da'),
                                  ('gross_capital_investment', 'gross_capital_investment')):
                if abs(number(row[field], field) - linked[target]) > 1e-6:
                    raise ValueError('DCF economics differ from acquired intangible ledger: ' + field)
            if ('operating_capital' in linked
                    and abs(number(row['working_capital_increase'], 'DCF working capital')
                            - linked['dcf_working_capital_increase']) > 1e-6):
                raise ValueError('DCF economics differ from acquired intangible ledger: working capital')
    return company_cashflow.value(case)


def business_equity(case, quarters, *, sources, cutoff, funding_gap, operating=None):
    if case is None:
        return {"status": "unknown", "reason": "business_values_and_equity_claims_missing"}
    _evidence(case, sources, cutoff)
    case = deepcopy(case)
    allow_unresolved = case.get('allow_unresolved_operating_inventory', False)
    if not isinstance(allow_unresolved, bool) or (allow_unresolved and case.get('use_consolidated_dcf') is not True):
        raise ValueError('unresolved operating inventory requires explicit consolidated host binding')
    if case.get('use_consolidated_dcf'):
        if case['required_business_ids'] != ['consolidated'] or case['businesses']:
            raise ValueError('consolidated DCF replaces all business values; separate segment values forbidden')
        part = {'economic_id': 'consolidated', 'date': case['date'], 'source_refs': case['source_refs']}
        if (allow_unresolved and operating and operating.get('status') == 'unknown'
                and isinstance(operating.get('reason'), str) and operating['reason'].strip()):
            part.update(valuation_status='unresolved', basis='Host operating value unresolved: ' + operating['reason'])
        else:
            if not operating or operating['status'] != 'research_sensitivity' or operating['date'] != case['date']:
                raise ValueError('matching dated consolidated DCF required')
            part.update(value=operating['operating_value'],
                        basis='host-calculated consolidated DCF ' + operating['receipt_hash'])
        case['businesses'] = [part]
    matches = [q for q in quarters if q['end'] == case['date']]
    if len(matches) != 1:
        raise ValueError('equity bridge date must match an explicit forecast quarter end')
    if abs(number(case['outstanding_shares'], 'bridge shares') - matches[0]['closing_shares']) > 1e-6:
        raise ValueError('equity bridge shares must match forecast closing shares, excluding separately modelled claims')
    cash_links = []
    if (any(a.get('forecast_liquidity_surplus') is not None for a in case['nonoperating_assets'])
            and sum(a.get('basis_type') == 'excess_unrestricted_cash'
                    for a in case['nonoperating_assets']) != 1):
        raise ValueError('linked total liquidity cannot coexist with another excess cash asset')
    for asset in case['nonoperating_assets']:
        surplus = asset.get('forecast_liquidity_surplus')
        if surplus is None:
            continue
        if (cash_links or not isinstance(surplus, dict)
                or asset['basis_type'] != 'excess_unrestricted_cash'
                or asset.get('valuation_status') != 'scenario_linked' or 'value' in asset):
            raise ValueError('one explicitly linked excess cash asset without a supplied value required')
        if (surplus.get('reserve_status') != 'scenario_assumption'
                or surplus.get('pool_basis') != 'scenario_unrestricted_liquidity'
                or not isinstance(surplus.get('unrestricted_pool_basis'), str)
                or not surplus['unrestricted_pool_basis'].strip()
                or not isinstance(surplus.get('reserve_basis'), str)
                or not surplus['reserve_basis'].strip()):
            raise ValueError('explicit unrestricted pool interpretation and scenario reserve basis required')
        reserve = number(surplus.get('operating_reserve'), 'operating cash reserve', minimum=0)
        liquidity = number(matches[0]['cash']['ending_liquidity'], 'forecast closing liquidity')
        value = max(0.0, liquidity - reserve)
        asset.update(valuation_status='valued', value=value)
        cash_links.append({'economic_id': asset['economic_id'], 'date': case['date'],
                           'status': 'research_sensitivity', 'ending_liquidity': liquidity,
                           'operating_reserve': reserve, 'value': value,
                           'reserve_basis': surplus['reserve_basis'],
                           'unrestricted_pool_basis': surplus['unrestricted_pool_basis'],
                           'source_completeness': 'not_established'})
    for row in case['businesses'] + case['nonoperating_assets'] + case['claims']:
        _evidence({**case, 'basis': row['basis'], 'source_refs': row['source_refs']}, sources, cutoff)
        if row.get('settlement') == 'promised_cashflow_component':
            if 'translation' in row:
                translation = row['translation']
                if not isinstance(translation, dict):
                    raise ValueError('explicit translation evidence required')
                _evidence({**case, 'basis': translation['basis'], 'source_refs': translation['source_refs']}, sources, cutoff)
            for payment in row['payments']:
                _evidence({**case, 'basis': payment['basis'], 'source_refs': payment['source_refs']}, sources, cutoff)
    result = company_equity.bridge(case)
    if cash_links:
        result['forecast_liquidity_surplus'] = cash_links
        result['receipt_hash'] = canonical_hash({k: v for k, v in result.items() if k != 'receipt_hash'})
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
            budget_case = scenario.get('cash_capital_budget')
            budget = (cash_capital_budget(budget_case, scenario['quarters'], opening_date=opening['date'],
                                         sources=sources, cutoff=cutoff, operating_hold=operating_hold)
                      if budget_case is not None else None)
            lease_case = scenario.get('finance_lease_runoff')
            leases = None
            if lease_case is not None:
                _evidence(lease_case, sources, cutoff)
                if lease_case['date'] != opening['date']:
                    raise ValueError('lease opening date must match company opening balance')
                for row in lease_case['payment_buckets'] + lease_case['periods']:
                    _evidence({**lease_case, 'basis': row['basis'], 'source_refs': row['source_refs']}, sources, cutoff)
                leases = company_leases.project(lease_case)
                if [(r['start'], r['end']) for r in leases['periods']] != [(q['start'], q['end']) for q in scenario['quarters']]:
                    raise ValueError('lease periods must match all company forecast quarters')
            amort_case = scenario.get('acquired_intangible_runoff')
            contract_case = scenario.get('selected_contract_costs')
            contracts = None
            if contract_case is not None:
                if not operating_hold:
                    raise ValueError('partial service-cost ledger requires unresolved full operating path hold')
                _evidence(contract_case,sources,cutoff)
                if contract_case['date'] != opening['date']:
                    raise ValueError('selected contract opening date must match company balance date')
                for row in ([contract_case['disclosed_commencement_window']] + contract_case['payments']
                            + contract_case['periods'] + contract_case['unresolved_scope']):
                    _evidence({**contract_case,'basis':row['basis'],'source_refs':row['source_refs']},sources,cutoff)
                contracts=company_contract_costs.project(contract_case)
                if [(r['start'],r['end']) for r in contracts['periods']] != [(q['start'],q['end']) for q in scenario['quarters']]:
                    raise ValueError('selected contract periods must match all forecast quarters')
            ppe_case = scenario.get('selected_ppe_runoff')
            ppe = None
            if ppe_case is not None:
                if not operating_hold:
                    raise ValueError('partial selected PPE ledger requires unresolved full operating path hold')
                _evidence(ppe_case, sources, cutoff)
                if ppe_case['date'] != opening['date']:
                    raise ValueError('selected PPE opening date must match company balance date')
                for row in [ppe_case['reference_quarter']] + ppe_case['excluded_populations'] + ppe_case['periods']:
                    _evidence({**ppe_case, 'basis':row['basis'], 'source_refs':row['source_refs']}, sources, cutoff)
                    if row.get('income_cash_binding') is not None:
                        _evidence({**ppe_case, **row['income_cash_binding']}, sources, cutoff)
                ppe = company_ppe_runoff.project(ppe_case)
                if (ppe['native_period_count'] != len(scenario['quarters'])
                        or [(r['start'],r['end']) for r in ppe['periods'][:ppe['native_period_count']]]
                           != [(q['start'],q['end']) for q in scenario['quarters']]):
                    raise ValueError('selected PPE native prefix must match all forecast quarters')
            debt_cost_case = scenario.get('debt_cost_runoff')
            debt_costs = None
            if debt_cost_case is not None:
                _evidence(debt_cost_case, sources, cutoff)
                if debt_cost_case['date'] != opening['date']:
                    raise ValueError('note cost opening date must match company opening balance')
                for row in debt_cost_case['periods']:
                    _evidence({**debt_cost_case, 'basis': row['basis'], 'source_refs': row['source_refs']}, sources, cutoff)
                debt_costs = company_debt_costs.project(debt_cost_case)
                if (abs(debt_costs['opening_carrying'] + debt_costs['other_opening_debt']
                        - opening['computed']['opening_debt']) > 1e-6
                        or [(r['start'],r['end']) for r in debt_costs['periods'][:len(scenario['quarters'])]]
                           != [(q['start'],q['end']) for q in scenario['quarters']]):
                    raise ValueError('note cost population and prefix periods must match company debt and forecast')
            amortization = None
            if amort_case is not None:
                _evidence(amort_case, sources, cutoff)
                if amort_case['date'] != opening['date']:
                    raise ValueError('acquired intangible opening date must match company opening balance')
                for row in amort_case['periods']:
                    _evidence({**amort_case, 'basis': row['basis'], 'source_refs': row['source_refs']}, sources, cutoff)
                    if isinstance(row.get('operating_capital'), dict):
                        _evidence({**amort_case, **row['operating_capital']}, sources, cutoff)
                    if isinstance(row.get('other_non_cash_operating_capital'), dict):
                        _evidence({**amort_case, **row['other_non_cash_operating_capital']}, sources, cutoff)
                for asset in amort_case.get('excluded_nonamortizing_assets', []):
                    _evidence({**amort_case, 'basis': asset['basis'], 'source_refs': asset['source_refs']}, sources, cutoff)
                amortization = company_amortization.project(amort_case)
            if scenario["opening_date"] != opening["date"]:
                raise ValueError("scenario opening date must match disclosed balance date")
            principal_case = scenario.get('note_principal_cash')
            principal = None
            if principal_case is not None:
                if principal_case['date'] != opening['date']:
                    raise ValueError('selected note principal opening date differs from company balance')
                principal = note_principal_cash(principal_case, scenario['quarters'], sources=sources, cutoff=cutoff)
            for key in ("opening_liquidity", "opening_debt", "opening_shares"):
                if abs(number(scenario[key], key) - opening["computed"][key]) > 1e-6:
                    raise ValueError(f"scenario {key} differs from disclosed balance; explicit intervening bridge required")
            settlement_case = scenario.get('ordinary_share_cash_settlements')
            settlements = (ordinary_share_cash_settlements(
                settlement_case, scenario['quarters'], opening_date=opening['date'],
                opening_shares=opening['computed']['opening_shares'], equity_case=scenario.get('business_equity'),
                sources=sources, cutoff=cutoff) if settlement_case is not None else None)
            content_case = scenario.get('content_capital')
            content = None
            if content_case is not None:
                if operating_hold:
                    if scenario.get('operating_dcf') is not None:
                        raise ValueError('held content ledger must archive the active operating DCF')
                elif scenario.get('operating_dcf') is None:
                    raise ValueError('content capital requires an explicit linked conditional operating DCF')
                content = content_capital(content_case, scenario['quarters'], opening_date=opening['date'],
                                          equity_case=scenario.get('business_equity'), sources=sources, cutoff=cutoff)
                if operating_hold:
                    content['operating_dcf_binding_status'] = 'inactive_whole_operating_path_hold'
                    content['receipt_hash'] = canonical_hash({k:v for k,v in content.items() if k != 'receipt_hash'})
            liquid = number(scenario["opening_liquidity"], "opening_liquidity", minimum=0)
            debt = number(scenario["opening_debt"], "opening_debt", minimum=0)
            prior_end, rows = date.fromisoformat(scenario["opening_date"]), []
            for q in scenario["quarters"]:
                start, end = date.fromisoformat(q["start"]), date.fromisoformat(q["end"])
                bridge = q.get("unreported_bridge")
                # Fiscal end dates are inclusive calendar dates. Compare in
                # the supplied cutoff's calendar; a same-day end remains a
                # projection, never evidence that results are reported.
                period_status = ("current_period_projection" if start <= cutoff.date() <= end
                                 else "future_projection")
                if not 75 <= (end - start).days <= 105:
                    raise ValueError("explicit future quarter required")
                if end < cutoff.date():
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
                if contracts is not None:
                    cost_row=contracts['periods'][len(rows)]
                    timing=q['cash'].get('income_cash_timing') or {}
                    key=contracts['cash_timing_key']
                    if (abs(e['operating_profit']-cost_row['operating_profit']) > 1e-6
                            or abs(number(timing.get('operating_service_expense'),'linked service expense')-cost_row['expense']) > 1e-6
                            or abs(number(timing.get('operating_service_cash_paid'),'linked service cash')-cost_row['cash_paid']) > 1e-6
                            or timing.get('operating_service_expense_is_in_ebit') is not True
                            or key in q['cash']['other_cash_flows'] or key in q['cash']['noncash_adjustments']):
                        raise ValueError('quarter expense or cash timing differs from selected service contract ledger')
                if ppe is not None:
                    ppe_row=ppe['periods'][len(rows)]
                    if (abs(e['operating_profit']-ppe_row['operating_profit']) > 1e-6
                            or abs(number(q['cash']['depreciation_amortization'],'linked physical cash DA')-ppe_row['total_cash_da']) > 1e-6):
                        raise ValueError('quarter expense or cash DA differs from selected PPE ledger')
                if debt_costs is not None:
                    cost_row = debt_costs['periods'][len(rows)]
                    if (q['income'].get('nonoperating_basis') != 'combined_net'
                            or abs(number(q['income']['net_nonoperating'], 'linked note cost income') - cost_row['net_nonoperating']) > 1e-6
                            or abs(number(q['cash']['debt_non_cash_change'], 'linked note cost debt') - cost_row['debt_non_cash_change']) > 1e-6
                            or abs(number(q['cash']['noncash_adjustments'].get(debt_costs['noncash_adjustment_key']), 'linked note cost addback') - cost_row['amortization']) > 1e-6
                            or abs(number(q['cash']['debt_repaid'], 'linked note principal repayment') - cost_row['debt_principal_repaid']) > 1e-6):
                        raise ValueError('quarter income, noncash addback or debt differs from finite note cost ledger')
                if leases is not None:
                    lease_row = leases['periods'][len(rows)]
                    if (q['income'].get('nonoperating_basis') != 'combined_net'
                            or abs(number(q['income']['net_nonoperating'], 'linked lease income') - lease_row['net_nonoperating']) > 1e-6
                            or abs(number(q['cash']['lease_principal'], 'linked lease principal') - lease_row['principal']) > 1e-6):
                        raise ValueError('quarter principal or income differs from finance lease ledger')
                if amortization is not None:
                    matches = [a for a in amortization['periods'] if a['start'] == q['start'] and a['end'] == q['end']]
                    if len(matches) != 1:
                        raise ValueError('quarter must match one acquired intangible ledger period')
                    linked = matches[0]
                    if ppe is not None and abs(linked['total_da']-linked['acquired_amortization']-ppe_row['physical_da']) > 1e-6:
                        raise ValueError('selected physical and acquired amortization populations overlap or differ')
                    for actual, expected in ((e['operating_profit'], linked['operating_profit']),
                            (q['cash']['depreciation_amortization'], linked['total_da']),
                            (q['cash']['capex_investing'], linked['cash_new_assets']),
                            (q['cash']['capex_financing'], linked['technology_license_principal_paid'])):
                        if abs(number(actual, 'linked quarter economics') - expected) > 1e-6:
                            raise ValueError('quarter economics differ from acquired intangible ledger')
                    capital = linked.get('operating_capital')
                    physical = linked.get('other_non_cash_operating_capital')
                    if physical is not None:
                        if (physical['binding_scope'] != 'native_cash_debt'
                                or abs(number(q['cash']['lease_principal'], 'linked physical lease principal')-physical['lease_principal_paid']) > 1e-6
                                or abs(number(q['cash'].get('debt_non_cash_change'), 'linked physical lease debt')-linked['physical_lease_debt_non_cash_change']) > 1e-6):
                            raise ValueError('physical finance-lease assets, principal and noncash debt must match the intangible capital ledger')
                    if capital is not None:
                        amount = q['cash']['noncash_adjustments'].get(capital['noncash_adjustment_key'])
                        if (abs(number(amount, 'linked operating capital noncash adjustment') - capital['amortization']) > 1e-6
                                or abs(number(q['cash']['working_capital_cash_change'], 'linked operating capital WC')
                                       - (capital['other_working_capital_cash_change'] - capital['cash_investment'])) > 1e-6):
                            raise ValueError('quarter contract capital differs from acquired intangible ledger')
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
                if leases is not None:
                    cash['ending_opening_population_finance_lease_liability'] = lease_row['closing_liability']
                liquid, debt, prior_end = cash["ending_liquidity"], cash["ending_debt"], end
            if not rows:
                raise ValueError("forecast quarters required")
            gap = max(r["cash"]["funding_gap"] for r in rows)
            operating = ({'status': 'unknown', 'reason': 'operating_path_unestimable: ' + operating_hold['reason']}
                         if operating_hold else operating_value(scenario.get('operating_dcf'), rows, sources=sources, cutoff=cutoff, amortization=amortization, content=content))
            out["scenarios"][name] = {"basis": scenario["basis"], "quarters": rows,
                "assumptions": scenario.get("assumptions", {}),
                "earnings_windows": {label: earnings_window(rows, window["quarters"], window["basis"])
                                     for label, window in scenario.get("earnings_windows", {}).items()},
                "maximum_funding_gap": gap,
                "operating_value": operating,
                "business_equity": ({'status': 'unknown', 'reason': 'operating_path_unestimable'}
                                    if operating_hold and not (scenario.get('business_equity') or {}).get('allow_unresolved_operating_inventory')
                                    else business_equity(scenario.get("business_equity"), rows, sources=sources, cutoff=cutoff, funding_gap=gap, operating=operating)),
                "target_12m": independent_multiple(scenario.get("multiple"), rows, cutoff=cutoff, sources=sources, funding_gap=gap, ticker=package["ticker"]),
                "intrinsic": owner_value(scenario.get("owner_dcf"), cutoff=cutoff, sources=sources, funding_gap=gap)}
            if amortization is not None:
                out['scenarios'][name]['acquired_intangible_runoff'] = amortization
            if contracts is not None:
                out['scenarios'][name]['selected_contract_costs'] = contracts
            if ppe is not None:
                out['scenarios'][name]['selected_ppe_runoff'] = ppe
            if leases is not None:
                out['scenarios'][name]['finance_lease_runoff'] = leases
            if debt_costs is not None:
                out['scenarios'][name]['debt_cost_runoff'] = debt_costs
            if budget is not None:
                out['scenarios'][name]['cash_capital_budget'] = budget
            if principal is not None:
                out['scenarios'][name]['note_principal_cash'] = principal
            if settlements is not None:
                out['scenarios'][name]['ordinary_share_cash_settlements'] = settlements
            if content is not None:
                out['scenarios'][name]['content_capital'] = content
            selected_obligations = [(q, q['cash']['financing_obligation_cash']) for q in rows
                                    if 'financing_obligation_cash' in q['cash']]
            if selected_obligations:
                out['scenarios'][name]['native_financing_obligation_cash'] = {
                    'status': 'conditional_research',
                    'scope': 'selected_obligations_outside_recourse_nonrecourse_debt',
                    'native_period_count': len(rows), 'selected_period_count': len(selected_obligations),
                    'cash_paid': sum(t['cash_paid'] for _, t in selected_obligations),
                    'cash_received': sum(t['cash_received'] for _, t in selected_obligations),
                    'net_cash': sum(t['net_cash'] for _, t in selected_obligations),
                    'periods': [{'start': q['start'], 'end': q['end'], **t} for q, t in selected_obligations],
                    'book_rollforward_verified': False, 'publication': 'withheld', 'validated': False,
                    'limitations': ['Selected existing financing cash is excluded from new operating capital and the recourse/nonrecourse debt bridge.',
                        'Other cash and capital inputs must exclude this same population; labels do not prove absence of overlap.',
                        'Cash amounts do not establish contractual timing, full carrying balances, interest allocation or fair value.']}
            selected_taxes = [(q, q['cash']['income_cash_timing']) for q in rows
                              if q['cash']['income_cash_timing'].get('tax_cash_basis')
                              == 'modeled_provision_cash_equivalence']
            if selected_taxes:
                out['scenarios'][name]['native_cash_tax_basis'] = {
                    'scope': 'modeled_provision_cash_equivalence',
                    'status': 'conditional_research', 'native_period_count': len(rows),
                    'selected_period_count': len(selected_taxes),
                    'tax_provision': sum(t['tax_provision'] for _, t in selected_taxes),
                    'net_cash_tax': sum(t['tax_cash_paid_net'] for _, t in selected_taxes),
                    'timing_adjustment': sum(t['components']['tax'] for _, t in selected_taxes),
                    'periods': [{'start': q['start'], 'end': q['end'],
                                'tax_provision': t['tax_provision'],
                                'net_cash_tax': t['tax_cash_paid_net'], 'basis': t['basis']}
                               for q, t in selected_taxes],
                    'payment_status': 'scenario_assumption_not_verified_payment',
                    'publication': 'withheld', 'validated': False,
                    'limitations': ['Cash equals modeled provision only by explicit scenario.',
                        'Current/deferred tax, NOL use, valuation allowances, jurisdiction and payment timing are not estimated by this bridge.',
                        'Tax payable movements alone do not establish cash payments or refunds.']}
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
