#!/usr/bin/env python3
"""SSS 信息含量回测 — 私人预注册协议的执行器。

判据、池、槽位代理和政策必须先锁定；本文件只执行，不新增自由度。
它检验 Signal Strength Score 是否具有信息含量。

Usage: .venv/moomoo/bin/python3 quant/research/sss_backtest.py [--json]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import argparse  # noqa: E402
import json  # noqa: E402
import random  # noqa: E402
import statistics as st  # noqa: E402
from collections import defaultdict  # noqa: E402

from quant.core import technical  # noqa: E402
from quant.core.paths import KLINE_DEEP_DIR as KDIR  # noqa: E402
from quant.core.sectors import benchmark_for  # noqa: E402

# ── §5 锁定参数 (勿改 — 改 = 重新预注册) ─────────────────────────
WING = 2
STRIDE = 5                 # 采样步长 (自相关控制)
WARMUP = 480               # MA200+斜率 / ATR% 分位窗 / MACD / B 槽 取最大留余量
STOP_N = 2.0               # stop = entry − 2×ATR20
SLIP = 0.005               # 止损确认后次日收盘滑点
TERM = 60                  # 终点 horizon
BANDS = [(0.0, 0.3), (0.3, 0.5), (0.5, 0.65), (0.65, 0.85), (0.85, 1.001)]
MID, TOP = 2, 4            # 参与域 mid=[0.5,0.65) / top=[0.85,1]
W = {"T": 0.30, "B": 0.25, "M": 0.20, "V": 0.15, "VOL": 0.10}
BOOT = 1000
SEED = 387_298
GUARD_STOP_PP = 5.0        # 止损率守卫: top ≤ mid + 5pp (入场锚定家系)


def load(path: Path):
    rows = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    return [r for r in rows if r.get("close")]


def trend_series(klines):
    """道氏 trend 逐日序列 — technical.swing_structure 前缀等价 (wing=WING) 的
    增量版: pivot 在 idx+WING 日确认, 此后参与最近两高/两低比较。O(n)。"""
    sh, sl = technical.swing_points(klines, WING)
    n = len(klines)
    out = ["insufficient"] * n
    hs, ls = [], []
    hi_i = lo_i = 0
    for i in range(n):
        while hi_i < len(sh) and sh[hi_i][0] + WING <= i:
            hs.append(sh[hi_i][1]); hi_i += 1
        while lo_i < len(sl) and sl[lo_i][0] + WING <= i:
            ls.append(sl[lo_i][1]); lo_i += 1
        if len(hs) >= 2 and len(ls) >= 2:
            hi = "HH" if hs[-1] > hs[-2] else "LH"
            lo = "HL" if ls[-1] > ls[-2] else "LL"
            out[i] = {"HHHL": "up", "LHLL": "down"}.get(hi + lo, "transition")
    return out


def clip01(x):
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def bench_vr_map(name, cache):
    if name not in cache:
        rows = load(KDIR / f"{name}.jsonl")
        vr = technical.volume_ratio([r.get("volume") or 0 for r in rows], 20)
        cache[name] = {r["time"][:10]: v for r, v in zip(rows, vr) if v}
    return cache[name]


def scan_ticker(t, bcache, samples, warn, start):
    rows = load(KDIR / f"{t}.jsonl")
    n = len(rows)
    if n < WARMUP + TERM + 2:
        return
    closes = [r["close"] for r in rows]
    dates = [r["time"][:10] for r in rows]
    vols = [r.get("volume") or 0 for r in rows]
    ma200 = technical.sma(closes, 200)
    atr20 = technical.atr(rows, 20)
    hist = technical.macd(closes)["histogram"]
    vr = technical.volume_ratio(vols, 20)
    trend = trend_series(rows)
    bvr = bench_vr_map(benchmark_for(t), bcache)

    # B 槽: 距最近 60d 收盘新高的天数
    last_nh = [-1] * n
    for i in range(59, n):
        prev = last_nh[i - 1] if i else -1
        last_nh[i] = i if closes[i] >= max(closes[i - 59:i + 1]) else prev
    # Vol 槽: ATR% 往前 252 值分位
    atrpct = [a / c if (a and c) else None for a, c in zip(atr20, closes)]

    for i in range(WARMUP, n - TERM - 2, STRIDE):
        if dates[i] < start:
            continue
        a = atr20[i]
        if not a or not ma200[i] or hist[i] is None or not vr[i] or not atrpct[i]:
            continue
        win = [x for x in atrpct[i - 251:i + 1] if x is not None]
        if len(win) < 200:
            continue
        t_slot = 0.5 * (trend[i] == "up") + 0.5 * (closes[i] > ma200[i] and ma200[i] > ma200[i - 20])
        d_nh = i - last_nh[i] if last_nh[i] >= 0 else 10 **9
        b_slot = max(0.0, 1 - d_nh / 20)
        m_slot = clip01(0.5 + hist[i] / (0.5 * a))
        bv = bvr.get(dates[i])
        if bv:
            v_slot = clip01((vr[i] / max(bv, 0.4) - 0.8) / 0.8)
        else:
            v_slot = clip01((vr[i] - 0.8) / 0.8)   # 绝对量比兜底 (降级出声)
            warn["v_abs"] += 1
        vol_slot = 1 - sum(1 for x in win if x < atrpct[i]) / len(win)
        sss = (W["T"] * t_slot + W["B"] * b_slot + W["M"] * m_slot
               + W["V"] * v_slot + W["VOL"] * vol_slot)

        entry, stop = closes[i], closes[i] - STOP_N * a
        term = i + TERM
        stopped, exit_px = False, closes[term]
        for j in range(i + 1, term + 1):
            if closes[j] < stop:                    # 收盘 ×1 日确认
                exit_px = closes[min(j + 1, term)] * (1 - SLIP)
                stopped = True
                break
        r_mult = (exit_px - entry) / (STOP_N * a)
        band = next(k for k, (lo, hi) in enumerate(BANDS) if lo <= sss < hi)
        samples.append({"t": t, "band": band, "R": r_mult, "stopped": stopped,
                        "t60": closes[term] / entry - 1,
                        "confirmed": trend[i] not in ("down", "transition")})


def band_stats(rows):
    if not rows:
        return {"n": 0}
    return {"n": len(rows),
            "meanR": st.mean(x["R"] for x in rows),
            "stop_rate": st.mean(1.0 if x["stopped"] else 0.0 for x in rows),
            "t60": st.mean(x["t60"] for x in rows)}


def boot_delta(samples, tickers, key_filter, rng):
    """Δ(top−mid) mean R 的按票 cluster bootstrap → (point, lo90, hi90)。"""
    by_t = defaultdict(lambda: {"mid": [], "top": []})
    for x in samples:
        if not key_filter(x):
            continue
        if x["band"] == MID:
            by_t[x["t"]]["mid"].append(x["R"])
        elif x["band"] == TOP:
            by_t[x["t"]]["top"].append(x["R"])
    names = list(tickers)
    def pooled(sel):
        mid = [r for t in sel for r in by_t[t]["mid"]]
        top = [r for t in sel for r in by_t[t]["top"]]
        if not mid or not top:
            return None
        return st.mean(top) - st.mean(mid)
    point = pooled(names)
    deltas = []
    for _ in range(BOOT):
        sel = [names[rng.randrange(len(names))] for _ in names]
        d = pooled(sel)
        if d is not None:
            deltas.append(d)
    deltas.sort()
    if not deltas:
        return point, None, None
    return point, deltas[int(0.05 * len(deltas))], deltas[int(0.95 * len(deltas))]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--start", required=True, help="locked study start session")
    args = ap.parse_args()
    rng = random.Random(SEED)

    tickers = sorted(p.stem for p in KDIR.glob("*.jsonl"))
    samples, bcache = [], {}
    warn = defaultdict(int)
    for t in tickers:
        scan_ticker(t, bcache, samples, warn, args.start)

    used = sorted({x["t"] for x in samples})
    bands = [band_stats([x for x in samples if x["band"] == k]) for k in range(len(BANDS))]

    d_all = boot_delta(samples, used, lambda x: True, rng)
    d_conf = boot_delta(samples, used, lambda x: x["confirmed"], rng)

    # 判据 (§5 锁定)
    part = bands[2:5]
    mono = all(part[k].get("meanR") is not None for k in range(3)) and \
        part[0]["meanR"] <= part[1]["meanR"] <= part[2]["meanR"]
    c1 = mono and d_all[1] is not None and d_all[1] > 0
    c2 = (bands[TOP]["n"] and bands[MID]["n"]
          and bands[TOP]["stop_rate"] <= bands[MID]["stop_rate"] + GUARD_STOP_PP / 100)
    c3 = d_conf[1] is not None and d_conf[1] > 0
    verdict = "SUPPORT" if (c1 and c2 and c3) else "FAIL"

    out = {"n_samples": len(samples), "n_tickers": len(used),
           "v_abs_fallback": warn["v_abs"],
           "bands": [{"range": f"[{lo},{hi if hi <= 1 else 1}]", **b}
                     for (lo, hi), b in zip(BANDS, bands)],
           "delta_top_mid_R": {"point": d_all[0], "ci90": [d_all[1], d_all[2]]},
           "delta_confirmed_subset": {"point": d_conf[0], "ci90": [d_conf[1], d_conf[2]]},
           "criteria": {"c1_monotone_ci": bool(c1), "c2_stop_guard": bool(c2),
                        "c3_h_shadow_increment": bool(c3)},
           "verdict": verdict}
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=1, default=float))
        return
    print(f"SSS 回测 (§5 预注册) — {len(samples):,} 样本 / {len(used)} 票 / "
          f"V槽绝对兜底 {warn['v_abs']}")
    print(f"{'带':<14}{'n':>8}{'meanR':>9}{'止损率':>8}{'T60':>9}")
    for (lo, hi), b in zip(BANDS, bands):
        if not b["n"]:
            print(f"[{lo:.2f},{min(hi,1):.2f})  {'—':>8}")
            continue
        print(f"[{lo:.2f},{min(hi,1):.2f})  {b['n']:>8,}{b['meanR']:>9.3f}"
              f"{b['stop_rate']*100:>7.1f}%{b['t60']*100:>8.1f}%")
    p, lo, hi = d_all
    print(f"Δ(top−mid) R = {p:+.3f}  CI90 [{lo:+.3f}, {hi:+.3f}]")
    p, lo, hi = d_conf
    print(f"Δ within confirmed 子集 = {p:+.3f}  CI90 [{lo:+.3f}, {hi:+.3f}]")
    print(f"判据: c1 单调+CI={out['criteria']['c1_monotone_ci']} "
          f"c2 止损守卫={out['criteria']['c2_stop_guard']} "
          f"c3 H-shadow增量={out['criteria']['c3_h_shadow_increment']} → {verdict}")


if __name__ == "__main__":
    main()
