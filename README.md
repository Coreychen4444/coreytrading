# CoreyTrading

A small, deterministic, broker-agnostic reference implementation of a trading
decision pipeline. It focuses on explainable state scoring, explicit strategy
authorization, constraint-based position construction, and risk-adjusted order
intent.

[中文说明](README_CN.md)

> This project is educational software, not investment advice. Defaults and
> examples are illustrative and must not be treated as production parameters.

## Architecture

```text
Data
  ↓
Model
  ↓
Prediction / Score
  ↓
Strategy
  ↓
Signal
  ↓
Portfolio Construction
  ↓
Target Position
  ↓
Risk
  ↓
Adjusted Target Position
  ↓
Order
  ↓
Execution
```

| Stage | Public implementation | Output |
|---|---|---|
| Data | Vendor-neutral `DataSource`; in-memory source for tests | `MarketSnapshot` |
| Model | Deterministic trend, distance, volume, and regime features | `ModelFeatures` |
| Prediction / Score | Explainable current-state score; not a return forecast | `Prediction` |
| Strategy | Zone, regime, and score authorization | `GO`, `WAIT`, or `VETO` |
| Portfolio Construction | Conviction-scaled incremental allocation | `TargetPosition` |
| Risk | Single-position, gross-exposure, stop-risk, and whole-unit constraints | `AdjustedTargetPosition` |
| Order | Deterministic limit-order intent | `OrderIntent` |
| Execution | Explicit adapter boundary; paper executor included | `ExecutionReport` |

The default pipeline stops at order intent. Execution requires both an explicit
adapter and `execute=True`, keeping human review or an external approval system
in the loop.

## Design boundaries

- The score classifies current technical alignment; it does not claim to
  predict expected return.
- Portfolio construction is constraint-based, not a covariance optimizer.
- A target is built for the current decision rather than as a global optimizer
  over every asset.
- No broker credentials, account adapters, market datasets, live positions, or
  autonomous order submission are included.
- Every bundled example and test uses synthetic inputs.

## Quick start

The package has no runtime dependencies and requires Python 3.11 or newer.

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
python examples/minimal_pipeline.py
```

Run the test suite:

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

## Example

```python
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
print(result.order)
```

## License

MIT
