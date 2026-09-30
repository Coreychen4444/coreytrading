"""Ordinary legal share/cash flow proxy; 零授权效力.

Amounts and shares are in millions. Gross issuance and explicit repurchases
remain distinct even if net shares are unchanged. An unassigned cash residual
is a scenario component, never proof of a particular historical cash category.
"""
from copy import deepcopy
from datetime import date, timedelta
import math

from quant.core.agent_contracts import canonical_hash


def _number(value, name, minimum=None):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or (minimum is not None and value < minimum)):
        raise ValueError(f'{name}: explicit finite value within bounds required')
    return float(value)


def _evidence(row):
    if (not isinstance(row.get('basis'), str) or not row['basis'].strip()
            or not isinstance(row.get('source_refs'), list) or not row['source_refs']
            or not all(isinstance(r, str) and r for r in row['source_refs'])):
        raise ValueError('ordinary settlement basis and source refs required')


def project(case):
    """Reconcile one declared future flow population; never settle old rights."""
    required = {'date', 'currency', 'scale', 'scope', 'known_at', 'basis', 'source_refs',
                'opening_common_shares', 'cohort_assignment', 'claim_valuation_status',
                'unresolved_current_claim_ids', 'periods'}
    if (set(case) != required or case['currency'] != 'USD' or case['scale'] != 'millions'
            or case['scope'] != 'ordinary_share_cash_flow_proxy'
            or case['cohort_assignment'] != 'not_established'
            or case['claim_valuation_status'] != 'unresolved'):
        raise ValueError('ordinary flow proxy requires exact scope and unresolved current rights')
    _evidence(case)
    claims = case['unresolved_current_claim_ids']
    if (not isinstance(claims, list) or not claims
            or not all(isinstance(c, str) and c for c in claims) or len(claims) != len(set(claims))):
        raise ValueError('unique unresolved current claim IDs required')
    prior = date.fromisoformat(case['date'])
    opening = _number(case['opening_common_shares'], 'opening ordinary shares', 1e-12)
    closing, periods, ids = opening, [], set()
    for row in case['periods']:
        required_row = {'id', 'start', 'end', 'issuances', 'repurchase', 'other_financing', 'basis', 'source_refs'}
        if set(row) != required_row:
            raise ValueError('exact ordinary settlement period schema required')
        _evidence(row)
        start, end = date.fromisoformat(row['start']), date.fromisoformat(row['end'])
        ident = row['id']
        if (not isinstance(ident, str) or not ident or ident in ids
                or start != prior + timedelta(days=1) or not 75 <= (end-start).days <= 105):
            raise ValueError('unique consecutive ordinary settlement quarters required')
        ids.add(ident)
        issued, raised, components, component_ids = 0.0, 0.0, [], set()
        if not isinstance(row['issuances'], list) or not row['issuances']:
            raise ValueError('explicit gross ordinary issuance inventory required')
        for item in row['issuances']:
            if set(item) != {'economic_id', 'shares', 'cash_proceeds', 'status', 'basis', 'source_refs'}:
                raise ValueError('exact gross issuance schema required')
            _evidence(item)
            key = item['economic_id']
            if (not isinstance(key, str) or not key or key in component_ids
                    or item['status'] != 'scenario_assumption'):
                raise ValueError('unique gross issuance components and scenario status required')
            component_ids.add(key)
            shares = _number(item['shares'], 'gross issued shares', 0)
            cash = _number(item['cash_proceeds'], 'issuance cash proceeds', 0)
            if cash > 0 and shares == 0:
                raise ValueError('issuance proceeds require a positive legal share population')
            issued += shares; raised += cash
            components.append(deepcopy(item))
        buyback, other = row['repurchase'], row['other_financing']
        if (set(buyback) != {'shares', 'cash_paid', 'cash_status', 'basis', 'source_refs'}
                or buyback['cash_status'] != 'book_equals_cash_scenario_assumption'):
            raise ValueError('repurchase cash requires explicit book-equals-cash scenario qualification')
        if (set(other) != {'cash_flow', 'native_cash_key', 'status', 'basis', 'source_refs'}
                or other['status'] != 'unassigned_scenario_residual'
                or not isinstance(other['native_cash_key'], str) or not other['native_cash_key'].strip()):
            raise ValueError('unassigned financing residual requires unique native cash key and scenario status')
        _evidence(buyback); _evidence(other)
        repurchased = _number(buyback['shares'], 'repurchased shares', 0)
        paid = _number(buyback['cash_paid'], 'repurchase cash', 0)
        if paid > 0 and repurchased == 0:
            raise ValueError('repurchase cash requires a positive legal share population')
        residual = _number(other['cash_flow'], 'unassigned financing cash')
        previous_shares = closing
        closing += issued - repurchased
        if closing <= 0:
            raise ValueError('ordinary share settlement leaves no positive closing shares')
        periods.append({'id': ident, 'start': str(start), 'end': str(end),
                        'opening_common_shares': previous_shares, 'gross_issued': issued,
                        'repurchased': repurchased, 'net_issued': issued-repurchased,
                        'closing_common_shares': closing, 'issuance_cash_proceeds': raised,
                        'repurchase_cash': paid, 'unassigned_financing_cash': residual,
                        'net_financing_cash': raised-paid+residual,
                        'issuances': components, 'repurchase': deepcopy(buyback),
                        'other_financing': deepcopy(other)})
        prior = end
    if not periods:
        raise ValueError('ordinary settlement projection quarters required')
    out = {'status': 'conditional_research', 'scope': case['scope'], 'date': case['date'],
           'opening_common_shares': opening, 'periods': periods,
           'cohort_assignment': case['cohort_assignment'], 'claim_valuation_status': 'unresolved',
           'unresolved_current_claim_ids': deepcopy(claims),
           'source_completeness': 'not_established', 'publication': 'withheld', 'validated': False,
           'input_hash': canonical_hash(case),
           'limitations': ['Historical flow extrapolation is a scenario, not an issuer issuance or exercise plan.',
                           'Repurchase equity book equals future cash only by assumption; historical settled cash amount/timing is unproven.',
                           'Residual financing cash has no established transaction category.',
                           'No current rights cohort assignment or fair value; future issuance does not extinguish or value old employee claims.',
                           'SBC expense and cash addback stay in the native statements; this flow ledger supplies no additional expense, tax benefit or claim deduction.']}
    out['receipt_hash'] = canonical_hash(out)
    return out
