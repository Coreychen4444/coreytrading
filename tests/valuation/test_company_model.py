"""Independent hand-calculated statements; no provider/real account access."""
from copy import deepcopy
from datetime import date, timedelta

import pytest

from quant.valuation import company_model as cm

STAMP = "2026-09-08T08:00:00+00:00"


def packet():
    evidence = {"known_at": "2026-09-08T07:00:00+00:00", "source_refs": ["synthetic"], "basis": "synthetic only"}
    segments = {"products": {"revenue": 100, "operating_profit": 20},
                "foundry": {"revenue": 50, "operating_profit": -5},
                "eliminations": {"revenue": -20, "operating_profit": 0},
                "corporate": {"revenue": 0, "operating_profit": -2}}
    income = {"adjusted_definition": "synthetic_after_tax_explicit", "interest_income": 1, "interest_expense": 2, "other_nonoperating": {"other": -1},
              "tax_expense": 2, "noncontrolling_income": 1, "parent_after_tax_adjustments": {"explicit": 2}}
    shares = {"basic_weighted": 100, "incremental_dilutive": 0, "closing": 100, "issued": 0, "repurchased": 0}
    cash = {"depreciation_amortization": 5, "capex_investing": 10, "capex_financing": 2,
            "capital_grants": 1, "partner_contributions": 3, "partner_distributions": 2,
            "debt_issued": 5, "debt_repaid": 2, "debt_non_cash_change": 0, "equity_cash_raised": 0, "lease_principal": 1,
            "common_dividends": 1, "common_buybacks": 0, "minimum_liquidity": 5,
            "capitalized_interest_cash": 0,
            "noncash_adjustments": {"sbc": 1}, "working_capital_cash_change": -2, "other_cash_flows": {}}
    quarters = []
    start = date(2026, 9, 9)
    for i in range(8):
        end = start + timedelta(days=90)
        quarters.append({"id": str(i), "start": str(start), "end": str(end),
                         "segments": deepcopy(segments), "income": deepcopy(income),
                         "shares": deepcopy(shares), "cash": deepcopy(cash)})
        start = end + timedelta(days=1)
    multiple = {**evidence, "method": "analyst_sensitivity", "earnings_basis": "adjusted", "target_date": "2027-09-08",
                "adjusted_definition": "synthetic_after_tax_explicit",
                "eps_quarters": ["4", "5", "6", "7"], "low": 10, "high": 20}
    return {**evidence, "schema_version": "1.0", "currency": "USD", "scale": "millions", "ticker": "SYN",
            "opening_balance": {**evidence, "date": "2026-09-08", "cash_and_equivalents": 80,
                "liquid_investments": 20, "restricted_cash_outside_pool": 7,
                "current_debt": 5, "noncurrent_debt": 15, "closing_common_shares": 100,
                "debt_basis": "synthetic carrying value, not a complete senior-claims bridge",
                "reported": {"opening_liquidity": 100, "opening_debt": 20, "opening_shares": 100,
                             "cash_including_restricted": 87}},
            "valid_until": "2026-10-01T00:00:00+00:00", "segment_names": list(segments),
            "sources": {"synthetic": {"url": "https://example.test/synthetic", "sha256": "a" * 64,
                "available_at": "2026-07-01T00:00:00+00:00", "captured_at": "2026-09-01T00:00:00+00:00"}},
            "observed": {**evidence, "end": "2026-06-27", "segments": segments, "income": income, "shares": shares,
                "reported": {"revenue": 130, "operating_profit": 13, "pretax": 11, "parent_income": 8, "gaap_eps": .08}},
            "scenarios": [{**evidence, "id": "base", "opening_date": "2026-09-08", "opening_liquidity": 100, "opening_debt": 20,
                           "opening_shares": 100, "quarters": quarters, "multiple": multiple}]}


def result(p=None):
    return cm.build(p or packet(), as_of=STAMP)


def tax_equivalence_packet():
    p = packet()
    for q in p['scenarios'][0]['quarters']:
        q['cash']['income_cash_timing'] = {
            'basis': 'synthetic scenario only; current/deferred tax and payments unknown',
            'tax_cash_basis': 'modeled_provision_cash_equivalence',
            'tax_cash_paid_net': 2,
            'other_entries_exclude_selected_timing_items': True}
    return p


def test_explicit_tax_equivalence_preserves_cash_and_remains_a_scenario():
    old = result(); new = result(tax_equivalence_packet())
    old_case = old['scenarios']['base']; case = new['scenarios']['base']
    for a, b in zip(old_case['quarters'], case['quarters']):
        assert b['gaap_eps'] == a['gaap_eps'] == .08
        assert b['cash']['operating_cash_flow'] == a['cash']['operating_cash_flow'] == 13
        assert b['cash']['ending_liquidity'] == a['cash']['ending_liquidity']
        assert b['cash']['income_cash_timing']['components'] == {'tax': 0}
        assert b['cash']['income_cash_timing']['tax_payment_status'] == 'scenario_assumption_not_verified_payment'
    taxes = case['native_cash_tax_basis']
    assert taxes['selected_period_count'] == taxes['native_period_count'] == 8
    assert taxes['tax_provision'] == taxes['net_cash_tax'] == 16
    assert taxes['timing_adjustment'] == 0 and taxes['validated'] is False
    assert taxes['publication'] == 'withheld' and len(taxes['periods']) == 8


@pytest.mark.parametrize('change', [
    {'tax_cash_paid_net': 3}, {'tax_cash_basis': 'reported_cash_payment'},
    {'tax_cash_basis': None}, {'tax_cash_paid_net': True}])
def test_tax_equivalence_cannot_assert_unmatched_or_verified_payment(change):
    p = tax_equivalence_packet()
    p['scenarios'][0]['quarters'][0]['cash']['income_cash_timing'].update(change)
    with pytest.raises(ValueError):
        result(p)


def test_selected_tax_equivalence_does_not_claim_unmodeled_quarters():
    p = tax_equivalence_packet()
    for q in p['scenarios'][0]['quarters'][1:]:
        del q['cash']['income_cash_timing']
    taxes = result(p)['scenarios']['base']['native_cash_tax_basis']
    assert taxes['native_period_count'] == 8 and taxes['selected_period_count'] == 1
    assert taxes['tax_provision'] == taxes['net_cash_tax'] == 2


def test_after_tax_equity_and_discontinued_items_preserve_pretax_and_cash_bridge():
    p = packet()
    q = p['scenarios'][0]['quarters'][0]
    q['income']['after_tax_items'] = {'basis': 'synthetic source presentation',
        'excluded_from_pretax_and_tax': True, 'equity_income': 6, 'discontinued_income': -8}
    r = cm.earnings(q['segments'], q['income'], q['shares'], p['segment_names'])
    assert r['pretax'] == 11
    assert r['continuing_income'] == 15
    assert r['consolidated_income'] == 7 and r['parent_income'] == 6
    # Cash starts with total consolidated income. Remove noncash equity income
    # and discontinued loss exactly once when constructing continuing cash flow.
    q['cash']['noncash_adjustments'].update(equity_income_reversal=-6, discontinued_loss_reversal=8)
    assert cm.liquidity(r, q['cash'], 100, 20)['operating_cash_flow'] == 13
    assert r['gaap_eps'] == .06


def test_preferred_dividend_reduces_common_income_not_consolidated_cash():
    p = packet(); q = p['scenarios'][0]['quarters'][0]
    q['income'].update(preferred_dividends=3, preferred_dividend_basis='synthetic declared cumulative allocation')
    r = cm.earnings(q['segments'], q['income'], q['shares'], p['segment_names'])
    assert r['consolidated_income'] == 9 and r['parent_income_before_preferred'] == 8
    assert r['parent_income'] == 5 and r['gaap_eps'] == .05
    assert cm.liquidity(r, q['cash'], 100, 20)['operating_cash_flow'] == 13
    del q['income']['preferred_dividend_basis']
    with pytest.raises(ValueError, match='preferred dividend'):
        cm.earnings(q['segments'], q['income'], q['shares'], p['segment_names'])


@pytest.mark.parametrize('shares,expected', [(20,.05),(100,.04)])
def test_preferred_if_converted_adds_back_dividend_only_when_dilutive(shares,expected):
    p=packet(); q=p['scenarios'][0]['quarters'][0]
    q['income'].update(preferred_dividends=3,preferred_dividend_basis='synthetic')
    q['shares']['preferred_if_converted']={'basis':'synthetic separate from ordinary dilution','shares':shares,'dividend_addback':3}
    r=cm.earnings(q['segments'],q['income'],q['shares'],p['segment_names'])
    assert r['gaap_eps']==pytest.approx(expected)
    assert r['parent_income']==5 and r['consolidated_income']==9


def test_preferred_conversion_annual_numerator_and_denominator():
    p=packet()
    for q in p['scenarios'][0]['quarters']:
        q['income'].update(preferred_dividends=3,preferred_dividend_basis='synthetic')
        q['shares']['preferred_if_converted']={'basis':'synthetic','shares':100,'dividend_addback':3}
    r=result(p)['scenarios']['base']['quarters']
    w=cm.earnings_window(r,['0','1','2','3'],'gaap')
    assert w['parent_income']==20 and w['eps_income']==32
    assert w['weighted_diluted_shares']==200 and w['eps']==.16


def test_preferred_conversion_cannot_add_back_more_than_dividend():
    p=packet();q=p['scenarios'][0]['quarters'][0]
    q['income'].update(preferred_dividends=3,preferred_dividend_basis='synthetic')
    q['shares']['preferred_if_converted']={'basis':'synthetic','shares':100,'dividend_addback':4}
    with pytest.raises(ValueError,match='exceeds'):
        cm.earnings(q['segments'],q['income'],q['shares'],p['segment_names'])


def test_security_eps_does_not_change_consolidated_cash_or_legal_shares():
    p=packet();q=p['scenarios'][0]['quarters'][0]
    q['income']['parent_after_tax_adjustments']={}
    q['shares']['eps_securities']={'basis':'synthetic', 'basic_equivalents':[], 'participation':None,
        'debt_if_converted':[{'id':'N','basis':'net tax expense','shares':100,'after_tax_interest':1}]}
    r=cm.earnings(q['segments'],q['income'],q['shares'],p['segment_names'])
    assert r['consolidated_income']==9 and r['parent_income']==8
    assert r['gaap_eps_income']==9 and r['gaap_diluted_shares']==200
    assert r['closing_shares']==100
    assert cm.liquidity(r,q['cash'],100,20)['operating_cash_flow']==13


def test_security_eps_full_year_recomputes_allocation():
    p=packet();s=p['scenarios'][0];s.pop('multiple')
    s['earnings_windows']={'four':{'quarters':['0','1','2','3'],'basis':'gaap'}}
    for q in s['quarters']:
        q['income']['parent_after_tax_adjustments']={}
        q['shares']['eps_securities']={'basis':'synthetic','basic_equivalents':[], 'participation':None,
            'debt_if_converted':[{'id':'N','basis':'net tax expense','shares':100,'after_tax_interest':1}]}
    w=result(p)['scenarios']['base']['earnings_windows']['four']
    assert w['eps_income']==36 and w['weighted_diluted_shares']==200
    assert w['eps']==.18


def test_ended_unreported_bridge_is_explicit_estimate_not_actual_opening():
    p=packet();s=p['scenarios'][0];s.pop('multiple')
    start=date(2026,6,9)
    p['opening_balance']['date']=s['opening_date']='2026-06-08'
    p['observed']['end']='2026-06-08'
    for q in s['quarters']:
        end=start+timedelta(days=90);q.update(start=str(start),end=str(end));start=end+timedelta(days=1)
    with pytest.raises(ValueError,match='ended-unreported'):result(p)
    q=s['quarters'][0]
    q['unreported_bridge']={'basis':'synthetic ended but unreported at current cutoff',
        'known_at':'2026-09-08T07:00:00+00:00','source_refs':['synthetic'],
        'expires_at':'2026-09-09T07:00:00+00:00','reported_results_available':False}
    r=result(p)
    assert r['opening_balance']['date']=='2026-06-08'
    assert r['scenarios']['base']['quarters'][0]['period_status']=='ended_unreported_estimate'
    assert r['scenarios']['base']['quarters'][1]['period_status']=='current_period_projection'
    assert r['scenarios']['base']['quarters'][2]['period_status']=='future_projection'
    assert r['publication']['status']=='withheld'
    q['unreported_bridge']['expires_at']='2026-09-08T07:00:00+00:00'
    with pytest.raises(ValueError,match='freshness'):result(p)


def test_inclusive_fiscal_end_remains_projection_until_next_cutoff_calendar_day():
    p = packet(); s = p['scenarios'][0]; s.pop('multiple')
    p['opening_balance']['date'] = s['opening_date'] = '2026-06-08'
    p['observed']['end'] = '2026-06-08'
    start = date(2026, 6, 9)
    for q in s['quarters']:
        end = start + timedelta(days=91)
        q.update(start=str(start), end=str(end)); start = end + timedelta(days=1)
    assert s['quarters'][0]['end'] == '2026-09-08'
    # No financial inputs change when the timestamp moves within the final
    # inclusive date. This is an estimate, not a reported actual quarter.
    early = cm.build(p, as_of='2026-09-08T07:00:00+00:00')
    late = cm.build(p, as_of='2026-09-08T23:59:59+00:00')
    assert early['scenarios'] == late['scenarios']
    assert early['scenarios']['base']['quarters'][0]['period_status'] == 'current_period_projection'
    assert early['publication']['status'] == 'withheld'
    with pytest.raises(ValueError, match='ended-unreported'):
        cm.build(p, as_of='2026-09-09T00:00:00+00:00')
    # The caller's explicit cutoff calendar controls the date comparison;
    # the engine does not infer an issuer's closing/reporting timezone.
    same_date = cm.build(p, as_of='2026-09-08T23:00:00-04:00')
    assert same_date['scenarios']['base']['quarters'][0]['period_status'] == 'current_period_projection'
    s['quarters'][0]['unreported_bridge'] = {
        'basis': 'synthetic next-day unreported estimate', 'source_refs': ['synthetic'],
        'known_at': '2026-09-08T07:00:00+00:00',
        'expires_at': '2026-09-10T07:00:00+00:00', 'reported_results_available': False}
    with pytest.raises(ValueError, match='only for an ended quarter'):
        cm.build(p, as_of='2026-09-08T23:59:59+00:00')
    next_day = cm.build(p, as_of='2026-09-09T00:00:00+00:00')
    assert next_day['scenarios']['base']['quarters'][0]['period_status'] == 'ended_unreported_estimate'


@pytest.mark.parametrize('change', ['missing', 'included', 'unknown', 'extra'])
def test_after_tax_scope_rejected(change):
    p = packet(); q = p['scenarios'][0]['quarters'][0]
    a = {'basis': 'synthetic', 'excluded_from_pretax_and_tax': True,
         'equity_income': 6, 'discontinued_income': -8}
    if change == 'missing': del a['equity_income']
    elif change == 'included': a['excluded_from_pretax_and_tax'] = False
    elif change == 'unknown': a['discontinued_income'] = None
    elif change == 'extra': a['pretax_income'] = 6
    q['income']['after_tax_items'] = a
    with pytest.raises(ValueError):
        cm.earnings(q['segments'], q['income'], q['shares'], p['segment_names'])


def test_hand_reconciliation_and_financing():
    p = packet()
    before = deepcopy(p)
    r = result(p)
    assert p == before
    assert r["observed"]["status"] == "reconciled"
    row = r["scenarios"]["base"]["quarters"][0]
    assert row["parent_income"] == 8 and row["adjusted_parent_income"] == 10
    assert row["cash"]["operating_cash_flow"] == 13
    assert row["cash"]["total_capex"] == 12
    assert row["cash"]["ending_liquidity"] == 104
    assert row["cash"]["ending_debt"] == 23
    assert r["publication"]["status"] == "withheld"
    target = r["scenarios"]["base"]["target_12m"]
    assert target["eps"] == .4
    assert target["low"] == 4 and target["high"] == 8
    assert target["validated"] is False
    assert r["scenarios"]["base"]["intrinsic"]["status"] == "unknown"


def test_duplicate_segment_parent_or_missing_elimination_is_rejected():
    for name in ["products_total", "eliminations"]:
        p = packet()
        if name == "eliminations":
            del p["observed"]["segments"][name]
        else:
            p["observed"]["segments"][name] = {"revenue": 100, "operating_profit": 20}
        with pytest.raises(ValueError, match="nonoverlapping"):
            result(p)


def test_wrong_reporting_basis_stops_all_forecasts():
    p = packet()
    p["observed"]["reported"]["operating_profit"] = 15
    r = result(p)
    assert r["status"] == "input_mismatch" and r["scenarios"] == {}


def test_loss_dilution_and_explicit_adjusted_bridge():
    p = packet()["observed"]
    p["income"]["other_nonoperating"] = {"loss": -101}
    p["income"]["parent_after_tax_adjustments"] = {"after_tax_nonrecurring": 102}
    p["shares"]["incremental_dilutive"] = 10
    e = cm.earnings(p["segments"], p["income"], p["shares"], list(p["segments"]))
    assert e["parent_income"] == -92
    assert e["gaap_eps"] == -.92 and e["gaap_diluted_shares"] == 100
    assert e["adjusted_eps"] == pytest.approx(10 / 110)


def test_partner_distribution_can_make_positive_earnings_unfunded():
    p = packet()
    p["scenarios"][0]["quarters"][0]["cash"]["partner_distributions"] = 200
    s = result(p)["scenarios"]["base"]
    assert s["quarters"][0]["gaap_eps"] > 0
    assert s["maximum_funding_gap"] > 0
    assert s["target_12m"]["status"] == "unknown" and s["target_12m"]["low"] is None
    assert s["quarters"][0]["cash"]["ending_debt"] == 23  # No balancing loan.


@pytest.mark.parametrize("field", ["capex_financing", "partner_distributions", "lease_principal", "noncash_adjustments", "debt_non_cash_change"])
def test_omitted_cash_fields_do_not_default_to_zero(field):
    p = packet()
    del p["scenarios"][0]["quarters"][0]["cash"][field]
    with pytest.raises(KeyError):
        result(p)


def test_new_equity_requires_issuance_and_reconciles_share_rollforward():
    p = packet()
    q = p["scenarios"][0]["quarters"][0]
    q["cash"]["equity_cash_raised"] = 100
    with pytest.raises(ValueError, match="issuance"):
        result(p)
    q["shares"]["issued"] = 10
    with pytest.raises(ValueError, match="roll-forward"):
        result(p)


@pytest.mark.parametrize("change,match", [("old_eps", "forward year"), ("path_pe", "cashflow-derived"),
                                         ("basis", "earnings basis"), ("reverse", "ordered")])
def test_multiple_semantics(change, match):
    p = packet()
    m = p["scenarios"][0]["multiple"]
    if change == "old_eps": m["eps_quarters"] = ["0", "1", "2", "3"]
    if change == "path_pe": m["method"] = "path_justified_pe"
    if change == "basis": m["earnings_basis"] = "unknown"
    if change == "reverse": m["low"] = 30
    with pytest.raises(ValueError, match=match): result(p)


def test_missing_multiple_has_no_archetype_fallback():
    p = packet()
    del p["scenarios"][0]["multiple"]
    assert result(p)["scenarios"]["base"]["target_12m"]["status"] == "unknown"


def test_adjusted_eps_cannot_use_a_different_pe_definition():
    p = packet()
    p["scenarios"][0]["multiple"]["adjusted_definition"] = "different_exclusions"
    with pytest.raises(ValueError, match="definitions must match"):
        result(p)


def test_disclosure_balance_or_cashflow_mismatch_also_stops_forecast():
    p = packet()
    p["observed"]["identities"] = [{"id": "owner_fcf", "components": {"ocf": 20, "capex": -10, "partner": -15}, "reported": 10}]
    assert result(p)["status"] == "input_mismatch"


def test_company_path_integrates_without_legacy_valuation_or_rate(monkeypatch):
    from quant.valuation import assessment
    from quant.report.valuation_report import fyi_map
    monkeypatch.setattr(assessment.model, 'valuate', lambda *a, **kw: pytest.fail('legacy model used'))
    r = assessment.build({'selected_tickers': ['SYN'], 'valuation': {'company_models': {'SYN': packet()}}}, as_of=STAMP)
    row = r['tickers']['SYN']
    assert row['status'] == 'company_research' and row['baseline'] is None
    assert row['company_research']['observed']['status'] == 'reconciled'
    assert cm.VERSION in fyi_map(r)['SYN']


def test_invalid_company_input_does_not_fall_back_to_old_prices(monkeypatch):
    from quant.valuation import assessment
    monkeypatch.setattr(assessment.model, 'valuate', lambda *a, **kw: pytest.fail('legacy model used'))
    p = packet()
    p['valid_until'] = STAMP
    r = assessment.build({'selected_tickers': ['SYN'], 'valuation': {'company_models': {'SYN': p}}}, as_of=STAMP)
    row = r['tickers']['SYN']
    assert row['status'] == 'unknown' and row['baseline'] is None
    assert 'company input expired' in row['missing']


def test_annual_eps_uses_weighted_shares_not_quarter_eps_sum():
    p = packet()
    qs = p["scenarios"][0]["quarters"]
    for q in qs[6:]:
        q["shares"].update(basic_weighted=200, closing=200)
    qs[6]["shares"]["issued"] = 100
    r = result(p)["scenarios"]["base"]["target_12m"]
    assert r["eps"] == pytest.approx(40 / 150)
    assert r["eps"] != pytest.approx(.1 + .1 + .05 + .05)


def test_cashflow_and_independent_pe_are_separate():
    p = packet()
    s = p["scenarios"][0]
    s["owner_dcf"] = {k: s[k] for k in ["known_at", "source_refs", "basis"]}
    s["owner_dcf"].update(cashflow_basis="common_equity_after_financing_and_partner_claims", claims_basis="synthetic all claims deducted",
                          cost_of_equity=.1, cashflows=[{"date": "2027-09-08", "common_cash": 10, "shares": 100}],
                          terminal={"date": "2027-09-08", "common_equity_value": 200, "shares": 100})
    a = result(p)["scenarios"]["base"]
    assert a["intrinsic"]["per_share"] == pytest.approx(2.1 / 1.1 ** (365 / 365.25))
    s["owner_dcf"]["terminal"]["shares"] = 200
    b = result(p)["scenarios"]["base"]
    assert b["intrinsic"]["terminal_pv"] == a["intrinsic"]["terminal_pv"] / 2
    assert b["target_12m"] == a["target_12m"]


@pytest.mark.parametrize("case", ["future_source", "future_capture", "future_assumption", "expired", "units", "nan", "gap"])
def test_availability_and_units_fail_closed(case):
    p = packet()
    if case == "future_source": p["sources"]["synthetic"]["available_at"] = "2027-01-01T00:00:00Z"
    if case == "future_capture": p["sources"]["synthetic"]["captured_at"] = "2027-01-01T00:00:00Z"
    if case == "future_assumption": p["scenarios"][0]["known_at"] = "2027-01-01T00:00:00Z"
    if case == "expired": p["valid_until"] = STAMP
    if case == "units": p["scale"] = "dollars"
    if case == "nan": p["observed"]["income"]["tax_expense"] = float('nan')
    if case == "gap": p["scenarios"][0]["quarters"][1]["start"] = "2026-12-10"
    with pytest.raises(ValueError): result(p)


def test_output_is_append_only(tmp_path):
    path = tmp_path / 'receipt.json'
    path.write_text('existing')
    with pytest.raises(SystemExit):
        cm.main(['--input', str(tmp_path / 'absent.json'), '--as-of', STAMP, '--output', str(path)])
    assert path.read_text() == 'existing'


@pytest.mark.parametrize('field', ['opening_liquidity', 'opening_debt', 'opening_shares'])
def test_scenario_cannot_invent_a_favourable_starting_balance(field):
    p = packet()
    p['scenarios'][0][field] += 100
    with pytest.raises(ValueError, match='differs from disclosed'):
        result(p)


def test_restricted_cash_is_excluded_from_operating_liquidity():
    r = result()
    assert r['opening_balance']['restricted_cash_excluded'] == 7
    assert r['opening_balance']['computed']['opening_liquidity'] == 100
    assert r['scenarios']['base']['quarters'][0]['cash']['ending_liquidity'] == 104
    p = packet()
    p['opening_balance']['reported']['opening_liquidity'] = 107
    r = result(p)
    assert r['status'] == 'input_mismatch' and r['scenarios'] == {}


def test_missing_or_wrong_dated_opening_is_not_a_zero_balance():
    p = packet()
    del p['opening_balance']
    with pytest.raises(ValueError, match='opening_balance required'):
        result(p)
    p = packet()
    p['scenarios'][0]['opening_date'] = '2026-09-07'
    with pytest.raises(ValueError, match='date must match'):
        result(p)


def test_debt_carrying_value_adjustment_is_not_new_financing_cash():
    p = packet()
    p['scenarios'][0]['quarters'][0]['cash']['debt_non_cash_change'] = 2
    row = result(p)['scenarios']['base']['quarters'][0]
    assert row['cash']['ending_debt'] == 25
    assert row['cash']['ending_liquidity'] == 104


def test_combined_nonoperating_disclosure_does_not_require_fake_zero_components():
    from quant.valuation.company_model import earnings
    import pytest
    income = {'nonoperating_basis': 'combined_net', 'net_nonoperating': 4,
              'nonoperating_definition': 'Synthetic aggregate; interest split unavailable',
              'tax_expense': 2, 'noncontrolling_income': 0, 'parent_after_tax_adjustments': {},
              'adjusted_definition': 'No adjustment'}
    args = ({'one': {'revenue': 100, 'operating_profit': 20}}, income,
            {'basic_weighted': 10, 'incremental_dilutive': 0, 'closing': 10}, ['one'])
    assert earnings(*args)['parent_income'] == 22
    income['interest_income'] = 0
    with pytest.raises(ValueError, match='split components'): earnings(*args)
    del income['interest_income']; del income['nonoperating_definition']
    with pytest.raises(ValueError, match='definition'): earnings(*args)


def timing_block(**values):
    return {'basis': 'synthetic selected cash flows; excludes tax/interest in all other cash entries',
            'other_entries_exclude_selected_timing_items': True, **values}


def test_cash_timing_replaces_accruals_without_changing_earnings():
    p = packet()
    q = p['scenarios'][0]['quarters'][0]
    q['cash']['income_cash_timing'] = timing_block(
        tax_cash_paid_net=5, interest_cash_paid=4, interest_cash_received=0,
        interest_cash_excludes_capitalized=True)
    r = result(p)['scenarios']['base']['quarters'][0]
    # Original CFO13: tax2->cash5 (-3), expense2->cash4 (-2), income1->cash0 (-1).
    assert r['cash']['operating_cash_flow'] == 7
    assert r['cash']['ending_liquidity'] == 98
    assert r['parent_income'] == 8


def test_tax_refund_and_combined_income_allow_tax_only_bridge():
    p = packet(); q = p['scenarios'][0]['quarters'][0]
    for name in ('interest_income', 'interest_expense', 'other_nonoperating'):
        q['income'].pop(name)
    q['income'].update(nonoperating_basis='combined_net', net_nonoperating=-2,
                       nonoperating_definition='synthetic combined')
    q['cash']['income_cash_timing'] = timing_block(tax_cash_paid_net=-3)
    r = result(p)['scenarios']['base']['quarters'][0]
    assert r['cash']['operating_cash_flow'] == 18  # Add provision2 and refund3.
    q['cash']['income_cash_timing'].update(interest_cash_paid=0, interest_cash_received=0,
                                         interest_cash_excludes_capitalized=True)
    with pytest.raises(ValueError, match='split interest'):
        result(p)


@pytest.mark.parametrize('change', ['overlap', 'capitalized', 'missing_side', 'empty', 'unknown'])
def test_cash_timing_rejects_ambiguous_or_duplicate_scope(change):
    p = packet(); block = timing_block(interest_cash_paid=4, interest_cash_received=0,
                                      interest_cash_excludes_capitalized=True)
    if change == 'overlap': block['other_entries_exclude_selected_timing_items'] = False
    if change == 'capitalized': block['interest_cash_excludes_capitalized'] = False
    if change == 'missing_side': del block['interest_cash_received']
    if change == 'empty': block = timing_block()
    if change == 'unknown': block['invented_tax_addback'] = 2
    p['scenarios'][0]['quarters'][0]['cash']['income_cash_timing'] = block
    with pytest.raises(ValueError): result(p)


def test_cash_timing_is_invariant_to_selected_accrual_timing():
    p = packet(); q = p['scenarios'][0]['quarters'][0]
    q['cash']['income_cash_timing'] = timing_block(tax_cash_paid_net=5,
        interest_cash_paid=4, interest_cash_received=0, interest_cash_excludes_capitalized=True)
    before = result(p)['scenarios']['base']['quarters'][0]['cash']['ending_liquidity']
    q['income']['tax_expense'] += 7
    q['income']['interest_expense'] += 3
    q['income']['interest_income'] += 2
    assert result(p)['scenarios']['base']['quarters'][0]['cash']['ending_liquidity'] == before


def legal_cash_packet():
    p=packet();s=p['scenarios'][0]
    for i,q in enumerate(s['quarters']):
        expense=40 if i==0 else 0
        paid=15 if i==0 else (25 if i==2 else 0)
        q['segments']['corporate']['operating_profit']-=expense
        q['cash']['income_cash_timing']=timing_block(legal_settlement={
            'basis':'Synthetic nondeductible selected operating settlement, payments hypothetical',
            'scope':'selected_conditional_operating_settlement','expense':expense,'cash_paid':paid,
            'expense_is_in_ebit':True,'operating_profit_before_expense':13,
            'timing_key':'legal_cash'})
    return p


def test_selected_legal_expense_and_dated_cash_enter_cfo_once():
    old=result(packet())['scenarios']['base']
    new=result(legal_cash_packet())['scenarios']['base']
    cumulative=0
    for i,(q,oq) in enumerate(zip(new['quarters'],old['quarters'])):
        expense=40 if i==0 else 0
        paid=15 if i==0 else (25 if i==2 else 0)
        cumulative+=paid
        assert q['operating_profit']==oq['operating_profit']-expense
        assert q['cash']['operating_cash_flow']==oq['cash']['operating_cash_flow']-paid
        assert q['cash']['ending_liquidity']==oq['cash']['ending_liquidity']-cumulative
        assert q['cash']['ending_debt']==oq['cash']['ending_debt']
        assert q['cash']['total_capex']==oq['cash']['total_capex']
        assert q['cash']['income_cash_timing']['selected_legal_settlement']['cash_paid']==paid


@pytest.mark.parametrize('change',['missing_expense','missing_cash','unknown_cash','negative_expense',
    'bool_expense','not_in_ebit','unpaired_ebit','actual_scope','missing_key','duplicate_other',
    'duplicate_noncash','overlap_wc','empty_legal'])
def test_legal_cash_bridge_rejects_unpaired_or_duplicate_items(change):
    p=legal_cash_packet();q=p['scenarios'][0]['quarters'][0]
    b=q['cash']['income_cash_timing'];a=b['legal_settlement']
    if change=='missing_expense':a.pop('expense')
    elif change=='missing_cash':a.pop('cash_paid')
    elif change=='unknown_cash':a['cash_paid']=None
    elif change=='negative_expense':a['expense']=-1
    elif change=='bool_expense':a['expense']=True
    elif change=='not_in_ebit':a['expense_is_in_ebit']=False
    elif change=='unpaired_ebit':q['segments']['corporate']['operating_profit']+=1
    elif change=='actual_scope':a['scope']='all_actual_company_settlements'
    elif change=='missing_key':a['timing_key']=''
    elif change=='duplicate_other':q['cash']['other_cash_flows'][a['timing_key']]=0
    elif change=='duplicate_noncash':q['cash']['noncash_adjustments'][a['timing_key']]=0
    elif change=='overlap_wc':b['other_entries_exclude_selected_timing_items']=False
    elif change=='empty_legal':b['legal_settlement']=None
    with pytest.raises(ValueError):result(p)


def principal_packet():
    p=packet();s=p['scenarios'][0]
    e={k:s[k] for k in ['known_at','basis','source_refs']}
    case={**e,'currency':'USD','scale':'millions','date':s['opening_date'],
          'scope':'selected_contractual_note_principal_only',
          'execution_assumption':'contractual_maturity_no_early_redemption_refinancing',
          'payment_date_basis':'Synthetic contractual quarter date, no actual future transfer asserted',
          'selected_opening_nominal':9,'payments':[], 'periods':[],
          'unresolved_economics':[{**e,'economic_id':'book_coupon_credit_refinancing', 'status':'unresolved'}]}
    for i,q in enumerate(s['quarters']):
        amount={0:4,2:5}.get(i,0)
        case['periods'].append({**e,'start':q['start'],'end':q['end'],
                               'other_debt_principal_paid':q['cash']['debt_repaid']})
        if amount:case['payments'].append({**e,'economic_id':q['id'],'contractual_due':q['end'],'amount':amount})
        q['cash']['debt_repaid']+=amount
    s['note_principal_cash']=case
    return p


def test_contractual_principal_cash_binds_dates_once_without_changing_income_or_cfo():
    old=result(packet())['scenarios']['base'];new=result(principal_packet())['scenarios']['base']
    total=0
    for i,(q,oq) in enumerate(zip(new['quarters'],old['quarters'])):
        total+={0:4,2:5}.get(i,0)
        assert {k:v for k,v in q.items() if k!='cash'}=={k:v for k,v in oq.items() if k!='cash'}
        assert q['cash']['operating_cash_flow']==oq['cash']['operating_cash_flow']
        assert q['cash']['total_capex']==oq['cash']['total_capex']
        assert q['cash']['ending_liquidity']==oq['cash']['ending_liquidity']-total
        assert q['cash']['ending_debt']==oq['cash']['ending_debt']-total
    ledger=new['note_principal_cash']
    assert [r['selected_principal_paid'] for r in ledger['periods']]==[4,0,5,0,0,0,0,0]
    assert ledger['periods'][-1]['remaining_selected_nominal']==0
    assert ledger['selected_principal_paid_total']==9
    assert ledger['unresolved_economics'][0]['status']=='unresolved'


@pytest.mark.parametrize('change',['scope','execution','nominal','unknown_payment','negative_payment',
    'duplicate_payment','past_payment','late_payment','quarter_binding','missing_quarter','other_cash',
    'inventory','duplicate_inventory','future_evidence','missing_evidence','wrong_opening','missing_date_basis'])
def test_selected_note_principal_rejects_unavailable_or_unpaired_payments(change):
    p=principal_packet();s=p['scenarios'][0];a=s['note_principal_cash']
    if change=='scope':a['scope']='all_company_fair_debt'
    elif change=='execution':a['execution_assumption']='actual_transfers'
    elif change=='nominal':a['selected_opening_nominal']+=1
    elif change=='unknown_payment':a['payments'][0]['amount']=None
    elif change=='negative_payment':a['payments'][0]['amount']=-1
    elif change=='duplicate_payment':a['payments']*=2
    elif change=='past_payment':a['payments'][0]['contractual_due']=s['opening_date']
    elif change=='late_payment':a['payments'][0]['contractual_due']='2030-01-01'
    elif change=='quarter_binding':a['periods'][0]['end']=a['periods'][1]['end']
    elif change=='missing_quarter':a['periods'].pop()
    elif change=='other_cash':s['quarters'][0]['cash']['debt_repaid']+=1
    elif change=='inventory':a['unresolved_economics']=[]
    elif change=='duplicate_inventory':a['unresolved_economics']*=2
    elif change=='future_evidence':a['payments'][0]['known_at']='2026-09-09T00:00:00Z'
    elif change=='missing_evidence':a['payments'][0]['source_refs']=['absent']
    elif change=='wrong_opening':a['date']='2026-09-07'
    elif change=='missing_date_basis':a.pop('payment_date_basis')
    with pytest.raises((ValueError,KeyError)):result(p)


def financing_obligation_packet():
    p = packet()
    for q in p['scenarios'][0]['quarters']:
        q['cash']['capex_financing'] = 0
        q['cash']['financing_obligation_cash'] = {
            'basis': 'synthetic existing-obligation payment; book and interest unverified',
            'population_basis': 'selected_obligations_outside_recourse_nonrecourse_debt',
            'cash_paid': 2, 'cash_received': 0,
            'excluded_from_debt_rollforward': True, 'not_new_operating_capital': True,
            'other_entries_exclude_this_population': True}
    return p


def test_existing_financing_payment_is_not_new_capital_or_recourse_debt():
    old = result()['scenarios']['base']; new = result(financing_obligation_packet())['scenarios']['base']
    for a, b in zip(old['quarters'], new['quarters']):
        assert b['gaap_eps'] == a['gaap_eps'] == .08
        assert b['cash']['operating_cash_flow'] == a['cash']['operating_cash_flow'] == 13
        assert b['cash']['total_capex'] == a['cash']['total_capex'] - 2 == 10
        assert b['cash']['cash_before_external_financing'] == a['cash']['cash_before_external_financing'] + 2
        assert b['cash']['ending_liquidity'] == a['cash']['ending_liquidity']
        assert b['cash']['ending_debt'] == a['cash']['ending_debt']
    s = new['native_financing_obligation_cash']
    assert s['cash_paid'] == 16 and s['cash_received'] == 0 and s['net_cash'] == -16
    assert s['selected_period_count'] == s['native_period_count'] == 8
    assert s['book_rollforward_verified'] is False and s['validated'] is False
    assert s['publication'] == 'withheld'


def test_selected_financing_cash_receipt_does_not_invent_debt_or_assets():
    p = financing_obligation_packet()
    p['scenarios'][0]['quarters'][0]['cash']['financing_obligation_cash']['cash_received'] = 3
    a = result(financing_obligation_packet())['scenarios']['base']
    b = result(p)['scenarios']['base']
    for qa, qb in zip(a['quarters'], b['quarters']):
        assert qb['cash']['ending_liquidity'] == qa['cash']['ending_liquidity'] + 3
        assert qb['cash']['ending_debt'] == qa['cash']['ending_debt']
        assert qb['cash']['total_capex'] == qa['cash']['total_capex']
    assert b['native_financing_obligation_cash']['net_cash'] == -13


def test_partial_financing_cash_scope_is_not_full_period_coverage():
    p = packet()
    p['scenarios'][0]['quarters'][0]['cash'] = financing_obligation_packet()['scenarios'][0]['quarters'][0]['cash']
    s = result(p)['scenarios']['base']['native_financing_obligation_cash']
    assert s['selected_period_count'] == 1 and s['native_period_count'] == 8
    assert s['cash_paid'] == 2 and len(s['periods']) == 1


@pytest.mark.parametrize('change', [None, {'cash_paid': None}, {'cash_received': True},
    {'cash_paid': -1}, {'cash_received': float('nan')}, {'basis': ''},
    {'population_basis': 'new_PPE'}, {'excluded_from_debt_rollforward': False},
    {'not_new_operating_capital': False}, {'other_entries_exclude_this_population': False},
    {'book_rollforward_verified': True}])
def test_financing_cash_rejects_unknown_or_inconsistent_population(change):
    cash = financing_obligation_packet()['scenarios'][0]['quarters'][0]['cash']
    if change is None:
        cash['financing_obligation_cash'] = None
    else:
        cash['financing_obligation_cash'].update(change)
    with pytest.raises(ValueError):
        cm.liquidity({'consolidated_income': 8}, cash, 100, 20)
