"""Contract capital is one cash outflow under both CFO and FCFF presentations."""
from copy import deepcopy

import pytest

from quant.valuation.company_amortization import project
from quant.valuation.company_model import build
from tests.valuation.test_company_amortization import case
from tests.valuation.test_company_model import packet, STAMP
from tests.valuation.test_company_cashflow import case as dcf_case


def capital():
    return {'basis_type': 'deferred_contract_acquisition_costs',
            'cashflow_classification': 'operating_working_capital',
            'cash_investment': 4, 'amortization': 3,
            'noncash_adjustment_key': 'commission',
            'other_working_capital_cash_change': -2,
            'basis': 'synthetic commission cash and other AR cash separated',
            'source_refs': ['synthetic']}


def test_contract_capital_moves_to_dcf_once_without_changing_normal_da():
    p = case(); p['periods'][0]['operating_capital'] = capital()
    original = deepcopy(p); r = project(p); row = r['periods'][0]
    assert p == original
    assert row['dcf_depreciation_amortization'] == row['total_da'] + 3
    assert row['gross_capital_investment'] == 30 + 8 + 4
    assert row['dcf_working_capital_increase'] == 2
    assert r['operating_capital_scope']['period_count'] == 1
    assert r['publication'] == 'withheld' and r['source_completeness'] == 'not_established'
    row['operating_capital']['cash_investment'] = 500
    assert p == original


@pytest.mark.parametrize('field,value', [('cash_investment', None), ('cash_investment', True),
    ('cash_investment', -1), ('amortization', float('nan')), ('amortization', -1),
    ('other_working_capital_cash_change', None), ('other_working_capital_cash_change', float('inf')),
    ('basis_type', 'property_equipment'), ('cashflow_classification', 'investing'),
    ('noncash_adjustment_key', ''), ('basis', ''), ('source_refs', []), ('source_refs', [False])])
def test_unknown_amount_or_ambiguous_capital_scope_rejected(field, value):
    p = case(); p['periods'][0]['operating_capital'] = capital()
    p['periods'][0]['operating_capital'][field] = value
    with pytest.raises(ValueError): project(p)


@pytest.mark.parametrize('mutation', ['none', 'missing_amount', 'extra_amount'])
def test_optional_field_presence_does_not_make_unknown_values_zero(mutation):
    p = case(); c = capital(); p['periods'][0]['operating_capital'] = c
    if mutation == 'none': p['periods'][0]['operating_capital'] = None
    elif mutation == 'missing_amount': del c['cash_investment']
    else: c['other_capital'] = 5
    with pytest.raises(ValueError): project(p)


def linked_packet():
    p = packet(); s = p['scenarios'][0]
    ledger = case(); ledger.update(known_at=s['known_at'], basis=s['basis'], source_refs=s['source_refs'],
                                  date=s['opening_date'], periods=[])
    ledger['iprd_readiness'] = {'status': 'not_ready_in_projection', 'basis': 'synthetic delay'}
    for q in s['quarters']:
        cash = q['cash']; cash['noncash_adjustments']['commission'] = 3
        cash['working_capital_cash_change'] = -6
        op = sum(x['operating_profit'] for x in q['segments'].values())
        ledger['periods'].append({'start': q['start'], 'end': q['end'], 'basis': 'synthetic',
            'source_refs': s['source_refs'], 'existing_amortization': 1,
            'other_da': 4, 'operating_profit_before_acquired_amortization': op + 1,
            'cash_new_assets': 10, 'new_financed_technology_licenses': 2,
            'technology_license_principal_paid': cash['capex_financing'], 'operating_capital': capital()})
    s['acquired_intangible_runoff'] = ledger
    d = dcf_case(); q = s['quarters'][4]
    d.update(known_at=s['known_at'], basis=s['basis'], source_refs=s['source_refs'], date=s['quarters'][3]['end'])
    d['periods'][0].update(start=q['start'], end=q['end'], operating_profit=13,
        cash_operating_tax=2.73, depreciation_amortization=8,
        gross_capital_investment=16, capital_grants=0, working_capital_increase=2)
    d['terminal']['date'] = q['end']; s['operating_dcf'] = d
    return p


def test_host_cash_and_dcf_reclassification_have_equal_unlevered_cash():
    p = linked_packet(); original = deepcopy(p); r = build(p, as_of=STAMP)['scenarios']['base']
    assert p == original
    assert r['quarters'][0]['cash']['operating_cash_flow'] == 12
    # CFO-style unlevered: EBIT-tax+normalDA+commissionAmort-PPE-financedNew+combinedWC.
    # DCF-style: EBIT-tax+normalDA+commissionAmort-(PPE+financedNew+commissionCash)-otherWC.
    assert r['operating_value']['periods'][0]['fcff'] == pytest.approx(13 - 2.73 + 5 + 3 - 10 - 2 - 6)
    assert r['acquired_intangible_runoff']['operating_capital_scope']['period_count'] == 8


@pytest.mark.parametrize('mutation', ['quarter_double_da', 'quarter_missing_adjustment', 'quarter_other_wc',
    'DCF_missing_amort', 'DCF_double_amort', 'DCF_double_capital', 'DCF_double_wc', 'source'])
def test_host_rejects_dropped_or_repeated_commission_cash_and_amort(mutation):
    p = linked_packet(); s = p['scenarios'][0]; q = s['quarters'][4]; d = s['operating_dcf']['periods'][0]
    if mutation == 'quarter_double_da': q['cash']['depreciation_amortization'] += 3
    elif mutation == 'quarter_missing_adjustment': del q['cash']['noncash_adjustments']['commission']
    elif mutation == 'quarter_other_wc': q['cash']['working_capital_cash_change'] = -2
    elif mutation == 'DCF_missing_amort': d['depreciation_amortization'] = 5
    elif mutation == 'DCF_double_amort': d['depreciation_amortization'] += 3
    elif mutation == 'DCF_double_capital': d['gross_capital_investment'] += 4
    elif mutation == 'DCF_double_wc': d['working_capital_increase'] += 4
    else: s['acquired_intangible_runoff']['periods'][4]['operating_capital']['source_refs'] = ['absent']
    with pytest.raises((ValueError, KeyError)): build(p, as_of=STAMP)


def test_worker_keeps_contract_capital_scope_and_unverified_status():
    from quant.ops.agent_workflow import _valuation_worker_receipt
    company = build(linked_packet(), as_of=STAMP)
    receipt = {'receipt_hash': 'full-host-receipt', 'tickers': {'SYN': {'company_research': company}}}
    view = _valuation_worker_receipt(receipt)
    scope = view['tickers']['SYN']['company_research']['scenarios']['base']['acquired_intangible_runoff']['operating_capital_scope']
    assert scope == {'basis_type': 'deferred_contract_acquisition_costs',
                     'cashflow_classification': 'operating_working_capital',
                     'period_count': 8, 'source_completeness': 'not_established'}


def test_fyi_keeps_scope_limit_and_withholds_prices():
    from quant.report.valuation_report import fyi_map
    from quant.valuation.assessment import build as assess
    p = linked_packet()
    receipt = assess({'selected_tickers': ['SYN'], 'valuation': {'company_models': {'SYN': p}}}, as_of=STAMP)
    text = fyi_map(receipt)['SYN']
    assert '佣金付款与摊销分别计入一次' in text
    assert '新收购资产与资本范围未验收' in text and '暂不发布' in text
