#!/usr/bin/env python3
"""Generate a compact portfolio brief from state/positions.json.

Usage:
  python3 quant/report/portfolio_brief.py
  python3 quant/report/portfolio_brief.py --json

Notes:
- Reads state/positions.json + state/watchlist.json (v2 layout, post-)
- Quotes are kept fresh by quant/sync/stock_fetch.py (writes broker.current_price etc.)
- Summarizes what matters for heartbeat/daily reporting
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 直接 `python3 quant/<层>/<模块>.py` 时让 `quant.*` 可 import

from quant.core import env  # noqa: F401  (.env → os.environ)

import json
import time
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from quant.core import book, earnings, sizing, structure  # noqa: E402  组合占用 + 财报缩仓 + gate 结构完整性
from quant.core.holdings import (
    UNMANAGED_ROLES, cards_missing_archetype, flat_with_lots, is_candidate, is_managed,
    is_reference_role, live_tickers, lot_gap, lot_qty, managed_tickers, underlying_cards_missing_zones)
from quant.core.io_json import atomic_write_json, iter_jsonl, read_json
from quant.core.occ import is_option_ticker
from quant.core.num import coerce_float as normalize_num
from quant.core.occ import parse_occ
from quant.core.paths import (
    EVENTS_DIR, HEARTBEAT_STATE_PATH, HOLDING_ROOT, IV_HISTORY_DIR,
    KLINE_CACHE_DIR, NAV_HISTORY_PATH, SCORES_PATH, VALUATION_INPUTS_PATH,
)
from quant.core.sizing import regime_lines
from quant.core.technical import atr, resample_weekly
from quant.core.timeutil import et_today, parse_ts
# 持仓加仓阶梯：价位与模式判定直接复用 entry_guard，watchlist 与持仓票
# 必须是同一套 build_ladder / share_ladder, 不在本模块另存一份 (report 5 → decision 4, 向下)。
from quant.decision import entry_guard as _eg  # noqa: E402
from quant.core import dow as _dow  # noqa: E402  (短期道氏结构位 = 清单排序键)

STATE_PATH = HEARTBEAT_STATE_PATH
STATE_POSITIONS_PATH = HOLDING_ROOT / "state/positions.json"
STATE_WATCHLIST_PATH = HOLDING_ROOT / "state/watchlist.json"
TOP_MOVERS_COUNT = 3
# Edge-triggered alerts: first occurrence fires; persisting condition re-confirms
# at most every 24h (was: re-fired every 4h, contradicting heartbeat.md 边沿触发)
REALERT_SECONDS = 24 * 60 * 60
# zone-maintenance staleness 口径 —— 判别式手搓孪生保留 (历史有意重复),
# 常量本身 re-export core.structure (单一真源, entry_guard 同源);
# 三方恒等由 tests/test_constant_pins.py 钉死。语义:
#   breakout (上行) 连续 ×2 确认 | breakdown (破 z2 下沿) **当日**即 stale
#   (带内挂单语义反转, 多等一天没有意义) | 波动 stale = |单日收收| > 2×ATR14
#   (per-ticker 尺, 固定 10% 从未进代码且"没有意义"; 校准 2,490 票日触发率 1.5%)
STALE_CONFIRM_DAYS = structure.STALE_CONFIRM_DAYS
STALE_BREAKDOWN_DAYS = structure.STALE_BREAKDOWN_DAYS
STALE_VOL_ATR_MULT = structure.STALE_VOL_ATR_MULT


def _load_state_doc(path: Path) -> Dict:
    if not path.exists():
        return {"tickers": {}}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"tickers": {}}
        data.setdefault("tickers", {})
        return data
    except Exception:
        return {"tickers": {}}


def load_rows_from_state(path: Path = STATE_POSITIONS_PATH) -> Tuple[List[dict], str]:
    """Read state/positions.json and produce CSV-shaped rows.

    Downstream code (`row_summary` etc.) expects the same column names that
    portfolio_latest.csv carried; this is the only adapter point. Closed
    positions (broker == None) are skipped from heartbeat reports.
    """
    doc = _load_state_doc(path)
    live = live_tickers(doc)
    rows: List[dict] = []
    for ticker, body in doc.get("tickers", {}).items():
        broker = body.get("broker")
        if broker is None:
            continue
        # Closed position; not relevant to heartbeat / "today's brief".
        # v2 closed entries keep their last broker snapshot (stock_fetch no
        # longer refreshes them), so status gates them — frozen change_pct
        # would otherwise ghost-alert forever.
        #
        # qty wins over status in BOTH directions. status 是纯手工字段 —— 全仓
        # 没有一行代码把它写成 "closed" (只有 setdefault), 平仓后靠人改。所以
        # 老的 `status=="closed" and qty==0` 只挡得住"已经有人清理过"的卡:
        #   - qty>0 + status="closed" → 挡不住, 也不该挡 (INTC  持仓
        #     qty=2 却标 closed, 被这条静音掉整天的 -7.89% / zone / stop)
        #   - qty==0 + status="active" → **漏过去**。NBIS  16:48 ET
        #     清仓, broker.qty 当晚就是 0.0, 但没人手动改 status → 一只 0 股的
        #     票带着完整 zone_config 当上 7/29 简报的头号异动 (-9.82%) 并进
        #     budgets。持仓与否只有一个真源: qty。
        if ticker not in live:
            continue
        # 结构豁免仓 (holdings.UNMANAGED_ROLES): 不受交易纪律管 ⇒ 也不占播报注意力。
        # 唯一的过滤点就在这里 —— 下游 (position_cards / top_movers / zone 行 /
        # budgets / alerts) 全部消费 rows, 挡在源头才不会像  之前那样,
        # 五个模块各挡各的、剩第六个漏出来 (用户)。NAV 走账户级快照,
        # 不受影响 —— 钱照算, 只是不播报。
        if not is_managed(body):
            continue
        instrument = body.get("instrument") or {}
        rows.append({
            "代码": ticker,
            "名称": instrument.get("name") or "",
            "持有数量": broker.get("qty"),
            "current_price": broker.get("current_price"),
            "change": None,  # absolute change isn't stored in v2 broker block
            "change_pct": broker.get("change_pct"),
            "price_source": broker.get("quote_source") or "",
            "quote_time": broker.get("as_of") or "",
            "持仓占比": None,  # derived elsewhere; not in v2 schema
            "未实现盈亏比例": broker.get("unrealized_pnl_pct"),
            "未实现盈亏": broker.get("unrealized_pnl_usd"),
            "平均成本价": broker.get("avg_cost"),
            "synthetic": "Y" if broker.get("synthetic") else "",
            # 期权 greeks (stock_fetch 落库的 OpenD 真值) — 保费投影 (止损/止盈
            # 单行的期权分支) 要 IV/delta,  起随行透传
            "delta": broker.get("delta"),
            "iv_pct": broker.get("iv_pct"),
        })
    return rows, "代码"


def _agent_block_to_legacy_zone(body: Dict) -> Dict:
    """Convert v2 ticker body to legacy zone-map shape consumed by the
    portfolio_brief enrichment code. Empty/missing fields drop out.
    """
    agent = body.get("agent") or {}
    zones = agent.get("zones") or {}
    instrument = body.get("instrument") or {}
    out: Dict = {}
    for k in ("accumulation_zone_1", "accumulation_zone_2", "trim_zone"):
        v = zones.get(k)
        if isinstance(v, list) and len(v) == 2:
            out[k] = list(v)
    if zones.get("no_chase_above") is not None:
        out["no_chase_above"] = zones["no_chase_above"]
    if zones.get("stop_level") is not None:
        out["stop_level"] = zones["stop_level"]
    if zones.get("stop_level_basis"):
        out["stop_level_basis"] = zones["stop_level_basis"]
    if zones.get("stop_level_days"):
        out["stop_level_days"] = zones["stop_level_days"]
    if zones.get("valid_until"):
        out["valid_until"] = zones["valid_until"]
    if zones.get("update_trigger"):
        out["update_trigger"] = zones["update_trigger"]
    if zones.get("methodology"):
        out["zone_methodology"] = zones["methodology"]
    if zones.get("timeframe"):
        out["zone_timeframe"] = zones["timeframe"]
    if zones.get("reasoning_summary"):
        out["zone_reasoning_summary"] = zones["reasoning_summary"]
    # gate = 状态转移锚点。此前这个 whitelist 把它丢在转换里, 所以卡片写了
    # "880 收复 / 1008 升 confirmed" 而 brief 一个字都出不来 —— 同一形状的
    # "字段在转换中被丢" (data_discipline 6 形状之一)。
    if isinstance(zones.get("reclaim_gates"), list):
        out["reclaim_gates"] = zones["reclaim_gates"]
    if isinstance(agent.get("pending_repaint"), dict):
        out["pending_repaint"] = agent["pending_repaint"]
    # current_regime 的 cap/participation 是**授权属性**, 不是描述。丢在转换里
    # = 一个标着"不授权入场"的观察 regime 在手册里只剩两条 accumulation 带
    # ()。持仓票不走 entry_guard, 状态卡是它到手册的唯一通道。
    if isinstance(agent.get("current_regime"), dict):
        out["current_regime"] = agent["current_regime"]
    for slot in ("last_regime", "next_regime"):
        if isinstance(agent.get(slot), dict):
            out[slot] = agent[slot]
    if instrument.get("role"):
        out["role"] = instrument["role"]
    thesis = agent.get("thesis") or {}
    if thesis.get("summary"):
        out["thesis"] = thesis["summary"]
    if agent.get("key_risk"):
        out["key_risk"] = agent["key_risk"]
    if agent.get("invalid_if"):
        out["invalid_if"] = agent["invalid_if"]
    if agent.get("linked_underlying"):
        out["linked_underlying"] = agent["linked_underlying"]
    # broker delta (, stock_fetch 落库的 OpenD 真 greeks): 期权的组合层
    # 上限是**标的名义**, 折成保费预算要 delta。丢在这个 whitelist 里 = 预算行
    # 只剩分类 cap —— 同 gate / current_regime 那两次"字段在转换中被丢"。
    if (body.get("broker") or {}).get("delta") is not None:
        out["delta"] = (body.get("broker") or {})["delta"]
    return out


def lot_drift(path: Path = STATE_POSITIONS_PATH) -> List[str]:
    """lots 与 broker qty 对不上 —— 两个方向都报。

    两条自动化各管一头 (pipeline 的 reconcile / lots 步):
      qty == 0 但 lots 还挂着   平仓收尾没做完 → `quant/sync/close_reconcile.py`
      qty != 0 且 lots 对不上   买入没落账 / 部分卖出没分配 → `quant/sync/lot_reconcile.py`

    所以这条正常情况下不该响 —— 它是**自动化失灵时的安全网**, 也是自动化明确
    拒绝动手 (部分卖出、数量对不上) 时唯一会被人看见的出口。少了它就是回到
     (NBIS 带 1 股 lot 过夜) 和  (AAOI/INTC 漏 lot 一天):
    schema 两次都报了 drift, 但只 print 到 stderr, 定时任务没人读。

    结构豁免层 (base) 不进这条 —— 它整个不在自动化辖区内, 报了也没有会清掉它的
    动作, 只会变成永远响的噪音 (position-tiers.md: 不进任何 task 输出)。
    """
    doc = _load_state_doc(path)
    tickers = doc.get("tickers") or {}
    rows = [f"{t}: broker qty 0 但 lots 还有 {lot_qty(tickers[t]):g} 股 "
            f"(status={tickers[t].get('status')}) — 平仓收尾未完成"
            for t in flat_with_lots(doc)]
    for t in sorted(managed_tickers(doc)):
        body = tickers[t]
        bqty = float((body.get("broker") or {}).get("qty") or 0)
        if not bqty or (body.get("broker") or {}).get("synthetic"):
            continue
        gap = lot_gap(body)
        if abs(gap) > 1e-6:
            rows.append(f"{t}: lots {lot_qty(body):g} 股 != broker {bqty:g} 股 "
                        f"(缺 {gap:+g}) — 买入未落账或部分卖出未分配")
    return rows


def qty_desync(path: Optional[Path] = None) -> List[str]:
    """成交事件流的**净持仓**与 `broker.qty` 对不上 —— 安全网 (立)。

    不变量: 对 moomoo 源的每张卡, Σ(BUY qty) − Σ(SELL qty) == `broker.qty`。
    事件流是 append-only 且按 order_id 去重, 券商快照是当轮真值, 两者本来必须相等。
    立此条时实测 26 张卡里**只有出事的那张**违反, 所以它是干净判据不是噪音源。

    🚨 它补的是 `lot_drift` 结构上看不见的那半: lot_drift 比的是 lots ↔ broker.qty,
    而 NBIS  的事故里**两个都错**(都是 1)、差为 0, 整整两天零告警。
    根因是 `update_broker_blocks` 按 ticker 合并 —— 券商不再返回的卡一个字节都不碰,
    所以"最后一次同日快照之后成交的平仓"永远追不上。根因已修 (state_io 的
    `zero_absent`), 本条按 close_reconcile 的先例保留为安全网: **自动化失灵时,
    这是唯一会被人看见的出口**。

    结构豁免层不排除 —— 它虽然不进交易纪律, 但"账上有几股"是账本事实, 错了就得报。
    synthetic 行 (期权 underlying 的影子行, 无成交) 排除。

    **在事件流里一条记录都没有的 ticker 跳过**: 那是"事件流覆盖不到它"(早于
     的仓 / 手工迁移进来的), 不是"记录说它平了" —— 两件事, 报前者是噪音。
    代价明示: 一张卡的事件**全部**丢失时本条会沉默, 那一半归 `lot_drift`。
    ⚠️ `path` 默认 None 再取模块全局, 不写成默认参数 —— 默认参数在 def 时求值,
    monkeypatch 模块全局对它无效 (`lot_drift` 正因此在测试里只能整个被 stub 掉)。
    """
    doc = _load_state_doc(path or STATE_POSITIONS_PATH)
    net: Dict[str, float] = {}
    for month_dir in sorted(p for p in EVENTS_DIR.glob("2*") if p.is_dir()):
        for row in iter_jsonl(month_dir / "trades.jsonl"):
            tk = str(row.get("ticker") or "").upper()
            side = str(row.get("side") or "").upper()
            q = normalize_num(row.get("qty")) or 0.0
            if not tk or side not in ("BUY", "SELL"):
                continue
            net[tk] = net.get(tk, 0.0) + (q if side == "BUY" else -q)
    out: List[str] = []
    for tk, body in sorted((doc.get("tickers") or {}).items()):
        br = (body or {}).get("broker") or {}
        if str(br.get("source") or "moomoo_opend") != "moomoo_opend" or br.get("synthetic"):
            continue
        if tk not in net:
            continue                      # 事件流覆盖不到它 (见 docstring)
        bqty = normalize_num(br.get("qty")) or 0.0
        ev = net[tk]
        if abs(ev - bqty) > 1e-6:
            out.append(f"{tk}: 事件流净 {ev:g} 股 != broker {bqty:g} 股 (差 {ev - bqty:+g})")
    return out


#: 未盖章成交的回看窗口。历史遗留 (落库即盖章之前的 60 笔) 不进告警 ——
#: 它们是既成事实, 天天点名只会训练人无视这条。
UNTAGGED_FILL_WINDOW_DAYS = 14


#: 盖章字段切换日 (classification_removal_design.md D4): 此日前的 fill 认
#: classification (历史只读), 此日起认 auth {cap_pct_nav, gate_no}。
AUTH_TAG_SINCE = "2026-08-13"


def scored_fill_refs() -> set:
    """scores.jsonl 里已被人裁定 (有评分行) 的成交 order_id 集合。

    cap 0 的成交按 schema **不能**盖 auth 章 (`validate_event`: cap 0 = 无授权, 不该有
    auth), 所以 `fill_untagged` 告警文案里那句「判定后 annotate_event 补注 + 落评分」
    此前对它是假的: 注解 + 评分做完, 告警照响到 14 天窗口自然滑出 —— 人工裁定章解的只是 cap>0 那半;
    cap 0 的规则外成交裁「直接评 C, 不再点名」: 评分行 (`ref` = order_id) 是判定完成的唯一机器
    可见形状, 告警以它收口。只读 scores.jsonl, 不改事件, 读不到 = 没人判过, 照常点名。
    """
    try:
        return {str(r.get("ref")) for r in iter_jsonl(SCORES_PATH) if r.get("ref")}
    except Exception:                                   # noqa: BLE001  账本读不到 = 照常点名
        return set()


def untagged_fills(days: int = UNTAGGED_FILL_WINDOW_DAYS) -> List[str]:
    """近 N 天内落库却没盖上授权标签、也还没被裁定 (无评分行) 的 BUY fill。

     起 `trade_history_sync` 落库即盖章 (判别式 core/sizing +
    core/holdings;  起盖 `auth` 不再盖 classification), 所以正常情况下
    这条不该响。它响 = **推导返回了 None**, 而 None 不是"算不出来", 是一个判断:
    regime cap 0 / 无 current_regime = 这笔成交没有系统授权的形状 —— 按规则外
    处理, 归人判 (CLAUDE.md §Trading Discipline 的注解清单正是这一类)。

    没有这条告警, "推不出来"就退化成一个空字段: 卡片预算行降级、注解清单里少一
    笔, 谁也不会发现。空字段不会自己举手 (data_discipline: 会驱动动作的缺失必须
    看得见)。
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    scored = scored_fill_refs()
    out: List[str] = []
    for month_dir in sorted(p for p in EVENTS_DIR.glob("2*") if p.is_dir()):
        for row in iter_jsonl(month_dir / "trades.jsonl"):
            if row.get("event_type") != "fill" or str(row.get("side", "")).upper() != "BUY":
                continue
            ts = str(row.get("timestamp") or "")
            tagged = row.get("auth") if ts[:10] >= AUTH_TAG_SINCE \
                else (row.get("classification") or row.get("auth"))
            if tagged:
                continue
            if str(row.get("order_id") or "") in scored:
                continue                # 已裁定 (scores.jsonl 有评分行) = 判定完成, 告警收口
            when = parse_ts(ts)
            if not when or when < cutoff:
                continue
            out.append(f"{row.get('ticker')} {row.get('qty')}@{row.get('price')} "
                       f"({ts[:10]}) 无授权标签 (auth)")
    return out


def price_breach_fills(days: int = UNTAGGED_FILL_WINDOW_DAYS) -> List[str]:
    """Return recent BUY fills stamped outside the card's execution rules.

    The synchronization layer freezes these flags at ingestion time. Recomputing
    them here would compare an old fill with today's potentially redrawn card.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    out: List[str] = []
    for month_dir in sorted(p for p in EVENTS_DIR.glob("2*") if p.is_dir()):
        for row in iter_jsonl(month_dir / "trades.jsonl"):
            if row.get("event_type") != "fill" or str(row.get("side", "")).upper() != "BUY":
                continue
            flags = row.get("price_flags") or []
            if not flags:
                continue
            ts = str(row.get("timestamp") or "")
            when = parse_ts(ts)
            if not when or when < cutoff:
                continue
            out.append(
                f"{row.get('ticker')} {row.get('qty')}@{row.get('price')} "
                f"({ts[:10]}) {'+'.join(flags)}"
                + (" ⚠️跨日盖章" if row.get("stamp_stale_card_risk") else "")
            )
    return out


def load_zone_maps_from_state() -> Tuple[Dict[str, dict], Dict[str, dict], List[dict]]:
    """v2 replacement for load_zone_maps(): pull agent zones from state/.

    Returns (current_zones, watchlist_zones, watchlist_basics) — same shape as
    before so downstream pure-data code is unchanged.
    """
    pos_doc = _load_state_doc(STATE_POSITIONS_PATH)
    wl_doc = _load_state_doc(STATE_WATCHLIST_PATH)

    # 平仓卡留着 agent block 当历史, 但它的 zone 不再是任何人的真源。不过滤会
    # (a) 永远喷 ⚠ ZONE STALE (AAOI270617C200000 7/16 就平了, valid_until 7/23)
    # (b) 与活卡撞车 —— INTC 平仓卡挂 z1 102.5-106/stop 98, 同时 watchlist 卡写
    #     z1 85-89.5/stop 84, 两套 zone 同一 ticker, 谁也不作废谁。
    live = live_tickers(pos_doc)
    current = {
        str(t).upper(): _agent_block_to_legacy_zone(body)
        for t, body in pos_doc.get("tickers", {}).items()
        if t in live
    }
    watchlist = {
        str(t).upper(): _agent_block_to_legacy_zone(body)
        for t, body in wl_doc.get("tickers", {}).items()
    }

    basics: List[dict] = []
    for ticker, body in wl_doc.get("tickers", {}).items():
        instrument = body.get("instrument") or {}
        agent = body.get("agent") or {}
        thesis = agent.get("thesis") or {}
        broker = body.get("broker") or {}      # 报价快照 (stock_fetch 顺手拉,)
        basics.append({
            "ticker": ticker,
            "name": instrument.get("name") or "",
            "status": body.get("status") or "watching",
            "thesis": thesis.get("summary") or "",
            "priority": body.get("priority") or "medium",
            "moomoo_listed": body.get("moomoo_listed"),
            # 候选判据单一真源 (holdings.is_candidate = status=watching 且 moomoo_listed≠false):
            # 下游触碰 / z2 冻结 / zone 行 / stale 清单四处只读这一个布尔, 不再各自看 status
            "is_candidate": is_candidate(body),
            "role": instrument.get("role"),
            "cap_pct_nav": (agent.get("current_regime") or {}).get("cap_pct_nav"),
            "price": broker.get("current_price"),
            "change_pct": broker.get("change_pct"),
            "quote_as_of": broker.get("as_of"),
        })
    return current, watchlist, basics


def normalize_zone_pair(value) -> Optional[Tuple[float, float]]:
    if not isinstance(value, list) or len(value) != 2:
        return None
    low = normalize_num(value[0])
    high = normalize_num(value[1])
    if low is None or high is None:
        return None
    return (min(low, high), max(low, high))


def parse_dateish(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    candidates = [text]
    if text.endswith('Z'):
        candidates.append(text.replace('Z', '+00:00'))
    for c in candidates:
        try:
            dt = datetime.fromisoformat(c)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            pass
    for fmt in ('%Y-%m-%d', '%Y/%m/%d'):
        try:
            dt = datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            pass
    return None


def classify_instrument(ticker: str, qty: Optional[float]) -> Dict[str, object]:
    ticker = (ticker or "").strip().upper()
    parts = parse_occ(ticker)
    if not parts:
        return {
            "is_option": False,
            "underlying": ticker,
            "option_side": None,
            "exposure": "long" if (qty or 0) >= 0 else "short",
            "label": "stock",
            "strike": None,
            "expiry": None,
        }

    cp = parts["side"]
    qty_val = qty or 0.0
    exposure = "long" if qty_val >= 0 else "short"
    if cp == "C":
        label = "long call" if qty_val >= 0 else "short call"
    else:
        label = "long put" if qty_val >= 0 else "short put"

    return {
        "is_option": True,
        "underlying": parts["underlying"],
        "option_side": cp,
        "exposure": exposure,
        "label": label,
        "strike": parts["strike"],
        "expiry": parts["expiry"],
    }


def evaluate_zone(price: Optional[float], zone_pair: Optional[Tuple[float, float]], near_pct: float = 0.03) -> Optional[str]:
    if price is None or zone_pair is None:
        return None
    low, high = zone_pair
    if low <= price <= high:
        return "inside"
    if price < low:
        gap = (low - price) / low if low else 0.0
        if gap <= near_pct:
            return "near_below"
        return "below"
    if price > high:
        gap = (price - high) / high if high else 0.0
        if gap <= near_pct:
            return "near_above"
        return "above"
    return None


def zone_hint_lines(label: str, zone_pair: Optional[Tuple[float, float]],
                    price: Optional[float], *, actionable: bool = True) -> List[str]:
    """actionable=False → 位置语义, 不是入场语义。

    候选票"进入首档吸纳区"= 可以挂单; 已持仓且本轮 ladder 预算用尽时同一句话
    是纯位置信息。同一个函数服务两种读者, 措辞必须分叉, 否则读者只能靠上下文
    猜 —— 而 heartbeat 的读者是被叫醒的人。
    """
    if zone_pair is None:
        return []
    low, high = zone_pair
    state = evaluate_zone(price, zone_pair)
    zone_txt = f"{label} {low:.2f}-{high:.2f}"
    if state == "inside":
        return [f"{'进入' if actionable else '位于'}{zone_txt}"]
    if state in {"near_above", "near_below"}:
        return [f"{'接近' if actionable else '临近'}{zone_txt}"]
    return []


def no_chase_hint(no_chase_above, price: Optional[float]) -> Optional[str]:
    limit = normalize_num(no_chase_above)
    if limit is None or price is None:
        return None
    if price > limit:
        return f"高于不追价上限 {limit:.2f}"
    gap = (limit - price) / limit if limit else None
    if gap is not None and 0 <= gap <= 0.03:
        return f"接近不追价上限 {limit:.2f}"
    return None


def freshness_hints(zone_cfg: Dict) -> List[str]:
    hints: List[str] = []
    valid_until = parse_dateish(zone_cfg.get('valid_until'))
    if valid_until is None:
        return hints
    now = datetime.now(timezone.utc)
    delta_days = (valid_until - now).total_seconds() / 86400
    if delta_days < 0:
        hints.append(f"区间已过期 {valid_until.date().isoformat()}")
    elif delta_days <= 2:
        hints.append(f"区间即将过期 {valid_until.date().isoformat()}")
    return hints


def waiting_zone_hint(price: Optional[float], zone1: Optional[Tuple[float, float]], trim_zone: Optional[Tuple[float, float]]) -> Optional[str]:
    if price is None or zone1 is None:
        return None
    _, zone1_high = zone1
    trim_low = trim_zone[0] if trim_zone else None
    if price > zone1_high and (trim_low is None or price < trim_low):
        return "位于等待区：不在吸纳区，未到减仓区"
    return None


def below_zone_hint(price: Optional[float], zone1: Optional[Tuple[float, float]], zone2: Optional[Tuple[float, float]]) -> Optional[str]:
    if price is None or zone1 is None:
        return None
    zone1_low = zone1[0]
    if price < zone1_low:
        if zone2 and price < zone2[0]:
            return "低于二档吸纳区下沿：警惕结构破坏，不要机械接飞刀"
        return "低于首档吸纳区：若 thesis 未坏，可按两段式计划看是否接近二档"
    return None


def enrich_with_zone_context(item: Dict, current_zones: Dict[str, dict],
                              all_items: Optional[List[Dict]] = None,
                              budget: Optional[Dict] = None) -> Dict:
    ticker = item["ticker"]
    underlying = str(item.get("underlying") or ticker).upper()
    own_cfg = current_zones.get(ticker) or {}
    linked_underlying = str(own_cfg.get("linked_underlying") or underlying).upper()
    underlying_cfg = current_zones.get(linked_underlying) or {}

    use_underlying_cfg = bool(item.get("is_option")) and not normalize_zone_pair(own_cfg.get("accumulation_zone_1"))
    zone_cfg = underlying_cfg if use_underlying_cfg else (own_cfg or underlying_cfg or {})
    zone_basis = f"underlying:{linked_underlying}" if use_underlying_cfg and linked_underlying else "self"

    # For options inheriting underlying zones, use underlying's price for zone comparison
    compare_price = item.get("current_price")
    if use_underlying_cfg and all_items:
        for other in all_items:
            if other.get("ticker") == linked_underlying and not other.get("is_option"):
                compare_price = other.get("current_price")
                break

    zone1 = normalize_zone_pair(zone_cfg.get("accumulation_zone_1"))
    zone2 = normalize_zone_pair(zone_cfg.get("accumulation_zone_2"))
    trim_zone = normalize_zone_pair(zone_cfg.get("trim_zone"))
    # 预算用尽 = 吸纳区退化为位置信息。减仓区不受影响 —— 那是退出侧信号,
    # 跟还能不能加仓无关。
    # 失败为**关**: budget 判不出来时不得把吸纳区渲染成可执行加仓提示。
    #  INTC 实证 —— 计划内成交不再注解后 budget 返回 None, 旧写法
    # `not (budget or {}).get("exhausted")` 恰好为 True, 于是加仓通道已关的卡
    # 仍在提示"接近首档吸纳区"。判不出来 ≠ 可以加。
    # **持仓**判不出预算 → 失败为关; **候选**本来就没有预算 (无成交) → 照常可执行。
    #  INTC 实证: 计划内成交不再注解后 budget 返回 None, 旧写法
    # `not (budget or {}).get("exhausted")` 恰好为 True, 于是加仓通道已关的持仓卡
    # 仍在提示"接近首档吸纳区"。判不出来 ≠ 可以加 —— 但这只对已经有仓位的卡成立。
    held = bool(normalize_num(item.get("qty")))
    can_add = (bool(budget) and not budget.get("exhausted")) if held else True
    hints: List[str] = []
    hints.extend(zone_hint_lines("首档吸纳区", zone1, compare_price, actionable=can_add))
    hints.extend(zone_hint_lines("二档吸纳区", zone2, compare_price, actionable=can_add))
    hints.extend(zone_hint_lines("减仓观察区", trim_zone, compare_price))
    if can_add:
        wait_hint = waiting_zone_hint(compare_price, zone1, trim_zone)
        if wait_hint:
            hints.append(wait_hint)
        below_hint = below_zone_hint(compare_price, zone1, zone2)
        if below_hint:
            hints.append(below_hint)
        chase = no_chase_hint(zone_cfg.get("no_chase_above"), compare_price)
        if chase:
            hints.append(chase)
    elif budget:
        hints.append(
            f"本 campaign 预算已用尽 "
            f"(已投 ${budget['deployed_usd']:,.0f} / cap ${budget['cap_usd']:,.0f}, "
            f"余 ${budget['remaining_usd']:,.0f} 买不起 1 股) → zone 触碰非加仓信号"
)
    hints.extend(freshness_hints(zone_cfg))

    # Add option-specific semantic hints
    if item.get("is_option") and use_underlying_cfg:
        strike = item.get("strike")
        label = item.get("label", "")
        if strike and compare_price:
            if "short put" in label:
                if compare_price > strike:
                    hints.append(f"正股 {linked_underlying} {compare_price:.2f} > 行权价 {strike:.0f}，short put 安全")
                else:
                    gap_pct = (strike - compare_price) / strike * 100
                    hints.append(f"正股 {linked_underlying} {compare_price:.2f} 低于行权价 {strike:.0f} ({gap_pct:.1f}%)，short put 面临接货风险")
            elif "long call" in label:
                if compare_price > strike:
                    hints.append(f"正股 {linked_underlying} {compare_price:.2f} > 行权价 {strike:.0f}，long call 实值")
                else:
                    gap_pct = (strike - compare_price) / strike * 100
                    hints.append(f"正股 {linked_underlying} {compare_price:.2f} 距行权价 {strike:.0f} 还有 {gap_pct:.1f}%，long call 虚值 + 时间衰减")
        # Prefix all hints with underlying basis
        hints = [f"基于正股 {linked_underlying}: {h}" for h in hints]
    elif use_underlying_cfg and linked_underlying:
        hints = [f"基于正股 {linked_underlying}: {h}" for h in hints]

    item["zone_config"] = zone_cfg
    item["zone_basis"] = zone_basis
    item["zone_compare_price"] = compare_price
    item["zone_hints"] = hints
    item["zone_summary"] = {
        "accumulation_zone_1": zone1,
        "accumulation_zone_2": zone2,
        "trim_zone": trim_zone,
        "no_chase_above": normalize_num(zone_cfg.get("no_chase_above")),
        "valid_until": zone_cfg.get('valid_until'),
    }
    return item


def build_watchlist_zone_lines(watchlist_rows: List[dict], watchlist_zones: Dict[str, dict]) -> List[str]:
    lines: List[str] = []
    for row in watchlist_rows:
        ticker = str(row.get("ticker", "")).strip().upper()
        if not ticker:
            continue
        # 非候选卡 (已移出 moomoo 分组的墓碑 / status≠watching) 不进 zone 行 —— 判据
        # holdings.is_candidate, 与触碰 / 冻结 / stale 清单同源
        if not row.get("is_candidate"):
            continue
        cfg = watchlist_zones.get(ticker, {})
        zone1 = normalize_zone_pair(cfg.get("accumulation_zone_1"))
        zone2 = normalize_zone_pair(cfg.get("accumulation_zone_2"))
        trim_zone = normalize_zone_pair(cfg.get("trim_zone"))
        no_chase = normalize_num(cfg.get("no_chase_above"))
        parts = [ticker]
        if zone1:
            parts.append(f"首档 {zone1[0]:.2f}-{zone1[1]:.2f}")
        if zone2:
            parts.append(f"二档 {zone2[0]:.2f}-{zone2[1]:.2f}")
        if trim_zone:
            parts.append(f"减仓 {trim_zone[0]:.2f}-{trim_zone[1]:.2f}")
        if no_chase is not None:
            parts.append(f"不追价>{no_chase:.2f}")
        exp = cfg.get('valid_until')
        if exp:
            parts.append(f"有效期至 {exp}")
        if len(parts) > 1:
            lines.append(" | ".join(parts))
    return lines


def row_summary(row: dict, ticker_col: str) -> Dict:
    ticker = (row.get(ticker_col) or "").strip().upper()
    name = (row.get("名称") or row.get("name") or ticker).strip()
    qty = normalize_num(row.get("持有数量"))
    current_price = normalize_num(row.get("current_price"))
    change = normalize_num(row.get("change"))
    change_pct = normalize_num(row.get("change_pct"))
    source = (row.get("price_source") or "").strip()
    quote_time = (row.get("quote_time") or "").strip()
    position_weight = normalize_num(row.get("持仓占比"))
    pnl_pct = normalize_num(row.get("未实现盈亏比例"))
    unrealized_pl = normalize_num(row.get("未实现盈亏"))
    avg_cost = normalize_num(row.get("平均成本价"))
    synthetic = (row.get("synthetic") or "").strip().upper() == "Y"
    meta = classify_instrument(ticker, qty)

    return {
        "ticker": ticker,
        "name": name,
        "qty": qty,
        "current_price": current_price,
        "change": change,
        "change_pct": change_pct,
        "price_source": source,
        "quote_time": quote_time,
        "position_weight": position_weight,
        "pnl_pct": pnl_pct,
        "unrealized_pl": unrealized_pl,
        "avg_cost": avg_cost,
        "synthetic": synthetic,
        "delta": normalize_num(row.get("delta")),
        "iv_pct": normalize_num(row.get("iv_pct")),
        **meta,
    }


def fmt_price(v: Optional[float]) -> str:
    return "?" if v is None else f"{v:.2f}"


def fmt_pct(v: Optional[float]) -> str:
    return "?" if v is None else f"{v:+.2f}%"


def load_state(path: Path = STATE_PATH) -> Dict:
    if not path.exists():
        return {"alerts": {}}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"alerts": {}}
        data.setdefault("alerts", {})
        return data
    except Exception:
        return {"alerts": {}}


def save_state(state: Dict, path: Path = STATE_PATH) -> None:
    atomic_write_json(path, state)  # 曾是全系统唯一非原子状态写


def alert_key(ticker: str, trigger_type: str) -> str:
    return f"{ticker}:{trigger_type}"


def should_emit(state: Dict, ticker: str, trigger_type: str, now_ts: int) -> bool:
    """Edge-triggered: 条件首次成立 → 提醒; 持续成立 → 24h 内不重复;
    条件解除 (clear_inactive 置 active=False) 后再次成立 → 重新提醒。"""
    key = alert_key(ticker, trigger_type)
    node = state.setdefault("alerts", {}).setdefault(key, {"lastAlertTs": 0, "active": False})
    last_ts = int(node.get("lastAlertTs") or 0)
    active = bool(node.get("active"))
    if (not active) or (now_ts - last_ts >= REALERT_SECONDS):
        node["lastAlertTs"] = now_ts
        node["active"] = True
        return True
    return False


def clear_inactive(state: Dict, active_keys: set[str]) -> None:
    alerts = state.setdefault("alerts", {})
    for key, node in alerts.items():
        if key not in active_keys:
            node["active"] = False


def prune_dead_keys(state: Dict, universe: set[str]) -> int:
    """删除已不在持仓/watchlist 范围内 ticker 的告警 key (INFRA:* 保留), 外加
    改过名的 trigger 遗留 key —— 按 ticker 判的话它们永远躺在状态文件里。"""
    alerts = state.setdefault("alerts", {})
    dead = []
    for k in alerts:
        if k.startswith("INFRA:"):
            continue
        ticker, _, trigger = k.partition(":")
        if ticker not in universe or trigger in RETIRED_TRIGGERS:
            dead.append(k)
    for k in dead:
        alerts.pop(k)
    return len(dead)


# ── Zone staleness (代码化 zone-maintenance.md 触发条件) ───────────

def _cached_closes(ticker: str, n: int = 3) -> List[Tuple[str, float]]:
    path = KLINE_CACHE_DIR / f"{ticker}.jsonl"
    rows = [(str(r.get("time", ""))[:10], r.get("close"))
            for r in iter_jsonl(path) if r.get("close") is not None]
    return rows[-n:]


def stop_breach(ticker: str, item: Dict, cfg: Optional[Dict]) -> Optional[Dict]:
    """卡片自己写的失效线破了没有 — agent.zones.stop_level, 收盘口径。

    stop_level 此前只有 entry_guard 查 (入场侧 VETO), 持仓侧全栈无人看:
    exit_guard 只管赢家侧 (利润棘轮 + 3xATR 趋势死亡线), zone 信号只报吸纳/
    减仓区。 INTC 收 92.32 vs 卡片 stop 98 (invalid_if "close < 98"),
    brief 一个字都没出。

    收盘口径不是盘中价: 卡片写的是"收盘<98 全出", 盘中插针不算。

    确认窗口跟卡片走: 多数卡是单日 (INTC "close < 98"), 但 MU/AMKR/NVDA 写的是
    "连续 2 日" —— 一律单日判会对这些卡早响一天。窗口存在 zones.stop_level_days,
    缺省 1 (与 reclaim_gates[].days 同一约定)。
    """
    if not isinstance(cfg, dict) or not item.get("qty"):
        return None
    if item.get("synthetic") or cfg.get("role") in UNMANAGED_ROLES:
        return None
    stop = normalize_num(cfg.get("stop_level"))
    if stop is None:
        return None

    # 期权卡的 stop 可以锚在正股上 (schema 强制 stop_level_basis)。
    ref = ticker
    if item.get("is_option") and cfg.get("stop_level_basis") == "underlying":
        ref = item.get("underlying") or ticker

    days = max(1, int(normalize_num(cfg.get("stop_level_days")) or 1))
    closes = _cached_closes(ref, days)
    if len(closes) < days:
        return None
    if any(c is None or c >= stop for _, c in closes):
        return None
    day, close = closes[-1]
    return {"ref": ref, "date": day, "close": close, "stop": stop, "days": days,
            "invalid_if": cfg.get("invalid_if") or ""}


# ── 异动阈值: 标的自身的波动尺, 不是全池一个 5% ────────────────────
#
# 固定 5% 在不同标的上根本不是同一个事件。实测  (32 票 / 本地 kline
# 缓存 / close-to-close 触发率): SMU **68.4%**的交易日都 >5% (三天两响 = 纯噪音),
# 而 QQQ/SPY 在 274 根里一次都没到过 5%, AAPL 0.5%, MSFT/AMZN 2.0% —— 同一把尺
# 一边是日常一边是永不, 低波动持仓等于无人监控。换成标的自身 ATR 后全池收进
# 7.9%-32.1%:
#
#     阈值          触发率(全池均值)   频次/票      跨票区间
#     固定 >5%          25.2%        每 4.0 天    0.0% - 68.4%   ← 尺不统一
#     1.00x ATR14       18.8%        每 5.3 天    7.9% - 32.1%   ← 现行
#     1.25x ATR14       11.1%        每 9.0 天
#     1.50x ATR14        6.6%        每 15.2 天
#     2.00x ATR14        2.4%        每 42.3 天
#
# 倍数 1.0 = 用户  定 (换尺后总量反而比固定 5% 更低)。想调只动
# MOVE_ATR_MULT 一个常数, 上表就是代价表。
#
# ATR14 而非 ATR20: technical.atr 的口径分工 () 是 ATR20=算钱
# (stop 距离/sizing/回测), ATR14=读盘。告警问的是"这动得反常吗", 读盘。
MOVE_ATR_PERIOD = 14
MOVE_ATR_MULT = 1.0
MOVE_PCT_FALLBACK = 5.0
OPTION_MOVE_PCT = 20.0      # 期权无 kline 缓存, 保持固定档


def move_threshold(ticker: str) -> Tuple[float, str]:
    """(阈值 %, 口径标签)。kline 不足以算 ATR → 回落固定档, **并在标签里说出来**:
    静默降级就是又一个只在事后才发现的失效 (memory data_discipline)。"""
    rows = [r for r in iter_jsonl(KLINE_CACHE_DIR / f"{ticker}.jsonl")
            if r.get("close") is not None
            and r.get("high") is not None and r.get("low") is not None]
    if len(rows) > MOVE_ATR_PERIOD:
        series = atr(rows, period=MOVE_ATR_PERIOD)
        n = series[-1] if series else None
        last_close = normalize_num(rows[-1].get("close"))
        if n and last_close:
            return (MOVE_ATR_MULT * n / last_close * 100,
                    f"{MOVE_ATR_MULT:g}N ATR{MOVE_ATR_PERIOD}")
    return MOVE_PCT_FALLBACK, f"{MOVE_PCT_FALLBACK:g}% 兜底 (kline 不足)"


INDEX_TICKERS = ("QQQ", "SPY")
INDEX_DROP_PCT = -2.0


def index_risk() -> List[Dict]:
    """大盘补充 (heartbeat.md 检查项 2): QQQ/SPY 最近完成日线跌 > 2%。

    : 这条检查此前写在持仓循环里 (`if ticker in {"QQQ","SPY"}`), 而
    load_rows_from_state 只产出 `broker.qty != 0` 的持仓 —— QQQ/SPY 不是持仓,
    永远进不了那个循环。**一条描述得很具体的死代码, 比没有这条检查更危险**:
    读规则文档的人以为大盘风险有人看着。

    数据源改成 kline 缓存 = 已完成日线, 与 stop_breach 同源, 白拿"不吃盘中未
    完成 bar"那道闸。代价: 盘中那轮 (ET 12:26) 读的是上一个完成交易日; 四轮
    heartbeat 里另外三轮 (ET 18:26 / 00:26 / 06:26) 本来就在收盘之后。
    """
    out: List[Dict] = []
    for ticker in INDEX_TICKERS:
        closes = _cached_closes(ticker, 2)
        if len(closes) < 2:
            continue
        (_, prev), (day, close) = closes
        if not prev or close is None:
            continue
        pct = (close / prev - 1) * 100
        if pct < INDEX_DROP_PCT:
            out.append({"ticker": ticker, "date": day, "close": close, "pct": pct})
    return out


def move_trigger_type(pct: float) -> str:
    """方向进 key。同一 ticker 的涨与跌是两个事件, 共用一个 latch 会让后一个被
    前一个的 24h 冷却吃掉: 某标的白天 +9.43% 合法触发, 当晚自家财报
    -7.5% 时条件仍"持续成立" → should_emit 直接 False, 夜盘那记跳空一声没有。
    (那晚真正让它没被吃掉的, 反而是 change_pct 用错基准算出的假 +1.01%。)"""
    return "adverse_move_up" if pct > 0 else "adverse_move_down"


# 状态卡头衔。**这张表漏一个 type, fired_cards 就整个空掉**(heartbeat 推的
# 就是 fired_cards), 所以 move_trigger_type 的两个方向由测试钉住必须在表内。
HEADLINES = {"stop_breach": "🚨 STOP",
             "exit_overdue": "🚨 STOP未执行",
             "adverse_move_up": "⚠️", "adverse_move_down": "⚠️"}

# 改过名的 trigger, 老 key 留在 tmp/heartbeat-state.json 里会永远躺着 (ticker
# 仍在 universe 内, prune_dead_keys 按 ticker 判不会碰)。
RETIRED_TRIGGERS = {"adverse_move"}      # → adverse_move_up / _down,

# 预算超顶告警阈值 (A7, 用户): 整股约束下 1.0-1.25× 是买不起
# 半股的正常溢出 (AAOI 1.18× / MRVL 1.01× 都属此), 超过才是真出格。
CAP_MULTIPLE_ALERT = 1.25


# ── 本轮 ladder 预算 (机器真值, 不再只活在卡片 prose) ──────────────
#
# : MU 的 "ladder 已用尽" 只写在 lot notes 和 action.reason 里,
# 机器读不到 → brief 照喊 "进入首档吸纳区 885-925", heartbeat 照抄, 一笔预算
# 早已花光的仓位被播成加仓信号 —— 预算行由此而生。
#
#  分类轴退役: cap 来源只剩 current_regime.cap_pct_nav (二值), gate 预算
# GATE_RISK_CAPS 单 schedule —— 入场侧和持仓侧对"这一档上限是多少"必须同一份答案。


def _latest_nav() -> Optional[float]:
    last = None
    for r in iter_jsonl(NAV_HISTORY_PATH):
        last = r
    return normalize_num((last or {}).get("total_assets"))


def campaign_fills(ticker: str) -> List[Dict]:
    """本轮 campaign 的成交 (上一次清仓事件之后), 按落库顺序。

    重进 = 新 campaign, 上一轮的 size 不该继续占用本轮预算 (MU 7/16 全清 →
    7/24 重进首笔)。`close` 事件是分界。
    """
    fills: List[Dict] = []
    for month_dir in sorted(p for p in EVENTS_DIR.glob("2*") if p.is_dir()):
        for row in iter_jsonl(month_dir / "trades.jsonl"):
            if str(row.get("ticker", "")).upper() != ticker.upper():
                continue
            et = row.get("event_type")
            if et in {"close", "expire", "assign"}:
                fills = []            # campaign 结束, 计数归零
            elif et == "fill" and str(row.get("side", "")).upper() == "BUY":
                fills.append(row)
    return fills


def exposure_map(items: List[Dict]) -> Dict[str, float]:
    """{underlying: 当前敞口 USD} —— 分类 cap 累计扣减的减数 (C4/用户
    「cap 应该重算, 本身 cap 就是跟随 NAV」)。**市值口径**: cap = 40%×NAV 逐日
    mark-to-market, 减数也必须是今天的钱, 不能冻在成本上。

    单名含期权腿 (期权按保费市值, 与 8/10 gross 裁决同拍) —— "单票总暴露"按
    position-tiers 含期权。数据源 = brief 自己的 rows (已滤掉结构豁免层),
    与 `book.exposure_of` 同口径但不重读 state: 同一轮播报的敞口与算 cap 的敞口
    必须是同一份数。
    """
    out: Dict[str, float] = {}
    for x in items:
        qty = normalize_num(x.get("qty")) or 0
        px = normalize_num(x.get("current_price"))
        if not qty or not px or x.get("synthetic"):
            continue
        mult = (parse_occ(x["ticker"]) or {}).get("multiplier") or 1
        und = str(x.get("underlying") or x["ticker"]).upper()
        out[und] = out.get(und, 0.0) + abs(qty) * px * mult
    return out


def position_budget(ticker: str, item: Dict, nav: Optional[float],
                    cfg: Optional[Dict] = None,
                    usage: Optional[Dict] = None,
                    calendar: Optional[Dict] = None,
                    exposure: Optional[float] = None,
                    card: Optional[Dict] = None,
                    power_room: Optional[float] = None) -> Optional[Dict]:
    """本轮**实际**cap 还剩多少 / 还买得起几股。

    None = 无法判定 (无成交记录或无 NAV) —— 沉默好过编一个数。
    cfg 带 current_regime 时, cap 与 regime cap 取小 (regime 只收紧不放宽)。

    正股预算统一走 `sizing.entry_cap`: regime、gate risk、at-risk、组合冲击、
    gross 与购买力六条约束共同取小。算钱价格使用 z1 上沿，与首档限价和授权组
    口径一致。`usage` 不传会把组合当作空仓，调用方应整轮复用同一份占用快照。

    期权行只用分类 cap: 组合层那两条是**标的名义**上限, 而这里的 `deployed`
    是**保费**, 两种钱不能直接取小 (换算式见 `sizing.notional_to_premium_cap`);
    换算需要 delta+spot, 拿不到就宁可只报分类 cap 并在 `portfolio_binding`
    里写明没接。期权 gate premium 使用另一量纲，本函数不编造换算。
    """
    if not item.get("qty") or nav is None:
        return None
    fills = [f for f in campaign_fills(ticker) if f.get("tier") != "base"]
    if not fills:
        return None
    regime = (cfg or {}).get("current_regime") or {}
    rc = regime.get("cap_pct_nav")
    #  分类轴退役: 授权量只有 regime cap 一个来源。无 regime → None
    # (无法判定, 沉默好过编数); cap 0 → **照出预算行**(额度 0 显式化 —— 旧逻辑
    # 反查分类失败直接 return None, 预算行消失 = can_add 失败为开的另一半)。
    if not isinstance(rc, (int, float)):
        return None
    cap_frac = float(rc)

    occ = parse_occ(ticker)
    mult = (occ or {}).get("multiplier") or 1
    # campaign 内全部 BUY fill 都计入投入 (分类过滤随轴退役 —— 本 campaign 的钱
    # 就是本 campaign 的钱)。
    deployed = sum((normalize_num(f.get("qty")) or 0) * (normalize_num(f.get("price")) or 0) * mult
                   for f in fills)
    price = normalize_num(item.get("current_price"))
    # 组合层 (冲击预算 / gross) —— 名义口径, 所以正股直接取小, 期权走换算或不接。
    und = (occ or {}).get("underlying") or ticker
    # 显式传模块级 KLINE_CACHE_DIR (而不是吃 book 的默认值): 本模块所有 kline 读取
    # 都走这个 global, 测试替换它就能整体隔离 —— 半边读 tmp 半边读真缓存最难查。
    ev = earnings.event_state(und, et_today(), calendar)
    # 分类 cap = **累计**语义, 减掉该名当前敞口 (B7/C4)。口径 = **市值**
    # (用户 : 「cap 应该重算, 本身 cap 就是跟随 NAV」—— cap = 40%×NAV
    # 逐日 mark-to-market, 减数冻在成本上就是同一个减法两边活在不同时间)。
    # 单名口径含期权 (按保费市值, 与 8/10 gross 裁决同拍), 所以按 underlying 取。
    # `exposure` 由调用方按 **underlying 聚合**传入 (run() 用自己那份 rows 算, 见
    # `exposure_map`) —— 单名口径含同标的的股+期权腿。缺省 = 只算本行自己,
    # 独立调用/测试时正确且不去读全局 state (读全局会让单测吃到 live 持仓)。
    if exposure is None:
        exposure = abs(normalize_num(item.get("qty")) or 0) * (price or 0) * mult
    atr_value = book.atr20_of(und, KLINE_CACHE_DIR)
    underlying_price = book.price_of(und, KLINE_CACHE_DIR)
    event_shrink = ev["shrink"] if ev["active"] else None
    gate_no = sizing.gate_count(card)
    portfolio_binding = None
    unit = (price or 0) * mult
    if occ:
        ac = sizing.available_cap(
            nav, cap_frac, atr_value, underlying_price, usage,
            event_shrink=event_shrink, exposure=exposure,
        )
        cap = ac["regime_cap"] if ac["regime_cap"] is not None else nav * cap_frac
        pc = sizing.notional_to_premium_cap(
            min(x for x in (ac["shock_budget_cap"], ac["gross_cap"]) if x is not None)
            if any(x is not None for x in (ac["shock_budget_cap"], ac["gross_cap"])) else None,
            normalize_num((cfg or {}).get("delta")),
            underlying_price, price)
        if pc is not None and pc < cap:
            cap, portfolio_binding = pc, "notional→premium"
        elif pc is None:
            portfolio_binding = "未接 (期权缺 delta/标的价 — 组合层只在书级口径生效)"
    else:
        calculation_price, _ = sizing.line_price(
            (cfg or {}).get("accumulation_zone_1"), price)
        stop, _ = sizing.binding_stop(
            calculation_price,
            [normalize_num((cfg or {}).get("stop_level"))],
            atr_value,
            2.0,
        )
        entry = sizing.entry_cap(
            nav, cfg, atr=atr_value, price=calculation_price, stop=stop,
            usage=usage, event_shrink=event_shrink, gate_no=gate_no,
            power_room=power_room, exposure=exposure,
        )
        cap = (
            entry["cap_usd"] if entry["cap_usd"] is not None
            else max(nav * cap_frac - float(exposure or 0.0), 0.0)
        )
        if entry.get("binding") not in (None, "regime"):
            portfolio_binding = entry["binding"]
        if calculation_price:
            unit = calculation_price * mult
    # cap 已经净掉当前敞口 —— **不再减 deployed**(那是成本口径, 会双重扣减)。
    # deployed 保留为"本 campaign 已投入"的展示量与 cap_multiple 的分子。
    remaining = max(0.0, cap)
    addable = int(remaining // unit) if unit > 0 else 0
    return {
        # 档位 = 这张卡爬过几个 gate (`sizing.gate_count`, 判据 = regime_history)。
        #  二次定版前这里用 `campaign_round_no` (1 + 不同入场日数) ——
        # 一个连"过没过 gate"都不看的近似, 与授权侧读的根本不是同一件事。
        # card 缺省 (独立调用/测试) → 第一档, 不去读全局 state。
        "gate_no": gate_no,
        "regime_cap_frac": cap_frac,
        "regime_name": regime.get("name"),
        "portfolio_binding": portfolio_binding,
        "event_shrink": ev["shrink"] if ev["active"] else None,
        "earnings_reaction_day": ev["reaction_day"] if ev["active"] else None,
        "class_cap_usd": round(nav * cap_frac, 2),   # 名义 (未过组合层/财报缩仓/敞口扣减)
        "exposure_usd": round(exposure, 2),
        "cap_usd": round(cap, 2),
        "deployed_usd": round(deployed, 2),
        "remaining_usd": round(remaining, 2),
        # 分母 = **名义**class_cap (不是净额): 净额扣完敞口常为 0, 除法会消失,
        # 而超顶告警恰恰要在那时候响 (AAOI 1.18× 就是这个形状)。
        "cap_multiple": round(deployed / (nav * cap_frac), 2) if cap_frac else None,
        "addable_shares": addable,
        # 预算耗尽 = 连 1 股都买不起。整股约束下 "还剩 $208" 和 "剩 $0" 对
        # 一只 $912 的票没有区别, 别把买不到的额度播成可加仓。
        "exhausted": addable < 1,
    }


def position_ladder(ticker: str, cfg: Optional[Dict], budget: Optional[Dict],
                    card: Optional[Dict], today: Optional[str] = None) -> Optional[Dict]:
    """持仓加仓的三档阶梯 —— 与 entry_guard 同一套价位 (`build_ladder`) 与整股降级
    (`sizing.share_ladder`), 钱 = 预算行已算出的 `remaining_usd`。

    持仓卡渲染明确阶梯，使后续清单可以照抄而不是发明分批数字。
    模式判定逐字同 entry_guard.evaluate_ticker: 刚收复的快车道 gate →
    冻结锚 (repair_reclaim), 否则 momentum_mode (parabolic / normal)。

    None = 不出这一行: 无预算 / 预算耗尽 / 期权 (走 option_plan) / 无 zone / 无 K 线。
    """
    if not budget or budget.get("exhausted") or not (budget.get("remaining_usd") or 0) > 0:
        return None
    if parse_occ(ticker):
        return None
    zb = _eg.zone_bands(cfg or {})
    if not zb:
        return None
    rows = [r for r in iter_jsonl(KLINE_CACHE_DIR / f"{ticker}.jsonl")
            if normalize_num(r.get("close")) is not None]
    if not rows:
        return None
    close = normalize_num(rows[-1].get("close"))
    if not close:
        return None
    full = [r for r in rows if r.get("high") is not None and r.get("low") is not None]
    series = atr(full, period=_eg.PARAMS["atr_period"]) if len(full) > _eg.PARAMS["atr_period"] else []
    atr_v = series[-1] if series else None
    agent = (card or {}).get("agent") or {}
    reclaimed = _eg.reclaimed_gate_of(agent, today or et_today(), rows)
    mode = "repair_reclaim" if reclaimed else _eg.momentum_mode(rows)
    prices = _eg.build_ladder(mode, close, rows, zb, atr=atr_v, reclaim=reclaimed)
    if not prices:
        return None
    sl = sizing.share_ladder(budget["remaining_usd"], prices, _eg.PARAMS["ladder_weights"])
    if not sl:
        return None
    # 持仓加仓票与 watchlist 票使用同一把结构排序尺。
    # 只排序, 不改预算/阶梯里的任何数字。
    pr = _dow.priority_of_bars(full, resample_weekly(full)) if len(full) >= 3 else None
    return {"mode": mode, "prices": prices, "tiers": sl["tiers"], "n_tiers": sl["n_tiers"],
            "n_shares": sl["n_shares"], "degraded": sl["degraded"],
            "exception": sl["exception"], "note": sl["note"], "dow": pr}


# ── 财报缩仓的减仓半边 (ledger A2) ──────────────────────
#
# earnings_window 裁决原话「必须在反应日的**前一交易日收盘前减完**」此前只有
# 入场半边 (available_cap 的 event_shrink 只压新买), T-1 日已有敞口超过缩仓后
# cap 时无任何提示。8/11 LITE (反应日 8/12, ×0.24) 是首个 live 案例。

def at_risk_breach_signal(item: Dict, cfg: Optional[Dict], guard: Optional[Dict],
                          nav: Optional[float]) -> Optional[Dict]:
    """单笔 at-risk 天花板的持仓侧核查 (risk_unit (b), N1 立项):
    **止损打掉后的损失 ≤ AT_RISK_CAP_FRAC (5%) × NAV**。

    正股: at-risk = qty × (现价 − binding stop), binding = max(卡 stop, 吊灯 trail)
    多头取大 —— 与 stop_status / price_reminder 下方线同口径。
    期权 (long): at-risk = 保费 mark × **OPTION_ATRISK_LOSS_FRAC (0.25)**—— S1 premium
    硬 stop (-25%) 接线后的 loss/unit (用户  裁决放宽自保费全额; 系数与
    exit_guard 的 opt_s1_stop 相等由 pins 测试钉)。标的 stop 仍不给期权折扣。
    入场侧的对称物 = entry_guard.size_caps 第⑤条 (同一常量, 那边几乎恒被 n_unit
    先咬 = 后备; 真咬点在这里)。无 stop 正股跳过 (v1; 无界 at-risk 归 zone/schema 层)。
    """
    if not nav or not item.get("qty") or item.get("synthetic"):
        return None
    price = normalize_num(item.get("current_price"))
    qty = normalize_num(item.get("qty")) or 0
    if not price or qty <= 0:                 # 空头腿不在 long-only 口径内
        return None
    cap = sizing.AT_RISK_CAP_FRAC * nav
    occ = parse_occ(item["ticker"])
    if occ:
        mult = occ.get("multiplier") or 100
        per_unit = price * mult * sizing.OPTION_ATRISK_LOSS_FRAC
        at_risk = qty * per_unit
        if at_risk <= cap:
            return None
        keep = int(cap // per_unit)
        return {"ticker": item["ticker"], "kind": "option_premium",
                "at_risk_usd": round(at_risk, 2), "cap_usd": round(cap, 2),
                "pct_nav": round(at_risk / nav * 100, 1), "stop": None,
                "stop_basis": f"保费 × {sizing.OPTION_ATRISK_LOSS_FRAC:.0%} (S1 premium stop)",
                "trim_units": int(qty - keep)}
    card = normalize_num((cfg or {}).get("stop_level"))
    trail = normalize_num(((guard or {}).get("trail") or {}).get("trail"))
    stops = [s for s in (card, trail) if s is not None]
    if not stops:
        return None
    stop = max(stops)
    per_share = price - stop
    if per_share <= 0:                        # stop 在价上 = 已在破位区, 归 stop_breach
        return None
    at_risk = qty * per_share
    if at_risk <= cap:
        return None
    keep = int(cap // per_share)
    return {"ticker": item["ticker"], "kind": "stock",
            "at_risk_usd": round(at_risk, 2), "cap_usd": round(cap, 2),
            "pct_nav": round(at_risk / nav * 100, 1), "stop": stop,
            "stop_basis": ("trail" if (trail is not None and stop == trail
                                       and (card is None or trail >= card)) else "card"),
            "trim_units": int(qty - keep)}


def shock_breaker_signal(items: List[Dict], nav: Optional[float]) -> Optional[Dict]:
    """冲击熔断 (shock_budget §2.7, 用户  定版): 受管书单日合计损失
    ≥ SHOCK_BREAKER_MULT × SHOCK_BUDGET × NAV (22.5% NAV) → 熔断。
    裁决动作 = **直接清仓, 盘中立即执行**(原话 「单日触碰亏损达到回撤
    要求, 立即减仓甚至清仓, 盘中立即执行」; 08-09 定「直接清仓」)。

    日内损益用 brief 已有的报价字段现算 (qty×乘数×(现价−昨收)), 不等 NAV 快照 ——
    nav_snapshot 是 eod_only, 而 §2.7 要求盘中执行。base 豁免层不在 rows 里,
    与 book.load_usage 的「预算只数受管书」同口径。
    """
    if not nav:
        return None
    total, n = 0.0, 0
    for x in items:
        if x.get("synthetic") or not x.get("qty"):
            continue
        pct = normalize_num(x.get("change_pct"))
        price = normalize_num(x.get("current_price"))
        if pct is None or not price or pct <= -100:
            continue
        mult = (parse_occ(x["ticker"]) or {}).get("multiplier") or 1
        prev = price / (1 + pct / 100.0)
        total += (normalize_num(x.get("qty")) or 0) * mult * (price - prev)
        n += 1
    if n == 0:
        return None
    threshold = sizing.SHOCK_BREAKER_MULT * sizing.SHOCK_BUDGET * nav
    if total > -threshold:
        return None
    return {"day_pnl_usd": round(total, 2), "pct_nav": round(total / nav * 100, 2),
            "threshold_usd": round(-threshold, 2), "n_positions": n}


def calendar_stale_note(calendar: Optional[Dict], today: str,
                        max_bdays: int = 2) -> Optional[str]:
    """财报日历陈旧 > max_bdays 个工作日 (或整个缺失) → 告警文本, 否则 None。

    earnings.event_state 自己的兜底阈值是 7 天 — 那是"过期也照用"的下限播报;
    这里是**基础设施告警**: 日历只在 briefing 模式重建, 连续 3 个 briefing 失败
    就意味着缩仓在用改期风险未知的旧反应日。工作日口径, 周末不累积。
    """
    if not calendar:
        return ("INFRA ⚠️ 财报日历缺失 — 财报缩仓/T-1 减仓检查全部失效 "
                "(跑 .venv/moomoo/bin/python3 quant/data/earnings_calendar.py --json)")
    as_of = str(calendar.get("as_of") or "")[:10]
    try:
        d0, d1 = date.fromisoformat(as_of), date.fromisoformat(str(today)[:10])
    except ValueError:
        return f"INFRA ⚠️ 财报日历 as_of 不可解析 ({as_of!r}) — 视同缺失, 重建日历"
    age = sum(1 for i in range(1, max((d1 - d0).days, 0) + 1)
              if (d0 + timedelta(days=i)).weekday() < 5)
    if age <= max_bdays:
        return None
    return (f"INFRA ⚠️ 财报日历 as_of {as_of} 已 {age} 个工作日未重建 "
            f"(> {max_bdays}) — briefing 的 earnings 步连续失败? 反应日可能已改期, "
            "缩仓正在用旧日历 (静默不缩仓是这条规则最可能的失效形态)")


def _weekdays_between(d0: date, d1: date) -> int:
    """(d0, d1) 开区间内的工作日数。0 = d0 就是 d1 前最后一个工作日。"""
    n, d = 0, d0 + timedelta(days=1)
    while d < d1:
        if d.weekday() < 5:
            n += 1
        d += timedelta(days=1)
    return n


def earnings_trim_signal(item: Dict, calendar: Optional[Dict], today: str,
                         nav: Optional[float] = None) -> Optional[Dict]:
    """今天是 T-1 (下一交易日 = 反应日) 且敞口 > 明日缩仓后上限 → 减仓信号。

    基数 = **名义上限**`sizing.NOTIONAL_CAP` × NAV × 明日 shrink_ratio —— 裁决
    字面 (`core/earnings.py` 顶部:「财报反应日 T+0 的仓位上限 = 名义上限 ×
    缩仓比例(per-ticker) × 0.7」)。敞口 = |qty| × 现价 × 乘数。组合层 (冲击预算 /
    gross) 不进这里 —— 明日书的占用未知, 而裁决压的本来就是名义上限那一项。
    NAV 拿不到 = 不知道, 不编数 (返回 None)。

    🚨 **基数不取 regime cap**(修): regime cap 答的是"能不能进 / 还能
    加多少" —— 持仓卡 `cap 0` = **无加仓授权**(NBIS 规则), 不是"这个
    仓位该多大"; 而 earnings.py 顶部同时写明本条属 sizing 层, **只改"那一天能有
    多大", 不改"能不能进"**。两个轴, 不可互借。
    此前基数取 `position_budget.class_cap_usd` (= NAV × regime cap): 分类轴退役前
    confirmed 卡上它与名义上限恰好相等, 区别看不出来。08-13 cap 二值 {0, 40%} 后
    cap 0 的持仓卡把目标算成 $0 = **每个反应日要求清仓**; 为这个 case 写的
    `if base is None` 回退同一天失效 —— `position_budget` 改成 cap 0 也照出预算行
    (`class_cap_usd` = **0.0**而不是 None), 回退再没触发过 (改一个调用点, 下游的
    None 检查静默失效)。COHR  实证: 20 股 $6,854 被要求"减 20 股",
    裁决口径是减 10 股 (目标 40%×NAV×0.44 ≈ $3,534)。

    ⚠️ **期权行单位偏松 (已知, 未修)**: 这里的敞口是**保费市值**, 名义上限是
    **标的名义**—— 两种钱 (`sizing.notional_to_premium_cap`)。换算要 delta+spot,
    本函数拿不到 cfg, 所以期权只在真出格时才响。收紧要另立裁决, 不在这里顺手改。

    工作日近似当交易日: 周五对周一反应日照样算 T-1; 美股节假日贴着反应日的
    极端情形可能早/晚一天, 接受 (无节假日表, 反应日本身来自 vendor 交易日)。
    """
    if not item.get("qty") or item.get("synthetic") or not nav:
        return None
    und = str(item.get("underlying") or item["ticker"]).upper()
    row = ((calendar or {}).get("tickers") or {}).get(und) or {}
    day = str(row.get("next_reaction_day") or "")[:10]
    if not day:
        return None
    try:
        d0, d1 = date.fromisoformat(str(today)[:10]), date.fromisoformat(day)
    except ValueError:
        return None
    if not (d0 < d1 and _weekdays_between(d0, d1) == 0):
        return None
    shrink = earnings.shrink_ratio(normalize_num(row.get("x_earn")))
    price = normalize_num(item.get("current_price"))
    if not price:
        return None
    occ = parse_occ(item["ticker"])
    unit = price * ((occ or {}).get("multiplier") or 1)
    exposure = abs(normalize_num(item.get("qty")) or 0) * unit
    basis = f"名义上限 {sizing.NOTIONAL_CAP:.0%}"
    target = nav * sizing.NOTIONAL_CAP * shrink
    if exposure <= target:
        return None
    excess = exposure - target
    return {"ticker": item["ticker"], "reaction_day": day,
            "shrink": round(shrink, 2), "exposure_usd": round(exposure, 2),
            "cap_usd": round(target, 2), "excess_usd": round(excess, 2),
            "cap_basis": basis,
            "trim_units": int(-(-excess // unit)) if unit > 0 else None,
            "is_option": bool(occ)}


# ── 止损/止盈显示合同 (用户: 每卡两条线, 各基折 max 只显示一个值) ──
#
# 历史:  之前只印 trail (MU trail 750.84 比卡片 stop 800 松 6.2%, 把会
# 先触发的线藏了) → 双线并列标 binding →  收敛为**单值 + 来源**:
# 各基 (card/lock 棘轮地板/trail) 照常运转, 显示层取 max — 与 price_reminder 的
# stop:card|lock|trail 词表同源。期权全部折进**保费空间**(S1 地板原生, 正股
# stop/trail 经 premium_proj 投影) — 旧实现拿期权 mark 比正股价位, 距离恒
# +1500%, 一个永远显示同一件事的数字等于没有数字。
def stop_status(ticker: str, item: Dict, cfg: Optional[Dict],
                guard: Optional[Dict], *, und_spot: Optional[float] = None,
                today: Optional[str] = None) -> Optional[Dict]:
    if not isinstance(cfg, dict) or not item.get("qty"):
        return None
    if item.get("synthetic") or cfg.get("role") in UNMANAGED_ROLES:
        return None
    from quant.decision.exit_guard import PARAMS as _XP
    from quant.options import premium_proj
    price = normalize_num(item.get("current_price"))
    card = normalize_num(cfg.get("stop_level"))
    days = max(1, int(normalize_num(cfg.get("stop_level_days")) or 1))
    trail_block = (guard or {}).get("trail") or {}
    ratchet = (guard or {}).get("ratchet") or {}
    is_opt = bool(item.get("is_option"))
    kw = {}
    if is_opt:
        avg_cost = normalize_num(item.get("avg_cost"))
        kw = {
            "s1_floor": round(avg_cost * (1 + _XP["opt_s1_stop"]), 2) if avg_cost else None,
            "mark": price, "spot": normalize_num(und_spot),
            "strike": normalize_num(item.get("strike")),
            "dte_days": premium_proj.dte_of(ticker, today or et_today()),
            "iv_pct": normalize_num(item.get("iv_pct")),
            "delta": normalize_num(item.get("delta")),
            # 期权 S5g gate 线 (用户): exit_guard 已算好写在 guard 上, 只读
            "gate_level": normalize_num(((guard or {}).get("opt_gate_stop") or {}).get("level")),
            "gate_days": int(normalize_num(((guard or {}).get("opt_gate_stop") or {}).get("days")) or 1),
        }
    st = premium_proj.binding_stop(
        is_option=is_opt, card_stop=card, card_days=days,
        lock_floor=normalize_num(ratchet.get("floor")), lock_stage=ratchet.get("stage"),
        trail=normalize_num(trail_block.get("trail")), trail_mode=trail_block.get("mode"),
        trail_broken=bool(trail_block.get("broken")), **kw)
    if not st:
        return None
    st["price"] = price
    st["dist_pct"] = ((st["value"] - price) / price * 100) if price else None
    st["breached"] = bool(price is not None and st["value"] is not None
                          and price <= st["value"])
    return st


def format_stop_status(st: Dict) -> str:
    d = f" ({st['dist_pct']:+.1f}%)" if st.get("dist_pct") is not None else ""
    tail = " ⚠️已在线下" if st.get("breached") else ""
    return f"{st['value']:g} ({st['source']} {st['note']}){d}{tail}"


def trim_status(item: Dict, cfg: Optional[Dict], *, und_spot: Optional[float] = None,
                und_cfg: Optional[Dict] = None, today: Optional[str] = None) -> Optional[Dict]:
    """止盈线: 正股 = 本卡 trim 带; 期权 = 正股 trim 下沿的保费投影 (trim 带住在
    underlying 卡上 — 期权卡 zones 只有 stop)。"""
    from quant.options import premium_proj
    price = normalize_num(item.get("current_price"))
    if not item.get("is_option"):
        band = normalize_zone_pair((cfg or {}).get("trim_zone"))
        if not band:
            return None
        dist = ((band[0] - price) / price * 100) if price else None
        return {"kind": "band", "lo": band[0], "hi": band[1], "dist_pct": dist}
    band = normalize_zone_pair((und_cfg or {}).get("trim_zone")) \
        or normalize_zone_pair((cfg or {}).get("trim_zone"))
    if not band:
        return None
    pj = premium_proj.project_call_premium(
        band[0], mark=price, spot=normalize_num(und_spot), strike=normalize_num(item.get("strike")),
        dte_days=premium_proj.dte_of(item.get("ticker") or "", today or et_today()),
        iv_pct=normalize_num(item.get("iv_pct")), delta=normalize_num(item.get("delta")))
    if not pj:
        return None
    dist = ((pj["premium"] - price) / price * 100) if price else None
    return {"kind": "proj", "premium": pj["premium"], "method": pj["method"],
            "und_level": band[0], "dist_pct": dist}


def format_trim_status(t: Dict) -> str:
    d = f" ({t['dist_pct']:+.1f}%)" if t.get("dist_pct") is not None else ""
    if t["kind"] == "band":
        return f"trim {t['lo']:g}-{t['hi']:g}{d}"
    return f"≈{t['premium']:g} (正股 trim {t['und_level']:g} 投影({t['method']})){d}"


def gate_status(ticker: str, cfg: Optional[Dict]) -> List[str]:
    """卡片自己写的 reclaim gate 及其达成状态 —— 转移锚点得进播报,
    否则告警文本没有锚, 只能靠叙述补。"""
    gates = (cfg or {}).get("reclaim_gates")
    if not isinstance(gates, list):
        return []
    out: List[str] = []
    for g in gates:
        level = normalize_num((g or {}).get("level"))
        if level is None:
            continue
        days = max(1, int(normalize_num(g.get("days")) or 1))
        closes = _cached_closes(ticker, days)
        if len(closes) < days:
            state = "?"
        else:
            state = "✅" if all(c is not None and c > level for _, c in closes) else "✗"
        label = str(g.get("label") or "").split("(")[0].strip()
        out.append(f"{level:g}×{days}日 {state}" + (f" {label}" if label else ""))
    return out


# ── 持仓状态卡 (heartbeat 原文推送, 不再由 LLM 叙述) ────────────────
#
# : heartbeat 的 Telegram 文本一直是 LLM 拿 brief JSON 现写的散文,
# 而那份 JSON 里没有预算、没有卡片 stop、没有 gate —— 缺什么就叙述什么, 于是
# 播出了"仍在首档吸纳区"(预算早已用尽) 和 "trail stop 750.84 未破"(真正 binding
# 的是卡片 800)。修法跟 exit guard 告警同规: 文本由代码渲染, 推送不得改写。
STRUCT_CN = {"up": "上升", "down": "下降", "sideways": "震荡"}


def format_regimes(cfg: Optional[Dict]) -> str:
    """滚动窗口的两端 — 破了往哪落, 收复了往哪升。"""
    parts: List[str] = []
    last = (cfg or {}).get("last_regime") or {}
    if last:
        z = (last.get("zones") or {})
        z1 = normalize_zone_pair(z.get("accumulation_zone_1"))
        band = f" 观察带 {z1[0]:g}-{z1[1]:g}" if z1 else ""
        stop = normalize_num(z.get("stop_level"))
        air = " ⚠️空气区" if last.get("air_pocket") else ""
        parts.append(f"↓{band}{f' stop {stop:g}' if stop is not None else ''} "
                     f"(cap 0, 只观察){air}")
    nxt = (cfg or {}).get("next_regime") or {}
    gate = nxt.get("entry_gate") or {}
    lvl = normalize_num(gate.get("level"))
    if lvl is not None:
        cap = nxt.get("cap_pct_nav")
        cap_txt = f" cap {cap * 100:g}%" if isinstance(cap, (int, float)) else ""
        # 语义分层 (): unlocks (probe/confirmed) 是仓位层词汇, 渲染层
        #  起停读 —— gate_ladder_report 同日已停, 这里  补齐
        # (ledger R4: 两个渲染层不同步, 且本行在 fired_cards 推送链上)。
        parts.append(f"↑{lvl:g}×{int(normalize_num(gate.get('days')) or 1)}日 → "
                     f"{nxt.get('name') or '?'}{cap_txt}")
    return " | ".join(parts)


def build_position_card(item: Dict, cfg: Optional[Dict], budget: Optional[Dict],
                        guard: Optional[Dict], *, headline: str = "",
                        und_spot: Optional[float] = None,
                        und_cfg: Optional[Dict] = None) -> str:
    ticker = item["ticker"]
    head = f"{headline} " if headline else ""
    lines = [f"{head}{ticker} {fmt_pct(item.get('change_pct'))} @ "
             f"{fmt_price(item.get('current_price'))} ({item.get('price_source') or '?'})"]

    qty, cost = item.get("qty"), item.get("avg_cost")
    if qty and cost:
        upl = f" | UPL {item['unrealized_pl']:+,.2f}" if item.get("unrealized_pl") is not None else ""
        unit = "张" if item.get("is_option") else "股"
        lines.append(f"持仓 {qty:g} {unit} @ {cost:,.2f}{upl}")

    # regime 授权行先于预算行 —— cap 0 时"预算余 $0"看着像花完了, 而真相是
    # 这个 regime 从来没授权过。持仓票不走 entry_guard, 这是它到手册的唯一通道。
    lines.extend(regime_lines({"current_regime": (cfg or {}).get("current_regime")}))

    if budget:
        mult = f" ({budget['cap_multiple']:g}x)" if budget.get("cap_multiple") else ""
        tail = ("买不起 1 股 → zone 触碰非加仓信号" if budget["exhausted"]
                else f"还可 {budget['addable_shares']} 股")
        # 组合层 binding 必须点名: 一张写着 40% 的卡今天只给 34%, 不写出来就
        # 无法解释 —— 同 regime binding 那一条 (会驱动动作的约束要可解释)。
        if budget.get("portfolio_binding"):
            label = sizing.binding_label({
                "binding": budget["portfolio_binding"],
                "gate_no": budget.get("gate_no"),
            })
            tail += (f" [{label} binding: 名义 "
                     f"${budget.get('class_cap_usd') or 0:,.0f} → 实际 ${budget['cap_usd']:,.0f}]")
        if budget.get("event_shrink"):
            tail += (f" [📉 财报反应日 {budget.get('earnings_reaction_day')} 缩仓 ×"
                     f"{budget['event_shrink']:.2f}, T+1 起恢复全额]")
        lines.append(f"预算 [regime cap {budget['regime_cap_frac'] * 100:g}% · "
                     f"gate{budget['gate_no']} 上限"
                     f" {sizing.gate_risk_cap_frac(budget['gate_no']) * 100:g}%风险]: "
                     f"已投 ${budget['deployed_usd']:,.0f} / "
                     f"cap ${budget['cap_usd']:,.0f}{mult} → 余 ${budget['remaining_usd']:,.0f}, {tail}")
        # 与 entry_guard 的阶梯挂单同格式，清单照抄。
        lad = budget.get("ladder")
        if lad:
            seg = [f"${t['price']:g}×{t['shares']}股(${t['usd']:,.0f})" for t in lad["tiers"]]
            extra = f" — {lad['note']}" if lad.get("note") else ""
            lines.append(f"阶梯 [{lad['mode']}] {lad['n_tiers']}档: " + " / ".join(seg) + extra)
            if lad.get("exception"):
                lines.append("⛔ 未落 decisions.jsonl 书面例外前, 这不是可执行计划")
            if lad.get("dow"):
                lines.append(_eg.dow_line(lad["dow"]))

    st = stop_status(ticker, item, cfg, guard, und_spot=und_spot)
    if st:
        lines.append(f"止损 {format_stop_status(st)}")
    tz = trim_status(item, cfg, und_spot=und_spot, und_cfg=und_cfg)
    if tz:
        lines.append(f"止盈 {format_trim_status(tz)}")

    # 预算耗尽那句由上面的「预算」行负责, 这里不重复播。判据是 contains 不是
    # startswith —— 期权 hint 带 "基于正股 X: " 前缀, startswith 会整个失效,
    # 预算行播两遍 (GOOGL 案)
    hints = [h for h in (item.get("zone_hints") or []) if "本轮 " not in h]
    if hints:
        lines.append("位置 " + " | ".join(hints))

    gates = gate_status(ticker, cfg)
    if gates:
        lines.append("gate " + " | ".join(gates))

    regime = format_regimes(cfg)
    if regime:
        lines.append("regime " + regime)

    pr = (cfg or {}).get("pending_repaint")
    if pr:
        lines.append(f"⏳ 待补画 [{'/'.join(pr.get('slots') or [])}] since {pr.get('since')} "
                     "— 补画前不授权入场")

    struct = (guard or {}).get("structure") or {}
    if struct.get("trend"):
        sline = (f"结构 {struct.get('high_label')}+{struct.get('low_label')}"
                 f"({STRUCT_CN.get(struct['trend'], struct['trend'])})")
        # v3 ⑤ (): 动态 lsh = 修复线权威读数, 必须与结构标签同行渲染 ——
        # 卡上静态 gate 只是它的保守锚。数字不带机制单独出现 = 231 未送达事件的病根。
        lsh = struct.get("last_swing_high")
        if lsh:
            sline += f" | 动态 lsh ${lsh:g} (修复线权威读数, 随 swing 演化重锚; 卡上 gate=静态保守锚)"
        lines.append(sline)
    return "\n".join(lines)


def zone_staleness(ticker: str, cfg: Dict) -> List[str]:
    """Returns list of staleness reasons (empty = fresh). Time + price triggers;
    event-stale 仍需 LLM 判断 (宏观事件不可代码化)。"""
    reasons: List[str] = []
    today = et_today()
    vu = str(cfg.get("valid_until") or "")
    if vu and vu < today:
        reasons.append(f"valid_until {vu} 已过期")

    closes = _cached_closes(ticker, STALE_CONFIRM_DAYS + 1)
    trim = normalize_zone_pair(cfg.get("trim_zone"))
    z2 = normalize_zone_pair(cfg.get("accumulation_zone_2"))
    if len(closes) >= STALE_CONFIRM_DAYS:
        last2 = closes[-STALE_CONFIRM_DAYS:]
        if trim and all(c > trim[1] for _, c in last2):
            reasons.append(f"Breakout stale: 连续 {len(last2)} 日 close > trim 上沿 {trim[1]}")
    # A completed close below zone 2 invalidates the map immediately.
    if closes and z2 and closes[-1][1] < z2[0]:
        reasons.append(f"Breakdown stale: 收盘 {closes[-1][1]:g} < zone2 下沿 {z2[0]:g} "
                       "(当日口径 — 带内挂单语义已反转, 先重画再挂单)")
    # Use the same per-instrument volatility ruler as entry_guard. Missing ATR
    # data suppresses only this trigger and never creates a fixed fallback.
    krows = [r for r in iter_jsonl(KLINE_CACHE_DIR / f"{ticker}.jsonl")][-(MOVE_ATR_PERIOD * 3):]
    a14 = (atr(krows, MOVE_ATR_PERIOD) or [None])[-1] if krows else None
    kcl = [normalize_num(r.get("close")) for r in krows[-2:]]
    if a14 and len(kcl) == 2 and kcl[0] and kcl[1]:
        chg, thr = abs(kcl[1] / kcl[0] - 1), STALE_VOL_ATR_MULT * a14 / kcl[0]
        if chg > thr:
            reasons.append(f"Volatility stale: 单日 {chg * 100:.1f}% > "
                           f"{STALE_VOL_ATR_MULT:g}×ATR14 ({thr * 100:.1f}%)")
    return reasons


# ── IV percentile 信号 (数据攒够后自动生效) ────────────────────────

def iv_signal(ticker: str) -> Optional[str]:
    path = IV_HISTORY_DIR / f"{ticker}.jsonl"
    rows = [r for r in iter_jsonl(path) if r.get("atm_iv_pct") is not None]
    cutoff = (date.fromisoformat(et_today()) - timedelta(days=370)).isoformat()
    rows = [r for r in rows if r.get("date", "") >= cutoff]
    if len(rows) < 60:  # percentile 不可靠前保持沉默
        return None
    latest = rows[-1]["atm_iv_pct"]
    pct = 100 * sum(1 for r in rows if r["atm_iv_pct"] <= latest) / len(rows)
    if pct >= 80:
        return f"IV percentile {pct:.0f} (≥80 极端: 禁 naked long, 卖方压测 tail — 闸门表 §六)"
    if pct <= 20:
        return f"IV percentile {pct:.0f} (≤20 便宜: 买方友好窗口)"
    return None


def valuation_fyi_map(pos_doc: Optional[dict], wl_doc: Optional[dict]) -> Dict[str, str]:
    """卡片机器区 `agent.valuation.fyi` → {ticker: 一行}。判断层零授权效力: 简报只把这一行**原样**
    挂在该票依据行尾 (📐[FYI]), 不进动作判定、不进预算、不进任何告警。来源 = valuation_sync 镜像的
    valuations.jsonl 最新行 ( 用户: 「估值结果在哪里呈现」→ 卡片是简报读的地方)。
    positions 只取 active 正股卡, watchlist 只取 watching; 期权卡归 underlying。"""
    out: Dict[str, str] = {}
    for doc, want in ((pos_doc, "active"), (wl_doc, "watching")):
        for tk, card in ((doc or {}).get("tickers") or {}).items():
            if is_option_ticker(tk) or str((card or {}).get("status")) != want:
                continue
            v = ((card or {}).get("agent") or {}).get("valuation") or {}
            line = str(v.get("fyi") or "").strip()
            if line and tk not in out:
                out[str(tk).upper()] = line
    return out


def build_brief(rows: List[dict], ticker_col: str, *, mutate_state: bool = True) -> Dict:
    """mutate_state=False (--read-only): 报告当前条件但不消耗告警冷却、不写
    heartbeat-state — 手动/session 内查看用,否则会吃掉真 heartbeat 的提醒窗口。"""
    current_zones, watchlist_zones, watchlist_basic = load_zone_maps_from_state()
    items = [row_summary(r, ticker_col) for r in rows if (r.get(ticker_col) or "").strip()]
    nav = _latest_nav()
    # 组合占用整轮算一次, 与 entry_guard 同一份读法 (core.book) —— 入场侧和持仓侧
    # 对"书里还剩多少预算"必须是同一个答案, 否则简报上下两段互相打脸。
    usage = book.load_usage(nav=nav)
    calendar = earnings.load_calendar()      # 整轮一份 (财报反应日缩仓)

    # 触碰告警上的钱 = 白名单 grant 那一个数 (用户  Q2)。lazy import:
    # report(5) → sync(2) 是向下依赖 (分层合法), 但 today_group_sync 会拉 openapi
    # bootstrap, 没必要进本模块的 import 图 —— 与 exit_guard 的 PARAMS 同样处理。
    def actual_cap_of(ticker: str, px):
        try:
            from quant.sync import today_group_sync as _tgs
            card = (_load_state_doc(STATE_WATCHLIST_PATH).get("tickers") or {}).get(ticker) or {}
            ac = _tgs.grant_actual_cap(ticker, card, nav, usage, calendar=calendar)
            return {**ac, "binding_label": _tgs.binding_label(ac)} if ac else None
        except Exception:                                          # noqa: BLE001
            return None                # 算不出就说算不出 (下方回落名义并标注)

    exp_map = exposure_map(items)
    _pos_cards = (_load_state_doc(STATE_POSITIONS_PATH).get("tickers") or {})
    try:
        current_power_room = (book.power_room(et_today()) or {}).get("room")
    except Exception:                                          # noqa: BLE001
        current_power_room = None
    budgets = {x["ticker"]: position_budget(
        x["ticker"], x, nav, current_zones.get(x["ticker"]), usage, calendar,
        exposure=exp_map.get(str(x.get("underlying") or x["ticker"]).upper()),
        card=_pos_cards.get(x["ticker"]), power_room=current_power_room)
        for x in items}
    # 持仓加仓阶梯挂在预算 dict 上: JSON `budgets` 与持仓卡同一份, 清单层照抄不另算。
    for x in items:
        _b = budgets.get(x["ticker"])
        if _b:
            _b["ladder"] = position_ladder(x["ticker"], current_zones.get(x["ticker"]), _b,
                                           _pos_cards.get(x["ticker"]))
    items = [enrich_with_zone_context(x, current_zones, all_items=items,
                                      budget=budgets.get(x["ticker"]))
             for x in items]
    quoted = [x for x in items if x.get("current_price") is not None]
    watch = quoted[:]
    state = load_state()
    now_ts = int(time.time())
    active_keys: set[str] = set()

    def emit_gate(ticker: str, trigger: str) -> bool:
        return should_emit(state, ticker, trigger, now_ts) if mutate_state else True

    movers = sorted(
        [x for x in quoted if x.get("change_pct") is not None and not x.get("synthetic")],
        key=lambda x: abs(x["change_pct"]),
        reverse=True,
)[:TOP_MOVERS_COUNT]

    alerts: List[str] = []
    triggers: List[Dict] = []
    all_conditions: List[Dict] = []
    zone_signals: List[str] = []

    for x in watch:
        ticker = x["ticker"]
        pct = x.get("change_pct")
        price = x.get("current_price")
        src = x.get("price_source") or "stooq"
        is_synthetic = bool(x.get("synthetic"))

        adverse_move = False
        thr: Optional[float] = None
        thr_basis = ""
        if x.get("is_option"):
            # 期权链没有 kline 缓存 → 没有 ATR, 保持固定档 (long 亏 20% / short 涨 20%)
            thr, thr_basis = OPTION_MOVE_PCT, f"{OPTION_MOVE_PCT:g}% 期权固定档"
            if x.get("exposure") == "long" and pct is not None and pct <= -OPTION_MOVE_PCT:
                adverse_move = True
            if x.get("exposure") == "short" and pct is not None and pct >= OPTION_MOVE_PCT:
                adverse_move = True
        elif not is_synthetic:
            thr, thr_basis = move_threshold(ticker)
            if pct is not None and abs(pct) > thr:
                adverse_move = True

        if adverse_move:
            trigger_type = move_trigger_type(pct)
            active_keys.add(alert_key(ticker, trigger_type))
            all_conditions.append({"ticker": ticker, "type": trigger_type, "value": pct})
            if emit_gate(ticker, trigger_type):
                # 阈值随告警一起播报: 换成标的自身的尺之后, 不写出来就没人能判断
                # "为什么 -3.4% 响了而昨天 -4.1% 没响"。
                alerts.append(f"{ticker} {x.get('label', 'position')} {fmt_pct(pct)} @ "
                              f"{fmt_price(price)} ({src}) [阈值 {thr:.1f}% = {thr_basis}]")
                triggers.append({"ticker": ticker, "type": trigger_type, "value": pct,
                                 "threshold_pct": round(thr, 2), "threshold_basis": thr_basis})

        breach = stop_breach(ticker, x, current_zones.get(ticker))
        if breach:
            trigger_type = "stop_breach"
            active_keys.add(alert_key(ticker, trigger_type))
            all_conditions.append({"ticker": ticker, "type": trigger_type,
                                   "value": breach["close"]})
            ref_note = "" if breach["ref"] == ticker else f" [锚 {breach['ref']}]"
            span = f" 连续 {breach['days']} 日" if breach["days"] > 1 else ""
            detail = (f"{ticker} 🚨 STOP 破位{ref_note}: {breach['date']} 收 "
                      f"{fmt_price(breach['close'])} < stop {fmt_price(breach['stop'])}{span}")
            zone_signals.append(f"{detail} → 卡片失效线, 当日清仓 (含盘后), 无 override 路径")
            if emit_gate(ticker, trigger_type):
                tail = f" | invalid_if: {breach['invalid_if']}" if breach["invalid_if"] else ""
                alerts.append(detail + tail)
                triggers.append({"ticker": ticker, "type": trigger_type,
                                 "value": breach["close"], "stop": breach["stop"]})

        if x.get("zone_hints"):
            zone_signals.append(f"{ticker}: " + " | ".join(x["zone_hints"]))

    # 冲击熔断 (§2.7 定版): 与 pending_repaint 同理**绕过 24h 冷却**——
    # 这是动作指令不是提醒, 火警不允许被边沿去重吃掉; 条件持续 = 每轮都响。
    br = shock_breaker_signal(watch, nav)
    if br:
        detail = (f"🚨🚨 冲击熔断: 受管书单日 {br['day_pnl_usd']:+,.0f} = "
                  f"{br['pct_nav']:+.1f}% NAV ≤ −1.5×冲击预算 "
                  f"(−{sizing.SHOCK_BREAKER_MULT * sizing.SHOCK_BUDGET * 100:.1f}% NAV) → "
                  "裁决动作 (用户): **直接清仓, 盘中立即执行**— "
                  "不等收盘不等归因; 复盘走 post-trade scoring, 想回来走重进协议")
        alerts.append(detail)
        zone_signals.append(detail)
        triggers.append({"ticker": "PORTFOLIO", "type": "shock_breaker",
                         "value": br["pct_nav"]})

    # 财报缩仓 T-1 减仓检查 (ledger A2): 入场半边住 available_cap(event_shrink),
    # 这半边管"已有的敞口明天超缩仓 cap"。裁决 =「反应日前一交易日收盘前减完」,
    # 所以在 T-1 当天点名要减多少, 不等 T+0 (AMC print 在 T-1 盘后, T+0 已太晚)。
    for x in watch:
        et_sig = earnings_trim_signal(x, calendar, et_today(), nav=nav)
        if not et_sig:
            continue
        tk = et_sig["ticker"]
        active_keys.add(alert_key(tk, "earnings_trim"))
        all_conditions.append({"ticker": tk, "type": "earnings_trim",
                               "value": et_sig["excess_usd"]})
        unit_cn = "张" if et_sig["is_option"] else "股"
        detail = (f"{tk} 📉 财报缩仓 T-1: 下一交易日 {et_sig['reaction_day']} 反应日, "
                  f"敞口 ${et_sig['exposure_usd']:,.0f} > 缩仓后 cap "
                  f"${et_sig['cap_usd']:,.0f} (×{et_sig['shrink']:.2f}, 基数 "
                  f"{et_sig['cap_basis']}) → 收盘前减 ${et_sig['excess_usd']:,.0f} "
                  f"≈ {et_sig['trim_units']} {unit_cn} "
                  "[earnings_window 路 A 减仓半边; 不减 = 名义仓押一次财报]")
        zone_signals.append(detail)
        if emit_gate(tk, "earnings_trim"):
            alerts.append(detail)
            triggers.append({"ticker": tk, "type": "earnings_trim",
                             "value": et_sig["excess_usd"],
                             "reaction_day": et_sig["reaction_day"],
                             "cap_usd": et_sig["cap_usd"]})

    # 大盘补充: 循环外, 因为 QQQ/SPY 不是持仓 —— 在循环里就是  之前
    # 那条永不触发的死代码。
    for idx in index_risk():
        trigger_type = "index_below_minus_2"
        active_keys.add(alert_key(idx["ticker"], trigger_type))
        all_conditions.append({"ticker": idx["ticker"], "type": trigger_type,
                               "value": idx["pct"]})
        if emit_gate(idx["ticker"], trigger_type):
            alerts.append(f"{idx['ticker']} market risk: {fmt_pct(idx['pct'])} @ "
                          f"{fmt_price(idx['close'])} (日线 {idx['date']})")
            triggers.append({"ticker": idx["ticker"], "type": trigger_type,
                             "value": round(idx["pct"], 2), "date": idx["date"]})

    # watchlist z1 触碰 (H1 补盲): 退出侧有五类告警, 入场侧此前**零**——
    # 两次 briefing 之间候选票砸进吸纳带, 唯一即时性是 moomoo ≤7 字符响铃。报价
    # 快照由 stock_fetch 顺手拉 (heartbeat 两模式都跑 → ≤6h 粒度); 只给 regime
    # cap>0 的卡 (与 z1 画线  补洞同判据 — 无授权卡响铃只会邀请未授权
    # 入场)。24h 边沿冷却与持仓告警共用同一份状态。
    for wb in watchlist_basic:
        # 候选判据 = holdings.is_candidate: 移出自选分组只翻 moomoo_listed 不改 status, 只看
        # status 会给墓碑卡响铃 (移出分组的墓碑卡曾在周复盘里冒出
        # 「破 z2 下沿 → 当日 BUY 冻结」—— 同概念多份判别式的老病, 本文件是漏网的那一处)
        if not wb.get("is_candidate") or is_reference_role(wb.get("role")):
            continue
        cap = normalize_num(wb.get("cap_pct_nav"))
        px = normalize_num(wb.get("price"))
        if not cap or px is None:
            continue
        wt = str(wb["ticker"]).upper()
        zcfg = watchlist_zones.get(wt) or {}
        z1 = normalize_zone_pair(zcfg.get("accumulation_zone_1"))
        z2 = normalize_zone_pair(zcfg.get("accumulation_zone_2"))
        if not z1:
            continue
        lo = z2[0] if z2 else z1[0]
        if not (lo <= px <= z1[1]):
            continue
        # 机械 stale 卡不发触碰告警 ( 用户, MU 案: entry_guard 判 STALE
        # 拒绝给计划, 本告警却带 cap $7,460 邀请按旧图入场)。触碰的语义 = "回踩进
        # 参与带", 图作废后带已不是参与带; today_group/z1 备注同轮接同判别式。
        # 防守向的 z2_breach_buy_freeze **有意不 skip** — stale 卡更该冻结 BUY。
        if zone_staleness(wt, zcfg):
            continue
        trigger_type = "wl_zone_touch"
        active_keys.add(alert_key(wt, trigger_type))
        all_conditions.append({"ticker": wt, "type": trigger_type, "value": px})
        if emit_gate(wt, trigger_type):
            stop = normalize_num(zcfg.get("stop_level"))
            # : 旧文案印卡片名义 `cap_pct_nav` (AMZN 会印 "40% NAV",
            # 而当天实际能下的是 n_unit 咬出来的 38.6%) —— 告警上的钱必须是
            # **算出来的那一个数**, 与 entry_guard / moomoo 备注同源 (用户 Q2)。
            ac = actual_cap_of(wt, px)
            money = (f" | 实际 cap ${ac['cap_usd']:,.0f} ({ac['cap_frac'] * 100:.1f}% NAV, "
                     f"gate{ac['gate_no']}, {ac['binding_label']} binding)"
                     if ac else f" | regime cap {cap * 100:g}% NAV (实际额度算不出 — NAV/K线缺)")
            alerts.append(
                f"📥 {wt} 触及吸纳带: {fmt_price(px)} ({fmt_pct(normalize_num(wb.get('change_pct')))})"
                f" z1 {z1[0]:g}-{z1[1]:g}"
                + (f" | z2 {z2[0]:g}-{z2[1]:g}" if z2 else "")
                + (f" | stop {stop:g}" if stop is not None else "")
                + money
                + " — 限价单按今晨 entry 计划, 触碰判读走 zone-touch 协议")
            triggers.append({"ticker": wt, "type": trigger_type, "value": px,
                             "z1": list(z1), "stop": stop,
                             "cap_usd": (ac or {}).get("cap_usd"),
                             "cap_binding": (ac or {}).get("binding")})

    # Below the lower accumulation boundary, an old-zone buy can execute immediately
    # in broken structure. Freeze BUY actions for the session without mutating the
    # cap; sell/reduce actions remain available and completed-close logic owns redraws.
    freeze_type = "z2_breach_buy_freeze"
    breach_rows: List[Tuple[str, Optional[float], Optional[float], Dict]] = [
        (x["ticker"], x.get("current_price"), x.get("change_pct"),
         current_zones.get(x["ticker"]) or {})
        for x in watch if not x.get("is_option")]
    held_stock = {r[0] for r in breach_rows}  # 双桶同 ticker (促升窗): 持仓卡为真源
    for wb in watchlist_basic:
        wt = str(wb["ticker"]).upper()
        if wt in held_stock or not wb.get("is_candidate") \
                or is_reference_role(wb.get("role")):
            continue
        breach_rows.append((wt, normalize_num(wb.get("price")),
                            normalize_num(wb.get("change_pct")),
                            watchlist_zones.get(wt) or {}))
    for tk, px, chg, zcfg in breach_rows:
        cap = normalize_num((zcfg.get("current_regime") or {}).get("cap_pct_nav"))
        z1 = normalize_zone_pair(zcfg.get("accumulation_zone_1"))
        z2 = normalize_zone_pair(zcfg.get("accumulation_zone_2"))
        if not cap or px is None or not z1:
            continue
        lo = min(z1[0], z2[0]) if z2 else z1[0]
        if px >= lo:
            continue
        active_keys.add(alert_key(tk, freeze_type))
        all_conditions.append({"ticker": tk, "type": freeze_type, "value": px})
        if emit_gate(tk, freeze_type):
            stop = normalize_num(zcfg.get("stop_level"))
            day = f", 当日 {fmt_pct(chg)}" if chg is not None else ""
            alerts.append(
                f"🚫 {tk} 破 z2 下沿: {fmt_price(px)} < {lo:g} "
                f"({(px / lo - 1) * 100:+.1f}%{day})"
                + (f" | stop {stop:g}" if stop is not None else "")
                + " → 当日 BUY 冻结: 带内清单价挂上去 = 立即成交在破位区; "
                  "等收盘裁定 (收回带内 → 明日照新清单 / 确认破位 → 重画换图); "
                  "卖出/减仓不受影响, cap 不在盘中动 — 授权改动归收盘机器")
            triggers.append({"ticker": tk, "type": freeze_type, "value": px,
                             "z2_lo": lo, "stop": stop})

    # 候选卡 staleness 清单 (): 周复盘此前自己扫 watchlist.json 的 valid_until 列
    # 「候选卡过期」, 把已移出自选的墓碑卡一起列了 —— 范围判据只在这里出口一次,
    # 消费端抄输出。只报不拦 (触碰告警已按同一 zone_staleness 跳过 stale 卡; 冻结有意不跳)。
    watchlist_stale: List[Dict] = []
    for wb in watchlist_basic:
        if not wb.get("is_candidate") or is_reference_role(wb.get("role")):
            continue
        wt = str(wb["ticker"]).upper()
        if wt in held_stock:                      # 促升窗双桶同票: 持仓卡为真源
            continue
        zcfg = watchlist_zones.get(wt) or {}
        if not zcfg.get("accumulation_zone_1"):   # 空 stub 归 cardless (autopaint), 不是 stale
            continue
        reasons = zone_staleness(wt, zcfg)
        if reasons:
            watchlist_stale.append({"ticker": wt, "reasons": reasons})

    # Zone staleness (代码化触发: valid_until / breakout / breakdown via kline 缓存)
    stale_zones: List[Dict] = []
    for zticker, cfg in current_zones.items():
        if not isinstance(cfg, dict):
            continue
        reasons = zone_staleness(zticker, cfg)
        if reasons:
            stale_zones.append({"ticker": zticker, "reasons": reasons})
            zone_signals.append(f"{zticker}: ⚠ ZONE STALE — {'; '.join(reasons)} → 先刷新再做交易判断")
            active_keys.add(alert_key(zticker, "zone_stale"))
            if emit_gate(zticker, "zone_stale"):
                alerts.append(f"{zticker} zone stale: {'; '.join(reasons)}")
                triggers.append({"ticker": zticker, "type": "zone_stale", "value": reasons})

    # regime 转移后未补画 —— 这是**待办**不是提醒, 所以绕过 emit_gate 的 24h
    # 冷却: 待办被冷却吃掉就等于漏。转移是机械的, 补画是人做的, 中间这段窗口
    # 必须一直可见, 直到有人把 provisional zone 落实。
    for zticker, cfg in current_zones.items():
        pr = (cfg or {}).get("pending_repaint") if isinstance(cfg, dict) else None
        if not pr:
            continue
        slots = "/".join(pr.get("slots") or []) or "?"
        detail = (f"{zticker} ⏳ regime 转移待补画 [{slots}] since {pr.get('since')} — "
                  f"补画前 entry_guard = NEEDS_CARD, 不授权入场")
        zone_signals.append(detail)
        alerts.append(detail)
        triggers.append({"ticker": zticker, "type": "pending_repaint",
                         "value": pr.get("slots")})

    # IV percentile 极值信号 (样本 <60 自动沉默)
    iv_notes: List[str] = []
    for u in sorted({x.get("underlying") or x["ticker"] for x in watch}):
        note = iv_signal(u)
        if note:
            iv_notes.append(f"{u}: {note}")
            zone_signals.append(f"{u}: {note}")

    # Exit guards (trend-exit-system.md): 利润棘轮 / 趋势死亡线 / G-01 档位 / margin。
    # one_shot 由 exit_guard 的 fired flag 自去重; persistent 走本文件边沿触发机制。
    # guard 层故障不允许拖垮 brief 主体 (监控失效本身要可见)。
    guard_lines: List[str] = []
    guard_account: Optional[Dict] = None
    guard_states: Dict[str, Dict] = {}
    try:
        from quant.decision import exit_guard
        gout = exit_guard.run_checks(mutate_state=mutate_state, live_account=mutate_state)
        guard_lines = gout.get("summary_lines") or []
        guard_account = gout.get("account")
        guard_states = gout.get("guards") or {}
        for cond in gout.get("one_shot", []):
            alerts.append(cond["text"])
            triggers.append({"ticker": cond["ticker"], "type": cond["type"],
                             "value": cond.get("value")})
        for cond in gout.get("persistent", []):
            active_keys.add(alert_key(cond["ticker"], cond["type"]))
            all_conditions.append({"ticker": cond["ticker"], "type": cond["type"],
                                   "value": cond.get("value")})
            if emit_gate(cond["ticker"], cond["type"]):
                alerts.append(cond["text"])
                triggers.append({"ticker": cond["ticker"], "type": cond["type"],
                                 "value": cond.get("value")})
    except Exception as e:
        guard_lines = [f"⚠ exit_guard 异常 (监控降级, 需人工看): {e}"]
        alerts.append(f"INFRA exit_guard error: {e}")

    # 单笔 at-risk 天花板持仓侧核查 (N1 立项 , ≤5% NAV) —— 要在
    # exit_guard 之后跑: binding stop 的 trail 半来自 guard_states。
    for x in watch:
        ar = at_risk_breach_signal(x, current_zones.get(x["ticker"]),
                                   guard_states.get(x["ticker"]), nav)
        if not ar:
            continue
        tk = ar["ticker"]
        active_keys.add(alert_key(tk, "at_risk_breach"))
        all_conditions.append({"ticker": tk, "type": "at_risk_breach",
                               "value": ar["at_risk_usd"]})
        unit_cn = "张" if ar["kind"] == "option_premium" else "股"
        stop_txt = (f"binding stop {ar['stop']:g} ({ar['stop_basis']})"
                    if ar["stop"] is not None else ar["stop_basis"])
        detail = (f"{tk} ⚠️ 单笔 at-risk 超顶: 止损打掉亏 ${ar['at_risk_usd']:,.0f} "
                  f"= {ar['pct_nav']:.1f}% NAV > cap ${ar['cap_usd']:,.0f} "
                  f"({sizing.AT_RISK_CAP_FRAC * 100:g}% NAV, N1) "
                  f"[{stop_txt}] → 减 {ar['trim_units']} {unit_cn}至合规, 或书面 override 进 journal")
        zone_signals.append(detail)
        if emit_gate(tk, "at_risk_breach"):
            alerts.append(detail)
            triggers.append({"ticker": tk, "type": "at_risk_breach",
                             "value": ar["at_risk_usd"], "cap_usd": ar["cap_usd"]})

    # A stop-triggered demotion replaces the old stop on the card. Continue to
    # surface the unresolved exit decision until a sell, recovery transition or
    # explicit scored override provides a durable resolution receipt.
    for overdue in structure.unresolved_demotions(_load_state_doc(STATE_POSITIONS_PATH)):
        tk = overdue["ticker"]
        days_open = structure.trading_days_after(
            (day for day, _ in _cached_closes(tk, 30)), overdue["date"])
        active_keys.add(alert_key(tk, "exit_overdue"))
        all_conditions.append({"ticker": tk, "type": "exit_overdue", "value": overdue["date"]})
        old_s = f"{overdue['old_stop']:g}" if overdue.get("old_stop") is not None else "?"
        new_s = f"{overdue['new_stop']:g}" if overdue.get("new_stop") is not None else "?"
        expo = (f"仍持 {overdue['qty']:g} 股" if overdue["qty"] > 0 else "正股已平") \
            + (f" + 期权 {', '.join(overdue['opt_tickers'])}"
               if overdue.get("opt_tickers") else "")
        detail = (f"{tk} 🚨 破 stop 降级 D+{days_open} 未执行: {overdue['date']} "
                  f"{overdue.get('reason') or f'收盘破 stop {old_s}'} → 已降级 cap 0, "
                  f"卡上 stop 已换 {old_s}→{new_s}, {expo}。裁决在案 (trend-exit-system): "
                  f"清仓, 无 override 路径; 未执行 = 规则外评 C 起步, "
                  f"annotate_event --score 落 scores.jsonl 即消音; 卖出/收复转移亦自动解除")
        zone_signals.append(detail)
        if emit_gate(tk, "exit_overdue"):
            alerts.append(detail)
            triggers.append({"ticker": tk, "type": "exit_overdue",
                             "value": overdue["date"],
                             "old_stop": overdue.get("old_stop"),
                             "new_stop": overdue.get("new_stop"),
                             "days_open": days_open})

    # 预算超顶 (补): `cap_multiple` 早就算出来了, 但只是卡上一个
    # 括号里的数字 —— 没有任何告警通道。整股约束下 1.0-1.25× 是正常溢出 (买不起
    # 半股), 超过就是真出格, 该有人看见。
    for tk, b in budgets.items():
        cm = normalize_num((b or {}).get("cap_multiple"))
        if cm is None or cm <= CAP_MULTIPLE_ALERT:
            continue
        active_keys.add(alert_key(tk, "cap_multiple"))
        all_conditions.append({"ticker": tk, "type": "cap_multiple", "value": cm})
        detail = (f"{tk} ⚠️ 预算超顶 {cm:g}×: 已投 ${b['deployed_usd']:,.0f} > "
                  f"cap ${b['cap_usd']:,.0f} (regime cap {b['regime_cap_frac'] * 100:g}%) — 整股溢出的上限是 "
                  f"{CAP_MULTIPLE_ALERT:g}×, 超过属规则外成交, 补注解或书面 override")
        zone_signals.append(detail)
        if emit_gate(tk, "cap_multiple"):
            alerts.append(detail)
            triggers.append({"ticker": tk, "type": "cap_multiple", "value": cm})

    # lots 台账与 broker 对不上 (买入没落账 / 平仓没清理) → 会响, 不再只 print
    # 到没人看的 stderr。两条自动化 (lot_reconcile / close_reconcile) 的安全网。
    drift_rows = lot_drift()
    if drift_rows:
        active_keys.add(alert_key("INFRA", "lot_drift"))
        all_conditions.append({"ticker": "INFRA", "type": "lot_drift",
                               "value": len(drift_rows)})
        if emit_gate("INFRA", "lot_drift"):
            alerts.append("⚠️ lots 台账与 broker 对不上: " + "; ".join(drift_rows)
                          + " → 自动化 (pipeline 的 lots / reconcile 步) 没接住, "
                            "查它为什么没动手; 部分卖出要人工决定哪个 lot 被卖")
            triggers.append({"ticker": "INFRA", "type": "lot_drift",
                             "value": len(drift_rows)})

    # 成交事件流的净持仓 != broker.qty —— `lot_drift` 结构上看不见的那半
    # (NBIS : lots 与 broker 都是 1、差为 0, 两天零告警)。
    desync_rows = qty_desync()
    if desync_rows:
        active_keys.add(alert_key("INFRA", "qty_desync"))
        all_conditions.append({"ticker": "INFRA", "type": "qty_desync",
                               "value": len(desync_rows)})
        if emit_gate("INFRA", "qty_desync"):
            alerts.append("🚨 账上持仓与成交记录对不上: " + "; ".join(desync_rows)
                          + " → 事件流少一笔, 或券商快照没跟上 (卡从券商列表消失时"
                            "旧值会一直留着 —— state_io 的 zero_absent 管这个)。"
                            "**在对齐之前, 这张卡的授权/敞口/退出线全部按错数字在跑**")
            triggers.append({"ticker": "INFRA", "type": "qty_desync",
                             "value": len(desync_rows)})

    # 期权的退出线锚在**正股**结构上 (stop_level_basis: underlying), 正股卡没 zones
    # = 那条线没有任何东西承载, staleness / 重画 SLA 一并失效。
    # 必须直扫 positions doc: 涉事卡片 qty==0 (期权的 synthetic 行), 进不了 rows ——
    #  GOOGL 空了 6 天简报一声没响, 就是因为它根本进不了数据链。
    pos_doc_infra = _load_state_doc(STATE_POSITIONS_PATH)
    missing_und = underlying_cards_missing_zones(pos_doc_infra)
    if missing_und:
        active_keys.add(alert_key("INFRA", "underlying_card_missing"))
        all_conditions.append({"ticker": "INFRA", "type": "underlying_card_missing",
                               "value": len(missing_und)})
        if emit_gate("INFRA", "underlying_card_missing"):
            alerts.append("⚠️ 在持期权的 underlying 正股卡缺 zones: "
                          + ", ".join(missing_und)
                          + " → 期权 stop 无结构承载、staleness 失效。跑 "
                            "/trading-analysis 建卡; 若卡曾经存在, 去 "
                            "agent.prior_campaigns 查是不是被 watchlist stub 覆盖")
            triggers.append({"ticker": "INFRA", "type": "underlying_card_missing",
                             "value": len(missing_und)})

    # 卡缺上行状态转移轴 = 这张卡只能活一轮 (用户 : gate = 状态转移轴,
    # 上升/下降/修复三种趋势都有, 不是破位卡专属; 当前 regime 的参与权与 next_regime
    # 的存在是两件独立的事, 画卡时都要给)。机器只在转移发生时写 next_regime,
    # 建卡不写就没人补 —— 与 current_regime 同生同死那条一样, 属于缺字段静默。
    no_next_gate = structure.cards_missing_next_gate(
        pos_doc_infra, _load_state_doc(STATE_WATCHLIST_PATH))
    if no_next_gate:
        active_keys.add(alert_key("INFRA", "card_missing_next_gate"))
        all_conditions.append({"ticker": "INFRA", "type": "card_missing_next_gate",
                               "value": len(no_next_gate)})
        if emit_gate("INFRA", "card_missing_next_gate"):
            alerts.append("⚠️ 卡缺上行状态转移轴 (next_regime.entry_gate): "
                          + "; ".join(no_next_gate)
                          + " → 重画时用 update_position_zones --next-regime 补; "
                            "价位要带 level_map 证据, 站不住的带标 provisional "
                            "(范本见 AMZN R_突破_279)")
            triggers.append({"ticker": "INFRA", "type": "card_missing_next_gate",
                             "value": len(no_next_gate)})

    # 画了卡没归估值原型: 新候选卡画完
    # 一周, 估值层人工区没有它们, 周复盘估值一览只能印 ✗。判断层零授权 —— 只报不拦, 与
    # card_missing_next_gate 同款出口; 修法一条命令 (archetypes.py set), 不是让人手填 JSON。
    try:
        _inputs = read_json(VALUATION_INPUTS_PATH) if VALUATION_INPUTS_PATH.exists() else {}
    except Exception:                                              # noqa: BLE001  坏文件不拖垮 brief
        _inputs = {}
    no_arch = cards_missing_archetype(
        pos_doc_infra, _load_state_doc(STATE_WATCHLIST_PATH), _inputs or {})
    if no_arch:
        active_keys.add(alert_key("INFRA", "card_missing_archetype"))
        all_conditions.append({"ticker": "INFRA", "type": "card_missing_archetype",
                               "value": len(no_arch)})
        if emit_gate("INFRA", "card_missing_archetype"):
            alerts.append("⚠️ 卡已画但估值层未归原型 (inputs.json 无条目, 画卡一并做): "
                          + ", ".join(no_arch)
                          + " → `python3 quant/valuation/archetypes.py set <T> --archetype "
                            "<platform|semi_cyclical|ai_infra_capex|software_growth|not_applicable> "
                            "--rationale \"<商业模式一句话>\"` (判断层零授权, 只影响估值一览覆盖)")
            triggers.append({"ticker": "INFRA", "type": "card_missing_archetype",
                             "value": len(no_arch)})

    # 转移装上来的 zones 没有锚 → anchor_date() 用 thesis.last_reviewed 凭空补新鲜度,
    # 财报重画 SLA 与同锚层一并消音 (用户 , NBIS 快车道前夜)。
    # 出口是告警不是补锚 —— 自动盖锚 = 让转移前推的价位块自称锚在转移日。
    no_zone_anchor = structure.cards_missing_zone_anchor(
        pos_doc_infra, _load_state_doc(STATE_WATCHLIST_PATH))
    if no_zone_anchor:
        active_keys.add(alert_key("INFRA", "zones_missing_anchor"))
        all_conditions.append({"ticker": "INFRA", "type": "zones_missing_anchor",
                               "value": len(no_zone_anchor)})
        if emit_gate("INFRA", "zones_missing_anchor"):
            alerts.append("⚠️ 转移装上来的 zones 缺 anchored_on (锚被 fallback 凭空补新): "
                          + "; ".join(no_zone_anchor)
                          + " → 这套价位是**转移前**推的, 财报重画 SLA 与 gate 同锚检查"
                            "已因此消音; 按转移日收盘那根 bar 重画并显式 "
                            "--anchored-on, **不要**只补锚不复核")
            triggers.append({"ticker": "INFRA", "type": "zones_missing_anchor",
                             "value": len(no_zone_anchor)})

    # gate 锚 ≠ zone 锚 = 只重画了一半 (zone-maintenance §Gate 与 zone 必须同步)。
    # entry_guard 的 STALE 只覆盖 **watchlist**—— 持仓卡的同锚破裂此前零出口,
    # 而持仓卡的 gate 正是加仓授权的来源 (促升卡 cap 0, 额度由 gate 转移授予)。
    # 源案例 : 884411d 一笔造出两个方向相反的破裂 (MU zone 8/06 而
    # gate 8/11 / MRVL zone 8/11 而 gate 8/06), MU 在 watchlist 天天喊 STALE,
    # 持仓的 MRVL 一声不响。判别式复用 structure.gate_anchor_desync, 不写第二份。
    desync = structure.cards_with_gate_anchor_desync(
        pos_doc_infra, _load_state_doc(STATE_WATCHLIST_PATH))
    if desync:
        active_keys.add(alert_key("INFRA", "gate_anchor_desync"))
        all_conditions.append({"ticker": "INFRA", "type": "gate_anchor_desync",
                               "value": len(desync)})
        if emit_gate("INFRA", "gate_anchor_desync"):
            alerts.append("⚠️ gate 与 zone 锚不同步 (只重画了一半): "
                          + "; ".join(desync)
                          + " → 复核该 gate 后连 zone 一起重画 (同一次); "
                            "刷新收据只改 basis 文本不动日期 —— 复核 ≠ 重锚")
            triggers.append({"ticker": "INFRA", "type": "gate_anchor_desync",
                             "value": len(desync)})

    # 财报 print 已落地、卡锚还停在 print 之前 = 这张图不含反应日的信息。
    # 重画 SLA 此前只活在卡片 `update_trigger` 的 prose 里, 没有任何读者 ——
    # 日历知道 print 哪天 (它天天在算缩仓), 卡知道自己哪天画的, 中间没有代码。
    # 源案例  用户 抓: LITE (AMC) 锚停  四个交易日零告警。
    # ⚠️ 只报不拦: v3 财报是情报层, 不冻结参与 —— 见 earnings.cards_stale_after_print。
    stale_print = earnings.cards_stale_after_print(
        pos_doc_infra, _load_state_doc(STATE_WATCHLIST_PATH),
        today=et_today(), calendar=calendar)
    if stale_print:
        active_keys.add(alert_key("INFRA", "card_stale_earnings"))
        all_conditions.append({"ticker": "INFRA", "type": "card_stale_earnings",
                               "value": len(stale_print)})
        if emit_gate("INFRA", "card_stale_earnings"):
            alerts.append("⚠️ 财报已落地但卡未重画 (v3 重画 SLA): "
                          + "; ".join(stale_print)
                          + " → 用**反应日收盘**那根 bar 全重画 (盘前照 print 画 = "
                            "未完成 bar 当收盘 + 提前画白画率 40%); **全重画含 thesis "
                            "情报腿** (print 事实读 ai_news 归档 [FYI] — zones-only "
                            "只消音本判据, thesis 仍停在 print 前, MRVL 9/01 案); "
                            "授权不受影响, 财报只压 T+0 size 不压参与")
            triggers.append({"ticker": "INFRA", "type": "card_stale_earnings",
                             "value": len(stale_print)})

    # 促升守卫拦下了「空 stub 覆盖真卡」。拦下是对的, 但候选卡该补 —— 不补的话
    # 它每轮都拦, 而 watchlist 里躺着一张永远升不上去的空卡。
    try:
        from quant.sync.watchlist_migrate import blocked_promotions  # noqa: PLC0415
        blocked_pro = blocked_promotions(
            pos_doc_infra, _load_state_doc(STATE_WATCHLIST_PATH))
    except Exception:                                              # noqa: BLE001
        blocked_pro = []
    if blocked_pro:
        active_keys.add(alert_key("INFRA", "promote_blocked"))
        all_conditions.append({"ticker": "INFRA", "type": "promote_blocked",
                               "value": len(blocked_pro)})
        if emit_gate("INFRA", "promote_blocked"):
            alerts.append("⚠️ 促升被守卫拦下 (watchlist 空卡会覆盖 positions 的图): "
                          + ", ".join(blocked_pro)
                          + " → 跑 /trading-analysis 补全候选卡, 或从 moomoo "
                            "自选分组移除该 ticker")
            triggers.append({"ticker": "INFRA", "type": "promote_blocked",
                             "value": len(blocked_pro)})

    # 成交落库了但分类推不出来 → 那是"无授权成交"的形状, 归人判, 别让它变成空字段。
    untagged = untagged_fills()
    if untagged:
        active_keys.add(alert_key("INFRA", "fill_untagged"))
        all_conditions.append({"ticker": "INFRA", "type": "fill_untagged",
                               "value": len(untagged)})
        if emit_gate("INFRA", "fill_untagged"):
            alerts.append("⚠️ 成交无授权标签 (regime cap 0 / 无 regime = 无授权形状): "
                          + "; ".join(untagged)
                          + " → 判定后 annotate_event 补注 + 落评分 (规则外成交清单)")
            triggers.append({"ticker": "INFRA", "type": "fill_untagged",
                             "value": len(untagged)})

    breached = price_breach_fills()
    if breached:
        active_keys.add(alert_key("INFRA", "fill_price_breach"))
        all_conditions.append({
            "ticker": "INFRA", "type": "fill_price_breach", "value": len(breached),
        })
        if emit_gate("INFRA", "fill_price_breach"):
            alerts.append(
                "⚠️ 成交价位违规 (>no_chase 追价 / trim 区加仓, 带授权标签也点名): "
                + "; ".join(breached)
                + " → 规则外成交清单, annotate_event 补注 + 落评分"
            )
            triggers.append({
                "ticker": "INFRA", "type": "fill_price_breach", "value": len(breached),
            })

    # earnings 日历陈旧 (BACKLOG §3.5-4): 重建只挂 briefing 模式, briefing 连续
    # 失败时 heartbeat/postclose 会**静默用旧日历**—— earnings.py 自陈"静默不缩仓
    # 是最可能的失效形态", 而它自己的兜底阈值 7 天正是入口。这里 >2 个工作日就响
    # (工作日口径: 周五建→周一读 = 1 个工作日, 周末不误报)。
    cal_note = calendar_stale_note(calendar, et_today())
    if cal_note:
        active_keys.add(alert_key("INFRA", "earnings_calendar_stale"))
        all_conditions.append({"ticker": "INFRA", "type": "earnings_calendar_stale",
                               "value": cal_note})
        if emit_gate("INFRA", "earnings_calendar_stale"):
            alerts.append(cal_note)
            triggers.append({"ticker": "INFRA", "type": "earnings_calendar_stale",
                             "value": cal_note})

    # 状态卡: 每个持仓一张, 代码渲染。fired_cards = 本轮真触发的那些, heartbeat
    # 照原文推送 (改写削弱 = 又回到叙述模式)。头衔表 HEADLINES 在模块层。
    fired: Dict[str, str] = {}
    for t in triggers:
        head = HEADLINES.get(t.get("type"))
        if head and t["ticker"] not in fired:
            fired[t["ticker"]] = head
    position_cards: Dict[str, str] = {}
    spot_by_ticker = {x["ticker"]: x.get("current_price") for x in items}
    for x in watch:
        if x.get("synthetic") or not x.get("qty"):
            continue
        tk = x["ticker"]
        und = str(x.get("underlying") or tk).upper()
        position_cards[tk] = build_position_card(
            x, current_zones.get(tk), budgets.get(tk), guard_states.get(tk),
            headline=fired.get(tk, ""),
            und_spot=spot_by_ticker.get(und) if x.get("is_option") else None,
            und_cfg=current_zones.get(und) if x.get("is_option") else None)
    fired_cards = [position_cards[t] for t in fired if t in position_cards]

    if mutate_state:
        clear_inactive(state, active_keys)
        # INDEX_TICKERS 必须进 universe: 它们不是持仓, 漏掉 → prune_dead_keys
        # 每轮把 QQQ:index_below_minus_2 删掉 → latch 永远不持久 → 大盘一直跌
        # 就一直重发, 24h 边沿去重形同虚设。
        universe = {x["ticker"] for x in items} | {x.get("underlying") or "" for x in items} \
                   | set(current_zones) | {str(r.get("ticker", "")).upper() for r in watchlist_basic} \
                   | set(INDEX_TICKERS)
        prune_dead_keys(state, universe)
        state.setdefault("lastChecks", {})["portfolio_brief"] = now_ts  # dead-man 心跳
        save_state(state)

    source = quoted[0].get("price_source") if quoted else ""
    latest_quote_time = max((x.get("quote_time") or "" for x in quoted), default="")
    watchlist_zone_lines = build_watchlist_zone_lines(watchlist_basic, watchlist_zones)

    lines: List[str] = []
    lines.append(f"Portfolio brief ({source or 'unknown'})")
    if latest_quote_time:
        lines.append(f"Quote time: {latest_quote_time}")
    # 组合占用 —— 与 entry_guard 同一段渲染 (sizing.usage_lines)。持仓侧此前完全
    # 没有这行, 于是简报里的预算读数是名义 cap、入场计划里是实际 cap, 两处口径
    # 看起来不一致 (shock_budget_wiring_todo §D)。
    usage_txt = sizing.usage_lines(usage, nav)
    lines.extend(usage_txt)

    if movers:
        lines.append("Top movers:")
        for x in movers:
            lines.append(f"- {x['ticker']} [{x.get('label', 'position')}] {fmt_pct(x.get('change_pct'))} @ {fmt_price(x.get('current_price'))}")

    focus = sorted(
        [x for x in watch if not x.get("synthetic")],
        key=lambda x: (x.get("position_weight") is None, -(x.get("position_weight") or 0)),
)[:8]
    if focus:
        lines.append("Focus names:")
        for x in focus:
            extra = f", UPL {x['unrealized_pl']:.2f}" if x.get("unrealized_pl") is not None else ""
            line = f"- {x['ticker']}: {x.get('label', 'position')} | px {fmt_price(x.get('current_price'))} | day {fmt_pct(x.get('change_pct'))} | pnl {fmt_pct(x.get('pnl_pct'))}{extra}"
            if x.get("zone_hints"):
                line += " | zones: " + " ; ".join(x["zone_hints"])
            lines.append(line)

    if zone_signals:
        lines.append("Zone signals:")
        for z in zone_signals:
            lines.append(f"- {z}")
    else:
        lines.append("Zone signals: none")

    if position_cards:
        lines.append("Position cards:")
        for tk in sorted(position_cards):
            lines.append(position_cards[tk])
            lines.append("")

    if guard_lines:
        lines.append("Exit guards:")
        for g in guard_lines:
            lines.append(f"- {g}")

    if watchlist_zone_lines:
        lines.append("Watchlist plans:")
        for z in watchlist_zone_lines:
            lines.append(f"- {z}")

    if watchlist_stale:
        lines.append("Watchlist stale (候选判据已过滤, 墓碑卡不进; 先重画再用):")
        for st in watchlist_stale:
            lines.append(f"- {st['ticker']}: {'; '.join(st['reasons'])}")

    if alerts:
        lines.append("Alerts:")
        for a in alerts:
            lines.append(f"- {a}")
    else:
        lines.append("Alerts: none")

    return {
        "source": source,
        "valuation_fyi": valuation_fyi_map(pos_doc_infra, _load_state_doc(STATE_WATCHLIST_PATH)),
        "quote_time": latest_quote_time,
        "top_movers": movers,
        "watchlist": watch,
        "zone_signals": zone_signals,
        "stale_zones": stale_zones,
        "iv_notes": iv_notes,
        "book_usage": {**usage, "lines": usage_txt},
        "budgets": budgets,
        "position_cards": position_cards,
        # heartbeat 推这个, 原文不改写 (空 = 无事发生, 不推)
        "fired_cards": fired_cards,
        "exit_guards": guard_lines,
        "margin_account": guard_account,
        "watchlist_zone_lines": watchlist_zone_lines,
        "watchlist_stale": watchlist_stale,
        "alerts": alerts,
        "triggers": triggers,
        "active_conditions": all_conditions,
        "read_only": not mutate_state,
        "text": "\n".join(lines),
    }


def parse_args(argv: List[str]) -> Tuple[bool, bool]:
    args = list(argv)
    want_json = False
    read_only = False
    if "--json" in args:
        args.remove("--json")
        want_json = True
    if "--read-only" in args:
        args.remove("--read-only")
        read_only = True
    if args:
        # Backward-compat: prior CLI accepted a CSV path; ignore it now and warn.
        print(
            f"[note] portfolio_brief now reads state/positions.json; ignoring positional arg {args[0]!r}",
            file=sys.stderr,
)
    return want_json, read_only


def main(argv: List[str]) -> int:
    want_json, read_only = parse_args(argv)
    if not STATE_POSITIONS_PATH.exists():
        raise SystemExit(f"state/positions.json not found: {STATE_POSITIONS_PATH}")
    rows, ticker_col = load_rows_from_state()
    brief = build_brief(rows, ticker_col, mutate_state=not read_only)
    if want_json:
        print(json.dumps(brief, ensure_ascii=False, indent=2))
    else:
        print(brief["text"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
