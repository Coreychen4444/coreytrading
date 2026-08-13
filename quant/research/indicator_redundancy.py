#!/usr/bin/env python3
"""指标冗余体检 —— 「指标要结合使用」的前提问题：**它们彼此独立吗?**

设计稿: quant/research/price_action_framework_design.md §13

为什么这个先于「怎么组合」:
    Zakamulin (2017) 证明各类均线/动量指标都能写成**过去收益的线性组合**, 差别只在
    权重函数的形状; Neely/Rapach/Tu/Zhou (2014, Management Science) 对 14 个技术指标
    做 PCA, **K=1 被 adj-R² 选中**, 且 14 个指标在第一主成分上的载荷"几乎均匀" ——
    也就是说这一族指标本质上是**一个共识因子**。
    ⇒ 「3 个信号共振」如果是同一个事实的三份复印件, 它不是 3 份证据。
    这个可以直接在我们自己的指标集上测, **不需要看任何收益**。

两部分:
  A. 连续特征的**有效维数**: 12 个常用读数 → 逐票标准化 → PCA
     → 解释方差 + participation ratio (有效维数 = (Σλ)²/Σλ²)
  B. `technical.detect_bottom_signals` 的**共振投票**: 逐日调**真函数**快照
     (不复刻实现), 统计每个信号的基础发生率、两两 φ 相关、二值矩阵的有效票数,
     以及 score=A 时最常见的信号组合。

Usage:
    .venv/moomoo/bin/python3 quant/research/indicator_redundancy.py [--limit 40] [--json]
    .venv/moomoo/bin/python3 quant/research/indicator_redundancy.py --live      # 只跑活跃卡
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import argparse  # noqa: E402
import json  # noqa: E402
from collections import Counter  # noqa: E402

import numpy as np  # noqa: E402

from quant.core import technical as ta  # noqa: E402
from quant.core.paths import KLINE_DEEP_DIR  # noqa: E402

WINDOW = 320          # 每次快照喂给 detect_bottom_signals 的 bar 数 (够 MA250)
STEP = 5              # 每 5 个交易日取一次快照 (相邻日高度重叠, 全取只是灌水)
SPAN = 1000           # 每票回看多少根 bar

# detect_bottom_signals 的信号文本 → 归一化类别 (前缀匹配, 与 technical.py 逐条对齐)
SIGNAL_MAP = [
    ("缩量下跌后放量止跌", "vol_shrink_then_stop"),
    ("缩量下跌", "vol_shrink"),
    ("RSI 底背离", "rsi_div"),
    ("MACD 柱缩短", "macd_hist_shrink"),
    ("MACD 底背离", "macd_div"),
    ("触及布林带下轨", "bb_lower_touch"),
    ("布林带下轨反包", "bb_lower_reclaim"),
    ("严重偏离60日均线", "ma60_extreme"),
    ("回踩60日决策线附近", "near_ma60"),
    ("K线形态", "kline_pattern"),
    ("长下影线", "long_lower_shadow"),
]


def load_bars(t: str) -> list[dict]:
    p = KLINE_DEEP_DIR / f"{t}.jsonl"
    if not p.exists():
        return []
    out = []
    for line in p.read_text().splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    out.sort(key=lambda r: str(r.get("time", "")))
    return out


def features(bars: list[dict]) -> tuple[list[str], np.ndarray]:
    """A 部分: 12 个连续读数, 逐日一行。全部用 core.technical 的实现。"""
    closes = [float(b["close"]) for b in bars]
    highs = [float(b["high"]) for b in bars]
    lows = [float(b["low"]) for b in bars]
    vols = [int(b.get("volume") or 0) for b in bars]
    rsi = ta.rsi(closes, 14)
    macd = ta.macd(closes)
    boll = ta.bollinger(closes)
    ma20, ma60 = ta.sma(closes, 20), ta.sma(closes, 60)
    atr = ta.atr(bars, 20)
    vr = ta.volume_ratio(vols, 20)
    names = ["rsi14", "macd_hist_n", "boll_pctB", "ma20_dev", "ma60_dev",
             "ma20_over_ma60", "log_volratio", "atr_pct", "pos_20d_range",
             "close_pos", "mom20", "mom60"]
    rows, idx = [], []
    for i in range(len(bars)):
        a = atr[i]
        if None in (rsi[i], macd["histogram"][i], boll["upper"][i], ma20[i], ma60[i], vr[i]) or not a:
            continue
        if i < 60:
            continue
        hi20, lo20 = max(highs[i - 19:i + 1]), min(lows[i - 19:i + 1])
        rng = highs[i] - lows[i]
        rows.append([
            rsi[i] - 50.0,
            macd["histogram"][i] / a,
            (closes[i] - boll["lower"][i]) / max(boll["upper"][i] - boll["lower"][i], 1e-9) - 0.5,
            closes[i] / ma20[i] - 1.0,
            closes[i] / ma60[i] - 1.0,
            ma20[i] / ma60[i] - 1.0,
            float(np.log(max(vr[i], 1e-3))),
            a / closes[i],
            (closes[i] - lo20) / max(hi20 - lo20, 1e-9) - 0.5,
            ((closes[i] - lows[i]) / rng - 0.5) if rng > 0 else 0.0,
            closes[i] / closes[i - 20] - 1.0,
            closes[i] / closes[i - 60] - 1.0,
        ])
        idx.append(i)
    return names, np.array(rows) if rows else np.zeros((0, len(names)))


def participation_ratio(eigvals: np.ndarray) -> float:
    """有效维数 = (Σλ)² / Σλ² —— 1 = 完全共线, N = 完全独立。"""
    s = float(eigvals.sum())
    return s * s / float((eigvals **2).sum()) if s > 0 else 0.0


def part_a(universe: list[str]) -> dict:
    mats = []
    names: list[str] = []
    for t in universe:
        bars = load_bars(t)[-SPAN:]
        if len(bars) < 200:
            continue
        names, m = features(bars)
        if len(m) < 100:
            continue
        m = (m - m.mean(axis=0)) / (m.std(axis=0) + 1e-12)   # 逐票标准化后再池化
        mats.append(m)
    if not mats:
        return {}
    X = np.vstack(mats)
    C = np.corrcoef(X, rowvar=False)
    w = np.linalg.eigvalsh(C)[::-1]
    ev = w / w.sum()
    pairs = [(names[i], names[j], round(float(C[i, j]), 2))
             for i in range(len(names)) for j in range(i + 1, len(names))]
    pairs.sort(key=lambda p: -abs(p[2]))
    return {
        "n_rows": int(X.shape[0]), "n_features": len(names),
        "explained_pc1": round(float(ev[0]), 3),
        "explained_pc1_3": round(float(ev[:3].sum()), 3),
        "pcs_for_80pct": int(np.searchsorted(np.cumsum(ev), 0.80) + 1),
        "effective_dim": round(participation_ratio(w), 2),
        "top_pairs": pairs[:8],
    }


def part_b(universe: list[str]) -> dict:
    keys = [k for _, k in SIGNAL_MAP]
    rows: list[list[int]] = []
    scores: Counter = Counter()
    combos: Counter = Counter()
    for t in universe:
        bars = load_bars(t)
        if len(bars) < WINDOW + 50:
            continue
        for i in range(max(WINDOW, len(bars) - SPAN), len(bars), STEP):
            res = ta.detect_bottom_signals(bars[i - WINDOW:i])
            if res.get("error"):
                continue
            hits = set()
            for s in res.get("signals", []):
                for pref, key in SIGNAL_MAP:
                    if s.startswith(pref):
                        hits.add(key)
                        break
            rows.append([1 if k in hits else 0 for k in keys])
            scores[res.get("score", "?")] += 1
            if res.get("score") == "A":
                combos[" + ".join(sorted(hits))] += 1
    if not rows:
        return {}
    M = np.array(rows, dtype=float)
    base = {k: round(float(M[:, i].mean()), 3) for i, k in enumerate(keys)}
    keep = [i for i in range(len(keys)) if 0.01 < M[:, i].mean() < 0.99]
    C = np.corrcoef(M[:, keep], rowvar=False)          # 二值 → Pearson = φ 系数
    w = np.linalg.eigvalsh(C)[::-1]
    pairs = [(keys[keep[i]], keys[keep[j]], round(float(C[i, j]), 2))
             for i in range(len(keep)) for j in range(i + 1, len(keep))]
    pairs.sort(key=lambda p: -abs(p[2]))
    # φ 在稀疏二值上被基础率压住, 会**高估独立性**—— 同时报提升度 lift = P(B|A)/P(B)
    lifts = []
    for ii in range(len(keep)):
        for jj in range(ii + 1, len(keep)):
            a_col, b_col = M[:, keep[ii]], M[:, keep[jj]]
            co = float((a_col * b_col).sum())
            if co < 10:
                continue
            pa, pb = a_col.mean(), b_col.mean()
            lift = (co / len(M)) / (pa * pb) if pa * pb > 0 else 0.0
            lifts.append((keys[keep[ii]], keys[keep[jj]], round(lift, 2), int(co)))
    lifts.sort(key=lambda x: -x[2])
    return {
        "snapshots": len(rows),
        "score_dist": {k: f"{v} ({100*v/len(rows):.1f}%)" for k, v in scores.most_common()},
        "base_rates": base,
        "effective_votes": round(participation_ratio(w), 2),
        "n_signals_considered": len(keep),
        "top_phi_pairs": pairs[:8],
        "top_lift_pairs": lifts[:8],
        "top_A_combos": combos.most_common(6),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--tickers", nargs="*")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    universe = a.tickers or [p.stem for p in sorted(KLINE_DEEP_DIR.glob("*.jsonl"))
                             if p.stem not in {"QQQ", "SPY", "SMH"}][:a.limit]
    out = {"universe": len(universe), "A_continuous": part_a(universe), "B_votes": part_b(universe)}
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return
    A, B = out["A_continuous"], out["B_votes"]
    print(f"池 {out['universe']} 票")
    print(f"\n[A] 连续指标 {A['n_features']} 个 / {A['n_rows']} 行")
    print(f"    PC1 解释 {A['explained_pc1']*100:.1f}% | PC1-3 {A['explained_pc1_3']*100:.1f}% | "
          f"到 80% 需 {A['pcs_for_80pct']} 个主成分 | **有效维数 {A['effective_dim']}**")
    print("    最相关的对:", "; ".join(f"{x}~{y} {c}" for x, y, c in A["top_pairs"][:5]))
    print(f"\n[B] detect_bottom_signals 快照 {B['snapshots']} 个")
    print(f"    评分分布 {B['score_dist']}")
    print(f"    **有效票数 {B['effective_votes']} / {B['n_signals_considered']} 个信号**")
    print("    φ 最高的信号对:", "; ".join(f"{x}~{y} {c}" for x, y, c in B["top_phi_pairs"][:4]))
    print("    提升度最高 (共现≥10):", "; ".join(f"{x}~{y} ×{c}(n={n})" for x, y, c, n in B["top_lift_pairs"][:5]))
    print("    score=A 时最常见的组合:")
    for combo, n in B["top_A_combos"]:
        print(f"      {n:>4}×  {combo}")


if __name__ == "__main__":
    main()
