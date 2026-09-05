#!/usr/bin/env python3
"""Sync portfolio positions from OpenD and refresh quotes.

One command to: pull latest positions → fetch quotes → write CSV.

Usage:
  python3 quant/sync/stock_fetch.py              # sync positions + quotes → CSV
  python3 quant/sync/stock_fetch.py --json       # JSON output instead of CSV
  python3 quant/sync/stock_fetch.py MU SNDK      # only refresh quotes for these tickers
  python3 quant/sync/stock_fetch.py --quotes-only  # skip position sync, just refresh quotes
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 直接 `python3 quant/<层>/<模块>.py` 时让 `quant.*` 可 import

from quant.core.paths import add_openapi_path  # noqa: E402

from quant.core import env  # noqa: F401  (.env → os.environ)

import json
import os
from typing import Dict, List, Set

# Import common.py from skills (connection, RSA, trade context)
add_openapi_path()  # openapi skill scripts (`common`) 上 sys.path
from common import (
    create_trade_context, safe_close,
    get_default_acc_id, get_default_security_firm,
    parse_trd_env, RET_OK,
)

# Import project market data module for quotes
from quant.data.market_data import get_quote, get_snapshot, get_market_state

QUOTE_CLOCK_FIELDS = ('quote_time_raw', 'quote_time_basis', 'quote_retrieved_at')
QUOTE_COLS = ['quote_time', *QUOTE_CLOCK_FIELDS, 'price_source', 'current_price', 'change', 'change_pct',
              'open', 'high', 'low', 'prev_close']
# Option greeks persisted into the broker block (, shock-budget
# framework). `delta` is the load-bearing one — gross exposure is delta
# notional, not contract market value. The rest ride along free of charge
# and close `shock_budget_wiring_todo.md` §C ("落库 Greeks 快照").
GREEK_FIELDS = ('delta', 'gamma', 'theta', 'vega', 'iv_pct')
POS_COLS = ['代码', '名称', '持有数量', '可用数量',
            '现价', '平均成本价', '市值',
            '未实现盈亏比例', '总盈亏金额',
            '未实现盈亏', '已实现盈亏',
            '今日盈亏', '币种', 'synthetic']

# v2: writes go directly to state/positions.json broker blocks.
# CSV reads/writes were retired alongside `current/portfolio_latest.csv`.
from quant.sync import state_io  # noqa: E402  (sibling script)
from quant.core.num import coerce_float as _safe_float  # noqa: E402
from quant.core.occ import OCC_RE as OPTION_RE  # noqa: E402  (group(1) = underlying)
from quant.core.timeutil import now_utc_iso as now_iso  # noqa: E402
from quant.core.quote_times import opend_quote_time


def rows_to_broker_updates(rows: List[dict]) -> Dict[str, dict]:
    """Convert CSV-shaped rows to {ticker: broker_block} for state_io."""
    out: Dict[str, dict] = {}
    for row in rows:
        ticker = (row.get('代码') or '').strip()
        if not ticker:
            continue
        block: Dict[str, object] = {
            'source': 'moomoo_opend',
            'qty': _safe_float(row.get('持有数量')) or 0.0,
            'avg_cost': max(_safe_float(row.get('平均成本价')) or 0.0, 0.0),
            'currency': (row.get('币种') or 'USD').strip(),
            'as_of': opend_quote_time(row.get('quote_time')),
            'synthetic': (row.get('synthetic') or '').strip().upper() == 'Y',
        }
        for src, dst in (
            ('current_price', 'current_price'),
            ('prev_close', 'prev_close'),
            ('change_pct', 'change_pct'),
            # today_change_usd is the BROKER's own P&L, not a price baseline:
            # for a carried position it is current - prior close, but for one
            # opened today it is current - fill price (measured
            # AFTER_HOURS: MU -75.53 = after 733.55 - that day's 809.08 entry,
            # neither the -86.98 full-day nor the -5.45 after-hours move). Handy
            # as a cross-check on change_pct for held names, never as the oracle
            # for which close is "yesterday's".
            ('今日盈亏', 'today_change_usd'),
            ('未实现盈亏', 'unrealized_pnl_usd'),
            ('未实现盈亏比例', 'unrealized_pnl_pct'),
            ('已实现盈亏', 'realized_pnl_usd'),
):
            v = _safe_float(row.get(src))
            if v is not None:
                block[dst] = v
        if row.get('price_source'):
            block['quote_source'] = row['price_source']
        for field in QUOTE_CLOCK_FIELDS:
            if row.get(field) is not None:
                block[field] = row[field]
        # Greeks: only written when the snapshot actually carried them. A stale
        # persisted delta is fine (it ages like current_price does); a silently
        # absent one is not — core.sizing.book_usage falls back to delta=1.0 and
        # SAYS SO (`assumed_full_delta`), which over-states exposure on purpose.
        for g in GREEK_FIELDS:
            v = _safe_float(row.get(g))
            if v is not None:
                block[g] = v
        out[ticker] = block
    return out


# ── Position sync via trade API ──────────────────────────────

REAL_TRD_ENV = parse_trd_env('REAL')


def fetch_positions() -> List[dict]:
    """Pull live positions from OpenD trade API using app-aligned fields.

    方案A: real-account sync flows must not depend on FUTU_TRD_ENV defaults.
    """
    ctx = create_trade_context(security_firm=get_default_security_firm())
    try:
        ret, data = ctx.position_list_query(trd_env=REAL_TRD_ENV, acc_id=get_default_acc_id())
        if ret != RET_OK or data is None or data.empty:
            print(f'[warn] position_list_query failed: {data}', file=sys.stderr)
            return []
        rows = []
        for _, r in data.iterrows():
            raw_code = str(r.get('code', ''))
            code = raw_code.split('.', 1)[1] if '.' in raw_code else raw_code
            plr = r.get('pl_ratio_avg_cost', 0)
            try:
                plr_f = float(plr)
            except Exception:
                plr_f = 0.0
            plr_str = f'+{plr_f}%' if plr_f > 0 else f'{plr_f}%'
            unrealized = r.get('unrealized_pl', '')
            realized = r.get('realized_pl', '')
            try:
                total_pl = float(unrealized) + float(realized)
            except Exception:
                total_pl = r.get('pl_val', '')
            rows.append({
                '代码': code,
                '名称': r.get('stock_name', ''),
                '持有数量': r.get('qty', ''),
                '可用数量': r.get('can_sell_qty', ''),
                '现价': r.get('nominal_price', ''),
                '平均成本价': r.get('average_cost', ''),
                '市值': r.get('market_val', ''),
                '未实现盈亏比例': plr_str,
                '总盈亏金额': total_pl,
                '未实现盈亏': unrealized,
                '已实现盈亏': realized,
                '今日盈亏': r.get('today_pl_val', ''),
                '币种': r.get('currency', 'USD') or 'USD',
            })
        return rows
    finally:
        safe_close(ctx)


# ── Synthetic underlying rows for option-only exposure ───────

def add_synthetic_underlying_rows(rows: List[dict]) -> List[dict]:
    """Append zero-qty rows for option underlyings not already held as stock.

    Lets downstream consumers (portfolio_brief.py, heartbeat) resolve the
    underlying's live price for zone comparison and adverse-move signals when
    the position is expressed only via options. Idempotent — skips underlyings
    already present, including previously-added synthetic rows.
    """
    held = {(r.get('代码') or '').strip().upper() for r in rows if r.get('代码')}
    needed: Set[str] = set()
    for r in rows:
        code = (r.get('代码') or '').strip().upper()
        m = OPTION_RE.match(code)
        if m:
            underlying = m.group(1)
            if underlying not in held:
                needed.add(underlying)
    for u in sorted(needed):
        rows.append({
            '代码': u,
            '名称': f'{u} (option underlying)',
            '持有数量': '0',
            '可用数量': '0',
            '现价': '',
            '平均成本价': '',
            '市值': '0',
            '未实现盈亏比例': '',
            '总盈亏金额': '',
            '未实现盈亏': '',
            '已实现盈亏': '',
            '今日盈亏': '',
            '币种': 'USD',
            'synthetic': 'Y',
        })
    return rows


# ── Quote fetching ───────────────────────────────────────────

# Market states with a live trading session, split by whether the
# subscription's best_price is trustworthy.
#
# best_price has priority overnight > after-hours > last. OpenD never
# invalidates overnight_price / after_price once the regular session opens,
# so during MORNING/AFTERNOON best_price keeps returning last night's tick
# while last_price moves correctly (e.g.  12:27 ET: INTC
# best_price 105.08 = overnight_price vs true last 95.33 — the subscription
# even reported change_pct +4.84% against a fresh prev_close 100.23 when the
# stock was actually -4.86%). That silently blinded every alert threshold.
# So in a REGULAR session we take the request-based snapshot's last_price.
REGULAR_SESSION_STATES = {
    'MORNING', 'AFTERNOON',        # US regular session
    'AUCTION', 'TRADE_AT_LAST',    # open/close auction phases
}

# In these an extended-hours tick IS the latest trade, so it beats the
# snapshot (whose last_price is only the official close). But WHICH extended
# field is live depends on the session, and best_price must never be used to
# decide: its priority is overnight > after > last regardless of session, and
# OpenD never clears a finished session's price. Measured  16:15 ET
# in AFTER_HOURS_BEGIN:
#     INTC  overnight 105.08 (Thu-night leftover) | after 92.44 (live) | last 92.32
#     MU    overnight 977.21                      | after 924.00      | last 920.95
#     NBIS  overnight 219.44                      | after 189.88      | last 187.77
# best_price returned the overnight column every time, i.e. a price from
# *before* Friday's regular session, which had since moved INTC -7.9%. So we
# map each state to the fields whose sessions have not been superseded, most
# recent first.
#
# Ordering rule: never offer a field whose session predates a session that
# has since completed. After-hours therefore excludes overnight (yesterday's,
# pre-dating today's regular session); pre-market and overnight may use it
# because the overnight session is contiguous with them.
EXTENDED_PRICE_CHAIN = {
    'AFTER_HOURS_BEGIN':  ('after_price', 'last_price'),
    'NIGHT_OPEN':         ('overnight_price', 'after_price', 'last_price'),
    'NIGHT':              ('overnight_price', 'after_price', 'last_price'),
    'OVERNIGHT':          ('overnight_price', 'after_price', 'last_price'),
    # Pre-market's live field is pre_price; the overnight tick is only the
    # fallback before the first pre-market trade (the sessions are contiguous).
    # Verified live  08:51 ET (PRE_MARKET_BEGIN): NBIS pre_price
    # 186.12 (450k shares traded) vs overnight_price 194.74 — a Sunday-night
    # tick ~5h stale that reported +3.35% while the stock was actually -2.25%.
    # The old chain led with overnight_price and get_quote didn't even extract
    # pre_price; that morning's briefing published the inverted sign.
    'PRE_MARKET_BEGIN':   ('pre_price', 'overnight_price', 'after_price', 'last_price'),
}

# Baseline for change/change_pct per extended state. This table and
# EXTENDED_PRICE_CHAIN are a matched pair — which tick, and what it is measured
# against — so every chain key MUST appear here
# (test_every_extended_state_has_a_baseline). A state in one table but not the
# other silently mixes two sessions into one number.
#
# prev_close_price does NOT roll at the close; it rolls when the next regular
# session opens. Measured live  01:33 ET (OVERNIGHT): NBIS
# prev_close_price 212.58 = Mon 8/3, while Tue 8/4's close 225.74 sits in
# last_price. So the right baseline depends on which trading day the tick
# belongs to — and moomoo assigns the overnight session to the NEXT day:
#
#   AFTER_HOURS_BEGIN   tick is on today's date  → prev_close, i.e. change_pct
#                       reads "today incl. after hours". Deliberate (用户
#): it is the only window where a scheduled
#                       run sees the completed session's move at all — the
#                       ET 16:10 postclose and ET 18:26 heartbeat both land
#                       here. Not an omission; do not "fix" it to last_price.
#   NIGHT / NIGHT_OPEN  tick belongs to the next trading day, whose baseline is
#   OVERNIGHT / PRE_     the close just posted = last_price.
#   MARKET_BEGIN
#
# Cross-check: moomoo's own extended fields are each measured against the close
# preceding their session (measured  01:33 ET: INTC
# overnight_change_val -0.22 = overnight 100.64 - last 100.86; AMZN
# after_change_val +0.38 = after 277.80 - last 277.42; NBIS pre_change_rate
# -2.25% on  vs last 190.41, not vs the rolled prev_close 188.43).
#
#  regression: only PRE_MARKET_BEGIN was listed and the other four
# fell through `.get(state, 'prev_close')`. During OVERNIGHT that published
# NBIS +5.63% (tick 224.54 against Mon's 212.58) when the truth against Tue's
# close was -0.53%, and another name +1.01% when it was -7.7% on its own earnings
# night — price from session N+1, baseline from session N-1, with a whole
# completed session in between. Both failure directions at once: a night
# gap-down could never trip the >5% alert, and the false positive kept NBIS's
# alert latch active so its next real move would have been deduped away.
EXTENDED_BASELINE_FIELD = {
    'AFTER_HOURS_BEGIN':  'prev_close',
    'NIGHT_OPEN':         'last_price',
    'NIGHT':              'last_price',
    'OVERNIGHT':          'last_price',
    'PRE_MARKET_BEGIN':   'last_price',
}

EXTENDED_LIVE_STATES = set(EXTENDED_PRICE_CHAIN)

# In ANY other state (session ended, closed, pre-open, no data) the
# subscription's after_price / overnight_price are stale leftovers — on a
# closed market best_price returns a stale extended tick, not the official
# close (e.g.  Sat: MU best_price 936.71 vs true Fri close 971.0).
# So we treat this as an allowlist: any state NOT listed here falls back to
# the request-based snapshot's official close. An unknown/new state therefore
# defaults to the safe official close, never a stale extended tick.
LIVE_TRADING_STATES = REGULAR_SESSION_STATES | EXTENDED_LIVE_STATES


def _quote_from_subscription(q: dict, state: str) -> dict:
    """Build a quote row from the real-time subscription (extended sessions).

    Picks the extended field that belongs to `state` rather than best_price,
    which serves a finished session's leftover. change/change_pct are
    recomputed from the state's own baseline (EXTENDED_BASELINE_FIELD: the
    close preceding the trading day the tick belongs to) for the same reason
    they are in _quote_from_sub_last: the subscription derives its own off
    best_price. The baseline used is also written out as prev_close so
    downstream consumers recomputing pct agree.

    Returns None if no field in the chain has a price, so the caller can fall
    back to the official snapshot instead of writing a null price.
    """
    for field in EXTENDED_PRICE_CHAIN.get(state, ('last_price',)):
        price = _safe_float(q.get(field))
        if price is None:
            continue
        baseline_field = EXTENDED_BASELINE_FIELD.get(state, 'prev_close')
        prev = _safe_float(q.get(baseline_field))
        if prev is None:
            prev = _safe_float(q.get('prev_close'))
        chg = (price - prev) if prev is not None else None
        pct = (chg / prev * 100) if (chg is not None and prev) else None
        return {
            'quote_time': q.get('quote_time'),
            **{k: q[k] for k in QUOTE_CLOCK_FIELDS if k in q},
            'price_source': f'moomoo_{field.replace("_price", "")}:{state}',
            'current_price': price,
            'change': chg,
            'change_pct': pct,
            'open': q.get('open'),
            'high': q.get('high'),
            'low': q.get('low'),
            'prev_close': prev,
        }
    return None


def _quote_from_sub_last(q: dict) -> dict:
    """Build a quote row from the subscription's last_price (regular session).

    Fallback for a regular session when the snapshot is unavailable:
    last_price tracks the regular session correctly, unlike best_price.
    change/change_pct are recomputed from prev_close because the
    subscription derives its own off best_price (the stale overnight tick).
    """
    last = _safe_float(q.get('last_price'))
    prev = _safe_float(q.get('prev_close'))
    chg = (last - prev) if (last is not None and prev is not None) else None
    pct = (chg / prev * 100) if (chg is not None and prev) else None
    return {
        'quote_time': q.get('quote_time'),
        **{k: q[k] for k in QUOTE_CLOCK_FIELDS if k in q},
        'price_source': 'moomoo_last',
        'current_price': q.get('last_price'),
        'change': chg,
        'change_pct': pct,
        'open': q.get('open'),
        'high': q.get('high'),
        'low': q.get('low'),
        'prev_close': q.get('prev_close'),
    }


def _quote_from_snapshot(snap: dict, state: str, live: bool = False) -> dict:
    """Build a quote row from the official snapshot.

    Snapshot is request-based (not a subscription), so its last_price is the
    official last/close and is also immune to a cold OpenD subscription cache
    and to the never-cleared overnight_price leftover.
    """
    prefix = 'moomoo_live' if live else 'moomoo_close'
    return {
        'quote_time': opend_quote_time(snap.get('update_time')),
        'quote_time_raw': snap.get('update_time_raw', snap.get('update_time')),
        'quote_time_basis': 'opend_snapshot_update_time',
        'quote_retrieved_at': snap.get('quote_retrieved_at'),
        'price_source': f'{prefix}:{state or "no_state"}',
        'current_price': snap.get('last_price'),
        'change': snap.get('change'),
        'change_pct': snap.get('change_pct'),
        'open': snap.get('open'),
        'high': snap.get('high'),
        'low': snap.get('low'),
        'prev_close': snap.get('prev_close'),
    }


def fetch_moomoo_quotes(tickers: List[str]) -> Dict[str, dict]:
    """Batch fetch quotes, choosing source by live-session state.

    - Regular session (MORNING/AFTERNOON/auction) → official snapshot
      last_price. The subscription's best_price still serves the previous
      night's overnight_price here, so it must not be used.
    - Extended live session (pre/after/overnight) → the subscription field
      belonging to that session (EXTENDED_PRICE_CHAIN), where the extended
      tick IS the latest trade. Never best_price, which serves whichever
      extended session ran most recently rather than the current one.
    - No live session (closed / session ended / pre-open) → official close
      from get_market_snapshot. Fixes stale extended-hours leftovers that
      OpenD's subscription returns on a closed market or fresh start.
    """
    raw = get_quote(tickers)             # real-time subscription (extended-hours aware)
    states = get_market_state(tickers)   # ticker -> MarketState name
    snaps = get_snapshot(tickers)        # request-based official last/close

    results: Dict[str, dict] = {}
    for ticker in sorted(set(raw) | set(snaps)):
        q = raw.get(ticker) or {}
        snap = snaps.get(ticker) or {}
        state = states.get(ticker, '')
        regular = state in REGULAR_SESSION_STATES
        live = state in LIVE_TRADING_STATES
        snap_has_price = snap.get('last_price') is not None

        row = None
        if regular:
            if snap_has_price:
                # Regular session: snapshot last_price is authoritative;
                # subscription best_price is a stale overnight leftover.
                row = _quote_from_snapshot(snap, state, live=True)
            elif q.get('last_price') is not None:
                # Regular session with no snapshot — use the subscription's
                # last_price, never best_price.
                row = _quote_from_sub_last(q)
        elif live:
            # Extended live session: this session's own extended tick is the
            # latest trade. Returns None if that field is empty.
            row = _quote_from_subscription(q, state)

        if row is None and snap_has_price:
            # No live session, or a live one with no usable subscription quote
            # (e.g. cold cache): the official last/close is always safe.
            row = _quote_from_snapshot(snap, state, live=live)
        if row is None:
            continue
        # Surface session liveness so the caller can prefer the broker's own
        # mark (nominal_price) over market data when the market is closed.
        row['market_live'] = live
        # Option greeks ride along from the request-based snapshot (the
        # subscription has no greeks). The shock-budget framework measures
        # gross by DELTA notional — contract market value books GOOGL's LEAPS
        # at 10.5% NAV when the real exposure is 71%. Before  each
        # caller reverse-solved IV then delta from Black-Scholes, i.e. one
        # answer per caller; the broker publishes the real greeks, so persist
        # those instead. Absent on stock rows (option_valid False).
        for g in GREEK_FIELDS:
            if snap.get(g) is not None:
                row[g] = snap[g]
        results[ticker] = row
    return results


# ── State I/O (replaces CSV) ─────────────────────────────────

def refresh_watchlist_quotes() -> int:
    """watchlist 候选的报价快照 → watchlist.json broker block (H1/E4)。

    此前 watchlist broker 恒 null → heartbeat 对入场侧完全盲: 两次 briefing 之间
    (最长 24h, 周末 72h) 候选票砸进 z1 系统一声不吭, 唯一即时性是 moomoo 服务端
    ≤7 字符响铃。快照只写行情字段, **qty 恒 0**—— watchlist_migrate 的 promote
    判据与 watchlist_sync 的 held 集都读 positions 的 broker, 不受影响; 卡片
    所有权字段 (agent/thesis/zones) 一概不碰。消费者: portfolio_brief 的
    wl_zone_touch 告警 + 入场计划的现价参照。
    """
    wl = state_io.read_watchlist(allow_missing=True)
    tks = [t for t, b in (wl.get("tickers") or {}).items()
           if str((b or {}).get("status")) == "watching"
           and (b or {}).get("moomoo_listed") is not False]
    if not tks:
        return 0
    quotes = fetch_moomoo_quotes(tks)
    n = 0
    for t in tks:
        q = quotes.get(t)
        if not q or q.get("current_price") is None:
            continue
        wl["tickers"][t]["broker"] = {
            "source": "moomoo_quote_snapshot", "qty": 0, "synthetic": False,
            "current_price": q.get("current_price"),
            "prev_close": q.get("prev_close"),
            "change_pct": q.get("change_pct"),
            "as_of": opend_quote_time(q.get("quote_time")),
            "quote_source": q.get("price_source") or "",
            **{k: q[k] for k in QUOTE_CLOCK_FIELDS if k in q},
        }
        n += 1
    if n:
        state_io.write_watchlist(wl)
    return n


def read_rows_from_state() -> List[dict]:
    """Read state/positions.json and produce CSV-shaped rows for the existing
    quote-merge pipeline. Skips closed positions (no broker block).
    """
    doc = state_io.read_positions()
    rows: List[dict] = []
    for ticker, body in doc.get('tickers', {}).items():
        broker = body.get('broker')
        if broker is None:
            continue
        instrument = body.get('instrument') or {}
        rows.append({
            '代码': ticker,
            '名称': instrument.get('name') or '',
            '持有数量': broker.get('qty', 0),
            '可用数量': broker.get('qty', 0),
            '现价': broker.get('current_price') or '',
            '平均成本价': broker.get('avg_cost', 0),
            '币种': broker.get('currency', 'USD'),
            'synthetic': 'Y' if broker.get('synthetic') else '',
            'quote_time': broker.get('as_of') or '',
            **{k: broker[k] for k in QUOTE_CLOCK_FIELDS if k in broker},
            'price_source': broker.get('quote_source') or 'moomoo',
            'current_price': broker.get('current_price') or '',
            'change_pct': broker.get('change_pct') or '',
            '未实现盈亏比例': broker.get('unrealized_pnl_pct') or '',
            '未实现盈亏': broker.get('unrealized_pnl_usd') or '',
            '已实现盈亏': broker.get('realized_pnl_usd') or '',
            '今日盈亏': broker.get('today_change_usd') or '',
        })
    return rows


def fmt(v, digits=3) -> str:
    if v is None:
        return ''
    try:
        return f'{float(v):.{digits}f}'
    except (ValueError, TypeError):
        return ''


# ── Main ─────────────────────────────────────────────────────

def main(argv: List[str]) -> int:
    args = list(argv)
    want_json = '--json' in args
    if want_json:
        args.remove('--json')
    quotes_only = '--quotes-only' in args
    if quotes_only:
        args.remove('--quotes-only')
    filter_tickers = [t.upper() for t in args]

    # Step 1: Get position rows
    if quotes_only:
        rows = read_rows_from_state()
        fresh_positions = False
    else:
        pos = fetch_positions()
        # Fresh broker marks (现价 = nominal_price) are only present when the
        # trade API actually returned positions this run.
        fresh_positions = bool(pos)
        rows = pos if pos else read_rows_from_state()

    if not rows:
        print('No positions found.', file=sys.stderr)
        return 1

    # Step 1.5: Add synthetic rows for option underlyings not held as stock
    add_synthetic_underlying_rows(rows)

    # Step 2: Determine tickers to quote
    all_tickers = [r.get('代码', '').strip() for r in rows if r.get('代码', '').strip()]
    tickers_to_quote = filter_tickers or all_tickers

    # Step 3: Fetch real-time quotes via OpenD
    quotes = fetch_moomoo_quotes(tickers_to_quote)
    missing = [t for t in tickers_to_quote if t not in quotes]
    if missing:
        print(f'[warn] no quote for: {", ".join(missing)}', file=sys.stderr)

    # Step 4: Merge quotes into rows.
    # On a CLOSED market the broker's own end-of-day mark (nominal_price,
    # captured as 现价 during a fresh position sync) is the authoritative
    # price: it equals the official close for stocks and is a better mark than
    # a stale last-trade for illiquid options. Prefer it for real positions;
    # synthetic underlyings (no position, empty 现价) keep the market-data
    # quote (snapshot). During a live session the market quote is fresher, so
    # this override never applies — live behavior is unchanged.
    for row in rows:
        ticker = row.get('代码', '').strip()
        q = quotes.get(ticker)
        if not q:
            continue
        is_synth = (row.get('synthetic') or '').strip().upper() == 'Y'
        nominal = _safe_float(row.get('现价'))
        prefer_mark = (
            fresh_positions
            and not q.get('market_live')
            and not is_synth
            and nominal is not None and nominal > 0
)
        if prefer_mark:
            cur = nominal
            prev = _safe_float(q.get('prev_close'))
            chg = (cur - prev) if prev is not None else _safe_float(q.get('change'))
            pct = (chg / prev * 100) if (chg is not None and prev) else _safe_float(q.get('change_pct'))
            src = (q.get('price_source') or 'moomoo') + '+broker_mark'
        else:
            cur = q.get('current_price')
            chg = q.get('change')
            pct = q.get('change_pct')
            src = q.get('price_source', '')

        row['quote_time'] = q.get('quote_time', '')
        for field in QUOTE_CLOCK_FIELDS:
            row[field] = q.get(field)
        row['price_source'] = src
        row['current_price'] = fmt(cur)
        row['change'] = fmt(chg)
        row['change_pct'] = fmt(pct, 2)
        row['open'] = fmt(q.get('open'))
        row['high'] = fmt(q.get('high'))
        row['low'] = fmt(q.get('low'))
        row['prev_close'] = fmt(q.get('prev_close'))
        for g in GREEK_FIELDS:
            if q.get(g) is not None:
                row[g] = q[g]

    # Step 5: Output
    if want_json:
        out = []
        for row in rows:
            ticker = row.get('代码', '')
            if filter_tickers and ticker not in filter_tickers:
                continue
            out.append({k: row.get(k, '') for k in POS_COLS + QUOTE_COLS + list(GREEK_FIELDS)})
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        # Update state/positions.json broker blocks. Preserves agent block,
        # lots, pending_orders, exit_info — those are owned by the agent and
        # the trade-history detector. See state_io.py.
        # zero_absent: 券商这一轮没返回的卡 = 已经平掉了 → qty 归零 (NBIS 源案例,
        # 见 state_io.update_broker_blocks 的长注)。三道护栏都在这里, 缺一不可:
        #   fresh_positions  trade API **真返回了持仓行**(失败/空 → fetch_positions
        #                    返回 [], rows 回落 read_rows_from_state = 卡片自己的旧值。
        #                    拿那份去判"谁不在里面"会把整本书清零)
        #   not filter_tickers   CLI 只查了几只票 → "不在里面"毫无意义
        #   not quotes_only      根本没问过持仓
        zero_absent = bool(fresh_positions) and not filter_tickers and not quotes_only
        state_io.update_broker_blocks(rows_to_broker_updates(rows),
                                      zero_absent=zero_absent)
        print(
            f'Updated state/positions.json broker blocks for {len(rows)} tickers',
            file=sys.stderr,
)
        # watchlist 报价快照顺手拉 (一次批量 quote 调用) — 失败不拖垮持仓刷新
        try:
            nwl = refresh_watchlist_quotes()
            if nwl:
                print(f'Refreshed watchlist quote snapshots for {nwl} tickers',
                      file=sys.stderr)
        except Exception as e:                                    # noqa: BLE001
            print(f'[warn] watchlist quote refresh failed: {type(e).__name__}: {e}',
                  file=sys.stderr)

    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
