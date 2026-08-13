#!/usr/bin/env python3
"""premium_proj.py — 正股价位 → 期权权利金投影 (显示层折算,  用户 F1 裁决 b)。

为什么是投影不是卡片存权利金: 结构是正股的属性, 权利金是它的投影 —— theta 会让
静态权利金线自己漂 (今天正股 358.5 对应 $22, 两个月后同一 358.5 只值 $15, 静态线
会在结构没坏时越来越容易触发)。卡片单一真源仍存正股结构位 (zone 维护流程零改动),
显示/告警层每日用当日 mark/IV/DTE 重新折算。

方法 (BS 偏移 优先, delta 线性退路):
    proj = mark + [BS(level) − BS(spot)]        ← 偏移而非比例: BS 定价与真实 mark
                                                  之间的常数偏差 (skew/供需) 被保留
    退路 = mark + delta × (level − spot)          ← 无 IV/DTE 时; gamma 误差随距离放大
    下限 = max(内在价值, 0)

消费者: portfolio_brief 的止损/止盈单行 (期权卡的正股 stop/trim → 保费空间取
max/最近)。不下单不落库 — 纯显示层数学。
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from quant.core.num import coerce_float  # noqa: E402
from quant.core.occ import parse_occ  # noqa: E402
from quant.options.strike_compare import bs_call  # noqa: E402


def project_call_premium(level: float, *, mark: float, spot: float,
                         strike: float, dte_days: Optional[float],
                         iv_pct: Optional[float],
                         delta: Optional[float] = None) -> Optional[dict]:
    """正股到 level 时该 long call 约值多少权利金。None = 两条路都算不了。

    返回 {"premium", "method"}; method ∈ {"bs", "delta"} — 播报必须带方法标注,
    投影是估计不是报价。
    """
    level = coerce_float(level)
    mark = coerce_float(mark)
    spot = coerce_float(spot)
    strike = coerce_float(strike)
    if None in (level, mark, spot, strike) or spot <= 0 or mark < 0:
        return None
    iv = coerce_float(iv_pct)
    t_years = (coerce_float(dte_days) or 0) / 365.0
    intrinsic = max(level - strike, 0.0)
    if iv and iv > 0 and t_years > 0:
        proj = mark + bs_call(level, strike, t_years, iv / 100.0) \
                    - bs_call(spot, strike, t_years, iv / 100.0)
        return {"premium": round(max(proj, intrinsic, 0.0), 2), "method": "bs"}
    d = coerce_float(delta)
    if d is not None:
        proj = mark + d * (level - spot)
        return {"premium": round(max(proj, intrinsic, 0.0), 2), "method": "delta"}
    return None


def dte_of(occ_ticker: str, today: str) -> Optional[int]:
    """OCC ticker → 剩余日历日。None = 不是期权 / 日期解析失败。"""
    parts = parse_occ(occ_ticker)
    if not parts:
        return None
    try:
        return (date.fromisoformat(parts["expiry"]) - date.fromisoformat(str(today)[:10])).days
    except ValueError:
        return None


def binding_stop(*, is_option: bool,
                 card_stop: Optional[float] = None, card_days: int = 1,
                 lock_floor: Optional[float] = None, lock_stage: Optional[str] = None,
                 trail: Optional[float] = None, trail_mode: Optional[str] = None,
                 trail_broken: bool = False,
                 s1_floor: Optional[float] = None,
                 mark: Optional[float] = None, spot: Optional[float] = None,
                 strike: Optional[float] = None, dte_days: Optional[float] = None,
                 iv_pct: Optional[float] = None,
                 delta: Optional[float] = None) -> Optional[dict]:
    """止损显示合同的单一装配点 (用户: 内部算不同基的 max, 只显示一个值)。

    来源词表与 price_reminder 同源: card | lock | trail (+期权原生 s1)。
    正股: card / lock(棘轮地板) / trail(趋势死亡线) 同一价格空间, 直接 max。
    期权: card / trail 是 **underlying 基**→ 先投影进保费空间; lock(保费棘轮) 与
    S1(保费地板) 原生; 再 max。混基直接比 = 量纲错 (期权 mark 比正股价位,
    距离恒 +1500%,  brief 修掉的形状)。

    各基照常运转 (F2「只是不显示」) —— 折进 max 的是**显示**, 不是执行。
    返回 {"value","source","note"}; note 带口径/投影方法 (投影是估计不是报价)。
    """
    bases: list = []
    if not is_option:
        if card_stop is not None:
            span = f"收盘×{card_days}日" if card_days > 1 else "收盘"
            bases.append(("card", card_stop, span))
        if lock_floor is not None:
            bases.append(("lock", lock_floor, f"棘轮[{lock_stage or '?'}]"))
        if trail is not None:
            note = (trail_mode or "trail") + (" BROKEN" if trail_broken else "")
            bases.append(("trail", trail, note))
    else:
        if card_stop is not None:
            pj = project_call_premium(card_stop, mark=mark, spot=spot, strike=strike,
                                      dte_days=dte_days, iv_pct=iv_pct, delta=delta)
            if pj:
                span = f"×{card_days}日" if card_days > 1 else ""
                bases.append(("card", pj["premium"],
                              f"正股 {card_stop:g}{span} 投影({pj['method']})"))
        if trail is not None:
            pj = project_call_premium(trail, mark=mark, spot=spot, strike=strike,
                                      dte_days=dte_days, iv_pct=iv_pct, delta=delta)
            if pj:
                bases.append(("trail", pj["premium"],
                              f"正股 {trail:g} 投影({pj['method']})"
                              + (" BROKEN" if trail_broken else "")))
        if lock_floor is not None:
            bases.append(("lock", lock_floor, f"保费棘轮[{lock_stage or '?'}]"))
        if s1_floor is not None:
            bases.append(("s1", s1_floor, "保费地板 成本×0.75"))
    if not bases:
        return None
    source, value, note = max(bases, key=lambda b: b[1])
    return {"value": round(value, 2 if is_option else 4), "source": source, "note": note}
