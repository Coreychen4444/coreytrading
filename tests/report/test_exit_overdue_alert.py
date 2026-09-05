"""Synthetic portfolio-brief tests for unresolved demotion alerts."""
import json

import pytest

from quant.core import ledger as core_ledger
from quant.core import paths as core_paths
from quant.report import portfolio_brief as pb


def _demoted_card(qty=20.0, price=96.0, old_stop=95.25, new_stop=88.5):
    return {
        "instrument": {"type": "stock", "name": "t"},
        "status": "active",
        "broker": {"qty": qty, "current_price": price, "change_pct": -1.0},
        "agent": {
            "thesis": {"summary": "t"},
            "action": {"intent": "hold"},
            "zones": {"accumulation_zone_1": [113.0, 120.0], "stop_level": new_stop,
                      "valid_until": "2099-01-01"},
            "current_regime": {"name": "demoted_at_2026-08-12", "cap_pct_nav": 0,
                               "participation": "gate_only",
                               "source": "demoted", "since": "2026-08-19"},
            "regime_history": [
                {"date": "2026-08-12", "direction": "up", "level": 100.0},
                {"date": "2026-08-19", "direction": "down", "level": old_stop,
                 "reason": f"收盘 94.0 < 卡片 stop {old_stop:g} 连续 1 日",
                 "cap_pct_nav": 0},
            ],
        },
    }


@pytest.fixture
def brief(tmp_path, monkeypatch):
    def _run(positions=None, events=None, scores=None, closes=None):
        path = tmp_path / "positions.json"
        path.write_text(json.dumps({"tickers": positions or {}}), encoding="utf-8")
        monkeypatch.setattr(pb, "STATE_POSITIONS_PATH", path)
        # 预算/敞口使用同一份合成账本，不能回读 book 的默认私有路径。
        monkeypatch.setattr(pb.book, "POSITIONS_PATH", path)
        wl = tmp_path / "watchlist.json"
        wl.write_text(json.dumps({"tickers": {}}), encoding="utf-8")
        monkeypatch.setattr(pb, "STATE_WATCHLIST_PATH", wl)
        monkeypatch.setattr(pb, "lot_drift", lambda *a, **k: [])
        monkeypatch.setattr(pb.earnings, "load_calendar",
                            lambda *a, **k: {"as_of": pb.et_today(), "tickers": {}})
        from quant.decision import exit_guard as _eg
        monkeypatch.setattr(_eg, "run_checks", lambda **k: {
            "summary_lines": [], "account": None, "guards": {},
            "one_shot": [], "persistent": []})
        events_dir = tmp_path / "events"
        (events_dir / "2026-08").mkdir(parents=True, exist_ok=True)
        (events_dir / "2026-08" / "trades.jsonl").write_text(
            "\n".join(json.dumps(e) for e in (events or [])), encoding="utf-8")
        monkeypatch.setattr(pb, "EVENTS_DIR", events_dir)
        # 判别式的懒加载路径: ledger.iter_events 用 ledger 模块级 EVENTS_DIR,
        # scores 用 paths.SCORES_PATH (call-time import) —— 两处都要指到沙箱
        monkeypatch.setattr(core_ledger, "EVENTS_DIR", events_dir)
        scores_path = tmp_path / "scores.jsonl"
        scores_path.write_text(
            "\n".join(json.dumps(s) for s in (scores or [])), encoding="utf-8")
        monkeypatch.setattr(core_paths, "SCORES_PATH", scores_path)
        nav = tmp_path / "nav_history.jsonl"
        nav.write_text("", encoding="utf-8")
        monkeypatch.setattr(pb, "NAV_HISTORY_PATH", nav)
        monkeypatch.setattr(pb.book, "NAV_HISTORY_PATH", nav)
        kdir = tmp_path / "klines"
        kdir.mkdir(exist_ok=True)
        for tk, bars in (closes or {}).items():
            (kdir / f"{tk}.jsonl").write_text(
                "\n".join(json.dumps({"time": d, "close": c}) for d, c in bars),
                encoding="utf-8")
        monkeypatch.setattr(pb, "KLINE_CACHE_DIR", kdir)
        monkeypatch.setattr(pb.book, "KLINE_CACHE_DIR", kdir)

        rows, col = pb.load_rows_from_state(path)
        return pb.build_brief(rows, col, mutate_state=False)
    return _run


def _overdue(out):
    return [t for t in out["triggers"] if t["type"] == "exit_overdue"]


def test_unresolved_demotion_is_named_with_old_and_new_stop(brief):
    out = brief(positions={"TST": _demoted_card()},
                closes={"TST": [("2026-08-18", 127.0), ("2026-08-19", 94.0),
                                 ("2026-08-20", 123.5)]})
    trig = _overdue(out)
    assert len(trig) == 1 and trig[0]["ticker"] == "TST"
    assert trig[0]["old_stop"] == 95.25 and trig[0]["new_stop"] == 88.5
    line = next(a for a in out["alerts"] if "破 stop 降级" in a)
    # 断层的两半都要说出口: 被破的旧 stop (卡上已读不到) 与降级换上的新 stop
    assert "95.25" in line and "88.5" in line
    assert "D+1" in line                      # 8/20 bar 已收 = 降级后第 1 个交易日
    assert "override" in line
    # 持仓状态卡跟着 fired (HEADLINES 有 exit_overdue 头衔)
    assert any("TST" in c for c in out["fired_cards"])


def test_sell_after_demotion_close_silences(brief):
    ev = [{"event_type": "fill", "timestamp": "2026-08-19T16:30:00-04:00",
           "ticker": "TST", "side": "SELL", "qty": 20.0, "price": 94.5,
           "order_id": "X1", "broker": "moomoo_opend"}]
    out = brief(positions={"TST": _demoted_card()}, events=ev,
                closes={"TST": [("2026-08-19", 94.0)]})
    assert _overdue(out) == []


def test_written_override_in_scores_silences(brief):
    sc = [{"date": "2026-08-20", "ticker": "TST", "score": "B", "phase": "exit",
           "notes": "override: 板块普跌回声, 等 2 日确认"}]
    out = brief(positions={"TST": _demoted_card()}, scores=sc,
                closes={"TST": [("2026-08-19", 94.0)]})
    assert _overdue(out) == []


def test_card_without_demotion_never_touches_the_ledger(brief, monkeypatch):
    """没有降级候选时判别式在读事件流之前就返回 —— 既有 brief 测试与生产轮
    不因这条新检查多付 IO (也是它们不用打事件流补丁的原因)。"""
    def _boom(*a, **k):
        raise AssertionError("无候选卡不该读事件流")
    monkeypatch.setattr(core_ledger, "iter_events", _boom)
    card = _demoted_card()
    card["agent"].pop("regime_history")
    out = brief(positions={"TST": card},
                closes={"TST": [("2026-08-19", 94.0)]})
    assert _overdue(out) == []
