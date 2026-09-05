"""INFRA:card_missing_archetype —— 画了卡没归估值原型。

新候选进自选后卡都画完了, 估值层人工区没有它们, 周复盘估值一览只能印 ✗。只报不拦 (判断层零授权)。
"""
import json

import pytest

from quant.report import portfolio_brief as pb


def _wl_card(with_zones=True):
    return {
        "status": "watching", "moomoo_listed": True,
        "instrument": {"type": "stock", "name": "t"},
        "broker": {"qty": 0, "current_price": 100.0, "change_pct": 0.5},
        "agent": {"thesis": {"summary": "t"}, "action": {"intent": "watch"},
                  **({"zones": {"accumulation_zone_1": [90.0, 95.0], "accumulation_zone_2": [80.0, 90.0],
                                "valid_until": "2099-01-01"},
                      "current_regime": {"name": "R", "cap_pct_nav": 0.4, "participation": "zone_eligible",
                                         "source": "card", "since": "2026-08-01"}} if with_zones else {})},
    }


@pytest.fixture
def brief(tmp_path, monkeypatch):
    def _run(watchlist, inputs):
        pos = tmp_path / "positions.json"
        pos.write_text(json.dumps({"tickers": {}}), encoding="utf-8")
        wl = tmp_path / "watchlist.json"
        wl.write_text(json.dumps({"tickers": watchlist}), encoding="utf-8")
        inp = tmp_path / "inputs.json"
        if inputs is not None:
            inp.write_text(json.dumps(inputs), encoding="utf-8")
        elif inp.exists():
            inp.unlink()
        monkeypatch.setattr(pb, "STATE_POSITIONS_PATH", pos)
        # 预算/敞口使用同一份合成账本，不能回读 book 的默认私有路径。
        monkeypatch.setattr(pb.book, "POSITIONS_PATH", pos)
        monkeypatch.setattr(pb, "STATE_WATCHLIST_PATH", wl)
        monkeypatch.setattr(pb, "VALUATION_INPUTS_PATH", inp)
        monkeypatch.setattr(pb, "lot_drift", lambda *a, **k: [])
        monkeypatch.setattr(pb.earnings, "load_calendar",
                            lambda *a, **k: {"as_of": pb.et_today(), "tickers": {}})
        from quant.decision import exit_guard as _eg
        monkeypatch.setattr(_eg, "run_checks", lambda **k: {
            "summary_lines": [], "account": None, "guards": {}, "one_shot": [], "persistent": []})
        events = tmp_path / "events"
        events.mkdir(exist_ok=True)
        monkeypatch.setattr(pb, "EVENTS_DIR", events)
        nav = tmp_path / "nav_history.jsonl"
        nav.write_text("", encoding="utf-8")
        monkeypatch.setattr(pb, "NAV_HISTORY_PATH", nav)
        monkeypatch.setattr(pb.book, "NAV_HISTORY_PATH", nav)
        kdir = tmp_path / "klines"
        kdir.mkdir(exist_ok=True)
        monkeypatch.setattr(pb, "KLINE_CACHE_DIR", kdir)
        monkeypatch.setattr(pb.book, "KLINE_CACHE_DIR", kdir)
        return pb.build_brief([], "代码", mutate_state=False)
    return _run


def _hits(out):
    return [t for t in out["triggers"] if t["type"] == "card_missing_archetype"]


def test_painted_card_without_archetype_is_named_with_the_fix_command(brief):
    out = brief({"AVGO": _wl_card(), "BE": _wl_card(with_zones=False)},
                {"tickers": {"MU": {"archetype": "semi_cyclical", "rationale": "x"}}})
    assert len(_hits(out)) == 1 and _hits(out)[0]["value"] == 1          # BE 空 stub 归 autopaint, 不进
    line = next(a for a in out["alerts"] if "未归原型" in a)
    assert "AVGO" in line and "BE" not in line and "archetypes.py set" in line and "零授权" in line


def test_silent_when_every_painted_card_has_an_archetype_or_inputs_file_is_absent(brief):
    out = brief({"AVGO": _wl_card()}, {"tickers": {"AVGO": {"archetype": "platform", "rationale": "x"}}})
    assert _hits(out) == []
    out = brief({"AVGO": _wl_card()}, None)                                # 人工区文件不存在 = 全缺, 照报
    assert _hits(out) and "AVGO" in next(a for a in out["alerts"] if "未归原型" in a)
