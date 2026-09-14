"""卡片量腿阈值迁移 1.2 → 1.1 的机械件。

钉死三件事: ①六条字段路径一条不漏 ②只改恰好等于 FROM 的值 (显式例外原样保留) ③幂等。
"""
import copy

from quant.decision import regime_transition as rt
from quant.ops import migrate_volume_leg_threshold as mig


def test_constants_track_the_ruling():
    assert mig.FROM_RATIO == 1.2 and mig.TO_RATIO == 1.1
    assert mig.TO_RATIO == rt.RECLAIM_VOL_RATIO_MIN        # 迁移目标 = live 常量, 永不分叉


def _card(**agent):
    return {"agent": agent}


def _full_shape_doc(v=1.2):
    """六条路径各一份 (entry_gate / next_gate_preview / preserved_upper_ladder 两条 /
    zones.reclaim_gates / last_regime.repair_gate)。"""
    g = lambda: {"level": 100.0, "days": 2, "volume_ratio_min": v}
    return {"tickers": {"T": _card(
        zones={"reclaim_gates": [g(), g()]},
        next_regime={"entry_gate": g(), "next_gate_preview": g(),
                     "preserved_upper_ladder": {"entry_gate": g(), "next_gate_preview": g()}},
        last_regime={"repair_gate": g(), "zones": {"reclaim_gates": [g()]}},
    )}}


def test_all_six_field_paths_are_reached():
    doc = _full_shape_doc()
    changed, skipped = mig.migrate_doc(doc)
    paths = {p for _, p, _ in changed}
    assert paths == {
        "agent.zones.reclaim_gates[0]", "agent.zones.reclaim_gates[1]",
        "agent.next_regime.entry_gate", "agent.next_regime.next_gate_preview",
        "agent.next_regime.preserved_upper_ladder.entry_gate",
        "agent.next_regime.preserved_upper_ladder.next_gate_preview",
        "agent.last_regime.repair_gate", "agent.last_regime.zones.reclaim_gates[0]",
    }
    assert skipped == []
    a = doc["tickers"]["T"]["agent"]
    assert a["next_regime"]["entry_gate"]["volume_ratio_min"] == 1.1
    assert a["next_regime"]["preserved_upper_ladder"]["next_gate_preview"]["volume_ratio_min"] == 1.1
    assert a["last_regime"]["zones"]["reclaim_gates"][0]["volume_ratio_min"] == 1.1
    assert a["next_regime"]["entry_gate"]["level"] == 100.0 and a["next_regime"]["entry_gate"]["days"] == 2


def test_explicit_exceptions_are_preserved():
    """卡上写了 1.2 以外的数 = 有人显式裁过 (同 agent_submit「不得放宽证据标准」语义) → 不动。"""
    doc = {"tickers": {"A": _card(next_regime={"entry_gate": {"level": 1, "volume_ratio_min": 1.5}}),
                       "B": _card(next_regime={"entry_gate": {"level": 1, "volume_ratio_min": 1.0}}),
                       "C": _card(next_regime={"entry_gate": {"level": 1, "volume_ratio_min": "1.2"}}),
                       "D": _card(next_regime={"entry_gate": {"level": 1}})}}
    changed, skipped = mig.migrate_doc(doc)
    assert changed == []
    assert {t for t, _, _ in skipped} == {"A", "B", "C"}          # D 无字段 = 不进任何清单
    assert doc["tickers"]["A"]["agent"]["next_regime"]["entry_gate"]["volume_ratio_min"] == 1.5
    assert doc["tickers"]["C"]["agent"]["next_regime"]["entry_gate"]["volume_ratio_min"] == "1.2"


def test_already_migrated_value_is_neither_changed_nor_flagged():
    doc = _full_shape_doc(v=1.1)
    changed, skipped = mig.migrate_doc(doc)
    assert changed == [] and skipped == []                        # 1.1 已是目标: 不改也不报「例外」


def test_idempotent():
    doc = _full_shape_doc()
    mig.migrate_doc(doc)
    once = copy.deepcopy(doc)
    changed, skipped = mig.migrate_doc(doc)
    assert changed == [] and skipped == [] and doc == once


def test_missing_or_odd_agent_blocks_are_skipped_safely():
    doc = {"tickers": {"X": {}, "Y": {"agent": None}, "Z": {"agent": {"zones": None, "next_regime": []}}}}
    assert mig.migrate_doc(doc) == ([], [])


def test_does_not_touch_unrelated_numbers():
    doc = {"tickers": {"T": _card(next_regime={
        "cap_pct_nav": 1.2,                                        # 同一个数字, 不同身份
        "entry_gate": {"level": 1.2, "days": 2, "volume_ratio_min": 1.2}})}}
    mig.migrate_doc(doc)
    nr = doc["tickers"]["T"]["agent"]["next_regime"]
    assert nr["cap_pct_nav"] == 1.2 and nr["entry_gate"]["level"] == 1.2
    assert nr["entry_gate"]["volume_ratio_min"] == 1.1
