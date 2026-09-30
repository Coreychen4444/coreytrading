"""Finite acquired-intangible runoff and conditional IPRD completion; 零授权效力.

Book asset balances bound amortization, not economic value. Projection inputs
remain assumptions; this ledger does not establish project readiness or price
existing/future technology rights.
"""
from datetime import date, timedelta
from copy import deepcopy
import math

from quant.core.agent_contracts import canonical_hash


def _number(value, name, minimum=0):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < minimum:
        raise ValueError(f'{name}: explicit finite value above minimum required')
    return float(value)


def project(case):
    if case['currency'] != 'USD' or case['scale'] != 'millions':
        raise ValueError('USD millions required for acquired intangible ledger')
    if (case.get('new_acquisitions_assumption') != 'none_in_projection'
            or case.get('impairment_assumption') != 'none_in_projection'):
        raise ValueError('explicit no-new-acquisition and no-impairment scenario assumptions required')
    prior = date.fromisoformat(case['date'])
    existing = _number(case['opening_existing_amortizable'], 'existing amortizable pool')
    iprd = _number(case['opening_iprd'], 'IPRD pool')
    if abs(existing + iprd - _number(case['reported_total'], 'reported acquired assets')) > 1e-6:
        raise ValueError('opening acquired intangible pools must reconcile')
    readiness = case['iprd_readiness']
    scope = case.get('population_scope')
    excluded = case.get('excluded_nonamortizing_assets')
    if scope is not None:
        if (scope != 'definite_lived_intangibles_only' or iprd != 0
                or readiness.get('status') != 'not_ready_in_projection'
                or not isinstance(excluded, list)):
            raise ValueError('finite-only scope excludes IPRD readiness and requires an explicit excluded asset inventory')
        ids = set()
        for asset in excluded:
            if (not asset.get('asset_id') or asset['asset_id'] in ids
                    or asset.get('asset_type') != 'indefinite_lived_intangibles'
                    or asset.get('valuation_status') != 'unresolved'
                    or not asset.get('basis') or not asset.get('source_refs')):
                raise ValueError('excluded nonamortizing assets require unique identity, scope, evidence and unresolved valuation')
            _number(asset['book_balance'], 'excluded nonamortizing book balance')
            ids.add(asset['asset_id'])
    elif excluded is not None:
        raise ValueError('excluded asset inventory requires explicit finite-only scope')
    if not readiness.get('basis') or readiness.get('status') not in ('scenario_assumption', 'not_ready_in_projection'):
        raise ValueError('explicit conditional IPRD readiness basis required')
    if readiness['status'] == 'not_ready_in_projection':
        if readiness.get('ready_date') is not None or readiness.get('life_years') is not None:
            raise ValueError('unready IPRD must not supply an amortization date/life')
        ready, daily = None, 0.0
    else:
        ready = date.fromisoformat(readiness['ready_date'])
        if ready <= prior:
            raise ValueError('future IPRD readiness must follow opening balance date')
        daily = iprd / (_number(readiness['life_years'], 'IPRD life', 1e-12) * 365.25)
    rows = []
    operating_capital_periods = 0
    for row in case['periods']:
        start, end = date.fromisoformat(row['start']), date.fromisoformat(row['end'])
        if start != prior + timedelta(days=1) or not start <= end or (end - start).days > 370:
            raise ValueError('complete consecutive acquired intangible periods required')
        if not row.get('basis') or not row.get('source_refs'):
            raise ValueError('dated period assumptions and source references required')
        charge = _number(row['existing_amortization'], 'existing amortization')
        if charge > existing + 1e-6:
            raise ValueError('amortization exceeds remaining existing acquired asset pool')
        existing = max(0.0, existing - charge)
        days = max(0, (end - max(start, ready)).days + 1) if ready else 0
        iprd_charge = min(iprd, daily * days)
        iprd = max(0.0, iprd - iprd_charge)
        amort = charge + iprd_charge
        other_da = _number(row['other_da'], 'other D&A')
        before = _number(row['operating_profit_before_acquired_amortization'], 'pre-amort operating profit', -math.inf)
        cash_capital = _number(row['cash_new_assets'], 'new assets paid in cash')
        noncash_capital = _number(row['new_financed_technology_licenses'], 'new financed licenses')
        principal = _number(row['technology_license_principal_paid'], 'old license principal paid')
        output = {'start': str(start), 'end': str(end), 'existing_amortization': charge,
                     'iprd_amortization': iprd_charge, 'acquired_amortization': amort,
                     'remaining_existing': existing, 'remaining_iprd': iprd,
                     'operating_profit': before - amort, 'total_da': other_da + amort,
                     'cash_new_assets': cash_capital, 'new_financed_technology_licenses': noncash_capital,
                     'technology_license_principal_paid': principal,
                     'gross_capital_investment': cash_capital + noncash_capital}
        physical = row.get('other_non_cash_operating_capital')
        if 'other_non_cash_operating_capital' in row:
            base_fields = {'basis_type', 'binding_scope', 'new_assets', 'basis', 'source_refs'}
            binding = physical.get('binding_scope') if isinstance(physical, dict) else None
            required = (base_fields | {'lease_principal_paid', 'other_debt_non_cash_change'}
                        if binding == 'native_cash_debt' else base_fields)
            if (not isinstance(physical, dict)
                    or binding not in {'native_cash_debt', 'operating_capital_only'}
                    or set(physical) != required
                    or physical.get('basis_type') != 'new_physical_finance_lease_ROU'
                    or not physical.get('basis')
                    or not isinstance(physical.get('source_refs'), list)
                    or not physical['source_refs']
                    or not all(isinstance(ref, str) and ref for ref in physical['source_refs'])):
                raise ValueError('explicit disjoint physical finance-lease capital and evidence required')
            assets = _number(physical['new_assets'], 'new physical financed assets')
            output.update(other_non_cash_operating_capital=deepcopy(physical),
                          gross_capital_investment=cash_capital+noncash_capital+assets)
            if binding == 'native_cash_debt':
                principal_paid = _number(physical['lease_principal_paid'], 'physical lease principal')
                other_debt = _number(physical['other_debt_non_cash_change'], 'other noncash debt change', -math.inf)
                output['physical_lease_debt_non_cash_change'] = assets-principal_paid+other_debt
        capital = row.get('operating_capital')
        if 'operating_capital' in row:
            required = {'basis_type', 'cashflow_classification', 'cash_investment', 'amortization',
                        'noncash_adjustment_key', 'other_working_capital_cash_change', 'basis', 'source_refs'}
            if (not isinstance(capital, dict) or set(capital) != required
                    or capital['basis_type'] != 'deferred_contract_acquisition_costs'
                    or capital['cashflow_classification'] != 'operating_working_capital'
                    or not isinstance(capital['noncash_adjustment_key'], str)
                    or not capital['noncash_adjustment_key'].strip()
                    or not isinstance(capital['basis'], str) or not capital['basis'].strip()
                    or not isinstance(capital['source_refs'], list) or not capital['source_refs']
                    or not all(isinstance(ref, str) and ref for ref in capital['source_refs'])):
                raise ValueError('explicit deferred contract capital scope, cashflow key and evidence required')
            investment = _number(capital['cash_investment'], 'operating capital cash investment')
            capital_amort = _number(capital['amortization'], 'operating capital amortization')
            other_wc = _number(capital['other_working_capital_cash_change'], 'other operating working capital', -math.inf)
            output.update(operating_capital=deepcopy(capital),
                          dcf_depreciation_amortization=other_da + amort + capital_amort,
                          dcf_working_capital_increase=-other_wc,
                          gross_capital_investment=output['gross_capital_investment'] + investment)
            operating_capital_periods += 1
        rows.append(output)
        prior = end
    if not rows:
        raise ValueError('explicit acquired intangible projection periods required')
    exhausted = case.get('require_exhaustion_before_terminal', False)
    if not isinstance(exhausted, bool) or (exhausted and (existing != 0 or iprd != 0)):
        raise ValueError('declared pre-terminal exhaustion requires fully depleted selected pools')
    result = {'status': 'conditional_research', 'periods': rows, 'input_hash': canonical_hash(case),
              'source_completeness': 'not_established', 'publication': 'withheld',
              'limitations': ['Readiness/life, no new acquisitions or impairment are scenario conditions, not issuer confirmations.',
                              'Other D&A and new license capital need independent population/tax review.',
                              'Book runoff is not a valuation of acquired/IPRD assets; future financing claims remain separate.']}
    if scope is not None:
        result['population_scope'] = scope
        result['excluded_nonamortizing_assets'] = deepcopy(excluded)
        result['limitations'].append('Finite subset only: zero selected IPRD means exclusion, not proof the company has no IPRD. Excluded indefinite assets remain unvalued and unamortized.')
    if exhausted:
        result['require_exhaustion_before_terminal'] = True
    if any('other_non_cash_operating_capital' in row for row in rows):
        result['limitations'].append('Physical finance-lease assets are separate from financed technology licenses; principal settlement is not new capital. Their amounts and disjoint scope remain scenario inputs requiring review.')
    if operating_capital_periods:
        result['operating_capital_scope'] = {
            'basis_type': 'deferred_contract_acquisition_costs',
            'cashflow_classification': 'operating_working_capital',
            'period_count': operating_capital_periods, 'source_completeness': 'not_established'}
        result['limitations'].append('Declared contract acquisition cash moves from quarterly working capital to DCF capital once; its amortization is separately added once. Cohort economics and source coverage remain unverified.')
    result['receipt_hash'] = canonical_hash(result)
    return result
