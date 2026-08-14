"""Single executable source for structural-breakdown episodes and gate labels.

A breakdown requires both a new rolling closing low and a close below the shared
moving average. ``COOLDOWN`` prevents overlapping observations. This structural
definition is intentionally distinct from a card-relative stop transition.

``gate_path`` produces descriptive labels only; it grants no authorization.
Private validation must import these functions instead of copying their logic.
"""
from __future__ import annotations

import bisect
from typing import Iterable, List, Optional, Sequence, Tuple

LOOKBACK = 60      # episode 触发: 60 日最低收盘
COOLDOWN = 120     # 同票两个 episode 的最小间隔 (交易日), 避免重叠计数
BROKE_WINDOW = COOLDOWN     # 判"这条 gate 前面有没有破位"的回看窗 (决策点 F 锁定)
DELTA_ATR = 0.5             # ATH 邻域容差 δ = 0.5×ATR20 (决策点 A 锁定)
MIN_BARS = COOLDOWN + LOOKBACK   # 180: 低于它 BROKE_WINDOW 算不满 ⇒ 不给身份


def episodes_of(closes: Sequence[float],
                ma60: Sequence[Optional[float]]) -> List[int]:
    """结构破位: 收 60 日最低收盘 且 close < MA60 (排除浅回调)。

    → 破位日的下标列表 (升序，间隔 ≥ COOLDOWN)。
    `ma60` 与 `closes` 等长、不足期为 None，并由 `core.technical.sma` 生成。

    ⚠️ 无前视: 第 i 位只用 closes[i-LOOKBACK:i+1] 与 ma60[i]。
    """
    out: List[int] = []
    last = -10 **9
    for i in range(LOOKBACK, len(closes)):
        if i - last < COOLDOWN:
            continue
        if closes[i] > min(closes[i - LOOKBACK:i]):
            continue
        if ma60[i] is None or closes[i] >= ma60[i]:
            continue
        out.append(i)
        last = i
    return out


# ── gate 结构身份 ───────────────────────────────────────────────────

def breakdown_in_window(eps_idx: Sequence[int], closes: Sequence[float], at: int,
                        gate: float, window: int = BROKE_WINDOW):
    """`at` 之前 `window` 个交易日内, 有没有破到这条 gate 之下?

    → (broke, 窗内 episode 数, 其中 close < gate 的数)。窗口是**半开**[at−window, at)。

    两个条件缺一不可：episode 落在窗口内，且当日收盘位于 gate 下方。
    卡片名称和叙述字段不是判据。
    """
    lo = bisect.bisect_left(eps_idx, at - window)
    hi = bisect.bisect_left(eps_idx, at)
    win = eps_idx[lo:hi]
    below = [e for e in win if closes[e] < gate]
    return bool(below), len(win), len(below)


def gate_path(broke: bool, trend: Optional[str], gate: float, ath: float,
              delta: float) -> str:
    """返回互斥且穷尽的 gate 结构身份四分类。

        ①repair     broke                                  破位后回来的那条线
        ②uptrend    ¬broke ∧ trend=="up"  ∧ gate < ath−δ   上升趋势里的上方供给簇
        ③range_top  ¬broke ∧ trend!="up"  ∧ gate < ath−δ   区间上沿
        ④ath        ¬broke ∧ gate >= ath−δ                 已在历史最高收盘的 δ 邻域

    `broke` 压倒一切; ④ 压倒 trend。**互斥且穷尽。**
    描述性标签不携带授权含义。
    """
    if broke:
        return "repair"
    if gate >= ath - delta:
        return "ath"
    return "uptrend" if trend == "up" else "range_top"


def merge_bars_by_date(*sources: Iterable[dict]) -> Tuple[List[dict], Optional[str]]:
    """按 `time` 日期去重合并多段日线 → (升序 bars, 告警 or None)。**后来者覆盖先来者。**

    为什么在这里: 本模块的两个判别式都要**足够深**的序列 —— `gate_path` 的 `ath`
    是"样本内历史最高收盘", `breakdown_in_window` 要 180 根才算得满 —— 而 live 的
    常规 kline 缓存只有 ~1 年 (221–291 根)。深度缓存补历史、常规缓存补最近,
    **拼接是用它们的前提, 不是一个独立能力**。

    🚨 **必须去重**:  直接拼接过一次, 重叠段的重复 bar 污染了 MA60,
    分类当场算错。调用顺序 = 由旧到新 (深度在前, 常规在后)。

    告警只报一种真问题: **两段之间有洞**(深度尾 < 常规头, 中间缺一大段)。
    此时序列不连续, MA60/episode 全部失真 —— 调用方应当降级而不是照用。
    """
    spans, merged = [], {}
    for src in sources:
        rows = [r for r in src if r.get("time")]
        if not rows:
            continue
        ds = sorted(str(r["time"])[:10] for r in rows)
        spans.append((ds[0], ds[-1]))
        for r in rows:
            merged[str(r["time"])[:10]] = r
    bars = [merged[d] for d in sorted(merged)]
    note = None
    for (a_lo, a_hi), (b_lo, b_hi) in zip(spans, spans[1:]):
        if b_lo > a_hi:                       # 前一段结束 < 后一段开始 = 中间有洞
            note = f"序列断层 {a_hi} → {b_lo}"
    return bars, note
