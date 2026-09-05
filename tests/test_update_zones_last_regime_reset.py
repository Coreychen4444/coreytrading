"""重画换了 stop → 转移机按旧 stop 预推的 last_regime (source=auto) 必须作废。

regime_transition 下行分支只在 last_regime 为空时重推, 否则把 last.zones 原样搬成 current。
卡片 stop 换了数值 → 机器按旧 stop 预推的 observation 带 (以及从它派生的更低 stop);
重画把 stop 抬高之后若不清掉, 破位当日装上的就是一套锚在旧 stop 之下的图。
"""
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".claude/skills/trading-analysis/scripts/update_position_zones.py"


@pytest.fixture
def upz(monkeypatch):
    spec = importlib.util.spec_from_file_location("upz_last_regime_reset_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setattr(sys, "argv", ["upz"])
    spec.loader.exec_module(mod)
    return mod


def _auto_obs(level: float) -> dict:
    return {"name": f"observation_below_{level:g}", "cap_pct_nav": 0, "source": "auto",
            "zones": {"accumulation_zone_1": [172.25, level], "accumulation_zone_2": [164.31, 172.25],
                      "stop_level": 154.04, "stop_level_days": 2},
            "repair_gate": {"level": level, "days": 2, "label": f"收复 {level:g} = 回到上一 regime"}}


def test_stop_moved_drops_auto_observation_keyed_to_old_stop(upz, capsys):
    agent = {"zones": {"stop_level": 201.9}, "last_regime": _auto_obs(180.6)}
    upz.drop_stale_auto_observation(agent, {"stop_level": 180.6})
    assert "last_regime" not in agent
    out = capsys.readouterr().out
    assert "observation_below_180.6" in out and "201.9" in out and "重推" in out


def test_stop_unchanged_keeps_block(upz, capsys):
    agent = {"zones": {"stop_level": 180.6}, "last_regime": _auto_obs(180.6)}
    upz.drop_stale_auto_observation(agent, {"stop_level": 180.6})
    assert agent["last_regime"]["name"] == "observation_below_180.6"
    assert capsys.readouterr().out == ""


def test_demoted_source_and_unrelated_repair_gate_are_left_alone(upz):
    # 上行转移写的 last_regime: repair_gate = 刚收复的 gate, 不是 stop 的函数 → stop 改了仍成立
    demoted = {"name": "demoted_at_2026-08-12", "source": "demoted",
               "zones": {"stop_level": 175.1}, "repair_gate": {"level": 231.0, "days": 2}}
    agent = {"zones": {"stop_level": 201.9}, "last_regime": demoted}
    upz.drop_stale_auto_observation(agent, {"stop_level": 218.11})
    assert agent["last_regime"] is demoted
    # auto 块但 repair_gate 与旧 stop 不同一条线 → 不是按这个 stop 推的, 不动
    agent = {"zones": {"stop_level": 201.9}, "last_regime": _auto_obs(175.1)}
    upz.drop_stale_auto_observation(agent, {"stop_level": 180.6})
    assert agent["last_regime"]["name"] == "observation_below_175.1"


def test_missing_fields_are_noops(upz):
    for agent, prev in (
        ({"zones": {"stop_level": 201.9}}, {"stop_level": 180.6}),                       # 无 last_regime
        ({"zones": {"stop_level": 201.9}, "last_regime": _auto_obs(180.6)}, {}),         # 旧卡无 stop
        ({"zones": {}, "last_regime": _auto_obs(180.6)}, {"stop_level": 180.6}),          # 新图无 stop
        ({"zones": {"stop_level": 201.9}, "last_regime": "garbage"}, {"stop_level": 180.6}),
    ):
        before = agent.get("last_regime")
        upz.drop_stale_auto_observation(agent, prev)
        assert agent.get("last_regime") == before


def test_merge_position_repaint_path_clears_it_end_to_end(upz):
    """走 merge_position 真路径: 带价位的重画 + stop 换数值 → 落盘 agent 无 last_regime。"""
    import argparse

    ns = argparse.Namespace(
        instrument_type="stock", role="", status="", zone1=[216.2, 225.4], zone2=[209.4, 216.2],
        trim=[230.0, 234.4], no_chase_above=226.8, stop_level=201.9, stop_level_basis="underlying",
        stop_level_days=2, thesis="", key_risk="", invalid_if="", notes="", timeframe="swing_2w",
        methodology="normal", reclaim_gates=None, valid_until="2026-09-18", anchored_on="2026-09-04",
        anchor_basis="test", repair_gate_anchor_basis="", update_trigger="", reasoning_summary="",
        conviction="medium", thesis_status="", action_intent="", action_reason="",
        max_classification="", constraint_reason="", constraint_until="", constraint_from="",
        touch_protocol="", vehicle="", next_regime=None, linked_underlying="",
    )
    existing = {"instrument": {"type": "stock"}, "broker": {"qty": 20}, "status": "active",
                "agent": {"zones": {"accumulation_zone_1": [192.7, 201.9], "stop_level": 180.6,
                                    "stop_level_days": 2},
                          "current_regime": {"name": "demoted_at_2026-08-12", "cap_pct_nav": 0,
                                             "source": "demoted"},
                          "last_regime": _auto_obs(180.6)}}
    out = upz.merge_position(existing, ns, ticker="NBIS")
    assert "last_regime" not in out["agent"]
    assert out["agent"]["zones"]["stop_level"] == 201.9
    assert out["agent"]["current_regime"]["source"] == "demoted"      # 机器 regime 不被触碰
    assert out["broker"] == {"qty": 20}
