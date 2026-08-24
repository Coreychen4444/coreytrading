"""Synthetic coverage for the canonical authorization-card merge."""
from quant.core.holdings import stamping_cards


def _pos(qty=0.0, exit_info=True, zones=None):
    body = {"instrument": {"type": "stock"}, "broker": {"qty": qty},
            "agent": {"zones": zones or {}}}
    if exit_info:
        body["exit_info"] = {"closed_at": "2026-07-16T10:00:00-04:00"}
        body["status"] = "closed"
    return body


def _wl(zones=None):
    return {"instrument": {"type": "stock"}, "broker": {"qty": 0.0},
            "agent": {"zones": zones if zones is not None
                      else {"accumulation_zone_1": [90.0, 95.0]},
                      "current_regime": {"name": "R_test", "cap_pct_nav": 0.4}}}


def test_dead_archived_card_yields_to_live_candidate():
    """A flat archived campaign yields to a zoned candidate campaign."""
    out = stamping_cards({"tickers": {"TST": _pos(qty=0.0, exit_info=True)}},
                         {"tickers": {"TST": _wl()}})
    assert out["TST"]["agent"]["current_regime"]["cap_pct_nav"] == 0.4


def test_live_position_card_always_wins():
    """促升后 positions 是真源 (无 exit_info) —— 即便 watchlist 还留着旧候选卡。"""
    live = _pos(qty=5.0, exit_info=False, zones={"accumulation_zone_1": [1, 2]})
    out = stamping_cards({"tickers": {"TST": live}}, {"tickers": {"TST": _wl()}})
    assert out["TST"] is live


def test_dead_card_yields_even_after_broker_qty_refresh():
    """A refreshed quantity does not revive an archived authorization card."""
    refreshed_dead = _pos(qty=5.0, exit_info=True)
    out = stamping_cards({"tickers": {"SYN": refreshed_dead}},
                         {"tickers": {"SYN": _wl()}})
    assert out["SYN"]["agent"]["current_regime"]["cap_pct_nav"] == 0.4


def test_synthetic_underlying_row_does_not_yield():
    """qty 0 但无 exit_info (在持期权的 synthetic 正股行) 不让位 ——
    那张卡归 positions 维护, 让位判据必须认 exit_info 不认裸 qty。"""
    synth = _pos(qty=0.0, exit_info=False, zones={"accumulation_zone_1": [3, 4]})
    out = stamping_cards({"tickers": {"UND": synth}}, {"tickers": {"UND": _wl()}})
    assert out["UND"] is synth


def test_dead_card_stays_when_candidate_is_a_bare_stub():
    """候选卡没 zones (空 stub) = 没有授权可言 → 不接管, 死卡留着 (derive_auth
    照旧推不出来 → untagged 点名, 这是对的: stub 候选本来就无授权)。"""
    dead = _pos(qty=0.0, exit_info=True)
    out = stamping_cards({"tickers": {"TST": dead}},
                         {"tickers": {"TST": _wl(zones={})}})
    assert out["TST"] is dead


def test_first_entry_watchlist_leg_unchanged():
    """Without a positions card, the watchlist candidate remains authoritative."""
    out = stamping_cards({"tickers": {}}, {"tickers": {"ALT": _wl()}})
    assert out["ALT"]["agent"]["current_regime"]["cap_pct_nav"] == 0.4


def test_positions_only_ticker_passes_through():
    live = _pos(qty=3.0, exit_info=False)
    out = stamping_cards({"tickers": {"XYZ": live}}, {"tickers": {}})
    assert out["XYZ"] is live and set(out) == {"XYZ"}
