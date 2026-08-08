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


if __name__ == "__main__":
    unittest.main()
