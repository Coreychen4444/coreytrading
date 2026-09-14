"""Observed same-basis forward PE panels, not cash-flow-implied PE; 零授权效力.

Pure research calculation. A panel range is descriptive, never a confidence
interval, proof of company comparability, or permission to publish a target.
Source clocks and accounting definitions are declarations requiring review.
"""
from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from datetime import date, datetime, timezone
import math

from quant.core.agent_contracts import canonical_hash


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
