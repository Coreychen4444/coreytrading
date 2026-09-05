"""Deterministic XNYS regular-equity sessions, with no network or state access.

Calendar rules belong to exchange_calendars, not a weekday/holiday approximation.
The public query range is ... Historical rules come from the
pinned library; 2026-2028 holidays/early closes are tested against NYSE's published
table, checked : https://www.nyse.com/trade/hours-calendars

This is the regular cash-equity session only: no options late closes, extended
hours or future emergency-closure feed. A scheduled close is necessary evidence
for a completed daily bar, not proof that a vendor has finalized that bar.
Outside the supported range or without the dependency, callers must fail closed.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
import re
from zoneinfo import ZoneInfo

CALENDAR_NAME = "XNYS"
CALENDAR_VERSION = "4.13.2"
SUPPORTED_START = date(2000, 1, 1)
SUPPORTED_END = date(2028, 12, 31)
OFFICIAL_SOURCE = "https://www.nyse.com/trade/hours-calendars"
ET = ZoneInfo("America/New_York")


class MarketSessionError(ValueError):
    """The requested session cannot be established by the supported calendar."""


def _day(value: date | str) -> date:
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        try:
            value = date.fromisoformat(value)
        except ValueError:
            raise MarketSessionError("session date must be a valid YYYY-MM-DD") from None
    if type(value) is not date:
        raise MarketSessionError("session date must be a date or YYYY-MM-DD")
    if not SUPPORTED_START <= value <= SUPPORTED_END:
        raise MarketSessionError(f"session outside supported range {SUPPORTED_START}..{SUPPORTED_END}")
    return value


@lru_cache(maxsize=1)
def _calendar():
    try:
        import exchange_calendars
    except ImportError as exc:
        raise MarketSessionError("exchange-calendars dependency unavailable; no weekday fallback") from exc
    if exchange_calendars.__version__ != CALENDAR_VERSION:
        raise MarketSessionError(f"calendar version requires validation: expected {CALENDAR_VERSION}")
    try:
        # Boundary padding lets the library answer weekends around the public
        # endpoints; it does not extend the adapter's advertised query range.
        return exchange_calendars.get_calendar(CALENDAR_NAME, start="1999-12-01", end="2029-01-31")
    except Exception as exc:
        raise MarketSessionError("XNYS calendar could not be constructed") from exc


def session_close(day: date | str) -> datetime | None:
    """Return the regular equity close in UTC; a holiday/weekend returns None."""
    day = _day(day)
    calendar = _calendar()
    if not calendar.is_session(day.isoformat()):
        return None
    return calendar.session_close(day.isoformat()).to_pydatetime().astimezone(timezone.utc)


def session_open(day: date | str) -> datetime | None:
    """Return regular equity open in UTC; a holiday/weekend returns None."""
    day = _day(day)
    calendar = _calendar()
    if not calendar.is_session(day.isoformat()):
        return None
    return calendar.session_open(day.isoformat()).to_pydatetime().astimezone(timezone.utc)


def latest_completed_session(now: datetime) -> date:
    """Latest scheduled session whose close is <= an explicit aware timestamp.

    Exactly at the closing time the session is complete; vendor-finality flags
    remain the caller's independent responsibility. TTLs are not extended.
    """
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise MarketSessionError("now must be a timezone-aware datetime")
    day = _day(now.astimezone(ET).date())
    calendar = _calendar()
    session = calendar.date_to_session(day.isoformat(), direction="previous")
    if calendar.session_close(session).to_pydatetime() > now:
        session = calendar.previous_session(session)
    result = session.date()
    if result < SUPPORTED_START:
        raise MarketSessionError("no completed session inside supported range")
    return result


def calendar_metadata() -> dict:
    """Detached provenance for frozen evidence; no claim of live market status."""
    return {"exchange": CALENDAR_NAME, "scope": "regular_cash_equity",
            "library": "exchange_calendars", "library_version": CALENDAR_VERSION,
            "supported_start": SUPPORTED_START.isoformat(), "supported_end": SUPPORTED_END.isoformat(),
            "official_source": OFFICIAL_SOURCE, "official_years_verified": [2026, 2027, 2028],
            "verified_on": "2026-09-05", "emergency_closures_refreshed": False}


def scheduled_session_due(kind: str, now: datetime) -> dict:
    """Calendar/time gate for host jobs; this never registers or executes a job.

    Host cadence: postclose/ladder/chart run in [close+10/20/35m, close+60m).
    Heartbeat is allowed on an ET session date, briefing only before its open,
    and weekly is calendar-independent. Idempotency belongs to the job runtime.
    """
    kinds = {"heartbeat", "briefing", "postclose", "ladder", "chart", "weekly"}
    if kind not in kinds:
        raise MarketSessionError("unsupported scheduled-session kind")
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise MarketSessionError("now must be a timezone-aware datetime")
    day = now.astimezone(ET).date()
    out = {"due": False, "reason": "non_session_day", "session": day.isoformat(), "close": None}
    if kind == "weekly":
        return {**out, "due": True, "reason": "weekly_calendar_independent"}
    try:
        close = session_close(day)
    except MarketSessionError as exc:
        return {**out, "reason": "calendar_unavailable: " + str(exc)}
    if close is None:
        return out
    out["close"] = close.isoformat()
    if kind == "heartbeat":
        return {**out, "due": True, "reason": "session_day"}
    if kind == "briefing":
        due = now < session_open(day)
        return {**out, "due": due, "reason": "before_session_open" if due else "session_already_opened"}
    offset = {"postclose": 10, "ladder": 20, "chart": 35}[kind]
    due = close + timedelta(minutes=offset) <= now < close + timedelta(minutes=60)
    return {**out, "due": due, "reason": "postclose_window" if due else "outside_postclose_window"}
