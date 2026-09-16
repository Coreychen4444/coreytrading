"""Render the frozen mechanical risk receipts as a report, without a model.

The risk role's contract already says it
must only *explain* host receipts (entry_cap arms, book usage, exit stack) and
never build a second sizing logic. Its daily output was mostly a restatement
of those receipts plus "receipt missing" unknowns, at ~140k tokens per call.
This module reads the same frozen snapshot fields the role was given and
renders them deterministically. It is a report: it never authorizes an entry,
never overrides the exit stack, and never writes state.
"""
from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import argparse
import json
from typing import Any


def _obj(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _num(value: Any):
    return value if type(value) in (int, float) else None


def _fmt(value: Any, digits: int = 0) -> str:
    number = _num(value)
    if number is None:
        return "unknown"
    return f"{number:,.{digits}f}"


def _pct(value: Any) -> str:
    number = _num(value)
    return "unknown" if number is None else f"{number * 100:.1f}%"


def build(data: dict) -> dict:
    """Structured receipt view from a technical/risk snapshot ``data`` object."""
    receipts = _obj(data.get("portfolio_risk_receipts"))
    holdings = _obj(data.get("holding_risk_inputs"))
    account = _obj(data.get("account_snapshot"))
    power = _obj(data.get("power_room_receipt"))
    usage = _obj(receipts.get("usage"))
    usage_values = _obj(usage.get("values"))
    inputs = _obj(data.get("portfolio_inputs"))
    book = {
        "status": usage.get("status", "unknown"),
        "nav": _num(account.get("total_assets")) if _num(account.get("total_assets")) is not None else _num(inputs.get("nav")),
        "power_room": _num(power.get("room")),
        "used_budget": _num(usage_values.get("used_budget")),
        "used_gross": _num(usage_values.get("used_gross")),
        "assumptions": list(usage.get("assumptions") or []),
        "unknowns": list(usage.get("unknowns") or []),
    }
    entries = []
    for ticker, receipt in sorted(_obj(receipts.get("entries")).items()):
        receipt = _obj(receipt)
        values = _obj(receipt.get("values"))
        inputs = _obj(receipt.get("inputs"))
        entries.append({
            "ticker": ticker, "status": receipt.get("status", "unknown"),
            "final": _num(values.get("final")), "binding": values.get("binding"),
            "gate_no": inputs.get("gate_no"), "price": _num(inputs.get("price")),
            "binding_stop": _num(inputs.get("binding_stop")), "exposure": _num(inputs.get("exposure")),
            "event_shrink": _num(inputs.get("event_shrink")),
            "unknowns": list(receipt.get("unknowns") or []),
        })
    exits = []
    for ticker, holding in sorted(_obj(holdings.get("positions")).items()):
        holding = _obj(holding)
        guard = _obj(holding.get("exit_guard_receipt"))
        ratchet, trail = _obj(guard.get("ratchet")), _obj(guard.get("trail"))
        structure = _obj(guard.get("structure"))
        broker = _obj(_obj(holding.get("card")).get("broker"))
        exits.append({
            "ticker": ticker, "membership": holding.get("membership"),
            "qty": _num(broker.get("qty")),
            "ratchet_stage": ratchet.get("stage"), "ratchet_floor": _num(ratchet.get("floor")),
            "trail": _num(trail.get("trail")), "trail_broken": trail.get("broken"),
            "no_add_lock": guard.get("no_add_lock"),
            "g01_crossed": bool(guard.get("g01_crossed")),
            "trend": structure.get("trend"),
            "unknowns": list(holding.get("unknowns") or []),
        })
    at_risk = _obj(holdings.get("portfolio_at_risk"))
    unknowns = sorted(set(receipts.get("unknowns") or []) | set(holdings.get("unknowns") or []))
    return {"kind": "mechanical_risk_receipt", "authority": "analysis_only",
            "receipt_id": receipts.get("receipt_id"), "as_of": receipts.get("as_of"),
            "freshness": _obj(receipts.get("freshness")).get("status", "unknown"),
            "book": book, "entries": entries, "exits": exits,
            "portfolio_at_risk": at_risk.get("status", "unknown"),
            "batch_allocation": _obj(receipts.get("batch_allocation")).get("status", "unknown"),
            "not_provided_by_design": [
                "portfolio_at_risk: no canonical pure at-risk receipt exists; do not sum ceilings",
                "batch_allocation: single-name ceilings share one budget; no reservations computed",
            ],
            "unknowns": unknowns,
            # 冻结时刻要印在脸上。这份收据是**采集那一刻**的世界, 而盘后流水线里它排在
            # 受控重画**之前** —— 当晚被重画的卡, 表上的价与 stop 仍是旧卡数值,
            # 否则只能靠人手写一句括号说明。
            # 靠人记得加注解 = 迟早漏; 时刻自己说出来, 读者拿它跟重画回执对得上。
            "as_of": receipts.get("as_of")}


def render(view: dict) -> str:
    """GFM text for the scheduled reports (Telegram rich channel renders tables)."""
    book = view["book"]
    lines = ["## 🛡️ 机械风控收据（代码渲染，零授权效力）",
             f"NAV {_fmt(book['nav'])} ｜ 购买力余额 {_fmt(book['power_room'])} ｜ "
             f"冲击预算已用 {_pct(book['used_budget'])} ｜ gross 已用 {_pct(book['used_gross'])} ｜ "
             f"时效 {view['freshness']}"
             + (f" ｜ 冻结于 {view['as_of']}" if view.get("as_of") else "")]
    if view.get("as_of"):
        lines.append(f"> 本表是采集时刻 {view['as_of']} 的快照, **早于同一轮的受控重画** —— "
                     f"重画过的卡, 价 / 真 stop 两列仍是重画前的数值, 以重画回执为准。")
    if view["entries"]:
        lines += ["", "| 票 | 档 | 上限 $ | binding | 价 | 真 stop | 敞口 | 状态 |", "|---|---|---|---|---|---|---|---|"]
        for e in view["entries"]:
            lines.append(f"| {e['ticker']} | {e['gate_no'] if e['gate_no'] is not None else '-'} | "
                         f"{_fmt(e['final'])} | {e['binding'] or '-'} | {_fmt(e['price'], 2)} | "
                         f"{_fmt(e['binding_stop'], 2)} | {_fmt(e['exposure'])} | {e['status']} |")
    if view["exits"]:
        lines += ["", "| 持仓 | 数量 | 棘轮 | 地板 | 趋势线 | 破 | no-add | G-01 | 结构 |", "|---|---|---|---|---|---|---|---|---|"]
        for x in view["exits"]:
            lines.append(f"| {x['ticker']} | {_fmt(x['qty'])} | {x['ratchet_stage'] or '-'} | "
                         f"{_fmt(x['ratchet_floor'], 2)} | {_fmt(x['trail'], 2)} | "
                         f"{'是' if x['trail_broken'] else '否'} | {'ON' if x['no_add_lock'] else 'off'} | "
                         f"{'是' if x['g01_crossed'] else '否'} | {x['trend'] or '-'} |")
    lines += ["", f"组合 at-risk：{view['portfolio_at_risk']}（设计上不提供，单票上限不可相加）；"
                  f"批量分配：{view['batch_allocation']}"]
    if view["unknowns"]:
        lines += ["", "未知项：" + "；".join(view["unknowns"][:12])]
    lines.append("本段由 quant/report/risk_receipt.py 从冻结收据渲染；cap / 股数 / 退出线真源仍是 sizing / exit_guard。")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path, help="a frozen snapshot or run record JSON")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    document = json.loads(args.snapshot.read_text())
    data = _obj(document.get("snapshot", document)).get("data", document.get("data", document))
    view = build(data)
    print(json.dumps(view, ensure_ascii=False, indent=2) if args.json else render(view))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
