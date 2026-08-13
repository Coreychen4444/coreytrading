"""Trade-event ledger: validated appends + FIFO episode replay.

Design: events/<YYYY-MM>/trades.jsonl stores RAW FACTS plus human tags
(tier / classification / zone_at_fill / greeks / notes). Derived values
(episodes, realized PnL, holding periods) are computed at read time here —
never persisted, so they can't drift from the facts.

Option PnL uses contract multiplier 100 (qty is in contracts, price per share).
"""
from __future__ import annotations

import sys
from typing import Any, Iterator

from .io_json import append_jsonl, iter_jsonl
from .occ import contract_multiplier
from .paths import EVENTS_DIR
from .schema import validate_event
from .timeutil import parse_ts

OPEN_SIDES = {"BUY": 1, "SELL_SHORT": -1}
CLOSE_SIDES = {"SELL": 1, "BUY_BACK": -1}  # value = direction they close


def iter_events(events_dir=None) -> Iterator[dict]:
    """All events across month files, globally sorted by timestamp."""
    rows: list[dict] = []
    for path in sorted((events_dir or EVENTS_DIR).glob("*/trades.jsonl")):
        rows.extend(iter_jsonl(path))
    rows.sort(key=lambda e: (parse_ts(e.get("timestamp") or "") or parse_ts("1970-01-01T00:00:00+00:00")))
    yield from rows


def append_event(event: dict) -> None:
    """Validate then append to the correct ET-month file. Raises on schema errors."""
    errors = validate_event(event)
    if errors:
        raise ValueError("refusing to append invalid event:\n  " + "\n  ".join(errors))
    ym = str(event["timestamp"])[:7]
    append_jsonl(EVENTS_DIR / ym / "trades.jsonl", event)


# ---------------------------------------------------------------- episodes

def build_episodes(events: list[dict] | None = None, *, warn=lambda m: print(m, file=sys.stderr)) -> dict[str, list[dict]]:
    """Replay events per ticker into position episodes (flat → open → flat).

    Episode dict:
      ticker, direction (long|short), status (open|closed), opened_at, closed_at,
      entries [...], exits [...], qty_peak, cost_in, proceeds, fees,
      realized_pnl (None if any orphan exit made basis unknowable),
      orphan (True when closes arrived without recorded opens — history
      predates the event stream), multiplier
    """
    by_ticker: dict[str, list[dict]] = {}
    for e in (events if events is not None else iter_events()):
        if e.get("event_type") not in {"fill", "close", "expire", "assign"}:
            continue
        by_ticker.setdefault(e.get("ticker") or "?", []).append(e)

    out: dict[str, list[dict]] = {}
    for ticker, evs in by_ticker.items():
        mult = contract_multiplier(ticker)
        episodes: list[dict] = []
        ep: dict | None = None
        book: list[list[float]] = []  # FIFO open lots: [qty_remaining, price]
        direction = 0  # +1 long, -1 short, 0 flat

        def _open_episode(e, dirn):
            return {
                "ticker": ticker, "direction": "long" if dirn > 0 else "short",
                "status": "open", "opened_at": e.get("timestamp"), "closed_at": None,
                "entries": [], "exits": [], "qty_peak": 0.0,
                "cost_in": 0.0, "proceeds": 0.0, "fees": 0.0,
                "realized_pnl": 0.0, "orphan": False, "multiplier": mult,
            }

        for e in evs:
            side = (e.get("side") or "").upper()
            etype = e.get("event_type")
            qty = float(e.get("qty") or 0)
            price = float(e.get("price") or 0)
            fees = float(e.get("fees") or 0)
            if etype == "expire":
                side, price = "SELL" if direction >= 0 else "BUY_BACK", 0.0
            if etype == "assign" and not side:
                side = "SELL" if direction >= 0 else "BUY_BACK"

            if side in OPEN_SIDES:
                dirn = OPEN_SIDES[side]
                if direction == 0:
                    ep = _open_episode(e, dirn)
                    episodes.append(ep)
                    direction = dirn
                elif dirn != direction:
                    warn(f"[ledger] {ticker}: {side} while {ep['direction']} open — treating as basis-unknown flip, marking orphan")
                    ep["orphan"] = True
                ep["entries"].append(e)
                ep["cost_in"] += qty * price * mult
                ep["fees"] += fees
                book.append([qty, price])
                ep["qty_peak"] = max(ep["qty_peak"], sum(q for q, _ in book))

            elif side in CLOSE_SIDES:
                if direction == 0 or ep is None:
                    # close with no recorded open — pre-stream history
                    ep = _open_episode(e, CLOSE_SIDES[side])
                    ep["orphan"] = True
                    ep["realized_pnl"] = None
                    episodes.append(ep)
                    direction = CLOSE_SIDES[side]
                ep["exits"].append(e)
                ep["proceeds"] += qty * price * mult
                ep["fees"] += fees
                remaining = qty
                exit_pnl = 0.0
                while remaining > 1e-9 and book:
                    lot = book[0]
                    take = min(lot[0], remaining)
                    if ep["realized_pnl"] is not None:
                        pnl = (price - lot[1]) * take * mult
                        if ep["direction"] == "short":
                            pnl = -pnl
                        ep["realized_pnl"] += pnl
                        exit_pnl += pnl
                    lot[0] -= take
                    remaining -= take
                    if lot[0] <= 1e-9:
                        book.pop(0)
                # per-exit FIFO attribution (tier/classification analytics);
                # note: FIFO may differ from journal-declared tier splits
                ep.setdefault("exit_pnls", []).append({
                    "order_id": e.get("order_id"),
                    "timestamp": e.get("timestamp"),
                    "tier": e.get("tier"),
                    "qty": qty,
                    "realized_fifo": round(exit_pnl - fees, 4) if ep["realized_pnl"] is not None else None,
                })
                if remaining > 1e-9:
                    if not ep["orphan"]:
                        warn(f"[ledger] {ticker}: exit qty {qty} exceeds open book by {remaining} — orphan basis")
                    ep["orphan"] = True
                    ep["realized_pnl"] = None
                if not book:
                    ep["status"] = "closed"
                    ep["closed_at"] = e.get("timestamp")
                    if ep["realized_pnl"] is not None:
                        ep["realized_pnl"] = round(ep["realized_pnl"] - ep["fees"], 4)
                    ep = None
                    direction = 0
            # unknown sides ignored (validated at append anyway)

        out[ticker] = episodes
    return out


def closed_episodes(episodes_by_ticker: dict[str, list[dict]] | None = None) -> list[dict]:
    eps = episodes_by_ticker if episodes_by_ticker is not None else build_episodes()
    flat = [e for lst in eps.values() for e in lst if e["status"] == "closed"]
    flat.sort(key=lambda e: e.get("closed_at") or "")
    return flat
