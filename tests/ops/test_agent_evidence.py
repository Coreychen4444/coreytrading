"""Synthetic-only tests for frozen portfolio receipts and missing-input safety."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from quant.core import book, sizing
from quant.core.agent_contracts import ContractError, canonical_hash
from quant.core.holdings import UNMANAGED_ROLES
from quant.ops.agent_evidence import build_portfolio_evidence


@pytest.fixture
def bundle():
    card = {"instrument": {"type": "stock"}, "agent": {
        "current_regime": {"name": "R_hold", "cap_pct_nav": sizing.NOTIONAL_CAP},
        "regime_history": [{"date": "2026-09-01", "direction": "up", "cap_pct_nav": sizing.NOTIONAL_CAP}]}}
    names = ("positions_doc", "nav", "power_room", "market", "entries")
    return {"as_of": "2026-09-05T04:30:00Z",
            "positions_doc": {"tickers": {
                "SYN": {"instrument": {"type": "stock"}, "broker": {"qty": 10, "current_price": 100},
                        "pending_orders": []},
                "SECOND": {"instrument": {"type": "stock"}, "broker": {"qty": 5, "current_price": 50},
                           "pending_orders": []}}},
            "nav": 10000, "power_room": 6000,
            "market": {"SYN": {"price": 100, "atr20": 3}, "SECOND": {"price": 50, "atr20": 2}},
            "entries": {"SYN": {"card": card, "price": 100, "binding_stop": 90,
                                "stop_basis": "host mechanical binding-stop receipt", "event_shrink": 1}},
            "source_refs": {name: "synthetic:" + name for name in names},
            "source_times": {name: "2026-09-04T20:00:00Z" for name in names}}


def test_matches_canonical_sizing_with_frozen_inputs(bundle):
    output = build_portfolio_evidence(bundle)
    usage = sizing.book_usage(bundle["positions_doc"], bundle["nav"],
                              price_of=lambda ticker: bundle["market"][ticker]["price"],
                              atr_of=lambda ticker: bundle["market"][ticker]["atr20"])
    exposure = book.exposure_of("SYN", positions_doc=bundle["positions_doc"])
    entry = bundle["entries"]["SYN"]
    expected = sizing.entry_cap(bundle["nav"], entry["card"]["agent"], atr=3, price=100, stop=90,
                                usage=usage, event_shrink=1, gate_no=sizing.gate_count(entry["card"]),
                                power_room=6000, exposure=exposure)
    assert output["usage"]["status"] == "computed"
    assert output["usage"]["values"] == usage
    receipt = output["entries"]["SYN"]
    assert receipt["status"] == "computed" and receipt["values"] == expected
    assert receipt["inputs"]["exposure"] == exposure == 1000
    assert receipt["inputs"]["card_revision"] == canonical_hash(entry["card"])
    assert output["positions_revision"] == canonical_hash(bundle["positions_doc"])
    assert output["policy_constants_hash"] == canonical_hash(output["policy_constants"])


def test_freeze_cli_recomputes_receipts_from_explicit_inputs(bundle, tmp_path, capsys):
    from quant.ops import agent_runtime
    data, output = tmp_path / "evidence.json", tmp_path / "snapshot.json"
    data.write_text(json.dumps({"portfolio_inputs": bundle,
                                "portfolio_risk_receipts": {"stale": "must be replaced"}}))
    assert agent_runtime.main(["freeze", "--data", str(data), "--output", str(output)]) == 0
    snapshot = json.loads(output.read_text())
    receipt = snapshot["data"]["portfolio_risk_receipts"]
    assert receipt == build_portfolio_evidence(bundle)
    assert snapshot["input_hash"] == canonical_hash(snapshot["data"])
    assert "portfolio_risk_receipts" in snapshot["evidence_refs"]
    assert receipt["authority"]["may_authorize_entry"] is False
    assert json.loads(capsys.readouterr().out)["snapshot_id"] == snapshot["snapshot_id"]


def test_no_io_defaults_and_no_mutation(bundle, monkeypatch):
    before = deepcopy(bundle)

    def forbidden(*args, **kwargs):
        raise AssertionError("A frozen receipt may not touch I/O-backed defaults")

    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", forbidden)
        patch.setattr(Path, "read_text", forbidden)
        for name in ("read_json", "iter_jsonl", "iter_events", "load_usage", "latest_nav", "power_room", "price_of", "atr20_of"):
            patch.setattr(book, name, forbidden)
        first = build_portfolio_evidence(bundle)
        second = build_portfolio_evidence(bundle)
    assert bundle == before
    assert first == second
    assert first["receipt_id"] == canonical_hash({k: v for k, v in first.items() if k != "receipt_id"})
    first["source_refs"]["nav"] = "altered"
    assert bundle == before
    bundle["power_room"] -= 1
    changed = build_portfolio_evidence(bundle)
    assert changed["input_hash"] != second["input_hash"]
    assert changed["receipt_id"] != second["receipt_id"]


@pytest.mark.parametrize("mutation,reason", [
    (lambda b: b.pop("nav"), "nav_missing_or_invalid"),
    (lambda b: b.update(nav=False), "nav_missing_or_invalid"),
    (lambda b: b.update(positions_doc=None), "positions_doc_missing_or_invalid"),
    (lambda b: b["positions_doc"]["tickers"]["SYN"]["broker"].pop("qty"), "quantity_missing_or_invalid:SYN"),
    (lambda b: b["positions_doc"]["tickers"]["SYN"]["broker"].pop("current_price"), "broker_price_missing_or_invalid:SYN"),
    (lambda b: b["market"]["SYN"].pop("atr20"), "atr20_missing_or_invalid:SYN"),
    (lambda b: b["positions_doc"]["tickers"]["SYN"].update(instrument=["stock"]), "instrument_missing_or_invalid:SYN"),
    (lambda b: b["positions_doc"]["tickers"]["SYN"]["broker"].update(qty=-2), "short_position_outside_receipt_scope:SYN"),
])
def test_incomplete_book_never_looks_like_zero_usage(bundle, mutation, reason):
    mutation(bundle)
    output = build_portfolio_evidence(bundle)
    assert output["usage"]["status"] == "unknown"
    assert output["usage"]["values"] is None
    assert reason in output["usage"]["unknowns"]
    assert output["entries"]["SYN"]["values"] is None


@pytest.mark.parametrize("mutation,reason", [
    (lambda b: b.pop("power_room"), "power_room_missing_or_invalid"),
    (lambda b: b["entries"]["SYN"].pop("binding_stop"), "binding_stop_missing_or_invalid"),
    (lambda b: b["entries"]["SYN"].update(binding_stop=100), "binding_stop_missing_or_invalid"),
    (lambda b: b["entries"]["SYN"].pop("stop_basis"), "binding_stop_provenance_missing"),
    (lambda b: b["entries"]["SYN"].pop("event_shrink"), "earnings_shrink_missing_or_invalid"),
    (lambda b: b["entries"]["SYN"].update(event_shrink=1.1), "earnings_shrink_missing_or_invalid"),
    (lambda b: b["entries"]["SYN"]["card"]["agent"].pop("current_regime"), "entry_regime_missing_or_noncanonical"),
    (lambda b: b["entries"]["SYN"]["card"]["agent"]["current_regime"].update(cap_pct_nav=False), "entry_regime_missing_or_noncanonical"),
    (lambda b: b["entries"]["SYN"]["card"].update(agent=["bad"]), "entry_card_missing_or_invalid"),
    (lambda b: b["entries"]["SYN"]["card"]["agent"].update(regime_history=[None]), "entry_regime_history_invalid"),
    (lambda b: b["entries"]["SYN"]["card"].update(exit_info="bad"), "entry_exit_info_invalid"),
    (lambda b: b["entries"]["SYN"]["card"].update(instrument=["bad"]), "option_entry_requires_separate_contract_receipt"),
])
def test_missing_entry_arm_cannot_silently_leave_an_inflated_cap(bundle, mutation, reason):
    mutation(bundle)
    output = build_portfolio_evidence(bundle)
    assert output["usage"]["status"] == "computed"
    receipt = output["entries"]["SYN"]
    assert receipt["status"] == "unknown" and receipt["values"] is None
    assert reason in receipt["unknowns"]


@pytest.mark.parametrize("field", ["power_room", "event_shrink", "cap_pct_nav"])
def test_explicit_zero_is_computed_as_zero_without_authority(bundle, field):
    if field == "power_room":
        bundle[field] = 0
    elif field == "event_shrink":
        bundle["entries"]["SYN"][field] = 0
    else:
        bundle["entries"]["SYN"]["card"]["agent"]["current_regime"][field] = 0
    output = build_portfolio_evidence(bundle)
    assert output["entries"]["SYN"]["status"] == "computed"
    assert output["entries"]["SYN"]["values"]["final"] == 0
    assert output["entries"]["SYN"]["order_authorization"] is False


def add_option(bundle, *, delta=0.6, code="SYN270115C100000"):
    bundle["positions_doc"]["tickers"][code] = {
        "instrument": {"type": "option", "option": {"underlying": "SYN"}},
        "broker": {"qty": 2, "current_price": 5, "delta": delta}, "pending_orders": []}
    return code


def test_long_call_book_uses_premium_gross_and_core_delta_shock(bundle):
    add_option(bundle)
    output = build_portfolio_evidence(bundle)
    expected = sizing.book_usage(bundle["positions_doc"], 10000,
                                 price_of=lambda t: bundle["market"][t]["price"],
                                 atr_of=lambda t: bundle["market"][t]["atr20"])
    assert output["usage"]["values"] == expected
    assert output["usage"]["values"]["used_gross"] == pytest.approx(0.225)
    assert output["entries"]["SYN"]["inputs"]["exposure"] == 2000


def test_missing_option_delta_preserves_canonical_conservative_assumption(bundle):
    code = add_option(bundle, delta=None)
    output = build_portfolio_evidence(bundle)
    assert output["usage"]["status"] == "computed"
    assert output["usage"]["assumptions"] == ["delta_assumed_one:" + code]
    assert output["usage"]["values"]["assumed_full_delta"] == [code]


@pytest.mark.parametrize("mutation,reason", [
    (lambda b, t: b["market"]["SYN"].pop("price"), "underlying_price_missing_or_invalid:SYN"),
    (lambda b, t: b["positions_doc"]["tickers"][t]["instrument"]["option"].update(underlying="WRONG"), "option_identity_inconsistent:"),
    (lambda b, t: b["positions_doc"]["tickers"][t]["broker"].update(delta=-0.5), "option_delta_invalid:"),
])
def test_incomplete_or_conflicting_options_do_not_reduce_usage(bundle, mutation, reason):
    code = add_option(bundle)
    mutation(bundle, code)
    output = build_portfolio_evidence(bundle)
    assert output["usage"]["values"] is None
    assert any(gap.startswith(reason) for gap in output["usage"]["unknowns"])


def test_put_book_and_option_entry_remain_unknown(bundle):
    code = add_option(bundle, code="SYN270115P100000")
    assert "put_position_outside_receipt_scope:" + code in build_portfolio_evidence(bundle)["usage"]["unknowns"]
    bundle["positions_doc"]["tickers"].pop(code)
    bundle["entries"]["SYN"]["card"]["instrument"]["type"] = "option"
    assert build_portfolio_evidence(bundle)["entries"]["SYN"]["values"] is None


def test_canonical_unmanaged_scope_and_known_empty_book(bundle):
    body = bundle["positions_doc"]["tickers"]["SYN"]
    body["instrument"]["role"] = next(iter(UNMANAGED_ROLES))
    body["broker"] = None  # Canonical policy excludes this body from managed risk.
    output = build_portfolio_evidence(bundle)
    assert output["usage"]["values"]["used_gross"] == 0.025
    assert output["entries"]["SYN"]["inputs"]["exposure"] == 0
    bundle["positions_doc"] = {"tickers": {}}
    output = build_portfolio_evidence(bundle)
    assert output["usage"]["status"] == "computed"
    assert output["usage"]["values"]["used_gross"] == 0


def test_pending_orders_and_multiple_names_do_not_become_batch_allocations(bundle):
    bundle["power_room"] = 100
    bundle["positions_doc"]["tickers"]["SYN"]["pending_orders"] = [{"id": "synthetic-buy", "side": "BUY", "qty": 3}]
    bundle["entries"]["SECOND"] = deepcopy(bundle["entries"]["SYN"])
    output = build_portfolio_evidence(bundle)
    assert all(entry["values"]["final"] == 100 for entry in output["entries"].values())
    assert all(entry["scope"] == "independent_ceiling_on_same_book" for entry in output["entries"].values())
    assert output["pending_orders"] == [{"ticker": "SYN", "orders": bundle["positions_doc"]["tickers"]["SYN"]["pending_orders"]}]
    assert "pending_order_reservations_not_computed" in output["unknowns"]
    assert output["batch_allocation"]["status"] == "unknown" and output["batch_allocation"]["values"] is None
    assert output["authority"] == {"analysis_only": True, "may_authorize_entry": False,
                                   "may_override_exit": False, "may_write_state": False, "may_place_orders": False}
    bundle["positions_doc"]["tickers"]["SYN"].pop("pending_orders")
    assert "pending_order_state_missing_or_invalid:SYN" in build_portfolio_evidence(bundle)["unknowns"]


def test_source_times_are_preserved_and_gaps_are_unknown(bundle):
    bundle["source_refs"].pop("nav")
    bundle["source_times"]["nav"] = "2026-09-01T20:00:00Z"
    bundle["source_times"]["power_room"] = "2026-09-06T00:00:00Z"
    bundle["source_times"]["market"] = "2026-09-04"
    output = build_portfolio_evidence(bundle)
    assert output["source_times"] == bundle["source_times"]
    assert output["freshness"]["status"] == "unknown"
    assert "source_ref_missing:nav" in output["unknowns"]
    assert "source_time_after_bundle:power_room" in output["unknowns"]
    assert "source_time_missing:market" in output["unknowns"]
    assert output["entries"]["SYN"]["status"] == "computed"  # Math exists; provenance still does not.
    assert "source_ref_missing:nav" in output["entries"]["SYN"]["unknowns"]


def test_non_json_rejected_and_absent_collections_reported(bundle):
    with pytest.raises(ValueError, match="explicit JSON"):
        build_portfolio_evidence(None)
    bundle["nav"] = float("nan")
    with pytest.raises(ContractError):
        build_portfolio_evidence(bundle)
    output = build_portfolio_evidence({})
    assert output["usage"]["values"] is None
    assert "entries_missing_or_invalid" in output["unknowns"]
    assert "market_missing_or_invalid" in output["unknowns"]
