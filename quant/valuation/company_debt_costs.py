"""Finite conditional runoff of opening note issuance costs; 零授权效力.

This calendar allocation is an explicit scenario approximation, not the issuer's
effective-interest calculation, note fair value, or a complete financing ledger.
"""
from datetime import date, timedelta
import math
from quant.core.agent_contracts import canonical_hash


def _number(value, name, minimum=0):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < minimum:
        raise ValueError(f'{name}: explicit finite number required')
    return float(value)


def project(case):
    if (case['currency'] != 'USD' or case['scale'] != 'millions'
            or case.get('scope') != 'opening_note_issuance_costs_only'
            or case.get('allocation') != 'calendar_days_to_maturity_scenario_assumption'
            or case.get('opening_debt_includes_this_population') is not True
            or case.get('other_noncash_adjustments_exclude_this_cost') is not True
            or case.get('no_early_settlement_scenario_assumption') is not True):
        raise ValueError('explicit finite note cost scope, approximation and exclusive linkage required')
    for key in ['allocation_basis', 'opening_population_basis', 'noncash_adjustment_key']:
        if not isinstance(case.get(key), str) or not case[key].strip():
            raise ValueError('note cost population, allocation and adjustment key required')
    opening = date.fromisoformat(case['date'])
    maturity = date.fromisoformat(case['maturity_date'])
    if maturity <= opening:
        raise ValueError('future note maturity required')
    face = _number(case['nominal_face'], 'note face', 1e-12)
    carrying = _number(case['opening_carrying'], 'note carrying')
    pool = _number(case['opening_unamortized_cost'], 'note unamortized cost')
    if abs(face - carrying - pool) > 1e-6:
        raise ValueError('note carrying plus finite cost must equal nominal face')
    other = _number(case['other_opening_debt'], 'other opening debt')
    rows, prior, consumed = [], opening, 0.0
    for row in case['periods']:
        start, end = date.fromisoformat(row['start']), date.fromisoformat(row['end'])
        if start != prior + timedelta(days=1) or not start <= end <= maturity or (end-start).days > 370:
            raise ValueError('complete consecutive note cost periods through maturity required')
        if not row.get('basis') or not row.get('source_refs') or not row.get('nonoperating_allocation_basis'):
            raise ValueError('note cost period evidence and expense allocation required')
        amount = (pool - consumed if end == maturity
                  else pool * ((end-start).days+1)/(maturity-opening).days)
        if amount < -1e-6 or consumed + amount > pool + 1e-6:
            raise ValueError('finite note cost cannot exceed its opening population')
        begin = pool - consumed
        consumed += amount
        remaining = max(0.0, pool - consumed)
        net = _number(row['net_nonoperating_excluding_cost_amortization'], 'net excluding note costs', -math.inf)
        change = _number(row['other_debt_non_cash_change'], 'other debt noncash change', -math.inf)
        repayments = _number(row['other_debt_principal_repaid'], 'other debt principal repayment')
        rows.append({'start': str(start), 'end': str(end), 'opening_unamortized_cost': begin,
                     'amortization': amount, 'remaining_unamortized_cost': remaining,
                     'note_carrying_before_principal_settlement': face - remaining,
                     'net_nonoperating_excluding_cost_amortization': net,
                     'net_nonoperating': net - amount,
                     'other_debt_non_cash_change': change, 'debt_non_cash_change': change + amount,
                     'other_debt_principal_repaid': repayments,
                     'debt_principal_repaid': repayments + (face if end == maturity else 0)})
        prior = end
    if not rows or prior != maturity or abs(consumed - pool) > 1e-6:
        raise ValueError('full finite note cost allocation must end at contractual maturity')
    result = {'status': 'conditional_research', 'scope': case['scope'], 'date': str(opening),
              'maturity_date': str(maturity), 'nominal_face': face,
              'opening_carrying': carrying, 'opening_unamortized_cost': pool,
              'other_opening_debt': other, 'noncash_adjustment_key': case['noncash_adjustment_key'],
              'allocation': case['allocation'], 'allocation_basis': case['allocation_basis'],
              'periods': rows, 'input_hash': canonical_hash(case),
              'source_completeness': 'not_established', 'publication': 'withheld',
              'limitations': ['Calendar allocation is a scenario assumption, not issuer effective-interest amortization.',
                             'Opening selected cost only; new financing costs, early settlement and other populations remain unresolved.',
                             'Carrying values before principal settlement are accounting stocks, not fair claims or additional assets.']}
    result['receipt_hash'] = canonical_hash(result)
    return result
