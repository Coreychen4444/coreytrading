"""Host cash integration and duplicate/unknown cash safety, not forecast accuracy."""
from copy import deepcopy

import pytest

from quant.core.agent_contracts import canonical_hash
from quant.valuation.company_model import business_equity, stamp
from tests.valuation.test_company_equity import case
from tests.valuation.test_company_model import packet, STAMP


def inputs():
    p = case()
    p['known_at'] = STAMP
    p['source_refs'] = ['synthetic']
    p['basis'] = 'Synthetic conditional equity cash integration.'
    a = p['nonoperating_assets'][0]
    a.pop('value'); a['valuation_status'] = 'scenario_linked'
    a['forecast_liquidity_surplus'] = {
        'pool_basis': 'scenario_unrestricted_liquidity',
        'unrestricted_pool_basis': 'Synthetic conditional cash ownership scope, not certified.',
        'operating_reserve': 50, 'reserve_status': 'scenario_assumption',
        'reserve_basis': 'Synthetic working cash hypothesis, not issuer fact.'}
    q = [{'end': p['date'], 'closing_shares': 100, 'cash': {'ending_liquidity': 300}}]
    return p, q


def run(p, q, gap=0):
    return business_equity(p, q, sources=packet()['sources'], cutoff=stamp(STAMP), funding_gap=gap)


def test_quarter_cash_and_explicit_reserve_link_to_equity_without_mutating_inputs():
    p, q = inputs(); saved = deepcopy((p, q))
    r = run(p, q)
    assert (p, q) == saved
    assert r['known_nonoperating_assets'] == 250
    assert r['per_share'] == pytest.approx((1100 + 250 - 200) / 125)
    link = r['forecast_liquidity_surplus'][0]
    assert link['ending_liquidity'] == 300 and link['operating_reserve'] == 50 and link['value'] == 250
    assert link['source_completeness'] == 'not_established'
    assert r['publication'] == 'withheld' and r['validated'] is False
    assert r['receipt_hash'] == canonical_hash({k: v for k, v in r.items() if k != 'receipt_hash'})


@pytest.mark.parametrize('liquidity', [0, 25, -25])
def test_nonpositive_surplus_is_a_conditional_zero_and_never_fills_a_cash_gap(liquidity):
    p, q = inputs(); q[0]['cash']['ending_liquidity'] = liquidity
    r = run(p, q, gap=1)
    assert r['known_nonoperating_assets'] == 0
    assert r['forecast_liquidity_surplus'][0]['value'] == 0
    assert r['status'] == 'unknown' and r['per_share'] is None
    assert r['reason'] == 'unfunded_operating_path'
    assert q[0]['cash']['ending_liquidity'] == liquidity
    assert r['receipt_hash'] == canonical_hash({k: v for k, v in r.items() if k != 'receipt_hash'})


@pytest.mark.parametrize('reserve', [None, True, -1, float('nan'), float('inf')])
def test_missing_or_invalid_reserve_is_not_assumed_zero(reserve):
    p, q = inputs()
    p['nonoperating_assets'][0]['forecast_liquidity_surplus']['operating_reserve'] = reserve
    with pytest.raises(ValueError): run(p, q)


@pytest.mark.parametrize('field, value', [
    ('reserve_status', 'issuer_requirement'), ('pool_basis', 'all_reported_assets'),
    ('unrestricted_pool_basis', ''), ('unrestricted_pool_basis', True), ('reserve_basis', '  ')])
def test_pool_ownership_and_reserve_assumptions_must_be_explicit(field, value):
    p, q = inputs()
    p['nonoperating_assets'][0]['forecast_liquidity_surplus'][field] = value
    with pytest.raises(ValueError): run(p, q)


@pytest.mark.parametrize('other_linked', [True, False])
def test_one_total_liquidity_link_cannot_be_added_to_a_second_cash_pool(other_linked):
    p, q = inputs(); a = deepcopy(p['nonoperating_assets'][0]); a['economic_id'] = 'cash_again'
    if not other_linked:
        a.pop('forecast_liquidity_surplus'); a.update(valuation_status='valued', value=100)
    p['required_asset_ids'].append(a['economic_id']); p['nonoperating_assets'].append(a)
    with pytest.raises(ValueError, match='another excess cash'): run(p, q)


@pytest.mark.parametrize('bad', ['supplied_value', 'wrong_asset', 'wrong_status', 'missing_liquidity', 'wrong_date'])
def test_unlinked_or_ambiguous_asset_cannot_supply_an_excess_cash_value(bad):
    p, q = inputs(); a = p['nonoperating_assets'][0]
    if bad == 'supplied_value': a['value'] = 999
    if bad == 'wrong_asset': a['basis_type'] = 'nonoperating_equity_value'
    if bad == 'wrong_status': a['valuation_status'] = 'valued'
    if bad == 'missing_liquidity': q[0]['cash']['ending_liquidity'] = None
    if bad == 'wrong_date': q[0]['end'] = '2027-09-29'
    with pytest.raises(ValueError): run(p, q)


def test_unknown_claims_remain_unknown_even_with_a_linked_cash_value():
    p, q = inputs(); p['claims'][1].update(settlement='unresolved')
    p['claims'][1].pop('new_shares'); p['claims'][1].pop('cash_proceeds')
    r = run(p, q)
    assert r['known_nonoperating_assets'] == 250
    assert r['status'] == 'unknown' and r['per_share'] is None and r['unresolved_claims'] == ['escrow']


def unresolved_inputs():
    p, q = inputs()
    p.update(use_consolidated_dcf=True, allow_unresolved_operating_inventory=True,
             required_business_ids=['consolidated'], businesses=[])
    return p, q


def test_opted_in_unknown_operating_inventory_binds_cash_and_shares_without_value():
    p, q = unresolved_inputs(); saved = deepcopy(p)
    r = business_equity(p, q, sources=packet()['sources'], cutoff=stamp(STAMP), funding_gap=0,
                        operating={'status': 'unknown', 'reason': 'capital source not reconciled'})
    assert p == saved
    assert r['unresolved_businesses'] == ['consolidated'] and r['operating_value'] is None
    assert r['per_share'] is None and r['status'] == 'unknown'
    assert r['known_nonoperating_assets'] == 250
    assert r['forecast_liquidity_surplus'][0]['value'] == 250
    assert r['publication'] == 'withheld' and r['validated'] is False
    assert r['receipt_hash'] == canonical_hash({k: v for k, v in r.items() if k != 'receipt_hash'})


@pytest.mark.parametrize('bad', ['no_opt_in', 'bad_flag', 'no_host_binding', 'prevalued_business',
                                 'no_operating_reason', 'wrong_shares', 'future_evidence', 'missing_claim'])
def test_held_operating_value_does_not_bypass_inventory_validation(bad):
    p, q = unresolved_inputs(); op = {'status': 'unknown', 'reason': 'synthetic held'}
    if bad == 'no_opt_in': p.pop('allow_unresolved_operating_inventory')
    if bad == 'bad_flag': p['allow_unresolved_operating_inventory'] = 'true'
    if bad == 'no_host_binding': p['use_consolidated_dcf'] = False
    if bad == 'prevalued_business': p['businesses'] = [{'economic_id': 'consolidated', 'value': 1000}]
    if bad == 'no_operating_reason': op.pop('reason')
    if bad == 'wrong_shares': p['outstanding_shares'] += 1
    if bad == 'future_evidence': p['known_at'] = '2026-10-01T00:00:00+00:00'
    if bad == 'missing_claim': p['claims'].pop()
    with pytest.raises(ValueError):
        business_equity(p, q, sources=packet()['sources'], cutoff=stamp(STAMP), funding_gap=0, operating=op)


def test_held_full_host_inventory_reaches_worker_and_report_without_eps_or_cash_changes():
    from quant.valuation.assessment import build
    from quant.ops.agent_workflow import _valuation_worker_receipt
    from quant.report.valuation_report import fyi_map
    package = packet(); equity, _ = unresolved_inputs()
    package['operating_path_hold'] = {**{k: package[k] for k in ['known_at', 'source_refs', 'basis']},
                                      'status': 'unestimable', 'reason': 'synthetic held capital'}
    before = build({'selected_tickers': ['SYN'], 'valuation': {'company_models': {'SYN': package}}}, as_of=STAMP)
    when = package['scenarios'][0]['quarters'][0]['end']; equity['date'] = when
    for row in equity['nonoperating_assets'] + equity['claims']: row['date'] = when
    equity['claims'][0].pop('cash'); equity['claims'][0]['settlement'] = 'unresolved'
    a = deepcopy(equity['nonoperating_assets'][0]); a.pop('forecast_liquidity_surplus')
    a.update(economic_id='restricted_rights', basis_type='nonoperating_financial_asset_value', valuation_status='unresolved')
    equity['nonoperating_assets'].append(a); equity['required_asset_ids'].append(a['economic_id'])
    package['scenarios'][0]['business_equity'] = equity
    r = build({'selected_tickers': ['SYN'], 'valuation': {'company_models': {'SYN': package}}}, as_of=STAMP)
    new = r['tickers']['SYN']['company_research']['scenarios']['base']
    prev = before['tickers']['SYN']['company_research']['scenarios']['base']
    assert {k:v for k,v in new.items() if k != 'business_equity'} == {k:v for k,v in prev.items() if k != 'business_equity'}
    full = new['business_equity']
    assert full['operating_value'] is None and full['per_share'] is None
    assert full['unresolved_businesses'] == ['consolidated']
    assert full['unresolved_assets'] == ['restricted_rights'] and full['unresolved_claims'] == ['debt']
    compact = _valuation_worker_receipt(r)['tickers']['SYN']['company_research']['scenarios']['base']['business_equity']
    assert compact['unresolved_businesses'] == full['unresolved_businesses']
    assert compact['known_operating_value'] == full['known_operating_value']
    assert compact['unresolved_assets'] == full['unresolved_assets']
    assert compact['unresolved_claims'] == full['unresolved_claims']
    assert compact['forecast_liquidity_surplus'] == full['forecast_liquidity_surplus']
    assert compact['per_share'] is None and compact['status'] == 'unknown'
    line = fyi_map(r)['SYN']
    assert '1项资产' in line and '1项索偿' in line
    assert '基准DCF $' not in line and '12个月条件价 $' not in line


def test_full_receipt_worker_and_report_keep_cash_assumptions_without_publishing_a_price():
    from quant.valuation.assessment import build
    from quant.ops.agent_workflow import _valuation_worker_receipt
    from quant.report.valuation_report import fyi_map
    package = packet(); equity, _ = inputs()
    when = package['scenarios'][0]['quarters'][0]['end']
    equity['date'] = when
    for row in equity['businesses'] + equity['nonoperating_assets'] + equity['claims']:
        row['date'] = when
    package['scenarios'][0]['business_equity'] = equity
    r = build({'selected_tickers': ['SYN'], 'valuation': {'company_models': {'SYN': package}}}, as_of=STAMP)
    full = r['tickers']['SYN']['company_research']['scenarios']['base']['business_equity']
    view = _valuation_worker_receipt(r)
    compact = view['tickers']['SYN']['company_research']['scenarios']['base']['business_equity']
    assert compact['forecast_liquidity_surplus'] == full['forecast_liquidity_surplus']
    line = fyi_map(r)['SYN']
    assert '减营运储备假设 $0.05 十亿美元' in line
    assert '资金归属与储备充分性未验收' in line
    assert '基准DCF $' not in line and '12个月条件价 $' not in line
