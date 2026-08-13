from quant.core.ledger import build_episodes


def _e(ts, ticker, side, qty, price, etype=None, **extra):
    return {
        "event_type": etype or ("fill" if side in ("BUY", "SELL_SHORT") else "close"),
        "timestamp": ts, "ticker": ticker, "side": side,
        "qty": qty, "price": price, "order_id": f"T{ts}{side}{qty}",
        **extra,
    }


def test_stock_round_trip_realized():
    evs = [
        _e("2026-04-10T10:00:00-04:00", "NOK", "BUY", 50, 9.48),
        _e("2026-04-14T10:00:00-04:00", "NOK", "BUY", 50, 10.40),
        _e("2026-05-15T15:48:00-04:00", "NOK", "SELL", 100, 13.96),
    ]
    eps = build_episodes(evs)["NOK"]
    assert len(eps) == 1
    ep = eps[0]
    assert ep["status"] == "closed"
    # (13.96-9.48)*50 + (13.96-10.40)*50 = 224 + 178 = 402
    assert abs(ep["realized_pnl"] - 402.0) < 1e-6
    assert ep["qty_peak"] == 100


def test_option_multiplier_applied():
    evs = [
        _e("2026-06-09T11:46:00-04:00", "INTC260612P100000", "BUY", 2, 1.59),
        _e("2026-06-10T09:31:00-04:00", "INTC260612P100000", "SELL", 1, 1.2),
        _e("2026-06-10T09:32:00-04:00", "INTC260612P100000", "SELL", 1, 1.3),
    ]
    ep = build_episodes(evs)["INTC260612P100000"][0]
    assert ep["status"] == "closed"
    # ((1.2-1.59) + (1.3-1.59)) * 1 * 100 = -68
    assert abs(ep["realized_pnl"] - (-68.0)) < 1e-6


def test_partial_close_keeps_episode_open():
    evs = [
        _e("2026-01-01T10:00:00-05:00", "MU", "BUY", 10, 100.0),
        _e("2026-02-01T10:00:00-05:00", "MU", "SELL", 4, 120.0),
    ]
    ep = build_episodes(evs)["MU"][0]
    assert ep["status"] == "open"
    assert abs(ep["realized_pnl"] - 80.0) < 1e-6  # fees not yet deducted until close


def test_fifo_order():
    evs = [
        _e("2026-01-01T10:00:00-05:00", "X", "BUY", 5, 10.0),
        _e("2026-01-02T10:00:00-05:00", "X", "BUY", 5, 20.0),
        _e("2026-01-03T10:00:00-05:00", "X", "SELL", 5, 30.0),  # consumes the $10 lot
        _e("2026-01-04T10:00:00-05:00", "X", "SELL", 5, 30.0),
    ]
    ep = build_episodes(evs)["X"][0]
    assert abs(ep["realized_pnl"] - (100.0 + 50.0)) < 1e-6


def test_orphan_close_marks_unknown_basis():
    evs = [_e("2026-05-13T10:00:00-04:00", "LITE", "SELL", 1.5, 1040.0)]
    ep = build_episodes(evs, warn=lambda m: None)["LITE"][0]
    assert ep["orphan"] is True
    assert ep["realized_pnl"] is None


def test_fees_reduce_realized_on_close():
    evs = [
        _e("2026-01-01T10:00:00-05:00", "X", "BUY", 10, 10.0, fees=1.0),
        _e("2026-01-02T10:00:00-05:00", "X", "SELL", 10, 11.0, fees=1.0),
    ]
    ep = build_episodes(evs)["X"][0]
    assert abs(ep["realized_pnl"] - (10.0 - 2.0)) < 1e-6


def test_expire_closes_at_zero():
    evs = [
        _e("2026-05-01T10:00:00-04:00", "SMR260618C20000", "BUY", 10, 0.86),
        {"event_type": "expire", "timestamp": "2026-06-18T16:00:00-04:00",
         "ticker": "SMR260618C20000", "qty": 10, "price": None, "order_id": "EXP"},
    ]
    ep = build_episodes(evs)["SMR260618C20000"][0]
    assert ep["status"] == "closed"
    assert abs(ep["realized_pnl"] - (-860.0)) < 1e-6


def test_two_episodes_split_on_flat():
    evs = [
        _e("2026-01-01T10:00:00-05:00", "X", "BUY", 5, 10.0),
        _e("2026-01-05T10:00:00-05:00", "X", "SELL", 5, 12.0),
        _e("2026-02-01T10:00:00-05:00", "X", "BUY", 3, 20.0),
    ]
    eps = build_episodes(evs)["X"]
    assert len(eps) == 2
    assert eps[0]["status"] == "closed" and eps[1]["status"] == "open"
