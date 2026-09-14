"""Dated macro evidence from frozen captures; no fetching or causal inference.

Availability means a finite, dated observation, not a current market or a proven
mechanism. Comparisons use actual sample endpoints, never vendor change_pct.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import math

from .agent_contracts import canonical_hash


def _time(value):
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return stamp.astimezone(timezone.utc) if stamp.tzinfo is not None else None
    except ValueError:
        return None


def capture_time(document):
    value = document.get("generated_at_utc") or document.get("generated_at") or document.get("captured_at")
    return _time(value.get("utc") if isinstance(value, dict) else value)


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _aligned_rates(observations):
    results = {}
    for name, left, right, meaning in (
        ("curve_2s10s", "US10Y_OFFICIAL", "US2Y", "10Y minus 2Y; not term premium"),
        ("inflation_compensation_10y", "US10Y_OFFICIAL", "US10Y_REAL", "Nominal minus TIPS; includes inflation risk and liquidity compensation"),
    ):
        a = observations.get(left) or observations.get("US10Y", {})
        b = observations.get(right, {})
        result = {"status": "unknown", "value": None, "unit": "bp", "evidence_refs": [],
                  "observed_at": None, "meaning": meaning, "reason": "matching_official_dates_and_units_required"}
        if (a.get("status") == b.get("status") == "dated" and a["observed_at"] == b["observed_at"]
                and a["unit"] == b["unit"] == "percent"
                and a["source"].get("provider") == b["source"].get("provider") == "FRED"
                and a["source"].get("identifier") == "DGS10"
                and b["source"].get("identifier") == ("DGS2" if right == "US2Y" else "DFII10")):
            amount = (a["value"] - b["value"]) * 100
            if math.isfinite(amount):
                result.update(status="computed", value=round(amount, 8), observed_at=a["observed_at"], reason=None,
                              evidence_refs=[a["evidence_ref"], b["evidence_ref"]])
        results[name] = result
    return results


# These groups describe inputs, not sufficient evidence for a macro conclusion.
_COVERAGE = {
    "treasury_curve": (("US2Y", "US10Y", "US30Y"), "Policy path and term premium cannot be identified from yields alone."),
    "real_rate_inflation": (("US10Y_REAL", "US10Y_OFFICIAL"), "Only matching nominal/TIPS tenors and dates support inflation compensation; it is not pure inflation expectations."),
    "credit": (("HY_OAS", "IG_OAS", "HYG", "LQD"), "ETF prices include duration; old OAS cannot confirm a new intraday shock."),
    "funding": (("SOFR_IORB", "EFFR_IORB", "SOFR_EFFR"), "Daily averages do not identify individual funding constraints or intraday stress."),
    "yen": (("USDJPY", "DXY"), "Missing Japan policy path, yen crosses, hedges and investor flows; spot does not identify carry liquidation."),
    "oil": (("WTI", "BRENT"), "Prices do not identify physical supply, demand, inventories or contract-roll effects."),
    "equity_prices": (("SPY", "QQQ", "IWM", "SMH"), "These are price proxies, not constituent breadth or pure factor portfolios."),
    "volatility_prices": (("VIX", "MOVE"), "Different underlyings and methods; no matched IV/RV, skew or dealer positioning."),
}
_STRUCTURE_GAPS = {
    "positioning_leverage": "No standardized gross/net, hedges, margin or investor-level positions in this quote feed.",
    "flows": "No investor net purchases, subscriptions/redemptions or executed buybacks in this quote feed.",
    "momentum_factors": "ETF prices do not supply defined long/short factor returns, holdings or costs.",
    "volatility_pricing": "No matched underlying/expiry/delta option surface and realized-volatility window.",
    "breadth": "No dated constituent universe or member-level participation denominator.",
    "liquidity": "Daily funding averages are partial; no depth, impact, margin or cross-currency basis.",
    "price_structure": "Market sample changes are partial; individual technical levels belong to the technical role.",
    "seasonality": "No predefined seasonal sample, roll calendar or out-of-sample estimate in this feed.",
}


def build_market_context(captures, *, as_of):
    """Build a source-addressable receipt from [{ref, document}] without mutation.

    Capture time is only when the system held this version, never the release
    time of the economic data. Unknown/future captures are excluded, not repaired.
    """
    cutoff = _time(as_of)
    if cutoff is None:
        raise ValueError("macro context requires a timezone-aware as_of")
    eligible, excluded = [], []
    for item in captures:
        document = item["document"]
        at = capture_time(document)
        if at is None or at > cutoff:
            excluded.append({"source_ref": item["ref"], "reason": "capture_time_unknown_or_future"})
        else:
            eligible.append((at, item["ref"], document))
    eligible.sort(key=lambda item: (item[0], item[1]))
    histories = {}
    for captured, source_ref, document in eligible:
        metrics = document.get("metrics")
        if not isinstance(metrics, dict):
            excluded.append({"source_ref": source_ref, "reason": "metrics_missing_or_invalid"})
            continue
        for key, raw in sorted(metrics.items()):
            if not isinstance(raw, dict):
                continue
            source = raw.get("source") if isinstance(raw.get("source"), dict) else {}
            reported = _time(raw.get("as_of"))
            bound = _time(raw.get("observation_time_upper_bound"))
            observed = min(reported, bound) if reported and bound else reported
            reasons = []
            if not _number(raw.get("value")):
                reasons.append("value_missing_or_nonfinite")
            if observed is None or reported > captured:
                reasons.append("observation_time_unknown_or_after_capture")
            if not raw.get("unit") or not source.get("provider") or not source.get("identifier"):
                reasons.append("unit_or_source_identity_missing")
            if source.get("provider") == "OpenD" and not raw.get("timestamp_basis"):
                reasons.append("legacy_opend_clock_unverified")
            precision = "date" if source.get("provider") == "FRED" else "timestamp"
            row = {
                "metric": key, "source_ref": source_ref,
                "value": raw.get("value") if _number(raw.get("value")) else None,
                "unit": raw.get("unit"), "observed_at": observed.isoformat() if observed else None,
                "reported_as_of": raw.get("as_of"), "observation_time_upper_bound": raw.get("observation_time_upper_bound"),
                "timestamp_basis": raw.get("timestamp_basis"), "price_field": raw.get("price_field"),
                "instrument_note": raw.get("instrument_note"),
                "timestamp_precision": precision, "captured_at": captured.isoformat(),
                "source": deepcopy(source), "source_freshness": raw.get("freshness", "unknown"),
                "age_seconds": round((cutoff - observed).total_seconds()) if observed else None,
                "status": "unusable" if reasons else "dated", "issues": reasons,
                "currentness": "not_revalidated", "release_time": None,
                "limitation": "Observation time is not release time. Capture does not refresh the quote or establish a historical vintage.",
            }
            row["evidence_ref"] = "macro_observation:" + canonical_hash(row)
            histories.setdefault(key, []).append(row)
    observations, changes = {}, {}
    for key, history in histories.items():
        latest = history[-1]
        observations[key] = latest
        change = {"status": "unknown", "value": None, "unit": None, "evidence_refs": [],
                  "window_start": None, "window_end": latest["observed_at"],
                  "reason": "no_comparable_distinct_observation",
                  "basis": "two archived observations, not a daily return or an identified event effect"}
        changes[key] = change
        if latest["status"] != "dated":
            change["reason"] = "latest_observation_unusable"
            continue
        identity = (latest["source"].get("provider"), latest["source"].get("identifier"), latest["unit"], latest["price_field"])
        comparable = [row for row in history if row["status"] == "dated" and
                      (row["source"].get("provider"), row["source"].get("identifier"), row["unit"], row["price_field"]) == identity]
        # A changed value at the same observation time is a revision/conflict,
        # never a new market move. Keep versions in the original captures.
        seen = {}
        conflict = False
        for row in comparable:
            if row["observed_at"] in seen and seen[row["observed_at"]]["value"] != row["value"]:
                conflict = True
            seen[row["observed_at"]] = row
        if conflict:
            change["reason"] = "same_timestamp_revision_or_conflict"
            continue
        before = sorted((row for row in seen.values() if _time(row["observed_at"]) < _time(latest["observed_at"])),
                        key=lambda row: _time(row["observed_at"]))
        if not before:
            continue
        previous = before[-1]
        if latest["unit"] in {"percent", "percentage_points"}:
            amount, unit = (latest["value"] - previous["value"]) * 100, "bp"
        elif latest["unit"] == "basis_points":
            amount, unit = latest["value"] - previous["value"], "bp"
        elif latest["unit"] == "index_points":
            amount, unit = latest["value"] - previous["value"], "index_points"
        elif previous["value"] > 0 and latest["value"] > 0:
            amount, unit = (latest["value"] / previous["value"] - 1) * 100, "percent_change"
        else:
            change["reason"] = "relative_change_requires_positive_levels"
            continue
        if not math.isfinite(amount):
            change["reason"] = "nonfinite_change"
            continue
        change.update(status="computed", value=round(amount, 8), unit=unit,
                      evidence_refs=[previous["evidence_ref"], latest["evidence_ref"]],
                      window_start=previous["observed_at"], reason=None,
                      previous_observation=previous)
    coverage = {}
    for name, (keys, limitation) in _COVERAGE.items():
        present = [key for key in keys if observations.get(key, {}).get("status") == "dated"]
        coverage[name] = {"dated_inputs": present, "missing_or_unusable": [key for key in keys if key not in present],
                          "evidence_refs": [observations[key]["evidence_ref"] for key in present],
                          "mechanism_identified": False, "limitation": limitation}
    return {
        "schema_version": "1.0.0", "as_of": as_of, "observations": observations, "sample_changes": changes,
        "aligned_rates": _aligned_rates(observations),
        "coverage": coverage, "quote_feed_structure_gaps": dict(_STRUCTURE_GAPS), "excluded_captures": excluded,
        "authority": "analysis_only", "independent_evidence": False,
        "limitations": ["Coverage describes only the quote feed; frozen narrative reports may contain additional dated evidence.",
                         "Changes across assets may use different windows. Do not infer lead/lag, daily returns, flows or causality.",
                         "Futures samples may span contract rolls. No total return, seasonality or carry-profit calculation is made."],
    }


def context_evidence(context):
    """Return detached observation records, including comparison endpoints."""
    records = {row["evidence_ref"]: deepcopy(row) for row in context["observations"].values()}
    for change in context["sample_changes"].values():
        row = change.get("previous_observation")
        if row:
            records[row["evidence_ref"]] = deepcopy(row)
    return records
