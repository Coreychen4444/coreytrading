import unittest

from coreytrading import MarketSnapshot, PortfolioState, Regime, RiskEngine, RiskLimits, TargetPosition


class RiskEngineTest(unittest.TestCase):
    def test_single_position_cap_binds_and_rounds_to_whole_units(self) -> None:
        snapshot = MarketSnapshot(
            symbol="SYNTH_A",
            close=30.0,
            moving_average=29.0,
            atr=1.0,
            volume_ratio=1.1,
            regime=Regime.BULL,
        )
        portfolio = PortfolioState(nav=10_000.0, gross_exposure=1_000.0)
        target = TargetPosition(
            symbol=snapshot.symbol,
            current_notional=0.0,
            incremental_notional=5_000.0,
            target_notional=5_000.0,
            reference_price=snapshot.close,
        )
        adjusted = RiskEngine(
            RiskLimits(
                max_single_position_fraction=0.10,
                max_gross_exposure_fraction=1.0,
                risk_per_trade_fraction=0.10,
                stop_distance_atr=2.0,
            )
        ).adjust(snapshot, target, portfolio)
        self.assertIn("single_position", adjusted.binding_reasons)
        self.assertEqual(adjusted.target_quantity, 33)
        self.assertEqual(adjusted.target_notional, 990.0)

    def test_round_budget_caps_only_the_current_increment(self) -> None:
        snapshot = MarketSnapshot(
            symbol="SYNTH_ROUND",
            close=50.0,
            moving_average=48.0,
            atr=2.0,
            volume_ratio=1.2,
            regime=Regime.BULL,
        )
        portfolio = PortfolioState(
            nav=20_000.0,
            gross_exposure=2_000.0,
            position_notionals={snapshot.symbol: 1_000.0},
            position_rounds={snapshot.symbol: 2},
        )
        target = TargetPosition(
            symbol=snapshot.symbol,
            current_notional=1_000.0,
            incremental_notional=5_000.0,
            target_notional=6_000.0,
            reference_price=snapshot.close,
        )
        adjusted = RiskEngine(
            RiskLimits(
                max_single_position_fraction=1.0,
                max_gross_exposure_fraction=1.0,
                risk_per_trade_fraction=1.0,
                stop_distance_atr=2.0,
                round_risk_fractions=(0.02, 0.01, 0.005),
            )
        ).adjust(snapshot, target, portfolio)
        self.assertEqual(adjusted.binding_reasons, ("round_risk",))
        self.assertEqual(adjusted.target_notional, 3_500.0)
        self.assertEqual(adjusted.target_quantity, 70)

    def test_exhausted_round_schedule_adds_no_exposure(self) -> None:
        snapshot = MarketSnapshot(
            symbol="SYNTH_DONE",
            close=25.0,
            moving_average=24.0,
            atr=1.0,
            volume_ratio=1.1,
            regime=Regime.BULL,
        )
        portfolio = PortfolioState(
            nav=10_000.0,
            gross_exposure=1_000.0,
            position_notionals={snapshot.symbol: 500.0},
            position_rounds={snapshot.symbol: 3},
        )
        target = TargetPosition(
            symbol=snapshot.symbol,
            current_notional=500.0,
            incremental_notional=1_000.0,
            target_notional=1_500.0,
            reference_price=snapshot.close,
        )
        adjusted = RiskEngine(
            RiskLimits(round_risk_fractions=(0.01, 0.005))
        ).adjust(snapshot, target, portfolio)
        self.assertEqual(adjusted.binding_reasons, ("round_risk",))
        self.assertEqual(adjusted.target_notional, 500.0)

    def test_round_schedule_must_decrease(self) -> None:
        with self.assertRaisesRegex(ValueError, "non-increasing"):
            RiskLimits(round_risk_fractions=(0.01, 0.02))


if __name__ == "__main__":
    unittest.main()
