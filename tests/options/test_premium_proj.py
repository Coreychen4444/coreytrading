"""premium_proj — 正股价位 → 保费投影 (F1 裁决 b: 卡片存正股结构位,
显示层每日折算; theta 由每日重估自动吸收, 静态保费线会自己漂)。"""
import pytest

from quant.options import premium_proj as pp


ARGS = dict(mark=20.48, spot=355.60, strike=390.0, dte_days=523, iv_pct=34.1)


def test_bs_projection_is_monotonic_in_level():
    lo = pp.project_call_premium(340.0, **ARGS)
    mid = pp.project_call_premium(355.60, **ARGS)
    hi = pp.project_call_premium(392.0, **ARGS)
    assert lo["method"] == "bs"
    assert lo["premium"] < mid["premium"] < hi["premium"]
    # level == spot → 偏移为零 → 投影 == mark (BS 与真实 mark 的常数偏差被保留)
    assert mid["premium"] == pytest.approx(20.48, abs=0.01)


def test_falls_back_to_delta_linear_without_iv():
    pj = pp.project_call_premium(360.0, mark=20.48, spot=355.60, strike=390.0,
                                 dte_days=None, iv_pct=None, delta=0.405)
    assert pj["method"] == "delta"
    assert pj["premium"] == pytest.approx(20.48 + 0.405 * (360.0 - 355.60), abs=0.01)


def test_floors_at_intrinsic_never_negative():
    # 深下方投影: delta 线性会给出负数 → 夹 0 / 内在价值
    pj = pp.project_call_premium(200.0, mark=5.0, spot=350.0, strike=390.0,
                                 dte_days=None, iv_pct=None, delta=0.9)
    assert pj["premium"] == 0.0
    deep_itm = pp.project_call_premium(500.0, mark=5.0, spot=350.0, strike=390.0,
                                       dte_days=None, iv_pct=None, delta=0.2)
    assert deep_itm["premium"] >= 110.0            # ≥ 内在价值 500-390


def test_none_when_neither_path_computable():
    assert pp.project_call_premium(360.0, mark=20.0, spot=355.0, strike=390.0,
                                   dte_days=None, iv_pct=None, delta=None) is None
    assert pp.project_call_premium(None, **ARGS) is None


def test_dte_of_parses_occ():
    assert pp.dte_of("GOOGL270115C390000", "2026-08-10") == 158
    assert pp.dte_of("GOOGL", "2026-08-10") is None


# ── binding_stop 折基 (显示合同: max 单值 + 来源) ────────────────

def test_stock_fold_picks_max_of_three_bases():
    st = pp.binding_stop(is_option=False, card_stop=800.0, card_days=2,
                         lock_floor=850.0, lock_stage="stage1",
                         trail=750.84, trail_mode="normal")
    assert st["source"] == "lock" and st["value"] == 850.0
    st2 = pp.binding_stop(is_option=False, card_stop=800.0, trail=880.0,
                          trail_mode="normal")
    assert st2["source"] == "trail" and st2["value"] == 880.0


def test_option_fold_projects_underlying_bases_into_premium():
    """混基直接 max = 量纲错 (正股 358.5 恒 > 保费 26.25, card 永远赢且读数无意义)。
    变异守卫: 若实现直接拿 card_stop 原值进 max, source 会是 card 且 value 358.5。"""
    st = pp.binding_stop(is_option=True, card_stop=358.5, card_days=2,
                         trail=340.55, trail_mode="normal",
                         s1_floor=26.25, **ARGS)
    assert st["source"] == "s1"
    assert st["value"] == 26.25
    assert st["value"] < 40, "card 原值 (358.5) 混进了保费空间"


def test_option_fold_survives_missing_greeks():
    """无 IV/delta → 投影不可得, 原生保费基 (S1) 仍在 — 显示降级不失明。"""
    st = pp.binding_stop(is_option=True, card_stop=358.5, s1_floor=26.25,
                         mark=20.48, spot=355.60, strike=390.0,
                         dte_days=None, iv_pct=None, delta=None)
    assert st["source"] == "s1" and st["value"] == 26.25


def test_empty_bases_returns_none():
    assert pp.binding_stop(is_option=False) is None
    assert pp.binding_stop(is_option=True, mark=20.0, spot=355.0) is None
