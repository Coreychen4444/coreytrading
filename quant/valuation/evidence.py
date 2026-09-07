"""Sourced balance-sheet supplements and rate exposures; 零授权效力.

Missing disclosures never imply zero. Supplements fill missing broker fields
in a detached calculation copy; conflicting values are withheld for review.
"""
from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import argparse
from copy import deepcopy
from datetime import date, datetime
import json

from quant.core.agent_contracts import canonical_hash
from quant.core.valuation_contracts import finite
from quant.core.io_json import atomic_write_json, read_json
from quant.data import sec_facts

BRIDGE_FIELDS = ("cash_sti", "lt_investments", "current_debt", "lt_debt")


def timestamp(value):
    if type(value) is not str or "T" not in value:
        raise ValueError("timezone-aware timestamp required")
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("timezone-aware timestamp required")
    return stamp


def supplement(bundle: dict, records: list[dict], *, as_of: str) -> tuple[dict, dict]:
    cutoff = timestamp(as_of)
    updated = deepcopy(bundle)
    receipt = {"applied": [], "rejected": [], "source_bundle_hash": canonical_hash(bundle)}
    grouped = {}
    for record in records:
        try:
            if record.get("ticker") != bundle["ticker"] or record.get("field") not in BRIDGE_FIELDS:
                raise ValueError("supplement_identity_or_field_mismatch")
            if record.get("unit") != "USD" or not finite(record.get("value")) or record["value"] < 0:
                raise ValueError("supplement_requires_nonnegative_usd")
            if not record.get("source") or not record.get("basis") or not record.get("source_hash"):
                raise ValueError("supplement_requires_source_and_mapping_basis")
            available, reviewed = timestamp(record.get("available_at")), timestamp(record.get("reviewed_at"))
            if not available <= reviewed <= cutoff:
                raise ValueError("supplement_not_available_at_cutoff")
            period = record["period_end"]
            date.fromisoformat(period)
            if not any(row.get("period_end") == period for row in updated.get("quarters", [])):
                raise ValueError("supplement_period_mismatch_requires_source_review")
            grouped.setdefault((period, record["field"]), []).append(record)
        except (KeyError, ValueError, TypeError) as exc:
            receipt["rejected"].append({"field": record.get("field"), "reason": str(exc)})
    for (period, field), items in grouped.items():
        # Do not select an arbitrary winner among contradictory attestations.
        values = {r["value"] for r in items}
        target = next(row for row in updated["quarters"] if row.get("period_end") == period)
        existing = target.get(field)
        if len(values) != 1 or (existing is not None and existing not in values):
            target[field] = None
            receipt["rejected"].append({"field": field, "period_end": period, "reason": "conflicting_balance_evidence"})
            continue
        target[field] = items[0]["value"]
        receipt["applied"].append({"field": field, "period_end": period, "value": target[field],
                                    "evidence": deepcopy(items)})
    receipt["receipt_hash"] = canonical_hash(receipt)
    return updated, receipt


def rate_exposure(profile: dict, *, as_of: str, baseline_period: str) -> dict:
    """Derive annual after-tax interest sensitivity per policy basis point.

Balances and pass-through are explicit, after hedges. This narrow earnings
channel says nothing about long yields, ERP, demand or a fixed-debt coupon.
"""
    cutoff = timestamp(as_of)
    if not timestamp(profile.get("known_at")) <= cutoff < timestamp(profile.get("valid_until")):
        raise ValueError("rate_exposure_not_valid_at_cutoff")
    if profile.get("period_end") != baseline_period or not profile.get("source") or not profile.get("basis"):
        raise ValueError("rate_exposure_requires_matching_period_and_source")
    names = ("repricing_assets_usd", "repricing_debt_usd", "asset_pass_through", "debt_pass_through", "tax_rate")
    if any(not finite(profile.get(name)) for name in names):
        raise ValueError("rate_exposure_requires_explicit_finite_balances_and_coefficients")
    if (any(profile[name] < 0 for name in names[:2])
            or any(not 0 <= profile[name] <= 1 for name in names[2:])):
        raise ValueError("rate_exposure_outside_support")
    first, last = profile.get("start_year"), profile.get("end_year")
    if type(first) is not int or type(last) is not int or not 1 <= first <= last <= 30:
        raise ValueError("rate_exposure_requires_explicit_cash_flow_years")
    coefficient = (profile["repricing_assets_usd"] * profile["asset_pass_through"]
                   - profile["repricing_debt_usd"] * profile["debt_pass_through"]) * (1 - profile["tax_rate"]) / 10000
    return {"factor": "policy_rate_bp", "driver": "net_interest_usd", "coefficient": coefficient,
            "start_year": first, "end_year": last, "terminal": False,
            "basis": "annual after-tax repricing assets minus debt, after hedges; " + profile["basis"],
            "source": profile["source"]}


def readiness(bundle: dict | None) -> dict:
    rows = (bundle or {}).get("quarters") or []
    latest = rows[-1] if rows else {}
    forward = (bundle or {}).get("forward") or {}
    return {"bridge_missing": [k for k in BRIDGE_FIELDS if not finite(latest.get(k))],
            "financial_period_end": latest.get("period_end"),
            "financial_availability": "unverified" if any(not r.get("available_at") for r in rows) or not rows else "declared",
            "forward_horizon": forward.get("horizon_months"), "forward_earnings_basis": forward.get("earnings_basis"),
            "historical_accuracy": "not_established_by_data_completeness"}


def forward_at(forward: dict, *, as_of: str) -> tuple[dict, list[str]]:
    """Only dated, sourced, explicit twelve-month forecasts may anchor the model."""
    result = deepcopy(forward)
    if not forward or forward.get("horizon_months") != 12:
        return result, []
    try:
        cutoff = timestamp(as_of)
        available = timestamp(forward.get("available_at"))
        expiry = timestamp(forward.get("valid_until"))
        start, end = date.fromisoformat(forward["period_start"]), date.fromisoformat(forward["period_end"])
        if not available <= cutoff < expiry or (cutoff - available).days > 90:
            raise ValueError("forward_not_current_at_cutoff")
        if not 350 <= (end - start).days <= 380 or abs((start - cutoff.date()).days) > 45:
            raise ValueError("forward_period_not_aligned_to_next_twelve_months")
        if not forward.get("source") or not forward.get("basis"):
            raise ValueError("forward_source_and_accounting_basis_required")
        for field in ("forward_revenue", "forward_eps"):
            if forward.get(field) is not None and not finite(forward[field]):
                raise ValueError("forward_values_must_be_finite")
    except (KeyError, ValueError, TypeError) as exc:
        result["horizon_months"] = None
        return result, ["前瞻数据只作参考: " + str(exc)]
    return result, []


def prepare_sec(bundle: dict, payload: dict, mapping: dict, *, as_of: str) -> dict:
    """Prepare a detached, reviewed SEC supplement; preserve the original cache.

Mappings attest issuer identity, field completeness and disjoint components.
A provider date correction must be explicit and sourced; nearest-date matching
is never performed. No absent XBRL concept is treated as an explicit zero.
"""
    if (mapping.get("ticker") != bundle.get("ticker") or mapping.get("cik") != payload.get("cik")
            or not mapping.get("identity_basis") or not mapping.get("period_basis")):
        raise ValueError("SEC mapping requires matching issuer and reviewed identity/period basis")
    if timestamp(mapping.get("reviewed_at")) > timestamp(as_of):
        raise ValueError("SEC mapping not reviewed by cutoff")
    fields = mapping.get("fields")
    if not isinstance(fields, dict) or not fields or set(fields) - set(BRIDGE_FIELDS):
        raise ValueError("explicit bridge field mappings required")
    concepts = []
    for rule in fields.values():
        if not rule.get("basis") or not isinstance(rule.get("concepts"), list) or not rule["concepts"]:
            raise ValueError("each field requires complete disjoint concept mapping and basis")
        concepts.extend(rule["concepts"])
    if len(concepts) != len(set(concepts)):
        raise ValueError("overlapping SEC concepts would double count bridge fields")
    period = mapping["period_end"]
    date.fromisoformat(period)
    original_period = mapping["provider_period_end"]
    output_bundle = deepcopy(bundle)
    targets = [r for r in output_bundle["quarters"] if r.get("period_end") == original_period]
    if len(targets) != 1:
        raise ValueError("provider period does not identify exactly one quarter")
    if period != original_period and any(r.get("period_end") == period for r in output_bundle["quarters"]):
        raise ValueError("period correction would create duplicate quarter")
    row = targets[0]
    row.update(period_end=period, provider_period_end=original_period,
               period_end_basis=mapping["period_basis"])
    selected, records = {}, []
    for field, rule in fields.items():
        receipt = sec_facts.instant(payload, rule["concepts"], period_end=period, as_of=as_of)
        selected[field] = receipt
        if receipt["status"] == "selected":
            if timestamp(receipt["available_at"]) > timestamp(mapping["reviewed_at"]):
                raise ValueError("mapping review predates selected disclosure")
            records.append({"ticker": bundle["ticker"], "field": field, "value": receipt["value"], "unit": "USD",
                "period_end": period, "available_at": receipt["available_at"], "reviewed_at": mapping["reviewed_at"],
                "source": "https://data.sec.gov/api/xbrl/companyfacts/CIK" + receipt["cik"] + ".json",
                "source_hash": receipt["source_hash"], "basis": rule["basis"], "selection": receipt})
    output_bundle["balance_evidence"] = (output_bundle.get("balance_evidence") or []) + records
    output_bundle["evidence_prepared_at"] = as_of
    output_bundle["sec_mapping"] = deepcopy(mapping)
    # Keep evidence separate; assessment applies it in a temporary calculation copy.
    return {"bundle": output_bundle, "mapping": deepcopy(mapping), "selections": selected,
            "original_bundle_hash": canonical_hash(bundle), "authority": "analysis_only"}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Prepare sourced SEC bridge supplements (零授权效力)")
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--sec-input", type=Path, required=True)
    parser.add_argument("--mapping", type=Path, required=True)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--output", type=Path, help="optional preparation receipt; source cache is not updated")
    args = parser.parse_args(argv)
    result = prepare_sec(read_json(args.bundle), read_json(args.sec_input), read_json(args.mapping), as_of=args.as_of)
    if args.output:
        atomic_write_json(args.output, result)
    print(json.dumps(result, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
