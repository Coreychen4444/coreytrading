"""episodes.py — 「什么算一次结构破位」的单一真源。

## 为什么它在 core/ 而不是留在 research/

`gate_backtest.episodes_of` 是本仓**四份回测的共同 episode 构造**
(gate_backtest / gate_backtest2 / ladder_entry / relative_volume)，
也就是说：`ladder_entry` 采纳 `fastlane_k = 0.5` 时，那个「①修复」采纳域的
**字面含义就是「这个函数造出来的那种 episode」**。

 `gate_path_taxonomy` 预注册要给 `_fastlane` 划适用域，判别式的第一道
就是「这次触发前面有没有破位」。E 决策点锁定的原话:

> **`gate_backtest.episodes_of` 原式，且提到 `core/` 让回测与 live `import` 同一个函数**。
> 理由：①的采纳依据 (`ladder_entry`) 的样本**就是**由它构造的 —— 用别的实现去界定
> "采纳域"，等于用一把没量过它的尺去画它的边界。

实测过的教训 (预注册 §3c): 同一批 live 卡、同一套锁定值，`broke` 换五种"合理实现"
得到 **0 / 1 / 2 张**域外卡，且落进的类别都不同。所以它不能是"各写各的"。

⚠️ **名字逐字保留 `episodes_of`**—— 四份设计稿、`RULINGS_LEDGER`、多处注释都按这个
名字引用它。改名会让 `grep episodes_of` 从"找得到唯一实现"退化成"找得到一堆别名"。

## 语义

破位 = **收 60 日最低收盘 且 close < MA60**。两条腿缺一不可:
  · 60 日最低收盘 —— 创新低，不是随便一根阴线
  · close < MA60  —— 排除上升趋势里的浅回调 (那一桶的前向收益实测最好，
                     见 structure_downgrade_backtest；把它算成破位是砍掉最好的一桶)

`COOLDOWN` 120 交易日 = 同票两个 episode 的最小间隔，避免重叠计数。

⚠️ **这不是卡片语义的破位。**系统里有两套「破位」定义，live 上不一致:
  · 卡片相对: `regime_transition.detect_transition` —— `close < 卡片 stop_level` 连续 N 日
  · 结构绝对: 本函数 —— 收 60 日最低收盘 且 close < MA60
实测分歧 (): MRVL/MU 的卡片名叫 `B_repair_probe_*` (转移机判过 demote)，
但按本函数在 120 日窗内 **0 天**深度破位 —— 它们的跌幅没有创 60 日最低收盘。
引用「破位」两个字之前先问是哪一套。

## gate 的结构身份 (`gate_path_taxonomy` 裁决之后)

四分类判别式 `gate_path()` 也住在这里 —— 它的第一道就是 `episodes_of`，
两个消费者必须共用一份:
  · `quant/research/gate_path_taxonomy_backtest`（裁决它的那份回测）
  · `quant/report/gate_ladder_report`（给 gate 行取名）

⚠️ **四分类是描述性标签, 不携带授权含义。**裁决 = **四类全部不采纳**
(②③④ 上 `_fastlane` 维持 ×2)。看到 "箱体顶" 不表示系统对它做了任何授权判断,
只表示"这条线相对破位史是什么身份"。
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
    `ma60` 与 `closes` 等长、不足期为 None (= `core.technical.sma(closes, 60)`
    或 `gate_backtest.sma_series(closes, 60)`，两者逐点等价，由 tests 钉住)。

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

    ⚠️ 两个条件缺一不可: 落在窗内 **且**那天的收盘在 gate 之下。
    §3d 的实证: MRVL/MU 的卡片名叫 `B_repair_probe_*`(转移机判过 demote), 但按本判据
    窗内 0 天深度破位 —— 它们的跌幅没有创 60 日最低收盘。**卡片名不是判据。**
    """
    lo = bisect.bisect_left(eps_idx, at - window)
    hi = bisect.bisect_left(eps_idx, at)
    win = eps_idx[lo:hi]
    below = [e for e in win if closes[e] < gate]
    return bool(below), len(win), len(below)


def gate_path(broke: bool, trend: Optional[str], gate: float, ath: float,
              delta: float) -> str:
    """gate 的结构身份四分类 (预注册 §3, 判据锁定)。

        ①repair     broke                                  破位后回来的那条线
        ②uptrend    ¬broke ∧ trend=="up"  ∧ gate < ath−δ   上升趋势里的上方供给簇
        ③range_top  ¬broke ∧ trend!="up"  ∧ gate < ath−δ   区间上沿
        ④ath        ¬broke ∧ gate >= ath−δ                 已在历史最高收盘的 δ 邻域

    `broke` 压倒一切; ④ 压倒 trend。**互斥且穷尽。**
    ⚠️ 描述性标签, 不携带授权含义 (四类  全部不采纳)。
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
