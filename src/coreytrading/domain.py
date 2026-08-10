"""Domain objects shared by every stage of the public pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping


class Regime(str, Enum):
    BULL = "bull"
    NEUTRAL = "neutral"
    BEAR = "bear"


class SignalAction(str, Enum):
    GO = "GO"
    WAIT = "WAIT"
    VETO = "VETO"


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass(frozen=True)
class MarketSnapshot:
    """A small, vendor-neutral input record for one instrument."""

    symbol: str
    close: float
    moving_average: float
    atr: float
    volume_ratio: float
    regime: Regime = Regime.NEUTRAL
    zone_authorized: bool = True

    def __post_init__(self) -> None:
        if not self.symbol.strip():
            raise ValueError("symbol must not be empty")
        if self.close <= 0 or self.moving_average <= 0:
            raise ValueError("prices must be positive")
        if self.atr <= 0:
            raise ValueError("atr must be positive")
        if self.volume_ratio < 0:
            raise ValueError("volume_ratio must not be negative")


@dataclass(frozen=True)
class ModelFeatures:
    trend_strength: float
    normalized_distance: float
    volume_confirmation: float
    regime_bias: float


@dataclass(frozen=True)
class Prediction:
    """A current-state score, not a forecast of future returns."""

    score: float
    confidence: float
    label: str

    def __post_init__(self) -> None:
        if not 0 <= self.score <= 100:
            raise ValueError("score must be between 0 and 100")
        if not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")


@dataclass(frozen=True)
class Signal:
    action: SignalAction
    strength: float
    reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not 0 <= self.strength <= 1:
            raise ValueError("strength must be between 0 and 1")


@dataclass(frozen=True)
class PortfolioState:
    nav: float
    gross_exposure: float
    position_notionals: Mapping[str, float] = field(default_factory=dict)
    position_rounds: Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.nav <= 0:
            raise ValueError("nav must be positive")
        if self.gross_exposure < 0:
            raise ValueError("gross_exposure must not be negative")
        if any(value < 0 for value in self.position_notionals.values()):
            raise ValueError("position notionals must not be negative")
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 1
            for value in self.position_rounds.values()
        ):
            raise ValueError("position rounds must be positive integers")

    def notional_for(self, symbol: str) -> float:
        return float(self.position_notionals.get(symbol, 0.0))

    def round_for(self, symbol: str) -> int:
        """Return the one-based construction round for ``symbol``."""

        return self.position_rounds.get(symbol, 1)


@dataclass(frozen=True)
class TargetPosition:
    symbol: str
    current_notional: float
    incremental_notional: float
    target_notional: float
    reference_price: float


@dataclass(frozen=True)
class AdjustedTargetPosition:
    symbol: str
    current_notional: float
    target_notional: float
    target_quantity: int
    reference_price: float
    binding_reasons: tuple[str, ...]


@dataclass(frozen=True)
class OrderIntent:
    client_id: str
    symbol: str
    side: OrderSide
    quantity: int
    limit_price: float
    rationale: tuple[str, ...]


@dataclass(frozen=True)
class ExecutionReport:
    client_id: str
    status: str
    filled_quantity: int
    average_price: float | None


@dataclass(frozen=True)
class PipelineResult:
    snapshot: MarketSnapshot
    features: ModelFeatures
    prediction: Prediction
    signal: Signal
    target: TargetPosition
    adjusted_target: AdjustedTargetPosition
    order: OrderIntent | None
    execution: ExecutionReport | None
