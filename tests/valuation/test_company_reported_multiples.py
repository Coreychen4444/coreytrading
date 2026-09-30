"""Synthetic provenance/math checks, not actual forecast accuracy evidence."""
from copy import deepcopy
from datetime import date, timedelta

import pytest

from quant.core.market_sessions import session_close
from quant.valuation.company_multiples import calculate, calculate_reported_annual_panel

AS_OF = '2026-09-26T19:00:00Z'
CAPTURE = '2026-09-26T18:00:00Z'


def inputs(n=8):
    years = list(range(2010, 2010 + n))
    sources = {'epoch': {'url': 'https://example.test/accounting', 'sha256': 'c' * 64,
                         'available_at': CAPTURE, 'captured_at': CAPTURE}}
    rows = []
    for i, year in enumerate(years):
        day = date(year + 1, 1, 1)
        close = next(session_close(day + timedelta(days=d)) for d in range(10)
                     if session_close(day + timedelta(days=d)) is not None).isoformat()
        known = f'{year}-12-15T21:10:00+00:00'
        sources[f'p{i}'] = {'url': 'opend://synthetic/result', 'sha256': 'a' * 64,
                            'available_at': close, 'captured_at': CAPTURE, 'kind': 'serialized_opend_result'}
        sources[f'e{i}'] = {'url': 'https://example.test/original', 'sha256': 'b' * 64,
                            'available_at': known, 'captured_at': CAPTURE, 'kind': 'raw_primary_filing'}
        rows.append({'id': str(i), 'ticker': 'SYN', 'fiscal_year': year,
                     'comparability_basis': 'synthetic constant operations within this epoch',
                     'share_normalization_basis': 'synthetic no split in selected scope',
                     'price': {'value': (i + 1) * 10, 'currency': 'USD', 'share_basis': 'common',
                               'adjustment': 'none', 'session': 'RTH', 'as_of': close,
                               'captured_at': CAPTURE, 'source_ref': f'p{i}'},
                     'eps': {'value': 2, 'currency': 'USD', 'share_basis': 'common',
                             'available_at': known, 'captured_at': CAPTURE, 'source_ref': f'e{i}',
                             'kind': 'reported_annual', 'value_origin': 'original_reported_financial_statement',
                             'vintage_basis': 'synthetic original annual filing, not a forecast',
                             'period_start': f'{year - 1}-11-01', 'period_end': f'{year}-10-31',
                             'earnings_definition': 'GAAP_original_reported', 'accounting_epoch': 'stable',
                             'original_filing': {'form': '10-K', 'accession_number': f'0000000001-{year % 100:02d}-000001',
                                                 'accepted_at': known, 'report_date': f'{year}-10-31'}}})
    panel = {'selection': {'registered_at': '2026-09-26T18:30:00Z', 'basis': 'synthetic consecutive-year selection',
                            'fiscal_years': years, 'price_rule': 'first_xnys_session_of_january_after_fiscal_year',
                            'accounting_epochs': [{'id': 'stable', 'fiscal_years': years,
                                                  'basis': 'synthetic stable accounting', 'source_refs': ['epoch']}],
                            'target_accounting_epoch': 'stable'}, 'observations': rows}
    return panel, sources


def run(p, s):
    return calculate_reported_annual_panel(p, ticker='SYN', sources=s, as_of=AS_OF)


def test_exact_descriptive_ratios_never_create_forward_price_or_acceptance():
    p, s = inputs()
    before = deepcopy(p)
    p.update(low=999, high=1000, target_12m=2000)
    result = run(p, s)
    assert [x['pe'] for x in result['included']] == [5, 10, 15, 20, 25, 30, 35, 40]
    assert (result['low'], result['median'], result['high']) == (13.75, 22.5, 31.25)
    assert result['status'] == 'research_panel'
    assert result['publication'] == 'withheld' and result['validated'] is False and result['target_12m'] is None
    assert result['profit_basis'] == 'last_original_reported_fiscal_year_not_forward_or_exact_current_TTM'
    assert p['observations'] == before['observations']


def test_incompatible_accounting_epochs_cannot_make_eight_current_basis_years():
    p, s = inputs(9)
    p['selection']['accounting_epochs'] = [
        {'id': 'old', 'fiscal_years': [2010, 2011], 'basis': 'synthetic prior standard', 'source_refs': ['epoch']},
        {'id': 'current', 'fiscal_years': list(range(2012, 2019)),
         'basis': 'synthetic change independently requiring review', 'source_refs': ['epoch']}]
    p['selection']['target_accounting_epoch'] = 'current'
    for row in p['observations']:
        row['eps']['accounting_epoch'] = 'old' if row['fiscal_year'] <= 2011 else 'current'
    result = run(p, s)
    assert result['all_valid_years'] == 9 and result['sample_count'] == 7
    assert result['accounting_epoch_counts'] == {'old': 2, 'current': 7}
    assert result['low'] is None and result['median'] is None and result['high'] is None
    assert result['reason'] == 'insufficient_history_in_selected_accounting_epoch'


@pytest.mark.parametrize('change', ['zero_eps', 'negative_eps', 'currency', 'share_basis', 'no_normalization',
                                   'cash_dividend_adjustment', 'extended_session', 'price_source_kind',
                                   'source_hash', 'source_capture', 'EPS_origin', 'forward_kind',
                                   'EPS_source_kind', 'no_vintage', 'filing_form', 'filing_accession',
                                   'filing_clock', 'fiscal_period', 'accounting_epoch', 'no_comparability'])
def test_invalid_history_withholds_range_without_replacing_failed_year(change):
    p, s = inputs()
    row = p['observations'][0]
    if change == 'zero_eps': row['eps']['value'] = 0
    if change == 'negative_eps': row['eps']['value'] = -1
    if change == 'currency': row['price']['currency'] = 'EUR'
    if change == 'share_basis': row['price']['share_basis'] = 'different_split'
    if change == 'no_normalization': del row['share_normalization_basis']
    if change == 'cash_dividend_adjustment': row['price']['adjustment'] = 'QFQ'
    if change == 'extended_session': row['price']['session'] = 'ALL'
    if change == 'price_source_kind': s['p0']['kind'] = 'web_excerpt'
    if change == 'source_hash': s['e0']['sha256'] = 'invalid'
    if change == 'source_capture': s['e0']['captured_at'] = '2026-09-27T00:00:00Z'
    if change == 'EPS_origin': row['eps']['value_origin'] = 'price_divided_by_PE'
    if change == 'forward_kind': row['eps']['kind'] = 'analyst_forecast'
    if change == 'EPS_source_kind': s['e0']['kind'] = 'latest_revised_companyfacts'
    if change == 'no_vintage': del row['eps']['vintage_basis']
    if change == 'filing_form': row['eps']['original_filing']['form'] = '10-K/A'
    if change == 'filing_accession': row['eps']['original_filing']['accession_number'] = 'unknown'
    if change == 'filing_clock': row['eps']['original_filing']['accepted_at'] = '2011-01-05T21:00:00Z'
    if change == 'fiscal_period': row['eps']['period_start'] = '2010-01-01'
    if change == 'accounting_epoch': row['eps']['accounting_epoch'] = 'undeclared'
    if change == 'no_comparability': del row['comparability_basis']
    result = run(p, s)
    assert result['status'] == 'unknown' and result['low'] is None
    assert result['excluded'] and result['missing_fiscal_years'] == [2010]


def test_later_restatement_known_after_price_cannot_backfill_the_original():
    p, s = inputs()
    row = p['observations'][0]
    row['eps']['available_at'] = '2012-12-15T21:10:00Z'
    row['eps']['original_filing']['accepted_at'] = row['eps']['available_at']
    result = run(p, s)
    assert result['missing_fiscal_years'] == [2010] and result['status'] == 'unknown'
    assert result['excluded']


def test_same_day_with_wrong_timestamp_and_favorable_next_day_fail_fixed_calendar():
    p, s = inputs()
    row = p['observations'][0]
    row['price']['as_of'] = '2011-01-03T20:59:00Z'
    s['p0']['available_at'] = row['price']['as_of']
    result = run(p, s)
    assert 'fixed January' in result['excluded'][0]['reason']
    row['price']['as_of'] = '2011-01-04T21:00:00Z'
    s['p0']['available_at'] = row['price']['as_of']
    assert 'fixed January' in run(p, s)['excluded'][0]['reason']


def test_duplicate_year_and_missing_selected_year_cannot_change_weights():
    p, s = inputs()
    duplicate = deepcopy(p['observations'][0])
    duplicate['id'], duplicate['price']['value'] = 'extra', 100000
    p['observations'].append(duplicate)
    result = run(p, s)
    assert result['all_valid_years'] == 8 and result['low'] is None
    assert 'duplicate fiscal' in result['excluded'][0]['reason']
    p['observations'] = p['observations'][1:-1]
    assert run(p, s)['missing_fiscal_years'] == [2010]


def test_gaap_and_adjusted_cannot_share_one_epoch_label():
    p, s = inputs()
    p['observations'][0]['eps']['earnings_definition'] = 'adjusted'
    result = run(p, s)
    assert result['all_valid_years'] == 8 and result['low'] is None
    assert result['reason'] == 'earnings_definitions_differ_within_target_epoch'


@pytest.mark.parametrize('change', ['gap_year', 'epoch_gap', 'epoch_overlap', 'source_missing', 'target_missing', 'future_registration'])
def test_bad_selection_or_partition_fails_closed(change):
    p, s = inputs()
    if change == 'gap_year': p['selection']['fiscal_years'].remove(2012)
    if change == 'epoch_gap': p['selection']['accounting_epochs'][0]['fiscal_years'] = list(range(2011, 2018))
    if change == 'epoch_overlap': p['selection']['accounting_epochs'][0]['fiscal_years'].append(2010)
    if change == 'source_missing': p['selection']['accounting_epochs'][0]['source_refs'] = ['missing']
    if change == 'target_missing': p['selection']['target_accounting_epoch'] = 'undeclared'
    if change == 'future_registration': p['selection']['registered_at'] = '2026-09-27T00:00:00Z'
    with pytest.raises(ValueError):
        run(p, s)


def test_reported_annual_method_cannot_route_through_forward_multiple_api():
    p, s = inputs()
    with pytest.raises(ValueError, match='forward PE panel method'):
        calculate(p, method='historical_original_reported_annual_pe', ticker='SYN',
                  earnings_definition='GAAP_original_reported', sources=s, as_of=AS_OF)
