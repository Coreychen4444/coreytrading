"""Synthetic coverage for stop confirmation metadata persistence."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from quant.core import io_json, paths

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".claude/skills/trading-analysis/scripts/update_position_zones.py"

BFLOOR_NOTE = "synthetic bfloor: 100 - 0.5×ATR20(10) = 95, close×1"


def _card():
    return {
        "instrument": {"type": "stock"}, "broker": None, "status": "watching",
        "agent": {
            "thesis": {"summary": "t", "status": "intact", "conviction": "medium",
                       "last_reviewed": "2026-08-13T00:00:00Z"},
            "zones": {"accumulation_zone_1": [100.0, 105.0],
                      "accumulation_zone_2": [95.0, 100.0],
                      "stop_level": 95.0,
                      "stop_level_days": 1,
                      "stop_derivation": BFLOOR_NOTE,
                      "valid_until": "2026-08-28",
                      "anchored_on": {"date": "2026-08-14", "basis": "b"}},
            "action": {"intent": "wait_for_zone", "reason": "r"},
            "current_regime": {"name": "R_突破_100", "cap_pct_nav": 0.4,
                               "participation": "zone_eligible",
                               "since": "2026-08-12", "source": "auto"},
        },
    }


@pytest.fixture
def upz(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "ENV_PATH", tmp_path / "absent.env")
    monkeypatch.setattr(io_json, "STATE_LOCK_PATH", tmp_path / ".state.lock")
    spec = importlib.util.spec_from_file_location("upz_stop_days_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setattr(sys, "argv", ["upz"])
    spec.loader.exec_module(mod)
    wl = tmp_path / "watchlist.json"
    wl.write_text(json.dumps({"tickers": {"TST": _card()}}), encoding="utf-8")
    pos = tmp_path / "positions.json"
    pos.write_text(json.dumps({"tickers": {}}), encoding="utf-8")
    monkeypatch.setattr(mod, "WATCHLIST_PATH", wl)
    monkeypatch.setattr(mod, "POSITIONS_PATH", pos)
    monkeypatch.setattr(mod, "KLINE_CACHE_DIR", tmp_path / "klines")
    monkeypatch.setattr(mod, "VALUATION_INPUTS_PATH", tmp_path / "inputs.json")
    return mod, wl


def _run(mod, argv):
    sys.argv = ["upz"] + argv
    return mod.main()


def _zones(wl):
    return json.loads(wl.read_text())["tickers"]["TST"]["agent"]["zones"]


def test_explicit_one_persists(upz):
    """显式 --stop-level-days 1 必须落盘 (旧守卫 >1 把它静默丢弃)。"""
    mod, wl = upz
    _run(mod, ["--ticker", "TST", "--bucket", "watchlist",
               "--zone1", "100", "105", "--stop-level", "90",
               "--stop-level-days", "1", "--conviction", "medium"])
    assert _zones(wl)["stop_level_days"] == 1


def test_repaint_same_stop_carries_days_and_derivation(upz):
    """A redraw with the same stop preserves its confirmation metadata."""
    mod, wl = upz
    _run(mod, ["--ticker", "TST", "--bucket", "watchlist",
               "--zone1", "98", "103", "--stop-level", "95.0",
               "--conviction", "medium"])
    z = _zones(wl)
    assert z["stop_level_days"] == 1
    assert z["stop_derivation"] == BFLOOR_NOTE
    assert z["accumulation_zone_1"] == [98.0, 103.0]   # 替换语义本身不变


def test_repaint_new_stop_drops_stale_attrs(upz):
    """stop 换了数值 → 旧口径/旧来历描述的不再是这条线, 不带。"""
    mod, wl = upz
    _run(mod, ["--ticker", "TST", "--bucket", "watchlist",
               "--zone1", "98", "103", "--stop-level", "90.0",
               "--conviction", "medium"])
    z = _zones(wl)
    assert "stop_level_days" not in z
    assert "stop_derivation" not in z


def test_explicit_days_beats_carried_value(upz):
    """同 stop 数值但本次显式改口径 → 显式值赢, 不被 carry 覆盖。"""
    mod, wl = upz
    _run(mod, ["--ticker", "TST", "--bucket", "watchlist",
               "--zone1", "98", "103", "--stop-level", "95.0",
               "--stop-level-days", "2", "--conviction", "medium"])
    z = _zones(wl)
    assert z["stop_level_days"] == 2
    assert z["stop_derivation"] == BFLOOR_NOTE   # 来历仍随 stop 本体保留
