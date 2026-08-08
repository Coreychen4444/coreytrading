"""Order planning plus an explicit, opt-in execution adapter."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Protocol
import uuid

from .domain import AdjustedTargetPosition, ExecutionReport, OrderIntent, OrderSide


@dataclass(frozen=True)
class OrderConfig:
    limit_offset_bps: float = 0.0


class OrderPlanner:
    def __init__(self, config: OrderConfig | None = None) -> None:
        self.config = config or OrderConfig()

    def plan(self, adjusted: AdjustedTargetPosition) -> OrderIntent | None:
        current_quantity = math.floor(adjusted.current_notional / adjusted.reference_price)
        difference = adjusted.target_quantity - current_quantity
        if difference == 0:
            return None
        side = OrderSide.BUY if difference > 0 else OrderSide.SELL
        direction = 1.0 if side is OrderSide.BUY else -1.0
        limit_price = adjusted.reference_price * (
            1.0 + direction * self.config.limit_offset_bps / 10_000.0
        )
        fingerprint = (
            f"{adjusted.symbol}:{side.value}:{abs(difference)}:{limit_price:.8f}:"
            f"{','.join(adjusted.binding_reasons)}"
        )
        return OrderIntent(
            client_id=str(uuid.uuid5(uuid.NAMESPACE_URL, fingerprint)),
            symbol=adjusted.symbol,
            side=side,
            quantity=abs(difference),
            limit_price=round(limit_price, 8),
            rationale=adjusted.binding_reasons,
        )


class ExecutionAdapter(Protocol):
    def submit(self, order: OrderIntent) -> ExecutionReport: ...


class PaperExecutor:
    """A deterministic executor for demonstrations; it never reaches a broker."""

    def submit(self, order: OrderIntent) -> ExecutionReport:
        return ExecutionReport(
            client_id=order.client_id,
            status="paper_filled",
            filled_quantity=order.quantity,
            average_price=order.limit_price,
        )
