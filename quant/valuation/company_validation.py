"""Prospective earnings evaluation with frozen definitions and availability times; 零授权效力.

This module never approves publication. It scores one preregistered scenario and
earnings definition against a frozen simple baseline. No revised-history replay,
price fitting, scenario selection after outcomes, or automatic threshold tuning.
"""
from __future__ import annotations

import sys
from datetime import date, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from quant.core.agent_contracts import canonical_hash
from quant.valuation.company_model import number, stamp


def _digest(value):
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _utc(value):
    return stamp(value).astimezone(timezone.utc)


def _period(row):
    start, end = date.fromisoformat(row["period_start"]), date.fromisoformat(row["period_end"])
    if start >= end:
        raise ValueError("forecast period order invalid")
    return start, end


def evaluate(spec, forecasts, actuals, *, as_of):
    cutoff, registered = _utc(as_of), _utc(spec["registered_at"])
    if registered > cutoff or not spec.get("baseline_definition"):
        raise ValueError("evaluation must be preregistered with a baseline definition")
    tickers = spec.get("tickers")
    if (not isinstance(tickers, list) or not tickers or len(tickers) != len(set(tickers))
            or any(not isinstance(t, str) or not t.strip() for t in tickers)):
        raise ValueError("explicit preregistered ticker scope required")
    spec_hash = canonical_hash(spec)
    minimum = spec["minimum_nonoverlapping_samples"]
    if isinstance(minimum, bool) or not isinstance(minimum, int) or minimum < 1:
        raise ValueError("positive minimum sample count required")
    selected, excluded, pending = [], [], []
    periods, actual_index, ids = {}, {}, set()
    for row in actuals:
        if row["ticker"] not in tickers or row["earnings_definition"] != spec["earnings_definition"]:
            continue
        _, end = _period(row)
        key = (row["ticker"], row["period_start"], row["period_end"], row["earnings_definition"])
        available, captured = _utc(row["available_at"]), _utc(row["captured_at"])
        if available > cutoff or captured > cutoff:
            continue
        if not _digest(row.get("source_hash")) or available.date() < end or captured < available:
            raise ValueError("actual disclosure evidence/time invalid")
        if key in actual_index:
            raise ValueError("duplicate or revised actuals require explicit vintage selection")
        actual_index[key] = row
    for row in sorted(forecasts, key=lambda r: (_utc(r["generated_at"]), r["forecast_id"])):
        forecast_id = row["forecast_id"]
        if forecast_id in ids:
            raise ValueError("duplicate forecast id")
        ids.add(forecast_id)
        if (row["ticker"] not in tickers or row["model_version"] != spec["model_version"] or row["scenario"] != spec["scenario"]
                or row["earnings_definition"] != spec["earnings_definition"]):
            excluded.append({"forecast_id": forecast_id, "reason": "outside_preregistered_scope"})
            continue
        made = _utc(row["generated_at"])
        start, end = _period(row)
        if (not registered <= made <= cutoff or made.date() >= start
                or not _digest(row.get("input_hash")) or not _digest(row.get("receipt_hash"))
                or row.get("spec_hash") != spec_hash
                or row["baseline_definition"] != spec["baseline_definition"]
                or ("baseline_eps" in spec and number(row["baseline_eps"], "baseline EPS") != number(spec["baseline_eps"], "registered baseline EPS"))):
            excluded.append({"forecast_id": forecast_id, "reason": "not_frozen_before_period_or_baseline_mismatch"})
            continue
        key = (row["ticker"], row["period_start"], row["period_end"], row["earnings_definition"])
        history = periods.setdefault((key[0], key[3]), [])
        if any(key[1] <= end and key[2] >= start for start, end in history):
            excluded.append({"forecast_id": forecast_id, "reason": "overlapping_outcome"})
            continue
        history.append((key[1], key[2]))
        if key not in actual_index:
            pending.append(forecast_id)
            continue
        actual = number(actual_index[key]["eps"], "actual EPS")
        predicted = number(row["eps"], "forecast EPS")
        baseline = number(row["baseline_eps"], "baseline EPS")
        selected.append({"forecast_id": forecast_id, "error": predicted - actual,
                         "baseline_error": baseline - actual, "actual_eps": actual})
    n = len(selected)
    mae = sum(abs(r["error"]) for r in selected) / n if n else None
    bias = sum(r["error"] for r in selected) / n if n else None
    baseline_mae = sum(abs(r["baseline_error"]) for r in selected) / n if n else None
    result = {"as_of": as_of, "spec_hash": spec_hash, "evaluation_version": "2.0",
              "tickers": tickers, "sample_count": n,
              "status": "diagnostic" if n >= minimum else "insufficient_samples",
              "mae": mae, "bias": bias, "baseline_mae": baseline_mae,
              "pending": pending, "excluded": excluded, "evaluated": selected,
              "publication": "withheld", "price_accuracy": "not_evaluated",
              "limitations": ["No publication approval from this statistic.",
                              "Hashes bind declared records; source and frozen artifact contents need independent verification.",
                              "Sample count is not proof of representativeness or regime stability."]}
    result["receipt_hash"] = canonical_hash(result)
    return result
