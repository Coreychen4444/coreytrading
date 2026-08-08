"""Public API for the CoreyTrading reference pipeline."""

from .data import DataSource, InMemoryDataSource
from .domain import (
    AdjustedTargetPosition,
    ExecutionReport,
    MarketSnapshot,
    ModelFeatures,
    OrderIntent,
    OrderSide,
    PipelineResult,
    PortfolioState,
    Prediction,
    Regime,
    Signal,
    SignalAction,
    TargetPosition,
)
from .execution import ExecutionAdapter, OrderConfig, OrderPlanner, PaperExecutor
from .model import ScoreModel, TechnicalScoreModel
from .pipeline import TradingPipeline
from .portfolio import ConstructionConfig, IncrementalPortfolioConstructor
from .risk import RiskEngine, RiskLimits
from .strategy import StrategyConfig, ThresholdStrategy

__all__ = [
    "AdjustedTargetPosition",
    "ConstructionConfig",
    "DataSource",
    "ExecutionAdapter",
    "ExecutionReport",
    "InMemoryDataSource",
    "IncrementalPortfolioConstructor",
    "MarketSnapshot",
    "ModelFeatures",
    "OrderConfig",
    "OrderIntent",
    "OrderPlanner",
    "OrderSide",
    "PaperExecutor",
    "PipelineResult",
    "PortfolioState",
    "Prediction",
    "Regime",
    "RiskEngine",
    "RiskLimits",
    "ScoreModel",
    "Signal",
    "SignalAction",
    "StrategyConfig",
    "TargetPosition",
    "TechnicalScoreModel",
    "ThresholdStrategy",
    "TradingPipeline",
]
