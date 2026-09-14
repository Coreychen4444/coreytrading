#!/usr/bin/env python3
"""migrate_volume_leg_threshold — 卡片量腿阈值 1.2 → 1.1 的一次性迁移 (预注册回测确定 θ=1.1)。

**为什么需要它**: 改 `regime_transition.RECLAIM_VOL_RATIO_MIN` **不等于**改 live —— 转移机与报表读的是
**卡片自己的字段** `volume_ratio_min`（两桶每张活跃卡都写死 1.2，那个常量只是机器补齐 reclaim gate 时的缺省）。
不迁卡 = 常量改了、行为一点没变 = 本仓「规则不进代码 = 不存在」的镜像版（进了代码但没进数据）。

迁移的字段路径（全部在 `agent` 块内，逐条枚举、不做通配递归——通配会连注释性字段一起改）:
    agent.zones.reclaim_gates[].volume_ratio_min
    agent.next_regime.entry_gate / next_gate_preview .volume_ratio_min
    agent.next_regime.preserved_upper_ladder.entry_gate / next_gate_preview .volume_ratio_min
    agent.last_regime.zones.reclaim_gates[].volume_ratio_min
    agent.last_regime.repair_gate.volume_ratio_min

**只改恰好等于 FROM 的值**（默认 1.2 → 1.1）: 卡上写了别的数 = 有人显式裁过的例外，原样保留
（`agent_submit` 的「不得放宽证据标准」守卫同款语义）。幂等: 跑第二遍 0 改动。

写路径 = `state_io.write_positions` / `write_watchlist`（schema 校验 + 原子写），全程持 `state_lock`。
默认 **dry-run**，`--apply` 才落盘。

Usage:
    python3 quant/ops/migrate_volume_leg_threshold.py            # 预览
    python3 quant/ops/migrate_volume_leg_threshold.py --apply    # 落盘
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 直接 `python3 quant/<层>/<模块>.py` 时让 `quant.*` 可 import

import argparse  # noqa: E402
import json  # noqa: E402
from typing import Dict, List, Tuple  # noqa: E402

from quant.core.io_json import state_lock  # noqa: E402
from quant.sync import state_io  # noqa: E402

FROM_RATIO = 1.2      # 换相对尺时沿用的 convention (从未被回测)
TO_RATIO = 1.1        # adaptive_volume_leg round-3 确定 θ=1.1
EPS = 1e-9
FIELD = "volume_ratio_min"


def _gate_dicts(agent: Dict) -> List[Tuple[str, Dict]]:
    """枚举一张卡里所有**带量腿的 gate dict** → [(路径, dict)]。逐条枚举, 不递归通配。"""
    out: List[Tuple[str, Dict]] = []

    def add(trail: str, node) -> None:
        if isinstance(node, dict):
            out.append((trail, node))

    def gates_of(container: Dict, trail: str) -> None:
        for key in ("entry_gate", "next_gate_preview", "repair_gate"):
            add(f"{trail}.{key}", container.get(key))
        zones = container.get("zones")
        if isinstance(zones, dict):
            for i, g in enumerate(zones.get("reclaim_gates") or []):
                add(f"{trail}.zones.reclaim_gates[{i}]", g)

    zones = agent.get("zones")
    if isinstance(zones, dict):
        for i, g in enumerate(zones.get("reclaim_gates") or []):
            add(f"agent.zones.reclaim_gates[{i}]", g)
    for block in ("next_regime", "last_regime", "current_regime"):
        node = agent.get(block)
        if not isinstance(node, dict):
            continue
        gates_of(node, f"agent.{block}")
        upper = node.get("preserved_upper_ladder")
        if isinstance(upper, dict):
            gates_of(upper, f"agent.{block}.preserved_upper_ladder")
    return out


def migrate_doc(doc: Dict, *, frm: float = FROM_RATIO, to: float = TO_RATIO):
    """就地改 doc → (changed, skipped)。changed/skipped = [(ticker, 路径, 旧值)]。"""
    changed, skipped = [], []
    for ticker, card in sorted((doc.get("tickers") or {}).items()):
        agent = card.get("agent")
        if not isinstance(agent, dict):
            continue
        for trail, gate in _gate_dicts(agent):
            if FIELD not in gate:
                continue
            val = gate[FIELD]
            if not isinstance(val, (int, float)):
                skipped.append((ticker, trail, val))
                continue
            if abs(float(val) - frm) <= EPS:
                gate[FIELD] = to
                changed.append((ticker, trail, val))
            elif abs(float(val) - to) > EPS:
                skipped.append((ticker, trail, val))   # 显式裁过的例外, 不动
    return changed, skipped


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="落盘 (缺省 = dry-run)")
    ap.add_argument("--from-ratio", type=float, default=FROM_RATIO)
    ap.add_argument("--to-ratio", type=float, default=TO_RATIO)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    with state_lock():
        pos, wl = state_io.read_positions(), state_io.read_watchlist()
        pc, ps = migrate_doc(pos, frm=a.from_ratio, to=a.to_ratio)
        wc, ws = migrate_doc(wl, frm=a.from_ratio, to=a.to_ratio)
        if a.apply and (pc or wc):
            if pc:
                state_io.write_positions(pos)
            if wc:
                state_io.write_watchlist(wl)

    report = {"from": a.from_ratio, "to": a.to_ratio, "applied": bool(a.apply),
              "positions": {"changed": len(pc), "skipped": len(ps)},
              "watchlist": {"changed": len(wc), "skipped": len(ws)},
              "changed_detail": [{"bucket": b, "ticker": t, "path": p, "was": v}
                                 for b, rows in (("positions", pc), ("watchlist", wc)) for t, p, v in rows],
              "skipped_detail": [{"bucket": b, "ticker": t, "path": p, "value": v}
                                 for b, rows in (("positions", ps), ("watchlist", ws)) for t, p, v in rows]}
    if a.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
    else:
        head = "已落盘" if a.apply else "dry-run (未落盘)"
        print(f"量腿阈值迁移 {a.from_ratio:g} → {a.to_ratio:g} — {head}")
        print(f"  positions: 改 {len(pc)} / 保留 {len(ps)}   watchlist: 改 {len(wc)} / 保留 {len(ws)}")
        for row in report["changed_detail"]:
            print(f"   ✏️  {row['bucket']:<10} {row['ticker']:<6} {row['path']}")
        for row in report["skipped_detail"]:
            print(f"   ⏭️  {row['bucket']:<10} {row['ticker']:<6} {row['path']} = {row['value']} (显式例外, 不动)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
