#!/usr/bin/env python3
"""Auto-migrate tickers between watchlist and current based on actual portfolio holdings (v2 state).

When a watchlist ticker (state/watchlist.json) appears in state/positions.json
broker block (broker.qty != 0) → migrate the agent block to positions.json.
When a positions ticker is no longer present in broker sync → flag stale (no auto-delete).

Usage:
  python3 quant/sync/watchlist_migrate.py              # dry-run
  python3 quant/sync/watchlist_migrate.py --apply      # actually migrate
  python3 quant/sync/watchlist_migrate.py --json       # JSON output
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 直接 `python3 quant/<层>/<模块>.py` 时让 `quant.*` 可 import

from quant.core import env, schema as _schema  # noqa: F401  (.env → os.environ)

import json
from typing import Any, Dict, List, Set

from quant.core.holdings import held_incl_option_underlyings
from quant.core.io_json import atomic_write_json, state_lock
from quant.core.paths import POSITIONS_PATH, WATCHLIST_PATH
from quant.core.structure import derive_card_regime
from quant.core.timeutil import et_today, now_utc_iso as utc_now_iso


def load_doc(path: Path, *, required: bool = False) -> Dict[str, Any]:
    if not path.exists():
        if required:
            raise FileNotFoundError(
                f"{path} missing — refusing to operate on a skeleton (restore from git)"
)
        return {"schema_version": 2, "generated_at": utc_now_iso(), "tickers": {}}
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    data.setdefault("tickers", {})
    return data


def get_active_portfolio_tickers(positions: Dict[str, Any]) -> Set[str]:
    """Tickers with broker.qty != 0 **and underlyings of held options**.

    判据 = `holdings.held_incl_option_underlyings` (单一真源)。与 watchlist_sync
    的 `held_bare` **有意不同**—— 那边管自选分组成员 (入场候选语义, 期权不算),
    这边管卡片促升 (期权的 underlying 需要一张图)。差异的命名与恒等式在
    `holdings.live_tickers` 文档里, 由 tests/core/test_holdings.py 钉住。
    此前这两处各自手抄一份, 谁都没写明不同 →  GOOGL 卡被 stub 覆盖。
    """
    return held_incl_option_underlyings(positions)


def _erases_card(existing: Dict[str, Any], incoming: Dict[str, Any]) -> bool:
    """这次促升会不会拿一张空卡覆盖掉一张有图的卡。

    `run_migration` 的合并假设是「新卡更好」—— 对分析过的候选成立, 对
    `watchlist_sync.make_stub()` 自动生成的空卡**不成立**: stub 更新但更空。
    `_archive_prior_campaign` 有守卫, 但它防的是「别归档垃圾」(旧卡为空则不存),
    **没有任何一侧检查进来的那张是不是空的**—— 守卫装在了不会出事的方向上。
    """
    return bool(((existing or {}).get("agent") or {}).get("zones")) and \
        not (((incoming or {}).get("agent") or {}).get("zones"))


def blocked_promotions(pos_doc: Dict[str, Any], wl_doc: Dict[str, Any]) -> List[str]:
    """会被守卫拦下的促升 —— **对账不是事件**, 两份 JSON 的纯函数。

    拦下之后候选**留在 watchlist**, 于是这个条件是稳定的、每轮重新算得出来的,
    portfolio_brief 因此能把它当告警 (`INFRA:promote_blocked`) 报出去。
    换成"促升那一刻记个事件"就又是一次性的了 —— 漏跑那轮就永远错过
    (position-tiers.md 的对账式幂等, 同 close_reconcile / lot_reconcile)。
    """
    held = get_active_portfolio_tickers(pos_doc)
    wl = wl_doc.get("tickers") or {}
    pos = pos_doc.get("tickers") or {}
    return sorted(t for t in set(wl) & held
                  if _erases_card(pos.get(t) or {}, wl[t]))


def candidate_to_position_block(candidate: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a watchlist.json candidate block into a positions.json block.

    Strips watchlist-specific fields (entry_plan, priority); maps action.intent
    to a positions-side ActionIntent. Broker block stays None — broker sync
    will populate on next run.
    """
    instrument = dict(candidate.get("instrument") or {})
    agent_in = dict(candidate.get("agent") or {})

    # Map watchlist intent → position intent (closest neighbor)
    INTENT_MAP = {
        "watching": "hold",
        "wait_for_zone": "hold",
        "scale_in_acc1": "scale_in_acc1",
        "scale_in_acc2": "scale_in_acc2",
        "drop": "exit",
    }
    action_in = dict(agent_in.get("action") or {})
    action_in["intent"] = INTENT_MAP.get(action_in.get("intent"), "hold")
    action_in.setdefault("reason", "auto-migrated from watchlist")
    action_in.setdefault("confidence", "medium")
    # 全量携带 + deny-list 剥离 watchlist 专属字段。此前是 allowlist 搬运,
    # next_regime 不在名单上 →  INTC promote 把 B_repair_probe_98 整个
    # regime 定义(gate→cap 阶梯)静默丢掉, 手工从 watchlist 原文恢复。
    # 「字段在转换中被丢」的修法是换搬运方向, 不是往白名单再加一个名字。
    agent_out: Dict[str, Any] = dict(agent_in)
    agent_out.pop("entry_plan", None)          # watchlist-specific
    agent_out["thesis"] = agent_in.get("thesis") or {
        "summary": "[migrated; pending]",
        "status": "intact",
        "conviction": "medium",
        "last_reviewed": utc_now_iso(),
    }
    agent_out["zones"] = agent_in.get("zones")
    agent_out["action"] = action_in
    # 促升 = 入场授权已行使 (regime 统一): card 源 regime (常规参与等)
    # 改写为「持仓·无加仓授权」; 机器源 (auto/demoted — gate 达标转移给的加仓 cap)
    # 由 derive 内部保护原样携带 —— 那本来就是 gate 授权的加仓资格, 消费判定管出组。
    derive_card_regime(agent_out, bucket="positions", today=et_today())

    return {
        "instrument": instrument,
        "broker": None,
        "status": "active",
        "agent": agent_out,
        "lots": {"base": [], "position": []},
        "position_summary": {},
        "pending_orders": [],
    }


def _archive_prior_campaign(existing: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Keep the closed campaign's record before the new card overwrites it.

    A re-entry replaces agent wholesale, which silently destroyed the previous
    campaign's closing note. MU lost this on :

        CLOSED  全清 episode (TSM 财报日板块出清, exit_guard 全线
        FIRED/BROKEN 下当日告警执行, score A-). SELL 9 @ 868.01. Realized +4,409.

    That is one of the few written records of exit_guard alerts being executed
    the same day — the numerator of the exit-side execution rate. Archive it
    rather than let a re-entry erase the campaign that came before.

    Returns the accumulated archive list (oldest first); empty if the existing
    agent block carries nothing worth keeping.
    """
    agent = existing.get("agent") or {}
    prior: List[Dict[str, Any]] = list(agent.get("prior_campaigns") or [])
    action = agent.get("action") or {}
    thesis = agent.get("thesis") or {}
    if not (action.get("reason") or thesis.get("summary")):
        return prior

    entry = {
        "archived_at": utc_now_iso(),
        "intent": action.get("intent"),
        "reason": action.get("reason"),
        "thesis_summary": thesis.get("summary"),
        "last_reviewed": thesis.get("last_reviewed"),
        "invalid_if": agent.get("invalid_if"),
        "zones": agent.get("zones"),
    }
    entry = {k: v for k, v in entry.items() if v is not None}
    # Re-promoting an unchanged card shouldn't stack duplicates.
    if prior and {k: v for k, v in prior[-1].items() if k != "archived_at"} == \
            {k: v for k, v in entry.items() if k != "archived_at"}:
        return prior
    prior.append(entry)
    return prior


def run_migration(apply: bool = False) -> Dict[str, Any]:
    pos_doc = load_doc(POSITIONS_PATH, required=True)
    wl_doc = load_doc(WATCHLIST_PATH)

    held = get_active_portfolio_tickers(pos_doc)
    wl_tickers = set(wl_doc.get("tickers", {}).keys())

    # Watchlist → positions: tickers now actually held
    to_promote = sorted(wl_tickers & held)

    # Stale positions: have an agent block but broker.qty == 0 and not closed
    stale: List[str] = []
    for ticker, body in pos_doc.get("tickers", {}).items():
        if body.get("status") == "closed":
            continue
        broker = body.get("broker") or {}
        qty = broker.get("qty") or 0
        # Skip option underlyings synthetic rows + IBKR pending positions
        if qty == 0 and not broker.get("synthetic"):
            stale.append(ticker)

    # 空卡不许覆盖有图的卡。拦下的候选**留在 watchlist**(不 pop) —— 条件因此稳定,
    # 下轮照样算得出来, 也让 portfolio_brief 能当告警报。
    blocked = blocked_promotions(pos_doc, wl_doc)
    to_promote = [t for t in to_promote if t not in set(blocked)]

    promoted: List[str] = []
    if apply and to_promote:
        for ticker in to_promote:
            candidate = wl_doc["tickers"].pop(ticker)
            new_block = candidate_to_position_block(candidate)
            # If position already exists (e.g. broker added it as stub), merge
            existing = pos_doc["tickers"].get(ticker)
            if existing:
                # Preserve broker + lots + pending_orders + position_summary
                merged = dict(existing)
                merged["instrument"] = new_block["instrument"]
                prior = _archive_prior_campaign(existing)
                merged["agent"] = new_block["agent"]
                if prior:
                    merged["agent"]["prior_campaigns"] = prior
                # status must follow the broker, not whatever the last campaign
                # left behind. setdefault() kept re-entries at "closed", and
                # portfolio_brief drops closed tickers — INTC held qty=2 under
                # status="closed" on  and was invisible to every
                # alert for the whole day.
                qty_held = (merged.get("broker") or {}).get("qty") or 0
                merged["status"] = "active" if qty_held else merged.get("status", "active")
                pos_doc["tickers"][ticker] = merged
            else:
                pos_doc["tickers"][ticker] = new_block
            promoted.append(ticker)

        pos_doc["generated_at"] = utc_now_iso()
        wl_doc["generated_at"] = utc_now_iso()
        pos_errors, pos_warnings = _schema.validate_positions_doc(pos_doc)
        wl_errors, wl_warnings = _schema.validate_watchlist_doc(wl_doc)
        report = _schema.format_report(pos_errors, pos_warnings, label="positions.json")
        report_wl = _schema.format_report(wl_errors, wl_warnings, label="watchlist.json")
        for r in (report, report_wl):
            if r:
                print(r, file=sys.stderr)
        if pos_errors or wl_errors:
            raise RuntimeError("migration aborted — schema validation failed (see stderr)")
        with state_lock():
            atomic_write_json(POSITIONS_PATH, pos_doc)
            atomic_write_json(WATCHLIST_PATH, wl_doc)

    result: Dict[str, Any] = {
        "active_portfolio_tickers": sorted(held),
        "to_promote": to_promote,
        "promoted": promoted,
        "promote_blocked": blocked,
        "stale_position_zones": stale,
        "applied": apply,
    }
    if blocked:
        result["promote_blocked_warning"] = (
            f"促升被拦: {blocked} —— watchlist 卡无 zones, positions 卡有, "
            f"覆盖会毁图 (2026-08-06 GOOGL 案)。候选留在 watchlist 未动; "
            f"要么跑 /trading-analysis 把候选卡补全, 要么从自选分组移除。"
)
    if stale:
        result["stale_warning"] = (
            f"These tickers have an agent block in positions.json but broker.qty=0 "
            f"and are not marked closed: {stale}. Review manually — sold/expired?"
)
    return result


def main() -> int:
    apply = "--apply" in sys.argv
    want_json = "--json" in sys.argv

    result = run_migration(apply=apply)

    if want_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        if result["to_promote"]:
            if apply:
                print(f"Migrated watchlist -> positions: {result['promoted']}")
            else:
                print(f"Would migrate watchlist -> positions: {result['to_promote']}")
                print("   Run with --apply to execute.")
        else:
            print("No watchlist tickers need migration.")

        if result.get("promote_blocked"):
            print(f"⚠️ 促升被拦 (空卡不许覆盖有图的卡): {result['promote_blocked']}")
            print(f"   {result['promote_blocked_warning']}")

        if result["stale_position_zones"]:
            print(
                f"Stale agent blocks in positions.json (broker.qty=0, not closed): {result['stale_position_zones']}"
)
            print("   Review manually — may be sold or expired positions.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
