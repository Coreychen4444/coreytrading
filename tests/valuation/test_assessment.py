"""Daily receipts recompute existing evidence without a fetch or model call."""
from copy import deepcopy

import pytest

from quant.valuation import assessment
from tests.valuation.conftest import make_bundle

STAMP = "2026-09-03T12:00:00Z"


def data():
    return {"selected_tickers": ["SYN"], "valuation": {
        "inputs": {"tickers": {"SYN": {"archetype": "platform", "rationale": "synthetic"}}},
        "fundamentals": {"SYN": make_bundle()},
        "market_snapshots": [{"metrics": {"US10Y": {"value": 4.5, "as_of": "2026-09-02",
                                                        "source": {"identifier": "synthetic"}}}}]}}


def test_rate_selection_uses_observation_time_and_rejects_stale_future():
    documents = data()["valuation"]["market_snapshots"]
    original = deepcopy(documents[0])
    future = deepcopy(original)
    future["metrics"]["US10Y"].update(value=1.0, as_of="2027-01-01")
    assert assessment.market_rate([future, original], as_of=STAMP)["rf"] == .045
    assert assessment.market_rate(documents, as_of="2026-10-03T12:00:00Z")["rf"] is None


def test_complete_daily_receipt_records_unknown_short_term_and_independent_targets():
    before = data()
    result = assessment.build(before, as_of=STAMP)
    row = result["tickers"]["SYN"]
    assert row["status"] == "computed"
    assert row["baseline"]["fair_value_basis"] == "base_case_intrinsic_dcf"
    assert row["sensitivities"][0]["fair_value"] > row["baseline"]["fair_value"]
    assert row["short_term"]["status"] == "unknown"
    assert before == data()


def test_new_macro_evidence_recomputes_from_same_full_baseline():
    package = data()
    scenario = {"id": "rates", "label": "conditional", "basis": "synthetic", "evidence_refs": ["macro"],
                "shocks": [{"factor": "rf_bp", "delta": -25, "start_year": 1, "end_year": 10, "terminal": True}]}
    result = assessment.build(package, as_of=STAMP, macro_scenarios=[scenario], evidence_refs=["macro"])
    row = result["tickers"]["SYN"]
    assert row["macro_scenarios"][0]["status"] == "computed"
    assert row["macro_scenarios"][0]["fair_value"] == row["sensitivities"][0]["fair_value"]


@pytest.mark.parametrize("missing", ["rf", "cash", "future_bundle"])
def test_missing_or_unavailable_inputs_do_not_turn_into_fake_value(missing):
    package = data()
    if missing == "rf":
        package["valuation"]["market_snapshots"] = []
    elif missing == "cash":
        package["valuation"]["fundamentals"]["SYN"]["quarters"][-1]["cash_sti"] = None
    else:
        package["valuation"]["fundamentals"]["SYN"]["fetched_at"] = "2027-01-01T00:00:00Z"
    row = assessment.build(package, as_of=STAMP)["tickers"]["SYN"]
    assert row["status"] == "unknown" and row["baseline"] is None and row["missing"]


def test_market_assumptions_need_timed_basis_and_compact_receipts_are_verifiable():
    from quant.core.agent_contracts import canonical_hash
    package = data()
    package["valuation"]["inputs"]["market"] = {"erp": .05}
    with pytest.raises(ValueError, match="availability"):
        assessment.build(package, as_of=STAMP)
    receipt = assessment.build(data(), as_of=STAMP)["tickers"]["SYN"]["sensitivities"][0]
    assert receipt["receipt_hash"] == canonical_hash({k: v for k, v in receipt.items() if k != "receipt_hash"})
    assert receipt["full_receipt_hash"]


def test_refresh_does_not_make_obsolete_financials_current():
    package = data()
    bundle = package["valuation"]["fundamentals"]["SYN"]
    for row in bundle["quarters"]:
        row["period_end"] = "2000-01-01"
    result = assessment.build(package, as_of=STAMP)["tickers"]["SYN"]
    assert result["status"] == "unknown"
    assert "latest_financial_period_stale" in result["missing"]


def test_short_term_research_survives_missing_fundamentals_and_discount_rate():
    from tests.valuation.test_response import events
    package = data()
    rows = events()
    cohort = {"ticker": "SYN", "feature_schema": "synthetic:v1", "anchor_convention": "synthetic_close"}
    for row in rows:
        row.update(cohort)
    package["valuation"].update(fundamentals={}, market_snapshots=[], response_events=rows,
        response_queries={"SYN": {"features": {"policy_surprise_bp": -25}, "source": "synthetic query",
            "cohort": cohort, "known_at": "2026-09-02T00:00:00Z", "valid_until": "2026-09-04T00:00:00Z",
            "spot": {"price": 100, "as_of": "2026-09-02T20:00:00Z", "source": "synthetic close"}}})
    result = assessment.build(package, as_of=STAMP)["tickers"]["SYN"]
    assert result["status"] == "unknown" and result["baseline"] is None
    assert result["short_term"]["status"] == "research_estimate"
    assert result["short_term"]["windows"]["1"]["price"] > 100
    assert result["data_readiness"]["bridge_missing"]


def test_exposure_transmission_has_finite_lifetime_and_missing_curve_is_partial():
    from tests.valuation.test_evidence import profile
    package = data()
    p = profile()
    p["debt_pass_through"] = 1
    package["valuation"]["inputs"]["tickers"]["SYN"]["rate_exposure"] = p
    scenario = {"id": "policy", "label": "synthetic cut", "basis": "synthetic", "evidence_refs": ["macro"],
                "shocks": [{"factor": "policy_rate_bp", "delta": -25, "start_year": 1, "end_year": 10, "terminal": True}]}
    result = assessment.build(package, as_of=STAMP, macro_scenarios=[scenario], evidence_refs=["macro"])["tickers"]["SYN"]
    assert result["status"] == "computed"
    assert result["macro_scenarios"][0]["status"] == "partial"
    assert result["macro_scenarios"][0]["fair_value"] is None
    assert result["rate_exposure"]["derived_transmission"]["end_year"] == 1
    assert result["rate_exposure"]["derived_transmission"]["terminal"] is False


def test_supplement_unblocks_bridge_but_future_evidence_cannot_rewrite_old_cutoff():
    from tests.valuation.test_evidence import supplement
    package = data()
    bundle = package["valuation"]["fundamentals"]["SYN"]
    bundle["quarters"][-1]["lt_investments"] = None
    bundle["balance_evidence"] = [supplement()]
    assert assessment.build(package, as_of=STAMP)["tickers"]["SYN"]["status"] == "computed"
    bundle["balance_evidence"][0]["reviewed_at"] = "2027-01-01T00:00:00Z"
    result = assessment.build(package, as_of=STAMP)["tickers"]["SYN"]
    assert result["status"] == "unknown" and result["balance_evidence"]["rejected"]


def test_follow_through_query_cannot_use_intraday_spot_or_later_reanchor():
    from tests.valuation.test_response import events
    from quant.valuation.event_data import ANCHOR_CONVENTION, FEATURE_SCHEMA
    package = data()
    cohort = {"ticker": "SYN", "feature_schema": FEATURE_SCHEMA, "anchor_convention": ANCHOR_CONVENTION}
    rows = events()
    for row in rows:
        row.update(cohort)
    query = {"features": {"policy_surprise_bp": -25}, "source": "synthetic", "cohort": cohort,
             "known_at": "2026-09-02T18:00:00Z", "information_known_at": "2026-09-02T18:00:00Z",
             "valid_until": "2026-09-04T00:00:00Z", "spot": {"price": 100, "as_of": "2026-09-02T21:00:00Z", "source": "synthetic"}}
    package["valuation"].update(response_events=rows, response_queries={"SYN": query})
    output = assessment.build(package, as_of=STAMP)["tickers"]["SYN"]["short_term"]
    assert output["reason"] == "response_spot_does_not_match_first_information_close"
    query["spot"]["as_of"] = "2026-09-02T20:00:00Z"
    assert assessment.build(package, as_of=STAMP)["tickers"]["SYN"]["short_term"]["status"] == "research_estimate"


@pytest.mark.parametrize("invalid", ["capture", "unusable", "unit", "freshness"])
def test_private_macro_snapshot_contract_cannot_promote_unusable_or_future_data(invalid):
    document = deepcopy(data()["valuation"]["market_snapshots"][0])
    document["generated_at_utc"] = "2026-09-03T10:00:00Z"
    if invalid == "capture":
        document["generated_at_utc"] = "2026-09-04T10:00:00Z"
    elif invalid == "unusable":
        document["data_quality"] = {"unusable": ["US10Y"]}
    else:
        document["metrics"]["US10Y"][invalid] = "basis_points" if invalid == "unit" else "stale"
    assert assessment.market_rate([document], as_of=STAMP)["rf"] is None


def test_macro_background_is_usable_without_becoming_intraday_or_historical_evidence():
    document = deepcopy(data()["valuation"]["market_snapshots"][0])
    document.update(generated_at_utc="2026-09-03T10:00:00Z", data_quality={"unusable": ["DXY"]})
    document["metrics"]["US10Y"].update(unit="percent", freshness="official_daily")
    rate = assessment.market_rate([document], as_of=STAMP)
    assert rate["rf"] == .045 and rate["freshness"] == "official_daily"
    assert rate["snapshot_captured_at"] == document["generated_at_utc"]


def test_daily_price_uses_separate_completed_close_and_preserves_financial_evidence():
    package = data()
    bundle = package["valuation"]["fundamentals"]["SYN"]
    base_hash = assessment.canonical_hash(bundle)
    package["valuation"]["quotes"] = {"SYN": {"price": 75, "as_of": "2026-09-02T20:00:00Z",
        "captured_at": STAMP, "source": "bars:SYN", "basis": "synthetic completed close"}}
    row = assessment.build(package, as_of=STAMP)["tickers"]["SYN"]
    assert row["status"] == "computed" and row["baseline"]["price"] == 75
    assert row["baseline"]["price_time"] == "2026-09-02T20:00:00+00:00"
    assert row["baseline"]["fundamentals_hash"] == base_hash
    assert bundle["snapshot"]["last_price"] == 50
    package["valuation"]["quotes"]["SYN"]["captured_at"] = "2027-01-01T00:00:00Z"
    assert assessment.build(package, as_of=STAMP)["tickers"]["SYN"]["status"] == "unknown"


def test_unrelated_ticker_bundle_and_stale_price_are_withheld():
    package = data()
    package["valuation"]["fundamentals"]["SYN"]["ticker"] = "OTHER"
    assert "fundamentals_ticker_mismatch" in assessment.build(package, as_of=STAMP)["tickers"]["SYN"]["missing"]
    package = data()
    package["valuation"]["fundamentals"]["SYN"]["snapshot"]["update_time"] = "2026-08-01T20:00:00Z"
    assert assessment.build(package, as_of=STAMP)["tickers"]["SYN"]["status"] == "unknown"


def test_repricing_preserves_original_market_cap_share_anchor():
    bundle = make_bundle()
    bundle["snapshot"]["outstanding_shares"] = 10e6
    quote = {"price": 100, "as_of": "2026-09-02T20:00:00Z", "captured_at": STAMP, "source": "synthetic"}
    updated, _ = assessment.with_quote(bundle, quote, as_of=STAMP)
    assert updated["snapshot"]["total_market_val"] == 10e9
    assert bundle["snapshot"]["total_market_val"] == 5e9
