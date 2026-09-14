#!/usr/bin/env python3
"""close_reconcile.py — 平仓后的卡片收尾, 从手工步骤变成机械步骤。

为什么存在 (用户 定: "自动化处理"):
    broker.qty 归零之后, 卡片还要做三件事: 清 lots、标 status=closed、落 exit_info。
    position-tiers.md §"卖出时 Moomoo 执行"第 3 步把这三件写成**手工**, 于是它们
    只在"当天恰好有人开 session"时才发生。NBIS  16:48 ET 清仓, 事件当晚
    就进了 trades.jsonl、broker.qty 当晚就是 0.0、regime_transition 当晚就把卡降了
    档 —— 三个模块都干了活, 唯独这一步没有模块, 于是卡片带着 1 股 lot 和
    status="active" 过了一整天。schema 早就报了 drift, 但只 print 到 stderr,
    定时任务没人读。

    手工步骤 + 无人看的警告 = 这一步实际上不存在。

判据是**对账不是事件**:
    触发条件是显式 `broker.qty == 0` 且状态未收尾或仍有现行分析/lots, 不是"收到了一条 close 事件"。
    对账幂等、与事件到达顺序无关、漏跑一轮下一轮补上; 事件驱动则要求恰好那一轮
    跑到, 且一次失败就永久错过。

不删数据, 只搬家:
    lots 原样归档进 `exit_info.closed_lots` —— cost basis 留在文件里, 不是只留在
    git 历史里。自动写入之所以敢做, 是因为它不销毁任何东西。

只处理**全平**:
    部分平仓 (qty 3→1) 要决定哪个 lot 被卖掉 (FIFO vs journal 声明的 tier 归属),
    这是判断不是对账 —— 留给人, 由 `core.holdings.flat_with_lots` 之外的
    schema warning 继续报。这里不猜。

CLI:
    .venv/moomoo/bin/python3 quant/sync/close_reconcile.py --json
    .venv/moomoo/bin/python3 quant/sync/close_reconcile.py --dry-run
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from quant.core import env  # noqa: F401,E402  (.env → os.environ)

import copy
import argparse  # noqa: E402
import json  # noqa: E402
from typing import Any, Dict, List  # noqa: E402

from quant.core import ledger as _ledger  # noqa: E402
from quant.core.holdings import held_incl_option_underlyings, lot_qty  # noqa: E402
from quant.sync import state_io  # noqa: E402

EMPTY_SUMMARY = {
    "base_qty": 0, "base_cost_total": 0.0,
    "position_qty": 0, "position_cost_total": 0.0,
    "total_qty": 0, "total_cost": 0.0,
    "pnl_tracking_method": "absolute_dollar_per_tier_independently",
}


def _last_closed_episode(ticker: str, episodes: Dict[str, List[dict]]) -> dict | None:
    """最近一个已平的 episode。realized_pnl / closed_at 的真源是事件流,
    不是卡片 —— 这里只引用, 不重算 (重算 = 两套账)。"""
    closed = [e for e in episodes.get(ticker) or [] if e.get("status") == "closed"]
    return max(closed, key=lambda e: e.get("closed_at") or "") if closed else None


def plan(doc: dict, episodes: Dict[str, List[dict]] | None = None) -> List[dict]:
    """返回待收尾的卡片, 不写盘。episodes 传 None 时从事件流重建。"""
    if episodes is None:
        episodes = _ledger.build_episodes(warn=lambda _m: None)
    live = held_incl_option_underlyings(doc)
    out: List[dict] = []
    for ticker, body in (doc.get("tickers") or {}).items():
        if ticker in live or (body.get("broker") or {}).get("qty") != 0:
            continue
        agent = body.get("agent") or {}
        archive = bool(agent.get("zones") or agent.get("current_regime"))
        finalize = bool(lot_qty(body) or body.get("status") != "closed")
        if not archive and not finalize:
            continue
        ep = _last_closed_episode(ticker, episodes)
        exits = (ep or {}).get("exits") or []
        last = exits[-1] if exits else {}
        out.append({
            "ticker": ticker,
            "finalize": finalize,
            "archive_analysis": archive,
            "synthetic_retired": bool((body.get("broker") or {}).get("synthetic") and ep is None),
            "lot_qty": lot_qty(body),
            "prev_status": body.get("status"),
            "closed_at": (ep or {}).get("closed_at"),
            "realized_pnl_usd": (ep or {}).get("realized_pnl"),
            "exit_order_id": last.get("order_id"),
            "exit_price": last.get("price"),
            "episode_found": ep is not None,
        })
    return out


def apply(items: List[dict], doc: dict) -> dict:
    """把 plan() 的结果写进 doc (原地)。调用方负责持锁与落盘。"""
    for item in items:
        body = doc["tickers"][item["ticker"]]
        exit_info = dict(body.get("exit_info") or {})
        if item.get("archive_analysis"):
            # Full historical snapshot, outside the live authorization namespace.
            previous = exit_info.get("closed_agent")
            if previous and previous != body.get("agent"):
                exit_info.setdefault("prior_closed_agents", []).append(previous)
            exit_info["closed_agent"] = copy.deepcopy(body.get("agent") or {})
            body["agent"] = {
                "thesis": {"summary": "已平仓分析归档于 exit_info.closed_agent", "status": "intact"},
                "action": {"intent": "cleanup", "reason": "历史卡，无现行入场授权"},
            }
        if not item.get("finalize", True):
            body["exit_info"] = exit_info
            continue
        exit_info.update({
            "closed_at": item["closed_at"],
            "order_id": item["exit_order_id"],
            "exit_price": item["exit_price"],
            "realized_pnl_usd": item["realized_pnl_usd"],
            "reconciled_by": "close_reconcile",
            # 归档而非删除 —— cost basis 留在文件里, 不是只留在 git 历史里
            "closed_lots": (body.get("lots") if lot_qty(body) else
                            exit_info.get("closed_lots", {"base": [], "position": []})),
        })
        if item.get("synthetic_retired"):
            exit_info["note"] = "期权标的行情行已停用；非正股平仓，不推定正股成交日期或盈亏。"
        elif not item["episode_found"]:
            exit_info["note"] = ("事件流里没有对应的已平 episode —— broker 说 0 股但账本"
                                 "对不上, 需人工查 events/。lots 已归档未丢失。")
        body["exit_info"] = exit_info
        body["lots"] = {"base": [], "position": []}
        body["position_summary"] = dict(EMPTY_SUMMARY)
        body["status"] = "closed"
    return doc


def run(*, dry_run: bool = False) -> dict:
    doc = state_io.read_positions()
    items = plan(doc)
    if not items or dry_run:
        return {"dry_run": dry_run, "reconciled": [] if dry_run else [], "planned": items}
    with state_io.state_lock():
        doc = state_io.read_positions()          # 持锁后重读, 免得踩别人的写
        items = plan(doc)
        apply(items, doc)
        state_io.write_positions(doc)            # schema 校验在这层, 坏结构写不进去
    return {"dry_run": False, "reconciled": items, "planned": items}


def render(res: dict) -> str:
    items = res.get("planned") or []
    if not items:
        return "close_reconcile: 无待收尾卡片 (状态、lots、现行分析均已对账)"
    verb = "待收尾" if res.get("dry_run") else "已收尾"
    lines = [f"close_reconcile: {verb} {len(items)} 张卡"]
    for it in items:
        if not it.get("finalize", True):
            lines.append(f"  {it['ticker']}: 现行分析移入 exit_info.closed_agent；原平仓记录保留")
            continue
        if it.get("synthetic_retired"):
            lines.append(f"  {it['ticker']}: 期权标的行情行停用 → closed；非正股成交")
            continue
        pnl = it["realized_pnl_usd"]
        pnl_s = f"{pnl:+,.2f}" if isinstance(pnl, (int, float)) else "?"
        lines.append(
            f"  {it['ticker']}: 清 {it['lot_qty']:g} 股 lots (status "
            f"{it['prev_status']}→closed) | 平于 {str(it['closed_at'])[:19] or '?'} "
            f"@ {it['exit_price'] or '?'} | 实现 ${pnl_s}"
            + ("" if it["episode_found"] else "  ⚠️ 事件流无对应 episode, 需人工查"))
    lines.append("  → lots 已归档进 exit_info.closed_lots (未删除); "
                 "评分仍需 annotate_event --score")
    return "\n".join(lines)


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="只印计划, 不写盘")
    a = ap.parse_args(argv)

    res: Dict[str, Any] = run(dry_run=a.dry_run)
    print(json.dumps(res, ensure_ascii=False) if a.json else render(res))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
