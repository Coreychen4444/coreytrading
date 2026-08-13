"""tier / auth 机械推导 (用户 : 不问人;  auth 接替 classification)。

tier 的语义是"受不受交易纪律管" —— base 只由结构授予 (role=commemorative_hold,
当前唯一成员 SPCX), 没有人工判断空间; core/trading  已并入 position。
auth = 成交时授权快照 {cap_pct_nav, gate_no} (classification_removal_design.md D4),
推不出来就留空 —— 推不出来正是"无授权成交"的形状, 那类才需要人。
"""
import pytest

from quant.journal import annotate_event as ae

from quant.core.sizing import NOTIONAL_CAP as _CONF, derive_auth


def _card(*, role=None, regime=None, lots=None, gates=None):
    card = {"instrument": {"type": "stock", **({"role": role} if role else {})},
            "agent": {}}
    if regime is not None:
        card["agent"]["current_regime"] = regime
    if gates:                      # 授权转移史 (gate_no 的判据,  二次定版)
        card["agent"]["regime_history"] = [
            {"date": d, "direction": "up", "cap_pct_nav": _CONF} for d in gates]
    if lots is not None:
        card["lots"] = lots
    return card


CARDS = {
    "SPCX": _card(role="commemorative_hold"),
    "AAOI": _card(regime={"name": "B_repair_probe_120.5", "cap_pct_nav": _CONF,
                          "since": "2026-08-05"},
                  gates=["2026-08-05", "2026-08-12"],
                  lots={"position": [{"qty": 2, "avg_cost": 125.0, "entry_period": "2026-08-05"},
                                     {"qty": 2, "avg_cost": 123.0, "entry_period": "2026-08-06"}]}),
    "MSFT": _card(regime={"name": "R_突破_473", "cap_pct_nav": _CONF, "since": "2026-08-04"}),
    "NBIS": _card(regime={"name": "持仓·无加仓授权", "cap_pct_nav": 0, "since": "2026-08-06"}),
    "NOREG": _card(),
    "GOOGL": _card(regime={"name": "R_x", "cap_pct_nav": _CONF, "since": "2026-08-01"}),
}


def test_tier_base_only_for_structural_exemption():
    assert ae.derive_tier("SPCX", CARDS)[0] == "base"
    assert "commemorative_hold" in ae.derive_tier("SPCX", CARDS)[1]
    for t in ("AAOI", "NBIS", "NOREG", "UNKNOWN_TICKER"):
        tier, why = ae.derive_tier(t, CARDS)
        assert tier == "position", t
        assert "core/trading" in why           # 依据必须说出口, 不静默默认


def test_tier_vocabulary_is_the_merged_one():
    """core / trading 不是可写值 (2026-07-25 并入 position, 只在 tier_legacy 留存)。"""
    from quant.core.schema import LEGACY_TIER_MAP, TIERS
    assert TIERS == {"base", "position"}
    assert LEGACY_TIER_MAP == {"core": "position", "trading": "position"}
    assert all(ae.derive_tier(t, CARDS)[0] in TIERS for t in CARDS)


def test_auth_snapshots_regime_cap_and_gate():
    """auth = 授权事实的快照: cap 说明授权档, gate_no 说明爬到第几个**给 cap 的**
    gate (判据 = 卡上的 regime_history, 与成交记录无关 —— 2026-08-13 二次定版)。"""
    auth, basis = derive_auth("MSFT", CARDS)
    assert auth == {"cap_pct_nav": _CONF, "gate_no": 1}      # 无转移史 = 第一档
    assert "R_突破_473" in basis and "since 2026-08-04" in basis
    auth2, _ = derive_auth("AAOI", CARDS)                    # 两道 gate → gate2
    assert auth2 == {"cap_pct_nav": _CONF, "gate_no": 2}


@pytest.mark.parametrize("ticker,marker", [
    ("NBIS", "cap 0"),          # 无授权 = 规则外成交的形状 → 归人
    ("NOREG", "无 current_regime"),
])
def test_unauthorized_or_unknown_is_left_to_the_human(ticker, marker):
    auth, basis = derive_auth(ticker, CARDS)
    assert auth is None
    assert marker in basis


def test_option_authorization_lives_on_the_underlying_card():
    """期权归正股 (与 Today 白名单同拍): OCC key 无 regime → 回落 underlying 卡。"""
    auth, basis = derive_auth("GOOGL270115C390000", CARDS)
    assert auth is not None and auth["cap_pct_nav"] == _CONF
    assert basis.startswith("GOOGL ")


def test_classification_derivation_is_gone():
    """derive_classification 随分类轴退役 () —— 不许悄悄回来。"""
    from quant.core import sizing
    assert not hasattr(sizing, "derive_classification")
    assert not hasattr(ae, "derive_classification")
