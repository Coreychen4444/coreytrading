"""Build daily event-follow-through samples from explicit evidence; 零授权效力.

The anchor is the first regular close after information was known. Daily bars
cannot identify the announcement-minute jump; this builder deliberately labels
only subsequent close-to-close returns. It performs no news or price collection.
"""
from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import argparse
from datetime import timedelta
import json
import math

from quant.core.agent_contracts import canonical_hash
from quant.core.io_json import atomic_write_json, read_json
from quant.core.market_sessions import ET, calendar_metadata, session_close
from quant.core.valuation_contracts import finite
from quant.valuation.evidence import timestamp

ANCHOR_CONVENTION = "first_close_after_information"
FEATURE_SCHEMA = "policy_surprise_bp:actual_change_minus_prior_expectation:v1"


def _closes_after(stamp, count, *, inclusive=False):
    day, result = stamp.astimezone(ET).date(), []
    while len(result) < count:
        close = session_close(day)
        if close is not None and (close > stamp or inclusive and close == stamp):
            result.append((day.isoformat(), close))
        day += timedelta(days=1)
    return result


def anchor_after_information(known_at: str):
    return _closes_after(timestamp(known_at), 1, inclusive=True)[0]


def policy_features(event: dict) -> dict:
    occurred = timestamp(event.get("event_at"))
    known = timestamp(event.get("known_at"))
    expected = event.get("expectation") or {}
    if timestamp(expected.get("known_at")) >= occurred or known < occurred:
        raise ValueError("policy_expectation_must_precede_event_and_actual_follow_it")
    if not expected.get("source") or not event.get("source"):
        raise ValueError("policy_actual_and_expectation_sources_required")
    actual, prior = event.get("actual_change_bp"), expected.get("change_bp")
    if not finite(actual) or not finite(prior) or max(abs(actual), abs(prior)) > 1000:
        raise ValueError("policy_changes_require_finite_basis_points")
    return {"policy_surprise_bp": actual - prior}


def build(events: list[dict], prices: dict, *, as_of: str) -> dict:
    """Reconstruct horizons using XNYS and original vendor availability times.

Prices contain ticker, source, price_basis and bars with session/close/available_at.
All prices must share one split-adjustment basis. Missing endpoint bars are never
replaced by the next available observation or a forward-filled close.
"""
    cutoff = timestamp(as_of)
    if prices.get("price_basis") != "split_adjusted_price" or not prices.get("source") or not prices.get("ticker"):
        raise ValueError("explicit ticker/source/split-adjusted prices required")
    bars, invalid = {}, set()
    for bar in prices.get("bars") or []:
        day = bar.get("session")
        if not isinstance(day, str):
            continue
        try:
            close = session_close(day)
            available = timestamp(bar.get("available_at"))
            if close is None or available < close or not finite(bar.get("close")) or bar["close"] <= 0:
                raise ValueError("invalid_bar")
            if day in bars or day in invalid:
                raise ValueError("duplicate_bar")
            bars[day] = {**bar, "available": available}
        except (ValueError, TypeError):
            invalid.add(day)
            bars.pop(day, None)
    output = {"as_of": as_of, "events": [], "rejected": [], "pending": [],
              "price_hash": canonical_hash(prices), "calendar": calendar_metadata(),
              "anchor_convention": ANCHOR_CONVENTION, "feature_schema": FEATURE_SCHEMA,
              "scope": "subsequent_daily_follow_through; announcement_jump_not_measured",
              "authority": "analysis_only"}
    ids = set()
    for event in events:
        identity = event.get("event_id")
        try:
            if not isinstance(identity, str) or not identity or identity in ids:
                raise ValueError("duplicate_or_missing_event_id")
            ids.add(identity)
            if event.get("ticker") != prices["ticker"]:
                raise ValueError("event_price_ticker_mismatch")
            features = policy_features(event)
            known = timestamp(event["known_at"])
            if known > cutoff:
                raise ValueError("event_not_known_at_cutoff")
            anchor_day, anchor = anchor_after_information(event["known_at"])
            anchor_bar = bars.get(anchor_day)
            if not anchor_bar or anchor_bar["available"] > cutoff:
                raise ValueError("anchor_price_missing_or_unavailable")
            record = {"event_id": identity, "ticker": event["ticker"], "event_at": event["event_at"],
                      "observed_at": anchor.isoformat(), "known_at": event["known_at"],
                      "features": features, "feature_definition": "pre_anchor_information",
                      "feature_schema": FEATURE_SCHEMA, "anchor_convention": ANCHOR_CONVENTION,
                      "source": event["source"], "event_evidence": event,
                      "return_basis": "split_adjusted_price", "anchor_price": anchor_bar["close"],
                      "price_source": prices["source"], "price_hash": output["price_hash"], "returns": {}}
            future = _closes_after(anchor, 20)
            for horizon in (1, 5, 20):
                day, end = future[horizon - 1]
                endpoint = bars.get(day)
                # Require the entire horizon: a suspension/gap is not normal session exposure.
                required = [bars.get(d) for d, _ in future[:horizon]]
                if end > cutoff or endpoint is None or any(b is None for b in required):
                    output["pending"].append({"event_id": identity, "horizon": horizon,
                                               "reason": "future_or_missing_session_bars"})
                    continue
                available = max([anchor_bar["available"]] + [b["available"] for b in required])
                if available > cutoff:
                    output["pending"].append({"event_id": identity, "horizon": horizon,
                                               "reason": "outcome_not_available_at_cutoff"})
                    continue
                record["returns"][str(horizon)] = {"sessions": horizon,
                    "log_return": math.log(endpoint["close"] / anchor_bar["close"]),
                    "ends_at": end.isoformat(), "available_at": available.isoformat()}
            output["events"].append(record)
        except (KeyError, ValueError, TypeError) as exc:
            output["rejected"].append({"event_id": identity, "reason": str(exc)})
    output["receipt_hash"] = canonical_hash(output)
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=Path, required=True, help="explicit event evidence JSON array")
    parser.add_argument("--prices", type=Path, required=True, help="dated split-adjusted daily price package")
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--output", type=Path, help="optional research build receipt")
    args = parser.parse_args(argv)
    result = build(read_json(args.events), read_json(args.prices), as_of=args.as_of)
    if args.output:
        atomic_write_json(args.output, result)
    print(json.dumps(result, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
