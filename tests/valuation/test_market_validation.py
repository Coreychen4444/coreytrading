from copy import deepcopy

import pytest

from quant.core.agent_contracts import canonical_hash
from quant.valuation.market_validation import evaluate, target_close


def inputs():
    s={'registered_at':'2026-09-01T00:00:00Z','tickers':['SYN'],'model_version':'synthetic',
       'target_convention':'first_xnys_close_on_or_after_twelve_calendar_months',
       'price_basis':'split_adjusted_price_on_forecast_share_basis_excluding_dividends',
       'baseline_interval_definition':'synthetic fixed 20% band around unchanged price','alpha':.2,
       'baseline_kind':'constant_price_fixed_fraction','baseline_halfwidth_fraction':.2}
    f={'forecast_id':'1','generated_at':'2026-09-08T22:00:00Z','ticker':'SYN','model_version':'synthetic',
       'spec_hash':canonical_hash(s),'target_close':'2027-09-08T20:00:00Z','price_basis':s['price_basis'],
       'input_hash':'a'*64,'receipt_hash':'b'*64,'share_basis_id':'shares_at_forecast',
       'status':'forecast','interval_kind':'predictive','alpha':.2,'baseline_interval_definition':s['baseline_interval_definition'],
       'anchor_price':100,'anchor_as_of':'2026-09-08T20:00:00Z','anchor_captured_at':'2026-09-08T21:00:00Z',
       'anchor_source_hash':'c'*64,'low':90,'point':110,'high':130,'baseline_low':80,'baseline_high':120}
    a={'ticker':'SYN','target_close':f['target_close'],'price_basis':s['price_basis'],'share_basis_id':f['share_basis_id'],
       'available_at':'2027-09-08T21:00:00Z','captured_at':'2027-09-08T22:00:00Z','source_hash':'d'*64,'price':140}
    return s,f,a


def test_independent_hand_price_error_and_interval_score():
    s,f,a=inputs();r=evaluate(s,[f],[a],as_of='2027-09-09T00:00:00Z')
    assert r['sample_count']==1 and r['normalized_absolute_error']==.3
    assert r['flat_price_baseline_error']==.4 and r['coverage']==0 and r['normalized_width']==.4
    assert r['interval_score']==1.4 and r['baseline_interval_score']==2.4
    assert r['publication']=='withheld' and r['acceptance']=='not_determined'


def test_unknown_future_actual_is_pending_not_a_hit():
    s,f,a=inputs();r=evaluate(s,[f],[a],as_of='2027-09-08T19:00:00Z')
    assert r['pending']==['1'] and r['sample_count']==0 and r['coverage'] is None


def test_one_year_target_on_holiday_uses_next_exchange_close():
    assert target_close('2025-09-07T22:00:00Z').isoformat()=='2026-09-08T20:00:00+00:00'


def test_scenario_envelope_does_not_become_a_probability_band():
    s,f,a=inputs();f['interval_kind']='operating_scenarios'
    with pytest.raises(ValueError,match='scenario extrema'):evaluate(s,[f],[a],as_of='2027-09-09T00:00:00Z')


def test_changed_protocol_and_overlapping_predictions_are_excluded():
    s,f,a=inputs();later={**f,'forecast_id':'2','generated_at':'2026-09-09T22:00:00Z','target_close':'2027-09-09T20:00:00Z'}
    r=evaluate(s,[f,later],[a],as_of='2027-09-10T00:00:00Z')
    assert r['sample_count']==1 and r['excluded'][0]['reason']=='overlapping_outcome_window'
    f['spec_hash']='x'*64
    assert evaluate(s,[f],[a],as_of='2027-09-09T00:00:00Z')['sample_count']==0


def test_very_wide_interval_does_not_win_on_coverage_alone():
    s,f,a=inputs();f.update(low=1,high=1000)
    r=evaluate(s,[f],[a],as_of='2027-09-09T00:00:00Z')
    assert r['coverage']==1 and r['interval_score']>r['baseline_interval_score']


def test_abstention_remains_in_coverage_denominator():
    s,f,a=inputs();f['status']='abstained'
    r=evaluate(s,[f],[a],as_of='2027-09-09T00:00:00Z')
    assert r['abstained']==['1'] and r['abstention_rate']==1 and r['sample_count']==0


def test_revised_actuals_and_future_anchor_capture_are_rejected():
    s,f,a=inputs()
    with pytest.raises(ValueError,match='vintage selection'):evaluate(s,[f],[a,a],as_of='2027-09-09T00:00:00Z')
    f['anchor_captured_at']='2027-01-01T00:00:00Z'
    with pytest.raises(ValueError,match='dated anchor'):evaluate(s,[f],[a],as_of='2027-09-09T00:00:00Z')


def test_bankruptcy_zero_outcome_is_scored_not_dropped():
    s,f,a=inputs();a['price']=0
    r=evaluate(s,[f],[a],as_of='2027-09-09T00:00:00Z')
    assert r['sample_count']==1 and r['normalized_absolute_error']==1.1


def test_cannot_change_baseline_width_or_interval_level_after_outcomes():
    s,f,a=inputs();f['baseline_high']=10000
    with pytest.raises(ValueError,match='frozen baseline formula'):evaluate(s,[f],[a],as_of='2027-09-09T00:00:00Z')
    f['alpha']=.1
    with pytest.raises(ValueError,match='nominal interval level'):evaluate(s,[f],[a],as_of='2027-09-09T00:00:00Z')
