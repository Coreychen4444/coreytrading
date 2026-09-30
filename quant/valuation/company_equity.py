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
from copy import deepcopy
import math

from quant.core.agent_contracts import canonical_hash


def _number(value, name, minimum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name}: finite explicit value required")
    if minimum is not None and value < minimum:
        raise ValueError(f"{name}: below minimum")
    return float(value)


def promised_payment_component(claim, when, claims):
    """Price one promised-payment component; the full financial claim stays open.

    A chosen yield discounts promised cash, not credit-adjusted expected cash.
    Embedded options, recoveries and other population economics remain in an
    explicitly unresolved companion. This is not a fair-value debt estimator.
    """
    residual = claim.get('unresolved_residual_id')
    companions = [c for c in claims if c['economic_id'] == residual]
    if (len(companions) != 1 or residual == claim['economic_id']
            or companions[0]['settlement'] != 'unresolved'
            or claim.get('component_scope') != 'promised_payments_before_credit_and_embedded_options'
            or claim.get('discount_status') != 'scenario_assumption'
            or claim.get('discount_day_count') != 'ACT_365.25'
            or not isinstance(claim.get('discount_basis'), str)
            or not claim['discount_basis'].strip()):
        raise ValueError('promised cash component requires explicit discount assumptions and unresolved full-claim residual')
    rate = _number(claim['annual_effective_discount_rate'], 'claim discount rate', 0)
    if rate >= 1:
        raise ValueError('claim discount rate must be below one')
    face = _number(claim['remaining_principal'], 'claim remaining principal', 1e-12)
    translation = claim.get('translation')
    local_face = local_principal = 0.0
    if 'translation' in claim:
        required = {'contract_currency', 'valuation_currency', 'contract_remaining_principal',
                    'usd_per_eur', 'status', 'basis', 'source_refs'}
        if (not isinstance(translation, dict) or set(translation) != required
                or translation['contract_currency'] != 'EUR'
                or translation['valuation_currency'] != 'USD'
                or translation['status'] != 'scenario_assumption'
                or not isinstance(translation['basis'], str) or not translation['basis'].strip()
                or not isinstance(translation['source_refs'], list) or not translation['source_refs']
                or not all(isinstance(r, str) and r for r in translation['source_refs'])):
            raise ValueError('explicit EUR/USD scenario translation and evidence required')
        fx = _number(translation['usd_per_eur'], 'scenario USD per EUR', 1e-12)
        local_face = _number(translation['contract_remaining_principal'], 'contract EUR principal', 1e-12)
        if not math.isclose(face, local_face * fx, rel_tol=1e-12, abs_tol=1e-6):
            raise ValueError('USD principal must match contract EUR principal at declared scenario FX')
    maturity = date.fromisoformat(claim['maturity_date'])
    previous, principal, pv, rows, ids = when, 0.0, 0.0, [], set()
    for payment in claim['payments']:
        due = date.fromisoformat(payment['due'])
        ident = payment['id']
        if (not isinstance(ident, str) or not ident or ident in ids
                or not previous < due <= maturity
                or not payment.get('basis') or not payment.get('source_refs')):
            raise ValueError('unique ordered future promised payments with evidence required')
        coupon = _number(payment['coupon'], 'promised coupon', 0)
        redemption = _number(payment['principal'], 'promised principal', 0)
        native = {}
        if translation is not None:
            local_coupon = _number(payment.get('contract_coupon'), 'contract EUR coupon', 0)
            local_redemption = _number(payment.get('contract_principal'), 'contract EUR principal payment', 0)
            if (not math.isclose(coupon, local_coupon * fx, rel_tol=1e-12, abs_tol=1e-6)
                    or not math.isclose(redemption, local_redemption * fx, rel_tol=1e-12, abs_tol=1e-6)):
                raise ValueError('USD payment must match contract EUR amounts at declared scenario FX')
            local_principal += local_redemption
            native = {'contract_coupon': local_coupon, 'contract_principal': local_redemption}
        elif {'contract_coupon', 'contract_principal'}.intersection(payment):
            raise ValueError('native contract payments require explicit translation')
        years = (due - when).days / 365.25
        discount = math.exp(-math.log1p(rate) * years)
        present = (coupon + redemption) * discount
        principal += redemption; pv += present; ids.add(ident); previous = due
        rows.append({'id': ident, 'due': str(due), 'coupon': coupon, 'principal': redemption,
                     'discount_factor': discount, 'present_value': present, **native})
    if not rows or previous != maturity or abs(principal - face) > 1e-6:
        raise ValueError('promised payments must repay remaining principal and finish at maturity')
    if translation is not None and abs(local_principal - local_face) > 1e-6:
        raise ValueError('contract EUR payments must repay contract EUR principal')
    out = {'economic_id': claim['economic_id'], 'date': str(when), 'status': 'research_sensitivity',
           'scope': claim['component_scope'], 'present_value': pv, 'remaining_principal': face,
           'annual_effective_discount_rate': rate, 'discount_basis': claim['discount_basis'],
           'discount_day_count': claim['discount_day_count'], 'payments': rows,
           'unresolved_residual_id': residual, 'source_completeness': 'not_established',
           'publication': 'withheld', 'validated': False,
           'limitations': ['Promised payments only; credit, recovery, embedded options and other claim economics remain unresolved.',
                           'Chosen discount rate is a scenario assumption, not an observed yield or accepted fair value.']}
    if translation is not None:
        out['translation'] = deepcopy(translation)
        out['limitations'].append('Fixed EUR/USD translation is an analyst condition, not spot/forward FX or a valuation of currency hedges and recovery rights.')
    out['receipt_hash'] = canonical_hash(out)
    return out


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

    operating, unresolved_businesses = 0.0, []
    for part in parts:
        identify(part)
        status = part.get('valuation_status', 'valued')
        if status == 'unresolved':
            if 'value' in part:
                raise ValueError('unresolved business must not supply a placeholder value')
            unresolved_businesses.append(part['economic_id'])
        elif status == 'valued':
            operating += _number(part['value'], 'business value')  # Corporate costs may be negative.
        else:
            raise ValueError('explicit valued/unresolved business status required')
    asset_rows = case['nonoperating_assets']
    required_assets = case.get('required_asset_ids')
    if (not isinstance(required_assets, list)
            or len(required_assets) != len(set(required_assets))
            or set(required_assets) != {a['economic_id'] for a in asset_rows}
            or len(asset_rows) != len(required_assets)):
        raise ValueError('complete explicit nonoperating asset inventory required')
    assets, unresolved_assets = 0.0, []
    for asset in asset_rows:
        identify(asset)
        if asset['basis_type'] not in ('excess_unrestricted_cash', 'nonoperating_equity_value',
                                      'nonoperating_financial_asset_value'):
            raise ValueError('only excess unrestricted cash or separately valued nonoperating assets may be added')
        status = asset.get('valuation_status')
        if status == 'unresolved':
            if 'value' in asset:
                raise ValueError('unresolved asset must not supply a placeholder value')
            unresolved_assets.append(asset['economic_id'])
        elif status == 'valued':
            assets += _number(asset['value'], 'nonoperating asset', 0)
        else:
            raise ValueError('explicit valued/unresolved asset status required')
    claims = case['claims']
    required_claims = case['required_claim_ids']
    if len(required_claims) != len(set(required_claims)) or set(required_claims) != {c['economic_id'] for c in claims}:
        raise ValueError('complete explicit claim inventory required')
    cash_deductions = proceeds = fixed_shares = participating_equivalents = 0.0
    has_participating_class = False
    options, unresolved, promised_components = [], [], []
    for claim in claims:
        identify(claim)
        mode = claim['settlement']
        numeric_fields = {'cash', 'new_shares', 'cash_proceeds', 'underlying_shares', 'strike',
                          'participating_shares', 'common_share_ratio'}
        allowed = {'cash': {'cash'}, 'shares': {'new_shares', 'cash_proceeds'},
                   'net_share_warrant': {'underlying_shares', 'strike'},
                   'net_cash_warrant': {'underlying_shares', 'strike'},
                   'pari_passu_equivalents': {'participating_shares', 'common_share_ratio'},
                   'promised_cashflow_component': set(),
                   'not_triggered': set(), 'unresolved': set()}
        if mode in allowed and numeric_fields.intersection(claim) - allowed[mode]:
            raise ValueError('conflicting settlement fields; share claim cannot also deduct book liability')
        if mode == 'unresolved':
            unresolved.append(claim['economic_id'])
        elif mode == 'promised_cashflow_component':
            component = promised_payment_component(claim, when, claims)
            cash_deductions += component['present_value']
            promised_components.append(component)
        elif mode == 'cash':
            cash_deductions += _number(claim['cash'], 'cash claim', 0)
        elif mode == 'shares':
            fixed_shares += _number(claim['new_shares'], 'new incremental shares', 0)
            proceeds += _number(claim['cash_proceeds'], 'new share cash proceeds', 0)
            if 'cash' in claim:
                raise ValueError('share-settled claim must not also deduct its book liability')
        elif mode == 'pari_passu_equivalents':
            # Already issued participating classes allocate residual wealth;
            # they are neither a new financing transaction nor senior debt.
            # EPS weighted-average dilution is not this closing population.
            if (claim.get('participation_basis') != 'pari_passu_dividends_and_liquidation'
                    or claim.get('already_in_common_shares') is not False
                    or claim.get('has_senior_or_redemption_rights') is not False
                    or not claim.get('rights_basis')):
                raise ValueError('separate outstanding pari-passu class and no senior/redemption rights required')
            count = _number(claim['participating_shares'], 'outstanding participating shares', 0)
            ratio = _number(claim['common_share_ratio'], 'common share participation ratio', 1e-12)
            participating_equivalents += count * ratio
            has_participating_class = True
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
    wealth = None if unresolved_businesses else operating + assets - cash_deductions + proceeds
    out = {'date': str(when), 'operating_value': None if unresolved_businesses else operating,
           'nonoperating_assets': None if unresolved_assets else assets,
           'known_nonoperating_assets': assets, 'unresolved_assets': unresolved_assets,
           'fixed_cash_claims': cash_deductions, 'share_issuance_cash_proceeds': proceeds,
           'outstanding_shares': shares, 'fixed_new_shares': fixed_shares,
           'unresolved_claims': unresolved, 'input_hash': canonical_hash(case),
           'status': 'unknown', 'per_share': None, 'publication': 'withheld', 'validated': False,
           'limitations': ['Conditional value at one date, not a twelve-month market price forecast.',
                          'Completeness, business values, ownership and claim economics require independent review.',
                          'Warrants use immediate exercise only; future optionality/time value is excluded.']}
    if has_participating_class:
        out['participating_common_equivalents'] = participating_equivalents
    if promised_components:
        out['promised_payment_components'] = promised_components
    if unresolved_businesses:
        out['known_operating_value'] = operating
        out['unresolved_businesses'] = unresolved_businesses
        out['reason'] = ('unresolved_equity_components' if unresolved_assets or unresolved
                         else 'unresolved_operating_businesses')
    elif unresolved_assets:
        out['reason'] = 'unresolved_equity_components' if unresolved else 'unresolved_nonoperating_assets'
    elif unresolved:
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
        common_units = shares + fixed_shares + participating_equivalents
        low, high = 0.0, wealth / common_units
        for _ in range(100):
            price = (low + high) / 2
            cash, dilution = amounts(price)
            if price * (common_units + dilution) + cash > wealth:
                high = price
            else:
                low = price
        price = (low + high) / 2
        cash, dilution = amounts(price)
        out.update(status='research_sensitivity', per_share=price, warrant_cash_settlement=cash,
                   warrant_new_shares=dilution,
                   residual_equity=wealth - cash,
                   settlement_residual=price * (common_units + dilution) + cash - wealth)
        if has_participating_class:
            out['total_common_units'] = common_units + dilution
            out['allocated_participating_equity'] = price * participating_equivalents
        else:
            out['total_shares'] = common_units + dilution
    out['receipt_hash'] = canonical_hash(out)
    return out
