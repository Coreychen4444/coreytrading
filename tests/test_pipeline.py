import unittest

from coreytrading import (
    IncrementalPortfolioConstructor,
    InMemoryDataSource,
    MarketSnapshot,
    OrderPlanner,
    PaperExecutor,
    PortfolioState,
    Regime,
    RiskEngine,
    TechnicalScoreModel,
    ThresholdStrategy,
    TradingPipeline,
)


def make_pipeline(snapshot: MarketSnapshot, *, with_executor: bool = False) -> TradingPipeline:
    return TradingPipeline(
        data_source=InMemoryDataSource({snapshot.symbol: snapshot}),
        model=TechnicalScoreModel(),
        strategy=ThresholdStrategy(),
        constructor=IncrementalPortfolioConstructor(),
        risk=RiskEngine(),
        order_planner=OrderPlanner(),
        executor=PaperExecutor() if with_executor else None,
    )


class TradingPipelineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.snapshot = MarketSnapshot(
            symbol="SYNTH_A",
            close=100.0,
            moving_average=96.0,
            atr=2.0,
            volume_ratio=1.5,
            regime=Regime.BULL,
        )
        self.portfolio = PortfolioState(nav=100_000.0, gross_exposure=20_000.0)

    def test_default_run_produces_intent_without_execution(self) -> None:
        result = make_pipeline(self.snapshot).run(self.snapshot.symbol, self.portfolio)
        self.assertEqual(result.signal.action.value, "GO")
        self.assertIsNotNone(result.order)
        self.assertIsNone(result.execution)
        self.assertGreater(result.adjusted_target.target_quantity, 0)

    def test_paper_execution_is_explicit(self) -> None:
        result = make_pipeline(self.snapshot, with_executor=True).run(
            self.snapshot.symbol,
            self.portfolio,
            execute=True,
        )
        self.assertIsNotNone(result.execution)
        self.assertEqual(result.execution.status, "paper_filled")

    def test_unauthorized_zone_vetoes_new_position(self) -> None:
        blocked = MarketSnapshot(
            symbol="SYNTH_B",
            close=100.0,
            moving_average=96.0,
            atr=2.0,
            volume_ratio=1.5,
            regime=Regime.BULL,
            zone_authorized=False,
        )
        result = make_pipeline(blocked).run(blocked.symbol, self.portfolio)
        self.assertEqual(result.signal.action.value, "VETO")
        self.assertEqual(result.target.incremental_notional, 0)
        self.assertIsNone(result.order)


if __name__ == "__main__":
    unittest.main()
