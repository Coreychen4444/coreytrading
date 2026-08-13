"""Numeric coercion for broker/API values ('--', '1,234.5', '12.3%', None)."""
from __future__ import annotations


def coerce_float(v, default: float | None = None) -> float | None:
    """Best-effort float. Returns `default` (None by default) when the value
    is missing/unparseable — callers must distinguish 0.0 from missing."""
    if v is None:
        return default
    if isinstance(v, (int, float)):
        try:
            f = float(v)
        except (ValueError, OverflowError):
            return default
        return f if f == f else default  # NaN -> default
    s = str(v).strip().replace(",", "").rstrip("%")
    if not s or s in {"--", "-", "N/A", "n/a", "None"}:
        return default
    try:
        return float(s)
    except ValueError:
        return default
