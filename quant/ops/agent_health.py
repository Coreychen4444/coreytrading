"""Host-owned model quotas, process leases and metadata-only health events.

Nothing here invokes a model, sends a message or reads trading state. Callers
must share one control_dir across foreground, background and scheduled work.
Reserve before invoking a runner and release only after its process group has
stopped. Reservations are charged even if the provider fails before inference.
"""
from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
import math
import os
import re
import time
import uuid

from quant.core.agent_contracts import ROLES, canonical_hash
from quant.core.io_json import atomic_write_json, state_lock
from quant.core.paths import AGENT_RUNS_DIR

MAX_WINDOW_S = 86400
MAX_METADATA_BYTES = 8 * 1024 * 1024
_RUN_ID = re.compile(r"[a-f0-9]{32}")
_SAFE_LABEL = re.compile(r"[A-Za-z0-9_.:-]{1,96}")
_SUCCESS = {"succeeded", "success", "completed", "applied"}
# "complete" is a controller receipt even when its referenced run failed.
_TERMINAL = _SUCCESS | {"complete", "skipped", "no_effect"}
_FAILURES = {"failed", "timeout", "timed_out", "invalid_result", "unavailable"}


class HealthError(ValueError):
    """A stable error code with no raw file contents or provider diagnostics."""


def _time(now=None) -> float:
    value = time.time() if now is None else now
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise HealthError("invalid_time")
    return float(value)


def _stamp(value: float) -> str:
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def _timestamp(value) -> float | None:
    if type(value) in (int, float) and math.isfinite(value) and value >= 0:
        return float(value)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                return parsed.timestamp()
        except (ValueError, OverflowError):
            pass
    return None


def _pid_alive(pid) -> bool:
    if type(pid) is not int or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # lack of signal permission is not proof of death
    except OSError:
        return True  # uncertainty never frees a global execution slot
    return True


def _group_alive(pgid) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return True
    return True


def validate_policy(policy: dict) -> dict:
    bounds = {"max_parallel": (1, 16), "window_s": (1, MAX_WINDOW_S),
              "max_calls": (1, 10000), "lease_grace_s": (0, 3600)}
    if type(policy) is not dict or set(policy) != set(bounds):
        raise HealthError("invalid_global_limits")
    if any(type(policy[key]) is not int or not low <= policy[key] <= high
           for key, (low, high) in bounds.items()):
        raise HealthError("invalid_global_limits")
    return deepcopy(policy)


def _read(path: Path) -> dict:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_METADATA_BYTES:
            raise HealthError("metadata_unreadable")
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        raise HealthError("metadata_unreadable") from None
    if type(value) is not dict:
        raise HealthError("metadata_invalid")
    return value


def _control(path: Path, now: float) -> dict:
    if not path.exists() and not path.is_symlink():
        return {"version": 1, "last_seen_ts": now, "calls": []}
    state = _read(path)
    if set(state) != {"version", "last_seen_ts", "calls"} or state["version"] != 1:
        raise HealthError("control_state_invalid")
    seen = _timestamp(state["last_seen_ts"])
    if seen is None or type(state["calls"]) is not list:
        raise HealthError("control_state_invalid")
    if now < seen:
        raise HealthError("control_clock_regressed")
    known = set()
    required = {"lease_id", "run_id", "role", "provider", "pid", "started_ts",
                "lease_until_ts", "status", "finished_ts", "outcome", "process_groups"}
    for item in state["calls"]:
        if type(item) is not dict or set(item) != required:
            raise HealthError("control_state_invalid")
        if (not isinstance(item["lease_id"], str) or not _RUN_ID.fullmatch(item["lease_id"])
                or not isinstance(item["run_id"], str) or not _RUN_ID.fullmatch(item["run_id"])
                or item["lease_id"] in known or not isinstance(item["role"], str)
                or item["role"] not in ROLES or not isinstance(item["provider"], str)
                or item["provider"] not in {"claude", "codex"}
                or type(item["pid"]) is not int or item["pid"] <= 0
                or not isinstance(item["status"], str)
                or item["status"] not in {"active", "releasing", "released", "orphaned"}
                or type(item["started_ts"]) not in (int, float)
                or type(item["lease_until_ts"]) not in (int, float)
                or _timestamp(item["started_ts"]) is None
                or _timestamp(item["lease_until_ts"]) is None
                or type(item["process_groups"]) is not list
                or len(item["process_groups"]) > 8
                or any(type(pid) is not int or pid <= 0 for pid in item["process_groups"])
                or (item["finished_ts"] is not None and _timestamp(item["finished_ts"]) is None)
                or item["outcome"] not in (None, "success", "succeeded", "failed", "timeout",
                                           "unavailable", "invalid_result", "cancelled", "unknown",
                                           "owner_expired")
                or item["lease_until_ts"] < item["started_ts"]):
            raise HealthError("control_state_invalid")
        known.add(item["lease_id"])
    return state


def _reap(state: dict, now: float) -> None:
    for item in state["calls"]:
        children_alive = any(_group_alive(pid) for pid in item["process_groups"])
        if item["status"] == "releasing" and not children_alive:
            item["status"] = "released"
        if (item["status"] == "active" and item["lease_until_ts"] <= now
                and not _pid_alive(item["pid"]) and not children_alive):
            item.update(status="orphaned", finished_ts=now, outcome="owner_expired")
    # Retain a full day even when the current policy uses a shorter window, so
    # increasing its window cannot silently reset historical charges.
    state["calls"] = [item for item in state["calls"]
                      if item["status"] in {"active", "releasing"} or item["started_ts"] > now - MAX_WINDOW_S]
    state["last_seen_ts"] = now


def acquire_call(control_dir: Path, policy: dict, *, provider: str, role: str,
                 run_id: str, timeout_s: int | float, pid: int | None = None,
                 now=None) -> dict:
    """Atomically charge and acquire; denial and duplicate returns never authorize a call."""
    limits = validate_policy(policy)
    owner = os.getpid() if pid is None else pid
    if (not isinstance(provider, str) or provider not in {"claude", "codex"}
            or not isinstance(role, str) or role not in ROLES
            or not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id)
            or type(owner) is not int or owner <= 0
            or type(timeout_s) not in (int, float) or not math.isfinite(timeout_s)
            or not 0 < timeout_s <= 1800):
        raise HealthError("invalid_call_metadata")
    root = Path(control_dir)
    path = root / "global-calls.json"
    with state_lock(root / ".global-calls.lock"):
        instant = _time(now)
        state = _control(path, instant)
        _reap(state, instant)
        active = [item for item in state["calls"] if item["status"] in {"active", "releasing"}]
        charged = [item for item in state["calls"] if item["started_ts"] > instant - limits["window_s"]]
        duplicate = next((item for item in state["calls"]
                          if item["run_id"] == run_id and item["provider"] == provider), None)
        result = {"status": "acquired", "lease_id": None, "retry_after_s": 0,
                  "active_calls": len(active), "window_calls": len(charged)}
        if duplicate is not None:
            result.update(status="duplicate_call", lease_id=duplicate["lease_id"])
        elif len(active) >= limits["max_parallel"]:
            result.update(status="concurrency_limit", retry_after_s=max(
                1, min(item["lease_until_ts"] for item in active) - instant))
        elif len(charged) >= limits["max_calls"]:
            result.update(status="window_budget", retry_after_s=max(
                1, min(item["started_ts"] for item in charged) + limits["window_s"] - instant))
        else:
            lease_id = uuid.uuid4().hex
            state["calls"].append({"lease_id": lease_id, "run_id": run_id,
                                   "provider": provider, "role": role, "pid": owner,
                                   "started_ts": instant,
                                   "lease_until_ts": instant + timeout_s + limits["lease_grace_s"],
                                   "status": "active", "finished_ts": None, "outcome": None,
                                   "process_groups": []})
            result.update(lease_id=lease_id, active_calls=len(active) + 1,
                          window_calls=len(charged) + 1)
        atomic_write_json(path, state)
        return result


def attach_call_process(control_dir: Path, lease_id: str, process_pid: int, *,
                        pid: int | None = None, now=None) -> None:
    """Record the runner process group before its stdin receives a model prompt."""
    if type(process_pid) is not int or process_pid <= 0:
        raise HealthError("invalid_process_group")
    root = Path(control_dir)
    owner = os.getpid() if pid is None else pid
    with state_lock(root / ".global-calls.lock"):
        instant = _time(now)
        state = _control(root / "global-calls.json", instant)
        item = next((item for item in state["calls"] if item["lease_id"] == lease_id), None)
        if item is None or item["status"] != "active" or item["pid"] != owner:
            raise HealthError("call_lease_not_owned")
        if process_pid not in item["process_groups"]:
            if len(item["process_groups"]) >= 8:
                raise HealthError("call_process_group_limit")
            item["process_groups"].append(process_pid)
        state["last_seen_ts"] = instant
        atomic_write_json(root / "global-calls.json", state)


def release_call(control_dir: Path, lease_id: str, *, status: str,
                 pid: int | None = None, now=None) -> dict:
    """Release concurrency only. Failure, timeout and unavailable calls stay charged."""
    outcomes = {"success", "succeeded", "failed", "timeout", "unavailable",
                "invalid_result", "cancelled", "unknown"}
    if not isinstance(lease_id, str) or not _RUN_ID.fullmatch(lease_id):
        raise HealthError("invalid_release_metadata")
    # An unknown/malformed provider status must not strand the host's lease or
    # copy provider-controlled text into this metadata-only ledger.
    status = status if isinstance(status, str) and status in outcomes else "unknown"
    owner = os.getpid() if pid is None else pid
    root = Path(control_dir)
    with state_lock(root / ".global-calls.lock"):
        instant = _time(now)
        state = _control(root / "global-calls.json", instant)
        item = next((item for item in state["calls"] if item["lease_id"] == lease_id), None)
        if item is None:
            raise HealthError("lease_not_found")
        if item["pid"] != owner:
            raise HealthError("lease_owner_mismatch")
        if item["status"] in {"active", "releasing"}:
            held = any(_group_alive(pid) for pid in item["process_groups"])
            item.update(status="releasing" if held else "released", finished_ts=instant, outcome=status)
        state["last_seen_ts"] = instant
        atomic_write_json(root / "global-calls.json", state)
        return {"lease_id": lease_id, "status": item["status"], "outcome": item["outcome"]}


def _business_state(path: Path, now: float) -> dict:
    if not path.exists() and not path.is_symlink():
        return {"version": 1, "last_seen_ts": now, "jobs": {}}
    state = _read(path)
    if (set(state) != {"version", "last_seen_ts", "jobs"} or state["version"] != 1
            or type(state["jobs"]) is not dict or _timestamp(state["last_seen_ts"]) is None):
        raise HealthError("business_state_invalid")
    if now < _timestamp(state["last_seen_ts"]):
        raise HealthError("control_clock_regressed")
    for key, item in state["jobs"].items():
        if (not re.fullmatch(r"[a-f0-9]{64}", key) or type(item) is not dict
                or set(item) != {"lease_id", "pid", "started_ts", "lease_until_ts", "status"}
                or not isinstance(item["lease_id"], str) or not _RUN_ID.fullmatch(item["lease_id"])
                or type(item["pid"]) is not int or item["pid"] <= 0
                or type(item["lease_until_ts"]) not in (int, float)
                or type(item["started_ts"]) not in (int, float)
                or _timestamp(item["lease_until_ts"]) is None
                or _timestamp(item["started_ts"]) is None
                or item["lease_until_ts"] < item["started_ts"]
                or not isinstance(item["status"], str)
                or item["status"] not in {"active", "released"}):
            raise HealthError("business_state_invalid")
    return state


def acquire_job(control_dir: Path, key: str, *, timeout_s: int | float,
                pid: int | None = None, now=None) -> dict:
    """Fence a host business key independently of snapshots, prompts and call quotas."""
    owner = os.getpid() if pid is None else pid
    if (not isinstance(key, str) or not key or len(key) > 512 or "\x00" in key
            or type(owner) is not int or owner <= 0
            or type(timeout_s) not in (int, float) or not math.isfinite(timeout_s)
            or not 0 < timeout_s <= MAX_WINDOW_S):
        raise HealthError("invalid_job_metadata")
    root, digest = Path(control_dir), canonical_hash(key)
    with state_lock(root / ".business-leases.lock"):
        instant = _time(now)
        path = root / "business-leases.json"
        state = _business_state(path, instant)
        previous = state["jobs"].get(digest)
        if previous and previous["status"] == "active" and (
                previous["lease_until_ts"] > instant or _pid_alive(previous["pid"])):
            state["last_seen_ts"] = instant
            atomic_write_json(path, state)
            return {"status": "busy", "lease_id": None,
                    "lease_until_ts": previous["lease_until_ts"],
                    "retry_after_s": max(1, previous["lease_until_ts"] - instant)}
        lease_id = uuid.uuid4().hex
        state["jobs"][digest] = {"lease_id": lease_id, "pid": owner,
                                 "started_ts": instant, "lease_until_ts": instant + timeout_s,
                                 "status": "active"}
        state["last_seen_ts"] = instant
        atomic_write_json(path, state)
        return {"status": "acquired", "lease_id": lease_id, "retry_after_s": 0,
                "lease_until_ts": instant + timeout_s}


def owns_job(control_dir: Path, lease_id: str, *, pid: int | None = None, now=None) -> bool:
    owner = os.getpid() if pid is None else pid
    root = Path(control_dir)
    with state_lock(root / ".business-leases.lock"):
        instant = _time(now)
        state = _business_state(root / "business-leases.json", instant)
        return any(item["lease_id"] == lease_id and item["pid"] == owner
                   and item["status"] == "active"
                   and (item["lease_until_ts"] > instant or _pid_alive(owner))
                   for item in state["jobs"].values())


def release_job(control_dir: Path, lease_id: str, *, pid: int | None = None, now=None) -> dict:
    owner = os.getpid() if pid is None else pid
    root = Path(control_dir)
    with state_lock(root / ".business-leases.lock"):
        instant = _time(now)
        path = root / "business-leases.json"
        state = _business_state(path, instant)
        item = next((item for item in state["jobs"].values() if item["lease_id"] == lease_id), None)
        if item is None or item["pid"] != owner:
            raise HealthError("job_lease_not_owned")
        item["status"] = "released"
        state["last_seen_ts"] = instant
        atomic_write_json(path, state)
        return {"status": "released", "lease_id": lease_id}


def _label(value) -> str | None:
    return value if isinstance(value, str) and _SAFE_LABEL.fullmatch(value) else None


def _metadata(doc: dict, source: str, identity: str) -> dict:
    out = {"source": source, "source_id": f"{source}:{identity}"}
    for key in ("run_id", "job_id", "role", "provider", "stage", "ticker", "bucket", "session"):
        value = _label(doc.get(key))
        if value is not None:
            out[key] = value
    kind = _label(doc.get("job_kind", doc.get("kind")))
    if kind is not None:
        out["job_kind"] = kind
    return out


def _issue(meta: dict, code: str, *, severity="error", age_s=None) -> dict:
    out = {**meta, "code": code, "severity": severity,
           "issue_id": canonical_hash({"source_id": meta["source_id"], "code": code})}
    if age_s is not None:
        out["age_s"] = max(0, round(age_s, 3))
    return out


def scan_health(run_dir: Path, *, now=None, stale_after_s=1800,
                expected_roles=(), expected_jobs: dict | None = None) -> dict:
    """Read metadata only; absent roles are quiet unless the host declares cadence.

    Reads runtime runs/jobs plus task-jobs written by the background controller.
    Never returns prompts, results, snapshots, submission reasons or CLI errors.
    """
    instant = _time(now)
    if type(stale_after_s) not in (int, float) or not 0 < stale_after_s <= MAX_WINDOW_S:
        raise HealthError("invalid_health_policy")
    if not isinstance(expected_roles, (tuple, list)) or any(
            not isinstance(role, str) or role not in ROLES for role in expected_roles):
        raise HealthError("invalid_expected_roles")
    expected_jobs = expected_jobs or {}
    if type(expected_jobs) is not dict:
        raise HealthError("invalid_expected_jobs")
    for kind, options in expected_jobs.items():
        age = options.get("max_age_s") if isinstance(options, dict) else options
        if _label(kind) is None or type(age) not in (int, float) or not 0 < age <= MAX_WINDOW_S * 31:
            raise HealthError("invalid_expected_jobs")
    root, issues, observed, success_times = Path(run_dir), [], {}, {}
    counts = {"runs": 0, "jobs": 0, "task_jobs": 0, "active": 0, "succeeded": 0,
              "failed": 0, "needs_review": 0, "uncertain_submission": 0}
    for directory, source in (("runs", "run"), ("jobs", "job"), ("task-jobs", "task_job")):
        folder = root / directory
        if not folder.exists():
            continue
        for path in sorted(folder.glob("*.json")):
            identity = _label(path.stem) or canonical_hash(path.name)
            meta = _metadata({}, source, identity)
            counts[{"run": "runs", "job": "jobs", "task_job": "task_jobs"}[source]] += 1
            try:
                doc = _read(path)
            except HealthError:
                issues.append(_issue(meta, "metadata_unreadable"))
                observed[meta["source_id"]] = "invalid"
                continue
            meta = _metadata(doc, source, identity)
            status = doc.get("status") if isinstance(doc.get("status"), str) else "invalid"
            observed[meta["source_id"]] = status if isinstance(status, str) else "invalid"
            started = _timestamp(doc.get("started_at_ts", doc.get("started_at")))
            finished = _timestamp(doc.get("finished_at_ts", doc.get("finished_at")))
            lease = _timestamp(doc.get("lease_until_ts", doc.get("lease_until")))
            deadline = lease if lease is not None else (started + stale_after_s if started is not None else None)
            age = instant - started if started is not None else None
            submission = doc.get("submission") or {}
            if isinstance(submission, dict) and (submission.get("status") in ("applying", "uncertain")
                                                or submission.get("outcome_uncertain") is True):
                counts["uncertain_submission"] += 1
                issues.append(_issue(meta, "submission_uncertain", age_s=age))
            if isinstance(submission, dict) and submission.get("status") in ("needs_review", "rejected"):
                issues.append(_issue(meta, "submission_" + submission["status"],
                                     severity="warning", age_s=age))
            if status in _TERMINAL:
                counts["succeeded"] += int(status in _SUCCESS)
                if status in _SUCCESS and finished is not None and finished <= instant:
                    for kind in (meta.get("role"), meta.get("job_kind")):
                        if kind:
                            success_times[kind] = max(finished, success_times.get(kind, 0))
                if source == "job" and doc.get("run_id"):
                    run_id = doc["run_id"]
                    if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id) or not (root / "runs" / f"{run_id}.json").is_file():
                        issues.append(_issue(meta, "completed_job_result_missing"))
            elif status in _FAILURES:
                counts["failed"] += 1
                attempts = doc.get("attempts") if type(doc.get("attempts")) is list else []
                timed_out = status in {"timeout", "timed_out"} or any(
                    isinstance(a, dict) and a.get("status") == "timeout" for a in attempts)
                issues.append(_issue(meta, "run_timeout" if timed_out else "run_failed", age_s=age))
            elif status == "needs_review":
                counts["needs_review"] += 1
                issues.append(_issue(meta, "needs_review", severity="warning", age_s=age))
            elif status in {"running", "starting", "queued", "applying"}:
                counts["active"] += 1
                if deadline is None or deadline <= instant:
                    alive = _pid_alive(doc.get("pid", doc.get("owner_pid")))
                    code = "unfinished_owner_alive" if alive else "unfinished_owner_missing"
                    issues.append(_issue(meta, code, age_s=age))
            elif status in {"partial", "busy", "superseded"}:
                issues.append(_issue(meta, "job_" + status, severity="warning", age_s=age))
            elif status == "expired":
                issues.append(_issue(meta, "job_unfinished", age_s=age))
            else:
                issues.append(_issue(meta, "metadata_status_invalid"))
    expectations = {role: stale_after_s for role in expected_roles}
    expectations.update({kind: options.get("max_age_s") if isinstance(options, dict) else options
                         for kind, options in expected_jobs.items()})
    for kind, allowed_age in expectations.items():
        meta = {"source": "cadence", "source_id": f"cadence:{kind}", "job_kind": kind}
        stamp = success_times.get(kind)
        if stamp is None or instant - stamp > allowed_age:
            observed[meta["source_id"]] = "overdue"
            issues.append(_issue(meta, "expected_completion_missing", age_s=instant - stamp if stamp is not None else None))
        else:
            observed[meta["source_id"]] = "succeeded"
    return {"version": 1, "checked_at": _stamp(instant), "counts": counts,
            "healthy": not issues, "issues": sorted(issues, key=lambda item: item["issue_id"]),
            "observed": observed}


def _health_state(path: Path) -> dict:
    if not path.exists() and not path.is_symlink():
        return {"version": 1, "sequence": 0, "issues": {}, "outbox": []}
    state = _read(path)
    if (set(state) != {"version", "sequence", "issues", "outbox"} or state["version"] != 1
            or type(state["sequence"]) is not int or type(state["issues"]) is not dict
            or state["sequence"] < 0 or type(state["outbox"]) is not list):
        raise HealthError("health_state_invalid")
    for identity, item in state["issues"].items():
        if (not re.fullmatch(r"[a-f0-9]{64}", identity) or type(item) is not dict
                or type(item.get("active")) is not bool or type(item.get("issue")) is not dict
                or item["issue"].get("issue_id") != identity
                or not isinstance(item["issue"].get("source_id"), str)):
            raise HealthError("health_state_invalid")
    for event in state["outbox"]:
        if (type(event) is not dict or not isinstance(event.get("event_id"), str)
                or not re.fullmatch(r"[a-f0-9]{64}", event["event_id"])
                or event.get("kind") not in ("opened", "recovered")
                or type(event.get("issue")) is not dict):
            raise HealthError("health_state_invalid")
    return state


def update_health(run_dir: Path, *, state_dir: Path | None = None, now=None,
                  stale_after_s=1800, expected_roles=(), expected_jobs=None) -> dict:
    """Atomically record transitions and a durable outbox; never send messages."""
    root = Path(state_dir or run_dir)
    with state_lock(root / ".health.lock"):
        instant = _time(now)
        summary = scan_health(run_dir, now=instant, stale_after_s=stale_after_s,
                              expected_roles=expected_roles, expected_jobs=expected_jobs)
        path = root / "health.json"
        state = _health_state(path)
        current = {item["issue_id"]: item for item in summary["issues"]}
        new_events = []

        def transition(item, kind):
            state["sequence"] += 1
            event = {"event_id": canonical_hash({"issue_id": item["issue_id"],
                                                   "sequence": state["sequence"], "kind": kind}),
                     "kind": kind, "created_at": _stamp(instant), "issue": deepcopy(item)}
            new_events.append(event)
            state["outbox"].append(event)

        for identity, item in current.items():
            previous = state["issues"].get(identity)
            if previous is None or not previous["active"]:
                transition(item, "opened")
            state["issues"][identity] = {"active": True, "issue": item,
                                           "last_seen_at": _stamp(instant)}
        for identity, previous in state["issues"].items():
            if identity in current or not previous["active"]:
                continue
            item = previous["issue"]
            # Deleted/missing records and a still-running retry are not recovery.
            if summary["observed"].get(item["source_id"]) in _TERMINAL:
                transition(item, "recovered")
                previous.update(active=False, recovered_at=_stamp(instant))
        atomic_write_json(path, state)
        active = [value["issue"] for value in state["issues"].values() if value["active"]]
        return {**summary, "healthy": not active, "active_issues": active,
                "new_events": new_events, "pending_events": deepcopy(state["outbox"])}


def acknowledge_events(state_dir: Path, event_ids: list[str]) -> dict:
    """Host calls this only after successfully consuming the stable event IDs."""
    if type(event_ids) is not list or any(not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value)
                                         for value in event_ids):
        raise HealthError("invalid_event_ids")
    root = Path(state_dir)
    with state_lock(root / ".health.lock"):
        path = root / "health.json"
        state = _health_state(path)
        before = len(state["outbox"])
        wanted = set(event_ids)
        state["outbox"] = [event for event in state["outbox"] if event["event_id"] not in wanted]
        atomic_write_json(path, state)
        return {"acknowledged": before - len(state["outbox"]), "pending": len(state["outbox"])}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, default=AGENT_RUNS_DIR)
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--record-events", action="store_true")
    args = parser.parse_args(argv)
    result = (update_health(args.run_dir, state_dir=args.state_dir) if args.record_events
              else scan_health(args.run_dir))
    result.pop("observed", None)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["healthy"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
