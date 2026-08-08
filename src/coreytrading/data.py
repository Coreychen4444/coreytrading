"""Data-source interfaces. No vendor or account adapter is bundled."""

from __future__ import annotations

from typing import Mapping, Protocol

from .domain import MarketSnapshot


class DataSource(Protocol):
    def snapshot(self, symbol: str) -> MarketSnapshot:
        """Return the latest complete snapshot for ``symbol``."""


class InMemoryDataSource:
    """Deterministic source for examples, tests, and research notebooks."""

    def __init__(self, snapshots: Mapping[str, MarketSnapshot]) -> None:
        self._snapshots = dict(snapshots)

    def snapshot(self, symbol: str) -> MarketSnapshot:
        try:
            return self._snapshots[symbol]
        except KeyError as exc:
            raise KeyError(f"no snapshot configured for {symbol!r}") from exc
