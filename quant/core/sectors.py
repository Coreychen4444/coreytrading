"""Single source for symbol-to-relative-volume benchmark mapping.

Semiconductor-design, manufacturing, equipment, storage and related component
names use the sector benchmark; other and unknown names use the broad benchmark.
Private validation must import this table instead of copying it. Changing the
mapping changes live behavior and therefore requires a locked private study plus
production tests.
"""
from __future__ import annotations

SEMI_SMH = frozenset({
    "AAOI", "ADI", "AMAT", "AMD", "AMKR", "ASML", "AVGO", "AXTI", "INTC", "KLAC",
    "LITE", "LRCX", "MCHP", "MRVL", "MU", "NVDA", "ON", "QCOM", "QRVO", "SNDK",
    "SWKS", "TER", "TSM", "TXN",
})


def benchmark_for(ticker: str | None) -> str:
    """量比基准: 半导体 → SMH, 其余(含未知) → QQQ。"""
    return "SMH" if str(ticker or "").upper() in SEMI_SMH else "QQQ"
