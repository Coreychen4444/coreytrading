"""Validate technical proposals and submit through the existing revision-checked writer.

Preview is entirely in-memory: it never imports the writer or reads live data.
An approved review is evidence for a bounded card update, not trading authority.
The runtime owns run leases and receipts; this module never writes state itself.
"""
from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from contextlib import redirect_stdout
from copy import deepcopy
from datetime import date, datetime, timezone
import importlib.util
import io
import json
import math
import re

from quant.core.agent_contracts import (ContractError, canonical_hash, card_revision,
                                         validate_result, validate_snapshot)
from quant.core.market_sessions import MarketSessionError, latest_completed_session
from quant.core.num import coerce_float
from quant.core.paths import ZONE_WRITER_PATH
from quant.core.sizing import NOTIONAL_CAP
from quant.core.structure import gate_coherence
from quant.core.timeutil import ET


class SubmitError(ValueError):
    """A rejected or uncertain submission, with explicit side-effect provenance."""

    def __init__(self, message, *, side_effect_possible=False):
        super().__init__(message)
        self.side_effect_possible = side_effect_possible


# Exact spellings only: argparse abbreviations, duplicate options, equals syntax,
# positional arguments and worker-supplied identity/CAS/path flags are forbidden.
FLAG_ARITY = {
    "--zone1": 2, "--zone2": 2, "--trim": 2,
    **dict.fromkeys(("--no-chase-above", "--stop-level", "--stop-level-days",
                     "--thesis", "--key-risk", "--invalid-if", "--notes",
                     "--timeframe", "--methodology", "--reclaim-gates", "--valid-until",
                     "--anchored-on", "--anchor-basis", "--repair-gate-anchor-basis",
                     "--update-trigger", "--reasoning-summary", "--conviction", "--thesis-status",
                     "--action-reason", "--next-regime", "--touch-protocol", "--vehicle",
                     "--constraint-reason", "--constraint-from", "--constraint-until"), 1),
}
ZONE_KEYS = {"accumulation_zone_1", "accumulation_zone_2", "trim_zone", "no_chase_above",
             "stop_level", "stop_level_days", "valid_until", "timeframe", "methodology",
             "update_trigger", "reasoning_summary", "anchored_on", "reclaim_gates"}
GATE_KEYS = {"level", "days", "volume_ratio_min", "label", "unlocks", "anchored_on"}
NEXT_KEYS = {"name", "entry_gate", "cap_pct_nav", "participation", "zones", "provisional",
             "next_gate_preview", "derivation", "reason"}
FLAG_CHOICES = {
    "--timeframe": {"swing_2w", "swing_4w", "event_window", "post_earnings"},
    "--methodology": {"normal", "parabolic_mode_spirit"},
    "--conviction": {"low", "medium", "high"},
    "--thesis-status": {"intact", "needs_reverification", "weakening", "broken"},
    "--touch-protocol": {"A_take_first", "B_passive_only", "C_60m_first_bar", "none"},
    "--vehicle": {"stock", "leaps", "spread", "stock_first", "none"},
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _digest(value, label):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise SubmitError(f"{label}: expected canonical SHA256")
    return value


def _positive(value, label):
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise SubmitError(f"{label}: expected positive finite number")


def _day(value, label):
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError):
        raise SubmitError(f"{label}: expected YYYY-MM-DD") from None
    if parsed.isoformat() != value:
        raise SubmitError(f"{label}: expected YYYY-MM-DD")
    return parsed


def _keys(value, allowed, label, required=()):
    if type(value) is not dict or set(value) - allowed or set(required) - set(value):
        raise SubmitError(f"{label}: missing or unauthorized fields")


def _anchor(value, label):
    _keys(value, {"date", "basis"}, label, ("date", "basis"))
    _day(value["date"], label)
    if not isinstance(value["basis"], str) or not value["basis"].strip():
        raise SubmitError(f"{label}: missing evidence basis")


def _gate(value, label, *, preview=False):
    _keys(value, GATE_KEYS | ({"cap_pct_nav"} if preview else set()), label, ("level",))
    _positive(value["level"], label)
    if "days" in value and (type(value["days"]) is not int or value["days"] < 1):
        raise SubmitError(f"{label}.days: expected positive integer")
    if "volume_ratio_min" in value:
        _positive(value["volume_ratio_min"], label + ".volume_ratio_min")
    if "cap_pct_nav" in value:
        _cap(value["cap_pct_nav"], label)
    if "anchored_on" in value:
        _anchor(value["anchored_on"], label + ".anchored_on")
    for key in ("label", "unlocks"):
        if key in value and (not isinstance(value[key], str) or not value[key].strip()):
            raise SubmitError(f"{label}.{key}: expected nonempty text")


def _cap(value, label):
    if type(value) not in (int, float) or value not in (0, NOTIONAL_CAP):
        raise SubmitError(f"{label}: cap must use the existing binary policy")


def _zones(value, label):
    _keys(value, ZONE_KEYS, label)
    for key, item in value.items():
        if key in ("accumulation_zone_1", "accumulation_zone_2", "trim_zone"):
            if type(item) is not list or len(item) != 2:
                raise SubmitError(f"{label}.{key}: expected a pair")
            for n in item:
                _positive(n, label + "." + key)
            if item[0] > item[1]:
                raise SubmitError(f"{label}.{key}: reversed zone")
        elif key in ("no_chase_above", "stop_level"):
            _positive(item, label + "." + key)
        elif key == "stop_level_days":
            if type(item) is not int or item < 1:
                raise SubmitError(f"{label}.{key}: expected positive integer")
        elif key == "anchored_on":
            _anchor(item, label + "." + key)
        elif key == "reclaim_gates":
            _gates(item, label + "." + key)
        elif key == "valid_until":
            _day(item, label + "." + key)
        elif key in ("timeframe", "methodology") and (
                not isinstance(item, str) or item not in FLAG_CHOICES["--" + key]):
            raise SubmitError(f"{label}.{key}: unsupported value")
        elif not isinstance(item, str) or not item.strip():
            raise SubmitError(f"{label}.{key}: expected nonempty text")


def _gates(value, label):
    if type(value) is not list:
        raise SubmitError(f"{label}: expected gate list")
    for gate in value:
        _gate(gate, label)


def _json(raw, label):
    def unique(pairs):
        out = {}
        for key, value in pairs:
            if key in out:
                raise SubmitError(f"{label}: duplicate JSON key")
            out[key] = value
        return out

    try:
        value = json.loads(raw, object_pairs_hook=unique)
        canonical_hash(value)
    except (ValueError, TypeError) as exc:
        raise SubmitError(f"{label}: invalid strict JSON") from exc
    return value


def _parse_worker_args(argv):
    parsed, i = {}, 0
    while i < len(argv):
        flag = argv[i]
        if flag not in FLAG_ARITY or flag in parsed:
            raise SubmitError(f"unauthorized or duplicate worker flag: {flag!r}")
        count = FLAG_ARITY[flag]
        values = argv[i + 1:i + 1 + count]
        if len(values) != count or any(not v.strip() or v.startswith("-") for v in values):
            raise SubmitError(f"{flag}: missing or ambiguous value")
        value = values if count == 2 else values[0]
        if flag in FLAG_CHOICES and value not in FLAG_CHOICES[flag]:
            raise SubmitError(f"{flag}: unsupported value")
        if flag in ("--zone1", "--zone2", "--trim", "--no-chase-above", "--stop-level"):
            try:
                nums = [float(v) for v in values]
            except ValueError:
                raise SubmitError(f"{flag}: expected numbers") from None
            for n in nums:
                _positive(n, flag)
            if count == 2 and nums[0] > nums[1]:
                raise SubmitError(f"{flag}: reversed zone")
            value = nums if count == 2 else nums[0]
        elif flag == "--stop-level-days":
            if not re.fullmatch(r"[1-9][0-9]*", value):
                raise SubmitError(f"{flag}: expected positive integer")
            value = int(value)
        elif flag in ("--anchored-on", "--valid-until", "--constraint-from", "--constraint-until"):
            _day(value, flag)
        elif flag == "--reclaim-gates":
            value = _json(value, flag)
            _gates(value, flag)
        elif flag == "--next-regime":
            value = _json(value, flag)
            _keys(value, NEXT_KEYS, flag, ("name", "entry_gate"))
            # The transition engine emits future-gate skeletons with unknown
            # cap/zones. Match the existing writer schema without inventing
            # future authorization or forcing a speculative zone map.
            if value.get("cap_pct_nav") is not None:
                _cap(value["cap_pct_nav"], flag)
            _gate(value["entry_gate"], flag + ".entry_gate")
            if value.get("zones") is not None:
                _zones(value["zones"], flag + ".zones")
            if "next_gate_preview" in value:
                _gate(value["next_gate_preview"], flag + ".next_gate_preview", preview=True)
            if not isinstance(value["name"], str) or not value["name"].strip():
                raise SubmitError(f"{flag}.name: expected nonempty text")
            for key in ("derivation", "reason"):
                if key in value and (not isinstance(value[key], str) or not value[key].strip()):
                    raise SubmitError(f"{flag}.{key}: expected nonempty text")
            if "participation" in value and value["participation"] not in ("zone_eligible", "gate_only"):
                raise SubmitError(f"{flag}.participation: unsupported value")
            if "provisional" in value and (type(value["provisional"]) is not list or
                    any(v not in ("accumulation_zone_1", "accumulation_zone_2", "trim_zone")
                        for v in value["provisional"])):
                raise SubmitError(f"{flag}.provisional: unauthorized fields")
        parsed[flag] = value
        i += count + 1
    return parsed


def _validate_review(record, payload, snapshot, now):
    review = record.get("review")
    if review is None:
        return None
    if (not isinstance(review, dict) or review.get("status") != "succeeded"
            or review.get("role") != "technical"
            or not review.get("run_id") or review["run_id"] == record["run_id"]):
        raise SubmitError("review must be an independent successful run")
    proposer, reviewer = record.get("provider"), review.get("provider")
    if (not isinstance(proposer, str) or not proposer.strip()
            or not isinstance(reviewer, str) or not reviewer.strip()
            or proposer.strip().lower() == reviewer.strip().lower()):
        raise SubmitError("review requires distinct runtime provider identities")
    if review.get("policy_hash") != record["policy_hash"]:
        raise SubmitError("review policy differs from proposal policy")
    if review.get("reviewed_result_hash") != canonical_hash(payload):
        raise SubmitError("review is not bound to this exact result")
    if canonical_hash(review.get("snapshot")) != canonical_hash(snapshot):
        raise SubmitError("review used a different snapshot")
    reviewed = validate_result(review.get("role"), review.get("result"), snapshot=snapshot, now=now)
    decision = reviewed["review"]
    if not decision or decision["run_id"] != record["run_id"] or decision["verdict"] != "approve":
        raise SubmitError("review did not approve this proposal run")
    if datetime.fromisoformat(reviewed["generated_at"].replace("Z", "+00:00")) < datetime.fromisoformat(
            payload["generated_at"].replace("Z", "+00:00")):
        raise SubmitError("review predates proposal")
    return review["run_id"]


def _validate_card_inputs(data, parsed, now, *, apply):
    ticker, bucket, card = data.get("ticker"), data.get("bucket"), data.get("card")
    if not isinstance(ticker, str) or not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,31}", ticker):
        raise SubmitError("invalid snapshot ticker")
    if bucket not in ("positions", "watchlist") or not isinstance(card, dict):
        raise SubmitError("snapshot must identify an existing card and bucket")
    if not isinstance(card.get("instrument"), dict) or card["instrument"].get("type") != "stock":
        raise SubmitError("technical card submission supports stock structures only")
    if data.get("card_revision") != card_revision(card):
        raise SubmitError("snapshot card revision mismatch")
    bars, latest = data.get("completed_bars"), data.get("latest_completed_session")
    if not isinstance(bars, list) or not bars:
        raise SubmitError("missing completed bars")
    # Pure helper, no capture/network/state paths are invoked here.
    from quant.ops.agent_snapshots import completed_bars
    try:
        complete = completed_bars(bars, now)
    except (ValueError, TypeError, AttributeError) as exc:
        raise SubmitError("invalid completed bars") from exc
    if len(complete) != len(bars) or complete[-1]["time"] != latest:
        raise SubmitError("unfinished bars or latest completed session mismatch")
    session_day = _day(latest, "latest completed session")
    warnings = data.get("capture_warnings")
    if not isinstance(warnings, list) or any(not isinstance(v, str) for v in warnings):
        raise SubmitError("snapshot capture warnings are malformed")
    if any("bar" in warning and any(word in warning for word in ("missing", "older", "stale"))
           for warning in warnings):
        raise SubmitError("snapshot reports missing or stale bars")
    if parsed.get("--anchored-on") != latest:
        raise SubmitError("zone anchor must equal latest completed session")
    if not parsed.get("--anchor-basis") or not parsed.get("--reasoning-summary"):
        raise SubmitError("zone proposal requires evidence basis and reasoning")
    def same_anchor(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "anchored_on" and item["date"] != latest:
                    raise SubmitError("every proposed gate/zone anchor must match completed session")
                same_anchor(item)
        elif isinstance(value, list):
            for item in value:
                same_anchor(item)
    for flag in ("--next-regime", "--reclaim-gates"):
        if flag in parsed:
            same_anchor(parsed[flag])
    if (now.astimezone(ET).date() - session_day).days > 7:
        raise SubmitError("completed bars are stale")
    if apply:
        et = now.astimezone(ET)
        try:
            expected = latest_completed_session(now)
        except MarketSessionError as exc:
            raise SubmitError("exchange calendar unavailable: " + str(exc)) from exc
        if session_day != expected:
            raise SubmitError("latest exchange close unavailable; completed bars are stale")
        if "--valid-until" in parsed and _day(parsed["--valid-until"], "valid_until") < et.date():
            raise SubmitError("proposed zones have expired")
    return ticker, "current" if bucket == "positions" else "watchlist"


def _load_writer():
    spec = importlib.util.spec_from_file_location("controlled_zone_writer", ZONE_WRITER_PATH)
    if spec is None or spec.loader is None:
        raise SubmitError("controlled zone writer unavailable")
    writer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(writer)
    return writer


def _check_candidate(writer, argv, data):
    """Use the existing merger/schema and canonical policy defaults, without writing."""
    from quant.decision import regime_transition as rt
    args = writer.parse_args(argv)
    old = data["card"]
    # stop「消失」比「变松」更严重: 带价位带、不带 --stop-level 的重画会让 writer
    # 整块替换擦掉现有 stop; regime_transition 下行
    # 分支 `if stop is not None` 整段不评估 = 破位不降级。此处在 merge 之前按提案本身判:
    # 老卡有 stop、提案重画却没给 stop → 拒。保持 = 传原值 (writer 同值时自动带过 days/来历)。
    proposed = writer.build_zones_block(args, ticker=data["ticker"])
    old_stop = coerce_float(((old.get("agent") or {}).get("zones") or {}).get("stop_level"))
    if (set(proposed) & writer.REPAINT_KEYS) and old_stop is not None and proposed.get("stop_level") is None:
        raise SubmitError("proposal would remove existing stop")
    if data["bucket"] == "positions":
        candidate = writer.merge_position(deepcopy(old), args, ticker=data["ticker"])
        errors, _ = writer._schema.validate_positions_doc({"tickers": {data["ticker"]: candidate}})
    else:
        existing = deepcopy(old)
        existing["_ticker"] = data["ticker"]
        candidate = writer.merge_candidate(existing, args)
        errors, _ = writer._schema.validate_watchlist_doc({"tickers": {data["ticker"]: candidate}})
    if errors:
        raise SubmitError("candidate fails existing card schema: " + "; ".join(errors))
    agent, previous = candidate.get("agent") or {}, old.get("agent") or {}
    if old_stop is not None and coerce_float((agent.get("zones") or {}).get("stop_level")) is None:
        raise SubmitError("proposal would remove existing stop")   # merge-level backstop, live zones only
    coherence = gate_coherence(agent)
    if coherence:
        raise SubmitError("candidate fails existing gate coherence: " + "; ".join(coherence))
    for zones, old_zones in ((agent.get("zones") or {}, previous.get("zones") or {}),
                             ((agent.get("next_regime") or {}).get("zones") or {},
                              (previous.get("next_regime") or {}).get("zones") or {})):
        if zones == old_zones:
            continue
        old_stop_days = old_zones.get("stop_level_days") or rt.PARAMS["stop_days_default"]
        if (zones.get("stop_level_days") or rt.PARAMS["stop_days_default"]) > old_stop_days:
            raise SubmitError("proposal would relax existing stop confirmation")
    new_next, old_next = agent.get("next_regime") or {}, previous.get("next_regime") or {}
    for key in ("entry_gate", "next_gate_preview"):
        gate, baseline = new_next.get(key), old_next.get(key) or {}
        if not gate:
            continue
        if gate == baseline:
            continue  # Preserve an existing explicitly adjudicated exception.
        days = baseline.get("days") or rt.PARAMS["default_days"]
        ratio = baseline.get("volume_ratio_min") or rt.RECLAIM_VOL_RATIO_MIN
        if (gate.get("days") or rt.PARAMS["default_days"]) < days:
            raise SubmitError("proposal would relax existing gate confirmation")
        if (gate.get("volume_ratio_min") or 0) < ratio:
            raise SubmitError("proposal would relax existing gate volume evidence")
    for field in ("broker", "lots", "pending_orders", "position_summary", "exit_info"):
        if field in old and candidate.get(field) != old[field]:
            raise SubmitError(f"candidate changed protected field: {field}")


def apply_proposal(record, *, apply=False, require_review=True, on_precommit=None) -> dict:
    """Preview or apply a bounded card proposal under the host review policy.

    ``require_review=False`` is a trusted-host opt-out from model review only.
    Every evidence, revision, writer and trading-policy check still runs.
    ``on_precommit(receipt)`` is a trusted-host audit callback, never model input.
    It may persist the exact prepared hashes before the writer commits. It must
    not acquire the state lock, write cards, or perform external actions.
    """
    now = _now()
    writer_invoked = False
    try:
        if type(apply) is not bool or type(require_review) is not bool:
            raise SubmitError("apply and require_review must be explicit booleans")
        if on_precommit is not None and not callable(on_precommit):
            raise SubmitError("on_precommit must be a trusted callable")
        if not isinstance(record, dict) or record.get("role") != "technical":
            raise SubmitError("only the technical role can submit card proposals")
        if record.get("status") not in ("succeeded", "needs_review") or not record.get("run_id"):
            raise SubmitError("proposal run is not complete")
        _digest(record.get("policy_hash"), "policy_hash")
        snapshot = validate_snapshot(record.get("snapshot"), now=now)
        payload = validate_result("technical", record.get("result"), snapshot=snapshot, now=now)
        proposal = payload["proposal"]
        if not proposal or proposal["kind"] != "zone_update":
            raise SubmitError("run has no zone proposal")
        parsed = _parse_worker_args(proposal["zone_update_args"])
        ticker, bucket = _validate_card_inputs(snapshot["data"], parsed, now, apply=apply)
        reviewer_id = _validate_review(record, payload, snapshot, now)
        if apply and require_review and reviewer_id is None:
            raise SubmitError("apply requires independent review")
        argv = ["--ticker", ticker, "--bucket", bucket, "--expected-revision",
                snapshot["data"]["card_revision"], *proposal["zone_update_args"]]
        receipt = {"run_id": record["run_id"], "status": "dry_run", "applied": False,
                   "writer_args": argv, "card_revision": snapshot["data"]["card_revision"],
                   "before_revision": snapshot["data"]["card_revision"], "after_revision": None,
                   "proposal_hash": canonical_hash(payload), "review_run_id": reviewer_id,
                   "review_policy": "independent_required" if require_review else "not_required_by_host",
                   "review_required": bool(require_review and reviewer_id is None),
                   "policy_hash": record["policy_hash"]}
        if not apply:
            blockers = []
            if require_review and reviewer_id is None:
                blockers.append("independent_review_required")
            try:
                _validate_card_inputs(snapshot["data"], parsed, now, apply=True)
            except SubmitError as exc:
                blockers.append(str(exc))
            receipt["can_apply"] = not blockers
            receipt["blocked_reasons"] = blockers
            receipt["checks_pending"] = ["live_revision", "candidate_schema_and_policy"]
            return receipt
        writer = _load_writer()
        captured = io.StringIO()
        prepared = None

        def validate_commit_time():
            # Runs under the writer's RMW lock, immediately before persistence.
            # A lock wait may cross the evidence/review TTL or a market close.
            commit_now = _now()
            try:
                validate_snapshot(snapshot, now=commit_now)
                validate_result("technical", payload, snapshot=snapshot, now=commit_now)
                _validate_review(record, payload, snapshot, commit_now)
                _validate_card_inputs(snapshot["data"], parsed, commit_now, apply=True)
            except ContractError as exc:
                raise SubmitError("precommit evidence rejected: " + str(exc)) from exc

        def precommit_check(writer_receipt):
            nonlocal prepared
            validate_commit_time()
            if (not isinstance(writer_receipt, dict) or writer_receipt.get("ticker") != ticker
                    or writer_receipt.get("bucket") != bucket
                    or writer_receipt.get("before_revision") != receipt["before_revision"]):
                raise SubmitError("writer precommit identity/revision mismatch")
            _digest(writer_receipt.get("after_revision"), "writer after_revision")
            prepared = {"run_id": record["run_id"], "ticker": ticker, "bucket": snapshot["data"]["bucket"],
                        "before_revision": writer_receipt["before_revision"],
                        "after_revision": writer_receipt["after_revision"],
                        "proposal_hash": receipt["proposal_hash"], "policy_hash": receipt["policy_hash"],
                        "review_run_id": reviewer_id, "review_policy": receipt["review_policy"]}
            if on_precommit is not None:
                try:
                    on_precommit(deepcopy(prepared))
                except Exception as exc:
                    raise SubmitError("precommit audit could not be persisted: " + str(exc)) from exc
                # Audit fsync can itself block; it cannot extend the TTL.
                validate_commit_time()

        with redirect_stdout(captured):
            _check_candidate(writer, argv, snapshot["data"])
            writer_invoked = True
            rc = writer.main(argv, precommit_check=precommit_check)
        if rc != 0:
            raise SubmitError(f"controlled writer returned {rc!r}", side_effect_possible=True)
        if prepared is None:
            raise SubmitError("writer did not provide a final target receipt", side_effect_possible=True)
        receipt.update(status="applied", applied=True, review_required=False,
                       can_apply=True, blocked_reasons=[],
                       after_revision=prepared["after_revision"], prepared=prepared,
                       writer_output=captured.getvalue())
        return receipt
    except SubmitError:
        raise
    except (ContractError, RuntimeError) as exc:
        # Revision conflicts are raised before the writer changes anything.
        # Other failures after main begins need reconciliation, not blind retry.
        possible = writer_invoked and not str(exc).startswith("revision conflict:")
        raise SubmitError(str(exc), side_effect_possible=possible) from exc
    except SystemExit as exc:
        raise SubmitError(f"controlled writer rejected arguments: {exc}",
                          side_effect_possible=writer_invoked) from exc
    except Exception as exc:
        raise SubmitError(f"{type(exc).__name__}: {exc}",
                          side_effect_possible=writer_invoked) from exc


def preview_candidate(record, *, require_review=True) -> dict:
    """Host-side dry run that also exercises the existing writer merge/schema.

    : six of the first scheduled card failures were SubmitErrors
    raised only at apply time (next_regime skeleton fields, binary cap), after
    the research had already succeeded. This preview runs every static check
    and ``_check_candidate`` (writer merge + card schema + gate coherence +
    stop/gate no-relax + protected fields) without writing, so a Desktop
    session can correct its result before the run is accepted. Live revision
    is still verified only inside the writer lock at apply time.
    """
    receipt = apply_proposal(record, apply=False, require_review=require_review)
    blockers = list(receipt.get("blocked_reasons") or [])
    try:
        snapshot = record["snapshot"]
        argv = receipt["writer_args"]
        _check_candidate(_load_writer(), argv, snapshot["data"])
    except SubmitError as exc:
        blockers.append("candidate: " + str(exc))
    except Exception as exc:  # writer import/merge failure is a pre-write rejection
        blockers.append(f"candidate: {type(exc).__name__}: {exc}")
    receipt["blocked_reasons"] = blockers
    receipt["can_apply"] = not blockers
    receipt["checks_pending"] = ["live_revision"]
    receipt["candidate_checked"] = True
    return receipt


def reconcile_submission(prepared: dict, actual_revision: str) -> dict:
    """Classify a host-read current card against persisted prepared hashes.

    Pure comparison only: never reads/writes cards and never retries a proposal.
    A same-before/after hash cannot establish whether a write happened, so it
    requires review. Equality establishes the current target state, not history.
    """
    if not isinstance(prepared, dict):
        raise SubmitError("missing prepared submission receipt")
    for key in ("before_revision", "after_revision"):
        _digest(prepared.get(key), key)
    _digest(actual_revision, "actual_revision")
    before, after = prepared["before_revision"], prepared["after_revision"]
    if before == after:
        status, reason = "needs_review", "before and intended target are identical; write history is indeterminate"
    elif actual_revision == after:
        status, reason = "applied", "current target matches the exact prepared card"
    elif actual_revision == before:
        status, reason = "no_effect", "current target matches the frozen pre-write card"
    else:
        status, reason = "needs_review", "current target matches neither prepared revision"
    return {"status": status, "reason": reason, "actual_revision": actual_revision,
            "before_revision": before, "after_revision": after,
            "retry_authorized": False, "writes_performed": False}
