"""watchlist z1 触碰告警 (H1 补盲)。

退出侧有 🔒📉🎯🚨🔓 五类告警, 入场侧此前**零类**—— 两次 briefing 之间 (最长
24h, 周末 72h) 候选票砸进吸纳带, 系统一声不吭, 唯一即时性是 moomoo 服务端
≤7 字符响铃。报价快照由 stock_fetch 顺手拉进 watchlist broker block;
本告警只给 regime cap>0 的卡 (与 z1 画线  补洞同判据)。
"""
import json

import pytest

from quant.report import portfolio_brief as pb


def _wl_card(price=212.4, cap=0.02, z1=(211.0, 214.4), z2=(205.0, 209.0),
             status="watching", role=None, broker=True):
    card = {
        "status": status, "moomoo_listed": True,
        "instrument": {"type": "stock", "name": "t", **({"role": role} if role else {})},
        "agent": {
            "thesis": {"summary": "t"},
            "action": {"intent": "accumulate"},
            "current_regime": {"name": "常规参与", "cap_pct_nav": cap,
                               "participation": "zone_eligible",
                               "since": "2026-08-01", "source": "card", "reason": "t"},
            "zones": {"accumulation_zone_1": list(z1), "accumulation_zone_2": list(z2),
                      "stop_level": 200.15, "valid_until": "2099-01-01"},
        },
        "broker": ({"source": "moomoo_quote_snapshot", "qty": 0, "synthetic": False,
                    "current_price": price, "change_pct": -5.16,
                    "as_of": "2026-08-10T14:31:00+00:00"} if broker else None),
    }
    return card


@pytest.fixture
def brief(tmp_path, monkeypatch):
    def _run(wl_tickers):
        pos = tmp_path / "positions.json"
        pos.write_text(json.dumps({"tickers": {}}), encoding="utf-8")
        wl = tmp_path / "watchlist.json"
        wl.write_text(json.dumps({"tickers": wl_tickers}), encoding="utf-8")
        monkeypatch.setattr(pb, "STATE_POSITIONS_PATH", pos)
        monkeypatch.setattr(pb, "STATE_WATCHLIST_PATH", wl)
        monkeypatch.setattr(pb, "lot_drift", lambda *a, **k: [])
        # 同上: build_brief 读真日历 = 断言跟着财报季红绿 (立规)。
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
        return pb.build_brief([], "代码", mutate_state=False)
    return _run


def _touch_alerts(out):
    return [a for a in out["alerts"] if "触及吸纳带" in a]


def test_touch_inside_band_fires_with_zone_stop_and_cap(brief):
    out = brief({"NVDA": _wl_card(price=212.4)})
    (a,) = _touch_alerts(out)
    assert "NVDA" in a and "z1 211-214.4" in a
    assert "stop 200.15" in a
    assert "cap 2%" in a
    assert "zone-touch 协议" in a


def test_touch_extends_down_through_z2(brief):
    out = brief({"NVDA": _wl_card(price=206.0)})       # z2 带内
    assert len(_touch_alerts(out)) == 1


def test_cap_zero_card_never_rings(brief):
    """修复观察 (cap 0) 卡的回踩不是动作触发点 — 响铃只会邀请未授权入场,
    与 2026-08-09 z1 画线补洞同判据。变异守卫: 拿掉 cap 判断即红。"""
    out = brief({"NVDA": _wl_card(price=212.4, cap=0)})
    assert _touch_alerts(out) == []


def test_price_outside_band_is_silent(brief):
    assert _touch_alerts(brief({"NVDA": _wl_card(price=220.0)})) == []   # 上方
    assert _touch_alerts(brief({"NVDA": _wl_card(price=198.0)})) == []   # 跌穿 z2 下沿


def test_no_snapshot_no_opinion(brief):
    """broker 快照缺 (OpenD 没跑到 / 新 stub) → 不判 — 静默降级不误报。"""
    assert _touch_alerts(brief({"NVDA": _wl_card(broker=False)})) == []


def test_volume_reference_never_rings(brief):
    out = brief({"SPY": _wl_card(price=212.4, role="volume_reference")})
    assert _touch_alerts(out) == []
