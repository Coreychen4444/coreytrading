"""thesis.frame 写入路径 (基本面分析参考卖方研报骨架; 存量卡不立即迁, 重画时逐张替换)。

writer 这一侧钉四件事: frame 写入即派生 summary / 两代参数不许混写 / 结构重画不碰 frame /
上一版看点到期后换 frame 必须逐条对账。
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from quant.core import io_json, paths, thesis_frame

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".claude/skills/trading-analysis/scripts/update_position_zones.py"
LEGACY = "旧散文 thesis" + thesis_frame.NOTES_SEPARATOR + "结构注记"
BASE = ["--ticker", "SYN", "--bucket", "watchlist"]
REPAINT = ["--zone1", "127", "130.2", "--stop-level", "120"]


def _card():
    return {"instrument": {"type": "stock"}, "broker": None, "status": "watching",
            "agent": {"thesis": {"summary": LEGACY, "status": "intact", "conviction": "medium",
                                 "last_reviewed": "2026-09-01T00:00:00Z"},
                      "zones": {"accumulation_zone_1": [127.0, 130.2], "stop_level": 120.0,
                                "valid_until": "2026-12-28",
                                "anchored_on": {"date": "2026-09-10", "basis": "b"}},
                      "invalid_if": "收盘 < 120 ×1",
                      "action": {"intent": "wait_for_zone", "reason": "r"},
                      "current_regime": {"name": "R_突破_133", "cap_pct_nav": 0.4,
                                         "participation": "zone_eligible",
                                         "since": "2026-08-12", "source": "auto"}}}


def _frame(**over):
    frame = {"as_of": "2026-09-14", "hypothesis": "收入由 A 需求与 B 渗透两条腿驱动",
             "drivers": [{"name": "A 需求", "mechanism": "按用量计费", "evidence": "待核实",
                          "kpi": "A 收入占比"}],
             "bear_case": {"claim": "竞品追平", "rebuttal": "待核实", "falsifier": "A 收入环比转负"},
             "valuation": "待核实",
             "catalyst": {"event": "FQ3 财报", "date": "2026-11-05", "baseline": "待核实",
                          "watch": ["A 收入占比", "客户集中度"]},
             "sources": ["合成出处 2026-09-14"]}
    frame.update(over)
    return frame


@pytest.fixture
def upz(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "ENV_PATH", tmp_path / "absent.env")
    monkeypatch.setattr(io_json, "STATE_LOCK_PATH", tmp_path / ".state.lock")
    spec = importlib.util.spec_from_file_location("upz_thesis_frame_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setattr(sys, "argv", ["upz"])
    spec.loader.exec_module(mod)
    wl = tmp_path / "watchlist.json"
    wl.write_text(json.dumps({"tickers": {"SYN": _card()}}), encoding="utf-8")
    pos = tmp_path / "positions.json"
    pos.write_text(json.dumps({"tickers": {}}), encoding="utf-8")
    monkeypatch.setattr(mod, "WATCHLIST_PATH", wl)
    monkeypatch.setattr(mod, "POSITIONS_PATH", pos)
    monkeypatch.setattr(mod, "KLINE_CACHE_DIR", tmp_path / "klines")
    monkeypatch.setattr(mod, "VALUATION_INPUTS_PATH", tmp_path / "inputs.json")
    monkeypatch.setattr(mod, "et_today", lambda: "2026-09-15")
    return mod, wl


def _run(mod, argv):
    sys.argv = ["upz"] + argv
    return mod.main()


def _agent(wl):
    return json.loads(wl.read_text())["tickers"]["SYN"]["agent"]


def test_frame_write_derives_summary_and_leaves_zones_alone(upz):
    mod, wl = upz
    zones_before = _agent(wl)["zones"]
    assert _run(mod, BASE + ["--thesis-frame", json.dumps(_frame())]) == 0
    agent = _agent(wl)
    assert agent["thesis"]["frame"] == _frame()
    assert agent["thesis"]["summary"] == _frame()["hypothesis"]     # 旧散文与 [notes] 块一并被替换
    assert agent["zones"] == zones_before


@pytest.mark.parametrize("argv", [
    ["--thesis", "x", "--thesis-frame", json.dumps(_frame())],
    ["--thesis-frame", json.dumps(_frame(rating="OW"))],
    ["--thesis-frame", json.dumps(_frame(as_of="2026-09-16"))],
])
def test_rejected_frames_never_touch_the_file(upz, argv):
    mod, wl = upz
    before = wl.read_bytes()
    with pytest.raises(ValueError):
        _run(mod, BASE + argv)
    assert wl.read_bytes() == before


def test_prose_thesis_flag_is_refused_once_the_card_has_a_frame(upz):
    mod, wl = upz
    _run(mod, BASE + ["--thesis-frame", json.dumps(_frame())])
    before = wl.read_bytes()
    with pytest.raises(ValueError, match="thesis-frame"):
        _run(mod, BASE + ["--thesis", "另写一段散文"])
    assert wl.read_bytes() == before


def test_legacy_prose_card_still_takes_the_thesis_flag(upz):
    mod, wl = upz
    assert _run(mod, BASE + ["--thesis", "新散文"]) == 0
    assert _agent(wl)["thesis"]["summary"] == "新散文"
    assert "frame" not in _agent(wl)["thesis"]


def test_structural_repaint_and_notes_keep_the_frame(upz):
    mod, wl = upz
    _run(mod, BASE + ["--thesis-frame", json.dumps(_frame())])
    assert _run(mod, BASE + REPAINT + ["--notes", "结构注记"]) == 0
    thesis = _agent(wl)["thesis"]
    assert thesis["frame"] == _frame()
    assert thesis["summary"] == _frame()["hypothesis"] + thesis_frame.NOTES_SEPARATOR + "结构注记"


def test_reached_catalyst_must_be_reviewed_before_the_frame_is_replaced(upz):
    mod, wl = upz
    prior = _frame(catalyst={"event": "FQ2 财报", "date": "2026-09-10", "baseline": "待核实",
                             "watch": ["A 收入占比", "客户集中度"]})
    doc = json.loads(wl.read_text())
    doc["tickers"]["SYN"]["agent"]["thesis"] = {"summary": prior["hypothesis"], "status": "intact",
                                                 "conviction": "medium", "frame": prior}
    wl.write_text(json.dumps(doc), encoding="utf-8")
    before = wl.read_bytes()
    with pytest.raises(ValueError, match="review"):
        _run(mod, BASE + ["--thesis-frame", json.dumps(_frame())])
    assert wl.read_bytes() == before
    reviewed = _frame(review={"event": "FQ2 财报", "date": "2026-09-10", "outcomes": [
        {"watch": "A 收入占比", "result": "+12% (合成归档)"},
        {"watch": "客户集中度", "result": "待核实 (归档无披露)"}]})
    assert _run(mod, BASE + ["--thesis-frame", json.dumps(reviewed),
                             "--thesis-status", "needs_reverification"]) == 0
    assert _agent(wl)["thesis"]["frame"]["review"] == reviewed["review"]
    assert _agent(wl)["thesis"]["status"] == "needs_reverification"


def test_repainting_a_prose_card_prompts_migration_without_blocking(upz, capsys):
    mod, wl = upz
    assert _run(mod, BASE + REPAINT) == 0
    assert "--thesis-frame" in capsys.readouterr().out
    _run(mod, BASE + ["--thesis-frame", json.dumps(_frame())])
    capsys.readouterr()
    assert _run(mod, BASE + REPAINT) == 0
    assert "--thesis-frame" not in capsys.readouterr().out
