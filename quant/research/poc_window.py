#!/usr/bin/env python3
"""POC 窗口与 ②维去留 — round-2 (预注册 poc_window_design.md, 已锁定)。

## round-1 留下的两件未了事
1. `POC_WINDOW` 该不该 20 → 60 (live 参数)
2. ②POC 维该不该留 (round-1 §5 否定分支: 判据 2 不过 ⇒ 时间基线已足够 ⇒ 考虑摘掉)
**二选一, 不允许"既不改也不摘"继续挂着。**

## 本轮的核心修正 (设计稿 §2)
round-1 里 poc60 的 AUC 0.812 **不可信地高**: 自变量 (POC = 60 根 profile 的 argmax) 与
因变量 (label = 同一份 profile 的 HVN 带动没动) **共用一个分布**。
本轮把 label 换成 **pivot 锚的 zone 组**—— 那才是 `regime_transition` 画 zone 的真实机制,
且与 volume profile **不共用任何分布**:
  自变量 = 成交额在价格上的分布 (volume 来源)
  因变量 = 价格的转折点          (price 来源)

## 判据 (§5, 运行前锁定)
5a 窗口: AUC(60) − AUC(20) ≥ 0.05 且 CI90 下界 > 0, 且 AUC(60) ≥ 0.65 → 改; 否则维持 20
5b 去留: 打赢时间基线 (≥0.05) **且**打赢 ①price_drift (≥0.03), 两者 CI90 下界 > 0;
        且存在 T 使 FPR ≤30% 且 T ∈ [0.5,3.0]; 且该 T 上 AUC ≥ 0.65
        —— 任一不满足 → **摘掉 ②POC 维**

Usage: .venv/moomoo/bin/python3 quant/research/poc_window.py [--json] [--report PATH]
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
from quant.research.gate_backtest import load  # noqa: E402
from quant.research.gate_backtest2 import atr_series  # noqa: E402
from quant.research.poc_calibration import (# noqa: E402  同一套机械件, 不再抄一份
    ANCHOR_STRIDE, AUC_MIN, FPR_MAX, K_VALUES, RIGHT_GUARD, T_RANGE,
    _auc_np, auc, pick_threshold)

# : ②POC 维已从 live 的 drift.py 摘除。这个常量当时是 drift.py 的守卫,
# 复制到这里是为了让本回测**保持可复跑**—— 研究件不该依赖一个已经不存在的 live 参数。
POC_MIN_SESSIONS = 10
POC_WINDOWS = (20, 60)
SWING_WING = 2          # = regime_transition.PARAMS["swing_wing"], 锁死不扫
PIVOT_LOOKBACK = 120    # = regime_transition.PARAMS["pivot_lookback"], 锁死不扫
ZONE_LEGS = 3           # z1_lo / z2_lo / stop —— derive_observation_regime 取 below[:3]
WINDOW_MARGIN = 0.05    # 判据 5a-1
TIME_MARGIN = 0.05      # 判据 5b-1
PRICE_MARGIN = 0.03     # 判据 5b-2
BOOT_DRAWS = 2000
BOOT_SEED = 20260807


# ---------- pivot 锚的 zone 组 (新 label 的来源) ----------

def _swing_lows(lows, wing=SWING_WING):
    out = []
    for i in range(wing, len(lows) - wing):
        w = lows[i - wing:i + wing + 1]
        if lows[i] == min(w) and w.count(lows[i]) == 1:
            out.append((i, lows[i]))
    return out


def zone_legs(swings_l, idx, ref_close):
    """= derive_observation_regime 的画法: 近 PIVOT_LOOKBACK 根内、低于参考价的
    **已确认**swing low, 由高到低取 3 档 → (z1_lo, z2_lo, stop)。不足则短。"""
    lo_i = max(0, idx - PIVOT_LOOKBACK + 1)
    cands = sorted({round(v, 2) for j, v in swings_l
                    if lo_i <= j and j + SWING_WING <= idx and v < ref_close}, reverse=True)
    return tuple(cands[:ZONE_LEGS])


def zone_changed(a_legs, d_legs, bucket):
    """任一档移动 ≥1 bucket, 或档数变化 (pivot 消失/新生) → 1。"""
    if len(a_legs) != len(d_legs):
        return 1
    return int(any(abs(x - y) >= bucket for x, y in zip(a_legs, d_legs)))


# ---------- POC (drift.py 口径) ----------

def _poc(bars, end_idx, window, bucket, cache):
    key = (end_idx, window, bucket)
    if key in cache:
        return cache[key]
    prof = technical.volume_profile(bars[max(0, end_idx - window + 1):end_idx + 1], bucket)
    val = (max(prof, key=lambda b: b["turnover"])["lo"] + bucket / 2) if prof else None
    cache[key] = val
    return val


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
        a20 = atr_series(highs, lows, closes, 20)
        swl = _swing_lows(lows)
        cache = {}
        for ia in range(PIVOT_LOOKBACK, n, ANCHOR_STRIDE):
            if not a20[ia]:
                continue
            a_legs = zone_legs(swl, ia, closes[ia])
            for k in K_VALUES:
                idx = ia + k
                if idx > n - 1 - RIGHT_GUARD or not a20[idx]:
                    continue
                bucket = technical.auto_bucket(closes[idx], a20[idx])
                d_legs = zone_legs(swl, idx, closes[idx])
                rec = {"ticker": path.stem, "k": k,
                       "label": zone_changed(a_legs, d_legs, bucket),
                       "price_drift_n": abs(closes[idx] - closes[ia]) / a20[ia]}
                for w in POC_WINDOWS:
                    if idx - ia < POC_MIN_SESSIONS:
                        rec[f"poc{w}"] = None
                        continue
                    pa = _poc(bars, ia, w, bucket, cache)
                    pd = _poc(bars, idx, w, bucket, cache)
                    rec[f"poc{w}"] = (abs(pd - pa) / a20[idx]) if (pa and pd) else None
                rows.append(rec)
    return rows


# ---------- bootstrap (按 ticker 整块) ----------

def _blocks(rows, keys):
    by = defaultdict(list)
    for r in rows:
        by[r["ticker"]].append(r)
    import numpy as np
    out = {}
    for t, rs in by.items():
        out[t] = {k: np.asarray([r[k] for r in rs], dtype=np.float64) for k in keys}
        out[t]["label"] = np.asarray([r["label"] for r in rs], dtype=np.int8)
    return out


def boot_auc_diff(rows, key_a, key_b, draws=BOOT_DRAWS, seed=BOOT_SEED):
    """AUC(key_a) − AUC(key_b) 的 CI90, 按 ticker 整块重抽。"""
    usable = [r for r in rows if r.get(key_a) is not None and r.get(key_b) is not None]
    if not usable:
        return None
    import numpy as np
    blocks = _blocks(usable, (key_a, key_b))
    ts = sorted(blocks)
    M = len(ts)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, M, size=(draws, M))
    vals = []
    for draw in idx:
        picks = [blocks[ts[int(t)]] for t in draw]
        lb = np.concatenate([p["label"] for p in picks])
        a = _auc_np(np.concatenate([p[key_a] for p in picks]), lb)
        b = _auc_np(np.concatenate([p[key_b] for p in picks]), lb)
        if a is not None and b is not None:
            vals.append(a - b)
    if not vals:
        return None
    vals.sort()
    return [round(vals[int(len(vals) * 0.05)], 4),
            round(vals[min(len(vals) - 1, int(len(vals) * 0.95))], 4)]


def _auc_of(rows, key):
    sub = [(r[key], r["label"]) for r in rows if r.get(key) is not None]
    if not sub:
        return None, 0
    return auc([s for s, _ in sub], [l for _, l in sub]), len(sub)


# ---------- 裁决 ----------

def evaluate(rows):
    """⚠️ 所有**比较**都在共同可评分子集上做 (POC 有读数的那些行)。

    POC_MIN_SESSIONS=10 让 k=3/5 没有 POC 读数; 若点估计在全样本上算、CI 却在子集上算,
    两者就是两个估计量 —— 冒烟时实测到点估计 −0.1603 落在自己的 CI [−0.039,−0.033]
    之外, 正是这个错。基线 AUC 一并在同一子集上重算。
    """
    common = [r for r in rows
              if r.get("poc20") is not None and r.get("poc60") is not None]
    out = {"n_units": len(rows), "n_tickers": len({r["ticker"] for r in rows}),
           "base_rate": round(sum(r["label"] for r in rows) / len(rows), 4),
           "n_common": len(common),
           "base_rate_common": (round(sum(r["label"] for r in common) / len(common), 4)
                                if common else None)}
    for key in ("poc20", "poc60", "price_drift_n", "k"):
        a, n = _auc_of(rows, key)
        out[f"auc_full_{key}"] = round(a, 4) if a is not None else None
        ac, nc = _auc_of(common, key)
        out[f"auc_{key}"] = round(ac, 4) if ac is not None else None
        out[f"n_{key}"] = nc
    all_rows, rows = rows, common      # 之后所有判据都用共同子集; 分 k 表仍看全样本

    # 5a 窗口之争
    d_win = ((out["auc_poc60"] - out["auc_poc20"])
             if None not in (out["auc_poc60"], out["auc_poc20"]) else None)
    ci_win = boot_auc_diff(rows, "poc60", "poc20")
    w1 = bool(d_win is not None and d_win >= WINDOW_MARGIN and ci_win and ci_win[0] > 0)
    w2 = bool(out["auc_poc60"] is not None and out["auc_poc60"] >= AUC_MIN)
    out["window"] = {"delta": round(d_win, 4) if d_win is not None else None,
                     "ci90": ci_win, "c1_margin": w1, "c2_auc": w2,
                     "switch_to_60": bool(w1 and w2)}
    winner = "poc60" if out["window"]["switch_to_60"] else "poc20"
    out["winner"] = winner

    # 5b 去留 (用胜出的窗口)
    d_time = ((out[f"auc_{winner}"] - out["auc_k"])
              if None not in (out[f"auc_{winner}"], out["auc_k"]) else None)
    d_price = ((out[f"auc_{winner}"] - out["auc_price_drift_n"])
               if None not in (out[f"auc_{winner}"], out["auc_price_drift_n"]) else None)
    ci_time = boot_auc_diff(rows, winner, "k")
    ci_price = boot_auc_diff(rows, winner, "price_drift_n")
    pick = pick_threshold(rows, winner)
    best = (pick or {}).get("best")
    k1 = bool(d_time is not None and d_time >= TIME_MARGIN and ci_time and ci_time[0] > 0)
    k2 = bool(d_price is not None and d_price >= PRICE_MARGIN and ci_price and ci_price[0] > 0)
    k3 = bool(best and best["fpr"] <= FPR_MAX and T_RANGE[0] <= best["T"] <= T_RANGE[1])
    k4 = bool(out[f"auc_{winner}"] is not None and out[f"auc_{winner}"] >= AUC_MIN)
    out["keep"] = {"vs_time": round(d_time, 4) if d_time is not None else None,
                   "vs_time_ci90": ci_time,
                   "vs_price": round(d_price, 4) if d_price is not None else None,
                   "vs_price_ci90": ci_price, "chosen_T": best,
                   "c1_beats_time": k1, "c2_beats_price": k2,
                   "c3_threshold": k3, "c4_auc": k4,
                   "keep_dimension": bool(k1 and k2 and k3 and k4)}

    out["per_k"] = {}
    for k in K_VALUES:
        sub = [r for r in all_rows if r["k"] == k]
        if not sub:
            continue
        a20, n20 = _auc_of(sub, "poc20")
        a60, n60 = _auc_of(sub, "poc60")
        out["per_k"][k] = {"n": len(sub), "scored": n60,
                           "base": round(sum(r["label"] for r in sub) / len(sub), 3),
                           "auc20": round(a20, 4) if a20 else None,
                           "auc60": round(a60, 4) if a60 else None}
    return out


def final_action(res):
    w = res["window"]["switch_to_60"]
    keep = res["keep"]["keep_dimension"]
    #  已裁决并执行: ②POC 维摘除 (drop_dimension 分支), drift 的
    # poc_shift_n 阈值与播报已删 —— 文案改为已裁决态 (ledger R5), 复跑本脚本
    # 不再产出指向已删符号的待办。裁决全文 poc_window_design.md §10。
    if not keep:
        return {"action": "drop_dimension",
                "text": "已裁决 (, 即本分支): ②POC 维已从 drift 装置摘除, "
                        "drift 的 poc_shift_n 阈值与相应播报已删。本输出为复算确认, "
                        "无待办。判据 5b 未全过 (round-1 §5 的否定分支); "
                        "裁决记录 poc_window_design.md §10。"}
    if w:
        return {"action": "switch_window_60",
                "text": f"⚠️ 复算落在「保留②维且窗口 20→60」分支 (阈值 T = "
                        f"{res['keep']['chosen_T']['T']}N) — 与  已执行裁决 "
                        "(②维摘除) 相反。恢复②维是翻案不是参数调整: 先读 "
                        "poc_window_design.md §10, 归 用户 重裁, 勿直接改 live。"}
    return {"action": "keep_window_20",
            "text": f"⚠️ 复算落在「保留②维, 窗口维持 20」分支 (阈值 T = "
                    f"{res['keep']['chosen_T']['T']}N) — 与  已执行裁决 "
                    "(②维摘除) 相反。恢复②维是翻案不是参数调整: 先读 "
                    "poc_window_design.md §10, 归 用户 重裁, 勿直接改 live。"}


def render(res, act):
    L = [f"=== POC 窗口与②维去留 (round-2) === {res['n_units']} 单元 / "
         f"{res['n_tickers']} 票",
         f"label = **pivot 锚的 zone 组**(z1/z2/stop) 重画会变的比例: {res['base_rate']:.1%}",
         "  (round-1 用的是 volume profile 的 HVN 带 = 与 POC 共用分布; 本轮已解耦)", "",
         f"共同可评分子集 n={res['n_common']} (POC 有读数的行; k=3/5 结构上没有), "
         f"该子集 label 基准率 {res['base_rate_common']:.1%}",
         "  **所有 AUC 比较都在这个子集上做**—— 点估计与 CI 必须同一个估计量", "",
         f"{'指标':<22}{'AUC (共同子集)':>16}{'AUC (全样本)':>14}",
         f"{'poc20 (live 现行)':<22}{res['auc_poc20']:>16}{res['auc_full_poc20']:>14}",
         f"{'poc60':<22}{res['auc_poc60']:>16}{res['auc_full_poc60']:>14}",
         f"{'①price_drift_n':<22}{res['auc_price_drift_n']:>16}"
         f"{res['auc_full_price_drift_n']:>14}",
         f"{'时间 (距锚天数)':<22}{res['auc_k']:>16}{res['auc_full_k']:>14}", ""]
    w = res["window"]
    L.append(f"[5a 窗口] AUC(60)−AUC(20) = {w['delta']}  CI90 {w['ci90']}  "
             f"→ 幅度 {'✓' if w['c1_margin'] else '✗'} / AUC≥0.65 "
             f"{'✓' if w['c2_auc'] else '✗'} → "
             f"{'改用 60' if w['switch_to_60'] else '维持 20'}")
    k = res["keep"]
    L.append(f"[5b 去留] 用 {res['winner']}:")
    L.append(f"  打赢时间基线 Δ {k['vs_time']} CI90 {k['vs_time_ci90']} "
             f"(门槛 {TIME_MARGIN}) → {'✓' if k['c1_beats_time'] else '✗'}")
    L.append(f"  打赢①价格离锚 Δ {k['vs_price']} CI90 {k['vs_price_ci90']} "
             f"(门槛 {PRICE_MARGIN}) → {'✓' if k['c2_beats_price'] else '✗'}")
    if k["chosen_T"]:
        b = k["chosen_T"]
        L.append(f"  阈值 T = {b['T']}N (TPR {b['tpr']:.1%} / FPR {b['fpr']:.1%}) "
                 f"→ {'✓' if k['c3_threshold'] else '✗ (FPR 或区间不合格)'}")
    else:
        L.append("  阈值: 无合格候选 → ✗")
    L.append(f"  AUC ≥ 0.65 → {'✓' if k['c4_auc'] else '✗'}")
    L.append("")
    L.append("分 k (⚠️ POC_MIN_SESSIONS=10 → k=3/5 结构上无读数):")
    for kk, v in res["per_k"].items():
        L.append(f"  k={kk:<3} n={v['n']:<7} 可评分 {v['scored']:<7} base {v['base']}  "
                 f"AUC20 {v['auc20']}  AUC60 {v['auc60']}")
    L.append("")
    L.append(act["text"])
    return "\n".join(L)


def main_cli():
    ap = argparse.ArgumentParser(description="POC 窗口与②维去留 round-2")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--report", type=Path, default=None)
    ap.add_argument("--limit", type=int, default=None)
    a = ap.parse_args()
    rows = collect(limit=a.limit)
    if not rows:
        print("无样本")
        return 1
    res = evaluate(rows)
    act = final_action(res)
    text = render(res, act)
    if a.json:
        print(json.dumps({"result": res, "action": act}, ensure_ascii=False,
                         indent=1, default=float))
    else:
        print(text)
    if a.report:
        a.report.parent.mkdir(parents=True, exist_ok=True)
        a.report.write_text("# POC 窗口 round-2 (机器输出存档)\n\n```\n" + text + "\n```\n")
    return 0


if __name__ == "__main__":
    sys.exit(main_cli())
