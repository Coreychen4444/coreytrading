"""Content book capital, accruals and operating cash; 零授权效力.

Book stock and aggregate cost assumptions do not value title rights, certify
cohort readiness/lives or price off-balance-sheet commitments. All units are
USD millions. Cash content spending is inside CFO, separate from cash PPE.
"""
from copy import deepcopy
from datetime import date, timedelta
import math

from quant.core.agent_contracts import canonical_hash


def _number(value, name, minimum=None):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or (minimum is not None and value < minimum)):
        raise ValueError(f'{name}: finite explicit content value within bounds required')
    return float(value)


def project(case):
    """Aggregate recognition/cash bridge, explicitly conditional and incomplete."""
    required_case = {'date', 'currency', 'scale', 'scope', 'known_at', 'basis', 'source_refs',
                     'economic_value_status', 'book_is_not_nonoperating_value', 'incremental_contract_claim_id',
                     'assumption_basis', 'content_noncash_key', 'native_period_count',
                     'opening_content_asset_net', 'opening_content_liability', 'periods'}
    if (set(case) != required_case or case['currency'] != 'USD' or case['scale'] != 'millions'
            or case.get('scope') != 'aggregate_content_book_and_operating_cash'
            or case.get('economic_value_status') != 'unresolved'
            or case.get('book_is_not_nonoperating_value') is not True
            or not isinstance(case.get('incremental_contract_claim_id'), str)
            or not case['incremental_contract_claim_id'].strip()
            or not isinstance(case.get('assumption_basis'), str) or not case['assumption_basis'].strip()
            or not isinstance(case.get('content_noncash_key'), str) or not case['content_noncash_key'].strip()):
        raise ValueError('explicit content scope, unresolved economic rights and noncash key required')
    native = case['native_period_count']
    if isinstance(native, bool) or not isinstance(native, int) or native < 1:
        raise ValueError('positive native content quarter count required')
    asset = _number(case['opening_content_asset_net'], 'opening net content assets', 0)
    liability = _number(case['opening_content_liability'], 'opening content liabilities', 0)
    prior, rows, ids = date.fromisoformat(case['date']), [], set()
    required = {'id', 'start', 'end', 'binding_scope', 'basis', 'source_refs',
                'recognized_additions', 'content_amortization', 'liability_operating_cash_change',
                'other_asset_book_change', 'other_liability_book_change',
                'operating_profit_before_content_cost', 'noncontent_DA', 'noncontent_cash_capital',
                'other_working_capital_cash_change'}
    for i, row in enumerate(case['periods']):
        if set(row) != required:
            raise ValueError('exact content capital period schema required')
        start, end = date.fromisoformat(row['start']), date.fromisoformat(row['end'])
        ident = row['id']; days = (end-start).days
        scope = 'native_cash_and_operating_DCF' if i < native else 'operating_DCF_only'
        if (not isinstance(ident, str) or not ident or ident in ids or start != prior+timedelta(days=1)
                or (not 75 <= days <= 105 if i < native else not 364 <= days <= 370)
                or row['binding_scope'] != scope
                or not isinstance(row['basis'], str) or not row['basis'].strip()
                or not isinstance(row['source_refs'], list) or not row['source_refs']
                or not all(isinstance(ref, str) and ref for ref in row['source_refs'])):
            raise ValueError('ordered content periods, native scope and evidence required')
        ids.add(ident)
        additions = _number(row['recognized_additions'], 'recognized content additions', 0)
        amort = _number(row['content_amortization'], 'content cost amortization', 0)
        liab_cash = _number(row['liability_operating_cash_change'], 'content liability cashflow')
        other_asset = _number(row['other_asset_book_change'], 'other content asset book change')
        other_liab = _number(row['other_liability_book_change'], 'other content liability book change')
        other_wc = _number(row['other_working_capital_cash_change'], 'noncontent working cash')
        other_da = _number(row['noncontent_DA'], 'noncontent DA', 0)
        other_capital = _number(row['noncontent_cash_capital'], 'noncontent cash capital', 0)
        before_cost = _number(row['operating_profit_before_content_cost'], 'before content cost operating profit')
        opening_asset, opening_liab = asset, liability
        asset += additions-amort+other_asset
        liability += liab_cash+other_liab
        cash = additions-liab_cash
        if asset < 0 or liability < 0 or cash < 0:
            raise ValueError('content book pool, liability or cash spending below zero')
        rows.append({**deepcopy(row), 'opening_content_asset_net': opening_asset,
                     'closing_content_asset_net': asset, 'opening_content_liability': opening_liab,
                     'closing_content_liability': liability,
                     'net_content_book_capital': asset-liability, 'content_cash_spending': cash,
                     'native_working_capital_cash_change': -cash+other_wc,
                     'operating_profit': before_cost-amort,
                     'dcf_depreciation_amortization': other_da+amort,
                     'dcf_gross_capital_investment': other_capital+additions,
                     'dcf_working_capital_increase': -liab_cash-other_wc})
        prior = end
    if len(rows) < native:
        raise ValueError('content native prefix must contain all declared quarters')
    out = {'status': 'conditional_research', 'scope': case['scope'], 'date': case['date'],
           'economic_value_status': 'unresolved', 'book_is_not_nonoperating_value': True,
           'native_period_count': native, 'content_noncash_key': case['content_noncash_key'],
           'incremental_contract_claim_id': case['incremental_contract_claim_id'],
           'assumption_basis': case['assumption_basis'], 'periods': rows,
           'input_hash': canonical_hash(case), 'source_completeness': 'not_established',
           'publication': 'withheld', 'validated': False,
           'limitations': ['Content book capital is not economic title value or a nonoperating asset to add again.',
                           'Recognition, amortization, content cash and liability accruals are separate; native CFO and DCF investment classifications differ.',
                           'Future recognition, payables, other book movements and cost paths are scenario assumptions, not issuer title-cohort schedules.',
                           'Historic unexplained stock/cash differences remain unassigned; no automatic FX, impairment, incentive or tax attribution.',
                           'Incremental contracts, renewals, title readiness/life, content tax and complete rights economics remain unresolved; this bridge supplies no extra claim deduction.']}
    out['receipt_hash'] = canonical_hash(out)
    return out
