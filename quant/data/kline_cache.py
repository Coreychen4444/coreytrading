#!/usr/bin/env python3
"""kline_cache.py — 日线 K 线本地增量缓存 → market/klines/<TICKER>.jsonl

为什么存在: P4 回测 (zone hit rate / ladder 实证) 需要可重复的本地历史;
OpenD 每次现拉既慢又不可复现。增量: 只补 last cached date 之后的 bar。

Universe: positions.json 全部 ticker (期权取 underlying) + watchlist + BENCHMARKS/指数硬名单。

CLI:
    .venv/moomoo/bin/python3 quant/data/kline_cache.py                # incremental update
    .venv/moomoo/bin/python3 quant/data/kline_cache.py --backfill 365 # 首次回填一年
    .venv/moomoo/bin/python3 quant/data/kline_cache.py --tickers MU NOK
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 直接 `python3 quant/<层>/<模块>.py` 时让 `quant.*` 可 import

from quant.core import env  # noqa: F401  (.env → os.environ)

import argparse
import json
from datetime import date, timedelta

from quant.core.io_json import iter_jsonl, read_json  # noqa: E402
from quant.core.num import coerce_float  # noqa: E402
from quant.core.occ import parse_occ  # noqa: E402
from quant.core.paths import KLINE_CACHE_DIR, POSITIONS_PATH, WATCHLIST_PATH  # noqa: E402
from quant.core.timeutil import et_today  # noqa: E402

from quant.data.market_data import get_kline  # noqa: E402

BENCHMARKS = ["QQQ", "SPY", "SMH", "SOXX"]   # SMH/SOXX: 半导体板块量能 gauge (gate_ladder_report 板块行)
# 基准名单是**硬名单**: 参考票不再靠 watchlist 成员资格被顺带拉到 K 线 ——
# 数据层与 watchlist membership 解耦 (watchlist 恒为正股入场候选)。

# 指数 gauge (用户 : 大盘每日涨跌读数)。指数不可交易、被 watchlist_sync
# 跳过不建卡 — 只进数据层 + 报表表头。
# ⚠️ 数据源考古 (三连探针): OpenD request_history_kline / get_market_snapshot
# 对美股指数一律 "US stock indices are not supported"; Stooq CSV 端点返回反爬 HTML。
# 最终源 = Yahoo chart JSON (结构化端点, 四指数全覆盖, 收盘后即有当日终值; SPX 用 ^GSPC)。
# (宏观三票 TNX/DXY/BTC 曾入列, 随情报层收拢 ai-news-radar 摘除 —
# 用户: 本仓只做技术分析+风控, 宏观读数不再采集; UTC 日历守卫 YH_UTC_DATED 随 BTC 一并退役。)
INDEX_YH = {"NDX": "^NDX", "SPX": "^GSPC", "SOX": "^SOX", "VIX": "^VIX"}

# 定型时刻特例 (ET wall-clock, 缺省 16:05)。VIX 16:15 结算取 16:20 ( 案)。
YH_CUTOFF = {"VIX": "16:20"}


def _yahoo_daily(sym: str, start: str, tz_name: str = "America/New_York") -> list[dict]:
    """Yahoo chart API 日线 → bar dicts (与 OpenD kline 同形)。失败抛异常, 由 main 的
    per-ticker try 隔离 — 断网不影响正股更新。盘中的当日进行中 bar 由 update_ticker
    的定型时刻守卫统一丢弃 (与正股同一契约: 缓存只含已完成日线)。
    tz_name: bar 日期标注时区, 美股日历票用 ET (缺省)。"""
    import urllib.parse
    import urllib.request
    from datetime import datetime
    from zoneinfo import ZoneInfo
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/"
           f"{urllib.parse.quote(sym)}?range=1y&interval=1d")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        payload = json.loads(resp.read())
    res = payload["chart"]["result"][0]
    ts = res.get("timestamp") or []
    q = (res.get("indicators") or {}).get("quote", [{}])[0]
    et = ZoneInfo(tz_name)
    out = []
    for i, t in enumerate(ts):
        c = (q.get("close") or [None])[i] if i < len(q.get("close") or []) else None
        if c is None:
            continue
        d = datetime.fromtimestamp(t, tz=et).date().isoformat()
        if d < start:
            continue
        def _f(key):
            v = (q.get(key) or [])
            return float(v[i]) if i < len(v) and v[i] is not None else float(c)
        out.append({"time": f"{d} 00:00:00", "open": _f("open"), "high": _f("high"),
                    "low": _f("low"), "close": float(c),
                    "volume": int(_f("volume")) if q.get("volume") else 0, "turnover": 0.0})
    return out


def default_universe() -> list[str]:
    out: set[str] = set(BENCHMARKS) | set(INDEX_YH)
    for path in (POSITIONS_PATH, WATCHLIST_PATH):
        if not path.exists():
            continue
        for tk, body in read_json(path).get("tickers", {}).items():
            p = parse_occ(tk)
            u = p["underlying"] if p else tk
            if "." not in u:  # 跳过非美股 (SIVE.ST 等, OpenD 无数据)
                out.add(u)
            # 活仓期权合约本身也进刷新集 (用户 抓漏 GOOGL LEAPS
            # "无缓存 K 线"): 期权卡  起按 premium 口径画线, gate 阶梯
            # 表读合约自己的日线 —— 只折 underlying 会让合约缓存永不刷新, 首日
            # 报"无缓存"、手拉之后则**静默用旧 close**。只收 qty≠0: 到期/平仓
            # 合约会退市, 拉了只收 API 错 (历史合约归档走 option_klines/)。
            if p and coerce_float((body.get("broker") or {}).get("qty")):
                out.add(tk)
    return sorted(out)


def last_cached_date(path: Path) -> str | None:
    last = None
    for r in iter_jsonl(path):
        t = str(r.get("time", ""))[:10]
        if t and (last is None or t > last):
            last = t
    return last


def _et_hhmm() -> str:
    """ET wall-clock 'HH:MM' — update_ticker 的定型时刻判据。独立函数以便测试注入。"""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("America/New_York")).strftime("%H:%M")


def update_ticker(ticker: str, backfill_days: int) -> int:
    KLINE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = KLINE_CACHE_DIR / f"{ticker}.jsonl"
    last = last_cached_date(path)
    start = (date.fromisoformat(last) + timedelta(days=1)).isoformat() if last \
        else (date.fromisoformat(et_today()) - timedelta(days=backfill_days)).isoformat()
    end = et_today()
    if start > end:
        return 0
    if ticker in INDEX_YH:
        bars = _yahoo_daily(INDEX_YH[ticker], start)
    else:
        bars = get_kline(ticker, ktype="1d", start=start, end=end)
    new = [b for b in bars if str(b.get("time", ""))[:10] >= start]
    # 当日那根 bar 只有在它的 session 完整结束后才定型 —— 之前它的 close 只是
    # 拉数据那一刻的现价。缓存的契约是"**已完成**的日线", 全部下游
    # (regime_transition / exit_guard 棘轮与趋势线 / entry_guard gate 计数 /
    # zone staleness) 都把 rows[-1] 读作昨收。
    #  ET 13:56 盘中手工跑污染了 NBIS 卡片 → 加了 et_session_open() 闸。
    # 但那道闸只盖 09:30-16:00 常规时段, 而 ^VIX 从 03:15 ET 就开始播值:
    #  06:31 ET (盘前) briefing 轮把 VIX 当日进行中读数 16.35 当日线
    # 落了库 —— NDX/SPX/SOX 没中招只因 Yahoo 盘前不播股指当日 row, 是运气不是
    # 机制 (同夜盘基准案: 守卫只盖一个窗口, 失效存在于更宽的窗口)。
    # 判据改为**定型时刻**: ET 时刻 < cutoff → 丢一切当日 bar, 与处在哪个 session
    # 无关。VIX 16:15 ET 结算取 16:20; 其余 16:05 —— postclose (16:10 ET) 依赖
    # 当日正股收盘, 不能一刀切 16:20, 代价是 VIX 当日 bar 次晨 briefing 轮才入库。
    # 特例表 YH_CUTOFF (VIX 16:20)。
    if _et_hhmm() < YH_CUTOFF.get(ticker, "16:05"):
        cutoff = YH_CUTOFF.get(ticker, "16:05")
        today = et_today()
        dropped = [b for b in new if str(b.get("time", ""))[:10] >= today]
        if dropped:
            print(f"[kline_cache] {ticker}: 未过定型时刻 {cutoff} ET, 丢弃当日未完成 "
                  f"bar {today} (C={dropped[0].get('close')} 是现价不是收盘)", file=sys.stderr)
        new = [b for b in new if str(b.get("time", ""))[:10] < today]
    if not new:
        return 0
    with path.open("a", encoding="utf-8") as f:
        for b in new:
            f.write(json.dumps(b, ensure_ascii=False) + "\n")
    return len(new)


def main() -> int:
    ap = argparse.ArgumentParser(description="日线缓存增量更新")
    ap.add_argument("--tickers", nargs="*", default=None)
    ap.add_argument("--backfill", type=int, default=365, help="首次缓存回填天数 (默认 365)")
    a = ap.parse_args()

    tickers = [t.upper() for t in a.tickers] if a.tickers else default_universe()
    total = 0
    for t in tickers:
        try:
            n = update_ticker(t, a.backfill)
        except SystemExit:
            raise
        except Exception as e:
            print(f"[kline_cache] {t}: {type(e).__name__}: {e}", file=sys.stderr)
            continue
        if n:
            print(f"{t}: +{n} bars")
        total += n
    print(f"[kline_cache] {total} new bars across {len(tickers)} tickers → {KLINE_CACHE_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
