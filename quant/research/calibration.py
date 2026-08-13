#!/usr/bin/env python3
"""calibration.py — EV 框架 / Kelly 后验的预测 vs 实际校准。

数据源: events/decisions.jsonl (decision_log.py 落的 worksheet)。
回答两个校准问题:
1. EV 框架: 选 wait 的决策里 target 实际 fill 率 vs 预测 P_fill (P_fill 表准不准)
2. Kelly: 后验 p 的分桶实际胜率 (p=0.7 的注是不是真的 ~70% 赢) — join closed episodes

诚实边界: decisions.jsonl  才开始积累 — 空数据时明确说"攒数据中",
不假装有结论。每个桶永远带 n。

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


def kelly_calibration(decisions: list[dict]) -> dict:
    """p 分桶 → join 该 ticker 决策后最近一个 closed episode 的输赢。"""
    eps = build_episodes(warn=lambda m: None)
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
    waits = [d for d in decisions if d.get("chosen") in ("wait", "wait_target")]
    nows = [d for d in decisions if d.get("chosen") in ("buy_now", "now")]
    return {
        "decisions_total": len(decisions),
        "chose_wait": len(waits),
        "chose_buy_now": len(nows),
        "wait_with_p_fill": sum(1 for d in waits if d.get("p_fill") is not None),
        "note": ("P_fill 实际命中率需要 target 价 + 后续 kline 对照 — "
                 "decisions 攒到 ≥10 条 wait 后此处自动补 fill-rate 表"),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="EV/Kelly 预测 vs 实际校准")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    decisions = list(iter_jsonl(DECISIONS_PATH))
    out = {
        "decisions_logged": len(decisions),
        "kelly_calibration": kelly_calibration(decisions),
        "ev_calibration": ev_calibration(decisions),
    }
    if not decisions:
        out["status"] = ("攒数据中 — decisions.jsonl 为空。每次跑 kelly_size / EV 框架后用 "
                        "quant/journal/decision_log.py 落一行, 校准曲线随数据自动出现。")
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(f"decisions logged: {len(decisions)}")
        if "status" in out:
            print(out["status"])
        else:
            print("Kelly p 分桶 vs 实际胜率:")
            for b, v in out["kelly_calibration"].items():
                print(f"  p {b}: n={v['n']} joined={v['joined']} actual_win={v['actual_win_rate']}")
            print(json.dumps(out["ev_calibration"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
