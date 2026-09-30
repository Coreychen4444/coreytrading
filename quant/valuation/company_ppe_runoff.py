"""Selected opening finance-ROU depreciation only; 零授权效力.

This finite book-cost ledger excludes owned PPE, construction and future leases.
It is neither an economic capital model nor an asset valuation. A source quarter
rate is an explicit forward allocation assumption, not issuer remaining life.
"""
from datetime import date, timedelta
from copy import deepcopy
import math

from quant.core.agent_contracts import canonical_hash


def _n(value, name, low=0):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < low:
        raise ValueError(f'{name}: explicit finite value required')
    return float(value)


def project(case):
    if (case.get('currency') != 'USD' or case.get('scale') != 'millions'
            or case.get('scope') != 'selected_opening_finance_ROU_only'
            or case.get('allocation') != 'constant_daily_source_quarter_rate_scenario_assumption'
            or case.get('opening_selected_in_reported_PPE') is not True
            or case.get('other_DA_excludes_selected_population') is not True
            or case.get('retirement_impairment_assumption') != 'none_for_selected_opening_pool'
            or not case.get('allocation_basis')):
        raise ValueError('explicit partial finance-ROU scope and allocation assumptions required')
    prior = date.fromisoformat(case['date'])
    opening = remaining = _n(case['opening_selected_net_ROU'], 'selected ROU')
    reported = _n(case['reported_total_net_PPE'], 'reported PPE')
    excluded = _n(case['excluded_other_net_PPE'], 'other net PPE')
    if opening <= 0 or abs(opening + excluded - reported) > 1e-6:
        raise ValueError('selected and excluded opening PPE must reconcile without a second asset')
    inventory = case.get('excluded_populations')
    if not isinstance(inventory, list) or not inventory:
        raise ValueError('unresolved excluded capital population inventory required')
    ids = set()
    for item in inventory:
        if (not isinstance(item, dict) or not item.get('economic_id') or item['economic_id'] in ids
                or item.get('status') != 'unresolved' or not item.get('basis') or not item.get('source_refs')):
            raise ValueError('unique unresolved excluded capital population and evidence required')
        ids.add(item['economic_id'])
    reference = case['reference_quarter']
    a,b = date.fromisoformat(reference['start']),date.fromisoformat(reference['end'])
    if b > prior or not 75 <= (b-a).days <= 105 or not reference.get('basis') or not reference.get('source_refs'):
        raise ValueError('explicit observed reference-quarter rate required')
    daily = _n(reference['amortization'], 'source quarter amortization') / ((b-a).days+1)
    if daily <= 0:
        raise ValueError('positive selected forward rate required')
    rows = []; native = 0; tail = False
    for row in case['periods']:
        start,end = date.fromisoformat(row['start']),date.fromisoformat(row['end'])
        if start != prior + timedelta(days=1) or not start <= end or (end-start).days > 370:
            raise ValueError('complete consecutive selected PPE periods required')
        if remaining <= 0:
            raise ValueError('selected PPE ledger must end at exhaustion')
        if not row.get('basis') or not row.get('source_refs'):
            raise ValueError('dated period evidence required')
        days = (end-start).days+1
        charge = min(remaining,daily*days)
        # The final partial period cannot quietly include years after depletion.
        if charge == remaining and daily*(days-1) >= remaining:
            raise ValueError('final selected PPE period extends beyond exhaustion day')
        remaining = max(0.0,remaining-charge)
        output = {'start':str(start),'end':str(end),'selected_depreciation':charge,
                  'remaining_selected_net_ROU':remaining}
        binding = row.get('income_cash_binding')
        if binding is None:
            if row.get('role') != 'cost_only_outside_native_forecast':
                raise ValueError('outside forecast tail must explicitly omit corporate earnings/cash')
            tail = True
            output['role'] = row['role']
        else:
            if tail or row.get('role') != 'native_income_cash_forecast':
                raise ValueError('native income/cash bindings must be a contiguous prefix')
            if (not isinstance(binding,dict)
                    or set(binding) != {'other_physical_da','other_cash_da','operating_profit_before_selected_depreciation','basis','source_refs'}
                    or not binding.get('basis') or not binding.get('source_refs')):
                raise ValueError('explicit selected physical expense and cash binding required')
            physical = _n(binding['other_physical_da'],'other physical DA') + charge
            output.update(role=row['role'],physical_da=physical,
                          total_cash_da=physical+_n(binding['other_cash_da'],'other cash DA'),
                          operating_profit=_n(binding['operating_profit_before_selected_depreciation'],'preselected operating profit',-math.inf)-charge)
            native += 1
        rows.append(output);prior=end
    if not rows or not native or remaining != 0:
        raise ValueError('native prefix and complete selected pool exhaustion required')
    result = {'status':'conditional_research','scope':case['scope'],'date':case['date'],
              'opening_selected_net_ROU':opening,'reported_total_net_PPE':reported,
              'excluded_other_net_PPE':excluded,'excluded_populations':deepcopy(inventory),
              'allocation':case['allocation'],'allocation_basis':case['allocation_basis'],
              'native_period_count':native,'periods':rows,'input_hash':canonical_hash(case),
              'source_completeness':'not_established','publication':'withheld',
              'limitations':['Source-quarter expense is a forward allocation hypothesis, not asset remaining life or actual future expense.',
                             'Existing ROU is already included in PPE; this ledger adds no asset, debt, new lease or financing cash.',
                             'Owned PPE, construction, readiness, prepayments and future leases remain outside this selected scope.',
                             'Tail only exhausts book cost and is not a complete future-company forecast.',
                             'Book cost is not fair asset value, and partial depreciation does not repair the full operating path.']}
    result['receipt_hash']=canonical_hash(result)
    return result
