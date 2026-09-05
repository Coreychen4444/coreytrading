#!/usr/bin/env python3
"""Validate and publish the optional intelligence workspace's macro state without trading authority.

Trading code only uses `validate_document` (read-only); the publish paths belong to the
external intelligence workflow that owns `ai_news/` and are inert when that workspace is absent.

The JSON Schema is the structural source of truth.  A small stdlib validator
implements the schema features used by macro-state.schema.json so the writer
works under both system Python and the Moomoo venv.  Semantic checks then
enforce evidence references, scenario probability envelopes and immutable
event plans.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from quant.core.io_json import atomic_write_json, read_json, state_lock  # noqa: E402
from quant.core.paths import (  # noqa: E402
    MACRO_CURRENT_PATH,
    MACRO_DIR,
    MACRO_EVENTS_DIR,
    MACRO_HISTORY_DIR,
    MACRO_LOCK_PATH,
    MACRO_SCHEMA_PATH,
)

SCHEMA_VERSION = "0.1.0"
MATERIAL_KEYS = (
    "risk_environment", "state_vector", "horizons", "market_pricing",
    "next_event", "signals",
)
EVIDENCE_REF_KEYS = {"evidence_ids", "counterevidence_ids", "contradiction_ids"}


class MacroStateError(ValueError):
    pass


def _type_ok(value: Any, expected: str) -> bool:
    if expected == "null":
        return value is None
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return True


def _resolve_ref(root: dict, ref: str) -> dict:
    if not ref.startswith("#/"):
        raise MacroStateError(f"unsupported external schema ref: {ref}")
    node: Any = root
    for part in ref[2:].split("/"):
        node = node[part.replace("~1", "/").replace("~0", "~")]
    return node


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z")
                                    else value)
    if "T" not in value or parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include time and UTC offset")
    return parsed


def _valid_datetime(value: str) -> bool:
    try:
        _parse_datetime(value)
        return True
    except ValueError:
        return False


def _schema_errors(value: Any, rule: dict, root: dict, path: str = "$") -> list[str]:
    if "$ref" in rule:
        return _schema_errors(value, _resolve_ref(root, rule["$ref"]), root, path)

    if "oneOf" in rule:
        matches = [candidate for candidate in rule["oneOf"]
                   if not _schema_errors(value, candidate, root, path)]
        return [] if len(matches) == 1 else [f"{path}: must match exactly one schema"]

    errors: list[str] = []
    if "const" in rule and (value != rule["const"] or
                            isinstance(value, bool) != isinstance(rule["const"], bool)):
        errors.append(f"{path}: must equal {rule['const']!r}")
    if "enum" in rule and value not in rule["enum"]:
        errors.append(f"{path}: {value!r} not in enum")

    expected = rule.get("type")
    if expected is not None:
        types = expected if isinstance(expected, list) else [expected]
        if not any(_type_ok(value, item) for item in types):
            return errors + [f"{path}: expected {'|'.join(types)}, "
                             f"got {type(value).__name__}"]

    if isinstance(value, dict):
        required = rule.get("required") or []
        for key in required:
            if key not in value:
                errors.append(f"{path}.{key}: required")
        properties = rule.get("properties") or {}
        if rule.get("additionalProperties") is False:
            for key in value:
                if key not in properties:
                    errors.append(f"{path}.{key}: additional property not allowed")
        for key, child in properties.items():
            if key in value:
                errors.extend(_schema_errors(value[key], child, root,
                                             f"{path}.{key}"))

    if isinstance(value, list):
        if len(value) < rule.get("minItems", 0):
            errors.append(f"{path}: fewer than {rule['minItems']} items")
        if "maxItems" in rule and len(value) > rule["maxItems"]:
            errors.append(f"{path}: more than {rule['maxItems']} items")
        if "items" in rule:
            for index, item in enumerate(value):
                errors.extend(_schema_errors(item, rule["items"], root,
                                             f"{path}[{index}]"))

    if isinstance(value, str):
        if len(value) < rule.get("minLength", 0):
            errors.append(f"{path}: string too short")
        if "maxLength" in rule and len(value) > rule["maxLength"]:
            errors.append(f"{path}: string too long")
        if "pattern" in rule and re.search(rule["pattern"], value) is None:
            errors.append(f"{path}: does not match {rule['pattern']}")
        if rule.get("format") == "date-time" and not _valid_datetime(value):
            errors.append(f"{path}: invalid date-time")
        if rule.get("format") == "uri":
            try:
                parsed = urlsplit(value)
                valid = bool(parsed.scheme) and not (
                    parsed.scheme in {"http", "https"} and not parsed.netloc)
            except ValueError:
                valid = False
            if not valid:
                errors.append(f"{path}: invalid URI")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and not math.isfinite(value):
            return errors + [f"{path}: number must be finite"]
        if "minimum" in rule and value < rule["minimum"]:
            errors.append(f"{path}: below minimum {rule['minimum']}")
        if "maximum" in rule and value > rule["maximum"]:
            errors.append(f"{path}: above maximum {rule['maximum']}")
    return errors


def _walk_evidence_refs(value: Any, path: str = "$"):
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if key in EVIDENCE_REF_KEYS and isinstance(child, list):
                for item in child:
                    yield child_path, item
            yield from _walk_evidence_refs(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_evidence_refs(child, f"{path}[{index}]")


def _semantic_errors(doc: dict) -> list[str]:
    errors: list[str] = []
    evidence = doc.get("evidence") or []
    evidence_ids = [item.get("id") for item in evidence if isinstance(item, dict)]
    evidence_by_id = {
        item.get("id"): item for item in evidence
        if isinstance(item, dict) and item.get("id")
    }
    if len(evidence_ids) != len(set(evidence_ids)):
        errors.append("$.evidence: duplicate evidence id")
    known = set(evidence_ids)
    for path, ref in _walk_evidence_refs(doc):
        if ref not in known:
            errors.append(f"{path}: unknown evidence id {ref!r}")

    # A confirmed phase is a cross-asset state, not a one-print label.
    # Collapse correlated categories before counting independent buckets.
    category_bucket = {
        "economic_data": "economy",
        "policy": "policy_liquidity",
        "liquidity": "policy_liquidity",
        "rates": "rates",
        "credit": "credit",
        "volatility": "volatility",
        "fx": "fx_commodity",
        "commodity": "fx_commodity",
        "equity": "equity",
        "geopolitics": "geopolitics",
    }
    for name, horizon in (doc.get("horizons") or {}).items():
        if horizon.get("directional_prior") == "unknown" and horizon.get("confidence") != "low":
            errors.append(f"$.horizons.{name}.confidence: unknown directional prior requires low")
        if (horizon or {}).get("transition_stage") != "confirmed_phase":
            continue
        buckets = {
            category_bucket.get(evidence_by_id.get(ref, {}).get("category"))
            for ref in horizon.get("evidence_ids") or []
        } - {None}
        if len(buckets) < 2:
            errors.append(f"$.horizons.{name}: confirmed_phase requires "
                          "two independent evidence buckets")

    risk = doc.get("risk_environment") or {}
    if risk.get("level") in {"mixed", "unknown"} and risk.get("score") is not None:
        errors.append("$.risk_environment.score: mixed/unknown requires null")
    if risk.get("level") not in {"mixed", "unknown"} and risk.get("score") is None:
        errors.append("$.risk_environment.score: a directional risk level requires a score")
    if risk.get("level") == "unknown" and risk.get("confidence") != "low":
        errors.append("$.risk_environment.confidence: unknown requires low")
    if risk.get("level") == "crisis":
        categories = {
            evidence_by_id.get(ref, {}).get("category")
            for ref in risk.get("evidence_ids") or []
        }
        if not categories.intersection({"credit", "liquidity", "volatility"}):
            errors.append("$.risk_environment: crisis requires credit, "
                          "liquidity or volatility stress evidence")

    event = doc.get("next_event")
    if isinstance(event, dict):
        metric_names = [metric["name"] for metric in event["metrics"]]
        if len(metric_names) != len(set(metric_names)):
            errors.append("$.next_event.metrics: duplicate metric name")
        scenarios = event.get("scenarios") or []
        scenario_ids = [x.get("id") for x in scenarios if isinstance(x, dict)]
        if len(scenario_ids) != len(set(scenario_ids)):
            errors.append("$.next_event.scenarios: duplicate scenario id")
        mins, maxes = [], []
        missing_probabilities = 0
        for index, scenario in enumerate(scenarios):
            if scenario["probability_range"] is None:
                missing_probabilities += 1
                if not (scenario.get("probability_basis") or "").strip():
                    errors.append(f"$.next_event.scenarios[{index}].probability_basis: "
                                  "required when probability is unavailable")
            pr = (scenario or {}).get("probability_range") or {}
            lo, hi = pr.get("min_pct"), pr.get("max_pct")
            if isinstance(lo, (int, float)) and isinstance(hi, (int, float)):
                if lo > hi:
                    errors.append(
                        f"$.next_event.scenarios[{index}].probability_range: min > max")
                mins.append(lo)
                maxes.append(hi)
        if missing_probabilities and missing_probabilities != len(scenarios):
            errors.append("$.next_event.scenarios: probabilities must be all ranges or all null")
        if mins and (sum(mins) > 100 or sum(maxes) < 100):
            errors.append("$.next_event.scenarios: probability ranges must "
                          "satisfy sum(min)<=100<=sum(max)")
        selected = event.get("selected_scenario_id")
        if selected is not None and selected not in scenario_ids:
            errors.append("$.next_event.selected_scenario_id: unknown scenario")
        match = event.get("scenario_match")
        if match in {"outside_tree", "pending"} and selected is not None:
            errors.append("$.next_event.selected_scenario_id: outside_tree/pending requires null")
        if match == "matched" and selected is None:
            errors.append("$.next_event.selected_scenario_id: matched requires a scenario")

    signals = doc.get("signals") or {}
    expected_status = {
        "confirmed": {"confirmed"},
        "pending": {"pending"},
        "contradicted": {"contradicted", "expired"},
    }
    for bucket, allowed in expected_status.items():
        for index, signal in enumerate(signals.get(bucket) or []):
            if signal.get("status") not in allowed:
                errors.append(f"$.signals.{bucket}[{index}].status: "
                              f"must be one of {sorted(allowed)}")

    degraded = (doc.get("data_quality") or {}).get("degraded")
    missing = (doc.get("data_quality") or {}).get("missing_critical") or []
    if degraded or missing:
        high_paths = []
        if (doc.get("risk_environment") or {}).get("confidence") == "high":
            high_paths.append("$.risk_environment.confidence")
        for name, horizon in (doc.get("horizons") or {}).items():
            if (horizon or {}).get("confidence") == "high":
                high_paths.append(f"$.horizons.{name}.confidence")
        errors.extend(f"{path}: high forbidden while critical data are degraded"
                      for path in high_paths)

    run_kind = doc.get("run_kind")
    if run_kind == "pre_event":
        if not isinstance(event, dict) or event.get("status") != "upcoming":
            errors.append("$.next_event: pre_event run requires upcoming event")
        elif any(metric["actual"] is not None for metric in event["metrics"]):
            errors.append("$.next_event.metrics: pre_event cannot contain actual values")
        if isinstance(event, dict) and (event.get("selected_scenario_id") is not None
                                       or event.get("scenario_match") in {"matched", "outside_tree"}):
            errors.append("$.next_event: pre_event cannot select or classify the realized outcome")
    if run_kind == "post_event":
        if not isinstance(event, dict):
            errors.append("$.next_event: post_event run requires event")
        else:
            if not event.get("pre_event_run_id"):
                errors.append("$.next_event.pre_event_run_id: required post-event")
            can_abstain = (event.get("scenario_match") == "outside_tree" or
                           event.get("scenario_match") == "pending" and event.get("status") in
                           {"released", "awaiting_market_confirmation"})
            if not event.get("selected_scenario_id") and not can_abstain:
                errors.append("$.next_event.selected_scenario_id: required post-event")
            if event.get("status") == "upcoming":
                errors.append("$.next_event.status: post-event cannot be upcoming")
    return errors


def _temporal_errors(doc: dict) -> list[str]:
    """Check information availability; a future event schedule is not evidence."""
    errors = []
    generated = {name: _parse_datetime(value)
                 for name, value in doc["generated_at"].items()}
    published = generated["utc"]
    if any(value != published for value in generated.values()):
        errors.append("$.generated_at: utc, sgt and et must identify the same instant")
    offsets = {"utc": timedelta(0), "sgt": timedelta(hours=8),
               "et": published.astimezone(ZoneInfo("America/New_York")).utcoffset()}
    for name, value in generated.items():
        if value.utcoffset() != offsets[name]:
            errors.append(f"$.generated_at.{name}: incorrect timezone offset")
    cutoff = _parse_datetime(doc["data_quality"]["cutoff_utc"])
    if cutoff > published:
        errors.append("$.data_quality.cutoff_utc: cannot follow generated_at")

    def walk(value: Any, path: str = "$") -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                child_path = f"{path}.{key}"
                if key in {"as_of", "last_observed_at"} and _parse_datetime(child) > cutoff:
                    errors.append(f"{child_path}: observation cannot follow data cutoff")
                walk(child, child_path)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, f"{path}[{index}]")

    walk(doc)
    event = doc["next_event"]
    if isinstance(event, dict):
        released = _parse_datetime(event["scheduled_at_et"])
        if doc["run_kind"] == "pre_event" and published >= released:
            errors.append("$.generated_at: pre_event must precede the scheduled release")
        if doc["run_kind"] == "post_event" and (published < released or cutoff < released):
            errors.append("$.generated_at: post_event and its cutoff must follow the scheduled release")
    return errors


def validate_document(doc: Any, schema_path: Path = MACRO_SCHEMA_PATH) -> list[str]:
    if not isinstance(doc, dict):
        return ["$: macro state must be an object"]
    schema = read_json(schema_path)
    errors = _schema_errors(doc, schema, schema)
    # Semantic checks may only dereference fields after their shapes are proven.
    if errors:
        return errors
    return _semantic_errors(doc) + _temporal_errors(doc)


def state_hash(doc: dict) -> str:
    material = {key: doc.get(key) for key in MATERIAL_KEYS}
    encoded = json.dumps(material, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _history_path(doc: dict, history_dir: Path) -> Path:
    generated = doc["generated_at"]["utc"]
    month = generated[:7]
    if not re.fullmatch(r"\d{4}-\d{2}", month):
        raise MacroStateError("generated_at.utc does not provide YYYY-MM")
    return history_dir / f"{month}.jsonl"


def _find_run(run_id: str, history_dir: Path) -> dict | None:
    if not history_dir.exists():
        return None
    for path in sorted(history_dir.glob("*.jsonl")):
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(item, dict) and item.get("run_id") == run_id:
                    return item
    return None


def _append_fsync(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(doc, ensure_ascii=False, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _event_target(doc: dict, events_dir: Path) -> tuple[Path, str] | None:
    event = doc.get("next_event")
    if not isinstance(event, dict) or event.get("tier") != "tier1":
        return None
    safe_id = event["event_id"]
    if doc["run_kind"] == "pre_event":
        return events_dir / f"{safe_id}-plan.json", "plan"
    if doc["run_kind"] == "post_event":
        return events_dir / f"{safe_id}-result.json", "result"
    return None


def _check_event_link(doc: dict, events_dir: Path,
                      schema_path: Path = MACRO_SCHEMA_PATH) -> None:
    event = doc.get("next_event")
    if doc.get("run_kind") != "post_event" or not isinstance(event, dict):
        return
    plan_path = events_dir / f"{event['event_id']}-plan.json"
    if not plan_path.exists():
        raise MacroStateError(f"post-event plan missing: {plan_path}")
    plan = read_json(plan_path)
    errors = validate_document(plan, schema_path)
    if errors or plan.get("run_kind") != "pre_event":
        raise MacroStateError("post-event plan invalid: " + "; ".join(errors[:5]))
    if event.get("pre_event_run_id") != plan.get("run_id"):
        raise MacroStateError("post-event pre_event_run_id does not match immutable plan")
    before = plan["next_event"]
    for key in ("event_id", "name", "tier", "scheduled_at_et", "scenarios",
                "priced_policy_path_before"):
        if event[key] != before[key]:
            raise MacroStateError(f"post-event {key} differs from immutable plan")
    baseline_keys = ("name", "prior", "consensus", "unit", "consensus_status")
    def baselines(metrics: list[dict]) -> dict:
        return {metric["name"]: {key: metric[key] for key in baseline_keys}
                for metric in metrics}
    if baselines(event["metrics"]) != baselines(before["metrics"]):
        raise MacroStateError("post-event metric baselines differ from immutable plan")


def _same_payload(left: dict, right: dict) -> bool:
    """Writer-owned linkage/hash may change on retries; evidence may not."""
    ignored = {"previous_run_id", "state_hash"}
    return ({key: value for key, value in left.items() if key not in ignored}
            == {key: value for key, value in right.items() if key not in ignored})


def commit_state(
    candidate: dict,
    *,
    macro_dir: Path = MACRO_DIR,
    schema_path: Path = MACRO_SCHEMA_PATH,
    lock_path: Path = MACRO_LOCK_PATH,
    now: datetime | None = None,
) -> dict:
    """Validate, append history and atomically replace current.

    Event plan/result files are write-once. An identical payload retry is
    idempotent, including recovery after history append but before publication.
    """
    initial_errors = validate_document(candidate, schema_path)
    if initial_errors:
        raise MacroStateError("; ".join(initial_errors[:20]))
    if candidate.get("example_only") is True:
        raise MacroStateError("example_only state cannot be committed")
    published_at = now or datetime.now(timezone.utc)
    if published_at.tzinfo is None or published_at.utcoffset() is None:
        raise MacroStateError("publication clock must carry a UTC offset")
    if _parse_datetime(candidate["generated_at"]["utc"]) > published_at:
        raise MacroStateError("generated_at cannot be in the future of the publication clock")

    current_path = macro_dir / "current.json"
    history_dir = macro_dir / "history"
    events_dir = macro_dir / "events"
    with state_lock(lock_path):
        doc = copy.deepcopy(candidate)
        current = read_json(current_path) if current_path.exists() else None
        if current is not None and not isinstance(current, dict):
            raise MacroStateError("current macro state must be an object")
        prior_id = current.get("run_id") if isinstance(current, dict) else None
        try:
            current_at = _parse_datetime(current["generated_at"]["utc"]) if current else None
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise MacroStateError("current macro state has invalid generated_at") from exc
        generated_at = _parse_datetime(doc["generated_at"]["utc"])
        doc["state_hash"] = state_hash(doc)
        duplicate = _find_run(doc["run_id"], history_dir)
        if duplicate is not None:
            if not _same_payload(duplicate, doc):
                raise MacroStateError(f"run_id already exists with different payload: {doc['run_id']}")
            recovered = False
            if prior_id == doc["run_id"]:
                if not _same_payload(current, duplicate):
                    raise MacroStateError("current payload disagrees with committed history")
            elif current_at is None or current_at <= generated_at:
                if prior_id != duplicate.get("previous_run_id"):
                    raise MacroStateError("cannot recover committed run over a different current branch")
                # A history entry only proves the append completed. Repair the
                # missing publication, without re-appending or overwriting a
                # newer current state when an old run is retried.
                atomic_write_json(current_path, duplicate)
                recovered = True
            return {"ok": True, "already_committed": True,
                    "recovered_current": recovered, "run_id": doc["run_id"],
                    "state_hash": duplicate["state_hash"], "current": str(current_path)}

        if current_at is not None and generated_at < current_at:
            raise MacroStateError("generated_at cannot move current macro state backwards")
        if doc["run_kind"] == "pre_event" and published_at >= _parse_datetime(
                doc["next_event"]["scheduled_at_et"]):
            raise MacroStateError("pre_event cannot first be published after the scheduled release")
        supplied_prior = doc.get("previous_run_id")
        if supplied_prior not in (None, prior_id):
            raise MacroStateError(
                f"previous_run_id mismatch: expected {prior_id!r}, "
                f"got {supplied_prior!r}")
        doc["previous_run_id"] = prior_id

        final_errors = validate_document(doc, schema_path)
        if final_errors:
            raise MacroStateError("; ".join(final_errors[:20]))

        _check_event_link(doc, events_dir, schema_path)
        event_target = _event_target(doc, events_dir)
        if event_target:
            path, kind = event_target
            if path.exists():
                old = read_json(path)
                if old.get("run_id") != doc["run_id"] or not _same_payload(old, doc):
                    raise MacroStateError(f"immutable event {kind} already exists: {path}")
            else:
                atomic_write_json(path, doc)

        history_path = _history_path(doc, history_dir)
        _append_fsync(history_path, doc)
        atomic_write_json(current_path, doc)
        return {
            "ok": True,
            "already_committed": False,
            "run_id": doc["run_id"],
            "state_hash": doc["state_hash"],
            "current": str(current_path),
            "history": str(history_path),
            "event_file": str(event_target[0]) if event_target else None,
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate/publish macro state")
    parser.add_argument("command", choices=("validate", "commit"))
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        candidate = read_json(args.candidate)
        if args.command == "validate":
            errors = validate_document(candidate)
            result = {"ok": not errors, "errors": errors}
        else:
            result = commit_state(candidate)
    except (OSError, json.JSONDecodeError, MacroStateError) as exc:
        result = {"ok": False, "errors": [str(exc)]}
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif result["ok"]:
        print(f"macro_state: ok ({result.get('run_id', 'validated')})")
    else:
        for error in result["errors"]:
            print(f"ERROR: {error}", file=sys.stderr)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
