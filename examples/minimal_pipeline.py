from coreytrading import (
    IncrementalPortfolioConstructor,
    InMemoryDataSource,
    MarketSnapshot,
    OrderPlanner,
    PortfolioState,
    Regime,
    RiskEngine,
    TechnicalScoreModel,
    ThresholdStrategy,
    TradingPipeline,
)


snapshot = MarketSnapshot(
    symbol="SYNTH_A",
    close=100.0,
    moving_average=96.0,
    atr=2.0,
    volume_ratio=1.5,
    regime=Regime.BULL,
)

pipeline = TradingPipeline(
    data_source=InMemoryDataSource({snapshot.symbol: snapshot}),
    model=TechnicalScoreModel(),
    strategy=ThresholdStrategy(),
    constructor=IncrementalPortfolioConstructor(),
    risk=RiskEngine(),
    order_planner=OrderPlanner(),
)

result = pipeline.run(
    snapshot.symbol,
    PortfolioState(nav=100_000.0, gross_exposure=20_000.0),
)

print(result)
