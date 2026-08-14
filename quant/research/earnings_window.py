#!/usr/bin/env python3
"""财报窗口缩仓规则 — 主回测 (预注册 private preregistration, 已锁定)。

## 窗口已由数据定 (§0, 不是本轮假设)
T−5~T−1 与基线无区别 (0.99–1.05x); **风险全部集中在反应日 T+0**(3.51x, >2N 占 36.2%);
T+1 残留 1.29x; T+2 归零。→ 缩仓窗口 = **T+0 单日**, 前一收盘减完、T+1 起恢复全额
(旧文「T+1 收盘后恢复」与 T+0 单日自相矛盾,  V4 案更正; live 实现即 T+0 单日)。

## 缩仓公式 (§2)
要求"财报日的单笔风险占用 ≤ 它在普通日的占用":
    w_earn = w_normal × (X_normal / X_earn),  X_normal = 普通日 p98 ≈ 1.94N

## 四臂 (§3, 锁定)
D 不缩仓 (对照) / A 池级 p90 / B 单票 p98 / C 收缩估计 λ=n/(n+10)

## 样本外 (§4)
测第 k 个事件时 X_earn 只由第 1..k−1 个估计; 先验 <8 个 → 走 A 臂回退 (规则的一部分)。

## 判据 (§5)
1 超限频率 ∈ [1%, 4%]  (低于 1% = 缩过头, 也是失败)
2 显著低于 D 臂 (bootstrap CI90 排除 0)
3 保费: 放弃的收益 ≤ 避免的损失
4 Kish ≥ 30
全过者取**平均缩仓幅度最小**者。无一通过 → 40% 名义上限须下调 (§7)。

Usage: .venv/moomoo/bin/python3 quant/research/earnings_window.py [--json] [--report PATH]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import argparse  # noqa: E402
import json  # noqa: E402
import random  # noqa: E402
import statistics as st  # noqa: E402
from collections import Counter  # noqa: E402

from quant.core.paths import KLINE_DEEP_DIR as KDIR  # noqa: E402
from quant.research.gate_backtest import load  # noqa: E402
from quant.research.gate_backtest2 import atr_series  # noqa: E402

EARNINGS_DIR = Path("trade/us_stock/market/earnings_deep")
X_NORMAL = 1.94            # 普通日 p98 (shock_budget §2.3)
POOL_Q = 0.90              # A 臂: 池级 p90
TICKER_Q = 0.98            # B 臂: 单票 p98
SHRINK_N0 = 10             # C 臂: λ = n/(n+n0)
MIN_PRIOR = 8              # §4: 先验事件 <8 → 回退 A 臂
BREACH_BAND = (0.01, 0.04)  # §5-1
KISH_MIN = 30
ARMS = ("D_none", "A_pool", "B_ticker", "C_shrink")
BOOT = 2000
SEED = 331_777


def earnings_days(tk):
    """{反应日} —— `trading_day_str` 已是市场反应日 (AMC 的次日)。"""
    p = EARNINGS_DIR / f"{tk}.json"
    if not p.exists():
        return set()
    try:
        d = json.load(open(p))
    except (json.JSONDecodeError, OSError):
        return set()
    return {r.get("trading_day_str") for r in (d.get("data") or [])
            if r.get("trading_day_str")}


def collect():
    """每票按时间排序的财报事件: (日期, 跌幅N, 涨幅N)。跌为正。"""
    out = {}
    for path in sorted(KDIR.glob("*.jsonl")):
        tk = path.stem
        ev = earnings_days(tk)
        if not ev:
            continue
        bars = load(path)
        if len(bars) < 300:
            continue
        cl = [b["close"] for b in bars]
        hi = [b["high"] for b in bars]
        lo = [b["low"] for b in bars]
        a = atr_series(hi, lo, cl, 20)
        idx = {str(b["time"])[:10]: i for i, b in enumerate(bars)}
        rows = []
        for d in sorted(ev):
            i = idx.get(d)
            if not i or not a[i - 1]:
                continue
            move = (cl[i] - cl[i - 1]) / a[i - 1]
            rows.append({"date": d, "down": -move, "up": max(move, 0.0),
                         "month": d[:7]})
        if len(rows) >= 2:
            out[tk] = rows
    return out


def pool_quantile(vals, q):
    v = sorted(vals)
    return v[min(len(v) - 1, int(len(v) * q))] if v else None


def estimate(arm, prior_downs, pool_x):
    """X_earn 的估计。prior_downs = 该票**此前**的财报日跌幅 (N)。"""
    n = len(prior_downs)
    if arm == "D_none":
        return X_NORMAL                       # 不缩仓
    if arm == "A_pool" or n < MIN_PRIOR:      # §4 回退: 先验不足一律走池级
        return pool_x
    tq = pool_quantile(prior_downs, TICKER_Q)
    if arm == "B_ticker":
        return tq
    lam = n / (n + SHRINK_N0)                 # C 臂
    return lam * tq + (1 - lam) * pool_x


def run():
    data = collect()
    # 池级 X: 只用**全部**事件的 p90 —— 池级量在时间上近似稳定, 且它是回退项;
    # 逐事件重估池级会让 4 臂共享一个抖动的分母, 掩盖臂间差异 (运行前记录此选择)
    all_downs = [r["down"] for rows in data.values() for r in rows]
    pool_x = pool_quantile(all_downs, POOL_Q)

    recs = []
    fallback = 0
    for tk, rows in data.items():
        for k, r in enumerate(rows):
            prior = [x["down"] for x in rows[:k]]
            cell = {"tk": tk, "date": r["date"], "month": r["month"],
                    "down": r["down"], "up": r["up"]}
            if len(prior) < MIN_PRIOR:
                fallback += 1
            for arm in ARMS:
                xe = estimate(arm, prior, pool_x)
                shrink = min(1.0, X_NORMAL / xe) if xe and xe > 0 else 1.0
                # 该仓位在普通日的预算占用 = 1 单位 (归一); 财报日实际占用 =
                # shrink × 实际跌幅 / X_normal。>1 即超限。
                cell[arm] = {
                    "shrink": shrink,
                    "over": (shrink * r["down"] / X_NORMAL) > 1.0,
                    # 保费两侧: 少亏的 与 少赚的 (都以"普通日预算"为单位)
                    "saved": max(r["down"], 0.0) * (1 - shrink) / X_NORMAL,
                    "given_up": r["up"] * (1 - shrink) / X_NORMAL,
                }
            recs.append(cell)
    return recs, pool_x, fallback, data


def _ci_diff(recs, arm, base="D_none", draws=BOOT, seed=SEED):
    """(arm 超限率 − base 超限率) 的 CI90, 按月整块重抽。"""
    by = {}
    for r in recs:
        by.setdefault(r["month"], []).append(r)
    months = sorted(by)
    rng = random.Random(seed)
    vals = []
    for _ in range(draws):
        pick = [by[months[rng.randrange(len(months))]] for _ in months]
        flat = [x for g in pick for x in g]
        if not flat:
            continue
        a = sum(x[arm]["over"] for x in flat) / len(flat)
        b = sum(x[base]["over"] for x in flat) / len(flat)
        vals.append(a - b)
    vals.sort()
    return [round(vals[int(len(vals) * 0.05)], 5),
            round(vals[min(len(vals) - 1, int(len(vals) * 0.95))], 5)]


def summarize(recs, pool_x, fallback):
    n = len(recs)
    mc = Counter(r["month"] for r in recs)
    kish = round(n **2 / sum(v * v for v in mc.values()), 1)
    out = {"n_events": n, "n_tickers": len({r["tk"] for r in recs}),
           "kish": kish, "pool_x": round(pool_x, 3),
           "fallback_pct": round(fallback / n, 4), "arms": {}}
    for arm in ARMS:
        over = sum(r[arm]["over"] for r in recs) / n
        row = {
            "breach_rate": round(over, 5),
            "shrink_mean": round(st.mean(r[arm]["shrink"] for r in recs), 4),
            "saved": round(sum(r[arm]["saved"] for r in recs) / n, 5),
            "given_up": round(sum(r[arm]["given_up"] for r in recs) / n, 5),
        }
        row["premium_ratio"] = (round(row["given_up"] / row["saved"], 3)
                                if row["saved"] > 0 else None)
        row["vs_none_ci90"] = None if arm == "D_none" else _ci_diff(recs, arm)
        c1 = bool(BREACH_BAND[0] <= over <= BREACH_BAND[1])
        c2 = bool(arm != "D_none" and row["vs_none_ci90"] and row["vs_none_ci90"][1] < 0)
        c3 = bool(row["premium_ratio"] is not None and row["premium_ratio"] <= 1.0)
        c4 = bool(kish >= KISH_MIN)
        row["crit"] = {"c1_band": c1, "c2_beats_none": c2, "c3_premium": c3,
                       "c4_kish": c4, "all_pass": bool(c1 and c2 and c3 and c4)}
        out["arms"][arm] = row
    winners = [a for a in ARMS if a != "D_none" and out["arms"][a]["crit"]["all_pass"]]
    if winners:
        pick = min(winners, key=lambda a: 1 - out["arms"][a]["shrink_mean"])
        out["verdict"] = {"adopt": True, "arm": pick,
                          "text": f"VERDICT: **采纳 {pick}**(全过者中平均缩仓幅度最小 = "
                                  f"{(1 - out['arms'][pick]['shrink_mean']) * 100:.1f}%)"}
    else:
        out["verdict"] = {"adopt": False, "arm": None,
                          "text": "VERDICT: **无一臂通过 → 不采纳**(预注册 §7: "
                                  "40% 名义上限须下调, 或明写接受财报日超预算 —— "
                                  "不允许挂着一条测不过的规则继续用 40%)"}
    return out


def book_table(recs, data, pool_x, arm):
    """私有输入池各标的在选定臂下的缩仓比例。"""
    out = {}
    for tk in sorted(data):
        rows = data.get(tk)
        if not rows:
            out[tk] = None
            continue
        downs = [r["down"] for r in rows]
        xe = estimate(arm, downs, pool_x)
        out[tk] = {"n": len(downs), "x_earn": round(xe, 2),
                   "shrink": round(min(1.0, X_NORMAL / xe), 3)}
    return out


def render(s, book, arm_for_book):
    L = [f"=== 财报窗口缩仓规则 === {s['n_events']:,} 事件 / {s['n_tickers']} 票 / "
         f"Kish≈{s['kish']}",
         f"窗口 = 反应日 T+0 单日 (已由数据定, 非本轮假设); X_normal = {X_NORMAL}N; "
         f"池级 p90 = {s['pool_x']}N",
         f"先验 <{MIN_PRIOR} 个事件而走 A 臂回退的比例: {s['fallback_pct']:.1%}", "",
         f"{'臂':<12}{'超限率':>9}{'平均缩仓':>10}{'少亏':>9}{'少赚':>9}{'保费比':>8}"
         f"{'vs 不缩仓 CI90':>20}{'判据':>18}"]
    names = {"D_none": "D 不缩仓", "A_pool": "A 池级p90",
             "B_ticker": "B 单票p98", "C_shrink": "C 收缩"}
    for arm in ARMS:
        r = s["arms"][arm]
        c = r["crit"]
        flags = ("①" + ("✓" if c["c1_band"] else "✗")
                 + " ②" + ("✓" if c["c2_beats_none"] else "✗")
                 + " ③" + ("✓" if c["c3_premium"] else "✗"))
        L.append(f"{names[arm]:<12}{r['breach_rate'] * 100:>8.2f}%"
                 f"{(1 - r['shrink_mean']) * 100:>9.1f}%"
                 f"{r['saved']:>9.3f}{r['given_up']:>9.3f}"
                 f"{(r['premium_ratio'] if r['premium_ratio'] is not None else float('nan')):>8.2f}"
                 f"{(str(r['vs_none_ci90']) if r['vs_none_ci90'] else '—'):>20}"
                 f"{flags:>18}")
    L += ["", f"判据①带 = [{BREACH_BAND[0]:.0%}, {BREACH_BAND[1]:.0%}] "
              f"(低于下界 = 缩过头, 也算失败); ②显著低于不缩仓; ③少赚 ≤ 少亏", ""]
    L.append(s["verdict"]["text"])
    if book:
        L += ["", f"私有输入池各标的缩仓比例 (臂 = {arm_for_book}, 用全部历史估计):",
              f"  {'标的':<7}{'事件数':>6}{'X_earn':>9}{'缩到':>8}"]
        for tk, v in book.items():
            if v is None:
                L.append(f"  {tk:<7}{'—':>6}   (无财报数据 → 规则回退池级 p90)")
            else:
                L.append(f"  {tk:<7}{v['n']:>6}{v['x_earn']:>8.2f}N{v['shrink'] * 100:>7.0f}%")
    return "\n".join(L)


def main_cli():
    ap = argparse.ArgumentParser(description="财报窗口缩仓规则回测")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--report", type=Path, default=None)
    a = ap.parse_args()
    recs, pool_x, fallback, data = run()
    if not recs:
        print("无样本")
        return 1
    s = summarize(recs, pool_x, fallback)
    arm = s["verdict"]["arm"] or "C_shrink"
    book = book_table(recs, data, pool_x, arm)
    text = render(s, book, arm)
    if a.json:
        print(json.dumps({"summary": s, "book": book}, ensure_ascii=False,
                         indent=1, default=float))
    else:
        print(text)
    if a.report:
        a.report.parent.mkdir(parents=True, exist_ok=True)
        a.report.write_text("# 财报窗口缩仓规则 (机器输出存档)\n\n```\n" + text + "\n```\n")
    return 0


if __name__ == "__main__":
    sys.exit(main_cli())
