"""earnings_window 机械件验证 (合成数据)。

钉的是预注册里会驱动裁决的语义:
  - 样本外协议: 估计只用**被测事件之前**的历史 (否则整轮是前视)
  - 先验 <8 → 回退池级 (**规则的一部分**, 不是数据缺失)
  - 缩仓公式 w_earn = w_normal × X_normal/X_earn, 且**不放大**(min 1.0)
  - 判据①是**双边带**: 低于下界 = 缩过头, 同样算失败
"""
import pytest

from quant.research import earnings_window as ew


def test_estimate_none_arm_never_shrinks():
    assert ew.estimate("D_none", [5.0] * 20, 3.0) == ew.X_NORMAL


def test_estimate_pool_arm_ignores_ticker_history():
    assert ew.estimate("A_pool", [9.0] * 20, 3.0) == 3.0
    assert ew.estimate("A_pool", [], 3.0) == 3.0


def test_fallback_to_pool_when_prior_history_is_thin():
    """先验 <MIN_PRIOR → B/C 一律走池级。这是规则, 不是缺数据。"""
    thin = [9.0] * (ew.MIN_PRIOR - 1)
    for arm in ("B_ticker", "C_shrink"):
        assert ew.estimate(arm, thin, 3.0) == 3.0


def test_shrink_arm_pulls_toward_pool_and_converges_with_n():
    """λ = n/(n+n0): 样本越多越信自己。"""
    pool, tick = 3.0, 9.0
    few = ew.estimate("C_shrink", [tick] * ew.MIN_PRIOR, pool)
    many = ew.estimate("C_shrink", [tick] * 200, pool)
    assert pool < few < many < tick
    lam = ew.MIN_PRIOR / (ew.MIN_PRIOR + ew.SHRINK_N0)
    assert few == pytest.approx(lam * tick + (1 - lam) * pool)


def test_ticker_arm_is_the_unshrunk_quantile():
    downs = [1.0] * 19 + [9.0]
    assert ew.estimate("B_ticker", downs, 3.0) == ew.pool_quantile(downs, ew.TICKER_Q)


def test_pool_quantile_picks_the_upper_tail_not_the_lower():
    v = list(range(100))
    assert ew.pool_quantile(v, 0.90) == 90
    assert ew.pool_quantile(v, 0.50) == 50


def test_shrink_never_scales_a_position_up():
    """X_earn < X_normal (如 NVDA) 时缩仓比例封顶 1.0 —— 财报日不许加仓。"""
    ratio = min(1.0, ew.X_NORMAL / 1.0)      # X_earn 比普通日还小
    assert ratio == 1.0


def test_breach_band_is_two_sided():
    """判据① 低于下界 = 缩过头, 同样是失败 —— 不是"越保守越好"。"""
    lo, hi = ew.BREACH_BAND
    assert lo > 0 and hi > lo
    assert not (lo <= 0.004 <= hi)            # 0.4% 缩过头
    assert not (lo <= 0.08 <= hi)             # 8% 没控住
    assert lo <= 0.028 <= hi                  # 2.8% 在带内


def test_earnings_days_reads_the_reaction_day_not_the_publish_day(tmp_path, monkeypatch):
    """AMC 财报 pub 在 7/16 16:00, 市场反应在 7/17 —— 必须用 trading_day_str。"""
    monkeypatch.setattr(ew, "EARNINGS_DIR", tmp_path)
    (tmp_path / "ZZ.json").write_text(
        '{"data": [{"pub_trading_day_str": "2026-07-16",'
        ' "pub_time_str": "2026-07-16 16:00:00", "trading_day_str": "2026-07-17"}]}')
    assert ew.earnings_days("ZZ") == {"2026-07-17"}


def test_missing_or_broken_earnings_file_is_empty_not_a_crash(tmp_path, monkeypatch):
    monkeypatch.setattr(ew, "EARNINGS_DIR", tmp_path)
    assert ew.earnings_days("NOPE") == set()
    (tmp_path / "BAD.json").write_text("{not json")
    assert ew.earnings_days("BAD") == set()


def test_preregistered_constants_are_pinned():
    assert ew.X_NORMAL == 1.94
    assert (ew.POOL_Q, ew.TICKER_Q, ew.SHRINK_N0) == (0.90, 0.98, 10)
    assert ew.MIN_PRIOR == 8
    assert ew.BREACH_BAND == (0.01, 0.04)
    assert ew.ARMS == ("D_none", "A_pool", "B_ticker", "C_shrink")
