"""Explicit securities-level EPS arithmetic; 零授权效力. No cash/debt conversion or valuation.

One equal-rights participating class is supported. Sources must establish its
rights and provide weighted shares, dividends and after-tax note interest.
Different participation tiers, caps and contingent rights require another model.
"""
from __future__ import annotations

import math
from copy import deepcopy


def _number(x, *, nonnegative=False):
    if isinstance(x, bool) or not isinstance(x, (float, int)) or not math.isfinite(x):
        raise ValueError("EPS requires finite explicit numbers")
    if nonnegative and x < 0:
        raise ValueError("EPS shares/dividends/addbacks must be nonnegative")
    return float(x)


def validate(block):
    if not isinstance(block, dict) or set(block) != {"basis", "basic_equivalents", "participation", "debt_if_converted"} or not block["basis"]:
        raise ValueError("explicit EPS securities scope required")
    ids = set()
    for field, keys in [("basic_equivalents", {"id", "basis", "shares"}),
                        ("debt_if_converted", {"id", "basis", "shares", "after_tax_interest"})]:
        if not isinstance(block[field], list):
            raise ValueError("EPS security list required")
        for x in block[field]:
            if set(x) != keys or not x["id"] or not x["basis"] or x["id"] in ids:
                raise ValueError("unique identified EPS securities required")
            ids.add(x["id"])
            shares = _number(x["shares"], nonnegative=True)
            if field == "debt_if_converted":
                addback = _number(x["after_tax_interest"], nonnegative=True)
                if shares == 0 and addback:
                    raise ValueError("interest addback without conversion shares")
    p = block["participation"]
    if p is not None:
        keys = {"id", "basis", "shares", "loss_participation", "convertible", "common_dividends", "participant_dividends"}
        if set(p) != keys or not p["id"] or not p["basis"] or p["id"] in ids:
            raise ValueError("one explicit participating class required")
        if type(p["loss_participation"]) is not bool or p["convertible"] is not False:
            raise ValueError("explicit participation rights required")
        for k in ("shares", "common_dividends", "participant_dividends"):
            _number(p[k], nonnegative=True)
        if p["shares"] == 0 and p["participant_dividends"]:
            raise ValueError("participant dividends without shares")


def calculate(*, income, control_income, basic_common, incremental, securities):
    """Use continuing income as control; recompute two-class allocation per trial.

Basic-equivalent warrants stay in the loss-period denominator. Instruments are
included one at a time, most dilutive first. Capped calls are never netted here.
income/control_income are after NCI and fixed preferred dividends, before the
participating-class allocation. Interest addbacks must use this earnings basis.
"""
    validate(securities)
    income, control = _number(income), _number(control_income)
    basic = _number(basic_common, nonnegative=True)
    if basic <= 0:
        raise ValueError("positive weighted common shares required")
    basic += sum(x["shares"] for x in securities["basic_equivalents"])
    ordinary = _number(incremental, nonnegative=True)
    p = securities["participation"]

    def allocation(total, denominator, converted):
        if p is None or converted or not p["shares"]:
            return total
        undistributed = total - p["common_dividends"] - p["participant_dividends"]
        allocated = p["participant_dividends"]
        if undistributed >= 0 or p["loss_participation"]:
            allocated += undistributed * p["shares"] / (denominator + p["shares"])
        return total - allocated

    basic_income = allocation(income, basic, False)
    # Participation dividends may turn positive total continuing income into a
    # loss available to common. Test the common control EPS, not total income.
    denominator = basic
    if ordinary:
        current = allocation(control, basic, False) / basic
        trial = allocation(control, basic + ordinary, False) / (basic + ordinary)
        if trial < current - 1e-12:
            denominator += ordinary
    converted = False
    debt_addback = 0.0
    candidates = [dict(x, kind="debt") for x in securities["debt_if_converted"] if x["shares"]]
    included = []
    while candidates:
        current = allocation(control + debt_addback, denominator, converted) / denominator
        trials = []
        for x in candidates:
            d = denominator + x["shares"]
            a = debt_addback + (x["after_tax_interest"] if x["kind"] == "debt" else 0)
            c = converted or x["kind"] == "participant"
            value = allocation(control + a, d, c) / d
            trials.append((value, x["id"], x, d, a, c))
        value, _, x, d, a, c = min(trials, key=lambda r: (r[0], r[1]))
        if value >= current - 1e-12:
            break
        denominator, debt_addback, converted = d, a, c
        included.append(x["id"])
        candidates = [row for row in candidates if row["id"] != x["id"]]
    numerator = allocation(income + debt_addback, denominator, converted)
    return {"basic_common_income": basic_income, "basic_shares": basic,
            "basic_eps": basic_income / basic, "diluted_income": numerator,
            "diluted_shares": denominator, "diluted_eps": numerator / denominator,
            "control_eps": allocation(control + debt_addback, denominator, converted) / denominator,
            "included": included, "excluded": [x["id"] for x in candidates],
            "debt_interest_addback": debt_addback, "participation_converted": converted}


def aggregate(inputs, days):
    """Aggregate raw inputs, then retest dilution at the annual earnings level.

Never average quarterly EPS or sum already allocated quarterly class earnings.
Missing security IDs in a quarter mean zero weighted exposure for that quarter.
"""
    if len(inputs) != len(days) or not inputs or any(d <= 0 for d in days):
        raise ValueError("EPS aggregation needs positive period weights")
    for x in inputs:
        validate(x["securities"])
    total_days = sum(days)
    out = {"income": sum(x["income"] for x in inputs),
           "control_income": sum(x["control_income"] for x in inputs),
           "basic_common": sum(x["basic_common"] * d for x, d in zip(inputs, days)) / total_days,
           "incremental": sum(x["incremental"] * d for x, d in zip(inputs, days)) / total_days}
    blocks = [x["securities"] for x in inputs]
    merged = {"basis": "Period-weighted securities; income/dividends/addbacks summed", "participation": None}
    for key in ("basic_equivalents", "debt_if_converted"):
        entries = {}
        for b, d in zip(blocks, days):
            for x in b[key]:
                row = entries.setdefault(x["id"], {**deepcopy(x), "shares": 0})
                row["shares"] += x["shares"] * d / total_days
        if key == "debt_if_converted":
            for id_, row in entries.items():
                row["after_tax_interest"] = sum(x["after_tax_interest"] for b in blocks for x in b[key] if x["id"] == id_)
        merged[key] = list(entries.values())
    ps = [b["participation"] for b in blocks if b["participation"] is not None]
    if ps:
        rights = {(p["id"], p["loss_participation"], p["convertible"]) for p in ps}
        if len(rights) != 1:
            raise ValueError("changing participation rights need separate annual allocation")
        merged["participation"] = {**deepcopy(ps[0]),
            "shares": sum((b["participation"] or {}).get("shares", 0) * d for b, d in zip(blocks, days)) / total_days,
            "common_dividends": sum(p["common_dividends"] for p in ps),
            "participant_dividends": sum(p["participant_dividends"] for p in ps)}
    out["securities"] = merged
    return out
