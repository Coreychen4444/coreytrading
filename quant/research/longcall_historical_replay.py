#!/usr/bin/env python3
"""Replay long-call campaigns from an explicit private manifest.

The public module contains pricing and exit mechanics only. Symbols, entry
sessions, option codes, provider observations and study results stay in the
ignored private workspace. The manifest fixes the population before results are
read and prevents public source from becoming a portfolio snapshot.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from quant.core import technical  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
OPT_DIR = ROOT / "trade/us_stock/market/option_klines"
KL = ROOT / "trade/us_stock/market/klines"
DEEP = ROOT / "trade/us_stock/market/klines_deep"
DEFAULT_MANIFEST = ROOT / "trade/us_stock/knowledge/research/longcall_replay/campaigns.json"
DEFAULT_REPORT = ROOT / "tmp/backtest/longcall_historical_replay_report.md"
R = 0.045
WHIPSAW_DAYS = 60
MAX_LATE = 10  # 合约首根晚于入场日历日的最大交易日数


def load_campaigns(path: Path) -> list[tuple[str, str, str]]:
    """Load ``ticker``, ``entry_date`` and ``option_code`` from a private JSON list."""
    if not path.exists():
        raise FileNotFoundError(
            f"private campaign manifest not found: {path}; create it in the ignored workspace"
        )
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or not payload:
        raise ValueError("campaign manifest must be a non-empty JSON list")
    campaigns = []
    for index, row in enumerate(payload):
        if not isinstance(row, dict):
            raise ValueError(f"campaign[{index}] must be an object")
        values = (row.get("ticker"), row.get("entry_date"), row.get("option_code"))
        if not all(isinstance(value, str) and value for value in values):
            raise ValueError(f"campaign[{index}] requires ticker, entry_date and option_code")
        campaigns.append(values)
    return campaigns


def _ncdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_call(s, k, tau, iv, r=R):
    if tau <= 0 or iv <= 0:
        return max(0.0, s - k)
    sq = iv * math.sqrt(tau)
    d1 = (math.log(s / k) + (r + 0.5 * iv * iv) * tau) / sq
    return s * _ncdf(d1) - k * math.exp(-r * tau) * _ncdf(d1 - sq)


def implied_vol(price, s, k, tau):
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


def bs_delta(s, k, tau, iv):
    if tau <= 0 or iv is None or iv <= 0:
        return 1.0 if s > k else 0.0
    sq = iv * math.sqrt(tau)
    return _ncdf((math.log(s / k) + (R + 0.5 * iv * iv) * tau) / sq)


def und_rows(t):
    for d in (KL, DEEP):
        f = d / f"{t}.jsonl"
        if f.exists():
            rs = [json.loads(l) for l in open(f)]
            for r_ in rs:
                r_["date"] = (r_.get("time") or r_.get("date"))[:10]
            return sorted(rs, key=lambda r_: r_["date"])
    return None


def tau_of(d, code):
    d0 = date(*map(int, d.split("-")))
    match = re.match(r"^[A-Z]+(\d{2})(\d{2})(\d{2})[CP]\d+$", code)
    if not match:
        raise ValueError(f"invalid option code: {code}")
    d1 = date(2000 + int(match.group(1)), int(match.group(2)), int(match.group(3)))
    return max(0.0, (d1 - d0).days / 365.0)


def replay(t, entry_cal, code, s1=None, s2=None):
    bars = [json.loads(l) for l in open(OPT_DIR / f"{code}.jsonl")]
    for b in bars:
        b["date"] = b["time"][:10]
    bars = [b for b in bars if b["date"] >= entry_cal]
    urows = und_rows(t)
    udates = [r_["date"] for r_ in urows]
    # 首根晚于入场日历日 ≤10 交易日才可用
    if not bars:
        return None
    first = bars[0]["date"]
    i_cal = next((i for i, d in enumerate(udates) if d >= entry_cal), None)
    i_first = next((i for i, d in enumerate(udates) if d >= first), None)
    if i_cal is None or i_first is None or i_first - i_cal > MAX_LATE:
        return None
    cost = bars[0]["close"]
    if cost <= 0:
        return None
    uclose = {r_["date"]: r_["close"] for r_ in urows}
    atr = technical.atr(urows, period=20)
    uatr = {r_["date"]: atr[i] for i, r_ in enumerate(urows)}
    s0 = uclose.get(bars[0]["date"])
    iv0 = implied_vol(cost, s0, _strike(code), tau_of(bars[0]["date"], code)) if s0 else None
    d0 = bs_delta(s0, _strike(code), tau_of(bars[0]["date"], code), iv0) if s0 else None

    hwm_pm, hwm_close = cost, s0 or 0.0
    armed50 = armed100 = False
    res = None
    for j in range(1, len(bars)):
        b = bars[j]
        p, c = b["close"], uclose.get(b["date"])
        hwm_pm = max(hwm_pm, p)
        if c:
            hwm_close = max(hwm_close, c)
        gain_pk = hwm_pm / cost - 1.0
        armed100 = armed100 or gain_pk >= 1.0
        armed50 = armed50 or gain_pk >= 0.5

        def exit_at(reason):
            px = bars[j + 1]["open"] if j + 1 < len(bars) else p
            wh = False
            if reason.startswith("S1"):
                for k2 in range(j + 1, min(j + 1 + WHIPSAW_DAYS, len(bars))):
                    if bars[k2]["close"] >= cost:
                        wh = True
                        break
                if j + 1 + WHIPSAW_DAYS > len(bars):
                    wh = wh or None  # 窗口未走完: None = 未决
            return {"pnl": px / cost - 1.0, "exit": reason, "days": j,
                    "whipsaw": wh, "delta0": d0, "iv0": iv0}

        if armed100 and (hwm_pm - p) >= (hwm_pm - cost) / 3.0:
            res = exit_at("棘轮回吐1/3")
            break
        if armed50 and not armed100 and p <= cost:
            res = exit_at("棘轮保本")
            break
        a = uatr.get(b["date"])
        if c and a and c < hwm_close - 3.0 * a:
            res = exit_at("趋势死亡线")
            break
        if s1 is not None and p <= cost * (1 + s1):
            res = exit_at(f"S1{s1:+.0%}")
            break
        if s2 is not None and c is not None:
            t1, t2 = s2
            a0 = uatr.get(bars[0]["date"]) or 0.0
            if j >= t2 and c < (s0 or 0.0) + a0:
                res = exit_at(f"S2清仓d{t2}")
                break
    if res is None:
        res = {"pnl": bars[-1]["close"] / cost - 1.0, "exit": "持有中(尾截断)",
               "days": len(bars) - 1, "whipsaw": None, "delta0": d0, "iv0": iv0}
    return res


def _strike(code):
    match = re.match(r"^[A-Z]+\d{6}[CP](\d+)$", code)
    if not match:
        raise ValueError(f"invalid option code: {code}")
    return int(match.group(1)) / 1000.0


def p10(v):
    s = sorted(v)
    return s[max(0, int(0.10 * len(s)))] if s else 0.0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Replay private long-call campaigns")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args(argv)
    campaigns = load_campaigns(args.manifest)

    lines = ["# long-call historical replay", "",
             "Population and entry sessions come from the locked private manifest.", ""]
    base_res = {}
    valid = []
    for t, ed, code in campaigns:
        r_ = replay(t, ed, code, s1=None)
        if r_:
            base_res[(t, ed)] = r_
            valid.append((t, ed, code))
    lines.append(f"有效 campaigns: {len(valid)}（超过最大延迟的首根记录已排除）")
    lines += ["", "## 真实入场 delta 分布 (strike 由 HV 代理反解, 执行误差如实报)", ""]
    ds = sorted(r_["delta0"] for r_ in base_res.values() if r_["delta0"])
    lines.append(f"- delta 中位 {ds[len(ds)//2]:.2f}, p25 {ds[len(ds)//4]:.2f}, "
                 f"p75 {ds[3*len(ds)//4]:.2f}; Δ≥0.55 的 {sum(1 for d in ds if d >= 0.55)}/{len(ds)}")

    lines += ["", "## base 栈 (无 S1) vs +S1 各档", "",
              "| 臂 | mean | 中位 | p10 | S1触发 | whipsaw 已决 | whipsaw率(已决) | 未决 |",
              "|---|---|---|---|---|---|---|---|"]
    rows_by_arm = {}
    for arm, s1, s2 in [("base", None, None), ("S1 -20%", -0.20, None),
                    ("S1 -25%", -0.25, None), ("S1 -30%", -0.30, None),
                    ("S1 -35%", -0.35, None), ("S1 -40%", -0.40, None),
                    ("S2 (20,30)", None, (20, 30)),
                    ("S1-35+S2", -0.35, (20, 30)), ("S1-30+S2", -0.30, (20, 30))]:
        rs = []
        for t, ed, code in valid:
            r_ = replay(t, ed, code, s1=s1, s2=s2)
            if r_:
                rs.append((t, r_))
        rows_by_arm[arm] = rs
        pnls = [r_["pnl"] for _, r_ in rs]
        fires = [r_ for _, r_ in rs if r_["exit"].startswith("S1")]
        wh_decided = [r_ for r_ in fires if r_["whipsaw"] in (True, False)]
        wh_hit = sum(1 for r_ in wh_decided if r_["whipsaw"])
        pend = sum(1 for r_ in fires if r_["whipsaw"] is None)
        whr = f"{wh_hit/len(wh_decided):.0%}" if wh_decided else "—"
        lines.append(f"| {arm} | {statistics.mean(pnls):+.1%} | "
                     f"{statistics.median(pnls):+.1%} | {p10(pnls):+.1%} "
                     f"| {len(fires)}/{len(rs)} | {wh_hit}/{len(wh_decided)} | {whr} | {pend} |")

    # 明细: base 与 S1-30 对照
    lines += ["", "## 明细 (base → S1-30)", "",
              "| campaign | Δ0 | base 退出 | base PnL | S1-30 退出 | S1-30 PnL | whipsaw |",
              "|---|---|---|---|---|---|---|"]
    s130 = {(t, r_2["days"]): r_2 for (t, r_2) in []}
    arm30 = {(t, ed): None for t, ed, _ in valid}
    for (t, ed, code) in valid:
        r30 = replay(t, ed, code, s1=-0.30)
        b = base_res[(t, ed)]
        wh = {True: "✔回来了", False: "✘没回来", None: "未决"}.get(
            r30["whipsaw"] if r30["exit"].startswith("S1") else "", "")
        lines.append(f"| {t} {ed[5:7]}月 | {b['delta0']:.2f} | {b['exit']}(d{b['days']}) "
                     f"| {b['pnl']:+.1%} | {r30['exit']}(d{r30['days']}) | {r30['pnl']:+.1%} | {wh} |")

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
