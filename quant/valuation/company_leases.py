"""Conditional runoff of an opening commenced finance-lease population; 零授权效力.

Contractual cash totals and book liabilities are not claim fair values. This
partial ledger does not infer new leases, asset readiness or capital additions.
"""
from datetime import date, timedelta
import math

from quant.core.agent_contracts import canonical_hash


def _number(value, name, minimum=0):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < minimum:
        raise ValueError(f'{name}: explicit finite value above minimum required')
    return float(value)


def project(case):
    if case['currency'] != 'USD' or case['scale'] != 'millions':
        raise ValueError('finance lease ledger requires USD millions')
    if (case.get('scope') != 'opening_commenced_finance_leases_only'
            or case.get('payment_timing') != 'period_end_scenario_assumption'
            or case.get('bucket_allocation') != 'proportional_calendar_days_scenario_assumption'
            or case.get('opening_debt_excludes_this_population') is not True):
        raise ValueError('explicit partial lease scope, payment timing, allocation and debt exclusion required')
    if not case.get('interest_basis') or case.get('interest_cash_equals_accrual') is not True:
        raise ValueError('explicit lease interest proxy and cash-accrual scenario required')
    rate = _number(case['annual_effective_rate'], 'lease effective rate')
    if rate >= 1:
        raise ValueError('lease effective rate must be below one')
    prior = date.fromisoformat(case['date'])
    balance = _number(_number(case['opening_current'], 'current lease liability') + _number(case['opening_noncurrent'], 'noncurrent lease liability'), 'combined opening lease liability')
    if abs(balance - _number(case['reported_total'], 'reported lease liability')) > 1e-6:
        raise ValueError('opening lease liability must reconcile')
    buckets = {}
    for bucket in case['payment_buckets']:
        ident = bucket['id']
        start, end = date.fromisoformat(bucket['start']), date.fromisoformat(bucket['end'])
        if ident in buckets or not start <= end or start <= prior:
            raise ValueError('unique future lease payment buckets required')
        if not bucket.get('basis') or not bucket.get('source_refs'):
            raise ValueError('lease payment caption mapping and source references required')
        if any(start <= b['end'] and end >= b['start'] for b in buckets.values()):
            raise ValueError('lease payment buckets overlap')
        buckets[ident] = {'start': start, 'end': end,
                         'cash': _number(bucket['total_cash_payment'], 'lease contractual cash payment')}
    rows = []
    for row in case['periods']:
        start, end = date.fromisoformat(row['start']), date.fromisoformat(row['end'])
        if start != prior + timedelta(days=1) or not start <= end or (end - start).days > 370:
            raise ValueError('complete consecutive lease periods required')
        if not row.get('basis') or not row.get('source_refs'):
            raise ValueError('lease period assumptions and source references required')
        bucket = buckets.get(row['payment_bucket_id'])
        if bucket is None or not bucket['start'] <= start <= end <= bucket['end']:
            raise ValueError('lease period must fit its declared payment bucket')
        days = (end - start).days + 1
        cash = bucket['cash'] * (days / ((bucket['end'] - bucket['start']).days + 1))
        interest = _number(balance * math.expm1(math.log1p(rate) * days / 365.25), 'projected lease interest')
        principal = cash - interest
        if principal < -1e-6:
            raise ValueError('lease payment insufficient for interest; explicit financing required')
        if principal > balance + 1e-6:
            raise ValueError('lease payment exceeds outstanding principal and interest')
        principal = min(balance, max(0.0, principal))
        nonlease = _number(row['net_nonoperating_excluding_this_lease_interest'], 'nonlease net nonoperating', -math.inf)
        if not row.get('nonoperating_allocation_basis'):
            raise ValueError('explicit lease interest income allocation basis required')
        closing = max(0.0, balance - principal)
        rows.append({'start': str(start), 'end': str(end), 'payment_bucket_id': row['payment_bucket_id'],
                     'opening_liability': balance, 'cash_payment': cash, 'interest': interest,
                     'principal': principal, 'closing_liability': closing,
                     'net_nonoperating_excluding_this_lease_interest': nonlease,
                     'net_nonoperating': _number(nonlease - interest, 'projected net nonoperating', -math.inf)})
        balance, prior = closing, end
    if not rows:
        raise ValueError('explicit lease projection periods required')
    result = {'status': 'conditional_research', 'scope': case['scope'], 'periods': rows,
              'input_hash': canonical_hash(case), 'source_completeness': 'not_established',
              'publication': 'withheld',
              'limitations': ['Opening commenced leases only; new leases, prepayments and future capital remain unmodelled.',
                              'Calendar allocation, period-end payments, effective-rate proxy and expense scope are scenario assumptions.',
                              'Principal is financing cash, not new capital; liability runoff is not a claim fair value.']}
    result['receipt_hash'] = canonical_hash(result)
    return result
