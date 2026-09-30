from copy import deepcopy
from datetime import date,timedelta
import pytest
from quant.valuation.company_ppe_runoff import project


def case():
    return {'currency':'USD','scale':'millions','date':'2026-06-30',
        'scope':'selected_opening_finance_ROU_only',
        'allocation':'constant_daily_source_quarter_rate_scenario_assumption',
        'allocation_basis':'Synthetic quarter rate, not issuer remaining life',
        'opening_selected_in_reported_PPE':True,'other_DA_excludes_selected_population':True,
        'retirement_impairment_assumption':'none_for_selected_opening_pool',
        'opening_selected_net_ROU':100,'reported_total_net_PPE':150,'excluded_other_net_PPE':50,
        'excluded_populations':[{'economic_id':'other_owned_and_future_capital','status':'unresolved',
                                'basis':'Excluded inventory, not actual zero','source_refs':['synthetic']}],
        'reference_quarter':{'start':'2026-04-01','end':'2026-06-30','amortization':91,
                             'basis':'Synthetic rate','source_refs':['synthetic']},
        'periods':[{'start':'2026-07-01','end':'2026-09-30','role':'native_income_cash_forecast',
                    'basis':'Synthetic linked expense','source_refs':['synthetic'],
                    'income_cash_binding':{'other_physical_da':3,'other_cash_da':1,
                        'operating_profit_before_selected_depreciation':100,
                        'basis':'Nonoverlapping expense/cash','source_refs':['synthetic']}},
                   {'start':'2026-10-01','end':'2026-10-08','role':'cost_only_outside_native_forecast',
                    'basis':'Book cost only tail','source_refs':['synthetic']}]}


def test_pool_exhaustion_never_adds_an_asset_and_tail_is_not_a_corporate_forecast():
    c=case(); old=deepcopy(c);r=project(c)
    assert [row['selected_depreciation'] for row in r['periods']]==[92,8]
    assert r['periods'][0]['operating_profit']==8 and r['periods'][0]['total_cash_da']==96
    assert r['periods'][-1]['remaining_selected_net_ROU']==0
    assert 'operating_profit' not in r['periods'][-1] and 'total_cash_da' not in r['periods'][-1]
    assert r['source_completeness']=='not_established' and r['publication']=='withheld'
    r['excluded_populations'][0]['basis']='changed output';assert c==old


@pytest.mark.parametrize('mutation',['scope','allocation','double_asset','overlap','unknown_pool','bool_pool',
    'negative_pool','unreconciled_pool','future_reference','zero_rate','reference_source','unresolved_inventory',
    'missing_inventory','duplicate_inventory','period_gap','incomplete','too_long_tail','after_exhaustion',
    'tail_earnings','native_after_tail','unknown_other_da','negative_other_da','binding_source'])
def test_invalid_or_unbounded_projection_rejected(mutation):
    p=case()
    if mutation=='scope':p['scope']='all_company_capital'
    elif mutation=='allocation':p['allocation']='issuer_actual_remaining_life'
    elif mutation=='double_asset':p['opening_selected_in_reported_PPE']=False
    elif mutation=='overlap':p['other_DA_excludes_selected_population']=False
    elif mutation=='unknown_pool':p['opening_selected_net_ROU']=None
    elif mutation=='bool_pool':p['opening_selected_net_ROU']=True
    elif mutation=='negative_pool':p['opening_selected_net_ROU']=-1
    elif mutation=='unreconciled_pool':p['reported_total_net_PPE']+=1
    elif mutation=='future_reference':p['reference_quarter']['end']='2026-07-01'
    elif mutation=='zero_rate':p['reference_quarter']['amortization']=0
    elif mutation=='reference_source':p['reference_quarter']['source_refs']=[]
    elif mutation=='unresolved_inventory':p['excluded_populations'][0]['status']='valued'
    elif mutation=='missing_inventory':p['excluded_populations']=[]
    elif mutation=='duplicate_inventory':p['excluded_populations']*=2
    elif mutation=='period_gap':p['periods'][0]['start']='2026-07-02'
    elif mutation=='incomplete':p['periods'].pop()
    elif mutation=='too_long_tail':p['periods'][-1]['end']='2026-10-09'
    elif mutation=='after_exhaustion':p['periods'].append({**p['periods'][-1],'start':'2026-10-09','end':'2026-10-10'})
    elif mutation=='tail_earnings':p['periods'][-1]['role']='native_income_cash_forecast'
    elif mutation=='native_after_tail':p['periods'][0].pop('income_cash_binding');p['periods'][0]['role']='cost_only_outside_native_forecast'
    elif mutation=='unknown_other_da':p['periods'][0]['income_cash_binding']['other_physical_da']=None
    elif mutation=='negative_other_da':p['periods'][0]['income_cash_binding']['other_physical_da']=-1
    elif mutation=='binding_source':p['periods'][0]['income_cash_binding']['source_refs']=[]
    with pytest.raises(ValueError):project(p)


def linked_packet():
    from tests.valuation.test_company_model import packet
    p=packet();s=p['scenarios'][0];e={k:s[k] for k in ['known_at','source_refs','basis']}
    p['operating_path_hold']={**e,'status':'unestimable','reason':'Synthetic other capital unresolved'}
    c=case();c.update(e,date=s['opening_date'],opening_selected_net_ROU=800,
                     reported_total_net_PPE=900,excluded_other_net_PPE=100,periods=[])
    c['reference_quarter'].update(start='2026-06-09',end='2026-09-07',amortization=91)
    for q in s['quarters']:
        c['periods'].append({'start':q['start'],'end':q['end'],'role':'native_income_cash_forecast',
            'basis':'Synthetic link','source_refs':['synthetic'],
            'income_cash_binding':{'other_physical_da':2,'other_cash_da':0,
                'operating_profit_before_selected_depreciation':16,
                'basis':'Selected historical3/q cost replaced; no tax change in synthetic case',
                'source_refs':['synthetic']}})
    start=date.fromisoformat(s['quarters'][-1]['end'])+timedelta(days=1)
    c['periods'].append({'start':str(start),'end':str(start+timedelta(days=71)),
                       'role':'cost_only_outside_native_forecast','basis':'Synthetic final72days','source_refs':['synthetic']})
    rows=project(c)['periods']
    for q,row in zip(s['quarters'],rows):
        q['segments']['corporate']['operating_profit']+=row['operating_profit']-13
        q['cash']['depreciation_amortization']=row['total_cash_da']
    s['selected_ppe_runoff']=c;return p


def test_native_ebit_cash_da_and_worker_match_without_new_lease_cash_or_debt():
    from tests.valuation.test_company_model import packet,STAMP
    from quant.valuation.company_model import build
    from quant.ops.agent_workflow import _valuation_worker_receipt
    old=build(packet(),as_of=STAMP)['scenarios']['base'];new=build(linked_packet(),as_of=STAMP)['scenarios']['base']
    for q,prior in zip(new['quarters'],old['quarters']):
        assert q['cash']['ending_liquidity']==prior['cash']['ending_liquidity']
        assert q['cash']['ending_debt']==prior['cash']['ending_debt'] and q['closing_shares']==prior['closing_shares']
        assert q['operating_profit']==-75 and q['cash']['total_capex']==prior['cash']['total_capex']
    assert new['operating_value']['status']=='unknown' and new['business_equity']['status']=='unknown'
    receipt={'receipt_hash':'synthetic','tickers':{'SYN':{'company_research':{'scenarios':{'base':new}}}}}
    small=_valuation_worker_receipt(receipt)['tickers']['SYN']['company_research']['scenarios']['base']['selected_ppe_runoff']
    assert 'periods' not in small and small['native_ending_pool']['remaining_selected_net_ROU']==72
    assert small['full_ledger_ending_pool']['remaining_selected_net_ROU']==0
    assert small['receipt_hash']==new['selected_ppe_runoff']['receipt_hash']


@pytest.mark.parametrize('mutation',['income','cash_da','opening','periods','future_evidence','missing_evidence','full_DCF_claim'])
def test_host_rejects_unpaired_or_unavailable_selected_physical_cost(mutation):
    from tests.valuation.test_company_model import STAMP
    from quant.valuation.company_model import build
    p=linked_packet();s=p['scenarios'][0];c=s['selected_ppe_runoff']
    if mutation=='income':s['quarters'][0]['segments']['corporate']['operating_profit']+=1
    elif mutation=='cash_da':s['quarters'][0]['cash']['depreciation_amortization']+=1
    elif mutation=='opening':c['date']='2026-09-07'
    elif mutation=='periods':c['periods'][0]['end']=s['quarters'][1]['end']
    elif mutation=='future_evidence':c['known_at']='2026-09-09T00:00:00Z'
    elif mutation=='missing_evidence':c['periods'][0]['income_cash_binding']['source_refs']=['absent']
    elif mutation=='full_DCF_claim':p.pop('operating_path_hold')
    with pytest.raises((ValueError,KeyError)):build(p,as_of=STAMP)


def test_physical_and_acquired_cash_populations_must_match_even_if_both_totals_match():
    from tests.valuation.test_company_model import STAMP
    from quant.valuation.company_model import build
    p=linked_packet();s=p['scenarios'][0];c=s['selected_ppe_runoff']
    e={k:s[k] for k in ['known_at','source_refs','basis']}
    amort={**e,'currency':'USD','scale':'millions','date':s['opening_date'],
        'opening_existing_amortizable':8,'opening_iprd':0,'reported_total':8,
        'new_acquisitions_assumption':'none_in_projection','impairment_assumption':'none_in_projection',
        'iprd_readiness':{'status':'not_ready_in_projection','basis':'No synthetic IPRD'},'periods':[]}
    for q,row in zip(s['quarters'],c['periods']):
        row['income_cash_binding']['other_cash_da']=1
        q['cash']['depreciation_amortization']+=1
        amort['periods'].append({'start':q['start'],'end':q['end'],'basis':'Synthetic separate acquired expense',
            'source_refs':['synthetic'],'existing_amortization':1,'other_da':93,
            'operating_profit_before_acquired_amortization':-74,'cash_new_assets':10,
            'new_financed_technology_licenses':0,'technology_license_principal_paid':2})
    s['acquired_intangible_runoff']=amort
    assert build(p,as_of=STAMP)['scenarios']['base']['quarters'][0]['cash']['ending_liquidity'] is not None
    # Both ledgers and cash total agree after this mutation, but the declared
    # physical subtotal differs; the overlap guard must still reject it.
    amort['periods'][0]['other_da']+=1
    c['periods'][0]['income_cash_binding']['other_cash_da']+=1
    s['quarters'][0]['cash']['depreciation_amortization']+=1
    with pytest.raises(ValueError,match='populations overlap or differ'):build(p,as_of=STAMP)
