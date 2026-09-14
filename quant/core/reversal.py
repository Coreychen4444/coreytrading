"""reversal.py — 抄底入场类 (`reversal_probe`) 的机械判别式。

设计: 不等 gate, 在标的跌到足够便宜、空头力量衰竭时尝试进场, 严格设置止损。
第一版信号 = 5 日内 20 日新低 + 一根阳线吃掉前 5 日最高, 只开 R1 (已退役)。

现行信号 (试行):
    抄底 = 止跌底部确认后做反弹或反转行情;
    止跌不用道氏结构, 只用最基本的价格行为判断空头力量衰竭 (次日没有继续新低或收出长下影线),
    止损放在下影线低点; 抄底的前提是该标的为空仓。
→ 信号 = **core.setups.bottom_setup 的 ✓止跌** (次日未新低 或 止跌日长下影), 止损 = 止跌低 (下影线低点),
  域 = R0 (距 60 日高 ≥10%, 不排 R2 —— 第一版「只开 R1」是旧信号的裁决, 新信号定价 R2 更好),
  工具 = 正股 + 单腿 long call (IVP 门), 授权住 option_plans.json, 持仓票不走 (空仓前提)。
  定价: `research/bottom_stage_backtest.py` (探索定价, 非预注册; 试行期满按预先锁定判据复核)。
  第一版信号 (旧判别式) 的数字冻结在私有研究记录, 本文件不再实现它。

类名 `reversal_probe` 与接线 (option_plans / Today / oz1 / auth 章 / exit_guard 分流) 沿用, 语义 = 抄底。

判读层 ≠ 授权层的边界照旧: 本模块只产**信号 + 止跌低 + 域 + 阶梯**; cap / 张数 / 股数归
sizing / option_plan / entry_guard。纯函数, 不读文件, 不联网。
"""
from __future__ import annotations

from typing import List, Optional

from .num import coerce_float

ENTRY_CLASS = "reversal_probe"
ENTRY_CLASS_CN = "抄底"

# 判别式参数 (与 core.setups 共用窗口词汇; 改任一项 = 换了判别式, 必须重跑
# research/bottom_stage_backtest 并记录结果)。
PARAMS = {
    "newlow_lookback": 20,    # 止跌低 = 近 20 日最低 low (setups.LOW_LOOKBACK)
    "high_lookback": 60,      # 回撤参照 = 60 日高 (setups.HIGH_LOOKBACK)
    "drawdown_min": 0.10,     # 域下沿: 止跌低距 60 日高 ≥10% 才是「跌到足够便宜」
    "drawdown_max": None,     # 域上沿: None = 不封顶 (R0; 第一版 0.20 只开 R1 已退役)
    "valid_days": 3,          # 信号有效期 (交易日): 「不能等待过多」的操作化
}
LADDER_FRACS = (2.0 / 3.0, 1.0 / 3.0)   # 二/三档 = 止跌低 + 首档距的 2/3 / 1/3 (全在止跌低之上)


def _f(row: dict, key: str) -> float:
    return float(row.get(key) or 0.0)


def signal_at(rows: List[dict], i: int, atr20: Optional[float] = None,
              p: dict = PARAMS) -> Optional[dict]:
    """rows[i] 是否为 ✓止跌 的**形成日** (setups._frame 在 i 处 stage==formed 且信号年龄 0)。
    → dict | None。`in_domain` = 止跌低距 60 日高 ≥ drawdown_min (且 < drawdown_max 若设)。"""
    from . import setups  # noqa: PLC0415  (setups 在模块级 import 本模块的 PARAMS, 这里延迟)
    if i < 0 or i >= len(rows):
        return None
    fr = setups._frame(rows[:i + 1])
    if fr.get("stage") != "formed" or fr.get("bars_since_signal") != 0:
        return None
    dd_low = float(fr["dd_at_low"])
    dmax = p.get("drawdown_max")
    in_domain = (-dd_low >= float(p["drawdown_min"])) and (dmax is None or -dd_low < float(dmax))
    a = coerce_float(atr20)
    c, sup = float(fr["close"]), float(fr["low"])
    return {
        "index": i,
        "date": str(fr["signal_date"]), "close": c,
        "signal": fr["signal"], "low_date": fr["low_date"],
        "support": sup, "lower_high": fr.get("lh"),
        "high_60d": float(fr["high_60d"]), "drawdown": dd_low, "drawdown_now": float(fr["dd_now"]),
        "domain": ("R0" if in_domain else "shallow"), "in_domain": in_domain,
        "hammer": bool(fr.get("hammer")), "shadow": fr.get("shadow"),
        "above_low_bar_high": bool(fr.get("above_low_bar_high")),
        "low_bar_vol_ratio": fr.get("low_bar_vol_ratio"),
        "atr20": a,
        "stop_dist_n": (round((c - sup) / a, 2) if a and a > 0 else None),
    }


def detect(rows: List[dict], atr20: Optional[float] = None,
           valid_days: Optional[int] = None, p: dict = PARAMS) -> Optional[dict]:
    """最近 valid_days 根里最新的一个 ✓止跌 形成日 (含 age = 距今根数); 信号之后任一收盘 < 止跌低
    = 信号已作废 (反弹结束) → None。"""
    n = len(rows)
    vd = int(valid_days if valid_days is not None else p["valid_days"])
    for k in range(0, vd):
        i = n - 1 - k
        if i < 0:
            break
        sig = signal_at(rows, i, atr20, p)
        if not sig:
            continue
        if any(_f(x, "close") < sig["support"] for x in rows[i + 1:]):
            return None                                 # 止跌低已收破: 反弹结束, 不复活
        sig["age"] = k
        return sig
    return None


def ladder(sig: dict, last_close: Optional[float] = None) -> List[float]:
    """本类的限价阶梯 (降序): 首档 = min(信号收盘, 最新收盘) (买参与权, 「不能等待过多」; 挂在市价
    之上的限价 = 即时成交不是回踩单), 二/三档 = 止跌低 + 首档距的 2/3 / 1/3。全部在止跌低之上;
    股数 / 张数由 sizing.share_ladder 按 50/30/20 切。"""
    c, sp = float(sig["close"]), float(sig["support"])
    lc = coerce_float(last_close)
    top = min(c, lc) if lc else c
    if top <= sp:
        return []                                   # 收盘已在止跌低下 = 信号作废, 无阶梯可言
    rungs = [top] + [sp + f * (top - sp) for f in LADDER_FRACS]
    out: List[float] = []
    for x in rungs:
        x = round(x, 2)
        if x > sp and (not out or x < out[-1]):
            out.append(x)
    return out
