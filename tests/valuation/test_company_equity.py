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
            'nonoperating_assets': [{**row, 'economic_id': 'cash', 'basis_type': 'excess_unrestricted_cash', 'value': 100}],
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
