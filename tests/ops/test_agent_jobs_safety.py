"""Offline production handoff failures: leases, backups and crash reconciliation."""
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from quant.core import paths
from quant.core.agent_contracts import canonical_hash
from quant.core.io_json import atomic_write_json, read_json
from quant.ops import agent_jobs as jobs, agent_runtime as runtime
from tests.ops.test_agent_jobs import capture, record as job_record, settings
from tests.ops.test_agent_submit import bind_review, record, writer  # noqa: F401 — synthetic fixtures


def test_concurrent_normalized_card_identity_cannot_start_second_capture(tmp_path):
    entered, release = threading.Event(), threading.Event()
    calls = []

    def analyze(*args, **kwargs):
        calls.append("analysis")
        entered.set()
        assert release.wait(5)
        out = job_record()
        out["result"]["proposal"] = None
        return out

    options = {"routes": settings(), "run_dir": tmp_path, "control_dir": tmp_path / "control"}
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(jobs.run_card, "DEMO", "watchlist", capture_fn=capture,
                            run_role_fn=analyze, **options)
        assert entered.wait(5)
        try:
            second = jobs.run_card(" demo ", "watchlist", capture_fn=lambda **kw: pytest.fail(
                "duplicate capture started despite active card lease"), **options)
            assert second["status"] == "busy"
        finally:
            release.set()
        assert first.result()["status"] == "needs_review"
    assert calls == ["analysis"]


def test_failed_model_never_submits_and_releases_business_lease(tmp_path):
    def timed_out(*args, **kwargs):
        return {**job_record(), "status": "failed", "result": None,
                "attempts": [{"status": "timeout"}]}

    out = jobs.run_card("DEMO", "watchlist", apply=True, routes=settings(), run_dir=tmp_path,
                        control_dir=tmp_path / "control", capture_fn=capture, run_role_fn=timed_out,
                        submit_fn=lambda *a, **k: pytest.fail("failed inference submitted"))
    assert out["status"] == "failed"
    assert not (tmp_path / "card-backups").exists()
    lease = jobs.acquire_job(tmp_path / "control", "technical:watchlist:DEMO", timeout_s=10)
    assert lease["status"] == "acquired"
    jobs.release_job(tmp_path / "control", lease["lease_id"])


@pytest.mark.parametrize("failure", ["lease_lost", "backup_failed"])
def test_apply_stops_before_writer_when_business_lease_or_backup_fails(tmp_path, monkeypatch, failure):
    calls = []
    if failure == "lease_lost":
        monkeypatch.setattr(jobs, "owns_job", lambda *a, **k: False)
    else:
        real_atomic = jobs.atomic_write_json

        def failing_backup(path, value):
            if path.parent.name == "card-backups":
                raise OSError("synthetic backup fsync failed")
            real_atomic(path, value)

        monkeypatch.setattr(jobs, "atomic_write_json", failing_backup)

    def submit(run_id, *, apply, **kwargs):
        calls.append(apply)
        assert not apply, "state submission started without its prerequisites"
        return {"status": "dry_run", "can_apply": True}

    out = jobs.run_card("DEMO", "watchlist", apply=True, routes=settings(), run_dir=tmp_path,
                        control_dir=tmp_path / "control", capture_fn=capture,
                        run_role_fn=job_record, submit_fn=submit)
    assert out["status"] == "failed" and calls == [False]
    assert not list((tmp_path / "card-backups").glob("*.json"))
    lease = jobs.acquire_job(tmp_path / "control", "technical:watchlist:DEMO", timeout_s=10)
    assert lease["status"] == "acquired"
    jobs.release_job(tmp_path / "control", lease["lease_id"])


@pytest.mark.parametrize("crash_at,expected", [("before_state_write", "no_effect"),
                                              ("after_state_write", "applied")])
def test_prepared_crash_reconciles_without_retrying_or_rewriting_state(
        record, writer, monkeypatch, tmp_path, crash_at, expected):
    """Actual old merger/state lock and tmp card; crash bypasses Exception handlers."""
    policy = {"synthetic": "existing deterministic policy"}
    monkeypatch.setattr(runtime, "policy_context", lambda role: policy)
    record.update(run_id="a" * 32, job_key="b" * 64, status="succeeded", policy_hash=canonical_hash(policy))
    bind_review(record)
    root = tmp_path / "runs-artifacts"
    run_path = runtime._run_path(root, record["run_id"])
    atomic_write_json(run_path, record)
    atomic_write_json(root / "jobs" / f"{record['job_key']}.json",
                      {"run_id": record["run_id"], "status": "complete", "lease_until": 0})
    monkeypatch.setattr(paths, "POSITIONS_PATH", writer.POSITIONS_PATH)
    monkeypatch.setattr(paths, "WATCHLIST_PATH", writer.WATCHLIST_PATH)
    monkeypatch.setattr(paths, "STATE_LOCK_PATH", tmp_path / ".state.lock")
    before = (writer.POSITIONS_PATH.read_bytes(), writer.WATCHLIST_PATH.read_bytes())
    original_main, calls = writer.main, []

    def interrupted_main(argv=None, *, precommit_check=None):
        calls.append(True)

        def prepared_then_crash(receipt):
            precommit_check(receipt)
            # The audit must already be durable before any target-state effect.
            saved = read_json(run_path)["submission"]
            assert saved["status"] == "applying"
            assert saved["prepared"]["before_revision"] == receipt["before_revision"]
            assert saved["prepared"]["after_revision"] == receipt["after_revision"]
            if crash_at == "before_state_write":
                raise KeyboardInterrupt("synthetic crash before atomic state replace")

        original_main(argv, precommit_check=prepared_then_crash)
        raise KeyboardInterrupt("synthetic crash after atomic state replace")

    monkeypatch.setattr(writer, "main", interrupted_main)
    with pytest.raises(KeyboardInterrupt, match="synthetic crash"):
        runtime.submit_run(record["run_id"], apply=True, run_dir=root)
    after_crash = (writer.POSITIONS_PATH.read_bytes(), writer.WATCHLIST_PATH.read_bytes())
    assert (after_crash == before) is (crash_at == "before_state_write")
    with pytest.raises(ValueError, match="uncertain"):
        runtime.submit_run(record["run_id"], apply=True, run_dir=root)

    receipt = runtime.reconcile_run(record["run_id"], run_dir=root)
    assert receipt["status"] == expected
    assert receipt["writes_performed"] is False and receipt["retry_authorized"] is False
    assert (writer.POSITIONS_PATH.read_bytes(), writer.WATCHLIST_PATH.read_bytes()) == after_crash
    assert len(calls) == 1
    if expected == "applied":
        assert runtime.submit_run(record["run_id"], apply=True, run_dir=root) == receipt
        assert len(calls) == 1


@pytest.mark.parametrize("field,value", [("run_id", "c" * 32), ("ticker", "OTHER"),
                                        ("bucket", "positions"), ("bucket", "unknown"),
                                        ("before_revision", "d" * 64), ("proposal_hash", "d" * 64),
                                        ("policy_hash", "d" * 64), ("review_run_id", "wrong-review")])
def test_reconcile_rejects_receipt_not_bound_to_original_run_before_state_read(
        record, monkeypatch, tmp_path, field, value):
    record.update(run_id="a" * 32)
    bind_review(record)
    data = record["snapshot"]["data"]
    prepared = {"run_id": record["run_id"], "ticker": data["ticker"], "bucket": data["bucket"],
                "before_revision": data["card_revision"], "after_revision": "e" * 64,
                "proposal_hash": canonical_hash(record["result"]), "policy_hash": record["policy_hash"],
                "review_run_id": record["review"]["run_id"]}
    prepared[field] = value
    record["submission"] = {"status": "applying", "prepared": prepared}
    run_path = runtime._run_path(tmp_path, record["run_id"])
    atomic_write_json(run_path, record)
    original = run_path.read_bytes()
    monkeypatch.setattr(paths, "STATE_LOCK_PATH", tmp_path / "must-not-touch-state.lock")
    monkeypatch.setattr(paths, "POSITIONS_PATH", tmp_path / "no-positions.json")
    monkeypatch.setattr(paths, "WATCHLIST_PATH", tmp_path / "no-watchlist.json")
    with pytest.raises(ValueError, match="not bound to the original reviewed run"):
        runtime.reconcile_run(record["run_id"], run_dir=tmp_path)
    assert not paths.STATE_LOCK_PATH.exists(), "invalid receipt reached the state-read boundary"
    assert run_path.read_bytes() == original
