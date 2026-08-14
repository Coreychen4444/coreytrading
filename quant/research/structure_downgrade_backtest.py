#!/usr/bin/env python3
"""Structure downgrade backtest — entry_guard 分类降级判别式该多宽?

现状 (entry_guard.py): `if trend == "down" and not repaired: → probe`，只认 LH+LL。
候选扩展还包括 mixed transition，以及 trend="up" 但收盘低于最近 swing low 的状态。

问题: 这两个状态的前向收益, 更像 clean up 还是更像 down?
      像 down → 降级有据; 像 up → 现状是对的, 别改。

方法 (复刻 entry_ladder_backtest 的采样口径):
  每个 (ticker, day) 用**截至当日**的 K 线算 swing_structure (无未来函数),
  分桶后测 T+10/20/40 的前向收盘收益。
  两个样本池:
    ALL       所有交易日 —— 看判别式本身的分辨力
    ZONE_PROXY close ∈ [0.80, 0.98] × trailing-20d high —— 逼近 entry_guard
              真正做分类的population (价格在 accumulation zone 内, 非追高)。
              真实 zone 是逐卡手画且历史不可重建, 这是可得的最近代理。

CLI: .venv/moomoo/bin/python3 quant/research/structure_downgrade_backtest.py [--json]
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 直接 `python3 quant/<层>/<模块>.py` 时让 `quant.*` 可 import

import argparse
import json
import statistics as st
from collections import defaultdict

from quant.core import technical  # noqa: E402
from quant.core.paths import KLINE_CACHE_DIR as KDIR  # noqa: E402
HORIZONS = (10, 20, 40)
WARMUP = 60          # 结构标签需要足够左侧历史
ZONE_LO, ZONE_HI = 0.80, 0.98   # zone proxy: 距 20d high 的回撤带
HIGH_LOOKBACK = 20


def available_tickers() -> list[str]:
    """Discover the private study universe without embedding it in public source."""
    return sorted(path.stem for path in KDIR.glob("*.jsonl") if path.is_file())


def load(tk):
    p = KDIR / f"{tk}.jsonl"
    if not p.exists():
        return []
    out = []
    for line in p.read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            out.append({"date": r["time"][:10], "open": r["open"], "high": r["high"],
                        "low": r["low"], "close": r["close"]})
    return out


def bucket_of(s):
    """把结构状态分成 5 桶。命名对应 entry_guard 的决策语言。"""
    trend = s.get("trend")
    below = bool(s.get("close_below_last_swing_low"))
    if trend == "insufficient":
        return None
    if trend == "down":
        return "down (LH+LL) ←现状已降级"
    if trend == "transition":
        return "transition (混合)"
    if trend == "up":
        return "up 但收盘<最近swing低" if below else "up 干净 (HH+HL)"
    return "sideways/其他"


def describe(vals):
    if not vals:
        return None
    vals = sorted(vals)
    n = len(vals)
    return {
        "n": n,
        "mean": round(st.mean(vals), 2),
        "median": round(st.median(vals), 2),
        "p10": round(vals[int(n * 0.10)], 2),
        "p90": round(vals[min(n - 1, int(n * 0.90))], 2),
        "win_rate": round(sum(1 for v in vals if v > 0) / n * 100, 1),
    }


def run():
    # pools[pool][horizon][bucket] = [forward return %]
    pools = {p: {h: defaultdict(list) for h in HORIZONS} for p in ("ALL", "ZONE_PROXY")}
    per_ticker_days = {}

    for tk in available_tickers():
        bars = load(tk)
        if len(bars) < WARMUP + max(HORIZONS) + 1:
            continue
        used = 0
        for i in range(WARMUP, len(bars) - max(HORIZONS)):
            hist = bars[:i + 1]                       # 截至当日, 无未来函数
            s = technical.swing_structure(hist)
            b = bucket_of(s)
            if b is None:
                continue
            c0 = bars[i]["close"]
            if not c0:
                continue
            hi20 = max(x["high"] for x in bars[max(0, i - HIGH_LOOKBACK + 1):i + 1])
            in_zone = hi20 > 0 and ZONE_LO <= c0 / hi20 <= ZONE_HI
            used += 1
            for h in HORIZONS:
                ret = (bars[i + h]["close"] / c0 - 1) * 100
                pools["ALL"][h][b].append(ret)
                if in_zone:
                    pools["ZONE_PROXY"][h][b].append(ret)
        per_ticker_days[tk] = used

    return pools, per_ticker_days


ORDER = ["up 干净 (HH+HL)", "up 但收盘<最近swing低", "transition (混合)",
         "sideways/其他", "down (LH+LL) ←现状已降级"]
BASE = "up 干净 (HH+HL)"
DOWN = "down (LH+LL) ←现状已降级"


def _wr(v):
    return sum(1 for x in v if x > 0) / len(v) if v else 0.0


def wr_diff_sigma(a, b):
    """胜率差 / 合并标准误 —— |z| ≥ 2 视为可区分。"""
    if not a or not b:
        return None
    pa, pb = _wr(a), _wr(b)
    se = (pa * (1 - pa) / len(a) + pb * (1 - pb) / len(b)) **0.5
    return (pa - pb) / se if se > 0 else None


def median_diff_ci(a, b, iters=2000, seed=399_989):
    """中位数差的 bootstrap 95% CI (自带 LCG, 不用 random 以便可复现)。"""
    if not a or not b:
        return None
    state = seed
    na, nb = len(a), len(b)
    diffs = []
    for _ in range(iters):
        sa, sb = [], []
        for _ in range(na):
            state = (1103515245 * state + 12345) % (2 **31)
            sa.append(a[state % na])
        for _ in range(nb):
            state = (1103515245 * state + 12345) % (2 **31)
            sb.append(b[state % nb])
        diffs.append(st.median(sa) - st.median(sb))
    diffs.sort()
    return round(diffs[int(iters * 0.025)], 2), round(diffs[int(iters * 0.975)], 2)


def print_significance(pools, pool="ZONE_PROXY", h=20):
    data = pools[pool][h]
    base, down = data.get(BASE, []), data.get(DOWN, [])
    print(f"{'=' * 92}\n### 显著性 ({pool}, T+{h}) — 每桶分别对比 [up 干净] 和 [down]\n{'=' * 92}")
    print(f"{'结构桶':<34} {'vs up: Δ胜率σ':>14} {'中位差 95%CI':>22} "
          f"{'vs down: Δ胜率σ':>16} {'中位差 95%CI':>22}")
    for b in ORDER:
        v = data.get(b, [])
        if not v or b == BASE:
            continue
        zu, zd = wr_diff_sigma(v, base), wr_diff_sigma(v, down)
        cu, cd = median_diff_ci(v, base), median_diff_ci(v, down)
        f = lambda c: f"[{c[0]:+.2f}, {c[1]:+.2f}]" if c else "—"      # noqa: E731
        g = lambda z: f"{z:+.2f}σ" if z is not None else "—"           # noqa: E731
        print(f"{b:<34} {g(zu):>14} {f(cu):>22} {g(zd):>16} {f(cd):>22}")
    print("\n判读: CI 含 0 = 与该基准不可区分。降级的依据 = 「与 up 可区分」且「与 down 不可区分」。\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    pools, days = run()

    if args.json:
        out = {p: {str(h): {b: describe(v) for b, v in pools[p][h].items()}
                   for h in HORIZONS} for p in pools}
        out["ticker_days"] = days
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0

    print(f"样本: {len(days)} 票, {sum(days.values()):,} ticker-day "
          f"(warmup {WARMUP}d, horizons {HORIZONS})")
    print(f"ZONE_PROXY = close ∈ [{ZONE_LO}, {ZONE_HI}] × trailing-{HIGH_LOOKBACK}d high\n")
    for pool in ("ALL", "ZONE_PROXY"):
        print(f"{'=' * 92}\n### 样本池 {pool}\n{'=' * 92}")
        for h in HORIZONS:
            print(f"\n-- 前向 T+{h} 日收益 % --")
            print(f"{'结构桶':<34} {'n':>6} {'均值':>7} {'中位':>7} "
                  f"{'p10':>7} {'p90':>7} {'胜率%':>7}")
            for b in ORDER:
                d = describe(pools[pool][h].get(b, []))
                if not d:
                    continue
                print(f"{b:<34} {d['n']:>6} {d['mean']:>7.2f} {d['median']:>7.2f} "
                      f"{d['p10']:>7.2f} {d['p90']:>7.2f} {d['win_rate']:>7.1f}")
        print()
    print_significance(pools, "ZONE_PROXY", 20)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
