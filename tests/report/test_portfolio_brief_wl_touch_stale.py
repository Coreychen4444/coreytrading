"""wl_zone_touch × 机械 staleness ( 用户, MU 案)。

源案例: MU 收 910.43 破 z2 下沿 921.5 (breakdown stale, entry_guard
已判 STALE「拒绝用旧结构给计划」), 8/25 盘前快照 935.5 回到带内 —— brief 照发
📥 触碰告警且带 `实际 cap $7,460`, 邀请按一张已作废的图挂单。触碰的语义 =
「回踩进参与带」, 图作废后带已不是参与带。

防守向的 z2_breach_buy_freeze **有意不 skip** — stale 卡更该冻结 BUY
(tests/report/test_portfolio_brief_z2_freeze.py 钉它)。
"""
import json

import pytest

from quant.report import portfolio_brief as pb


def _wl_card(z1, z2, cap, price, valid_until="2099-01-01"):
    return {
        "instrument": {"type": "stock", "name": "t"},
        "status": "watching",
        "broker": {"qty": 0, "current_price": price, "change_pct": 2.75},
        "agent": {
            "thesis": {"summary": "t"},
            "action": {"intent": "watch"},
            "zones": {"accumulation_zone_1": z1, "accumulation_zone_2": z2,
                      "trim_zone": [990.0, 1008.0], "valid_until": valid_until,
                      "stop_level": 907.48},
            "current_regime": {"name": "R_突破_945", "cap_pct_nav": cap,
                               "participation": "zone_eligible",
                               "source": "card", "since": "2026-08-14"},
        },
    }


@pytest.fixture
def brief(tmp_path, monkeypatch):
    def _run(watchlist=None, klines=None):
        path = tmp_path / "positions.json"
        path.write_text(json.dumps({"tickers": {}}), encoding="utf-8")
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
        for tk, rows in (klines or {}).items():
            (kdir / f"{tk}.jsonl").write_text(
                "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        monkeypatch.setattr(pb, "KLINE_CACHE_DIR", kdir)

        rows, col = pb.load_rows_from_state(path)
        return pb.build_brief(rows, col, mutate_state=False)
    return _run


def _touches(out):
    return [t for t in out["triggers"] if t["type"] == "wl_zone_touch"]


_KL_BREAKDOWN = [
    {"time": "2026-08-21 00:00:00", "close": 966.78, "high": 970.0, "low": 960.0},
    {"time": "2026-08-24 00:00:00", "close": 910.43, "high": 968.0, "low": 905.0},
]
_KL_FRESH = [
    {"time": "2026-08-21 00:00:00", "close": 966.78, "high": 970.0, "low": 960.0},
    {"time": "2026-08-24 00:00:00", "close": 945.0, "high": 968.0, "low": 940.0},
]


def test_stale_card_touch_is_suppressed(brief):
    """收盘破 z2 (breakdown stale) 后盘前快照回到带内 → 不发 📥 触碰。MU 实况。"""
    out = brief(watchlist={"MU": _wl_card([940.5, 950.0], [921.5, 940.5],
                                          cap=0.4, price=935.5)},
                klines={"MU": _KL_BREAKDOWN})
    assert _touches(out) == []
    assert not any("📥 MU" in a for a in out["alerts"])


def test_fresh_card_touch_still_fires(brief):
    out = brief(watchlist={"MU": _wl_card([940.5, 950.0], [921.5, 940.5],
                                          cap=0.4, price=935.5)},
                klines={"MU": _KL_FRESH})
    trig = _touches(out)
    assert len(trig) == 1 and trig[0]["ticker"] == "MU"


def test_valid_until_expiry_also_suppresses_touch(brief):
    """时间腿同权: valid_until 过期的卡同样不该发触碰 (同一判别式, 不另开口径)。"""
    out = brief(watchlist={"MU": _wl_card([940.5, 950.0], [921.5, 940.5],
                                          cap=0.4, price=935.5,
                                          valid_until="2026-01-01")},
                klines={"MU": _KL_FRESH})
    assert _touches(out) == []
