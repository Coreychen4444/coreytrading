#!/usr/bin/env python3
"""Gate 回测第二轮 — 止损建模下的 gate 价值裁决 (正式预注册)。

设计稿全文: quant/research/gate_backtest2_design.md
D1/D2/D3 已由 用户 锁定 (, 运行前)。本 docstring = 预注册摘要,
实现期间只用合成数据验证 (tests/research/test_gate_backtest2.py), 真数据只跑一次。

## 假说
H1(主): 建模止损后, gate 入场通过降低止损触发率/whipsaw 成本改善 expectancy 与左尾。
H2(判别力): 触发率显著<100% 且 not-fired 组的 World-A 反事实收益更差 = gate 有拒绝权。
H0: gate 仍是延迟器 → D1 分支 2 (卸下 size 授权职能)。

## 两个世界 (paired, 同 episodes, 从破位日到同一终点 T+60/T+120 的 policy 总账)
A: 破位日收盘买入。B: gate 确认日收盘买入, i→j 持现金。
**Policy 口径 (intent-to-treat)**: 全部 episode 计入, B 未触发 = 现金 = 0% ——
拒绝权直接进 expectancy; fired-only 条件统计只作诊断 (round-1 是 conditional-only)。

## 止损 (D2 锁定)
- primary: 对称 entry − 2×ATR20@入场, 收盘×1日触发, 次日收盘出场, 滑点 0.2%
  (= exit_rules_backtest L12/L50 官方结构位代理; 确认单日 = live 卡口径 7/25)
- bfloor: B 止损 = gate level (系统语义直译), A 不变 (primary)
- structure: 两世界 = 入场价下方最近的已确认 swing low (wing=2, 无前视), 无则回退 2N
  ⚠️ 设计稿原文 "episode 内最低收盘" 对 World-A 是前视退化 (A 入场日即 60 日最低收盘,
  low 在未来) — 实现改为 swing-low 版本, 运行前记录于此, 非结果驱动的改动
- sens3n: 对称 3×ATR20 (敏感性)
稳健性裁决 (预注册): D1 以 primary 读数为准; bfloor 与 primary 方向矛盾 → 自动落分支 3。

## 重进 (D3 锁定)
- 主: single-shot (止损后现金到终点)
- 次: machine-replay 仅 World B, 止损后等下一次同 gate 确认再进, 最多 3 段 (≤2 次重进),
  仅 primary 止损口径

## Episodes
主池 = round-1 同口径 (import 同一函数): 60 日最低收盘 且 close<MA60, cooldown 120,
window 120, 终点 T 只在 i+T ≤ end 时计。
副池 (浅回调, 只报告不裁决): close<MA20 且 close>MA60 且 MA60 20 日斜率>0, cooldown 40,
window 60, 右截断守卫 end−i≥30, 终点 T+20/T+60, gate = {ma20, swing} × days{1,2} vol off,
single-shot + primary 止损。

## Gate 格 (主 12 格 = kinds{swing, ma60, cluster} × days{1,2} × vol{off,on}; days=3 附表)
- swing/ma60: round-1 同款 (import gate_level, 逐日重算, 无前视)
- cluster(新, 真方法论): level_map 同式 pivot 聚类 — **破位日 i 画一次, 固定**(卡片语义:
  gate 画卡时定)。头顶最近 touches≥2 簇 (H+L pivot, wing=2 确认 ≤i, tol=0.35×ATR14@i)
  的上沿; 无头顶簇 = gate 不存在 = 永不触发 (拒绝权的来源之一)
- vol=on: 触发完成日 20 日量比 ≥1.2 (mirror regime_transition._volume_ok, 完成日判定);
  fire = 首个 j 满足 streak≥days 且 (vol off 或 vol_ok(j))
- 跌回 level 之下 streak 清零 (round-1 同)

## 指标 (每格 paired vs A, 同止损口径)
policy expectancy(mean)/median/p10/p90 | 止损触发率 (policy 口径 + fired@T 条件口径) |
whipsaw 成本 | fire rate (120 日窗口) 与 fire_rate_at_T (入场日 ≤ 终点) |
not-fired@T 反事实 (该终点上 gate 未放行的 episode 里 A 的 policy 收益) |
trap rate | delay | lateness | 半凯利 (fired@T-only, round-1 同式)。
CI (全部按月聚类 block bootstrap, 10,000 draws numpy, 90% CI, 共享月索引矩阵):
① diff_mean ② 止损率差 (stopped_B − stopped_A) ③ notfired-vs-fired 反事实 gap (H2)。
p10-diff CI: 全部 12 主格 × primary × T+60, 1,000 draws 独立种子, 无 lazy 选择自由度;
估计量与点估计同一索引约定 (round-1 同款)。
D1 三分支机械读数 d1_flags 直接落 JSON (exp_win / stop_better / p10_better /
bfloor_contradiction), 裁决不经人手挑格。
Cohort: 1995-04 /  /  /  / 2020-26 点估计 + n。

## 审查修正记录 (对抗性审查 , 全部在真数据运行之前; 前视 lane 零发现)
1. vol 量比分母改为**含完成日**(与 technical.volume_ratio 逐点一致, 原实现偏松)
2. 止损触发在窗口最后一根 bar = 出场悬置 (按终点收盘 mark, 不计滑点/止损率)
3. terminals 为空 (end−i ∈ [50,60)) 的 episode 剔除 — n/Kish/fire_rate 只计承载裁决的样本
4. 条件诊断与 H2 反事实改按 fired@T 分组 (原按 120 日窗口分组, 在 T+60 上错位)
5. 止损率差 CI / notfired gap CI / p10-diff CI 全部预实现 — 防"看完结果再写统计代码"
6. replay 重进计数只数终点前的入场
7. S-structure 止损实现为"入场价下方最近已确认 swing low, 回退 2N" — 设计稿原文
   "episode 内最低收盘"对 World-A 前视退化 (入场日即 60 日最低), 该修正为非结果驱动;
   cluster gate 固定于破位日 (卡片语义)。两处 pin 均在运行前书面记录, 用户 保留否决权
   (否决 = 按其版本重跑该变体/该 kind, 主格 primary 2N 不受影响)
8. 设计稿"月×行业细口径附注"运行前撤销 (未实现, 不作任何行业级 claim);
   主口径按月聚簇不受影响

## 数据
market/klines_deep/ 165 票 (56 OpenD 2006+ / 109 Yahoo 1995+, _sources.json 审计),
Kish 有效独立事件 ≈197。Survivorship 偏向低估 gate 价值 (方向已声明)。

## Stage 0
先原样跑 round-1 (gate_backtest.run, 无止损, conditional 口径) 于扩容池 —
分离"数据变了"与"规则变了"。

Usage: .venv/moomoo/bin/python3 quant/research/gate_backtest2.py [--json] [--report PATH]
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 直接 `python3 quant/<层>/<模块>.py` 时让 `quant.*` 可 import

import json
import statistics as st
from collections import Counter, defaultdict

from quant.core.paths import KLINE_DEEP_DIR as KDIR  # noqa: E402
from quant.research.gate_backtest import (# noqa: E402  round-1 口径按构造复用
    WING, TRAP_DAYS, load, sma_series, episodes_of, confirmed_swing_highs,
)

WINDOW = 120
TERMINAL = (60, 120)
STOP_MULT = 2.0
SENS_MULT = 3.0
SLIP = 0.002              # 止损出场滑点 (exit_rules_backtest 正股同款); 入场无滑点 (两世界对称)
VOL_RATIO = 1.2
VOL_LOOKBACK = 20
CLUSTER_TOL_ATR = 0.35    # level_map 同式
BOOT_DRAWS = 10_000
BOOT_SEED = 20260730
CI_Q = (0.05, 0.95)       # 90% CI
KINDS = ("swing", "ma60", "cluster")
DAYS_MAIN = (1, 2)
DAYS_APPENDIX = (3,)
STOP_MODES = ("primary", "bfloor", "structure", "sens3n")
MAX_REPLAY_SEGMENTS = 3   # 1 初始 + ≤2 重进

ERAS = (("1995-04", 1995, 2004), ("2005-09", 2005, 2009), ("2010-15", 2010, 2015),
        ("2016-19", 2016, 2019), ("2020-26", 2020, 2026))


# ---------- 基础序列 ----------

def atr_series(highs, lows, closes, n):
    """SMA-ATR。out[i] 只用 ≤i 的数据。"""
    out = [None] * len(closes)
    trs = []
    for i in range(len(closes)):
        if i == 0:
            trs.append(highs[i] - lows[i])
        else:
            pc = closes[i - 1]
            trs.append(max(highs[i] - lows[i], abs(highs[i] - pc), abs(lows[i] - pc)))
        if i >= n - 1:
            out[i] = sum(trs[i - n + 1:i + 1]) / n
    return out


def confirmed_swing_lows(lows, wing=WING):
    """[(idx, low)] 严格单谷, 与 confirmed_swing_highs 镜像。"""
    out = []
    for i in range(wing, len(lows) - wing):
        win = lows[i - wing:i + wing + 1]
        if lows[i] == min(win) and win.count(lows[i]) == 1:
            out.append((i, lows[i]))
    return out


def vol_ok(vols, j, ratio=VOL_RATIO, lb=VOL_LOOKBACK):
    """完成日量比 ≥ ratio。分母 = SMA20 **含完成日**, 与 technical.volume_ratio /
    regime_transition._volume_ok 逐点一致 (审查修正 2026-07-30: 原实现不含完成日,
    比 live 规则系统性偏松)。数据不足 = False。"""
    if j < lb - 1:
        return False
    base = sum(vols[j - lb + 1:j + 1]) / lb
    return base > 0 and vols[j] / base >= ratio


def last_swing_high_array(n, swings_h, wing=WING):
    """L[j] = 第 j 日可用的最后一个已确认 swing high (round-1 gate_level('swing') 同口径)。

    gate_level 每次调用 O(P) 扫全 pivot, 120 日窗 × 18 格 × 4721 episode 会爆;
    这里一次遍历预计算成数组, 语义逐点等价 (usable = idx + wing <= j, 取最后一个)。
    """
    out = [None] * n
    ptr, cur = 0, None
    for j in range(n):
        while ptr < len(swings_h) and swings_h[ptr][0] + wing <= j:
            cur = swings_h[ptr][1]
            ptr += 1
        out[j] = cur
    return out


# ---------- cluster gate (破位日画一次, 固定) ----------

def cluster_gate(i, close_i, swings_h, swings_l, atr14_i):
    """头顶最近 touches≥2 pivot 簇的上沿。无 → None (gate 不存在, 永不触发)。

    pivot 只用 idx+WING ≤ i 已确认的 (无前视); tol = 0.35×ATR14@i;
    greedy 分组 (排序后与组均值差 > tol 即开新组); 候选簇 = 上沿 > close_i,
    取上沿最小者 (最近的头顶供给, 清掉它 = 修复证明)。
    """
    if atr14_i is None:
        return None
    prices = sorted(p for idx, p in swings_h + swings_l if idx + WING <= i)
    if not prices:
        return None
    tol = CLUSTER_TOL_ATR * atr14_i
    groups, cur = [], [prices[0]]
    for p in prices[1:]:
        if p - (sum(cur) / len(cur)) > tol:
            groups.append(cur)
            cur = [p]
        else:
            cur.append(p)
    groups.append(cur)
    overhead = [max(g) for g in groups if len(g) >= 2 and max(g) > close_i]
    return min(overhead) if overhead else None


# ---------- fire / stop / policy 机械件 ----------

def fire_scan(start, end, closes, level_fn, need, vols=None, vol_on=False):
    """首个 j∈(start, end]: 连续 need 日收盘 > level 且 (vol off 或完成日量比过)。

    level_fn(j) -> float|None; None 清零 streak (round-1 同)。
    返回 (j, level_at_j) 或 None。
    """
    streak = 0
    for j in range(start + 1, end + 1):
        lvl = level_fn(j)
        if lvl is None:
            streak = 0
            continue
        streak = streak + 1 if closes[j] > lvl else 0
        if streak >= need and (not vol_on or vol_ok(vols, j)):
            return j, lvl
    return None


def stop_scan(entry_j, end, closes, stop_lvl):
    """收盘 < stop ×1 日 → 次日收盘出场。返回 (trigger, exit_idx, exit_px_raw) 或 None。

    触发在窗口最后一根 bar (m == end) = 出场悬置: 无次日可成交, 返回 None,
    持仓按终点收盘 mark、不计滑点、不计入止损率 (审查修正 :
    原 min(m+1, end) 会让终点日触发按当日收盘+滑点成交, 与 k<end 的悬置口径不一致)。
    """
    if stop_lvl is None:
        return None
    for m in range(entry_j + 1, end + 1):
        if closes[m] < stop_lvl:
            if m == end:
                return None
            return m, m + 1, closes[m + 1]
    return None


def nearest_swing_low_below(entry_j, entry_px, swings_l):
    """入场日已确认 (idx+WING≤entry_j)、价格 < entry_px 的最近 swing low。无 → None。"""
    cands = [p for idx, p in swings_l if idx + WING <= entry_j and p < entry_px]
    return max(cands) if cands else None


def stop_level_for(mode, world, entry_j, entry_px, atr20, gate_lvl, swings_l):
    """D2 锁定的四口径。world ∈ {'A','B'}。返回 stop 价 (None = 该口径下无止损可定, 回退 2N)。"""
    two_n = entry_px - STOP_MULT * atr20[entry_j] if atr20[entry_j] else None
    if mode == "primary":
        return two_n
    if mode == "sens3n":
        return entry_px - SENS_MULT * atr20[entry_j] if atr20[entry_j] else None
    if mode == "bfloor":
        return two_n if world == "A" else (gate_lvl if gate_lvl is not None else two_n)
    if mode == "structure":
        sl = nearest_swing_low_below(entry_j, entry_px, swings_l)
        return sl if sl is not None else two_n
    raise ValueError(mode)


def simulate_segments(i, end, closes, level_fn, need, vols, vol_on,
                      mode, atr20, swings_l, max_segments=1):
    """World B: [(entry_j, entry_px, exit_idx|None, exit_px_raw|None, stop_lvl)]。

    max_segments=1 = single-shot; >1 = machine-replay (止损后从出场日之后重新数 streak)。
    """
    segs, cursor = [], i
    while len(segs) < max_segments:
        fired = fire_scan(cursor, end, closes, level_fn, need, vols, vol_on)
        if not fired:
            break
        j, lvl = fired
        px = closes[j]
        stop = stop_level_for(mode, "B", j, px, atr20, lvl, swings_l)
        hit = stop_scan(j, end, closes, stop)
        if hit:
            _, x, xp = hit
            segs.append((j, px, x, xp, stop))
            cursor = x            # 出场日之后重新等 gate
            if x >= end:
                break
        else:
            segs.append((j, px, None, None, stop))
            break
    return segs


def a_segments(i, end, closes, mode, atr20, swings_l):
    """World A: 破位日收盘入场, 同函数形状。"""
    px = closes[i]
    stop = stop_level_for(mode, "A", i, px, atr20, None, swings_l)
    hit = stop_scan(i, end, closes, stop)
    if hit:
        _, x, xp = hit
        return [(i, px, x, xp, stop)]
    return [(i, px, None, None, stop)]


def policy_value(segments, k, closes):
    """从破位日 1 元本金到终点 k 的 policy 收益 (现金段=1)。止损出场滑点在此计入。"""
    v = 1.0
    for (j, px, x, xp, _stop) in segments:
        if j > k:
            break
        if x is not None and x <= k:
            v *= xp * (1 - SLIP) / px
        else:
            v *= closes[k] / px       # 终点仍持仓 (或触发未出场) 按终点收盘 mark
            break
    return v - 1.0


def whipsaw_cost(segments, k):
    """终点前已完成的止损段亏损合计 (负数; 盈利止损段不计)。"""
    c = 0.0
    for (j, px, x, xp, _stop) in segments:
        if x is not None and x <= k:
            r = xp * (1 - SLIP) / px - 1.0
            if r < 0:
                c += r
    return c


def stopped_by(segments, k):
    return any(x is not None and x <= k for (_j, _px, x, _xp, _s) in segments)


# ---------- 主循环 ----------

def collect(kdir=KDIR, kinds=KINDS, extra_gates=None):
    """全池扫描 → 每 episode 一条记录 (A 各止损口径 + 每格每口径的 B)。

    **默认参数 = round-2 原样路径, 逐字未动**—— 衍生回测 (hvn) 靠扩展点接入,
    不靠复制一份 collect。回归对照 ("原三 kind 必须复现 round-2 数字") 的效力
    完全依赖这一点: 若这里改了默认行为, 对照就成了自证。
    extra_gates: {name: builder(ctx) -> level_fn}; ctx 见下方构造处。
    """
    episodes = []
    for path in sorted(kdir.glob("*.jsonl")):
        bars = load(path)
        if len(bars) < 300:
            continue
        closes = [b["close"] for b in bars]
        highs = [b["high"] for b in bars]
        lows = [b["low"] for b in bars]
        vols = [b.get("volume") or 0 for b in bars]
        ma60 = sma_series(closes, 60)
        atr20 = atr_series(highs, lows, closes, 20)
        atr14 = atr_series(highs, lows, closes, 14)
        swings_h = confirmed_swing_highs(highs)
        swings_l = confirmed_swing_lows(lows)
        swing_arr = last_swing_high_array(len(closes), swings_h)   # 逐点 ≡ round-1 gate_level

        for i in episodes_of(closes, ma60):
            end = min(i + WINDOW, len(closes) - 1)
            if end - i < 50:          # round-1 同守卫 (max(FWD)+TRAP_DAYS)
                continue
            rec = {"ticker": path.stem, "i": i, "end": end, "month": bars[i]["time"][:7],
                   "year": int(bars[i]["time"][:4]),
                   "terminals": {T: i + T for T in TERMINAL if i + T <= end}}
            if not rec["terminals"]:
                continue    # 审查修正: 无任何终点的 episode 不承载裁决, 不进 n/Kish/fire_rate

            rec["A"] = {m: a_segments(i, end, closes, m, atr20, swings_l)
                        for m in STOP_MODES}
            c_gate = cluster_gate(i, closes[i], swings_h, swings_l, atr14[i])
            level_fns = {"swing": (lambda j, _a=swing_arr: _a[j]),
                         "ma60": (lambda j, _a=ma60: _a[j]),
                         "cluster": (lambda j, _g=c_gate: _g)}
            if extra_gates:
                ctx = {"bars": bars, "i": i, "close_i": closes[i], "closes": closes,
                       "atr14_i": atr14[i], "atr20": atr20,
                       "swings_h": swings_h, "swings_l": swings_l,
                       "cluster_gate": c_gate, "ticker": path.stem}
                for name, builder in extra_gates.items():
                    level_fns[name] = builder(ctx)

            cells = {}
            for kind in kinds:
                level_fn = level_fns[kind]
                for need in DAYS_MAIN + DAYS_APPENDIX:
                    for von in (False, True):
                        # fire 与止损口径无关: 每格只扫一次, 四口径只各自跑 stop_scan
                        fired = fire_scan(i, end, closes, level_fn, need, vols, von)
                        segs = {}
                        if fired:
                            j0, lvl0 = fired
                            px0 = closes[j0]
                            for m in STOP_MODES:
                                stop = stop_level_for(m, "B", j0, px0, atr20, lvl0, swings_l)
                                hit = stop_scan(j0, end, closes, stop)
                                segs[m] = ([(j0, px0, hit[1], hit[2], stop)] if hit
                                           else [(j0, px0, None, None, stop)])
                            replay = simulate_segments(i, end, closes, level_fn, need,
                                                       vols, von, "primary", atr20,
                                                       swings_l,
                                                       max_segments=MAX_REPLAY_SEGMENTS)
                        else:
                            segs = {m: [] for m in STOP_MODES}
                            replay = []
                        cell = {"segs": segs, "replay": replay, "fired": bool(fired)}
                        if fired:
                            low_so_far = min(closes[i:j0 + 1])
                            trap_end = min(j0 + TRAP_DAYS, end)
                            cell["delay"] = j0 - i
                            cell["lateness"] = px0 / low_so_far - 1
                            cell["trap"] = any(closes[kk] < lvl0
                                               for kk in range(j0 + 1, trap_end + 1))
                        cells[(kind, need, von)] = cell
            rec["cells"] = cells
            rec["closes_ref"] = closes      # 供 policy_value 用 (同票共享引用, 不复制)
            episodes.append(rec)
    return episodes


# ---------- 汇总 ----------

def _pct_row(values):
    v = sorted(values)
    n = len(v)
    return {"mean": sum(v) / n, "median": st.median(v),
            "p10": v[max(0, int(n * 0.10) - 1)], "p90": v[min(n - 1, int(n * 0.90))]}


def _half_kelly(returns):
    wins = [r for r in returns if r > 0]
    losses = [-r for r in returns if r < 0]
    if not wins or not losses:
        return None
    p = len(wins) / (len(wins) + len(losses))
    b = (sum(wins) / len(wins)) / (sum(losses) / len(losses))
    return (p - (1 - p) / b) / 2


def summarize(episodes, kinds=KINDS):
    """全部格 × 口径 × 终点的 policy 汇总 + 诊断。kinds 默认 = round-2 原样。"""
    out = {"n_episodes": len(episodes), "cells": {}, "A": {}, "cohorts": {}}
    months = Counter(e["month"] for e in episodes)
    out["months"] = len(months)
    out["kish_n"] = round(len(episodes) **2 / sum(v * v for v in months.values()), 1)

    for m in STOP_MODES:
        for T in TERMINAL:
            vals = [policy_value(e["A"][m], e["terminals"][T], e["closes_ref"])
                    for e in episodes if T in e["terminals"]]
            if not vals:
                continue
            row = _pct_row(vals)
            row["n"] = len(vals)
            row["stop_rate"] = (sum(stopped_by(e["A"][m], e["terminals"][T])
                                    for e in episodes if T in e["terminals"]) / len(vals))
            row["whipsaw"] = (sum(whipsaw_cost(e["A"][m], e["terminals"][T])
                                  for e in episodes if T in e["terminals"]) / len(vals))
            row["half_kelly"] = _half_kelly(vals)
            out["A"][f"{m}_T{T}"] = row

    for kind in kinds:
        for need in DAYS_MAIN + DAYS_APPENDIX:
            for von in (False, True):
                key = f"{kind}_x{need}{'_vol' if von else ''}"
                ck = (kind, need, von)
                cell_out = {"appendix": need in DAYS_APPENDIX}
                fired_recs = [e for e in episodes if e["cells"][ck]["fired"]]
                cell_out["fire_rate"] = len(fired_recs) / len(episodes)
                if fired_recs:
                    cell_out["trap_rate"] = (sum(e["cells"][ck]["trap"] for e in fired_recs)
                                             / len(fired_recs))
                    cell_out["delay_median"] = st.median(e["cells"][ck]["delay"] for e in fired_recs)
                    cell_out["lateness_median"] = st.median(
                        e["cells"][ck]["lateness"] for e in fired_recs)
                for m in STOP_MODES:
                    for T in TERMINAL:
                        elig = [e for e in episodes if T in e["terminals"]]
                        if not elig:
                            continue
                        b_vals, diffs, fired_rets, nf_vals = [], [], [], []
                        # _by_month 槽位: [Σdiff, n, Σ(stopB−stopA),
                        #                  Σ A@notfired, n_nf, Σ A@fired, n_f]
                        by_month = defaultdict(lambda: [0.0, 0, 0, 0.0, 0, 0.0, 0])
                        vals_by_month = defaultdict(lambda: ([], []))   # p10-diff CI 用
                        n_fired_at, stopped_fired, stop_pol, whip = 0, 0, 0, 0.0
                        for e in elig:
                            k = e["terminals"][T]
                            segs = e["cells"][ck]["segs"][m]
                            b = policy_value(segs, k, e["closes_ref"])
                            a = policy_value(e["A"][m], k, e["closes_ref"])
                            b_vals.append(b)
                            diffs.append(b - a)
                            whip += whipsaw_cost(segs, k)
                            sb = stopped_by(segs, k)
                            sa = stopped_by(e["A"][m], k)
                            stop_pol += int(sb)
                            # fired@T = 触发且入场日 ≤ 终点 (审查修正: 横截面一致的分组;
                            # 晚于终点触发的 episode 在该终点上属于"被 gate 拒之门外")
                            fired_at = bool(segs) and segs[0][0] <= k
                            agg = by_month[e["month"]]
                            agg[0] += b - a
                            agg[1] += 1
                            agg[2] += int(sb) - int(sa)
                            if fired_at:
                                n_fired_at += 1
                                fired_rets.append(b)
                                stopped_fired += int(sb)
                                agg[5] += a
                                agg[6] += 1
                            else:
                                nf_vals.append(a)
                                agg[3] += a
                                agg[4] += 1
                            if m == "primary" and T == 60 and need not in DAYS_APPENDIX:
                                vb, va = vals_by_month[e["month"]]
                                vb.append(b)
                                va.append(a)
                        row = _pct_row(b_vals)
                        row["n"] = len(b_vals)
                        row["diff_mean"] = sum(diffs) / len(diffs)
                        row["fire_rate_at_T"] = n_fired_at / len(elig)
                        row["stop_rate_fired"] = (stopped_fired / n_fired_at
                                                  if n_fired_at else None)
                        row["stop_rate_policy"] = stop_pol / len(elig)
                        row["whipsaw"] = whip / len(elig)
                        row["half_kelly_fired"] = _half_kelly(fired_rets) if fired_rets else None
                        if nf_vals:
                            row["notfired_A_mean"] = sum(nf_vals) / len(nf_vals)
                            row["notfired_A_median"] = st.median(nf_vals)
                            row["notfired_n"] = len(nf_vals)
                        row["_by_month"] = dict(by_month)   # bootstrap 用, render 前剥离
                        if vals_by_month:
                            row["_by_month_vals"] = dict(vals_by_month)
                        cell_out[f"{m}_T{T}"] = row
                # machine-replay (primary only)
                for T in TERMINAL:
                    elig = [e for e in episodes if T in e["terminals"]]
                    if not elig:
                        continue
                    rv = [policy_value(e["cells"][ck]["replay"], e["terminals"][T],
                                       e["closes_ref"]) for e in elig]
                    av = [policy_value(e["A"]["primary"], e["terminals"][T], e["closes_ref"])
                          for e in elig]
                    cell_out[f"replay_T{T}"] = {
                        "mean": sum(rv) / len(rv), "median": st.median(rv),
                        "diff_mean": sum(r - a for r, a in zip(rv, av)) / len(rv),
                        # 只数终点前已发生的入场 (审查修正: 终点后的重进不属于该终点的报告)
                        "n_reentry_episodes": sum(
                            1 for e in elig
                            if len([s for s in e["cells"][ck]["replay"]
                                    if s[0] <= e["terminals"][T]]) > 1),
                    }
                out["cells"][key] = cell_out

    # cohorts: primary T+60 paired diff 点估计
    for era, y0, y1 in ERAS:
        sub = [e for e in episodes if y0 <= e["year"] <= y1 and 60 in e["terminals"]]
        if not sub:
            continue
        row = {"n": len(sub)}
        for kind in kinds:
            for need in DAYS_MAIN:
                for von in (False, True):
                    ck = (kind, need, von)
                    d = [policy_value(e["cells"][ck]["segs"]["primary"], e["terminals"][60],
                                      e["closes_ref"])
                         - policy_value(e["A"]["primary"], e["terminals"][60], e["closes_ref"])
                         for e in sub]
                    row[f"{kind}_x{need}{'_vol' if von else ''}"] = round(sum(d) / len(d), 4)
        out["cohorts"][era] = row
    return out


# ---------- bootstrap (按月聚类, 全指标共享月索引) ----------

def bootstrap_cis(summary, modes=("primary", "bfloor"), draws=BOOT_DRAWS, seed=BOOT_SEED):
    """对主 12 格 × modes × T 的 diff_mean 做按月 block bootstrap 90% CI。

    per-month (sum_diff, count) 聚合 → 重采样月份 → Σsum/Σcount。
    numpy 可用则矩阵化 (draws×M 索引全指标共享), 否则纯 Python 退化 (慢, 测试用)。
    """
    months_all = sorted({m for cell in summary["cells"].values()
                         for mk in modes for T in TERMINAL
                         for m in cell.get(f"{mk}_T{T}", {}).get("_by_month", {})})
    M = len(months_all)
    if M == 0:
        return
    try:
        import numpy as np
        rng = np.random.default_rng(seed)
        idx = rng.integers(0, M, size=(draws, M))
        use_np = True
    except ImportError:
        import random
        rng = random.Random(seed)
        idx = [[rng.randrange(M) for _ in range(M)] for _ in range(min(draws, 2000))]
        use_np = False

    ZERO = [0.0, 0, 0, 0.0, 0, 0.0, 0]

    def _ratio_ci(bm, num_slot, den_slot):
        """CI of Σnum/Σden over month resamples (共享 idx 矩阵)。"""
        nums = [bm.get(m, ZERO)[num_slot] for m in months_all]
        dens = [bm.get(m, ZERO)[den_slot] for m in months_all]
        if use_np:
            import numpy as np
            s = np.asarray(nums, dtype=float)[idx].sum(axis=1)
            c = np.asarray(dens, dtype=float)[idx].sum(axis=1)
            vals = s[c > 0] / c[c > 0]
            if not len(vals):
                return None
            return [round(float(np.quantile(vals, CI_Q[0])), 4),
                    round(float(np.quantile(vals, CI_Q[1])), 4)]
        vals = []
        for draw in idx:
            s = sum(nums[t] for t in draw)
            c = sum(dens[t] for t in draw)
            if c:
                vals.append(s / c)
        if not vals:
            return None
        vals.sort()
        return [round(vals[int(len(vals) * CI_Q[0])], 4),
                round(vals[min(len(vals) - 1, int(len(vals) * CI_Q[1]))], 4)]

    def _gap_ci(bm):
        """notfired-vs-fired 反事实 gap: (Σnf/Σn_nf − Σf/Σn_f) per draw (H2 显著性)。"""
        nf_s = [bm.get(m, ZERO)[3] for m in months_all]
        nf_c = [bm.get(m, ZERO)[4] for m in months_all]
        f_s = [bm.get(m, ZERO)[5] for m in months_all]
        f_c = [bm.get(m, ZERO)[6] for m in months_all]
        if use_np:
            import numpy as np
            a = np.asarray(nf_s, dtype=float)[idx].sum(axis=1)
            b = np.asarray(nf_c, dtype=float)[idx].sum(axis=1)
            c = np.asarray(f_s, dtype=float)[idx].sum(axis=1)
            d = np.asarray(f_c, dtype=float)[idx].sum(axis=1)
            mask = (b > 0) & (d > 0)
            if not mask.any():
                return None
            gaps = a[mask] / b[mask] - c[mask] / d[mask]
            return [round(float(np.quantile(gaps, CI_Q[0])), 4),
                    round(float(np.quantile(gaps, CI_Q[1])), 4)]
        gaps = []
        for draw in idx:
            b = sum(nf_c[t] for t in draw)
            d = sum(f_c[t] for t in draw)
            if b and d:
                gaps.append(sum(nf_s[t] for t in draw) / b
                            - sum(f_s[t] for t in draw) / d)
        if not gaps:
            return None
        gaps.sort()
        return [round(gaps[int(len(gaps) * CI_Q[0])], 4),
                round(gaps[min(len(gaps) - 1, int(len(gaps) * CI_Q[1]))], 4)]

    for key, cell in summary["cells"].items():
        if cell.get("appendix"):
            continue
        for mk in modes:
            for T in TERMINAL:
                row = cell.get(f"{mk}_T{T}")
                if not row or "_by_month" not in row:
                    continue
                bm = row["_by_month"]
                row["diff_ci90"] = _ratio_ci(bm, 0, 1)
                row["stop_rate_diff_ci90"] = _ratio_ci(bm, 2, 1)
                row["notfired_gap_ci90"] = _gap_ci(bm)
    summary["bootstrap"] = {"draws": len(idx), "backend": "numpy" if use_np else "python",
                            "seed": seed}


def p10_diff_cis(summary, draws=1000, seed=BOOT_SEED + 1):
    """p10(B)−p10(A) 的按月 bootstrap CI — 全部 12 主格 × primary × T+60, 无 lazy 选择自由度。

    p10 估计量与 _pct_row 同一索引约定 (round-1 同款), CI 内外一致。
    独立种子 (主 bootstrap seed+1), 与主 CI 的月索引矩阵无关。
    """
    def _p10(sorted_vals):
        n = len(sorted_vals)
        return sorted_vals[max(0, int(n * 0.10) - 1)]

    try:
        import numpy as np
        use_np = True
    except ImportError:
        import random
        use_np = False
        draws = min(draws, 300)

    for key, cell in summary["cells"].items():
        if cell.get("appendix"):
            continue
        row = cell.get("primary_T60")
        if not row or "_by_month_vals" not in row:
            continue
        bmv = row["_by_month_vals"]
        months = sorted(bmv)
        M = len(months)
        if not M:
            continue
        b_all = sorted(x for mo in months for x in bmv[mo][0])
        a_all = sorted(x for mo in months for x in bmv[mo][1])
        row["p10_diff_point"] = round(float(_p10(b_all) - _p10(a_all)), 4)
        if use_np:
            import numpy as np
            rng = np.random.default_rng(seed)
            b_arr = {mo: np.asarray(bmv[mo][0]) for mo in months}
            a_arr = {mo: np.asarray(bmv[mo][1]) for mo in months}
            vals = []
            for _ in range(draws):
                pick = rng.integers(0, M, size=M)
                b = np.sort(np.concatenate([b_arr[months[t]] for t in pick]))
                a = np.sort(np.concatenate([a_arr[months[t]] for t in pick]))
                vals.append(_p10(list(b)) - _p10(list(a)))
        else:
            import random
            rng = random.Random(seed)
            vals = []
            for _ in range(draws):
                pick = [rng.randrange(M) for _ in range(M)]
                b = sorted(x for t in pick for x in bmv[months[t]][0])
                a = sorted(x for t in pick for x in bmv[months[t]][1])
                vals.append(_p10(b) - _p10(a))
        vals.sort()
        row["p10_diff_ci90"] = [
            round(float(vals[int(len(vals) * CI_Q[0])]), 4),
            round(float(vals[min(len(vals) - 1, int(len(vals) * CI_Q[1]))]), 4)]
    meta = summary.setdefault("bootstrap", {})
    meta["p10_draws"] = draws
    meta["p10_seed"] = seed


def strip_private(summary):
    for cell in summary["cells"].values():
        for row in cell.values():
            if isinstance(row, dict):
                row.pop("_by_month", None)
                row.pop("_by_month_vals", None)
    return summary


def d1_flags(summary):
    """D1 三分支的机械读数 (预注册于 design doc; 只读 primary, bfloor 仅测矛盾)。"""
    flags = {}
    for key, cell in summary["cells"].items():
        if cell.get("appendix"):
            continue
        r = cell.get("primary_T60")
        if not r or not r.get("diff_ci90"):
            continue
        ci = r["diff_ci90"]
        sci = r.get("stop_rate_diff_ci90")
        pci = r.get("p10_diff_ci90")
        exp_win = bool(ci[0] > 0)                       # bool() 防 numpy 标量渗进 JSON
        exp_flat = bool(ci[0] <= 0 <= ci[1])
        stop_better = bool(sci) and bool(sci[1] < 0)
        p10_better = bool(pci) and bool(pci[0] > 0)
        branch1 = exp_win or (exp_flat and (stop_better or p10_better))
        bf = cell.get("bfloor_T60") or {}
        bci = bf.get("diff_ci90")
        contradiction = bool(bci) and bool((branch1 and bci[1] < 0) or
                                           (not branch1 and ci[1] < 0 and bci[0] > 0))
        flags[key] = {"exp_win": exp_win, "exp_flat": exp_flat,
                      "stop_better": stop_better, "p10_better": p10_better,
                      "branch1": branch1, "bfloor_contradiction": contradiction}
    return flags


# ---------- 副池: 浅回调 (只报告不裁决) ----------

def shallow_report(kdir=KDIR):
    SH_WINDOW, SH_COOLDOWN, SH_TERM = 60, 40, (20, 60)
    agg = {f"{k}_x{n}": defaultdict(list) for k in ("ma20", "swing") for n in (1, 2)}
    a_agg = defaultdict(list)
    n_ep = 0
    for path in sorted(kdir.glob("*.jsonl")):
        bars = load(path)
        if len(bars) < 300:
            continue
        closes = [b["close"] for b in bars]
        highs = [b["high"] for b in bars]
        lows = [b["low"] for b in bars]
        ma20 = sma_series(closes, 20)
        ma60 = sma_series(closes, 60)
        atr20 = atr_series(highs, lows, closes, 20)
        swings_h = confirmed_swing_highs(highs)
        swings_l = confirmed_swing_lows(lows)
        swing_arr = last_swing_high_array(len(closes), swings_h)
        last = -10 **9
        for i in range(80, len(closes)):
            if i - last < SH_COOLDOWN:
                continue
            if (ma20[i] is None or ma60[i] is None or ma60[i - 20] is None
                    or closes[i] >= ma20[i] or closes[i] <= ma60[i]
                    or ma60[i] <= ma60[i - 20]):
                continue
            end = min(i + SH_WINDOW, len(closes) - 1)
            if end - i < 30:
                continue
            last = i
            n_ep += 1
            a_seg = a_segments(i, end, closes, "primary", atr20, swings_l)
            for T in SH_TERM:
                if i + T <= end:
                    a_agg[T].append(policy_value(a_seg, i + T, closes))
            for kind in ("ma20", "swing"):
                if kind == "ma20":
                    level_fn = (lambda j, _ma=ma20: _ma[j])
                else:
                    level_fn = (lambda j, _a=swing_arr: _a[j])
                for need in (1, 2):
                    segs = simulate_segments(i, end, closes, level_fn, need, None, False,
                                             "primary", atr20, swings_l)
                    for T in SH_TERM:
                        if i + T <= end:
                            agg[f"{kind}_x{need}"][T].append(policy_value(segs, i + T, closes))
    out = {"n_episodes": n_ep, "A": {}, "gates": {}}
    for T in SH_TERM:
        if a_agg[T]:
            out["A"][f"T{T}"] = {"mean": round(sum(a_agg[T]) / len(a_agg[T]), 4),
                                 "median": round(st.median(a_agg[T]), 4), "n": len(a_agg[T])}
    for gk, byT in agg.items():
        out["gates"][gk] = {f"T{T}": {"mean": round(sum(v) / len(v), 4),
                                      "median": round(st.median(v), 4)}
                            for T, v in byT.items() if v}
    return out


# ---------- render ----------

def _f(x, w=8):
    return f"{x * 100:+.1f}%".rjust(w) if x is not None else "—".rjust(w)


def render(summary, shallow=None, stage0=None):
    L = [f"=== gate backtest round-2 === {summary['n_episodes']} episodes / "
         f"{summary['months']} 月 / Kish ≈{summary['kish_n']}",
         "policy 口径: 全 episode 计入, B 未触发 = 现金 0%; 止损后现金至终点 (single-shot)", ""]
    for m in ("primary", "bfloor", "structure", "sens3n"):
        a60 = summary["A"].get(f"{m}_T60")
        if a60:
            L.append(f"World A [{m}] T+60: mean {_f(a60['mean'])} med {_f(a60['median'])} "
                     f"p10 {_f(a60['p10'])} 止损率 {a60['stop_rate']:.0%} "
                     f"whipsaw {_f(a60['whipsaw'])}")
    L.append("")
    hdr = (f"{'cell':<16}{'mode':<10}{'fire':>5}{'ΔT60':>8}{'CI90':>18}{'ΔT120':>8}"
           f"{'止损Δ':>7}{'p10':>8}{'反事实nf':>9}")
    L.append(hdr)
    for key, cell in summary["cells"].items():
        if cell.get("appendix"):
            continue
        for m in ("primary", "bfloor"):
            r60 = cell.get(f"{m}_T60")
            a60 = summary["A"].get(f"{m}_T60")
            if not r60 or not a60:
                continue
            r120 = cell.get(f"{m}_T120") or {}
            ci = r60.get("diff_ci90")
            ci_s = f"[{ci[0] * 100:+.1f},{ci[1] * 100:+.1f}]" if ci else "—"
            L.append(f"{key:<16}{m:<10}{cell['fire_rate']:>5.0%}"
                     f"{_f(r60['diff_mean'])}{ci_s:>18}{_f(r120.get('diff_mean'))}"
                     f"{(r60['stop_rate_policy'] - a60['stop_rate']) * 100:>+6.1f}pp"
                     f"{_f(r60['p10'])}{_f(r60.get('notfired_A_mean'), 9)}")
    L.append("")
    L.append("cohorts (primary ΔT60 mean):")
    for era, row in summary["cohorts"].items():
        cells = " ".join(f"{k}={v * 100:+.1f}%" for k, v in row.items() if k != "n")
        L.append(f"  {era} (n={row['n']}): {cells}")
    if summary.get("d1_flags"):
        L.append("")
        L.append("D1 机械读数 (primary T+60; 预注册 d1_flags, 不经人手挑格):")
        for key, f in summary["d1_flags"].items():
            L.append(f"  {key}: branch1={'✓' if f['branch1'] else '✗'}"
                     f" exp_win={f['exp_win']} flat={f['exp_flat']}"
                     f" stop↓={f['stop_better']} p10↑={f['p10_better']}"
                     f" bfloor矛盾={f['bfloor_contradiction']}")
    if shallow:
        L.append("")
        L.append(f"副池·浅回调 (只报告不裁决, n={shallow['n_episodes']}): "
                 f"A T+20 {shallow['A'].get('T20', {}).get('median')} "
                 f"T+60 {shallow['A'].get('T60', {}).get('median')}")
        for gk, r in shallow["gates"].items():
            L.append(f"  {gk}: T20 med {r.get('T20', {}).get('median')} "
                     f"T60 med {r.get('T60', {}).get('median')}")
    return "\n".join(L)


def main():
    episodes = collect()
    summary = summarize(episodes)
    bootstrap_cis(summary)
    p10_diff_cis(summary)
    summary["d1_flags"] = d1_flags(summary)
    strip_private(summary)
    shallow = shallow_report()

    # Stage 0: round-1 原样复刻 (conditional 口径, 无止损) — 分离数据效应
    from quant.research import gate_backtest as r1
    stage0 = r1.summarize(*r1.run())

    payload = {"stage0_round1_replica": stage0, "round2": summary, "shallow": shallow}
    if "--json" in sys.argv:
        print(json.dumps(payload, ensure_ascii=False, indent=1))
    else:
        print("——— Stage 0: round-1 复刻 (扩容池, 无止损, conditional) ———")
        print(r1.render(stage0))
        print()
        print("——— Stage 1: round-2 (止损建模, policy 口径) ———")
        print(render(summary, shallow))
    if "--report" in sys.argv:
        rp = Path(sys.argv[sys.argv.index("--report") + 1])
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text("# gate round-2 结果 (机器输出存档)\n\n```\n"
                      + r1.render(stage0) + "\n\n" + render(summary, shallow)
                      + "\n```\n\n<details><summary>full json</summary>\n\n```json\n"
                      + json.dumps(payload, ensure_ascii=False, indent=1)
                      + "\n```\n</details>\n", encoding="utf-8")
        print(f"\nreport → {rp}")
    return payload


if __name__ == "__main__":
    main()
