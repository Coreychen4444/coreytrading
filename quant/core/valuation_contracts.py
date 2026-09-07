"""Strict conditional valuation inputs shared by host and tool-free workers.

Scenarios are hypotheses, never trading instructions or baseline publication.
Percent/pp inputs are fractions; rate inputs explicitly use basis points.
"""
from __future__ import annotations

from copy import deepcopy
import math

DIRECT_FACTORS = frozenset({"rf_bp", "erp_bp", "debt_spread_bp", "revenue_pct",
                            "margin_pp", "reinvestment_usd", "net_interest_usd", "pe_pct"})
MACRO_FACTORS = frozenset({"policy_rate_bp", "demand_pct", "usd_pct", "energy_pct", "inflation_pp"})
FACTORS = DIRECT_FACTORS | MACRO_FACTORS


def _object(properties):
    return {"type": "object", "properties": properties, "required": list(properties),
            "additionalProperties": False}


TEXT = {"type": "string", "minLength": 1, "maxLength": 4000}
SCENARIO_SCHEMA = _object({
    "id": {"type": "string", "minLength": 1, "maxLength": 128},
    "label": TEXT, "basis": TEXT,
    "evidence_refs": {"type": "array", "items": TEXT, "minItems": 1, "maxItems": 30},
    "shocks": {"type": "array", "minItems": 1, "maxItems": 30, "items": _object({
        "factor": {"type": "string", "enum": sorted(FACTORS)},
        "delta": {"type": "number"},
        "start_year": {"type": "integer", "minimum": 1, "maximum": 30},
        "end_year": {"type": "integer", "minimum": 1, "maximum": 30},
        "terminal": {"type": "boolean"},
    })},
})


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def validate_scenario(value, *, horizon=30, evidence_refs=None):
    """Validate units, coverage and uniqueness without manufacturing evidence."""
    if type(value) is not dict or set(value) != set(SCENARIO_SCHEMA["properties"]):
        raise ValueError("scenario: unexpected or missing fields")
    for name in ("id", "label", "basis"):
        if type(value[name]) is not str or not value[name].strip() or len(value[name]) > (128 if name == "id" else 4000):
            raise ValueError("scenario: invalid " + name)
    refs = value["evidence_refs"]
    if (type(refs) is not list or not 1 <= len(refs) <= 30
            or any(type(r) is not str or not r.strip() or len(r) > 4000 for r in refs)
            or len(refs) != len(set(refs))):
        raise ValueError("scenario: invalid evidence references")
    if evidence_refs is not None and set(refs) - set(evidence_refs):
        raise ValueError("scenario: unknown evidence reference")
    shocks = value["shocks"]
    if type(shocks) is not list or not 1 <= len(shocks) <= 30:
        raise ValueError("scenario: invalid shocks")
    occupied = set()
    for shock in shocks:
        if type(shock) is not dict or set(shock) != {"factor", "delta", "start_year", "end_year", "terminal"}:
            raise ValueError("scenario: invalid shock fields")
        factor, delta = shock["factor"], shock["delta"]
        if type(factor) is not str or factor not in FACTORS or not finite(delta):
            raise ValueError("scenario: invalid factor or delta")
        if type(shock["terminal"]) is not bool:
            raise ValueError("scenario: terminal must be boolean")
        first, last = shock["start_year"], shock["end_year"]
        if type(first) is not int or type(last) is not int or not 1 <= first <= last <= horizon:
            raise ValueError("scenario: invalid cash-flow years")
        if shock["terminal"] and last != horizon:
            raise ValueError("scenario: terminal shock must include final forecast year")
        if factor == "pe_pct" and (first != 1 or last != 1 or shock["terminal"]):
            raise ValueError("pe_pct refers only to the year-one price multiple")
        if factor.endswith("_bp") and abs(delta) > 1000:
            raise ValueError("scenario: rate shock outside 1000 bp support")
        if factor.endswith(("_pct", "_pp")) and abs(delta) > 1:
            raise ValueError("scenario: percentages must be fractions within [-1, 1]")
        keys = {(factor, year) for year in range(first, last + 1)}
        if occupied & keys:
            raise ValueError("scenario: overlapping shocks double count a factor")
        occupied |= keys
    return deepcopy(value)


def validate_transmissions(rows):
    if type(rows) is not list or len(rows) > 50:
        raise ValueError("transmissions must be an array of at most 50 coefficients")
    seen = set()
    for row in rows:
        required = {"factor", "driver", "coefficient", "basis", "source"}
        bounds = {"start_year", "end_year", "terminal"}
        if type(row) is not dict or set(row) not in (required, required | bounds):
            raise ValueError("transmission: unexpected or missing fields")
        if bounds <= set(row):
            if (type(row["start_year"]) is not int or type(row["end_year"]) is not int
                    or not 1 <= row["start_year"] <= row["end_year"] <= 30 or type(row["terminal"]) is not bool):
                raise ValueError("transmission: invalid effect horizon")
        if (type(row["factor"]) is not str or type(row["driver"]) is not str
                or row["factor"] not in MACRO_FACTORS or row["driver"] not in DIRECT_FACTORS):
            raise ValueError("transmission: unsupported factor/driver")
        if not finite(row["coefficient"]):
            raise ValueError("transmission: nonfinite coefficient")
        if any(type(row[k]) is not str or not row[k].strip() for k in ("basis", "source")):
            raise ValueError("transmission: coefficient requires source and basis")
        key = (row["factor"], row["driver"])
        if key in seen:
            raise ValueError("transmission: duplicate channel")
        seen.add(key)
    return deepcopy(rows)
