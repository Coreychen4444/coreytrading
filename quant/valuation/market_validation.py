"""Preregistered twelve-calendar-month price evaluation; 零授权效力.

Descriptive diagnostics only. Never converts an intrinsic scenario envelope to a
probability interval or approves publication. Each prediction binds its protocol.
"""
from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from datetime import timedelta, timezone
from statistics import mean

from quant.core.agent_contracts import canonical_hash
from quant.core.market_sessions import ET, session_close
from quant.valuation.company_model import number, stamp


def target_close(generated_at):
    day = stamp(generated_at).astimezone(ET).date()
    try:
        anniversary = day.replace(year=day.year + 1)
    except ValueError:  # Feb 29 -> Feb 28 next year.
        anniversary = day.replace(year=day.year + 1, day=28)
    for offset in range(8):
        end = session_close(anniversary + timedelta(days=offset))
        if end is not None:
            return end
    raise ValueError('target session unavailable')


def _hash(value):
    return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def _score(low, high, actual, alpha, scale):
    return ((high-low) + 2/alpha*max(low-actual, 0) + 2/alpha*max(actual-high, 0))/scale


def evaluate(spec, forecasts, outcomes, *, as_of):
    cutoff, registered = stamp(as_of), stamp(spec['registered_at'])
    if registered > cutoff or spec['target_convention'] != 'first_xnys_close_on_or_after_twelve_calendar_months':
        raise ValueError('dated fixed target protocol required')
    if not spec.get('tickers') or not spec.get('baseline_interval_definition'):
        raise ValueError('ticker scope and frozen baseline interval rule required')
    if spec['price_basis'] != 'split_adjusted_price_on_forecast_share_basis_excluding_dividends':
        raise ValueError('explicit price/split basis required')
    alpha = number(spec['alpha'], 'alpha')
    baseline_width = number(spec['baseline_halfwidth_fraction'], 'baseline halfwidth')
    if not 0 < alpha < 1:
        raise ValueError('alpha must be a probability fraction')
    if spec['baseline_kind'] != 'constant_price_fixed_fraction' or not 0 < baseline_width < 1:
        raise ValueError('fixed preregistered constant-price interval baseline required')
    spec_hash = canonical_hash(spec)
    actuals = {}
    for row in outcomes:
        if row['ticker'] not in spec['tickers']:
            continue
        end, known, captured = stamp(row['target_close']), stamp(row['available_at']), stamp(row['captured_at'])
        if known > cutoff or captured > cutoff:
            continue
        if not end <= known <= captured or not _hash(row['source_hash']):
            raise ValueError('outcome availability/source invalid')
        key = (row['ticker'], end, row['share_basis_id'])
        if key in actuals:
            raise ValueError('duplicate/revised outcomes require explicit vintage selection')
        actuals[key] = row
    ids, history, scored, excluded, pending, abstained = set(), {}, [], [], [], []
    for row in sorted(forecasts, key=lambda x: (stamp(x['generated_at']), x['forecast_id'])):
        identity = row['forecast_id']
        if identity in ids:
            raise ValueError('duplicate forecast id')
        ids.add(identity)
        made = stamp(row['generated_at'])
        if (row['ticker'] not in spec['tickers'] or row['model_version'] != spec['model_version']
                or row.get('spec_hash') != spec_hash or not registered <= made <= cutoff):
            excluded.append({'forecast_id': identity, 'reason': 'outside_frozen_protocol'})
            continue
        end = target_close(row['generated_at'])
        if stamp(row['target_close']) != end or row['price_basis'] != spec['price_basis']:
            raise ValueError('prediction horizon or price basis mismatch')
        if (not _hash(row.get('input_hash')) or not _hash(row.get('receipt_hash'))
                or not row.get('share_basis_id')):
            raise ValueError('frozen input/receipt/share basis required')
        previous = history.setdefault(row['ticker'], [])
        if any(made <= last for last in previous):
            excluded.append({'forecast_id': identity, 'reason': 'overlapping_outcome_window'})
            continue
        previous.append(end)
        if row['status'] == 'abstained':
            abstained.append(identity)
            continue
        if row['status'] != 'forecast' or row['interval_kind'] != 'predictive':
            raise ValueError('scenario extrema cannot be scored as a predictive interval')
        if row.get('alpha') != alpha:
            raise ValueError('prediction nominal interval level differs from protocol')
        if row['baseline_interval_definition'] != spec['baseline_interval_definition']:
            raise ValueError('baseline rule changed after registration')
        price = number(row['anchor_price'], 'anchor price', minimum=1e-12)
        anchor = stamp(row['anchor_as_of'])
        captured = stamp(row['anchor_captured_at'])
        if (not 0 <= (made-anchor).total_seconds() <= 4*86400 or not anchor <= captured <= made
                or not _hash(row.get('anchor_source_hash'))):
            raise ValueError('dated anchor price required')
        low, point, high, bl, bh = [number(row[k], k, minimum=0) for k in
                                  ['low','point','high','baseline_low','baseline_high']]
        if abs(bl-price*(1-baseline_width)) > 1e-9 or abs(bh-price*(1+baseline_width)) > 1e-9:
            raise ValueError('baseline bounds differ from frozen baseline formula')
        if not 0 < low <= point <= high or not 0 < bl <= price <= bh:
            raise ValueError('ordered positive forecast and baseline intervals required')
        actual = actuals.get((row['ticker'], end, row['share_basis_id']))
        if not actual:
            pending.append(identity)
            continue
        if actual['price_basis'] != spec['price_basis']:
            raise ValueError('outcome price basis mismatch')
        realized = number(actual['price'], 'actual price', minimum=0)
        scored.append({'forecast_id': identity, 'ticker': row['ticker'], 'target_close': end.isoformat(),
                       'normalized_absolute_error': abs(point-realized)/price,
                       'flat_price_baseline_error': abs(price-realized)/price,
                       'covered': low <= realized <= high, 'normalized_width': (high-low)/price,
                       'interval_score': _score(low, high, realized, alpha, price),
                       'baseline_interval_score': _score(bl, bh, realized, alpha, price)})
    n = len(scored)
    result = {'as_of': as_of, 'spec_hash': spec_hash, 'sample_count': n,
              'status': 'descriptive_only' if n else 'insufficient_samples',
              'pending': pending, 'excluded': excluded, 'abstained': abstained, 'evaluated': scored,
              'abstention_rate': len(abstained)/(n+len(pending)+len(abstained)) if n+len(pending)+len(abstained) else None,
              'publication': 'withheld', 'acceptance': 'not_determined',
              'limitations': ['Cross-company outcomes may share market shocks; no independent sample claim.',
                             'Metrics do not prove source content, provenance or interval calibration.',
                             'No automatic acceptance threshold; independent review and preregistered statistical decision still required.']}
    for key in ['normalized_absolute_error','flat_price_baseline_error','normalized_width','interval_score','baseline_interval_score']:
        result[key] = mean(r[key] for r in scored) if n else None
    result['coverage'] = mean(r['covered'] for r in scored) if n else None
    result['receipt_hash'] = canonical_hash(result)
    return result
