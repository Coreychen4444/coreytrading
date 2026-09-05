"""Only synthetic control and run metadata; never call a model or inspect a book."""
from concurrent.futures import ThreadPoolExecutor
import json
import os

import pytest

from quant.ops import agent_health as health


POLICY = {"max_parallel": 3, "window_s": 3600, "max_calls": 24, "lease_grace_s": 60}


def reserve(root, number, *, now=10000, policy=None, **kwargs):
    return health.acquire_call(root, policy or POLICY, provider="claude", role="macro",
                               run_id=f"{number:032x}", timeout_s=60, now=now, **kwargs)


def write(root, directory, identity, **metadata):
    path = root / directory / f"{identity}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(metadata))
    return path


def test_global_cap_is_atomic_across_simultaneous_tasks(tmp_path):
    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(lambda n: reserve(tmp_path, n), range(12)))
    assert sum(item["status"] == "acquired" for item in results) == 3
    assert sum(item["status"] == "concurrency_limit" for item in results) == 9
    ledger = json.loads((tmp_path / "global-calls.json").read_text())
    assert len(ledger["calls"]) == 3


def test_contended_real_clock_is_sampled_inside_lock(tmp_path):
    def lifecycle(number):
        claim = reserve(tmp_path, number, now=None,
                        policy={**POLICY, "max_parallel": 16, "max_calls": 100})
        assert claim["status"] == "acquired"
        health.release_call(tmp_path, claim["lease_id"], status="success")
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lifecycle, range(32)))
    ledger = json.loads((tmp_path / "global-calls.json").read_text())
    assert len(ledger["calls"]) == 32 and all(i["status"] == "released" for i in ledger["calls"])


def test_runtime_claim_exposes_same_owner_deadline_to_health(tmp_path):
    from quant.ops import agent_runtime as runtime
    record, claimed = runtime._claim(tmp_path, "synthetic", {"role": "macro"}, 7200)
    job = json.loads((tmp_path / "jobs/synthetic.json").read_text())
    assert claimed and record["pid"] == job["pid"] == os.getpid()
    assert record["started_at"] == job["started_at"]
    assert record["lease_until"] == job["lease_until"]
    assert health.scan_health(tmp_path, now=record["lease_until"] - 1)["healthy"]


@pytest.mark.parametrize("outcome", ["failed", "timeout", "unavailable", "success", None, {"secret": "x"}])
def test_every_reservation_is_charged_even_after_failure_or_malformed_status(tmp_path, outcome):
    policy = {**POLICY, "max_calls": 1}
    claim = reserve(tmp_path, 1, policy=policy)
    released = health.release_call(tmp_path, claim["lease_id"], status=outcome, now=10001)
    assert released["status"] == "released"
    assert released["outcome"] == (outcome if isinstance(outcome, str) else "unknown")
    denied = reserve(tmp_path, 2, policy=policy, now=10002)
    assert denied["status"] == "window_budget" and denied["retry_after_s"] == 3598
    assert reserve(tmp_path, 3, policy=policy, now=13600)["status"] == "acquired"
    assert "secret" not in (tmp_path / "global-calls.json").read_text()


def test_same_run_provider_cannot_be_charged_or_invoked_twice(tmp_path):
    first = reserve(tmp_path, 1)
    assert reserve(tmp_path, 1)["status"] == "duplicate_call"
    health.release_call(tmp_path, first["lease_id"], status="success", now=10001)
    assert reserve(tmp_path, 1, now=10002)["status"] == "duplicate_call"
    other = health.acquire_call(tmp_path, POLICY, provider="codex", role="macro",
                                run_id=f"{1:032x}", timeout_s=60, now=10002)
    assert other["status"] == "acquired" and other["window_calls"] == 2


def test_expiry_never_steals_live_owner_or_registered_child_slot(monkeypatch, tmp_path):
    policy = {**POLICY, "max_parallel": 1}
    claim = reserve(tmp_path, 1, policy=policy, pid=101)
    monkeypatch.setattr(health, "_pid_alive", lambda pid: True)
    assert reserve(tmp_path, 2, policy=policy, now=10200)["status"] == "concurrency_limit"
    health.attach_call_process(tmp_path, claim["lease_id"], 202, pid=101, now=10201)
    monkeypatch.setattr(health, "_pid_alive", lambda pid: False)
    monkeypatch.setattr(health, "_group_alive", lambda pid: True)
    assert reserve(tmp_path, 3, policy=policy, now=10202)["status"] == "concurrency_limit"
    monkeypatch.setattr(health, "_group_alive", lambda pid: False)
    assert reserve(tmp_path, 4, policy=policy, now=10203)["status"] == "acquired"
    old = json.loads((tmp_path / "global-calls.json").read_text())["calls"][0]
    assert old["status"] == "orphaned" and old["outcome"] == "owner_expired"


def test_release_waits_for_group_exit_then_reaps_even_when_owner_is_alive(monkeypatch, tmp_path):
    policy = {**POLICY, "max_parallel": 1}
    claim = reserve(tmp_path, 1, policy=policy)
    health.attach_call_process(tmp_path, claim["lease_id"], 202, now=10000)
    monkeypatch.setattr(health, "_group_alive", lambda pid: True)
    assert health.release_call(tmp_path, claim["lease_id"], status="timeout", now=10001)["status"] == "releasing"
    assert reserve(tmp_path, 2, policy=policy, now=10002)["status"] == "concurrency_limit"
    monkeypatch.setattr(health, "_group_alive", lambda pid: False)
    assert reserve(tmp_path, 3, policy=policy, now=10003)["status"] == "acquired"


def test_only_reservation_owner_can_attach_or_release(tmp_path):
    claim = reserve(tmp_path, 1, pid=101)
    with pytest.raises(health.HealthError, match="call_lease_not_owned"):
        health.attach_call_process(tmp_path, claim["lease_id"], 202, pid=999, now=10000)
    with pytest.raises(health.HealthError, match="lease_owner_mismatch"):
        health.release_call(tmp_path, claim["lease_id"], status="success", pid=999, now=10000)


def test_clock_rollback_and_corrupt_ledger_fail_closed(tmp_path):
    reserve(tmp_path, 1)
    with pytest.raises(health.HealthError, match="control_clock_regressed"):
        reserve(tmp_path, 2, now=9999)
    path = tmp_path / "global-calls.json"
    path.write_text('{"secret":"not-a-ledger"}')
    with pytest.raises(health.HealthError, match="control_state_invalid"):
        reserve(tmp_path, 3)
    assert json.loads(path.read_text()) == {"secret": "not-a-ledger"}


def test_window_policy_increase_cannot_erase_old_charges(tmp_path):
    policy = {**POLICY, "window_s": 10, "max_calls": 1}
    claim = reserve(tmp_path, 1, policy=policy)
    health.release_call(tmp_path, claim["lease_id"], status="success", now=10001)
    assert reserve(tmp_path, 2, policy={**policy, "window_s": 3600}, now=10020)["status"] == "window_budget"


def test_business_keys_are_exclusive_hash_only_and_do_not_spend_model_budget(tmp_path):
    first = health.acquire_job(tmp_path, "technical:watchlist:TEST", timeout_s=7200, now=10000)
    assert first["lease_until_ts"] == 17200
    assert health.acquire_job(tmp_path, "technical:watchlist:TEST", timeout_s=7200, now=10001)["status"] == "busy"
    assert health.acquire_job(tmp_path, "technical:watchlist:OTHER", timeout_s=7200, now=10002)["status"] == "acquired"
    assert health.owns_job(tmp_path, first["lease_id"], now=10003)
    assert not (tmp_path / "global-calls.json").exists()
    assert "TEST" not in (tmp_path / "business-leases.json").read_text()
    health.release_job(tmp_path, first["lease_id"], now=10004)
    assert not health.owns_job(tmp_path, first["lease_id"], now=10004)


def test_business_crash_recovery_protects_alive_owner_and_rejects_old_fence(monkeypatch, tmp_path):
    first = health.acquire_job(tmp_path, "same-card", timeout_s=10, pid=101, now=10000)
    monkeypatch.setattr(health, "_pid_alive", lambda pid: True)
    assert health.acquire_job(tmp_path, "same-card", timeout_s=10, pid=102, now=10011)["status"] == "busy"
    monkeypatch.setattr(health, "_pid_alive", lambda pid: False)
    second = health.acquire_job(tmp_path, "same-card", timeout_s=10, pid=102, now=10012)
    assert second["status"] == "acquired"
    with pytest.raises(health.HealthError, match="job_lease_not_owned"):
        health.release_job(tmp_path, first["lease_id"], pid=101, now=10012)
    assert health.owns_job(tmp_path, second["lease_id"], pid=102, now=10012)


def test_health_is_quiet_without_explicit_cadence_and_is_read_only(tmp_path, capsys):
    missing = tmp_path / "absent"
    assert health.scan_health(missing, now=10000)["healthy"]
    assert health.main(["--run-dir", str(missing)]) == 0
    assert json.loads(capsys.readouterr().out)["healthy"]
    assert not missing.exists()
    explicit = health.scan_health(missing, now=10000, expected_roles=["macro"],
                                  expected_jobs={"weekly": {"max_age_s": 604800}})
    assert len(explicit["issues"]) == 2
    assert all(i["code"] == "expected_completion_missing" for i in explicit["issues"])


def test_long_running_background_job_uses_its_declared_deadline(tmp_path):
    write(tmp_path, "task-jobs", "synthetic", status="running", started_at_ts=1000,
          lease_until=11800, pid=os.getpid(), job_kind="weekly", stage="research")
    assert health.scan_health(tmp_path, now=5000)["healthy"]
    overdue = health.scan_health(tmp_path, now=11801)
    assert overdue["issues"][0]["code"] == "unfinished_owner_alive"
    assert overdue["issues"][0]["stage"] == "research"


@pytest.mark.parametrize("status,code", [("failed", "run_failed"), ("timeout", "run_timeout"),
    ("needs_review", "needs_review"), ("partial", "job_partial"),
    ("busy", "job_busy"), ("superseded", "job_superseded"), ("expired", "job_unfinished")])
def test_non_success_outcomes_are_actionable_and_never_count_as_success(tmp_path, status, code):
    write(tmp_path, "task-jobs", "synthetic", status=status, started_at_ts=1000,
          finished_at_ts=1050, job_kind="weekly", ticker="TEST")
    result = health.scan_health(tmp_path, now=1100)
    assert result["counts"]["succeeded"] == 0 and result["issues"][0]["code"] == code


@pytest.mark.parametrize("status", ["no_effect", "skipped", "complete"])
def test_neutral_terminal_is_finished_but_does_not_satisfy_success_cadence(tmp_path, status):
    write(tmp_path, "task-jobs", "synthetic", status=status, started_at_ts=1000,
          finished_at_ts=1050, job_kind="weekly")
    assert health.scan_health(tmp_path, now=1100)["healthy"]
    result = health.scan_health(tmp_path, now=1100, expected_jobs={"weekly": 1000})
    assert result["counts"]["succeeded"] == 0
    assert result["issues"][0]["code"] == "expected_completion_missing"


def test_health_exports_metadata_only_and_detects_uncertain_submission(tmp_path):
    write(tmp_path, "runs", "synthetic", status="failed", started_at_ts=1000,
          task="secret-task", snapshot={"private": "secret-snapshot"}, result={"summary": "secret-result"},
          error="secret-auth", role="technical", ticker="TEST", provider="claude",
          attempts=[{"status": "timeout", "stderr": "secret-provider-diagnostic"}],
          submission={"status": "applying", "prepared": {"private": "secret-card"}})
    result = health.update_health(tmp_path, now=1100)
    assert {i["code"] for i in result["issues"]} == {"run_timeout", "submission_uncertain"}
    assert "secret-" not in json.dumps(result)
    assert "secret-" not in (tmp_path / "health.json").read_text()


def test_completed_receipt_with_missing_run_is_not_clean(tmp_path):
    write(tmp_path, "jobs", "synthetic", status="complete", run_id="a" * 32)
    assert health.scan_health(tmp_path, now=1100)["issues"][0]["code"] == "completed_job_result_missing"


def test_reconciliation_needing_review_does_not_turn_uncertainty_into_green(tmp_path):
    path = write(tmp_path, "runs", "synthetic", status="succeeded", started_at_ts=1000,
                 submission={"status": "applying"})
    health.update_health(tmp_path, now=1100)
    path.write_text(json.dumps({"status": "succeeded", "submission": {"status": "needs_review"}}))
    review = health.update_health(tmp_path, now=1101)
    assert not review["healthy"]
    assert {i["code"] for i in review["active_issues"]} == {"submission_needs_review"}
    path.write_text(json.dumps({"status": "succeeded", "submission": {"status": "no_effect"}}))
    assert health.update_health(tmp_path, now=1102)["healthy"]


def test_health_outbox_is_durable_idempotent_and_recovers_exactly_once(tmp_path):
    path = write(tmp_path, "runs", "synthetic", status="needs_review", started_at_ts=1000)
    first = health.update_health(tmp_path, now=1100)
    opened = first["new_events"][0]
    assert opened["kind"] == "opened"
    repeated = health.update_health(tmp_path, now=1101)
    assert repeated["new_events"] == [] and repeated["pending_events"] == [opened]
    assert health.acknowledge_events(tmp_path, [opened["event_id"]]) == {"acknowledged": 1, "pending": 0}
    assert health.acknowledge_events(tmp_path, [opened["event_id"]])["acknowledged"] == 0
    path.write_text(json.dumps({"status": "no_effect", "finished_at_ts": 1150}))
    recovered = health.update_health(tmp_path, now=1200)
    assert recovered["healthy"] and recovered["new_events"][0]["kind"] == "recovered"
    assert health.update_health(tmp_path, now=1201)["new_events"] == []
    path.write_text(json.dumps({"status": "needs_review"}))
    recurrence = health.update_health(tmp_path, now=1202)["new_events"][0]
    assert recurrence["kind"] == "opened" and recurrence["event_id"] != opened["event_id"]


def test_disappearing_metadata_or_running_retry_is_not_false_recovery(tmp_path):
    path = write(tmp_path, "runs", "synthetic", status="failed")
    health.update_health(tmp_path, now=1100)
    path.unlink()
    missing = health.update_health(tmp_path, now=1101)
    assert not missing["healthy"] and missing["new_events"] == []
    path.write_text(json.dumps({"status": "running", "lease_until": 2000}))
    retry = health.update_health(tmp_path, now=1102)
    assert not retry["healthy"] and retry["new_events"] == []


def test_expected_job_cadence_recovers_only_after_success(tmp_path):
    opts = {"expected_jobs": {"weekly": 1000}}
    health.update_health(tmp_path, now=1100, **opts)
    write(tmp_path, "task-jobs", "synthetic", status="completed", job_kind="weekly", finished_at_ts=1150)
    recovered = health.update_health(tmp_path, now=1200, **opts)
    assert recovered["healthy"] and recovered["new_events"][0]["kind"] == "recovered"


def test_corrupt_health_state_and_malformed_run_are_reported_without_content(tmp_path):
    path = write(tmp_path, "runs", "synthetic", status="failed", attempts=4)
    assert health.scan_health(tmp_path, now=1100)["issues"][0]["code"] == "run_failed"
    path.write_text('not json secret-payload')
    result = health.scan_health(tmp_path, now=1101)
    assert result["issues"][0]["code"] == "metadata_unreadable"
    assert "secret" not in json.dumps(result)
    (tmp_path / "health.json").write_text('{"version":1,"sequence":0,"issues":{"bad":null},"outbox":[]}')
    with pytest.raises(health.HealthError, match="health_state_invalid"):
        health.update_health(tmp_path, now=1102)
