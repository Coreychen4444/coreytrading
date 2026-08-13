#!/usr/bin/env python3
"""Pull multi-year daily bars for the gate backtest.

Why a separate puller instead of kline_cache.py: kline_cache is incremental
maintenance for live cards (13 months, holdings + watchlist only). A gate
backtest needs the opposite shape — deep history across a DEcorrelated
universe. The live cache is 32 tickers of almost pure AI/semi over one macro
cycle: episodes there fire simultaneously, so N≈300 ticker-days is really
N≈4 independent events and any fitted threshold is cycle-fitting.

Writes to market/klines_deep/ (separate dir, gitignored like klines/) so the
live cache keeps its own 13-month contract.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 直接 `python3 quant/<层>/<模块>.py` 时让 `quant.*` 可 import

import json
import time

from quant.core import env  # noqa: F401  (.env → os.environ)
from quant.data.market_data import get_kline  # noqa: E402

from quant.core.paths import KLINE_DEEP_DIR as OUT  # noqa: E402
#  数据扩容 (用户 批准): 2020→2005 起拉, round-2 回测要跨宏观周期抬 Kish N。
# 同名票 30 日滚动 quota 不重复计费, 深化 = 免费; 新增票走 pull_stooq_history.py (research-only)。
# END 固定  = round-1 口径, 保证复刻段可比。
START, END = "2005-01-01", "2026-07-24"
# 首根 bar 早于此 = 已深化, 跳过。**由  改**: OpenD 的日线
# 最早只到  (SPY/QQQ/大盘股实测), 所以旧值让这条判据**永远为假**——
# 每次运行重拉全部 57 票 (实测 "0 already deep"), 注释写的「晚上市票每次重拉」
# 实际是「全部每次重拉」。同票 30 日滚动 quota 不重复计费故无成本, 只是慢。
DEEP_BEFORE = "2006-08-31"

# Held / traded / watchlist — the names the rule will actually be applied to.
TRADED = ["INTC", "MU", "NBIS", "MRVL", "AAOI", "LITE", "NOK", "RKLB", "AXTI",
          "AMKR", "CRWV", "QCOM", "NVDA", "SNDK", "WDC",
          # COHR  补: 它是 live watchlist 卡且带 gate, 而 gate_ladder_report
          # 的 deep_bars() 判「文件不存在 → 回落旧线名规则」—— 缺深度缓存时 gate 的
          # 结构身份 (①修复/②突破/③箱体顶/④新高) 与 ⚡ 快车道适用域都不是判出来的,
          # 是启发式猜的。光模块同族 (LITE/AAOI) 本来就在池内。
          "COHR"]
# Semis/AI beyond the book — same mechanism, different names.
SEMI = ["AMD", "TSM", "AVGO", "ASML", "LRCX", "AMAT", "KLAC", "MCHP", "ON",
        "SWKS", "QRVO", "TER", "ADI", "TXN"]
# Megacap tech — different beta, same era.
MEGA = ["AAPL", "MSFT", "GOOGL", "AMZN", "META", "TSLA"]
# Non-tech — the decorrelation payload. Their drawdowns are driven by
# different things (rates, oil, litigation), so a gate that works across
# these too is a structural rule, not a 2025-26 AI-cycle artifact.
OTHER = ["XOM", "CVX", "JPM", "BAC", "WMT", "PFE", "MRK", "CAT", "BA", "DIS",
         "NKE", "KO", "UNH", "GE", "F"]
INDEX = ["SPY", "QQQ", "SMH", "IWM", "XLE", "XLF"]

UNIVERSE = TRADED + SEMI + MEGA + OTHER + INDEX


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    ok = fail = skip = 0
    for i, tk in enumerate(UNIVERSE, 1):
        path = OUT / f"{tk}.jsonl"
        if path.exists():
            # 深度感知跳过: 首根 bar 已早于 DEEP_BEFORE 才算深化完成。
            # 晚上市票 (NBIS/CRWV/RKLB...) 首根永远晚于 2005 → 每次重拉, 幂等且同票不耗新 quota。
            first = path.read_text().split("\n", 1)[0]
            if first.strip() and json.loads(first).get("time", "9999")[:10] <= DEEP_BEFORE:
                skip += 1
                continue
        try:
            rows = get_kline(tk, ktype="1d", start=START, end=END)
        except Exception as e:                                   # noqa: BLE001
            print(f"[{i}/{len(UNIVERSE)}] {tk}: ERROR {type(e).__name__}: {e}", flush=True)
            fail += 1
            continue
        if not rows:
            print(f"[{i}/{len(UNIVERSE)}] {tk}: no data", flush=True)
            fail += 1
            continue
        path.write_text(
            "\n".join(json.dumps({k: r.get(k) for k in
                                  ("time", "open", "high", "low", "close", "volume",
                                   "turnover")},   # turnover: cluster-gate HVN 加权用 ()
                                 ensure_ascii=False) for r in rows) + "\n",
            encoding="utf-8")
        print(f"[{i}/{len(UNIVERSE)}] {tk}: {len(rows)} bars "
              f"{rows[0]['time'][:10]} → {rows[-1]['time'][:10]}", flush=True)
        ok += 1
        time.sleep(0.6)   # OpenD history quota is per-30-days-rolling; don't burst
    print(f"\ndone: {ok} pulled, {skip} already deep, {fail} failed → {OUT}")
    return 0 if ok or skip else 1


if __name__ == "__main__":
    raise SystemExit(main())
