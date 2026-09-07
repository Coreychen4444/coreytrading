"""Sourced supplements and exposure-based interest transmission without made-up zeros."""
from copy import deepcopy

import pytest

from quant.valuation import evidence
from tests.valuation.conftest import make_bundle

STAMP = "2026-09-03T12:00:00Z"


def supplement():
    return {"ticker": "SYN", "field": "lt_investments", "value": 0, "unit": "USD",
            "period_end": make_bundle()["quarters"][-1]["period_end"],
            "source": "synthetic filing", "source_hash": "synthetic-hash",
            "basis": "explicit zero in the complete synthetic balance sheet",
            "available_at": "2026-09-01T00:00:00Z", "reviewed_at": "2026-09-02T00:00:00Z"}


def profile():
    return {"period_end": make_bundle()["quarters"][-1]["period_end"],
            "known_at": "2026-09-01T00:00:00Z", "valid_until": "2026-10-01T00:00:00Z",
            "source": "synthetic debt and cash note", "basis": "after hedges, full-year exposure",
            "start_year": 1, "end_year": 1,
            "repricing_assets_usd": 1e9, "repricing_debt_usd": 2e9,
            "asset_pass_through": 1, "debt_pass_through": .5, "tax_rate": .2}


def test_missing_field_only_filled_by_explicit_matching_evidence_without_mutating_bundle():
    bundle = make_bundle()
    bundle["quarters"][-1]["lt_investments"] = None
    fixed, receipt = evidence.supplement(bundle, [supplement()], as_of=STAMP)
    assert fixed["quarters"][-1]["lt_investments"] == 0
    assert bundle["quarters"][-1]["lt_investments"] is None
    assert receipt["applied"][0]["evidence"][0]["source_hash"]


@pytest.mark.parametrize("changes", [{"period_end": "2026-01-01"}, {"unit": "EUR"},
                                      {"reviewed_at": "2027-01-01T00:00:00Z"}, {"source": ""}])
def test_unverified_or_future_supplements_cannot_fill(changes):
    bundle = make_bundle()
    bundle["quarters"][-1]["lt_investments"] = None
    fixed, receipt = evidence.supplement(bundle, [{**supplement(), **changes}], as_of=STAMP)
    assert fixed["quarters"][-1]["lt_investments"] is None
    assert receipt["rejected"]


def test_conflicting_evidence_invalidates_field_instead_of_arbitrary_winner():
    fixed, receipt = evidence.supplement(make_bundle(), [supplement(), {**supplement(), "value": 100}], as_of=STAMP)
    assert fixed["quarters"][-1]["lt_investments"] is None
    assert receipt["rejected"][0]["reason"] == "conflicting_balance_evidence"


def test_cash_income_and_floating_debt_cost_offset_at_equal_pass_through():
    p = profile()
    assert evidence.rate_exposure(p, as_of=STAMP, baseline_period=p["period_end"])["coefficient"] == 0
    p["debt_pass_through"] = 1
    coefficient = evidence.rate_exposure(p, as_of=STAMP, baseline_period=p["period_end"])["coefficient"]
    assert -25 * coefficient == pytest.approx(2e6)
    p["repricing_debt_usd"] = 0
    assert evidence.rate_exposure(p, as_of=STAMP, baseline_period=p["period_end"])["coefficient"] > 0


def test_unknown_or_expired_repricing_profile_cannot_become_a_coefficient():
    p = profile()
    p.pop("repricing_assets_usd")
    with pytest.raises(ValueError):
        evidence.rate_exposure(p, as_of=STAMP, baseline_period=p["period_end"])
    p = profile()
    with pytest.raises(ValueError, match="not_valid"):
        evidence.rate_exposure(p, as_of="2027-01-01T00:00:00Z", baseline_period=p["period_end"])


def test_sec_preparation_requires_explicit_period_mapping_and_preserves_source():
    from tests.data.test_sec_facts import payload
    bundle = make_bundle()
    original = deepcopy(bundle)
    bundle["quarters"][-1]["period_end"] = "2026-06-29"
    mapping = {"ticker": "SYN", "cik": 1, "identity_basis": "synthetic issuer registry",
               "provider_period_end": "2026-06-29", "period_end": "2026-06-30",
               "period_basis": "synthetic source filing cover date", "reviewed_at": "2026-09-02T00:00:00Z",
               "fields": {"current_debt": {"concepts": ["us-gaap:PartA", "us-gaap:PartB"],
                                            "basis": "synthetic disjoint current debt components"}}}
    prepared = evidence.prepare_sec(bundle, payload(), mapping, as_of=STAMP)
    assert prepared["bundle"]["quarters"][-1]["period_end"] == "2026-06-30"
    assert prepared["bundle"]["balance_evidence"][0]["value"] == 300
    assert bundle["quarters"][-1]["period_end"] == "2026-06-29"
    mapping["cik"] = 2
    with pytest.raises(ValueError, match="issuer"):
        evidence.prepare_sec(bundle, payload(), mapping, as_of=STAMP)


def test_dated_forward_forecasts_expire_and_must_describe_the_actual_projection_period():
    fw = {"horizon_months": 12, "forward_eps": 5, "earnings_basis": "core", "source": "synthetic forecast",
          "basis": "synthetic accounting reconciliation", "available_at": "2026-09-01T00:00:00Z",
          "valid_until": "2026-10-01T00:00:00Z", "period_start": "2026-09-01", "period_end": "2027-08-31"}
    assert evidence.forward_at(fw, as_of=STAMP)[0]["horizon_months"] == 12
    assert evidence.forward_at(fw, as_of="2026-10-02T00:00:00Z")[0]["horizon_months"] is None
    fw["period_end"] = "2026-12-31"
    assert evidence.forward_at(fw, as_of=STAMP)[0]["horizon_months"] is None
    assert fw["horizon_months"] == 12
