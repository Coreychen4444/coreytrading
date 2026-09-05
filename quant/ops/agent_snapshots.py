"""Freeze explicit evidence for role workers without refreshing or changing data.

Snapshots are private artifacts, not broker truth. Capture reads cached inputs;
the caller selects the role and the controlled writer checks the card revision
again before accepting a proposal. No model receives repository access.
"""
from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from datetime import datetime, timedelta, timezone
import json
import re

from quant.core.agent_contracts import canonical_hash
from quant.core.io_json import read_json
from quant.core.market_sessions import MarketSessionError, calendar_metadata, latest_completed_session, session_close
from quant.core import technical
from quant.core.paths import KLINE_CACHE_DIR, MACRO_CURRENT_PATH, POSITIONS_PATH, WATCHLIST_PATH
from quant.decision.level_map import build_map
from quant.ops.macro_state import validate_document as validate_macro_document


def make_snapshot(data: dict, *, evidence_refs: list[str] | None = None,
                  now: datetime | None = None, ttl_s: int = 3600) -> dict:
    now = now or datetime.now(timezone.utc)
    if type(data) is not dict:
        raise ValueError("snapshot data must be a JSON object")
    if now.tzinfo is None or type(ttl_s) is not int or not 1 <= ttl_s <= 86400:
        raise ValueError("snapshot requires timezone-aware time and TTL of 1..86400 seconds")
    # Strict roundtrip detaches callers and rejects non-JSON/nonfinite values.
    digest = canonical_hash(data)
    detached = json.loads(json.dumps(data, ensure_ascii=False, allow_nan=False))
    refs = list(evidence_refs if evidence_refs is not None else sorted(data))
    if len(set(refs)) != len(refs) or any(not isinstance(ref, str) or not ref for ref in refs):
        raise ValueError("evidence references must be unique nonempty strings")
    as_of = now.isoformat()
    return {"snapshot_id": canonical_hash({"data": digest, "as_of": as_of, "refs": refs}),
            "input_hash": digest, "as_of": as_of,
            "expires_at": (now + timedelta(seconds=ttl_s)).isoformat(),
            "evidence_refs": refs, "data": detached}


def completed_bars(rows: list[dict], now: datetime) -> list[dict]:
    """Keep finalized daily bars only after the exchange's actual scheduled close."""
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("completed bars require a timezone-aware time")
    out = []
    for row in rows:
        stamp = str(row.get("time") or row.get("date") or "")[:10]
        try:
            day = datetime.strptime(stamp, "%Y-%m-%d").date()
        except ValueError:
            raise ValueError("bar missing a valid session date") from None
        close = session_close(day)
        if close is None or now < close:
            continue
        if row.get("complete") is False or row.get("is_complete") is False:
            continue
        bar = dict(row, time=stamp)
        for key in ("open", "high", "low", "close", "volume"):
            if type(bar.get(key)) not in (int, float) or bar[key] < 0:
                raise ValueError(f"bar has invalid {key}")
        if not 0 < bar["low"] <= min(bar["open"], bar["close"]) <= max(bar["open"], bar["close"]) <= bar["high"]:
            raise ValueError("bar has inconsistent OHLC values")
        canonical_hash(bar)
        out.append(bar)
    if any(a["time"] >= b["time"] for a, b in zip(out, out[1:])):
        raise ValueError("bars must have unique increasing session dates")
    return out


def _macro_prior(path: Path, now: datetime) -> tuple[dict | None, str]:
    """Use the existing macro schema; a capture cannot repair or refresh a prior."""
    if not path.exists():
        return None, "missing"
    try:
        macro = read_json(path)
        canonical_hash(macro)
        if validate_macro_document(macro) or macro.get("example_only") is True:
            return None, "invalid"
        produced = datetime.fromisoformat(macro["generated_at"]["utc"].replace("Z", "+00:00"))
        if produced > now:
            return None, "future"
        return macro, "valid"
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None, "invalid"


def capture_card(ticker: str, bucket: str, *, now: datetime | None = None,
                 positions_path: Path | None = None, watchlist_path: Path | None = None,
                 kline_dir: Path | None = None, macro_path: Path | None = None) -> dict:
    """Freeze one current card and its cached evidence; no network or writes."""
    ticker = ticker.upper()
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9.-]{0,31}", ticker):
        raise ValueError("invalid ticker")
    if bucket not in ("positions", "watchlist"):
        raise ValueError("bucket must be positions or watchlist")
    now = now or datetime.now(timezone.utc)
    path = (positions_path or POSITIONS_PATH) if bucket == "positions" else (watchlist_path or WATCHLIST_PATH)
    doc = read_json(path)
    card = doc.get("tickers", {}).get(ticker)
    if card is None:
        raise ValueError("target card missing; membership must be synchronized first")
    kpath = (kline_dir or KLINE_CACHE_DIR) / f"{ticker}.jsonl"
    rows = [json.loads(line) for line in kpath.read_text().splitlines() if line.strip()] if kpath.exists() else []
    bars = completed_bars(rows, now)[-260:]
    warnings = []
    if not bars:
        warnings.append("missing_completed_bars")
    if bars and (now.date() - datetime.fromisoformat(bars[-1]["time"]).date()).days > 7:
        warnings.append("cached_bars_older_than_seven_days")
    expected_session = None
    try:
        expected_session = latest_completed_session(now).isoformat()
        if bars and bars[-1]["time"] != expected_session:
            warnings.append("cached_bars_missing_latest_exchange_session")
    except MarketSessionError:
        warnings.append("market_calendar_unavailable")
    mpath = macro_path or MACRO_CURRENT_PATH
    macro, macro_status = _macro_prior(mpath, now)
    if macro_status == "missing":
        warnings.append("macro_prior_unavailable")
    elif macro_status != "valid":
        warnings.append("macro_prior_" + macro_status)
    valuation = (card.get("agent") or {}).get("valuation")
    valuation_as_of = valuation.get("as_of") if isinstance(valuation, dict) else None
    if not isinstance(valuation_as_of, str):
        valuation_as_of = None
    source_times = {
        "market_calendar": calendar_metadata(),
        "expected_completed_session": expected_session,
        "completed_session_close_utc": session_close(bars[-1]["time"]).isoformat() if bars else None,
        "captured_at": now.isoformat(), "cache_not_refreshed": True,
        "snapshot_as_of_is_capture_time": True,
        "completed_session": bars[-1]["time"] if bars else None,
        "macro_status": macro_status,
        "macro_generated_at": macro["generated_at"]["utc"] if macro else None,
        "macro_evidence_cutoff": macro["data_quality"]["cutoff_utc"] if macro else None,
        "valuation_as_of": valuation_as_of,
    }
    data = {"ticker": ticker, "bucket": bucket, "card": card,
            "card_revision": canonical_hash(card), "completed_bars": bars,
            "latest_completed_session": bars[-1]["time"] if bars else None,
            "technical": technical.analyze(bars) if len(bars) >= 60 else None,
            "level_map": build_map(bars) if len(bars) >= 20 else None,
            "macro": macro, "valuation": valuation, "source_times": source_times,
            "capture_warnings": warnings}
    return make_snapshot(data, now=now)
