"""Constraint-friendly portfolio construction for one incremental decision."""

from __future__ import annotations

from dataclasses import dataclass

from .domain import MarketSnapshot, PortfolioState, Prediction, Signal, SignalAction, TargetPosition


@dataclass(frozen=True)
class ConstructionConfig:
    base_allocation_fraction: float = 0.05

    def __post_init__(self) -> None:
        if not 0 < self.base_allocation_fraction <= 1:
            raise ValueError("base_allocation_fraction must be in (0, 1]")


class IncrementalPortfolioConstructor:
    """Build a requested position before portfolio risk constraints are applied."""

    def __init__(self, config: ConstructionConfig | None = None) -> None:
        self.config = config or ConstructionConfig()

    def construct(
        self,
        snapshot: MarketSnapshot,
        prediction: Prediction,
        signal: Signal,
        portfolio: PortfolioState,
    ) -> TargetPosition:
        current = portfolio.notional_for(snapshot.symbol)
        if signal.action is not SignalAction.GO:
            incremental = 0.0
        else:
            conviction = max(0.0, min(1.0, (prediction.score - 50.0) / 50.0))
            requested_total = portfolio.nav * self.config.base_allocation_fraction * (0.5 + 0.5 * conviction)
            incremental = max(0.0, requested_total - current)
        target = current + incremental
        return TargetPosition(
            symbol=snapshot.symbol,
            current_notional=current,
            incremental_notional=incremental,
            target_notional=target,
            reference_price=snapshot.close,
        )
