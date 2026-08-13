#!/usr/bin/env python3
"""Cap-schedule sensitivity backtest: replay classified episodes at %NAV caps.

Provenance:  参数修订源 (cap_confirmed 0.025→0.05, gate to 0.10;
decisions.jsonl ticker=SYSTEM action=param_revision)。月度滚动重跑校准 —
样本随 decisions.jsonl/annotate_event 分类覆盖增长。
已知限制: n=16 单 regime (→07 一轮 boom-bust), 13/16 期权 (正股 per-$
温和 3-5x → 对正股 campaign 高估双尾), realized-basis DD 低估盘中回撤。
MU260717 440/500 spread 两腿在 events 无 parent 链接, 脚本内硬编码合并净额。
用法: python3 quant/research/entry_caps_backtest.py

Method:
  1. Episodes from lib.ledger (same engine as perf_report), classified only.
  2. Per-$ return r = realized_pnl / cost_in  (long, cost_in > 0 only).
  3. Chronological replay: at open, size = cap[class] * NAV_now (cash-clipped);
     at close, NAV += size * r.  MaxDD on realized-basis NAV path.
  4. Stress variant: losses amplified 1.5x / 2.0x (gap-through + execution slip).
  5. Kelly anchors per class from empirical p / avg-win / avg-loss per $.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 直接 `python3 quant/<层>/<模块>.py` 时让 `quant.*` 可 import

import json
from collections import defaultdict

from quant.core.ledger import closed_episodes

NAV0 = 22657.0

def entry_tag(ep, field):
    for e in ep.get("entries", []):
        v = e.get(field)
        if v:
            return v
    return None

eps = []
skipped = []
raw = {e["ticker"]: e for e in closed_episodes()}
# MU 440/500 bull call spread: legs not parent-linked in events; merge to net unit
MERGE = ("MU260717C440000", "MU260717C500000")
for ep in closed_episodes():
    cls = entry_tag(ep, "classification")
    if not cls:
        continue
    if ep["ticker"] == MERGE[1]:
        continue  # folded into long leg below
    pnl, cost = ep.get("realized_pnl"), ep.get("cost_in")
    if ep["ticker"] == MERGE[0] and MERGE[1] in raw:
        short = raw[MERGE[1]]
        pnl = (pnl or 0) + (short.get("realized_pnl") or 0)
        cost = (cost or 0) - (short.get("cost_in") or 0)  # net debit
    if pnl is None or not cost or cost <= 0 or (ep.get("direction") != "long" and ep["ticker"] != MERGE[0]):
        skipped.append((ep["ticker"], cls, ep.get("direction"), cost, pnl))
        continue
    eps.append({
        "ticker": ep["ticker"] + ("(+500C短腿)" if ep["ticker"] == MERGE[0] else ""), "cls": cls,
        "open": str(ep["opened_at"])[:10], "close": str(ep["closed_at"])[:10],
        "cost": cost, "pnl": pnl, "r": pnl / cost,
    })

eps.sort(key=lambda e: (e["open"], e["close"]))

print("=== classified long episodes (per-$ returns) ===")
for e in eps:
    print(f"  {e['open']}→{e['close']}  {e['ticker']:<22} {e['cls']:<12} "
          f"cost ${e['cost']:>9,.0f}  pnl ${e['pnl']:>9,.0f}  r {e['r']:+.2%}")
if skipped:
    print("  -- skipped (short/orphan/zero-cost):")
    for s in skipped:
        print("    ", s)

# per-class stats + Kelly
print("\n=== per-class stats & Kelly anchors ===")
for cls in ("confirmed", "probe", "early_risky"):
    sub = [e for e in eps if e["cls"] == cls]
    if not sub:
        continue
    wins = [e["r"] for e in sub if e["pnl"] > 0]
    losses = [e["r"] for e in sub if e["pnl"] <= 0]
    p = len(wins) / len(sub)
    aw = sum(wins) / len(wins) if wins else 0.0
    al = abs(sum(losses) / len(losses)) if losses else 0.0
    line = f"  {cls:<12} n={len(sub):<3} p={p:.2f}  avg_win/$ {aw:+.2%}  avg_loss/$ {-al:+.2%}"
    if wins and losses and al > 0:
        b = aw / al
        f_full = (p * (b + 1) - 1) / b
        # loss-per-unit scaling: fraction of NAV such that at-risk matches Kelly
        # position_frac = kelly_at_risk / loss_per_unit(=al)
        line += f"  b={b:.2f}  f*(at-risk)={f_full:+.1%}"
        if f_full > 0:
            line += (f" -> half-Kelly position ≈ {0.5*f_full/al:.1%} NAV,"
                     f" quarter ≈ {0.25*f_full/al:.1%} NAV")
    print(line)

# replay
def replay(cap_map, loss_mult=1.0, nav0=NAV0):
    nav = nav0
    path = [nav]
    active = []  # (close_date, size, r)
    deployed = 0.0
    peak_conc = 0.0
    worst_ep = 0.0
    events = []
    for e in eps:
        events.append((e["open"], "open", e))
        events.append((e["close"], "close", e))
    events.sort(key=lambda x: (x[0], 0 if x[1] == "close" else 1))
    sizes = {}
    for d, kind, e in events:
        key = id(e)
        if kind == "open":
            cap = cap_map.get(e["cls"], 0.0)
            size = cap * nav
            if deployed + size > nav:  # cash-constrained account
                size = max(0.0, nav - deployed)
            sizes[key] = size
            deployed += size
            peak_conc = max(peak_conc, deployed / nav if nav > 0 else 0)
        else:
            size = sizes.pop(key, 0.0)
            deployed -= size
            r = e["r"]
            if r < 0:
                r *= loss_mult
            pnl = size * r
            worst_ep = min(worst_ep, pnl / nav if nav > 0 else 0)
            nav += pnl
            path.append(nav)
    peak = path[0]
    maxdd = 0.0
    for v in path:
        peak = max(peak, v)
        maxdd = min(maxdd, (v - peak) / peak)
    return {"end": nav, "pnl": nav - nav0, "ret": nav / nav0 - 1,
            "maxdd": maxdd, "peak_conc": peak_conc, "worst_ep": worst_ep}

schedules = [
    ("current  1% / 2.5%", {"probe": 0.01, "confirmed": 0.025, "early_risky": 0.0}),
    ("mid      2.5% / 5%", {"probe": 0.025, "confirmed": 0.05, "early_risky": 0.0}),
    ("用户    5% / 10%",  {"probe": 0.05, "confirmed": 0.10, "early_risky": 0.0}),
    ("heavy    10% / 15%", {"probe": 0.10, "confirmed": 0.15, "early_risky": 0.0}),
    ("conf-only 0% / 10%", {"probe": 0.0, "confirmed": 0.10, "early_risky": 0.0}),
    ("conf-only 0% / 5%",  {"probe": 0.0, "confirmed": 0.05, "early_risky": 0.0}),
    ("no-veto  5%/10%+ER5%", {"probe": 0.05, "confirmed": 0.10, "early_risky": 0.05}),
]

print("\n=== replay on NAV0 $22,657 (classified subset, realized-basis) ===")
hdr = f"{'schedule':<22} {'endNAV':>9} {'pnl':>8} {'ret':>7} {'maxDD':>7} {'peakConc':>8} {'worstEp':>8}"
for lm, label in ((1.0, "base"), (1.5, "stress: losses x1.5"), (2.0, "stress: losses x2.0")):
    print(f"\n-- {label} --")
    print(hdr)
    for name, caps in schedules:
        r = replay(caps, loss_mult=lm)
        print(f"{name:<22} {r['end']:>9,.0f} {r['pnl']:>+8,.0f} {r['ret']:>+7.1%} "
              f"{r['maxdd']:>7.1%} {r['peak_conc']:>8.0%} {r['worst_ep']:>+8.1%}")
