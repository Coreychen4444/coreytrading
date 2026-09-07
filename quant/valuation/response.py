"""Chronological event-response estimates for 1/5/20 sessions; 零授权效力.

Inputs are owner-produced event surprises and subsequent split-adjusted log
price returns. Daily changes are not relabelled as surprises. Calibration uses
later events than fitting; overlapping outcome windows are purged. Prediction
intervals assume comparable future residuals, not causal identification.
"""
from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import argparse
from datetime import datetime
import json
import math

import numpy as np

from quant.core.agent_contracts import canonical_hash
from quant.core.io_json import atomic_write_json, read_json
from quant.core.valuation_contracts import finite

HORIZONS = (1, 5, 20)
MIN_TRAIN = 40
MIN_CALIBRATION = 20


def _time(value):
    if type(value) is not str or "T" not in value:
        raise ValueError("event time must be a timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("event time must include timezone")
    return parsed


def _samples(records, keys, horizon, cutoff):
    accepted, excluded, seen = [], {}, set()
    for row in records:
        reason = None
        try:
            event_id = row["event_id"]
            if type(event_id) is not str or not event_id or event_id in seen:
                raise ValueError("duplicate_or_missing_event_id")
            observed, known = _time(row["observed_at"]), _time(row["known_at"])
            if known > observed or observed >= cutoff:
                raise ValueError("features_not_available_at_anchor")
            if not row.get("source") or row.get("feature_definition") != "pre_anchor_information":
                raise ValueError("feature_provenance_unverified")
            feature = row["features"]
            if any(k not in feature or not finite(feature[k]) for k in keys):
                raise ValueError("missing_features")
            outcome = row["returns"][str(horizon)]
            if type(outcome.get("sessions")) is not int or outcome["sessions"] != horizon:
                raise ValueError("trading_session_horizon_unverified")
            ends, available = _time(outcome["ends_at"]), _time(outcome["available_at"])
            if ends <= observed or available < ends or available > cutoff:
                raise ValueError("outcome_not_mature")
            if not finite(outcome["log_return"]):
                raise ValueError("invalid_return")
            if row.get("return_basis") != "split_adjusted_price":
                raise ValueError("return_basis_unverified")
            seen.add(event_id)
            accepted.append({"id": event_id, "anchor": observed, "end": ends,
                             "available": available, "x": [feature[k] for k in keys],
                             "y": outcome["log_return"]})
        except (KeyError, ValueError, TypeError) as exc:
            reason = str(exc)
        if reason:
            excluded[reason] = excluded.get(reason, 0) + 1
    accepted.sort(key=lambda r: (r["anchor"], r["id"]))
    # Two records at the same market instant do not supply independent events.
    unique = []
    for row in accepted:
        if unique and row["anchor"] == unique[-1]["anchor"]:
            excluded["duplicate_anchor"] = excluded.get("duplicate_anchor", 0) + 1
        else:
            unique.append(row)
    return unique, excluded


def _nonoverlap(rows):
    kept = []
    for row in rows:
        if not kept or row["anchor"] > kept[-1]["end"]:
            kept.append(row)
    return kept


def forecast(records: list[dict], *, features: dict, spot: float, as_of: str,
             feature_source: str, coverage: float = 0.8, cohort: dict | None = None) -> dict:
    """Fit only mature events; the supplied query is a labelled conditional case."""
    cutoff = _time(as_of)
    if not finite(spot) or spot <= 0 or not 0.5 <= coverage < 1:
        raise ValueError("invalid price or interval coverage")
    if (type(features) is not dict or not features or len(features) > 8
            or any(type(k) is not str or not finite(v) for k, v in features.items()) or not feature_source):
        raise ValueError("query requires finite, sourced features (at most eight)")
    keys = sorted(features)
    # A shared column name does not make tickers, anchors or feature definitions interchangeable.
    identity_keys = ("ticker", "feature_schema", "anchor_convention")
    if cohort is not None:
        if set(cohort) != set(identity_keys) or any(not isinstance(v, str) or not v for v in cohort.values()):
            raise ValueError("cohort requires ticker, feature_schema and anchor_convention")
        records = [r for r in records if all(r.get(k) == v for k, v in cohort.items())]
    else:
        identities = {tuple(r.get(k) for k in identity_keys) for r in records}
        if len(identities) > 1:
            raise ValueError("mixed response cohorts require explicit selection")
    output = {"as_of": as_of, "spot": spot, "features": features, "feature_source": feature_source,
              "coverage_target": coverage, "model": "standardized_ridge_split_conformal",
              "basis": "conditional association; interval coverage not guaranteed under regime change",
              "windows": {}, "dataset_hash": canonical_hash(records), "cohort": cohort,
              "validation_status": "research_only_without_untouched_test",
              "authority": "analysis_only"}
    for horizon in HORIZONS:
        rows, excluded = _samples(records, keys, horizon, cutoff)
        # A fixed later calibration set; purging uses actual ends/availability.
        independent = _nonoverlap(rows)
        n_cal = max(MIN_CALIBRATION, len(independent) // 4)
        calibration = independent[-n_cal:]
        boundary = calibration[0]["anchor"] if calibration else cutoff
        train = [r for r in independent[:-n_cal] if r["end"] < boundary and r["available"] < boundary]
        receipt = {"status": "unknown", "train_n": len(train), "calibration_n": len(calibration),
                   "eligible_n": len(rows), "nonoverlapping_n": len(independent), "excluded": excluded,
                   "price": None, "low": None, "high": None}
        output["windows"][str(horizon)] = receipt
        if len(train) < max(MIN_TRAIN, 10 * len(keys)) or len(calibration) < MIN_CALIBRATION:
            receipt["reason"] = "insufficient_mature_independent_events"
            continue
        x = np.array([r["x"] for r in train], dtype=float)
        y = np.array([r["y"] for r in train], dtype=float)
        center, scale = x.mean(axis=0), x.std(axis=0)
        if np.any(scale < 1e-10):
            receipt["reason"] = "unidentified_constant_factor"
            continue
        query = np.array([features[k] for k in keys], dtype=float)
        if np.any(query < x.min(axis=0)) or np.any(query > x.max(axis=0)):
            receipt["reason"] = "query_outside_observed_support"
            continue
        z = np.column_stack([np.ones(len(x)), (x - center) / scale])
        if np.linalg.matrix_rank(z) < z.shape[1]:
            receipt["reason"] = "collinear_factors"
            continue
        penalty = np.eye(z.shape[1]) * 1.0
        penalty[0, 0] = 0
        beta = np.linalg.solve(z.T @ z + penalty, z.T @ y)
        cx = np.array([r["x"] for r in calibration], dtype=float)
        cy = np.array([r["y"] for r in calibration], dtype=float)
        predictions = np.column_stack([np.ones(len(cx)), (cx - center) / scale]) @ beta
        residuals = np.abs(cy - predictions)
        order = math.ceil((len(residuals) + 1) * coverage)
        if order > len(residuals):
            receipt["reason"] = "insufficient_calibration_for_coverage"
            continue
        radius = float(np.sort(residuals)[order - 1])
        estimate = float(np.r_[1.0, (query - center) / scale] @ beta)
        if max(abs(estimate - radius), abs(estimate + radius)) > 5:
            receipt["reason"] = "prediction_outside_numerical_support"
            continue
        receipt.update(status="estimated", price=spot * math.exp(estimate),
                       low=spot * math.exp(estimate - radius), high=spot * math.exp(estimate + radius),
                       log_return=estimate, residual_radius=radius,
                       coefficients=dict(zip(["intercept"] + keys, map(float, beta))),
                       feature_center=dict(zip(keys, map(float, center))),
                       feature_scale=dict(zip(keys, map(float, scale))),
                       training_last_anchor=train[-1]["anchor"].isoformat(),
                       calibration_first_anchor=calibration[0]["anchor"].isoformat(),
                       calibration_last_outcome=max(r["available"] for r in calibration).isoformat(),
                       calibration_mae=float(residuals.mean()),
                       baseline_calibration_mae=float(np.abs(cy).mean()),
                       validation="calibration diagnostics only; independent test performance not established")
    return output


def walk_forward(records: list[dict], *, feature_names: list[str], test_start: str,
                 as_of: str | None = None, cohort: dict | None = None) -> dict:
    """Untouched chronological test period; re-fit only what was then mature."""
    start = _time(test_start)
    evaluation_cutoff = _time(as_of) if as_of else None
    if evaluation_cutoff is not None and evaluation_cutoff <= start:
        raise ValueError("evaluation cutoff must follow test start")
    if not feature_names or len(feature_names) != len(set(feature_names)):
        raise ValueError("unique feature names required")
    if cohort is not None:
        records = [r for r in records if all(r.get(k) == v for k, v in cohort.items())]
    rows, errors = [], []
    # Normalize offsets before chronological ordering, rather than sorting ISO strings.
    ordered = []
    for row in records:
        try:
            ordered.append((_time(row["observed_at"]), row))
        except (KeyError, ValueError, TypeError) as exc:
            errors.append(str(exc))
    ordered.sort(key=lambda pair: pair[0])
    used_ids = set()
    for anchor, row in ordered:
        try:
            if _time(row["observed_at"]) < start or row["event_id"] in used_ids:
                continue
            if evaluation_cutoff is not None and anchor >= evaluation_cutoff:
                continue
            used_ids.add(row["event_id"])
            if (_time(row["known_at"]) > _time(row["observed_at"])
                    or row.get("feature_definition") != "pre_anchor_information"
                    or row.get("return_basis") != "split_adjusted_price"):
                raise ValueError("test feature provenance unverified")
            result = forecast(records, features={k: row["features"][k] for k in feature_names},
                              spot=1.0, as_of=row["observed_at"], feature_source=row["source"], cohort=cohort)
            for horizon, prediction in result["windows"].items():
                actual = row.get("returns", {}).get(horizon)
                if prediction["status"] != "estimated" or not actual:
                    rows.append({"event_id": row["event_id"], "horizon": int(horizon), "status": "abstained"})
                    continue
                value = actual["log_return"]
                if evaluation_cutoff is not None and _time(actual["available_at"]) > evaluation_cutoff:
                    rows.append({"event_id": row["event_id"], "horizon": int(horizon), "status": "pending"})
                    continue
                if (not finite(value) or _time(actual["ends_at"]) <= _time(row["observed_at"])
                        or _time(actual["available_at"]) < _time(actual["ends_at"])
                        or type(actual.get("sessions")) is not int or actual["sessions"] != int(horizon)):
                    raise ValueError("invalid test outcome")
                error = abs(prediction["log_return"] - value)
                rows.append({"event_id": row["event_id"], "horizon": int(horizon), "status": "scored",
                             "anchor": row["observed_at"], "ends_at": actual["ends_at"],
                             "absolute_error": error, "baseline_absolute_error": abs(value),
                             "covered": error <= prediction["residual_radius"]})
        except (KeyError, ValueError, TypeError) as exc:
            errors.append(str(exc))
    summaries = {}
    for horizon in HORIZONS:
        window = [r for r in rows if r["horizon"] == horizon]
        independent, last_end = [], None
        for row in window:
            if row["status"] != "scored":
                continue
            if last_end is None or _time(row["anchor"]) > last_end:
                independent.append(row)
                last_end = _time(row["ends_at"])
        summaries[str(horizon)] = {"test_n": len(independent), "attempted_n": len(window),
            "abstained_n": sum(r["status"] == "abstained" for r in window),
            "pending_n": sum(r["status"] == "pending" for r in window),
            "overlap_excluded_n": sum(r["status"] == "scored" for r in window) - len(independent),
            "mae": float(np.mean([r["absolute_error"] for r in independent])) if independent else None,
            "baseline_mae": float(np.mean([r["baseline_absolute_error"] for r in independent])) if independent else None,
            "coverage": float(np.mean([r["covered"] for r in independent])) if independent else None}
    result = {"test_start": test_start, "as_of": as_of, "feature_names": feature_names, "cohort": cohort,
              "dataset_hash": canonical_hash(records), "windows": summaries, "rows": rows, "errors": errors,
              "basis": "chronological out-of-sample; zero-return baseline; overlapping test outcomes not pooled"}
    result["receipt_hash"] = canonical_hash(result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only chronological event-response evaluation (零授权效力)")
    parser.add_argument("--input", type=Path, required=True, help="event array or event_data build receipt")
    parser.add_argument("--features", nargs="+", required=True)
    parser.add_argument("--test-start", required=True, help="freeze before viewing the test period")
    parser.add_argument("--as-of", required=True, help="only score outcomes available by this cutoff")
    parser.add_argument("--cohort", type=Path, help="explicit ticker, feature_schema and anchor_convention JSON")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    package = read_json(args.input)
    result = walk_forward(package.get("events", []) if isinstance(package, dict) else package,
                          feature_names=args.features, test_start=args.test_start, as_of=args.as_of,
                          cohort=read_json(args.cohort) if args.cohort else None)
    if args.output:
        atomic_write_json(args.output, result)
    print(json.dumps(result, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
