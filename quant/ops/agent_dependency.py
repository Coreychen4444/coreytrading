"""Read-only barrier between scheduled domain jobs and dependent reports."""
from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import argparse
from datetime import date
import json
import math
import re
import time

from quant.core.paths import AGENT_RUNS_DIR
from quant.ops.agent_health import (DOMAIN_JOB_LEASE_S, MAX_WINDOW_S, HealthError, _pid_alive,
                                    _read, _timestamp)

# A fixed private wait budget can starve a downstream job while a healthy
# upstream legitimately runs long. The wait budget is therefore the upstream's own lease
# (DOMAIN_JOB_LEASE_S); a running receipt whose pid is dead or whose lease has
# expired returns at once, and an upstream that never wrote a receipt is only
# waited for NOT_STARTED_GRACE_S (postclose fires 10 min before the ladder and
# took ~5 min to its first receipt; 15 min is 3x that, not a guess at runtime).
NOT_STARTED_GRACE_S = 900
ACTIVE_STATUSES = ("running", "starting", "queued", "completed", "partial")
FAILURE_STATUSES = ("failed", "timeout", "needs_review", "invalid", "expired", "superseded")


def _latest(run_dir: Path, kind: str, session: str) -> dict | None:
    candidates = []
    for path in (Path(run_dir) / "task-jobs").glob("*.json"):
        try:
            doc = _read(path)
        except HealthError:
            # An unreadable latest receipt could conceal an in-flight writer.
            return {"status": "invalid", "reason": "job_metadata_unreadable"}
        if doc.get("job_kind") != kind or doc.get("session") not in (None, session):
            continue
        started = _timestamp(doc.get("started_at"))
        if started is None:
            return {"status": "invalid", "reason": "job_started_at_invalid"}
        meta = {"job_kind": kind, "session": session,
                "session_confirmed": doc.get("session") == session,
                "status": doc.get("status"), "started_at": doc["started_at"],
                "finished_at": _timestamp(doc.get("finished_at")),
                "lease_until": _timestamp(doc.get("lease_until")),
                "pid": doc.get("pid") if type(doc.get("pid")) is int else None}
        if isinstance(doc.get("job_id"), str) and re.fullmatch(r"[a-f0-9]{32}", doc["job_id"]):
            meta["job_id"] = doc["job_id"]
        candidates.append((started, path.name, meta))
    return max(candidates, key=lambda item: item[:2])[2] if candidates else None


def wait_for_job(run_dir: Path, kind: str, session: str, timeout_s=None, *,
                 monotonic=None, sleep=None, wall_time=None, poll_s=5,
                 not_started_grace_s=NOT_STARTED_GRACE_S, pid_alive=None) -> dict:
    """Wait for the latest same-session receipt to leave all card-writing stages.

    Missing receipts keep waiting for ``not_started_grace_s``: the upstream may
    still be capturing evidence. Completed/partial requires finished_at, since a
    research result can be stored before its subsequent reviewed repaint has
    actually finished. ``timeout_s=None`` means the upstream's own business
    lease budget; a receipt whose lease expired or whose pid is dead returns
    immediately instead of burning the budget. Results contain only host
    metadata and never job payloads or diagnostics.
    """
    if not isinstance(kind, str) or not re.fullmatch(r"[a-z][a-z_-]{0,31}", kind):
        raise ValueError("invalid dependency kind")
    try:
        if not isinstance(session, str) or date.fromisoformat(session).isoformat() != session:
            raise ValueError
    except (TypeError, ValueError):
        raise ValueError("invalid dependency session") from None
    if timeout_s is None:
        timeout_s = DOMAIN_JOB_LEASE_S
    for value, upper in ((timeout_s, MAX_WINDOW_S), (not_started_grace_s, MAX_WINDOW_S)):
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= upper:
            raise ValueError("invalid dependency timeout")
    if type(poll_s) not in (int, float) or not math.isfinite(poll_s) or not 0 < poll_s <= 60:
        raise ValueError("invalid dependency timeout")
    clock, pause, wall = monotonic or time.monotonic, sleep or time.sleep, wall_time or time.time
    alive = pid_alive or _pid_alive
    started, latest = clock(), None
    while True:
        latest = _latest(Path(run_dir), kind, session)
        elapsed = max(0, clock() - started)
        out = {"job_kind": kind, "session": session, "elapsed_s": round(elapsed, 3),
               "budget_s": timeout_s}
        if latest:
            status = latest["status"]
            if "job_id" in latest:
                out["job_id"] = latest["job_id"]
            if (status in ("completed", "partial") and latest.get("finished_at") is not None
                    and latest.get("session_confirmed")):
                return {**out, "status": "settled", "upstream_status": status}
            if status in FAILURE_STATUSES:
                return {**out, "status": "failure", "reason": "upstream_" + status}
            if status not in ACTIVE_STATUSES:
                return {**out, "status": "failure", "reason": "upstream_status_invalid"}
            lease = latest.get("lease_until")
            if lease is not None and lease <= wall():
                return {**out, "status": "timeout", "reason": "upstream_lease_expired"}
            if latest.get("finished_at") is None and latest.get("pid") is not None \
                    and not alive(latest["pid"]):
                # The writer died without closing its receipt; the lease would
                # otherwise hide the corpse for hours.
                return {**out, "status": "failure", "reason": "upstream_process_dead"}
            limit = timeout_s
        else:
            limit = min(timeout_s, not_started_grace_s)
        if elapsed >= limit:
            return {**out, "status": "timeout",
                    "reason": "upstream_unfinished" if latest else "upstream_not_started"}
        pause(min(poll_s, limit - elapsed))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, default=AGENT_RUNS_DIR)
    parser.add_argument("--kind", required=True)
    parser.add_argument("--session", required=True)
    parser.add_argument("--timeout-s", type=float, default=None,
                        help="wait budget; default = upstream business lease (%d s)" % DOMAIN_JOB_LEASE_S)
    args = parser.parse_args(argv)
    result = wait_for_job(args.run_dir, args.kind, args.session, timeout_s=args.timeout_s)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "settled" else 1


if __name__ == "__main__":
    raise SystemExit(main())
