"""Synthetic dependency receipts; no pipeline, model, network or live state."""
import json

import pytest

from quant.ops import agent_dependency as dependency


SESSION = "2026-09-04"


class Clock:
    def __init__(self, hook=None):
        self.elapsed, self.hook = 0, hook

    def now(self):
        return self.elapsed

    def sleep(self, seconds):
        self.elapsed += seconds
        if self.hook:
            self.hook(self.elapsed)


def receipt(root, *, identity="a", status="running", session=SESSION, kind="postclose", **kwargs):
    folder = root / "task-jobs"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{identity * 32}.json"
    doc = {"job_id": identity * 32, "job_kind": kind, "session": session,
           "status": status, "started_at": "2026-09-04T21:00:00+00:00", **kwargs}
    path.write_text(json.dumps(doc))
    return path


def wait(root, clock=None, timeout=10, **kwargs):
    clock = clock or Clock()
    return dependency.wait_for_job(root, "postclose", SESSION, timeout_s=timeout,
                                   monotonic=clock.now, sleep=clock.sleep,
                                   wall_time=lambda: 1000, poll_s=1, **kwargs)


@pytest.mark.parametrize("status", ["completed", "partial"])
def test_only_finished_same_session_job_settles_dependency(tmp_path, status):
    receipt(tmp_path, status=status, finished_at="2026-09-04T22:00:00+00:00",
            reports={"secret": "payload"}, error="private diagnostic")
    out = wait(tmp_path)
    assert out["status"] == "settled" and out["upstream_status"] == status
    assert out["elapsed_s"] == 0 and "secret" not in json.dumps(out)
    assert "private" not in json.dumps(out)


def test_completed_research_does_not_release_barrier_while_repaint_is_active(tmp_path):
    receipt(tmp_path, status="completed", stage="reviewed_repaint")
    assert wait(tmp_path)["status"] == "timeout"


def test_missing_job_keeps_waiting_until_upstream_finishes(tmp_path):
    def progress(elapsed):
        if elapsed == 2:
            receipt(tmp_path, status="running")
        if elapsed == 4:
            receipt(tmp_path, status="partial", finished_at="2026-09-04T22:00:00+00:00")
    out = wait(tmp_path, Clock(progress))
    assert out["status"] == "settled" and out["elapsed_s"] == 4


def test_missing_dependency_times_out_without_creating_any_files(tmp_path):
    out = wait(tmp_path)
    assert out["status"] == "timeout" and out["reason"] == "upstream_not_started"
    assert list(tmp_path.iterdir()) == []


def test_old_session_or_other_kind_success_never_satisfies_current_dependency(tmp_path):
    receipt(tmp_path, identity="a", status="completed", session="2026-09-03",
            finished_at="2026-09-03T22:00:00+00:00")
    receipt(tmp_path, identity="b", status="completed", kind="weekly",
            finished_at="2026-09-04T22:00:00+00:00")
    assert wait(tmp_path)["reason"] == "upstream_not_started"


def test_latest_same_session_attempt_controls_barrier(tmp_path):
    receipt(tmp_path, status="completed", finished_at="2026-09-04T22:00:00+00:00")
    receipt(tmp_path, identity="b", status="running", started_at="2026-09-04T22:10:00+00:00")
    assert wait(tmp_path)["status"] == "timeout"


def test_newer_capture_without_session_blocks_an_old_completed_receipt(tmp_path):
    receipt(tmp_path, status="completed", finished_at="2026-09-04T22:00:00+00:00")
    receipt(tmp_path, identity="b", session=None, status="running",
            started_at="2026-09-04T22:10:00+00:00")
    assert wait(tmp_path)["status"] == "timeout"


def test_unscoped_completed_receipt_cannot_authorize_a_session(tmp_path):
    receipt(tmp_path, status="completed", session=None, finished_at="2026-09-04T22:00:00+00:00")
    assert wait(tmp_path)["status"] == "timeout"


@pytest.mark.parametrize("status", ["failed", "timeout", "needs_review", "expired", "superseded"])
def test_upstream_failure_halts_dependent_work(tmp_path, status):
    receipt(tmp_path, status=status, error="private diagnostic")
    out = wait(tmp_path)
    assert out["status"] == "failure" and out["reason"] == "upstream_" + status
    assert out["elapsed_s"] == 0


def test_expired_running_lease_returns_without_waiting(tmp_path):
    receipt(tmp_path, lease_until=999)
    assert wait(tmp_path)["reason"] == "upstream_lease_expired"


def test_unreadable_receipt_cannot_hide_an_inflight_writer(tmp_path):
    path = receipt(tmp_path)
    path.write_text("secret invalid metadata")
    out = wait(tmp_path)
    assert out["status"] == "failure" and "secret" not in json.dumps(out)


def test_cli_exit_code_prevents_followup_when_no_job_finished(tmp_path, capsys):
    assert dependency.main(["--run-dir", str(tmp_path), "--kind", "postclose",
                            "--session", SESSION, "--timeout-s", "0"]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "timeout"
    receipt(tmp_path, status="completed", finished_at="2026-09-04T22:00:00+00:00")
    assert dependency.main(["--run-dir", str(tmp_path), "--kind", "postclose",
                            "--session", SESSION, "--timeout-s", "0"]) == 0


# Regression: the default wait budget must cover the upstream job lease, not a fixed guess.

def test_default_budget_is_the_upstream_business_lease(tmp_path):
    from quant.ops.agent_health import DOMAIN_JOB_LEASE_S
    receipt(tmp_path, status="completed", finished_at="2026-09-04T22:00:00+00:00")
    out = dependency.wait_for_job(tmp_path, "postclose", SESSION, monotonic=lambda: 0,
                                  sleep=lambda s: None, wall_time=lambda: 1000)
    assert out["status"] == "settled" and out["budget_s"] == DOMAIN_JOB_LEASE_S
    assert DOMAIN_JOB_LEASE_S > 1800  # covers research + repaint_bound cards, not the old guess


def test_healthy_upstream_is_waited_for_beyond_the_old_thirty_minutes(tmp_path):
    receipt(tmp_path, status="completed", stage="reviewed_repaint", pid=4242)
    def progress(elapsed):
        if elapsed == 2700:
            receipt(tmp_path, status="completed", finished_at="2026-09-04T22:00:00+00:00", pid=4242)
    out = wait(tmp_path, Clock(progress), timeout=None, pid_alive=lambda pid: True)
    assert out["status"] == "settled" and out["elapsed_s"] == 2700


def test_dead_upstream_process_fails_fast_instead_of_burning_the_lease(tmp_path):
    receipt(tmp_path, status="running", stage="reviewed_repaint", pid=4242, lease_until=99999)
    out = wait(tmp_path, timeout=None, pid_alive=lambda pid: pid != 4242)
    assert out["status"] == "failure" and out["reason"] == "upstream_process_dead"
    assert out["elapsed_s"] == 0
    # A finished receipt is settled by finished_at alone; its writer is expected to be gone.
    receipt(tmp_path, status="completed", finished_at="2026-09-04T22:00:00+00:00", pid=4242)
    assert wait(tmp_path, pid_alive=lambda pid: False)["status"] == "settled"


def test_receipt_without_pid_is_judged_by_lease_only(tmp_path):
    receipt(tmp_path, status="running", lease_until=99999)
    out = wait(tmp_path, timeout=3, pid_alive=lambda pid: (_ for _ in ()).throw(AssertionError))
    assert out["status"] == "timeout" and out["reason"] == "upstream_unfinished"


def test_never_started_upstream_only_gets_the_start_grace(tmp_path):
    out = wait(tmp_path, timeout=None, not_started_grace_s=5)
    assert out["status"] == "timeout" and out["reason"] == "upstream_not_started"
    assert out["elapsed_s"] == 5


def test_cli_default_budget_omits_timeout_flag(tmp_path, capsys):
    receipt(tmp_path, status="completed", finished_at="2026-09-04T22:00:00+00:00")
    assert dependency.main(["--run-dir", str(tmp_path), "--kind", "postclose", "--session", SESSION]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "settled"
