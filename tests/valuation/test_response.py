"""Chronology, independent events and non-fabricated short-horizon prices."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import random

import pytest

from quant.valuation import response


def events(n=120):
    rng = random.Random(7)
    origin = datetime(2000, 1, 3, tzinfo=timezone.utc)
    rows = []
    for index in range(n):
        anchor = origin + timedelta(days=index * 35)
        surprise = rng.uniform(-100, 100)
        outcomes = {}
        for horizon in response.HORIZONS:
            end = anchor + timedelta(days=horizon + 8)
            outcomes[str(horizon)] = {"sessions": horizon, "log_return": -.001 * surprise + rng.gauss(0, .01),
                                     "ends_at": end.isoformat(), "available_at": (end + timedelta(hours=1)).isoformat()}
        rows.append({"event_id": f"event-{index}", "ticker": "SYN", "observed_at": anchor.isoformat(),
                     "known_at": anchor.isoformat(), "feature_definition": "pre_anchor_information",
                     "features": {"policy_surprise_bp": surprise}, "source": "synthetic event source",
                     "return_basis": "split_adjusted_price", "returns": outcomes})
    return rows


def run(rows, features=None, as_of="2015-01-01T00:00:00Z"):
    return response.forecast(rows, features=features or {"policy_surprise_bp": -25},
                             spot=100, as_of=as_of, feature_source="synthetic conditional scenario")


def test_conditional_rate_surprise_with_independent_calibration():
    result = run(events())
    for horizon in response.HORIZONS:
        prediction = result["windows"][str(horizon)]
        assert prediction["status"] == "estimated"
        assert 101 < prediction["price"] < 105
        assert prediction["low"] < prediction["price"] < prediction["high"]
        assert prediction["calibration_mae"] < prediction["baseline_calibration_mae"]
        assert prediction["training_last_anchor"] < prediction["calibration_first_anchor"]


def test_future_outcomes_do_not_change_a_historical_prediction():
    rows = events()
    cutoff = rows[90]["observed_at"]
    first = run(rows, as_of=cutoff)
    modified = deepcopy(rows)
    for row in modified[90:]:
        for outcome in row["returns"].values():
            outcome["log_return"] = 4
    second = run(modified, as_of=cutoff)
    for horizon in ("1", "5", "20"):
        assert first["windows"][horizon]["price"] == second["windows"][horizon]["price"]


def test_future_duplicate_cannot_hide_an_eligible_historical_sample():
    rows = events()
    future = deepcopy(rows[0])
    future["known_at"] = "2020-01-01T00:00:00Z"
    before = run(rows)
    after = run([future] + rows)
    for horizon in ("1", "5", "20"):
        assert before["windows"][horizon]["price"] == after["windows"][horizon]["price"]


def test_no_data_or_extrapolation_returns_unknown():
    assert all(r["status"] == "unknown" for r in run([])["windows"].values())
    outside = run(events(), features={"policy_surprise_bp": -1000})
    assert all(r["reason"] == "query_outside_observed_support" for r in outside["windows"].values())


def test_features_known_after_anchor_and_unknown_return_basis_are_excluded():
    rows = events(65)
    rows[0]["known_at"] = "2020-01-01T00:00:00Z"
    rows[1]["return_basis"] = "unadjusted"
    result = run(rows)
    excluded = result["windows"]["1"]["excluded"]
    assert excluded["features_not_available_at_anchor"] == 1
    assert excluded["return_basis_unverified"] == 1


def test_collinear_factors_are_not_given_separate_coefficients():
    rows = events(120)
    for row in rows:
        row["features"]["same_source"] = row["features"]["policy_surprise_bp"] * 2
    result = run(rows, features={"policy_surprise_bp": -25, "same_source": -50})
    assert all(r["reason"] == "collinear_factors" for r in result["windows"].values())


def test_walk_forward_scores_untouched_test_period_against_zero_return():
    rows = events(120)
    result = response.walk_forward(rows, feature_names=["policy_surprise_bp"], test_start=rows[100]["observed_at"])
    assert result["errors"] == []
    for stats in result["windows"].values():
        assert stats["test_n"] >= 15
        assert stats["mae"] < stats["baseline_mae"]
        assert 0 <= stats["coverage"] <= 1


@pytest.mark.parametrize("value", [True, float("nan"), float("inf")])
def test_invalid_query_values_fail(value):
    with pytest.raises(ValueError):
        run(events(), features={"policy_surprise_bp": value})


def test_unverified_session_count_and_late_test_features_are_not_scored():
    rows = events()
    rows[0]["returns"]["1"].pop("sessions")
    assert run(rows)["windows"]["1"]["excluded"]["trading_session_horizon_unverified"] == 1
    rows[100]["known_at"] = "2020-01-01T00:00:00Z"
    tested = response.walk_forward(rows, feature_names=["policy_surprise_bp"], test_start=rows[100]["observed_at"])
    assert tested["errors"]
    assert all(r["event_id"] != rows[100]["event_id"] for r in tested["rows"])


def test_mixed_tickers_or_anchor_definitions_require_an_explicit_cohort():
    rows = events()
    other = deepcopy(rows[0])
    other["ticker"] = "OTHER"
    with pytest.raises(ValueError, match="mixed"):
        run(rows + [other])
    for row in rows:
        row.update(feature_schema="synthetic:v1", anchor_convention="synthetic_close")
    cohort = {"ticker": "SYN", "feature_schema": "synthetic:v1", "anchor_convention": "synthetic_close"}
    selected = response.forecast(rows + [other], features={"policy_surprise_bp": -25}, spot=100,
                                 as_of="2015-01-01T00:00:00Z", feature_source="synthetic", cohort=cohort)
    assert selected["windows"]["1"]["status"] == "estimated"


def test_walk_forward_cutoff_does_not_score_future_labels():
    rows = events()
    cutoff = rows[105]["observed_at"]
    result = response.walk_forward(rows, feature_names=["policy_surprise_bp"],
                                   test_start=rows[100]["observed_at"], as_of=cutoff)
    assert max(s["test_n"] for s in result["windows"].values()) <= 5
    assert all(r["event_id"] != rows[105]["event_id"] for r in result["rows"])
    scored_id = next(r["event_id"] for r in result["rows"] if r["horizon"] == 1 and r["status"] == "scored")
    next(r for r in rows if r["event_id"] == scored_id)["returns"]["1"]["available_at"] = "2020-01-01T00:00:00Z"
    later = response.walk_forward(rows, feature_names=["policy_surprise_bp"],
                                  test_start=rows[100]["observed_at"], as_of=cutoff)
    assert later["windows"]["1"]["pending_n"] == 1
