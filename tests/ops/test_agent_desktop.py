"""Desktop packet handoff through the real runtime and controlled synthetic writer."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta
import json
import os
import time

import pytest

from quant.core.agent_contracts import canonical_hash
from quant.core.io_json import read_json
from quant.ops import agent_desktop as desktop, agent_runtime as runtime, agent_submit as submit
from tests.ops.test_agent_submit import NOW, record, writer  # noqa: F401


class Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)


@pytest.fixture
def environment(record, writer, monkeypatch, tmp_path):
    monkeypatch.setattr(runtime, "datetime", Clock)
    monkeypatch.setattr(desktop, "datetime", Clock)
    monkeypatch.setattr(runtime, "policy_context", lambda role: {"rule": "synthetic fixed policy"})
    routes = runtime.load_routes()
    routes["execution_transport"] = "desktop"
    routes["autopaint"].update(enabled=True, apply=True, review=False)
    routes["roles"]["technical"].update(provider="claude", fallback=[], timeout_s=1)
    monkeypatch.setattr(runtime, "load_routes", lambda *a: deepcopy(routes))
    root = tmp_path / "runtime"
    return record, routes, root


def test_desktop_result_becomes_record_then_applied_receipt(environment, writer):
    record, routes, root = environment
    before = writer.WATCHLIST_PATH.read_bytes()
    with ThreadPoolExecutor() as pool:
        future = pool.submit(runtime.run_role, "technical", "synthetic", record["snapshot"],
                             routes=routes, run_dir=root, control_dir=root / "control")
        item = desktop.claim("claude", root=root, wait_s=3)
        assert item["status"] == "claimed"
        assert desktop.claim("codex", root=root)["status"] == "idle"
        time.sleep(1.05)  # Route timeout is 1s; Desktop remains valid until snapshot expiry.
        payload = deepcopy(record["result"])
        # The Desktop need not construct host binding fields.
        for key in ("role", "schema_version", "contract_version", "snapshot_id", "input_hash", "generated_at", "expires_at"):
            payload.pop(key)
        accepted = desktop.complete(item["request_id"], item["claim_token"], "claude", payload, root=root)
        assert accepted["status"] == "answered" and accepted["applied"] is False
        assert writer.WATCHLIST_PATH.read_bytes() == before
        result = future.result(timeout=5)
    assert result["status"] == "succeeded"
    assert result["execution_transport"] == "desktop"
    assert result["provider"] == "claude" and result["model_verification"] == "unreported"
    assert result["policy_hash"] == canonical_hash({"rule": "synthetic fixed policy"})
    assert result["snapshot"] == record["snapshot"]
    receipt = runtime.submit_run(result["run_id"], run_dir=root, apply=True, automatic=True)
    assert receipt["status"] == "applied"
    assert runtime.submit_run(result["run_id"], run_dir=root, apply=True, automatic=True) == receipt
    after = writer.WATCHLIST_PATH.read_bytes()
    assert after != before
    with pytest.raises(ValueError, match="no longer claimed"):
        desktop.complete(item["request_id"], item["claim_token"], "claude", payload, root=root)
    assert writer.WATCHLIST_PATH.read_bytes() == after


@pytest.mark.parametrize("change", [
    "provider", "token", "binding", "packet", "policy", "protected", "expired",
])
def test_desktop_rejects_corrupt_or_unowned_results(environment, change, monkeypatch):
    record, routes, root = environment
    # Prepare a queue fixture without starting any controller/model threads.
    policy = {"rule": "synthetic fixed policy"}
    packet = {"snapshot": record["snapshot"], "role": "technical", "contract_version": record["result"]["contract_version"],
              "schema": runtime.execution_schema("technical", record["snapshot"], bind_inputs=False)}
    packet_hash, policy_hash = canonical_hash(packet), canonical_hash(policy)
    from quant.core.io_json import atomic_write_json
    atomic_write_json(root / "desktop" / "packets" / (packet_hash + ".json"), packet)
    atomic_write_json(root / "desktop" / "policies" / (policy_hash + ".json"), policy)
    rid = "a" * 32
    atomic_write_json(desktop._path(root, rid), {"request_id": rid, "provider": "claude", "role": "technical",
        "controller_pid": os.getpid(), "packet_hash": packet_hash, "policy_hash": policy_hash, "status": "pending"})
    item = desktop.claim("claude", root=root)
    payload = deepcopy(record["result"])
    provider, token = "claude", item["claim_token"]
    if change == "provider": provider = "codex"
    if change == "token": token = "wrong"
    if change == "binding": payload["input_hash"] = "0" * 64
    if change == "packet": atomic_write_json(root / "desktop" / "packets" / (packet_hash + ".json"), {})
    if change == "policy": atomic_write_json(root / "desktop" / "policies" / (policy_hash + ".json"), {})
    if change == "protected": payload["proposal"]["zone_update_args"] += ["--ticker", "OTHER"]
    if change == "expired":
        class ExpiredClock(Clock):
            @classmethod
            def now(cls, tz=None):
                return super().now(tz) + timedelta(days=2)
        monkeypatch.setattr(desktop, "datetime", ExpiredClock)
    with pytest.raises(ValueError):
        desktop.complete(rid, token, provider, payload, root=root)
    assert read_json(desktop._path(root, rid))["status"] == "claimed"


def test_controller_launch_failure_has_completion_receipt(tmp_path, monkeypatch):
    rid = "b" * 32
    directory = tmp_path / "desktop" / "controllers" / rid
    directory.mkdir(parents=True)
    def fail(*args, **kwargs):
        raise OSError("synthetic launch failure")
    monkeypatch.setattr(desktop.subprocess, "run", fail)
    result = desktop.controller("weekly", rid, root=tmp_path)
    assert result["exit_code"] == 1 and result["error"] == "synthetic launch failure"
    assert read_json(directory / "completion.json") == result


def _queue_request(root, record, rid, *, deadline_ts=None):
    """Write a pending request fixture without a controller thread."""
    from quant.core.io_json import atomic_write_json
    policy = {"rule": "synthetic fixed policy"}
    packet = {"snapshot": record["snapshot"], "role": "technical", "contract_version": record["result"]["contract_version"],
              "schema": runtime.execution_schema("technical", record["snapshot"], bind_inputs=False)}
    packet_hash, policy_hash = canonical_hash(packet), canonical_hash(policy)
    atomic_write_json(root / "desktop" / "packets" / (packet_hash + ".json"), packet)
    atomic_write_json(root / "desktop" / "policies" / (policy_hash + ".json"), policy)
    request = {"request_id": rid, "provider": "claude", "role": "technical", "controller_pid": os.getpid(),
               "packet_hash": packet_hash, "policy_hash": policy_hash, "status": "pending",
               "created_at": time.time(), "deadline_ts": deadline_ts or time.time() + 600}
    atomic_write_json(desktop._path(root, rid), request)
    return request


def test_request_deadline_fails_the_attempt_instead_of_waiting_for_ttl(environment, monkeypatch):
    """An absent provider costs at most the role deadline, never the whole TTL."""
    record, routes, root = environment
    monkeypatch.setattr(desktop, "REQUEST_DEADLINES_S", {"technical": 1})
    started = time.monotonic()
    with pytest.raises(ValueError, match="desktop_deadline:technical"):
        runtime_prompt = runtime.build_prompt("technical", "synthetic", record["snapshot"], {"rule": "p"})
        desktop.exchange("claude", runtime_prompt, runtime.execution_schema("technical", record["snapshot"], bind_inputs=False),
                         root=root, model="claude-opus-5", timeout_s=3600)
    assert time.monotonic() - started < 5
    request = next((root / "desktop" / "requests").glob("*.json"))
    assert read_json(request)["status"] == "expired"


def test_dead_claim_is_reclaimable_after_lease_and_stale_token_rejected(environment, monkeypatch):
    record, routes, root = environment
    rid = "c" * 32
    _queue_request(root, record, rid)
    first = desktop.claim("claude", root=root)
    assert first["status"] == "claimed"
    assert desktop.claim("claude", root=root)["status"] == "idle"
    monkeypatch.setattr(desktop, "CLAIM_LEASE_S", 0)
    stored = read_json(desktop._path(root, rid)); stored["claim_expires_ts"] = time.time() - 1
    from quant.core.io_json import atomic_write_json
    atomic_write_json(desktop._path(root, rid), stored)
    second = desktop.claim("claude", root=root)
    assert second["status"] == "claimed" and second["claim_token"] != first["claim_token"]
    assert read_json(desktop._path(root, rid))["reclaimed"] is True
    payload = deepcopy(record["result"])
    with pytest.raises(ValueError, match="claim/provider mismatch"):
        desktop.complete(rid, first["claim_token"], "claude", payload, root=root)


def test_complete_runs_the_writer_candidate_check_before_accepting(environment, writer):
    """A proposal the writer would reject is bounced at complete, not hours later."""
    record, routes, root = environment
    rid = "d" * 32
    _queue_request(root, record, rid)
    item = desktop.claim("claude", root=root)
    payload = deepcopy(record["result"])
    payload["proposal"]["zone_update_args"] += [
        "--next-regime", json.dumps({"name": "R_next", "entry_gate": {"level": 115, "days": 1, "volume_ratio_min": 1.2}})]
    with pytest.raises(ValueError, match="proposal blocked: .*relax existing gate confirmation"):
        desktop.complete(rid, item["claim_token"], "claude", payload, root=root)
    assert read_json(desktop._path(root, rid))["status"] == "claimed"
    good = deepcopy(record["result"])
    accepted = desktop.complete(rid, item["claim_token"], "claude", good, root=root, session="sess-1")
    assert accepted["status"] == "answered"
    assert read_json(desktop._path(root, rid))["session"] == "sess-1"
