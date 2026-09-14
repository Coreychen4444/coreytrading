"""Conditional business-value to common-equity bridge at settlement; 零授权效力.

Inputs are explicit values at one date, not automatically book values. Net-settled
warrants are immediate exercise cases, not valuations of unexpired option rights.
No observed market price is needed to solve their circular settlement amounts.
"""
from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from datetime import date
import math

from quant.core.agent_contracts import canonical_hash


def _number(value, name, minimum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name}: finite explicit value required")
    if minimum is not None and value < minimum:
        raise ValueError(f"{name}: below minimum")
    return float(value)


def bridge(case):
    """Full consolidated operating value plus assets, less claims, divided by shares.

    Units: USD millions / shares millions. Each component and claim has a unique
    economic_id across the whole bridge to prevent charging a derivative both as
    a book liability and as dilution. Independence/completeness of the declared
    IDs and business values still needs external review.
    """
    when = date.fromisoformat(case['date'])
    if case['value_basis'] != 'consolidated_operating_value_before_financial_claims':
        raise ValueError('explicit consolidated enterprise-value basis required')
    if case['currency'] != 'USD' or case['scale'] != 'millions':
        raise ValueError('USD millions for money and shares required')
    if not case.get('coverage_basis') or not case.get('share_basis'):
        raise ValueError('business coverage and actual outstanding share basis required')
    shares = _number(case['outstanding_shares'], 'outstanding shares', 1e-12)
    required = case['required_business_ids']
    parts = case['businesses']
    if (not required or len(required) != len(set(required))
            or set(required) != {p['economic_id'] for p in parts} or len(parts) != len(required)):
        raise ValueError('complete nonoverlapping business coverage required')
    ids = set()

    def identify(row):
        key = row['economic_id']
        if not isinstance(key, str) or not key or key in ids:
            raise ValueError('duplicate economic claim/component; possible double count')
        ids.add(key)
        if row['date'] != str(when) or not row.get('basis') or not row.get('source_refs'):
            raise ValueError('dated evidence and value basis required for each component')

    operating = 0.0
    for part in parts:
        identify(part)
        operating += _number(part['value'], 'business value')  # Corporate costs may be negative.
    assets = 0.0
    for asset in case['nonoperating_assets']:
        identify(asset)
        if asset['basis_type'] not in ('excess_unrestricted_cash', 'nonoperating_equity_value'):
            raise ValueError('only excess unrestricted cash or nonoperating equity value may be added')
        assets += _number(asset['value'], 'nonoperating asset', 0)
    claims = case['claims']
    required_claims = case['required_claim_ids']
    if len(required_claims) != len(set(required_claims)) or set(required_claims) != {c['economic_id'] for c in claims}:
        raise ValueError('complete explicit claim inventory required')
    cash_deductions = proceeds = fixed_shares = 0.0
    options, unresolved = [], []
    for claim in claims:
        identify(claim)
        mode = claim['settlement']
        numeric_fields = {'cash', 'new_shares', 'cash_proceeds', 'underlying_shares', 'strike'}
        allowed = {'cash': {'cash'}, 'shares': {'new_shares', 'cash_proceeds'},
                   'net_share_warrant': {'underlying_shares', 'strike'},
                   'net_cash_warrant': {'underlying_shares', 'strike'},
                   'not_triggered': set(), 'unresolved': set()}
        if mode in allowed and numeric_fields.intersection(claim) - allowed[mode]:
            raise ValueError('conflicting settlement fields; share claim cannot also deduct book liability')
        if mode == 'unresolved':
            unresolved.append(claim['economic_id'])
        elif mode == 'cash':
            cash_deductions += _number(claim['cash'], 'cash claim', 0)
        elif mode == 'shares':
            fixed_shares += _number(claim['new_shares'], 'new incremental shares', 0)
            proceeds += _number(claim['cash_proceeds'], 'new share cash proceeds', 0)
            if 'cash' in claim:
                raise ValueError('share-settled claim must not also deduct its book liability')
        elif mode in ('net_share_warrant', 'net_cash_warrant'):
            if claim.get('exercise_basis') != 'immediate_exercise_at_bridge_date':
                raise ValueError('unexpired option time value is not modelled by settlement bridge')
            options.append((claim['economic_id'], mode,
                _number(claim['underlying_shares'], 'warrant shares', 0),
                _number(claim['strike'], 'strike', 0)))
        elif mode == 'not_triggered':
            if claim.get('trigger_satisfied') is not False or not claim.get('trigger_basis'):
                raise ValueError('explicit false trigger and scenario rationale required')
        else:
            raise ValueError('unsupported claim settlement')
    wealth = operating + assets - cash_deductions + proceeds
    out = {'date': str(when), 'operating_value': operating, 'nonoperating_assets': assets,
           'fixed_cash_claims': cash_deductions, 'share_issuance_cash_proceeds': proceeds,
           'outstanding_shares': shares, 'fixed_new_shares': fixed_shares,
           'unresolved_claims': unresolved, 'input_hash': canonical_hash(case),
           'status': 'unknown', 'per_share': None, 'publication': 'withheld', 'validated': False,
           'limitations': ['Conditional value at one date, not a twelve-month market price forecast.',
                          'Completeness, business values, ownership and claim economics require independent review.',
                          'Warrants use immediate exercise only; future optionality/time value is excluded.']}
    if unresolved:
        out['reason'] = 'unresolved_equity_claims'
    elif wealth <= 0:
        out.update(reason='nonpositive_residual_equity_requires_restructuring_model', residual_equity=wealth)
    else:
        def amounts(price):
            net_cash = net_shares = 0.0
            for _, mode, count, strike in options:
                payoff = count * max(price - strike, 0.0)
                if mode == 'net_cash_warrant':
                    net_cash += payoff
                elif price > 0:
                    net_shares += payoff / price
            return net_cash, net_shares

        # Monotone equation: P*(old+fixed+net shares) + net cash payout = wealth.
        low, high = 0.0, wealth / (shares + fixed_shares)
        for _ in range(100):
            price = (low + high) / 2
            cash, dilution = amounts(price)
            if price * (shares + fixed_shares + dilution) + cash > wealth:
                high = price
            else:
                low = price
        price = (low + high) / 2
        cash, dilution = amounts(price)
        out.update(status='research_sensitivity', per_share=price, warrant_cash_settlement=cash,
                   warrant_new_shares=dilution, total_shares=shares + fixed_shares + dilution,
                   residual_equity=wealth - cash,
                   settlement_residual=price * (shares + fixed_shares + dilution) + cash - wealth)
    out['receipt_hash'] = canonical_hash(out)
    return out
