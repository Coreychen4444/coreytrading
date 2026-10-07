"""期权卡 stop 跟随授权 gate —— 它会写 positions.json, 每条不动手的路径都要钉住。

合成场景: 正股越过新 gate, S5g 当轮读到新 gate, call 卡的 stop 仍是建仓时手写的旧线
—— 同一条退出线两个数字。只升不降; 严于 gate 的手工线不覆盖。
"""
from quant.sync import option_stop_sync as oss

C1 = "SYNTH270319C100000"


def _und(hist):
    return {"instrument": {"type": "stock"}, "broker": {"qty": 0},
            "agent": {"zones": {"stop_level": 103.5}, "regime_history": hist}}


UP_OLD = {"date": "2030-01-07", "direction": "up", "level": 98.0, "cap_pct_nav": 0.4}
UP_NEW = {"date": "2030-01-08", "direction": "up", "level": 105.0, "cap_pct_nav": 0.4,
          "reason": "合成快车道"}
DOWN = {"date": "2030-01-09", "direction": "down", "level": 103.5, "cap_pct_nav": 0}


def _opt(stop=98.0, basis="underlying", qty=1.0, **agent):
    zones = {"stop_level": stop, "stop_level_days": 1}
    if basis:
        zones["stop_level_basis"] = basis
    return {"instrument": {"type": "option"}, "broker": {"qty": qty},
            "agent": {"zones": zones, "linked_underlying": "SYNTH", **agent}}


def _doc(opt, hist):
    return {"tickers": {C1: opt, "SYNTH": _und(hist)}}


def _one(doc):
    (it,) = oss.plan(doc)
    return it


def test_raises_to_new_granting_gate():
    doc = _doc(_opt(), [UP_OLD, UP_NEW])
    it = _one(doc)
    assert it["action"] == "raise" and it["gate"] == 105.0 and it["gate_date"] == "2030-01-08"
    assert oss.apply([it], doc) == 1
    z = doc["tickers"][C1]["agent"]["zones"]
    assert z["stop_level"] == 105.0 and z["stop_level_days"] == 1
    assert z["stop_level_basis"] == "underlying"
    assert "98" in z["stop_derivation"] and "只升不降" in z["stop_derivation"]
    assert _one(doc)["action"] == "skip", "幂等: 第二轮不再动"


def test_never_lowers_stricter_manual_stop():
    """期权退出线可以严于 gate。"""
    assert _one(_doc(_opt(stop=107.0), [UP_NEW]))["action"] == "skip"


def test_no_gate_after_down_transition_leaves_card_alone():
    it = _one(_doc(_opt(), [UP_NEW, DOWN]))
    assert it["action"] == "skip" and it["gate"] is None


def test_other_basis_or_unlabelled_not_compared():
    assert _one(_doc(_opt(basis="option_price"), [UP_NEW]))["action"] == "skip"
    assert _one(_doc(_opt(basis=None), [UP_NEW]))["action"] == "skip"


def test_reversal_probe_keeps_its_own_gate():
    assert _one(_doc(_opt(entry_class="reversal_probe"), [UP_NEW]))["action"] == "skip"


def test_closed_option_not_touched():
    assert oss.plan(_doc(_opt(qty=0), [UP_NEW])) == []
