"""Finite selected short-term service expense/cash hypothesis; 零授权效力.

Approximate commitments do not establish execution dates, cash payments,
recognition elections or complete operating-capital scope. No assets or debt
are added by this partial ledger.
"""
from datetime import date,timedelta
from copy import deepcopy
import math
from quant.core.agent_contracts import canonical_hash


def _n(value,name,low=0):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<low:
        raise ValueError(f'{name}: explicit finite amount required')
    return float(value)


def project(case):
    if (case.get('currency')!='USD' or case.get('scale')!='millions'
            or case.get('scope')!='selected_shortterm_service_contract_only'
            or case.get('amount_status')!='approximate_disclosure_as_scenario_cost_and_cash'
            or case.get('recognition_status')!='scenario_expense_no_new_capital_or_debt'
            or case.get('expense_allocation')!='calendar_service_days_scenario_assumption'
            or case.get('other_margin_excludes_selected_contract') is not True
            or case.get('working_capital_and_other_cash_exclude_selected_contract') is not True
            or not case.get('recognition_basis') or not case.get('allocation_basis')
            or not isinstance(case.get('cash_timing_key'),str) or not case['cash_timing_key'].strip()):
        raise ValueError('explicit partial service cost, recognition and nonoverlap hypotheses required')
    opening=date.fromisoformat(case['date']);start=date.fromisoformat(case['service_start']);end=date.fromisoformat(case['service_end'])
    window=case['disclosed_commencement_window']
    wstart,wend=date.fromisoformat(window['start']),date.fromisoformat(window['end'])
    if (not window.get('basis') or not window.get('source_refs') or not wstart<=wend
            or not wstart<=start<=wend or start<=opening or not 1<=(end-start).days+1<=366):
        raise ValueError('chosen future short-term service dates must fit disclosed commencement window')
    amount=_n(case['selected_amount'],'selected amount')
    if amount<=0 or _n(case['opening_selected_timing_balance_assumption'],'opening timing',-math.inf)!=0:
        raise ValueError('positive selected amount and explicit zero opening selected timing hypothesis required')
    inventory=case.get('unresolved_scope')
    if not isinstance(inventory,list) or not inventory:
        raise ValueError('unresolved actual contract/cost scope must remain explicit')
    ids=set()
    for item in inventory:
        if (not isinstance(item,dict) or not item.get('economic_id') or item['economic_id'] in ids
                or item.get('status')!='unresolved' or not item.get('basis') or not item.get('source_refs')):
            raise ValueError('unique unresolved cost scope and evidence required')
        ids.add(item['economic_id'])
    periods=case['periods']
    if not isinstance(periods,list) or not periods:
        raise ValueError('complete forecast cost periods required')
    last=date.fromisoformat(periods[-1]['end'])
    if end>last:
        raise ValueError('forecast must cover entire chosen service-cost window')
    payments=case['payments'];paid=0;names=set();parsed=[]
    if not isinstance(payments,list) or not payments:
        raise ValueError('explicit complete chosen payment schedule required')
    for payment in payments:
        when=date.fromisoformat(payment['due'])
        if (not payment.get('id') or payment['id'] in names or not opening<when<=last
                or not payment.get('basis') or not payment.get('source_refs')):
            raise ValueError('unique dated future payments within forecast and evidence required')
        value=_n(payment['amount'],'payment amount');paid+=value
        names.add(payment['id']);parsed.append((when,value))
    if abs(paid-amount)>1e-6:
        raise ValueError('complete selected cash payments must equal chosen amount')
    prior=opening;remaining=amount;expense_total=cash_total=0;rows=[]
    service_days=(end-start).days+1
    for row in periods:
        a,b=date.fromisoformat(row['start']),date.fromisoformat(row['end'])
        if a!=prior+timedelta(days=1) or not a<=b or (b-a).days>370:
            raise ValueError('complete consecutive contract periods required')
        if not row.get('basis') or not row.get('source_refs'):
            raise ValueError('period cost assumptions and source references required')
        days=max(0,(min(b,end)-max(a,start)).days+1)
        expense=(remaining if days and b>=end else amount*days/service_days)
        remaining=max(0,remaining-expense);expense_total+=expense
        cash=sum(v for when,v in parsed if a<=when<=b);cash_total+=cash
        before=_n(row['operating_profit_before_selected_cost'],'preselected operating profit',-math.inf)
        rows.append({'start':str(a),'end':str(b),'service_days':days,'expense':expense,'cash_paid':cash,
                     'cash_timing_adjustment':expense-cash,'operating_profit':before-expense,
                     'remaining_selected_cost':remaining,'selected_timing_balance':expense_total-cash_total})
        prior=b
    if remaining!=0 or abs(cash_total-amount)>1e-6:
        raise ValueError('chosen service expense and payments must fully exhaust finite scope')
    result={'status':'conditional_research','scope':case['scope'],'date':case['date'],
            'selected_amount':amount,'amount_status':case['amount_status'],
            'recognition_status':case['recognition_status'],'recognition_basis':case['recognition_basis'],
            'service_start':case['service_start'],'service_end':case['service_end'],
            'allocation_basis':case['allocation_basis'],'cash_timing_key':case['cash_timing_key'],
            'unresolved_scope':deepcopy(inventory),'expense_total':expense_total,'cash_total':cash_total,
            'periods':rows,'input_hash':canonical_hash(case),'source_completeness':'not_established',
            'publication':'withheld','limitations':['Approximate commitment and chosen service/cash dates are hypotheses, not verified issuer accounting or payments.',
                'Old margins and working capital exclude this selected cost only as an explicit modelling condition; source nonoverlap is not established.',
                'No new PPE, ROU or debt; full leases, renewals, upfront balances and future capital/cost economics remain unresolved.',
                'Expense-minus-cash replaces selected cash timing once; tax payment timing needs separate review.']}
    result['receipt_hash']=canonical_hash(result)
    return result
