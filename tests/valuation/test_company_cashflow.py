from copy import deepcopy
from datetime import date, timedelta

import pytest

from quant.valuation.company_cashflow import value


def case():
    evidence = {'basis': 'synthetic unlevered cash assumption', 'source_refs': ['synthetic']}
    return {'date': '2026-09-08', 'currency': 'USD', 'scale': 'millions',
        'cashflow_basis': 'consolidated_unlevered_after_operating_tax', 'sbc_treatment': 'expense_no_addback',
        'lease_treatment': 'operating_leases_expensed_finance_leases_in_capital_and_debt',
        'tax_basis': 'synthetic cash tax excluding financing tax shields', 'capital_basis': 'synthetic full reinvestment',
        'periods': [{**evidence, 'start': '2026-09-09', 'end': '2027-09-08', 'wacc': .1,
                     'operating_profit': 100, 'cash_operating_tax': 20, 'depreciation_amortization': 30,
                     'gross_capital_investment': 50, 'capital_grants': 5, 'working_capital_increase': 10}],
        'terminal': {**evidence, 'date': '2027-09-08', 'growth': .03, 'incremental_roic': .12,
                     'wacc': .1, 'next_year_normalized_nopat': 80}}


def test_hand_cashflow_and_reinvestment_consistent_terminal():
    p=case(); saved=deepcopy(p); r=value(p)
    assert p==saved
    assert r['periods'][0]['fcff']==55
    assert r['terminal_reinvestment_rate']==.25 and r['terminal_fcff']==60
    assert r['operating_value']==pytest.approx((55+60/.07)/1.1**(365/365.25))
    assert r['publication']=='withheld' and not r['validated']


def capital_case():
    p = lease_case()
    p['periods'][0]['capital_details'] = {
        'basis': 'synthetic complete asset populations, all D&A included in EBIT',
        'investment_basis': 'gross_new_assets_excluding_financing_settlements',
        'working_capital_excludes_these_assets': True,
        'categories': {name: {'new_investment': investment, 'da_in_ebit': da, 'basis': 'synthetic'}
                       for name, investment, da in [
                           ('tangible_excluding_finance_leases', 20, 15),
                           ('intangibles_excluding_finance_leases', 10, 7),
                           ('finance_leases', 20, 8), ('other_operating_capital', 0, 0)]}}
    return p


def test_capital_bridge_reconciles_without_double_deduction_or_claiming_source_truth():
    p = capital_case(); saved = deepcopy(p)
    r = value(p)
    assert p == saved
    assert r['operating_value'] == value(lease_case())['operating_value']
    assert r['periods'][0]['capital']['status'] == 'arithmetically_reconciled'
    assert r['periods'][0]['capital']['source_completeness'] == 'not_established'
    assert value(case())['periods'][0]['capital']['status'] == 'not_separately_reconciled'


@pytest.mark.parametrize('mutation', ['missing', 'unknown', 'investment', 'da', 'working', 'settlement', 'lease'])
def test_capital_omissions_and_conflicting_populations_rejected(mutation):
    p = capital_case(); d = p['periods'][0]['capital_details']; c = d['categories']
    if mutation == 'missing': del c['intangibles_excluding_finance_leases']
    elif mutation == 'unknown': c['intangibles_excluding_finance_leases']['new_investment'] = None
    elif mutation == 'investment': c['intangibles_excluding_finance_leases']['new_investment'] = 0
    elif mutation == 'da': c['intangibles_excluding_finance_leases']['da_in_ebit'] = 0
    elif mutation == 'working': d['working_capital_excludes_these_assets'] = False
    elif mutation == 'settlement': d['investment_basis'] = 'cash_payments_including_old_debt'
    elif mutation == 'lease':
        c['finance_leases']['new_investment'] += 1
        c['tangible_excluding_finance_leases']['new_investment'] -= 1
    with pytest.raises(ValueError): value(p)


@pytest.mark.parametrize('field',['debt_issued','interest_expense','stock_compensation_addback','partner_distributions'])
def test_financing_and_sbc_cannot_inflate_operating_cashflow(field):
    p=case();p['periods'][0][field]=100
    with pytest.raises(ValueError,match='financing or SBC'):value(p)


def test_growth_cannot_be_free_or_exceed_discount_rate():
    p=case();p['terminal']['growth']=.1
    with pytest.raises(ValueError,match='below WACC'):value(p)
    p=case();p['terminal']['incremental_roic']=.02
    with pytest.raises(ValueError,match='incremental ROIC'):value(p)


def test_missing_cash_tax_is_not_zero_and_gapped_periods_rejected():
    p=case();del p['periods'][0]['cash_operating_tax']
    with pytest.raises(KeyError):value(p)
    p=case();p['periods'][0]['start']='2026-10-01'
    with pytest.raises(ValueError,match='consecutive'):value(p)


def test_host_links_earnings_and_equity_bridge_without_duplicate_segment_value():
    from quant.valuation import company_model as cm
    from tests.valuation.test_company_model import packet,STAMP
    from tests.valuation.test_company_equity import case as equity_case
    p=packet();s=p['scenarios'][0]; d=case();q=s['quarters'][4]
    d.update(known_at=s['known_at'],basis=s['basis'],source_refs=s['source_refs'],date=s['quarters'][3]['end'])
    d['periods'][0].update(start=q['start'],end=q['end'],operating_profit=13)
    d['terminal']['date']=q['end']
    s['operating_dcf']=d
    e=equity_case();e.update(known_at=s['known_at'],basis=s['basis'],source_refs=s['source_refs'],date=d['date'],
                            use_consolidated_dcf=True,required_business_ids=['consolidated'],businesses=[])
    for row in e['nonoperating_assets']+e['claims']:row['date']=d['date']
    s['business_equity']=e
    r=cm.build(p,as_of=STAMP)['scenarios']['base']
    assert r['business_equity']['operating_value']==r['operating_value']['operating_value']
    assert r['business_equity']['per_share'] is not None
    # Unknown nonoperating economics hold only the ordinary-equity value. The
    # calculated operating path and its dated DCF remain inspectable.
    asset = e['nonoperating_assets'][0]
    asset['valuation_status'] = 'unresolved'
    del asset['value']
    unknown = cm.build(p, as_of=STAMP)['scenarios']['base']
    assert unknown['quarters'] == r['quarters']
    assert unknown['operating_value'] == r['operating_value']
    assert unknown['business_equity']['per_share'] is None
    assert unknown['business_equity']['unresolved_assets'] == ['cash']
    from quant.report.valuation_report import fyi_map
    from quant.valuation.assessment import build as assessment
    receipt = assessment({'selected_tickers': ['SYN'],
                          'valuation': {'company_models': {'SYN': p}}}, as_of=STAMP)
    line = fyi_map(receipt)['SYN']
    assert '基准条件经营价值 $' in line and '普通股权益桥未闭合：1项资产待估' in line
    assert receipt['tickers']['SYN']['company_research']['scenarios']['base']['business_equity']['unresolved_assets'] == ['cash']
    d['periods'][0]['operating_profit']=100
    with pytest.raises(ValueError,match='differs from company'):cm.build(p,as_of=STAMP)


def test_unestimable_company_does_not_hide_another_company_operating_value():
    from quant.report.valuation_report import coverage_summary, fyi_map
    from quant.valuation.assessment import build
    from tests.valuation.test_company_model import packet, STAMP
    ready = packet()
    scenario = ready['scenarios'][0]
    dcf = case()
    next_q = scenario['quarters'][4]
    dcf.update(known_at=scenario['known_at'], basis=scenario['basis'],
               source_refs=scenario['source_refs'], date=scenario['quarters'][3]['end'])
    dcf['periods'][0].update(start=next_q['start'], end=next_q['end'], operating_profit=13)
    dcf['terminal']['date'] = next_q['end']
    scenario['operating_dcf'] = dcf
    held = deepcopy(ready)
    held['ticker'] = 'HELD'
    held['operating_path_hold'] = {'status': 'unestimable', 'reason': 'cash and capital path conflict',
                                   'known_at': scenario['known_at'], 'basis': 'synthetic review',
                                   'source_refs': scenario['source_refs']}
    expired = deepcopy(ready)
    expired['ticker'] = 'OLD'
    expired['valid_until'] = STAMP
    receipt = build({'selected_tickers': ['SYN', 'HELD', 'OLD'],
                     'valuation': {'company_models': {'SYN': ready, 'HELD': held, 'OLD': expired}}},
                    as_of=STAMP)
    rows = receipt['tickers']
    assert rows['SYN']['company_research']['scenarios']['base']['operating_value']['status'] == 'research_sensitivity'
    assert rows['HELD']['company_research']['scenarios']['base']['operating_value']['status'] == 'unknown'
    assert rows['HELD']['company_research']['scenarios']['base']['business_equity']['status'] == 'unknown'
    assert rows['OLD']['status'] == 'unknown' and 'company input expired' in rows['OLD']['missing']
    assert [rows[t]['estimability']['operating_value'] for t in ('SYN', 'HELD', 'OLD')] == [
        'conditional_research', 'unestimable', 'input_expired']
    assert coverage_summary(receipt) == {'total': 3, 'publishable': 0, 'company_research': 2,
                                          'computed': 0, 'conditional_operating': 1,
                                          'operating_path_unestimable': 1, 'expired_inputs': 1,
                                          'unestimable': 2}
    lines = fyi_map(receipt)
    assert '基准条件经营价值 $' in lines['SYN'] and '不是普通股每股价值' in lines['SYN']
    assert '长期经营价值不可估' in lines['HELD'] and '基准条件经营价值 $' not in lines['HELD']
    assert '本轮估值未知' in lines['OLD'] and '基准条件经营价值 $' not in lines['OLD']


def lease_case():
    p = case()
    p['periods'][0]['lease_details'] = {
        'population_basis': 'same_contract_population_expense_and_cash',
        'working_capital_excludes_lease_accrual': True,
        'cash_tax_basis': 'operating_lease_deductions_included_finance_interest_shield_excluded',
        'basis': 'Synthetic common contract population, tax and working capital already reconciled',
        'operating_expense_in_ebit': 12, 'operating_cash_paid': 9,
        'finance_new_assets': 20, 'finance_principal_paid': 5, 'finance_interest_paid': 2,
        'finance_da_in_total': 8, 'capital_investment_excluding_finance_leases': 30}
    return p


def test_cash_rent_replaces_accrual_once_and_finance_cash_is_not_capex():
    p = lease_case()
    result = value(p)['periods'][0]
    assert result['fcff'] == 58  # Base 55 plus expense 12 minus cash rent 9.
    assert result['leases']['finance_cash_excluded_from_fcff'] == 7
    p['periods'][0]['lease_details']['finance_principal_paid'] = 18
    assert value(p)['periods'][0]['fcff'] == 58  # Financing is not another operating investment.
    p['periods'][0]['lease_details']['operating_cash_paid'] = 15
    assert value(p)['periods'][0]['fcff'] == 52


@pytest.mark.parametrize('mutation', ['capital', 'da', 'working_capital', 'population', 'tax'])
def test_inconsistent_lease_contract_rejected(mutation):
    p = lease_case(); d = p['periods'][0]['lease_details']
    if mutation == 'capital': d['finance_new_assets'] = 21
    elif mutation == 'da': d['finance_da_in_total'] = 31
    elif mutation == 'working_capital': d['working_capital_excludes_lease_accrual'] = False
    elif mutation == 'population': d['population_basis'] = 'unknown'
    else: d['cash_tax_basis'] = 'GAAP tax rate only'
    with pytest.raises(ValueError): value(p)


def test_same_rent_cash_with_different_accrual_does_not_change_value():
    p = lease_case(); changed = deepcopy(p)
    changed['periods'][0]['operating_profit'] -= 3
    changed['periods'][0]['lease_details']['operating_expense_in_ebit'] += 3
    assert value(p)['operating_value'] == value(changed)['operating_value']
