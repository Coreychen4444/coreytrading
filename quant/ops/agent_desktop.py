"""Desktop transport for existing domain runners; no model subprocesses or writes to cards.

The controller queues immutable research packets and waits within evidence TTL.
A native Desktop task claims its provider's packet and returns structured JSON.
Result acceptance is not submission: existing runtime/submit own those receipts.
"""
from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import argparse
from datetime import datetime, timezone
import json
import os
import re
import subprocess
import time
import uuid

from quant.core.agent_contracts import canonical_hash, validate_result, validate_snapshot
from quant.core.io_json import atomic_write_json, read_json, state_lock
from quant.core.paths import AGENT_RUNS_DIR, ROOT

# Per-request wall-clock deadlines (seconds). Without them the queue
# would wait for the whole evidence TTL (up to 3600 s) when the other
# provider's session never showed up. A missed deadline is a failed attempt the
# workflow reports as a missing role; it never extends or reuses evidence.
REQUEST_DEADLINES_S = {"macro": 900, "valuation": 1200, "technical": 1200, "risk": 900}
DEFAULT_DEADLINE_S = 900
# A claimed request whose session died is reclaimable after this lease.
CLAIM_LEASE_S = 1200

COMMANDS = {
    "briefing": ["quant/ops/pipeline.py", "--mode", "briefing", "--json"],
    "postclose": ["quant/ops/pipeline.py", "--mode", "postclose", "--json"],
    "heartbeat": ["quant/ops/pipeline.py", "--mode", "heartbeat", "--json"],
    "weekly": ["quant/ops/agent_jobs.py", "run", "--kind", "weekly", "--scheduled", "--json"],
    "valuation": ["quant/ops/valuation_daily.py", "--run", "--json"],
}


def launch(kind, *, root=AGENT_RUNS_DIR):
    """Detach the existing mechanical controller so its Desktop can answer it."""
    if kind not in COMMANDS:
        raise ValueError("invalid controller kind")
    if Path(root).resolve() != Path(AGENT_RUNS_DIR).resolve():
        raise ValueError("Business controllers require the canonical run directory")
    from quant.ops.agent_runtime import load_routes
    if load_routes().get("execution_transport") != "desktop":
        raise ValueError("Desktop transport is not enabled")
    run_id = uuid.uuid4().hex
    directory = Path(root) / "desktop" / "controllers" / run_id
    directory.mkdir(parents=True, mode=0o700)
    with (directory / "launcher.stderr").open("wb") as err:
        process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()),
                                    "--run-dir", str(root), "controller", "--kind", kind,
                                    "--id", run_id], cwd=ROOT, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=err, start_new_session=True)
    return {"status": "started", "controller_id": run_id, "pid": process.pid,
            "directory": str(directory), "output": str(directory / "output.json"),
            "completion": str(directory / "completion.json")}


def controller(kind, run_id, *, root):
    if kind not in COMMANDS or not re.fullmatch(r"[a-f0-9]{32}", run_id):
        raise ValueError("invalid controller")
    directory = Path(root) / "desktop" / "controllers" / run_id
    completion = {"exit_code": 1, "status": "finished", "kind": kind}
    try:
        with (directory / "output.json").open("wb") as out, (directory / "stderr.log").open("wb") as err:
            process = subprocess.run([sys.executable, *COMMANDS[kind]], cwd=ROOT,
                                     stdin=subprocess.DEVNULL, stdout=out, stderr=err)
        completion["exit_code"] = process.returncode
    except OSError as exc:
        completion["error"] = str(exc)
    atomic_write_json(directory / "completion.json", completion)
    return completion


def _path(root, request_id):
    if not re.fullmatch(r"[a-f0-9]{32}", request_id):
        raise ValueError("invalid request id")
    return Path(root) / "desktop" / "requests" / (request_id + ".json")


def _alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def _validate_request(root, request):
    packet = read_json(Path(root) / "desktop" / "packets" / (request["packet_hash"] + ".json"))
    if canonical_hash(packet) != request["packet_hash"]:
        raise ValueError("Desktop packet changed")
    validate_snapshot(packet["snapshot"], now=datetime.now(timezone.utc))
    if not _alive(request["controller_pid"]):
        raise ValueError("Desktop controller exited")
    return packet


def exchange(provider, prompt, schema, *, root, model=None, **kwargs):
    """Runner-compatible adapter. No CLI timeout; frozen evidence expiry still applies."""
    started = time.monotonic()
    instructions, packet = prompt.split("\n", 1)
    packet = json.loads(packet)
    packet["instructions"] = instructions
    packet["schema"] = schema
    root = Path(root)
    policy = packet.pop("project_rules")
    policy_hash = canonical_hash(policy)
    directory = root / "desktop"
    atomic_write_json(directory / "policies" / (policy_hash + ".json"), policy)
    packet["policy_hash"] = policy_hash
    packet_hash = canonical_hash(packet)
    atomic_write_json(directory / "packets" / (packet_hash + ".json"), packet)
    request_id = uuid.uuid4().hex
    path = _path(root, request_id)
    packet_bytes = len(json.dumps(packet, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    deadline_s = min(float(kwargs.get("timeout_s") or DEFAULT_DEADLINE_S),
                     REQUEST_DEADLINES_S.get(packet["role"], DEFAULT_DEADLINE_S))
    request = {"request_id": request_id, "provider": provider, "requested_model": model,
               "role": packet["role"], "packet_hash": packet_hash, "policy_hash": policy_hash,
               "controller_pid": os.getpid(), "status": "pending", "created_at": time.time(),
               "deadline_ts": time.time() + deadline_s, "packet_bytes": packet_bytes}
    atomic_write_json(path, request)
    try:
        while True:
            with state_lock(directory / ".lock"):
                current = read_json(path)
                _validate_request(root, current)
                if current["status"] == "answered":
                    result = current["result"]
                    current["status"] = "consumed"
                    current["consumed_at"] = time.time()
                    atomic_write_json(path, current)
                    # Bytes are the only cost signal a Desktop session leaves behind;
                    # estimated_tokens uses the  audit ratio (~1.9 B/token).
                    return {"status": "succeeded", "provider": provider, "model": model,
                            "payload": result, "elapsed_s": round(time.monotonic() - started, 3),
                            "usage": {"packet_bytes": packet_bytes,
                                      "estimated_tokens": round(packet_bytes / 1.9),
                                      "token_count_measured": False,
                                      "session": current.get("session")},
                            "transport": "desktop", "request_id": request_id,
                            "reported_model": None, "reported_models": [],
                            "model_verification": "unreported"}
                if current["status"] == "failed":
                    raise ValueError(current.get("error", "Desktop research failed"))
                if time.time() >= current["deadline_ts"]:
                    raise ValueError("desktop_deadline:" + packet["role"])
            time.sleep(0.25)
    except Exception as exc:
        with state_lock(directory / ".lock"):
            current = read_json(path)
            if current["status"] not in ("consumed", "failed"):
                current.update(status="expired", error=str(exc)[:500])
                atomic_write_json(path, current)
        raise


def claim(provider, *, root=AGENT_RUNS_DIR, wait_s=0):
    if provider not in ("claude", "codex") or not 0 <= wait_s <= 60:
        raise ValueError("invalid Desktop claim")
    root = Path(root)
    deadline = time.monotonic() + wait_s
    while True:
        with state_lock(root / "desktop" / ".lock"):
            for path in sorted((root / "desktop" / "requests").glob("*.json")):
                request = read_json(path)
                if request["provider"] != provider:
                    continue
                reclaim = (request["status"] == "claimed"
                           and time.time() >= request.get("claim_expires_ts", float("inf")))
                if request["status"] != "pending" and not reclaim:
                    continue
                try:
                    packet = _validate_request(root, request)
                except ValueError:
                    request["status"] = "expired"
                    atomic_write_json(path, request)
                    continue
                if time.time() >= request.get("deadline_ts", float("inf")):
                    continue  # the controller will mark it; do not hand out dead work
                token = uuid.uuid4().hex
                request.update(status="claimed", claim_token=token, claimed_at=time.time(),
                               claim_expires_ts=time.time() + CLAIM_LEASE_S,
                               reclaimed=bool(reclaim))
                atomic_write_json(path, request)
                return {"status": "claimed", "request_id": request["request_id"],
                        "claim_token": token, "provider": provider, "role": request["role"],
                        "deadline_ts": request.get("deadline_ts"),
                        "expires_at": packet["snapshot"]["expires_at"],
                        "packet": str(root / "desktop" / "packets" / (request["packet_hash"] + ".json")),
                        "policy": str(root / "desktop" / "policies" / (request["policy_hash"] + ".json"))}
        if time.monotonic() >= deadline:
            return {"status": "idle"}
        time.sleep(min(0.25, deadline - time.monotonic()))


def complete(request_id, token, provider, payload=None, *, error=None, root=AGENT_RUNS_DIR,
             session=None):
    root = Path(root)
    path = _path(root, request_id)
    with state_lock(root / "desktop" / ".lock"):
        request = read_json(path)
        if request["provider"] != provider or request.get("claim_token") != token:
            raise ValueError("Desktop claim/provider mismatch")
        if request["status"] != "claimed":
            raise ValueError("Desktop request is no longer claimed")
        packet = _validate_request(root, request)
        policy = read_json(root / "desktop" / "policies" / (request["policy_hash"] + ".json"))
        if canonical_hash(policy) != request["policy_hash"]:
            raise ValueError("Desktop policy artifact changed")
        if error:
            request.update(status="failed", error=str(error)[:500])
        else:
            # Binding fields are generated by the host, never by model prose.
            snapshot = packet["snapshot"]
            bindings = {"role": packet["role"], "contract_version": packet["contract_version"],
                        "schema_version": packet["schema"]["properties"]["schema_version"]["const"],
                        "snapshot_id": snapshot["snapshot_id"], "input_hash": snapshot["input_hash"],
                        "generated_at": snapshot["as_of"], "expires_at": snapshot["expires_at"]}
            if not isinstance(payload, dict):
                raise ValueError("Desktop result must be an object")
            for key, value in bindings.items():
                if key in payload and payload[key] != value:
                    raise ValueError("Desktop result binding mismatch: " + key)
            result = validate_result(packet["role"], {**payload, **bindings}, snapshot=snapshot,
                                     now=datetime.now(timezone.utc))
            target = packet.get("review_target")
            if (result["review"] or {}).get("run_id") != (target or {}).get("run_id"):
                raise ValueError("Desktop review target mismatch")
            if result.get("proposal"):
                # Left-shifted submission preview: run the
                # full static + writer-candidate dry run now, so the session can
                # fix a rejected proposal instead of failing hours later at apply.
                from quant.ops.agent_submit import preview_candidate
                probe = {"run_id": request_id, "role": packet["role"], "status": "succeeded",
                         "policy_hash": request["policy_hash"], "snapshot": snapshot,
                         "result": result, "review": None}
                preview = preview_candidate(probe, require_review=False)
                if not preview["can_apply"]:
                    raise ValueError("proposal blocked: " + "; ".join(preview["blocked_reasons"]))
            request.update(status="answered", result=result, answered_at=time.time(),
                           session=(session or None))
        atomic_write_json(path, request)
        return {"status": request["status"], "request_id": request_id,
                "applied": False, "note": "Host runtime owns validation and submission receipts."}


def main(argv=None):
    from quant.core.interpreter import require_project_interpreter
    require_project_interpreter()  # : system python killed three card jobs with a calendar error
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, default=AGENT_RUNS_DIR)
    sub = parser.add_subparsers(dest="command", required=True)
    start = sub.add_parser("launch")
    start.add_argument("--kind", choices=COMMANDS, required=True)
    host = sub.add_parser("controller")
    host.add_argument("--kind", choices=COMMANDS, required=True)
    host.add_argument("--id", required=True)
    poll = sub.add_parser("claim")
    poll.add_argument("--provider", choices=("claude", "codex"), required=True)
    poll.add_argument("--wait-s", type=float, default=0)
    reply = sub.add_parser("complete")
    reply.add_argument("--provider", choices=("claude", "codex"), required=True)
    reply.add_argument("--request-id", required=True)
    reply.add_argument("--claim-token", required=True)
    group = reply.add_mutually_exclusive_group(required=True)
    group.add_argument("--result", type=Path)
    group.add_argument("--error")
    reply.add_argument("--session", help="Desktop session identifier for the cost ledger (optional)")
    args = parser.parse_args(argv)
    try:
        if args.command == "launch":
            result = launch(args.kind, root=args.run_dir)
        elif args.command == "controller":
            result = controller(args.kind, args.id, root=args.run_dir)
        elif args.command == "claim":
            result = claim(args.provider, root=args.run_dir, wait_s=args.wait_s)
        else:
            result = complete(args.request_id, args.claim_token, args.provider,
                              read_json(args.result) if args.result else None,
                              error=args.error, root=args.run_dir, session=args.session)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (ValueError, OSError) as exc:
        print(json.dumps({"status": "rejected", "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
