#!/usr/bin/env python3
"""Structured news overlays with visibility, expiry and finite effects; 零授权效力.

The reader never writes the intelligence archive. Explicit CLI add/cancel writes
are append-only and require caller authorization. Reliability gates do not prove
a coefficient. New records require a fixed date/timestamp; legacy next_print or
next_tier1 applies only with a previously pinned resolved_until. Missing expiry
never rolls forward with the latest calendar. Finite revenue/margin/interest
shocks specify start_year/end_year and the matching fundamentals baseline hash.
See quant/valuation/README.md for units, baseline absorption and limitations.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 直接 `python3 quant/<层>/<模块>.py` 时让 `quant.*` 可 import

import argparse
import hashlib
import json
import re
from datetime import date, datetime, time, timedelta
import math

from quant.core.io_json import append_jsonl, iter_jsonl  # noqa: E402
from quant.core.paths import VALUATION_OVERLAYS_PATH  # noqa: E402
from quant.core.timeutil import et_today, now_et_iso  # noqa: E402
from quant.core.timeutil import ET
from quant.valuation.archetypes import ARCHETYPES, EPS_DRIVERS, PE_DRIVERS  # noqa: E402

RELIABILITY = ("filing", "company_official", "official_data", "major_wire", "analyst", "rumor", "manual")
APPLIED_RELIABILITY = frozenset({"filing", "company_official", "official_data", "major_wire", "manual"})
CHANNELS = ("eps", "pe")
KINDS = ("overlay", "cancel")
VALID_UNTIL_SYMBOLS = ("next_print", "next_tier1")
MAX_AGE_DAYS = 180
MULTIPLICATIVE = frozenset({"revenue_base", "pe_multiplier"})
DELTA_BOUNDS = {   # |delta| 上限 — 超出 = 十有八九单位写错 (百分数当小数)
    "revenue_base": 1.0, "growth_start": 0.5, "target_margin": 0.3, "margin_start": 0.3,
    "share_change_rate": 0.1, "tax_rate": 0.15, "net_interest_after_tax": None,
    "erp": 0.03, "rf": 0.03, "wacc": 0.03, "terminal_growth": 0.02, "pe_multiplier": 0.5, "scenario_tilt": 0.25,
}
MACRO = "MACRO"
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ── 校验 ────────────────────────────────────────────────────────────

def validate_record(r: dict) -> None:
    if not isinstance(r, dict):
        raise ValueError("记录必须是对象")
    rid = r.get("id")
    if not isinstance(rid, str) or not rid.strip():
        raise ValueError("缺 id")
    kind = r.get("kind", "overlay")
    if kind not in KINDS:
        raise ValueError(f"kind {kind!r} 不在 {KINDS}")
    if not str(r.get("note") or "").strip():
        raise ValueError(f"{rid}: 缺 note (数字怎么来的一句话)")
    if "as_of" in r and _moment(r["as_of"]) is None:
        raise ValueError(f"{rid}: invalid as_of timestamp")
    if kind == "cancel":
        return
    for k in ("date", "ticker", "source", "reliability", "channel", "driver", "delta", "valid_until"):
        if k not in r:
            raise ValueError(f"{rid}: 缺 {k}")
    if not _DATE.match(str(r["date"])) or _moment(r["date"]) is None:
        raise ValueError(f"{rid}: date 必须 YYYY-MM-DD")
    t = r["ticker"]
    if not isinstance(t, str) or t != t.upper() or not t:
        raise ValueError(f"{rid}: ticker 必须大写")
    if r["reliability"] not in RELIABILITY:
        raise ValueError(f"{rid}: reliability {r['reliability']!r} 不在 {RELIABILITY}")
    if r["channel"] not in CHANNELS:
        raise ValueError(f"{rid}: channel 必须 eps|pe")
    drv = r["driver"]
    allowed = EPS_DRIVERS if r["channel"] == "eps" else PE_DRIVERS
    if drv not in allowed:
        raise ValueError(f"{rid}: driver {drv!r} 不属于 {r['channel']} 通道 {sorted(allowed)}")
    d = r["delta"]
    if not isinstance(d, (int, float)) or isinstance(d, bool) or not math.isfinite(d):
        raise ValueError(f"{rid}: delta 必须是数")
    bound = DELTA_BOUNDS.get(drv)
    if bound is not None and abs(d) > bound:
        raise ValueError(f"{rid}: |delta| {d} 超过 {drv} 的上限 {bound} (单位写错? 小数不是百分数)")
    vu = str(r["valid_until"])
    if vu not in VALID_UNTIL_SYMBOLS and _moment(vu) is None:
        raise ValueError(f"{rid}: valid_until 必须 YYYY-MM-DD 或 {VALID_UNTIL_SYMBOLS}")
    if _DATE.match(vu) and vu < str(r["date"]):
        raise ValueError(f"{rid}: valid_until 早于 date")
    if "start_year" in r or "end_year" in r:
        first, last = r.get("start_year"), r.get("end_year")
        if type(first) is not int or type(last) is not int or not 1 <= first <= last <= 30:
            raise ValueError(f"{rid}: invalid cash-flow years")
        if not r.get("baseline_id"):
            raise ValueError(f"{rid}: finite effects require baseline_id")
        if drv not in ("revenue_base", "margin_start", "net_interest_after_tax"):
            raise ValueError(f"{rid}: driver does not support finite-year effects")
    if t == MACRO:
        sc = r.get("scope")
        if sc is not None:
            if not isinstance(sc, dict) or not (set(sc) <= {"archetypes", "tickers"}):
                raise ValueError(f"{rid}: scope 只认 archetypes / tickers")
            bad = set(sc.get("archetypes") or []) - set(ARCHETYPES)
            if bad:
                raise ValueError(f"{rid}: scope.archetypes 不认识 {sorted(bad)}")
    elif r.get("scope") is not None:
        raise ValueError(f"{rid}: 只有 MACRO 记录能带 scope")
    if "direction" in r and r["direction"] is not None and r["direction"] not in (-2, -1, 0, 1, 2):
        raise ValueError(f"{rid}: direction 只认 -2..2")


def make_id(r: dict) -> str:
    h = hashlib.sha1(f"{r['ticker']}|{r['date']}|{r['driver']}|{r['source']}|{r['delta']}".encode()).hexdigest()[:8]
    return f"{r['date']}-{r['ticker']}-{r['driver']}-{h}"


# ── 读取 / 生效筛选 ─────────────────────────────────────────────────

def load_records(path: Path | None = None) -> list[dict]:
    return list(iter_jsonl(Path(path or VALUATION_OVERLAYS_PATH)))


def _moment(value):
    try:
        if _DATE.fullmatch(str(value)):
            return datetime.combine(date.fromisoformat(value), time.max, tzinfo=ET)
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return stamp if stamp.tzinfo is not None else None
    except (TypeError, ValueError):
        return None


def collapse(records: list[dict], *, as_of: str | None = None) -> dict[str, dict]:
    """按 id 去重: 后写的赢; cancel 移除。返回 id → overlay 记录 (不含 cancel)。"""
    out: dict[str, dict] = {}
    visible = []
    cutoff = _moment(as_of) if as_of is not None else None
    for index, record in enumerate(records):
        known = _moment(record.get("as_of") or record.get("date"))
        if as_of is not None and (cutoff is None or known is None or known > cutoff):
            continue
        visible.append((known, index, record))
    if cutoff is not None:
        visible.sort(key=lambda item: (item[0], item[1]))
    for _, _, r in visible:
        rid = r.get("id")
        if not rid:
            continue
        if r.get("kind", "overlay") == "cancel":
            out.pop(rid, None)
        else:
            out[rid] = r
    return out


def _resolve_until(r: dict, next_print_day: str | None, next_tier1_day: str | None) -> tuple[str | None, str | None]:
    """→ (到期日 or None, 旗标 or None)。"""
    vu = str(r.get("valid_until") or "")
    if vu == "next_print":
        return (r.get("resolved_until"), "legacy next_print requires a pinned expiry")
    if vu == "next_tier1":
        return (r.get("resolved_until"), "legacy next_tier1 requires a pinned expiry")
    return (vu or None, None)


def _in_scope(r: dict, ticker: str, archetype: str | None) -> bool:
    if r.get("ticker") == ticker:
        return True
    if r.get("ticker") != MACRO:
        return False
    sc = r.get("scope") or {}
    if sc.get("tickers") and ticker not in sc["tickers"]:
        return False
    if sc.get("archetypes") and archetype not in sc["archetypes"]:
        return False
    return True


def active_overlays(records: list[dict], *, ticker: str, archetype: str | None, as_of: str,
                    next_print_day: str | None = None, next_tier1_day: str | None = None,
                    baseline_id: str | None = None) -> dict:
    """当时有效的 overlay: applied (可靠且未到期) / ignored (可靠度不够 或 到期) / flags。"""
    day = str(as_of)[:10]
    applied, ignored, flags = [], [], []
    cutoff = _moment(as_of)
    if cutoff is None:
        raise ValueError("invalid overlay cutoff")
    for rid, r in sorted(collapse(records, as_of=as_of).items()):
        if not _in_scope(r, ticker, archetype):
            continue
        if str(r.get("date")) > day:
            continue
        try:
            validate_record(r)
        except ValueError as exc:
            ignored.append({"id": rid, "reason": str(exc)})
            continue
        if r.get("baseline_id") and r["baseline_id"] != baseline_id:
            ignored.append({"id": rid, "reason": "baseline mismatch or unknown; absorption review required"})
            continue
        try:
            age = (date.fromisoformat(day) - date.fromisoformat(str(r["date"]))).days
        except ValueError:
            age = None
        until, flag = _resolve_until(r, next_print_day, next_tier1_day)
        if until is None or _moment(until) is None:
            ignored.append({"id": rid, "reason": flag or "expiry unknown"})
            continue
        if _moment(until) < cutoff or ("T" in until and _moment(until) == cutoff):
            ignored.append({"id": rid, "reason": f"已到期 {until}"})
            continue
        if age is not None and age > MAX_AGE_DAYS:
            ignored.append({"id": rid, "reason": f"距事件已 {age} 天 > {MAX_AGE_DAYS}"})
            continue
        if r.get("reliability") not in APPLIED_RELIABILITY:
            ignored.append({"id": rid, "reason": f"可靠度 {r.get('reliability')} 不应用"})
            continue
        if flag:
            flags.append(f"{rid}: {flag}")
        applied.append({**r, "resolved_until": until})
    return {"applied": applied, "ignored": ignored, "flags": flags}


def manual_records(ticker: str, entries: list[dict]) -> list[dict]:
    """Manual entries keep the same explicit expiry, visibility and finite-year contract."""
    out = []
    for i, o in enumerate(entries or []):
        drv = o["driver"]
        out.append({"id": f"manual-{ticker}-{i}-{drv}", "kind": "overlay", "date": o["date"], "ticker": ticker,
                    "source": o["source"], "reliability": "manual",
                    "channel": "eps" if drv in EPS_DRIVERS else "pe", "driver": drv, "delta": o["delta"],
                    "valid_until": o.get("valid_until"), "note": o["note"],
                    **{k: o[k] for k in ("as_of", "baseline_id", "start_year", "end_year") if k in o}})
    return out


# ── 写入 (append-only, 校验通过才写) ────────────────────────────────

def add_record(r: dict, path: Path | None = None) -> dict:
    r = dict(r)
    r.setdefault("kind", "overlay")
    r.setdefault("as_of", now_et_iso())
    if r.get("valid_until") in VALID_UNTIL_SYMBOLS:
        raise ValueError("new overlays require a fixed expiry, not a rolling event symbol")
    if r["kind"] == "overlay" and not r.get("id"):
        r["id"] = make_id(r)
    validate_record(r)
    append_jsonl(Path(path or VALUATION_OVERLAYS_PATH), r, lock=False)
    return r


def cancel_record(rid: str, note: str, path: Path | None = None) -> dict:
    r = {"id": rid, "kind": "cancel", "as_of": now_et_iso(), "note": note}
    validate_record(r)
    append_jsonl(Path(path or VALUATION_OVERLAYS_PATH), r, lock=False)
    return r


def describe(r: dict) -> str:
    d = r["delta"]
    dtxt = f"×{1+d:.3f}" if r["driver"] in MULTIPLICATIVE else (f"{d:+,.0f}" if r["driver"] == "net_interest_after_tax" else f"{d:+.2%}")
    until = r.get("resolved_until") or r.get("valid_until")
    return f"{r['date']} {r['ticker']} {r['channel']}:{r['driver']} {dtxt} [{r['reliability']}, 至 {until}] {r['note']} ← {r['source']}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="新闻→估值驱动 overlay 契约 (trade/intelligence/valuation/overlays.jsonl)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add")
    a.add_argument("--ticker", required=True)
    a.add_argument("--date", required=True)
    a.add_argument("--source", required=True)
    a.add_argument("--reliability", required=True, choices=RELIABILITY)
    a.add_argument("--channel", required=True, choices=CHANNELS)
    a.add_argument("--driver", required=True)
    a.add_argument("--delta", required=True, type=float)
    a.add_argument("--valid-until", required=True)
    a.add_argument("--note", required=True)
    a.add_argument("--direction", type=int)
    a.add_argument("--confidence")
    a.add_argument("--run-id")
    a.add_argument("--id")
    a.add_argument("--baseline-id")
    a.add_argument("--start-year", type=int)
    a.add_argument("--end-year", type=int)
    a.add_argument("--scope-archetypes", nargs="*")
    a.add_argument("--scope-tickers", nargs="*")
    a.add_argument("--json", action="store_true")
    ls = sub.add_parser("list")
    ls.add_argument("--ticker")
    ls.add_argument("--as-of")
    ls.add_argument("--json", action="store_true")
    c = sub.add_parser("cancel")
    c.add_argument("--id", required=True)
    c.add_argument("--note", required=True)
    args = ap.parse_args(argv)

    if args.cmd == "add":
        r = {"ticker": args.ticker.upper(), "date": args.date, "source": args.source, "reliability": args.reliability,
             "channel": args.channel, "driver": args.driver, "delta": args.delta, "valid_until": args.valid_until,
             "note": args.note}
        for k in ("direction", "confidence", "run_id", "id", "baseline_id", "start_year", "end_year"):
            v = getattr(args, k)
            if v is not None:
                r[k] = v
        if args.scope_archetypes or args.scope_tickers:
            r["scope"] = {}
            if args.scope_archetypes:
                r["scope"]["archetypes"] = args.scope_archetypes
            if args.scope_tickers:
                r["scope"]["tickers"] = [t.upper() for t in args.scope_tickers]
        try:
            out = add_record(r)
        except ValueError as e:
            print(f"✗ 未写入: {e}", file=sys.stderr)
            return 2
        print(json.dumps(out, ensure_ascii=False) if args.json else f"✓ {describe(out)}")
        return 0
    if args.cmd == "cancel":
        out = cancel_record(args.id, args.note)
        print(f"✓ cancelled {out['id']}")
        return 0
    recs = collapse(load_records())
    day = args.as_of or et_today()
    rows = [r for r in recs.values() if not args.ticker or r["ticker"] in (args.ticker.upper(), MACRO)]
    if args.json:
        print(json.dumps(rows, ensure_ascii=False))
    else:
        for r in sorted(rows, key=lambda x: (x["date"], x["ticker"])):
            print(describe(r))
        print(f"{len(rows)} 条 (as-of {day}; 有效性按票在估值时判)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
