"""Risk caps and whole-unit adjustment for requested target positions."""

from __future__ import annotations

from dataclasses import dataclass
import math

from .domain import AdjustedTargetPosition, MarketSnapshot, PortfolioState, TargetPosition


@dataclass(frozen=True)
class RiskLimits:
    max_single_position_fraction: float = 0.10
    max_gross_exposure_fraction: float = 1.00
    risk_per_trade_fraction: float = 0.01
    stop_distance_atr: float = 2.0

    def __post_init__(self) -> None:
        fractions = (
            self.max_single_position_fraction,
            self.max_gross_exposure_fraction,
            self.risk_per_trade_fraction,
        )
        if any(value <= 0 for value in fractions):
            raise ValueError("risk fractions must be positive")
        if self.stop_distance_atr <= 0:
            raise ValueError("stop_distance_atr must be positive")


class RiskEngine:
    def __init__(self, limits: RiskLimits | None = None) -> None:
        self.limits = limits or RiskLimits()

    def adjust(
        self,
        snapshot: MarketSnapshot,
        target: TargetPosition,
        portfolio: PortfolioState,
    ) -> AdjustedTargetPosition:
        gross_room = max(
            0.0,
            portfolio.nav * self.limits.max_gross_exposure_fraction - portfolio.gross_exposure,
        )
        caps = {
            "requested": target.target_notional,
            "single_position": portfolio.nav * self.limits.max_single_position_fraction,
            "gross_exposure": target.current_notional + gross_room,
            "stop_risk": (
                portfolio.nav
                * self.limits.risk_per_trade_fraction
                * snapshot.close
                / (snapshot.atr * self.limits.stop_distance_atr)
            ),
        }
        adjusted_cap = max(target.current_notional, min(caps.values()))
        quantity = math.floor(adjusted_cap / snapshot.close)
        target_notional = quantity * snapshot.close
        minimum = min(caps.values())
        reasons = tuple(name for name, value in caps.items() if math.isclose(value, minimum, rel_tol=1e-9, abs_tol=1e-9))
        if target.incremental_notional == 0:
            reasons = ("no_new_signal",)
        return AdjustedTargetPosition(
            symbol=target.symbol,
            current_notional=target.current_notional,
            target_notional=target_notional,
            target_quantity=quantity,
            reference_price=snapshot.close,
            binding_reasons=reasons,
        )
