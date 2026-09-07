#!/usr/bin/env python3
"""Compute implied move for a binary catalyst (earnings / FDA / FOMC / etc).

Usage:
  python3 quant/options/implied_move.py US.AAOI                          # 0a — anchor = current spot
  python3 quant/options/implied_move.py US.AAOI --anchor-close            # 0b — anchor = last close
  python3 quant/options/implied_move.py US.AAOI --json                    # JSON output
  python3 quant/options/implied_move.py US.AAOI --stop 130 --strike 140   # add stop/strike checks
  python3 quant/options/implied_move.py US.AAOI --weekly-expiry  # explicit weekly expiry

Methodology:
  implied_move_pct = ATM_straddle_mid / anchor_price
  upper = anchor × (1 + implied_move_pct)
  lower = anchor × (1 - implied_move_pct)

Step 0a: anchor = current spot (decision-time view, includes pre-drift + event)
Step 0b: anchor = last close (pre-event view, only event move)

The public schema is trade/strategy/entry-timing-ev-framework.example.md;
active methodology belongs in the ignored private copy without `.example`.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 直接 `python3 quant/<层>/<模块>.py` 时让 `quant.*` 可 import

from quant.core.paths import add_openapi_path  # noqa: E402

from quant.core import env  # noqa: F401  (.env → os.environ)
from quant.core.timeutil import ET

import argparse
import json
import os
from datetime import datetime, timedelta

add_openapi_path()  # openapi skill scripts (`common`) 上 sys.path
from common import create_quote_context, safe_close, RET_OK


def find_nearest_weekly_expiry(ctx, ticker: str, target_date: datetime | None = None) -> str | None:
    """Find the nearest weekly option expiry >= target_date (default: ET today + 1).

    : 此前用本地 datetime.now()+1d (SGT) 与到期日 00:00 整点比较 ——
    SGT 晚间 (= 美股盘中) 跑时 target 落在次日 23:00, 把次日到期的事件周链比没了
    (AAOI print 日 8/7 链被无声跳到 8/14, 事件 IM 21.8% vs 真 16.4%)。
    到期日是 ET 日历概念: 用 ET 日期、整日粒度比较。
    """
    if target_date is None:
        target = datetime.now(ET).date() + timedelta(days=1)
    else:
        target = target_date.date() if isinstance(target_date, datetime) else target_date
    ret, data = ctx.get_option_expiration_date(ticker)
    if ret != RET_OK or data is None or data.empty:
        return None
    expiries = sorted(str(d) for d in data['strike_time'])
    for e in expiries:
        e_date = datetime.strptime(e, '%Y-%m-%d').date()
        if e_date >= target:
            return e
    return None


def find_atm_strike(chain_df, spot: float) -> float:
    """Find strike closest to spot in the chain."""
    chain_df = chain_df.copy()
    chain_df['dist'] = (chain_df['strike_price'] - spot).abs()
    return chain_df.sort_values('dist').iloc[0]['strike_price']


def compute_implied_move(
    ticker: str,
    anchor_close: bool = False,
    anchor_price: float | None = None,
    weekly_expiry: str | None = None,
    your_stop: float | None = None,
    your_strike: float | None = None,
):
    ctx = create_quote_context()
    try:
        # 1. Get spot + last close
        ret, snap = ctx.get_market_snapshot([ticker])
        if ret != RET_OK or snap is None or snap.empty:
            return {'error': f'snapshot failed for {ticker}'}
        row = snap.iloc[0]
        last_price = float(row['last_price'])
        prev_close = float(row['prev_close_price'])

        # 2. Anchor selection per Step 0a/0b
        if anchor_price is not None:
            anchor = anchor_price
            anchor_label = f'manual override = ${anchor_price}'
        elif anchor_close:
            anchor = prev_close
            anchor_label = 'last_close from broker (Step 0b approximation)'
        else:
            anchor = last_price
            anchor_label = 'current_spot (Step 0a)'

        # 3. Find weekly expiry (covers earnings reaction)
        if not weekly_expiry:
            weekly_expiry = find_nearest_weekly_expiry(ctx, ticker)
        if not weekly_expiry:
            return {'error': 'no weekly expiry found'}

        # 4. Pull option chain for that expiry
        ret, chain = ctx.get_option_chain(ticker, start=weekly_expiry, end=weekly_expiry)
        if ret != RET_OK or chain is None or chain.empty:
            return {'error': f'no chain for expiry {weekly_expiry}'}

        calls = chain[chain['option_type'] == 'CALL'].copy()
        puts = chain[chain['option_type'] == 'PUT'].copy()
        if calls.empty or puts.empty:
            return {'error': 'incomplete call/put chain'}

        atm_strike = find_atm_strike(calls, anchor)
        atm_call = calls[calls['strike_price'] == atm_strike].iloc[0]
        atm_put = puts[puts['strike_price'] == atm_strike].iloc[0]

        # 5. Get bid/ask for ATM call + ATM put
        ret, opt_snap = ctx.get_market_snapshot([atm_call['code'], atm_put['code']])
        if ret != RET_OK or opt_snap is None or opt_snap.empty:
            return {'error': 'option snapshot failed'}

        call_row = opt_snap[opt_snap['code'] == atm_call['code']].iloc[0]
        put_row = opt_snap[opt_snap['code'] == atm_put['code']].iloc[0]

        call_bid = float(call_row['bid_price'])
        call_ask = float(call_row['ask_price'])
        put_bid = float(put_row['bid_price'])
        put_ask = float(put_row['ask_price'])

        if call_bid <= 0 or call_ask <= 0 or put_bid <= 0 or put_ask <= 0:
            # Fallback to last_price
            call_mid = float(call_row['last_price'])
            put_mid = float(put_row['last_price'])
        else:
            call_mid = (call_bid + call_ask) / 2
            put_mid = (put_bid + put_ask) / 2

        straddle = call_mid + put_mid
        implied_move_pct = straddle / anchor
        upper = anchor * (1 + implied_move_pct)
        lower = anchor * (1 - implied_move_pct)

        # 6. Build result
        result = {
            'ticker': ticker,
            'anchor_mode': anchor_label,
            'anchor_price': round(anchor, 4),
            'last_price': round(last_price, 4),
            'prev_close': round(prev_close, 4),
            'weekly_expiry': weekly_expiry,
            'atm_strike': round(atm_strike, 2),
            'atm_call_mid': round(call_mid, 2),
            'atm_put_mid': round(put_mid, 2),
            'straddle': round(straddle, 2),
            'implied_move_pct': round(implied_move_pct * 100, 2),
            'upper_bound': round(upper, 2),
            'lower_bound': round(lower, 2),
            'iv_call': float(call_row.get('option_implied_volatility', 0)),
            'iv_put': float(put_row.get('option_implied_volatility', 0)),
        }

        # 7. Apply 4 hard checks
        checks = {}

        # Stop stress test
        if your_stop is not None:
            buffer_pct = (lower - your_stop) / your_stop * 100
            if your_stop < lower:
                if buffer_pct > 5:
                    checks['stop_stress'] = f'PASS (your_stop ${your_stop} < lower ${round(lower,2)}, buffer {round(buffer_pct,1)}%)'
                else:
                    checks['stop_stress'] = f'WARN (your_stop ${your_stop} < lower ${round(lower,2)} but buffer {round(buffer_pct,1)}% < 5%, size 减半 or restrike)'
            else:
                checks['stop_stress'] = f'FAIL (your_stop ${your_stop} >= lower ${round(lower,2)}, market priced worst-case 触发 your stop)'

        # Strike guard (DITM)
        if your_strike is not None:
            if your_strike < lower * 0.95:
                checks['strike_guard'] = f'PASS (DITM strike ${your_strike} < lower×0.95 ${round(lower*0.95,2)}, even worst case still ITM)'
            elif your_strike < lower:
                checks['strike_guard'] = f'BORDERLINE (strike ${your_strike} ∈ [lower×0.95 ${round(lower*0.95,2)}, lower ${round(lower,2)}], borderline worst case)'
            else:
                checks['strike_guard'] = f'CAUTION (strike ${your_strike} >= lower ${round(lower,2)}, exposed to worst case OTM)'

        # Size modifier
        m = implied_move_pct * 100
        if m < 8:
            checks['size_modifier'] = f'standard size (implied move {round(m,1)}% < 8%)'
        elif m < 15:
            checks['size_modifier'] = f'-25% size (implied move {round(m,1)}% in 8-15%)'
        else:
            checks['size_modifier'] = f'-50% size or skip (implied move {round(m,1)}% > 15%)'

        result['checks'] = checks
        return result

    finally:
        safe_close(ctx)


def fmt_human(r: dict) -> str:
    if 'error' in r:
        return f"ERROR: {r['error']}"
    out = []
    out.append(f"=== {r['ticker']} Implied Move ===")
    out.append(f"Anchor: {r['anchor_mode']} = ${r['anchor_price']}")
    out.append(f"Spot: ${r['last_price']}, Prev close: ${r['prev_close']}")
    out.append(f"Weekly expiry: {r['weekly_expiry']}, ATM strike: ${r['atm_strike']}")
    out.append(f"ATM call mid: ${r['atm_call_mid']}, ATM put mid: ${r['atm_put_mid']}")
    out.append(f"Straddle: ${r['straddle']}, IV (call/put): {r['iv_call']:.1f}% / {r['iv_put']:.1f}%")
    out.append(f"")
    out.append(f"Implied move: ±{r['implied_move_pct']}%")
    out.append(f"Upper bound: ${r['upper_bound']}")
    out.append(f"Lower bound: ${r['lower_bound']}")
    if 'checks' in r and r['checks']:
        out.append(f"")
        out.append(f"--- Hard checks ---")
        for k, v in r['checks'].items():
            out.append(f"  {k}: {v}")
    return '\n'.join(out)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('ticker', help='Ticker (e.g., US.AAOI)')
    p.add_argument('--anchor-close', action='store_true', help='Step 0b: anchor = broker prev_close (last completed close)')
    p.add_argument('--anchor-price', type=float, help='Manual anchor override (e.g., specific pre-event close)')
    p.add_argument('--weekly-expiry', help='Explicit weekly expiry YYYY-MM-DD (default: nearest after today)')
    p.add_argument('--stop', type=float, help='Your stop level for stress test')
    p.add_argument('--strike', type=float, help='Your DITM strike for guard check')
    p.add_argument('--json', action='store_true', help='JSON output')
    args = p.parse_args()

    r = compute_implied_move(
        args.ticker,
        anchor_close=args.anchor_close,
        anchor_price=args.anchor_price,
        weekly_expiry=args.weekly_expiry,
        your_stop=args.stop,
        your_strike=args.strike,
)

    if args.json:
        print(json.dumps(r, indent=2, ensure_ascii=False))
    else:
        print(fmt_human(r))

    return 0 if 'error' not in r else 1


if __name__ == '__main__':
    raise SystemExit(main())
