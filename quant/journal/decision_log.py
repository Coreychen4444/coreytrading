#!/usr/bin/env python3
"""decision_log.py — 决策时刻持久化 (EV worksheet / Kelly posterior / 选择).

为什么存在: EV 框架和 Kelly 算过就丢 = 永远无法校准 (G4/G9 analytics gap)。
每次跑 entry-timing EV 或 kelly_size 后把 worksheet 落一行 JSONL,
未来 ev_calibration.py / kelly_calibration.py 用它对照实际 outcome。

CLI (全部字段可选, 有什么记什么):
    python3 quant/journal/decision_log.py --ticker AAOI --action buy_leaps \\
        --p 0.73 --b 2.5 --kelly-notional 8500 \\
        --ev-now 698 --ev-wait 675 --chosen buy_now \\
        --target-price 78 --p-fill 0.45 \\
        --notes "5/6 Jan2027 140C entry-timing worksheet"
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 直接 `python3 quant/<层>/<模块>.py` 时让 `quant.*` 可 import

import argparse
import json

from quant.core.io_json import append_jsonl  # noqa: E402
from quant.core.paths import DECISIONS_PATH  # noqa: E402
from quant.core.timeutil import now_et_iso  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="决策记录 → events/decisions.jsonl")
    ap.add_argument("--ticker", required=True)
    ap.add_argument("--action", required=True,
                    help="buy/sell/buy_leaps/sell_put/roll/wait/skip/...")
    ap.add_argument("--p", type=float, help="Bayesian 后验胜率 0-1")
    ap.add_argument("--b", type=float, help="净赔率")
    ap.add_argument("--kelly-notional", type=float, help="kelly_size.py 名义上限 $")
    ap.add_argument("--kelly-binding", help="kelly | concentration_cap | ladder | no_edge")
    ap.add_argument("--ev-now", type=float, help="EV(现在买) $")
    ap.add_argument("--ev-wait", type=float, help="EV(等) $")
    ap.add_argument("--target-price", type=float, help="rigorous 推导的 wait target")
    ap.add_argument("--p-fill", type=float, help="ATR 距离表查得的 P_fill")
    ap.add_argument("--implied-move-pct", type=float)
    ap.add_argument("--chosen", required=True, help="实际选择 (buy_now/wait/skip/...) ")
    ap.add_argument("--size", type=float, help="实际 size ($ 或张数, 注明于 notes)")
    ap.add_argument("--notes", default="")
    a = ap.parse_args()

    row = {"timestamp": now_et_iso(), "ticker": a.ticker.upper(), "action": a.action, "chosen": a.chosen}
    for k in ("p", "b", "kelly_notional", "kelly_binding", "ev_now", "ev_wait",
              "target_price", "p_fill", "implied_move_pct", "size", "notes"):
        v = getattr(a, k)
        if v not in (None, ""):
            row[k] = v
    append_jsonl(DECISIONS_PATH, row)
    print(f"logged → {DECISIONS_PATH.name}: {json.dumps(row, ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
