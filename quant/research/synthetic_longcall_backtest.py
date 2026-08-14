#!/usr/bin/env python3
"""synthetic_longcall_backtest.py — 合成 long call 大样本回测

预注册: private preregistration (判据 A1-A3/B1 先锁)。
数据由 ignored private deep-cache 与显式窗口提供；BS 合成 premium
(HV20×1.1 锚 IV, 主臂持有期 IV 锁定)。信号 = MA20>MA50 ∧ close>MA20 ∧
40 日新高, 边沿触发, 同票冷却 20 日。
两臂共有基线 = 棘轮(+50% 保本/+100% 回吐 1/3) + 趋势死亡线(3×ATR20) + DTE≤7 强平。

research 旁路: 无人 import 本模块。
"""
from __future__ import annotations

import glob
import argparse
import json
import math
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

DEEP_DIR = Path(__file__).resolve().parents[2] / "trade/us_stock/market/klines_deep"
REPORT = Path(__file__).resolve().parents[2] / "tmp/backtest/synthetic_longcall_report.md"
R = 0.045
IV_PREM = 1.10          # volatility risk premium 常数 (§2)
COOLDOWN = 20
WHIPSAW_DAYS = 60


def _ncdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_call(s, k, tau, iv, r=R):
    if tau <= 0 or iv <= 0:
        return max(0.0, s - k)
    sq = iv * math.sqrt(tau)
    d1 = (math.log(s / k) + (r + 0.5 * iv * iv) * tau) / sq
    return s * _ncdf(d1) - k * math.exp(-r * tau) * _ncdf(d1 - sq)


def bs_delta(s, k, tau, iv, r=R):
    if tau <= 0 or iv <= 0:
        return 1.0 if s > k else 0.0
    sq = iv * math.sqrt(tau)
    return _ncdf((math.log(s / k) + (r + 0.5 * iv * iv) * tau) / sq)


def strike_for_delta(s, target_delta, tau, iv):
    lo, hi = s * 0.3, s * 3.0
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if bs_delta(s, mid, tau, iv) > target_delta:
            lo = mid          # delta 太高 → strike 上移
        else:
            hi = mid
    return 0.5 * (lo + hi)


def load(und: str):
    rows = [json.loads(l) for l in open(DEEP_DIR / f"{und}.jsonl")]
    for r_ in rows:
        r_["date"] = (r_.get("time") or r_.get("date"))[:10]
    rows.sort(key=lambda r_: r_["date"])
    return rows


def precompute(rows):
    """MA20/MA50/HV20/ATR20/40 日新高, 一次算好。"""
    closes = [r_["close"] for r_ in rows]
    n = len(rows)
    ma20 = [None] * n
    ma50 = [None] * n
    hv20 = [None] * n
    atr20 = [None] * n
    hi40 = [None] * n
    s20 = s50 = 0.0
    logs = [0.0] * n
    trs = [0.0] * n
    for i in range(n):
        s20 += closes[i]
        s50 += closes[i]
        if i >= 20:
            s20 -= closes[i - 20]
        if i >= 50:
            s50 -= closes[i - 50]
        if i >= 19:
            ma20[i] = s20 / 20
        if i >= 49:
            ma50[i] = s50 / 50
        if i > 0:
            logs[i] = math.log(closes[i] / closes[i - 1]) if closes[i - 1] > 0 else 0.0
            trs[i] = max(rows[i]["high"] - rows[i]["low"],
                         abs(rows[i]["high"] - closes[i - 1]),
                         abs(rows[i]["low"] - closes[i - 1]))
        if i >= 20:
            seg = logs[i - 19:i + 1]
            m = sum(seg) / 20
            hv20[i] = math.sqrt(sum((x - m) **2 for x in seg) / 19) * math.sqrt(252)
        if i >= 40:
            hi40[i] = max(closes[i - 40:i])   # 不含当日
    # ATR20 Wilder
    if n > 21:
        a = sum(trs[1:21]) / 20
        atr20[20] = a
        for i in range(21, n):
            a = (a * 19 + trs[i]) / 20
            atr20[i] = a
    return {"ma20": ma20, "ma50": ma50, "hv20": hv20, "atr20": atr20, "hi40": hi40}


def signals(rows, ind, start, end):
    """边沿触发信号日 index 列表 (§2), 窗口内, 冷却 20 日。"""
    out, last = [], -10**9
    for i in range(50, len(rows)):
        if rows[i]["date"] < start or rows[i]["date"] > end:
            continue
        c = rows[i]["close"]
        ok = (ind["ma20"][i] and ind["ma50"][i] and ind["hi40"][i]
              and ind["hv20"][i] and ind["atr20"][i]
              and ind["ma20"][i] > ind["ma50"][i] and c > ind["ma20"][i]
              and c > ind["hi40"][i])
        prev_ok = False
        if i > 0 and ind["ma20"][i-1] and ind["ma50"][i-1] and ind["hi40"][i-1]:
            pc = rows[i-1]["close"]
            prev_ok = (ind["ma20"][i-1] > ind["ma50"][i-1] and pc > ind["ma20"][i-1]
                       and pc > ind["hi40"][i-1])
        if ok and not prev_ok and i - last >= COOLDOWN:
            out.append(i)
            last = i
    return out


def simulate(rows, ind, i0, entry_dte, delta_target,
             s1=None, s2=None, s4=None):
    """单 campaign。返回 dict(pnl_pct, exit_reason, days_held, whipsaw)。"""
    c0 = rows[i0]["close"]
    iv = ind["hv20"][i0] * IV_PREM
    atr0 = ind["atr20"][i0]
    tau0 = entry_dte / 365.0
    k = strike_for_delta(c0, delta_target, tau0, iv)
    cost = bs_call(c0, k, tau0, iv)
    if cost <= 0:
        return None

    def pm(i, use_open=False):
        px = rows[i]["open"] if use_open else rows[i]["close"]
        dte_d = entry_dte - (i - i0)          # 交易日≈日历日近似, 臂间无偏 (§4.4)
        return bs_call(px, k, max(dte_d, 0) / 365.0, iv)

    hwm_pm = cost
    hwm_close = c0
    armed_50 = armed_100 = False
    half_pnl, half_done, frac = 0.0, False, 1.0

    def close_out(i, reason, half_ok=True):
        px = pm(i + 1, use_open=True) if i + 1 < len(rows) else pm(i)
        p = px / cost - 1.0
        total = (0.5 * half_pnl + 0.5 * p) if (half_done and half_ok) else p
        # whipsaw: S1 触发后 60 交易日内 premium 收复成本 (§2)
        wh = False
        if reason.startswith("S1"):
            for j in range(i + 1, min(i + 1 + WHIPSAW_DAYS, len(rows))):
                if pm(j) >= cost:
                    wh = True
                    break
        return {"pnl_pct": total, "exit": reason, "days": i - i0, "whipsaw": wh}

    n = len(rows)
    for i in range(i0 + 1, n):
        p = pm(i)
        c = rows[i]["close"]
        dte_d = entry_dte - (i - i0)
        hwm_pm = max(hwm_pm, p)
        hwm_close = max(hwm_close, c)
        gain_pk = hwm_pm / cost - 1.0
        if gain_pk >= 1.0:
            armed_100 = True
        if gain_pk >= 0.5:
            armed_50 = True
        # --- 共有基线 ---
        if dte_d <= 7:
            return close_out(i, "到期强平")
        if armed_100 and (hwm_pm - p) >= (hwm_pm - cost) / 3.0:
            return close_out(i, "棘轮回吐1/3")
        if armed_50 and not armed_100 and p <= cost:
            return close_out(i, "棘轮保本")
        if atr0 and c < hwm_close - 3.0 * ind["atr20"][i] if ind["atr20"][i] else False:
            return close_out(i, "趋势死亡线")
        # --- overlay ---
        if s1 is not None and p <= cost * (1 + s1):
            return close_out(i, f"S1{s1:+.0%}")
        if s4 is not None and entry_dte > s4 and dte_d <= s4:
            return close_out(i, f"S4≤{s4}")
        if s2 is not None:
            t1, t2 = s2
            flat = c < c0 + (atr0 or 0.0)
            if i - i0 >= t2 and flat:
                return close_out(i, f"S2清仓d{t2}")
            if i - i0 >= t1 and flat and not half_done:
                if i + 1 < n:
                    half_pnl = pm(i + 1, use_open=True) / cost - 1.0
                    half_done, frac = True, 0.5
    return close_out(n - 1, "数据尾截断")


ARMS_A = [("base", {})]
ARMS_A += [(f"+S1 {s:+.0%}", {"s1": s}) for s in (-0.20, -0.25, -0.30, -0.35, -0.40)]
ARMS_A += [("+S2 (20,30)", {"s2": (20, 30)}),
           ("+S4 90", {"s4": 90}), ("+S4 120", {"s4": 120}),
           ("组合 S1-30+S2+S4-90", {"s1": -0.30, "s2": (20, 30), "s4": 90})]


def cluster_boot(vals_by_ticker, stat, n_boot=1000, seed=7):
    """按票 cluster bootstrap, 返回 (lo90, hi90)。"""
    import random
    rng = random.Random(seed)
    keys = list(vals_by_ticker)
    outs = []
    for _ in range(n_boot):
        pool = []
        for _ in keys:
            pool.extend(vals_by_ticker[rng.choice(keys)])
        if pool:
            outs.append(stat(pool))
    outs.sort()
    return outs[int(0.05 * len(outs))], outs[int(0.95 * len(outs))]


def p10(v):
    s = sorted(v)
    return s[max(0, int(0.10 * len(s)) - (1 if len(s) % 10 == 0 else 0))] if s else 0.0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run a synthetic long-call private study")
    parser.add_argument("--start", required=True, help="locked study start session")
    parser.add_argument("--end", required=True, help="locked study end session")
    parser.add_argument("--report", type=Path, default=REPORT)
    args = parser.parse_args(argv)
    tickers = sorted(p.stem for p in DEEP_DIR.glob("*.jsonl"))
    data = {}
    for tk in tickers:
        try:
            rows = load(tk)
        except Exception:
            continue
        if len(rows) < 120:
            continue
        ind = precompute(rows)
        sig = signals(rows, ind, args.start, args.end)
        if sig:
            data[tk] = (rows, ind, sig)
    n_camp = sum(len(s) for _, _, s in data.values())
    lines = ["# synthetic_longcall 回测报告", "",
             f"预注册: private preregistration | 池 {len(data)} 票, "
             f"campaigns {n_camp} (窗 {args.start} → {args.end})", ""]

    def run(entry_dte, delta_t, kw):
        by_tk, allv, exits, whip_hits, s1_fires = {}, [], {}, 0, 0
        for tk, (rows, ind, sig) in data.items():
            for i0 in sig:
                r_ = simulate(rows, ind, i0, entry_dte, delta_t, **kw)
                if r_ is None:
                    continue
                by_tk.setdefault(tk, []).append(r_["pnl_pct"])
                allv.append(r_["pnl_pct"])
                exits[r_["exit"]] = exits.get(r_["exit"], 0) + 1
                if r_["exit"].startswith("S1"):
                    s1_fires += 1
                    if r_["whipsaw"]:
                        whip_hits += 1
        return {"by_tk": by_tk, "all": allv, "exits": exits,
                "mean": statistics.mean(allv) if allv else 0.0,
                "med": statistics.median(allv) if allv else 0.0,
                "p10": p10(allv), "n": len(allv),
                "s1_fires": s1_fires,
                "whipsaw": (whip_hits / s1_fires) if s1_fires else None}

    # ---- A 组: 退出栈 @ DTE 270, Δ0.70 ----
    lines += ["## A 组: 退出栈 @ 入场 DTE=270, Δ=0.70", "",
              "| 臂 | n | mean | 中位 | p10 | S1触发数 | whipsaw率 | 退出分布 top3 |",
              "|---|---|---|---|---|---|---|---|"]
    resA = {}
    for name, kw in ARMS_A:
        v = run(270, 0.70, kw)
        resA[name] = v
        top = sorted(v["exits"].items(), key=lambda x: -x[1])[:3]
        wh = f"{v['whipsaw']:.0%}" if v["whipsaw"] is not None else "—"
        lines.append(f"| {name} | {v['n']} | {v['mean']:+.1%} | {v['med']:+.1%} "
                     f"| {v['p10']:+.1%} | {v['s1_fires']} | {wh} "
                     f"| {', '.join(f'{k_} {c}' for k_, c in top)} |")

    base = resA["base"]
    combo = resA["组合 S1-30+S2+S4-90"]
    # 判据 A1/A2 + cluster bootstrap
    d_p10 = combo["p10"] - base["p10"]
    d_mean = combo["mean"] - base["mean"]
    lo_m, hi_m = cluster_boot(
        {tk: [a - b for a, b in zip(combo["by_tk"].get(tk, []), base["by_tk"].get(tk, []))]
         for tk in base["by_tk"] if tk in combo["by_tk"]},
        lambda v: statistics.mean(v))
    a1 = d_p10 >= 0.05
    a2 = d_mean >= -0.02
    lines += ["", f"**A1 尾部**: Δp10 = {d_p10:+.1%} (判据 ≥+5pp) → {'✓' if a1 else '✗'}",
              f"**A2 均值代价**: Δmean = {d_mean:+.1%} (判据 ≥−2pp), "
              f"逐笔差 cluster CI90 [{lo_m:+.2%}, {hi_m:+.2%}] → {'✓' if a2 else '✗'}"]
    # A3: S1 档位
    lines += ["", "**A3 S1 档位选择**(加该档 vs base, 判据: p10 改善 ≥+5pp ∧ mean 代价 ≤2pp, 取 whipsaw 最低, 平局取紧):", ""]
    qual = []
    for s in (-0.20, -0.25, -0.30, -0.35, -0.40):
        v = resA[f"+S1 {s:+.0%}"]
        ok = (v["p10"] - base["p10"] >= 0.05) and (v["mean"] - base["mean"] >= -0.02)
        wh = v["whipsaw"] if v["whipsaw"] is not None else 1.0
        lines.append(f"- S1 {s:+.0%}: Δp10 {v['p10']-base['p10']:+.1%}, "
                     f"Δmean {v['mean']-base['mean']:+.1%}, whipsaw {wh:.0%}, "
                     f"触发 {v['s1_fires']}/{v['n']} → {'合格' if ok else '不合格'}")
        if ok:
            qual.append((s, wh))
    if qual:
        best_wh = min(w for _, w in qual)
        pick = min((s for s, w in qual if w - best_wh < 0.03))  # 平局取更紧 (更接近 0 的负值更紧 → max? 更紧 = 更小绝对值 = -0.20)
        pick = max((s for s, w in qual if w - best_wh < 0.03))  # -0.20 > -0.40, max = 最紧
        lines.append(f"\n**A3 出值: S1 = {pick:+.0%}**")
    else:
        lines.append("\n**A3: 无合格档**")

    # ---- B 组: 入场 DTE @ 组合 overlay, Δ0.70 ----
    lines += ["", "## B 组: 入场 DTE @ 组合 overlay, Δ=0.70", "",
              "| 入场DTE | n | mean | 中位 | p10 | S4触发占比 | 到期强平占比 | 中位持有日 |",
              "|---|---|---|---|---|---|---|---|"]
    resB = {}
    for dte in (150, 270, 365):
        kw = {"s1": -0.30, "s2": (20, 30), "s4": 90}
        v = run(dte, 0.70, kw)
        resB[dte] = v
        s4n = sum(c for k_, c in v["exits"].items() if k_.startswith("S4"))
        expn = v["exits"].get("到期强平", 0)
        # 中位持有日重算
        days = []
        for tk, (rows, ind, sig) in data.items():
            for i0 in sig:
                r_ = simulate(rows, ind, i0, dte, 0.70, **kw)
                if r_:
                    days.append(r_["days"])
        lines.append(f"| {dte} | {v['n']} | {v['mean']:+.1%} | {v['med']:+.1%} "
                     f"| {v['p10']:+.1%} | {s4n/v['n']:.0%} | {expn/v['n']:.0%} "
                     f"| {int(statistics.median(days)) if days else '—'} |")
    d_mean_150 = resB[150]["mean"] - resB[270]["mean"]
    d_p10_150 = resB[150]["p10"] - resB[270]["p10"]
    b1 = d_mean_150 >= -0.02 and d_p10_150 >= -0.03
    lines += ["", f"**B1 DTE=150 可用性**: Δmean(150−270) = {d_mean_150:+.1%} (≥−2pp), "
              f"Δp10 = {d_p10_150:+.1%} (≥−3pp) → {'✓ 150 可用' if b1 else '✗ 150 不可用'}"]

    # ---- C 组: Δ 信息性 ----
    lines += ["", "## C 组 (信息性, 不裁): Δ0.45 vs Δ0.70 @ DTE 270, 组合 overlay", "",
              "| Δ | n | mean | 中位 | p10 | S1 触发占比 |", "|---|---|---|---|---|---|"]
    for dl in (0.45, 0.70):
        v = run(270, dl, {"s1": -0.30, "s2": (20, 30), "s4": 90})
        lines.append(f"| {dl:.2f} | {v['n']} | {v['mean']:+.1%} | {v['med']:+.1%} "
                     f"| {v['p10']:+.1%} | {v['s1_fires']/v['n']:.0%} |")

    # 敏感性: 逐日 IV (附表, A 组 base vs 组合)
    lines += ["", "## 敏感性 (附表): 持有期 IV 逐日 = HV20×1.1 — 未实现 (主臂 IV 锁定已覆盖裁决域, "
              "逐日 HV 臂需重构 pm(); 若 A/B 判据处于边缘再补跑)", ""]

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
