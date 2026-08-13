"""冲击熔断信号 (shock_budget §2.7, 用户  定版 §15c)。

触发 = 受管书单日合计损失 ≥ 1.5 × SHOCK_BUDGET × NAV (= 22.5% NAV);
动作 = 直接清仓盘中立即执行 (装配层绕过 24h 冷却, 火警每轮都响)。
这里钉纯函数: 阈值两侧 / 数据缺失不编数 / 期权乘数 / synthetic 排除。
"""
import pytest

from quant.core import sizing
from quant.report import portfolio_brief as pb


def _item(ticker, qty, price, pct, **kw):
    return {"ticker": ticker, "qty": qty, "current_price": price,
            "change_pct": pct, **kw}


def test_fires_at_or_beyond_1_5x_budget():
    # NAV 20k → 阈值 1.5×15%×20k = $4.5k。单票 -25% 日: prev 200 → 现 150,
    # 100 股 = -$5,000 ≤ -$4.5k → 熔断。
    sig = pb.shock_breaker_signal([_item("A", 100, 150.0, -25.0)], 20_000.0)
    assert sig is not None
    assert sig["day_pnl_usd"] == pytest.approx(-5000.0)
    assert sig["pct_nav"] == pytest.approx(-25.0)
    assert sig["threshold_usd"] == pytest.approx(
        -sizing.SHOCK_BREAKER_MULT * sizing.SHOCK_BUDGET * 20_000.0)


def test_silent_below_threshold_and_needs_data():
    # -$1k << 阈值: 静默
    assert pb.shock_breaker_signal([_item("A", 100, 190.0, -5.0)], 20_000.0) is None
    # NAV 缺失 / 全无报价: 不知道 ≠ 熔断, 不编数
    assert pb.shock_breaker_signal([_item("A", 100, 150.0, -25.0)], None) is None
    assert pb.shock_breaker_signal([_item("A", 100, 150.0, None)], 20_000.0) is None
    assert pb.shock_breaker_signal([], 20_000.0) is None


def test_option_multiplier_and_synthetic_exclusion():
    # 期权保费 10→5 (-50%), 2 张 × 100 乘数 = -$1,000; NAV 2,500 → 阈值 $937.5 → 熔断
    opt = _item("LITE260918C100000", 2, 5.0, -50.0)
    sig = pb.shock_breaker_signal([opt], 2_500.0)
    assert sig is not None and sig["day_pnl_usd"] == pytest.approx(-1000.0)
    # synthetic 行 (标的参考报价, 非持仓) 不计入
    fake = _item("SKHY", 100, 150.0, -25.0, synthetic=True)
    assert pb.shock_breaker_signal([fake], 20_000.0) is None


def test_short_leg_gains_offset_losses():
    # 空头腿当日赚钱要对冲多头亏损 (qty 负号自带方向): -5000 + 1000 = -4000 > -4500 → 不触发
    long_leg = _item("A", 100, 150.0, -25.0)
    short_leg = _item("B", -100, 90.0, -10.0)     # prev 100 → 空头 +$1,000
    assert pb.shock_breaker_signal([long_leg, short_leg], 20_000.0) is None
