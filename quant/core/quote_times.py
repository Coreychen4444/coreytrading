"""Normalize documented OpenD market timestamps without inventing observations.

US snapshot update_time and subscription data_date/data_time use US Eastern
wall time; HK/SH/SZ use Beijing time. Owner contracts checked :
https://openapi.moomoo.com/moomoo-api-doc/en/quote/get-market-snapshot.html
https://openapi.moomoo.com/moomoo-api-doc/en/quote/get-stock-quote.html
Retrieval time is separate. Date-only, DST-ambiguous and nonexistent wall times
remain unknown; the host clock cannot fill an absent quote observation.
"""
from __future__ import annotations

from datetime import datetime, timezone
import re
from zoneinfo import ZoneInfo


def opend_quote_time(raw, *, market="US"):
    """Return aware ISO time or None using an explicit provider market contract."""
    if not isinstance(raw, str) or not re.match(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}", raw):
        return None
    try:
        stamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is not None:
        return stamp.isoformat()
    zone = {"US": "America/New_York", "HK": "Asia/Shanghai", "SH": "Asia/Shanghai", "SZ": "Asia/Shanghai"}.get(market)
    if zone is None:
        return None
    tz = ZoneInfo(zone)
    choices = [stamp.replace(tzinfo=tz, fold=fold) for fold in (0, 1)]
    choices = [choice for choice in choices if choice.astimezone(timezone.utc).astimezone(tz).replace(tzinfo=None) == stamp]
    if not choices or len({choice.utcoffset() for choice in choices}) != 1:
        return None
    return choices[0].isoformat()
