"""Filing-dated USD facts require compatible concepts and accession identity."""
from copy import deepcopy
import json
from io import BytesIO

import pytest

from quant.data import sec_facts


def payload():
    def fact(value):
        return {"units": {"USD": [{"end": "2026-06-30", "filed": "2026-08-01", "val": value,
                                   "accn": "0000000001-26-000001", "form": "10-Q"}]}}
    return {"cik": 1, "facts": {"us-gaap": {"PartA": fact(100), "PartB": fact(200)}}}


def select(p=None, **kwargs):
    return sec_facts.instant(p or payload(), ["us-gaap:PartA", "us-gaap:PartB"],
                             period_end="2026-06-30", as_of=kwargs.get("as_of", "2026-09-01T00:00:00Z"))


def test_same_filing_sum_and_conservative_filing_day_availability():
    assert select()["value"] == 300
    assert select(as_of="2026-08-01T12:00:00Z")["status"] == "unknown"
    assert select(as_of="2026-08-02T05:00:00Z")["value"] == 300


def test_new_partial_revision_cannot_be_combined_with_old_other_component():
    p = payload()
    rows = p["facts"]["us-gaap"]["PartA"]["units"]["USD"]
    rows.append({**rows[0], "filed": "2026-08-15", "val": 500, "accn": "0000000001-26-000002"})
    assert select(p)["status"] == "unknown"
    assert select(p, as_of="2026-08-10T00:00:00Z")["value"] == 300


@pytest.mark.parametrize("mutation", ["unit", "duration", "conflict", "absent", "nonfinite"])
def test_ambiguous_or_incompatible_facts_are_never_zero_filled(mutation):
    p = payload()
    fact = p["facts"]["us-gaap"]["PartA"]
    row = fact["units"]["USD"][0]
    if mutation == "unit":
        fact["units"]["EUR"] = fact["units"].pop("USD")
    elif mutation == "duration":
        row["start"] = "2026-01-01"
    elif mutation == "conflict":
        fact["units"]["USD"].append({**row, "val": 5})
    elif mutation == "absent":
        fact["units"]["USD"] = []
    else:
        row["val"] = float("inf")
    if mutation == "nonfinite":
        # Non-standard JSON itself is rejected by canonical provenance hashing.
        with pytest.raises(ValueError):
            select(p)
    else:
        assert select(p)["value"] is None


def test_network_request_is_identity_bound_and_has_no_secret_dependency():
    seen = []
    def opener(request, timeout):
        seen.append((request.full_url, timeout))
        return BytesIO(json.dumps(payload()).encode())
    assert sec_facts.fetch("1", user_agent="SyntheticResearch contact at example.invalid", opener=opener)["cik"] == 1
    assert seen == [("https://data.sec.gov/api/xbrl/companyfacts/CIK0000000001.json", 30)]
    with pytest.raises(ValueError, match="identity"):
        sec_facts.fetch("2", user_agent="SyntheticResearch", opener=opener)
