#!/usr/bin/env python3
"""macro_series — 跨资产宏观日线（Yahoo chart + FRED）→ trade/market/macro_daily/<KEY>.jsonl

宏观层唯一的取数口：回测与每日判读读同一份缓存，同一个 KEY 同一个源，
「研究口径 = 渲染口径」。价格一律结构化端点（Yahoo chart JSON / FRED CSV），不走 WebSearch。

缓存语义（与 klines/ 的 append-only 不同，刻意的）：
  每次更新重拉最近 TAIL_DAYS 天并**覆盖尾部**。期货 / 外汇 / 加密在美股收盘后仍在交易，
  收盘后跑的那根当日 bar 不是终值 —— 覆盖尾部让它在下一轮自动被终值替换，不需要逐品种
  的定型时刻表；下游一律读 `load()` 的 (date, value) 序列，不假设最后一行已定型。

值口径：
  ETF 用 adjclose（含分红的总回报；HYG / TLT 这类高派息品种不复权会凭空多出下行漂移）；
  指数 / 期货 / 外汇 / 收益率用 close。^TNX / ^FVX / ^IRX 两种口径并存（老约定 = 收益率×10），
  >20 视为 ×10 口径再除（写死 ÷10 会在另一种口径上输出 0.46% 这类荒谬但不报错的数字）。

CLI:
    .venv/moomoo/bin/python3 quant/data/macro_series.py                 # 增量（尾部覆盖）
    .venv/moomoo/bin/python3 quant/data/macro_series.py --backfill <start-date>
    .venv/moomoo/bin/python3 quant/data/macro_series.py --keys WTI US10Y
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 直接 `python3 quant/<层>/<模块>.py` 时让 `quant.*` 可 import

import argparse
import json
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from quant.core.io_json import iter_jsonl
from quant.core.paths import MACRO_DAILY_DIR

TAIL_DAYS = 12            # 每轮覆盖的尾部日历天数（覆盖周末 + 一个长假）
YIELD_INDEX = {"^TNX", "^FVX", "^IRX", "^TYX"}

# KEY → (源, 代码, 值口径)。源 ∈ {yahoo, fred}；值口径 ∈ {adj, close}。
# 新增品种只改这里；下游因子定义在 core/macro_factors.py。
SERIES: dict[str, tuple[str, str, str]] = {
    # ── 股票（宽基 / 风格 / 行业）
    "SPY": ("yahoo", "SPY", "adj"),
    "QQQ": ("yahoo", "QQQ", "adj"),
    "IWM": ("yahoo", "IWM", "adj"),
    "RSP": ("yahoo", "RSP", "adj"),
    "SMH": ("yahoo", "SMH", "adj"),
    "XLK": ("yahoo", "XLK", "adj"),
    "XLE": ("yahoo", "XLE", "adj"),
    "XLF": ("yahoo", "XLF", "adj"),
    "XLU": ("yahoo", "XLU", "adj"),
    "XLP": ("yahoo", "XLP", "adj"),
    "XLY": ("yahoo", "XLY", "adj"),
    "XLV": ("yahoo", "XLV", "adj"),
    "XLI": ("yahoo", "XLI", "adj"),
    "XLB": ("yahoo", "XLB", "adj"),
    "KRE": ("yahoo", "KRE", "adj"),
    "XLC": ("yahoo", "XLC", "adj"),            # 通信（序列自 2018 年起）—— 广度描述行的 11 个行业
    "XLRE": ("yahoo", "XLRE", "adj"),          # 地产（序列自 2015 年起）
    # ── 利率（收益率指数 = 百分比；期货 / ETF = 价格）
    "US10Y": ("yahoo", "^TNX", "close"),
    "US5Y": ("yahoo", "^FVX", "close"),
    "US3M": ("yahoo", "^IRX", "close"),
    "ZT": ("yahoo", "ZT=F", "close"),          # 2 年期美债期货（价格，与 2Y 收益率反向）
    "TLT": ("yahoo", "TLT", "adj"),
    "SHY": ("yahoo", "SHY", "adj"),            # 1–3 年美债 ETF：2Y 因子的无换月、与美股同收盘口径（v1.1）
    "IEF": ("yahoo", "IEF", "adj"),
    "TIP": ("yahoo", "TIP", "adj"),
    "MOVE": ("yahoo", "^MOVE", "close"),       # 美债隐含波动率
    # ── 信用
    "HYG": ("yahoo", "HYG", "adj"),
    "LQD": ("yahoo", "LQD", "adj"),
    # ── 商品
    "WTI": ("yahoo", "CL=F", "close"),
    "USO": ("yahoo", "USO", "adj"),
    "GOLD": ("yahoo", "GC=F", "close"),
    "GLD": ("yahoo", "GLD", "adj"),            # 黄金因子（v1.1：与美股同收盘，无换月）
    "COPPER": ("yahoo", "HG=F", "close"),
    # ── 外汇
    "DXY": ("yahoo", "DX-Y.NYB", "close"),
    "USDJPY": ("yahoo", "USDJPY=X", "close"),
    "USDCNY": ("yahoo", "USDCNY=X", "close"),
    # ── 波动率
    "VIX": ("yahoo", "^VIX", "close"),
    "VIX3M": ("yahoo", "^VIX3M", "close"),
    # ── 加密
    "BTC": ("yahoo", "BTC-USD", "close"),
    # ── FRED（官方口径；T 日数值 T+1 才发布 —— 只作水平读数，不进同日归因）
    "DGS2": ("fred", "DGS2", "close"),
    "DGS10": ("fred", "DGS10", "close"),
    "T10YIE": ("fred", "T10YIE", "close"),     # 10 年盈亏平衡通胀
    "DFII10": ("fred", "DFII10", "close"),     # 10 年实际利率
    "HY_OAS": ("fred", "BAMLH0A0HYM2", "close"),  # 高收益利差（FRED 只给近 3 年）
}

_YAHOO = ("https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
          "?period1={p1}&period2={p2}&interval=1d&includeAdjustedClose=true")
_FRED = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}&cosd={start}"


def _get(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    last = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except Exception as e:  # 网络抖动重试两次；最终失败交给调用方隔离
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise last  # type: ignore[misc]


def parse_yahoo(payload: dict, value: str) -> list[dict]:
    """Yahoo chart JSON → [{date, value}]（按交易所时区取日期；同日多根取最后一根）。"""
    res = payload["chart"]["result"][0]
    ts = res.get("timestamp") or []
    tz = ZoneInfo((res.get("meta") or {}).get("exchangeTimezoneName") or "America/New_York")
    ind = res.get("indicators") or {}
    closes = (ind.get("quote") or [{}])[0].get("close") or []
    adj = ((ind.get("adjclose") or [{}])[0].get("adjclose") or []) if value == "adj" else []
    sym = (res.get("meta") or {}).get("symbol", "")
    by_date: dict[str, float] = {}
    for i, t in enumerate(ts):
        v = adj[i] if value == "adj" and i < len(adj) and adj[i] is not None else \
            (closes[i] if i < len(closes) else None)
        if v is None:
            continue
        v = float(v)
        if sym in YIELD_INDEX and v > 20:     # 老口径 = 收益率 ×10
            v = v / 10.0
        d = datetime.fromtimestamp(t, tz=tz).date().isoformat()
        by_date[d] = v
    return [{"date": d, "value": by_date[d]} for d in sorted(by_date)]


def parse_fred(text: str) -> list[dict]:
    out = []
    for line in text.strip().splitlines()[1:]:
        parts = line.split(",")
        if len(parts) != 2:
            continue
        try:
            out.append({"date": parts[0].strip(), "value": float(parts[1])})
        except ValueError:
            continue    # '.' = 缺值
    return out


def fetch(key: str, start: str) -> list[dict]:
    source, code, value = SERIES[key]
    if source == "fred":
        return parse_fred(_get(_FRED.format(sid=code, start=start)).decode("utf-8", "replace"))
    p1 = int(datetime.fromisoformat(start).replace(tzinfo=timezone.utc).timestamp())
    p2 = int(time.time()) + 86400
    payload = json.loads(_get(_YAHOO.format(sym=urllib.parse.quote(code), p1=p1, p2=p2)))
    return [r for r in parse_yahoo(payload, value) if r["date"] >= start]


def path_of(key: str) -> Path:
    return MACRO_DAILY_DIR / f"{key}.jsonl"


def load(key: str, root: Path | None = None) -> list[tuple[str, float]]:
    """[(date, value)] 升序。缺文件 = []（调用方决定缺数据怎么报）。"""
    p = (root or MACRO_DAILY_DIR) / f"{key}.jsonl"
    rows = {r["date"]: float(r["value"]) for r in iter_jsonl(p)
            if r.get("date") and r.get("value") is not None}
    return sorted(rows.items())


def merge(existing: list[dict], fresh: list[dict]) -> list[dict]:
    """fresh 覆盖同日期；fresh 最早日期之后、fresh 里不存在的旧行删除（供方撤回的 bar 不残留）。"""
    if not fresh:
        return existing
    first = fresh[0]["date"]
    kept = [r for r in existing if r["date"] < first]
    return kept + fresh


def update(key: str, backfill: str | None = None, root: Path | None = None) -> int:
    root = root or MACRO_DAILY_DIR
    root.mkdir(parents=True, exist_ok=True)
    p = root / f"{key}.jsonl"
    existing = list(iter_jsonl(p))
    if backfill or not existing:
        start = backfill or "2000-01-01"
    else:
        last = max(r["date"] for r in existing)
        start = (date.fromisoformat(last) - timedelta(days=TAIL_DAYS)).isoformat()
    fresh = fetch(key, start)
    rows = merge([] if backfill else existing, fresh)
    tmp = p.with_suffix(".jsonl.tmp")
    tmp.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    tmp.replace(p)
    return len(fresh)


RATE_SOURCES = (("DGS10", "FRED DGS10"), ("US10Y", "Yahoo ^TNX"))


def rate_document(key: str, rows) -> dict | None:
    """一个 10 年期源的最新观测 → 估值层 `market_rate` 认得的文档形状；rows = [(date, value)] 或 [{date, value}]。"""
    pts = [(r["date"], r["value"]) if isinstance(r, dict) else tuple(r) for r in rows or []]
    pts = [(d, v) for d, v in pts if d and isinstance(v, (int, float))]
    if not pts:
        return None
    day, value = max(pts)
    label = dict(RATE_SOURCES)[key]
    return {"metrics": {"US10Y": {"value": float(value), "as_of": day, "source": label,
                                  "unit": "percent", "freshness": "cached_daily_close"}},
            "source_key": key}


def rate_documents(root: Path | None = None) -> list[dict]:
    """10 年期收益率观测（每个源一份）。估值的无风险利率读本缓存。
    FRED DGS10 = 官方口径（T+1 发布）；Yahoo ^TNX = 同日收盘。`market_rate` 按观测时点取最新。"""
    return [d for d in (rate_document(k, load(k, root)) for k, _ in RATE_SOURCES) if d]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="跨资产宏观日线缓存（尾部覆盖式增量）")
    ap.add_argument("--keys", nargs="*", default=None)
    ap.add_argument("--backfill", default=None, help="YYYY-MM-DD：从该日起整段重拉")
    a = ap.parse_args(argv)
    keys = [k.upper() for k in a.keys] if a.keys else list(SERIES)
    failed = []
    for k in keys:
        if k not in SERIES:
            print(f"[macro_series] 未知 KEY {k}", file=sys.stderr)
            failed.append(k)
            continue
        try:
            n = update(k, a.backfill)
            print(f"{k}: {n} rows fetched")
        except Exception as e:
            print(f"[macro_series] {k}: {type(e).__name__}: {e}", file=sys.stderr)
            failed.append(k)
    print(f"[macro_series] {len(keys) - len(failed)}/{len(keys)} ok → {MACRO_DAILY_DIR}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
