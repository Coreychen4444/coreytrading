"""Strategy policy that turns a score into an actionable signal."""

from __future__ import annotations

from dataclasses import dataclass

from .domain import MarketSnapshot, Prediction, Regime, Signal, SignalAction


@dataclass(frozen=True)
class StrategyConfig:
    go_score: float = 65.0
    veto_score: float = 35.0
    veto_bear_regime: bool = True

    def __post_init__(self) -> None:
        if not 0 <= self.veto_score < self.go_score <= 100:
            raise ValueError("thresholds must satisfy 0 <= veto < go <= 100")


class ThresholdStrategy:
    def __init__(self, config: StrategyConfig | None = None) -> None:
        self.config = config or StrategyConfig()

    def signal(self, snapshot: MarketSnapshot, prediction: Prediction) -> Signal:
        if not snapshot.zone_authorized:
            return Signal(SignalAction.VETO, 0.0, ("zone_not_authorized",))
        if self.config.veto_bear_regime and snapshot.regime is Regime.BEAR:
            return Signal(SignalAction.VETO, 0.0, ("bear_regime",))
        if prediction.score <= self.config.veto_score:
            return Signal(SignalAction.VETO, 0.0, ("score_below_veto",))
        if prediction.score >= self.config.go_score:
            strength = (prediction.score - self.config.go_score) / (100.0 - self.config.go_score)
            return Signal(
                SignalAction.GO,
                max(0.05, min(1.0, strength)),
                ("score_authorized", "zone_authorized"),
            )
        return Signal(SignalAction.WAIT, 0.0, ("insufficient_confirmation",))
