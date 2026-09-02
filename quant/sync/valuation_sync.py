#!/usr/bin/env python3
"""valuation_sync.py — valuations.jsonl 最新一行 → agent.valuation 机器区 (卡片是简报读的地方)。

为什么存在 (用户): 「估值结果在哪里呈现」—— 估值层只有 CLI 与账本, 而 用户 每天看的是
简报里的卡片与 /trading-analysis 的 thesis。本模块把估值账本镜像成卡片机器区 `agent.valuation`
(与 company_profile 同一哲学: 机器区由 sync 覆盖, 人工区永不触碰), 简报从卡上取 `fyi` 一行原样播报。

为什么读账本而不 import 估值层: 分层 (sync rank 2 不能 import valuation rank 5), 也让估值层保持
「不写 state」的零授权不变量 —— 估值算, 账本记, sync 镜像, 三件事三个模块。

块内容 (全部来自账本行, 不重算):
  as_of (账本时间的 ET 日期) · price · fair_value · verdict · ratio · confidence · archetype ·
  implied_cagr / implied_horizon_years / implied_target_margin / implied_cagr_at_current_margin ·
  growth_creates_value · news_applied · fyi (简报照抄的一行) · source (账本行时间戳) · synced_at

🚨 零授权效力: 不被 sizing/entry_guard/exit_guard/today_group 读; fyi 行不含股数/金额/cap;
低估 ≠ GO, 高估 ≠ SELL。not_applicable 与账本无行的票不写块 (已有旧块保留不删, 由 as_of 暴露陈旧)。

CLI:
    python3 quant/sync/valuation_sync.py [--tickers T ...] [--json]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 直接 `python3 quant/<层>/<模块>.py` 时让 `quant.*` 可 import

import argparse
import json

from quant.core.io_json import iter_jsonl, state_lock  # noqa: E402
from quant.core.paths import VALUATIONS_PATH  # noqa: E402
from quant.core.timeutil import now_utc_iso  # noqa: E402
from quant.sync import state_io  # noqa: E402
from quant.sync.company_profile_sync import target_cards  # noqa: E402


def latest_rows(path: Path | None = None) -> dict[str, dict]:
    """每票最新一条**非 skipped** 估值行 (账本 append-only, 后写的赢)。"""
    out: dict[str, dict] = {}
    for r in iter_jsonl(Path(path or VALUATIONS_PATH)):
        if r.get("skipped") or not r.get("ticker") or r.get("fair_value") is None:
            continue
        out[str(r["ticker"]).upper()] = r
    return out


def _pct(x, d=0):
    return "—" if x is None else f"{x*100:+.{d}f}%"


def fyi_line(row: dict) -> str:
    """简报依据行尾的一句 (判断层词汇, 无股数/金额/cap)。与 valuate.digest 同一口径, 只是更短。"""
    st = row.get("street") or {}
    dcf = row.get("dcf_per_share")
    split = (f" (DCF ${dcf:,.0f} / 街 ${st['per_share']:,.0f})" if (st.get("per_share") and dcf is not None) else "")
    parts = [f"📐[FYI] 估值 {row.get('verdict')} {_pct(row.get('ratio'))} (置信{row.get('confidence')})",
             f"公允 ${row['fair_value']:,.0f} vs ${row['price']:,.0f}{split}"]
    if row.get("growth_creates_value") is False:
        parts.append("增长不创造价值, 看利润率")
    elif row.get("implied_cagr") is not None:
        parts.append(f"要求年2起 CAGR {_pct(row['implied_cagr'])}")
    spec = row.get("spec") or {}
    reverting = (abs(float(spec.get("target_margin") or 0) - float(spec.get("margin_start") or 0)) > 0.02
                 or float(spec.get("revenue_level_ratio") or 1.0) != 1.0)
    if row.get("implied_cagr_at_current_margin") is not None and reverting:   # 只有利润率/收入在回归的票这句才有信息
        parts.append(f"峰值不回归需 {_pct(row['implied_cagr_at_current_margin'])}")
    n = len(((row.get("news") or {}).get("applied")) or [])
    if n:
        parts.append(f"新闻 {n} 条")
    parts.append("零授权")
    return " · ".join(parts)


def block_from_row(row: dict) -> dict:
    return {
        "as_of": str(row.get("timestamp") or "")[:10],
        "price": row.get("price"), "fair_value": row.get("fair_value"), "verdict": row.get("verdict"),
        "ratio": row.get("ratio"), "confidence": row.get("confidence"), "archetype": row.get("archetype"),
        "implied_cagr": row.get("implied_cagr"), "implied_horizon_years": (row.get("spec") or {}).get("horizon_years"),
        "implied_target_margin": row.get("implied_target_margin"),
        "implied_cagr_at_current_margin": row.get("implied_cagr_at_current_margin"),
        "growth_creates_value": row.get("growth_creates_value"),
        "news_applied": len(((row.get("news") or {}).get("applied")) or []),
        "fyi": fyi_line(row),
        "source": f"valuations.jsonl {row.get('timestamp')}",
        "synced_at": now_utc_iso(),
    }


def sync_cards(pos_doc: dict, wl_doc: dict, rows: dict[str, dict], *, only: set[str] | None = None) -> dict:
    """对账式幂等: 卡上 source 已等于账本行 → 不动 (synced_at 不刷, 让 touched 诚实)。"""
    synced, missing = [], []
    touched = {"positions": False, "watchlist": False}
    docs = {"positions": pos_doc, "watchlist": wl_doc}
    for bucket, tk in target_cards(pos_doc, wl_doc):
        if only and tk not in only:
            continue
        row = rows.get(tk)
        if row is None:
            missing.append(f"{bucket}:{tk}")
            continue
        card = docs[bucket]["tickers"][tk]
        agent = card.get("agent")
        if not isinstance(agent, dict):
            agent = {}
            card["agent"] = agent
        block = block_from_row(row)
        prev = agent.get("valuation") or {}
        if prev.get("source") == block["source"] and prev.get("fyi") == block["fyi"]:
            continue
        agent["valuation"] = block
        touched[bucket] = True
        synced.append(f"{bucket}:{tk}")
    return {"synced": synced, "missing": missing, "touched": touched}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="valuations.jsonl → agent.valuation 机器区")
    ap.add_argument("--tickers", nargs="*")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    rows = latest_rows()
    with state_lock():
        pos = state_io.read_positions()
        wl = state_io.read_watchlist()
        res = sync_cards(pos, wl, rows, only=set(t.upper() for t in a.tickers) if a.tickers else None)
        if res["touched"]["positions"]:
            state_io.write_positions(pos)
        if res["touched"]["watchlist"]:
            state_io.write_watchlist(wl)
    if a.json:
        print(json.dumps(res, ensure_ascii=False))
    else:
        print(f"[valuation_sync] synced {len(res['synced'])}: {', '.join(res['synced']) or '-'}")
        if res["missing"]:
            print(f"[valuation_sync] 账本无行 (未估值或不适用): {', '.join(res['missing'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
