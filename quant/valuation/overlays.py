#!/usr/bin/env python3
"""overlays.py — 新闻 → 估值驱动的结构化契约 (`ai_news/valuation/overlays.jsonl`)。

为什么存在 (用户): 「有些订单新闻会影响 EPS, 有些新闻会影响 PE, 估值需要跟随这些
可靠的新闻变化而不是一成不变, 宏观新闻亦是如此」。情报层收拢 () 之后本仓不读新闻,
读新闻的是 ai_news 雷达 (Claude 定时任务 ai-news-radar; 前是 Codex)。雷达每轮本来就按
`直接EPS / 间接EPS / PE / ERP` 四栏推演
(PROMPT.md §5.1, 框架 §8), 只是落在 markdown 表格里, 机器读不到。本文件把那一栏变成机器可读的
**一行 JSON**: 哪只票、哪条新闻、哪个通道 (eps|pe)、哪个驱动、多少 delta、可靠度、到期日、出处。
估值层每次估值把当时**有效**的 overlay 加在驱动上, 应用了哪些逐条进输出与账本。

所有权: 雷达 (或 用户 手动) 通过本模块 CLI `add` 写入 —— 只校验通过才 append, 文件在
`ai_news/` 内 (雷达 §0 硬边界: 只写 ai_news/; 与 macro_state.py commit 同款「经写入器」);
估值层只读。撤销 = 追加 `cancel` 记录, 不改历史行 (append-only)。

一条记录:
    {"id": "-AMZN-ftc-suit", "kind": "overlay", "date": "",
     "as_of": "T22:30:00+08:00", "ticker": "AMZN", "scope": null,
     "source": "https://...", "reliability": "filing", "channel": "pe",
     "driver": "pe_multiplier", "delta": -0.05, "valid_until": "",
     "direction": -1, "confidence": "中", "note": "FTC 诉讼: 监管折价 5% (推导: ...)", "run_id": "..."}

通道与驱动 (词表在 archetypes.EPS_DRIVERS / PE_DRIVERS, 语义在 model.apply_overlays_*):
  eps  revenue_base(×1+δ) · growth_start · target_margin · margin_start · share_change_rate
       · net_interest_after_tax($) · tax_rate
  pe   erp · rf · wacc · terminal_growth · pe_multiplier(×1+δ) · scenario_tilt(概率从熊搬到牛)
可靠度门: 只有 filing / company_official / official_data / major_wire / manual 会被应用;
  analyst / rumor 只记录不应用 (列在 ignored 里, 让人看得见)。
到期: 具体日期 | next_print (该票下一个财报反应日: TTM 一落地就吸收了订单, 再加就是重复计量)
  | next_tier1 (下一个 Tier-1 宏观事件日)。解析不了 → 仍应用但打旗; 任何记录距 date 超 180 天一律过期。
MACRO 记录 (ticker="MACRO") 作用于全部票, `scope` 可限 archetypes / tickers。

🚨 零授权效力: 本层不被 sizing/entry_guard/exit_guard import, 不写 positions/watchlist。

CLI:
    python3 quant/valuation/overlays.py add --ticker AMZN --date --source URL \\
        --reliability filing --channel pe --driver pe_multiplier --delta -0.05 \\
        --valid-until --note "..." [--direction -1 --confidence 中 --run-id R] [--json]
    python3 quant/valuation/overlays.py add --ticker MACRO --scope-archetypes semi_cyclical ai_infra_capex ...
    python3 quant/valuation/overlays.py list [--ticker T] [--as-of YYYY-MM-DD] [--json]
    python3 quant/valuation/overlays.py cancel --id ID --note "..."
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 直接 `python3 quant/<层>/<模块>.py` 时让 `quant.*` 可 import

import argparse
import hashlib
import json
import re
from datetime import date, timedelta

from quant.core.io_json import append_jsonl, iter_jsonl  # noqa: E402
from quant.core.paths import VALUATION_OVERLAYS_PATH  # noqa: E402
from quant.core.timeutil import et_today, now_et_iso  # noqa: E402
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
    if kind == "cancel":
        return
    for k in ("date", "ticker", "source", "reliability", "channel", "driver", "delta", "valid_until"):
        if k not in r:
            raise ValueError(f"{rid}: 缺 {k}")
    if not _DATE.match(str(r["date"])):
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
    if not isinstance(d, (int, float)) or isinstance(d, bool):
        raise ValueError(f"{rid}: delta 必须是数")
    bound = DELTA_BOUNDS.get(drv)
    if bound is not None and abs(d) > bound:
        raise ValueError(f"{rid}: |delta| {d} 超过 {drv} 的上限 {bound} (单位写错? 小数不是百分数)")
    vu = str(r["valid_until"])
    if vu not in VALID_UNTIL_SYMBOLS and not _DATE.match(vu):
        raise ValueError(f"{rid}: valid_until 必须 YYYY-MM-DD 或 {VALID_UNTIL_SYMBOLS}")
    if _DATE.match(vu) and vu < str(r["date"]):
        raise ValueError(f"{rid}: valid_until 早于 date")
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


def collapse(records: list[dict]) -> dict[str, dict]:
    """按 id 去重: 后写的赢; cancel 移除。返回 id → overlay 记录 (不含 cancel)。"""
    out: dict[str, dict] = {}
    for r in records:
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
        return (next_print_day, None) if next_print_day else (None, "到期日 next_print 未解析 (财报日历无该票下一反应日)")
    if vu == "next_tier1":
        return (next_tier1_day, None) if next_tier1_day else (None, "到期日 next_tier1 未解析 (宏观状态无下一事件)")
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
                    next_print_day: str | None = None, next_tier1_day: str | None = None) -> dict:
    """当时有效的 overlay: applied (可靠且未到期) / ignored (可靠度不够 或 到期) / flags。"""
    day = str(as_of)[:10]
    applied, ignored, flags = [], [], []
    for rid, r in sorted(collapse(records).items()):
        if not _in_scope(r, ticker, archetype):
            continue
        if str(r.get("date")) > day:
            continue
        try:
            age = (date.fromisoformat(day) - date.fromisoformat(str(r["date"]))).days
        except ValueError:
            age = None
        until, flag = _resolve_until(r, next_print_day, next_tier1_day)
        if until is not None and until < day:
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
    """inputs.json 里 用户 手写的 news_overlay → 同一契约 (reliability=manual, 不过期)。"""
    out = []
    for i, o in enumerate(entries or []):
        drv = o["driver"]
        out.append({"id": f"manual-{ticker}-{i}-{drv}", "kind": "overlay", "date": o["date"], "ticker": ticker,
                    "source": o["source"], "reliability": "manual",
                    "channel": "eps" if drv in EPS_DRIVERS else "pe", "driver": drv, "delta": float(o["delta"]),
                    "valid_until": o.get("valid_until") or "2099-12-31", "note": o["note"]})
    return out


# ── 写入 (append-only, 校验通过才写) ────────────────────────────────

def add_record(r: dict, path: Path | None = None) -> dict:
    r = dict(r)
    r.setdefault("kind", "overlay")
    r.setdefault("as_of", now_et_iso())
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
    ap = argparse.ArgumentParser(description="新闻→估值驱动 overlay 契约 (ai_news/valuation/overlays.jsonl)")
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
        for k in ("direction", "confidence", "run_id", "id"):
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
