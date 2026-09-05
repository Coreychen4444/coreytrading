"""book.py — 组合层风控上下文的**装配层**: NAV / 每票 ATR20·价 / 全书占用。

为什么是一个模块而不是每个 caller 自己接线 (用户 「所有涉及到的地方
进行对齐」):

    冲击预算余额与 gross 余额是**组合层标量**。同一天里
      · entry_guard 的 GO size 上限
      · portfolio_brief 的持仓预算行
      · moomoo 价格提醒 z1 备注上那个金额
    必须是同一个数。谁自己再写一遍 price_of / atr_of / book_usage,
    谁就在造第二份真源 —— `shock_budget_wiring_todo.md` §C 记的正是这条
    ("其他 caller 若要用, 得各自再实现一遍")。

    NAV 也一样:  之前 `_latest_nav()` 在 entry_guard / portfolio_brief /
    price_reminder_sync 里各存一份同样的 6 行, 三处读同一个文件却是三份代码。

口径 (不要在调用处覆盖):
    ATR20 = **算钱**尺 (technical.atr 的分工: ATR20 算钱 / ATR14 读盘)。
    价格 = kline 缓存最后一根**已完成**日线的收盘 —— 与 exit/entry guard 的
    结构判定同源; kline_cache 盘中已丢弃未完成 bar, 所以这里天然是收盘口径。

层次: 只读 (kline 缓存 / nav_history / positions.json), 不写、不联网。
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from .io_json import iter_jsonl, read_json
from .ledger import iter_events
from .num import coerce_float
from .occ import parse_occ
from .paths import KLINE_CACHE_DIR, NAV_HISTORY_PATH, POSITIONS_PATH
from .sizing import book_usage
from .technical import atr as _atr
from .timeutil import et_today

ATR_PERIOD = 20            # 算钱口径; 调用点不要传别的 period 进来
_MIN_BARS = ATR_PERIOD + 5  # Wilder RMA 需要暖机, 太短的序列宁可返回 None

_BARS: Dict[str, List[dict]] = {}


def clear_cache() -> None:
    """进程内 bar 缓存清空 (测试用; CLI 一次性进程不需要)。"""
    _BARS.clear()


def bars(ticker: str, kdir: Optional[Path] = None) -> List[dict]:
    """日线缓存 (已完成 bar)。缺文件 = 空列表, 不抛。"""
    d = kdir or KLINE_CACHE_DIR
    key = f"{d}/{ticker.upper()}"
    if key not in _BARS:
        p = d / f"{ticker.upper()}.jsonl"
        _BARS[key] = list(iter_jsonl(p)) if p.exists() else []
    return _BARS[key]


def price_of(ticker: str, kdir: Optional[Path] = None) -> Optional[float]:
    """最后一根已完成日线的收盘。None = 无缓存。"""
    rows = bars(ticker, kdir)
    return coerce_float(rows[-1].get("close")) if rows else None


def atr20_of(ticker: str, kdir: Optional[Path] = None) -> Optional[float]:
    """ATR20 (算钱尺)。序列不够暖机 → None, **不返回一个凑合的数**。"""
    rows = bars(ticker, kdir)
    if len(rows) < _MIN_BARS:
        return None
    series = _atr(rows, ATR_PERIOD)
    return series[-1] if series else None


def latest_nav() -> Optional[float]:
    """nav_history 最后一行的 total_assets = 预算分母的单一读法。"""
    last = None
    if NAV_HISTORY_PATH.exists():
        for r in iter_jsonl(NAV_HISTORY_PATH):
            last = r
    return coerce_float((last or {}).get("total_assets"))


def latest_account() -> Dict:
    """nav_history 最后一行整行 (total_assets / cash / market_val / power / date)。"""
    last: Dict = {}
    if NAV_HISTORY_PATH.exists():
        for r in iter_jsonl(NAV_HISTORY_PATH):
            last = r
    return last or {}


def power_room(today: Optional[str] = None, events_dir=None, *, account=None, events=None) -> Dict:
    """**真实购买力余额** —— 冲击预算/gross 之外的第⑥条臂。

    「零杠杆现金户里 110% NAV 是够不着的线, 理论上真 binding 的是现金。」实测
    : power $17,780 vs gross 臂 $19,503 —— ④比现实松 $1,723, 是当下
    唯一"真 binding 却缺席"的约束。

    room = power 快照 − **当日已成交 BUY 金额**。快照来自 nav_snapshot (eod_only,
    postclose 刷当日) —— 盘中买完它不会动, 所以要自己减掉当天的成交, 否则同一笔
    钱可以被授权两次 (A3)。

    ⚠️ `cash` 字段**不可用**:  读出 0.0, 历史上出现过 −1,223 / −252,
    而 total 21,038 / market_val 3,776 —— 三个数对不上, 那不是现金余额。真实现金
    ≈ total − market_val, 与 power 差 3%。
    ⚠️ power 是**券商口径购买力**, 不是现金: 账户一旦带杠杆它会大于现金, 这条臂
    就随之变宽。`leveraged` = power > 1.2 ×(total − market_val) 时置位, 调用方
    必须播报 —— 静默变宽正是本仓反复出的那种失效 (A4)。
    """
    # Explicit frozen inputs let research callers reuse this exact calculator
    # without reading mutable defaults between portfolio capture steps.
    acct = account if account is not None else latest_account()
    power = coerce_float(acct.get("power"))
    total, mv = coerce_float(acct.get("total_assets")), coerce_float(acct.get("market_val"))
    implied_cash = (total - mv) if (total is not None and mv is not None) else None
    out: Dict = {"power": power, "as_of": str(acct.get("date") or "")[:10],
                 "implied_cash": implied_cash, "spent_today": 0.0, "room": None,
                 "leveraged": bool(power and implied_cash and power > 1.2 * implied_cash)}
    if power is None:
        return out
    day = str(today or et_today())[:10]
    spent = 0.0
    for e in (events if events is not None else iter_events(events_dir)):
        if str(e.get("timestamp") or "")[:10] != day:
            continue
        if e.get("event_type") != "fill" or str(e.get("side") or "").upper() != "BUY":
            continue
        mult = (parse_occ(str(e.get("ticker") or "")) or {}).get("multiplier") or 1
        spent += (coerce_float(e.get("qty")) or 0.0) * (coerce_float(e.get("price")) or 0.0) * mult
    out["spent_today"] = round(spent, 2)
    out["room"] = max(power - spent, 0.0)
    return out


def exposure_of(ticker: str, positions_doc: Optional[dict] = None) -> float:
    """单票**当前敞口**= 正股市值 + 该标的所有期权的保费市值 (与 gross 同口径,
    用户  期权按 premium 记 gross 的裁决)。期权归到 underlying ——
    "单票总暴露"按 position-tiers 含期权。

    用途 = 分类 cap 的**累计**扣减 (B7 / 用户  A1): 40% 是这个 ticker
    总共能有多少, 不是每轮都重新给一份。结构豁免层 (base) 不计, 与 load_usage
    的「预算只数受管书」同口径。
    """
    from .holdings import is_managed
    doc = positions_doc if positions_doc is not None else (read_json(POSITIONS_PATH) or {})
    key = ticker.upper()
    total = 0.0
    for tk, body in ((doc or {}).get("tickers") or {}).items():
        if not isinstance(body, dict) or not is_managed(body):
            continue
        occ = parse_occ(tk)
        und = (occ or {}).get("underlying") or tk
        if str(und).upper() != key:
            continue
        br = body.get("broker") or {}
        qty = coerce_float(br.get("qty")) or 0.0
        px = coerce_float(br.get("current_price"))
        if not qty or not px:
            continue
        total += abs(qty) * px * ((occ or {}).get("multiplier") or 1)
    return total


def load_usage(positions_doc: Optional[dict] = None, nav: Optional[float] = None,
               kdir: Optional[Path] = None) -> Dict:
    """全书已用的冲击预算与 gross。**所有 caller 的唯一入口。**

    期权 delta 走 `broker.delta` (stock_fetch 落库的 OpenD 快照真 greeks) ——
    不再各自 BS 反解: 反解要 IV、要 T、要合约参数, 每个 caller 实现一遍就是
    每个 caller 一个答案。落不到 delta 时 `book_usage` 按 1.0 保守计并报出来。
    """
    doc = positions_doc if positions_doc is not None else (read_json(POSITIONS_PATH) or {})
    return book_usage(doc, nav if nav is not None else latest_nav(),
                      price_of=lambda t: price_of(t, kdir),
                      atr_of=lambda t: atr20_of(t, kdir))
