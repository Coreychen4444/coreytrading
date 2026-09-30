from copy import deepcopy
import pytest

from quant.valuation.company_leases import project


def case():
    return {'currency': 'USD', 'scale': 'millions', 'date': '2026-12-31',
            'scope': 'opening_commenced_finance_leases_only',
            'opening_debt_excludes_this_population': True,
            'opening_current': 20, 'opening_noncurrent': 80, 'reported_total': 100,
            'annual_effective_rate': .1, 'interest_basis': 'synthetic effective-rate scenario',
            'interest_cash_equals_accrual': True, 'payment_timing': 'period_end_scenario_assumption',
            'bucket_allocation': 'proportional_calendar_days_scenario_assumption',
            'payment_buckets': [{'id': '2027', 'start': '2027-01-01', 'end': '2027-12-31',
                                'total_cash_payment': 20, 'basis': 'explicit calendar caption', 'source_refs': ['synthetic']}],
            'periods': [{'start': '2027-01-01', 'end': '2027-12-31', 'payment_bucket_id': '2027',
                         'net_nonoperating_excluding_this_lease_interest': 50,
                         'nonoperating_allocation_basis': 'synthetic excludes lease interest',
                         'basis': 'scenario timing', 'source_refs': ['synthetic']}]}


def test_cash_interest_principal_book_runoff_and_income_are_distinct():
    p = case(); old = deepcopy(p); r = project(p); row = r['periods'][0]
    interest = 100 * (1.1 ** (365 / 365.25) - 1)
    assert row['cash_payment'] == 20 and row['interest'] == pytest.approx(interest)
    assert row['principal'] == pytest.approx(20 - interest)
    assert row['closing_liability'] == pytest.approx(80 + interest)
    assert row['net_nonoperating'] == pytest.approx(50 - interest)
    assert p == old and r['publication'] == 'withheld' and r['source_completeness'] == 'not_established'
    assert not any('capital' in key for key in row)


def test_calendar_day_allocation_handles_leap_year_and_carries_liability():
    p = case(); p['date'] = '2027-12-31'; p['annual_effective_rate'] = 0
    bucket = p['payment_buckets'][0]; bucket.update(id='2028', start='2028-01-01', end='2028-12-31', total_cash_payment=36.6)
    template = p['periods'][0]; p['periods'] = []
    for start, end in [('2028-01-01', '2028-03-31'), ('2028-04-01', '2028-06-30')]:
        p['periods'].append({**template, 'start': start, 'end': end, 'payment_bucket_id': '2028'})
    a, b = project(p)['periods']
    assert a['cash_payment'] == pytest.approx(9.1) and b['cash_payment'] == pytest.approx(9.1)
    assert b['opening_liability'] == a['closing_liability'] and b['closing_liability'] == pytest.approx(81.8)


@pytest.mark.parametrize('mutation', ['opening', 'rate_unknown', 'rate_bool', 'rate_negative', 'rate_nan',
                                    'rate_high', 'neg_amort', 'overpay', 'bucket_missing', 'bucket_overlap',
                                    'period_gap', 'period_outside', 'scope', 'debt_overlap', 'income_basis', 'empty'])
def test_unknown_or_inconsistent_contract_rejected(mutation):
    p = case()
    if mutation == 'opening': p['reported_total'] = 101
    elif mutation == 'rate_unknown': p['annual_effective_rate'] = None
    elif mutation == 'rate_bool': p['annual_effective_rate'] = True
    elif mutation == 'rate_negative': p['annual_effective_rate'] = -.1
    elif mutation == 'rate_nan': p['annual_effective_rate'] = float('nan')
    elif mutation == 'rate_high': p['annual_effective_rate'] = 1
    elif mutation == 'neg_amort': p['payment_buckets'][0]['total_cash_payment'] = 1
    elif mutation == 'overpay': p['payment_buckets'][0]['total_cash_payment'] = 200
    elif mutation == 'bucket_missing': p['periods'][0]['payment_bucket_id'] = 'missing'
    elif mutation == 'bucket_overlap': p['payment_buckets'].append({**p['payment_buckets'][0], 'id': 'overlap'})
    elif mutation == 'period_gap': p['periods'][0]['start'] = '2027-01-02'
    elif mutation == 'period_outside': p['periods'][0]['end'] = '2028-01-01'
    elif mutation == 'scope': p['scope'] = 'all_leases'
    elif mutation == 'debt_overlap': p['opening_debt_excludes_this_population'] = False
    elif mutation == 'income_basis': p['periods'][0].pop('nonoperating_allocation_basis')
    elif mutation == 'empty': p['periods'] = []
    with pytest.raises(ValueError): project(p)


def linked_packet():
    from tests.valuation.test_company_model import packet
    p = packet(); s = p['scenarios'][0]; ledger = case()
    ledger.update(date=s['opening_date'], basis=s['basis'], source_refs=s['source_refs'], known_at=s['known_at'],
                  annual_effective_rate=.03, payment_buckets=[], periods=[])
    for q in s['quarters']:
        ledger['payment_buckets'].append({'id': q['id'], 'start': q['start'], 'end': q['end'],
                                         'total_cash_payment': 2, 'basis': 'synthetic quarter cash', 'source_refs': ['synthetic']})
        ledger['periods'].append({'start': q['start'], 'end': q['end'], 'payment_bucket_id': q['id'],
                                  'net_nonoperating_excluding_this_lease_interest': -1,
                                  'nonoperating_allocation_basis': 'synthetic one excluding lease interest',
                                  'basis': 'synthetic', 'source_refs': ['synthetic']})
    rows = project(ledger)['periods']
    for q, row in zip(s['quarters'], rows):
        q['income'].pop('interest_income'); q['income'].pop('interest_expense'); q['income'].pop('other_nonoperating')
        q['income'].update(nonoperating_basis='combined_net', nonoperating_definition='synthetic net with explicit lease interest',
                          net_nonoperating=row['net_nonoperating'])
        q['cash']['lease_principal'] = row['principal']
    s['finance_lease_runoff'] = ledger
    return p


def test_host_binds_all_principal_and_interest_periods_without_double_debt_or_capital():
    from tests.valuation.test_company_model import STAMP
    from quant.valuation.company_model import build
    p = linked_packet(); r = build(p, as_of=STAMP)['scenarios']['base']
    assert r['quarters'][-1]['cash']['ending_debt'] == 44
    assert r['quarters'][0]['cash']['total_capex'] == 12
    last = r['finance_lease_runoff']['periods'][-1]
    assert r['quarters'][-1]['cash']['ending_opening_population_finance_lease_liability'] == last['closing_liability']
    assert last['closing_liability'] < 100


def test_combined_asset_and_lease_ledgers_preserve_distinct_row_bindings():
    from tests.valuation.test_company_amortization import finite_only_case
    from tests.valuation.test_company_model import STAMP
    from quant.valuation.company_model import build
    p = linked_packet(); s = p['scenarios'][0]
    ledger = finite_only_case()
    ledger.update(date=s['opening_date'], known_at=s['known_at'], source_refs=s['source_refs'], basis=s['basis'], periods=[])
    for q in s['quarters']:
        ledger['periods'].append({'start': q['start'], 'end': q['end'], 'basis': 'synthetic', 'source_refs': ['synthetic'],
            'existing_amortization': 1, 'other_da': 4, 'operating_profit_before_acquired_amortization': 14,
            'cash_new_assets': 10, 'new_financed_technology_licenses': 0, 'technology_license_principal_paid': 2})
    s['acquired_intangible_runoff'] = ledger
    r = build(p, as_of=STAMP)['scenarios']['base']
    for q, asset, lease in zip(r['quarters'], r['acquired_intangible_runoff']['periods'], r['finance_lease_runoff']['periods']):
        assert q['operating_profit'] == asset['operating_profit'] == 13
        assert q['cash']['ending_opening_population_finance_lease_liability'] == lease['closing_liability']
    # Evidence for excluded assets is checked independently from included periods.
    ledger['excluded_nonamortizing_assets'][0]['source_refs'] = ['absent']
    with pytest.raises(KeyError): build(p, as_of=STAMP)


@pytest.mark.parametrize('mutation', ['principal', 'income', 'period', 'opening', 'future_source'])
def test_host_rejects_unlinked_or_unavailable_lease_evidence(mutation):
    from tests.valuation.test_company_model import STAMP
    from quant.valuation.company_model import build
    p = linked_packet(); s = p['scenarios'][0]
    if mutation == 'principal': s['quarters'][0]['cash']['lease_principal'] += 1
    elif mutation == 'income': s['quarters'][0]['income']['net_nonoperating'] += 1
    elif mutation == 'period': s['finance_lease_runoff']['periods'].pop()
    elif mutation == 'opening': s['finance_lease_runoff']['date'] = '2026-09-07'
    elif mutation == 'future_source': s['finance_lease_runoff']['known_at'] = '2026-09-09T00:00:00+00:00'
    with pytest.raises(ValueError): build(p, as_of=STAMP)
