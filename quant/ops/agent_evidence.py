"""Build research-only portfolio sizing receipts from explicit frozen inputs.

This adapter performs no I/O and never calls an entry/exit CLI, account fetch,
option-chain builder, notification or state writer. The host supplies already
computed buying-power room and binding stops. Formula ownership stays in core.
Each entry result is an independent ceiling against the same book, not a
reservation, entry authorization, exit override or combined allocation.
"""
from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from copy import deepcopy
from datetime import datetime
import math

from quant.core import book, sizing
from quant.core.agent_contracts import canonical_hash, card_revision
from quant.core.holdings import is_managed
from quant.core.occ import parse_occ


def _number(value, *, positive=False):
    return (type(value) in (int, float) and math.isfinite(value)
            and (value > 0 if positive else value >= 0))


def _time(value):
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp if "T" in value and stamp.tzinfo is not None else None


def _object(value):
    return value if isinstance(value, dict) else {}


def _source_gaps(bundle):
    refs, times = bundle.get("source_refs"), bundle.get("source_times")
    refs = refs if isinstance(refs, dict) else {}
    times = times if isinstance(times, dict) else {}
    as_of = _time(bundle.get("as_of"))
    gaps = [] if as_of else ["as_of_missing_or_without_timezone"]
    for key in ("positions_doc", "nav", "power_room", "market", "entries"):
        if not isinstance(refs.get(key), str) or not refs[key].strip():
            gaps.append(f"source_ref_missing:{key}")
        source_time = _time(times.get(key))
        if source_time is None:
            gaps.append(f"source_time_missing:{key}")
        elif as_of and source_time > as_of:
            gaps.append(f"source_time_after_bundle:{key}")
    return gaps


def _book_gaps(positions, nav, market):
    gaps, pending = [], []
    if not _number(nav, positive=True):
        gaps.append("nav_missing_or_invalid")
    if not isinstance(positions, dict) or not isinstance(positions.get("tickers"), dict):
        return gaps + ["positions_doc_missing_or_invalid"], pending
    for ticker, body in positions["tickers"].items():
        if not isinstance(body, dict):
            gaps.append(f"position_invalid:{ticker}")
            continue
        orders = body.get("pending_orders")
        if orders:
            pending.append({"ticker": ticker, "orders": deepcopy(orders)})
        instrument = body.get("instrument")
        if (not isinstance(instrument, dict)
                or instrument.get("type") not in ("stock", "option")
                or (instrument.get("role") is not None and not isinstance(instrument["role"], str))):
            gaps.append(f"instrument_missing_or_invalid:{ticker}")
            continue
        if not is_managed(body):
            continue  # The existing managed-book policy determines this scope.
        broker = body.get("broker")
        qty = broker.get("qty") if isinstance(broker, dict) else None
        if type(qty) not in (int, float) or not math.isfinite(qty):
            gaps.append(f"quantity_missing_or_invalid:{ticker}")
            continue
        if qty == 0:
            continue
        if qty < 0:
            gaps.append(f"short_position_outside_receipt_scope:{ticker}")
        if not _number(broker.get("current_price"), positive=True):
            gaps.append(f"broker_price_missing_or_invalid:{ticker}")
        underlying = ticker
        if instrument["type"] == "option":
            option = instrument.get("option") or {}
            underlying = option.get("underlying") if isinstance(option, dict) else None
            if not isinstance(underlying, str) or not underlying:
                gaps.append(f"option_underlying_missing:{ticker}")
                continue
            occ = parse_occ(ticker)
            if not occ or occ["underlying"] != underlying:
                gaps.append(f"option_identity_inconsistent:{ticker}")
            elif occ["side"] != "C":
                gaps.append(f"put_position_outside_receipt_scope:{ticker}")
            delta = broker.get("delta")
            if delta is not None and (type(delta) not in (int, float) or not 0 <= delta <= 1):
                gaps.append(f"option_delta_invalid:{ticker}")
        elif parse_occ(ticker):
            gaps.append(f"stock_identity_inconsistent:{ticker}")
        data = market.get(underlying)
        if not isinstance(data, dict):
            gaps.append(f"market_missing:{underlying}")
            continue
        if not _number(data.get("atr20"), positive=True):
            gaps.append(f"atr20_missing_or_invalid:{underlying}")
        if instrument["type"] == "option" and not _number(data.get("price"), positive=True):
            gaps.append(f"underlying_price_missing_or_invalid:{underlying}")
    return gaps, pending


def _card_gaps(card):
    if not isinstance(card, dict) or not isinstance(card.get("agent"), dict):
        return ["entry_card_missing_or_invalid"]
    gaps = []
    if _object(card.get("instrument")).get("type") != "stock":
        gaps.append("option_entry_requires_separate_contract_receipt")
    regime = card["agent"].get("current_regime")
    cap = _object(regime).get("cap_pct_nav")
    if not _number(cap) or cap not in (0, sizing.NOTIONAL_CAP):
        gaps.append("entry_regime_missing_or_noncanonical")
    history = card["agent"].get("regime_history", [])
    if (history is not None and (not isinstance(history, list)
                                or any(not isinstance(item, dict) for item in history))):
        gaps.append("entry_regime_history_invalid")
    for field in ("exit_info", "readd_from_position"):
        if card.get(field) is not None and not isinstance(card[field], dict):
            gaps.append(f"entry_{field}_invalid")
    return gaps


def build_portfolio_evidence(bundle: dict) -> dict:
    """Return immutable-source mechanical receipts, without reading any defaults.

    Required bundle fields:
      as_of: timezone-aware ISO timestamp (capture time, not a source timestamp)
      positions_doc: the frozen positions document, including pending_orders
      nav: positive USD NAV; power_room: nonnegative *remaining* USD buying power
      market: {ticker: {price: number, atr20: positive number}}
      entries: {ticker: {card: dict, price: positive number,
                        binding_stop: positive number, stop_basis: nonempty text,
                        event_shrink: number in [0, 1]}}
      source_refs/source_times: maps keyed positions_doc/nav/power_room/market/entries

    event_shrink=1 is an explicit inactive earnings adjustment, not an omitted
    assumption. ATR20, binding stops and power_room are frozen host receipts;
    missing values remain unknown. This function does not derive them from prose.
    Supported book scope is long stocks and long calls; entry ceilings are stock
    only. Unsupported contracts remain unknown, without approximating their risk.
    """
    if not isinstance(bundle, dict):
        raise ValueError("portfolio inputs must be an explicit JSON object")
    input_hash = canonical_hash(bundle)  # Strict JSON, including rejection of NaN.
    frozen = deepcopy(bundle)
    positions, nav, power = frozen.get("positions_doc"), frozen.get("nav"), frozen.get("power_room")
    market = frozen.get("market") if isinstance(frozen.get("market"), dict) else {}
    entries = frozen.get("entries") if isinstance(frozen.get("entries"), dict) else {}
    source_gaps = _source_gaps(frozen)
    for key in ("market", "entries"):
        if not isinstance(frozen.get(key), dict):
            source_gaps.append(f"{key}_missing_or_invalid")
    book_gaps, pending = _book_gaps(positions, nav, market)
    pending_gaps = [f"pending_order_state_missing_or_invalid:{ticker}"
                    for ticker, body in _object(_object(positions).get("tickers")).items()
                    if not isinstance(body, dict) or not isinstance(body.get("pending_orders"), list)]
    usage = None
    if not book_gaps:
        # Frozen-input counterpart of book.load_usage: its disk-backed defaults
        # cannot be used here. Pass both callbacks, reusing the exact pure math.
        usage = sizing.book_usage(positions, nav,
                                  price_of=lambda ticker: (market.get(ticker) or {}).get("price"),
                                  atr_of=lambda ticker: (market.get(ticker) or {}).get("atr20"))
        if usage["missing_atr"]:
            book_gaps += ["usage_input_missing:" + ticker for ticker in usage["missing_atr"]]
            usage = None
    assumptions = (["delta_assumed_one:" + ticker for ticker in usage["assumed_full_delta"]]
                   if usage else [])
    receipts = {}
    for ticker, entry in entries.items():
        gaps = list(book_gaps)
        values = None
        inputs = {}
        if not isinstance(entry, dict):
            gaps.append("entry_input_invalid")
        else:
            card = entry.get("card")
            gaps += _card_gaps(card)
            price, stop = entry.get("price"), entry.get("binding_stop")
            if not _number(price, positive=True):
                gaps.append("entry_price_missing_or_invalid")
            if not _number(stop, positive=True) or (_number(price, positive=True) and stop >= price):
                gaps.append("binding_stop_missing_or_invalid")
            if not isinstance(entry.get("stop_basis"), str) or not entry["stop_basis"].strip():
                gaps.append("binding_stop_provenance_missing")
            atr = _object(market.get(ticker)).get("atr20")
            if not _number(atr, positive=True):
                gaps.append("entry_atr20_missing_or_invalid")
            if not _number(power):
                gaps.append("power_room_missing_or_invalid")
            shrink = entry.get("event_shrink")
            if not _number(shrink) or shrink > 1:
                gaps.append("earnings_shrink_missing_or_invalid")
            if not gaps:
                exposure = book.exposure_of(ticker, positions_doc=positions)
                gate_no = sizing.gate_count(card)
                inputs = {"nav": nav, "power_room": power, "price": price, "atr20": atr,
                          "binding_stop": stop, "stop_basis": entry["stop_basis"],
                          "event_shrink": shrink, "gate_no": gate_no, "exposure": exposure,
                          "card_revision": card_revision(card)}
                values = sizing.entry_cap(nav, card["agent"], atr=atr, price=price, stop=stop,
                                          usage=usage, event_shrink=shrink, gate_no=gate_no,
                                          power_room=power, exposure=exposure)
        receipts[ticker] = {"status": "unknown" if gaps else "computed",
                            "calculator": "quant.core.sizing.entry_cap", "values": values,
                            "inputs": inputs, "unknowns": gaps + source_gaps,
                            "scope": "independent_ceiling_on_same_book", "order_authorization": False}
    constants = {key: deepcopy(getattr(sizing, key)) for key in (
        "NOTIONAL_CAP", "GATE_RISK_CAPS", "AT_RISK_CAP_FRAC", "SHOCK_BUDGET", "SHOCK_X", "GROSS_CAP")}
    # Some pure constants are tuples; the receipt itself remains strict JSON.
    constants = {key: list(value) if isinstance(value, tuple) else value for key, value in constants.items()}
    out = {"receipt_version": "1.0.0", "input_hash": input_hash,
           "as_of": frozen.get("as_of"), "source_refs": frozen.get("source_refs") or {},
           "source_times": frozen.get("source_times") or {},
           "positions_revision": canonical_hash(positions),
           "policy_constants": constants, "policy_constants_hash": canonical_hash(constants),
           "usage": {"status": "unknown" if book_gaps else "computed", "values": usage,
                     "calculator": "quant.core.sizing.book_usage", "unknowns": book_gaps + source_gaps,
                     "assumptions": assumptions},
           "entries": receipts, "pending_orders": pending,
           "freshness": {"status": "unknown", "reason": "source timestamps preserved; host must apply source-specific freshness policy"},
           "batch_allocation": {"status": "unknown", "values": None,
                                "reason": "single-name ceilings share one remaining budget; no reservations or batch allocation computed"},
           "unknowns": book_gaps + source_gaps + pending_gaps + ["source_freshness_not_evaluated"]
                       + (["pending_order_reservations_not_computed"] if pending else []),
           "authority": {"analysis_only": True, "may_authorize_entry": False,
                         "may_override_exit": False, "may_write_state": False, "may_place_orders": False}}
    out["receipt_id"] = canonical_hash(out)
    return out
