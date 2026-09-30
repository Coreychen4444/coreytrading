"""Independent content recognition, payable/cash and DCF classification cases."""
from copy import deepcopy
from datetime import date, timedelta

import pytest

from quant.core.agent_contracts import canonical_hash
from quant.valuation.company_content_capital import project
from quant.valuation.company_model import build
from tests.valuation.test_company_model import packet, STAMP


def linked_packet():
    p = packet(); s = p['scenarios'][0]; s.pop('multiple')
    ev = {k: p[k] for k in ('known_at', 'basis', 'source_refs')}
    flow_ev = {k: ev[k] for k in ('basis', 'source_refs')}
    periods = []
    for q in s['quarters']:
        q['cash']['noncash_adjustments']['content_cost'] = 4
        q['cash']['working_capital_cash_change'] = -8  # -7 recognition +1 payable CF -2 other WC.
        periods.append({**flow_ev, 'id': q['id'], 'start': q['start'], 'end': q['end'],
                        'binding_scope': 'native_cash_and_operating_DCF',
                        'recognized_additions': 7, 'content_amortization': 4,
                        'liability_operating_cash_change': 1, 'other_asset_book_change': 0,
                        'other_liability_book_change': 0, 'operating_profit_before_content_cost': 17,
                        'noncontent_DA': 5, 'noncontent_cash_capital': 12, 'other_working_capital_cash_change': -2})
    end = date.fromisoformat(periods[-1]['end'])
    periods.append({**flow_ev, 'id': 'later_annual', 'start': str(end+timedelta(days=1)),
                    'end': str(end.replace(year=end.year+1)), 'binding_scope': 'operating_DCF_only',
                    'recognized_additions': 20, 'content_amortization': 15,
                    'liability_operating_cash_change': 0, 'other_asset_book_change': 0,
                    'other_liability_book_change': 0, 'operating_profit_before_content_cost': 67,
                    'noncontent_DA': 20, 'noncontent_cash_capital': 48, 'other_working_capital_cash_change': 0})
    s['content_capital'] = {**ev, 'date': s['opening_date'], 'currency': 'USD', 'scale': 'millions',
                           'scope': 'aggregate_content_book_and_operating_cash',
                           'economic_value_status': 'unresolved', 'book_is_not_nonoperating_value': True,
                           'incremental_contract_claim_id': 'content_contracts', 'native_period_count': 8,
                           'content_noncash_key': 'content_cost', 'opening_content_asset_net': 30,
                           'opening_content_liability': 10,
                           'assumption_basis': 'Synthetic recognition/payable/stock assumptions, not title fair value.',
                           'periods': periods}
    equity_date = periods[0]['end']
    s['business_equity'] = {**ev, 'date': equity_date, 'currency': 'USD', 'scale': 'millions',
                            'value_basis': 'consolidated_operating_value_before_financial_claims',
                            'coverage_basis': 'synthetic consolidated content business', 'share_basis': 'legal closing',
                            'outstanding_shares': 100, 'use_consolidated_dcf': True,
                            'required_business_ids': ['consolidated'], 'businesses': [],
                            'required_asset_ids': [], 'nonoperating_assets': [],
                            'required_claim_ids': ['content_contracts'],
                            'claims': [{**flow_ev, 'date': equity_date, 'economic_id': 'content_contracts',
                                        'settlement': 'unresolved'}]}
    dcf_periods = []
    for r in periods[1:]:
        op = r['operating_profit_before_content_cost']-r['content_amortization']
        dcf_periods.append({**ev, 'start': r['start'], 'end': r['end'], 'wacc': .1,
                            'operating_profit': op, 'cash_operating_tax': op*.2,
                            'depreciation_amortization': r['noncontent_DA']+r['content_amortization'],
                            'gross_capital_investment': r['noncontent_cash_capital']+r['recognized_additions'],
                            'capital_grants': 0,
                            'working_capital_increase': -r['liability_operating_cash_change']-r['other_working_capital_cash_change']})
    s['operating_dcf'] = {**ev, 'date': equity_date, 'currency': 'USD', 'scale': 'millions',
                          'cashflow_basis': 'consolidated_unlevered_after_operating_tax',
                          'sbc_treatment': 'expense_no_addback',
                          'lease_treatment': 'operating_leases_expensed_finance_leases_in_capital_and_debt',
                          'tax_basis': 'synthetic 20%', 'capital_basis': 'synthetic recognition and payable bridge',
                          'periods': dcf_periods,
                          'terminal': {**ev, 'date': periods[-1]['end'], 'growth': .03,
                                       'incremental_roic': .2, 'wacc': .1, 'next_year_normalized_nopat': 42.848}}
    return p


def test_independent_recognition_liability_cash_and_book_arithmetic():
    p = linked_packet(); saved = deepcopy(p)
    out = build(p, as_of=STAMP)['scenarios']['base']; ledger = out['content_capital']
    first = ledger['periods'][0]; last = ledger['periods'][-1]
    assert first['content_cash_spending'] == 6
    assert first['closing_content_asset_net'] == 33 and first['closing_content_liability'] == 11
    assert first['net_content_book_capital'] == 22
    assert first['dcf_gross_capital_investment'] == 19 and first['dcf_working_capital_increase'] == 1
    assert first['dcf_depreciation_amortization'] == 9 and first['native_working_capital_cash_change'] == -8
    assert last['closing_content_asset_net'] == 59 and last['closing_content_liability'] == 18
    assert out['quarters'][0]['gaap_eps'] == .08
    assert out['quarters'][0]['cash']['operating_cash_flow'] == 11
    assert out['quarters'][0]['cash']['ending_liquidity'] == 102
    assert out['business_equity']['per_share'] is None and out['business_equity']['unresolved_claims'] == ['content_contracts']
    assert ledger['publication'] == 'withheld' and ledger['validated'] is False
    assert ledger['economic_value_status'] == 'unresolved'
    assert ledger['receipt_hash'] == canonical_hash({k: v for k, v in ledger.items() if k != 'receipt_hash'})
    assert p == saved


def test_other_book_movements_are_separate_from_cash_and_cost():
    case = linked_packet()['scenarios'][0]['content_capital']
    row = case['periods'][0]; row['other_asset_book_change'] = -2; row['other_liability_book_change'] = -1
    out = project(case)['periods'][0]
    assert out['closing_content_asset_net'] == 31 and out['closing_content_liability'] == 10
    assert out['content_cash_spending'] == 6 and out['operating_profit'] == 13


@pytest.mark.parametrize('field', ['depreciation_amortization', 'gross_capital_investment', 'working_capital_increase'])
def test_dcf_rejects_double_addback_capital_or_payable_charge(field):
    p = linked_packet(); p['scenarios'][0]['operating_dcf']['periods'][-1][field] += 1
    with pytest.raises(ValueError, match='DCF economics differ from content'): build(p, as_of=STAMP)


@pytest.mark.parametrize('mutation', ['DA', 'WC', 'PPE', 'amort', 'profit', 'missing_amort'])
def test_native_cfo_capital_and_cost_cannot_disconnect(mutation):
    p = linked_packet(); q = p['scenarios'][0]['quarters'][0]
    if mutation == 'DA': q['cash']['depreciation_amortization'] += 4
    elif mutation == 'WC': q['cash']['working_capital_cash_change'] -= 1
    elif mutation == 'PPE': q['cash']['capex_investing'] += 7
    elif mutation == 'amort': q['cash']['noncash_adjustments']['content_cost'] += 1
    elif mutation == 'profit': q['segments']['corporate']['operating_profit'] += 1
    else: q['cash']['noncash_adjustments'].pop('content_cost')
    with pytest.raises(ValueError, match='native profit|amortization addback key'): build(p, as_of=STAMP)


@pytest.mark.parametrize('mutation', ['scope', 'economic_value', 'nonoperating', 'native_count', 'gap', 'future_evidence',
                                    'missing_source', 'debt_field', 'long_scope', 'negative_cash', 'depleted_asset', 'negative_liability'])
def test_content_population_timing_evidence_and_unknowns_stay_explicit(mutation):
    p = linked_packet(); case = p['scenarios'][0]['content_capital']; row = case['periods'][0]
    if mutation == 'scope': case['scope'] = 'complete_content_value'
    elif mutation == 'economic_value': case['economic_value_status'] = 'valued'
    elif mutation == 'nonoperating': case['book_is_not_nonoperating_value'] = False
    elif mutation == 'native_count': case['native_period_count'] = True
    elif mutation == 'gap': row['start'] = '2026-09-10'
    elif mutation == 'future_evidence': case['known_at'] = '2026-09-09T00:00:00Z'
    elif mutation == 'missing_source': row['source_refs'] = []
    elif mutation == 'debt_field': case['periods'][-1]['debt_repaid'] = 0
    elif mutation == 'long_scope': case['periods'][-1]['binding_scope'] = 'native_cash_and_operating_DCF'
    elif mutation == 'negative_cash': row['liability_operating_cash_change'] = 8
    elif mutation == 'depleted_asset': row['content_amortization'] = 38
    else: row['other_liability_book_change'] = -12
    with pytest.raises(ValueError): build(p, as_of=STAMP)


@pytest.mark.parametrize('field', ['recognized_additions', 'liability_operating_cash_change', 'noncontent_DA'])
@pytest.mark.parametrize('value', [True, None, float('nan'), float('inf')])
def test_invalid_numbers_cannot_fill_content_fields(field, value):
    case = linked_packet()['scenarios'][0]['content_capital']; case['periods'][0][field] = value
    with pytest.raises(ValueError): project(case)


@pytest.mark.parametrize('mutation', ['remove_claim', 'value_claim', 'missing_DCF', 'missing_DCF_period', 'terminal_date'])
def test_content_cash_does_not_finish_equity_rights_or_cover_missing_long_period(mutation):
    p = linked_packet(); s = p['scenarios'][0]
    if mutation == 'remove_claim': s['business_equity']['claims'] = []
    elif mutation == 'value_claim': s['business_equity']['claims'][0].update(settlement='cash', cash=10)
    elif mutation == 'missing_DCF': s.pop('operating_dcf')
    elif mutation == 'missing_DCF_period': s['operating_dcf']['periods'].pop()
    else: s['operating_dcf']['terminal']['date'] = '2030-09-06'
    with pytest.raises(ValueError): build(p, as_of=STAMP)


def held_packet():
    p = linked_packet(); s = p['scenarios'][0]
    p['operating_path_hold'] = {k: p[k] for k in ('known_at', 'basis', 'source_refs')}
    p['operating_path_hold'].update(status='unestimable', reason='Noncontent physical capital path unresolved.')
    s.pop('operating_dcf'); s['business_equity']['allow_unresolved_operating_inventory'] = True
    return p


def test_held_whole_path_retains_content_native_cash_but_no_operating_or_equity_price():
    prior = build(linked_packet(), as_of=STAMP)['scenarios']['base']
    packet = held_packet(); saved = deepcopy(packet)
    out = build(packet, as_of=STAMP)
    s = out['scenarios']['base']; ledger = s['content_capital']
    assert s['quarters'] == prior['quarters']
    assert ledger['periods'] == prior['content_capital']['periods']
    assert ledger['operating_dcf_binding_status'] == 'inactive_whole_operating_path_hold'
    assert ledger['receipt_hash'] == canonical_hash({k:v for k,v in ledger.items() if k != 'receipt_hash'})
    assert s['operating_value']['status'] == 'unknown' and s['business_equity']['per_share'] is None
    assert out['estimability']['operating_value'] == 'unestimable'
    assert s['business_equity']['unresolved_claims'] == ['content_contracts']
    assert packet == saved


@pytest.mark.parametrize('mutation', ['active_DCF', 'native_cash_mismatch', 'resolved_rights', 'future_evidence'])
def test_held_content_ledger_cannot_bypass_binding_or_evidence(mutation):
    p = held_packet(); s = p['scenarios'][0]
    if mutation == 'active_DCF': s['operating_dcf'] = linked_packet()['scenarios'][0]['operating_dcf']
    elif mutation == 'native_cash_mismatch': s['quarters'][0]['cash']['working_capital_cash_change'] += 1
    elif mutation == 'resolved_rights': s['business_equity']['claims'][0].update(settlement='cash', cash=10)
    else: s['content_capital']['known_at'] = '2026-09-09T00:00:00Z'
    with pytest.raises(ValueError): build(p, as_of=STAMP)
