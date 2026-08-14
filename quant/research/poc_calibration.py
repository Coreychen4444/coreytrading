#!/usr/bin/env python3
"""POC 漂移阈值校准 (预注册 private preregistration, 已锁定)。

## 主问题
POC 迁移到多少个 N 时, 机械重画会画出**实质不同**的结构? 该阈值能否把"该重画"
与"不用重画"分开?

## 对照问题 (更重要, §6)
POC 迁移能不能打赢「距锚定日过了几天」这个朴素基线? —— 整个 drift 装置的立论就是
「时间是上界, 结构漂移才是判据」, 而这句话**从未被检验过**。

## 两臂全机械, 不用历史卡 (§2)
历史卡是用**旧的错误 profile**画的, 拿它当基线会把"卡从第一天就画错了"混进
"卡漂移了"里。锚定日画一版、anchor+k 日画一版, 同一套 volume_profile + auto_bucket。

## 度量 (§3)
`poc_shift_n = |POC_D − POC_anchor| / ATR20_D`, 沿用 `core/drift.py` 现行口径:
两端**等长**窗口、锚后不足 `POC_MIN_SESSIONS=10` 根不算、**bucket 取 D 日的**
(drift.py 对两端用同一个 bucket)。预注册的额外臂: `POC_WINDOW ∈ {20, 60}`,
运行前不预判哪个好 (卡片结构是 60 根画的, 度量却用 20 根 —— 这本身可能就是"太吵"的来源)。

## 标签 (§4)
label = 1 ⇔ 在 D 日重画会画出实质不同的结构:
取「价格下方最近的 HVN 带」与「价格上方最近的 HVN 带」(top_hvn(n=5) 产物),
任一带的边缘移动 ≥ 1 个 bucket, 或该带 identity 消失/新生 → 1。
zone 边界就锚在这些带上; 带没动 ⇒ 重画画出同样的线 ⇒ 那次重画本来就没价值。

## 判据 (§5, 运行前锁定)
1 AUC ≥ 0.65
2 打赢时间基线: AUC(POC) − AUC(距锚天数) ≥ 0.05 且 bootstrap CI90 下界 > 0
3 选定 T 上假阳性率 ≤ 30%
4 T ∈ [0.5N, 3.0N]
全部成立才形成采纳建议；任一不满足 → POC 维保持 advisory-only。
T 的选法: 满足判据 3 的候选里取 Youden's J 最大者; 平手取更大的 T。

Usage: .venv/moomoo/bin/python3 quant/research/poc_calibration.py [--json] [--report PATH]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import argparse  # noqa: E402
import json  # noqa: E402
from collections import defaultdict  # noqa: E402

from quant.core import technical  # noqa: E402
from quant.core.paths import KLINE_DEEP_DIR as KDIR  # noqa: E402
from quant.decision import level_map  # noqa: E402
from quant.research.gate_backtest import load  # noqa: E402
from quant.research.gate_backtest2 import atr_series  # noqa: E402

ANCHOR_STRIDE = 5                       # §2: 每票每 5 个交易日一个 anchor (去自相关)
K_VALUES = (3, 5, 10, 15, 20, 30)       # §2
RIGHT_GUARD = 5                         # §2: anchor+k ≤ 数据末尾 − 5
LABEL_WINDOW = 60                       # §2: profile 用锚定日往前 60 根 (= level_map.profile_days)
# 研究参数在本模块中显式固定，不依赖 production 当前是否仍使用该维度。
POC_MIN_SESSIONS = 10
POC_WINDOWS = (20, 60)                  # §3: 预注册的两个臂
HVN_N = 5
AUC_MIN = 0.65                          # 判据 1
BASELINE_MARGIN = 0.05                  # 判据 2
FPR_MAX = 0.30                          # 判据 3
T_RANGE = (0.5, 3.0)                    # 判据 4
T_GRID = [round(0.1 * i, 1) for i in range(1, 51)]     # 0.1N .. 5.0N
BOOT_DRAWS = 2000
BOOT_SEED = 316_227


# ---------- 画线 ----------

def _bands(bars, idx, close, atr):
    """D 日机械重画的产物: (下方最近 HVN 带, 上方最近 HVN 带, bucket)。"""
    if atr is None or idx < LABEL_WINDOW - 1:
        return None, None, None
    bkt = technical.auto_bucket(close, atr)
    prof = technical.volume_profile(bars[idx - LABEL_WINDOW + 1:idx + 1], bkt)
    if not prof:
        return None, None, bkt
    hvn = level_map.top_hvn(prof, n=HVN_N)
    below = [b for b in hvn if b["hi"] <= close]
    above = [b for b in hvn if b["lo"] >= close]
    lo_band = max(below, key=lambda b: b["hi"]) if below else None
    hi_band = min(above, key=lambda b: b["lo"]) if above else None
    return lo_band, hi_band, bkt


def _band_changed(a, d, bucket):
    """一条带: identity 消失/新生 → True; 边缘移动 ≥ 1 bucket → True。"""
    if (a is None) != (d is None):
        return True          # 消失或新生
    if a is None:
        return False         # 两边都没有 = 没变
    return (abs(d["lo"] - a["lo"]) >= bucket) or (abs(d["hi"] - a["hi"]) >= bucket)


def label_of(bars, ia, id_, closes, atr20):
    """label = 1 ⇔ 在 D 日重画会画出实质不同的结构 (§4)。None = 画不出来。"""
    a_lo, a_hi, _ba = _bands(bars, ia, closes[ia], atr20[ia])
    d_lo, d_hi, bd = _bands(bars, id_, closes[id_], atr20[id_])
    if bd is None or _ba is None:
        return None
    return int(_band_changed(a_lo, d_lo, bd) or _band_changed(a_hi, d_hi, bd))


# ---------- POC 度量 ----------

def _poc_cached(bars, end_idx, window, bucket, cache):
    key = (end_idx, window, bucket)
    if key in cache:
        return cache[key]
    seg = bars[max(0, end_idx - window + 1):end_idx + 1]
    prof = technical.volume_profile(seg, bucket)
    val = (max(prof, key=lambda b: b["turnover"])["lo"] + bucket / 2) if prof else None
    cache[key] = val
    return val


def poc_shift_n(bars, ia, id_, atr20, window, cache):
    """|POC_D − POC_anchor| / ATR20_D, drift.py 口径 (两端等长, bucket 取 D 日)。"""
    atr_d = atr20[id_]
    if not atr_d:
        return None
    if id_ - ia < POC_MIN_SESSIONS:      # drift.py 的第二道守卫
        return None
    bucket = technical.auto_bucket(bars[id_]["close"], atr_d)
    pa = _poc_cached(bars, ia, window, bucket, cache)
    pd = _poc_cached(bars, id_, window, bucket, cache)
    if pa is None or pd is None:
        return None
    return abs(pd - pa) / atr_d


# ---------- 采样 ----------

def collect(kdir=KDIR, limit=None):
    rows = []
    files = sorted(kdir.glob("*.jsonl"))
    for path in (files[:limit] if limit else files):
        bars = load(path)
        n = len(bars)
        if n < 300:
            continue
        closes = [b["close"] for b in bars]
        highs = [b["high"] for b in bars]
        lows = [b["low"] for b in bars]
        atr20 = atr_series(highs, lows, closes, 20)
        cache = {}
        band_cache = {}

        def bands_at(idx):
            if idx not in band_cache:
                band_cache[idx] = _bands(bars, idx, closes[idx], atr20[idx])
            return band_cache[idx]

        for ia in range(LABEL_WINDOW, n, ANCHOR_STRIDE):
            a_lo, a_hi, ba = bands_at(ia)
            if ba is None:
                continue
            for k in K_VALUES:
                id_ = ia + k
                if id_ > n - 1 - RIGHT_GUARD:
                    continue
                d_lo, d_hi, bd = bands_at(id_)
                if bd is None:
                    continue
                lab = int(_band_changed(a_lo, d_lo, bd) or _band_changed(a_hi, d_hi, bd))
                rec = {"ticker": path.stem, "ia": ia, "k": k, "label": lab}
                for w in POC_WINDOWS:
                    rec[f"poc{w}"] = poc_shift_n(bars, ia, id_, atr20, w, cache)
                rows.append(rec)
    return rows


# ---------- AUC ----------

def auc(scores, labels):
    """Mann-Whitney U / (n_pos·n_neg), 含并列的 0.5 处理。None = 某一类为空。"""
    pairs = sorted(zip(scores, labels))
    n = len(pairs)
    npos = sum(labels)
    nneg = n - npos
    if not npos or not nneg:
        return None
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and pairs[j + 1][0] == pairs[i][0]:
            j += 1
        r = (i + j) / 2 + 1
        for t in range(i, j + 1):
            ranks[t] = r
        i = j + 1
    rsum = sum(r for r, (_s, l) in zip(ranks, pairs) if l == 1)
    return (rsum - npos * (npos + 1) / 2) / (npos * nneg)


def _auc_pair(rows, key):
    sub = [(r[key], r["label"]) for r in rows if r.get(key) is not None]
    if not sub:
        return None, 0
    return auc([s for s, _ in sub], [l for _, l in sub]), len(sub)


def _auc_np(scores, labels):
    """numpy 版 AUC (含并列 0.5), 与纯 python `auc` 同一估计量 —— 由测试钉住等价。"""
    import numpy as np
    npos = float(labels.sum())
    nneg = float(labels.size - npos)
    if not npos or not nneg:
        return None
    order = np.argsort(scores, kind="stable")
    s = scores[order]
    l = labels[order]
    n = s.size
    # 平均秩 (处理并列): 每个并列块用块内秩均值
    ranks = np.empty(n, dtype=np.float64)
    start = 0
    boundaries = np.flatnonzero(np.diff(s)) + 1
    for end in np.append(boundaries, n):
        ranks[start:end] = (start + end - 1) / 2.0 + 1.0
        start = end
    rsum = float(ranks[l == 1].sum())
    return (rsum - npos * (npos + 1) / 2) / (npos * nneg)


def bootstrap_delta_auc(rows, key, draws=BOOT_DRAWS, seed=BOOT_SEED):
    """ΔAUC = AUC(POC) − AUC(距锚天数) 的 CI90, **按 ticker 整块重抽**(§6)。

    按 ticker 而不是按行重抽: 同票不同 anchor 高度自相关, 按行抽会把 CI 抽窄。
    """
    usable = [r for r in rows if r.get(key) is not None]
    if not usable:
        return None
    by_t = defaultdict(list)
    for r in usable:
        by_t[r["ticker"]].append(r)
    tickers = sorted(by_t)
    M = len(tickers)
    try:
        import numpy as np
    except ImportError:
        return None
    blocks = {t: (np.asarray([r[key] for r in by_t[t]], dtype=np.float64),
                  np.asarray([r["k"] for r in by_t[t]], dtype=np.float64),
                  np.asarray([r["label"] for r in by_t[t]], dtype=np.int8))
              for t in tickers}
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, M, size=(draws, M))
    vals = []
    for draw in idx:
        picks = [blocks[tickers[int(t)]] for t in draw]
        sc = np.concatenate([p[0] for p in picks])
        kk = np.concatenate([p[1] for p in picks])
        lb = np.concatenate([p[2] for p in picks])
        a1 = _auc_np(sc, lb)
        a0 = _auc_np(kk, lb)
        if a1 is not None and a0 is not None:
            vals.append(a1 - a0)
    if not vals:
        return None
    vals.sort()
    return [round(vals[int(len(vals) * 0.05)], 4),
            round(vals[min(len(vals) - 1, int(len(vals) * 0.95))], 4)]


# ---------- 阈值选择 ----------

def pick_threshold(rows, key):
    """满足 FPR ≤ 30% 的候选里取 Youden's J 最大者; 平手取更大的 T (§5)。"""
    sub = [(r[key], r["label"]) for r in rows if r.get(key) is not None]
    if not sub:
        return None
    pos = [s for s, l in sub if l == 1]
    neg = [s for s, l in sub if l == 0]
    if not pos or not neg:
        return None
    best = None
    table = []
    for T in T_GRID:
        tpr = sum(1 for s in pos if s > T) / len(pos)
        fpr = sum(1 for s in neg if s > T) / len(neg)
        row = {"T": T, "tpr": round(tpr, 4), "fpr": round(fpr, 4),
               "J": round(tpr - fpr, 4), "eligible": fpr <= FPR_MAX}
        table.append(row)
        if not row["eligible"]:
            continue
        if best is None or (row["J"], T) > (best["J"], best["T"]):
            best = row
    return {"best": best, "table": table}


# ---------- 裁决 ----------

def evaluate(rows, key):
    a_poc, n_used = _auc_pair(rows, key)
    usable = [r for r in rows if r.get(key) is not None]
    a_time = auc([r["k"] for r in usable], [r["label"] for r in usable]) if usable else None
    ci = bootstrap_delta_auc(rows, key)
    pick = pick_threshold(rows, key)
    best = (pick or {}).get("best")
    d = (a_poc - a_time) if (a_poc is not None and a_time is not None) else None
    c1 = bool(a_poc is not None and a_poc >= AUC_MIN)
    c2 = bool(d is not None and d >= BASELINE_MARGIN and ci and ci[0] > 0)
    c3 = bool(best and best["fpr"] <= FPR_MAX)
    c4 = bool(best and T_RANGE[0] <= best["T"] <= T_RANGE[1])
    return {"key": key, "n_used": n_used, "n_total": len(rows),
            "auc_poc": round(a_poc, 4) if a_poc is not None else None,
            "auc_time": round(a_time, 4) if a_time is not None else None,
            "delta_auc": round(d, 4) if d is not None else None,
            "delta_auc_ci90": ci, "chosen": best,
            "base_rate": round(sum(r["label"] for r in usable) / len(usable), 4) if usable else None,
            "crit": {"c1_auc": c1, "c2_beats_time": c2, "c3_fpr": c3, "c4_range": c4,
                     "all_pass": bool(c1 and c2 and c3 and c4)}}


def final_verdict(results):
    winners = [r for r in results if r["crit"]["all_pass"]]
    if not winners:
        return {"adopt": False,
                "text": "VERDICT: **不采纳 POC 阈值, ②POC 维保持 advisory-only**(预注册 §5)"}
    pick = max(winners, key=lambda r: r["auc_poc"])
    return {"adopt": True, "key": pick["key"], "T": pick["chosen"]["T"],
            "text": f"VERDICT: **采纳 {pick['key']} 阈值 T = {pick['chosen']['T']}N**"
                    f"(AUC {pick['auc_poc']}, 时间基线 {pick['auc_time']})"}


def per_k_table(rows, key):
    out = {}
    for k in K_VALUES:
        sub = [r for r in rows if r["k"] == k]
        us = [r for r in sub if r.get(key) is not None]
        a, _ = _auc_pair(sub, key)
        out[k] = {"n": len(sub), "n_scored": len(us),
                  "base_rate": (round(sum(r["label"] for r in sub) / len(sub), 3)
                                if sub else None),
                  "auc": round(a, 4) if a is not None else None}
    return out


def render(results, rows, verdict):
    L = [f"=== POC 漂移阈值校准 === {len(rows)} 个 (ticker, anchor, k) 单元 / "
         f"{len({r['ticker'] for r in rows})} 票",
         f"label 基准率 (重画会画出不同结构的比例): "
         f"{sum(r['label'] for r in rows) / len(rows):.1%}", ""]
    for res in results:
        L.append(f"--- {res['key']} (可评分 {res['n_used']}/{res['n_total']}) ---")
        L.append(f"  AUC(POC) = {res['auc_poc']}   AUC(距锚天数) = {res['auc_time']}   "
                 f"Δ = {res['delta_auc']}   CI90 {res['delta_auc_ci90']}")
        b = res["chosen"]
        if b:
            L.append(f"  选定 T = {b['T']}N  (TPR {b['tpr']:.1%} / FPR {b['fpr']:.1%} / "
                     f"J {b['J']})")
        c = res["crit"]
        L.append(f"  判据: ①AUC≥{AUC_MIN} {'✓' if c['c1_auc'] else '✗'}  "
                 f"②打赢时间基线 {'✓' if c['c2_beats_time'] else '✗'}  "
                 f"③FPR≤{FPR_MAX:.0%} {'✓' if c['c3_fpr'] else '✗'}  "
                 f"④T∈[0.5,3.0] {'✓' if c['c4_range'] else '✗'}  "
                 f"→ {'PASS' if c['all_pass'] else 'FAIL'}")
        pk = per_k_table(rows, res["key"])
        L.append("  分 k: " + "  ".join(
            f"k={k}(n={v['n_scored']}/{v['n']}, base {v['base_rate']}, AUC {v['auc']})"
            for k, v in pk.items()))
        L.append("")
    L.append(verdict["text"])
    return "\n".join(L)


def main_cli():
    ap = argparse.ArgumentParser(description="POC 漂移阈值校准")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--report", type=Path, default=None)
    ap.add_argument("--limit", type=int, default=None)
    a = ap.parse_args()
    rows = collect(limit=a.limit)
    if not rows:
        print("无样本")
        return 1
    results = [evaluate(rows, f"poc{w}") for w in POC_WINDOWS]
    verdict = final_verdict(results)
    text = render(results, rows, verdict)
    if a.json:
        print(json.dumps({"results": results, "verdict": verdict,
                          "n_units": len(rows)}, ensure_ascii=False, indent=1, default=float))
    else:
        print(text)
    if a.report:
        a.report.parent.mkdir(parents=True, exist_ok=True)
        a.report.write_text("# POC 漂移阈值校准 (机器输出存档)\n\n```\n" + text + "\n```\n")
    return 0


if __name__ == "__main__":
    sys.exit(main_cli())
