#!/usr/bin/env python3
"""zone_hit_analysis.py — 历史 BUY fill 落点 vs zone + 前瞻收益。

回答: "zone1/zone2 内的买入 vs waiting/追价买入，后续 5d/20d 表现差多少？"
(zone 方法论第一次有实证检验, 不再只是信念)

方法:
1. zone_timeline (git 挖掘) → 每笔 BUY fill 当日生效的 zone
2. fill 落点分类: zone1 / zone2 / below_zone2 / waiting / trim / above_no_chase
   (期权 fill 用 underlying 的 zone + 当日 underlying 收盘价定位 — fill 的
   期权价不能直接比 zone)
3. kline 缓存 → underlying 前瞻 +5d / +20d 收益
4. 按落点分组统计

诚实边界: v2 zone 历史从  起, 样本量小 — 输出永远带 n, 不装显著。

CLI: python3 quant/research/zone_hit_analysis.py [--json]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 直接 `python3 quant/<层>/<模块>.py` 时让 `quant.*` 可 import

import argparse
import json
from collections import defaultdict

from quant.core.io_json import iter_jsonl  # noqa: E402
from quant.core.ledger import iter_events  # noqa: E402
from quant.core.occ import parse_occ  # noqa: E402
from quant.core.paths import KLINE_CACHE_DIR  # noqa: E402
from quant.research.zone_timeline import build_timeline, zones_asof  # noqa: E402


def kline_closes(ticker: str) -> list[tuple[str, float]]:
    return [(str(r.get("time", ""))[:10], float(r["close"]))
            for r in iter_jsonl(KLINE_CACHE_DIR / f"{ticker}.jsonl") if r.get("close")]


def close_on_or_after(closes, day: str, offset_bars: int = 0):
    idx = next((i for i, (d, _) in enumerate(closes) if d >= day), None)
    if idx is None or idx + offset_bars >= len(closes):
        return None
    return closes[idx + offset_bars][1]


def classify(price: float, z: dict) -> str:
    def pair(k):
        v = z.get(k)
        return v if isinstance(v, list) and len(v) == 2 else None
    z1, z2, trim = pair("accumulation_zone_1"), pair("accumulation_zone_2"), pair("trim_zone")
    nca = z.get("no_chase_above")
    if z1 and z1[0] <= price <= z1[1]:
        return "zone1"
    if z2 and z2[0] <= price <= z2[1]:
        return "zone2"
    if z2 and price < z2[0]:
        return "below_zone2"
    if trim and trim[0] <= price <= trim[1]:
        return "trim"
    if isinstance(nca, (int, float)) and price > nca:
        return "above_no_chase"
    return "waiting"


def main() -> int:
    ap = argparse.ArgumentParser(description="zone 命中 vs 前瞻收益")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    timeline = build_timeline()
    rows = []
    for e in iter_events():
        if e.get("event_type") != "fill" or (e.get("side") or "").upper() != "BUY":
            continue
        day = str(e.get("timestamp"))[:10]
        if day < "2026-05-06":  # v2 zone 史之前没有可信 zone 快照
            continue
        tk = e["ticker"]
        p = parse_occ(tk)
        underlying = p["underlying"] if p else tk
        z = zones_asof(timeline, underlying, day) or zones_asof(timeline, tk, day)
        if not z:
            continue
        closes = kline_closes(underlying)
        spot = close_on_or_after(closes, day)
        if spot is None:
            continue
        f5 = close_on_or_after(closes, day, 5)
        f20 = close_on_or_after(closes, day, 20)
        rows.append({
            "date": day, "ticker": tk, "underlying": underlying,
            "bucket": classify(spot, z), "underlying_close": spot,
            "fwd_5d_pct": round((f5 / spot - 1) * 100, 2) if f5 else None,
            "fwd_20d_pct": round((f20 / spot - 1) * 100, 2) if f20 else None,
            "zone_at_fill": e.get("zone_at_fill"),
        })

    groups = defaultdict(list)
    for r in rows:
        groups[r["bucket"]].append(r)
    summary = {}
    for b, rs in sorted(groups.items()):
        f5s = [r["fwd_5d_pct"] for r in rs if r["fwd_5d_pct"] is not None]
        f20s = [r["fwd_20d_pct"] for r in rs if r["fwd_20d_pct"] is not None]
        summary[b] = {
            "n": len(rs),
            "avg_fwd_5d_pct": round(sum(f5s) / len(f5s), 2) if f5s else None,
            "avg_fwd_20d_pct": round(sum(f20s) / len(f20s), 2) if f20s else None,
        }

    out = {"fills_analyzed": len(rows), "by_bucket": summary, "rows": rows,
           "caveat": "v2 zone 史 2026-05-06 起, 样本小 — 看 n 别装显著; 前瞻收益是 underlying 非期权 PnL"}
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(f"BUY fills analyzed: {len(rows)} (since 2026-05-06)")
        print(f"{'bucket':16s} {'n':>3s} {'fwd5d%':>8s} {'fwd20d%':>8s}")
        for b, s in summary.items():
            print(f"{b:16s} {s['n']:>3d} {str(s['avg_fwd_5d_pct']):>8s} {str(s['avg_fwd_20d_pct']):>8s}")
        for r in rows:
            print(f"  {r['date']} {r['ticker']:20s} → {r['bucket']:14s} fwd5 {r['fwd_5d_pct']} fwd20 {r['fwd_20d_pct']}")
        print(out["caveat"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
