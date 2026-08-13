#!/usr/bin/env python3
"""修复 gate 的幅度条件快车道 — 主回测 (预注册 fastlane_repair_gate_design.md, 已锁定)。

## 问题 (用户)
「如果是强势突破 gate 并且突破幅度够大, 是否可以不等 ×2、当天确认」

## 为什么 escape_velocity 答不了 (设计稿 §0)
它测的是箱体顶逃逸不是修复 gate; 且两个世界都用**入场锚定**止损 (entry − 2N),
于是 FAST 与 SLOW 的每股风险按构造相同 —— 把快车道在真实系统里唯一的收益来源消掉了。
**本回测的关键差异: 两世界都用 gate 锚定止损**`gate − 0.5×ATR20@确认日` (= live bfloor_buf)。

## 三个世界 (同 episodes, 同终点, 1 元本金, 从破位日起账 = round-2 policy 口径)
设 c = 窗口内**首个**close > gate 的日子:
- **SLOW (现行)**: close[c+1] 也 > gate → c+2 收盘买入; 否则不入场持现金
- **FAST_k (裁决臂, hybrid)**: close[c] > gate + k×ATR20[c] → c+1 收盘买入; 否则回落 SLOW
- **FAST_pure_k (机制拆解)**: 达标 → c+1 收盘买入; 不达标 → 持现金
两臂各有 `_rescan` 变体 (首次确认失败后继续扫下一次机会), 检验结论是否只是"放弃规则"的产物。

## 主口径 R-multiple (§4, 运行前锁定)
`R = 终点 policy 收益 × 入场价 / (入场价 − stop)` —— 赚了几个自己承担的风险单位。
快车道的立论就是**分母更小**, 只看分子会系统性低估它。未入场 = 0。

## 判据 (§5 + §5b, 运行前锁定; 裁决单元 = k 合池, 终点 T+60)
1 R-multiple 期望差 (FAST−SLOW) CI90 下界 > 0
2 止损率差 CI90 上界 < +8pp
3 FAST 绝对 expectancy 不显著为负 (CI90 上界 > 0)
4 Kish ≥ 30
任一不满足 → 不采纳, ×2 确认保留。两个 k 都过 → 取更严的 k。

Usage: .venv/moomoo/bin/python3 quant/research/fastlane_backtest.py [--json] [--report PATH]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import argparse  # noqa: E402
import json  # noqa: E402
import statistics as st  # noqa: E402
from collections import Counter, defaultdict  # noqa: E402

from quant.core.paths import KLINE_DEEP_DIR as KDIR  # noqa: E402
from quant.research.gate_backtest import (# noqa: E402
    load, sma_series, episodes_of, confirmed_swing_highs)
from quant.research.gate_backtest2 import (# noqa: E402
    ERAS, SLIP, WINDOW, atr_series, cluster_gate, confirmed_swing_lows, stop_scan)

TERMINALS = (20, 60)
DECISION_T = 60                 # §5b-C
K_VALUES = (1.0, 1.5)           # §3, 运行前锁定; 不加第三个
BFLOOR_BUF = 0.5                # live bfloor_buf: stop = gate − 0.5×ATR20@确认日
STOP_CI_MAX = 0.08              # 判据 2: 止损率差 CI90 上界 < +8pp
KISH_MIN = 30                   # 判据 4
BOOT_DRAWS = 10_000
BOOT_SEED = 20260807
CI_Q = (0.05, 0.95)

# fast_nomag = §6 明确要求的"无幅度"对照池: 同样 c+1 早进, 但**不看幅度**。
# 若它与 fast_hybrid 一样好 → 起作用的是"早", 不是"幅度" = H-fast 的立论落空。
ARMS = ("slow", "fast_hybrid", "fast_pure", "fast_nomag")


# ---------- 单 episode 的三个世界 ----------

def _enter(entry_j, closes, end, stop):
    """入场后跑到底 → (entry_j, entry_px, exit_idx|None, exit_px|None, stop)。"""
    px = closes[entry_j]
    hit = stop_scan(entry_j, end, closes, stop)
    return (entry_j, px, hit[1], hit[2], stop) if hit else (entry_j, px, None, None, stop)


def build_worlds(i, end, closes, gate, atr20, k, rescan):
    """返回 {arm: segment|None}。None = 从未入场 (全程现金)。

    c = 首个 close > gate 的日子。rescan=False 时只看第一次机会 (设计稿字面);
    True 时首次失败后继续找下一个 c (round-1/2 fire_scan 语义)。
    """
    out = {a: None for a in ARMS}
    if gate is None:
        return out, None
    first_c = None
    c = i + 1
    pending = {a: True for a in ARMS}
    while c <= end:
        if closes[c] <= gate:
            c += 1
            continue
        if first_c is None:
            first_c = c
        a20c = atr20[c]
        if a20c is None:
            c += 1
            continue
        stop = gate - BFLOOR_BUF * a20c
        qualified = closes[c] > gate + k * a20c

        stop_f = gate - BFLOOR_BUF * a20c        # 确认日 = c
        # 无幅度对照 (§6): 只要收上 gate 就 c+1 进, 不看幅度
        if pending["fast_nomag"] and c + 1 <= end:
            out["fast_nomag"] = _enter(c + 1, closes, end, stop_f)
            pending["fast_nomag"] = False
        # FAST: 达标 → c+1 收盘 (1 日执行延迟)
        if qualified and c + 1 <= end:
            for arm in ("fast_hybrid", "fast_pure"):
                if pending[arm]:
                    out[arm] = _enter(c + 1, closes, end, stop_f)
                    pending[arm] = False

        # SLOW (以及未达标时的 fast_hybrid): 需 c+1 也 > gate → c+2 收盘
        two_day = c + 1 <= end and closes[c + 1] > gate
        if two_day and c + 2 <= end:
            a20e = atr20[c + 1] or a20c
            stop_s = gate - BFLOOR_BUF * a20e
            seg = _enter(c + 2, closes, end, stop_s)
            if pending["slow"]:
                out["slow"] = seg
                pending["slow"] = False
            if pending["fast_hybrid"]:      # 未达标 → 回落 ×2 原规则
                out["fast_hybrid"] = seg
                pending["fast_hybrid"] = False

        if not rescan or not any(pending.values()):
            break
        c += 1
        while c <= end and closes[c] > gate:   # 找下一次"从 gate 下方重新收上去"
            c += 1
    return out, first_c


# ---------- policy / R ----------

def policy_return(seg, k_idx, closes):
    """从破位日 1 元本金到终点的 policy 收益 (未入场 = 现金 = 0)。"""
    if seg is None:
        return 0.0
    j, px, x, xp, _s = seg
    if j > k_idx:
        return 0.0
    if x is not None and x <= k_idx:
        return xp * (1 - SLIP) / px - 1.0
    return closes[k_idx] / px - 1.0


def r_multiple(seg, k_idx, closes):
    """R = 收益 / 每股风险比例。未入场 = 0 (没冒风险也没收益, §5b)。"""
    if seg is None:
        return 0.0
    j, px, x, xp, stop = seg
    if j > k_idx or stop is None or px <= stop:
        return 0.0
    r = policy_return(seg, k_idx, closes)
    return r * px / (px - stop)


def stopped(seg, k_idx):
    return bool(seg and seg[2] is not None and seg[2] <= k_idx)


def risk_frac(seg):
    """每股风险 (入场价 − stop) / 入场价 —— 快车道立论的分母。"""
    if seg is None:
        return None
    _j, px, _x, _xp, stop = seg
    return (px - stop) / px if (stop is not None and px > stop) else None


def risk_n(seg, atr20):
    """每股风险的 N 值 = (入场价 − stop) / ATR20@入场日 (§4 明确要求的分布)。"""
    if seg is None:
        return None
    j, px, _x, _xp, stop = seg
    a = atr20[j] if j < len(atr20) else None
    return (px - stop) / a if (stop is not None and a and px > stop) else None


# ---------- 主循环 ----------

def collect(kdir=KDIR, rescan=False):
    rows = []
    for path in sorted(kdir.glob("*.jsonl")):
        bars = load(path)
        if len(bars) < 300:
            continue
        closes = [b["close"] for b in bars]
        highs = [b["high"] for b in bars]
        lows = [b["low"] for b in bars]
        ma60 = sma_series(closes, 60)
        atr20 = atr_series(highs, lows, closes, 20)
        atr14 = atr_series(highs, lows, closes, 14)
        sh = confirmed_swing_highs(highs)
        sl = confirmed_swing_lows(lows)
        for i in episodes_of(closes, ma60):
            end = min(i + WINDOW, len(closes) - 1)
            if end - i < 50:
                continue
            terms = {T: i + T for T in TERMINALS if i + T <= end}
            if not terms:
                continue
            gate = cluster_gate(i, closes[i], sh, sl, atr14[i])
            rec = {"ticker": path.stem, "i": i, "end": end,
                   "month": bars[i]["time"][:7], "year": int(bars[i]["time"][:4]),
                   "terminals": terms, "gate": gate, "closes": closes,
                   "atr20": atr20, "worlds": {}}
            any_conf = False
            for k in K_VALUES:
                worlds, first_c = build_worlds(i, end, closes, gate, atr20, k, rescan)
                rec["worlds"][k] = worlds
                if first_c is not None:
                    any_conf = True
                    rec["first_c"] = first_c
            # §5b 样本条件: 窗口内必须出现过首个 close > gate
            if not any_conf:
                continue
            rows.append(rec)
    return rows


# ---------- 统计 ----------

def _boot_ci(by_month, num_slot, den_slot, months, idx, use_np):
    Z = [0.0] * 8
    nums = [by_month.get(m, Z)[num_slot] for m in months]
    dens = [by_month.get(m, Z)[den_slot] for m in months]
    if use_np:
        import numpy as np
        s = np.asarray(nums, float)[idx].sum(axis=1)
        c = np.asarray(dens, float)[idx].sum(axis=1)
        v = s[c > 0] / c[c > 0]
        if not len(v):
            return None
        return [round(float(np.quantile(v, CI_Q[0])), 4),
                round(float(np.quantile(v, CI_Q[1])), 4)]
    vals = []
    for draw in idx:
        s = sum(nums[t] for t in draw)
        c = sum(dens[t] for t in draw)
        if c:
            vals.append(s / c)
    vals.sort()
    return [round(vals[int(len(vals) * CI_Q[0])], 4),
            round(vals[min(len(vals) - 1, int(len(vals) * CI_Q[1]))], 4)]


def analyse(rows, k, T=DECISION_T, draws=BOOT_DRAWS, seed=BOOT_SEED):
    """一个 k 一个终点的完整读数 (FAST_hybrid vs SLOW 配对)。"""
    elig = [r for r in rows if T in r["terminals"]]
    if not elig:
        return None
    months = Counter(r["month"] for r in elig)
    kish = round(len(elig) **2 / sum(v * v for v in months.values()), 1)

    # _by_month 槽位: [ΔR, n, Δstop, n, R_fast, n_entered_f, ret_fast, n]
    bm = defaultdict(lambda: [0.0, 0, 0.0, 0, 0.0, 0, 0.0, 0])
    agg = {a: {"R": [], "ret": [], "stop": 0, "entered": 0, "risk": [], "riskn": []}
           for a in ARMS}
    n_qualified = 0
    qual_rows = []          # 达标子集: fast 与 slow 真正走了不同入场日的那些
    for r in elig:
        ki = r["terminals"][T]
        w = r["worlds"][k]
        for a in ARMS:
            seg = w[a]
            agg[a]["R"].append(r_multiple(seg, ki, r["closes"]))
            agg[a]["ret"].append(policy_return(seg, ki, r["closes"]))
            agg[a]["stop"] += int(stopped(seg, ki))
            if seg is not None and seg[0] <= ki:
                agg[a]["entered"] += 1
                rf = risk_frac(seg)
                if rf is not None:
                    agg[a]["risk"].append(rf)
                rn = risk_n(seg, r["atr20"])
                if rn is not None:
                    agg[a]["riskn"].append(rn)
        # 达标 = fast 与 slow 走了不同的入场日
        differs = bool((w["fast_hybrid"] and w["slow"] and w["fast_hybrid"][0] != w["slow"][0])
                       or (w["fast_hybrid"] and not w["slow"]))
        if differs:
            n_qualified += 1
            qual_rows.append((r, ki, w))
        m = bm[r["month"]]
        dR = r_multiple(w["fast_hybrid"], ki, r["closes"]) - r_multiple(w["slow"], ki, r["closes"])
        m[0] += dR
        m[1] += 1
        m[2] += int(stopped(w["fast_hybrid"], ki)) - int(stopped(w["slow"], ki))
        m[3] += 1
        m[6] += policy_return(w["fast_hybrid"], ki, r["closes"])
        m[7] += 1

    mlist = sorted(bm)
    M = len(mlist)
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

    out = {"k": k, "T": T, "n": len(elig), "months": M, "kish": kish,
           "n_qualified": n_qualified,
           "qualified_pct": round(n_qualified / len(elig), 4) if elig else None}
    for a in ARMS:
        d = agg[a]
        rets = sorted(d["ret"])
        out[a] = {
            "R_mean": round(sum(d["R"]) / len(d["R"]), 4),
            "R_median": round(st.median(d["R"]), 4),
            "ret_mean": round(sum(d["ret"]) / len(d["ret"]), 4),
            "ret_p10": round(rets[max(0, int(len(rets) * 0.10) - 1)], 4),
            "stop_rate": round(d["stop"] / len(elig), 4),
            "entry_rate": round(d["entered"] / len(elig), 4),
            "risk_frac_median": (round(st.median(d["risk"]), 4) if d["risk"] else None),
            "risk_n_median": (round(st.median(d["riskn"]), 3) if d["riskn"] else None),
            "risk_n_p90": (round(sorted(d["riskn"])[int(len(d["riskn"]) * 0.9)], 3)
                           if d["riskn"] else None),
        }

    # 达标子集条件读数 (§4 要求的"每股风险 N 值分布"最有信息的切法):
    # 只在 fast 与 slow 真的走了不同路的 episode 上比 —— 其余两臂逐字相同, 差恒为 0
    if qual_rows:
        qf = [r_multiple(w["fast_hybrid"], ki, r["closes"]) for r, ki, w in qual_rows]
        qs = [r_multiple(w["slow"], ki, r["closes"]) for r, ki, w in qual_rows]
        fn = [risk_n(w["fast_hybrid"], r["atr20"]) for r, _ki, w in qual_rows]
        sn = [risk_n(w["slow"], r["atr20"]) for r, _ki, w in qual_rows]
        fn = [x for x in fn if x is not None]
        sn = [x for x in sn if x is not None]
        dq = [f - s_ for f, s_ in zip(qf, qs)]
        out["conditional"] = {
            "n": len(qual_rows),
            "fast_R_mean": round(sum(qf) / len(qf), 4),
            "slow_R_mean": round(sum(qs) / len(qs), 4),
            "dR_mean": round(sum(dq) / len(dq), 4),
            "dR_median": round(st.median(dq), 4),
            "fast_risk_n_median": round(st.median(fn), 3) if fn else None,
            "slow_risk_n_median": round(st.median(sn), 3) if sn else None,
            "fast_stop_rate": round(sum(stopped(w["fast_hybrid"], ki)
                                        for _r, ki, w in qual_rows) / len(qual_rows), 4),
            "slow_stop_rate": round(sum(stopped(w["slow"], ki)
                                        for _r, ki, w in qual_rows) / len(qual_rows), 4),
        }
        # 尾部诊断 (自加, 非预注册): ΔR 均值有多少来自最极端的 5 个 episode
        top = sorted(dq, key=abs, reverse=True)[:5]
        out["conditional"]["top5_share_of_dR"] = (
            round(sum(top) / sum(dq), 3) if sum(dq) else None)
    else:
        out["conditional"] = None
    out["dR_point"] = round(out["fast_hybrid"]["R_mean"] - out["slow"]["R_mean"], 4)
    out["dR_ci90"] = _boot_ci(bm, 0, 1, mlist, idx, use_np)
    out["dstop_point"] = round(out["fast_hybrid"]["stop_rate"] - out["slow"]["stop_rate"], 4)
    out["dstop_ci90"] = _boot_ci(bm, 2, 3, mlist, idx, use_np)
    out["fast_exp_ci90"] = _boot_ci(bm, 6, 7, mlist, idx, use_np)

    c1 = bool(out["dR_ci90"] and out["dR_ci90"][0] > 0)
    c2 = bool(out["dstop_ci90"] and out["dstop_ci90"][1] < STOP_CI_MAX)
    c3 = bool(out["fast_exp_ci90"] and out["fast_exp_ci90"][1] > 0)
    c4 = bool(kish >= KISH_MIN)
    out["crit"] = {"c1_R_diff_positive": c1, "c2_stop_within_8pp": c2,
                   "c3_abs_exp_not_negative": c3, "c4_kish": c4,
                   "all_pass": bool(c1 and c2 and c3 and c4)}

    # era cohorts (§5b-D): 并列报, 受 Kish≥30 守卫
    out["eras"] = {}
    for era, y0, y1 in ERAS:
        sub = [r for r in elig if y0 <= r["year"] <= y1]
        if not sub:
            continue
        mm = Counter(r["month"] for r in sub)
        kk = round(len(sub) **2 / sum(v * v for v in mm.values()), 1)
        d = [r_multiple(r["worlds"][k]["fast_hybrid"], r["terminals"][T], r["closes"])
             - r_multiple(r["worlds"][k]["slow"], r["terminals"][T], r["closes"]) for r in sub]
        out["eras"][era] = {"n": len(sub), "kish": kk,
                            "dR_mean": round(sum(d) / len(d), 4),
                            "ruled": kk >= KISH_MIN}
    return out


def final_verdict(results):
    """§5 的机械执行: 任一判据不满足 → 不采纳; 两个 k 都过 → 取更严的 k。"""
    passed = [r for r in results if r and r["crit"]["all_pass"]]
    if not passed:
        return {"adopt": False, "k": None, "reason": "no_k_passes",
                "text": "VERDICT: **不采纳幅度快车道, ×2 确认保留**(预注册 §5: 无 k 满足全部判据)"}
    pick = max(passed, key=lambda r: r["k"])       # 更严 = 更大的 k = 更少触发
    flips = sum(1 for e in pick["eras"].values() if e["ruled"] and e["dR_mean"] < 0)
    ruled = sum(1 for e in pick["eras"].values() if e["ruled"])
    warn = (f"  ⚠️ 但该 k 在 {flips}/{ruled} 个可裁决 era 上符号翻转 (§5b-D: 明写不静默采纳)"
            if ruled and flips >= 3 else "")
    return {"adopt": True, "k": pick["k"], "reason": "passes", "era_flips": flips,
            "text": f"VERDICT: **采纳幅度快车道, k = {pick['k']}**(两个 k 都过则取更严者)" + warn}


# ---------- render ----------

def _p(x, w=9):
    return (f"{x * 100:+.1f}%".rjust(w) if x is not None else "—".rjust(w))


def render(main, rescan, verdict):
    L = ["=== 修复 gate 幅度快车道 === 裁决终点 T+60 / gate = cluster / "
         "两世界均 gate 锚定止损 (bfloor_buf)"]
    for tag, res in (("主口径 (首次确认机会)", main), ("_rescan 变体 (失败后继续扫)", rescan)):
        L.append("")
        L.append(f"--- {tag} ---")
        for r in res:
            if not r:
                continue
            L.append(f"k={r['k']}  n={r['n']}  Kish≈{r['kish']}  "
                     f"达标(走了快车道)={r['qualified_pct']:.1%}")
            L.append(f"{'arm':<14}{'R均值':>9}{'R中位':>9}{'收益均值':>10}{'p10':>9}"
                     f"{'入场率':>8}{'止损率':>8}{'风险N中位':>11}{'风险N p90':>11}")
            for a in ARMS:
                d = r[a]
                rn = d["risk_n_median"]
                rn9 = d["risk_n_p90"]
                L.append(f"  {a:<12}{d['R_mean']:>9.3f}{d['R_median']:>9.3f}"
                         f"{_p(d['ret_mean'], 10)}{_p(d['ret_p10'], 9)}"
                         f"{d['entry_rate']:>8.0%}{d['stop_rate']:>8.0%}"
                         f"{(f'{rn:.2f}N' if rn else '—'):>11}"
                         f"{(f'{rn9:.2f}N' if rn9 else '—'):>11}")
            q = r.get("conditional")
            if q:
                top5 = q.get("top5_share_of_dR")
                top5_s = f"{top5:.0%}" if top5 is not None else "—"
                L.append(f"  [达标子集 n={q['n']}] fast R {q['fast_R_mean']:+.3f} vs "
                         f"slow R {q['slow_R_mean']:+.3f} | ΔR 均值 {q['dR_mean']:+.3f} "
                         f"中位 {q['dR_median']:+.3f} | 风险 fast {q['fast_risk_n_median']}N "
                         f"vs slow {q['slow_risk_n_median']}N | 止损率 "
                         f"{q['fast_stop_rate']:.0%} vs {q['slow_stop_rate']:.0%}"
                         f" | 最极端 5 例占 ΔR 总量 {top5_s}")
            c = r["crit"]
            L.append(f"  ΔR (fast−slow) = {r['dR_point']:+.3f}  CI90 {r['dR_ci90']}"
                     f"   → 判据1 {'✓' if c['c1_R_diff_positive'] else '✗'}")
            L.append(f"  Δ止损率 = {r['dstop_point'] * 100:+.1f}pp  CI90 "
                     f"{[round(x * 100, 1) for x in r['dstop_ci90']] if r['dstop_ci90'] else '—'}pp"
                     f"   → 判据2 {'✓' if c['c2_stop_within_8pp'] else '✗'}")
            L.append(f"  FAST 绝对 expectancy CI90 "
                     f"{[round(x * 100, 1) for x in r['fast_exp_ci90']] if r['fast_exp_ci90'] else '—'}%"
                     f"   → 判据3 {'✓' if c['c3_abs_exp_not_negative'] else '✗'}"
                     f"   判据4 Kish {'✓' if c['c4_kish'] else '✗'}"
                     f"   → {'PASS' if c['all_pass'] else 'FAIL'}")
            eras = " ".join(f"{e}={v['dR_mean']:+.3f}{'' if v['ruled'] else '(n少)'}"
                            for e, v in r["eras"].items())
            L.append(f"  era ΔR: {eras}")
            L.append("")
    L.append(verdict["text"])
    return "\n".join(L)


def main_cli():
    ap = argparse.ArgumentParser(description="修复 gate 幅度快车道回测")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--report", type=Path, default=None)
    ap.add_argument("--limit", type=int, default=None)
    a = ap.parse_args()

    kdir = KDIR
    if a.limit:
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        for p in sorted(kdir.glob("*.jsonl"))[:a.limit]:
            (tmp / p.name).symlink_to(p)
        kdir = tmp

    rows = collect(kdir, rescan=False)
    rows_rs = collect(kdir, rescan=True)
    main = [analyse(rows, k) for k in K_VALUES]
    rescan = [analyse(rows_rs, k) for k in K_VALUES]
    verdict = final_verdict(main)
    t20 = [analyse(rows, k, T=20) for k in K_VALUES]
    text = render(main, rescan, verdict)
    text += ("\n\nT+20 并列 (不参与裁决): " +
             "; ".join(f"k={r['k']} ΔR={r['dR_point']:+.3f} CI90 {r['dR_ci90']}"
                       for r in t20 if r))
    payload = {"decision_terminal": DECISION_T, "main": main, "rescan": rescan,
               "t20": t20, "verdict": verdict}
    if a.json:
        print(json.dumps(payload, ensure_ascii=False, indent=1, default=float))
    else:
        print(text)
    if a.report:
        a.report.parent.mkdir(parents=True, exist_ok=True)
        a.report.write_text("# 幅度快车道回测 (机器输出存档)\n\n```\n" + text + "\n```\n")
    return 0


if __name__ == "__main__":
    sys.exit(main_cli())
