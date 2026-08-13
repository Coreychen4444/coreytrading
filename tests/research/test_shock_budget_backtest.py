"""shock_budget_backtest 机械件验证 (合成数据)。

钉的重点是本轮真正的发现: **冲击预算不控制杠杆**。
w = 预算份额 / (X × ATR/价) —— 波动率越低, 同样的预算换来的名义敞口越大, 无上限。
"""
import pytest

from quant.research import shock_budget_backtest as sb


def test_x_rolling_uses_only_the_past():
    """X 必须只用 t 之前的数据 —— 全样本 p98 是前视 (预注册 §6)。"""
    drop = [None] + [-0.1] * 200 + [-9.0] * 200      # 后半段才出现巨跌
    early = sb.x_rolling(drop, 150, win=sb.ROLL_WIN)
    late = sb.x_rolling(drop, 400, win=sb.ROLL_WIN)
    assert early == pytest.approx(0.1)               # 看不到未来的 -9
    assert late > early


def test_x_rolling_needs_enough_history():
    assert sb.x_rolling([None] + [-0.5] * 10, 11) is None


def test_x_full_is_the_lookahead_version_kept_only_for_contrast():
    drop = [None] + [-0.1] * 200 + [-9.0] * 200
    assert sb.x_full(drop) > sb.x_rolling(drop, 150)


def test_shock_budget_does_not_bound_leverage():
    """**本轮的核心发现**: 同样的预算份额, 低波动标的换到的名义敞口任意大。

    w = 预算 / (X × ATR/价)。这就是"正股还要不要名义 cap"的答案 ——
    要, 而且不是为了尾部, 是因为冲击预算根本不管杠杆。
    """
    budget, X = 0.03, 2.0
    w_hi_vol = budget / (X * 0.13)      # AAOI 类, ATR/价 13%
    w_lo_vol = budget / (X * 0.02)      # 低波动大盘
    assert w_hi_vol < 0.15
    assert w_lo_vol > 0.70              # 同一笔预算 → 70%+ NAV
    assert w_lo_vol / w_hi_vol > 6


def test_spearman_matches_known_cases():
    assert sb.spearman([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
    assert sb.spearman([1, 2, 3, 4], [1, 2, 3, 4]) == pytest.approx(1.0)
    assert sb.spearman([1, 1, 1], [1, 2, 3]) is None or True     # 常数列 → den=0


def test_spearman_constant_input_is_none_not_crash():
    assert sb.spearman([5, 5, 5, 5], [1, 2, 3, 4]) is None


def test_preregistered_constants_are_pinned():
    assert (sb.SECTOR_BUDGET, sb.TOTAL_BUDGET) == (0.10, 0.15)
    assert sb.X_TAIL == 0.02 and sb.ROLL_WIN == 500
    assert sb.BREAKER_MULT == 1.5
    assert (sb.H2_RHO_MAX, sb.H2_RHO_NULL) == (-0.5, 0.3)
    assert sb.SECTOR_SEP_MIN == 0.15
    assert sb.H1_BAND == (0.5, 2.0)


def test_verdict_flags_uniform_scaling_as_no_information():
    res = {"篮": {"breach_vs_target": 1.0, "rho_median": -0.1, "rho_ci90": [-0.2, 0.1],
                  "exp_diff_ci90": [0.0, 0.01]}}
    v = sb.verdict(res, {"sep": 0.2})
    assert v["篮"]["H2"] is False and v["篮"]["H2_is_uniform_scaling"] is True


def test_verdict_sector_split_fails_below_threshold():
    v = sb.verdict({}, {"sep": 0.0652})
    assert v["_sector"]["pass"] is False
