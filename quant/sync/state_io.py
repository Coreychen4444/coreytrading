"""state/ I/O helpers for v2 — atomic JSON read/write that preserves agent block.

Thin positions-specific facade over scripts/lib (paths, locking, atomic IO,
schema validation). Other writers (watchlist_migrate, update_position_zones)
use lib directly; this module keeps the stable API that stock_fetch.py and
trade_history_sync.py import.

Critical invariant: when broker sync updates a ticker, ONLY the broker block
is touched. Agent block (thesis, zones, action), lots, position_summary and
pending_orders are preserved verbatim. New tickers (broker reports a position
not yet in positions.json) are added with a stub agent block that the agent
must review and complete.

Every write is schema-validated: structural errors abort the write; drift
warnings (lots↔broker mismatch) print to stderr but don't block.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 直接 `python3 quant/<层>/<模块>.py` 时让 `quant.*` 可 import

from typing import Any

from quant.core import ledger as _ledger, occ as _occ, schema as _schema
from quant.core.io_json import atomic_write_json, read_json, state_lock
from quant.core.paths import EVENTS_DIR, PORTFOLIO_ROOT, POSITIONS_PATH, STATE_DIR, WATCHLIST_PATH  # noqa: F401 (re-exported)
from quant.core.timeutil import now_utc_iso as _now_iso


def read_positions(*, allow_missing: bool = False) -> dict[str, Any]:
    """Read state/positions.json.

    Missing file is an ERROR by default: silently recreating a skeleton here
    would let a broker sync rewrite the whole document with stub agent blocks
    (theses/zones/lots gone) and auto-push it. Pass allow_missing=True only
    for explicit bootstrap flows.
    """
    if not POSITIONS_PATH.exists():
        if allow_missing:
            return {
                "schema_version": 2,
                "generated_at": _now_iso(),
                "tickers": {},
            }
        raise FileNotFoundError(
            f"{POSITIONS_PATH} is missing — refusing to recreate it implicitly. "
            "Restore from git (state/ is committed) or pass allow_missing=True "
            "for a deliberate bootstrap."
)
    return read_json(POSITIONS_PATH)


def write_positions(doc: dict[str, Any]) -> None:
    """Validate + atomically write positions.json. Caller should hold state_lock
    if this is part of a read-modify-write cycle."""
    errors, warnings = _schema.validate_positions_doc(doc)
    report = _schema.format_report(errors, warnings, label="positions.json")
    if report:
        print(report, file=sys.stderr)
    if errors:
        raise RuntimeError(
            f"positions.json failed schema validation ({len(errors)} errors) — write aborted"
)
    atomic_write_json(POSITIONS_PATH, doc)


def read_watchlist(*, allow_missing: bool = False) -> dict[str, Any]:
    """Read state/watchlist.json — 语义与 read_positions 相同 (缺文件默认报错)。
     加: 用户 裁决 watchlist regime 自动切, regime_transition 需要
    合法的 watchlist 读写路径 (write_positions 不能写 watchlist)。"""
    if not WATCHLIST_PATH.exists():
        if allow_missing:
            return {"schema_version": 2, "generated_at": _now_iso(), "tickers": {}}
        raise FileNotFoundError(
            f"{WATCHLIST_PATH} is missing — refusing to recreate it implicitly. "
            "Restore from git or pass allow_missing=True for deliberate bootstrap.")
    return read_json(WATCHLIST_PATH)


def write_watchlist(doc: dict[str, Any]) -> None:
    """Validate + atomically write watchlist.json (镜像 write_positions, 但用
    watchlist 形状校验器 —— positions 校验器会把合法的 watching 卡拦成 75 错)。"""
    errors, warnings = _schema.validate_watchlist_doc(doc)
    report = _schema.format_report(errors, warnings, label="watchlist.json")
    if report:
        print(report, file=sys.stderr)
    if errors:
        raise RuntimeError(
            f"watchlist.json failed schema validation ({len(errors)} errors) — write aborted")
    atomic_write_json(WATCHLIST_PATH, doc)


def update_broker_blocks(
    broker_updates: dict[str, dict[str, Any]],
    *,
    broker_source: str = "moomoo_opend",
    zero_absent: bool = False,
) -> dict[str, Any]:
    """Merge broker updates into state/positions.json. Atomic + locked + validated.

    `broker_updates` maps ticker -> broker-block dict (same shape as the
    `broker` field in the v2 schema). Anything in this dict is overwritten
    on the existing position; agent block + lots are preserved.

    `zero_absent`（补，NBIS 源案例）: 券商这一轮**没返回**的卡, 若它
    还记着 qty≠0, 就把 qty 写成 0 —— "从持仓列表里消失" = 已经平掉了。

    🚨 为什么必须有这一步: 本函数原本是**纯按 ticker 合并**, 券商没返回的卡一个
    字节都不碰。于是一笔在"最后一次同日快照之后"成交的平仓, 会让那张卡的
    `broker.qty` **永远**停在旧值 —— 而下游整条链的判据全是 `broker.qty`:
    close_reconcile 要 qty==0 才收尾、watchlist_sync 要 exit_info 才加回自选、
    `lot_gap` 比的是 lots 与 broker.qty(两个都错, 差为 0, 静默)。
    NBIS  19:59 ET(盘后)卖掉最后 1 股, 两天里一直是"活跃持仓 1 股 @
    陈价 252.32": 拿着 gate1 授权 $4,118、占着冲击预算、当晚还被转移机换了张图,
    全程零告警。AAOI 同日 13:29 ET 平仓却接上了 —— 因为 18:26 ET 那次快照券商
    **还返回它 qty=0**(同日已平仓的行还在列表里)。**能不能收尾取决于平仓时刻
    落在哪次快照之前, 那是时间巧合不是机制。**

    只在调用方能确认"这一轮拿到的是完整持仓列表"时传 True(见 stock_fetch:
    trade API 真返回了行 + 无 ticker 过滤 + 非 --quotes-only)。另外两道护栏在
    本函数内: **只碰 source 相同的卡**(IBKR 的仓永远不在 moomoo 响应里, 碰它
    等于每轮清零一次) + **只把 qty≠0 写成 0**(不新建、不改别的字段)。
    """
    zeroed: list[str] = []
    with state_lock():
        doc = read_positions()
        tickers = doc.get("tickers", {})

        if zero_absent:
            for ticker, body in tickers.items():
                if ticker in broker_updates or not isinstance(body, dict):
                    continue
                br = body.get("broker")
                if not isinstance(br, dict):
                    continue
                if str(br.get("source") or broker_source) != broker_source:
                    continue                      # 别的券商的仓, 不在这份响应的辖区内
                try:
                    qty = float(br.get("qty") or 0)
                except (TypeError, ValueError):
                    continue
                if qty == 0:
                    continue
                body["broker"] = {**br, "qty": 0.0, "as_of": _now_iso(),
                                  "qty_source": "absent_from_broker"}
                zeroed.append(ticker)

        for ticker, broker_block in broker_updates.items():
            ensured_block = dict(broker_block)
            ensured_block.setdefault("source", broker_source)
            ensured_block.setdefault("as_of", _now_iso())

            if ticker in tickers:
                # Update broker block; preserve agent + lots
                tickers[ticker]["broker"] = ensured_block
            else:
                # Stub position for fresh ticker — agent must complete
                tickers[ticker] = {
                    "instrument": _stub_instrument(ticker),
                    "broker": ensured_block,
                    "status": "active",
                    "agent": _stub_agent_block(),
                    "lots": {"base": [], "position": []},
                    "position_summary": {},
                    "pending_orders": [],
                }

        doc["tickers"] = tickers
        doc["generated_at"] = _now_iso()
        write_positions(doc)
        if zeroed:
            # 静默清零 = 把"仓没了"藏起来。下游 close_reconcile 当轮就会收尾,
            # 但这一步本身必须留痕(它改的是账本上的持仓数)。
            print(f"[state_io] 券商未返回, qty 归零 (= 已平仓): {', '.join(sorted(zeroed))}",
                  file=sys.stderr)
        return doc


def append_trade_event(event: dict[str, Any]) -> Path:
    """Validate + append one event to events/<YYYY-MM>/trades.jsonl (ET month)."""
    event = dict(event)
    event.setdefault("timestamp", _now_iso())
    _ledger.append_event(event)
    return EVENTS_DIR / str(event["timestamp"])[:7] / "trades.jsonl"


def _stub_instrument(ticker: str) -> dict[str, Any]:
    parts = _occ.parse_occ(ticker)
    if not parts:
        return {"type": "stock"}
    return {
        "type": "option",
        "option": {
            "underlying": parts["underlying"],
            "strike": parts["strike"],
            "expiry": parts["expiry"],
            "side": parts["side"],
            "exposure": "long",  # default; agent should flip if short
        },
    }


def _stub_agent_block() -> dict[str, Any]:
    """Minimal agent block for new positions auto-detected by broker sync."""
    return {
        "thesis": {
            "summary": "[NEW POSITION — broker sync auto-detected; agent review pending]",
            "status": "intact",
            "conviction": "medium",
            "last_reviewed": _now_iso(),
        },
        "action": {
            "intent": "hold",
            "reason": "auto-stub from broker sync; review and update",
            "confidence": "low",
        },
    }
