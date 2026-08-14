#!/usr/bin/env python3
"""冲击预算 sizing 框架 — 主回测 (预注册 private preregistration, 已锁定)。

## 两条 sizing 臂 (同一批入场信号, 只换 sizing)
A 现行: CAP_FRACS 名义 cap (confirmed 5% NAV) + 25% 单票集中度
B 本框架: 两级冲击预算 —— 单板块 ≤10% NAV, 全组合 ≤15% NAV, 同时成立取更紧者
          occupancy_i = 敞口_i × X_i × (ATR20_i / 价格_i)

## X (运行前锁定)
X_i = 标的 i 自身单日跌幅分布 (ATR 归一) 的 **p98**, per-ticker。
⚠️ **滚动窗口**: 只用 t 之前 ROLL_WIN 根 K 估计 (§6 混淆表: 全样本 p98 是前视)。
并列报全样本版, 差异必须写出来。

## 判据 (§5)
H1 控制力  : B 单日损失超预算的频率 ∈ [0.5×, 2×] 目标 (0.34%/日)
H2 选择性  : Spearman ρ(B/A 仓位比, X_i×ATR_i/价_i) ≤ −0.5 且 CI90 排除 0
             |ρ| < 0.3 = 等比缩放 = 无信息含量 → **不采纳**
H3 绩效非劣: (B − A) 的 T+60 组合 expectancy 配对 CI90 下界 > −1pp
H4 熔断    : 触发后清仓 vs 扛住的 T+20 反事实; 差 < −2pp → 熔断线上调或改只减不清
判据 4 板块: 组内相关 − 组间相关 ≥ 0.15 且 CI90 排除 0

Usage: .venv/moomoo/bin/python3 quant/research/shock_budget_backtest.py [--json] [--report PATH]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import argparse  # noqa: E402
import json  # noqa: E402
import random  # noqa: E402
import statistics as st  # noqa: E402
from collections import defaultdict  # noqa: E402

from quant.core.paths import KLINE_DEEP_DIR as KDIR  # noqa: E402
from quant.core.sectors import SEMI_SMH  # noqa: E402
from quant.research.gate_backtest import load, sma_series, episodes_of, confirmed_swing_highs  # noqa: E402
from quant.research.gate_backtest2 import (# noqa: E402
    WINDOW, atr_series, cluster_gate, confirmed_swing_lows)

# ---- 预注册锁定的常量 ----
X_TAIL = 0.02              # p98
ROLL_WIN = 500             # X 的滚动估计窗口 (交易日)
SECTOR_BUDGET = 0.10       # 单板块 ≤10% NAV
TOTAL_BUDGET = 0.15        # 全组合 ≤15% NAV
BREAKER_MULT = 1.5         # 熔断 = 预算 × 1.5
CAP_CONFIRMED = 0.05       # A 臂: confirmed 名义 cap
CONC_CAP = 0.25            # A 臂: 单票集中度
HOLD = 60                  # 持有期 (交易日)
TARGET_BREACH = 0.0034     # H1 目标频率 (探路校准值)
H1_BAND = (0.5, 2.0)
H2_RHO_MAX = -0.5
H2_RHO_NULL = 0.3
H3_FLOOR = -0.01
H4_FLOOR = -0.02
SECTOR_SEP_MIN = 0.15
BASKETS = 200
BASKET_N = 5
SEED = 264_575

# ---- 运行前补记 (, 冒烟后、全量前) ----
# ① 预注册 §2 **没有规定单笔可占预算的份额**, 于是"先到先得"会让第一笔吃光整个板块预算
#    例如 room/(X·vol) 可大于 100% NAV；这不是实现 bug，而是名义约束缺口。
#    两种读法都跑，选择必须由私人预注册提前锁定：
#      B_greedy : 字面实现 (剩余预算全给当前这笔)
#      B_share  : 单笔 ≤ 总预算 / BASKET_N（均分解释）
# ② H2 的 ρ 必须在**同等预算余量**下测: wB 天然含 room 这个路径依赖项, 直接测会把
#    "开仓顺序"的噪音混进"选择性"。改测 wB/room ↔ X·vol —— 这才是 §5-H2 问的那件事
#    用于检验约束是**选择性**收紧，而非等比缩小。
SHARE_CAP = 1.0 / BASKET_N

# ---- §2.9 第二轮修订 (, 运行前锁定) ----
# (1) 取消分板块预算 (判据 4 已否, 只留总额)  (2) 名义上限 40% 并用, 不另设份额规则
# (3) 组合 gross 做成显式对照臂，裁决格由私人协议锁定。
NOTIONAL_CAP = 0.40                       # production contract value
GROSS_ARMS = (None, 2.00, 1.50, 1.10, 1.00)   # ∞ / Reg T / 150% / 110% / 不加杠杆
# §12 简化版: X 用**池级常数**替代 per-ticker。数学上 ≡ 纯波动率目标 + 重标定预算。
SIMPLE_EQUIV_W = 0.10       # 判据①: 逐笔仓位相对差中位 ≤10%
SIMPLE_TOL_BREACH = 0.5     # 判据②: 打穿率不得比 FULL 高出 0.5×目标频率


def build():
    """每票: 日线 + ATR20 + 跌幅序列 + gate 确认日。"""
    out = {}
    for path in sorted(KDIR.glob("*.jsonl")):
        bars = load(path)
        if len(bars) < ROLL_WIN + 300:
            continue
        cl = [b["close"] for b in bars]
        hi = [b["high"] for b in bars]
        lo = [b["low"] for b in bars]
        a20 = atr_series(hi, lo, cl, 20)
        a14 = atr_series(hi, lo, cl, 14)
        ma60 = sma_series(cl, 60)
        drop = [None] * len(cl)
        for t in range(1, len(cl)):
            if a20[t - 1]:
                drop[t] = (cl[t] - cl[t - 1]) / a20[t - 1]
        # 入场信号 = gate 收复 ×2 日确认 (复用 round-2 口径, 本轮不测择时)
        sh, sl = confirmed_swing_highs(hi), confirmed_swing_lows(lo)
        entries = []
        for i in episodes_of(cl, ma60):
            end = min(i + WINDOW, len(cl) - 1)
            if end - i < 50:
                continue
            g = cluster_gate(i, cl[i], sh, sl, a14[i])
            if g is None:
                continue
            c = next((j for j in range(i + 1, end + 1) if cl[j] > g), None)
            if c is None or c + 2 > end or cl[c + 1] <= g:
                continue
            if c + 2 + HOLD > len(cl) - 1:
                continue
            entries.append(c + 2)
        out[path.stem] = {"dates": [str(b["time"])[:10] for b in bars], "close": cl,
                          "atr": a20, "drop": drop, "entries": sorted(set(entries))}
    return out


def x_rolling(drop, t, tail=X_TAIL, win=ROLL_WIN):
    """X_i(t) = 仅用 t 之前 win 根的跌幅分布 p98。数据不足 → None。"""
    seg = [-v for v in drop[max(0, t - win):t] if v is not None and v < 0]
    if len(seg) < 60:
        return None
    seg.sort()
    return seg[min(len(seg) - 1, int(len(seg) * (1 - tail)))]


def x_full(drop, tail=X_TAIL):
    seg = sorted(-v for v in drop if v is not None and v < 0)
    if not seg:
        return None
    return seg[min(len(seg) - 1, int(len(seg) * (1 - tail)))]


def simulate(basket, D, rolling=True, breaker=True, greedy=True,
             gross_cap=None, v2=False, simple_x=None):
    """一个篮子跑一遍。返回逐日组合损益(占 NAV)、入场记录、熔断记录。"""
    dates = sorted({d for tk in basket for d in D[tk]["dates"]})
    di = {tk: {d: i for i, d in enumerate(D[tk]["dates"])} for tk in basket}
    sectors = {tk: ("SEMI" if tk in SEMI_SMH else "OTHER") for tk in basket}
    open_pos = []          # (ticker, entry_idx_local, exit_date_idx, w_A, w_B, occ)
    recs = []
    daily = []
    fired = []
    for gi, d in enumerate(dates):
        # 关闭到期
        open_pos = [p for p in open_pos if p["until"] > gi]
        # 当日组合损益 (B 权重)
        pnl_A = pnl_B = 0.0
        occ_by_sec = defaultdict(float)
        for p in open_pos:
            tk = p["tk"]
            t = di[tk].get(d)
            if t is None or t == 0:
                continue
            r = D[tk]["close"][t] / D[tk]["close"][t - 1] - 1
            pnl_A += p["wA"] * r
            pnl_B += p["wB"] * r
            occ_by_sec[sectors[tk]] += p["occ"]
        if open_pos:
            daily.append({"i": gi, "pnl_A": pnl_A, "pnl_B": pnl_B,
                          "gross": sum(p["wB"] for p in open_pos),
                          "occ_total": sum(occ_by_sec.values()),
                          "occ_max_sector": max(occ_by_sec.values()) if occ_by_sec else 0.0})
            if breaker and (-pnl_B >= TOTAL_BUDGET * BREAKER_MULT
                            or -pnl_B >= SECTOR_BUDGET * BREAKER_MULT
                            and max(occ_by_sec.values() or [0]) > 0):
                fired.append({"i": gi, "loss": -pnl_B,
                              "fwd20": _fwd(basket, D, di, dates, gi, 20, open_pos)})
                open_pos = []          # 清仓
        # 开新仓
        for tk in basket:
            t = di[tk].get(d)
            if t is None or t not in set(D[tk]["entries"]):
                continue
            atr, px = D[tk]["atr"][t], D[tk]["close"][t]
            if not atr or not px:
                continue
            X = x_rolling(D[tk]["drop"], t) if rolling else x_full(D[tk]["drop"])
            if X is None:
                continue
            x_full_val = X
            if simple_x is not None:          # §12 SIMPLE 臂: 池级常数
                X = simple_x
            vol = atr / px
            # A: 名义 cap
            wA = min(CAP_CONFIRMED, CONC_CAP - sum(p["wA"] for p in open_pos if p["tk"] == tk))
            wA = max(wA, 0.0)
            # B: 两级冲击预算反推
            used_sec = sum(p["occ"] for p in open_pos if sectors[p["tk"]] == sectors[tk])
            used_tot = sum(p["occ"] for p in open_pos)
            if v2:
                # §2.9: 只剩总额预算; 名义 40% 并用; gross 上限按臂
                room = max(TOTAL_BUDGET - used_tot, 0.0)
                wB = room / (X * vol) if X * vol > 0 else 0.0
                wB = min(wB, NOTIONAL_CAP)
                if gross_cap is not None:
                    used_gross = sum(p["wB"] for p in open_pos)
                    wB = min(wB, max(gross_cap - used_gross, 0.0))
                room_eff = room
            else:
                room = max(min(SECTOR_BUDGET - used_sec, TOTAL_BUDGET - used_tot), 0.0)
                room_eff = room if greedy else min(room, TOTAL_BUDGET * SHARE_CAP)
                wB = room_eff / (X * vol) if X * vol > 0 else 0.0
            occ = wB * X * vol
            if wA <= 0 and wB <= 0:
                continue
            until = gi
            for k in range(gi + 1, len(dates)):
                if di[tk].get(dates[k]) is not None and di[tk][dates[k]] >= t + HOLD:
                    until = k
                    break
            else:
                until = len(dates) - 1
            open_pos.append({"tk": tk, "wA": wA, "wB": wB, "occ": occ, "until": until})
            fwd = (D[tk]["close"][min(t + HOLD, len(D[tk]["close"]) - 1)] / px - 1)
            recs.append({"tk": tk, "wA": wA, "wB": wB, "X": X, "x_full": x_full_val,
                         "vol": vol,
                         "shock_unit": X * vol, "room": room_eff,
                         # H2: 同等预算余量下的单位敞口 —— 去掉开仓顺序的路径依赖
                         "per_room": (wB / room_eff) if room_eff > 1e-9 else None,
                         "ratio": (wB / wA) if wA > 0 else None, "fwd": fwd})
    return daily, recs, fired


def _fwd(basket, D, di, dates, gi, k, open_pos):
    """熔断当日之后 k 日, 若**不清仓**会怎样 (H4 反事实)。"""
    if gi + k >= len(dates):
        return None
    d0, d1 = dates[gi], dates[gi + k]
    tot = 0.0
    for p in open_pos:
        t0, t1 = di[p["tk"]].get(d0), di[p["tk"]].get(d1)
        if t0 is None or t1 is None:
            continue
        tot += p["wB"] * (D[p["tk"]]["close"][t1] / D[p["tk"]]["close"][t0] - 1)
    return tot


def spearman(xs, ys):
    def rank(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for t in range(i, j + 1):
                r[order[t]] = avg
            i = j + 1
        return r
    if len(xs) < 3:
        return None
    rx, ry = rank(xs), rank(ys)
    mx, my = st.mean(rx), st.mean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = (sum((a - mx) **2 for a in rx) * sum((b - my) **2 for b in ry)) **0.5
    return num / den if den else None


def sector_separation(D):
    """判据 4: 组内相关 − 组间相关 (宏观冲击日的 ATR 归一跌幅)。"""
    byday = defaultdict(dict)
    for tk, v in D.items():
        for i, d in enumerate(v["dates"]):
            if v["drop"][i] is not None:
                byday[d][tk] = v["drop"][i]
    shock = {d for d, m in byday.items() if len(m) >= 20 and st.median(m.values()) <= -1.0}
    series = defaultdict(dict)
    for d in shock:
        for tk, x in byday[d].items():
            series[tk][d] = x
    tks = [t for t in series if len(series[t]) >= 20]

    def corr(a, b):
        common = sorted(set(series[a]) & set(series[b]))
        if len(common) < 15:
            return None
        xa = [series[a][d] for d in common]
        xb = [series[b][d] for d in common]
        ma, mb = st.mean(xa), st.mean(xb)
        den = (sum((x - ma) **2 for x in xa) * sum((x - mb) **2 for x in xb)) **0.5
        return (sum((x - ma) * (y - mb) for x, y in zip(xa, xb)) / den) if den else None

    within, between = [], []
    rng = random.Random(SEED)
    pairs = [(a, b) for i, a in enumerate(tks) for b in tks[i + 1:]]
    rng.shuffle(pairs)
    for a, b in pairs[:4000]:
        c = corr(a, b)
        if c is None:
            continue
        (within if (a in SEMI_SMH) == (b in SEMI_SMH) and a in SEMI_SMH else
         between if (a in SEMI_SMH) != (b in SEMI_SMH) else within).append(c)
    if not within or not between:
        return None
    return {"within": round(st.mean(within), 4), "between": round(st.mean(between), 4),
            "sep": round(st.mean(within) - st.mean(between), 4),
            "n_within": len(within), "n_between": len(between)}


def run(D, rolling=True, baskets=BASKETS, greedy=True,
        gross_cap=None, v2=False, simple_x=None):
    rng = random.Random(SEED)
    semis = sorted(t for t in D if t in SEMI_SMH)
    allt = sorted(D)
    out = {}
    for name, pool in (("半导体篮 (同板块, 与本书同构)", semis), ("分散篮", allt)):
        breach = []
        rhos = []
        exp_A, exp_B = [], []
        fires = []
        maxw, maxexp, maxgross = [], [], []
        for _ in range(baskets):
            if len(pool) < BASKET_N:
                continue
            basket = rng.sample(pool, BASKET_N)
            daily, recs, fired = simulate(basket, D, rolling=rolling,
                                          breaker=True, greedy=greedy,
                                          gross_cap=gross_cap, v2=v2,
                                          simple_x=simple_x)
            if len(daily) < 200 or len(recs) < 5:
                continue
            over = sum(1 for r in daily if -r["pnl_B"] > TOTAL_BUDGET)
            breach.append(over / len(daily))
            rr = [r for r in recs if r["per_room"] is not None]
            if len(rr) >= 5:
                rho = spearman([r["shock_unit"] for r in rr], [r["per_room"] for r in rr])
                if rho is not None:
                    rhos.append(rho)
            maxw.append(max(r["wB"] for r in recs))
            maxexp.append(max(d["occ_total"] for d in daily) if daily else 0.0)
            maxgross.append(max(d.get("gross", 0.0) for d in daily) if daily else 0.0)
            exp_A.append(sum(r["wA"] * r["fwd"] for r in recs) / len(recs))
            exp_B.append(sum(r["wB"] * r["fwd"] for r in recs) / len(recs))
            fires += [f["fwd20"] for f in fired if f["fwd20"] is not None]
        if not breach:
            continue
        d = [b - a for a, b in zip(exp_A, exp_B)]
        s = sorted(rhos)
        out[name] = {
            "n_baskets": len(breach),
            "breach_rate": round(st.mean(breach), 5),
            "breach_vs_target": round(st.mean(breach) / TARGET_BREACH, 2),
            "rho_median": round(st.median(rhos), 3) if rhos else None,
            "rho_ci90": ([round(s[int(len(s) * 0.05)], 3),
                          round(s[min(len(s) - 1, int(len(s) * 0.95))], 3)] if len(s) >= 20 else None),
            "exp_A": round(st.mean(exp_A), 5), "exp_B": round(st.mean(exp_B), 5),
            "exp_diff": round(st.mean(d), 5),
            "exp_diff_ci90": _ci(d),
            "max_single_wB": round(st.median(maxw), 3) if maxw else None,
            "max_occ_total": round(st.median(maxexp), 4) if maxexp else None,
            "max_gross": round(st.median(maxgross), 3) if maxgross else None,
            "breaker_fires": len(fires),
            "breaker_fwd20_median": round(st.median(fires), 4) if fires else None,
        }
    return out


def _ci(v, q=(0.05, 0.95), draws=2000, seed=SEED):
    if len(v) < 10:
        return None
    rng = random.Random(seed)
    m = [st.mean(rng.choices(v, k=len(v))) for _ in range(draws)]
    m.sort()
    return [round(m[int(len(m) * q[0])], 5), round(m[min(len(m) - 1, int(len(m) * q[1]))], 5)]


def verdict(res, sep):
    out = {}
    for name, r in res.items():
        h1 = bool(H1_BAND[0] <= r["breach_vs_target"] <= H1_BAND[1])
        rho, ci = r["rho_median"], r["rho_ci90"]
        h2 = bool(rho is not None and rho <= H2_RHO_MAX and ci and ci[1] < 0)
        h2_null = bool(rho is not None and abs(rho) < H2_RHO_NULL)
        h3 = bool(r["exp_diff_ci90"] and r["exp_diff_ci90"][0] > H3_FLOOR)
        out[name] = {"H1": h1, "H2": h2, "H2_is_uniform_scaling": h2_null, "H3": h3,
                     "all_pass": bool(h1 and h2 and h3)}
    out["_sector"] = {"pass": bool(sep and sep["sep"] >= SECTOR_SEP_MIN), "detail": sep}
    return out


def render_simple(simp, px):
    L = [f"=== §12 简化版对照 === X 用池级常数 {px:.2f}N 替代 per-ticker",
         "  (数学事实: X 恒定 ⟺ 纯波动率目标 + 重标定预算 —— 见 §12.1)",
         f"{'gross 臂':<12}{'配对数':>7}{'仓位相对差中位':>15}{'p90':>8}"
         f"{'打穿 FULL':>11}{'打穿 SIMPLE':>13}{'Δexp':>9}{'CI90':>20}{'判据':>16}"]
    for tag, r in simp.items():
        c = r["crit"]
        flags = ("①" + ("✓" if c["c1_weights"] else "✗")
                 + " ②" + ("✓" if c["c2_breach"] else "✗")
                 + " ③" + ("✓" if c["c3_equivalent"] else "✗"))
        L.append(f"{tag:<12}{r['n_pairs']:>7}{r['w_rel_diff_median'] * 100:>14.1f}%"
                 f"{r['w_rel_diff_p90'] * 100:>7.1f}%"
                 f"{r['breach_full'] * 100:>10.3f}%{r['breach_simple'] * 100:>12.3f}%"
                 f"{r['exp_diff'] * 100:>8.2f}%"
                 f"{(str(r['exp_diff_ci90']) if r['exp_diff_ci90'] else '—'):>20}{flags:>16}")
    ok = [t for t, r in simp.items() if r["crit"]["adopt_simple"]]
    L.append("")
    L.append(f"§12 裁决: **采纳 SIMPLE, 删掉 per-ticker X**({len(ok)}/{len(simp)} 个 gross 臂全过)"
             if len(ok) == len(simp) and simp else
             f"§12 裁决: **保留 FULL**—— 只有 {len(ok)}/{len(simp)} 个 gross 臂通过三判据")
    return "\n".join(L)


def render_v2(arms, sep, v):
    L = ["=== 冲击预算 sizing 回测 (第二轮, §2.9 修订) ===",
         f"总预算 {TOTAL_BUDGET:.0%} NAV (分板块已取消) / 名义上限 {NOTIONAL_CAP:.0%} / "
         f"X = 自身跌幅 p98 滚动 {ROLL_WIN} 根", ""]
    for tag, res in arms.items():
        L.append(f"--- {tag} ---")
        L.append(f"{'篮子':<24}{'n':>4}{'打穿':>8}{'/目标':>7}{'单笔最大':>9}{'组合gross':>10}"
                 f"{'ρ':>7}{'expA':>8}{'expB':>8}{'Δexp':>8}")
        for name, r in res.items():
            L.append(f"{name:<24}{r['n_baskets']:>4}{r['breach_rate'] * 100:>7.3f}%"
                     f"{r['breach_vs_target']:>7.2f}x"
                     f"{(r['max_single_wB'] or 0) * 100:>8.0f}%"
                     f"{(r['max_gross'] or 0) * 100:>9.0f}%"
                     f"{(r['rho_median'] if r['rho_median'] is not None else float('nan')):>7.2f}"
                     f"{r['exp_A'] * 100:>7.2f}%{r['exp_B'] * 100:>7.2f}%{r['exp_diff'] * 100:>7.2f}%")
        L.append("")
    L.append("判据（私人协议裁决口径 = gross ≤110%）：")
    for name, f in v.items():
        if name.startswith("_"):
            continue
        L.append(f"  {name:<24} H1 {'✓' if f['H1'] else '✗'}  H2 {'✓' if f['H2'] else '✗'}"
                 f"{'  ⚠️等比缩放' if f['H2_is_uniform_scaling'] else ''}  "
                 f"H3 {'✓' if f['H3'] else '✗'}  → {'PASS' if f['all_pass'] else 'FAIL'}")
    s_ = v["_sector"]["detail"]
    if s_:
        L.append(f"\n判据4 板块表 (已按 §2.9 取消分板块, 此处仅存档): "
                 f"组内 {s_['within']} − 组间 {s_['between']} = {s_['sep']}")
    fires = [(n, r["breaker_fires"], r["breaker_fwd20_median"])
             for n, r in arms["gross ≤110%"].items()]
    L.append("\nH4 熔断反事实 (触发后**不清仓**的 T+20):")
    for n, cnt, med in fires:
        L.append(f"  {n:<24} 触发 {cnt} 次   不清仓 T+20 中位 "
                 f"{(f'{med * 100:+.2f}%' if med is not None else '—')}")
    return "\n".join(L)


def render(greedy, share, full, sep, v):
    L = ["=== 冲击预算 sizing 回测 === 板块预算 10% / 总预算 15% NAV / X = 自身跌幅 p98",
         f"X 用**滚动窗口**{ROLL_WIN} 根 (防前视); 全样本 X 并列作前视对照",
         "⚠️ 预注册 §2 未规定单笔可占预算的份额 → 两种读法并列, 裁决用**均分**读法", ""]
    for tag, res in (("B_greedy: 剩余预算全给当前这笔 (字面实现)", greedy),
                     ("B_share: 单笔 ≤ 总预算/5 (裁决口径)", share),
                     ("B_share + 全样本 X (前视对照)", full)):
        L.append(f"--- {tag} ---")
        L.append(f"{'篮子':<24}{'n':>4}{'打穿':>8}{'/目标':>7}{'单笔最大':>9}{'总敞口':>8}"
                 f"{'ρ':>8}{'expA':>8}{'expB':>8}{'Δexp':>8}")
        for name, r in res.items():
            L.append(f"{name:<24}{r['n_baskets']:>4}{r['breach_rate'] * 100:>7.3f}%"
                     f"{r['breach_vs_target']:>7.2f}x"
                     f"{(r['max_single_wB'] or 0) * 100:>8.0f}%{(r['max_occ_total'] or 0) * 100:>7.1f}%"
                     f"{(r['rho_median'] if r['rho_median'] is not None else float('nan')):>8.2f}"
                     f"{r['exp_A'] * 100:>7.2f}%{r['exp_B'] * 100:>7.2f}%{r['exp_diff'] * 100:>7.2f}%")
        L.append("")
    L.append("判据 (滚动 X):")
    for name, f in v.items():
        if name.startswith("_"):
            continue
        L.append(f"  {name:<26} H1控制力 {'✓' if f['H1'] else '✗'}  "
                 f"H2选择性 {'✓' if f['H2'] else '✗'}"
                 f"{'  ⚠️等比缩放=无信息含量' if f['H2_is_uniform_scaling'] else ''}  "
                 f"H3绩效非劣 {'✓' if f['H3'] else '✗'}  → "
                 f"{'PASS' if f['all_pass'] else 'FAIL'}")
    s = v["_sector"]["detail"]
    if s:
        L.append(f"\n判据4 板块表: 组内相关 {s['within']} − 组间 {s['between']} = "
                 f"**{s['sep']}**(门槛 {SECTOR_SEP_MIN}) → "
                 f"{'✓ 分板块有实质' if v['_sector']['pass'] else '✗ → 退回只用总额 15% 一条'}")
    fires = [(n, r["breaker_fires"], r["breaker_fwd20_median"]) for n, r in share.items()]
    L.append("\nH4 熔断反事实 (触发后**不清仓**的 T+20 收益):")
    for n, cnt, med in fires:
        L.append(f"  {n:<26} 触发 {cnt} 次   不清仓的 T+20 中位 "
                 f"{(f'{med * 100:+.2f}%' if med is not None else '—')}"
                 f"   {'→ 熔断在丢钱, 考虑上调或改只减不清' if (med is not None and med > -H4_FLOOR) else ''}")
    return "\n".join(L)


def pooled_x(D, rolling=True):
    """池级常数 X = 各票滚动 p98 的中位 (§12 SIMPLE 臂)。"""
    vals = []
    for tk, v in D.items():
        n = len(v["drop"])
        for t in range(ROLL_WIN, n, 250):          # 每年取一个采样点
            x = x_rolling(v["drop"], t) if rolling else x_full(v["drop"])
            if x:
                vals.append(x)
    return st.median(vals) if vals else X_TAIL


def compare_simple(D, baskets, gross_cap, px):
    """§12: FULL vs SIMPLE 的配对比较 (同 basket 同种子)。"""
    rng_full = random.Random(SEED)
    rng_simp = random.Random(SEED)
    semis = sorted(t for t in D if t in SEMI_SMH)
    dw, dexp, br_f, br_s = [], [], [], []
    for _ in range(baskets):
        if len(semis) < BASKET_N:
            break
        bf = rng_full.sample(semis, BASKET_N)
        bs = rng_simp.sample(semis, BASKET_N)
        assert bf == bs                                  # 同种子 → 同篮子, 真配对
        df, rf, _ = simulate(bf, D, v2=True, gross_cap=gross_cap)
        ds, rs, _ = simulate(bs, D, v2=True, gross_cap=gross_cap, simple_x=px)
        if len(rf) < 5 or len(rf) != len(rs):
            continue
        for a, b in zip(rf, rs):
            if a["wB"] > 1e-9:
                dw.append(abs(b["wB"] - a["wB"]) / a["wB"])
        ef = sum(r["wB"] * r["fwd"] for r in rf) / len(rf)
        es = sum(r["wB"] * r["fwd"] for r in rs) / len(rs)
        dexp.append(es - ef)
        if df:
            br_f.append(sum(1 for r in df if -r["pnl_B"] > TOTAL_BUDGET) / len(df))
        if ds:
            br_s.append(sum(1 for r in ds if -r["pnl_B"] > TOTAL_BUDGET) / len(ds))
    if not dw:
        return None
    ci = _ci(dexp)
    c1 = bool(st.median(dw) <= SIMPLE_EQUIV_W)
    c2 = bool(st.mean(br_s) <= st.mean(br_f) + SIMPLE_TOL_BREACH * TARGET_BREACH)
    c3 = bool(ci and ci[0] <= 0 <= ci[1])
    return {"pooled_x": round(px, 3), "n_pairs": len(dexp),
            "w_rel_diff_median": round(st.median(dw), 4),
            "w_rel_diff_p90": round(sorted(dw)[int(len(dw) * 0.9)], 4),
            "breach_full": round(st.mean(br_f), 5), "breach_simple": round(st.mean(br_s), 5),
            "exp_diff": round(st.mean(dexp), 5), "exp_diff_ci90": ci,
            "crit": {"c1_weights": c1, "c2_breach": c2, "c3_equivalent": c3,
                     "adopt_simple": bool(c1 and c2 and c3)}}


def main_cli():
    ap = argparse.ArgumentParser(description="冲击预算 sizing 回测")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--report", type=Path, default=None)
    ap.add_argument("--baskets", type=int, default=BASKETS)
    a = ap.parse_args()
    D = build()
    arms = {}
    for g in GROSS_ARMS:
        tag = "gross ∞ (不设)" if g is None else f"gross ≤{g:.0%}"
        arms[tag] = run(D, rolling=True, baskets=a.baskets, v2=True, gross_cap=g)
    sep = sector_separation(D)
    v = verdict(arms["gross ≤110%"], sep)      # 私人协议锁定的裁决口径
    px = pooled_x(D)
    simp = {}
    for g in GROSS_ARMS:
        tag = "gross ∞" if g is None else f"gross ≤{g:.0%}"
        r = compare_simple(D, a.baskets, g, px)
        if r:
            simp[tag] = r
    text = render_v2(arms, sep, v) + "\n\n" + render_simple(simp, px)
    if a.json:
        print(json.dumps({"arms": arms, "sector": sep, "verdict": v,
                          "simple": simp}, ensure_ascii=False, indent=1, default=float))
    else:
        print(text)
    if a.report:
        a.report.parent.mkdir(parents=True, exist_ok=True)
        a.report.write_text("# 冲击预算 sizing 回测 (机器输出存档)\n\n```\n" + text + "\n```\n")
    return 0


if __name__ == "__main__":
    sys.exit(main_cli())
