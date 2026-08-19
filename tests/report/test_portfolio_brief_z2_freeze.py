"""Synthetic tests for session BUY freezes below the lower accumulation zone."""
import json

import pytest

from quant.report import portfolio_brief as pb


def _card(z1, z2, cap, price, change_pct=-1.0, qty=2.0, status="active",
          stop=None):
    body = {
        "instrument": {"type": "stock", "name": "t"},
        "status": status,
        "broker": {"qty": qty, "current_price": price, "change_pct": change_pct},
        "agent": {
            "thesis": {"summary": "t"},
            "action": {"intent": "hold"},
            "zones": {"accumulation_zone_1": z1, "accumulation_zone_2": z2,
                      "valid_until": "2099-01-01",
                      **({"stop_level": stop} if stop is not None else {})},
            "current_regime": {"name": "R_test", "cap_pct_nav": cap,
                               "participation": "zone_eligible",
                               "source": "card", "since": "2026-08-01"},
        },
    }
    return body


@pytest.fixture
def brief(tmp_path, monkeypatch):
    def _run(positions=None, watchlist=None):
        path = tmp_path / "positions.json"
        path.write_text(json.dumps({"tickers": positions or {}}), encoding="utf-8")
        monkeypatch.setattr(pb, "STATE_POSITIONS_PATH", path)
        wl = tmp_path / "watchlist.json"
        wl.write_text(json.dumps({"tickers": watchlist or {}}), encoding="utf-8")
        monkeypatch.setattr(pb, "STATE_WATCHLIST_PATH", wl)
        monkeypatch.setattr(pb, "lot_drift", lambda *a, **k: [])
        monkeypatch.setattr(pb.earnings, "load_calendar",
                            lambda *a, **k: {"as_of": pb.et_today(), "tickers": {}})
        from quant.decision import exit_guard as _eg
        monkeypatch.setattr(_eg, "run_checks", lambda **k: {
            "summary_lines": [], "account": None, "guards": {},
            "one_shot": [], "persistent": []})
        events = tmp_path / "events"
        events.mkdir(exist_ok=True)
        monkeypatch.setattr(pb, "EVENTS_DIR", events)
        nav = tmp_path / "nav_history.jsonl"
        nav.write_text("", encoding="utf-8")
        monkeypatch.setattr(pb, "NAV_HISTORY_PATH", nav)
        kdir = tmp_path / "klines"
        kdir.mkdir(exist_ok=True)
        monkeypatch.setattr(pb, "KLINE_CACHE_DIR", kdir)

        rows, col = pb.load_rows_from_state(path)
        return pb.build_brief(rows, col, mutate_state=False)
    return _run


def _freeze_triggers(out):
    return [t for t in out["triggers"] if t["type"] == "z2_breach_buy_freeze"]


# ── 持仓桶 ──────────────────────────────────────────────────────

def test_holding_below_z2_fires_buy_freeze(brief):
    out = brief(positions={"TST": _card([100.0, 105.0], [95.0, 100.0],
                                         cap=0.4, price=90.0, stop=80.0)})
    trig = _freeze_triggers(out)
    assert len(trig) == 1 and trig[0]["ticker"] == "TST"
    assert trig[0]["z2_lo"] == pytest.approx(95.0)
    line = next(a for a in out["alerts"] if "🚫 TST" in a)
    assert "BUY 冻结" in line and "等收盘" in line
    # 冻结不碰授权: 告警文本必须自己声明 cap 不在盘中动
    assert "cap 不在盘中动" in line


def test_holding_inside_zone_does_not_fire(brief):
    out = brief(positions={"ALT": _card([110.0, 115.0], [100.0, 105.0],
                                         cap=0.4, price=112.0)})
    assert _freeze_triggers(out) == []


def test_cap_zero_has_no_buy_to_freeze(brief):
    # 与 wl_zone_touch 同判据: 无授权卡的响铃只会邀请未授权动作
    out = brief(positions={"ZERO": _card([150.0, 160.0], [140.0, 150.0],
                                         cap=0, price=135.0)})
    assert _freeze_triggers(out) == []


# ── watchlist 桶 ────────────────────────────────────────────────

def test_watchlist_candidate_below_z2_fires(brief):
    out = brief(watchlist={"WAT": _card([200.0, 210.0], [185.0, 195.0],
                                         cap=0.4, price=180.0, qty=0,
                                         status="watching")})
    trig = _freeze_triggers(out)
    assert len(trig) == 1 and trig[0]["ticker"] == "WAT"


def test_held_ticker_dedups_to_positions_card(brief):
    # 促升窗内双桶同 ticker: 持仓卡 (在带内, 不该响) 是真源, watchlist 旧卡
    # (更高的旧带, 按它看是破带) 不得代它发声
    pos = _card([140.0, 148.0], [135.0, 140.0], cap=0.4, price=137.0)
    wl = _card([150.0, 158.0], [145.0, 150.0], cap=0.4, price=137.0,
               qty=0, status="watching")
    out = brief(positions={"TST": pos}, watchlist={"TST": wl})
    assert _freeze_triggers(out) == []
