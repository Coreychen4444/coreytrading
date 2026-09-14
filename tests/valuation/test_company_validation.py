from copy import deepcopy

import pytest

from quant.valuation.company_validation import evaluate
from quant.core.agent_contracts import canonical_hash


def inputs():
    spec = {'registered_at': '2026-09-01T00:00:00Z', 'model_version': 'company-v5.0-research', 'tickers': ['SYN'],
            'scenario': 'base', 'earnings_definition': 'GAAP', 'baseline_definition': 'frozen_flat_eps',
            'minimum_nonoverlapping_samples': 20, 'baseline_eps': 1}
    f = {'forecast_id': '1', 'generated_at': '2026-09-08T00:00:00Z', 'model_version': spec['model_version'],
         'scenario': 'base', 'earnings_definition': 'GAAP', 'baseline_definition': 'frozen_flat_eps',
         'ticker': 'SYN', 'period_start': '2027-01-01', 'period_end': '2027-12-31',
         'eps': 2, 'baseline_eps': 1, 'input_hash': 'a' * 64, 'receipt_hash': 'b' * 64,
         'spec_hash': canonical_hash(spec)}
    a = {'ticker': 'SYN', 'period_start': '2027-01-01', 'period_end': '2027-12-31',
         'earnings_definition': 'GAAP', 'eps': 1.5, 'available_at': '2028-02-01T00:00:00Z',
         'captured_at': '2028-02-02T00:00:00Z', 'source_hash': 'c' * 64}
    return spec, f, a


def test_mature_samples_and_simple_baseline_are_separate_from_publication():
    s, f, a = inputs()
    r = evaluate(s, [f], [a], as_of='2028-02-03T00:00:00Z')
    assert r['sample_count'] == 1 and r['mae'] == .5 and r['baseline_mae'] == .5 and r['bias'] == .5
    assert r['status'] == 'insufficient_samples' and r['publication'] == 'withheld'


def test_future_and_unknown_outcomes_remain_pending():
    s, f, a = inputs()
    r = evaluate(s, [f], [a], as_of='2027-12-31T00:00:00Z')
    assert r['sample_count'] == 0 and r['mae'] is None and r['pending'] == ['1']


@pytest.mark.parametrize('change', ['after_period', 'before_registration', 'baseline', 'accounting', 'scenario'])
def test_hindsight_scope_and_basis_mismatches_are_not_scored(change):
    s, f, a = inputs()
    if change == 'after_period': f['generated_at'] = '2028-01-01T00:00:00Z'
    if change == 'before_registration': f['generated_at'] = '2026-08-01T00:00:00Z'
    if change == 'baseline': f['baseline_definition'] = 'changed_after_outcome'
    if change == 'accounting': f['earnings_definition'] = 'adjusted'
    if change == 'scenario': f['scenario'] = 'selected_winner'
    r = evaluate(s, [f], [a], as_of='2028-02-03T00:00:00Z')
    assert r['sample_count'] == 0 and len(r['excluded']) == 1


def test_overlapping_samples_are_not_independent_observations():
    s, f, a = inputs()
    second = {**f, 'forecast_id': '2', 'period_start': '2027-04-01', 'period_end': '2028-03-31'}
    r = evaluate(s, [f, second], [a], as_of='2028-04-03T00:00:00Z')
    assert r['sample_count'] == 1
    assert r['excluded'] == [{'forecast_id': '2', 'reason': 'overlapping_outcome'}]


def test_revised_actuals_require_explicit_selection():
    s, f, a = inputs()
    with pytest.raises(ValueError, match='vintage selection'):
        evaluate(s, [f], [a, {**a, 'eps': 2}], as_of='2028-02-03T00:00:00Z')


@pytest.mark.parametrize('field,value', [('spec_hash', 'a' * 64), ('input_hash', 'not_a_hash'),
                                        ('receipt_hash', ''), ('baseline_eps', 99), ('ticker', 'OTHER')])
def test_forecast_must_bind_exact_spec_scope_and_baseline(field, value):
    s, f, a = inputs()
    f[field] = value
    r = evaluate(s, [f], [a], as_of='2028-02-03T00:00:00Z')
    assert r['sample_count'] == 0 and len(r['excluded']) == 1


def test_actual_cannot_be_captured_before_its_disclosure():
    s, f, a = inputs()
    a['captured_at'] = '2028-01-01T00:00:00Z'
    with pytest.raises(ValueError, match='evidence/time'):
        evaluate(s, [f], [a], as_of='2028-02-03T00:00:00Z')


def test_spec_cannot_silently_expand_to_other_companies():
    s, f, a = inputs()
    del s['tickers']
    with pytest.raises(ValueError, match='ticker scope'):
        evaluate(s, [f], [a], as_of='2028-02-03T00:00:00Z')


def test_forecasts_selected_in_real_time_order_across_timezones():
    s, f, a = inputs()
    later = {**f, 'forecast_id': 'later', 'generated_at': '2026-09-08T01:00:00+00:00', 'eps': 99}
    earlier = {**f, 'forecast_id': 'earlier', 'generated_at': '2026-09-08T08:30:00+08:00'}
    r = evaluate(s, [later, earlier], [a], as_of='2028-02-03T00:00:00Z')
    assert r['evaluated'][0]['forecast_id'] == 'earlier' and r['mae'] == .5
