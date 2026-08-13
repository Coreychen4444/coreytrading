#!/usr/bin/env python3
"""order_sync.py — OpenD 在途订单 → positions.json pending_orders 对账。

为什么存在: pending_orders 字段在 schema 里但从没被任何代码维护过 —
规则让 agent 读它, 实际永远是 [] 或 stale (审计发现的结构性缺口)。
本脚本拉 OpenD 当前未成交订单 (SUBMITTED/WAITING/SUBMITTING),
按 ticker 写回 pending_orders, 已成交/撤销的自动消失。

CLI:
    .venv/moomoo/bin/python3 quant/sync/order_sync.py [--json]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 直接 `python3 quant/<层>/<模块>.py` 时让 `quant.*` 可 import

from quant.core.paths import add_openapi_path  # noqa: E402

from quant.core import env  # noqa: F401  (.env → os.environ)

import argparse
import json
import os

from quant.sync import state_io  # noqa: E402
from quant.core.occ import strip_us_prefix  # noqa: E402
from quant.core.timeutil import et_naive_to_iso, now_utc_iso  # noqa: E402

add_openapi_path()  # openapi skill scripts (`common`) 上 sys.path
from common import (# noqa: E402
    create_trade_context, safe_close, safe_float, safe_get, format_enum,
    get_default_acc_id, get_default_security_firm, parse_trd_env, RET_OK,
)

REAL = parse_trd_env('REAL')
OPEN_STATUSES = {'SUBMITTED', 'SUBMITTING', 'WAITING_SUBMIT', 'FILLED_PART'}


def fetch_open_orders() -> list[dict] | None:
    ctx = create_trade_context(security_firm=get_default_security_firm())
    try:
        ret, data = ctx.order_list_query(trd_env=REAL, acc_id=get_default_acc_id())
        if ret != RET_OK or data is None:
            print(f'[order_sync] order_list_query failed: {data}', file=sys.stderr)
            return None
        out = []
        if data.empty:
            return out
        for _, r in data.iterrows():
            status = format_enum(safe_get(r, 'order_status', default=''))
            if status not in OPEN_STATUSES:
                continue
            out.append({
                'order_id': str(safe_get(r, 'order_id', default='')),
                'ticker': strip_us_prefix(safe_get(r, 'code', default='')),
                'side': format_enum(safe_get(r, 'trd_side', default='')),
                'qty': safe_float(safe_get(r, 'qty', default=0)),
                'dealt_qty': safe_float(safe_get(r, 'dealt_qty', default=0)),
                'limit_price': safe_float(safe_get(r, 'price', default=0)),
                'status': status,
                'created': et_naive_to_iso(safe_get(r, 'create_time', default='')),
                'tif': format_enum(safe_get(r, 'time_in_force', default='')),
            })
        return out
    finally:
        safe_close(ctx)


def main() -> int:
    ap = argparse.ArgumentParser(description='OpenD 在途订单对账 → pending_orders')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args()

    orders = fetch_open_orders()
    if orders is None:
        return 1

    by_ticker: dict[str, list[dict]] = {}
    for o in orders:
        by_ticker.setdefault(o['ticker'], []).append({k: v for k, v in o.items() if k != 'ticker'})

    with state_io.state_lock():
        doc = state_io.read_positions()
        changed = []
        for tk, body in doc['tickers'].items():
            new = by_ticker.get(tk, [])
            if body.get('pending_orders') != new:
                body['pending_orders'] = new
                changed.append(tk)
        orphan = sorted(set(by_ticker) - set(doc['tickers']))
        if changed:
            doc['generated_at'] = now_utc_iso()
            state_io.write_positions(doc)

    result = {
        'open_orders': len(orders),
        'tickers_updated': changed,
        'orphan_order_tickers': orphan,  # 挂单标的不在 positions — 提示需要 watchlist promote 或人工看
    }
    if a.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"[order_sync] {len(orders)} open orders | updated: {changed or '-'} | orphan tickers: {orphan or '-'}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
