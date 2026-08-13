#!/usr/bin/env python3
"""Project-level market data module.

Wraps Moomoo OpenD SDK (via common.py) into importable functions that return
structured data. Designed for project scripts (stock_fetch, portfolio_brief,
technical analysis) — not for CLI use.

All functions return dicts/lists (empty on failure) and never write to stdout;
unexpected errors are logged to stderr. Caveat: OpenD connection failures
exit the process via common.py `_check_opend_alive` (sys.exit 1) — callers
that need to survive OpenD-down must check connectivity first.

Usage:
    from quant.data.market_data import get_kline, get_quote, get_snapshot, get_rt_data
    klines = get_kline("NVDA", ktype="1d", num=60)
    quotes = get_quote(["NVDA", "AAPL"])
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 直接 `python3 quant/<层>/<模块>.py` 时让 `quant.*` 可 import

from quant.core.paths import add_openapi_path  # noqa: E402

from quant.core import env  # noqa: F401  (.env → os.environ)

import os
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

# Import common.py from openapi skills
add_openapi_path()  # openapi skill scripts (`common`) 上 sys.path
from common import (
    create_quote_context, safe_close, safe_float, safe_int, safe_get,
    RET_OK, KLType, SubType, AuType,
)


# ── Constants ──────────────────────────────────────────────────

KTYPE_MAP = {
    "1m": KLType.K_1M,
    "3m": KLType.K_3M,
    "5m": KLType.K_5M,
    "15m": KLType.K_15M,
    "30m": KLType.K_30M,
    "60m": KLType.K_60M,
    "1d": KLType.K_DAY,
    "1w": KLType.K_WEEK,
    "1M": KLType.K_MON,
}

REHAB_MAP = {
    "none": AuType.NONE,
    "forward": AuType.QFQ,
    "backward": AuType.HFQ,
}

KTYPE_TO_SUBTYPE = {
    KLType.K_1M: SubType.K_1M,
    KLType.K_3M: SubType.K_3M,
    KLType.K_5M: SubType.K_5M,
    KLType.K_15M: SubType.K_15M,
    KLType.K_30M: SubType.K_30M,
    KLType.K_60M: SubType.K_60M,
    KLType.K_DAY: SubType.K_DAY,
    KLType.K_WEEK: SubType.K_WEEK,
    KLType.K_MON: SubType.K_MON,
}


# ── Helpers ────────────────────────────────────────────────────

def _norm_code(ticker: str) -> str:
    """Normalize ticker to Moomoo code format. 'NVDA' → 'US.NVDA'."""
    ticker = ticker.strip().upper()
    if '.' in ticker:
        return ticker
    return f'US.{ticker}'


def _strip_prefix(code: str) -> str:
    """'US.NVDA' → 'NVDA'."""
    return code.split('.', 1)[1] if '.' in code else code


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── K-Line ─────────────────────────────────────────────────────

def get_kline(
    ticker: str,
    ktype: str = "1d",
    num: int = 60,
    start: Optional[str] = None,
    end: Optional[str] = None,
    rehab: str = "forward",
    max_page: Optional[int] = None,
    extended_time: bool = False,
) -> List[dict]:
    """Fetch K-line data for a ticker.

    Args:
        ticker: Stock ticker, e.g. 'NVDA' or 'US.NVDA'
        ktype: K-line type: 1m/5m/15m/30m/60m/1d/1w/1M
        num: Number of bars (realtime) or page size (history)
        start: History start date 'YYYY-MM-DD' (triggers history mode)
        end: History end date 'YYYY-MM-DD'
        rehab: Adjustment: none/forward/backward
        max_page: Max pages for history mode
        extended_time: 含盘前盘后 (仅 history 模式 + 美股分钟线有意义)。
            ⚠️ **算 volume profile 时必须开**: 实测 RTH-only 的 1m 加总只有日线
            成交额的 89.5%, 带盘前盘后是 97.8% —— 缺的 10.5% 正是 gap 与财报反应
            发生的地方 (调研, research/volume_profile_accuracy.md §6)。
            默认 False 保持既有 caller 行为不变。

    Returns:
        List of dicts with keys: time, open, high, low, close, volume, turnover
        Empty list on failure.
    """
    code = _norm_code(ticker)
    kl_type = KTYPE_MAP.get(ktype, KLType.K_DAY)
    au_type = REHAB_MAP.get(rehab, AuType.QFQ)
    ctx = None

    try:
        ctx = create_quote_context()

        if start or end:
            # History mode
            import pandas as pd
            page_size = min(num, 1000)
            ret, data, page_req_key = ctx.request_history_kline(
                code, start=start, end=end,
                ktype=kl_type, autype=au_type, max_count=page_size,
                extended_time=extended_time,
)
            if ret != RET_OK or data is None:
                return []
            all_data = data
            page_count = 1
            while page_req_key is not None:
                if max_page and page_count >= max_page:
                    break
                ret, data, page_req_key = ctx.request_history_kline(
                    code, start=start, end=end,
                    ktype=kl_type, autype=au_type,
                    max_count=page_size, page_req_key=page_req_key,
                    extended_time=extended_time,   # 分页也必须带, 否则后续页悄悄换口径
)
                if ret != RET_OK or data is None:
                    break
                if hasattr(data, 'empty') and not data.empty:
                    all_data = pd.concat([all_data, data], ignore_index=True)
                page_count += 1
            data = all_data
        else:
            # Realtime mode
            sub_type = KTYPE_TO_SUBTYPE.get(kl_type, SubType.K_DAY)
            ctx.subscribe([code], [sub_type])
            ret, data = ctx.get_cur_kline(code, num, kl_type, au_type)
            if ret != RET_OK or data is None:
                return []

        if hasattr(data, 'empty') and data.empty:
            return []

        records = []
        for i in range(len(data)):
            row = data.iloc[i] if hasattr(data, 'iloc') else data[i]
            records.append({
                "time": safe_get(row, "time_key", default=""),
                "open": safe_float(safe_get(row, "open", default=0)),
                "high": safe_float(safe_get(row, "high", default=0)),
                "low": safe_float(safe_get(row, "low", default=0)),
                "close": safe_float(safe_get(row, "close", default=0)),
                "volume": safe_int(safe_get(row, "volume", default=0)),
                "turnover": safe_float(safe_get(row, "turnover", default=0)),
            })
        return records

    except Exception as e:
        print(f"[market_data] swallowed: {type(e).__name__}: {e}", file=sys.stderr)
        return []
    finally:
        safe_close(ctx)


# ── Quote ──────────────────────────────────────────────────────

def get_quote(tickers: List[str]) -> Dict[str, dict]:
    """Fetch real-time quotes for multiple tickers.

    Includes extended-hours prices (pre_price, after_price, overnight_price).

    Returns:
        Dict of ticker → {last_price, open, high, low, prev_close, volume,
                          change, change_pct, pre_price, after_price, quote_time}
    """
    codes = [_norm_code(t) for t in tickers]
    code_to_ticker = {_norm_code(t): t.strip().upper() for t in tickers}
    ctx = None
    results: Dict[str, dict] = {}

    try:
        ctx = create_quote_context()
        try:
            ctx.subscribe(codes, [SubType.QUOTE])
        except Exception:
            pass

        ret, data = ctx.get_stock_quote(codes)
        if ret != RET_OK or data is None or (hasattr(data, 'empty') and data.empty):
            return results

        for i in range(len(data)):
            row = data.iloc[i] if hasattr(data, 'iloc') else data[i]
            code = str(safe_get(row, 'code', default='')).strip().upper()
            ticker = code_to_ticker.get(code, _strip_prefix(code))
            last = safe_float(safe_get(row, 'last_price'), default=None)
            prev = safe_float(safe_get(row, 'prev_close_price'), default=None)
            pre = safe_float(safe_get(row, 'pre_price'), default=None)
            after = safe_float(safe_get(row, 'after_price'), default=None)
            overnight = safe_float(safe_get(row, 'overnight_price'), default=None)
            # Best available price: overnight > after-hours > last
            price = overnight or after or last
            chg = (price - prev) if price and prev else None
            pct = (chg / prev * 100) if chg is not None and prev else None

            results[ticker] = {
                "quote_time": _now_iso(),
                "last_price": last,
                "pre_price": pre,
                "after_price": after,
                "overnight_price": overnight,
                "best_price": price,
                "prev_close": prev,
                "open": safe_float(safe_get(row, 'open_price'), default=None),
                "high": safe_float(safe_get(row, 'high_price'), default=None),
                "low": safe_float(safe_get(row, 'low_price'), default=None),
                "volume": safe_int(safe_get(row, 'volume'), default=None),
                "turnover": safe_float(safe_get(row, 'turnover'), default=None),
                "change": chg,
                "change_pct": pct,
                "amplitude": safe_float(safe_get(row, 'amplitude'), default=None),
                "turnover_rate": safe_float(safe_get(row, 'turnover_rate'), default=None),
            }
        return results

    except Exception as e:
        print(f"[market_data] swallowed: {type(e).__name__}: {e}", file=sys.stderr)
        return results
    finally:
        safe_close(ctx)


# ── Snapshot ───────────────────────────────────────────────────

def get_snapshot(tickers: List[str]) -> Dict[str, dict]:
    """Fetch market snapshots (no subscription needed).

    Returns:
        Dict of ticker → {last_price, open, high, low, prev_close, volume,
                          turnover, bid, ask, pe_ratio, ...}
    """
    codes = [_norm_code(t) for t in tickers]
    code_to_ticker = {_norm_code(t): t.strip().upper() for t in tickers}
    ctx = None
    results: Dict[str, dict] = {}

    try:
        ctx = create_quote_context()
        ret, data = ctx.get_market_snapshot(codes)
        if ret != RET_OK or data is None or (hasattr(data, 'empty') and data.empty):
            return results

        for i in range(len(data)):
            row = data.iloc[i] if hasattr(data, 'iloc') else data[i]
            code = str(safe_get(row, 'code', default='')).strip().upper()
            ticker = code_to_ticker.get(code, _strip_prefix(code))
            last = safe_float(safe_get(row, 'last_price'), default=None)
            prev = safe_float(safe_get(row, 'prev_close_price'), default=None)
            chg = (last - prev) if last and prev else None
            pct = (chg / prev * 100) if chg is not None and prev else None

            results[ticker] = {
                "update_time": safe_get(row, 'update_time', default=''),
                "last_price": last,
                "prev_close": prev,
                "open": safe_float(safe_get(row, 'open_price'), default=None),
                "high": safe_float(safe_get(row, 'high_price'), default=None),
                "low": safe_float(safe_get(row, 'low_price'), default=None),
                "volume": safe_int(safe_get(row, 'volume'), default=None),
                "turnover": safe_float(safe_get(row, 'turnover'), default=None),
                "bid": safe_float(safe_get(row, 'bid_price'), default=None),
                "ask": safe_float(safe_get(row, 'ask_price'), default=None),
                "change": chg,
                "change_pct": pct,
                "pe_ratio": safe_float(safe_get(row, 'pe_ratio'), default=None),
                "pe_ttm": safe_float(safe_get(row, 'pe_ttm_ratio'), default=None),
                "pb_ratio": safe_float(safe_get(row, 'pb_ratio'), default=None),
                "total_market_val": safe_float(safe_get(row, 'total_market_val'), default=None),
                "circular_market_val": safe_float(safe_get(row, 'circular_market_val'), default=None),
                "turnover_rate": safe_float(safe_get(row, 'turnover_rate'), default=None),
                "amplitude": safe_float(safe_get(row, 'amplitude'), default=None),
            }
            # 期权 greeks (): 冲击预算的 gross 口径按 **delta 名义**算,
            # 用合约市值会把 GOOGL LEAPS 的 71% NAV 记成 10.5%。此前 caller 各自
            # 用 BS 反解 IV 再求 delta —— 券商快照直接给真 greeks, 反解是多余的
            # 第二份真源。option_valid=False 的正股行不会带这些 key。
            # 真值判断不能写 `is True` —— pandas 给的是 numpy.bool_, 与 Python
            # 的 True 不是同一个对象, 整段会被静默跳过 (首版实测)。
            if bool(safe_get(row, 'option_valid', default=False)):
                for src, dst in (('option_delta', 'delta'),
                                 ('option_gamma', 'gamma'),
                                 ('option_theta', 'theta'),
                                 ('option_vega', 'vega'),
                                 ('option_implied_volatility', 'iv_pct')):
                    v = safe_float(safe_get(row, src), default=None)
                    if v is not None and v == v:       # v != v → NaN ('N/A' 那一列)
                        results[ticker][dst] = v
        return results

    except Exception as e:
        print(f"[market_data] swallowed: {type(e).__name__}: {e}", file=sys.stderr)
        return results
    finally:
        safe_close(ctx)


# ── Market State ───────────────────────────────────────────────

def get_market_state(tickers: List[str]) -> Dict[str, str]:
    """Fetch the current trading session state per ticker.

    Returns:
        Dict of ticker → MarketState name (e.g. 'MORNING', 'AFTERNOON',
        'PRE_MARKET_BEGIN', 'AFTER_HOURS_END', 'CLOSED', 'OVERNIGHT').
        Missing/failed tickers are simply absent from the dict.

    Used to decide whether a live session is active: when it is NOT, the
    real-time subscription quote's extended-hours fields go stale and the
    official snapshot close should be preferred instead.
    """
    codes = [_norm_code(t) for t in tickers]
    code_to_ticker = {_norm_code(t): t.strip().upper() for t in tickers}
    ctx = None
    results: Dict[str, str] = {}

    try:
        ctx = create_quote_context()
        ret, data = ctx.get_market_state(codes)
        if ret != RET_OK or data is None or (hasattr(data, 'empty') and data.empty):
            return results

        for i in range(len(data)):
            row = data.iloc[i] if hasattr(data, 'iloc') else data[i]
            code = str(safe_get(row, 'code', default='')).strip().upper()
            ticker = code_to_ticker.get(code, _strip_prefix(code))
            state = safe_get(row, 'market_state', default='')
            results[ticker] = state.name if hasattr(state, 'name') else str(state)
        return results

    except Exception as e:
        print(f"[market_data] swallowed: {type(e).__name__}: {e}", file=sys.stderr)
        return results
    finally:
        safe_close(ctx)


# ── RT Data (intraday time-sharing) ───────────────────────────

def get_rt_data(ticker: str) -> List[dict]:
    """Fetch intraday time-sharing data for a ticker.

    Returns:
        List of dicts with keys: time, price, avg_price, volume, turnover
    """
    code = _norm_code(ticker)
    ctx = None

    try:
        ctx = create_quote_context()
        ret, msg = ctx.subscribe([code], [SubType.RT_DATA])
        if ret != RET_OK:
            return []

        ret, data = ctx.get_rt_data(code)
        if ret != RET_OK or data is None or (hasattr(data, 'empty') and data.empty):
            return []

        records = []
        for i in range(len(data)):
            row = data.iloc[i] if hasattr(data, 'iloc') else data[i]
            records.append({
                "time": safe_get(row, "time", default=""),
                "price": safe_float(safe_get(row, "cur_price", default=0)),
                "avg_price": safe_float(safe_get(row, "avg_price", default=0)),
                "volume": safe_int(safe_get(row, "volume", default=0)),
                "turnover": safe_float(safe_get(row, "turnover", default=0)),
            })
        return records

    except Exception as e:
        print(f"[market_data] swallowed: {type(e).__name__}: {e}", file=sys.stderr)
        return []
    finally:
        safe_close(ctx)


# ── Batch K-Line (multiple tickers) ──────────────────────────

def get_kline_batch(
    tickers: List[str],
    ktype: str = "1d",
    num: int = 60,
    start: Optional[str] = None,
    end: Optional[str] = None,
    rehab: str = "forward",
    delay: float = 0.3,
) -> Dict[str, List[dict]]:
    """Fetch K-line data for multiple tickers with rate limiting.

    Returns:
        Dict of ticker → list of kline records
    """
    results: Dict[str, List[dict]] = {}
    for i, ticker in enumerate(tickers):
        results[ticker.strip().upper()] = get_kline(
            ticker, ktype=ktype, num=num, start=start, end=end, rehab=rehab,
)
        if i < len(tickers) - 1:
            time.sleep(delay)
    return results
