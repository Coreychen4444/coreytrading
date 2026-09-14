#!/usr/bin/env python3
"""reversal_probe_backtest.py — 反转探针入场类 (core.reversal) 的合成定价回测。

**性质: 探索定价, 不是预注册裁决** —— 判据未先锁, 数字是给「要不要这个类」
的输入; 全部臂与裁决记录在私有研究记录。
复测条件: live 本类完结 ≥10 笔或 +12 月, 届时判据先锁再跑。

信号 = **core.reversal.signal_at 同一份函数** (渲染口径 = 判定口径: 改判别式本文件自动跟随,
但那时数字就不再是裁决时的数字 —— 改了必须重新记录)。⚠️ core.reversal 的信号已换成
✓止跌 (setups.bottom_setup), 本文件跑出的数字是**新信号**的; 第一版数字冻结在私有研究记录。对照 G = synthetic_longcall 的突破类信号
(MA20>MA50 ∧ close>MA20 ∧ 40 日新高), 合成 BS 定价与该模块同源。

臂: 域 {G, R0 (≥10%), R1 (10–20%), R2 (≥20% ∧ <MA50)} × IV {锁定, 逐日=HV20×1.1} ×
合约 {DTE270 Δ0.70, DTE150 Δ0.50} × 退出 {支撑 stop + S1−20, 支撑 stop 无 S1}; 附 R1 按 HV
分位分桶、浅止损 (阳线低点) 变体、正股对照。

research 旁路: 无人 import 本模块 (tests/research 只做判别式同源检查)。
"""
from __future__ import annotations

import random
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from quant.core import reversal  # noqa: E402
from quant.core.paths import ROOT  # noqa: E402
from quant.research.synthetic_longcall_backtest import (  # noqa: E402
    DEEP_DIR, IV_PREM, WHIPSAW_DAYS, bs_call, load, p10, precompute, signals as gate_signals,
    strike_for_delta)

REPORT = ROOT / "tmp/backtest/reversal_probe_report.md"
# 🚫 冻结: 第一版裁决当时的域上沿 0.20 (只开 R1)。live `reversal.PARAMS["drawdown_max"]` 已改 None (R0),
# 本文件的 R1/R2 臂按裁决当时的世界分域, 否则第一版的数字不可复现 (研究层冻结阈值, 同 VOL_RATIO 先例)。
HISTORICAL_R1_MAX = 0.20
CAL = 365.0 / 252.0
WINDOW_START = "2024-01-01"
COOLDOWN = 20
HV_LOOKBACK, HV_MIN = 252, 120


def hv_pct(ind, i, lookback=HV_LOOKBACK, min_samples=HV_MIN):
    seg = [x for x in ind["hv20"][max(0, i - lookback):i] if x]
    if len(seg) < min_samples:
        return None
    return sum(1 for x in seg if x < ind["hv20"][i]) / len(seg)


def rev_signals(rows, ind, mode):
    """core.reversal.signal_at 逐根扫 + 冷却; mode ∈ {R0, R1, R2}。"""
    out, last = [], -10**9
    for i in range(60, len(rows)):
        if rows[i]["date"] < WINDOW_START or i - last < COOLDOWN:
            continue
        if not (ind["ma50"][i] and ind["hv20"][i] and ind["atr20"][i]):
            continue
        sig = reversal.signal_at(rows, i, ind["atr20"][i])
        if not sig:
            continue
        dd = -sig["drawdown"]
        if mode == "R1" and not (sig["in_domain"] and dd < HISTORICAL_R1_MAX):
            continue
        if mode == "R2" and not (dd >= HISTORICAL_R1_MAX and rows[i]["close"] < ind["ma50"][i]):
            continue
        if mode == "R0" and not (dd >= reversal.PARAMS["drawdown_min"]):
            continue
        out.append(i)
        last = i
    return out


def simulate(rows, ind, i0, entry_dte, delta_target, s1=None, iv_daily=False,
             support_mode="swing", stock=False, support=None, max_days=None,
             hold_above=None, whipsaw_days=WHIPSAW_DAYS):
    """`support` 显式给支撑 (bottom_stage_backtest 用: 止跌低 / HL); None = 原行为
    (反转探针信号的支撑 / 无信号时 6 根最低 low)。缺省不传 = 本文件既有各臂一字不变。
    `max_days` = 时间止损 (持有满 N 根收盘出, 抄底模式「几个交易日」); `hold_above` = 到期时收盘
    已在该线之上 (越前高 = 反转) 则不时间止损、继续交给退出栈; `whipsaw_days` = 止损后几根内
    收复成本算 whipsaw (缺省 60 = 探针原口径)。"""
    c0 = rows[i0]["close"]
    iv0 = ind["hv20"][i0] * IV_PREM
    atr0 = ind["atr20"][i0]
    sig = reversal.signal_at(rows, i0, atr0)
    if support is not None:
        sup = float(support)
    elif support_mode == "bar_low":
        sup = rows[i0]["low"]
    else:
        sup = sig["support"] if sig else min(r["low"] for r in rows[i0 - 5:i0 + 1])
    tau0 = entry_dte / 365.0
    k = strike_for_delta(c0, delta_target, tau0, iv0)
    cost = bs_call(c0, k, tau0, iv0)
    if cost <= 0:
        return None

    def iv_at(i):
        return ind["hv20"][i] * IV_PREM if (iv_daily and ind["hv20"][i]) else iv0

    def pm(i, use_open=False):
        px = rows[i]["open"] if use_open else rows[i]["close"]
        if stock:
            return px
        return bs_call(px, k, max(entry_dte - (i - i0) * CAL, 0) / 365.0, iv_at(i))

    base = c0 if stock else cost
    hwm, hwm_c, a50, a100 = base, c0, False, False
    n = len(rows)

    def out(i, reason):
        px = pm(i + 1, True) if i + 1 < n else pm(i)
        p = px / base - 1.0
        wh = False
        if reason.startswith(("S1", "支撑")):
            for j in range(i + 1, min(i + 1 + whipsaw_days, n)):
                if pm(j) >= base:
                    wh = True
                    break
        return {"pnl": p, "exit": reason, "days": i - i0, "whipsaw": wh, "iv0": iv0,
                "hvp": hv_pct(ind, i0)}

    for i in range(i0 + 1, n):
        p = pm(i)
        c = rows[i]["close"]
        hwm, hwm_c = max(hwm, p), max(hwm_c, c)
        gp = hwm / base - 1.0
        a100 = a100 or gp >= 1.0
        a50 = a50 or gp >= 0.5
        if not stock and entry_dte - (i - i0) * CAL <= 7:
            return out(i, "到期强平")
        if a100 and (hwm - p) >= (hwm - base) / 3.0:
            return out(i, "棘轮回吐1/3")
        if a50 and not a100 and p <= base:
            return out(i, "棘轮保本")
        if ind["atr20"][i] and c < hwm_c - 3.0 * ind["atr20"][i]:
            return out(i, "趋势死亡线")
        if c < sup:
            return out(i, "支撑破")
        if s1 is not None and p <= base * (1 + s1):
            return out(i, f"S1{s1:+.0%}")
        if max_days is not None and (i - i0) >= max_days and not (hold_above is not None and c > hold_above):
            return out(i, "时间止损")
    return out(n - 1, "数据尾截断")


def cluster_boot(by_tk, stat, n_boot=500, seed=7):
    rng = random.Random(seed)
    keys = list(by_tk)
    outs = []
    for _ in range(n_boot):
        pool = []
        for _ in keys:
            pool.extend(by_tk[rng.choice(keys)])
        if pool:
            outs.append(stat(pool))
    outs.sort()
    return outs[int(0.05 * len(outs))], outs[int(0.95 * len(outs))]


def stats(allr, by_tk):
    v = [r["pnl"] for r in allr]
    n = len(v)
    if not n:
        return None
    w = [x for x in v if x > 0]
    l_ = [x for x in v if x <= 0]
    aw = statistics.mean(w) if w else 0.0
    al = statistics.mean(l_) if l_ else 0.0
    lo, hi = cluster_boot(by_tk, statistics.mean)
    stops = sum(1 for r in allr if r["exit"].startswith(("S1", "支撑")))
    whip = sum(1 for r in allr if r["exit"].startswith(("S1", "支撑")) and r["whipsaw"])
    return {"n": n, "wr": len(w) / n, "mean": statistics.mean(v), "ci": (lo, hi),
            "med": statistics.median(v), "p10": p10(v), "aw": aw, "al": al,
            "payoff": (aw / abs(al) if al else float("nan")),
            "days": statistics.median(r["days"] for r in allr),
            "stop_rate": stops / n, "whip": (whip / stops if stops else None)}


def row(name, s):
    if not s:
        return f"| {name} | 0 | | | | | | | | | |"
    wh = f"{s['whip']:.0%}" if s["whip"] is not None else "—"
    return (f"| {name} | {s['n']} | {s['wr']:.0%} | {s['mean']:+.1%} [{s['ci'][0]:+.1%},{s['ci'][1]:+.1%}] "
            f"| {s['med']:+.1%} | {s['p10']:+.1%} | {s['aw']:+.1%} | {s['al']:+.1%} | {s['payoff']:.2f} "
            f"| {int(s['days'])} | {s['stop_rate']:.0%}/{wh} |")


HDR = ("| 臂 | n | 胜率 | mean [CI90 票 cluster] | 中位 | p10 | 平均赢 | 平均亏 | 盈亏比 | 中位持有日 | 止损率/whipsaw |\n"
       "|---|---|---|---|---|---|---|---|---|---|---|")


def run(data, sigkey, hv_bucket=None, **kw):
    by, allr = {}, []
    for tk, (rows, ind, sigs) in data.items():
        for i0 in sigs[sigkey]:
            if hv_bucket is not None:
                h = hv_pct(ind, i0)
                if h is None or not (hv_bucket[0] <= h < hv_bucket[1]):
                    continue
            r = simulate(rows, ind, i0, **kw)
            if r:
                by.setdefault(tk, []).append(r["pnl"])
                allr.append(r)
    return stats(allr, by)


def main():
    data = {}
    for p in sorted(DEEP_DIR.glob("*.jsonl")):
        try:
            rows = load(p.stem)
        except Exception:
            continue
        if len(rows) < 300 or any(r["close"] <= 0 or r["open"] <= 0 for r in rows):
            continue
        ind = precompute(rows)
        data[p.stem] = (rows, ind, {"G": gate_signals(rows, ind),
                                    "R0": rev_signals(rows, ind, "R0"),
                                    "R1": rev_signals(rows, ind, "R1"),
                                    "R2": rev_signals(rows, ind, "R2")})
    cnt = {k: sum(len(s[k]) for _, _, s in data.values()) for k in ("G", "R0", "R1", "R2")}
    L = [f"# reversal_probe 探索定价 (非预注册) — 池 {len(data)} 票, campaigns G {cnt['G']} / "
         f"R0 {cnt['R0']} / R1 {cnt['R1']} / R2 {cnt['R2']} (2024-01→2026-07)", ""]
    for ivd, lab in ((True, "IV逐日"), (False, "IV锁定")):
        L += [f"## {lab}: 支撑 stop, 有/无 S1−20", "", HDR]
        for sk in ("G", "R0", "R1", "R2"):
            for dte, dl in ((270, 0.70), (150, 0.50)):
                L.append(row(f"{sk} 支撑stop 无S1 DTE{dte} Δ{dl:.2f}", run(data, sk, entry_dte=dte, delta_target=dl, iv_daily=ivd)))
                L.append(row(f"{sk} 支撑stop+S1−20 DTE{dte} Δ{dl:.2f}", run(data, sk, entry_dte=dte, delta_target=dl, s1=-0.20, iv_daily=ivd)))
        L.append("")
    L += ["## R1 按 HV20 自身 252 日分位分桶 (IVP 门的选择器证据; IV逐日, DTE270 Δ0.70, 支撑 stop 无 S1)", "", HDR]
    for name, b in (("<40", (0, .4)), ("40-60", (.4, .6)), ("60-80", (.6, .8)), (">=80", (.8, 1.01))):
        L.append(row(f"R1 HV分位 {name}", run(data, "R1", hv_bucket=b, entry_dte=270, delta_target=0.70, iv_daily=True)))
    L += ["", "## 浅止损变体 (支撑 = 反转阳线低点, IV逐日 DTE270 Δ0.70 无 S1)", "", HDR]
    for sk in ("R0", "R1"):
        L.append(row(f"{sk} 浅止损=阳线低点", run(data, sk, entry_dte=270, delta_target=0.70, iv_daily=True, support_mode="bar_low")))
    L += ["", "## 正股对照 (同信号, 支撑 stop)", "", HDR]
    for sk in ("G", "R0", "R1", "R2"):
        L.append(row(f"{sk} 正股", run(data, sk, entry_dte=270, delta_target=0.70, stock=True)))
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
