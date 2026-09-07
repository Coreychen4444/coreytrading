"""Units and evidence boundaries for conditional valuation research."""
from copy import deepcopy

import pytest

from quant.core.valuation_contracts import validate_scenario, validate_transmissions


def case():
    return {"id": "cut", "label": "conditional", "basis": "synthetic assumption", "evidence_refs": ["source"],
            "shocks": [{"factor": "rf_bp", "delta": -25, "start_year": 1, "end_year": 10, "terminal": True}]}


@pytest.mark.parametrize("key,value", [("delta", float("nan")), ("delta", True), ("delta", 1001),
                                      ("start_year", 0), ("end_year", 31), ("terminal", "yes"), ("factor", "cap")])
def test_invalid_shocks_are_rejected(key, value):
    value_case = case()
    value_case["shocks"][0][key] = value
    with pytest.raises(ValueError):
        validate_scenario(value_case, horizon=10)


def test_binding_overlap_and_terminal_are_checked():
    good = case()
    assert validate_scenario(good, horizon=10, evidence_refs=["source"]) == good
    with pytest.raises(ValueError, match="evidence"):
        validate_scenario(good, horizon=10, evidence_refs=["other"])
    bad = deepcopy(good)
    bad["shocks"].append(deepcopy(bad["shocks"][0]))
    with pytest.raises(ValueError, match="overlapping"):
        validate_scenario(bad, horizon=10)
    bad = deepcopy(good)
    bad["shocks"][0]["end_year"] = 2
    with pytest.raises(ValueError, match="terminal"):
        validate_scenario(bad, horizon=10)


def test_coefficients_require_evidence_and_explicit_units():
    row = {"factor": "policy_rate_bp", "driver": "rf_bp", "coefficient": .4,
           "source": "synthetic model", "basis": "bp long rate per bp policy surprise"}
    assert validate_transmissions([row]) == [row]
    with pytest.raises(ValueError, match="duplicate"):
        validate_transmissions([row, row])
    with pytest.raises(ValueError, match="source"):
        validate_transmissions([{**row, "source": ""}])
