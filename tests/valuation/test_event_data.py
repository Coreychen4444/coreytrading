"""Actual exchange sessions, surprise timing, price availability and anchor semantics."""
from copy import deepcopy
from datetime import date, timedelta
import math

import pytest

from quant.core.market_sessions import session_close
from quant.valuation import event_data


def prices():
    rows = []
    day = date(2026, 11, 25)
    for index in range(50):
        current = day + timedelta(days=index)
        close = session_close(current)
        if close is not None:
            rows.append({"session": current.isoformat(), "close": 100 + len(rows),
                         "available_at": (close + timedelta(minutes=5)).isoformat()})
    return {"ticker": "SYN", "source": "synthetic split-adjusted tape", "price_basis": "split_adjusted_price", "bars": rows}


def event():
    return {"event_id": "synthetic-policy", "ticker": "SYN", "event_at": "2026-11-27T16:00:00Z",
            "known_at": "2026-11-27T16:00:01Z", "source": "synthetic announcement",
            "actual_change_bp": -25, "expectation": {"change_bp": -25,
            "known_at": "2026-11-27T15:00:00Z", "source": "synthetic pre-event expectation"}}


def run(e=None, p=None, cutoff="2027-01-20T00:00:00Z"):
    return event_data.build([e or event()], p or prices(), as_of=cutoff)


def test_expected_cut_is_zero_surprise_and_half_day_anchor_precedes_next_session():
    built = run()
    row = built["events"][0]
    assert row["features"]["policy_surprise_bp"] == 0
    assert row["observed_at"] == "2026-11-27T18:00:00+00:00"
    assert row["returns"]["1"]["ends_at"] == "2026-11-30T21:00:00+00:00"
    assert row["returns"]["1"]["log_return"] == pytest.approx(math.log(102 / 101))
    assert row["anchor_convention"] == "first_close_after_information"


def test_after_hours_information_anchors_at_next_close_not_stale_pre_event_price():
    e = event()
    e["known_at"] = "2026-11-27T19:00:00Z"
    row = run(e)["events"][0]
    assert row["observed_at"] == "2026-11-30T21:00:00+00:00"
    assert row["returns"]["1"]["ends_at"] == "2026-12-01T21:00:00+00:00"


def test_missing_or_late_bar_never_changes_a_session_horizon():
    p = prices()
    p["bars"] = [b for b in p["bars"] if b["session"] != "2026-11-30"]
    built = run(p=p)
    assert built["events"][0]["returns"] == {}
    assert len(built["pending"]) == 3
    p = prices()
    p["bars"][2]["available_at"] = "2027-02-01T00:00:00Z"
    assert run(p=p)["events"][0]["returns"] == {}


def test_post_event_expectation_and_unadjusted_prices_are_rejected():
    e = event()
    e["expectation"]["known_at"] = e["known_at"]
    assert run(e)["rejected"]
    p = prices()
    p["price_basis"] = "unadjusted"
    with pytest.raises(ValueError, match="split-adjusted"):
        run(p=p)


def test_immature_outcomes_do_not_enter_training_and_duplicate_bars_fail_closed():
    result = run(cutoff="2026-11-27T18:10:00Z")
    assert result["events"][0]["returns"] == {}
    p = prices()
    p["bars"].append(deepcopy(p["bars"][1]))
    assert run(p=p)["rejected"][0]["reason"] == "anchor_price_missing_or_unavailable"
