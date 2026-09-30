from copy import deepcopy

import pytest

from quant.valuation.company_amortization import project


def case():
    return {'date': '2026-12-31', 'currency': 'USD', 'scale': 'millions',
            'opening_existing_amortizable': 100, 'opening_iprd': 40, 'reported_total': 140,
            'new_acquisitions_assumption': 'none_in_projection', 'impairment_assumption': 'none_in_projection',
            'iprd_readiness': {'status': 'scenario_assumption', 'ready_date': '2027-02-01',
                               'life_years': 2, 'basis': 'synthetic readiness assumption'},
            'periods': [{'start': '2027-01-01', 'end': '2027-03-31', 'basis': 'synthetic',
                         'source_refs': ['synthetic'], 'existing_amortization': 25, 'other_da': 10,
                         'operating_profit_before_acquired_amortization': 80,
                         'cash_new_assets': 30, 'new_financed_technology_licenses': 8,
                         'technology_license_principal_paid': 12}]}


def test_finite_runoff_and_partial_readiness_do_not_call_repayments_new_assets():
    p = case(); original = deepcopy(p); r = project(p); row = r['periods'][0]
    assert p == original
    iprd_amort = 40 / (2 * 365.25) * 59
    assert row['iprd_amortization'] == pytest.approx(iprd_amort)
    assert row['operating_profit'] == pytest.approx(80 - 25 - iprd_amort)
    assert row['total_da'] == pytest.approx(10 + 25 + iprd_amort)
    assert row['remaining_existing'] == 75 and row['remaining_iprd'] == pytest.approx(40 - iprd_amort)
    assert row['gross_capital_investment'] == 38
    p['periods'][0]['technology_license_principal_paid'] = 50
    assert project(p)['periods'][0]['gross_capital_investment'] == 38
    assert r['publication'] == 'withheld' and r['source_completeness'] == 'not_established'


def test_unready_iprd_stays_separate_and_ready_pool_cannot_go_negative():
    p = case(); p['iprd_readiness'] = {'status': 'not_ready_in_projection', 'basis': 'synthetic delay'}
    row = project(p)['periods'][0]
    assert row['iprd_amortization'] == 0 and row['remaining_iprd'] == 40
    p = case(); p['iprd_readiness']['life_years'] = .01
    row = project(p)['periods'][0]
    assert row['iprd_amortization'] == 40 and row['remaining_iprd'] == 0


def test_asset_exhaustion_is_carried_across_periods():
    p = case(); p['periods'][0]['existing_amortization'] = 100
    p['iprd_readiness']['life_years'] = .01
    following = {**p['periods'][0], 'start': '2027-04-01', 'end': '2027-06-30', 'existing_amortization': 0}
    p['periods'].append(following)
    row = project(p)['periods'][1]
    assert row['remaining_existing'] == row['remaining_iprd'] == row['acquired_amortization'] == 0
    following['existing_amortization'] = 1
    with pytest.raises(ValueError, match='remaining existing'): project(p)


def finite_only_case():
    p = case(); p.update(population_scope='definite_lived_intangibles_only',
                         opening_iprd=0, reported_total=100)
    p['iprd_readiness'] = {'status': 'not_ready_in_projection', 'basis': 'IPRD excluded from selected finite population'}
    p['excluded_nonamortizing_assets'] = [{'asset_id': 'other_indefinite', 'asset_type': 'indefinite_lived_intangibles',
        'book_balance': 40, 'valuation_status': 'unresolved', 'basis': 'Generic indefinite category; not identified IPRD',
        'source_refs': ['synthetic']}]
    return p


def test_finite_subset_keeps_indefinite_book_assets_unamortized_and_unvalued():
    p = finite_only_case(); original = deepcopy(p); r = project(p)
    assert r['population_scope'] == 'definite_lived_intangibles_only'
    assert r['periods'][0]['acquired_amortization'] == 25 and r['periods'][0]['remaining_existing'] == 75
    assert r['periods'][0]['iprd_amortization'] == 0
    assert r['excluded_nonamortizing_assets'][0]['book_balance'] == 40
    assert r['excluded_nonamortizing_assets'][0]['valuation_status'] == 'unresolved'
    r['excluded_nonamortizing_assets'][0]['book_balance'] = 1
    assert p == original


@pytest.mark.parametrize('mutation', ['iprd', 'readiness', 'scope', 'inventory_missing', 'duplicate',
                                    'asset_kind', 'priced', 'unknown_balance', 'source', 'unscoped'])
def test_finite_subset_cannot_infer_iprd_or_price_excluded_assets(mutation):
    p = finite_only_case(); a = p['excluded_nonamortizing_assets'][0]
    if mutation == 'iprd': p.update(opening_iprd=40, reported_total=140)
    elif mutation == 'readiness': p['iprd_readiness'].update(status='scenario_assumption', ready_date='2027-01-01', life_years=2)
    elif mutation == 'scope': p['population_scope'] = 'all_assets'
    elif mutation == 'inventory_missing': p.pop('excluded_nonamortizing_assets')
    elif mutation == 'duplicate': p['excluded_nonamortizing_assets'].append(deepcopy(a))
    elif mutation == 'asset_kind': a['asset_type'] = 'IPRD'
    elif mutation == 'priced': a['valuation_status'] = 'valued'
    elif mutation == 'unknown_balance': a['book_balance'] = None
    elif mutation == 'source': a['source_refs'] = []
    elif mutation == 'unscoped': p.pop('population_scope')
    with pytest.raises(ValueError): project(p)


@pytest.mark.parametrize('mutation', ['negative_pool', 'excess_amort', 'opening', 'ready', 'unready_date', 'gap'])
def test_invalid_pool_or_time_contracts_rejected(mutation):
    p = case()
    if mutation == 'negative_pool': p['opening_existing_amortizable'] = -1
    elif mutation == 'excess_amort': p['periods'][0]['existing_amortization'] = 101
    elif mutation == 'opening': p['reported_total'] = 141
    elif mutation == 'ready': p['iprd_readiness']['ready_date'] = p['date']
    elif mutation == 'unready_date': p['iprd_readiness']['status'] = 'not_ready_in_projection'
    elif mutation == 'gap': p['periods'][0]['start'] = '2027-01-02'
    with pytest.raises(ValueError): project(p)


def test_host_binds_quarter_and_dcf_economics_to_same_asset_ledger():
    from tests.valuation.test_company_model import packet, STAMP
    from tests.valuation.test_company_cashflow import case as dcf_case
    from quant.valuation.company_model import build
    p = packet(); s = p['scenarios'][0]
    ledger = case(); ledger.update(known_at=s['known_at'], basis=s['basis'], source_refs=s['source_refs'],
                                  date=s['opening_date'], periods=[])
    ledger['iprd_readiness'] = {'status': 'not_ready_in_projection', 'basis': 'synthetic delay'}
    for q in s['quarters']:
        cash = q['cash']; op = sum(x['operating_profit'] for x in q['segments'].values())
        ledger['periods'].append({'start': q['start'], 'end': q['end'], 'basis': 'synthetic',
            'source_refs': s['source_refs'], 'existing_amortization': 1,
            'other_da': cash['depreciation_amortization'] - 1,
            'operating_profit_before_acquired_amortization': op + 1,
            'cash_new_assets': cash['capex_investing'], 'new_financed_technology_licenses': 2,
            'technology_license_principal_paid': cash['capex_financing']})
    s['acquired_intangible_runoff'] = ledger
    d = dcf_case(); q = s['quarters'][4]; d.update(known_at=s['known_at'], basis=s['basis'],
            source_refs=s['source_refs'], date=s['quarters'][3]['end'])
    d['periods'][0].update(start=q['start'], end=q['end'], operating_profit=13,
                         depreciation_amortization=q['cash']['depreciation_amortization'],
                         gross_capital_investment=q['cash']['capex_investing'] + 2)
    d['terminal']['date'] = q['end']; s['operating_dcf'] = d
    result = build(p, as_of=STAMP)['scenarios']['base']
    assert result['acquired_intangible_runoff']['status'] == 'conditional_research'
    q['cash']['depreciation_amortization'] += 1
    with pytest.raises(ValueError, match='quarter economics'): build(p, as_of=STAMP)
    q['cash']['depreciation_amortization'] -= 1
    d['periods'][0]['gross_capital_investment'] += q['cash']['capex_financing'] + 1
    with pytest.raises(ValueError, match='DCF economics'): build(p, as_of=STAMP)
