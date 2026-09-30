from copy import deepcopy
from datetime import date,timedelta
import pytest
from quant.valuation.company_contract_costs import project


def case():
    return {'currency':'USD','scale':'millions','date':'2027-12-31',
        'scope':'selected_shortterm_service_contract_only',
        'amount_status':'approximate_disclosure_as_scenario_cost_and_cash',
        'recognition_status':'scenario_expense_no_new_capital_or_debt',
        'recognition_basis':'Synthetic selected service expensed; actual election unknown',
        'expense_allocation':'calendar_service_days_scenario_assumption','allocation_basis':'Chosen service days',
        'other_margin_excludes_selected_contract':True,
        'working_capital_and_other_cash_exclude_selected_contract':True,
        'cash_timing_key':'selected_service_accrual_less_cash',
        'opening_selected_timing_balance_assumption':0,'selected_amount':121,
        'service_start':'2028-02-01','service_end':'2028-05-31',
        'disclosed_commencement_window':{'start':'2028-01-01','end':'2028-03-31',
                                        'basis':'Synthetic quarter window','source_refs':['synthetic']},
        'unresolved_scope':[{'economic_id':'actual_dates_and_recognition','status':'unresolved',
                             'basis':'Not assumed issuer facts','source_refs':['synthetic']}],
        'payments':[{'id':'upfront','due':'2028-01-01','amount':121,
                     'basis':'Chosen upfront cash, no implied actual payment','source_refs':['synthetic']}],
        'periods':[{'start':a,'end':b,'operating_profit_before_selected_cost':100,
                    'basis':'Chosen corporate earnings scope','source_refs':['synthetic']}
                   for a,b in [('2028-01-01','2028-03-31'),('2028-04-01','2028-06-30')]]}


def test_leap_service_expense_and_upfront_cash_are_separate_finite_populations():
    p=case();old=deepcopy(p);r=project(p)
    assert [q['expense'] for q in r['periods']]==[60,61]
    assert [q['cash_paid'] for q in r['periods']]==[121,0]
    assert [q['cash_timing_adjustment'] for q in r['periods']]==[-61,61]
    assert [q['operating_profit'] for q in r['periods']]==[40,39]
    assert r['periods'][-1]['remaining_selected_cost']==0 and r['periods'][-1]['selected_timing_balance']==0
    assert r['expense_total']==r['cash_total']==121 and r['publication']=='withheld'
    r['unresolved_scope'][0]['basis']='changed';assert p==old


@pytest.mark.parametrize('mutation',['scope','actual_amount','actual_policy','overlap_margin','overlap_cash',
    'unknown_amount','bool_amount','negative_amount','opening_timing','future_start_outside_window',
    'past_service','long_service','unresolved_inventory','duplicate_inventory','missing_inventory',
    'payment_duplicate','payment_negative','payment_future','payment_opening','payment_unknown',
    'payment_source','payment_incomplete','period_gap','incomplete_service','unknown_income'])
def test_unknown_or_unbounded_selected_contract_cost_cannot_pass(mutation):
    p=case()
    if mutation=='scope':p['scope']='all_company_leases'
    elif mutation=='actual_amount':p['amount_status']='issuer_exact_value'
    elif mutation=='actual_policy':p['recognition_status']='issuer_verified_election'
    elif mutation=='overlap_margin':p['other_margin_excludes_selected_contract']=False
    elif mutation=='overlap_cash':p['working_capital_and_other_cash_exclude_selected_contract']=False
    elif mutation=='unknown_amount':p['selected_amount']=None
    elif mutation=='bool_amount':p['selected_amount']=True
    elif mutation=='negative_amount':p['selected_amount']=-1
    elif mutation=='opening_timing':p['opening_selected_timing_balance_assumption']=None
    elif mutation=='future_start_outside_window':p['service_start']='2028-04-01'
    elif mutation=='past_service':p['service_start']='2027-12-31'
    elif mutation=='long_service':p['service_end']='2029-05-31'
    elif mutation=='unresolved_inventory':p['unresolved_scope'][0]['status']='closed'
    elif mutation=='duplicate_inventory':p['unresolved_scope']*=2
    elif mutation=='missing_inventory':p['unresolved_scope']=[]
    elif mutation=='payment_duplicate':p['payments']*=2
    elif mutation=='payment_negative':p['payments'][0]['amount']=-1
    elif mutation=='payment_future':p['payments'][0]['due']='2028-07-01'
    elif mutation=='payment_opening':p['payments'][0]['due']=p['date']
    elif mutation=='payment_unknown':p['payments'][0]['amount']=float('nan')
    elif mutation=='payment_source':p['payments'][0]['source_refs']=[]
    elif mutation=='payment_incomplete':p['payments'][0]['amount']-=1
    elif mutation=='period_gap':p['periods'][1]['start']='2028-04-02'
    elif mutation=='incomplete_service':p['periods'].pop()
    elif mutation=='unknown_income':p['periods'][0]['operating_profit_before_selected_cost']=None
    with pytest.raises(ValueError):project(p)


def linked_packet():
    from tests.valuation.test_company_model import packet
    p=packet();s=p['scenarios'][0];e={k:s[k] for k in ['known_at','source_refs','basis']}
    p['operating_path_hold']={**e,'status':'unestimable','reason':'Synthetic full capital/claims incomplete'}
    c=case();start=s['quarters'][0]['start'];end=s['quarters'][3]['end']
    c.update(e,date=s['opening_date'],selected_amount=40,service_start=start,service_end=end,
             disclosed_commencement_window={'start':start,'end':s['quarters'][0]['end'],
                                            'basis':'Synthetic disclosure window','source_refs':['synthetic']},
             payments=[{'id':'chosen_upfront','due':s['quarters'][0]['end'],'amount':40,
                        'basis':'Chosen cash timing','source_refs':['synthetic']}],periods=[])
    for q in s['quarters']:
        c['periods'].append({'start':q['start'],'end':q['end'],'operating_profit_before_selected_cost':13,
                            'basis':'Preselected expense margin','source_refs':['synthetic']})
    rows=project(c)['periods']
    for q,r in zip(s['quarters'],rows):
        q['segments']['corporate']['operating_profit']-=r['expense']
        q['cash']['income_cash_timing']={'basis':'Selected synthetic contract expense/payment',
            'other_entries_exclude_selected_timing_items':True,'operating_service_expense_is_in_ebit':True,
            'operating_service_expense':r['expense'],'operating_service_cash_paid':r['cash_paid']}
    s['selected_contract_costs']=c;return p


def test_native_cost_and_cash_timing_never_add_debt_or_capital_and_worker_keeps_full_hash():
    from tests.valuation.test_company_model import packet,STAMP
    from quant.valuation.company_model import build
    from quant.ops.agent_workflow import _valuation_worker_receipt
    old=build(packet(),as_of=STAMP)['scenarios']['base'];new=build(linked_packet(),as_of=STAMP)['scenarios']['base']
    for i,(q,oq) in enumerate(zip(new['quarters'],old['quarters'])):
        assert q['operating_profit']==pytest.approx(3 if i<4 else 13)
        assert q['cash']['ending_liquidity']==pytest.approx(oq['cash']['ending_liquidity']-40)
        assert q['cash']['operating_cash_flow']==pytest.approx(oq['cash']['operating_cash_flow']-(40 if i==0 else 0))
        assert q['cash']['ending_debt']==oq['cash']['ending_debt']
        assert q['cash']['total_capex']==oq['cash']['total_capex'] and q['closing_shares']==oq['closing_shares']
    assert new['operating_value']['status']=='unknown'
    full={'receipt_hash':'synthetic','tickers':{'SYN':{'company_research':{'scenarios':{'base':new}}}}}
    small=_valuation_worker_receipt(full)['tickers']['SYN']['company_research']['scenarios']['base']['selected_contract_costs']
    assert 'periods' not in small and small['period_count']==8 and small['expense_total']==small['cash_total']==40
    assert small['ending_selected_balances']['remaining_selected_cost']==0
    assert small['receipt_hash']==new['selected_contract_costs']['receipt_hash']


@pytest.mark.parametrize('mutation',['income','cash_timing','expense_timing','missing_service_expense','missing_service_cash','expense_not_in_ebit','duplicate_other','duplicate_noncash','opening','periods','future_evidence','missing_evidence','remove_hold'])
def test_host_rejects_unpaired_cost_or_unavailable_evidence(mutation):
    from tests.valuation.test_company_model import STAMP
    from quant.valuation.company_model import build
    p=linked_packet();s=p['scenarios'][0];c=s['selected_contract_costs']
    if mutation=='income':s['quarters'][0]['segments']['corporate']['operating_profit']+=1
    elif mutation=='cash_timing':s['quarters'][0]['cash']['income_cash_timing']['operating_service_cash_paid']+=1
    elif mutation=='expense_timing':s['quarters'][0]['cash']['income_cash_timing']['operating_service_expense']+=1
    elif mutation=='missing_service_expense':s['quarters'][0]['cash']['income_cash_timing'].pop('operating_service_expense')
    elif mutation=='missing_service_cash':s['quarters'][0]['cash']['income_cash_timing'].pop('operating_service_cash_paid')
    elif mutation=='expense_not_in_ebit':s['quarters'][0]['cash']['income_cash_timing']['operating_service_expense_is_in_ebit']=False
    elif mutation=='duplicate_other':s['quarters'][0]['cash']['other_cash_flows'][c['cash_timing_key']]=0
    elif mutation=='duplicate_noncash':s['quarters'][0]['cash']['noncash_adjustments'][c['cash_timing_key']]=0
    elif mutation=='opening':c['date']='2026-09-07'
    elif mutation=='periods':c['periods'][0]['end']=s['quarters'][1]['end']
    elif mutation=='future_evidence':c['known_at']='2026-09-09T00:00:00Z'
    elif mutation=='missing_evidence':c['payments'][0]['source_refs']=['absent']
    elif mutation=='remove_hold':p.pop('operating_path_hold')
    with pytest.raises((ValueError,KeyError)):build(p,as_of=STAMP)
