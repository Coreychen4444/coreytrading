#!/usr/bin/env python3
"""hvn_gate 预注册的**早停筛查**: HVN gate 与 cluster gate 的重合率。

预注册 (private preregistration §4) 写死: 重合率 (gate 价位差 < 1 bucket) **> 70%
则本假设几乎无信息, 直接停**—— 省得跑完全套才发现两者本来就是一回事。

只做这一件事, 不看任何前向收益 (看了就污染了主回测)。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import argparse
import json
import statistics as st

from quant.core import technical  # noqa: E402
from quant.core.paths import KLINE_DEEP_DIR as KDIR  # noqa: E402
from quant.decision import level_map  # noqa: E402
from quant.research.gate_backtest import (# noqa: E402
    WING, confirmed_swing_highs, episodes_of, load, sma_series)
from quant.research.gate_backtest2 import (# noqa: E402
    atr_series, cluster_gate, confirmed_swing_lows)

PROFILE_BARS = 60          # 与 level_map.profile_days 同拍
HVN_N = 5                  # 与 level_map 默认同拍


def hvn_gate(bars, i, close_i, atr14_i):
    """价格上方最近 HVN 带的上沿（定义必须锁入 private preregistration）。

    profile = 破位日 i 往前 60 根日线 (含 i), 三角铺开; bucket = auto_bucket。
    无上方 HVN → None (由调用方决定回退)。
    """
    # i 是**下标**: 窗口 bars[i-59 : i+1] 在 i == 59 时就正好 60 根 —— 用
    # `i < PROFILE_BARS` 会把这个边界误拒 (测试抓到)。真实 episode 恒有 i ≥ 60
    # (episodes_of 从 LOOKBACK=60 起), 所以筛查数字不受影响, 但函数本身该是对的。
    if atr14_i is None or i < PROFILE_BARS - 1:
        return None
    window = bars[i - PROFILE_BARS + 1:i + 1]
    bkt = level_map.auto_bucket(close_i, atr14_i)
    prof = technical.volume_profile(window, bkt)
    if not prof:
        return None
    bands = level_map.top_hvn(prof, n=HVN_N)
    above = [b for b in bands if b["hi"] > close_i]
    return min(b["hi"] for b in above) if above else None


def screen(kdir=KDIR, limit=None):
    rows = []
    files = sorted(kdir.glob("*.jsonl"))
    for path in files[:limit] if limit else files:
        bars = load(path)
        if len(bars) < 300:
            continue
        closes = [b["close"] for b in bars]
        highs = [b["high"] for b in bars]
        lows = [b["low"] for b in bars]
        ma60 = sma_series(closes, 60)
        atr14 = atr_series(highs, lows, closes, 14)
        sh = confirmed_swing_highs(highs)
        sl = confirmed_swing_lows(lows)
        for i in episodes_of(closes, ma60):
            if i < PROFILE_BARS - 1 or i >= len(bars) - 50:
                continue
            a14 = atr14[i]
            if not a14:
                continue
            cg = cluster_gate(i, closes[i], sh, sl, a14)
            hg = hvn_gate(bars, i, closes[i], a14)
            bkt = level_map.auto_bucket(closes[i], a14)
            rows.append({
                "ticker": path.stem, "i": i, "close": closes[i], "bucket": bkt,
                "cluster": cg, "hvn": hg,
                "diff_bucket": (abs(hg - cg) / bkt) if (cg and hg) else None,
                "diff_n": (abs(hg - cg) / a14) if (cg and hg) else None,
            })
    return rows


def report(rows):
    n = len(rows)
    both = [r for r in rows if r["diff_bucket"] is not None]
    hvn_missing = sum(1 for r in rows if r["hvn"] is None)
    cl_missing = sum(1 for r in rows if r["cluster"] is None)
    coincide = sum(1 for r in both if r["diff_bucket"] < 1.0)
    out = {
        "episodes": n,
        "both_defined": len(both),
        "hvn_undefined_pct": round(hvn_missing / n * 100, 1) if n else None,
        "cluster_undefined_pct": round(cl_missing / n * 100, 1) if n else None,
        "coincidence_pct": round(coincide / len(both) * 100, 1) if both else None,
        "diff_bucket_p50": round(st.median(r["diff_bucket"] for r in both), 2) if both else None,
        "diff_n_p50": round(st.median(r["diff_n"] for r in both), 2) if both else None,
        "diff_n_p90": round(sorted(r["diff_n"] for r in both)[int(len(both) * 0.9)], 2) if both else None,
        "hvn_above_cluster_pct": round(
            sum(1 for r in both if r["hvn"] > r["cluster"]) / len(both) * 100, 1) if both else None,
    }
    out["verdict"] = ("STOP — 重合率 >70%, 本假设几乎无信息 (预注册 §4)"
                      if (out["coincidence_pct"] or 0) > 70 else
                      "PROCEED — 两种画法给出实质不同的线, 主回测有意义")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="HVN gate 早停筛查 (重合率)")
    ap.add_argument("--limit", type=int, default=None, help="只扫前 N 个文件 (冒烟用)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    rows = screen(limit=a.limit)
    rep = report(rows)
    if a.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        for k, v in rep.items():
            print(f"{k:<24}{v}")
