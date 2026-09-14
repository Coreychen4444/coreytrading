"""Adversarial evidence-window tests; no network, prices or private state."""
from copy import deepcopy

import pytest

from quant.core.macro_evidence import build_market_context, context_evidence

AS_OF = "2026-09-05T12:00:00Z"


def observation(value, at, unit="percent", provider="FRED", identifier="DGS10", **extra):
    return {"value": value, "as_of": at, "unit": unit, "freshness": "official_daily",
            "source": {"provider": provider, "identifier": identifier}, **extra}


def capture(ref, metrics, at=AS_OF):
    return {"ref": ref, "document": {"generated_at_utc": at, "metrics": metrics}}


def build(*captures):
    return build_market_context(list(captures), as_of=AS_OF)


def test_bp_math_uses_two_distinct_observations_not_vendor_change_or_capture_count():
    first = capture("a", {"US10Y": observation(4.3, "2026-09-03T00:00:00Z")}, "2026-09-04T10:00:00Z")
    repeated = capture("b", first["document"]["metrics"], "2026-09-04T11:00:00Z")
    last = capture("c", {"US10Y": observation(4.1, "2026-09-04T00:00:00Z", change_pct=99)})
    original = deepcopy([first, repeated, last])
    context = build(first, repeated, last)
    change = context["sample_changes"]["US10Y"]
    assert change["value"] == pytest.approx(-20) and change["unit"] == "bp"
    assert change["window_start"] == "2026-09-03T00:00:00+00:00"
    assert change["window_end"] == "2026-09-04T00:00:00+00:00"
    assert context["observations"]["US10Y"]["timestamp_precision"] == "date"
    assert set(change["evidence_refs"]) <= set(context_evidence(context))
    assert original == [first, repeated, last]
    assert build(first, repeated)["sample_changes"]["US10Y"]["status"] == "unknown"


def test_yen_change_is_in_the_quoted_pair_and_not_assumed_daily_carry_return():
    old = observation(160, "2026-09-04T10:00:00Z", "index_or_pair", "Yahoo", "USDJPY=X")
    new = observation(154, "2026-09-05T10:00:00Z", "index_or_pair", "Yahoo", "USDJPY=X")
    context = build(capture("a", {"USDJPY": old}), capture("b", {"USDJPY": new}))
    assert context["sample_changes"]["USDJPY"]["value"] == -3.75
    assert context["sample_changes"]["USDJPY"]["unit"] == "percent_change"
    assert context["coverage"]["yen"]["mechanism_identified"] is False
    assert "not a daily return" in context["sample_changes"]["USDJPY"]["basis"]
    assert "positioning_leverage" in context["quote_feed_structure_gaps"]


@pytest.mark.parametrize("change", [
    {"source": {"provider": "Yahoo", "identifier": "^TNX"}},
    {"source": {"provider": "FRED", "identifier": "OTHER"}},
    {"unit": "basis_points"}, {"price_field": "after_price"},
])
def test_different_source_units_or_quote_field_cannot_be_spliced(change):
    old = observation(4, "2026-09-03T00:00:00Z")
    new = observation(4.5, "2026-09-04T00:00:00Z", **change)
    context = build(capture("a", {"US10Y": old}), capture("b", {"US10Y": new}))
    assert context["sample_changes"]["US10Y"]["status"] == "unknown"


def test_revised_same_date_is_not_a_market_move_and_future_capture_does_not_leak():
    context = build(capture("a", {"US10Y": observation(4, "2026-09-04T00:00:00Z")}),
                    capture("b", {"US10Y": observation(4.1, "2026-09-04T00:00:00Z")}),
                    capture("future", {"US10Y": observation(9, "2026-09-06T00:00:00Z")}, "2026-09-06T12:00:00Z"))
    assert context["sample_changes"]["US10Y"]["reason"] == "same_timestamp_revision_or_conflict"
    assert context["observations"]["US10Y"]["value"] == 4.1
    assert context["excluded_captures"][0]["source_ref"] == "future"


def test_equivalent_timezone_offsets_are_one_observation_in_revision_detection():
    context = build(capture("a", {"US10Y": observation(3.9, "2026-09-03T00:00:00Z")}),
                    capture("b", {"US10Y": observation(4, "2026-09-04T00:00:00Z")}),
                    capture("c", {"US10Y": observation(4.1, "2026-09-04T08:00:00+08:00")}))
    assert context["sample_changes"]["US10Y"]["reason"] == "same_timestamp_revision_or_conflict"
    assert context["observations"]["US10Y"]["reported_as_of"] == "2026-09-04T08:00:00+08:00"
    assert context["observations"]["US10Y"]["observed_at"] == "2026-09-04T00:00:00+00:00"


@pytest.mark.parametrize("value,at", [(True, AS_OF), (float("nan"), AS_OF),
    (None, AS_OF), (4, "2026-09-06T00:00:00Z"), (4, "2026-09-05T00:00:00"), (4, None)])
def test_bad_latest_quote_is_visible_unknown_and_never_replaced_with_old_good_quote(value, at):
    context = build(capture("a", {"US10Y": observation(4, "2026-09-03T00:00:00Z")}),
                    capture("b", {"US10Y": observation(value, at)}))
    assert context["observations"]["US10Y"]["status"] == "unusable"
    assert context["sample_changes"]["US10Y"]["status"] == "unknown"


def test_old_capture_live_label_never_refreshes_quote_and_close_bound_is_preserved():
    metric = observation(100, "2026-09-04T23:00:00Z", "usd", "OpenD", "US.SPY",
                         timestamp_basis="snapshot.update_time", price_field="last_price",
                         observation_time_upper_bound="2026-09-04T20:00:00Z")
    metric["freshness"] = "live"
    context = build(capture("a", {"SPY": metric}))
    row = context["observations"]["SPY"]
    assert row["observed_at"] == "2026-09-04T20:00:00+00:00"
    assert row["reported_as_of"] == "2026-09-04T23:00:00Z"
    assert row["currentness"] == "not_revalidated" and row["age_seconds"] == 16 * 3600
    del metric["timestamp_basis"]
    assert build(capture("a", {"SPY": metric}))["observations"]["SPY"]["status"] == "unusable"


def test_curve_and_inflation_compensation_only_use_matching_official_dates():
    metrics = {"US2Y": observation(4.2, "2026-09-04T00:00:00Z", identifier="DGS2"),
               "US10Y_OFFICIAL": observation(4.5, "2026-09-04T00:00:00Z"),
               "US10Y_REAL": observation(2, "2026-09-04T00:00:00Z", identifier="DFII10"),
               "US10Y": observation(4.8, AS_OF, provider="Yahoo", identifier="^TNX")}
    context = build(capture("a", metrics))
    rates = context["aligned_rates"]
    assert rates["curve_2s10s"]["value"] == 30
    assert rates["inflation_compensation_10y"]["value"] == 250
    assert "not term premium" in rates["curve_2s10s"]["meaning"]
    metrics["US10Y_REAL"]["as_of"] = "2026-09-03T00:00:00Z"
    assert build(capture("a", metrics))["aligned_rates"]["inflation_compensation_10y"]["status"] == "unknown"
    del metrics["US10Y_OFFICIAL"]
    assert build(capture("a", metrics))["aligned_rates"]["curve_2s10s"]["status"] == "unknown"


def test_empty_feed_has_explicit_gaps_and_no_default_direction():
    context = build()
    assert not context["observations"] and not context["sample_changes"]
    assert context["coverage"]["yen"]["missing_or_unusable"] == ["USDJPY", "DXY"]
    assert all(not item["mechanism_identified"] for item in context["coverage"].values())
