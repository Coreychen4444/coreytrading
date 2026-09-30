from copy import deepcopy

import pytest

from quant.valuation.company_equity import bridge


def case():
    row = {'date': '2027-09-30', 'basis': 'synthetic value at bridge date', 'source_refs': ['synthetic']}
    return {'date': row['date'], 'currency': 'USD', 'scale': 'millions',
            'value_basis': 'consolidated_operating_value_before_financial_claims',
            'coverage_basis': 'synthetic full group including corporate costs', 'share_basis': 'issued common excluding new claims',
            'outstanding_shares': 100, 'required_business_ids': ['products', 'corporate'],
            'businesses': [{**row, 'economic_id': 'products', 'value': 1200}, {**row, 'economic_id': 'corporate', 'value': -100}],
            'required_asset_ids': ['cash'],
            'nonoperating_assets': [{**row, 'economic_id': 'cash', 'basis_type': 'excess_unrestricted_cash',
                                    'valuation_status': 'valued', 'value': 100}],
            'required_claim_ids': ['debt', 'escrow'],
            'claims': [{**row, 'economic_id': 'debt', 'settlement': 'cash', 'cash': 200},
                       {**row, 'economic_id': 'escrow', 'settlement': 'shares', 'new_shares': 25, 'cash_proceeds': 0}]}


def test_hand_calculation_dilution_and_cash_claim_are_not_double_counted():
    p = case(); saved = deepcopy(p)
    r = bridge(p)
    assert p == saved
    assert r['per_share'] == pytest.approx(8)
    assert r['residual_equity'] == 1000 and r['total_shares'] == 125
    assert r['publication'] == 'withheld' and r['validated'] is False


def test_unknown_business_preserves_known_components_without_zero_enterprise_value():
    p = case(); saved = deepcopy(p)
    p['businesses'][0].pop('value')
    p['businesses'][0]['valuation_status'] = 'unresolved'
    r = bridge(p)
    assert r['operating_value'] is None and r['known_operating_value'] == -100
    assert r['unresolved_businesses'] == ['products']
    assert r['reason'] == 'unresolved_operating_businesses'
    assert r['status'] == 'unknown' and r['per_share'] is None
    assert 'residual_equity' not in r and 'total_shares' not in r
    assert r['known_nonoperating_assets'] == 100 and r['fixed_cash_claims'] == 200
    assert saved['businesses'][0]['value'] == 1200


@pytest.mark.parametrize('placeholder', [0, None, 1200])
def test_unknown_business_cannot_supply_a_placeholder_value(placeholder):
    p = case(); p['businesses'][0].update(valuation_status='unresolved', value=placeholder)
    with pytest.raises(ValueError, match='placeholder'): bridge(p)


def test_unknown_business_still_checks_dates_claims_and_asset_completeness():
    p = case(); p['businesses'][0].pop('value'); p['businesses'][0]['valuation_status'] = 'unresolved'
    a = p['nonoperating_assets'][0]; a.pop('value'); a['valuation_status'] = 'unresolved'
    c = p['claims'][0]; c.pop('cash'); c['settlement'] = 'unresolved'
    r = bridge(p)
    assert r['reason'] == 'unresolved_equity_components'
    assert r['unresolved_assets'] == ['cash'] and r['unresolved_claims'] == ['debt']
    p['required_claim_ids'].append('missing')
    with pytest.raises(ValueError, match='claim inventory'): bridge(p)
    p['required_claim_ids'].pop(); a['date'] = '2027-09-29'
    with pytest.raises(ValueError, match='dated evidence'): bridge(p)


def participating_claim():
    return {'date': '2027-09-30', 'economic_id': 'participating_class',
            'source_refs': ['synthetic'], 'basis': 'synthetic closing class, not weighted EPS',
            'settlement': 'pari_passu_equivalents', 'participating_shares': 20,
            'common_share_ratio': 1, 'participation_basis': 'pari_passu_dividends_and_liquidation',
            'already_in_common_shares': False, 'has_senior_or_redemption_rights': False,
            'rights_basis': 'synthetic equal distributions on conversion units; no redemption/seniority'}


def test_issued_participating_class_allocates_wealth_without_new_financing_or_debt():
    p = case(); p['claims'][1] = participating_claim()
    p['required_claim_ids'][1] = 'participating_class'
    r = bridge(p)
    # 1000 remaining wealth / (100 legal common + 20 existing common units).
    assert r['per_share'] == pytest.approx(1000 / 120)
    assert r['participating_common_equivalents'] == 20
    assert r['fixed_new_shares'] == r['share_issuance_cash_proceeds'] == 0
    assert r['fixed_cash_claims'] == 200
    assert r['total_common_units'] == 120 and r['residual_equity'] == 1000
    assert 'total_shares' not in r  # The preferred class is not legal common.
    assert r['allocated_participating_equity'] == pytest.approx(1000 * 20 / 120)
    p['claims'][1]['common_share_ratio'] = 2
    assert bridge(p)['per_share'] == pytest.approx(1000 / 140)


@pytest.mark.parametrize('field,value', [
    ('already_in_common_shares', True), ('has_senior_or_redemption_rights', True),
    ('participation_basis', 'preferred_liquidation_priority'), ('rights_basis', ''),
    ('cash', 2000), ('cash_proceeds', 2000), ('new_shares', 20),
    ('participating_shares', None), ('common_share_ratio', 0)])
def test_senior_duplicate_or_unknown_class_cannot_use_pari_passu_allocation(field, value):
    p = case(); p['claims'][1] = participating_claim()
    p['required_claim_ids'][1] = 'participating_class'; p['claims'][1][field] = value
    with pytest.raises(ValueError): bridge(p)


def test_participation_is_included_in_circular_warrant_price_and_unknown_bridge():
    p = case(); p['claims'][1] = participating_claim()
    p['required_claim_ids'][1] = 'participating_class'
    row = {'date': p['date'], 'basis': 'synthetic exercise', 'source_refs': ['synthetic'],
           'economic_id': 'option', 'settlement': 'net_share_warrant',
           'underlying_shares': 25, 'strike': 4, 'exercise_basis': 'immediate_exercise_at_bridge_date'}
    p['claims'].append(row); p['required_claim_ids'].append('option')
    # 120*P + 25*(P-4) = 1000 -> P = 1100/145.
    r = bridge(p)
    assert r['per_share'] == pytest.approx(1100 / 145)
    assert abs(r['settlement_residual']) < 1e-9
    del row['underlying_shares'], row['strike']; row['settlement'] = 'unresolved'
    r = bridge(p)
    assert r['per_share'] is None and r['unresolved_claims'] == ['option']
    assert r['participating_common_equivalents'] == 20


def test_share_settled_derivative_cannot_also_be_deducted_as_liability():
    p = case(); p['claims'][1]['cash'] = 250
    with pytest.raises(ValueError, match='book liability'): bridge(p)
    p = case(); p['claims'].append({**p['claims'][0], 'economic_id': 'escrow'})
    with pytest.raises(ValueError, match='double count'): bridge(p)


@pytest.mark.parametrize('mode', ['net_share_warrant', 'net_cash_warrant'])
def test_warrant_settlement_solves_price_without_market_price_input(mode):
    p = case()
    p['claims'][1] = {**{k: v for k, v in p['claims'][1].items() if k not in ('new_shares', 'cash_proceeds')}, 'settlement': mode, 'underlying_shares': 25, 'strike': 4,
                       'exercise_basis': 'immediate_exercise_at_bridge_date'}
    r = bridge(p)
    # 100*P + 25*(P-4) = 1000 -> P=8.8, payoff=120.
    assert r['per_share'] == pytest.approx(8.8)
    assert abs(r['settlement_residual']) < 1e-9
    if mode == 'net_cash_warrant': assert r['warrant_cash_settlement'] == pytest.approx(120)
    else: assert r['warrant_new_shares'] == pytest.approx(120 / 8.8)


def test_out_of_money_warrant_has_no_immediate_settlement():
    p = case()
    del p['claims'][1]['new_shares'], p['claims'][1]['cash_proceeds']
    p['claims'][1].update(settlement='net_share_warrant', underlying_shares=25, strike=20,
                          exercise_basis='immediate_exercise_at_bridge_date')
    r = bridge(p)
    assert r['per_share'] == pytest.approx(10) and r['warrant_new_shares'] == 0


def test_missing_or_unresolved_claim_does_not_produce_equity_price():
    p = case(); p['claims'].pop()
    with pytest.raises(ValueError, match='claim inventory'): bridge(p)
    p = case(); del p['claims'][1]['new_shares'], p['claims'][1]['cash_proceeds']
    p['claims'][1]['settlement'] = 'unresolved'
    r = bridge(p)
    assert r['per_share'] is None and r['unresolved_claims'] == ['escrow']


def test_missing_assets_and_unresolved_asset_values_cannot_be_assumed_zero():
    p = case(); p['nonoperating_assets'].clear()
    with pytest.raises(ValueError, match='asset inventory'): bridge(p)
    p = case(); del p['required_asset_ids']
    with pytest.raises(ValueError, match='asset inventory'): bridge(p)
    p = case(); a = p['nonoperating_assets'][0]
    a['valuation_status'] = 'unresolved'
    with pytest.raises(ValueError, match='placeholder'): bridge(p)
    del a['value']
    r = bridge(p)
    assert r['status'] == 'unknown' and r['per_share'] is None
    assert r['nonoperating_assets'] is None and r['known_nonoperating_assets'] == 0
    assert r['unresolved_assets'] == ['cash'] and r['reason'] == 'unresolved_nonoperating_assets'
    del p['claims'][1]['new_shares'], p['claims'][1]['cash_proceeds']
    p['claims'][1]['settlement'] = 'unresolved'
    assert bridge(p)['reason'] == 'unresolved_equity_components'


def test_dated_nonoperating_bond_value_is_not_relabelled_cash():
    p = case()
    p['nonoperating_assets'][0]['basis_type'] = 'nonoperating_financial_asset_value'
    assert bridge(p)['per_share'] == pytest.approx(8)


def test_conditional_warrant_requires_explicit_trigger_state():
    p = case(); c = p['claims'][1]; del c['new_shares'], c['cash_proceeds']; c['settlement'] = 'not_triggered'
    with pytest.raises(ValueError, match='false trigger'): bridge(p)
    c.update(trigger_satisfied=False, trigger_basis='synthetic foundry ownership maintained')
    assert bridge(p)['per_share'] == pytest.approx(10)


def test_future_option_time_value_cannot_be_relabelled_immediate_exercise():
    p = case(); del p['claims'][1]['new_shares'], p['claims'][1]['cash_proceeds']
    p['claims'][1]['settlement'] = 'net_cash_warrant'
    with pytest.raises(ValueError, match='time value'): bridge(p)


def test_restricted_cash_and_incomplete_business_coverage_rejected():
    p = case(); p['nonoperating_assets'][0]['basis_type'] = 'restricted_cash'
    with pytest.raises(ValueError, match='unrestricted'): bridge(p)
    p = case(); p['businesses'].pop()
    with pytest.raises(ValueError, match='business coverage'): bridge(p)


def test_negative_residual_needs_restructuring_model_not_negative_target():
    p = case(); p['claims'][0]['cash'] = 2000
    r = bridge(p)
    assert r['status'] == 'unknown' and r['per_share'] is None and r['residual_equity'] == -800


def test_company_integration_binds_date_shares_and_sources():
    from quant.valuation.company_model import build
    from tests.valuation.test_company_model import packet, STAMP
    p = packet(); s = p['scenarios'][0]; c = case()
    c.update(known_at=s['known_at'], source_refs=s['source_refs'], basis=s['basis'])
    c['date'] = s['quarters'][3]['end']
    for r in c['businesses'] + c['nonoperating_assets'] + c['claims']: r['date'] = c['date']
    s['business_equity'] = c
    r = build(p, as_of=STAMP)['scenarios']['base']['business_equity']
    assert r['per_share'] == pytest.approx(8)
    c['outstanding_shares'] = 101
    with pytest.raises(ValueError, match='forecast closing shares'): build(p, as_of=STAMP)
