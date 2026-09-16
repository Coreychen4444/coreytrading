"""带 gate 的 next_regime 必须说出它授予多少 —— 存量补齐。"""
import json

import pytest

from quant.core.sizing import NOTIONAL_CAP
from quant.ops import migrate_next_regime_cap as mig


def _card(cap, *, gate=True, name="R_突破_219.35"):
    block = {"name": name, "cap_pct_nav": cap}
    if gate:
        block["entry_gate"] = {"level": 219.35, "days": 2, "volume_ratio_min": 1.1}
    return {"agent": {"zones": {"stop_level": 196.4}, "next_regime": block}}


def _doc(**tickers):
    return {"schema_version": 2, "tickers": tickers}


def test_null_cap_on_a_gated_regime_is_filled_with_the_notional_cap():
    doc = _doc(SYN=_card(None))
    changed, intact = mig.migrate_doc(doc, zero=set())
    assert [(t, w, v) for t, _n, w, v in changed] == [("SYN", None, NOTIONAL_CAP)]
    assert intact == []
    assert doc["tickers"]["SYN"]["agent"]["next_regime"]["cap_pct_nav"] == NOTIONAL_CAP


def test_a_missing_field_counts_the_same_as_an_explicit_null():
    """省略与 null 是同一件事: 「这道 gate 授予多少」没人回答过。"""
    doc = _doc(SYNA=_card(None))
    del doc["tickers"]["SYNA"]["agent"]["next_regime"]["cap_pct_nav"]
    changed, _ = mig.migrate_doc(doc, zero=set())
    assert len(changed) == 1
    assert doc["tickers"]["SYNA"]["agent"]["next_regime"]["cap_pct_nav"] == NOTIONAL_CAP


@pytest.mark.parametrize("cap", [0, 0.4, 0.25])
def test_existing_numbers_are_never_touched_including_an_explicit_zero(cap):
    """显式 0 = 「修复路上的中间站」, 是合法答案而不是缺失 —— 迁移不得把它改成 40%。"""
    doc = _doc(SYNB=_card(cap))
    changed, intact = mig.migrate_doc(doc, zero=set())
    assert changed == [] and intact == [("SYNB", cap)]
    assert doc["tickers"]["SYNB"]["agent"]["next_regime"]["cap_pct_nav"] == cap


def test_zero_list_writes_an_explicit_zero_for_the_named_ticker():
    doc = _doc(SYNC_T=_card(None), SYN=_card(None))
    mig.migrate_doc(doc, zero={"SYNC_T"})
    assert doc["tickers"]["SYNC_T"]["agent"]["next_regime"]["cap_pct_nav"] == 0
    assert doc["tickers"]["SYN"]["agent"]["next_regime"]["cap_pct_nav"] == NOTIONAL_CAP


def test_a_gateless_skeleton_is_left_alone():
    """无 gate 的块可空 (转移机写完 preview 骨架会立刻补) —— 迁移不去替它作答。"""
    doc = _doc(SYND=_card(None, gate=False))
    changed, intact = mig.migrate_doc(doc, zero=set())
    assert changed == [] and intact == []
    assert doc["tickers"]["SYND"]["agent"]["next_regime"]["cap_pct_nav"] is None


def test_second_run_changes_nothing():
    doc = _doc(SYN=_card(None), SYNB=_card(0))
    mig.migrate_doc(doc, zero=set())
    before = json.dumps(doc, sort_keys=True)
    changed, _ = mig.migrate_doc(doc, zero=set())
    assert changed == [] and json.dumps(doc, sort_keys=True) == before
