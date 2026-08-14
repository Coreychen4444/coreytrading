#!/usr/bin/env python3
"""Entry ladder backtest — 评测追涨设计 (3-tier ladder vs alternatives).

对每个 (ticker, day, regime) 采样点模拟 7 种入场策略, 比较部署率/均价/收益/痛感.
Regime 定义复刻 zone-maintenance.md momentum diagnosis (Q1/Q2/Q3).
Fill 模拟: 日线 OHLC, gap-through 按 open 成交, 否则 limit 价成交.
LADDER_RE = production behavior（unfilled rungs 每 3 日向上重锚，只升不降）。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 直接 `python3 quant/<层>/<模块>.py` 时让 `quant.*` 可 import

import json
import statistics as st
from collections import defaultdict

from quant.core.paths import KLINE_CACHE_DIR as KDIR  # noqa: E402
WINDOW = 10   # 挂单有效期 (交易日)
T1 = 20       # 主评估 horizon
T2 = 40       # 次评估 horizon
REANCHOR_EVERY = 3


def available_tickers() -> list[str]:
    """Discover the private study universe without embedding it in public source."""
    return sorted(path.stem for path in KDIR.glob("*.jsonl") if path.is_file())


def load(tk):
    rows = []
    p = KDIR / f"{tk}.jsonl"
    if not p.exists():
        return rows
    for line in p.read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            rows.append((r["time"][:10], r["open"], r["high"], r["low"], r["close"]))
    return rows


def regime_of(bars, i):
    """返回 'parabolic' / 'strong_up' / 'other', bars[i] 为决策日 (收盘后)."""
    if i < 126:
        return None
    close = [b[4] for b in bars]
    high = [b[2] for b in bars]
    c = close[i]
    q1 = c / close[i - 126] - 1 >= 1.50
    q2 = any(close[j] / close[j - 1] - 1 >= 0.10 for j in range(i - 19, i + 1) if j >= 1)
    ath = max(high[: i + 1])
    q3 = c >= 0.90 * ath
    if q1 and q2 and q3:
        return "parabolic"
    ma20 = sum(close[i - 19: i + 1]) / 20
    ma50 = sum(close[i - 49: i + 1]) / 50
    ret60 = c / close[i - 60] - 1
    if c > ma20 > ma50 and ret60 >= 0.15:
        return "strong_up"
    return "other"


def sim_fills(bars, d, rungs, reanchor=False):
    """rungs: list of (limit, weight). 返回 (deployed, avg_cost, first_fill_idx).

    reanchor: False=静态 rung; True=每 REANCHOR_EVERY 日重锚; "re2"=每 2 日重锚
    (对齐 zone-maintenance breakout-stale 2 交易日口径)。重锚只升不降。
    """
    n = len(bars)
    end = min(d + WINDOW, n - 1)
    every = 2 if reanchor == "re2" else REANCHOR_EVERY
    live = [{"L": L, "w": w, "filled_at": None} for L, w in rungs]
    for t in range(d + 1, end + 1):
        if reanchor and (t - d - 1) > 0 and (t - d - 1) % every == 0:
            # 重锚未成交 rung (只升不降): 以 t-1 收盘时点重算
            j = t - 1
            c2 = bars[j][4]
            h5 = max(b[2] for b in bars[j - 4: j + 1])
            h10 = max(b[2] for b in bars[j - 9: j + 1])
            new_ls = [c2 * 0.99, h5 * 0.935, h10 * 0.875]
            for k, r in enumerate(live[:3]):
                if r["filled_at"] is None and k < len(new_ls):
                    r["L"] = max(r["L"], new_ls[k])
        o, h, lo = bars[t][1], bars[t][2], bars[t][3]
        for r in live:
            if r["filled_at"] is None:
                if o <= r["L"]:
                    r["filled_at"] = t
                    r["px"] = o
                elif lo <= r["L"]:
                    r["filled_at"] = t
                    r["px"] = r["L"]
    filled = [r for r in live if r["filled_at"] is not None]
    if not filled:
        return 0.0, None, None
    dep = sum(r["w"] for r in filled)
    cost = sum(r["w"] * r["px"] for r in filled) / dep
    return dep, cost, min(r["filled_at"] for r in filled)


def strategies(bars, d):
    p0 = bars[d][4]
    h5 = max(b[2] for b in bars[d - 4: d + 1])
    h10 = max(b[2] for b in bars[d - 9: d + 1])
    return {
        "ALLIN":     ([(10**9, 1.0)], False),                # open[D+1] 全进 (10^9=必成交)
        "LADDER":    ([(p0 * 0.99, .35), (h5 * .935, .35), (h10 * .875, .30)], False),
        "LADDER_RE": ([(p0 * 0.99, .35), (h5 * .935, .35), (h10 * .875, .30)], True),
        "FRONT_RE2": ([(p0 * 0.99, .50), (h5 * .935, .30), (h10 * .875, .20)], "re2"),
        "LAD_PRICE": ([(p0 * 0.99, .35), (p0 * .95, .35), (p0 * .90, .30)], False),
        "LAD_2RUNG": ([(p0 * 0.99, .50), (h5 * .93, .50)], False),
        "DIP5":      ([(p0 * 0.95, 1.0)], False),
        "DIP12":     ([(p0 * 0.88, 1.0)], False),
    }


def main():
    agg = defaultdict(lambda: defaultdict(list))   # regime -> strat -> list of dict
    per_ticker = defaultdict(lambda: defaultdict(list))  # (regime,strat) -> tk -> ret
    counts = defaultdict(int)
    for tk in available_tickers():
        bars = load(tk)
        if len(bars) < 170:
            continue
        n = len(bars)
        for d in range(126, n - T1 - 1):
            reg = regime_of(bars, d)
            if reg is None:
                continue
            counts[reg] += 1
            base = strategies(bars, d)
            t1 = d + T1
            t2 = d + T2 if d + T2 < n else None
            allin_ret = None
            res = {}
            for name, (rungs, re_) in base.items():
                if name == "ALLIN":
                    dep, cost, ff = 1.0, bars[d + 1][1], d + 1
                else:
                    dep, cost, ff = sim_fills(bars, d, rungs, reanchor=re_)
                if dep > 0:
                    r1 = dep * (bars[t1][4] / cost - 1)
                    mdd = min(b[3] for b in bars[ff: t1 + 1]) / cost - 1
                    r2 = dep * (bars[t2][4] / cost - 1) if t2 else None
                else:
                    r1, mdd, r2 = 0.0, 0.0, 0.0 if t2 else None
                res[name] = {"dep": dep, "ret1": r1, "ret2": r2, "mdd": mdd,
                             "miss": 1 if dep == 0 else 0}
                if name == "ALLIN":
                    allin_ret = r1
            for name, v in res.items():
                v["edge"] = v["ret1"] - allin_ret
                agg[reg][name].append(v)
                per_ticker[(reg, name)][tk].append(v["ret1"])

    def fmt(x, pct=True):
        if x is None:
            return "  n/a"
        return f"{x * 100:+6.2f}%" if pct else f"{x:6.2f}"

    for reg in ["parabolic", "strong_up", "other"]:
        rows = agg[reg]
        if not rows:
            continue
        n = len(rows["ALLIN"])
        print(f"\n=== regime: {reg}  (samples {n}, ticker-days) ===")
        print(f"{'strategy':<10} {'dep%':>6} {'miss%':>6} {'meanR20':>8} {'medR20':>8} "
              f"{'p10':>8} {'meanR40':>8} {'winVsAllin':>10} {'meanMDD':>8}")
        for name in ["ALLIN", "LADDER", "LADDER_RE", "FRONT_RE2", "LAD_PRICE",
                     "LAD_2RUNG", "DIP5", "DIP12"]:
            vs = rows[name]
            dep = st.mean(v["dep"] for v in vs)
            miss = st.mean(v["miss"] for v in vs)
            r1 = [v["ret1"] for v in vs]
            r2 = [v["ret2"] for v in vs if v["ret2"] is not None]
            win = st.mean(1 if v["edge"] > 0 else 0 for v in vs)
            mdd = st.mean(v["mdd"] for v in vs)
            p10 = sorted(r1)[max(0, int(len(r1) * 0.10) - 1)]
            print(f"{name:<10} {dep*100:5.0f}% {miss*100:5.0f}% {fmt(st.mean(r1)):>8} "
                  f"{fmt(st.median(r1)):>8} {fmt(p10):>8} "
                  f"{fmt(st.mean(r2) if r2 else None):>8} {win*100:9.0f}% {fmt(mdd):>8}")

    # parabolic per-ticker sanity (防单票主导)
    print("\n=== parabolic per-ticker medianR20 (LADDER_RE vs ALLIN vs DIP5) ===")
    tks = sorted(per_ticker[("parabolic", "ALLIN")].keys())
    for tk in tks:
        a = per_ticker[("parabolic", "ALLIN")][tk]
        l = per_ticker[("parabolic", "LADDER_RE")][tk]
        d5 = per_ticker[("parabolic", "DIP5")][tk]
        print(f"{tk:<6} n={len(a):<4} ALLIN {st.median(a)*100:+7.2f}%  "
              f"LADDER_RE {st.median(l)*100:+7.2f}%  DIP5 {st.median(d5)*100:+7.2f}%")


if __name__ == "__main__":
    main()
