#!/usr/bin/env python3
"""修复 gate 幅度达标突破后的入场执行 — 主回测 (预注册 private preregistration, 已锁定)。

## 问题
D1 收盘幅度达标的突破之后: 还要不要多等一天确认? 入场是收盘价全额还是 ladder?

## 研究边界
快车道否的是「**全额**提前」(FAST 全仓 vs SLOW 全仓); 本轮问的是「**分档**提前」——
变的是执行结构不是时点。而且上一轮"D1 全额 ≈ D2 全额"的平局正是本轮的前提。

## 四条臂 (同 episodes, 同 stop, 终点自 D1 起算)
D1 = episode 内首个 close > gate 且 close[D1] > gate + k·ATR20[D1] (k ∈ {0.5, 1.0})
  A  : D1 收盘全额
  B1 : D2 收盘全额; close[D2] ≤ gate 则不入场 (= 现行 ×2 的机械代理)
  B2 : D2 确认后挂「D2 收盘下方最近 HVN 带」中点限价 (更贴近 live); 无带 → 回落 B1
  C  : ladder 50/30/20 — 首档 D1 收盘, 二三档限价 max(close[D1] − {0.5,1.0}N, gate)
限价有效期 10 交易日; stop = gate − 0.5×ATR20[D1] (live bfloor_buf), 收盘 ×1 日, 滑点 0.2%。

## 判据 (§5, 运行前锁定; 按 k 分格, 裁决终点 D1+60)
1 风险N(C) − 风险N(B1) 配对 CI90 上界 < 0
2 R(C) − R(B1) 配对 CI90 下界 > 0 **且对 B2 同样成立**(两个基线都要赢)
3 部署率 ≥ 80% (杀 H0: 靠不成交刷 R)
4 C 的绝对收益均值 CI90 上界 > 0
5 Kish ≥ 30
任一不满足 → 不采纳。两个 k 都过 → 取更严的 k (更大者)。

Usage: .venv/moomoo/bin/python3 quant/research/ladder_entry_backtest.py [--json] [--report PATH]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import argparse  # noqa: E402
import json  # noqa: E402
import statistics as st  # noqa: E402
from collections import Counter, defaultdict  # noqa: E402

from quant.core import technical  # noqa: E402
from quant.core.paths import KLINE_DEEP_DIR as KDIR  # noqa: E402
from quant.decision import level_map  # noqa: E402
from quant.research.gate_backtest import (# noqa: E402
    load, sma_series, episodes_of, confirmed_swing_highs)
from quant.research.gate_backtest2 import (# noqa: E402
    SLIP, WINDOW, atr_series, cluster_gate, confirmed_swing_lows, stop_scan)

K_VALUES = (0.5, 1.0)          # §3 运行前锁定, 不加第三个
DECISION_T = 60                # §3: D1+60 裁决, D1+20 并列
REPORT_T = 20
LADDER = ((0.5, 0.0), (0.3, 0.5), (0.2, 1.0))   # (权重, 低于 D1 收盘几个 N); 50/30/20 锁死
LIMIT_DAYS = 10                # §3b-A: ladder 二三档与 B2 同为 10 交易日
BFLOOR_BUF = 0.5
PROFILE_BARS = 60
HVN_N = 5
DEPLOY_MIN = 0.80              # 判据 3
KISH_MIN = 30                  # 判据 5
BOOT_DRAWS = 10_000
BOOT_SEED = 244_949
CI_Q = (0.05, 0.95)
ARMS = ("A", "B1", "B2", "C")


# ---------- 单 episode ----------

def _leg_return(j, px, stop, term, closes):
    """一档从入场到终点的收益 (止损则按次日收盘 + 滑点)。"""
    hit = stop_scan(j, term, closes, stop)
    if hit and hit[1] <= term:
        return hit[2] * (1 - SLIP) / px - 1.0, True
    return closes[term] / px - 1.0, False


def _limit_fill(limit, start, until, lows, opens):
    """挂在 limit 的买入限价: 盘中触及即成交; 跳空低开按开盘价 (更优)。"""
    for j in range(start, until + 1):
        if lows[j] <= limit:
            return j, min(limit, opens[j])
    return None


def _hvn_band_below(bars, idx, close, atr14_i):
    """D2 收盘价下方最近的 HVN 带 (与 level_map 同一套画法)。None = 无。"""
    if atr14_i is None or idx < PROFILE_BARS - 1:
        return None
    bkt = technical.auto_bucket(close, atr14_i)
    prof = technical.volume_profile(bars[idx - PROFILE_BARS + 1:idx + 1], bkt)
    if not prof:
        return None
    below = [b for b in level_map.top_hvn(prof, n=HVN_N) if b["hi"] <= close]
    return max(below, key=lambda b: b["hi"]) if below else None


def build_arms(bars, closes, lows, opens, d1, gate, N, atr14, term, k):
    """四条臂 → {arm: [(w, entry_j, entry_px), ...]}; [] = 全程现金。"""
    stop = gate - BFLOOR_BUF * N
    d1c = closes[d1]
    d2 = d1 + 1
    until = min(d1 + LIMIT_DAYS, term)
    out = {"A": [(1.0, d1, d1c)]}

    # B1: ×2 确认的机械代理
    b1 = [(1.0, d2, closes[d2])] if closes[d2] > gate else []
    out["B1"] = b1

    # B2: 确认后挂下方最近 HVN 带中点; 无带 → 回落 B1 (§3b-B)
    fell_back = False
    if not b1:
        out["B2"] = []
    else:
        band = _hvn_band_below(bars, d2, closes[d2], atr14[d2])
        if band is None:
            out["B2"] = list(b1)
            fell_back = True
        else:
            mid = (band["lo"] + band["hi"]) / 2
            if mid <= stop:                     # 带子在 stop 之下 = 不是可用的入场位
                out["B2"] = list(b1)
                fell_back = True
            else:
                f = _limit_fill(mid, d2 + 1, until, lows, opens)
                out["B2"] = [(1.0, f[0], f[1])] if f else []

    # C: ladder 50/30/20, 二三档限价加 gate 地板
    legs = []
    for w, off in LADDER:
        if off == 0.0:
            legs.append((w, d1, d1c))
            continue
        lim = max(d1c - off * N, gate)
        f = _limit_fill(lim, d2, until, lows, opens)
        if f:
            legs.append((w, f[0], f[1]))
    out["C"] = legs
    return out, stop, fell_back


def measure(legs, stop, N, term, closes):
    """一条臂 → (部署率, policy 收益, 混合风险N, 承担风险比例, 是否止损)。"""
    if not legs:
        return 0.0, 0.0, None, 0.0, False
    dep = sum(w for w, _, _ in legs)
    ret = 0.0
    risk = 0.0
    stopped = False
    for w, j, px in legs:
        r, s = _leg_return(j, px, stop, term, closes)
        ret += w * r
        risk += w * (px - stop) / px
        stopped = stopped or s
    blend = sum(w * px for w, _, px in legs) / dep
    return dep, ret, (blend - stop) / N, risk, stopped


def collect(kdir=KDIR, k=1.0, term_len=DECISION_T):
    rows = []
    for path in sorted(kdir.glob("*.jsonl")):
        bars = load(path)
        if len(bars) < 300:
            continue
        closes = [b["close"] for b in bars]
        highs = [b["high"] for b in bars]
        lows = [b["low"] for b in bars]
        opens = [b.get("open") or b["close"] for b in bars]
        ma60 = sma_series(closes, 60)
        a20 = atr_series(highs, lows, closes, 20)
        a14 = atr_series(highs, lows, closes, 14)
        sh = confirmed_swing_highs(highs)
        sl = confirmed_swing_lows(lows)
        for i in episodes_of(closes, ma60):
            end = min(i + WINDOW, len(closes) - 1)
            if end - i < 50:
                continue
            gate = cluster_gate(i, closes[i], sh, sl, a14[i])
            if gate is None:
                continue
            d1 = next((j for j in range(i + 1, end + 1) if closes[j] > gate), None)
            if d1 is None or a20[d1] is None or d1 + 1 > end:
                continue
            N = a20[d1]
            if closes[d1] <= gate + k * N:
                continue
            term = d1 + term_len
            if term > len(closes) - 1:
                continue
            if closes[d1] <= gate - BFLOOR_BUF * N:
                continue
            arms, stop, fb = build_arms(bars, closes, lows, opens, d1, gate, N,
                                        a14, term, k)
            rec = {"ticker": path.stem, "month": bars[d1]["time"][:7],
                   "b2_fellback": fb,
                   "tranche2": len(arms["C"]) >= 2, "tranche3": len(arms["C"]) >= 3}
            for a in ARMS:
                dep, ret, rk, risk, stp = measure(arms[a], stop, N, term, closes)
                rec[a] = {"dep": dep, "ret": ret, "riskN": rk, "risk": risk,
                          "stopped": stp, "R": (ret / risk) if risk > 0 else 0.0}
            rows.append(rec)
    return rows


# ---------- 统计 ----------

def _boot(pairs_by_month, months, idx, use_np):
    """Σnum/Σden 的按月 block bootstrap CI90。pairs_by_month[m] = (num, den)。"""
    nums = [pairs_by_month.get(m, (0.0, 0))[0] for m in months]
    dens = [pairs_by_month.get(m, (0.0, 0))[1] for m in months]
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


def _winsor(vals):
    s = sorted(vals)
    lo, hi = s[int(len(s) * 0.05)], s[int(len(s) * 0.95)]
    return sum(min(max(x, lo), hi) for x in vals) / len(vals)


def analyse(rows, k, term_len=DECISION_T, draws=BOOT_DRAWS, seed=BOOT_SEED):
    n = len(rows)
    if not n:
        return None
    mc = Counter(r["month"] for r in rows)
    kish = round(n **2 / sum(v * v for v in mc.values()), 1)
    months = sorted(mc)
    M = len(months)
    try:
        import numpy as np
        rng = np.random.default_rng(seed)
        idx = rng.integers(0, M, size=(draws, M))
        use_np = True
    except ImportError:
        import random
        rg = random.Random(seed)
        idx = [[rg.randrange(M) for _ in range(M)] for _ in range(min(draws, 2000))]
        use_np = False

    out = {"k": k, "term": term_len, "n": n, "kish": kish, "months": M,
           "b2_fallback_rate": round(sum(r["b2_fellback"] for r in rows) / n, 4),
           "tranche2_fill": round(sum(r["tranche2"] for r in rows) / n, 4),
           "tranche3_fill": round(sum(r["tranche3"] for r in rows) / n, 4)}
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

    # 判据 1: 风险N(C) − 风险N(B1), 配对 (只在两臂都有入场的 episode 上)
    bm = defaultdict(lambda: [0.0, 0])
    for r in rows:
        if r["C"]["riskN"] is None or r["B1"]["riskN"] is None:
            continue
        m = bm[r["month"]]
        m[0] += r["C"]["riskN"] - r["B1"]["riskN"]
        m[1] += 1
    out["riskN_diff_point"] = round(sum(v[0] for v in bm.values())
                                    / max(sum(v[1] for v in bm.values()), 1), 4)
    out["riskN_diff_ci90"] = _boot({m: tuple(v) for m, v in bm.items()}, months, idx, use_np)

    # 判据 2: R(C) − R(B1) 与 R(C) − R(B2), 全 episode 配对 (未入场 R=0, ITT)
    for base in ("B1", "B2"):
        bm2 = defaultdict(lambda: [0.0, 0])
        for r in rows:
            m = bm2[r["month"]]
            m[0] += r["C"]["R"] - r[base]["R"]
            m[1] += 1
        out[f"R_diff_vs_{base}_point"] = round(
            sum(v[0] for v in bm2.values()) / max(sum(v[1] for v in bm2.values()), 1), 4)
        out[f"R_diff_vs_{base}_ci90"] = _boot({m: tuple(v) for m, v in bm2.items()},
                                              months, idx, use_np)

    # 判据 4: C 的绝对收益均值 CI90
    bm3 = defaultdict(lambda: [0.0, 0])
    for r in rows:
        m = bm3[r["month"]]
        m[0] += r["C"]["ret"]
        m[1] += 1
    out["C_ret_ci90"] = _boot({m: tuple(v) for m, v in bm3.items()}, months, idx, use_np)

    c1 = bool(out["riskN_diff_ci90"] and out["riskN_diff_ci90"][1] < 0)
    c2 = bool(out["R_diff_vs_B1_ci90"] and out["R_diff_vs_B1_ci90"][0] > 0
              and out["R_diff_vs_B2_ci90"] and out["R_diff_vs_B2_ci90"][0] > 0)
    c3 = bool(out["C"]["deploy"] >= DEPLOY_MIN)
    c4 = bool(out["C_ret_ci90"] and out["C_ret_ci90"][1] > 0)
    c5 = bool(kish >= KISH_MIN)
    out["crit"] = {"c1_riskN_down": c1, "c2_R_beats_both": c2, "c3_deploy": c3,
                   "c4_abs_exp": c4, "c5_kish": c5,
                   "all_pass": bool(c1 and c2 and c3 and c4 and c5)}
    return out


def final_verdict(results):
    """§5 机械执行: 任一判据不满足 → 不采纳; 两个 k 都过 → 取更严 (更大) 的 k。"""
    ok = [r for r in results if r and r["crit"]["all_pass"]]
    if not ok:
        return {"adopt": False, "k": None,
                "text": "VERDICT: **不采纳 ladder, 现行确认+单点入场保留**(预注册 §5)"}
    pick = max(ok, key=lambda r: r["k"])
    edge = "" if len(ok) == 2 else f"  ⚠️ 只有 k={pick['k']} 过, 采纳范围仅限该门槛以上"
    return {"adopt": True, "k": pick["k"],
            "text": f"VERDICT: **采纳 ladder 入场, 幅度门槛 k = {pick['k']}**"
                    f" (两个 k 都过则取更严者){edge}"}


def render(by_k, by_k20, verdict):
    L = ["=== 修复 gate 幅度达标突破后的入场执行 === gate=cluster / "
         f"stop=gate−0.5N / 限价 {LIMIT_DAYS}d / 裁决终点 D1+{DECISION_T}", ""]
    for res in by_k:
        if not res:
            continue
        L.append(f"--- k = {res['k']} (D1 收 > gate + {res['k']}N) --- "
                 f"n={res['n']}  Kish≈{res['kish']}  "
                 f"B2 回落率 {res['b2_fallback_rate']:.0%}  "
                 f"ladder 二档 {res['tranche2_fill']:.0%} 三档 {res['tranche3_fill']:.0%}")
        L.append(f"{'臂':<26}{'部署率':>8}{'风险N':>8}{'止损率':>8}"
                 f"{'收益均值':>10}{'收益中位':>10}{'R winsor':>10}{'R 中位':>9}")
        names = {"A": "A  D1 收盘全额", "B1": "B1 D2 收盘全额 (现行代理)",
                 "B2": "B2 D2 确认后挂 HVN 带限价", "C": "C  ladder 50/30/20"}
        for a in ARMS:
            d = res[a]
            rk = f"{d['riskN_median']:.2f}N" if d["riskN_median"] else "—"
            L.append(f"{names[a]:<26}{d['deploy']:>8.0%}{rk:>8}{d['stop_rate']:>8.0%}"
                     f"{d['ret_mean'] * 100:>9.1f}%{d['ret_median'] * 100:>9.1f}%"
                     f"{d['R_winsor']:>10.3f}{d['R_median']:>9.3f}")
        c = res["crit"]
        L.append(f"  ①风险N(C−B1) {res['riskN_diff_point']:+.3f}N CI90 "
                 f"{res['riskN_diff_ci90']} → {'✓' if c['c1_riskN_down'] else '✗'}")
        L.append(f"  ②R(C−B1) {res['R_diff_vs_B1_point']:+.3f} CI90 {res['R_diff_vs_B1_ci90']}"
                 f" | R(C−B2) {res['R_diff_vs_B2_point']:+.3f} CI90 "
                 f"{res['R_diff_vs_B2_ci90']} → {'✓' if c['c2_R_beats_both'] else '✗'}")
        L.append(f"  ③部署率 {res['C']['deploy']:.0%} ≥ {DEPLOY_MIN:.0%} "
                 f"{'✓' if c['c3_deploy'] else '✗'}"
                 f"   ④C 绝对收益 CI90 {res['C_ret_ci90']} {'✓' if c['c4_abs_exp'] else '✗'}"
                 f"   ⑤Kish {'✓' if c['c5_kish'] else '✗'}"
                 f"   → {'PASS' if c['all_pass'] else 'FAIL'}")
        L.append("")
    if by_k20:
        L.append(f"D1+{REPORT_T} 并列 (不参与裁决): " + "; ".join(
            f"k={r['k']} 风险N(C) {r['C']['riskN_median']} R(C) {r['C']['R_winsor']:+.3f} "
            f"vs B1 {r['B1']['R_winsor']:+.3f}" for r in by_k20 if r))
        L.append("")
    L.append(verdict["text"])
    return "\n".join(L)


def main_cli():
    ap = argparse.ArgumentParser(description="ladder 入场执行回测")
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
    by_k = [analyse(collect(kdir, k=k, term_len=DECISION_T), k) for k in K_VALUES]
    by_k20 = [analyse(collect(kdir, k=k, term_len=REPORT_T), k, term_len=REPORT_T)
              for k in K_VALUES]
    verdict = final_verdict(by_k)
    text = render(by_k, by_k20, verdict)
    if a.json:
        print(json.dumps({"decision": by_k, "report_T20": by_k20, "verdict": verdict},
                         ensure_ascii=False, indent=1, default=float))
    else:
        print(text)
    if a.report:
        a.report.parent.mkdir(parents=True, exist_ok=True)
        a.report.write_text("# ladder 入场执行回测 (机器输出存档)\n\n```\n" + text + "\n```\n")
    return 0


if __name__ == "__main__":
    sys.exit(main_cli())
