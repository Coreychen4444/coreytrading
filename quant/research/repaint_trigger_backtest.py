#!/usr/bin/env python3
"""Repaint trigger backtest — watchlist 卡重画触发线 A vs C（预注册: repaint_trigger_design.md）。

A（现行）: 收盘 > trim 上沿 ×2 连续（breakout stale, zone-maintenance）
C（提案）: 收盘 ≥ trim 下沿 ×2 连续（"两连收在 trim 区间内或上方", 用户）

设计/判据/阈值全部在 design.md 运行前锁定 —— 本文件只是它的执行体。
CLI: .venv/moomoo/bin/python3 quant/research/repaint_trigger_backtest.py [--json]
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 直接 `python3 quant/<层>/<模块>.py` 时让 `quant.*` 可 import

import argparse
import json
import random
import statistics as st

from quant.core import technical  # noqa: E402
from quant.core.paths import KLINE_CACHE_DIR as KDIR  # noqa: E402

UNIVERSE = ["AAOI", "MU", "LITE", "MRVL", "NBIS", "INTC", "NOK", "CRWV", "RKLB",
            "AXTI", "AMKR", "NVDA", "AMD", "GOOGL", "MSFT", "AAPL", "TSM",
            "QCOM", "SNDK", "TSLA", "WDC", "SMR", "ASTS"]
MIN_BARS = 200
WARMUP = 60
BAND_LO, BAND_HI = 0.97, 1.03      # trim 代理带 × 冻结锚 M
HI_LOOKBACK = 20
REANCHOR = 20                       # ARMED 无触发每 20 bars 原地重锚 (valid_until 节奏代理)
A_WAIT = 15                         # C 触发后等 A 的窗口
HORIZON = 20                        # 政策持有期
WHIPSAW_BARS = 5
STOP_N_FRAC = 0.5                   # stop = 带边 − 0.5×ATR20 (bfloor_buf 家规)
BOOT_DRAWS = 2000
BOOT_SEED = 20260805


def load(ticker):
    p = KDIR / f"{ticker}.jsonl"
    if not p.exists():
        return None
    rows = [json.loads(l) for l in p.open()]
    return rows if len(rows) >= MIN_BARS else None


def hi20(rows, i):
    return max(r["high"] for r in rows[max(0, i - HI_LOOKBACK + 1):i + 1])


def two_consec(closes, j, level, strict):
    a, b = closes[j - 1], closes[j]
    return (a > level and b > level) if strict else (a >= level and b >= level)


def policy(rows, closes, entry_j, band_edge, atr20):
    """entry close[entry_j]; stop = band_edge − 0.5N 收盘×1; else T+HORIZON。→ (ret, stopped)"""
    stop = band_edge - STOP_N_FRAC * (atr20[entry_j] or 0.0)
    px = closes[entry_j]
    end = entry_j + HORIZON
    for j in range(entry_j + 1, end + 1):
        if closes[j] < stop:
            return closes[j] / px - 1.0, True
    return closes[end] / px - 1.0, False


def episodes_for(ticker, rows):
    closes = [r["close"] for r in rows]
    atr20 = technical.atr(rows, 20)
    out = []
    i, n = WARMUP, len(rows)
    while i < n - 2:
        if closes[i] >= BAND_LO * hi20(rows, i):
            i += 1
            continue
        # ARM: 冻结锚
        M = hi20(rows, i)
        armed_at = i
        j = i + 1
        c_day = a_day = None
        while j < n:
            if j - armed_at >= REANCHOR and c_day is None:      # 周期性原地重锚
                M = hi20(rows, j)
                armed_at = j
            if c_day is None and two_consec(closes, j, BAND_LO * M, strict=False):
                c_day = j
            if c_day is not None:
                if two_consec(closes, j, BAND_HI * M, strict=True):
                    a_day = j
                    break
                if j - c_day >= A_WAIT:
                    break
            j += 1
        if c_day is None or c_day + HORIZON >= n:
            break                                               # 尾部不足, 本票扫完
        ep = {"ticker": ticker, "M": M, "c": c_day, "a": a_day,
              "gap_days": (a_day - c_day) if a_day is not None else None}
        ep["c_ret"], ep["c_stop"] = policy(rows, closes, c_day, BAND_LO * M, atr20)
        if a_day is not None and a_day + HORIZON < n:
            ep["a_ret"], ep["a_stop"] = policy(rows, closes, a_day, BAND_HI * M, atr20)
            ep["a_fired"] = True
        else:
            ep["a_ret"], ep["a_stop"], ep["a_fired"] = 0.0, False, False
        ep["whipsaw"] = any(closes[k] < BAND_LO * M
                            for k in range(c_day + 1, min(c_day + 1 + WHIPSAW_BARS, n)))
        out.append(ep)
        i = max(c_day, a_day or 0) + REANCHOR                   # 不重叠
    return out


def p10(vals):
    s = sorted(vals)
    return s[max(0, int(0.10 * len(s)) - (1 if len(s) % 10 == 0 else 0))] if s else 0.0


def bootstrap(eps, fn, draws=BOOT_DRAWS, seed=BOOT_SEED):
    """ticker-cluster bootstrap → (lo90, hi90)。fn: episodes → 标量。"""
    by = {}
    for e in eps:
        by.setdefault(e["ticker"], []).append(e)
    keys = sorted(by)
    rng = random.Random(seed)
    vals = []
    for _ in range(draws):
        sample = []
        for _ in keys:
            sample.extend(by[rng.choice(keys)])
        vals.append(fn(sample))
    vals.sort()
    return vals[int(0.05 * draws)], vals[int(0.95 * draws)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    eps = []
    for t in UNIVERSE:
        rows = load(t)
        if rows:
            eps.extend(episodes_for(t, rows))
    n = len(eps)
    per = {}
    for e in eps:
        per[e["ticker"]] = per.get(e["ticker"], 0) + 1
    max_share = max(per.values()) / n if n else 1.0

    diff = lambda s: st.mean(x["c_ret"] - x["a_ret"] for x in s)
    lo, hi = bootstrap(eps, diff)
    c_stop = st.mean(1.0 if e["c_stop"] else 0.0 for e in eps)
    a_stop = st.mean(1.0 if e["a_stop"] else 0.0 for e in eps)
    r = {
        "episodes": n, "tickers": len(per), "max_ticker_share": round(max_share, 3),
        "c_expectancy": round(st.mean(e["c_ret"] for e in eps), 5),
        "a_expectancy": round(st.mean(e["a_ret"] for e in eps), 5),
        "paired_diff": round(diff(eps), 5), "paired_diff_ci90": [round(lo, 5), round(hi, 5)],
        "c_stop_rate": round(c_stop, 4), "a_stop_rate": round(a_stop, 4),
        "stop_rate_delta_pp": round((c_stop - a_stop) * 100, 2),
        "c_p10": round(p10([e["c_ret"] for e in eps]), 5),
        "a_p10": round(p10([e["a_ret"] for e in eps]), 5),
        "gap_days_median": st.median([e["gap_days"] for e in eps if e["gap_days"] is not None])
                           if any(e["gap_days"] is not None for e in eps) else None,
        "a_never_fired_share": round(st.mean(0.0 if e["a_fired"] else 1.0 for e in eps), 4),
        "c_whipsaw_rate": round(st.mean(1.0 if e["whipsaw"] else 0.0 for e in eps), 4),
    }
    # 判据 (design.md 锁定)
    usable = n >= 60 and max_share <= 0.30
    passes = (lo >= 0) and ((c_stop - a_stop) * 100 <= 5.0) and (r["c_p10"] >= r["a_p10"] - 0.01)
    r["verdict"] = ("不裁决 (样本/集中度)" if not usable
                    else ("C 通过三判据 → 可换" if passes else "维持 A"))
    r["criteria"] = {"usable": usable, "ci90_lo_nonneg": lo >= 0,
                     "stop_delta_le_5pp": (c_stop - a_stop) * 100 <= 5.0,
                     "p10_within_1pp": r["c_p10"] >= r["a_p10"] - 0.01}
    print(json.dumps(r, ensure_ascii=False, indent=1) if a.json else _render(r))
    return 0


def _render(r):
    out = [f"=== repaint trigger A vs C === episodes {r['episodes']} / {r['tickers']} 票 "
           f"(最大票占比 {r['max_ticker_share']:.0%})",
           f"expectancy  C {r['c_expectancy']*100:+.2f}%  A {r['a_expectancy']*100:+.2f}%  "
           f"paired diff {r['paired_diff']*100:+.2f}pp  CI90 "
           f"[{r['paired_diff_ci90'][0]*100:+.2f}, {r['paired_diff_ci90'][1]*100:+.2f}]pp",
           f"止损率      C {r['c_stop_rate']:.0%}  A {r['a_stop_rate']:.0%}  "
           f"Δ {r['stop_rate_delta_pp']:+.1f}pp",
           f"p10        C {r['c_p10']*100:+.2f}%  A {r['a_p10']*100:+.2f}%",
           f"时效        median(a−c) {r['gap_days_median']} bars | A 未触发 "
           f"{r['a_never_fired_share']:.0%} (参与缺口)",
           f"白画率      C 触发后 {WHIPSAW_BARS}d 内跌回带下 {r['c_whipsaw_rate']:.0%}",
           f"判据        {json.dumps(r['criteria'], ensure_ascii=False)}",
           f"裁决        {r['verdict']}"]
    return "\n".join(out)


if __name__ == "__main__":
    raise SystemExit(main())
