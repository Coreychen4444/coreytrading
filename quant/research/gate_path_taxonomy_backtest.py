#!/usr/bin/env python3
"""gate 结构身份四分类 × 快车道适用域 — 主回测。

预注册全文: quant/research/gate_path_taxonomy_design.md
**A–G 全部由 用户 锁定 (, 运行前)。真数据只跑一次, 跑完不改判据。**

## 问题 (用户)
`ladder_entry` 采纳的快车道 (幅度达标 → 当日转移 → ladder 入场) 只在**修复 gate**
的样本上验过。live 的 `regime_transition._fastlane` **没有适用域判别式**, 突破 gate
也走同一条。那么: 快车道的收益依不依赖 gate 的**结构身份**?

## 四分类 (§3 判别式, 触发日 D1 用只含 D1 之前的信息机械算)
    ath   = max(close[0..D1-1])          δ = 0.5 × ATR20[D1]
    broke = 最近 120 交易日内存在 `episodes_of` 破位日 e 且 closes[e] < gate
    trend = swing_structure(bars[0:D1]).trend
    ①修复      broke
    ②上升续势  ¬broke ∧ trend == "up"  ∧ gate <  ath − δ
    ③箱体顶    ¬broke ∧ trend != "up"  ∧ gate <  ath − δ
    ④历史新高  ¬broke ∧ gate >= ath − δ
`broke` = `core.episodes.episodes_of` 原式 (E 锁定: 回测与 live import 同一份)。

## 样本 (§3e-G 锁定: 统一滚动上穿 —— gate2 的破位锚 episode 池 100% 是①)
    G(t) = cluster_gate(t, closes[t], ...)      头顶最近供给簇上沿, 逐日重画
    D1   = 首个 closes[j] > G(j-1)              G(j-1) > closes[j-1] ⟹ D1 必是首日
    gate = G(D1-1);  录得后 cooldown 120;  热身 D1 ≥ 180 (=120+60)
触发再过两道 (§4): 幅度 `closes[D1] > gate + 0.5×ATR20[D1]` (k 锁死) +
量腿 `RelVR ≥ 1.2` (相对口径, relative_volume 裁决 R)。量腿**只在 D1 查, 两臂共享**。

## 两世界 (每类内独立配对, 共同终点 D1+60)
FAST : D1 收盘起 ladder 50/30/20 (首档 D1 收盘; 二三档限价 max(close−{0.5,1.0}N, gate))
SLOW : close[D2] > gate 才入场 (否则持现金, ITT) → D2 收盘起**同样**ladder
两臂都用 ladder —— 本轮唯一的自由度是「早一天」, 不把 ladder 的增益混进来。
止损全臂 `gate − 0.5×ATR20@本臂确认日` (live bfloor_buf), 收盘 ×1 日, 滑点 0.2%。
「风险N」的分母两臂统一 ATR20[D1] (否则配对差单位不同)。

## 判据 (§5, 运行前锁定; 按类分格独立, 裁决终点 D1+60)
1 风险N(FAST−SLOW) 配对 CI90 上界 < 0
2 R(FAST−SLOW) 配对 CI90 下界 > 0
3 FAST 部署率 ≥ 80%
4 FAST 绝对收益均值 CI90 上界 > 0
5 止损率差 (FAST−SLOW) CI90 上界 < +5pp      ← 本轮新增, ②③正是栽在这条上
6 Kish ≥ 30, 否则**数据不足不裁决**(不并类凑样本)
任一不过 → 该类不采纳, `_fastlane` 在该类维持 ×2。四类不互相外推。
**①只作对照臂复现, 不重新裁决**(§3b-D: 样本已被 ladder_entry 用过)。

Usage: .venv/moomoo/bin/python3 quant/research/gate_path_taxonomy_backtest.py [--json] [--report PATH]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import argparse  # noqa: E402
import bisect  # noqa: E402
import json  # noqa: E402
import statistics as st  # noqa: E402
from collections import Counter, defaultdict  # noqa: E402

from quant.core.episodes import (# noqa: E402  判别式与 episode 构造的单一真源 (决策点 E)
    BROKE_WINDOW, COOLDOWN, DELTA_ATR, LOOKBACK, breakdown_in_window, episodes_of, gate_path)
from quant.core.paths import KLINE_DEEP_DIR as KDIR  # noqa: E402
from quant.core.sectors import benchmark_for  # noqa: E402  live 同一张冻结表
from quant.research.gate_backtest import (# noqa: E402
    WING, confirmed_swing_highs, load, sma_series)
from quant.research.gate_backtest2 import (# noqa: E402
    CLUSTER_TOL_ATR, SLIP, atr_series, cluster_gate, confirmed_swing_lows, stop_scan)
from quant.research.ladder_entry_backtest import (# noqa: E402  采纳物单一真源, 不另抄
    BFLOOR_BUF, LADDER, LIMIT_DAYS)
from quant.research.relative_volume_backtest import (# noqa: E402
    ETF_EXCLUDE, VOL_RATIO, ratio_series, relvr_series)

# ── 全部来自已锁定值, 本文件不新增任何可调参数 ─────────────────────────
K_MAG = 0.5                 # §4: 幅度门槛锁死 0.5, 不重扫 (ladder_entry 定版)
WARMUP = COOLDOWN + LOOKBACK    # §3e: 180 —— 低于它 broke 窗算不满
DECISION_T = 60             # §4: D1+60 裁决
REPORT_T = 20               # §4: D1+20 并列
DEPLOY_MIN = 0.80           # 判据 3
STOP_GUARD = 0.05           # 判据 5: 止损率差上界 < +5pp (escape_velocity 同阈值)
KISH_MIN = 30               # 判据 6
BOOT_DRAWS = 10_000
BOOT_SEED = 20260808
CI_Q = (0.05, 0.95)
ARMS = ("FAST", "SLOW")
CLASSES = ("repair", "uptrend", "range_top", "ath")
CLASS_CN = {"repair": "①修复", "uptrend": "②上升续势",
            "range_top": "③箱体顶", "ath": "④历史新高"}
CONTROL_ONLY = "repair"     # §3b-D: ① 只复现不裁决
FLAT_SCAN_CAP = 250         # 并列诊断「gate 下方横盘时长」的回扫上限 (不进裁决)


# ── 机械件: 与 incumbent 等价, 只为跑得动 ────────────────────────────

def gate_from_sorted(prices, close_i, atr14_i):
    """`gate_backtest2.cluster_gate` 的等价体, 输入换成**已排序的可用 pivot 价**。

    为什么要它: 滚动构造要在每个候选日画一次 gate, 而 cluster_gate 每次调用都
    `sorted(... for ... if idx + WING <= i)` —— O(P log P) × 数十万次跑不动。
    这里把排序外置成增量维护 (bisect.insort), 分组逻辑逐字照抄。
    ⚠️ 等价性由 `tests/research/test_gate_path_taxonomy.py` 对随机输入逐点钉住,
       不是靠"看起来一样"。
    """
    if atr14_i is None or not prices:
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


def trend_before(swings_h, swings_l, i):
    """`technical.swing_structure(bars[0:i])["trend"]` 的等价体 (wing=WING)。

    前缀 bars[0:i] 的最后一根是 i-1, 故可用 pivot = idx + WING <= i-1。
    fractal 的成立与否只取决于 idx±WING 那 5 根, 与更远的未来无关 ⟹ 前缀等价。
    """
    hs = [p for idx, p in swings_h if idx + WING <= i - 1]
    ls = [p for idx, p in swings_l if idx + WING <= i - 1]
    if len(hs) < 2 or len(ls) < 2:
        return "insufficient"
    hi = "HH" if hs[-1] > hs[-2] else "LH"
    lo = "HL" if ls[-1] > ls[-2] else "LL"
    return {"HHHL": "up", "LHLL": "down"}.get(hi + lo, "transition")


# `breakdown_in_window` (§3c-E 的 broke) 与 `gate_path` (§3 判别式) 住在
# `quant/core/episodes.py` —— 决策点 E 要求回测与 live import 同一份, 不在这里存第二份。


# ── 两世界 ──────────────────────────────────────────────────────────

def _limit_fill(limit, start, until, lows, opens):
    """买入限价: 盘中触及即成交; 跳空低开按开盘价 (更优)。ladder_entry 同款。"""
    for j in range(start, until + 1):
        if lows[j] <= limit:
            return j, min(limit, opens[j])
    return None


def _leg_return(j, px, stop, term, closes):
    """一档从入场到终点的收益 (止损则按次日收盘 + 滑点)。ladder_entry 同款。"""
    hit = stop_scan(j, term, closes, stop)
    if hit and hit[1] <= term:
        return hit[2] * (1 - SLIP) / px - 1.0, True
    return closes[term] / px - 1.0, False


def ladder_legs(confirm_j, gate, n_arm, term, closes, lows, opens):
    """从确认日收盘起的 ladder 50/30/20 —— 两臂共用同一份配方 (§4)。

    首档 = 确认日收盘; 二三档限价 max(close − {0.5,1.0}·N_arm, gate), 有效 10 交易日。
    """
    c = closes[confirm_j]
    until = min(confirm_j + LIMIT_DAYS, term)
    legs = []
    for w, off in LADDER:
        if off == 0.0:
            legs.append((w, confirm_j, c))
            continue
        f = _limit_fill(max(c - off * n_arm, gate), confirm_j + 1, until, lows, opens)
        if f:
            legs.append((w, f[0], f[1]))
    return legs


def measure(legs, stop, n_common, term, closes):
    """一条臂 → dict(dep, ret, riskN, risk, stopped, R)。未入场 = 全程现金。

    `n_common` 固定 ATR20[D1] —— 两臂同分母, 否则配对差的单位不同 (§3e 锁定)。
    """
    if not legs:
        return {"dep": 0.0, "ret": 0.0, "riskN": None, "risk": 0.0,
                "stopped": False, "R": 0.0, "stop_cost": 0.0}
    dep = sum(w for w, _, _ in legs)
    ret = risk = cost = 0.0
    stopped = False
    for w, j, px in legs:
        r, s = _leg_return(j, px, stop, term, closes)
        ret += w * r
        risk += w * (px - stop) / px
        if s:
            # 止损代价 (N 计, stop_cost_guard  加): 止损腿的**实现**亏损,
            # 含滑点与跳空穿越 (r 来自 _leg_return, 出场按次日收盘×(1−SLIP))。
            # 罕见的"止损却获利"为负, 不夹取 —— 实现是多少就是多少。
            # 只加新键: 既有键与两轮旧输出逐字不变 (提交前 diff 验证)。
            cost += w * (-r) * px / n_common
        stopped = stopped or s
    blend = sum(w * px for w, _, px in legs) / dep
    return {"dep": dep, "ret": ret, "riskN": (blend - stop) / n_common,
            "risk": risk, "stopped": stopped, "R": (ret / risk) if risk > 0 else 0.0,
            "stop_cost": cost}


def build_worlds(d1, gate, closes, lows, opens, atr20, term):
    """FAST / SLOW 两世界 → {arm: legs}, 外加每臂自己的 stop。"""
    n_f = atr20[d1]
    stop_f = gate - BFLOOR_BUF * n_f
    out = {"FAST": (ladder_legs(d1, gate, n_f, term, closes, lows, opens), stop_f)}

    d2 = d1 + 1
    n_s = atr20[d2]
    if closes[d2] > gate and n_s:                 # ×2 的机械代理 (ladder_entry B1 同款)
        stop_s = gate - BFLOOR_BUF * n_s
        out["SLOW"] = (ladder_legs(d2, gate, n_s, term, closes, lows, opens), stop_s)
    else:
        out["SLOW"] = ([], gate - BFLOOR_BUF * n_f)
    return out


# ── 采集 ────────────────────────────────────────────────────────────

def _flat_days(closes, d1, gate):
    """并列诊断: D1 之前连续多少日收盘 ≤ gate (gate 下方横盘时长)。不进裁决。"""
    n = 0
    for t in range(d1 - 1, max(-1, d1 - 1 - FLAT_SCAN_CAP), -1):
        if closes[t] > gate:
            break
        n += 1
    return n


def collect(kdir=KDIR, term_len=DECISION_T, require_volume=True):
    """全池滚动上穿扫描 → episode 记录。drops 逐项计数, 不静默丢。

    `require_volume=False` 只给 `ladder_timing_backtest` 的复制臂用 —— 那一轮要
    「两个构造格只差构造」, 而 ANCHOR 侧(= ladder_entry 的池)本来就没有量腿。
    **本轮裁决恒为 True**, 默认值不许动 (改了 §12 的读数就不可复现)。"""
    bench_maps = {}
    if require_volume:                     # 关掉量腿就不该再依赖基准文件存在
        for b in ("QQQ", "SMH"):
            bars = load(kdir / f"{b}.jsonl")
            rs = ratio_series([x.get("volume") or 0 for x in bars])
            bench_maps[b] = {bars[j]["time"][:10]: rs[j]
                             for j in range(len(bars)) if rs[j] is not None}

    rows = []
    drops = Counter()
    for path in sorted(kdir.glob("*.jsonl")):
        t = path.stem
        if t.startswith("_") or t in ETF_EXCLUDE:
            continue
        bars = load(path)
        if len(bars) < 300:
            drops["short_series"] += 1
            continue
        closes = [b["close"] for b in bars]
        highs = [b["high"] for b in bars]
        lows = [b["low"] for b in bars]
        opens = [b.get("open") or b["close"] for b in bars]
        vols = [b.get("volume") or 0 for b in bars]
        dates = [b["time"][:10] for b in bars]
        n = len(closes)

        ma60 = sma_series(closes, 60)
        atr20 = atr_series(highs, lows, closes, 20)
        atr14 = atr_series(highs, lows, closes, 14)
        swings_h = confirmed_swing_highs(highs)
        swings_l = confirmed_swing_lows(lows)
        eps_idx = episodes_of(closes, ma60)
        relvr = (relvr_series(ratio_series(vols), dates, bench_maps[benchmark_for(t)])
                 if require_volume else None)

        # pivot 价按「可用日 idx+WING」分桶, 增量维护有序表 (等价于每日重排序)
        avail = defaultdict(list)
        for idx, p in swings_h + swings_l:
            avail[idx + WING].append(p)
        prices, fed = [], -1

        ath = max(closes[:WARMUP]) if n > WARMUP else None
        last_fire = -10 **9
        for j in range(WARMUP, n):
            ath = max(ath, closes[j - 1])          # = max(close[0..j-1]), 无前视
            while fed < j - 1:                     # 把 ≤ j-1 可用的 pivot 喂进有序表
                fed += 1
                for p in avail.get(fed, ()):
                    bisect.insort(prices, p)

            if j - last_fire < COOLDOWN:
                continue
            term = j + term_len
            if term > n - 1 or j + 1 > n - 1:
                continue
            nj = atr20[j]
            if not nj or atr14[j - 1] is None or not atr20[j + 1]:
                continue
            # 必要条件预筛 (gate > closes[j-1] 恒成立 ⟹ 幅度腿蕴含这条)。
            # 只用来省算力, 不改语义 —— 与暴力版逐点等价, 由测试钉住。
            if closes[j] <= closes[j - 1] + K_MAG * nj:
                continue
            r = relvr[j] if require_volume else 1e9
            if r is None:
                # 基准缺当日 = 对称剔除 (§3e 锁定)。⚠️ 这个计数是**幅度已过的候选日**,
                # 不是"损失的 episode 数" —— 它们还没画 gate, 不知道会不会上穿。
                # 真正的截断代价看各类的 era 分布与 coverage 两行。
                drops["bench_gap_candidate_days"] += 1
                continue
            if r < VOL_RATIO:
                continue

            gate = gate_from_sorted(prices, closes[j - 1], atr14[j - 1])
            if gate is None:                       # 头顶无簇 = gate 不存在 = 不触发
                continue
            if closes[j] <= gate:                  # 不是上穿
                continue
            if closes[j] <= gate + K_MAG * nj:     # 幅度腿 (真判定)
                continue

            broke, n_ep, n_below = breakdown_in_window(eps_idx, closes, j, gate)
            trend = trend_before(swings_h, swings_l, j)
            cls = gate_path(broke, trend, gate, ath, DELTA_ATR * nj)

            worlds = build_worlds(j, gate, closes, lows, opens, atr20, term)
            rec = {"ticker": t, "date": dates[j], "month": dates[j][:7],
                   "year": int(dates[j][:4]),
                   "cls": cls, "trend": trend, "gate_over_ath": gate / ath,
                   "over_n": (closes[j] - gate) / nj,
                   "win_eps": n_ep, "win_eps_below": n_below,
                   "flat_days": _flat_days(closes, j, gate),
                   "slow_entered": bool(worlds["SLOW"][0])}
            for a in ARMS:
                legs, stop = worlds[a]
                rec[a] = measure(legs, stop, nj, term, closes)
            rec["fast_tranche2"] = len(worlds["FAST"][0]) >= 2
            rec["fast_tranche3"] = len(worlds["FAST"][0]) >= 3
            rows.append(rec)
            last_fire = j
    return rows, dict(drops)


# ── 统计 (按月聚类 block bootstrap, ladder_entry / gate2 同口径) ──────

def _month_index(months, draws, seed):
    import numpy as np
    rng = np.random.default_rng(seed)
    return rng.integers(0, len(months), size=(draws, len(months)))


def _boot(by_month, months, idx):
    """Σnum/Σden 的按月 block bootstrap CI90。by_month[m] = (num, den)。"""
    import numpy as np
    nums = np.asarray([by_month.get(m, (0.0, 0))[0] for m in months], float)
    dens = np.asarray([by_month.get(m, (0.0, 0))[1] for m in months], float)
    s = nums[idx].sum(axis=1)
    c = dens[idx].sum(axis=1)
    v = s[c > 0] / c[c > 0]
    if not len(v):
        return None
    return [round(float(np.quantile(v, CI_Q[0])), 4),
            round(float(np.quantile(v, CI_Q[1])), 4)]


def _pair(rows, fn, keep=lambda r: True):
    out = defaultdict(lambda: [0.0, 0])
    for r in rows:
        if not keep(r):
            continue
        m = out[r["month"]]
        m[0] += fn(r)
        m[1] += 1
    return {m: tuple(v) for m, v in out.items()}


def _winsor(vals):
    if not vals:
        return 0.0
    s = sorted(vals)
    lo, hi = s[int(len(s) * 0.05)], s[min(len(s) - 1, int(len(s) * 0.95))]
    return sum(min(max(x, lo), hi) for x in vals) / len(vals)


def analyse(rows, cls, term_len=DECISION_T, draws=BOOT_DRAWS, seed=BOOT_SEED):
    n = len(rows)
    if not n:
        return {"cls": cls, "n": 0, "kish": 0.0, "term": term_len,
                "verdict": "no_data", "crit": {}}
    mc = Counter(r["month"] for r in rows)
    months = sorted(mc)
    kish = round(n **2 / sum(v * v for v in mc.values()), 1)
    idx = _month_index(months, draws, seed)

    out = {"cls": cls, "term": term_len, "n": n, "kish": kish, "months": len(months),
           "trend_mix": dict(Counter(r["trend"] for r in rows).most_common()),
           "gate_over_ath_median": round(st.median(r["gate_over_ath"] for r in rows), 3),
           "over_n_median": round(st.median(r["over_n"] for r in rows), 2),
           "flat_days_median": round(st.median(r["flat_days"] for r in rows), 1),
           "slow_entry_rate": round(sum(r["slow_entered"] for r in rows) / n, 4),
           "fast_tranche2": round(sum(r["fast_tranche2"] for r in rows) / n, 4),
           "fast_tranche3": round(sum(r["fast_tranche3"] for r in rows) / n, 4),
           "era": dict(sorted(Counter(f"{r['year'] // 5 * 5}s" for r in rows).items()))}
    for a in ARMS:
        v = [r[a] for r in rows]
        rk = [x["riskN"] for x in v if x["riskN"] is not None]
        out[a] = {"deploy": round(sum(x["dep"] for x in v) / n, 4),
                  "riskN_median": round(st.median(rk), 3) if rk else None,
                  "ret_mean": round(sum(x["ret"] for x in v) / n, 4),
                  "ret_median": round(st.median([x["ret"] for x in v]), 4),
                  "stop_rate": round(sum(x["stopped"] for x in v) / n, 4),
                  "R_winsor": round(_winsor([x["R"] for x in v]), 4),
                  "R_median": round(st.median([x["R"] for x in v]), 4)}

    # ① 风险N 差: 配对, 只在两臂都入场的 episode 上
    both = lambda r: r["FAST"]["riskN"] is not None and r["SLOW"]["riskN"] is not None
    bm = _pair(rows, lambda r: r["FAST"]["riskN"] - r["SLOW"]["riskN"], both)
    out["riskN_diff_point"] = round(
        sum(v[0] for v in bm.values()) / max(sum(v[1] for v in bm.values()), 1), 4)
    out["riskN_diff_ci90"] = _boot(bm, months, idx)
    out["riskN_diff_n"] = sum(v[1] for v in bm.values())

    # ② R 差 / ④ FAST 绝对收益 / ⑤ 止损率差: 全 episode (ITT, 未入场 = 现金)
    # ⚠️ 止损率是 **policy 口径**: SLOW 没入场的那些 episode 记 stopped=False。
    # FAST 必定入场而 SLOW 不一定 ⟹ 判据 5 对 FAST 是**偏严**的。方向正确
    # (守卫本来就该难过), 且与 escape_velocity 的口径一致 —— 那份也是世界级
    # policy 统计。`slow_entry_rate` 一起报出来, 让这个不对称是可见的。
    for key, fn in (("R_diff", lambda r: r["FAST"]["R"] - r["SLOW"]["R"]),
                    ("fast_ret", lambda r: r["FAST"]["ret"]),
                    ("stop_diff", lambda r: float(r["FAST"]["stopped"])
                     - float(r["SLOW"]["stopped"]))):
        b = _pair(rows, fn)
        out[f"{key}_point"] = round(
            sum(v[0] for v in b.values()) / max(sum(v[1] for v in b.values()), 1), 4)
        out[f"{key}_ci90"] = _boot(b, months, idx)

    c6 = kish >= KISH_MIN
    c1 = bool(out["riskN_diff_ci90"] and out["riskN_diff_ci90"][1] < 0)
    c2 = bool(out["R_diff_ci90"] and out["R_diff_ci90"][0] > 0)
    c3 = bool(out["FAST"]["deploy"] >= DEPLOY_MIN)
    c4 = bool(out["fast_ret_ci90"] and out["fast_ret_ci90"][1] > 0)
    c5 = bool(out["stop_diff_ci90"] and out["stop_diff_ci90"][1] < STOP_GUARD)
    out["crit"] = {"c1_riskN_down": c1, "c2_R_up": c2, "c3_deploy": c3,
                   "c4_abs_exp": c4, "c5_stop_guard": c5, "c6_kish": c6,
                   "all_pass": bool(c1 and c2 and c3 and c4 and c5 and c6)}
    out["verdict"] = ("insufficient" if not c6
                      else "adopt" if out["crit"]["all_pass"] else "reject")
    return out


def verdicts(by_cls):
    """§5 机械执行, 逐类独立。① 只复现不裁决。"""
    out = {}
    for c in CLASSES:
        r = by_cls.get(c) or {"verdict": "no_data", "n": 0, "kish": 0.0}
        if c == CONTROL_ONLY:
            out[c] = {"decision": "control_only",
                      "text": f"{CLASS_CN[c]}: **对照臂, 不裁决**(§3b-D 样本已被 "
                              f"ladder_entry 用过) — 机械读数 "
                              f"{'PASS' if (r.get('crit') or {}).get('all_pass') else 'FAIL'}"
                              f" (n={r.get('n', 0)}, Kish {r.get('kish', 0)})"}
        elif r["verdict"] in ("no_data", "insufficient"):
            out[c] = {"decision": "insufficient",
                      "text": f"{CLASS_CN[c]}: **数据不足, 不裁决**"
                              f"(n={r.get('n', 0)}, Kish {r.get('kish', 0)} < {KISH_MIN}) "
                              f"— 不并类凑样本 (§3b-C)"}
        elif r["verdict"] == "adopt":
            out[c] = {"decision": "adopt",
                      "text": f"{CLASS_CN[c]}: **采纳快车道**(六判据全过)"}
        else:
            miss = [k for k, v in r["crit"].items() if not v]
            out[c] = {"decision": "reject",
                      "text": f"{CLASS_CN[c]}: **不采纳, `_fastlane` 在该类维持 ×2**"
                              f"(未过: {', '.join(miss)})"}
    return out


# ── 渲染 ────────────────────────────────────────────────────────────

def _ci(x):
    return "—" if not x else f"[{x[0]:+.4f}, {x[1]:+.4f}]"


def render(by_cls, by_cls20, vd, drops, total, cov="—"):
    L = ["=== gate 结构身份四分类 × 快车道适用域 ===",
         f"滚动上穿构造 (§3e-G) / gate=cluster / 幅度 k={K_MAG} / 量腿 RelVR≥{VOL_RATIO} @D1 / "
         f"stop=gate−{BFLOOR_BUF}N@确认日 / 限价 {LIMIT_DAYS}d / 裁决终点 D1+{DECISION_T}",
         f"样本合计 {total} episode; drops={drops}",
         f"coverage: {cov}   ⚠️ SMH 深度缓存自 2012-02 / QQQ 自 2006-07 起 —— "
         f"量腿要基准, 更早的 episode 全部出局 (§3e 已知并接受的代价)", ""]
    for c in CLASSES:
        r = by_cls.get(c)
        if not r or not r.get("n"):
            L.append(f"--- {CLASS_CN[c]} --- n=0")
            L.append("")
            continue
        L.append(f"--- {CLASS_CN[c]} --- n={r['n']}  Kish≈{r['kish']}  月={r['months']}  "
                 f"gate/ATH 中位 {r['gate_over_ath_median']:.0%}  "
                 f"超出 {r['over_n_median']:.2f}N  "
                 f"gate 下方横盘 {r['flat_days_median']:.0f}d  "
                 f"SLOW 入场率 {r['slow_entry_rate']:.0%}")
        L.append(f"    trend {r['trend_mix']}  era {r['era']}")
        L.append(f"{'臂':<8}{'部署率':>8}{'风险N':>8}{'止损率':>8}"
                 f"{'收益均值':>10}{'收益中位':>10}{'R winsor':>10}{'R 中位':>9}")
        for a in ARMS:
            d = r[a]
            rk = f"{d['riskN_median']:.2f}N" if d["riskN_median"] is not None else "—"
            L.append(f"{a:<8}{d['deploy']:>8.0%}{rk:>8}{d['stop_rate']:>8.0%}"
                     f"{d['ret_mean'] * 100:>9.1f}%{d['ret_median'] * 100:>9.1f}%"
                     f"{d['R_winsor']:>10.3f}{d['R_median']:>9.3f}")
        cr = r["crit"]
        m = lambda k: "✓" if cr.get(k) else "✗"
        L.append(f"  ①风险N(F−S) {r['riskN_diff_point']:+.3f}N CI90 {_ci(r['riskN_diff_ci90'])}"
                 f" (配对 n={r['riskN_diff_n']}) → {m('c1_riskN_down')}")
        L.append(f"  ②R(F−S) {r['R_diff_point']:+.3f} CI90 {_ci(r['R_diff_ci90'])}"
                 f" → {m('c2_R_up')}")
        L.append(f"  ③部署率 {r['FAST']['deploy']:.0%} ≥ {DEPLOY_MIN:.0%} {m('c3_deploy')}"
                 f"   ④FAST 绝对收益 CI90 {_ci(r['fast_ret_ci90'])} {m('c4_abs_exp')}")
        L.append(f"  ⑤止损率差 {r['stop_diff_point'] * 100:+.2f}pp CI90 "
                 f"{_ci(r['stop_diff_ci90'])} < +{STOP_GUARD * 100:.0f}pp {m('c5_stop_guard')}"
                 f"   ⑥Kish {m('c6_kish')}   → "
                 f"{'PASS' if cr.get('all_pass') else 'FAIL'}")
        L.append("")
    if by_cls20:
        L.append(f"D1+{REPORT_T} 并列 (不参与裁决):")
        for c in CLASSES:
            r = by_cls20.get(c)
            if r and r.get("n"):
                L.append(f"  {CLASS_CN[c]:<10} n={r['n']:<5} "
                         f"风险N F {r['FAST']['riskN_median']} / S {r['SLOW']['riskN_median']}"
                         f"  R F {r['FAST']['R_winsor']:+.3f} / S {r['SLOW']['R_winsor']:+.3f}"
                         f"  ②CI90 {_ci(r['R_diff_ci90'])}")
        L.append("")
    L.append("VERDICT (逐类独立, 不互相外推):")
    for c in CLASSES:
        L.append("  " + vd[c]["text"])
    return "\n".join(L)


def main_cli(argv=None):
    ap = argparse.ArgumentParser(description="gate 结构身份四分类 × 快车道适用域")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--report", type=Path, default=None)
    ap.add_argument("--limit", type=int, default=None, help="只跑前 N 只票 (冒烟用)")
    a = ap.parse_args(argv)

    kdir = KDIR
    if a.limit:
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        keep = [p for p in sorted(kdir.glob("*.jsonl"))][:a.limit]
        for b in ("QQQ", "SMH"):
            keep.append(kdir / f"{b}.jsonl")
        for p in dict.fromkeys(keep):
            (tmp / p.name).symlink_to(p)
        kdir = tmp

    rows, drops = collect(kdir, DECISION_T)
    rows20, _ = collect(kdir, REPORT_T)
    by_cls = {c: analyse([r for r in rows if r["cls"] == c], c) for c in CLASSES}
    by_cls20 = {c: analyse([r for r in rows20 if r["cls"] == c], c, REPORT_T)
                for c in CLASSES}
    vd = verdicts(by_cls)
    ds = sorted(r["date"] for r in rows)
    cov = (f"{ds[0]} → {ds[-1]}, {len({r['ticker'] for r in rows})} 票" if ds else "空")
    text = render(by_cls, by_cls20, vd, drops, len(rows), cov)
    if a.json:
        print(json.dumps({"decision": by_cls, "report_T20": by_cls20,
                          "verdict": vd, "drops": drops, "total": len(rows),
                          "coverage": cov},
                         ensure_ascii=False, indent=1, default=float))
    else:
        print(text)
    if a.report:
        a.report.parent.mkdir(parents=True, exist_ok=True)
        a.report.write_text("# gate 结构身份四分类 × 快车道适用域 (机器输出存档)\n\n"
                            "```\n" + text + "\n```\n")
    return 0


if __name__ == "__main__":
    sys.exit(main_cli())
