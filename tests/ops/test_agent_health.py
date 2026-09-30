"""Only synthetic control and run metadata; never call a model or inspect a book."""
from concurrent.futures import ThreadPoolExecutor
import json
import os

import pytest

from quant.ops import agent_health as health


POLICY = {"max_parallel": 3, "window_s": 3600, "max_calls": 24, "lease_grace_s": 60}


def reserve(root, number, *, now=10000, policy=None, **kwargs):
    return health.acquire_call(root, policy or POLICY, provider="claude", role="valuation",
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
    record, claimed = runtime._claim(tmp_path, "synthetic", {"role": "valuation"}, 7200)
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
    other = health.acquire_call(tmp_path, POLICY, provider="codex", role="valuation",
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


def test_retired_role_in_ledger_stays_charged_then_ages_out(tmp_path):
    # A retired role (macro) left ROLES while a released call was still in the
    # ledger; the reader rejected it before _reap could prune it, so every later
    # acquire failed with control_state_invalid.
    policy = {**POLICY, "window_s": 3600, "max_calls": 1}
    claim = reserve(tmp_path, 1, policy=policy)
    health.release_call(tmp_path, claim["lease_id"], status="success", now=10001)
    path = tmp_path / "global-calls.json"
    ledger = json.loads(path.read_text())
    ledger["calls"][0]["role"] = "retired_role"
    path.write_text(json.dumps(ledger))
    assert reserve(tmp_path, 2, policy=policy, now=10020)["status"] == "window_budget"
    assert reserve(tmp_path, 3, policy=policy, now=10000 + health.MAX_WINDOW_S + 1)["status"] == "acquired"
    assert [c["role"] for c in json.loads(path.read_text())["calls"]] == ["valuation"]
    with pytest.raises(health.HealthError, match="invalid_call_metadata"):
        health.acquire_call(tmp_path, POLICY, provider="claude", role="retired_role",
                            run_id=f"{4:032x}", timeout_s=60, now=10000 + health.MAX_WINDOW_S + 2)


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
    explicit = health.scan_health(missing, now=10000, expected_roles=["valuation"],
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


def card_attempt(root, identity, *, ticker="TEST", bucket="watchlist", start=1000,
                 finish=1050, status="failed", receipt=None):
    run_id = ("a" if identity == "old" else "b") * 32
    metadata = dict(job_kind="card", ticker=ticker, bucket=bucket, status=status,
                    started_at_ts=start, finished_at_ts=finish, run_id=run_id)
    if receipt is not None:
        metadata["submission"] = receipt
    return write(root, "task-jobs", identity, **metadata)


def applied_card(root, **kwargs):
    receipt = dict(status="applied", applied=True, after_revision="c" * 64, run_id="b" * 32)
    return card_attempt(root, "new", start=1100, finish=1150, status="applied",
                        receipt=receipt, **kwargs)


def test_later_applied_card_recovers_old_job_and_linked_run_without_rewriting(tmp_path):
    job = card_attempt(tmp_path, "old")
    run = write(tmp_path, "runs", "a" * 32, role="technical", status="failed",
                started_at_ts=1001, finished_at_ts=1049)
    originals = {p: p.read_bytes() for p in (job, run)}
    first = health.update_health(tmp_path, now=1070)
    assert len(first["active_issues"]) == 2
    applied_card(tmp_path)
    result = health.update_health(tmp_path, now=1200)
    assert result["healthy"] and result["counts"]["failed"] == 2
    assert len(result["new_events"]) == 2
    assert all(e["kind"] == "recovered" and e["issue"]["recovered_by"] == "task_job:new"
               for e in result["new_events"])
    assert all(p.read_bytes() == content for p, content in originals.items())
    # Delivery of opened events may already have happened; keep the durable outbox.
    assert len(result["pending_events"]) == 4
    assert health.update_health(tmp_path, now=1201)["new_events"] == []
    fresh = health.update_health(tmp_path, state_dir=tmp_path / "fresh", now=1201)
    assert fresh["healthy"] and fresh["new_events"] == []


@pytest.mark.parametrize("change", [
    {"status": "succeeded"}, {"status": "completed"}, {"status": "needs_review"},
    {"status": "running", "lease_until": 2000}, {"ticker": "OTHER"},
    {"bucket": "positions"}, {"started_at_ts": 1040}, {"finished_at_ts": 1300},
    {"submission": {"status": "applied", "applied": True}},
    {"submission": {"status": "applying", "outcome_uncertain": True}},
])
def test_unconfirmed_unrelated_overlapping_or_future_retry_does_not_recover(tmp_path, change):
    card_attempt(tmp_path, "old")
    health.update_health(tmp_path, now=1070)
    path = applied_card(tmp_path)
    doc = json.loads(path.read_text())
    doc.update(change)
    path.write_text(json.dumps(doc))
    result = health.update_health(tmp_path, now=1200)
    assert any(i["source_id"] == "task_job:old" for i in result["active_issues"])
    assert not any(e["kind"] == "recovered" for e in result["new_events"])


def test_new_failure_and_uncertain_submission_stay_actionable(tmp_path):
    card_attempt(tmp_path, "old", receipt={"status": "uncertain", "outcome_uncertain": True})
    applied_card(tmp_path)
    card_attempt(tmp_path, "later", start=1160, finish=1170)
    result = health.scan_health(tmp_path, now=1200)
    assert result["recoveries"] == {}
    assert {i["source_id"] for i in result["issues"]} == {"task_job:old", "task_job:later"}
    assert "submission_uncertain" in {i["code"] for i in result["issues"]}


def test_rejected_submission_on_a_succeeded_run_recovers_with_its_card(tmp_path):
    """典型: 研究成功、提交撞 revision conflict (未写盘), 4 分钟后重跑
    applied —— 旧判据下 run 的 status 是 succeeded, 不在 _FAILURES 里, 这条 warning
    每轮 heartbeat 报到天荒地老。"""
    run_id = "a" * 32
    card_attempt(tmp_path, "old")
    write(tmp_path, "runs", run_id, role="technical", status="succeeded",
          started_at_ts=1001, finished_at_ts=1049,
          submission={"status": "rejected", "run_id": run_id,
                      "reason": "revision conflict: … — abort, 未写盘",
                      "outcome_uncertain": False})
    first = health.scan_health(tmp_path, now=1070)
    assert "submission_rejected" in {i["code"] for i in first["issues"]}
    applied_card(tmp_path)
    result = health.scan_health(tmp_path, now=1200)
    assert result["healthy"], result["issues"]
    assert result["recoveries"]["run:" + run_id] == "task_job:new"


@pytest.mark.parametrize("receipt", [
    {"status": "rejected", "outcome_uncertain": True},
    {"status": "needs_review"},
    {"status": "applying", "outcome_uncertain": True},
])
def test_uncertain_or_reviewable_submission_never_auto_recovers(tmp_path, receipt):
    """"写没写不确定" 与 "被拒但确定没写" 不是一回事: 前者永远留人判。"""
    run_id = "a" * 32
    card_attempt(tmp_path, "old")
    write(tmp_path, "runs", run_id, role="technical", status="succeeded",
          started_at_ts=1001, finished_at_ts=1049, submission=dict(receipt, run_id=run_id))
    applied_card(tmp_path)
    result = health.scan_health(tmp_path, now=1200)
    assert not result["healthy"]
    assert any(i["source_id"] == "run:" + run_id for i in result["issues"])


def test_same_role_unlinked_run_is_not_recovered(tmp_path):
    card_attempt(tmp_path, "old")
    applied_card(tmp_path)
    write(tmp_path, "runs", "d" * 32, role="technical", status="failed", finished_at_ts=1040)
    result = health.scan_health(tmp_path, now=1200)
    assert [i["source_id"] for i in result["issues"]] == ["run:" + "d" * 32]


@pytest.mark.parametrize("block", [None, "missing_workflow", "partial_workflow", "failed_role",
                                    "missing_card", "uncertain_card"])
def test_partial_parent_requires_completed_research_and_every_card_resolved(tmp_path, block):
    reports = {role: {"status": "succeeded"} for role in ("technical", "risk")}
    if block == "failed_role":
        reports["risk"]["status"] = "failed"
    card = {"ticker": "TEST", "bucket": "watchlist", "status": "failed"}
    if block == "uncertain_card":
        card["submission"] = {"status": "uncertain"}
    # missing_workflow = the job references a workflow that is gone (workflow-era briefing)
    extra = {"workflow_artifact": "workflows/parent.json"} if block == "missing_workflow" else {}
    write(tmp_path, "task-jobs", "parent", job_kind="briefing", stage="finished",
          status="partial", started_at_ts=900, finished_at_ts=1060, reports=reports,
          cards=[card], pending_cards=[{"ticker": "OTHER", "bucket": "watchlist"}]
          if block == "missing_card" else [], **extra)
    if block != "missing_workflow":
        write(tmp_path, "workflows", "parent", status="partial" if block == "partial_workflow" else "completed",
              result="secret-workflow")
    health.update_health(tmp_path, now=1070)
    applied_card(tmp_path)
    result = health.update_health(tmp_path, now=1200)
    assert result["healthy"] == (block is None)
    assert "secret-workflow" not in json.dumps(result)
    if block is None:
        assert result["new_events"][0]["issue"]["recovered_by"] == ["task_job:new"]


def parent_job(root, identity, *, kind="briefing", start=900, finish=1060, research="succeeded",
               cards=(), pending=(), status="partial"):
    reports = {"technical": {"status": research}, "risk": {"status": "succeeded"}}
    return write(root, "task-jobs", identity, job_kind=kind, stage="finished", status=status,
                 started_at_ts=start, finished_at_ts=finish, reports=reports,
                 research_status="completed" if research == "succeeded" else "partial",
                 cards=list(cards), pending_cards=list(pending))


def test_briefing_without_workflow_recovers_once_cards_resolve(tmp_path):
    # briefing 不走 workflow; 旧判据把「没有 workflow 文件」当失败, 永不恢复
    parent_job(tmp_path, "parent", cards=[{"ticker": "TEST", "bucket": "watchlist", "status": "failed"}])
    assert not health.update_health(tmp_path, now=1070)["healthy"]
    applied_card(tmp_path)
    result = health.update_health(tmp_path, now=1200)
    assert result["healthy"]
    assert result["new_events"][0]["issue"]["recovered_by"] == ["task_job:new"]


def abstained(ticker="TEST", bucket="positions"):
    return dict(job_kind="card", ticker=ticker, bucket=bucket, status="needs_review", reason="model_abstained")


def test_model_abstention_is_neutral_and_clears_an_open_issue(tmp_path):
    write(tmp_path, "task-jobs", "abst", started_at_ts=1000, finished_at_ts=1050, **abstained())
    write(tmp_path, "task-jobs", "blocked", job_kind="card", ticker="TEST", bucket="watchlist",
          status="needs_review", reason="submission_preview_blocked", started_at_ts=1000, finished_at_ts=1050)
    result = health.scan_health(tmp_path, now=1100)
    assert [i["source_id"] for i in result["issues"]] == ["task_job:blocked"]   # 真待判的仍报
    assert result["observed"]["task_job:abst"] == "no_effect"
    # 旧版本已把弃权记成 active issue → 升级后转 recovered, 不永久挂着
    state = {"version": 1, "sequence": 1, "outbox": [], "issues": {}}
    old = {"source": "task_job", "source_id": "task_job:abst", "code": "needs_review", "issue_id": "e" * 64}
    state["issues"]["e" * 64] = {"active": True, "issue": old}
    (tmp_path / "health.json").write_text(json.dumps(state))
    kinds = {e["issue"]["source_id"]: e["kind"] for e in health.update_health(tmp_path, now=1100)["new_events"]}
    assert kinds["task_job:abst"] == "recovered"


def test_parent_with_abstained_card_recovers(tmp_path):
    parent_job(tmp_path, "parent", cards=[abstained(bucket="watchlist"),
                                          {"ticker": "OK", "bucket": "watchlist", "status": "applied",
                                           "run_id": "b" * 32, "submission": {
                                               "status": "applied", "applied": True,
                                               "after_revision": "c" * 64, "run_id": "b" * 32}}])
    result = health.scan_health(tmp_path, now=1100)
    assert result["healthy"]
    assert result["recoveries"]["task_job:parent"] == ["no_effect:model_abstained"]


def test_research_failed_parent_superseded_by_later_same_kind_job(tmp_path):
    parent_job(tmp_path, "old", kind="postclose", research="failed",
               cards=[{"ticker": "TEST", "bucket": "watchlist", "status": "failed"}],
               pending=[{"ticker": "GONE", "bucket": "positions"}])
    applied_card(tmp_path)                                               # TEST 之后落卡
    parent_job(tmp_path, "rerun", kind="postclose", start=1160, finish=1180, status="completed",
               cards=[{"ticker": "TEST", "bucket": "watchlist", "status": "applied"}])  # 不再排 GONE
    parent_job(tmp_path, "other", kind="briefing", start=1160, finish=1180, research="failed")
    result = health.scan_health(tmp_path, now=1200)
    assert result["recoveries"]["task_job:old"] == ["task_job:new", "task_job:rerun"]
    assert "task_job:other" not in result["recoveries"]                 # 自己研究失败且无后继


def test_research_failure_needs_a_later_same_kind_completion(tmp_path):
    parent_job(tmp_path, "old", kind="postclose", research="failed")
    parent_job(tmp_path, "briefing", kind="briefing", start=1160, finish=1180, status="completed")
    assert "task_job:old" not in health.scan_health(tmp_path, now=1200)["recoveries"]


def test_failed_research_run_recovered_by_later_same_task_success_but_not_card_runs(tmp_path):
    write(tmp_path, "runs", "1" * 32, run_id="1" * 32, role="valuation", status="failed",
          task="daily valuation", started_at_ts=1000, finished_at_ts=1001)
    write(tmp_path, "runs", "2" * 32, run_id="2" * 32, role="valuation", status="succeeded",
          task="daily valuation", started_at_ts=1100, finished_at_ts=1150)
    write(tmp_path, "runs", "3" * 32, run_id="3" * 32, role="technical", status="failed",
          task="card task", started_at_ts=1000, finished_at_ts=1001)
    write(tmp_path, "task-jobs", "cardjob", job_kind="card", ticker="AAA", bucket="watchlist",
          status="failed", run_id="3" * 32, started_at_ts=1000, finished_at_ts=1002)
    write(tmp_path, "runs", "4" * 32, run_id="4" * 32, role="technical", status="succeeded",
          task="card task", started_at_ts=1100, finished_at_ts=1150)
    write(tmp_path, "runs", "5" * 32, run_id="5" * 32, role="valuation", status="failed",
          task="another task", started_at_ts=1000, finished_at_ts=1001)
    rec = health.scan_health(tmp_path, now=1200)["recoveries"]
    assert rec["run:" + "1" * 32] == "run:" + "2" * 32
    assert "run:" + "3" * 32 not in rec                                  # 别的票的单卡成功不算
    assert "run:" + "5" * 32 not in rec
