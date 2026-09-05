"""Offline contract tests for role isolation, evidence, snapshots and review."""
from datetime import datetime
import hashlib
import json
from pathlib import Path

import pytest

from quant.core.agent_contracts import (
    CONTRACT_VERSION, RESULT_SCHEMA_VERSION, ROLES, ContractError,
    canonical_hash, result_schema, validate_result, validate_snapshot,
)

NOW = "2026-09-05T12:30:00+08:00"


@pytest.fixture
def snapshot():
    data = {"prices": [{"ticker": "SYN", "close": 100}], "revision": 1}
    return {"snapshot_id": "snapshot-test", "input_hash": canonical_hash(data),
            "as_of": "2026-09-05T12:00:00+08:00",
            "expires_at": "2026-09-05T13:00:00+08:00",
            "evidence_refs": ["price-1", "receipt-1"], "data": data}


def result(role, snapshot):
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "contract_version": CONTRACT_VERSION,
        "role": role, "snapshot_id": snapshot["snapshot_id"],
        "input_hash": snapshot["input_hash"],
        "generated_at": "2026-09-05T12:10:00+08:00",
        "expires_at": snapshot["expires_at"],
        "summary": "合成数据研究，无执行权。",
        "observations": [{"id": "obs-1", "kind": "fact", "text": "Synthetic close is 100.",
                          "evidence_refs": ["price-1"]}],
        "hypotheses": [{"id": "hyp-1", "statement": "Structure may hold.",
                        "evidence_refs": ["price-1"], "invalidation": "A lower close."}],
        "risks": [{"id": "risk-1", "text": "Receipt may require refresh.",
                   "evidence_refs": ["receipt-1"]}],
        "unknowns": ["Intraday path unavailable."], "proposal": None,
        "requests": [], "review": None,
        "authority": {"mode": "proposal_only" if role == "technical" else "analysis_only",
                      "may_write_state": False, "may_place_orders": False},
    }


def test_hash_is_canonical_strict_json():
    value = {"中文": [None, True, 3.5], "a": 1}
    expected = hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                        ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    assert canonical_hash(value) == expected
    assert canonical_hash({"a": 1, "中文": [None, True, 3.5]}) == expected
    assert canonical_hash(None) == hashlib.sha256(b"null").hexdigest()


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf"),
                                   {1: "key"}, (1, 2), {"x": {1, 2}}])
def test_hash_rejects_non_json(value):
    with pytest.raises(ContractError):
        canonical_hash(value)


@pytest.mark.parametrize("role", ROLES)
def test_valid_role_result_is_detached(role, snapshot):
    payload = result(role, snapshot)
    output = validate_result(role, payload, snapshot=snapshot, now=NOW)
    assert output == payload
    output["observations"].clear()
    assert payload["observations"]


@pytest.mark.parametrize("role", ["macro", "risk", "valuation"])
def test_information_roles_cannot_propose_state_changes(role, snapshot):
    payload = result(role, snapshot)
    payload["proposal"] = {"kind": "zone_update", "zone_update_args": ["--ticker", "SYN"]}
    with pytest.raises(ContractError, match="proposal"):
        validate_result(role, payload, snapshot=snapshot)


@pytest.mark.parametrize("mutation", [
    lambda p: p.update(provider="self-claimed"),
    lambda p: p["authority"].update(may_write_state=True),
    lambda p: p["authority"].update(may_place_orders=True),
    lambda p: p["authority"].update(may_place_orders=0),
    lambda p: p["authority"].update(hard_veto=True),
    lambda p: p["observations"][0].update(cap_pct_nav=0.4),
    lambda p: p.update(role="valuation"),
    lambda p: p.update(contract_version="0.0.1"),
    lambda p: p.update(summary=float("nan")),
    lambda p: p.update(summary="  "),
    lambda p: p.pop("unknowns"),
])
def test_invalid_or_overreaching_result_is_rejected(mutation, snapshot):
    payload = result("macro", snapshot)
    mutation(payload)
    with pytest.raises(ContractError):
        validate_result("macro", payload, snapshot=snapshot)


def test_technical_proposal_is_only_an_argument_list(snapshot):
    payload = result("technical", snapshot)
    payload["proposal"] = {"kind": "zone_update", "zone_update_args": ["--ticker", "SYN"]}
    assert validate_result("technical", payload, snapshot=snapshot)["proposal"] == payload["proposal"]
    for proposal in ({"kind": "shell", "zone_update_args": ["anything"]},
                     {"kind": "zone_update", "zone_update_args": "--ticker SYN"},
                     {"kind": "zone_update", "zone_update_args": ["--ticker", 1]},
                     {"kind": "zone_update", "zone_update_args": []},
                     {"kind": "zone_update", "zone_update_args": ["SYN\x00"]},
                     {"kind": "zone_update", "zone_update_args": ["SYN"], "apply": True}):
        payload["proposal"] = proposal
        with pytest.raises(ContractError):
            validate_result("technical", payload, snapshot=snapshot)


@pytest.mark.parametrize("mutation", [
    lambda p: p.update(snapshot_id="other"),
    lambda p: p.update(input_hash="0" * 64),
    lambda p: p["observations"][0].update(evidence_refs=["not-in-snapshot"]),
    lambda p: p["observations"][0].update(evidence_refs=[]),
    lambda p: p["observations"][0].update(evidence_refs=["price-1", "price-1"]),
    lambda p: p["risks"][0].update(id="obs-1"),
])
def test_binding_and_evidence_fail_closed(mutation, snapshot):
    payload = result("risk", snapshot)
    mutation(payload)
    with pytest.raises(ContractError):
        validate_result("risk", payload, snapshot=snapshot)


@pytest.mark.parametrize("field,value", [
    ("generated_at", "2026-09-05T12:10:00"),
    ("generated_at", "2026-09-05T11:59:59+08:00"),
    ("generated_at", "2026-09-05T12:31:00+08:00"),
    ("expires_at", "2026-09-05T13:01:00+08:00"),
    ("expires_at", "2026-09-05T12:10:00+08:00"),
    ("expires_at", NOW),
])
def test_result_time_bounds(field, value, snapshot):
    payload = result("macro", snapshot)
    payload[field] = value
    with pytest.raises(ContractError):
        validate_result("macro", payload, snapshot=snapshot, now=NOW)


@pytest.mark.parametrize("mutation", [
    lambda s: s["data"].update(revision=2),
    lambda s: s.update(as_of="2026-09-05T12:31:00+08:00"),
    lambda s: s.update(expires_at=NOW),
    lambda s: s.update(as_of="2026-09-05"),
    lambda s: s.update(evidence_refs=["x", "x"]),
    lambda s: s.update(unknown_field=True),
    lambda s: s.update(data={"price": float("nan")}),
])
def test_snapshot_tampering_and_expiry(mutation, snapshot):
    mutation(snapshot)
    with pytest.raises(ContractError):
        validate_snapshot(snapshot, now=NOW)


def test_cross_role_requests_are_bounded(snapshot):
    payload = result("valuation", snapshot)
    request = {"role": "macro", "question": "Check the source period.", "evidence_refs": ["price-1"]}
    payload["requests"] = [request]
    validate_result("valuation", payload, snapshot=snapshot)
    request["role"] = "valuation"
    with pytest.raises(ContractError, match="another role"):
        validate_result("valuation", payload, snapshot=snapshot)
    request["role"] = "macro"
    payload["requests"] = [request] * 13
    with pytest.raises(ContractError, match="item count"):
        validate_result("valuation", payload, snapshot=snapshot)


def test_structured_review_is_not_an_execution_approval(snapshot):
    payload = result("technical", snapshot)
    payload["review"] = {"run_id": "candidate-run", "verdict": "reject", "reasons": ["Missing price timing."]}
    validate_result("technical", payload, snapshot=snapshot)
    payload["review"]["verdict"] = "approve"
    output = validate_result("technical", payload, snapshot=snapshot)
    assert output["authority"]["may_write_state"] is False
    payload["proposal"] = {"kind": "zone_update", "zone_update_args": ["--ticker", "SYN"]}
    with pytest.raises(ContractError, match="reviewer"):
        validate_result("technical", payload, snapshot=snapshot)
    payload["proposal"] = None
    payload["review"]["reasons"] = []
    with pytest.raises(ContractError, match="review"):
        validate_result("technical", payload, snapshot=snapshot)


def test_timezone_comparison_and_naive_clock(snapshot):
    payload = result("macro", snapshot)
    assert validate_result("macro", payload, snapshot=snapshot, now="2026-09-05T04:30:00Z")
    with pytest.raises(ContractError, match="timezone"):
        validate_result("macro", payload, snapshot=snapshot, now=datetime(2026, 9, 5, 12, 30))


def test_schemas_and_registry_have_exactly_the_business_roles():
    with pytest.raises(ContractError, match="unknown role"):
        result_schema("coordinator")
    schema = result_schema("technical")
    schema["properties"]["authority"]["properties"]["may_write_state"]["const"] = True
    assert result_schema("technical")["properties"]["authority"]["properties"]["may_write_state"]["const"] is False
    root = Path(__file__).resolve().parents[2]
    registry = json.loads((root / "agents/roles.json").read_text())
    assert registry["contract_version"] == CONTRACT_VERSION
    assert set(registry["roles"]) == set(ROLES)
    assert "provider" not in registry and "model" not in registry
    for path in registry["roles"].values():
        assert (root / path).is_file()
