#!/usr/bin/env python3
"""风险板块表 — 按共动性真聚类 (预注册 private preregistration, 已锁定)。

## 问题
已有行业标签未必代表冲击日的真实共动分组，因此直接从冲击日相关性检验聚类。

## 设计 (§2-§3, 锁定)
只在**宏观冲击日**(横截面中位跌幅 ≤ −1N) 上算 Pearson 相关 —— 板块限额要防的是
板块一起跌, 平日相关不是它要回答的问题。层次聚类 (average linkage, d = 1−ρ),
k ∈ {2,3,4,5,6,8,10} **全部报出**。

## 判据 (§4, 锁定)
1 存在 k 使 sep = 组内均相关 − 组间均相关 **≥ 0.15**, 且 bootstrap CI90 下界 >0
  (bootstrap 按**冲击日**整块重抽 —— 日子是独立单元, 不是票对)
2 可用性: 最大组 ≤ 全池 50% (「全市场+零星」在数学上 sep 可能很高但毫无意义)
3 稳定性: 冲击日前后对半, 两半各自聚类的 **ARI ≥ 0.30**
三条全过 → 采纳; 任一不过 → **分板块不成立, 合上这个问题**。

Usage: .venv/moomoo/bin/python3 quant/research/risk_sector.py [--json] [--report PATH]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import argparse  # noqa: E402
import json  # noqa: E402
import statistics as st  # noqa: E402
from collections import defaultdict  # noqa: E402

from quant.core.paths import KLINE_DEEP_DIR as KDIR  # noqa: E402
from quant.research.gate_backtest import load  # noqa: E402
from quant.research.gate_backtest2 import atr_series  # noqa: E402

SHOCK_MEDIAN = -1.0        # 宏观冲击日定义 (与 shock_budget 判据 4 同)
MIN_TICKERS_PER_DAY = 20
MIN_COMMON_DAYS = 15       # 共同冲击日 <15 的票对丢弃
K_VALUES = (2, 3, 4, 5, 6, 8, 10)
SEP_MIN = 0.15             # 判据 1
MAX_GROUP_FRAC = 0.50      # 判据 2
ARI_MIN = 0.30             # 判据 3
BOOT = 2000
SEED = 346_410


def shock_matrix():
    """{ticker: {date: ATR 归一跌幅}} 限定在宏观冲击日上。"""
    per = {}
    for path in sorted(KDIR.glob("*.jsonl")):
        bars = load(path)
        if len(bars) < 300:
            continue
        cl = [b["close"] for b in bars]
        hi = [b["high"] for b in bars]
        lo = [b["low"] for b in bars]
        a = atr_series(hi, lo, cl, 20)
        d = {}
        for t in range(1, len(cl)):
            if a[t - 1]:
                d[str(bars[t]["time"])[:10]] = (cl[t] - cl[t - 1]) / a[t - 1]
        per[path.stem] = d
    byday = defaultdict(dict)
    for tk, d in per.items():
        for dt, v in d.items():
            byday[dt][tk] = v
    shock = sorted(dt for dt, m in byday.items()
                   if len(m) >= MIN_TICKERS_PER_DAY and st.median(m.values()) <= SHOCK_MEDIAN)
    out = {tk: {dt: byday[dt][tk] for dt in shock if tk in byday[dt]} for tk in per}
    return {tk: v for tk, v in out.items() if v}, shock


def corr_matrix(series, days=None):
    """逐对 Pearson。共同日 <MIN_COMMON_DAYS 的对 → None。"""
    tks = sorted(series)
    keep = set(days) if days is not None else None
    C = {}
    for i, a in enumerate(tks):
        for b in tks[i + 1:]:
            common = set(series[a]) & set(series[b])
            if keep is not None:
                common &= keep
            if len(common) < MIN_COMMON_DAYS:
                continue
            cl = sorted(common)
            xa = [series[a][d] for d in cl]
            xb = [series[b][d] for d in cl]
            ma, mb = st.mean(xa), st.mean(xb)
            den = (sum((x - ma) **2 for x in xa) * sum((x - mb) **2 for x in xb)) **0.5
            if den:
                C[(a, b)] = C[(b, a)] = sum((x - ma) * (y - mb)
                                            for x, y in zip(xa, xb)) / den
    return C, tks


def drop_isolated(C, tks):
    """与超过一半的票都配不上对的票整个剔除 (§5)。"""
    keep = [t for t in tks if sum(1 for o in tks if o != t and (t, o) in C) >= len(tks) / 2]
    return keep, [t for t in tks if t not in keep]


def hierarchical(C, tks, k):
    """average linkage, d = 1−ρ。返回 {ticker: cluster_id}。"""
    clusters = [[t] for t in tks]

    def dist(ca, cb):
        vals = [1 - C[(a, b)] for a in ca for b in cb if (a, b) in C]
        return st.mean(vals) if vals else 2.0        # 无可比 → 最远

    while len(clusters) > k:
        best, bi, bj = None, None, None
        for i in range(len(clusters)):
            for j in range(i + 1, len(clusters)):
                d = dist(clusters[i], clusters[j])
                if best is None or d < best:
                    best, bi, bj = d, i, j
        clusters[bi] = clusters[bi] + clusters[bj]
        clusters.pop(bj)
    return {t: i for i, c in enumerate(clusters) for t in c}, clusters


def separation(C, labels, days_subset=None, series=None):
    """组内平均相关 − 组间平均相关。"""
    within, between = [], []
    ks = sorted(labels)
    for i, a in enumerate(ks):
        for b in ks[i + 1:]:
            if (a, b) not in C:
                continue
            (within if labels[a] == labels[b] else between).append(C[(a, b)])
    if not within or not between:
        return None, len(within), len(between)
    return st.mean(within) - st.mean(between), len(within), len(between)


def ari(l1, l2):
    """调整兰德指数。"""
    common = sorted(set(l1) & set(l2))
    if len(common) < 4:
        return None
    from itertools import combinations
    n11 = n00 = n10 = n01 = 0
    for a, b in combinations(common, 2):
        s1 = l1[a] == l1[b]
        s2 = l2[a] == l2[b]
        n11 += s1 and s2
        n00 += (not s1) and (not s2)
        n10 += s1 and not s2
        n01 += (not s1) and s2
    total = n11 + n00 + n10 + n01
    if not total:
        return None
    exp = ((n11 + n10) * (n11 + n01) + (n00 + n01) * (n00 + n10)) / total
    mx = ((n11 + n10) + (n11 + n01)) / 2
    return (n11 + n00 - exp) / (total - exp) if total != exp else None if mx else None


def boot_sep_ci(series, labels, shock_days, draws=BOOT, seed=SEED):
    """sep 的 CI90, 按**冲击日**整块重抽 (§4-1)。"""
    import random
    rng = random.Random(seed)
    vals = []
    n = len(shock_days)
    for _ in range(draws // 20):        # 每次要重算相关矩阵, 降 draws
        pick = [shock_days[rng.randrange(n)] for _ in range(n)]
        C, _ = corr_matrix(series, days=pick)
        s, _w, _b = separation(C, labels)
        if s is not None:
            vals.append(s)
    if not vals:
        return None
    vals.sort()
    return [round(vals[int(len(vals) * 0.05)], 4),
            round(vals[min(len(vals) - 1, int(len(vals) * 0.95))], 4)]


def run():
    series, shock = shock_matrix()
    C, tks = corr_matrix(series)
    keep, dropped = drop_isolated(C, tks)
    half = len(shock) // 2
    C1, _ = corr_matrix(series, days=shock[:half])
    C2, _ = corr_matrix(series, days=shock[half:])
    out = {"n_tickers": len(keep), "n_dropped": len(dropped), "dropped": dropped[:10],
           "n_shock_days": len(shock), "ks": {}}
    for k in K_VALUES:
        if k >= len(keep):
            continue
        labels, clusters = hierarchical(C, keep, k)
        sep, nw, nb = separation(C, labels)
        sizes = sorted((len(c) for c in clusters), reverse=True)
        maxfrac = sizes[0] / len(keep)
        k1 = keep and [t for t in keep if any((t, o) in C1 for o in keep)]
        l1, _ = hierarchical(C1, k1, k) if k1 and k < len(k1) else ({}, [])
        k2 = [t for t in keep if any((t, o) in C2 for o in keep)]
        l2, _ = hierarchical(C2, k2, k) if k2 and k < len(k2) else ({}, [])
        a = ari(l1, l2) if l1 and l2 else None
        c1 = bool(sep is not None and sep >= SEP_MIN)
        c2 = bool(maxfrac <= MAX_GROUP_FRAC)
        c3 = bool(a is not None and a >= ARI_MIN)
        ci = boot_sep_ci(series, labels, shock) if c1 else None
        c1 = bool(c1 and ci and ci[0] > 0)
        out["ks"][k] = {
            "sep": round(sep, 4) if sep is not None else None, "sep_ci90": ci,
            "sizes": sizes, "max_group_frac": round(maxfrac, 3),
            "ari": round(a, 3) if a is not None else None,
            "n_within": nw, "n_between": nb,
            "crit": {"c1_sep": c1, "c2_usable": c2, "c3_stable": c3,
                     "all_pass": bool(c1 and c2 and c3)},
            "clusters": [sorted(c) for c in clusters] if (c1 and c2 and c3) else None,
        }
    winners = [k for k, v in out["ks"].items() if v["crit"]["all_pass"]]
    if winners:
        pick = max(winners, key=lambda k: out["ks"][k]["sep"])
        out["verdict"] = {"adopt": True, "k": pick,
                          "text": f"VERDICT: **采纳 k={pick} 的分组**"
                                  f"(sep {out['ks'][pick]['sep']}) → 写 core/risk_sectors.py"}
    else:
        out["verdict"] = {"adopt": False, "k": None,
                          "text": "VERDICT: **分板块在数据上不成立**—— 无 k 通过三判据。"
                                  "shock_budget 只留总额 15% 是对的, **合上这个问题**。"}
    return out


def render(o):
    L = [f"=== 风险板块表 (按共动性真聚类) === {o['n_tickers']} 票 / "
         f"{o['n_shock_days']} 个宏观冲击日"
         + (f" / 剔除配不上对的 {o['n_dropped']} 只" if o["n_dropped"] else ""),
         "只在冲击日上算相关 —— 板块限额要防的是板块**一起跌**", "",
         f"{'k':>3}{'sep':>9}{'CI90':>20}{'最大组占比':>11}{'ARI':>8}{'组大小':>26}{'判据':>16}"]
    for k, v in o["ks"].items():
        c = v["crit"]
        flags = ("①" + ("✓" if c["c1_sep"] else "✗")
                 + " ②" + ("✓" if c["c2_usable"] else "✗")
                 + " ③" + ("✓" if c["c3_stable"] else "✗"))
        L.append(f"{k:>3}{(v['sep'] if v['sep'] is not None else float('nan')):>9.4f}"
                 f"{(str(v['sep_ci90']) if v['sep_ci90'] else '—'):>20}"
                 f"{v['max_group_frac'] * 100:>10.0f}%"
                 f"{(v['ari'] if v['ari'] is not None else float('nan')):>8.2f}"
                 f"{str(v['sizes'][:6]):>26}{flags:>16}")
    L += ["", f"判据: ①sep ≥{SEP_MIN} 且 CI90 下界 >0  "
              f"②最大组 ≤{MAX_GROUP_FRAC:.0%}  ③ARI ≥{ARI_MIN}", "",
          o["verdict"]["text"]]
    if o["verdict"]["adopt"]:
        for i, c in enumerate(o["ks"][o["verdict"]["k"]]["clusters"] or []):
            L.append(f"  组{i}: {', '.join(c[:14])}{' …' if len(c) > 14 else ''}")
    return "\n".join(L)


def main_cli():
    ap = argparse.ArgumentParser(description="风险板块表聚类")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--report", type=Path, default=None)
    a = ap.parse_args()
    o = run()
    text = render(o)
    print(json.dumps(o, ensure_ascii=False, indent=1, default=float) if a.json else text)
    if a.report:
        a.report.parent.mkdir(parents=True, exist_ok=True)
        a.report.write_text("# 风险板块表聚类 (机器输出存档)\n\n```\n" + text + "\n```\n")
    return 0


if __name__ == "__main__":
    sys.exit(main_cli())
