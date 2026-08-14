#!/usr/bin/env python3
"""Replay single-leg long-call exit overlays from private event state.

The private preregistration must lock C1/C2/C3, exclusions and data sources.
方法: premium 序列 = BS 重估, IV 由入/出场 fill 价反解后按交易日线性插值 (两端构造性
锚定私人 fill 价); 信号 = 收盘读数, 成交 = 次日开盘重建价; 成本 = campaign 加权保费。

公开源码不保存合约清单、持仓快照、排除表或研究结果。
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import sys
from bisect import bisect_right
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from quant.core import technical  # noqa: E402
from quant.core.paths import ROOT  # noqa: E402

KLINE_DIR = ROOT / "trade/us_stock/market/klines"
EVENTS_GLOB = str(ROOT / "trade/us_stock/holding/events/*/trades.jsonl")
REPORT = ROOT / "tmp/backtest/options_exit_overlay_report.md"
DEFAULT_CONFIG = ROOT / "trade/us_stock/knowledge/research/options_exit_overlay/config.json"
R = 0.045  # 无风险利率常数 (§5)

WINNER_MIN = 0.40   # final ≥ +40% → 赢家 (C2)
LOSER_MAX = -0.20   # final ≤ -20% → 亏家 (C1)


def load_private_config(path: Path) -> dict:
    """Load exclusions locked by a private preregistration."""
    if not path.exists():
        raise FileNotFoundError(
            f"private study config not found: {path}; create it in the ignored workspace"
        )
    config = json.loads(path.read_text(encoding="utf-8"))
    excluded = config.get("exclude_symbols")
    if not isinstance(excluded, list) or not all(isinstance(item, str) for item in excluded):
        raise ValueError("private study config requires exclude_symbols as a JSON list")
    return {"exclude_symbols": set(excluded)}


# ---------- Black-Scholes ----------
def _ncdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_call(s: float, k: float, tau: float, iv: float, r: float = R) -> float:
    if tau <= 0 or iv <= 0:
        return max(0.0, s - k)
    sq = iv * math.sqrt(tau)
    d1 = (math.log(s / k) + (r + 0.5 * iv * iv) * tau) / sq
    return s * _ncdf(d1) - k * math.exp(-r * tau) * _ncdf(d1 - sq)


def implied_vol(price: float, s: float, k: float, tau: float) -> float | None:
    """bisect 反解; premium < intrinsic (数据噪音/深 ITM 贴水) → None."""
    if tau <= 0 or price <= max(0.0, s - k * math.exp(-R * tau)) + 1e-6:
        return None
    lo, hi = 0.01, 5.0
    if bs_call(s, k, tau, hi) < price:
        return None
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if bs_call(s, k, tau, mid) < price:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


# ---------- 数据装载 ----------
def load_klines(und: str) -> list[dict]:
    rows = [json.loads(l) for l in open(KLINE_DIR / f"{und}.jsonl")]
    for r_ in rows:
        r_["date"] = r_["time"][:10]
    return sorted(rows, key=lambda r_: r_["date"])


def parse_occ(sym: str):
    import re
    m = re.match(r"^([A-Z]+)(\d{6})([CP])(\d+)$", sym)
    und, ymd, cp, strike = m.group(1), m.group(2), m.group(3), int(m.group(4))
    expiry = f"20{ymd[:2]}-{ymd[2:4]}-{ymd[4:6]}"
    return und, expiry, cp, strike / 1000.0


def load_trips(exclude_symbols: set[str]) -> list[dict]:
    import re
    occ_re = re.compile(r"^([A-Z]+)\d{6}[CP]\d+$")
    fills: dict[str, list[dict]] = {}
    for f in sorted(glob.glob(EVENTS_GLOB)):
        for line in open(f):
            d = json.loads(line)
            sym = d.get("symbol") or d.get("ticker") or ""
            if occ_re.match(sym) and sym not in exclude_symbols:
                fills.setdefault(sym, []).append(
                    {"date": (d.get("timestamp") or "")[:10], "side": d.get("side"),
                     "qty": float(d.get("qty") or 0), "price": float(d.get("price") or 0)})
    trips = []
    for sym, evs in fills.items():
        evs.sort(key=lambda e: e["date"])
        buys = [e for e in evs if e["side"] == "BUY"]
        sells = [e for e in evs if e["side"] == "SELL"]
        und, expiry, cp, strike = parse_occ(sym)
        if cp != "C" or not buys:
            continue
        qty = sum(b["qty"] for b in buys)
        cost = sum(b["qty"] * b["price"] for b in buys) / qty
        trip = {"sym": sym, "und": und, "expiry": expiry, "strike": strike,
                "buys": buys, "sells": sells, "qty": qty, "avg_cost": cost,
                "entry_date": buys[0]["date"], "open": not sells}
        if sells:
            sq = sum(s_["qty"] for s_ in sells)
            trip["exit_date"] = sells[-1]["date"]
            trip["exit_price"] = sum(s_["qty"] * s_["price"] for s_ in sells) / sq
            trip["actual_pnl_pct"] = trip["exit_price"] / cost - 1.0
        trips.append(trip)
    return sorted(trips, key=lambda t: t["entry_date"])


# ---------- premium 序列重建 ----------
def tau_years(date: str, expiry: str) -> float:
    from datetime import date as D
    d0 = D(*map(int, date.split("-")))
    d1 = D(*map(int, expiry.split("-")))
    return max(0.0, (d1 - d0).days / 365.0)


OPT_KLINE_DIR = ROOT / "trade/us_stock/market/option_klines"


def rebuild_real(trip: dict, klines: list[dict]) -> list[dict] | None:
    """Use licensed local option bars when present, otherwise return ``None``.

    The option bar calendar defines executable sessions. IV is inverted from
    each close; deep-ITM failures remain missing for the S3 branch.
    """
    f = OPT_KLINE_DIR / f"{trip['sym']}.jsonl"
    if not f.exists():
        return None
    bars = [json.loads(l) for l in open(f)]
    for b in bars:
        b["date"] = b["time"][:10]
    bars = [b for b in bars if b["date"] >= trip["entry_date"]]
    end = trip.get("exit_date")
    if end:
        bars = [b for b in bars if b["date"] <= end]
    if not bars:
        return None
    und_close = {r_["date"]: r_["close"] for r_ in klines}
    atr_series = technical.atr(klines, period=20)
    und_atr = {r_["date"]: atr_series[i] for i, r_ in enumerate(klines)}
    rows = []
    for j, b in enumerate(bars):
        uc = und_close.get(b["date"])
        tau_c = tau_years(b["date"], trip["expiry"])
        iv = implied_vol(b["close"], uc, trip["strike"], tau_c) if uc else None
        rows.append({
            "date": b["date"], "i": j, "iv": iv,
            "und_close": uc, "und_open": uc,
            "dte": round(tau_c * 365),
            "pm_close": b["close"], "pm_open": b["open"],
            "atr20": und_atr.get(b["date"]), "src": "real",
        })
    # IV None 的日子 (深 ITM/und 缺) 用前值补, 首日缺则后值 — 只影响 S3
    last_iv = next((r_["iv"] for r_ in rows if r_["iv"]), None)
    for r_ in rows:
        if r_["iv"] is None:
            r_["iv"] = last_iv
        else:
            last_iv = r_["iv"]
    return rows


def rebuild(trip: dict, klines: list[dict]) -> list[dict]:
    """交易日序列: 入场日 → 出场日 (或最新), 每日 close/open premium + 插值 IV."""
    dates = [r_["date"] for r_ in klines]
    closes = {r_["date"]: r_["close"] for r_ in klines}
    opens = {r_["date"]: r_["open"] for r_ in klines}
    end = trip.get("exit_date") or dates[-1]
    i0, i1 = dates.index(trip["entry_date"]), dates.index(end)

    # IV 锚：每笔 BUY + 出场 SELL；未平仓 campaign 使用显式 private mark。
    anchors: list[tuple[int, float]] = []
    for b in trip["buys"]:
        iv = implied_vol(b["price"], closes[b["date"]], trip["strike"],
                         tau_years(b["date"], trip["expiry"]))
        if iv:
            anchors.append((dates.index(b["date"]), iv))
    if trip.get("exit_price"):
        iv = implied_vol(trip["exit_price"], closes[end], trip["strike"],
                         tau_years(end, trip["expiry"]))
        if iv:
            anchors.append((i1, iv))
    elif trip["open"]:
        iv = implied_vol(trip["mark"], closes[end], trip["strike"],
                         tau_years(end, trip["expiry"]))
        if iv:
            anchors.append((i1, iv))
    anchors.sort()
    if not anchors:
        raise RuntimeError(f"{trip['sym']}: 无可用 IV 锚")

    def iv_at(i: int) -> float:
        if i <= anchors[0][0]:
            return anchors[0][1]
        if i >= anchors[-1][0]:
            return anchors[-1][1]
        j = bisect_right([a[0] for a in anchors], i)
        (x0, y0), (x1, y1) = anchors[j - 1], anchors[j]
        return y0 + (y1 - y0) * (i - x0) / (x1 - x0)

    atr_series = technical.atr(klines, period=20)
    rows = []
    for i in range(i0, i1 + 1):
        d = dates[i]
        iv = iv_at(i)
        tau_c = tau_years(d, trip["expiry"])
        rows.append({
            "date": d, "i": i - i0, "iv": iv,
            "und_close": closes[d], "und_open": opens[d],
            "dte": round(tau_c * 365),
            "pm_close": bs_call(closes[d], trip["strike"], tau_c, iv),
            "pm_open": bs_call(opens[d], trip["strike"], tau_c, iv),
            "atr20": atr_series[i],
        })
    return rows


# ---------- 模拟 ----------
def simulate(trip: dict, daily: list[dict], s1=None, s2=None, s3=False, s4=None) -> dict:
    """返回 ``pnl_pct``, ``trigger`` 与 ``exit_date``；未触发则读取实际结果或 mark。"""
    entry = daily[0]
    entry_close, atr0, iv0 = entry["und_close"], entry["atr20"] or 0.0, entry["iv"]
    buys_by_date = {b["date"]: b for b in trip["buys"]}
    qty_now, cost_wsum = 0.0, 0.0
    half_done, half_pnl = False, 0.0
    frac = 1.0  # 剩余仓位比例

    def wcost() -> float:
        return cost_wsum / qty_now if qty_now else trip["avg_cost"]

    for j, row in enumerate(daily):
        if row["date"] in buys_by_date:
            b = buys_by_date[row["date"]]
            qty_now += b["qty"]
            cost_wsum += b["qty"] * b["price"]
        trig = None
        # S1 premium 硬 stop
        if s1 is not None and row["pm_close"] <= wcost() * (1 + s1):
            trig = f"S1({s1:+.0%})"
        # S4 DTE 地板 — 穿越语义: 入场时已在地板下的仓不适用 (入场 DTE 归入场规则管,
        # 历史短 DTE 彩票按新体系根本不存在; S4 只管持有中衰减穿越)
        if trig is None and s4 is not None and daily[0]["dte"] > s4 and row["dte"] <= s4:
            trig = f"S4(dte≤{s4})"
        # S3 IV×横盘
        if trig is None and s3 and (row["iv"] <= iv0 - 0.08) \
                and row["und_close"] < entry_close + atr0:
            trig = "S3"
        # S2 时间 stop
        if trig is None and s2 is not None:
            t1, t2 = s2
            flat = row["und_close"] < entry_close + atr0
            if row["i"] >= t2 and flat:
                trig = f"S2清仓(d{t2})"
            elif row["i"] >= t1 and flat and not half_done:
                # 减半: 次日开盘卖一半, 剩余继续
                if j + 1 < len(daily):
                    half_pnl = daily[j + 1]["pm_open"] / wcost() - 1.0
                    half_done, frac = True, 0.5
                continue
        if trig:
            if j + 1 < len(daily):
                px = daily[j + 1]["pm_open"]
            else:
                px = row["pm_close"]  # 序列末触发: 按当日收盘 (进行中笔)
            pnl = px / wcost() - 1.0
            total = (0.5 * half_pnl + 0.5 * pnl) if half_done else pnl
            return {"pnl_pct": total, "trigger": trig, "exit_date": row["date"],
                    "half": half_done}
    # 未触发 → actual
    if trip.get("actual_pnl_pct") is not None:
        base = trip["actual_pnl_pct"]
    else:
        base = daily[-1]["pm_close"] / wcost() - 1.0  # 进行中: mark-to-model
    total = (0.5 * half_pnl + 0.5 * base) if half_done else base
    return {"pnl_pct": total, "trigger": "—", "exit_date": trip.get("exit_date", "持有中"),
            "half": half_done}


# ---------- 判据 ----------
def judge(arm_results: list[dict], trips: list[dict]) -> dict:
    closed = [(t, r_) for t, r_ in zip(trips, arm_results) if not t["open"]]
    losers = [(t, r_) for t, r_ in closed if t["actual_pnl_pct"] <= LOSER_MAX]
    winners = [(t, r_) for t, r_ in closed if t["actual_pnl_pct"] >= WINNER_MIN]
    imp = sorted(r_["pnl_pct"] - t["actual_pnl_pct"] for t, r_ in losers)
    med_imp = imp[len(imp) // 2] if len(imp) % 2 else 0.5 * (imp[len(imp)//2 - 1] + imp[len(imp)//2])
    worst_loser = min(imp) if imp else 0.0
    c1 = med_imp >= 0.15 and worst_loser >= -0.05
    win_ratio = min((r_["pnl_pct"] / t["actual_pnl_pct"] for t, r_ in winners), default=1.0)
    c2 = win_ratio >= 0.60
    tot_ov = sum(r_["pnl_pct"] * t["avg_cost"] * t["qty"] * 100 for t, r_ in closed)
    tot_ac = sum(t["actual_pnl_pct"] * t["avg_cost"] * t["qty"] * 100 for t, _ in closed)
    worst_single = min(r_["pnl_pct"] - t["actual_pnl_pct"] for t, r_ in closed)
    c3 = tot_ov >= tot_ac and worst_single >= -0.10
    return {"med_imp": med_imp, "worst_loser": worst_loser, "win_ratio": win_ratio,
            "tot_ov": tot_ov, "tot_ac": tot_ac, "worst_single": worst_single,
            "C1": c1, "C2": c2, "C3": c3}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Replay private option exit overlays")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--report", type=Path, default=REPORT)
    args = parser.parse_args(argv)
    config = load_private_config(args.config)
    trips = load_trips(config["exclude_symbols"])
    # 未平仓 campaign 的出场锚来自 ignored positions state。
    for t in trips:
        if t["open"]:
            pos = json.load(open(ROOT / "trade/us_stock/holding/state/positions.json"))
            t["mark"] = pos["tickers"][t["sym"]]["broker"]["current_price"]
    dailies, srcs, calib = {}, {}, []
    for t in trips:
        kl = load_klines(t["und"])
        real = rebuild_real(t, kl)
        synth = rebuild(t, kl)
        if real:
            dailies[t["sym"]], srcs[t["sym"]] = real, "real"
            # 对拍: BS 合成在重叠日的相对误差 (给 synthetic_longcall 校准注脚)
            sm = {r_["date"]: r_["pm_close"] for r_ in synth}
            for r_ in real:
                if r_["date"] in sm and r_["pm_close"] > 0:
                    calib.append(abs(sm[r_["date"]] - r_["pm_close"]) / r_["pm_close"])
        else:
            dailies[t["sym"]], srcs[t["sym"]] = synth, "synth"

    # 重建质量: 两端锚定误差 (应≈0) + 中段 vs iv_history 形状 (打印不裁决)
    n_real = sum(1 for v in srcs.values() if v == "real")
    calib.sort()
    calib_line = (f"BS 合成 vs 本地 premium 相对误差 (重叠日 n={len(calib)}): "
                  f"中位 {calib[len(calib)//2]:.1%}, p90 {calib[int(len(calib)*0.9)]:.1%}"
                  if calib else "")
    lines = ["# options_exit_overlay 回测报告", "",
             f"协议: private preregistration | 样本 {len(trips)} 笔 "
             f"(判据域 {sum(1 for t in trips if not t['open'])}) | "
             f"**local option bars {n_real} 笔**, BS 合成 {len(trips)-n_real} 笔", "",
             calib_line, ""]

    arms: list[tuple[str, dict]] = []
    for s1 in (-0.20, -0.25, -0.30, -0.35, -0.40):
        arms.append((f"S1 {s1:+.0%}", {"s1": s1}))
    for s2 in ((15, 25), (20, 30)):
        arms.append((f"S2 {s2}", {"s2": s2}))
    arms.append(("S3 (−8pts,+1N)", {"s3": True}))
    for s4 in (90, 120, 150):
        arms.append((f"S4 dte≤{s4}", {"s4": s4}))
    arms.append(("组合 S1-30+S2(20,30)+S3+S4-90",
                 {"s1": -0.30, "s2": (20, 30), "s3": True, "s4": 90}))

    lines += ["## 臂 × 判据总表", "",
              "| 臂 | 亏家改善中位 | 亏家最差 | 赢家保留比 min | Σoverlay | Σactual | 最差单笔Δ | C1 | C2 | C3 |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    arm_details = {}
    for name, kw in arms:
        res = [simulate(t, dailies[t["sym"]], **kw) for t in trips]
        arm_details[name] = res
        v = judge(res, trips)
        lines.append(
            f"| {name} | {v['med_imp']:+.1%} | {v['worst_loser']:+.1%} | {v['win_ratio']:.2f} "
            f"| ${v['tot_ov']:+,.0f} | ${v['tot_ac']:+,.0f} | {v['worst_single']:+.1%} "
            f"| {'✓' if v['C1'] else '✗'} | {'✓' if v['C2'] else '✗'} | {'✓' if v['C3'] else '✗'} |")

    # 明细: 组合臂
    combo = arm_details["组合 S1-30+S2(20,30)+S3+S4-90"]
    lines += ["", "## 明细 (组合臂)", "",
              "| 笔 | 源 | 入场DTE | actual | overlay | Δ | 触发 | 触发日 |",
              "|---|---|---|---|---|---|---|---|"]
    for t, r_ in zip(trips, combo):
        ac = f"{t['actual_pnl_pct']:+.1%}" if t.get("actual_pnl_pct") is not None else "持有中"
        d0 = dailies[t["sym"]][0]["dte"]
        lines.append(f"| {t['sym']} | {srcs[t['sym']]} | {d0} | {ac} | {r_['pnl_pct']:+.1%} "
                     f"| {(r_['pnl_pct'] - (t.get('actual_pnl_pct') or r_['pnl_pct'])):+.1%} "
                     f"| {r_['trigger']}{' +减半' if r_.get('half') else ''} | {r_['exit_date']} |")

    # 预注册外解读切片: 入场 DTE ≥150 子样本 (新入场规则下仍会存在的仓形)。
    # 判据不改不重判 — 只报同一批臂在该切片上的读数, 供裁决参考。
    sub = [t for t in trips if dailies[t["sym"]][0]["dte"] >= 150]
    lines += ["", f"## 切片: 入场 DTE≥150 子样本 (n={sum(1 for t in sub if not t['open'])}, "
              "预注册外解读层 — 新入场规则可行仓形)", "",
              "| 臂 | 亏家改善中位 | 亏家最差 | 赢家保留比 min | Σoverlay | Σactual | 最差单笔Δ |",
              "|---|---|---|---|---|---|---|"]
    for name, kw in arms:
        res = [simulate(t, dailies[t["sym"]], **kw) for t in sub]
        v = judge(res, sub)
        lines.append(
            f"| {name} | {v['med_imp']:+.1%} | {v['worst_loser']:+.1%} | {v['win_ratio']:.2f} "
            f"| ${v['tot_ov']:+,.0f} | ${v['tot_ac']:+,.0f} | {v['worst_single']:+.1%} |")

    open_trips = [trip for trip in trips if trip["open"]]
    if open_trips:
        lines += ["", "## Open campaigns（不进判据）", ""]
        for trip in open_trips:
            for name, kw in arms:
                r_ = simulate(trip, dailies[trip["sym"]], **kw)
                if r_["trigger"] != "—":
                    lines.append(
                        f"- {trip['sym']} {name}: {r_['exit_date']} 触发 {r_['trigger']}, "
                        f"退出 PnL {r_['pnl_pct']:+.1%} "
                        f"(vs mark {trip['mark']/trip['avg_cost']-1:+.1%})"
                    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
