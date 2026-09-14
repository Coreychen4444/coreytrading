#!/usr/bin/env python3
"""Incrementally sync trade history from OpenD to events/<YYYY-MM>/trades.jsonl.

Pulls historical orders via OpenD API, dedups by order_id against existing
JSONL entries, appends new fills as `fill` (BUY) or `close` (SELL) events.
Only filled orders are kept by default.

Timestamps: moomoo returns ET wall-clock naive strings. Events are written as
ISO 8601 **with explicit America/New_York offset**(e.g. T15:03:48-04:00)
— trading-day semantics, month files bucket by ET calendar month.

Usage:
  python3 quant/sync/trade_history_sync.py                  # incremental sync (appends)
  python3 quant/sync/trade_history_sync.py --json           # appends AND prints JSON
  python3 quant/sync/trade_history_sync.py --dry-run --json # preview only, no writes
  python3 quant/sync/trade_history_sync.py --days 90        # look back 90 days
  python3 quant/sync/trade_history_sync.py --all-status     # include cancelled/pending

Exit codes: 0 = ok (even if no new fills); 1 = OpenD query failed.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 直接 `python3 quant/<层>/<模块>.py` 时让 `quant.*` 可 import

from quant.core.paths import add_openapi_path, EVENTS_DIR  # noqa: E402

from quant.core import env  # noqa: F401  (.env → os.environ)

import json
import os
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from zoneinfo import ZoneInfo

add_openapi_path()  # openapi skill scripts (`common`) 上 sys.path
from common import (
    create_trade_context, get_default_security_firm, get_default_acc_id,
    safe_close, safe_float, safe_get, format_enum, parse_trd_env, RET_OK,
)

FILLED_STATUSES = {'FILLED_ALL', 'FILLED_PART'}
REAL_TRD_ENV = parse_trd_env('REAL')
ET_TZ = ZoneInfo('America/New_York')

import re as _re  # noqa: E402
from quant.sync import state_io  # noqa: E402
from quant.core.holdings import derive_tier, stamping_cards  # noqa: E402  tier/卡片合并判别式单一真源
from quant.core.io_json import iter_jsonl, read_json  # noqa: E402
from quant.core.occ import parse_occ, strip_us_prefix as _strip_us_prefix  # noqa: E402
from quant.core.paths import KLINE_CACHE_DIR, OPTION_PLANS_PATH, POSITIONS_PATH, WATCHLIST_PATH  # noqa: E402
from quant.core.sizing import derive_auth  # noqa: E402  授权快照判别式单一真源
from quant.core.structure import option_fill_flags, zone_bands, zone_position  # noqa: E402  价位盖章真源
from quant.core.timeutil import et_naive_to_iso, et_today  # noqa: E402

_PARENT_RE = _re.compile(r'combo leg, parent order_id (\S+)')


def load_existing_keys() -> tuple[set[str], set[tuple[str, str]]]:
    """Walk events/<YYYY-MM>/trades.jsonl files.

    Returns (order_ids, covered_pairs):
      order_ids     — every event's own order_id (primary dedup key)
      covered_pairs — (order_id_or_parent_order_id, ticker) pairs already
                      represented by an event. Used so a combo-leg fill
                      (keyed by deal_id) is never appended when its parent
                      order already produced an event for the same ticker.
    """
    ids: set[str] = set()
    covered: set[tuple[str, str]] = set()
    if not EVENTS_DIR.exists():
        return ids, covered
    for month_dir in sorted(EVENTS_DIR.iterdir()):
        if not month_dir.is_dir():
            continue
        path = month_dir / 'trades.jsonl'
        if not path.exists():
            continue
        with path.open('r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                oid = obj.get('order_id')
                ticker = obj.get('ticker') or ''
                if oid:
                    ids.add(str(oid))
                    covered.add((str(oid), ticker))
                parent = obj.get('parent_order_id')
                if not parent:
                    m = _PARENT_RE.search(obj.get('notes') or '')
                    parent = m.group(1) if m else None
                if parent:
                    covered.add((str(parent), ticker))
    return ids, covered


_to_iso_et = et_naive_to_iso  # single implementation: lib.timeutil


SIDE_TO_EVENT = {
    'BUY': 'fill',         # open long
    'SELL_SHORT': 'fill',  # open short
    'SELL': 'close',       # close long
    'BUY_BACK': 'close',   # close short
}


def _option_plans() -> dict:
    """entry_guard 落的选约 (state/option_plans.json) 的 plans 段 —— reversal_probe 的授权
    住这里, 不住正股卡。读不到 = 空 dict (无本类授权, 不是崩)。"""
    try:
        if not OPTION_PLANS_PATH.exists():
            return {}
        return dict((read_json(OPTION_PLANS_PATH) or {}).get("plans") or {})
    except Exception:                       # noqa: BLE001
        return {}


def _cards() -> dict:
    """Read both card stores and apply the canonical authorization-card merge."""
    try:
        return stamping_cards(
            read_json(POSITIONS_PATH) or {},
            (read_json(WATCHLIST_PATH) or {}) if WATCHLIST_PATH.exists() else {})
    except Exception:                       # noqa: BLE001 — 读不到就不盖章, 不是崩
        return {}


def underlying_bar_at(underlying: str, ts_iso: str) -> Optional[dict]:
    """成交时刻正股那根 bar → {"time","low","high","close","basis"}; None = 拉不到。

    期权价位盖章的数据源 (期权锚定正股回踩 z1 的价格, 不追高):
    优先 OpenD 60m (label = bar **结束**时刻, 取第一根结束时刻 ≥ 成交时刻的 bar; 盘后
    成交取最后一根), 拉不到 → 本地日线缓存当日 bar (basis "1d", 整日范围, 判据同样保守),
    再没有 → None (事件标 unknown, 归人判)。sync(2) → data(1) 只在盖章那一刻才拉。
    """
    day, hhmm = ts_iso[:10], ts_iso[11:16]
    bars: List[dict] = []
    try:
        from quant.data.market_data import get_kline
        bars = [b for b in (get_kline(underlying, ktype="60m", start=day, end=day) or [])
                if str(b.get("time", "")).startswith(day)]
    except Exception as e:                       # noqa: BLE001 — OpenD 不在 = 退日线
        print(f"[warn] 60m bar fetch failed for {underlying} {day}: {e}", file=sys.stderr)
        bars = []
    if bars:
        later = [b for b in bars if str(b.get("time", ""))[11:16] >= hhmm]
        b = later[0] if later else bars[-1]
        try:
            return {"time": str(b.get("time")), "low": float(b["low"]), "high": float(b["high"]),
                    "close": float(b["close"]), "basis": "60m"}
        except (KeyError, TypeError, ValueError):
            pass
    p = KLINE_CACHE_DIR / f"{underlying}.jsonl"
    if p.exists():
        for r in iter_jsonl(p):
            if str(r.get("time", ""))[:10] == day and r.get("low") and r.get("high"):
                return {"time": str(r.get("time")), "low": float(r["low"]), "high": float(r["high"]),
                        "close": float(r.get("close") or r["high"]), "basis": "1d"}
    return None


def append_new_fills_to_events(new_rows: List[dict]) -> int:
    """For each new filled row, append a `fill` or `close` event to
    events/<YYYY-MM>/trades.jsonl. Handles all four moomoo trd_side values
    (BUY / SELL / SELL_SHORT / BUY_BACK). Returns number of events appended.

    落库即盖章 (, 用户 批): `tier` 与授权标签是**机械推导**的
    (定 —— 不问人), 判别式住 core。此前推导只活在手动 CLI
    `annotate_event` 里, 而"计划内成交不需要注解"意味着没人会去跑它 → 每笔计划内
    成交都裸着落库。8/6 INTC 两笔就是这么来的: 没有标签 →
    `position_budget` 返回 None → 卡上预算行整条消失, 加仓通道已关的仓位被播成
    "接近首档吸纳区"。**手工步骤 = 这一步实际上不存在**(data_discipline)。

    分工: tier 给所有事件 (它是"这仓在哪一层"); `auth` {cap_pct_nav, gate_no}
    只给 **BUY fill**(起接替 classification —— 入场授权快照, 卖出
    事件没有这个语义)。历史事件的 classification 只读保留。
    推不出来 = 留空**不猜**: 那正是"无授权成交"的形状, 由 brief 告警点名给人判,
    见 `sizing.derive_auth` 与 portfolio_brief 的 INFRA:fill_untagged。
    """
    cards = _cards()
    appended = 0
    for r in new_rows:
        side = (r.get('side') or '').upper()
        event_type = SIDE_TO_EVENT.get(side)
        if event_type is None:
            continue
        status = (r.get('status') or '').upper()
        if 'FILLED' not in status:
            continue
        ts = _to_iso_et(r.get('updated_time') or r.get('create_time'))
        if not ts:
            continue
        event = {
            'event_type': event_type,
            'timestamp': ts,
            'ticker': (r.get('code') or '').strip(),
            'side': side,
            'qty': float(r.get('dealt_qty') or 0),
            'price': float(r.get('dealt_avg_price') or 0),
            'order_id': r.get('order_id'),
            'broker': 'moomoo_opend',
        }
        tier, _ = derive_tier(event['ticker'], cards)
        event['tier'] = tier
        if event_type == 'fill' and side == 'BUY':
            stamp_day = et_today()
            cross_day = ts[:10] < stamp_day
            plans = _option_plans()
            auth, _ = derive_auth(event['ticker'], cards, today=ts[:10], option_plans=plans)
            if auth:
                event['auth'] = {**auth, 'stamped_at': stamp_day}
            elif cross_day:
                event['auth_note'] = (
                    f'落库跨日 (fill {ts[:10]} → 盖章 {stamp_day}), '
                    '卡片可能已被迁移或重画 — 授权需复核'
                )
            if cross_day:
                event['stamp_stale_card_risk'] = True

            # Freeze the fill's location against the card visible at ingestion.
            # Options are excluded because premium and underlying zones use
            # different price axes.
            if not parse_occ(event['ticker']):
                card = cards.get(event['ticker'].upper()) or {}
                bands = zone_bands((card.get('agent') or {}).get('zones'))
                if bands:
                    event.setdefault('zone_at_fill', zone_position(event['price'], bands))
                    no_chase, trim = bands.get('no_chase'), bands.get('trim')
                    flags = []
                    if no_chase and event['price'] >= no_chase:
                        flags.append('above_no_chase')
                    if (isinstance(trim, list) and len(trim) == 2
                            and trim[0] <= event['price'] <= trim[1]):
                        flags.append('in_trim_zone')
                    if flags:
                        event['price_flags'] = flags
                if (auth or {}).get('entry_class') == 'reversal_probe':
                    # ── 抄底正股 BUY : 锚 = 信号日收盘, 成交价在其上 = 追高 (above_signal_close)
                    sc = float((plans.get(event['ticker'].upper()) or {}).get('signal_close') or 0.0)
                    if sc > 0 and float(event['price']) > sc:
                        event['price_flags'] = sorted(set(event.get('price_flags') or []) | {'above_signal_close'})
            elif (auth or {}).get('entry_class') == 'reversal_probe':
                # ── reversal_probe 期权 BUY : 本类不锚 z1, 锚 = 信号日收盘;
                # 整根 60m bar 在信号收盘之上成交 = 追高 (above_signal_close), 跨越归人判。
                und = str((parse_occ(event['ticker']) or {}).get('underlying') or '').upper()
                sc = float((plans.get(und) or {}).get('signal_close') or 0.0)
                bar = underlying_bar_at(und, ts) if und else None
                if bar and sc > 0:
                    ambiguous = bool(bar['low'] <= sc < bar['high'])
                    event['underlying_at_fill'] = {
                        'price': round(bar['close'], 4), 'low': bar['low'], 'high': bar['high'],
                        'bar_time': bar['time'], 'basis': bar['basis'], 'ambiguous': ambiguous,
                        'anchor': 'signal_close', 'anchor_price': sc}
                    if bar['low'] > sc:
                        event['price_flags'] = ['above_signal_close']
                else:
                    event['underlying_at_fill'] = {
                        'price': None, 'basis': 'unknown',
                        'note': '成交时刻正股 bar 不可得 — reversal_probe 价位归人判'}
            else:
                # ── 期权 BUY 的**正股**价位盖章 (期权锚定正股回踩 z1 的价格,
                # 不追高) ─────────────────────────────────────────────────
                # 此前期权一律不盖 ("保费价与正股 zones 不同轴"), 于是确认之前、no_chase 之上的
                # 期权成交只能靠手写注解。轴换成**成交时刻正股的 bar**:
                # 60m 优先, 拉不到退当日日线; 判据保守 = 整根 bar 在带外才判, 跨带标
                # ambiguous 归人判。判别式 core.structure.option_fill_flags; 出口同正股
                # (brief INFRA:fill_price_breach), 期权多两条 flag: above_z1 / below_z2。
                und = str((parse_occ(event['ticker']) or {}).get('underlying') or '').upper()
                zb = zone_bands(((cards.get(und) or {}).get('agent') or {}).get('zones')) if und else None
                if zb:
                    bar = underlying_bar_at(und, ts)
                    if bar:
                        flags, ambiguous = option_fill_flags(bar['low'], bar['high'], zb)
                        event['underlying_at_fill'] = {
                            'price': round(bar['close'], 4), 'low': bar['low'], 'high': bar['high'],
                            'bar_time': bar['time'], 'basis': bar['basis'], 'ambiguous': ambiguous}
                        event['zone_at_fill'] = zone_position(bar['close'], zb)
                        if flags:
                            event['price_flags'] = flags
                    else:
                        event['underlying_at_fill'] = {
                            'price': None, 'basis': 'unknown',
                            'note': '成交时刻正股 bar 不可得 (60m/1d 都没有) — 期权价位归人判'}
        if r.get('parent_order_id'):
            event['parent_order_id'] = r['parent_order_id']
        if r.get('remark'):
            event['notes'] = r['remark']
        try:
            state_io.append_trade_event(event)
            appended += 1
        except Exception as e:
            print(f'[warn] events append failed for {r.get("order_id")}: {e}', file=sys.stderr)
    return appended


def fetch_history(days: int = 30, all_status: bool = False) -> tuple[List[dict], bool]:
    """Fetch historical orders from OpenD. Returns (rows, ok).

    Rows come from `history_order_list_query` PLUS any combo multi-leg fills
    that don't appear in the order list. Multi-leg combo orders (e.g. spread
    closes) share one parent `order_id` but have separate fills per leg with
    distinct `deal_id` and `code`. moomoo's `history_order_list_query` either
    omits combo parents entirely or returns one of the legs, so legs missed
    there are recovered from `history_deal_list_query` and synthesized into
    rows using `deal_id` as the unique event id (parent kept as
    `parent_order_id` for downstream dedup).

    ok=False when the order-list query itself failed (caller should exit 1).
    """
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    start_str = start.strftime('%Y-%m-%d')
    # +1 day: moomoo buckets the range in its own (HK) calendar, which is ahead
    # of both UTC and ET. A US after-hours fill (16:00-20:00 ET = 04:00-08:00
    # HKT next day) therefore lands on tomorrow's HK date and falls outside an
    # end date computed from "now". The INTC stop-out on  16:25 ET
    # was invisible to this sync while get_history_orders --end
    # returned it. It self-healed the next day, but "the fill ledger is a day
    # late whenever you trade after hours" is not a property worth keeping.
    end_str = (end + timedelta(days=1)).strftime('%Y-%m-%d')
    ok = True

    ctx = create_trade_context(security_firm=get_default_security_firm())
    try:
        ret, data = ctx.history_order_list_query(
            trd_env=REAL_TRD_ENV,
            acc_id=get_default_acc_id(),
            start=start_str, end=end_str,
)
        if ret != RET_OK or data is None:
            print(f'[warn] history_order_list_query failed: {data}', file=sys.stderr)
            order_rows = []
            ok = False
        elif data.empty:
            order_rows = []
        else:
            order_rows = []
            for _, r in data.iterrows():
                status = format_enum(safe_get(r, 'order_status', default=''))
                if not all_status and status not in FILLED_STATUSES:
                    continue
                code = _strip_us_prefix(safe_get(r, 'code', default=''))
                order_rows.append({
                    'order_id': str(safe_get(r, 'order_id', default='')),
                    'create_time': safe_get(r, 'create_time', default=''),
                    'updated_time': safe_get(r, 'updated_time', default=''),
                    'code': code,
                    'name': safe_get(r, 'stock_name', default=''),
                    'side': format_enum(safe_get(r, 'trd_side', default='')),
                    'order_type': format_enum(safe_get(r, 'order_type', default='')),
                    'status': status,
                    'qty': safe_float(safe_get(r, 'qty', default=0)),
                    'price': safe_float(safe_get(r, 'price', default=0)),
                    'dealt_qty': safe_float(safe_get(r, 'dealt_qty', default=0)),
                    'dealt_avg_price': safe_float(safe_get(r, 'dealt_avg_price', default=0)),
                    'currency': safe_get(r, 'currency', default='USD'),
                    'remark': safe_get(r, 'remark', default=''),
                })

        # Recover combo legs that order list omits. Index regular order rows by
        # (order_id, code) so we can detect fills that no order row covers.
        seen = {(r['order_id'], r['code']) for r in order_rows}
        ret_f, fills = ctx.history_deal_list_query(
            trd_env=REAL_TRD_ENV,
            acc_id=get_default_acc_id(),
            start=start_str, end=end_str,
)
        if ret_f != RET_OK or fills is None or fills.empty:
            return order_rows, ok

        combo_rows = []
        for _, f in fills.iterrows():
            order_id = str(safe_get(f, 'order_id', default=''))
            code = _strip_us_prefix(safe_get(f, 'code', default=''))
            if (order_id, code) in seen:
                continue
            deal_id = str(safe_get(f, 'deal_id', default=''))
            if not deal_id:
                continue
            combo_rows.append({
                'order_id': deal_id,  # use deal_id as event id for combo legs
                'parent_order_id': order_id,
                'create_time': safe_get(f, 'create_time', default=''),
                'updated_time': safe_get(f, 'create_time', default=''),
                'code': code,
                'name': safe_get(f, 'stock_name', default=''),
                'side': format_enum(safe_get(f, 'trd_side', default='')),
                'order_type': 'COMBO',
                'status': 'FILLED_ALL',
                'qty': safe_float(safe_get(f, 'qty', default=0)),
                'price': safe_float(safe_get(f, 'price', default=0)),
                'dealt_qty': safe_float(safe_get(f, 'qty', default=0)),
                'dealt_avg_price': safe_float(safe_get(f, 'price', default=0)),
                'currency': 'USD',
                'remark': f'combo leg, parent order_id {order_id}',
            })
            seen.add((order_id, code))
        if combo_rows:
            print(f'[info] recovered {len(combo_rows)} combo leg fills', file=sys.stderr)
        return order_rows + combo_rows, ok
    finally:
        safe_close(ctx)


def main(argv: List[str]) -> int:
    args = list(argv)
    want_json = '--json' in args
    if want_json:
        args.remove('--json')
    all_status = '--all-status' in args
    if all_status:
        args.remove('--all-status')
    dry_run = '--dry-run' in args
    if dry_run:
        args.remove('--dry-run')
    days = 30
    if '--days' in args:
        idx = args.index('--days')
        try:
            days = int(args[idx + 1])
        except (IndexError, ValueError):
            print('[error] --days requires an integer argument', file=sys.stderr)
            return 2
        args = args[:idx] + args[idx + 2:]

    # Dedup against events JSONL (order_id + parent coverage)
    existing_ids, covered_pairs = load_existing_keys()

    # Fetch new history from OpenD
    new_rows, ok = fetch_history(days=days, all_status=all_status)
    added = []
    for r in new_rows:
        if r['order_id'] in existing_ids:
            continue
        parent = r.get('parent_order_id')
        if parent and (parent, r['code']) in covered_pairs:
            # leg already represented by an event written from the parent order
            continue
        added.append(r)

    # Persist FIRST (unless --dry-run), then report. The old behavior of
    # `--json` printing a preview without appending silently dropped fills
    # when scheduled tasks ran in JSON mode (root cause of the missing
    #  INTC events).
    appended = 0
    if not dry_run:
        appended = append_new_fills_to_events(added)

    if want_json:
        print(
            json.dumps(
                {
                    'new': len(added),
                    'appended': appended,
                    'dry_run': dry_run,
                    'query_ok': ok,
                    'orders': added,
                },
                ensure_ascii=False,
                indent=2,
)
)
    else:
        if appended > 0:
            print(
                f'Appended {appended} fill events to events/<YYYY-MM>/trades.jsonl',
                file=sys.stderr,
)
        elif dry_run:
            print(f'[dry-run] {len(added)} new fills (not written)', file=sys.stderr)
        else:
            print('No new fills to append.', file=sys.stderr)

    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
