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


# ── 重画不许静默擦 stop; 只传 stop = 并入不重画 ─────────────
#
# 受控重画若带价位带、有意不传 --stop-level 以
# 「保持退出约束」—— writer 整块替换, stop_level / stop_level_days 全空, applied。
# regime_transition 下行分支 `if stop is not None` 整段不评估 = 破位不降级。

def test_repaint_without_stop_on_a_card_that_has_one_aborts_before_write(upz):
    """「不传 = 保持」是调用方脑补的; writer 现在把这句说出口并拒绝写盘。"""
    mod, wl = upz
    before = wl.read_text()
    with pytest.raises(RuntimeError, match="擦除现有 stop 95") as ei:
        _run(mod, ["--ticker", "TST", "--bucket", "watchlist",
                   "--zone1", "98", "103", "--conviction", "medium"])
    assert "--stop-level 95 --stop-level-days 1" in str(ei.value)   # 保持的正确写法印在错误里
    assert wl.read_text() == before


def test_repaint_can_only_drop_a_stop_with_an_explicit_clear_flag(upz):
    mod, wl = upz
    _run(mod, ["--ticker", "TST", "--bucket", "watchlist",
               "--zone1", "98", "103", "--clear-stop", "--conviction", "medium"])
    z = _zones(wl)
    assert "stop_level" not in z and "stop_level_days" not in z and "stop_derivation" not in z
    assert z["accumulation_zone_1"] == [98.0, 103.0]


def test_clear_stop_alone_or_with_a_stop_is_rejected(upz):
    mod, wl = upz
    before = wl.read_text()
    with pytest.raises(SystemExit, match="只随带价位带的重画"):
        _run(mod, ["--ticker", "TST", "--bucket", "watchlist", "--clear-stop", "--conviction", "medium"])
    with pytest.raises(SystemExit, match="矛盾"):
        _run(mod, ["--ticker", "TST", "--bucket", "watchlist", "--zone1", "98", "103",
                   "--stop-level", "90", "--clear-stop", "--conviction", "medium"])
    assert wl.read_text() == before


def test_stop_only_call_merges_and_keeps_the_geometry(upz):
    """此前只传 --stop-level 走整块替换: z1/z2/valid_until/anchored_on 会换成只剩一条 stop 的 dict。"""
    mod, wl = upz
    _run(mod, ["--ticker", "TST", "--bucket", "watchlist",
               "--stop-level", "95.0", "--stop-level-days", "1", "--conviction", "medium"])
    z = _zones(wl)
    assert z["accumulation_zone_1"] == [100.0, 105.0] and z["accumulation_zone_2"] == [95.0, 100.0]
    assert z["valid_until"] == "2026-08-28" and z["anchored_on"]["date"] == "2026-08-14"
    assert z["stop_level"] == 95.0 and z["stop_level_days"] == 1
    assert z["stop_derivation"] == BFLOOR_NOTE          # 同值 → 来历随卡


def test_stop_only_call_with_a_new_level_drops_the_old_attrs(upz):
    """stop 换了数值 → 旧口径 / 来历描述的不再是这条线, 不随行 (与重画分支同一语义)。"""
    mod, wl = upz
    _run(mod, ["--ticker", "TST", "--bucket", "watchlist",
               "--stop-level", "90", "--conviction", "medium"])
    z = _zones(wl)
    assert z["stop_level"] == 90.0 and z["accumulation_zone_1"] == [100.0, 105.0]
    assert "stop_level_days" not in z and "stop_derivation" not in z


def test_stop_only_call_new_level_with_explicit_attrs_keeps_them(upz):
    mod, wl = upz
    _run(mod, ["--ticker", "TST", "--bucket", "watchlist",
               "--stop-level", "90", "--stop-level-days", "2",
               "--stop-derivation", "synthetic: 90 = pivot cluster", "--conviction", "medium"])
    z = _zones(wl)
    assert z["stop_level_days"] == 2 and z["stop_derivation"] == "synthetic: 90 = pivot cluster"


def test_repaint_with_the_same_stop_still_carries_days_and_derivation(upz):
    """保持的正确写法 = 传原值: days / 来历自动随卡 (回归不变)。"""
    mod, wl = upz
    _run(mod, ["--ticker", "TST", "--bucket", "watchlist",
               "--zone1", "98", "103", "--stop-level", "95.0", "--conviction", "medium"])
    z = _zones(wl)
    assert z["stop_level_days"] == 1 and z["stop_derivation"] == BFLOOR_NOTE


def test_repaint_with_a_new_stop_can_state_its_derivation(upz):
    mod, wl = upz
    _run(mod, ["--ticker", "TST", "--bucket", "watchlist",
               "--zone1", "98", "103", "--stop-level", "88.5",
               "--stop-derivation", "synthetic: 88.5 = pivot low", "--conviction", "medium"])
    z = _zones(wl)
    assert z["stop_level"] == 88.5 and z["stop_derivation"] == "synthetic: 88.5 = pivot low"
    assert "stop_level_days" not in z


def test_first_paint_without_a_stop_warns_but_writes(upz, capsys):
    """建卡就没给 stop 归 INFRA:card_missing_stop 点名, 不在 writer 硬拦 (硬拦逼人随手塞数)。"""
    mod, wl = upz
    doc = json.loads(wl.read_text())
    doc["tickers"]["NEWX"] = {"instrument": {"type": "stock"}, "broker": None, "status": "watching",
                              "agent": {"thesis": {"summary": "t", "status": "intact"}}}
    wl.write_text(json.dumps(doc), encoding="utf-8")
    _run(mod, ["--ticker", "NEWX", "--bucket", "watchlist",
               "--zone1", "50", "52", "--conviction", "medium"])
    out = capsys.readouterr().out
    assert "NEWX: zones 有 z1/z2 但无 stop_level" in out
    assert json.loads(wl.read_text())["tickers"]["NEWX"]["agent"]["zones"]["accumulation_zone_1"] == [50.0, 52.0]
