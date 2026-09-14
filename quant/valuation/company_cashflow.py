"""Explicit consolidated operating cash-flow valuation; 零授权效力.

Operating value precedes financing claims. Inputs exclude debt issues/repayments,
interest, minority distributions, share issuance and buybacks. No stock price fit.
"""
from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from datetime import date, timedelta
import math

from quant.core.agent_contracts import canonical_hash


def _n(value, name, low=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f'{name}: explicit finite number required')
    if low is not None and value < low:
        raise ValueError(f'{name}: below lower bound')
    return float(value)



def lease_adjustment(row):
    """Optional same-population rent accrual bridge, with finance flows segregated.

    The tax and working-capital inputs must already use the stated exclusions.
    Labels validate a modelling contract; they do not establish source scope.
    """
    details = row.get('lease_details')
    if details is None:
        return {'status': 'not_separately_bridged', 'operating_cash_adjustment': 0.0}
    if (details.get('population_basis') != 'same_contract_population_expense_and_cash'
            or details.get('working_capital_excludes_lease_accrual') is not True
            or details.get('cash_tax_basis') != 'operating_lease_deductions_included_finance_interest_shield_excluded'
            or not details.get('basis')):
        raise ValueError('consistent lease population, working capital and cash tax basis required')
    fields = ('operating_expense_in_ebit', 'operating_cash_paid', 'finance_new_assets',
              'finance_principal_paid', 'finance_interest_paid', 'finance_da_in_total',
              'capital_investment_excluding_finance_leases')
    x = {key: _n(details[key], key, 0) for key in fields}
    if abs(x['capital_investment_excluding_finance_leases'] + x['finance_new_assets']
           - _n(row['gross_capital_investment'], 'capital investment', 0)) > 1e-6:
        raise ValueError('finance new assets must reconcile to total capital investment')
    if x['finance_da_in_total'] > _n(row['depreciation_amortization'], 'D&A', 0):
        raise ValueError('finance lease depreciation exceeds total D&A')
    return {'status': 'explicit_contract',
            'operating_cash_adjustment': x['operating_expense_in_ebit'] - x['operating_cash_paid'],
            'finance_new_assets_in_capital': x['finance_new_assets'],
            'finance_da_in_total': x['finance_da_in_total'],
            'finance_cash_excluded_from_fcff': x['finance_principal_paid'] + x['finance_interest_paid'],
            'limitation': 'Finance cash belongs in financing/liquidity; no extra deduction here. Scope needs source review.'}


def capital_reconciliation(row):
    """Check declared asset populations, not the truth/completeness of disclosures.

    Investment is gross new operating assets, including new financed assets.
    Settlement of opening financing belongs outside FCFF. Balance changes alone
    do not identify acquisitions, cash payments or period amortization.
    """
    details = row.get('capital_details')
    if details is None:
        return {'status': 'not_separately_reconciled', 'source_completeness': 'not_established'}
    categories = {'tangible_excluding_finance_leases', 'intangibles_excluding_finance_leases',
                  'finance_leases', 'other_operating_capital'}
    if (not isinstance(details, dict) or not details.get('basis')
            or details.get('investment_basis') != 'gross_new_assets_excluding_financing_settlements'
            or details.get('working_capital_excludes_these_assets') is not True
            or set(details.get('categories', {})) != categories):
        raise ValueError('explicit complete capital categories and working-capital exclusion required')
    investments, amortization = 0.0, 0.0
    parsed = {}
    for name, item in details['categories'].items():
        if (not isinstance(item, dict) or set(item) != {'new_investment', 'da_in_ebit', 'basis'}
                or not item.get('basis')):
            raise ValueError('capital category requires investment, D&A and basis; unknown is not zero')
        investment = _n(item['new_investment'], name + ' investment', 0)
        da = _n(item['da_in_ebit'], name + ' D&A', 0)
        investments += investment
        amortization += da
        parsed[name] = {'new_investment': investment, 'da_in_ebit': da}
    if abs(investments - _n(row['gross_capital_investment'], 'capital investment', 0)) > 1e-6:
        raise ValueError('capital categories do not reconcile to gross investment')
    if abs(amortization - _n(row['depreciation_amortization'], 'D&A', 0)) > 1e-6:
        raise ValueError('capital categories do not reconcile to D&A addback')
    if row.get('lease_details') is not None:
        finance = parsed['finance_leases']
        leases = row['lease_details']
        if (abs(finance['new_investment'] - _n(leases['finance_new_assets'], 'finance assets', 0)) > 1e-6
                or abs(finance['da_in_ebit'] - _n(leases['finance_da_in_total'], 'finance D&A', 0)) > 1e-6):
            raise ValueError('capital and lease populations disagree')
    return {'status': 'arithmetically_reconciled', 'categories': parsed,
            'source_completeness': 'not_established'}


def value(case):
    when = date.fromisoformat(case['date'])
    if case['currency'] != 'USD' or case['scale'] != 'millions':
        raise ValueError('USD millions required')
    if case['cashflow_basis'] != 'consolidated_unlevered_after_operating_tax':
        raise ValueError('consolidated unlevered cash-flow basis required')
    if (case['sbc_treatment'] != 'expense_no_addback'
            or case['lease_treatment'] != 'operating_leases_expensed_finance_leases_in_capital_and_debt'):
        raise ValueError('explicit consistent SBC and lease treatment required')
    if not case.get('tax_basis') or not case.get('capital_basis'):
        raise ValueError('unlevered cash-tax and capital-investment basis required')
    flows, pv, discount = [], 0.0, 1.0
    prior = when
    for row in case['periods']:
        start, end = date.fromisoformat(row['start']), date.fromisoformat(row['end'])
        if start != prior + timedelta(days=1) or not start <= end or (end - start).days > 370:
            raise ValueError('complete consecutive future cash-flow periods required')
        if not row.get('basis') or not row.get('source_refs'):
            raise ValueError('explicit period assumptions/evidence required')
        # Unknown financial items must not sneak into unlevered cashflows.
        forbidden = {'interest', 'interest_expense', 'debt_issued', 'debt_repaid', 'partner_distributions',
                     'equity_cash_raised', 'buybacks', 'stock_compensation_addback'}
        if forbidden.intersection(row):
            raise ValueError('financing or SBC addback cannot enter this operating-value path')
        rate = _n(row['wacc'], 'WACC', 0)
        if not 0 < rate < 1:
            raise ValueError('WACC must be a positive fraction below one')
        operating = _n(row['operating_profit'], 'operating profit')
        tax = _n(row['cash_operating_tax'], 'cash operating tax')  # Explicit NOL/tax-credit schedule may be negative.
        da = _n(row['depreciation_amortization'], 'D&A', 0)
        capex = _n(row['gross_capital_investment'], 'capital investment', 0)
        grants = _n(row['capital_grants'], 'capital grants', 0)
        working = _n(row['working_capital_increase'], 'working capital increase')
        leases = lease_adjustment(row)
        capital = capital_reconciliation(row)
        fcff = operating - tax + da - capex + grants - working + leases['operating_cash_adjustment']
        years = (end - prior).days / 365.25
        discount *= (1 + rate) ** years
        discounted = fcff / discount
        pv += discounted
        flows.append({'start': str(start), 'end': str(end), 'fcff': fcff,
                      'discount_factor': discount, 'present_value': discounted, 'leases': leases,
                      'capital': capital})
        prior = end
    if not flows:
        raise ValueError('explicit operating cashflows required')
    terminal = case['terminal']
    if terminal['date'] != str(prior) or not terminal.get('basis') or not terminal.get('source_refs'):
        raise ValueError('terminal evidence at final cash-flow date required')
    growth = _n(terminal['growth'], 'terminal growth', 0)
    roic = _n(terminal['incremental_roic'], 'incremental ROIC', 1e-12)
    rate = _n(terminal['wacc'], 'terminal WACC', 1e-12)
    nopat = _n(terminal['next_year_normalized_nopat'], 'terminal next-year NOPAT', 0)
    if not growth < rate < 1 or growth > roic:
        raise ValueError('growth must be below WACC and cannot exceed incremental ROIC')
    # Stable-state growth consumes capital: no perpetual growth with zero reinvestment.
    reinvestment_rate = growth / roic
    terminal_cash = nopat * (1 - reinvestment_rate)
    terminal_value = terminal_cash / (rate - growth)
    terminal_pv = terminal_value / discount
    total = pv + terminal_pv
    result = {'date': str(when), 'model_version': 'explicit-consolidated-fcff-1.0',
              'status': 'research_sensitivity', 'operating_value': total,
              'explicit_period_pv': pv, 'terminal_pv': terminal_pv,
              'terminal_value': terminal_value, 'terminal_fcff': terminal_cash,
              'terminal_reinvestment_rate': reinvestment_rate,
              'terminal_share_of_value': terminal_pv / total if total > 0 else None,
              'periods': flows, 'input_hash': canonical_hash(case),
              'publication': 'withheld', 'validated': False,
              'limitations': ['Conditional operating value; not common equity or a market target.',
                             'Cash tax, WACC, capital and stable-state assumptions require independent review.',
                             'Period-end discounting; terminal value assumes a stable regime not proved by the equation.',
                             'Finance lease liabilities require the matching equity bridge; operating leases stay expensed.']}
    result['receipt_hash'] = canonical_hash(result)
    return result
