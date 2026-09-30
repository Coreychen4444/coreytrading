from copy import deepcopy
import pytest
from quant.valuation.company_amortization import project
from tests.valuation.test_company_amortization import case


def physical():
    return {'basis_type':'new_physical_finance_lease_ROU', 'binding_scope':'native_cash_debt', 'new_assets':7,
            'lease_principal_paid':2, 'other_debt_non_cash_change':-1,
            'basis':'Synthetic disjoint equipment lease; licenses elsewhere', 'source_refs':['synthetic']}


def test_equipment_lease_is_disjoint_new_capital_and_principal_is_not_investment():
    p=case();p['periods'][0]['other_non_cash_operating_capital']=physical();original=deepcopy(p)
    r=project(p)['periods'][0]
    assert p==original
    assert r['gross_capital_investment']==45  # cash30 + license8 + physical7
    assert r['physical_lease_debt_non_cash_change']==4
    assert r['new_financed_technology_licenses']==8
    p['periods'][0]['other_non_cash_operating_capital']['lease_principal_paid']=50
    assert project(p)['periods'][0]['gross_capital_investment']==45


@pytest.mark.parametrize('mutation',['unknown','negative','bool','scope','missing_source','missing_other_debt'])
def test_missing_or_misclassified_physical_capital_rejected(mutation):
    p=case();x=physical();p['periods'][0]['other_non_cash_operating_capital']=x
    if mutation=='unknown':x['new_assets']=None
    elif mutation=='negative':x['new_assets']=-1
    elif mutation=='bool':x['new_assets']=True
    elif mutation=='scope':x['basis_type']='technology_license'
    elif mutation=='missing_source':x['source_refs']=[]
    elif mutation=='missing_other_debt':x.pop('other_debt_non_cash_change')
    with pytest.raises(ValueError):project(p)


def test_promised_exhaustion_cannot_leave_assets_in_terminal():
    p=case();p['require_exhaustion_before_terminal']=True
    with pytest.raises(ValueError,match='depleted'):project(p)
    p['periods'][0]['existing_amortization']=100
    p['iprd_readiness']['life_years']=.01
    assert project(p)['require_exhaustion_before_terminal'] is True
    p['require_exhaustion_before_terminal']='yes'
    with pytest.raises(ValueError):project(p)


def test_host_checks_physical_debt_and_terminal_date_with_same_selected_pool():
    from tests.valuation.test_company_model import packet,STAMP
    from tests.valuation.test_company_cashflow import case as dcf_case
    from quant.valuation.company_model import build
    p=packet();s=p['scenarios'][0];ledger=case();ledger.update(known_at=s['known_at'],basis=s['basis'],source_refs=s['source_refs'],date=s['opening_date'],opening_iprd=0,reported_total=100,require_exhaustion_before_terminal=True,periods=[])
    ledger['iprd_readiness']={'status':'not_ready_in_projection','basis':'No selected IPRD'}
    for i,q in enumerate(s['quarters']):
        x=physical();x['source_refs']=s['source_refs'];q['cash']['lease_principal']=2;q['cash']['debt_non_cash_change']=4
        charge=100 if i==len(s['quarters'])-1 else 0
        ledger['periods'].append({'start':q['start'],'end':q['end'],'basis':'synthetic','source_refs':s['source_refs'],'existing_amortization':charge,'other_da':q['cash']['depreciation_amortization'],'operating_profit_before_acquired_amortization':sum(x['operating_profit'] for x in q['segments'].values())+charge,'cash_new_assets':q['cash']['capex_investing'],'new_financed_technology_licenses':0,'technology_license_principal_paid':q['cash']['capex_financing'],'other_non_cash_operating_capital':x})
        q['cash']['depreciation_amortization']+=charge
    s['acquired_intangible_runoff']=ledger
    d=dcf_case();q=s['quarters'][-1];d.update(known_at=s['known_at'],basis=s['basis'],source_refs=s['source_refs'],date=s['quarters'][-2]['end']);d['periods'][0].update(start=q['start'],end=q['end'],operating_profit=sum(x['operating_profit'] for x in q['segments'].values()),depreciation_amortization=q['cash']['depreciation_amortization'],gross_capital_investment=q['cash']['capex_investing']+7);d['terminal']['date']=q['end'];s['operating_dcf']=d
    r=build(p,as_of=STAMP)['scenarios']['base'];assert r['acquired_intangible_runoff']['periods'][-1]['remaining_existing']==0
    q['cash']['debt_non_cash_change']+=1
    with pytest.raises(ValueError,match='physical finance-lease'):build(p,as_of=STAMP)
    q['cash']['debt_non_cash_change']-=1
    ledger['periods'].append({**ledger['periods'][-1],'start':'2028-04-01','end':'2028-06-30','existing_amortization':0})
    # The fixture's actual final date is used below to keep consecutive periods.
    from datetime import date,timedelta
    last=date.fromisoformat(q['end']);ledger['periods'][-1].update(start=str(last+timedelta(days=1)),end=str(last+timedelta(days=91)))
    with pytest.raises(ValueError,match='terminal'):build(p,as_of=STAMP)


def test_operating_only_tail_does_not_invent_future_financing_flows():
    p=case();x=physical();x['binding_scope']='operating_capital_only';x.pop('lease_principal_paid');x.pop('other_debt_non_cash_change');p['periods'][0]['other_non_cash_operating_capital']=x
    r=project(p)['periods'][0]
    assert 'physical_lease_debt_non_cash_change' not in r
    assert 'lease_principal_paid' not in r['other_non_cash_operating_capital']
    x['lease_principal_paid']=0
    with pytest.raises(ValueError,match='physical finance-lease'):project(p)
