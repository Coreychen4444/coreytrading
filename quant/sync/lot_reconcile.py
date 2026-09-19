#!/usr/bin/env python3
"""lot_reconcile.py — 买入成交后的 lot 落账, 从手工步骤变成机械步骤。

为什么存在 (用户 定"计划内成交不需要注解"之后):
    注解那一步被取消后, 规则内成交**唯一**剩下的人工动作就是往 positions.json
    的 lots.position 里补一条 lot 并更新 position_summary。而这条动作是纯机械的:
    qty / price / date / order_id 全都已经在 events/<YYYY-MM>/trades.jsonl 的
    fill 事件里, 计划字段 (stop / target) 全都已经在卡片 agent.zones 里。
    没有任何需要判断的东西 —— 却没有任何模块负责。

    于是它是 close_reconcile 的镜像病例, 同一个形状 (AGENTS.md 记了三次):
       INTC   残留 lot
       NBIS   平仓后卡片带 1 股 lot 过了一整天
       AAOI (2@123) + INTC (2@98.7 + 2@97.8) 各漏一天
    最后这次是 schema 的 `lots qty != broker qty` warn 抓到的 —— 而那条 warn
    只 print 到 stderr。**手工步骤 + 无人看的警告 = 这一步实际上不存在。**

判据是**对账不是事件**(与 close_reconcile 同):
    触发条件 = `sum(lots) != broker.qty` 且事件流的进行中 episode 里有未入账的
    BUY fill (按 order_id 去重)。不是"收到了一条 fill 事件"。对账幂等、与事件
    到达顺序无关、漏跑一轮下一轮补上; 事件驱动则要求恰好那一轮跑到。

只自动处理**增量 BUY**:
    · tier 恒 position —— base 是结构豁免层 (holdings.UNMANAGED_ROLES), 它的
      归属由结构授予, 不由成交产生。unmanaged 卡整张跳过。
    · 部分卖出 (lots 5 股 vs broker 3 股, 或进行中 episode 里已有 exit) 要决定
      **哪个 lot 被卖掉**—— 判断不是对账, 归人工, 与 close_reconcile 划同一条界。
    · 期权跳过 —— lot 要 Greeks 入场快照 (options_journal), stop 还要判
      underlying / option_price 基准, 都是判断。
    · 数量对不上 (未入账 BUF 合计 ≠ 缺口) 一律不写, 只报 —— **这里不猜**。

安全网: 降级出来的这些"不动手"的情况会经 portfolio_brief.lot_drift() 变成
INFRA:lot_drift 告警 (推 TG), 不是只留在本模块的 stdout 里。

CLI:
    .venv/moomoo/bin/python3 quant/sync/lot_reconcile.py --json
    .venv/moomoo/bin/python3 quant/sync/lot_reconcile.py --dry-run
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from quant.core import env  # noqa: F401,E402  (.env → os.environ)

import argparse  # noqa: E402
import json  # noqa: E402
from typing import Any, Dict, List, Optional  # noqa: E402

from quant.core import ledger as _ledger  # noqa: E402
from quant.core.holdings import is_managed, lot_qty  # noqa: E402
from quant.core.num import coerce_float  # noqa: E402
from quant.core.occ import contract_multiplier, is_option_ticker  # noqa: E402
from quant.core.structure import ZONE_LABELS, anchor_date, zone_bands, zone_position  # noqa: E402
from quant.core.timeutil import ET, parse_ts  # noqa: E402
from quant.sync import state_io  # noqa: E402

QTY_TOL = 1e-6
TIERS = ("base", "position")
RECONCILER = "lot_reconcile"
# 事件流的 zone_at_fill 比 zone_position 多一个值 (schema.ZONE_AT_FILL)
FILL_TAG_LABELS = {**ZONE_LABELS, "unknown": "未标注区位"}


# ── 已入账判定 ────────────────────────────────────────────────────

def accounted_order_ids(body: dict) -> set[str]:
    """卡片 lots 里已经记着的 order_id。

    两条来源, 缺一不可:
      · `lot["order_id"]`  本模块写的结构化字段
      · lot 里任何字符串值中出现的 order id   之前手写的 lot 把它写在
        notes 散文里 ("order FS1CEFF99C2BBA1000; 姊妹档...")。只认结构化字段的话,
        第一次跑就会把那些手写 lot 对应的 fill 再加一遍。
    """
    out: set[str] = set()
    for tier in TIERS:
        for lot in ((body.get("lots") or {}).get(tier) or []):
            if not isinstance(lot, dict):
                continue
            oid = lot.get("order_id")
            if oid:
                out.add(str(oid))
            for v in lot.values():
                if isinstance(v, str):
                    out.add(v)          # 整串留着, 由 _is_accounted 做子串判定
    return out


def _is_accounted(order_id: str, blob: set[str]) -> bool:
    return any(order_id == s or order_id in s for s in blob)


def untagged_lots(body: dict, episode_order_ids: set[str]) -> List[dict]:
    """Return lots that cannot be associated with any known episode order.

    A lot without a structured ``order_id`` can still be accounted for when an
    order id appears in a legacy free-text field. A truly untagged lot prevents
    idempotent fill reconciliation: every later incremental buy includes its old
    fill again, so the pending quantity remains larger than the broker/lot gap.
    """
    out: List[dict] = []
    for tier in TIERS:
        for lot in ((body.get("lots") or {}).get(tier) or []):
            if not isinstance(lot, dict) or lot.get("order_id"):
                continue
            strings = {value for value in lot.values() if isinstance(value, str)}
            if any(order_id and _is_accounted(order_id, strings)
                   for order_id in episode_order_ids):
                continue
            out.append(lot)
    return out


# ── 计划 ──────────────────────────────────────────────────────────

def _open_episode(ticker: str, episodes: Dict[str, List[dict]]) -> Optional[dict]:
    live = [e for e in episodes.get(ticker) or [] if e.get("status") == "open"]
    return live[-1] if live else None


def _skip(ticker: str, why: str) -> dict:
    return {"ticker": ticker, "action": "skip", "reason": why}


def _blocked(ticker: str, why: str, **extra) -> dict:
    return {"ticker": ticker, "action": "needs_human", "reason": why, **extra}


def _entry_period(ts: Any) -> str:
    """事件时间戳 → 'YYYY-MM-DD HH:MM ET'。事件流写的是带 ET offset 的 ISO,
    但这里照样显式转 ET —— 靠 offset 恰好是 ET 是约定, 不是保证。"""
    dt = parse_ts(str(ts or ""))
    return dt.astimezone(ET).strftime("%Y-%m-%d %H:%M ET") if dt else str(ts or "?")


def _zone_logic(price: float, agent: dict, fills: List[dict]) -> str:
    """成交价落在哪个 zone + 当时的 regime。

    zone 判别式与 entry_guard 同一份 (core.structure), 不另抄一套 —— 否则入场
    计划说 z1、lot 台账说等待区。

    定位口径按可得性分两级, **并且写出用的是哪一级**:
      1. 事件流的 `zone_at_fill`  成交当时记下的读数, 优先 (注解过的 fill 才有)
      2. 当前卡片 zones          回落。卡片锚定日晚于成交日 = 这是**回溯**定位,
         中间那次重画会让读数变掉 (AAOI 8/5 成交在当时 z1 上沿, 8/6 print 后
         重画, 同一笔按新卡读成"等待区") —— 必须标出来, 不能让回溯读数冒充
         当时的判断。
    """
    regime = agent.get("current_regime") or {}
    cap = coerce_float(regime.get("cap_pct_nav"))
    bits = []
    if regime.get("name"):
        bits.append(f"regime {regime['name']}"
                    + (f" (cap {cap:.1%} NAV" if cap is not None else " (cap ?")
                    + (f", since {regime['since']})" if regime.get("since") else ")"))

    tagged = next((str(f.get("zone_at_fill")) for f in fills if f.get("zone_at_fill")), None)
    if tagged:
        return "; ".join([f"成交 {price:g} 落在 {FILL_TAG_LABELS.get(tagged, tagged)} "
                          f"(成交时标注 zone_at_fill)"] + bits)

    zb = zone_bands(agent.get("zones"))
    if not zb:
        return "; ".join([f"成交 {price:g} — 卡片无数值 zone, 未定位"] + bits)
    pos = zone_position(price, zb)
    head = (f"成交 {price:g} 落在 {ZONE_LABELS.get(pos, pos)} "
            f"[z1 {zb['z1'][0]:g}-{zb['z1'][1]:g} / z2 {zb['z2'][0]:g}-{zb['z2'][1]:g}]")
    anchor = anchor_date(agent)
    fill_day = str(fills[0].get("timestamp") or "")[:10]
    if anchor and fill_day and anchor > fill_day:
        head += f" ⚠️ 按当前卡片回溯定位 (卡锚 {anchor} 晚于成交 {fill_day}, 中间重画过)"
    return "; ".join([head] + bits)


def _build_lot(fills: List[dict], agent: dict, order_id: str) -> dict:
    """一个 order_id → 一条 lot (同 order 的多笔部分成交按数量加权合并)。

    计划字段只从卡片**派生**, 不发明: stop_loss ← zones.stop_level,
    target_exit ← trim_zone。catalyst 这类要判断的字段一律不写 —— 空着比编强。
    """
    qty = sum(float(f.get("qty") or 0) for f in fills)
    cost = sum(float(f.get("qty") or 0) * float(f.get("price") or 0) for f in fills)
    price = cost / qty if qty else 0.0
    zones = agent.get("zones") or {}
    lot: Dict[str, Any] = {
        "qty": int(qty) if float(qty).is_integer() else round(qty, 6),
        "avg_cost": round(price, 6),
        "entry_period": _entry_period(fills[0].get("timestamp")),
        "zone_logic": _zone_logic(price, agent, fills),
        "order_id": order_id,
        "reconciled_by": RECONCILER,
        # 只陈述"这条 lot 是机器落的"这个事实 + 常设规则。**不写"计划内成交"**——
        # 这个模块判定不了合不合规 (那要看 cap / 白名单 / 时段), 台账不替人背书。
        "notes": (f"order {order_id}; lot_reconcile 自动落账 — "
                  f"规则外成交另需 annotate_event 注解 + 评分"),
    }
    stop = coerce_float(zones.get("stop_level"))
    if stop is not None:
        lot["stop_loss"] = stop
    # 抄底 (reversal_probe,): 授权住计划不住卡, stop = 止跌低 (auth.support), 与卡 stop 无关;
    # lot 带 entry_class 让 brief 能核「持仓卡有没有盖 entry_class + 止损 = 止跌低」(INFRA:bottom_card_unstamped)
    auth = next((f.get("auth") for f in fills if isinstance(f.get("auth"), dict)), None) or {}
    if auth.get("entry_class") == "reversal_probe":
        lot["entry_class"] = "reversal_probe"
        sup = coerce_float(auth.get("support"))
        if sup is not None:
            lot["stop_loss"] = sup
            lot["notes"] += f"; 抄底 止损 = 止跌低 {sup:g} ×1 (信号 {auth.get('signal_date')})"
    trim = zones.get("trim_zone")
    if isinstance(trim, list) and len(trim) == 2:
        lot["target_exit"] = f"trim {trim[0]:g}-{trim[1]:g}"
    return lot


def plan(doc: dict, episodes: Dict[str, List[dict]] | None = None) -> List[dict]:
    """返回每张待落账卡片的动作 (add_lots / needs_human / skip), 不写盘。

    episodes 传 None 时从事件流重建。skip 项只在 render 里当脚注, 不进告警。
    """
    if episodes is None:
        episodes = _ledger.build_episodes(warn=lambda _m: None)
    out: List[dict] = []
    for ticker, body in (doc.get("tickers") or {}).items():
        bqty = coerce_float((body.get("broker") or {}).get("qty")) or 0.0
        if not bqty:
            continue                     # 0 股 = close_reconcile 的辖区 (含 synthetic 行)
        gap = bqty - lot_qty(body)
        if abs(gap) <= QTY_TOL:
            continue                     # 已对齐 —— 绝大多数轮次走这里
        if not is_managed(body):
            out.append(_skip(ticker, "结构豁免层 (instrument.role) — lot 归属由结构授予, 不由成交产生"))
            continue
        if is_option_ticker(ticker):
            out.append(_skip(ticker, f"期权卡缺口 {gap:+g} — lot 要 Greeks 入场快照 + "
                                     "stop 基准判断 (underlying/option_price), 归人工"))
            continue
        if gap < 0:
            out.append(_blocked(ticker, f"lots 比 broker 多 {abs(gap):g} 股 = 卖出未落账 —— "
                                        "哪个 lot 被卖掉是判断不是对账", gap=gap))
            continue

        ep = _open_episode(ticker, episodes)
        if ep is None:
            out.append(_blocked(ticker, f"缺口 {gap:+g} 股, 但事件流没有进行中的 episode —— "
                                        "成交没落库? 先跑 trade_history_sync", gap=gap))
            continue
        if ep.get("exits"):
            out.append(_blocked(ticker, f"缺口 {gap:+g} 股, 但本轮 campaign 已有 "
                                        f"{len(ep['exits'])} 笔卖出 —— 部分卖出归人工", gap=gap))
            continue
        if ep.get("direction") != "long":
            out.append(_blocked(ticker, f"缺口 {gap:+g} 股, 但 episode 方向是 "
                                        f"{ep.get('direction')} —— 只自动处理增量 BUY", gap=gap))
            continue

        blob = accounted_order_ids(body)
        pending: Dict[str, List[dict]] = {}
        for e in ep.get("entries") or []:
            oid = str(e.get("order_id") or "")
            if not oid or _is_accounted(oid, blob):
                continue
            pending.setdefault(oid, []).append(e)
        pending_qty = sum(float(f.get("qty") or 0) for fs in pending.values() for f in fs)
        if abs(pending_qty - gap) > QTY_TOL:
            episode_order_ids = {
                str(event.get("order_id") or "") for event in (ep.get("entries") or [])
            }
            orphans = untagged_lots(body, episode_order_ids)
            orphan_qty = sum(coerce_float(lot.get("qty")) or 0.0 for lot in orphans)
            hint = ""
            if orphans and abs((pending_qty - gap) - orphan_qty) <= QTY_TOL:
                details = ", ".join(
                    f"{coerce_float(lot.get('qty')) or 0:g}股@"
                    f"{coerce_float(lot.get('avg_cost')) or 0:g}"
                    f"({lot.get('entry_period') or '?'})"
                    for lot in orphans
                )
                hint = (
                    f" ⤷ 差额 {orphan_qty:g} 股 = 卡上挂不上号的 lot [{details}] —— "
                    "lot 缺 order_id，去重无法识别；核对事件流后回填 order_id"
                )
            out.append(_blocked(
                ticker,
                f"缺口 {gap:+g} 股, 未入账 BUY 合计 {pending_qty:g} 股 —— 对不上, 不猜"
                + ("" if pending else " (进行中 episode 的 fill 全部已入账)")
                + hint,
                gap=gap, pending_qty=pending_qty,
                untagged_qty=orphan_qty if orphans else 0.0))
            continue

        agent = body.get("agent") or {}
        lots = [_build_lot(fs, agent, oid)
                for oid, fs in sorted(pending.items(),
                                      key=lambda kv: str(kv[1][0].get("timestamp") or ""))]
        out.append({"ticker": ticker, "action": "add_lots", "gap": gap,
                    "qty_added": pending_qty,
                    "cost_added": round(sum(l["qty"] * l["avg_cost"] for l in lots), 2),
                    "order_ids": [l["order_id"] for l in lots], "lots": lots})
    return out


# ── 落盘 ──────────────────────────────────────────────────────────

def summarize(body: dict, ticker: str) -> dict:
    """按 lots 重算 position_summary (schema 的对账警告读的就是这套算术)。
    保留卡片已有的其它 key —— 这里只负责数字, 不负责改口径。"""
    mult = contract_multiplier(ticker)
    out = dict(body.get("position_summary") or {})
    total_q = total_c = 0.0
    for tier in TIERS:
        q = c = 0.0
        for lot in ((body.get("lots") or {}).get(tier) or []):
            lq = coerce_float(lot.get("qty")) or 0.0
            q += lq
            c += lq * (coerce_float(lot.get("avg_cost")) or 0.0)
        c *= mult
        out[f"{tier}_qty"] = int(q) if float(q).is_integer() else round(q, 6)
        out[f"{tier}_cost_total"] = round(c, 2)
        total_q, total_c = total_q + q, total_c + c
    out["total_qty"] = int(total_q) if float(total_q).is_integer() else round(total_q, 6)
    out["total_cost"] = round(total_c, 2)
    out.setdefault("pnl_tracking_method", "absolute_dollar_per_tier_independently")
    return out


def apply(items: List[dict], doc: dict) -> dict:
    """把 plan() 里 action=add_lots 的项写进 doc (原地)。调用方负责持锁与落盘。"""
    for item in items:
        if item.get("action") != "add_lots":
            continue
        body = doc["tickers"][item["ticker"]]
        lots = body.setdefault("lots", {"base": [], "position": []})
        lots.setdefault("position", []).extend(item["lots"])
        body["position_summary"] = summarize(body, item["ticker"])
    return doc


def run(*, dry_run: bool = False) -> dict:
    doc = state_io.read_positions()
    items = plan(doc)
    if dry_run or not any(i["action"] == "add_lots" for i in items):
        return {"dry_run": dry_run, "planned": items, "reconciled": []}
    with state_io.state_lock():
        doc = state_io.read_positions()          # 持锁后重读, 免得踩别人的写
        items = plan(doc)
        apply(items, doc)
        state_io.write_positions(doc)            # schema 校验在这层
    return {"dry_run": False, "planned": items,
            "reconciled": [i for i in items if i["action"] == "add_lots"]}


def render(res: dict) -> str:
    items = res.get("planned") or []
    added = [i for i in items if i["action"] == "add_lots"]
    blocked = [i for i in items if i["action"] == "needs_human"]
    skipped = [i for i in items if i["action"] == "skip"]
    if not items:
        return "lot_reconcile: 无待落账成交 (lots 与 broker qty 一致)"
    verb = "待落账" if res.get("dry_run") else "已落账"
    lines: List[str] = []
    if added:
        lines.append(f"lot_reconcile: {verb} {len(added)} 张卡")
    for it in added:
        lines.append(f"  {it['ticker']}: +{it['qty_added']:g} 股 / ${it['cost_added']:,.2f} "
                     f"({len(it['lots'])} 笔)")
        for lot in it["lots"]:
            lines.append(f"    · {lot['qty']:g} @ {lot['avg_cost']:g}  {lot['entry_period']}"
                         f"  [{lot['order_id']}]")
            lines.append(f"      {lot['zone_logic']}")
    for it in blocked:
        lines.append(f"  ⚠️ {it['ticker']}: {it['reason']} — 未动手")
    for it in skipped:
        lines.append(f"  ⏭  {it['ticker']}: {it['reason']}")
    if added:
        lines.append("  → tier 恒 position; 计划字段派生自卡片 zones。"
                     "规则外成交仍要 annotate_event 注解 + 评分")
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
