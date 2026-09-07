"""Read SEC company facts and select dated instant disclosures, without guessing tags.

Custom/segment facts are not covered by the SEC companyfacts endpoint. A reviewed
concept mapping is required before a fact can be used as a valuation input.
"""
from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import argparse
from datetime import date, datetime, time
import json
import re
from urllib.request import Request, urlopen

from quant.core.agent_contracts import canonical_hash
from quant.core.io_json import atomic_write_json, read_json
from quant.core.timeutil import ET
from quant.core.valuation_contracts import finite


def _cik(value):
    if not re.fullmatch(r"[0-9]{1,10}", str(value)):
        raise ValueError("CIK must contain one to ten digits")
    return str(int(value)).zfill(10)


def fetch(cik, *, user_agent: str, opener=urlopen) -> dict:
    """One public-data request; caller supplies its actual identifying User-Agent."""
    cik = _cik(cik)
    if not isinstance(user_agent, str) or not user_agent.strip() or any(c in user_agent for c in "\r\n"):
        raise ValueError("an identifying SEC User-Agent is required")
    request = Request(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json",
                      headers={"User-Agent": user_agent, "Accept": "application/json"})
    with opener(request, timeout=30) as stream:
        raw = stream.read(30_000_001)
    if len(raw) > 30_000_000:
        raise ValueError("companyfacts response exceeds size limit")
    payload = json.loads(raw)
    if _cik(payload.get("cik")) != cik or not isinstance(payload.get("facts"), dict):
        raise ValueError("SEC response identity or shape mismatch")
    return payload


def instant(payload: dict, concepts: list[str], *, period_end: str, as_of: str) -> dict:
    """Sum explicitly disjoint USD concepts from one filing, or return unknown.

Latest disclosure for every component must belong to the same accession. An
older complete combination cannot conceal a newer partial/revised disclosure.
Filed dates are conservatively available at the end of that ET day. This is
filing-dated selection, not proof that a downloaded archive existed historically.
"""
    cutoff = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
    if cutoff.tzinfo is None:
        raise ValueError("as_of requires timezone")
    date.fromisoformat(period_end)
    if not concepts or len(concepts) != len(set(concepts)):
        raise ValueError("unique explicit concepts required")
    result = {"status": "unknown", "value": None, "unit": "USD", "period_end": period_end,
              "cik": _cik(payload.get("cik")),
              "concepts": concepts, "components": [], "missing": [],
              "source_hash": canonical_hash(payload), "selection_as_of": as_of}
    for concept in concepts:
        namespace, tag = concept.split(":", 1)
        rows = (((payload.get("facts") or {}).get(namespace) or {}).get(tag) or {}).get("units", {}).get("USD", [])
        eligible = []
        for row in rows:
            try:
                filed = datetime.combine(date.fromisoformat(row["filed"]), time.max, ET)
                if (row.get("end") != period_end or row.get("start") or filed > cutoff
                        or date.fromisoformat(period_end) > filed.date()
                        or row.get("form") not in {"10-Q", "10-Q/A", "10-K", "10-K/A", "20-F", "20-F/A"}
                        or not finite(row.get("val")) or row["val"] < 0
                        or not re.fullmatch(r"\d{10}-\d{2}-\d{6}", row.get("accn", ""))):
                    continue
                eligible.append((filed, row))
            except (KeyError, TypeError, ValueError):
                continue
        if not eligible:
            result["missing"].append("missing_instant_usd:" + concept)
            continue
        latest = max(t for t, _ in eligible)
        candidates = [row for t, row in eligible if t == latest]
        if len({(row["val"], row["accn"]) for row in candidates}) != 1:
            result["missing"].append("ambiguous_latest_disclosure:" + concept)
            continue
        row = candidates[0]
        result["components"].append({"concept": concept, "value": row["val"], "accession": row["accn"],
                                     "available_at": latest.isoformat(), "form": row["form"]})
    if len({r["accession"] for r in result["components"]}) > 1:
        result["missing"].append("components_from_different_filings")
    if not result["missing"]:
        result.update(status="selected", value=sum(r["value"] for r in result["components"]),
                      available_at=max(r["available_at"] for r in result["components"]))
    result["receipt_hash"] = canonical_hash(result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", type=Path, help="existing SEC companyfacts JSON")
    source.add_argument("--cik", help="explicit verified issuer CIK; one SEC request")
    parser.add_argument("--user-agent", help="actual application identity for SEC fair access")
    parser.add_argument("--concepts", nargs="+", required=True)
    parser.add_argument("--period-end", required=True)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--output", type=Path, help="optional research receipt only")
    args = parser.parse_args(argv)
    payload = read_json(args.input) if args.input else fetch(args.cik, user_agent=args.user_agent)
    result = instant(payload, args.concepts, period_end=args.period_end, as_of=args.as_of)
    if args.output:
        atomic_write_json(args.output, result)
    print(json.dumps(result, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
