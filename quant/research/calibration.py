#!/usr/bin/env python3
"""Read-only calibration for a private decision ledger.

The module compares logged probability estimates with closed synthetic or
private episodes. It ships no observations or conclusions. Empty and
unjoinable samples remain explicit, and every reported bucket includes its
sample size.

CLI: python3 quant/research/calibration.py [--json]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 直接 `python3 quant/<层>/<模块>.py` 时让 `quant.*` 可 import

import argparse
import json
from collections import defaultdict

from quant.core.io_json import iter_jsonl  # noqa: E402
from quant.core.ledger import build_episodes  # noqa: E402
from quant.core.paths import DECISIONS_PATH  # noqa: E402


def kelly_calibration(decisions: list[dict], episodes: dict | None = None) -> dict:
    """Bucket probabilities and join the next closed episode for each symbol."""
    eps = build_episodes(warn=lambda _message: None) if episodes is None else episodes
    buckets = defaultdict(lambda: {"n": 0, "wins": 0, "joined": 0})
    for d in decisions:
        p = d.get("p")
        if p is None:
            continue
        b = f"{int(p * 10) * 10}-{int(p * 10) * 10 + 9}%"
        buckets[b]["n"] += 1
        cands = [e for e in eps.get(d.get("ticker", ""), [])
                 if e["status"] == "closed" and e["realized_pnl"] is not None
                 and (e.get("closed_at") or "") >= (d.get("timestamp") or "")]
        if cands:
            buckets[b]["joined"] += 1
            if cands[0]["realized_pnl"] > 0:
                buckets[b]["wins"] += 1
    out = {}
    for b, v in sorted(buckets.items()):
        out[b] = {**v, "actual_win_rate": round(v["wins"] / v["joined"], 2) if v["joined"] else None}
    return out


def ev_calibration(decisions: list[dict]) -> dict:
    """Summarize logged timing choices without inferring missing outcomes."""
    waits = [d for d in decisions if d.get("chosen") in ("wait", "wait_target")]
    nows = [d for d in decisions if d.get("chosen") in ("buy_now", "now")]
    return {
        "decisions_total": len(decisions),
        "chose_wait": len(waits),
        "chose_buy_now": len(nows),
        "wait_with_p_fill": sum(1 for d in waits if d.get("p_fill") is not None),
        "note": ("Fill-rate calibration requires an explicit target-price and "
                 "completed-bar join; this summary does not infer that outcome."),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Calibrate logged EV and probability estimates")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    decisions = list(iter_jsonl(DECISIONS_PATH))
    out = {
        "decisions_logged": len(decisions),
        "kelly_calibration": kelly_calibration(decisions),
        "ev_calibration": ev_calibration(decisions),
    }
    if not decisions:
        out["status"] = (
            "No private decisions are available. Log reviewed worksheets through "
            "quant/journal/decision_log.py before requesting calibration."
        )
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(f"decisions logged: {len(decisions)}")
        if "status" in out:
            print(out["status"])
        else:
            print("Probability buckets versus realized outcomes:")
            for b, v in out["kelly_calibration"].items():
                print(f"  p {b}: n={v['n']} joined={v['joined']} actual_win={v['actual_win_rate']}")
            print(json.dumps(out["ev_calibration"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
