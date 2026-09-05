"""Provider clock normalization never turns retrieval time into observation time."""
import pytest

from quant.core.quote_times import opend_quote_time


@pytest.mark.parametrize("raw,expected", [
    ("2026-09-04 20:02:20.044", "2026-09-04T20:02:20.044000-04:00"),
    ("2026-12-01 16:00:00", "2026-12-01T16:00:00-05:00"),
    ("2026-09-04T20:00:00Z", "2026-09-04T20:00:00+00:00"),
    ("2026-11-01T01:30:00-04:00", "2026-11-01T01:30:00-04:00"),
])
def test_documented_us_opend_clock(raw, expected):
    assert opend_quote_time(raw) == expected


@pytest.mark.parametrize("raw", ["2026-11-01 01:30:00", "2026-03-08 02:30:00",
                                "2026-09-04", "", None, "not a timestamp"])
def test_missing_ambiguous_and_nonexistent_times_are_unknown(raw):
    assert opend_quote_time(raw) is None


def test_market_must_be_explicit_and_supported():
    assert opend_quote_time("2026-09-04 16:00:00", market="HK") == "2026-09-04T16:00:00+08:00"
    assert opend_quote_time("2026-09-04 16:00:00", market="unknown") is None
