#!/usr/bin/env python3
"""migrate_next_regime_cap — 给带 gate 却没说授予多少的 `next_regime` 补上 cap (一次性迁移)。

背景: 早期 schema 两头宽严不一致 —— `next_regime.cap_pct_nav` 允许空, `current_regime` 要求 numeric ——
而转移机上行分支原样搬 (`current_regime.cap_pct_nav = nr.get("cap_pct_nav")`)。于是「写卡时的沉默」在
**转移发生的那一刻**变成整本文件写入被拒, 达标的转移一笔都落不了卡。

判据已收紧 (`core.schema._validate_regime`): 带 `entry_gate` 的 regime 必须给 numeric cap。
存量私有工作区须**先跑本迁移再让新判据生效**, 否则旧卡会把该文件的写路径一起焊死。

**补什么值**: cap 是二值 {0, NOTIONAL_CAP} (position-tiers), 所以只有两种答案 ——
「这道 gate 授予参与资格」= NOTIONAL_CAP, 「这是修复路上的中间站」= 显式 0。本迁移默认补
NOTIONAL_CAP (突破 / 收复类 gate 的通常语义: 入场 = gate 达标转移, cap 由转移机授予 ——
一道给 0 的 gate 会让这些卡没有任何入场路径)。要写 0 的卡用 `--zero <T>` 逐票点名。

cap 是**名义天花板不是额度**: 实际能下多少仍由 `sizing.entry_cap()` 六臂取小裁定。

幂等: 已是数字的 cap 一律不动 (包括显式 0)。默认 dry-run, `--apply` 才落盘。

Usage:
    python3 quant/ops/migrate_next_regime_cap.py              # 预览
    python3 quant/ops/migrate_next_regime_cap.py --apply      # 落盘
    python3 quant/ops/migrate_next_regime_cap.py --zero SYNA --apply
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 直接 `python3 quant/<层>/<模块>.py` 时让 `quant.*` 可 import

import argparse  # noqa: E402
import json  # noqa: E402
from typing import Dict, List, Set, Tuple  # noqa: E402

from quant.core.io_json import state_lock  # noqa: E402
from quant.core.sizing import NOTIONAL_CAP  # noqa: E402
from quant.sync import state_io  # noqa: E402

FIELD = "cap_pct_nav"


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def migrate_doc(doc: Dict, *, zero: Set[str]) -> Tuple[List, List]:
    """就地改 doc → (changed, intact)。只碰 `next_regime` —— `last_regime` 的 cap 恒 0 由
    schema 单独管, `current_regime` 本来就不许空。"""
    changed, intact = [], []
    for ticker, card in sorted((doc.get("tickers") or {}).items()):
        agent = card.get("agent")
        if not isinstance(agent, dict):
            continue
        block = agent.get("next_regime")
        if not isinstance(block, dict) or not block.get("entry_gate"):
            continue          # 无 gate 的骨架块本来就可空 (转移机会立刻补)
        cap = block.get(FIELD)
        if _is_num(cap):
            intact.append((ticker, cap))
            continue
        value = 0 if ticker in zero else NOTIONAL_CAP
        block[FIELD] = value
        changed.append((ticker, block.get("name"), cap, value))
    return changed, intact


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="落盘 (缺省 = dry-run)")
    ap.add_argument("--zero", action="append", default=[], metavar="TICKER",
                    help="这些票补显式 0 (中间站, 不授权); 可重复")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    zero = {t.strip().upper() for t in a.zero if t.strip()}

    with state_lock():
        pos, wl = state_io.read_positions(), state_io.read_watchlist()
        pc, pi = migrate_doc(pos, zero=zero)
        wc, wi = migrate_doc(wl, zero=zero)
        if a.apply and (pc or wc):
            if pc:
                state_io.write_positions(pos)
            if wc:
                state_io.write_watchlist(wl)

    report = {"applied": bool(a.apply), "zero": sorted(zero),
              "changed": [{"bucket": b, "ticker": t, "regime": n, "was": w, "now": v}
                          for b, rows in (("positions", pc), ("watchlist", wc))
                          for t, n, w, v in rows],
              "intact": [{"bucket": b, "ticker": t, "cap": c}
                         for b, rows in (("positions", pi), ("watchlist", wi)) for t, c in rows]}
    if a.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
    else:
        print(f"next_regime.cap_pct_nav 补齐 — {'已落盘' if a.apply else 'dry-run (未落盘)'}")
        print(f"  改 {len(report['changed'])} 张 / 已有数字 {len(report['intact'])} 张")
        for row in report["changed"]:
            print(f"   ✏️  {row['bucket']:<10} {row['ticker']:<6} {row['regime']}  "
                  f"{row['was']!r} → {row['now']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
