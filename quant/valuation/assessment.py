"""Daily deterministic valuation receipts from existing frozen data; 零授权效力.

No network access, broker refresh, model invocation or state mutation. The CLI
reads an explicit input package and prints JSON; optional output is a research
artifact only. Missing data is reported per ticker, never replaced with a price.
"""
from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json

from quant.core.agent_contracts import canonical_hash
from quant.core.io_json import atomic_write_json, read_json
from quant.core.timeutil import ET
from quant.core.valuation_contracts import finite
from quant.valuation import archetypes, company_model, evidence, model, overlays, response, scenarios


def _timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        # Date-only observations are daily data, available at the day's end.
        if len(value) == 10:
            return parsed.replace(hour=23, minute=59, second=59, tzinfo=ET)
        return parsed if parsed.tzinfo is not None else None
    except ValueError:
        return None


def market_rate(documents: list[dict], *, as_of: str, max_age_days=7) -> dict:
    """Select by observation time, not filename; reject future/stale observations."""
    cutoff = _timestamp(as_of)
    if cutoff is None:
        raise ValueError("as_of requires a timestamp with timezone")
    candidates = []
    for document in documents:
        if not isinstance(document, dict):
            continue
        metric = (document.get("metrics") or {}).get("US10Y") or {}
        value, stamp = metric.get("value"), _timestamp(metric.get("as_of"))
        source = metric.get("source")
        if not finite(value) or not -2 < value < 25 or stamp is None or not source:
            continue
        if not 0 <= (cutoff - stamp).total_seconds() <= max_age_days * 86400:
            continue
        quality = document.get("data_quality") or {}
        if (metric.get("unit") not in (None, "percent") or metric.get("usable") is False
                or metric.get("freshness") in ("stale", "missing", "unknown", "invalid")
                or "US10Y" in (quality.get("unusable") or [])):
            continue
        published = document.get("generated_at_utc") or document.get("generated_at") or document.get("captured_at")
        if isinstance(published, dict):
            published = published.get("utc")
        if published and (_timestamp(published) is None or _timestamp(published) > cutoff):
            continue
        captured = _timestamp(published) if published else None
        candidates.append((stamp, captured or stamp, value / 100, source, metric.get("as_of"),
                           metric.get("freshness"), published))
    if not candidates:
        return {"rf": None, "status": "unknown", "basis": "无有效且未过期的US10Y观测；需要显式利率假设"}
    stamp, _, value, source, original, freshness, captured = max(candidates, key=lambda r: r[:2])
    return {"rf": value, "status": "observed", "as_of": original,
            "basis": f"US10Y {value:.2%} as_of {original}; flat-curve valuation approximation",
            "source": source, "snapshot_captured_at": captured, "freshness": freshness,
            "availability_basis": "captured source observation; not a verified historical publication vintage"}


def prepare_company(bundle, *, ticker: str, as_of: str):
    """Shared daily/weekly availability and evidence gate; detached calculation copy."""
    cutoff = _timestamp(as_of)
    if cutoff is None:
        raise ValueError("invalid valuation cutoff")
    if not isinstance(bundle, dict):
        raise ValueError("fundamentals_missing")
    if bundle.get("ticker") != ticker:
        raise ValueError("fundamentals_ticker_mismatch")
    fetched = _timestamp(bundle.get("fetched_at"))
    if fetched is None or fetched > cutoff:
        raise ValueError("financial_bundle_not_available_at_cutoff")
    prepared = bundle.get("evidence_prepared_at")
    if prepared and (_timestamp(prepared) is None or _timestamp(prepared) > cutoff):
        raise ValueError("financial_supplement_preparation_after_cutoff")
    if any(row.get("available_at") and (_timestamp(row["available_at"]) is None or
            _timestamp(row["available_at"]) > cutoff) for row in bundle.get("quarters") or []):
        raise ValueError("financial_disclosure_not_available_at_cutoff")
    periods = [_timestamp(row.get("period_end")) for row in bundle.get("quarters") or []]
    if not periods or any(stamp is None or stamp > cutoff for stamp in periods):
        raise ValueError("financial_periods_missing_or_after_cutoff")
    if (cutoff - max(periods)).days > 200:
        raise ValueError("latest_financial_period_stale")
    return evidence.supplement(bundle, bundle.get("balance_evidence") or [], as_of=as_of)


def with_quote(bundle: dict, quote: dict | None, *, as_of: str):
    """Separate current price evidence from the older financial capture (零授权效力)."""
    snap = bundle.get("snapshot") or {}
    quote = deepcopy(quote) if quote is not None else {
        "price": snap.get("last_price"), "as_of": snap.get("update_time"),
        "source": bundle.get("source"), "captured_at": bundle.get("fetched_at"),
        "basis": "financial cache quote; observation time retained"}
    observed, captured, cutoff = (_timestamp(quote.get("as_of")),
                                  _timestamp(quote.get("captured_at")), _timestamp(as_of))
    # Legacy private OpenD bundles contain an ET clock without an offset.
    if observed is None and str(quote.get("source") or "").startswith("OpenD"):
        from quant.core.quote_times import opend_quote_time
        observed = _timestamp(opend_quote_time(quote.get("as_of")))
    if (cutoff is None or observed is None or captured is None or captured > cutoff
            or observed > captured or not 0 <= (cutoff - observed).total_seconds() <= 4 * 86400
            or not quote.get("source") or not finite(quote.get("price")) or quote["price"] <= 0):
        raise ValueError("dated_recent_price_required; quote unavailable, stale or invalid")
    quote["as_of"] = observed.isoformat()
    result = deepcopy(bundle)
    result["snapshot"] = {**snap, "last_price": quote["price"], "update_time": quote["as_of"]}
    old_price, old_cap = snap.get("last_price"), snap.get("total_market_val")
    shares = (old_cap / old_price if finite(old_cap) and finite(old_price) and old_cap > 0 and old_price > 0
              else snap.get("outstanding_shares") or snap.get("issued_shares"))
    if finite(shares) and shares > 0:
        result["snapshot"]["total_market_val"] = quote["price"] * shares
        quote["market_cap_basis"] = "quote × cached share anchor (original market cap / original price when present); share count not refreshed"
    else:
        result["snapshot"]["total_market_val"] = None
    return result, quote


def _compact(receipt):
    value = {k: v for k, v in receipt.items() if k not in ("partial_calculation", "reference")}
    for name in ("partial_calculation", "reference"):
        if name in receipt:
            value[name] = {k: v for k, v in receipt[name].items() if k != "path"}
    value["full_receipt_hash"] = value.pop("receipt_hash")
    value["receipt_hash"] = canonical_hash(value)
    return value


def _short_term(value, ticker, bundle, *, cutoff, as_of):
    """Empirical returns do not depend on DCF coverage or a discount-rate input."""
    events = [row for row in value.get("response_events") or [] if row.get("ticker") == ticker]
    query = (value.get("response_queries") or {}).get(ticker)
    unknown = {"status": "unknown", "reason": "dated_event_surprises_and_mature_outcomes_required"}
    if not events or not query:
        return unknown
    try:
        if not isinstance(query, dict):
            raise ValueError("response_query_must_be_an_object")
        known, expires = _timestamp(query.get("known_at")), _timestamp(query.get("valid_until"))
        if known is None or expires is None or not known <= cutoff < expires:
            raise ValueError("response_query_not_valid_at_cutoff")
        snap = (bundle or {}).get("snapshot") or {}
        if not query.get("spot"):
            captured = _timestamp((bundle or {}).get("fetched_at"))
            if captured is None or captured > cutoff:
                raise ValueError("response_quote_capture_unavailable_at_cutoff")
        spot = query.get("spot") or {"price": snap.get("last_price"), "as_of": snap.get("update_time"),
                                     "source": (bundle or {}).get("source")}
        quote = _timestamp(spot.get("as_of"))
        if quote is None or not 0 <= (cutoff - quote).total_seconds() <= 4 * 86400 or not spot.get("source"):
            raise ValueError("dated_recent_spot_required_for_short_term")
        cohort = query.get("cohort")
        if not isinstance(cohort, dict) or cohort.get("ticker") != ticker:
            raise ValueError("explicit_response_cohort_required")
        if cohort.get("anchor_convention") == "first_close_after_information":
            from quant.valuation.event_data import anchor_after_information
            _, expected_anchor = anchor_after_information(query.get("information_known_at"))
            if quote != expected_anchor or known > quote:
                raise ValueError("response_spot_does_not_match_first_information_close")
        result = response.forecast(events, features=query["features"], spot=spot["price"], as_of=as_of,
                                   feature_source=query["source"], cohort=cohort)
        result["status"] = "research_estimate" if any(w["status"] == "estimated" for w in result["windows"].values()) else "unknown"
        result["spot_evidence"] = spot
        return result
    except (ValueError, KeyError, TypeError, OverflowError) as exc:
        return {"status": "unknown", "reason": str(exc)}


def resolve_market(inputs: dict, *, as_of: str, observed_rate: dict):
    """One market-assumption contract for frozen daily and weekly valuation."""
    cutoff = _timestamp(as_of)
    if cutoff is None:
        raise ValueError("invalid valuation cutoff")
    market = inputs.get("market") or {}
    if "rf" in market or "erp" in market:
        stamp = _timestamp(market.get("as_of"))
        if not market.get("basis") or stamp is None or stamp > cutoff:
            raise ValueError("market assumption requires basis and availability at cutoff")
    rf = dict(observed_rate)
    if market.get("rf") is not None:
        if not finite(market["rf"]) or not market.get("basis") or not _timestamp(market.get("as_of")):
            raise ValueError("explicit market assumption requires finite rate, basis and timestamp")
        if _timestamp(market["as_of"]) > cutoff:
            raise ValueError("market assumption was unavailable at cutoff")
        rf = {"rf": market["rf"], "status": "assumption", "basis": market["basis"], "as_of": market["as_of"]}
    erp = market.get("erp", model.DEFAULT_ERP)
    erp_basis = market.get("basis") if "erp" in market else "declared model ERP assumption; not a live observation"
    if not finite(erp) or erp < 0:
        raise ValueError("invalid ERP")
    return rf, erp, erp_basis


def build(data: dict, *, as_of: str, macro_scenarios=None, evidence_refs=None) -> dict:
    """Compute from the full shared archive, never the eight-quarter worker view."""
    cutoff = _timestamp(as_of)
    if cutoff is None:
        raise ValueError("invalid valuation cutoff")
    value = data.get("valuation") or {}
    inputs = value.get("inputs") or {"tickers": {}}
    rf, erp, erp_basis = resolve_market(inputs, as_of=as_of,
        observed_rate=market_rate(value.get("market_snapshots") or [], as_of=as_of))
    output = {"as_of": as_of, "model_version": model.MODEL_VERSION, "market": {**rf, "erp": erp, "erp_basis": erp_basis},
              "input_hash": canonical_hash(value), "tickers": {}, "authority": "analysis_only",
              "publication": model.publication_policy()}
    if value.get("company_models"):
        output["model_version"] += "+" + company_model.VERSION
    names = data.get("selected_tickers") or sorted((inputs.get("tickers") or {}))
    for ticker in names:
        bundle = (value.get("fundamentals") or {}).get(ticker)
        result = {"status": "unknown", "missing": [], "baseline": None,
                  "sensitivities": [], "macro_scenarios": [],
                  "data_readiness": evidence.readiness(bundle),
                  "short_term": _short_term(value, ticker, bundle, cutoff=cutoff, as_of=as_of)}
        output["tickers"][ticker] = result
        # Price evidence is useful even when a company model abstains; it is
        # neither a valuation nor a fallback to the archetype methodology.
        try:
            _, quote = with_quote(bundle or {}, (value.get('quotes') or {}).get(ticker), as_of=as_of)
            result['reference_quote'] = {'status': 'observed', **quote}
        except ValueError as exc:
            result['reference_quote'] = {'status': 'unknown', 'reason': str(exc)}
        try:
            # A company package owns its methodology. Failure/expiry cannot fall
            # back to the rejected archetype model. It does not require US10Y to
            # reconcile statements or calculate an explicit earnings path.
            if ticker in (value.get("company_models") or {}):
                packet = value["company_models"][ticker]
                if not isinstance(packet, dict) or packet.get("ticker") != ticker:
                    raise ValueError("company_model_ticker_mismatch")
                research = company_model.build(packet, as_of=as_of)
                result.update(status="company_research", company_research=research)
                result["missing"].append("company_model_not_yet_accepted")
                if macro_scenarios:
                    result["missing"].append("macro_scenarios_not_applied_to_company_model")
                if research["status"] == "input_mismatch":
                    result.update(status="unknown")
                    result["missing"].append("company_disclosure_reconciliation_failed")
                operating = research.get("estimability") or {}
                result["estimability"] = {"operating_value": (
                    operating.get("operating_value", "unknown") if research["status"] != "input_mismatch" else "unknown"),
                    "reason": operating.get("reason"),
                    "fair_value_per_share": "withheld", "market_target_12m": "withheld"}
                continue
            if rf["rf"] is None:
                raise ValueError(rf["basis"])
            bundle, supplement_receipt = prepare_company(bundle, ticker=ticker, as_of=as_of)
            result["balance_evidence"] = supplement_receipt
            result["data_readiness"] = evidence.readiness(bundle)
            resolved = archetypes.resolve(ticker, inputs)
            if resolved["archetype"] == "not_applicable":
                raise ValueError("business_model_not_supported")
            base_id = canonical_hash(bundle)
            active = overlays.active_overlays(value.get("overlays") or [], ticker=ticker,
                                               archetype=resolved["archetype"], as_of=as_of,
                                               baseline_id=base_id)
            # Manual entries retain the same visibility/expiry rules.
            manual = overlays.manual_records(ticker, resolved["news_overlay"])
            if manual:
                active = overlays.active_overlays((value.get("overlays") or []) + manual, ticker=ticker,
                                                   archetype=resolved["archetype"], as_of=as_of, baseline_id=base_id)
            calculation_bundle, quote = with_quote(bundle, (value.get("quotes") or {}).get(ticker), as_of=as_of)
            baseline = model.valuate(calculation_bundle, resolved, rf=rf["rf"], erp=erp, beta=None, overlays=active, as_of=as_of)
            result["quote_evidence"] = quote
            result.update(status="computed", baseline={k: baseline.get(k) for k in (
                "fair_value", "fair_value_basis", "market_target_12m", "scenarios", "scenario_expected_value",
                "scenario_probability_basis", "price", "price_time", "spec", "cost_of_capital", "flags", "confidence")})
            result["baseline"].update(fundamentals_hash=base_id, fundamentals_fetched_at=bundle["fetched_at"],
                                       forward=bundle.get("forward"), applied_overlays=active)
            result["missing"].extend(["segment_specific_operating_model_not_supplied",
                                       "future_financing_and_dilution_not_fully_modelled",
                                       "point_in_time_consensus_history_unverified"])
            ref = "fundamentals:" + ticker
            for scenario in scenarios.sensitivities(len(baseline["base"]["path"]), ref):
                result["sensitivities"].append(_compact(scenarios.evaluate(baseline, scenario)))
            links = list(resolved["transmissions"])
            exposure_error = None
            if resolved.get("rate_exposure"):
                try:
                    link = evidence.rate_exposure(resolved["rate_exposure"], as_of=as_of,
                                                  baseline_period=baseline["drivers"]["latest_period_end"])
                    if any(r["factor"] == link["factor"] and r["driver"] == link["driver"] for r in links):
                        raise ValueError("duplicate_explicit_and_exposure_interest_channel")
                    links.append(link)
                    result["rate_exposure"] = {"profile": resolved["rate_exposure"], "derived_transmission": link}
                except (ValueError, KeyError, TypeError) as exc:
                    exposure_error = str(exc)
                    result["missing"].append(exposure_error)
            for scenario in macro_scenarios or []:
                try:
                    receipt = scenarios.evaluate(baseline, scenario, transmissions=links, evidence_refs=evidence_refs)
                    policy = any(s["factor"] == "policy_rate_bp" for s in scenario["shocks"])
                    if policy and resolved.get("rate_exposure") and (exposure_error or not any(
                            r["factor"] == "policy_rate_bp" and r["driver"] == "rf_bp" for r in links)):
                        receipt["missing"].append(exposure_error or "policy_curve_transmission_not_supplied")
                        receipt.update(status="partial", fair_value=None, market_target_12m=None, intrinsic_delta=None)
                        receipt["receipt_hash"] = canonical_hash({k: v for k, v in receipt.items() if k != "receipt_hash"})
                    result["macro_scenarios"].append(_compact(receipt))
                except (ValueError, TypeError) as exc:
                    result["macro_scenarios"].append({"status": "unknown", "scenario_id": scenario.get("id"), "reason": str(exc)})
        except (ValueError, KeyError, TypeError, OverflowError) as exc:
            result["missing"].append(str(exc))
        reason = "company input expired" if "company input expired" in result["missing"] else (
            result["missing"][0] if result["missing"] else "company_model_not_available")
        result["estimability"] = {"operating_value": "input_expired" if reason == "company input expired"
                                  else "legacy_diagnostic" if result["status"] == "computed" else "unknown",
                                  "reason": reason, "fair_value_per_share": "withheld",
                                  "market_target_12m": "withheld"}
    output["receipt_hash"] = canonical_hash(output)
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="frozen input data JSON (or snapshot containing data)")
    parser.add_argument("--as-of", help="timezone-aware cutoff; defaults to snapshot as_of or current UTC")
    parser.add_argument("--scenarios", type=Path)
    parser.add_argument("--output", type=Path, help="optional research receipt; no state synchronization")
    args = parser.parse_args(argv)
    package = read_json(args.input)
    stamp = args.as_of or package.get("as_of") or datetime.now(timezone.utc).isoformat()
    result = build(package.get("data", package), as_of=stamp,
                   macro_scenarios=read_json(args.scenarios) if args.scenarios else [],
                   evidence_refs=package.get("evidence_refs"))
    if args.output:
        atomic_write_json(args.output, result)
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
