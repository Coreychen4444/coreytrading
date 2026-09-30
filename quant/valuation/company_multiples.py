"""Observed forward and original reported-year PE research; 零授权效力.

Pure research calculation. A panel range is descriptive, never a confidence
interval, proof of company comparability, or permission to publish a target.
Source clocks and accounting definitions are declarations requiring review.
"""
from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from datetime import date, datetime, timedelta, timezone
import math
import re

from quant.core.agent_contracts import canonical_hash
from quant.core.market_sessions import session_close


def _stamp(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("timezone required")
    return result.astimezone(timezone.utc)


def _positive(value):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value <= 0:
        raise ValueError("positive finite observation required")
    return float(value)


def _source(observation, sources, cutoff, *, clock):
    source = sources[observation["source_ref"]]
    digest = source.get("sha256", "")
    if (not source.get("url") or len(digest) != 64
            or any(c not in "0123456789abcdef" for c in digest)):
        raise ValueError("source URL/content hash required")
    available = _stamp(observation[clock])
    captured = _stamp(observation["captured_at"])
    source_available, source_captured = _stamp(source["available_at"]), _stamp(source["captured_at"])
    if not source_available <= available <= captured <= cutoff or not source_available <= source_captured <= cutoff:
        raise ValueError("observation/source availability or capture mismatch")
    return available


def _quantile(values, fraction):
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    left = int(position)
    right = min(left + 1, len(ordered) - 1)
    return ordered[left] + (ordered[right] - ordered[left]) * (position - left)


def calculate(panel, *, method, ticker, earnings_definition, sources, as_of):
    """Derive PE from dated price/EPS pairs under an explicit sample selection.

    Comparable panels require every named peer exactly once (at least three).
    Historical panels use one ticker and at least eight nonoverlapping forward
    earnings years. These floors prevent trivial panels; they are NOT acceptance
    thresholds. No dropping an expensive peer, winsorising, or market-price fit.
    """
    cutoff = _stamp(as_of)
    if method not in ("comparable_forward_pe", "historical_same_basis_pe"):
        raise ValueError("observed forward PE panel method required")
    selection = panel["selection"]
    registered = _stamp(selection["registered_at"])
    if registered > cutoff or not selection.get("basis"):
        raise ValueError("dated selection rationale required")
    universe = selection["tickers"]
    if (not universe or len(universe) != len(set(universe))
            or any(not isinstance(t, str) or not t.strip() for t in universe)):
        raise ValueError("explicit unique peer/ticker universe required")
    historical = method == "historical_same_basis_pe"
    if historical and universe != [ticker]:
        raise ValueError("historical PE must use the subject company only")
    if not historical and (ticker in universe or len(universe) < 3):
        raise ValueError("at least three independent named peers required")
    start, end = date.fromisoformat(selection["start"]), date.fromisoformat(selection["end"])
    if not start < end or end > cutoff.date():
        raise ValueError("selection dates must end no later than cutoff")
    observations = panel["observations"]
    ids, used_tickers, windows = set(), set(), []
    included, excluded = [], []
    # Fixed earliest-observation selection; ordering in the input cannot select
    # favourable historical outcomes. Malformed rows still get an audit reason.
    for row in sorted(observations, key=lambda x: (str((x.get("price") or {}).get("as_of", "")), str(x.get("id", "")))):
        row_id = row.get("id")
        if not isinstance(row_id, str) or not row_id or row_id in ids:
            raise ValueError("unique observation ids required")
        ids.add(row_id)
        try:
            peer = row["ticker"]
            if peer not in universe:
                raise ValueError("outside_selected_universe")
            price, eps = row["price"], row["eps"]
            when = _source(price, sources, cutoff, clock="as_of")
            known = _source(eps, sources, cutoff, clock="available_at")
            if not start <= when.date() <= end:
                raise ValueError("outside_selected_date_range")
            if known > when:
                raise ValueError("EPS vintage unavailable at price observation")
            if not historical and (cutoff - when).total_seconds() > 7 * 86400:
                raise ValueError("peer price older than seven days")
            if (eps["kind"] not in ("consensus_forecast", "company_guidance", "analyst_forecast")
                    or not eps.get("vintage_basis")):
                raise ValueError("explicit forward estimate vintage required")
            if eps.get("value_origin") != "direct_earnings_forecast":
                raise ValueError("EPS inferred from price/multiple cannot establish an independent panel")
            if eps["earnings_definition"] != earnings_definition:
                raise ValueError("earnings accounting mismatch")
            if (not price.get("currency") or price["currency"] != eps["currency"]
                    or not price.get("share_basis") or price["share_basis"] != eps["share_basis"]):
                raise ValueError("currency or split/share basis mismatch")
            first, last = date.fromisoformat(eps["period_start"]), date.fromisoformat(eps["period_end"])
            if not 350 <= (last - first).days <= 380 or abs((first - when.date()).days) > 45:
                raise ValueError("EPS must cover the forward year at observation")
            if historical and any(first <= b and last >= a for a, b in windows):
                raise ValueError("overlapping historical forward earnings window")
            if not historical and peer in used_tickers:
                # Duplicate peers are a malformed panel, not a silently ignored
                # extra observation that can alter peer weights.
                raise RuntimeError("duplicate comparable peer")
            value = _positive(price["value"]) / _positive(eps["value"])
            if not math.isfinite(value):
                raise ValueError("nonfinite PE ratio")
            if not row.get("comparability_basis"):
                raise ValueError("operating/accounting comparability rationale required")
            included.append({"id": row_id, "ticker": peer, "price_as_of": price["as_of"],
                             "eps_available_at": eps["available_at"], "eps_period_start": str(first),
                             "eps_period_end": str(last), "price": price["value"], "eps": eps["value"],
                             "pe": value, "estimate_kind": eps["kind"],
                             "comparability_basis": row["comparability_basis"],
                             "price_source_ref": price["source_ref"], "eps_source_ref": eps["source_ref"]})
            used_tickers.add(peer)
            windows.append((first, last))
        except RuntimeError as exc:
            raise ValueError(str(exc)) from exc
        except (ValueError, KeyError, TypeError, AttributeError, OverflowError) as exc:
            excluded.append({"id": row_id, "reason": str(exc)})
    minimum = 8 if historical else len(universe)
    missing = sorted(set(universe) - used_tickers)
    eligible = len(included) >= minimum and not missing
    # Exclusions prevent releasing a range: otherwise the set could be chosen
    # by corrupting an inconvenient peer's field. Historical overlap exclusions
    # are still visible and require an explicitly cleaned, reviewed new panel.
    eligible = eligible and not excluded
    values = [r["pe"] for r in included]
    result = {"status": "research_panel" if eligible else "unknown", "method": method,
              "as_of": as_of, "subject_ticker": ticker, "earnings_definition": earnings_definition,
              "panel_hash": canonical_hash(panel), "selection_hash": canonical_hash(selection),
              "sample_count": len(included), "minimum_samples": minimum,
              "missing_tickers": missing, "included": included, "excluded": excluded,
              "low": _quantile(values, .25) if eligible else None,
              "median": _quantile(values, .5) if eligible else None,
              "high": _quantile(values, .75) if eligible else None,
              "range_definition": "unweighted linear-interpolated 25th/75th PE percentiles",
              "selection_registered_at": selection["registered_at"],
              "validated": False, "publication": "withheld",
              "limitations": ["Descriptive panel dispersion, not a target probability interval.",
                              "Matching labels does not establish economic comparability or source truth.",
                              "Current peers are not a forecast of market multiples in twelve months.",
                              "Historical survivorship, regimes and selection timing require separate review."]}
    if not eligible:
        result["reason"] = "complete_valid_independent_multiple_panel_required"
    result["receipt_hash"] = canonical_hash(result)
    return result


def calculate_reported_annual_panel(panel, *, ticker, sources, as_of):
    """Describe last-original-reported-year PE within declared accounting epochs.

    This separate entry point never accepts a forward estimate or creates a
    twelve-month target. Epoch assignment and source provenance are supplied
    evidence requiring independent review, not facts proved by this function.
    The eight-year floor is an engineering history floor, not an accuracy test.
    """
    cutoff = _stamp(as_of)
    selection = panel['selection']
    registered = _stamp(selection['registered_at'])
    if registered > cutoff or not selection.get('basis'):
        raise ValueError('dated reported-year selection rationale required')
    years = selection['fiscal_years']
    if (not years or any(type(y) is not int for y in years)
            or years != list(range(years[0], years[-1] + 1))):
        raise ValueError('complete consecutive fiscal-year universe required')
    if selection['price_rule'] != 'first_xnys_session_of_january_after_fiscal_year':
        raise ValueError('fixed January session rule required')
    epochs, year_epoch = {}, {}
    for epoch in selection['accounting_epochs']:
        identity = epoch['id']
        if not isinstance(identity, str) or not identity or identity in epochs or not epoch.get('basis'):
            raise ValueError('unique explicit accounting epochs required')
        refs = epoch['source_refs']
        if not refs or any(ref not in sources for ref in refs):
            raise ValueError('accounting epoch source references required')
        for ref in refs:
            _source({'source_ref': ref, 'available_at': sources[ref]['available_at'],
                     'captured_at': sources[ref]['captured_at']}, sources, cutoff, clock='available_at')
        members = epoch['fiscal_years']
        if not members or any(type(y) is not int or y not in years or y in year_epoch for y in members):
            raise ValueError('accounting epochs must partition the selected years')
        year_epoch.update({y: identity for y in members})
        epochs[identity] = epoch
    target = selection['target_accounting_epoch']
    if set(year_epoch) != set(years) or target not in epochs:
        raise ValueError('complete accounting partition and target epoch required')

    seen_ids, seen_years, windows = set(), set(), []
    included, excluded = [], []
    for row in sorted(panel['observations'], key=lambda x: str(x.get('id', ''))):
        identity = row.get('id')
        if not isinstance(identity, str) or not identity or identity in seen_ids:
            raise ValueError('unique reported-year observation ids required')
        seen_ids.add(identity)
        try:
            year = row['fiscal_year']
            if type(year) is not int or year not in year_epoch or row['ticker'] != ticker:
                raise ValueError('outside selected company or fiscal years')
            if year in seen_years:
                raise ValueError('duplicate fiscal year cannot reweight history')
            # Claim the year before validating its values, so a second row
            # cannot replace a malformed or inconvenient first observation.
            seen_years.add(year)
            price, eps = row['price'], row['eps']
            when = _source(price, sources, cutoff, clock='as_of')
            known = _source(eps, sources, cutoff, clock='available_at')
            expected = date(year + 1, 1, 1)
            for offset in range(10):
                close = session_close(expected + timedelta(days=offset))
                if close is not None:
                    break
            else:
                raise ValueError('expected January session unavailable')
            if when != close:
                raise ValueError('price does not match the fixed January session close')
            if (price.get('adjustment') != 'none' or price.get('session') != 'RTH'
                    or sources[price['source_ref']].get('kind') != 'serialized_opend_result'):
                raise ValueError('unadjusted regular-session OpenD source required')
            if (eps.get('kind') != 'reported_annual'
                    or eps.get('value_origin') != 'original_reported_financial_statement'
                    or not eps.get('vintage_basis')
                    or sources[eps['source_ref']].get('kind') != 'raw_primary_filing'):
                raise ValueError('direct original reported annual earnings required')
            filing = eps['original_filing']
            if (filing.get('form') != '10-K'
                    or not re.fullmatch(r'\d{10}-\d{2}-\d{6}', filing.get('accession_number', ''))
                    or _stamp(filing['accepted_at']) != known):
                raise ValueError('original filing accession and availability binding required')
            first, last = date.fromisoformat(eps['period_start']), date.fromisoformat(eps['period_end'])
            if (not 350 <= (last - first).days <= 380 or last.year != year
                    or filing['report_date'] != str(last)
                    or not last <= known.date() <= when.date()
                    or not 0 <= (when.date() - last).days <= 90):
                raise ValueError('completed annual period and disclosure lag mismatch')
            if known > when:
                raise ValueError('original earnings unavailable at observed price')
            if any(first <= b and last >= a for a, b in windows):
                raise ValueError('overlapping reported fiscal-year windows')
            if (eps.get('accounting_epoch') != year_epoch[year]
                    or not eps.get('earnings_definition')):
                raise ValueError('reported earnings accounting epoch mismatch')
            if (price.get('currency') != 'USD' or eps.get('currency') != 'USD'
                    or not price.get('share_basis') or price['share_basis'] != eps.get('share_basis')
                    or not row.get('share_normalization_basis') or not row.get('comparability_basis')):
                raise ValueError('explicit USD share normalization and comparability required')
            value = _positive(price['value']) / _positive(eps['value'])
            if not math.isfinite(value):
                raise ValueError('nonfinite reported-year PE')
            included.append({'id': identity, 'fiscal_year': year, 'ticker': ticker,
                             'accounting_epoch': year_epoch[year], 'earnings_definition': eps['earnings_definition'],
                             'price_as_of': price['as_of'], 'price': price['value'], 'eps': eps['value'],
                             'eps_available_at': eps['available_at'], 'period_start': str(first), 'period_end': str(last),
                             'pe': value, 'price_source_ref': price['source_ref'], 'eps_source_ref': eps['source_ref'],
                             'comparability_basis': row['comparability_basis'],
                             'share_normalization_basis': row['share_normalization_basis']})
            windows.append((first, last))
        except (ValueError, KeyError, TypeError, AttributeError, OverflowError) as exc:
            excluded.append({'id': identity, 'fiscal_year': row.get('fiscal_year'), 'reason': str(exc)})
    missing = sorted(set(years) - {r['fiscal_year'] for r in included})
    groups = {key: [r for r in included if r['accounting_epoch'] == key] for key in epochs}
    # A same-epoch label alone is not enough to mix GAAP/adjusted definitions.
    definitions = {r['earnings_definition'] for r in groups[target]}
    complete = not missing and not excluded
    eligible = complete and len(groups[target]) >= 8 and len(definitions) == 1
    values = [r['pe'] for r in groups[target]]
    result = {'method': 'historical_original_reported_annual_pe', 'method_version': 'reported-annual-cycle-v1-research',
              'status': 'research_panel' if eligible else 'unknown', 'as_of': as_of, 'subject_ticker': ticker,
              'panel_hash': canonical_hash(panel), 'selection_hash': canonical_hash(selection),
              'selection_registered_at': selection['registered_at'], 'target_accounting_epoch': target,
              'minimum_history_years': 8, 'sample_count': len(groups[target]), 'all_valid_years': len(included),
              'missing_fiscal_years': missing, 'included': included, 'excluded': excluded,
              'accounting_epoch_counts': {key: len(rows) for key, rows in groups.items()},
              'low': _quantile(values, .25) if eligible else None,
              'median': _quantile(values, .5) if eligible else None,
              'high': _quantile(values, .75) if eligible else None,
              'range_definition': 'Descriptive unweighted 25th/75th percentiles within the declared target accounting epoch only',
              'profit_basis': 'last_original_reported_fiscal_year_not_forward_or_exact_current_TTM',
              'validated': False, 'publication': 'withheld', 'target_12m': None,
              'limitations': ['Source bytes, availability and epoch declarations require independent verification.',
                             'Historical captures now are not original model predictions or blind forecast validation.',
                             'Accounting labels do not prove economic comparability across a company cycle.',
                             'Eight years is an engineering history floor, not a statistical acceptance threshold.',
                             'Reported-year multiples need a separately justified transfer to future earnings.',
                             'Descriptive quartiles are neither confidence bounds nor a twelve-month price prediction.']}
    if not eligible:
        result['reason'] = ('complete_original_history_required' if not complete else
                            'earnings_definitions_differ_within_target_epoch' if len(definitions) != 1 else
                            'insufficient_history_in_selected_accounting_epoch')
    result['receipt_hash'] = canonical_hash(result)
    return result
