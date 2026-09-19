#!/usr/bin/env python3
"""nav_snapshot.py — 每日账户 NAV 快照 → events/nav_history.jsonl

为什么存在: 没有 NAV 历史就永远回答不了 "组合 vs QQQ 跑赢多少" (analytics gap G6/G8)。
get_portfolio 每天都在拉这份数据, 只是从来没人存。每日一行, briefing 与 postclose 调用。

记录: total_assets / cash / market_val / power / risk_status (USD 口径)
    + QQQ / SPY 收盘 (基准列, OpenD snapshot)。

Same-day reruns refresh the existing row instead of skipping or appending. This
keeps one row per ET date while letting the close-dependent run replace an earlier
pre-session fallback. Benchmark values are sampled in the same refresh.

CLI:
    .venv/moomoo/bin/python3 quant/data/nav_snapshot.py [--json]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 直接 `python3 quant/<层>/<模块>.py` 时让 `quant.*` 可 import

from quant.core.paths import add_openapi_path, NAV_HISTORY_PATH  # noqa: E402

from quant.core import env  # noqa: F401  (.env → os.environ)

import argparse
import json
import os
from datetime import date

from quant.core.io_json import iter_jsonl  # noqa: E402
from quant.core.timeutil import et_today, now_utc_iso  # noqa: E402

add_openapi_path()  # openapi skill scripts (`common`) 上 sys.path
from common import (# noqa: E402
    create_trade_context, create_quote_context, safe_close, safe_float, safe_get,
    get_default_acc_id, get_default_security_firm, parse_trd_env, RET_OK,
)

REAL = parse_trd_env('REAL')


def fetch_account_usd() -> dict | None:
    ctx = create_trade_context(security_firm=get_default_security_firm())
    try:
        # currency 必须显式 USD — FUTUSG 服务端默认 HKD 换算 (tools-environment.md 硬规则)
        from moomoo import Currency
        ret, data = ctx.accinfo_query(trd_env=REAL, acc_id=get_default_acc_id(), currency=Currency.USD)
        if ret != RET_OK or data is None or data.empty:
            print(f"[nav] accinfo_query failed: {data}", file=sys.stderr)
            return None
        r = data.iloc[0]
        return {
            "total_assets": safe_float(safe_get(r, "total_assets"), default=None),
            "cash": safe_float(safe_get(r, "cash"), default=None),
            "market_val": safe_float(safe_get(r, "market_val"), default=None),
            "power": safe_float(safe_get(r, "power"), default=None),
            "risk_status": str(safe_get(r, "risk_status", default="")),
            "currency": "USD",
        }
    finally:
        safe_close(ctx)


def fetch_benchmarks() -> dict:
    out = {}
    ctx = None
    try:
        ctx = create_quote_context()
        ret, snap = ctx.get_market_snapshot(["US.QQQ", "US.SPY"])
        if ret == RET_OK and snap is not None and not snap.empty:
            for _, r in snap.iterrows():
                code = str(safe_get(r, "code", default="")).replace("US.", "")
                out[f"{code.lower()}_close"] = safe_float(safe_get(r, "last_price"), default=None)
    except Exception as e:
        print(f"[nav] benchmark fetch failed: {e}", file=sys.stderr)
    finally:
        safe_close(ctx)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="每日 NAV 快照")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    # : date.today() = 系统本地日期 (SGT), 不是 ET 交易日历日。
    # 定时任务在 SGT 18:30 跑, 那一刻 SGT 日 == ET 日, 所以三个月来每次都碰巧对;
    # 一旦在别的时点跑 (人工预览 / pipeline --read-only), SGT 已经是明天 →
    # 写出比 ET 交易日晚一天的 date key, 且幂等检查 `date == today` 同时失效 →
    # 同一个 ET 交易日被写两行。IV 序列不可重拉 (AGENTS.md), 污染是永久的。
    # 口径见 lib/timeutil.et_today() docstring。
    today = et_today()
    rows = list(iter_jsonl(NAV_HISTORY_PATH))
    refreshing = any(r.get("date") == today for r in rows)

    acct = fetch_account_usd()
    if acct is None:
        return 1
    row = {"date": today, "logged_at": now_utc_iso(), **acct, **fetch_benchmarks()}
    if refreshing:
        row["refreshed"] = True          # 同日重写留痕: 这一行不是当日首笔读数
    NAV_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    if refreshing:
        # Same-day refresh replaces the row; the latest scheduled observation wins.
        rows = [row if r.get("date") == today else r for r in rows]
        tmp = NAV_HISTORY_PATH.with_suffix(".jsonl.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        os.replace(tmp, NAV_HISTORY_PATH)
    else:
        with NAV_HISTORY_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(json.dumps(row, ensure_ascii=False, indent=2) if a.json
          else f"[nav] {today}{' (refreshed)' if refreshing else ''}: "
               f"total ${row['total_assets']:,.0f} cash ${row['cash']:,.0f} "
               f"power ${row['power']:,.0f} | QQQ {row.get('qqq_close')} SPY {row.get('spy_close')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
