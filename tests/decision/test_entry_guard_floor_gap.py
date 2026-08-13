"""round-2b 地板贴 gate advisory (floor_gate_gap_lines) — 用户 批准 。

Advisory 语义: 只播报不改判 — 这里只测行生成, verdict 不受影响由既有 entry_guard
测试保障 (本函数不触碰 verdict 路径)。
"""
from quant.decision.entry_guard import FLOOR_GAP_ATR, floor_gate_gap_lines


def _agent(stop, gates=None, next_gate=None, repair=None):
    a = {"zones": {"stop_level": stop}}
    if gates:
        a["zones"]["reclaim_gates"] = [{"level": g, "days": 2} for g in gates]
    if next_gate is not None:
        a["next_regime"] = {"entry_gate": {"level": next_gate, "days": 2}}
    if repair is not None:
        a["last_regime"] = {"repair_gate": {"level": repair}}
    return a


def test_fires_when_stop_hugs_gate():
    # ATR=4 → 告警线 1.0; stop 100 vs gate 100.5 → 贴
    lines = floor_gate_gap_lines(_agent(100.0, gates=[100.5]), atr=4.0)
    assert len(lines) == 1 and "地板贴 gate" in lines[0] and "gate − 0.5N" in lines[0]


def test_silent_when_buffered_by_half_n():
    # 新约定地板 = gate − 0.5N = 98.0, 距 gate 2.0 = 0.5N ≥ 0.25N → 不告警
    assert floor_gate_gap_lines(_agent(98.0, gates=[100.0]), atr=4.0) == []


def test_checks_all_three_gate_sources():
    a = _agent(100.0, gates=[100.2], next_gate=100.3, repair=99.9)
    lines = floor_gate_gap_lines(a, atr=4.0)
    assert len(lines) == 3
    kinds = "".join(lines)
    assert "reclaim" in kinds and "up" in kinds and "down" in kinds


def test_silent_without_atr_or_stop():
    assert floor_gate_gap_lines(_agent(100.0, gates=[100.1]), atr=None) == []
    assert floor_gate_gap_lines(_agent(None, gates=[100.1]), atr=4.0) == []
    assert floor_gate_gap_lines({"zones": None}, atr=4.0) == []


def test_boundary_exactly_quarter_n_is_silent():
    # |stop − gate| == 0.25×ATR → 严格小于才告警 (边界不告)
    atr = 4.0
    gap = FLOOR_GAP_ATR * atr
    assert floor_gate_gap_lines(_agent(100.0 - gap, gates=[100.0]), atr=atr) == []
