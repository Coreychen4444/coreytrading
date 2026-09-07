"""Independent channel checks for the frozen company valuation."""
from copy import deepcopy

import pytest

from quant.valuation import archetypes, model, scenarios
from tests.valuation.conftest import make_bundle


@pytest.fixture
def baseline():
    resolved = archetypes.resolve("SYN", {"tickers": {"SYN": {"archetype": "platform", "rationale": "synthetic"}}})
    return model.valuate(make_bundle(), resolved, rf=.045, erp=.0423, beta=None,
                          overlays={"applied": [], "ignored": [], "flags": []})


def case(factor, delta, first=1, last=10, terminal=True):
    return {"id": "synthetic", "label": "conditional", "basis": "synthetic assumption", "evidence_refs": ["source"],
            "shocks": [{"factor": factor, "delta": delta, "start_year": first, "end_year": last, "terminal": terminal}]}


def test_zero_shock_matches_both_baseline_engines(baseline):
    value = scenarios.evaluate(baseline, case("rf_bp", 0))
    assert value["status"] == "computed"
    assert value["fair_value"] == pytest.approx(baseline["fair_value"])
    assert value["market_target_12m"] == pytest.approx(baseline["market_target_12m"]["price"])


def test_rate_drop_and_growth_scare_can_have_opposite_results(baseline):
    original = deepcopy(baseline)
    lower = scenarios.evaluate(baseline, case("rf_bp", -25))
    assert lower["fair_value"] > baseline["fair_value"]
    joint = case("rf_bp", -25)
    joint["shocks"].extend(case("erp_bp", 100)["shocks"] + case("revenue_pct", -.1)["shocks"])
    scare = scenarios.evaluate(baseline, joint)
    assert scare["fair_value"] < baseline["fair_value"]
    assert sum(c["intrinsic_delta"] for c in scare["attribution"]) + scare["interaction_delta"] == pytest.approx(scare["intrinsic_delta"])
    assert baseline == original


def test_policy_rate_is_not_assumed_to_be_discount_curve(baseline):
    unknown = scenarios.evaluate(baseline, case("policy_rate_bp", -25))
    assert unknown["status"] == "partial" and unknown["fair_value"] is None
    assert "unquantified_transmission:policy_rate_bp" in unknown["missing"]
    link = {"factor": "policy_rate_bp", "driver": "rf_bp", "coefficient": .4,
            "basis": "explicit synthetic transmission", "source": "fixture"}
    mapped = scenarios.evaluate(baseline, case("policy_rate_bp", -25), transmissions=[link])
    direct = scenarios.evaluate(baseline, case("rf_bp", -10))
    assert mapped["fair_value"] == pytest.approx(direct["fair_value"])


def test_interest_exposure_does_not_leak_past_its_declared_forecast_years(baseline):
    link = {"factor": "policy_rate_bp", "driver": "net_interest_usd", "coefficient": -80000,
            "start_year": 1, "end_year": 1, "terminal": False, "basis": "synthetic first-year exposure", "source": "fixture"}
    changed = scenarios.evaluate(baseline, case("policy_rate_bp", -25), transmissions=[link])
    calculation, reference = changed["partial_calculation"], changed["reference"]
    assert calculation["path"][0]["eps"] - reference["path"][0]["eps"] == pytest.approx(2e6 / baseline["drivers"]["shares"])
    assert calculation["path"][1:] == reference["path"][1:]
    assert calculation["market_target_12m"] == pytest.approx(reference["market_target_12m"])


def test_temporary_order_does_not_raise_terminal_revenue(baseline):
    temporary = scenarios.evaluate(baseline, case("revenue_pct", .1, last=1, terminal=False))
    persistent = scenarios.evaluate(baseline, case("revenue_pct", .1))
    changed = temporary["partial_calculation"]
    assert changed["path"][0]["revenue"] == pytest.approx(baseline["base"]["path"][0]["revenue"] * 1.1)
    assert changed["path"][1]["revenue"] == baseline["base"]["path"][1]["revenue"]
    assert changed["terminal_nopat"] == baseline["base"]["path"][-1]["nopat"]
    assert temporary["fair_value"] < persistent["fair_value"]


def test_multiple_changes_market_target_not_intrinsic_cash_flows(baseline):
    changed = scenarios.evaluate(baseline, case("pe_pct", -.2, last=1, terminal=False))
    assert changed["fair_value"] == pytest.approx(baseline["fair_value"])
    assert changed["market_target_12m"] == pytest.approx(baseline["market_target_12m"]["price"] * .8)


def test_terminal_rate_does_not_move_when_only_near_curve_moves(baseline):
    near = scenarios.evaluate(baseline, case("rf_bp", -25, last=1, terminal=False))
    far = scenarios.evaluate(baseline, case("rf_bp", -25))
    assert near["partial_calculation"]["terminal_wacc"] == baseline["spec"]["wacc"]
    assert baseline["fair_value"] < near["fair_value"] < far["fair_value"]


def test_same_direct_and_transmitted_channel_does_not_produce_complete_price(baseline):
    combined = case("rf_bp", -25)
    combined["shocks"] += case("policy_rate_bp", -25)["shocks"]
    out = scenarios.evaluate(baseline, combined, transmissions=[{"factor": "policy_rate_bp", "driver": "rf_bp",
                            "coefficient": 1, "source": "fixture", "basis": "synthetic"}])
    assert out["status"] == "partial" and out["fair_value"] is None


def test_inconsistent_terminal_spread_is_unknown(baseline):
    result = scenarios.evaluate(baseline, case("rf_bp", -900))
    assert result["status"] == "unknown" and result["fair_value"] is None



def test_zero_shock_agrees_near_small_but_positive_terminal_spread(baseline):
    baseline["spec"]["cost_of_equity"] = baseline["spec"]["terminal_growth"] + .003
    baseline["market_target_12m"] = model.earnings_target(baseline["base"]["path"], baseline["spec"])
    value = scenarios.evaluate(baseline, case("rf_bp", 0))
    assert value["status"] == "computed"
    assert value["market_target_12m"] == pytest.approx(baseline["market_target_12m"]["price"])
