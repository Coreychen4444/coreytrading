"""Promised-payment PV safety; synthetic arithmetic is not issuer acceptance."""
from copy import deepcopy
import pytest

from quant.valuation.company_equity import bridge
from quant.valuation.company_model import business_equity, stamp
from tests.valuation.test_company_equity import case
from tests.valuation.test_company_model import packet, STAMP


def inputs():
    p = case()
    p['date'] = '2026-01-01'
    for row in p['businesses'] + p['nonoperating_assets'] + p['claims']:
        row['date'] = p['date']
    p['required_claim_ids'] = ['promised', 'residual']
    p['claims'] = [
        {'economic_id': 'promised', 'date': p['date'], 'basis': 'Synthetic note component.',
         'source_refs': ['synthetic'], 'settlement': 'promised_cashflow_component',
         'component_scope': 'promised_payments_before_credit_and_embedded_options',
         'unresolved_residual_id': 'residual', 'discount_status': 'scenario_assumption',
         'discount_day_count': 'ACT_365.25', 'annual_effective_discount_rate': .05,
         'discount_basis': 'Synthetic chosen yield; full claim economics not accepted.',
         'remaining_principal': 100, 'maturity_date': '2027-01-01',
         'payments': [{'id': 'final', 'due': '2027-01-01', 'coupon': 5, 'principal': 100,
                       'basis': 'Synthetic future obligation.', 'source_refs': ['synthetic']}]},
        {'economic_id': 'residual', 'date': p['date'], 'basis': 'Unestimated credit/option economics.',
         'source_refs': ['synthetic'], 'settlement': 'unresolved'}]
    return p


def test_promised_pv_enters_known_claims_once_but_does_not_close_full_claim():
    p = inputs(); old = deepcopy(p)
    r = bridge(p)
    expected = 105 / 1.05 ** (365 / 365.25)
    assert r['fixed_cash_claims'] == pytest.approx(expected)
    assert r['promised_payment_components'][0]['present_value'] == pytest.approx(expected)
    assert r['status'] == 'unknown' and r['per_share'] is None
    assert r['unresolved_claims'] == ['residual']
    assert r['validated'] is False and r['publication'] == 'withheld'
    assert p == old


@pytest.mark.parametrize('change', ['remove', 'resolve', 'self'])
def test_residual_cannot_be_omitted_or_cleared_to_turn_promised_pv_into_fair_debt(change):
    p = inputs()
    if change == 'remove':
        p['claims'].pop(); p['required_claim_ids'].pop()
    elif change == 'resolve': p['claims'][1].update(settlement='cash', cash=0)
    else: p['claims'][0]['unresolved_residual_id'] = 'promised'
    with pytest.raises(ValueError, match='residual'): bridge(p)


@pytest.mark.parametrize('field,value', [
    ('annual_effective_discount_rate', None), ('annual_effective_discount_rate', True),
    ('annual_effective_discount_rate', float('nan')), ('annual_effective_discount_rate', -1),
    ('annual_effective_discount_rate', 1), ('discount_status', 'issuer_yield'),
    ('discount_day_count', '30_360'), ('discount_basis', ' '), ('remaining_principal', 0),
    ('remaining_principal', 99), ('maturity_date', '2027-02-01')])
def test_invalid_rate_scope_or_unrepaid_principal_fails_closed(field, value):
    p = inputs(); p['claims'][0][field] = value
    with pytest.raises(ValueError): bridge(p)


@pytest.mark.parametrize('change', ['past', 'duplicate', 'cash', 'new_shares', 'missing_basis', 'negative_coupon'])
def test_past_cash_duplicate_payment_or_scalar_settlement_cannot_be_charged_again(change):
    p = inputs(); c = p['claims'][0]
    if change == 'past': c['payments'][0]['due'] = p['date']
    elif change == 'duplicate': c['payments'].append(deepcopy(c['payments'][0]))
    elif change in ('cash', 'new_shares'): c[change] = 100
    elif change == 'missing_basis': c['payments'][0]['basis'] = ''
    else: c['payments'][0]['coupon'] = -1
    with pytest.raises(ValueError): bridge(p)


def test_host_checks_nested_payment_sources():
    p = inputs(); p.update(known_at=STAMP, source_refs=['synthetic'], basis='Synthetic evidence.')
    q = [{'end': p['date'], 'closing_shares': p['outstanding_shares']}]
    r = business_equity(p, q, sources=packet()['sources'], cutoff=stamp(STAMP), funding_gap=0)
    assert r['promised_payment_components'][0]['source_completeness'] == 'not_established'
    p['claims'][0]['payments'][0]['source_refs'] = ['missing_contract']
    with pytest.raises(KeyError):
        business_equity(p, q, sources=packet()['sources'], cutoff=stamp(STAMP), funding_gap=0)


def translated_inputs():
    p = inputs()
    c = p['claims'][0]
    c['translation'] = {'contract_currency': 'EUR', 'valuation_currency': 'USD',
                        'contract_remaining_principal': 80, 'usd_per_eur': 1.25,
                        'status': 'scenario_assumption', 'basis': 'Synthetic fixed FX condition.',
                        'source_refs': ['synthetic']}
    c['payments'][0].update(contract_coupon=4, contract_principal=80)
    return p


def test_native_contract_and_translation_are_preserved_without_charging_fx_twice():
    p = translated_inputs(); old = deepcopy(p)
    r = bridge(p); c = r['promised_payment_components'][0]
    assert r['fixed_cash_claims'] == pytest.approx(84 * 1.25 / 1.05 ** (365 / 365.25))
    assert c['translation'] == p['claims'][0]['translation']
    assert c['payments'][0]['contract_coupon'] == 4
    assert c['payments'][0]['contract_principal'] == 80
    assert c['payments'][0]['principal'] == 100
    assert r['unresolved_claims'] == ['residual'] and r['per_share'] is None
    assert p == old
    c['translation']['basis'] = 'changed output'
    assert p == old  # A receipt does not mutate the scenario.


@pytest.mark.parametrize('field,value', [
    ('contract_currency', 'USD'), ('contract_currency', 'JPY'),
    ('valuation_currency', 'EUR'), ('contract_remaining_principal', 81),
    ('contract_remaining_principal', 0), ('usd_per_eur', 0), ('usd_per_eur', -1),
    ('usd_per_eur', None), ('usd_per_eur', True), ('usd_per_eur', float('inf')),
    ('status', 'issuer_spot'), ('basis', ' '), ('source_refs', []),
    ('source_refs', ['']), ('source_refs', 'synthetic')])
def test_missing_or_inconsistent_fx_declaration_fails_closed(field, value):
    p = translated_inputs(); p['claims'][0]['translation'][field] = value
    with pytest.raises(ValueError): bridge(p)


@pytest.mark.parametrize('field,value', [
    ('contract_coupon', None), ('contract_coupon', 5), ('contract_coupon', -1),
    ('contract_principal', None), ('contract_principal', 81),
    ('coupon', 6), ('principal', 101)])
def test_translation_cannot_hide_missing_local_amounts_or_conflicting_usd_payments(field, value):
    p = translated_inputs(); p['claims'][0]['payments'][0][field] = value
    with pytest.raises(ValueError): bridge(p)


def test_native_amounts_without_translation_and_null_or_extra_translation_are_rejected():
    p = translated_inputs(); p['claims'][0].pop('translation')
    with pytest.raises(ValueError, match='translation'): bridge(p)
    p = translated_inputs(); p['claims'][0]['translation'] = None
    with pytest.raises(ValueError, match='translation'): bridge(p)
    p = translated_inputs(); p['claims'][0]['translation']['forward_value'] = 200
    with pytest.raises(ValueError, match='translation'): bridge(p)


def test_changing_fx_requires_every_translated_amount_to_change_consistently():
    p = translated_inputs(); c = p['claims'][0]; c['translation']['usd_per_eur'] = 2
    with pytest.raises(ValueError): bridge(p)
    c['remaining_principal'] = 160
    c['payments'][0].update(coupon=8, principal=160)
    r = bridge(p)
    assert r['fixed_cash_claims'] == pytest.approx(168 / 1.05 ** (365 / 365.25))
    assert r['per_share'] is None and r['publication'] == 'withheld'


def test_host_checks_translation_evidence_separately_from_contract_sources():
    p = translated_inputs(); p.update(known_at=STAMP, source_refs=['synthetic'], basis='Synthetic evidence.')
    q = [{'end': p['date'], 'closing_shares': p['outstanding_shares']}]
    r = business_equity(p, q, sources=packet()['sources'], cutoff=stamp(STAMP), funding_gap=0)
    assert r['promised_payment_components'][0]['translation']['status'] == 'scenario_assumption'
    p['claims'][0]['translation']['source_refs'] = ['missing_fx_basis']
    with pytest.raises(KeyError):
        business_equity(p, q, sources=packet()['sources'], cutoff=stamp(STAMP), funding_gap=0)


def test_translation_cannot_reference_information_available_after_input_freeze():
    p = translated_inputs(); p.update(known_at=STAMP, source_refs=['synthetic'], basis='Synthetic evidence.')
    q = [{'end': p['date'], 'closing_shares': p['outstanding_shares']}]
    sources = deepcopy(packet()['sources'])
    sources['late_fx'] = {**sources['synthetic'], 'available_at': '2027-01-01T00:00:00+00:00'}
    p['claims'][0]['translation']['source_refs'] = ['late_fx']
    with pytest.raises(ValueError, match='source not available'):
        business_equity(p, q, sources=sources, cutoff=stamp(STAMP), funding_gap=0)


def test_worker_keeps_fx_scope_and_rate_without_repeating_full_payment_schedule():
    from quant.ops.agent_workflow import _valuation_worker_receipt
    full = bridge(translated_inputs())
    receipt = {'receipt_hash': 'synthetic_full_receipt', 'tickers': {'SYN': {
        'company_research': {'scenarios': {'base': {'business_equity': full}}}}}}
    short = _valuation_worker_receipt(receipt)['tickers']['SYN']['company_research']['scenarios']['base']['business_equity']['promised_payment_components'][0]
    assert short['translation'] == full['promised_payment_components'][0]['translation']
    assert short['payment_count'] == 1 and 'payments' not in short
    assert short['receipt_hash'] == full['promised_payment_components'][0]['receipt_hash']
