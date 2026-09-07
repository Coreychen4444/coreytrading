#!/usr/bin/env python3
"""Build a zone-change timeline from an authorized private Git history.

The public repository contains no populated state. In a private workspace that
deliberately versions state snapshots, Git history can provide point-in-time
zone observations without embedding them in this module.

CLI:
    python3 quant/research/zone_timeline.py [--ticker <SYMBOL>] [--json]
输出: 每 (commit_date, ticker) 一行 zone 快照 (只输出 zone 实际变化的行)。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 直接 `python3 quant/<层>/<模块>.py` 时让 `quant.*` 可 import

import argparse
import json
import subprocess

from quant.core.paths import ROOT  # noqa: E402

from quant.data.position_history import list_position_commits, position_at

ZONE_KEYS = ("accumulation_zone_1", "accumulation_zone_2", "trim_zone",
             "no_chase_above", "stop_level", "valid_until", "methodology")


def list_commits() -> list[tuple[str, str]]:
    """[(sha, iso_date)] oldest→newest touching positions.json."""
    return list_position_commits(ROOT, reverse=True)


def zones_at(sha: str) -> dict[str, dict]:
    try:
        doc = position_at(sha, ROOT)
    except (subprocess.CalledProcessError, ValueError):
        return {}
    out = {}
    for tk, body in (doc.get("tickers") or {}).items():
        zones = ((body.get("agent") or {}).get("zones"))
        if isinstance(zones, dict):
            out[tk] = {k: zones.get(k) for k in ZONE_KEYS}
    return out


def build_timeline(ticker_filter: str | None = None) -> list[dict]:
    timeline: list[dict] = []
    prev: dict[str, dict] = {}
    for sha, ts in list_commits():
        snap = zones_at(sha)
        if not snap:
            continue
        for tk, z in snap.items():
            if ticker_filter and tk != ticker_filter:
                continue
            if prev.get(tk) != z:  # 只记变化
                timeline.append({"date": ts[:10], "commit": sha[:9], "ticker": tk, "zones": z})
        prev = snap
    return timeline


def zones_asof(timeline: list[dict], ticker: str, day: str) -> dict | None:
    """该 ticker 在 day 当天生效的 zone (最后一次 ≤ day 的变更)。"""
    best = None
    for row in timeline:
        if row["ticker"] != ticker or row["date"] > day:
            continue
        best = row["zones"]
    return best


def main() -> int:
    ap = argparse.ArgumentParser(description="zone 演化时间线 (git 历史挖掘)")
    ap.add_argument("--ticker", default=None)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    tl = build_timeline(a.ticker.upper() if a.ticker else None)
    if a.json:
        print(json.dumps(tl, ensure_ascii=False, indent=2))
    else:
        for r in tl:
            z = r["zones"]
            print(f"{r['date']} {r['ticker']:22s} z1={z.get('accumulation_zone_1')} "
                  f"z2={z.get('accumulation_zone_2')} trim={z.get('trim_zone')} "
                  f"stop={z.get('stop_level')} valid={z.get('valid_until')}")
        print(f"\n{len(tl)} zone changes available under the current private schema")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
