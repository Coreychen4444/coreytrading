#!/usr/bin/env python3
"""adaptive_volume_leg_backtest — 修复 gate 量腿: **阈值 1.2 合不合理** (预注册 round-3, 锁定后 --run 只执行一次)。

完整研究协议应保存在 private preregistration。问题: 价格腿已过、RelVR 只差阈值少许而未转移时,
1.2 这个阈值本身是否有依据。事实: 1.2 此前是沿用的 convention, 从未被回测; 早先几轮只测过量腿开/关、
计量尺与对齐方式, 没扫过阈值。

## 世界 (在 round-2 十二个世界之上追加; 池 / 锚 / 终点 / 统计原样复用; live 镜像 = W5 = ×2 + streak 任一日 RelVR≥1.2 + 快车道)
阈值轴 (主问题): W5_tXXX = W5 同构, 只把 1.2 换成 θ = XXX/100
  W5_t110 / W5_t100   **裁决序列** (固定次序步降: 先 1.1, 过了再 1.0, 一级不过即停 → FWER 不需校正)
  W5_t115 / W5_t130 / W5_t150 / W5_t000   响应曲线其余点, 只报 (t000 = 无量腿但带快车道 ≈ W3 ∪ ⚡)
尺轴 (参照臂, 只报): W6 幅度分层取大 / W6_m05 / W6_m15 / W6_waive /
  W7 双边归因 / W7_1s 单边归因 / W2any 不分层取大 — 定义见 `day_ok`。
剂量-反应诊断 (只报): 按 p 日 streak 内最大 RelVR 分桶, 看 W3 (无条件 p+1 入场) 的 T60 止损率与均值 —
  1.2 是不是一个「膝点」, 还是 1.1 与 1.2 之间根本分不出 whipsaw。

## 裁决 (T60 全样本, 运行前锁定)
三判据: ① Δmean **非劣效** CI90 下界 ≥ −0.10pp (`DMEAN_NONINF`, 决策点 3 锁 (b): 问题是「1.2 有没有白白付
覆盖」不是「1.1 更赚钱」; δ = RelVR 增益 +0.49pp 的 1/5 = round-2「看不出损益」的量级) ② Δ止损率 CI90 上界 < +3pp
③ Δ覆盖率 CI90 下界 > 0。②③ 与前几轮逐字相同。
序列: θ=1.1 对 W5 三判据全过 → 再测 θ=1.0; 全过 → 采纳最松的那级 (V:θ=1.0), 否则 V:θ=1.1; 第一级不过 → K。
no_call: Kish<30 / 回归门 (W1−W0 当前池锚 **与** W5−W1F round-2 报表原文) 不复现 / 数据不足。
曲线点与尺轴臂只报, 过了也不采纳 (另立预注册才能翻案)。

Usage: .venv/moomoo/bin/python3 quant/research/adaptive_volume_leg_backtest.py --screen   # 人口筛查 (零前向收益)
       ... --replay                                                                     # live 案例重放 (本机缓存, 零收益)
       ... --run [--json] [--report PATH] [--kdir DIR --earn-dir DIR]                   # 真数据, 锁定后只跑一次
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 直接 `python3 quant/<层>/<模块>.py` 时让 `quant.*` 可 import

import argparse  # noqa: E402
import json  # noqa: E402
from typing import Callable, Dict, List, Optional  # noqa: E402

from quant.core import technical  # noqa: E402  live ATR (Wilder) — 只用于 --replay
from quant.core.paths import KLINE_CACHE_DIR  # noqa: E402
from quant.core.sectors import SEMI_SMH, benchmark_for  # noqa: E402  冻结映射 (live 与回测同表同源)
from quant.decision import regime_transition as rt  # noqa: E402  live 常量同源: fastlane_k / 量腿阈值 / 分母地板
from quant.research import relative_volume_backtest as rv  # noqa: E402  池 / 锚 / 终点 / 统计原样复用
from quant.research import volume_clock_backtest as vc  # noqa: E402
from quant.research import volume_leg_align_backtest as va  # noqa: E402  round-2 世界 (W1any / W5) 逐字复用
from quant.research.gate_backtest import (  # noqa: E402
    load, sma_series, episodes_of, confirmed_swing_highs,
)
from quant.research.gate_backtest2 import (  # noqa: E402
    atr_series, confirmed_swing_lows, cluster_gate,
)

# ---------- 锁定项 (预注册; 测试逐条钉死) ----------

FASTLANE_K = rt.PARAMS["fastlane_k"]     # 0.5 — live 同源
VOL_RATIO = rv.VOL_RATIO                 # 1.2 = **被测的那个数** = 裁决当时的 live 值 (研究层历史常量)。
                                         # ⚠️ 本轮结果 θ=1.1 已用于 live; 本模块**不跟随** ——
                                         # W5 = 「live 镜像 @ 裁决日」, 复测必须能重现 round-3 的对照世界。
REL_FLOOR = rt.REL_FLOOR                 # 0.4
STOP_GUARD = rv.STOP_GUARD               # +3pp
DMEAN_NONINF = 0.0010                    # Δmean 非劣效边际 0.10pp (决策点 3 锁 (b),); 前三轮为 superiority (≥0)
KISH_MIN = vc.KISH_MIN
DECISION_T = vc.DECISION_T
BASE = "W5"                              # 主对照 = live 镜像
BASE_PREV = "W1F"                        # 上一轮 live 镜像 (回归门 ② 用)
BOOT_SEED = 20260909                     # 与 round-1 (20260908) / round-2 (20261908) 不共用抽样

# 阈值轴 (主问题)
THETA_LIVE = VOL_RATIO                   # 1.2 = 本轮被测的对照 (裁决后 live 已改 1.1, 见 §Verdict)
THETA_SEQ = (1.1, 1.0)                   # 固定次序步降裁决 (一级不过即停)
THETA_ARMS = (1.15, 1.3, 1.5, 0.0)       # 响应曲线其余点, 只报; 0.0 = 无量腿 (带快车道)
RELVR_BUCKETS = (0.8, 1.0, 1.1, 1.2, 1.5)   # 剂量-反应分桶边界 (p 日 streak 内最大 RelVR)


def theta_world(th: float) -> str:
    return f"W5_t{int(round(th * 100)):03d}"


def theta_of(world: str) -> float:
    return int(world[4:]) / 100


THETA_WORLDS = tuple(theta_world(t) for t in THETA_SEQ + THETA_ARMS)
DECISION_SEQ = tuple(theta_world(t) for t in THETA_SEQ)          # ("W5_t110", "W5_t100")
CURVE_ARMS = tuple(theta_world(t) for t in THETA_ARMS)

# 尺轴 (参照臂, 只报)
MAG_N = 1.0                              # 幅度分层门槛 (×ATR20@当日) = 2×fastlane_k
MAG_ARMS = (0.5, 1.5)
RET_EPS = 1e-4                           # 个股 |收收| ≤ 0.01% 视为零 → 归因权重 1 (全相对)
SCALE_ARMS = ("W6", "W6_m05", "W6_m15", "W6_waive", "W7", "W7_1s", "W2any")   # 字面量, 不随其他常量变
NEW_WORLDS = THETA_WORLDS + SCALE_ARMS
WORLDS_EXT = va.WORLDS_EXT + NEW_WORLDS

# 回归复现锚 ①: W1−W0 T60 当前池 (round-1 Verdict 追记, 六位小数) — 逐字复用 round-2 的锚与判别式
REGRESSION_CUR = va.REGRESSION_CUR
# 回归复现锚 ②: W5−W1F T60 = round-2 报表原文 (印刷精度: 4 位; Δ覆盖率 3 位)。
# 本轮主对照是 W5, 所以 W5 自己必须先复现它被裁决时的读数。
REGRESSION_R2 = {"n60": 2866, "kish": 168.2,
                 "dmean": -0.0005, "dmean_ci": (-0.0015, 0.0006),
                 "dstop": 0.0091, "dstop_ci": (0.0045, 0.0140),
                 "dcover": 0.009, "dcover_ci": (0.0064, 0.0120)}
TOL4 = 0.00005 + 1e-12                   # 四位小数印刷精度的一半
TOL3 = 0.0005 + 1e-12                    # 三位小数印刷精度的一半


# ---------- 纯函数件 ----------

def attribution_weight(r_stock: Optional[float], r_bench: Optional[float], eps: float = RET_EPS):
    """w ∈ [0,1] = 基准日收益能「解释」个股日收益的份额 (β 固定 1, 不估)。
    |r_stock| ≤ eps → 1.0 (个股没动 = 全相对, 现行 RelVR); 否则 clamp(|r_bench| / |r_stock|, 0, 1)。
    w=1 → 现行 RelVR; w=0 → 绝对量比。任一侧缺 → None。"""
    if r_stock is None or r_bench is None:
        return None
    if abs(r_stock) <= eps:
        return 1.0
    return max(0.0, min(1.0, abs(r_bench) / abs(r_stock)))


def relvr_weighted(ratio: Optional[float], bench_ratio: Optional[float], w: Optional[float],
                   floor: float = REL_FLOOR):
    """RelVR_w = 个股量比 / max(基准量比, floor)^w。w=1 与 rv.relvr_series 逐点相同。"""
    if ratio is None or bench_ratio is None or w is None:
        return None
    return ratio / (max(bench_ratio, floor) ** w)


def in_stratum(close: float, gate: float, atr: Optional[float], m: float) -> bool:
    """幅度分层: 当日 close − gate ≥ m × ATR20@当日。ATR 算不出 = 不在层内 (保守)。"""
    return bool(atr) and (close - gate) >= m * atr


def day_ok(world: str, closes, gate, ratios, relvr, atr20, bench_raw, r_stock, r_bench) -> Callable[[int], bool]:
    """第 k 日量腿过不过 —— 世界之间的唯一差异点。返回谓词 ok(k)。"""
    def rel_ok(k):
        return relvr[k] is not None and relvr[k] >= VOL_RATIO

    def abs_ok(k):
        return ratios[k] is not None and ratios[k] >= VOL_RATIO

    if world == "W5":
        return rel_ok
    if world.startswith("W5_t"):
        th = theta_of(world)
        if th <= 0:
            return lambda k: True                                  # 无量腿 (= W3 的谓词, 带快车道)
        return lambda k: relvr[k] is not None and relvr[k] >= th
    if world == "W2any":
        return lambda k: rel_ok(k) or abs_ok(k)
    if world in ("W6", "W6_m05", "W6_m15", "W6_waive"):
        m = {"W6": MAG_N, "W6_m05": MAG_ARMS[0], "W6_m15": MAG_ARMS[1], "W6_waive": MAG_N}[world]
        if world == "W6_waive":
            return lambda k: rel_ok(k) or in_stratum(closes[k], gate, atr20[k], m)
        return lambda k: rel_ok(k) or (in_stratum(closes[k], gate, atr20[k], m) and abs_ok(k))
    if world in ("W7", "W7_1s"):
        one_sided = world == "W7_1s"

        def ok(k):
            b = bench_raw[k]
            if ratios[k] is None or b is None:
                return False
            w = 1.0 if (one_sided and b <= 1.0) else attribution_weight(r_stock[k], r_bench[k])
            v = relvr_weighted(ratios[k], b, w)
            return v is not None and v >= VOL_RATIO
        return ok
    raise KeyError(world)


def fire_scan_any_ok(start, end, closes, level, ok, need=rv.NEED):
    """va.fire_scan_any 的谓词参数化版: ×2 价格腿 + streak 内任一日 ok(k) → 首个满足日 j。
    跌回 (收盘 ≤ level) streak 归零。ok = rel_ok 时与 va.fire_scan_any 逐点相同 (测试钉)。"""
    streak = 0
    for j in range(start + 1, end + 1):
        streak = streak + 1 if closes[j] > level else 0
        if streak >= need and any(ok(k) for k in range(j - streak + 1, j + 1)):
            return j
    return None


def fastlane_scan_ok(start, end, closes, atr20, level, ok, k=FASTLANE_K):
    """vc.fastlane_scan 的谓词参数化版: streak 首日 + close > level + k×ATR20 + ok(j) → j。
    ok = rel_ok 时与 vc.fastlane_scan 逐点相同 (测试钉)。"""
    for j in range(start + 1, end + 1):
        if not (closes[j - 1] <= level < closes[j]):
            continue
        a = atr20[j]
        if not a or closes[j] <= level + k * a:
            continue
        if not ok(j):
            continue
        return j
    return None


def entry_for(world, i, end_sim, closes, gate, ratios, relvr, atr20, bench_raw, r_stock, r_bench):
    """一个世界的满足日 q = min(任一日 ×2, 快车道) — 与 W5 同构。"""
    ok = day_ok(world, closes, gate, ratios, relvr, atr20, bench_raw, r_stock, r_bench)
    a = fire_scan_any_ok(i, end_sim, closes, gate, ok)
    f = fastlane_scan_ok(i, end_sim, closes, atr20, gate, ok)
    return min([x for x in (a, f) if x is not None], default=None)


def world_entries_ext3(i, end_sim, closes, gate, vols, relvr, ratios, ratios_dnx, e_mask,
                       highs, lows, atr20, bench_raw, r_stock, r_bench) -> Dict[str, Optional[int]]:
    """round-1 十世界 + round-2 两世界逐字复用 (va.world_entries_ext), 追加阈值轴与尺轴世界。"""
    q = va.world_entries_ext(i, end_sim, closes, gate, vols, relvr, ratios, ratios_dnx, e_mask,
                             highs, lows, atr20)
    for w in NEW_WORLDS:
        q[w] = entry_for(w, i, end_sim, closes, gate, ratios, relvr, atr20, bench_raw, r_stock, r_bench)
    return q


def streak_max_relvr(p, closes, gate, relvr):
    """p 日 streak (连续收盘 > gate, 含 p, 向前数) 内最大 RelVR = streak 任一日口径下 p 日「可用的量」。
    None 忽略; 全 None → None。剂量-反应诊断的分桶变量。"""
    vals = []
    j = p
    while j >= 0 and closes[j] > gate:
        if relvr[j] is not None:
            vals.append(relvr[j])
        j -= 1
    return max(vals) if vals else None


def relvr_bucket(v: Optional[float], edges=RELVR_BUCKETS) -> str:
    if v is None:
        return "NA"
    lo = None
    for e in edges:
        if v < e:
            return f"<{e:g}" if lo is None else f"{lo:g}-{e:g}"
        lo = e
    return f"≥{lo:g}"


# ---------- 主循环 (池 / 锚 / 剔除规则 = vc.collect 原样, 只多带三条序列给新世界 + relvr_p) ----------

def _bench_maps(kdir):
    raw, ret = {}, {}
    for b in ("QQQ", "SMH"):
        bars = load(Path(kdir) / f"{b}.jsonl")
        rs = rv.ratio_series([x.get("volume") or 0 for x in bars])
        raw[b] = {bars[j]["time"][:10]: rs[j] for j in range(len(bars)) if rs[j] is not None}
        ret[b] = {bars[j]["time"][:10]: bars[j]["close"] / bars[j - 1]["close"] - 1
                  for j in range(1, len(bars)) if bars[j - 1]["close"]}
    return raw, ret


def collect(kdir=rv.KDIR, earn_dir=rv.EARN_DIR):
    """全池扫描 → (episodes, drops)。episode 记录多带 `day` (日级计数) 与 `relvr_p`, 其余字段与 vc.collect 同。"""
    bench_raw_map, bench_ret_map = _bench_maps(kdir)

    episodes = []
    drops = {"no_gate": 0, "no_p": 0, "no_terminal": 0, "bench_gap": 0, "no_earnings_file": 0}
    for path in sorted(Path(kdir).glob("*.jsonl")):
        t = path.stem
        if t.startswith("_") or t in rv.ETF_EXCLUDE:
            continue
        bars = load(path)
        if len(bars) < 300:
            continue
        closes = [b["close"] for b in bars]
        highs = [b["high"] for b in bars]
        lows = [b["low"] for b in bars]
        vols = [b.get("volume") or 0 for b in bars]
        dates = [b["time"][:10] for b in bars]
        ma60 = sma_series(closes, 60)
        atr20 = atr_series(highs, lows, closes, 20)
        atr14 = atr_series(highs, lows, closes, 14)
        swings_h = confirmed_swing_highs(highs)
        swings_l = confirmed_swing_lows(lows)
        rs = rv.ratio_series(vols)
        semi = t in SEMI_SMH
        bench = "SMH" if semi else "QQQ"
        relvr = rv.relvr_series(rs, dates, bench_raw_map[bench])
        bench_raw = [bench_raw_map[bench].get(d) for d in dates]
        r_bench = [bench_ret_map[bench].get(d) for d in dates]
        r_stock = [None] + [closes[j] / closes[j - 1] - 1 if closes[j - 1] else None
                            for j in range(1, len(closes))]
        if not (Path(earn_dir) / f"{t}.json").exists():
            drops["no_earnings_file"] += 1
        rdays = vc.reaction_days_of(t, dates, earn_dir)
        e_mask = [d in rdays for d in dates]
        rs_dnx = vc.ratio_series_dnx(vols, e_mask)
        pub_days = rv.earnings_days_of(t, earn_dir)

        for i in episodes_of(closes, ma60):
            gate = cluster_gate(i, closes[i], swings_h, swings_l, atr14[i])
            if gate is None:
                drops["no_gate"] += 1
                continue
            wend = min(i + rv.WINDOW, len(closes) - 1)
            p = rv.fire_scan_v(i, wend, closes, gate, rv.NEED, lambda j: True)
            if p is None:
                drops["no_p"] += 1
                continue
            end_sim = min(p + rv.SIM_TAIL, len(closes) - 1)
            terms = {T: p + T for T in rv.TERMINALS if p + T <= len(closes) - 1}
            if not terms:
                drops["no_terminal"] += 1
                continue
            if any(relvr[j] is None for j in range(p, end_sim + 1)):
                drops["bench_gap"] += 1          # 基准覆盖规则: 对称剔除 (round-0 修订 #5)
                continue

            qs = world_entries_ext3(i, end_sim, closes, gate, vols, relvr, rs, rs_dnx, e_mask,
                                    highs, lows, atr20, bench_raw, r_stock, r_bench)
            segs = {w: rv.segs_for(q, end_sim, closes, atr20) for w, q in qs.items()}
            rec = {"ticker": t, "month": dates[p][:7], "semi": semi,
                   "pv": {}, "stopped": {}, "entered": {}, "q": dict(qs),
                   "lat": {w: (qs[w] - p) if qs[w] is not None else None for w in qs},
                   "e_tag": rv.near_earnings(
                       [dates[p]] + [dates[qs[w]] for w in qs if qs[w] is not None], pub_days),
                   "relvr_p": streak_max_relvr(p, closes, gate, relvr),
                   "day": day_counts(i, end_sim, closes, gate, rs, relvr, atr20, bench_raw, r_stock, r_bench)}
            for T, k in terms.items():
                for w in qs:
                    rec["pv"][(w, T)] = rv.policy_value(segs[w], k, closes)
                    rec["stopped"][(w, T)] = rv.stopped_by(segs[w], k)
                    rec["entered"][(w, T)] = bool(qs[w] is not None and qs[w] + 1 <= k)
            episodes.append(rec)
    return episodes, drops


def day_counts(i, end_sim, closes, gate, ratios, relvr, atr20, bench_raw, r_stock, r_bench) -> Dict[str, int]:
    """日级人口 (零前向收益): 窗内收盘 > gate 的日子里, 各世界的量腿与 W5 的分歧计数。"""
    oks = {w: day_ok(w, closes, gate, ratios, relvr, atr20, bench_raw, r_stock, r_bench)
           for w in ("W5",) + NEW_WORLDS}
    out = {"above": 0, "stratum": 0}
    for w in NEW_WORLDS:
        out[f"{w}_gain"] = 0          # 该世界过、W5 不过
        out[f"{w}_loss"] = 0          # W5 过、该世界不过
    for j in range(i + 1, end_sim + 1):
        if closes[j] <= gate:
            continue
        out["above"] += 1
        if in_stratum(closes[j], gate, atr20[j], MAG_N):
            out["stratum"] += 1
        base = oks["W5"](j)
        for w in NEW_WORLDS:
            o = oks[w](j)
            if o and not base:
                out[f"{w}_gain"] += 1
            elif base and not o:
                out[f"{w}_loss"] += 1
    return out


# ---------- 统计 / 回归门 / 剂量-反应 / 裁决 (vc.cell_stats / timing_stats 原样) ----------

def regression_r2_ok(got: Dict) -> bool:
    e = REGRESSION_R2
    return (got.get("n60") == e["n60"] and abs(got["kish"] - e["kish"]) <= 0.05 + 1e-9
            and abs(got["dmean"] - e["dmean"]) <= TOL4
            and abs(got["dmean_ci"][0] - e["dmean_ci"][0]) <= TOL4
            and abs(got["dmean_ci"][1] - e["dmean_ci"][1]) <= TOL4
            and abs(got["dstop"] - e["dstop"]) <= TOL4
            and abs(got["dstop_ci"][0] - e["dstop_ci"][0]) <= TOL4
            and abs(got["dstop_ci"][1] - e["dstop_ci"][1]) <= TOL4
            and abs(got["dcover"] - e["dcover"]) <= TOL3
            and abs(got["dcover_ci"][0] - e["dcover_ci"][0]) <= TOL4
            and abs(got["dcover_ci"][1] - e["dcover_ci"][1]) <= TOL4)


def regression_check(rows60, n_total) -> Dict:
    """双回归门: ① W1−W0 复现当前池锚 (va 判别式逐字) ② W5−W1F 复现 round-2 报表 (同 seed 20261968)。"""
    g1 = va.regression_check(rows60, n_total)
    if len(rows60) < 5:
        return {"ok": False, "w1_w0": g1, "w5_w1f": {"ok": False, "reason": "insufficient"}}
    c = vc.cell_stats(rows60, "W5", "W1F", 60, va.BOOT_SEED + 60)
    got = {"n60": c["n"], "kish": c["kish"], "dmean": c["dmean"], "dmean_ci": tuple(c["dmean_ci"]),
           "dstop": c["dstop"], "dstop_ci": tuple(c["dstop_ci"]),
           "dcover": c["dcover"], "dcover_ci": tuple(c["dcover_ci"])}
    g2 = {"ok": regression_r2_ok(got), "got": got, "expected": REGRESSION_R2}
    return {"ok": bool(g1.get("ok") and g2["ok"]), "w1_w0": g1, "w5_w1f": g2}


def dose_response(rows, T, world="W3") -> Dict[str, Dict]:
    """剂量-反应 (只报): 按 p 日 streak 内最大 RelVR 分桶, 看 `world` (缺省 W3 = 无条件 p+1 入场) 的止损率 / 均值。
    量腿的分辨力 = 桶间止损率差; 1.1-1.2 桶与 1.2-1.5 桶分不出 → 1.2 不是膝点。"""
    out: Dict[str, Dict] = {}
    for r in rows:
        b = relvr_bucket(r["relvr_p"])
        cell = out.setdefault(b, {"n": 0, "stopped": 0, "pv": 0.0, "entered": 0})
        cell["n"] += 1
        cell["stopped"] += int(bool(r["stopped"][(world, T)]))
        cell["pv"] += float(r["pv"][(world, T)])
        cell["entered"] += int(bool(r["entered"][(world, T)]))
    for b, c in out.items():
        n = c["n"]
        c["stop_rate"] = c["stopped"] / n if n else None
        c["mean_pv"] = c["pv"] / n if n else None
        c["share"] = n / len(rows) if rows else None
    return out


def summarize(episodes):
    out: Dict = {"n_total": len(episodes)}
    strata = {"full": episodes,
              "e_tag": [r for r in episodes if r["e_tag"]],
              "non_e": [r for r in episodes if not r["e_tag"]]}
    for name, rows_all in strata.items():
        out[name] = {}
        for T in rv.TERMINALS:
            rows = [r for r in rows_all if ((BASE, T) in r["pv"])]
            if len(rows) < 5:
                out[name][T] = {"n": len(rows), "insufficient": True}
                continue
            cells = {"seq": {w: vc.cell_stats(rows, w, BASE, T, BOOT_SEED + T + 10 * n)
                             for n, w in enumerate(DECISION_SEQ)}}
            if name == "full":
                cells["curve"] = {w: vc.cell_stats(rows, w, BASE, T, BOOT_SEED + T + 100 + 10 * n)
                                  for n, w in enumerate(CURVE_ARMS)}
                cells["scale"] = {w: vc.cell_stats(rows, w, BASE, T, BOOT_SEED + T + 200 + 10 * n)
                                  for n, w in enumerate(SCALE_ARMS)}
                cells["dose"] = dose_response(rows, T)
            out[name][T] = cells
    rows60 = [r for r in episodes if (BASE, 60) in r["pv"]]
    out["timing"] = ({w: vc.timing_stats(rows60, w, BASE) for w in DECISION_SEQ + CURVE_ARMS + SCALE_ARMS}
                     if rows60 else None)
    out["regression"] = regression_check(rows60, len(episodes))
    out["population"] = population(episodes)
    return out


def population(episodes) -> Dict:
    """人口筛查 (零前向收益): episode 级触发 / 先后 / 独占, 日级分歧计数, relvr_p 分桶直方图。--screen 只输出这一段。"""
    n = len(episodes)
    out: Dict = {"n_total": n, "worlds": {}, "days": {}, "relvr_p_hist": {}}
    if not n:
        return out
    base_fires = sum(1 for r in episodes if r["q"].get(BASE) is not None)
    for w in NEW_WORLDS:
        tm = vc.timing_stats(episodes, w, BASE)
        out["worlds"][w] = {"fires": sum(1 for r in episodes if r["q"].get(w) is not None), "base_fires": base_fires,
                            "only_w": tm["only_w"], "only_base": tm["only_base"],
                            "earlier": tm["earlier"], "later": tm["later"], "mean_dq": tm["mean_dq"],
                            "changed": sum(1 for r in episodes if r["q"].get(w) != r["q"].get(BASE))}
    keys = episodes[0]["day"].keys()
    out["days"] = {k: sum(r["day"][k] for r in episodes) for k in keys}
    hist: Dict[str, int] = {}
    for r in episodes:
        b = relvr_bucket(r["relvr_p"])
        hist[b] = hist.get(b, 0) + 1
    out["relvr_p_hist"] = hist
    return out


def flags(c: Dict) -> Dict[str, bool]:
    """三判据: ① Δmean 非劣效 (下界 ≥ −DMEAN_NONINF) ② 止损率守卫 ③ 覆盖显著增加。②③ 与 round-1/2 的判别式相同。"""
    return {"exp_ok": c["dmean_ci"][0] >= -DMEAN_NONINF,
            "stop_ok": c["dstop_ci"][1] < STOP_GUARD,
            "cover_ok": c["dcover_ci"][0] > 0}


def verdict(summary: Dict) -> Dict:
    """固定次序步降: DECISION_SEQ 逐级用三判据 (`flags`) 对 W5, 一级不过即停; 采纳最后一个过的 θ。"""
    reg = summary.get("regression") or {}
    if not reg.get("ok"):
        return {"branch": "no_call", "reason": "回归门不复现 (W1−W0 当前池锚 / W5−W1F round-2 报表) → 环境可疑, 中止",
                "regression": reg}
    cells = (summary.get("full") or {}).get(DECISION_T) or {}
    if not cells or cells.get("insufficient"):
        return {"branch": "no_call", "reason": "数据不足"}
    seq = cells["seq"]
    first = seq[DECISION_SEQ[0]]
    if first["kish"] < KISH_MIN:
        return {"branch": "no_call", "reason": f"Kish {first['kish']} < {KISH_MIN}"}
    keep = ("n", "kish", "dmean", "dmean_ci", "dstop", "dstop_ci", "dcover", "dcover_ci", "dp10", "lat_w", "lat_b")
    adopted, trail = None, []
    for w in DECISION_SEQ:
        f = flags(seq[w])
        trail.append({"world": w, "theta": theta_of(w), "flags": f, "cell": {k: seq[w][k] for k in keep}})
        if all(f.values()):
            adopted = w
        else:
            break
    branch = f"V:θ={theta_of(adopted):g}" if adopted else "K"
    return {"branch": branch, "adopted": adopted, "sequence": trail,
            "curve_flags": {w: flags(c) for w, c in cells["curve"].items()},
            "scale_flags": {w: flags(c) for w, c in cells["scale"].items()}}


# ---------- 报告 ----------

def render(summary, drops, vd):
    L = [f"# adaptive_volume_leg 量腿阈值裁决 — round-3 (seed {BOOT_SEED})", ""]
    L.append(f"episodes {summary['n_total']} | drops: " + " / ".join(f"{k} {v}" for k, v in drops.items()))
    reg = summary.get("regression") or {}
    L += ["", f"## 回归复现前置: **{'PASS' if reg.get('ok') else 'FAIL'}** (① W1−W0 当前池锚 {'✓' if (reg.get('w1_w0') or {}).get('ok') else '✗'} / ② W5−W1F round-2 报表 {'✓' if (reg.get('w5_w1f') or {}).get('ok') else '✗'})",
          "```json", json.dumps(reg, ensure_ascii=False, indent=1, default=str), "```"]
    hdr = ("| cmp | n | Kish | Δmean CI90 | Δ止损率 CI90 | Δp10 CI90(1k) | Δ覆盖率 CI90 "
           "| 覆盖 w/b | 止损率 w/b | 迟到 w/b |")
    sep = "|---|---|---|---|---|---|---|---|---|---|"
    for name, label in (("full", "全样本 (裁决)"), ("e_tag", "E±1 层 (报告)"), ("non_e", "非 E±1 层 (报告)")):
        for T in rv.TERMINALS:
            cells = (summary.get(name) or {}).get(T)
            if not cells:
                continue
            L += ["", f"## {label} T+{T} — 对 {BASE} (live 镜像, θ=1.2)", hdr, sep]
            if cells.get("insufficient"):
                L.append(f"| 数据不足 n={cells['n']} |")
                continue
            for w, c in cells["seq"].items():
                L.append(vc._cell_line(f"{w}(θ={theta_of(w):g}, 裁决序列)", c))
            for w, c in (cells.get("curve") or {}).items():
                L.append(vc._cell_line(f"{w}(θ={theta_of(w):g}, 曲线)", c))
            for w, c in (cells.get("scale") or {}).items():
                L.append(vc._cell_line(f"{w}(尺轴臂)", c))
            dose = cells.get("dose")
            if dose:
                L += ["", f"### 剂量-反应 T+{T}: p 日 streak 内最大 RelVR 分桶 × W3 无条件入场 (只报)",
                      "| 桶 | n | 占比 | 止损率 | 均值 |", "|---|---|---|---|---|"]
                order = sorted(dose.items(), key=lambda kv: (kv[0] == "NA", kv[0]))
                for b, c in order:
                    L.append(f"| {b} | {c['n']} | {c['share']:.1%} | {c['stop_rate']:.1%} | {c['mean_pv']:+.4f} |")
    tm_all = summary.get("timing") or {}
    if tm_all:
        L += ["", f"## 逐 episode 先后 (T60, 对 {BASE}, 只报)"]
        for w, tm in tm_all.items():
            L.append(f"- {w}: 双方触发 n={tm['n_both']}: 早 {vc._f((tm['earlier'] or 0) * 100)}% / 同 "
                     f"{vc._f((tm['same'] or 0) * 100)}% / 晚 {vc._f((tm['later'] or 0) * 100)}%; 均值 "
                     f"{vc._f(tm['mean_dq'], 2)} 日; 只有 {w} 触发 {tm['only_w']} / 只有对照 {tm['only_base']}")
    L += ["", "## 人口 (零前向收益)", "```json",
          json.dumps(summary.get("population"), ensure_ascii=False, indent=1, default=str), "```"]
    L += ["", f"## 机械裁决: **{vd['branch']}**", "```json",
          json.dumps(vd, ensure_ascii=False, indent=1, default=str), "```", ""]
    return "\n".join(L)


# ---------- live 案例重放 (本机常规缓存, live 尺: Wilder ATR20; 零收益) ----------

REPLAY_CASES: tuple = (
    # (ticker, gate, 首个观察日) — 观察窗从该日起到缓存末; 扫描起点 = 该日前一根 (与 live 首日判据同拍)。
    # 案例由使用者按本机缓存自行填写; 公开仓不附真实案例。
)
REPLAY_WORLDS = ("W5",) + NEW_WORLDS


def replay_case(ticker: str, gate: float, from_date: str, kdir=KLINE_CACHE_DIR) -> Dict:
    kl = list(rt._klines(ticker)) if kdir == KLINE_CACHE_DIR else load(Path(kdir) / f"{ticker}.jsonl")
    bench = benchmark_for(ticker)
    bk = list(rt._klines(bench)) if kdir == KLINE_CACHE_DIR else load(Path(kdir) / f"{bench}.jsonl")
    dates = [str(r["time"])[:10] for r in kl]
    closes = [float(r["close"]) for r in kl]
    vols = [int(r.get("volume") or 0) for r in kl]
    ratios = rv.ratio_series(vols)
    b_r = rv.ratio_series([int(r.get("volume") or 0) for r in bk])
    b_dates = [str(r["time"])[:10] for r in bk]
    b_close = [float(r["close"]) for r in bk]
    raw_map = {b_dates[j]: b_r[j] for j in range(len(bk)) if b_r[j] is not None}
    ret_map = {b_dates[j]: b_close[j] / b_close[j - 1] - 1 for j in range(1, len(bk)) if b_close[j - 1]}
    relvr = rv.relvr_series(ratios, dates, raw_map)
    bench_raw = [raw_map.get(d) for d in dates]
    r_bench = [ret_map.get(d) for d in dates]
    r_stock = [None] + [closes[j] / closes[j - 1] - 1 for j in range(1, len(closes))]
    atr20 = technical.atr(kl, rt.PARAMS["atr_period"])      # live 尺 (Wilder)
    start = dates.index(from_date) - 1
    end = len(kl) - 1
    fires = {}
    for w in REPLAY_WORLDS:
        q = entry_for(w, start, end, closes, gate, ratios, relvr, atr20, bench_raw, r_stock, r_bench)
        fires[w] = dates[q] if q is not None else None
    rows = []
    oks = {w: day_ok(w, closes, gate, ratios, relvr, atr20, bench_raw, r_stock, r_bench) for w in REPLAY_WORLDS}
    for j in range(start + 1, min(end, start + 6) + 1):
        w_attr = attribution_weight(r_stock[j], r_bench[j])
        rows.append({"date": dates[j], "close": closes[j], "ret": r_stock[j], "bench_ret": r_bench[j],
                     "abs": ratios[j], "bench": bench_raw[j], "relvr": relvr[j],
                     "mag_n": (closes[j] - gate) / atr20[j] if atr20[j] else None,
                     "w": w_attr, "relvr_w": relvr_weighted(ratios[j], bench_raw[j], w_attr),
                     "ok": {w: bool(oks[w](j)) for w in oks}})
    return {"ticker": ticker, "gate": gate, "bench": bench, "from": from_date, "fires": fires, "days": rows}


def render_replay(cases: List[Dict]) -> str:
    L = ["# live 案例重放 (常规缓存, Wilder ATR20 = live 尺; 零收益)", ""]
    for c in cases:
        L.append(f"## {c['ticker']} gate {c['gate']:g} (基准 {c['bench']}), 观察起 {c['from']}")
        L.append("触发日: " + " / ".join(f"{w} {d or '—'}" for w, d in c["fires"].items()))
        L.append("| 日 | 收 | 个股% | 基准% | 绝对 | 基准量比 | RelVR | 超 gate N | w | RelVR_w | " +
                 " | ".join(REPLAY_WORLDS) + " |")
        L.append("|---|---|---|---|---|---|---|---|---|---|" + "---|" * len(REPLAY_WORLDS))
        for d in c["days"]:
            f = lambda v, nd=2: "—" if v is None else f"{v:.{nd}f}"
            L.append(f"| {d['date'][5:]} | {d['close']:.2f} | {f((d['ret'] or 0) * 100, 1)} | {f((d['bench_ret'] or 0) * 100, 1)} "
                     f"| {f(d['abs'])} | {f(d['bench'])} | {f(d['relvr'])} | {f(d['mag_n'])} | {f(d['w'])} | {f(d['relvr_w'])} | "
                     + " | ".join("✓" if d["ok"][w] else "✗" for w in REPLAY_WORLDS) + " |")
        L.append("")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--screen", action="store_true", help="人口筛查: 只输出触发计数 / 先后 / 日级分歧 / relvr_p 直方图 (零前向收益)")
    ap.add_argument("--replay", action="store_true", help="live 案例重放 (常规缓存)")
    ap.add_argument("--run", action="store_true", help="真数据裁决 (锁定后只跑一次)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--report", default="tmp/backtest/adaptive_volume_leg_report.md")
    ap.add_argument("--kdir", default=str(rv.KDIR))
    ap.add_argument("--earn-dir", default=str(rv.EARN_DIR))
    a = ap.parse_args()
    if a.replay:
        if not REPLAY_CASES:
            ap.error("REPLAY_CASES is empty: add (ticker, gate, first_observation_date) cases first")
        print(render_replay([replay_case(*c) for c in REPLAY_CASES]))
        return
    if a.screen:
        episodes, drops = collect(kdir=Path(a.kdir), earn_dir=Path(a.earn_dir))
        print(json.dumps({"drops": drops, "population": population(episodes)}, ensure_ascii=False, indent=1))
        return
    if not a.run:
        ap.error("choose one of --screen / --replay / --run")
    episodes, drops = collect(kdir=Path(a.kdir), earn_dir=Path(a.earn_dir))
    summary = summarize(episodes)
    vd = verdict(summary)
    text = render(summary, drops, vd)
    out = Path(a.report)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    print(json.dumps({"drops": drops, "verdict": vd, "n": summary["n_total"]}, ensure_ascii=False, default=str)
          if a.json else text)


if __name__ == "__main__":
    main()
