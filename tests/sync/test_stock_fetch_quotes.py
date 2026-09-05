"""Quote-source selection in stock_fetch.fetch_moomoo_quotes.

Regression cover for : during a US regular session OpenD's
subscription best_price kept serving the previous night's overnight_price
(INTC best_price 105.08 vs true last 95.33, reported +4.84% when the stock
was -4.86%). Every heartbeat run during market hours wrote stale prices into
positions.json, so no >5% alert or exit guard could fire.
"""
import pathlib

import pytest

from quant.sync import stock_fetch  # noqa: E402


def test_snapshot_clock_is_aware_and_keeps_original_provenance():
    row = stock_fetch._quote_from_snapshot({"last_price": 100, "update_time": "2026-09-04 20:02:20"}, "CLOSED")
    assert row["quote_time"] == "2026-09-04T20:02:20-04:00"
    updates = stock_fetch.rows_to_broker_updates([{"代码": "SYN", "持有数量": 1, **row}])
    assert updates["SYN"]["as_of"] == row["quote_time"]
    assert updates["SYN"]["quote_time_raw"] == "2026-09-04 20:02:20"
    assert updates["SYN"]["quote_time_basis"] == "opend_snapshot_update_time"


def test_missing_observation_time_does_not_become_current_host_time(monkeypatch):
    monkeypatch.setattr(stock_fetch, "now_iso", lambda: "2026-09-05T10:00:00Z")
    row = stock_fetch._quote_from_snapshot({"last_price": 100}, "CLOSED")
    assert row["quote_time"] is None
    updates = stock_fetch.rows_to_broker_updates([{"代码": "SYN", "持有数量": 1, **row}])
    assert updates["SYN"]["as_of"] is None


@pytest.fixture
def patched(monkeypatch):
    """Stub the three OpenD calls fetch_moomoo_quotes depends on."""
    def _apply(quote, snapshot, state):
        monkeypatch.setattr(stock_fetch, 'get_quote', lambda t: quote)
        monkeypatch.setattr(stock_fetch, 'get_snapshot', lambda t: snapshot)
        monkeypatch.setattr(stock_fetch, 'get_market_state', lambda t: state)
    return _apply


# The exact OpenD payload observed at  12:29 ET.
INTC_SUB = {
    'best_price': 105.08,        # == overnight_price, stale
    'last_price': 95.3301,       # true regular-session price
    'after_price': 104.61,
    'overnight_price': 105.08,
    'prev_close': 100.23,
    'change_pct': 4.838870597625455,   # derived off the stale best_price
    'quote_time': '2026-07-24T16:29:30.978950+00:00',
}
INTC_SNAP = {
    'last_price': 95.36,
    'prev_close': 100.23,
    'change_pct': -4.858824703182684,
    'update_time': '2026-07-24 12:29:28',
}

# The exact OpenD payload observed at  16:15 ET, state
# AFTER_HOURS_BEGIN. overnight_price is Thursday night's leftover: OpenD
# never cleared it across Friday's regular session, which closed INTC at
# 92.32 (-7.89%). best_price == overnight_price here, 13.8% above the truth.
INTC_SUB_AH = {
    'best_price': 105.08,
    'overnight_price': 105.08,   # stale — predates Friday's regular session
    'after_price': 92.44,        # live after-hours print
    'last_price': 92.32,         # Friday official close
    'prev_close': 100.23,
    'change_pct': 4.838870597625455,   # derived off the stale best_price
    'quote_time': '2026-07-24T20:15:00+00:00',
}
INTC_SNAP_AH = {
    'last_price': 92.32,
    'prev_close': 100.23,
    'change_pct': -7.891849745585154,
    'update_time': '2026-07-24 16:00:01',
}


@pytest.mark.parametrize('state', sorted(stock_fetch.REGULAR_SESSION_STATES))
def test_regular_session_ignores_stale_overnight_best_price(patched, state):
    patched({'INTC': INTC_SUB}, {'INTC': INTC_SNAP}, {'INTC': state})
    row = stock_fetch.fetch_moomoo_quotes(['INTC'])['INTC']

    assert row['current_price'] == pytest.approx(95.36)
    assert row['change_pct'] < 0          # the sign flip that hid the selloff
    assert row['current_price'] != INTC_SUB['best_price']
    assert row['market_live'] is True


def test_regular_session_without_snapshot_uses_sub_last_price(patched):
    patched({'INTC': INTC_SUB}, {}, {'INTC': 'AFTERNOON'})
    row = stock_fetch.fetch_moomoo_quotes(['INTC'])['INTC']

    assert row['current_price'] == pytest.approx(95.3301)
    assert row['price_source'] == 'moomoo_last'
    # recomputed from prev_close, not the subscription's best_price-derived value
    assert row['change_pct'] == pytest.approx(-4.8887, abs=1e-3)
    assert row['change'] == pytest.approx(-4.8999, abs=1e-3)


@pytest.mark.parametrize('state', sorted(stock_fetch.EXTENDED_LIVE_STATES))
def test_extended_session_never_uses_best_price(patched, state):
    """Extended hours: use THIS session's tick, not whichever ran last.

    best_price is overnight > after > last regardless of session, so it must
    never be what an extended session resolves to.
    """
    patched({'INTC': INTC_SUB_AH}, {'INTC': INTC_SNAP_AH}, {'INTC': state})
    row = stock_fetch.fetch_moomoo_quotes(['INTC'])['INTC']

    assert row['market_live'] is True
    assert row['price_source'].startswith('moomoo_')
    # first chain field present in the payload — mirrors the fall-through
    expected = next(INTC_SUB_AH[f] for f in stock_fetch.EXTENDED_PRICE_CHAIN[state]
                    if INTC_SUB_AH.get(f) is not None)
    assert row['current_price'] == pytest.approx(expected)


def test_after_hours_uses_after_price_not_stale_overnight(patched):
    """ 16:15 ET regression.

    OpenD served a Thursday-night overnight_price of 105.08 while INTC was
    trading at 92.44 after hours — a price from before Friday's regular
    session, which had moved the stock -7.9%. stock_fetch wrote it into
    positions.json as +4.84%, so exit_guard evaluated INTC at 105.08 on the
    day it closed 92.32, below its own stop of 98, and stayed silent.
    """
    patched({'INTC': INTC_SUB_AH}, {'INTC': INTC_SNAP_AH},
            {'INTC': 'AFTER_HOURS_BEGIN'})
    row = stock_fetch.fetch_moomoo_quotes(['INTC'])['INTC']

    assert row['current_price'] == pytest.approx(92.44)
    assert row['current_price'] != INTC_SUB_AH['overnight_price']
    assert row['price_source'] == 'moomoo_after:AFTER_HOURS_BEGIN'
    # recomputed off prev_close, not the subscription's best_price-derived value
    assert row['change_pct'] == pytest.approx(-7.77, abs=1e-2)
    assert row['change_pct'] < 0          # the sign flip that hid the selloff


def test_after_hours_baseline_is_prev_close_by_design(patched):
    """After hours keeps prev_close — a decision, not the  omission.

    The tick is on today's date, so change_pct reads "today incl. after hours".
    This is the only window where a scheduled run sees the completed session's
    move (ET 16:10 postclose, ET 18:26 heartbeat both land here); switching it
    to last_price would report the after-hours drift alone and drop the day.
    用户's call  when the night states were fixed.
    """
    assert stock_fetch.EXTENDED_BASELINE_FIELD['AFTER_HOURS_BEGIN'] == 'prev_close'
    patched({'INTC': INTC_SUB_AH}, {'INTC': INTC_SNAP_AH},
            {'INTC': 'AFTER_HOURS_BEGIN'})
    row = stock_fetch.fetch_moomoo_quotes(['INTC'])['INTC']

    # 92.44 after hours against Thursday's 100.23 = Friday's -7.9% plus the
    # after-hours drift, not the +0.13% the after-hours session moved alone.
    assert row['change_pct'] == pytest.approx(-7.77, abs=1e-2)
    assert row['prev_close'] == pytest.approx(100.23)


def test_overnight_session_uses_overnight_price(patched):
    patched({'INTC': INTC_SUB_AH}, {'INTC': INTC_SNAP_AH}, {'INTC': 'NIGHT'})
    row = stock_fetch.fetch_moomoo_quotes(['INTC'])['INTC']

    assert row['current_price'] == pytest.approx(105.08)
    assert row['price_source'] == 'moomoo_overnight:NIGHT'


def test_every_extended_state_has_a_baseline():
    """The pair that broke : EXTENDED_PRICE_CHAIN picks the tick,
    EXTENDED_BASELINE_FIELD says what it is measured against. A key in one and
    not the other silently spans two sessions — OVERNIGHT fell through to
    prev_close and priced NBIS's night tick against the close from two sessions
    back. Neither table may grow a state without the other."""
    assert set(stock_fetch.EXTENDED_BASELINE_FIELD) == set(stock_fetch.EXTENDED_PRICE_CHAIN)
    for state, field in stock_fetch.EXTENDED_BASELINE_FIELD.items():
        assert field in ('prev_close', 'last_price'), (state, field)


def test_extended_session_without_its_field_falls_back(patched):
    """After hours with no after_price yet → last_price, never overnight."""
    sub = dict(INTC_SUB_AH, after_price=None)
    patched({'INTC': sub}, {'INTC': INTC_SNAP_AH}, {'INTC': 'AFTER_HOURS_BEGIN'})
    row = stock_fetch.fetch_moomoo_quotes(['INTC'])['INTC']

    assert row['current_price'] == pytest.approx(92.32)
    assert row['current_price'] != sub['overnight_price']


def test_extended_session_with_no_prices_falls_back_to_snapshot(patched):
    patched({'INTC': {'prev_close': 100.23}}, {'INTC': INTC_SNAP_AH},
            {'INTC': 'AFTER_HOURS_BEGIN'})
    row = stock_fetch.fetch_moomoo_quotes(['INTC'])['INTC']

    assert row['current_price'] == pytest.approx(92.32)
    assert row['price_source'].startswith('moomoo_live:')
    assert row['market_live'] is True


def test_no_extended_chain_offers_a_superseded_session():
    """After hours must not offer overnight: that session ran before today's
    regular session, so its price is known-superseded."""
    assert 'overnight_price' not in stock_fetch.EXTENDED_PRICE_CHAIN['AFTER_HOURS_BEGIN']
    for state, chain in stock_fetch.EXTENDED_PRICE_CHAIN.items():
        assert chain, state
        assert 'best_price' not in chain, state


def test_closed_market_uses_official_close(patched):
    patched({'INTC': INTC_SUB}, {'INTC': INTC_SNAP}, {'INTC': 'CLOSED'})
    row = stock_fetch.fetch_moomoo_quotes(['INTC'])['INTC']

    assert row['current_price'] == pytest.approx(95.36)
    assert row['price_source'].startswith('moomoo_close:')
    assert row['market_live'] is False


def test_unknown_state_defaults_to_snapshot(patched):
    """An unrecognised state must never fall through to a stale extended tick."""
    patched({'INTC': INTC_SUB}, {'INTC': INTC_SNAP}, {'INTC': 'SOME_NEW_STATE'})
    row = stock_fetch.fetch_moomoo_quotes(['INTC'])['INTC']

    assert row['current_price'] == pytest.approx(95.36)
    assert row['market_live'] is False


def test_regular_session_cold_subscription_falls_back_to_snapshot(patched):
    """Cold cache: no subscription prices at all, snapshot still wins."""
    patched({'INTC': {'prev_close': 100.23}}, {'INTC': INTC_SNAP}, {'INTC': 'MORNING'})
    row = stock_fetch.fetch_moomoo_quotes(['INTC'])['INTC']

    assert row['current_price'] == pytest.approx(95.36)
    assert row['market_live'] is True


def test_live_states_is_union_of_regular_and_extended():
    assert stock_fetch.LIVE_TRADING_STATES == (
        stock_fetch.REGULAR_SESSION_STATES | stock_fetch.EXTENDED_LIVE_STATES
)
    assert not (stock_fetch.REGULAR_SESSION_STATES & stock_fetch.EXTENDED_LIVE_STATES)


# The exact OpenD payload observed live at  08:51 ET, state
# PRE_MARKET_BEGIN. pre_price 186.12 is the live pre-market print (450k shares
# traded); overnight_price 194.74 is the Sunday-night session's tick, ~5h
# stale. prev_close has already rolled to Thu 7/30's close 188.43 — the true
# prior close 190.41 (Fri 7/31) is in last_price, and moomoo's own
# pre_change_rate -2.25% is computed against it. The old chain led with
# overnight_price against the rolled prev_close and published +3.35% while the
# stock was actually -2.25%: sign inverted in that morning's briefing.
NBIS_SUB_PRE = {
    'best_price': 194.74,
    'pre_price': 186.1241,       # live pre-market print
    'overnight_price': 194.74,   # Sunday-overnight leftover, session ended 04:00 ET
    'after_price': 186.7,        # Friday after-hours leftover
    'last_price': 190.41,        # Friday official close = true prior close
    'prev_close': 188.43,        # already rolled: Thursday's close
    'change_pct': 3.3487236639600924,  # derived off the stale best_price
    'quote_time': '2026-08-03T12:51:03.986516+00:00',
}
NBIS_SNAP_PRE = {
    'last_price': 190.41,
    'prev_close': 188.43,
    'update_time': '2026-08-03 08:51:00',
}


def test_pre_market_uses_pre_price_not_stale_overnight(patched):
    patched({'NBIS': NBIS_SUB_PRE}, {'NBIS': NBIS_SNAP_PRE},
            {'NBIS': 'PRE_MARKET_BEGIN'})
    row = stock_fetch.fetch_moomoo_quotes(['NBIS'])['NBIS']

    assert row['current_price'] == pytest.approx(186.1241)
    assert row['current_price'] != NBIS_SUB_PRE['overnight_price']
    assert row['price_source'] == 'moomoo_pre:PRE_MARKET_BEGIN'


def test_pre_market_baseline_is_true_prior_close_not_rolled_prev(patched):
    """change_pct must match moomoo's pre_change_rate (-2.25% vs 190.41), not
    +3.35% vs the rolled prev_close — the sign flip 用户 caught ."""
    patched({'NBIS': NBIS_SUB_PRE}, {'NBIS': NBIS_SNAP_PRE},
            {'NBIS': 'PRE_MARKET_BEGIN'})
    row = stock_fetch.fetch_moomoo_quotes(['NBIS'])['NBIS']

    assert row['change_pct'] == pytest.approx(-2.25, abs=0.01)
    assert row['change_pct'] < 0
    # the baseline actually used is written out, so downstream recomputation agrees
    assert row['prev_close'] == pytest.approx(190.41)


def test_pre_market_without_pre_trade_falls_back_to_overnight(patched):
    """Before the first pre-market print the contiguous overnight tick is the
    most recent trade — but the baseline stays the true prior close."""
    sub = dict(NBIS_SUB_PRE, pre_price=None)
    patched({'NBIS': sub}, {'NBIS': NBIS_SNAP_PRE}, {'NBIS': 'PRE_MARKET_BEGIN'})
    row = stock_fetch.fetch_moomoo_quotes(['NBIS'])['NBIS']

    assert row['current_price'] == pytest.approx(194.74)
    assert row['price_source'] == 'moomoo_overnight:PRE_MARKET_BEGIN'
    assert row['prev_close'] == pytest.approx(190.41)


def test_pre_market_baseline_missing_last_price_falls_back_to_prev_close(patched):
    sub = dict(NBIS_SUB_PRE, last_price=None)
    patched({'NBIS': sub}, {'NBIS': NBIS_SNAP_PRE}, {'NBIS': 'PRE_MARKET_BEGIN'})
    row = stock_fetch.fetch_moomoo_quotes(['NBIS'])['NBIS']

    assert row['current_price'] == pytest.approx(186.1241)
    assert row['prev_close'] == pytest.approx(188.43)


# The exact OpenD payload measured live at  01:33 ET, state
# OVERNIGHT. last_price is Tue 8/4's close; prev_close_price is still Mon 8/3's
# — it does not roll until the next regular session opens. moomoo's own
# overnight_change_rate is +0.877 for NBIS and -7.061 for SPCX, both computed
# against last_price. Against the un-rolled prev_close the same ticks read
# +7.12% and +1.70%: session N+1 price over session N-1 close, with all of
# Tuesday in between.
NBIS_SUB_ON = {
    'best_price': 227.72,
    'overnight_price': 227.72,   # live overnight print
    'after_price': 224.09,       # Tuesday's after-hours leftover
    'last_price': 225.74,        # Tue 8/4 official close = true prior close
    'prev_close': 212.58,        # not yet rolled: Mon 8/3's close
    'change_pct': 7.122024649073761,   # derived off best_price vs prev_close
    'quote_time': '2026-08-05T05:33:00+00:00',
}
NBIS_SNAP_ON = {
    'last_price': 225.74,
    'prev_close': 212.58,
    'change_pct': 6.190610593663562,
    'update_time': '2026-08-04 16:00:00',
}

# SPCX the same night: 8/4 AMC print, +9.43% in the session then -7.5% on the
# report. The bad baseline turned a 7.7% gap-down into +1.01% — under the >5%
# heartbeat threshold, so a night gap-down could never fire an alert.
SPCX_SUB_ON = {
    'best_price': 116.48,
    'overnight_price': 116.48,
    'after_price': 115.98,
    'last_price': 125.33,        # Tue 8/4 official close
    'prev_close': 114.53,        # not yet rolled: Mon 8/3's close
    'change_pct': 1.7026106696062167,
    'quote_time': '2026-08-05T05:33:00+00:00',
}
SPCX_SNAP_ON = {
    'last_price': 125.33,
    'prev_close': 114.53,
    'change_pct': 9.429844578713,
    'update_time': '2026-08-04 16:00:00',
}


@pytest.mark.parametrize('state', ['OVERNIGHT', 'NIGHT', 'NIGHT_OPEN'])
def test_overnight_baseline_is_prior_close_not_unrolled_prev(patched, state):
    """ 00:26 ET regression: NBIS published +5.63% against Monday's
    close while the overnight tick was -0.53% against Tuesday's."""
    patched({'NBIS': NBIS_SUB_ON}, {'NBIS': NBIS_SNAP_ON}, {'NBIS': state})
    row = stock_fetch.fetch_moomoo_quotes(['NBIS'])['NBIS']

    assert row['current_price'] == pytest.approx(227.72)
    # matches moomoo's own overnight_change_rate +0.877, not +7.12 vs prev_close
    assert row['change_pct'] == pytest.approx(0.877, abs=0.01)
    assert row['prev_close'] == pytest.approx(225.74)


def test_overnight_gap_down_survives_the_alert_threshold(patched):
    """The false negative half: SPCX -7.06% overnight read as +1.01%, under the
    >5% heartbeat trigger, so a gap-down on its own earnings night was silent."""
    patched({'SPCX': SPCX_SUB_ON}, {'SPCX': SPCX_SNAP_ON}, {'SPCX': 'OVERNIGHT'})
    row = stock_fetch.fetch_moomoo_quotes(['SPCX'])['SPCX']

    assert row['change_pct'] == pytest.approx(-7.061, abs=0.01)
    assert abs(row['change_pct']) > 5      # what portfolio_brief alerts on
    assert row['prev_close'] == pytest.approx(125.33)


def test_overnight_baseline_missing_last_price_falls_back_to_prev_close(patched):
    sub = dict(NBIS_SUB_ON, last_price=None)
    patched({'NBIS': sub}, {'NBIS': NBIS_SNAP_ON}, {'NBIS': 'OVERNIGHT'})
    row = stock_fetch.fetch_moomoo_quotes(['NBIS'])['NBIS']

    assert row['current_price'] == pytest.approx(227.72)
    assert row['prev_close'] == pytest.approx(212.58)


# ── watchlist 报价快照 (H1/E4: heartbeat 入场侧补盲) ──────

def _wl_doc(status="watching", listed=True):
    return {"schema_version": 2, "tickers": {"NVDA": {
        "status": status, "moomoo_listed": listed,
        "instrument": {"type": "stock", "name": "NVIDIA"},
        "agent": {"thesis": {"summary": "t"}, "action": {"intent": "accumulate"},
                  "zones": {"accumulation_zone_1": [211.0, 214.4]}},
        "broker": None,
    }}}


def _patch_wl(monkeypatch, doc, quotes):
    written = {}
    monkeypatch.setattr(stock_fetch.state_io, "read_watchlist",
                        lambda allow_missing=False: doc)
    monkeypatch.setattr(stock_fetch.state_io, "write_watchlist",
                        lambda d: written.update(d))
    monkeypatch.setattr(stock_fetch, "fetch_moomoo_quotes", lambda tks: quotes)
    return written


def test_watchlist_snapshot_writes_quote_with_zero_qty(monkeypatch):
    """qty 恒 0 —— watchlist_migrate 的 promote 判据读 positions 的 broker,
    但快照永远不能长出一个会被误读为持仓的形状。"""
    written = _patch_wl(monkeypatch, _wl_doc(), {"NVDA": {
        "current_price": 212.4, "prev_close": 223.96, "change_pct": -5.16,
        "quote_time": "2026-08-10T14:31:00+00:00", "price_source": "moomoo_live"}})
    n = stock_fetch.refresh_watchlist_quotes()

    assert n == 1
    b = written["tickers"]["NVDA"]["broker"]
    assert b["qty"] == 0
    assert b["current_price"] == 212.4
    assert b["source"] == "moomoo_quote_snapshot"
    # 卡片所有权字段一概不碰
    assert written["tickers"]["NVDA"]["agent"]["zones"]["accumulation_zone_1"] == [211.0, 214.4]


def test_watchlist_snapshot_skips_delisted_and_nonwatching(monkeypatch):
    for doc in (_wl_doc(status="paused"), _wl_doc(listed=False)):
        written = _patch_wl(monkeypatch, doc, {"NVDA": {"current_price": 212.4}})
        assert stock_fetch.refresh_watchlist_quotes() == 0
        assert written == {}, "不该有任何写入"


def test_watchlist_snapshot_no_quote_no_write(monkeypatch):
    written = _patch_wl(monkeypatch, _wl_doc(), {})
    assert stock_fetch.refresh_watchlist_quotes() == 0
    assert written == {}


def test_zero_absent_is_gated_on_a_trustworthy_full_position_list():
    """源码棘轮 (): `zero_absent` 只能在三个条件同时成立时打开。

    去掉任何一个都会变成"拿一份不完整/陈旧的列表去判谁不在里面" —— 而这条判定的
    动作是**把持仓数写成 0**, 下游 close_reconcile 会跟着清 lots、标 closed。
    这不是可以事后补救的写入, 所以护栏写死在源码里并在此钉住。
    """
    from pathlib import Path as _P
    src = _P("quant/sync/stock_fetch.py").read_text(encoding="utf-8")
    line = next(ln for ln in src.splitlines() if ln.strip().startswith("zero_absent ="))
    for guard in ("fresh_positions", "not filter_tickers", "not quotes_only"):
        assert guard in line, f"zero_absent 少了护栏 {guard}: {line.strip()}"
