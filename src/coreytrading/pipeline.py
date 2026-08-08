"""End-to-end orchestration for the public reference system."""

from __future__ import annotations

from .data import DataSource
from .domain import PipelineResult, PortfolioState
from .execution import ExecutionAdapter, OrderPlanner
from .model import ScoreModel
from .portfolio import IncrementalPortfolioConstructor
from .risk import RiskEngine
from .strategy import ThresholdStrategy


class TradingPipeline:
    def __init__(
        self,
        *,
        data_source: DataSource,
        model: ScoreModel,
        strategy: ThresholdStrategy,
        constructor: IncrementalPortfolioConstructor,
        risk: RiskEngine,
        order_planner: OrderPlanner,
        executor: ExecutionAdapter | None = None,
    ) -> None:
        self.data_source = data_source
        self.model = model
        self.strategy = strategy
        self.constructor = constructor
        self.risk = risk
        self.order_planner = order_planner
        self.executor = executor

    def run(self, symbol: str, portfolio: PortfolioState, *, execute: bool = False) -> PipelineResult:
        snapshot = self.data_source.snapshot(symbol)
        features = self.model.features(snapshot)
        prediction = self.model.predict(features)
        signal = self.strategy.signal(snapshot, prediction)
        target = self.constructor.construct(snapshot, prediction, signal, portfolio)
        adjusted = self.risk.adjust(snapshot, target, portfolio)
        order = self.order_planner.plan(adjusted)
        execution = None
        if execute:
            if self.executor is None:
                raise RuntimeError("execute=True requires an explicit execution adapter")
            if order is not None:
                execution = self.executor.submit(order)
        return PipelineResult(
            snapshot=snapshot,
            features=features,
            prediction=prediction,
            signal=signal,
            target=target,
            adjusted_target=adjusted,
            order=order,
            execution=execution,
        )
