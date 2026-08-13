"""Held-position visibility and stop_level breach detection in portfolio_brief.

Both regressions are from , and they compounded:

1. load_rows_from_state skipped every ticker with status == "closed" without
   looking at qty. A re-entry leaves status at "closed" (watchlist_migrate
   only setdefault()s it), so INTC sat at qty=2 / status="closed" and was
   absent from the brief's entire pipeline — no -7.89% adverse-move alert,
   no zone signals, no stop check.

2. stop_level was only ever read by entry_guard (the entry-side VETO).
   exit_guard covers the winner side (profit ratchet + 3xATR trend line) and
   the zone signals only describe accumulation/trim bands, so a held position
   breaking the stop written on its own card produced no alert at all. INTC
   closed 92.32 against a card stop of 98.
"""
import json

import pytest

from quant.report import portfolio_brief as pb


def _position(qty=2.0, status="active", price=92.4, stop=98.0, **agent_extra):
    zones = {"stop_level": stop} if stop is not None else {}
    zones.update(agent_extra.pop("zones", {}))
    return {
        "instrument": {"type": "stock", **agent_extra.pop("instrument", {})},
        "status": status,
        "broker": {"qty": qty, "current_price": price, "change_pct": -7.81},
        "agent": {
            "thesis": {"summary": "t"},
            "action": {"intent": "hold"},
            "zones": zones,
            **agent_extra,
        },
    }


@pytest.fixture
def state(tmp_path, monkeypatch):
    """Point portfolio_brief at a throwaway positions.json + kline cache."""
    def _apply(tickers, closes=None):
        path = tmp_path / "positions.json"
        path.write_text(json.dumps({"tickers": tickers}), encoding="utf-8")
        monkeypatch.setattr(pb, "STATE_POSITIONS_PATH", path)

        kdir = tmp_path / "klines"
        kdir.mkdir(exist_ok=True)
        for ticker, bars in (closes or {}).items():
            (kdir / f"{ticker}.jsonl").write_text(
                "\n".join(json.dumps({"time": d, "close": c}) for d, c in bars),
                encoding="utf-8",
)
        monkeypatch.setattr(pb, "KLINE_CACHE_DIR", kdir)
        return path
    return _apply


# ── 1. held positions must never be filtered out ──────────────

def test_reentry_with_stale_closed_status_is_still_monitored(state):
    """The INTC regression: qty wins over a stale status flag."""
    state({"INTC": _position(qty=2.0, status="closed")})
    rows, col = pb.load_rows_from_state(pb.STATE_POSITIONS_PATH)

    assert [r[col] for r in rows] == ["INTC"]
    assert rows[0]["持有数量"] == 2.0


def test_truly_closed_position_is_skipped(state):
    """qty=0 + status=closed keeps its frozen snapshot out of the brief."""
    state({"INTC": _position(qty=0.0, status="closed")})
    rows, _ = pb.load_rows_from_state(pb.STATE_POSITIONS_PATH)

    assert rows == []


def test_zero_qty_active_position_is_skipped(state):
    """The NBIS regression (): qty wins over status downward too.

    This test used to assert the opposite — that an active zero-qty row is
    "left alone" — which pinned the defect in place. Nothing in the repo ever
    writes status="closed"; it only setdefault()s it. So status tracks whether
    a human edited the file after a close, not whether a position exists.
    NBIS was sold 16:48 ET, broker.qty went to 0.0 that night, status stayed
    "active", and a 0-share ticker led the next brief's movers at -9.82% with
    a live zone_config feeding budgets.
    """
    state({"INTC": _position(qty=0.0, status="active")})
    rows, _ = pb.load_rows_from_state(pb.STATE_POSITIONS_PATH)

    assert rows == []


def test_synthetic_underlying_survives_only_while_its_option_is_held(state):
    """stock_fetch's zero-qty quote row for an option underlying outlives the
    option, so `synthetic` alone cannot mean live — six such rows (NOK, AAOI,
    MRVL, DRAM, RKLB, AXTI) were still flagged synthetic with every option
    closed. Keep it only when some option on that underlying still has qty.
    """
    synth = _position(qty=0.0, status="closed")
    synth["broker"]["synthetic"] = True

    state({"MRVL": synth, "MRVL270115C150000": _position(qty=0.0, status="closed")})
    assert pb.load_rows_from_state(pb.STATE_POSITIONS_PATH)[0] == []

    state({"MRVL": synth, "MRVL270115C150000": _position(qty=1.0, status="active")})
    rows, col = pb.load_rows_from_state(pb.STATE_POSITIONS_PATH)
    assert sorted(r[col] for r in rows) == ["MRVL", "MRVL270115C150000"]


def test_lot_drift_flags_unmaintained_lots_after_a_close(state):
    """position-tiers.md makes lot cleanup a manual step after a fill; a manual
    step needs an alarm that rings. schema.validate_positions_doc already warns
    but only to stderr, which no scheduled task reads.
    """
    body = _position(qty=0.0, status="active")
    body["lots"] = {"base": [], "position": [{"qty": 1.0, "avg_cost": 216.0}]}
    path = state({"NBIS": body})

    drift = pb.lot_drift(path)
    assert len(drift) == 1 and "NBIS" in drift[0]

    body["lots"]["position"] = []
    path.write_text(json.dumps({"tickers": {"NBIS": body}}), encoding="utf-8")
    assert pb.lot_drift(path) == []


def test_lot_drift_flags_a_held_card_whose_lots_do_not_match_broker(state):
    """买入侧的同一个洞 (AAOI/INTC 各漏一天): 落账已由
    `quant/sync/lot_reconcile.py` 自动化, 但它遇到部分卖出 / 数量对不上会**故意
    不动手**—— 那时这条告警是唯一会被人看见的出口。少了它就又只剩 stderr。
    """
    body = _position(qty=4.0)
    body["lots"] = {"base": [], "position": [{"qty": 2.0, "avg_cost": 98.7}]}
    path = state({"INTC": body})

    (row,) = pb.lot_drift(path)
    assert "INTC" in row and "+2" in row

    body["lots"]["position"].append({"qty": 2.0, "avg_cost": 97.8})
    path.write_text(json.dumps({"tickers": {"INTC": body}}), encoding="utf-8")
    assert pb.lot_drift(path) == []


def test_lot_drift_ignores_the_structurally_exempt_layer(state):
    """base 整个不在自动化辖区内 (归属由结构授予)。报它 = 一条永远响、没有任何
    动作能清掉的告警, 而 position-tiers.md 明写这层不进任何 task 输出。"""
    body = _position(qty=2.0)
    body["instrument"]["role"] = "commemorative_hold"
    body["lots"] = {"base": [], "position": []}
    assert pb.lot_drift(state({"SPCX": body})) == []


def test_position_without_broker_block_is_skipped(state):
    body = _position()
    body["broker"] = None
    state({"INTC": body})

    assert pb.load_rows_from_state(pb.STATE_POSITIONS_PATH)[0] == []


# ── 2. stop_level must survive the v2 → legacy zone conversion ──

def test_stop_level_survives_zone_conversion(state):
    state({"INTC": _position(stop=98.0)})
    current, _, _ = pb.load_zone_maps_from_state()

    assert current["INTC"]["stop_level"] == 98.0


# ── 3. stop breach detection ──────────────────────────────────

HELD = {"qty": 2.0, "is_option": False, "synthetic": False}


def test_stop_breach_fires_on_close_below_stop(state):
    state({"INTC": _position()}, {"INTC": [("2026-07-23", 100.23),
                                           ("2026-07-24", 92.32)]})
    breach = pb.stop_breach("INTC", HELD, {"stop_level": 98.0})

    assert breach["close"] == 92.32
    assert breach["stop"] == 98.0
    assert breach["date"] == "2026-07-24"


def test_stop_breach_silent_when_close_holds(state):
    state({"NBIS": _position()}, {"NBIS": [("2026-07-24", 187.77)]})

    assert pb.stop_breach("NBIS", HELD, {"stop_level": 176.0}) is None


def test_stop_breach_is_close_based_not_intraday(state):
    """Cards say "收盘<98 全出" — an intraday wick below the stop is not a break."""
    state({"INTC": _position(price=95.0)}, {"INTC": [("2026-07-24", 99.5)]})

    assert pb.stop_breach("INTC", HELD, {"stop_level": 98.0}) is None


def test_stop_breach_ignores_unheld_ticker(state):
    state({"INTC": _position(qty=0.0)}, {"INTC": [("2026-07-24", 92.32)]})
    flat = {"qty": 0.0, "is_option": False, "synthetic": False}

    assert pb.stop_breach("INTC", flat, {"stop_level": 98.0}) is None


def test_stop_breach_skips_commemorative_hold(state):
    """SPCX is held forever by decision; it has no exit discipline to breach."""
    state({"SPCX": _position()}, {"SPCX": [("2026-07-24", 115.07)]})
    cfg = {"stop_level": 150.0, "role": "commemorative_hold"}

    assert pb.stop_breach("SPCX", {**HELD, "qty": 1.0}, cfg) is None


def test_stop_breach_without_stop_level_is_silent(state):
    state({"INTC": _position(stop=None)}, {"INTC": [("2026-07-24", 92.32)]})

    assert pb.stop_breach("INTC", HELD, {}) is None


def test_stop_breach_without_kline_cache_is_silent(state):
    """No cached bars → no opinion, rather than a false all-clear or a crash."""
    state({"INTC": _position()}, {})

    assert pb.stop_breach("INTC", HELD, {"stop_level": 98.0}) is None


# ── 4. confirmation window (卡片写 "连续 2 日" 的那几张) ─────────

def test_two_day_stop_needs_two_closes_below(state):
    """MU/AMKR/NVDA say 连续 2 日; one close under the stop is not a break."""
    state({"MU": _position()}, {"MU": [("2026-07-23", 850.0), ("2026-07-24", 795.0)]})
    cfg = {"stop_level": 800.0, "stop_level_days": 2}

    assert pb.stop_breach("MU", HELD, cfg) is None


def test_two_day_stop_fires_on_the_second_close(state):
    state({"MU": _position()}, {"MU": [("2026-07-23", 799.0), ("2026-07-24", 795.0)]})
    breach = pb.stop_breach("MU", HELD, {"stop_level": 800.0, "stop_level_days": 2})

    assert breach["close"] == 795.0
    assert breach["date"] == "2026-07-24"      # reports the confirming day
    assert breach["days"] == 2


def test_two_day_stop_resets_when_price_recovers(state):
    """Below, back above, below again — not two consecutive."""
    state({"MU": _position()}, {"MU": [("2026-07-23", 795.0), ("2026-07-24", 805.0)]})

    assert pb.stop_breach("MU", HELD, {"stop_level": 800.0, "stop_level_days": 2}) is None


def test_two_day_stop_silent_without_enough_history(state):
    state({"MU": _position()}, {"MU": [("2026-07-24", 795.0)]})

    assert pb.stop_breach("MU", HELD, {"stop_level": 800.0, "stop_level_days": 2}) is None


def test_missing_stop_level_days_defaults_to_single_close(state):
    """INTC-style cards carry no window and must stay single-day."""
    state({"INTC": _position()}, {"INTC": [("2026-07-23", 100.23), ("2026-07-24", 92.32)]})

    assert pb.stop_breach("INTC", HELD, {"stop_level": 98.0})["days"] == 1


def test_stop_level_days_survives_zone_conversion(state):
    """The writer rebuilds zones from scratch, so this field has to be carried
    through the v2 → legacy conversion or it silently disappears."""
    state({"MU": _position(stop=800.0, zones={"stop_level_days": 2})})
    current, _, _ = pb.load_zone_maps_from_state()

    assert current["MU"]["stop_level_days"] == 2


# ── 5. closed cards must stop owning zones ────────────────────
#
# : load_zone_maps_from_state read every ticker regardless of status,
# so AAOI270617C200000 (closed 7/16, valid_until 7/23) emitted ⚠ ZONE STALE
# forever, and INTC's closed position card (z1 102.5-106 / stop 98) competed
# with the watchlist card that actually owned the ticker (z1 85-89.5 / stop 84).

def test_closed_flat_card_zones_are_not_loaded(state):
    state({"AAOI270617C200000": _position(qty=0.0, status="closed", stop=130.0)})

    assert "AAOI270617C200000" not in pb.load_zone_maps_from_state()[0]


def test_closed_card_still_held_keeps_its_zones(state):
    """Same qty-wins-over-status rule as the row loader — a re-entry left at
    status='closed' must not go zone-blind."""
    state({"INTC": _position(qty=2.0, status="closed", stop=98.0)})

    assert pb.load_zone_maps_from_state()[0]["INTC"]["stop_level"] == 98.0


def test_stale_check_skips_retired_cards(state):
    """The staleness loop walks the zone map, so filtering there is what
    actually silences a retired card."""
    state({"AAOI270617C200000": _position(qty=0.0, status="closed",
                                          zones={"valid_until": "2020-01-01"})})
    current, _, _ = pb.load_zone_maps_from_state()

    assert [pb.zone_staleness(t, cfg) for t, cfg in current.items()] == []


def test_option_stop_anchored_on_underlying_uses_underlying_close(state):
    """stop_level_basis=underlying means the stock's close decides, not the
    option's own mark."""
    state({}, {"AAOI": [("2026-07-24", 100.0)], "AAOI270617C200000": [("2026-07-24", 31.0)]})
    item = {"qty": 1.0, "is_option": True, "synthetic": False, "underlying": "AAOI"}
    cfg = {"stop_level": 110.0, "stop_level_basis": "underlying"}

    breach = pb.stop_breach("AAOI270617C200000", item, cfg)
    assert breach["ref"] == "AAOI"
    assert breach["close"] == 100.0
