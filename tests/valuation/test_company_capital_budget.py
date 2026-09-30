from copy import deepcopy

import pytest

from quant.valuation import company_model as cm
from tests.valuation.test_company_model import packet, STAMP


def budget_packet():
    p = packet(); s = p['scenarios'][0]; s.pop('multiple')
    p['opening_balance']['date'] = s['opening_date'] = '2026-06-30'
    p['observed']['end'] = '2026-03-31'
    p['operating_path_hold'] = {**{k: s[k] for k in ('basis', 'source_refs', 'known_at')},
                                'status': 'unestimable', 'reason': 'physical asset path not established'}
    periods = [('2026-07-01', '2026-09-30'), ('2026-10-01', '2026-12-31'),
               ('2027-01-01', '2027-03-31'), ('2027-04-01', '2027-06-30'),
               ('2027-07-01', '2027-09-30'), ('2027-10-01', '2027-12-31'),
               ('2028-01-01', '2028-03-31'), ('2028-04-01', '2028-06-30')]
    for q, (start, end) in zip(s['quarters'], periods): q.update(start=start, end=end)
    ev = {k: s[k] for k in ('basis', 'source_refs', 'known_at')}
    annual = []
    for year in (2026, 2027, 2028):
        row = {**ev, 'id': str(year), 'start': f'{year}-01-01', 'end': f'{year}-12-31',
               'amount': 40, 'reported_actual': None, 'unmodelled_remainder_cash': 0}
        if year == 2026:
            row['reported_actual'] = {**ev, 'start': '2026-01-01', 'through': '2026-06-30', 'amount': 20}
            row['guidance_range'] = {**ev, 'low': 38, 'high': 42,
                                     'metric_interpretation': 'same_cash_metric_assumption'}
        else:
            row['growth_assumption'] = {**ev, 'previous_budget_id': str(year-1),
                                        'rate': 0, 'status': 'scenario_assumption'}
        if year == 2028:
            row['unmodelled_remainder_cash'] = 20
            row['remainder_status'] = 'outside_forecast_horizon_scenario_assumption'
        annual.append(row)
    s['cash_capital_budget'] = {**ev, 'currency': 'USD', 'scale': 'millions',
                               'metric': 'cash_purchases_of_PPE',
                               'scope': 'cash_only_partial_capital_population', 'annual_budgets': annual}
    s['assumptions'] = {'cash_capital_budget': 'Cash only, future physical/finance population unresolved'}
    return p


def test_actual_plus_forecast_and_partial_remainder_reconcile_without_lease_or_financing_capital():
    p = budget_packet(); old = deepcopy(p)
    r = cm.build(p, as_of=STAMP)['scenarios']['base']; b = r['cash_capital_budget']
    first, second, last = b['annual_budgets']
    assert first['reported_actual_cash'] == first['projected_cash_in_forecast'] == 20
    assert second['projected_cash_in_forecast'] == 40 and second['unmodelled_remainder_cash'] == 0
    assert last['projected_cash_in_forecast'] == last['unmodelled_remainder_cash'] == 20
    assert all(row['annual_cash_budget'] == 40 for row in b['annual_budgets'])
    assert r['quarters'][0]['cash']['total_capex'] == 12  # additional financed principal excluded from this metric
    assert p == old and b['source_completeness'] == 'not_established' and b['publication'] == 'withheld'
    assert r['operating_value']['status'] == 'unknown'


@pytest.mark.parametrize('mutation', ['quarter_amount', 'guidance', 'guidance_metric', 'growth', 'growth_unknown',
                                    'actual_date', 'actual_start', 'actual_unknown', 'actual_missing', 'year_gap', 'remainder',
                                    'unlabelled_remainder', 'incomplete', 'scope', 'hold_removed', 'future_source'])
def test_inconsistent_unknown_or_unavailable_cash_budget_rejected(mutation):
    p = budget_packet(); s = p['scenarios'][0]; b = s['cash_capital_budget']; years = b['annual_budgets']
    if mutation == 'quarter_amount': s['quarters'][0]['cash']['capex_investing'] = 11
    elif mutation == 'guidance': years[0]['guidance_range']['low'] = 41
    elif mutation == 'guidance_metric': years[0]['guidance_range']['metric_interpretation'] = 'economic_capital'
    elif mutation == 'growth': years[1]['growth_assumption']['rate'] = .1
    elif mutation == 'growth_unknown': years[1]['growth_assumption']['rate'] = None
    elif mutation == 'actual_date': years[0]['reported_actual']['through'] = '2026-03-31'
    elif mutation == 'actual_start': years[0]['reported_actual']['start'] = '2026-04-01'
    elif mutation == 'actual_unknown': years[0]['reported_actual']['amount'] = None
    elif mutation == 'actual_missing': years[0]['reported_actual'] = None
    elif mutation == 'year_gap': years[1]['start'] = '2027-01-02'
    elif mutation == 'remainder': years[0]['unmodelled_remainder_cash'] = 1
    elif mutation == 'unlabelled_remainder': years[-1].pop('remainder_status')
    elif mutation == 'incomplete': years.pop()
    elif mutation == 'scope': b['scope'] = 'all_capital_complete'
    elif mutation == 'hold_removed': p.pop('operating_path_hold')
    elif mutation == 'future_source': years[0]['reported_actual']['known_at'] = '2026-09-09T00:00:00+00:00'
    with pytest.raises(ValueError): cm.build(p, as_of=STAMP)


def test_capital_budget_worker_summary_preserves_partial_cash_scope_and_growth_basis():
    from quant.valuation.assessment import build
    from quant.ops.agent_workflow import _valuation_worker_receipt
    p = budget_packet()
    full = build({'selected_tickers': ['SYN'], 'valuation': {'company_models': {'SYN': p}}}, as_of=STAMP)
    view = _valuation_worker_receipt(full)['tickers']['SYN']['company_research']['scenarios']['base']['cash_capital_budget']
    assert view['scope'] == 'cash_only_partial_capital_population'
    assert view['annual_budgets'][-1]['unmodelled_remainder_cash'] == 20
    assert view['source_completeness'] == 'not_established' and view['publication'] == 'withheld'
    assert 'future physical/finance population unresolved' in view['assumption_basis']
