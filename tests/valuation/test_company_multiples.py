from copy import deepcopy

import pytest

from quant.valuation.company_multiples import calculate

AS_OF = '2026-09-08T12:00:00Z'


def inputs():
    sources = {'price': {'url': 'https://example.test/prices', 'sha256': 'a' * 64,
                        'available_at': '2026-09-04T20:00:00Z', 'captured_at': '2026-09-08T08:00:00Z'},
               'eps': {'url': 'https://example.test/estimates', 'sha256': 'b' * 64,
                       'available_at': '2026-09-01T00:00:00Z', 'captured_at': '2026-09-02T00:00:00Z'}}
    rows = []
    for i, (ticker, price) in enumerate(zip(['A', 'B', 'C'], [20, 40, 100])):
        rows.append({'id': str(i), 'ticker': ticker, 'comparability_basis': 'synthetic comparable operations',
                     'price': {'value': price, 'currency': 'USD', 'share_basis': 'post_split_common',
                               'as_of': '2026-09-04T20:00:00Z', 'captured_at': '2026-09-08T08:00:00Z', 'source_ref': 'price'},
                     'eps': {'value': 2, 'currency': 'USD', 'share_basis': 'post_split_common',
                             'available_at': '2026-09-01T00:00:00Z', 'captured_at': '2026-09-02T00:00:00Z', 'source_ref': 'eps',
                             'kind': 'consensus_forecast', 'value_origin': 'direct_earnings_forecast',
                             'vintage_basis': 'synthetic archived estimate',
                             'period_start': '2026-09-01', 'period_end': '2027-08-31', 'earnings_definition': 'GAAP'}})
    panel = {'selection': {'registered_at': '2026-09-08T09:00:00Z', 'basis': 'synthetic fixed three-peer selection',
                            'tickers': ['A', 'B', 'C'], 'start': '2026-09-01', 'end': '2026-09-08'},
             'observations': rows}
    return panel, sources


def run(panel, sources, method='comparable_forward_pe', as_of=AS_OF):
    return calculate(panel, method=method, ticker='SYN', earnings_definition='GAAP', sources=sources, as_of=as_of)


def test_range_is_computed_from_observations_not_arbitrary_input():
    p, s = inputs()
    original = deepcopy(p)
    p['low'], p['high'] = 1000, 2000  # Extraneous top-level values never control the calculation.
    result = run(p, s)
    assert result['status'] == 'research_panel'
    assert [r['pe'] for r in result['included']] == [10, 20, 50]
    assert result['low'] == 15 and result['median'] == 20 and result['high'] == 35
    assert result['publication'] == 'withheld' and result['validated'] is False
    assert p['observations'] == original['observations']


@pytest.mark.parametrize('change', ['negative_eps', 'currency', 'shares', 'accounting', 'ttm', 'far_forward',
                                   'future_estimate', 'capture', 'source_hash', 'source_time', 'no_rationale', 'unknown_horizon', 'circular_eps'])
def test_invalid_peer_is_excluded_and_cannot_leave_publishable_range(change):
    p, s = inputs()
    row = p['observations'][0]
    if change == 'negative_eps': row['eps']['value'] = -.1
    if change == 'currency': row['eps']['currency'] = 'EUR'
    if change == 'shares': row['eps']['share_basis'] = 'pre_split'
    if change == 'accounting': row['eps']['earnings_definition'] = 'adjusted'
    if change == 'ttm': row['eps'].update(period_start='2025-09-01', period_end='2026-08-31')
    if change == 'far_forward': row['eps'].update(period_start='2027-09-01', period_end='2028-08-31')
    if change == 'future_estimate': row['eps'].update(available_at='2026-09-05T00:00:00Z', captured_at='2026-09-06T00:00:00Z')
    if change == 'capture': row['price']['captured_at'] = '2026-09-09T00:00:00Z'
    if change == 'source_hash': s['eps']['sha256'] = 'not_evidence'
    if change == 'source_time': s['eps']['available_at'] = '2026-09-03T00:00:00Z'
    if change == 'no_rationale': del row['comparability_basis']
    if change == 'unknown_horizon': del row['eps']['period_end']
    if change == 'circular_eps': row['eps']['value_origin'] = 'price_divided_by_forward_pe'
    result = run(p, s)
    assert result['status'] == 'unknown' and result['low'] is None and result['high'] is None
    assert result['excluded'] and 'A' in result['missing_tickers']


def test_missing_selected_peer_cannot_be_dropped_to_improve_range():
    p, s = inputs()
    p['observations'].pop()
    result = run(p, s)
    assert result['missing_tickers'] == ['C'] and result['status'] == 'unknown'


def test_duplicate_peer_cannot_reweight_panel():
    p, s = inputs()
    p['observations'].append({**deepcopy(p['observations'][0]), 'id': 'extra'})
    with pytest.raises(ValueError, match='duplicate comparable'):
        run(p, s)


def test_subject_cannot_supply_its_own_independent_peer_multiple():
    p, s = inputs()
    p['selection']['tickers'][0] = 'SYN'
    with pytest.raises(ValueError, match='independent'):
        run(p, s)


def test_stale_peer_price_is_not_refreshed_by_new_capture():
    p, s = inputs()
    result = run(p, s, as_of='2026-09-20T12:00:00Z')
    assert result['sample_count'] == 0 and result['status'] == 'unknown'
    assert all('older than seven' in r['reason'] for r in result['excluded'])


def historical_inputs():
    p, s = inputs()
    template = p['observations'][0]
    p['selection'].update(tickers=['SYN'], start='2010-01-01')
    p['observations'] = []
    for i in range(8):
        year = 2010 + i
        row = deepcopy(template)
        row.update(id=str(i), ticker='SYN')
        row['price'].update(value=(i + 1) * 10, as_of=f'{year}-01-04T21:00:00Z', source_ref=f'p{i}')
        row['eps'].update(available_at=f'{year}-01-02T00:00:00Z', captured_at=f'{year}-01-03T00:00:00Z',
                          period_start=f'{year}-01-01', period_end=f'{year}-12-31', source_ref=f'e{i}')
        s[f'p{i}'] = {**s['price'], 'available_at': row['price']['as_of']}
        s[f'e{i}'] = {**s['eps'], 'available_at': row['eps']['available_at'], 'captured_at': row['eps']['captured_at']}
        p['observations'].append(row)
    return p, s


def test_historical_panel_has_nonoverlapping_annual_earnings_vintages():
    p, s = historical_inputs()
    result = run(p, s, method='historical_same_basis_pe')
    assert result['sample_count'] == 8 and result['status'] == 'research_panel'
    assert result['low'] == 13.75 and result['high'] == 31.25


def test_historical_daily_quotes_cannot_count_as_independent_years():
    p, s = historical_inputs()
    row = deepcopy(p['observations'][0])
    row['id'] = 'duplicate_year'
    row['price']['as_of'] = '2010-01-05T21:00:00Z'
    p['observations'].append(row)
    result = run(p, s, method='historical_same_basis_pe')
    assert result['status'] == 'unknown' and result['sample_count'] == 8
    assert 'overlapping' in result['excluded'][0]['reason']


def test_company_engine_consumes_panel_and_rejects_arbitrary_comparable_bounds():
    from quant.valuation import company_model as cm
    from tests.valuation.test_company_model import packet
    p = packet()
    panel, sources = inputs()
    p['sources'].update(sources)
    m = p['scenarios'][0]['multiple']
    m.update(method='comparable_forward_pe', earnings_basis='gaap', panel=panel,
             comparability_basis='synthetic GAAP peers; hypothetical unchanged multiple at target')
    with pytest.raises(ValueError, match='bounds must be computed'):
        cm.build(p, as_of=AS_OF)
    del m['low'], m['high']
    result = cm.build(p, as_of=AS_OF)['scenarios']['base']['target_12m']
    assert result['pe_low'] == 15 and result['pe_high'] == 35
    assert result['eps'] == .32 and result['low'] == 4.8 and result['high'] == pytest.approx(11.2)


def test_plain_comparability_prose_does_not_establish_observed_pe():
    from quant.valuation import company_model as cm
    from tests.valuation.test_company_model import packet
    p = packet()
    m = p['scenarios'][0]['multiple']
    m.update(method='comparable_forward_pe', comparability_basis='trust this multiple')
    del m['low'], m['high']
    result = cm.build(p, as_of=AS_OF)['scenarios']['base']['target_12m']
    assert result == {'status': 'unknown', 'reason': 'independent_multiple_panel_missing'}
