"""--repair-gate-anchor-basis 通道 (用户 裁决)。

转移机把收复线放在 last_regime.repair_gate, 重画只推进 zones 锚 → 每次转移后的
下一次重画 repair gate 必然 STALE (LITE/MSFT 8/5 重画甩尾实证)。通道语义:
日期恒 = 本次调用后的 zones 锚日 (同锚由构造保证), level 不可经此改动,
漏传时重画路径 ⚠️ 提醒但不拦截。
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".claude/skills/trading-analysis/scripts/update_position_zones.py"


@pytest.fixture
def upz(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("upz_repair_anchor_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setattr(sys, "argv", ["upz"])
    spec.loader.exec_module(mod)

    def card(with_last_regime: bool):
        agent = {
            "thesis": {"summary": "t", "status": "intact", "conviction": "medium",
                       "last_reviewed": "2026-08-05T00:00:00Z"},
            "zones": {"accumulation_zone_1": [358.5, 368.0], "stop_level": 335.5,
                      "valid_until": "2026-08-17",
                      "anchored_on": {"date": "2026-08-04", "basis": "b"}},
            "action": {"intent": "wait_for_zone", "reason": "r"},
            "next_regime": {"name": "R_x", "entry_gate": {"level": 382.5, "days": 2},
                            "cap_pct_nav": 0.05},
            "current_regime": {"name": "常规参与", "cap_pct_nav": 0.05,
                               "participation": "zone_eligible",
                               "since": "2026-08-04", "source": "card"},
        }
        if with_last_regime:
            agent["last_regime"] = {
                "name": "demoted_at_2026-08-04", "cap_pct_nav": 0,
                "participation": "gate_only", "source": "demoted",
                "provisional": ["accumulation_zone_1"],
                "zones": {"accumulation_zone_1": [330.0, 345.0], "stop_level": 320.0},
                "repair_gate": {"level": 345.0, "days": 2, "unlocks": "probe",
                                "label": "收复 345 = 回到 R_x",
                                "anchored_on": {"date": "2026-08-01", "basis": "旧锚"}},
                "derivation": "d",
            }
        return {"instrument": {"type": "stock"}, "broker": None,
                "status": "watching", "agent": agent}

    wl = tmp_path / "watchlist.json"
    wl.write_text(json.dumps({"tickers": {"GOOGL": card(True), "NVDA": card(False)}}),
                  encoding="utf-8")
    pos = tmp_path / "positions.json"
    pos.write_text(json.dumps({"tickers": {}}), encoding="utf-8")
    monkeypatch.setattr(mod, "WATCHLIST_PATH", wl)
    monkeypatch.setattr(mod, "POSITIONS_PATH", pos)
    return mod, wl


def _run(mod, argv):
    sys.argv = ["upz"] + argv
    return mod.main()


def _rg(wl, ticker="GOOGL"):
    card = json.loads(wl.read_text())["tickers"][ticker]
    return card["agent"]["last_regime"]["repair_gate"]


def test_repaint_with_flag_stamps_repair_gate_to_zone_date(upz, capsys):
    mod, wl = upz
    _run(mod, ["--ticker", "GOOGL", "--bucket", "watchlist",
               "--zone1", "360", "370", "--stop-level", "340",
               "--anchored-on", "2026-08-12", "--anchor-basis", "b2",
               "--repair-gate-anchor-basis", "复核: 345 簇未变",
               "--conviction", "medium"])
    rg = _rg(wl)
    assert rg["anchored_on"] == {"date": "2026-08-12", "basis": "复核: 345 簇未变"}
    assert rg["level"] == 345.0 and rg["days"] == 2      # level/days = 转移史, 不可改
    assert "repair_gate 345 锚 → 2026-08-12" in capsys.readouterr().out


def test_standalone_flag_anchors_to_existing_zone_date(upz):
    """单独传 = 对现有 zone 锚日的补锚 (今日 MU/INTC 回填那种用法); zones 走保留路径。"""
    mod, wl = upz
    _run(mod, ["--ticker", "GOOGL", "--bucket", "watchlist",
               "--repair-gate-anchor-basis", "复核: 簇未变", "--conviction", "medium"])
    card = json.loads(wl.read_text())["tickers"]["GOOGL"]
    assert _rg(wl)["anchored_on"] == {"date": "2026-08-04", "basis": "复核: 簇未变"}
    assert card["agent"]["zones"]["accumulation_zone_1"] == [358.5, 368.0]
    assert card["agent"]["zones"]["anchored_on"]["date"] == "2026-08-04"


def test_bfloor_gap_warns_on_deviation_but_never_blocks(upz, capsys):
    """A3 (2026-08-09): 带 repair_gate 的卡, stop 距收复线偏离 [0.4,0.6]N → 喊。

    round-2b 约定 stop = gate − 0.5N, 此前写入路径零校验 (唯一告警线 entry_guard
    0.25N 比约定松一半), live 合规全靠画卡的人记得。"""
    mod, wl = upz
    agent = json.loads(wl.read_text())["tickers"]["GOOGL"]["agent"]
    # gate 345 − stop 335.5 = 9.5; ATR 19 → 0.50N 整: 合规不喊
    mod.warn_bfloor_gap("GOOGL", agent, atr=19.0)
    assert "bfloor_buf 偏离" not in capsys.readouterr().out
    # ATR 8 → 1.19N: 喊 (warn 不拦)
    mod.warn_bfloor_gap("GOOGL", agent, atr=8.0)
    out = capsys.readouterr().out
    assert "bfloor_buf 偏离" in out and "1.19N" in out and "不拦截" in out
    # 期权卡 / 无 repair_gate 的卡: 静默跳过, 不误伤
    mod.warn_bfloor_gap("GOOGL270115C390000", agent, atr=8.0)
    nvda = json.loads(wl.read_text())["tickers"]["NVDA"]["agent"]
    mod.warn_bfloor_gap("NVDA", nvda, atr=8.0)
    assert capsys.readouterr().out == ""


def test_flag_without_repair_gate_errors_loudly(upz):
    mod, wl = upz
    before = wl.read_text()
    with pytest.raises(SystemExit, match="没有 last_regime.repair_gate"):
        _run(mod, ["--ticker", "NVDA", "--bucket", "watchlist",
                   "--repair-gate-anchor-basis", "x", "--conviction", "medium"])
    assert wl.read_text() == before, "报错后文件必须原封不动"


def test_repaint_without_flag_warns_but_writes(upz, capsys):
    """甩尾必须说出口: 重锚 zones 不带收复线 → ⚠️ 提醒, 旧锚保留, 写入照常。"""
    mod, wl = upz
    _run(mod, ["--ticker", "GOOGL", "--bucket", "watchlist",
               "--zone1", "360", "370", "--stop-level", "340",
               "--anchored-on", "2026-08-12", "--anchor-basis", "b2",
               "--conviction", "medium"])
    out = capsys.readouterr().out
    assert "repair_gate 345 锚 2026-08-01 未随本次重画 (2026-08-12)" in out
    assert "--repair-gate-anchor-basis" in out
    rg = _rg(wl)
    assert rg["anchored_on"]["date"] == "2026-08-01"      # 不代复核, 不自动盖锚
    card = json.loads(wl.read_text())["tickers"]["GOOGL"]
    assert card["agent"]["zones"]["accumulation_zone_1"] == [360.0, 370.0]
