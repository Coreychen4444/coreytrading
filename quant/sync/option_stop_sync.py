#!/usr/bin/env python3
"""option_stop_sync.py — 期权卡 stop 跟随正股的授权 gate 上移 (只升不降)。

为什么存在:
    期权的正股退出线 = 授予 cap 的那道 gate (exit_guard S5g, `sizing.granting_gate_level`;
    期权卡 stop 映射正股卡授予 cap 的 gate)。正股越过新 gate 时转移机写下新的 up 转移,
    S5g 当轮即读到新 gate, 退出栈本身是对的; 但期权卡 `zones.stop_level` 若还躺着建仓时
    手写的旧线 —— brief 的 stop 行、卡片显示、任何直读卡 stop 的消费者都在报一条已经不生效
    的线。**同一条退出线两个数字** = 人看卡时以为还有空间, 实际只剩一小截。

    手工映射之后没人再改 —— 手工步骤 = 不存在。

判据 (对账不是事件, 幂等, 漏跑一轮下轮补上):
    期权卡 (在持 / 受管 / `stop_level_basis == "underlying"`) 的 stop < 正股卡
    `granting_gate_level` → 抬到 gate, days = exit_guard `opt_gate_stop_days` (1)。

不动手的情况 (保守方向才是安全方向):
    · gate 为 None (常规参与卡 / 参与流被 down 转移或平仓打断) —— 不降、不清。
    · 卡 stop ≥ gate —— 期权退出线允许**严于** gate (盘面价位), 不覆盖。
    · `stop_level_basis` 不是 underlying (option_price 基 / 未标基准) —— 不同基不比, 不猜。
    · reversal_probe 卡 —— 它的 gate 就是卡自己的止跌低 (exit_guard 分流), 不跟正股转移。
    · lots 的 stop_loss 不动: lot 记的是成交当时的计划, 不是现行退出线。

CLI:
    python3 quant/sync/option_stop_sync.py --json
    python3 quant/sync/option_stop_sync.py --dry-run
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from quant.core import env  # noqa: F401,E402  (.env → os.environ)

import argparse  # noqa: E402
import json  # noqa: E402
from typing import Any, Dict, List, Optional  # noqa: E402

from quant.core.holdings import held_bare, is_managed  # noqa: E402
from quant.core.num import coerce_float  # noqa: E402
from quant.core.occ import parse_occ  # noqa: E402
from quant.core.sizing import granting_gate_level  # noqa: E402
from quant.core.timeutil import et_today  # noqa: E402
from quant.sync import state_io  # noqa: E402

SYNCER = "option_stop_sync"
GATE_STOP_DAYS = 1      # = exit_guard PARAMS["opt_gate_stop_days"] (S5g 收盘 ×1)
EPS = 1e-6


def _gate_entry(card: Optional[dict], level: float) -> dict:
    """授予 cap 的那条 up 转移 (与 granting_gate_level 同一条) —— 只为写来历。"""
    hist = ((card or {}).get("agent") or {}).get("regime_history") or []
    for h in reversed(hist):
        if str(h.get("direction")) == "up" and coerce_float(h.get("level")) == level:
            return h
    return {}


def plan(doc: dict) -> List[Dict[str, Any]]:
    """→ [{ticker, underlying, action: raise|skip, ...}]。纯函数, 不写。"""
    tickers = doc.get("tickers") or {}
    out: List[Dict[str, Any]] = []
    for t in sorted(held_bare(doc)):
        occ = parse_occ(t)
        body = tickers.get(t) or {}
        if not occ or not is_managed(body):
            continue
        agent = body.get("agent") or {}
        zones = agent.get("zones") or {}
        und = agent.get("linked_underlying") or occ["underlying"]
        row: Dict[str, Any] = {"ticker": t, "underlying": und}
        if agent.get("entry_class") == "reversal_probe":
            out.append({**row, "action": "skip", "why": "reversal_probe: gate = 卡自己的止跌低"})
            continue
        if zones.get("stop_level_basis") != "underlying":
            out.append({**row, "action": "skip",
                        "why": f"stop 基准 {zones.get('stop_level_basis')!r} 不是 underlying, 不同基不比"})
            continue
        und_card = tickers.get(und)
        gate = granting_gate_level(und_card)
        stop = coerce_float(zones.get("stop_level"))
        row.update(gate=gate, stop=stop)
        if gate is None:
            out.append({**row, "action": "skip", "why": "正股卡无授予 cap 的 gate (不降不清)"})
        elif stop is not None and stop >= gate - EPS:
            out.append({**row, "action": "skip", "why": "卡 stop ≥ gate (严于 gate 合法, 不覆盖)"})
        else:
            h = _gate_entry(und_card, gate)
            out.append({**row, "action": "raise", "gate_date": str(h.get("date") or "")[:10],
                        "gate_reason": h.get("reason") or ""})
    return out


def apply(items: List[Dict[str, Any]], doc: dict) -> int:
    today = et_today()
    n = 0
    for it in items:
        if it["action"] != "raise":
            continue
        zones = doc["tickers"][it["ticker"]]["agent"].setdefault("zones", {})
        old = it.get("stop")
        zones["stop_level"] = round(float(it["gate"]), 4)
        zones["stop_level_days"] = GATE_STOP_DAYS
        zones["stop_derivation"] = (
            f"{SYNCER} {today}: 跟随 {it['underlying']} 授予 cap 的 gate {it['gate']:g}"
            f"（{it.get('gate_date') or '?'} up 转移；{it.get('gate_reason') or ''}）。"
            f"原 stop {old if old is not None else '无'} → {it['gate']:g}，只升不降；"
            f"收盘 ×{GATE_STOP_DAYS}，盘中破可手动走（严于 gate 合法、不会被本步覆盖）。")
        n += 1
    return n


def run(*, dry_run: bool = False) -> dict:
    items = plan(state_io.read_positions())
    if dry_run or not any(i["action"] == "raise" for i in items):
        return {"dry_run": dry_run, "raised": 0, "items": items}
    with state_io.state_lock():
        doc = state_io.read_positions()          # 持锁后重读, 免得踩别人的写
        items = plan(doc)
        n = apply(items, doc)
        if n:
            state_io.write_positions(doc)        # schema 校验在这层
    return {"dry_run": False, "raised": n, "items": items}


def render(res: dict) -> str:
    lines = []
    for it in res["items"]:
        if it["action"] == "raise":
            verb = "将抬" if res["dry_run"] else "已抬"
            lines.append(f"⬆️ {it['ticker']}: stop {it.get('stop')} → {it['gate']:g} "
                         f"({verb}; {it['underlying']} gate {it.get('gate_date') or '?'})")
        else:
            lines.append(f"· {it['ticker']}: {it['why']}")
    return "\n".join(lines) or "无在持期权卡"


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true", help="只算不写")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    res = run(dry_run=a.dry_run)
    print(json.dumps(res, ensure_ascii=False, indent=2) if a.json else render(res))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
