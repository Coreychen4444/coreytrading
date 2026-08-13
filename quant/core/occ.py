"""OCC-style option ticker parsing — the single implementation.

System convention (matches moomoo code minus 'US.' prefix):
    <UNDERLYING><YYMMDD><C|P><STRIKE*1000>
    e.g. NOK270115C15000 = NOK  Call $15.00
"""
from __future__ import annotations

import re
from typing import TypedDict

OCC_RE = re.compile(r"^(?P<u>[A-Z.]+?)(?P<yy>\d{2})(?P<mm>\d{2})(?P<dd>\d{2})(?P<cp>[CP])(?P<strike>\d+)$")


class OptionParts(TypedDict):
    underlying: str
    expiry: str       # YYYY-MM-DD
    side: str         # 'C' | 'P'
    strike: float
    multiplier: int


def is_option_ticker(ticker: str) -> bool:
    return bool(OCC_RE.match(str(ticker or "")))


def parse_occ(ticker: str) -> OptionParts | None:
    m = OCC_RE.match(str(ticker or ""))
    if not m:
        return None
    year = 2000 + int(m.group("yy"))
    return {
        "underlying": m.group("u"),
        "expiry": f"{year}-{m.group('mm')}-{m.group('dd')}",
        "side": m.group("cp"),
        "strike": int(m.group("strike")) / 1000.0,
        "multiplier": 100,
    }


def contract_multiplier(ticker: str) -> int:
    """PnL multiplier: 100 for option contracts, 1 for stock."""
    return 100 if is_option_ticker(ticker) else 1


def strip_us_prefix(code: str) -> str:
    return re.sub(r"^US\.", "", str(code or ""))
