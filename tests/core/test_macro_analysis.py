"""Synthetic boundary tests: explicit unknowns, evidence and causal follow-up."""
from copy import deepcopy
import json

import pytest

from quant.core.agent_contracts import ContractError, canonical_hash, result_schema, validate_result
from quant.core.macro_analysis import MACRO_RESULT_SCHEMA_VERSION


def macro_fields():
    """Explicit abstention fixture; production never auto-fills missing fields."""
    unknown = {"status": "unknown", "finding": "合成输入不足以判断。",
               "evidence_refs": [], "limitation": "缺少对应原始来源与窗口。"}
    # A JSON roundtrip gives each field its own object, as real runner output does.
    return json.loads(json.dumps({
        "macro_diagnosis": {
            "economic_state": unknown,
            "curve": {"ust_2y": unknown, "ust_10y": unknown, "ust_30y": unknown},
            "rate_drivers": {"policy_path": unknown, "real_yields": unknown,
                             "inflation_compensation": unknown, "term_premium": unknown,
                             "treasury_supply_demand": unknown},
            "pricing_baseline": unknown, "causal_hypotheses": [], "dominant_hypothesis_id": None, "synthesis": unknown},
        "market_structure": {"dimensions": {
            "positioning_leverage": unknown, "flows": unknown, "momentum_factors": unknown,
            "volatility_pricing": unknown, "breadth": unknown, "liquidity": unknown,
            "price_structure": unknown, "seasonality": unknown}, "macro_interaction": unknown},
    }))


def case():
    from tests.core.test_agent_contracts import result
    data = {"synthetic": True}
    s = {"snapshot_id": "macro-synthetic", "input_hash": canonical_hash(data), "data": data,
         "evidence_refs": ["price-1", "receipt-1"], "as_of": "2026-09-05T12:00:00+08:00",
         "expires_at": "2026-09-05T13:00:00+08:00"}
    return s, result("macro", s)


def chain():
    inferred = {"status": "inferred", "finding": "合成需求数据弱于事前基准。",
                "evidence_refs": ["receipt-1"], "limitation": "合成案例，不代表真实行情。"}
    return {"id": "causal-1", "shock": "合成增长下修", "horizon": "short_1_10d",
            "evidence_refs": ["receipt-1", "price-1"], "rate_channel": "政策路径可能下修。",
            "cash_flow_channel": "需求下降可能压低利润。", "risk_premium_channel": "信用风险可能上升。",
            "upstream": {**deepcopy(inferred), "driver_type": "demand", "occurred_at": None,
                         "known_at": "2026-09-05T10:00:00+08:00",
                         "baseline": {**deepcopy(inferred), "status": "observed", "known_at": "2026-09-05T09:00:00+08:00"},
                         "surprise": deepcopy(inferred)},
            "market_responses": [{**deepcopy(inferred), "status": "observed", "evidence_refs": ["price-1"],
                                  "variable": "US10Y", "role": "result", "window_start": "2026-09-05T10:00:00+08:00",
                                  "window_end": "2026-09-05T11:00:00+08:00"}],
            "feedback": {**deepcopy(inferred), "actor": "杠杆基金", "constraint": "抵押品要求", "trigger": "损失触及保证金约束时"},
            "asset_implications": [{**deepcopy(inferred), "asset": "equities", "channel": "cash_flow", "direction": "negative"}],
            "alternative": "也可能是良性去通胀。",
            "confirmation": "后续同口径需求和信用一起恶化。", "invalidation": "需求恢复且信用改善。",
            "next_observation": "下一份同口径需求发布，时间尚未核实。", "due_at": None}


def test_macro_version_isolated_and_explicit_abstention_is_valid():
    s, p = case()
    assert p["schema_version"] == MACRO_RESULT_SCHEMA_VERSION
    assert result_schema("technical")["properties"]["schema_version"]["const"] == "1.1.0"
    output = validate_result("macro", p, snapshot=s)
    assert output["macro_diagnosis"]["synthesis"]["status"] == "unknown"
    assert output["authority"]["may_write_state"] is False


@pytest.mark.parametrize("field", ["macro_diagnosis", "market_structure"])
def test_old_or_incomplete_macro_results_are_not_silently_upgraded(field):
    s, p = case()
    p.pop(field)
    with pytest.raises(ContractError, match="missing fields"):
        validate_result("macro", p, snapshot=s)


@pytest.mark.parametrize("mutation", [
    lambda p: p["macro_diagnosis"]["curve"].pop("ust_30y"),
    lambda p: p["market_structure"]["dimensions"].pop("flows"),
    lambda p: p["market_structure"].update(cap_pct_nav=0.4),
    lambda p: p["macro_diagnosis"]["rate_drivers"]["term_premium"].update(contribution_bp=30),
    lambda p: p.update(schema_version="1.1.0"),
])
def test_missing_coverage_unsupported_attribution_and_authority_fail(mutation):
    s, p = case()
    mutation(p)
    with pytest.raises(ContractError):
        validate_result("macro", p, snapshot=s)


@pytest.mark.parametrize("refs,status", [([], "observed"), ([], "inferred"),
                                         (["invented"], "inferred"),
                                         (["price-1", "price-1"], "unknown")])
def test_nested_evidence_is_validated_even_for_unknown_claims(refs, status):
    s, p = case()
    p["market_structure"]["dimensions"]["flows"].update(status=status, evidence_refs=refs)
    with pytest.raises(ContractError, match="evidence"):
        validate_result("macro", p, snapshot=s)


def test_term_premium_is_estimated_not_an_observed_price():
    s, p = case()
    value = p["macro_diagnosis"]["rate_drivers"]["term_premium"]
    value.update(status="observed", evidence_refs=["receipt-1"])
    with pytest.raises(ContractError, match="term_premium"):
        validate_result("macro", p, snapshot=s)
    value["status"] = "inferred"
    assert validate_result("macro", p, snapshot=s)


def test_supported_synthesis_requires_a_checkable_hypothesis():
    s, p = case()
    p["macro_diagnosis"]["synthesis"].update(status="inferred", evidence_refs=["receipt-1"])
    with pytest.raises(ContractError, match="causal hypothesis"):
        validate_result("macro", p, snapshot=s)
    p["macro_diagnosis"]["causal_hypotheses"] = [chain()]
    assert validate_result("macro", p, snapshot=s)


@pytest.mark.parametrize("mutation,match", [
    (lambda c: c.update(evidence_refs=[]), "evidence"),
    (lambda c: c.update(evidence_refs=["not-frozen"]), "evidence"),
    (lambda c: c.pop("alternative"), "missing"),
    (lambda c: c.update(confirmation=c["invalidation"]), "identical"),
    (lambda c: c.update(due_at="2026-09-05T12:00:00+08:00"), "cutoff"),
    (lambda c: c.update(due_at="2026-09-06T12:00:00"), "due_at"),
    (lambda c: c.update(id="obs-1"), "duplicate claim"),
])
def test_causal_chain_rejects_unbound_or_uncheckable_claims(mutation, match):
    s, p = case()
    c = chain()
    mutation(c)
    p["macro_diagnosis"]["causal_hypotheses"] = [c]
    with pytest.raises(ContractError, match=match):
        validate_result("macro", p, snapshot=s)


def test_future_check_is_distinct_from_result_validity():
    s, p = case()
    c = chain()
    c["due_at"] = "2026-09-10T12:00:00+08:00"
    p["macro_diagnosis"]["causal_hypotheses"] = [c]
    assert validate_result("macro", p, snapshot=s)["expires_at"] == s["expires_at"]
    p["macro_diagnosis"]["causal_hypotheses"].append(deepcopy(c))
    with pytest.raises(ContractError, match="duplicate ID"):
        validate_result("macro", p, snapshot=s)


@pytest.mark.parametrize("mutate,match", [
    (lambda c: c["upstream"].update(known_at="2026-09-05T13:00:00+08:00"), "cutoff"),
    (lambda c: c["upstream"]["baseline"].update(known_at="2026-09-05T10:01:00+08:00"), "before"),
    (lambda c: c["upstream"]["baseline"].update(known_at=None), "surprise"),
    (lambda c: c["upstream"].update(known_at=None), "surprise"),
    (lambda c: c["market_responses"][0].update(window_start="2026-09-05T08:00:00+08:00", window_end="2026-09-05T09:00:00+08:00"), "later information"),
    (lambda c: c["market_responses"][0].update(window_end=None), "complete"),
    (lambda c: c["market_responses"][0].update(window_start="2026-09-05T11:00:00+08:00"), "increasing"),
    (lambda c: c["feedback"].update(status="observed"), "feedback"),
    (lambda c: c["feedback"].update(evidence_refs=[]), "evidence"),
    (lambda c: c["feedback"].pop("actor"), "missing"),
    (lambda c: c["feedback"].pop("trigger"), "missing"),
    (lambda c: c["asset_implications"][0].update(status="unknown"), "direction"),
    (lambda c: c.update(evidence_refs=["receipt-1"]), "also be declared"),
])
def test_upstream_causality_temporal_and_feedback_shortcuts_are_rejected(mutate, match):
    s, p = case()
    c = chain()
    mutate(c)
    p["macro_diagnosis"]["causal_hypotheses"] = [c]
    with pytest.raises(ContractError, match=match):
        validate_result("macro", p, snapshot=s)


def test_unknown_root_remains_legal_without_claiming_a_surprise():
    s, p = case()
    c = chain()
    c["upstream"].update(status="unknown", driver_type="unknown", known_at=None)
    c["upstream"]["surprise"]["status"] = "unknown"
    c["market_responses"][0].update(window_start=None, window_end=None)
    p["macro_diagnosis"]["causal_hypotheses"] = [c]
    assert validate_result("macro", p, snapshot=s)


def test_dominant_hypothesis_must_exist_and_unknown_cannot_pick_a_winner():
    s, p = case()
    p["macro_diagnosis"].update(causal_hypotheses=[chain()], dominant_hypothesis_id="causal-1")
    with pytest.raises(ContractError, match="dominant"):
        validate_result("macro", p, snapshot=s)
    p["macro_diagnosis"]["synthesis"].update(status="inferred", evidence_refs=["receipt-1"])
    assert validate_result("macro", p, snapshot=s)
    p["macro_diagnosis"]["dominant_hypothesis_id"] = "fabricated"
    with pytest.raises(ContractError, match="dominant"):
        validate_result("macro", p, snapshot=s)


def test_catalogued_missing_source_cannot_be_cited_as_a_fact_even_in_followup():
    s, p = case()
    s["data"] = {"source_data": {"evidence_catalog": {"receipt-1": {"status": "missing"}}}}
    s["input_hash"] = canonical_hash(s["data"])
    p["input_hash"] = s["input_hash"]
    p["macro_diagnosis"]["economic_state"].update(status="observed", evidence_refs=["receipt-1"])
    with pytest.raises(ContractError, match="missing or invalid source"):
        validate_result("macro", p, snapshot=s)


def test_peer_agreement_is_not_an_observed_positioning_fact():
    s, p = case()
    s["evidence_refs"].append("peer:research")
    p["market_structure"]["dimensions"]["positioning_leverage"].update(status="observed", evidence_refs=["peer:research"])
    with pytest.raises(ContractError, match="peer interpretation"):
        validate_result("macro", p, snapshot=s)
    p["market_structure"]["dimensions"]["positioning_leverage"]["status"] = "unknown"
    p["observations"][0]["evidence_refs"] = ["peer:research"]
    with pytest.raises(ContractError, match="peer interpretation"):
        validate_result("macro", p, snapshot=s)


@pytest.mark.parametrize("driver,direction", [("demand", "positive"), ("supply", "negative")])
def test_same_oil_direction_can_have_different_conditional_cash_flow_implications(driver, direction):
    s, p = case()
    c = chain()
    c["shock"] = "合成油价上涨案例"
    c["upstream"]["driver_type"] = driver
    c["market_responses"][0]["variable"] = "BRENT"
    c["asset_implications"][0]["direction"] = direction
    p["macro_diagnosis"]["causal_hypotheses"] = [c]
    assert validate_result("macro", p, snapshot=s)


def test_physical_oil_supply_has_its_own_channel_and_notification_does_not_require_event_start():
    s, p = case()
    c = chain()
    c["upstream"].update(driver_type="supply", occurred_at=None,
                         finding="A shipping notice reports interrupted supply; the actual onset is unknown.")
    c["asset_implications"][0].update(asset="BRENT", channel="supply_demand", direction="positive",
                                       finding="Reduced deliverable oil could support price if inventories and substitutes do not offset it.")
    p["macro_diagnosis"]["causal_hypotheses"] = [c]
    validated = validate_result("macro", p, snapshot=s)
    upstream = validated["macro_diagnosis"]["causal_hypotheses"][0]["upstream"]
    assert upstream["occurred_at"] is None and upstream["known_at"] is not None
