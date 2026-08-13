"""kelly_size.py — money math must be exact."""
import pytest

from quant.risk.kelly_size import compute, kelly_fraction


def test_full_kelly_formula():
    # f* = (b*p - q) / b ; p=0.6, b=2 → (1.2 - 0.4)/2 = 0.4
    assert kelly_fraction(0.6, 2.0) == pytest.approx(0.4)
    # p=0.5, b=1 → 0 (coin flip at even odds = no edge)
    assert kelly_fraction(0.5, 1.0) == pytest.approx(0.0)


def test_no_edge_blocks_bet():
    out = compute(p=0.3, b=1.0, fraction=0.5, bankroll=50_000, loss_per_unit=1.0, cap_pct=None)
    assert out["edge"] is False
    assert out["at_risk_dollar"] == 0.0
    assert out["notional_dollar"] == 0.0
    assert out["binding"] == "no_edge"


def test_b_must_be_positive():
    with pytest.raises(ValueError):
        kelly_fraction(0.6, 0.0)


def test_half_kelly_at_risk_and_notional_long_option():
    # long option: loss_per_unit = 1.0 → notional == at-risk
    out = compute(p=0.65, b=2.5, fraction=0.5, bankroll=50_000, loss_per_unit=1.0, cap_pct=None)
    f_full = (2.5 * 0.65 - 0.35) / 2.5  # 0.51
    assert out["f_full"] == pytest.approx(f_full, abs=1e-4)
    assert out["at_risk_dollar"] == pytest.approx(0.5 * f_full * 50_000, abs=0.5)
    assert out["notional_dollar"] == pytest.approx(out["at_risk_dollar"])
    assert out["binding"] == "kelly"


def test_stock_with_stop_levers_notional():
    # stock with 20% stop: notional = at-risk / 0.20 = 5x
    out = compute(p=0.6, b=1.5, fraction=0.5, bankroll=10_000, loss_per_unit=0.2, cap_pct=None)
    assert out["notional_dollar"] == pytest.approx(out["at_risk_dollar"] / 0.2, abs=0.1)


def test_concentration_cap_binds_and_shrinks_at_risk():
    # huge edge → Kelly wants more than cap; cap must bind and at-risk shrink with it
    out = compute(p=0.8, b=3.0, fraction=1.0, bankroll=100_000, loss_per_unit=1.0, cap_pct=25)
    assert out["binding"] == "concentration_cap"
    assert out["notional_dollar"] == pytest.approx(25_000)
    assert out["at_risk_dollar"] == pytest.approx(25_000 * 1.0)
