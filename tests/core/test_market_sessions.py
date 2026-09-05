"""NYSE published schedules are external fixtures, not copied calendar formulas."""
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from quant.core import market_sessions as sessions


# NYSE official table, verified :
# https://www.nyse.com/trade/hours-calendars
# Deliberately enumerate the external schedule so an upstream library change
# cannot silently introduce July 2 early closes or observed Dec 31 holidays.
PUBLISHED = {
    2026: {"closed": "01-01 01-19 02-16 04-03 05-25 06-19 07-03 09-07 11-26 12-25",
           "early": "11-27 12-24"},
    2027: {"closed": "01-01 01-18 02-15 03-26 05-31 06-18 07-05 09-06 11-25 12-24",
           "early": "11-26"},
    2028: {"closed": "01-17 02-21 04-14 05-29 06-19 07-04 09-04 11-23 12-25",
           "early": "07-03 11-24"},
}


@pytest.mark.parametrize("year", PUBLISHED)
def test_full_published_years_match_holidays_and_early_closes(year):
    closed, early = set(), set()
    day = date(year, 1, 1)
    while day.year == year:
        close = sessions.session_close(day)
        if day.weekday() > 4:
            assert close is None
        elif close is None:
            closed.add(day.strftime("%m-%d"))
        else:
            assert close.tzinfo == timezone.utc
            et = close.astimezone(ZoneInfo("America/New_York"))
            assert et.date() == day and et.minute == 0 and et.hour in (13, 16)
            if et.hour == 13:
                early.add(day.strftime("%m-%d"))
        day += timedelta(days=1)
    assert closed == set(PUBLISHED[year]["closed"].split())
    assert early == set(PUBLISHED[year]["early"].split())


@pytest.mark.parametrize("now,expected", [
    ("2026-09-05T04:30:00Z", "2026-09-04"),
    ("2026-09-06T20:30:00Z", "2026-09-04"),
    ("2026-09-07T21:30:00Z", "2026-09-04"),
    ("2026-09-08T19:59:59Z", "2026-09-04"),
    ("2026-09-08T20:00:00Z", "2026-09-08"),
    ("2026-11-27T17:59:59Z", "2026-11-25"),
    ("2026-11-27T18:00:00Z", "2026-11-27"),
    ("2027-09-06T21:30:00Z", "2027-09-03"),
    ("2027-11-26T17:59:59Z", "2027-11-24"),
    ("2027-11-26T18:00:00Z", "2027-11-26"),
    ("2027-01-01T21:30:00Z", "2026-12-31"),
])
def test_latest_completed_session_respects_real_closes_and_holidays(now, expected):
    clock = datetime.fromisoformat(now.replace("Z", "+00:00"))
    assert sessions.latest_completed_session(clock) == date.fromisoformat(expected)
    assert sessions.latest_completed_session(clock.astimezone(ZoneInfo("Asia/Singapore"))) == date.fromisoformat(expected)


@pytest.mark.parametrize("day,utc_hour", [
    ("2026-03-06", 21), ("2026-03-09", 20),
    ("2026-10-30", 20), ("2026-11-02", 21),
    ("2027-03-12", 21), ("2027-03-15", 20),
    ("2027-11-05", 20), ("2027-11-08", 21),
])
def test_dst_changes_utc_close_without_changing_exchange_wall_time(day, utc_hour):
    close = sessions.session_close(day)
    assert close.hour == utc_hour
    assert close.astimezone(ZoneInfo("America/New_York")).hour == 16


@pytest.mark.parametrize("day", ["2029-01-02", "1999-12-31", "2026-02-30", "2026-9-04",
                                  None, datetime(2026, 9, 4)])
def test_unsupported_or_ambiguous_dates_raise(day):
    with pytest.raises(sessions.MarketSessionError):
        sessions.session_close(day)


def test_explicit_aware_clock_and_supported_horizon_are_required():
    for value in (None, datetime(2026, 9, 4), datetime(2029, 1, 2, tzinfo=timezone.utc)):
        with pytest.raises(sessions.MarketSessionError):
            sessions.latest_completed_session(value)


def test_changed_dependency_version_has_no_weekday_fallback(monkeypatch):
    import exchange_calendars
    sessions._calendar.cache_clear()
    try:
        monkeypatch.setattr(exchange_calendars, "__version__", "unreviewed")
        with pytest.raises(sessions.MarketSessionError, match="version requires validation"):
            sessions.session_close("2026-09-04")
    finally:
        sessions._calendar.cache_clear()


def test_metadata_never_claims_a_live_emergency_closure_feed():
    metadata = sessions.calendar_metadata()
    assert metadata["emergency_closures_refreshed"] is False
    assert metadata["scope"] == "regular_cash_equity"
    metadata["official_years_verified"].clear()
    assert sessions.calendar_metadata()["official_years_verified"] == [2026, 2027, 2028]


@pytest.mark.parametrize("kind,minutes", [("postclose", 10), ("ladder", 20), ("chart", 35)])
@pytest.mark.parametrize("day", ["2026-09-04", "2026-11-02", "2026-11-27", "2026-12-24"])
def test_postclose_jobs_follow_actual_dst_and_halfday_closes(kind, minutes, day):
    close = sessions.session_close(day)
    due = sessions.scheduled_session_due(kind, close + timedelta(minutes=minutes))
    assert due == {"due": True, "reason": "postclose_window", "session": day, "close": close.isoformat()}
    assert not sessions.scheduled_session_due(kind, close + timedelta(minutes=minutes, seconds=-1))["due"]
    assert not sessions.scheduled_session_due(kind, close + timedelta(minutes=60))["due"]
    assert sessions.scheduled_session_due(kind, close + timedelta(minutes=59, seconds=59))["due"]


def test_session_day_jobs_skip_labor_day_and_briefing_stops_at_open():
    holiday = datetime(2026, 9, 7, 20, tzinfo=timezone.utc)
    for kind in ("heartbeat", "briefing", "postclose", "ladder", "chart"):
        out = sessions.scheduled_session_due(kind, holiday)
        assert out["due"] is False and out["reason"] == "non_session_day"
        assert out["close"] is None
    opened = sessions.session_open("2026-09-08")
    assert sessions.scheduled_session_due("heartbeat", opened + timedelta(hours=8))["due"]
    assert sessions.scheduled_session_due("briefing", opened - timedelta(seconds=1))["due"]
    assert not sessions.scheduled_session_due("briefing", opened)["due"]
    assert sessions.session_open("2026-09-07") is None
    assert sessions.scheduled_session_due("weekly", holiday)["due"]


def test_future_calendar_gate_is_closed_except_calendar_independent_weekly():
    future = datetime(2029, 1, 2, 20, tzinfo=timezone.utc)
    assert sessions.scheduled_session_due("heartbeat", future)["due"] is False
    assert sessions.scheduled_session_due("weekly", future)["due"] is True
    with pytest.raises(sessions.MarketSessionError):
        sessions.scheduled_session_due("anything", future)
