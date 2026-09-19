#!/usr/bin/env python3
"""iv_logger.py — 每日 ATM IV 快照 → market/iv_history/<TICKER>.jsonl

为什么存在: 6 条活规则 gate 在 IV percentile 上 (canonical 闸门表
options-strategy-framework §六), 但 OpenD 无 IV 历史 — percentile 只能自己攒。
每日跑一次 (overnight-briefing 步骤), 攒满 ~60 个交易日后
`--percentile` 输出真实百分位。

Universe: positions.json 全部期权 underlying + 显式 --tickers 追加。

CLI:
    .venv/moomoo/bin/python3 quant/data/iv_logger.py              # log today
    .venv/moomoo/bin/python3 quant/data/iv_logger.py --percentile NOK   # query
    .venv/moomoo/bin/python3 quant/data/iv_logger.py --tickers MU NOK --json
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 直接 `python3 quant/<层>/<模块>.py` 时让 `quant.*` 可 import

from quant.core.paths import add_openapi_path, IV_HISTORY_DIR, POSITIONS_PATH, WATCHLIST_PATH  # noqa: E402

from quant.core import env  # noqa: F401  (.env → os.environ)

import argparse
import json
import os
from datetime import date, timedelta

from quant.core.io_json import iter_jsonl, read_json  # noqa: E402
from quant.core.occ import is_option_ticker, parse_occ  # noqa: E402
from quant.core.timeutil import et_today, now_utc_iso  # noqa: E402

add_openapi_path()  # openapi skill scripts (`common`) 上 sys.path


def default_universe() -> list[str]:
    """期权 underlying 全集 + 实持正股 (positions.json) + watchlist 入场候选
    (moomoo_listed 非 false)。正股也要 IV 历史 (sell put / 加仓 vehicle 选择都
    gate 在 percentile 上); watchlist 候选的 IV 闸门在 entry_guard 下单时刻查 —
    漏掉它们会让入场 IV gate 跑 stale 数据 (修复, 源 = MU 卡用 6 天前 IV)。"""
    out: set[str] = set()
    if POSITIONS_PATH.exists():
        doc = read_json(POSITIONS_PATH)
        for tk, body in doc.get("tickers", {}).items():
            p = parse_occ(tk)
            if p:
                out.add(p["underlying"])
                continue
            broker = body.get("broker") or {}
            if (broker.get("qty") or 0) != 0 and not broker.get("synthetic") and "." not in tk:
                out.add(tk)
    if WATCHLIST_PATH.exists():
        doc = read_json(WATCHLIST_PATH)
        for tk, body in doc.get("tickers", {}).items():
            if body.get("moomoo_listed") is False or "." in tk or parse_occ(tk):
                continue
            out.add(tk)
    return sorted(out)


def fetch_atm_iv(ticker: str, target_dte: int = 30) -> dict | None:
    """nearest-to-30DTE expiry 的 ATM call IV + 现价。失败返回 None。"""
    from common import create_quote_context, safe_close, safe_float, safe_get, RET_OK

    code = f"US.{ticker}"
    ctx = None
    try:
        ctx = create_quote_context()
        ret, exps = ctx.get_option_expiration_date(code)
        if ret != RET_OK or exps is None or exps.empty:
            return None
        today = date.fromisoformat(et_today())
        best_exp, best_diff = None, 10**9
        for _, r in exps.iterrows():
            ds = str(safe_get(r, "strike_time", default=""))[:10]
            try:
                d = date.fromisoformat(ds)
            except ValueError:
                continue
            diff = abs((d - today).days - target_dte)
            if (d - today).days >= 7 and diff < best_diff:
                best_exp, best_diff = ds, diff
        if not best_exp:
            return None

        # spot via snapshot
        ret, snap = ctx.get_market_snapshot([code])
        spot = None
        if ret == RET_OK and snap is not None and not snap.empty:
            spot = safe_float(safe_get(snap.iloc[0], "last_price"), default=None)
        if not spot:
            return None

        from moomoo import OptionType
        ret, chain = ctx.get_option_chain(code, start=best_exp, end=best_exp,
                                          option_type=OptionType.CALL)
        if ret != RET_OK or chain is None or chain.empty:
            return None
        # nearest strike to spot
        chain = chain.copy()
        chain["_dist"] = (chain["strike_price"].astype(float) - spot).abs()
        atm = chain.sort_values("_dist").iloc[0]
        atm_code = str(safe_get(atm, "code", default=""))

        ret, q = ctx.get_market_snapshot([atm_code])
        iv = None
        if ret == RET_OK and q is not None and not q.empty:
            iv = safe_float(safe_get(q.iloc[0], "option_implied_volatility"), default=None)
        if iv is None or iv <= 0:
            # 盘前/无成交时 moomoo 快照返回 0.0 — 无效读数不落库 (MRVL 案)
            return None
        # moomoo returns IV as e.g. 71.2 (percent) — keep as percent
        return {
            "date": today.isoformat(),
            "logged_at": now_utc_iso(),
            "ticker": ticker,
            "spot": spot,
            "atm_strike": float(safe_get(atm, "strike_price", default=0)),
            "expiry": best_exp,
            "dte": (date.fromisoformat(best_exp) - today).days,
            "atm_iv_pct": iv,
            "source": "moomoo_option_chain",
        }
    except Exception as e:
        print(f"[iv_logger] {ticker}: {type(e).__name__}: {e}", file=sys.stderr)
        return None
    finally:
        safe_close(ctx)


def log_today(tickers: list[str]) -> list[dict]:
    IV_HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    # : 原为 date.today() = 系统本地 (SGT)。定时任务在 SGT 18:30 跑,
    # 那一刻 SGT 日 == ET 日, 所以三个月来每次都碰巧对; 换个时点跑 (人工预览 /
    # pipeline --read-only) SGT 已是明天 → date key 比 ET 交易日晚一天, 且幂等
    # 检查 `date == today` 同时失效 → 同一 ET 交易日写两行。
    # **IV 序列不可重拉 (AGENTS.md), 污染永久**。口径见 lib/timeutil.et_today()。
    today = et_today()
    rows = []
    for t in tickers:
        path = IV_HISTORY_DIR / f"{t}.jsonl"
        if any(r.get("date") == today for r in iter_jsonl(path)):
            print(f"[iv_logger] {t}: already logged today", file=sys.stderr)
            continue
        row = fetch_atm_iv(t)
        if row is None:
            continue
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        rows.append(row)
    return rows


def percentile(ticker: str, window_days: int = 252) -> dict:
    path = IV_HISTORY_DIR / f"{ticker}.jsonl"
    rows = [r for r in iter_jsonl(path) if (r.get("atm_iv_pct") or 0) > 0]
    cutoff = (date.fromisoformat(et_today()) - timedelta(days=window_days)).isoformat()
    rows = [r for r in rows if r.get("date", "") >= cutoff]
    if not rows:
        return {"ticker": ticker, "error": "no IV history — 先每日跑 iv_logger 攒数据"}
    latest = rows[-1]["atm_iv_pct"]
    below = sum(1 for r in rows if r["atm_iv_pct"] <= latest)
    n = len(rows)
    return {
        "ticker": ticker,
        "latest_iv_pct": latest,
        "samples": n,
        "iv_percentile": round(100 * below / n, 1),
        "reliable": n >= 60,
        "note": None if n >= 60 else f"仅 {n} 个样本 (<60), percentile 不可靠 — 闸门判断继续手查外部源并标注",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="每日 ATM IV 快照 / percentile 查询")
    ap.add_argument("--tickers", nargs="*", default=None, help="默认 = positions.json 期权 underlying 全集")
    ap.add_argument("--percentile", metavar="TICKER", help="查询模式: 该 ticker 的 IV percentile")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    if a.percentile:
        out = percentile(a.percentile.upper())
        print(json.dumps(out, ensure_ascii=False, indent=2) if a.json else
              f"{out.get('ticker')}: IV {out.get('latest_iv_pct')}% → percentile {out.get('iv_percentile')} "
              f"({out.get('samples')} samples{'' if out.get('reliable') else ' ⚠ <60 不可靠'})"
              if "error" not in out else f"{a.percentile}: {out['error']}")
        return 0

    tickers = [t.upper() for t in a.tickers] if a.tickers else default_universe()
    if not tickers:
        print("[iv_logger] empty universe", file=sys.stderr)
        return 1
    rows = log_today(tickers)
    if a.json:
        print(json.dumps({"logged": len(rows), "rows": rows}, ensure_ascii=False, indent=2))
    else:
        for r in rows:
            print(f"{r['ticker']}: ATM IV {r['atm_iv_pct']}% (spot {r['spot']}, {r['expiry']} DTE {r['dte']})")
        print(f"logged {len(rows)}/{len(tickers)} tickers → {IV_HISTORY_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
