"""--entry-class 写入路径 (reversal_probe): 期权卡的 agent.entry_class 决定
exit_guard 的退出栈形状 (gate = 卡 stop, S1 关)。不传 = 保持; none = 清除。"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from quant.core import io_json, paths

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".claude/skills/trading-analysis/scripts/update_position_zones.py"


def _card():
    return {"instrument": {"type": "stock"}, "broker": None, "status": "watching",
            "agent": {"thesis": {"summary": "t", "status": "intact", "conviction": "medium",
                                 "last_reviewed": "2026-08-13T00:00:00Z"},
                      "zones": {"accumulation_zone_1": [127.0, 130.2], "stop_level": 120.0,
                                "valid_until": "2026-12-28",
                                "anchored_on": {"date": "2026-09-10", "basis": "b"}},
                      "action": {"intent": "wait_for_zone", "reason": "r"},
                      "current_regime": {"name": "R_突破_133", "cap_pct_nav": 0.4,
                                         "participation": "zone_eligible",
                                         "since": "2026-08-12", "source": "auto"}}}


@pytest.fixture
def upz(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "ENV_PATH", tmp_path / "absent.env")
    monkeypatch.setattr(io_json, "STATE_LOCK_PATH", tmp_path / ".state.lock")
    spec = importlib.util.spec_from_file_location("upz_entry_class_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setattr(sys, "argv", ["upz"])
    spec.loader.exec_module(mod)
    wl = tmp_path / "watchlist.json"
    wl.write_text(json.dumps({"tickers": {"AAOI": _card()}}), encoding="utf-8")
    pos = tmp_path / "positions.json"
    pos.write_text(json.dumps({"tickers": {}}), encoding="utf-8")
    monkeypatch.setattr(mod, "WATCHLIST_PATH", wl)
    monkeypatch.setattr(mod, "POSITIONS_PATH", pos)
    monkeypatch.setattr(mod, "KLINE_CACHE_DIR", tmp_path / "klines")
    return mod, wl


def _run(mod, argv):
    sys.argv = ["upz"] + argv
    return mod.main()


def _agent(wl):
    return json.loads(wl.read_text())["tickers"]["AAOI"]["agent"]


def test_entry_class_set_kept_and_cleared(upz):
    mod, wl = upz
    base = ["--ticker", "AAOI", "--bucket", "watchlist", "--zone1", "127", "130.2",
            "--stop-level", "120", "--conviction", "medium"]
    _run(mod, base + ["--entry-class", "reversal_probe"])
    assert _agent(wl)["entry_class"] == "reversal_probe"
    _run(mod, base)                                                # 不传 = 保持
    assert _agent(wl)["entry_class"] == "reversal_probe"
    _run(mod, base + ["--entry-class", "none"])
    assert "entry_class" not in _agent(wl)
