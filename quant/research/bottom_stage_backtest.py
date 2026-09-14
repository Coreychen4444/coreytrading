#!/usr/bin/env python3
"""bottom_stage_backtest.py — 两种模式中抄底（空头衰竭）入场的探索定价：值多少钱。

**性质: 探索定价, 不是预注册裁决** —— 判据未先锁, 数字是给
模式 1 的授权信号 / 域 / 工具选择的输入。采纳前按 N1 定式判据先锁再跑。

问题: 旧反转探针只测过「一根阳线吃掉 5 日 LH」入场 (R2 mean ≈ 0)。新
的止跌判据是最基本的价格行为: **第二天没有继续新低 / 收出长下影线, 止损放在下影线低点** —— 这个
信号从未定价。

信号 = **core.setups._frame 同一份函数** (判读口径 = 定价口径): 逐根回放, 某个止跌低 L 的框架第一次
成为 ✓止跌 (`stage == "formed"` 且 `bars_since_signal == 0`) 的那天 = 信号日; 按衰竭证据分臂
(全部 / 长下影 / 次日未新低)。域按 L 距 60 日高的回撤: R1 [10%,20%) / R2 ≥20% / R0 = 全部 ≥10%。
止损 = 止跌低 L (下影线低点) 收盘 ×1 无 S1。合约 / IV / 退出栈 / 统计全部复用 reversal_probe_backtest
(DTE270 Δ0.70, IV 逐日 = HV20×1.1, 棘轮 50/100 + 趋势死亡线 3×ATR20 + 到期); 对照 G = 突破类信号,
R1 探针 = 现行授权信号。附「反转候选 (越前高)」晚入场臂: 同框架首次收盘越前高的那天。

research 旁路: 无人 import 本模块。
    python3 quant/research/bottom_stage_backtest.py
"""
from __future__ import annotations

import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from quant.core import setups  # noqa: E402
from quant.core.paths import ROOT  # noqa: E402
from quant.research.reversal_probe_backtest import (  # noqa: E402
    COOLDOWN, HDR, WINDOW_START, rev_signals, row, simulate, stats)
from quant.research.synthetic_longcall_backtest import (  # noqa: E402
    DEEP_DIR, load, precompute, signals as gate_signals)

REPORT = ROOT / "tmp/backtest/bottom_stage_report.md"
ARMS = ("all", setups.SIGNAL_HAMMER, setups.SIGNAL_HELD, "held2", "held3", "above_lbar_high", "above_lh")
DOMAINS = {"R0": (0.10, 9.9), "R1": (0.10, 0.20), "R2": (0.20, 9.9)}


def stage_signals(rows, ind):
    """→ {arm: [(i0, support_low, dd_at_low)]}。逐根回放 setups._frame。

    arm "all" = 首次 ✓止跌; 按 `signal` 拆成 长下影 / 次日未新低 (同时成立记在长下影);
    "above_lh" = 同一个止跌低的框架首次收盘越前高 (反转候选) 的那天 —— 晚入场对照。"""
    out = {a: [] for a in ARMS}
    seen = set()
    last = {a: -10**9 for a in ARMS}
    formed_lows = set()          # 同一人群: 晚入场臂只数「全部」臂里出现过的那些止跌低
    n = len(rows)
    for i in range(setups.MIN_BARS, n):
        if rows[i]["date"] < WINDOW_START:
            continue
        if not (ind["ma50"][i] and ind["hv20"][i] and ind["atr20"][i]):
            continue
        fr = setups._frame(rows[:i + 1])
        if fr.get("stage") != "formed":
            continue
        rec = (i, float(fr["low"]), -float(fr["dd_at_low"]), fr.get("lh"))
        if fr.get("bars_since_signal") == 0:
            key = (fr["low_idx"], "formed")                # 底的身份 = 那根 bar 的下标 (日期截 10 字符会撞)
            if key not in seen and i - last["all"] >= COOLDOWN:
                seen.add(key)
                last["all"] = i
                formed_lows.add(fr["low_idx"])
                out["all"].append(rec)
                arm = setups.SIGNAL_HAMMER if setups.SIGNAL_HAMMER in fr["signal"] else setups.SIGNAL_HELD
                out[arm].append(rec)
        if fr["low_idx"] not in formed_lows:
            continue                                     # 「全部」臂没数的止跌低, 晚入场臂也不数
        # canonical 口径 (§七) 的两条更严口径, 作对照臂:
        #   止跌后观察 2-3 日不破前低 (held2 / held3) / 收盘站上止跌日最高点 (above_lbar_high)
        for k in (2, 3):
            if setups.SIGNAL_HELD in fr["signal"] and fr.get("held_days") == k:
                key = (fr["low_idx"], f"held{k}")
                if key not in seen and i - last[f"held{k}"] >= COOLDOWN:
                    seen.add(key)
                    last[f"held{k}"] = i
                    out[f"held{k}"].append(rec)
        if rows[i]["close"] > rows[fr["low_idx"]]["high"]:
            key = (fr["low_idx"], "above_lbar_high")
            if key not in seen and i - last["above_lbar_high"] >= COOLDOWN:
                seen.add(key)
                last["above_lbar_high"] = i
                out["above_lbar_high"].append(rec)
        if fr.get("above_lh"):
            key = (fr["low_idx"], "above_lh")
            if key not in seen and i - last["above_lh"] >= COOLDOWN:
                seen.add(key)
                last["above_lh"] = i
                out["above_lh"].append(rec)
    return out


def run_stage(data, arm, domain, hold_if_reversal=False, **kw):
    lo, hi = DOMAINS[domain]
    by, allr = {}, []
    for tk, (rows, ind, sigs) in data.items():
        for i0, sup_low, dd, lh in sigs["stages"][arm]:
            if not (lo <= dd < hi):
                continue
            r = simulate(rows, ind, i0, support=sup_low,
                         hold_above=(lh if hold_if_reversal else None), **kw)
            if r:
                r["stop_dist"] = rows[i0]["close"] / sup_low - 1.0     # 入场价距止损 (止跌低) 的距离
                by.setdefault(tk, []).append(r["pnl"])
                allr.append(r)
    st = stats(allr, by)
    if st:
        st["stop_dist_med"] = statistics.median(r["stop_dist"] for r in allr)
    return st


def run_probe(data, key, **kw):
    by, allr = {}, []
    for tk, (rows, ind, sigs) in data.items():
        for i0 in sigs[key]:
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
        except Exception:                                    # noqa: BLE001
            continue
        if len(rows) < 300 or any(r["close"] <= 0 or r["open"] <= 0 for r in rows):
            continue
        ind = precompute(rows)
        data[p.stem] = (rows, ind, {"G": gate_signals(rows, ind),
                                    "R1p": rev_signals(rows, ind, "R1"),
                                    "stages": stage_signals(rows, ind)})
    cnt = {a: sum(len(x["stages"][a]) for _, _, x in data.values()) for a in ARMS}
    L = [f"# 抄底 (空头衰竭) 入场 探索定价 (非预注册) — 池 {len(data)} 票, 信号 ✓止跌 {cnt['all']} "
         f"(长下影 {cnt[setups.SIGNAL_HAMMER]} / 次日未新低 {cnt[setups.SIGNAL_HELD]}) / 越前高 {cnt['above_lh']} "
         f"(2024-01→2026-07); 对照 G 突破 / R1 探针", ""]
    kw = dict(entry_dte=270, delta_target=0.70, iv_daily=True)
    names = (("all", "✓止跌 全部"), (setups.SIGNAL_HAMMER, "✓止跌 长下影"),
             (setups.SIGNAL_HELD, "✓止跌 次日未新低"), ("held2", "2 日不破低 (§七)"),
             ("held3", "3 日不破低 (§七)"), ("above_lbar_high", "收上止跌日高 (§七)"),
             ("above_lh", "越前高 (反转候选, 晚入场)"))
    L += ["## Long call DTE270 Δ0.70, IV 逐日, 止损 = 止跌低 (下影线低点) ×1 无 S1", "", HDR]
    L.append(row("G 突破 (对照)", run_probe(data, "G", **kw)))
    L.append(row("R1 探针 (对照, 现行授权信号)", run_probe(data, "R1p", **kw)))
    def srow(label, st):
        tag = f" [止损距 {st['stop_dist_med']:.1%}]" if st else ""
        return row(label + tag, st)

    for dom in ("R0", "R1", "R2"):
        for arm, name in names:
            L.append(srow(f"{dom} {name}", run_stage(data, arm, dom, **kw)))
    L += ["", "## 正股对照 (同信号, 止损 = 止跌低 ×1)", "", HDR]
    L.append(row("G 正股", run_probe(data, "G", entry_dte=270, delta_target=0.70, stock=True)))
    for dom in ("R0", "R1", "R2"):
        for arm, name in names:
            L.append(srow(f"{dom} {name} 正股", run_stage(data, arm, dom, entry_dte=270, delta_target=0.70, stock=True)))
    # 持有期: 一般持仓几个交易日, 具体看后续反弹还是反转
    # → 时间止损 T+5 / T+10; 「反转就继续拿着」= 到期时收盘已越前高则不平, 交给退出栈; whipsaw 改 10 根
    L += ["", "## 持有期臂 (锁定口径: 次日未新低 / 长下影, 止损 = 止跌低; 时间止损 T+N; whipsaw 改为 10 根内收复成本)", "", HDR]
    for dom in ("R0", "R2"):
        for stock, lab in ((True, "正股"), (False, "call")):
            base = dict(entry_dte=270, delta_target=0.70, iv_daily=True, stock=stock, whipsaw_days=10)
            L.append(srow(f"{dom} ✓止跌 {lab} 退出栈 (对照, whipsaw10)", run_stage(data, "all", dom, **base)))
            for nd in (5, 10):
                L.append(srow(f"{dom} ✓止跌 {lab} T+{nd} 平", run_stage(data, "all", dom, max_days=nd, **base)))
                L.append(srow(f"{dom} ✓止跌 {lab} T+{nd} 平·越前高则持有", run_stage(data, "all", dom, max_days=nd, hold_if_reversal=True, **base)))
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
