from copy import deepcopy
from datetime import date,timedelta
import pytest
from quant.valuation.company_debt_costs import project


def case():
    return {'currency':'USD','scale':'millions','date':'2027-12-31','maturity_date':'2028-12-31',
        'scope':'opening_note_issuance_costs_only','allocation':'calendar_days_to_maturity_scenario_assumption',
        'allocation_basis':'Synthetic chosen calendar allocation, not effective interest',
        'opening_population_basis':'Synthetic note clean carrying and known face',
        'noncash_adjustment_key':'selected_note_cost','opening_debt_includes_this_population':True,
        'other_noncash_adjustments_exclude_this_cost':True,'no_early_settlement_scenario_assumption':True,
        'nominal_face':100,'opening_carrying':96.34,'opening_unamortized_cost':3.66,'other_opening_debt':20,
        'periods':[{'start':a,'end':b,'basis':'Synthetic quarter','source_refs':['synthetic'],
                    'nonoperating_allocation_basis':'Chosen note cost excluded, other expense not removed',
                    'net_nonoperating_excluding_cost_amortization':5,'other_debt_non_cash_change':.1,
                    'other_debt_principal_repaid':2}
                   for a,b in [('2028-01-01','2028-03-31'),('2028-04-01','2028-06-30'),
                               ('2028-07-01','2028-09-30'),('2028-10-01','2028-12-31')]]}


def test_finite_calendar_pool_leap_year_and_nominal_principal_are_distinct():
    p=case();old=deepcopy(p);r=project(p)
    assert [x['amortization'] for x in r['periods']]==pytest.approx([.91,.91,.92,.92])
    assert r['periods'][-1]['remaining_unamortized_cost']==0
    assert r['periods'][-1]['note_carrying_before_principal_settlement']==100
    assert r['periods'][-1]['debt_principal_repaid']==102
    assert r['periods'][0]['net_nonoperating']==pytest.approx(4.09)
    assert r['periods'][0]['debt_non_cash_change']==pytest.approx(1.01)
    assert p==old and r['publication']=='withheld' and r['source_completeness']=='not_established'


@pytest.mark.parametrize('mutation',['scope','allocation','basis','population_basis','key','debt_excluded',
    'overlap','early_settlement','pool_unknown','pool_bool','pool_negative','face_nan','carrying_above_face',
    'face_identity','past_maturity','period_gap','period_overlap','period_after_maturity','incomplete',
    'row_source','income_unknown','other_change_unknown','other_repayment_negative'])
def test_unknown_or_unbounded_cost_population_rejected(mutation):
    p=case()
    if mutation=='scope':p['scope']='all_debt'
    elif mutation=='allocation':p['allocation']='issuer_effective_interest'
    elif mutation=='basis':p['allocation_basis']=''
    elif mutation=='population_basis':p['opening_population_basis']=''
    elif mutation=='key':p['noncash_adjustment_key']=''
    elif mutation=='debt_excluded':p['opening_debt_includes_this_population']=False
    elif mutation=='overlap':p['other_noncash_adjustments_exclude_this_cost']=False
    elif mutation=='early_settlement':p['no_early_settlement_scenario_assumption']=False
    elif mutation=='pool_unknown':p['opening_unamortized_cost']=None
    elif mutation=='pool_bool':p['opening_unamortized_cost']=True
    elif mutation=='pool_negative':p['opening_unamortized_cost']=-1
    elif mutation=='face_nan':p['nominal_face']=float('nan')
    elif mutation=='carrying_above_face':p['opening_carrying']=101
    elif mutation=='face_identity':p['nominal_face']=101
    elif mutation=='past_maturity':p['maturity_date']=p['date']
    elif mutation=='period_gap':p['periods'][0]['start']='2028-01-02'
    elif mutation=='period_overlap':p['periods'][1]['start']='2028-03-31'
    elif mutation=='period_after_maturity':p['periods'][-1]['end']='2029-01-01'
    elif mutation=='incomplete':p['periods'].pop()
    elif mutation=='row_source':p['periods'][0]['source_refs']=[]
    elif mutation=='income_unknown':p['periods'][0]['net_nonoperating_excluding_cost_amortization']=None
    elif mutation=='other_change_unknown':p['periods'][0]['other_debt_non_cash_change']=None
    elif mutation=='other_repayment_negative':p['periods'][0]['other_debt_principal_repaid']=-1
    with pytest.raises(ValueError):project(p)


def linked_packet():
    from tests.valuation.test_company_model import packet
    p=packet();s=p['scenarios'][0];ledger=case()
    ledger.update(date=s['opening_date'],maturity_date='2029-09-05',nominal_face=10,
                  opening_carrying=8,opening_unamortized_cost=2,other_opening_debt=12,
                  known_at=s['known_at'],source_refs=s['source_refs'],basis=s['basis'],periods=[])
    for q in s['quarters']:
        ledger['periods'].append({'start':q['start'],'end':q['end'],'basis':'Synthetic linked quarter',
            'source_refs':['synthetic'],'nonoperating_allocation_basis':'Chosen quarter net excludes finite note cost',
            'net_nonoperating_excluding_cost_amortization':-1.75,'other_debt_non_cash_change':0,
            'other_debt_principal_repaid':q['cash']['debt_repaid']})
    ledger['periods'].append({**ledger['periods'][-1],
        'start':str(date.fromisoformat(s['quarters'][-1]['end'])+timedelta(days=1)),
        'end':ledger['maturity_date']})
    rows=project(ledger)['periods']
    for q,r in zip(s['quarters'],rows):
        for k in ['interest_income','interest_expense','other_nonoperating']:q['income'].pop(k)
        q['income'].update(nonoperating_basis='combined_net',net_nonoperating=r['net_nonoperating'],
                          nonoperating_definition='Synthetic finite expense with unchanged other components')
        q['cash']['debt_non_cash_change']=r['debt_non_cash_change']
        q['cash']['noncash_adjustments'][ledger['noncash_adjustment_key']]=r['amortization']
    s['debt_cost_runoff']=ledger
    return p


def test_host_cost_expense_addback_book_debt_and_worker_all_linked_without_new_finance_cash():
    from tests.valuation.test_company_model import STAMP,packet
    from quant.valuation.company_model import build
    from quant.ops.agent_workflow import _valuation_worker_receipt
    old=build(packet(),as_of=STAMP)['scenarios']['base'];p=linked_packet();r=build(p,as_of=STAMP)['scenarios']['base']
    for i,(q,oq,cost) in enumerate(zip(r['quarters'],old['quarters'],r['debt_cost_runoff']['periods']),1):
        assert q['operating_profit']==oq['operating_profit']
        assert q['cash']['total_capex']==oq['cash']['total_capex']
        assert q['cash']['ending_liquidity']-oq['cash']['ending_liquidity']==pytest.approx(.25*i)
        assert q['cash']['ending_debt']-oq['cash']['ending_debt']==pytest.approx(2-cost['remaining_unamortized_cost'])
    receipt={'receipt_hash':'synthetic-full-receipt','tickers':{'SYN':{'company_research':{'scenarios':{'base':r}}}}}
    worker=_valuation_worker_receipt(receipt)['tickers']['SYN']['company_research']['scenarios']['base']['debt_cost_runoff']
    assert 'periods' not in worker and worker['period_count']==9
    assert worker['maturity_remaining_unamortized_cost']==0 and worker['receipt_hash']==r['debt_cost_runoff']['receipt_hash']


@pytest.mark.parametrize('mutation',['income','addback','book','principal','opening','other_opening','period','future_source','missing_row_source'])
def test_host_rejects_unpaired_cost_or_unavailable_evidence(mutation):
    from tests.valuation.test_company_model import STAMP
    from quant.valuation.company_model import build
    p=linked_packet();s=p['scenarios'][0];c=s['debt_cost_runoff'];q=s['quarters'][0]
    if mutation=='income':q['income']['net_nonoperating']+=1
    elif mutation=='addback':q['cash']['noncash_adjustments'][c['noncash_adjustment_key']]+=1
    elif mutation=='book':q['cash']['debt_non_cash_change']+=1
    elif mutation=='principal':q['cash']['debt_repaid']+=1
    elif mutation=='opening':c['date']='2026-09-07'
    elif mutation=='other_opening':c['other_opening_debt']+=1
    elif mutation=='period':c['periods'][0]['end']=s['quarters'][1]['end']
    elif mutation=='future_source':c['known_at']='2026-09-09T08:00:00+00:00'
    elif mutation=='missing_row_source':c['periods'][0]['source_refs']=['absent']
    with pytest.raises((ValueError,KeyError)):build(p,as_of=STAMP)
