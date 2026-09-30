"""Independent share/cash arithmetic and unresolved current-rights boundaries."""
from copy import deepcopy

import pytest

from quant.core.agent_contracts import canonical_hash
from quant.valuation import company_share_settlements as settlements
from quant.valuation.company_model import build
from tests.valuation.test_company_model import packet, STAMP


def linked_packet():
    p = packet(); scenario = p['scenarios'][0]; scenario.pop('multiple')
    evidence = {k: p[k] for k in ('known_at', 'basis', 'source_refs')}
    flow_evidence = {k: evidence[k] for k in ('basis', 'source_refs')}
    periods = []
    for i, q in enumerate(scenario['quarters']):
        q['shares'].update(issued=3, repurchased=1, closing=102+2*i, basic_weighted=101+2*i)
        q['cash'].update(equity_cash_raised=2, common_buybacks=5)
        q['cash']['other_cash_flows']['unassigned_financing'] = .3
        periods.append({**flow_evidence, 'id': q['id'], 'start': q['start'], 'end': q['end'],
                        'issuances': [{**flow_evidence, 'economic_id': 'exercise_proxy', 'shares': 1,
                                       'cash_proceeds': 2, 'status': 'scenario_assumption'},
                                      {**flow_evidence, 'economic_id': 'award_proxy', 'shares': 2,
                                       'cash_proceeds': 0, 'status': 'scenario_assumption'}],
                        'repurchase': {**flow_evidence, 'shares': 1, 'cash_paid': 5,
                                       'cash_status': 'book_equals_cash_scenario_assumption'},
                        'other_financing': {**flow_evidence, 'cash_flow': .3,
                                            'native_cash_key': 'unassigned_financing',
                                            'status': 'unassigned_scenario_residual'}})
    scenario['ordinary_share_cash_settlements'] = {
        **evidence, 'date': scenario['opening_date'], 'currency': 'USD', 'scale': 'millions',
        'scope': 'ordinary_share_cash_flow_proxy', 'opening_common_shares': 100,
        'cohort_assignment': 'not_established', 'claim_valuation_status': 'unresolved',
        'unresolved_current_claim_ids': ['employee_rights'], 'periods': periods}
    equity_date = scenario['quarters'][0]['end']
    row = {**evidence, 'date': equity_date}
    scenario['business_equity'] = {
        **row, 'currency': 'USD', 'scale': 'millions',
        'value_basis': 'consolidated_operating_value_before_financial_claims',
        'coverage_basis': 'synthetic consolidated business', 'share_basis': 'synthetic closing legal shares',
        'outstanding_shares': 102, 'required_business_ids': ['group'],
        'businesses': [{**row, 'economic_id': 'group', 'value': 100}],
        'required_asset_ids': [], 'nonoperating_assets': [], 'required_claim_ids': ['employee_rights'],
        'claims': [{**row, 'economic_id': 'employee_rights', 'settlement': 'unresolved'}]}
    return p


def test_flow_hand_calculation_is_separate_from_current_claim_value_and_sbc():
    p = linked_packet(); saved = deepcopy(p)
    out = build(p, as_of=STAMP)['scenarios']['base']
    first = out['ordinary_share_cash_settlements']['periods'][0]
    assert first['gross_issued'] == 3 and first['repurchased'] == 1
    assert first['closing_common_shares'] == 102
    assert first['net_financing_cash'] == pytest.approx(-2.7)
    assert out['quarters'][0]['gaap_eps'] == pytest.approx(8/101)
    assert out['quarters'][0]['cash']['operating_cash_flow'] == 13
    assert out['quarters'][0]['cash']['ending_liquidity'] == pytest.approx(101.3)
    assert out['business_equity']['unresolved_claims'] == ['employee_rights']
    assert out['business_equity']['per_share'] is None
    ledger = out['ordinary_share_cash_settlements']
    assert ledger['publication'] == 'withheld' and ledger['validated'] is False
    assert ledger['receipt_hash'] == canonical_hash({k: v for k, v in ledger.items() if k != 'receipt_hash'})
    assert p == saved


@pytest.mark.parametrize('section,key', [('shares', 'issued'), ('shares', 'repurchased'),
                                        ('shares', 'closing'), ('cash', 'equity_cash_raised'),
                                        ('cash', 'common_buybacks')])
def test_host_rejects_disconnected_native_share_and_cash_fields(section, key):
    p = linked_packet(); p['scenarios'][0]['quarters'][0][section][key] += 1
    with pytest.raises(ValueError, match='differ from settlement ledger'): build(p, as_of=STAMP)


def test_host_rejects_missing_or_duplicated_residual_binding():
    p = linked_packet(); q = p['scenarios'][0]['quarters'][0]
    q['cash']['other_cash_flows'].pop('unassigned_financing')
    with pytest.raises(ValueError, match='cash key missing'): build(p, as_of=STAMP)
    q['cash']['other_cash_flows']['unassigned_financing'] = .6
    with pytest.raises(ValueError, match='differ from settlement ledger'): build(p, as_of=STAMP)


@pytest.mark.parametrize('field,value', [('cohort_assignment', 'established'),
                                       ('claim_valuation_status', 'valued'), ('scope', 'claim_fair_value'),
                                       ('unresolved_current_claim_ids', []), ('opening_common_shares', True)])
def test_proxy_cannot_claim_cohort_or_current_rights_acceptance(field, value):
    p = linked_packet(); p['scenarios'][0]['ordinary_share_cash_settlements'][field] = value
    with pytest.raises(ValueError): build(p, as_of=STAMP)


@pytest.mark.parametrize('mode', ['remove', 'settle'])
def test_current_rights_cannot_be_extinguished_by_future_flow_proxy(mode):
    p = linked_packet(); equity = p['scenarios'][0]['business_equity']
    if mode == 'remove': equity['claims'] = []
    else: equity['claims'][0].update(settlement='cash', cash=1)
    with pytest.raises(ValueError, match='cannot value or remove'): build(p, as_of=STAMP)


@pytest.mark.parametrize('mutation', ['source', 'future_source', 'period', 'duplicate', 'cash_status',
                                    'nan', 'negative', 'no_shares', 'unassigned_status', 'claim_value'])
def test_declared_population_evidence_and_cash_qualification_required(mutation):
    p = linked_packet(); case = p['scenarios'][0]['ordinary_share_cash_settlements']; row = case['periods'][0]
    if mutation == 'source': row['issuances'][0]['source_refs'] = []
    elif mutation == 'future_source': case['known_at'] = '2026-09-09T00:00:00+00:00'
    elif mutation == 'period': row['start'] = '2026-09-10'
    elif mutation == 'duplicate': row['issuances'][1]['economic_id'] = 'exercise_proxy'
    elif mutation == 'cash_status': row['repurchase']['cash_status'] = 'observed_cash'
    elif mutation == 'nan': row['issuances'][0]['shares'] = float('nan')
    elif mutation == 'negative': row['repurchase']['shares'] = -1
    elif mutation == 'no_shares': row['issuances'][0]['shares'] = 0
    elif mutation == 'unassigned_status': row['other_financing']['status'] = 'observed_inflow'
    else: case['claim_value'] = 0
    with pytest.raises(ValueError): build(p, as_of=STAMP)


def test_projection_rejects_empty_or_overdrawn_legal_share_population():
    case = linked_packet()['scenarios'][0]['ordinary_share_cash_settlements']
    case['periods'] = []
    with pytest.raises(ValueError, match='projection quarters'): settlements.project(case)
    case = linked_packet()['scenarios'][0]['ordinary_share_cash_settlements']
    case['periods'][0]['repurchase']['shares'] = 104
    with pytest.raises(ValueError, match='no positive closing shares'): settlements.project(case)
