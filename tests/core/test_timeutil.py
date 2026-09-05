"""ET conversion is the timezone backbone of the ledger — DST must be exact."""
from datetime import datetime

import pytest

from quant.core.market_sessions import MarketSessionError
from quant.core.timeutil import et_session_open, et_session_state
from quant.core.timeutil import et_naive_to_iso, now_utc_iso, parse_ts


def test_winter_est_offset():
    assert et_naive_to_iso("2026-01-20 13:40:06.012") == "2026-01-20T13:40:06.012000-05:00"


def test_summer_edt_offset():
    assert et_naive_to_iso("2026-06-01 15:03:48.552") == "2026-06-01T15:03:48.552000-04:00"


def test_dst_boundary_2026():
    # US DST starts  02:00 ET
    assert et_naive_to_iso("2026-03-07 12:00:00").endswith("-05:00")
    assert et_naive_to_iso("2026-03-09 12:00:00").endswith("-04:00")


def test_date_only_and_garbage():
    assert et_naive_to_iso("2026-05-11") == "2026-05-11T00:00:00-04:00"
    assert et_naive_to_iso("") is None
    assert et_naive_to_iso("not-a-date") is None


def test_existing_offset_passthrough():
    assert et_naive_to_iso("2026-06-01T15:03:48+08:00") == "2026-06-01T15:03:48+08:00"


def test_parse_ts_handles_all_system_formats():
    z = parse_ts("2026-06-01T15:03:48Z")
    off = parse_ts("2026-06-01T11:03:48-04:00")
    assert z is not None and off is not None
    assert z == off  # same instant
    naive = parse_ts("2026-06-01 15:03:48")  # legacy naive → assumed ET
    assert naive is not None and naive.utcoffset() is not None


def test_now_utc_iso_is_zulu():
    assert now_utc_iso().endswith("Z")


@pytest.mark.parametrize("timestamp,expected", [
    ("2026-11-27T09:29:59-05:00", "pre"),
    ("2026-11-27T09:30:00-05:00", "open"),
    ("2026-11-27T12:59:59-05:00", "open"),
    ("2026-11-27T13:00:00-05:00", "closed"),
    ("2026-11-27T13:10:00-05:00", "closed"),
    ("2026-12-24T18:10:00+00:00", "closed"),
    ("2027-11-26T13:10:00-05:00", "closed"),
    ("2026-09-07T12:00:00-04:00", "weekend"),
    ("2027-09-06T16:10:00-04:00", "weekend"),
    ("2026-09-06T16:10:00-04:00", "weekend"),
    ("2026-03-06T20:59:59+00:00", "open"),
    ("2026-03-06T21:00:00+00:00", "closed"),
    ("2026-03-09T20:00:00+00:00", "closed"),
    ("2026-10-30T20:00:00+00:00", "closed"),
    ("2026-11-02T20:00:00+00:00", "open"),
    ("2026-11-02T21:00:00+00:00", "closed"),
    ("2026-11-28T02:10:00+08:00", "closed"),
])
def test_session_state_uses_exchange_calendar_and_input_timezone(timestamp, expected):
    now = datetime.fromisoformat(timestamp)
    assert et_session_state(now) == expected
    assert et_session_open(now) is (expected == "open")


def test_session_state_preserves_legacy_naive_et_input():
    assert et_session_state(datetime(2026, 11, 27, 13, 10)) == "closed"


def test_session_state_has_no_weekday_fallback_outside_calendar_range():
    with pytest.raises(MarketSessionError, match="outside supported range"):
        et_session_state(datetime.fromisoformat("2029-01-02T12:00:00-05:00"))
