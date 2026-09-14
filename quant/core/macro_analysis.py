"""Structured macro causality and market structure, without trading authority.

These contracts require explicit coverage and source binding. They cannot prove
causal identification, source independence, or the truth of natural language.
Shape validation belongs to agent_contracts; semantic checks below run after it.
"""
from __future__ import annotations

from datetime import datetime

MACRO_RESULT_SCHEMA_VERSION = "1.3.0"
RATE_TENORS = ("ust_2y", "ust_10y", "ust_30y")
RATE_DRIVERS = ("policy_path", "real_yields", "inflation_compensation",
                "term_premium", "treasury_supply_demand")
STRUCTURE_DIMENSIONS = ("positioning_leverage", "flows", "momentum_factors",
                        "volatility_pricing", "breadth", "liquidity",
                        "price_structure", "seasonality")


def _object(properties):
    return {"type": "object", "properties": properties,
            "required": list(properties), "additionalProperties": False}


_TEXT = {"type": "string", "minLength": 1, "maxLength": 2000}
_REFS = {"type": "array", "items": {"type": "string", "minLength": 1, "maxLength": 256},
         "minItems": 0, "maxItems": 20}
_ASSESSMENT = _object({
    "status": {"type": "string", "enum": ["observed", "inferred", "unknown"]},
    "finding": _TEXT,
    "evidence_refs": _REFS,
    "limitation": _TEXT,
})
_INFERENCE = _object({**_ASSESSMENT["properties"],
                      "status": {"type": "string", "enum": ["inferred", "unknown"]}})
_TIME = {"anyOf": [{"type": "null"}, {"type": "string", "format": "date-time"}]}
_BASELINE = _object({**_ASSESSMENT["properties"], "known_at": _TIME})
_UPSTREAM = _object({
    **_INFERENCE["properties"],
    "driver_type": {"type": "string", "enum": ["demand", "supply", "inflation", "policy",
        "fiscal", "funding", "positioning", "mixed", "unknown"]},
    "occurred_at": {**_TIME, "description": "Time of the underlying event itself. If only a notice/report time is known, leave this null; do not copy known_at. For a policy communication event, the communication itself may be dated."},
    "known_at": {**_TIME, "description": "When the source information became knowable, distinct from the underlying event and observation period."},
    "baseline": _BASELINE, "surprise": _INFERENCE,
})
_RESPONSE = _object({
    **_ASSESSMENT["properties"],
    "variable": {"type": "string", "minLength": 1, "maxLength": 100},
    "role": {"type": "string", "enum": ["result", "transmission", "feedback"]},
    "window_start": _TIME, "window_end": _TIME,
})
_FEEDBACK = _object({**_INFERENCE["properties"],
                     "actor": _TEXT, "constraint": _TEXT, "trigger": _TEXT})
_IMPLICATION = _object({
    **_INFERENCE["properties"],
    "asset": {"type": "string", "minLength": 1, "maxLength": 100},
    "channel": {"type": "string", "enum": ["cash_flow", "discount_rate", "risk_premium", "fx", "funding", "supply_demand"],
                "description": "Use supply_demand for physical commodity availability, inventories or consumption; do not invent commodity cash flows. Producers' earnings belong to cash_flow."},
    "direction": {"type": "string", "enum": ["positive", "negative", "mixed", "unknown"]},
})
_HYPOTHESIS = _object({
    "id": {"type": "string", "minLength": 1, "maxLength": 256},
    "shock": _TEXT,
    "horizon": {"type": "string", "enum": ["event_window", "short_1_10d", "medium_1_3m"]},
    "evidence_refs": _REFS,
    "rate_channel": _TEXT,
    "cash_flow_channel": _TEXT,
    "risk_premium_channel": _TEXT,
    "upstream": _UPSTREAM,
    "market_responses": {"type": "array", "items": _RESPONSE, "minItems": 0, "maxItems": 5},
    "feedback": _FEEDBACK,
    "asset_implications": {"type": "array", "items": _IMPLICATION, "minItems": 0, "maxItems": 6},
    "alternative": _TEXT,
    "confirmation": _TEXT,
    "invalidation": _TEXT,
    "next_observation": _TEXT,
    "due_at": _TIME,
})

MACRO_DIAGNOSIS_SCHEMA = _object({
    "economic_state": _ASSESSMENT,
    "curve": _object({key: _ASSESSMENT for key in RATE_TENORS}),
    # Two overlapping decompositions, NOT additive attribution factors.
    "rate_drivers": _object({key: _INFERENCE if key == "term_premium" else _ASSESSMENT
                             for key in RATE_DRIVERS}),
    "pricing_baseline": _ASSESSMENT,
    "causal_hypotheses": {"type": "array", "items": _HYPOTHESIS, "minItems": 0, "maxItems": 3},
    "dominant_hypothesis_id": {"anyOf": [{"type": "null"}, {"type": "string", "minLength": 1}]},
    "synthesis": _INFERENCE,
})
MARKET_STRUCTURE_SCHEMA = _object({
    "dimensions": _object({key: _ASSESSMENT for key in STRUCTURE_DIMENSIONS}),
    "macro_interaction": _INFERENCE,
})


def assessment_items(diagnosis, structure):
    """Yield named assessments for consistent evidence checks."""
    yield "economic_state", diagnosis["economic_state"]
    for group in ("curve", "rate_drivers"):
        for key, value in diagnosis[group].items():
            yield f"{group}.{key}", value
    yield "pricing_baseline", diagnosis["pricing_baseline"]
    yield "synthesis", diagnosis["synthesis"]
    for key, value in structure["dimensions"].items():
        yield f"market_structure.{key}", value
    yield "market_structure.macro_interaction", structure["macro_interaction"]


def usable_evidence_refs(snapshot):
    """A catalogued missing/invalid source cannot substantiate a claim.

    Explicit custom snapshots without a catalog retain their manifest contract.
    Follow-up wrappers retain the original source catalog.
    """
    data = snapshot["data"]
    while isinstance(data.get("source_data"), dict):
        data = data["source_data"]
    catalog = data.get("evidence_catalog") or {}
    return [ref for ref in snapshot["evidence_refs"]
            if ref not in catalog or catalog[ref].get("status") == "read"]


def validate_analysis_evidence(diagnosis, structure, *, evidence_refs, as_of, usable_refs=None):
    """Check source membership and unsupported certainty after schema validation.

    All-unknown research is a valid abstention. Empty hypotheses must be accompanied
    by an unknown synthesis. No default diagnosis is ever filled into model output.
    """
    allowed = set(evidence_refs)
    usable = allowed if usable_refs is None else set(usable_refs)

    def check_refs(value, label, *, required):
        refs = value["evidence_refs"]
        if len(refs) != len(set(refs)) or set(refs) - allowed:
            raise ValueError(f"{label}: duplicate or unknown evidence")
        if required and not refs:
            raise ValueError(f"{label}: non-unknown analysis requires evidence")
        if required and set(refs) - usable:
            raise ValueError(f"{label}: missing or invalid source cannot support analysis")

    for label, assessment in assessment_items(diagnosis, structure):
        check_refs(assessment, label, required=assessment["status"] != "unknown")
        if assessment["status"] == "observed" and all(ref.startswith("peer:") for ref in assessment["evidence_refs"]):
            raise ValueError(f"{label}: peer interpretation alone is not an observed fact")
    for label, assessment in (
            ("term_premium", diagnosis["rate_drivers"]["term_premium"]),
            ("synthesis", diagnosis["synthesis"]),
            ("macro_interaction", structure["macro_interaction"])):
        if assessment["status"] == "observed":
            raise ValueError(f"{label}: mechanism interpretation must be inferred or unknown")
    chains = diagnosis["causal_hypotheses"]
    if not chains and diagnosis["synthesis"]["status"] != "unknown":
        raise ValueError("synthesis: requires at least one causal hypothesis or unknown status")
    seen = set()
    cutoff = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
    for chain in chains:
        if chain["id"] in seen:
            raise ValueError("causal_hypotheses: duplicate ID")
        seen.add(chain["id"])
        check_refs(chain, "causal_hypotheses", required=True)
        upstream = chain["upstream"]
        nested = [("upstream", upstream), ("baseline", upstream["baseline"]),
                  ("surprise", upstream["surprise"]), ("feedback", chain["feedback"])]
        nested += [("market_response", value) for value in chain["market_responses"]]
        nested += [("asset_implication", value) for value in chain["asset_implications"]]
        for label, value in nested:
            check_refs(value, label, required=value["status"] != "unknown")
            if set(value["evidence_refs"]) - set(chain["evidence_refs"]):
                raise ValueError(f"{label}: evidence must also be declared by its causal hypothesis")
            if value["status"] == "observed" and all(ref.startswith("peer:") for ref in value["evidence_refs"]):
                raise ValueError(f"{label}: peer interpretation alone is not an observed fact")

        def past_time(value, label):
            if value is None:
                return None
            stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if stamp > cutoff:
                raise ValueError(f"{label}: exceeds evidence cutoff")
            return stamp

        past_time(upstream["occurred_at"], "upstream.occurred_at")
        known = past_time(upstream["known_at"], "upstream.known_at")
        baseline = upstream["baseline"]
        baseline_known = past_time(baseline["known_at"], "baseline.known_at")
        if baseline_known and known and baseline_known >= known:
            raise ValueError("baseline: must be available before the new information")
        if upstream["surprise"]["status"] != "unknown" and (
                upstream["status"] == "unknown" or baseline["status"] == "unknown" or known is None or baseline_known is None):
            raise ValueError("surprise: requires a dated pre-event baseline and information time")
        if upstream["status"] == "unknown" and upstream["driver_type"] != "unknown":
            raise ValueError("upstream: unknown driver must not be assigned a driver type")
        for response in chain["market_responses"]:
            start = past_time(response["window_start"], "market_response.window_start")
            end = past_time(response["window_end"], "market_response.window_end")
            if (start is None) != (end is None) or (start and end and start >= end):
                raise ValueError("market_response: requires a complete, increasing window or two nulls")
            if known and end and end < known and response["status"] != "unknown":
                raise ValueError("market_response: later information cannot explain an earlier reaction")
        for implication in chain["asset_implications"]:
            if implication["status"] == "unknown" and implication["direction"] != "unknown":
                raise ValueError("asset_implication: unknown analysis cannot assign direction")
        if chain["confirmation"].strip() == chain["invalidation"].strip():
            raise ValueError("causal_hypotheses: confirmation and invalidation are identical")
        if chain["due_at"] is not None:
            due = datetime.fromisoformat(chain["due_at"].replace("Z", "+00:00"))
            if due <= cutoff:
                raise ValueError("causal_hypotheses: next check must follow evidence cutoff")
    dominant = diagnosis["dominant_hypothesis_id"]
    if dominant is not None and (dominant not in seen or diagnosis["synthesis"]["status"] == "unknown"):
        raise ValueError("dominant_hypothesis_id: must name a hypothesis and have a supported synthesis")
