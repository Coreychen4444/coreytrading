"""Production domain jobs: frozen evidence, bounded collaboration and reviewed cards.

The desktop task is a scheduler and report courier. All domain dispatch happens
here through provider-neutral contracts. Nothing here places broker orders.
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
import uuid

from quant.core.io_json import atomic_write_json, read_json
from quant.core.paths import AGENT_CONTROL_DIR, AGENT_RUNS_DIR
from quant.ops.agent_health import acquire_job, owns_job, release_job, update_health
from quant.ops.agent_inputs import capture_inputs, repaint_targets
from quant.ops.agent_runtime import load_routes, run_role, submit_run
from quant.ops.agent_workflow import run_workflow

TASKS = {
    "briefing": "生成盘前领域复核：宏观只提供先验，技术检查价格结构，估值只给FYI，风控引用机械收据检查组合约束与未知项。不要提出跨多票写卡参数；待重画由独立单卡任务处理。",
    "postclose": "复核最新完整收盘后的价格结构与失效条件，指出需要重画的卡及证据缺口。保留退出与授权约束；此组合报告不直接提出写卡参数。",
    "weekly": "完成每周组合研究：汇总宏观先验、价格结构、估值证据与机械风控收据，列出分歧、风险和下周观察条件。业绩只引用已扣除外部现金流的指标；没有指标则明确未知。",
    "chart": "提供技术结构判读：形态作为假设并附失效条件，引用冻结日线与技术指标；不提出写卡、交易金额、股数或入场授权。",
}


def _stamp():
    return datetime.now(timezone.utc).isoformat()


def run_card(ticker: str, bucket: str, *, apply=False, routes=None, run_dir=None,
             control_dir=None, capture_fn=None, run_role_fn=None, submit_fn=None) -> dict:
    ticker = ticker.strip().upper()
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9.-]{0,31}", ticker) or bucket not in ("positions", "watchlist"):
        raise ValueError("invalid card identity")
    routes = routes or load_routes()
    root, control = Path(run_dir or AGENT_RUNS_DIR), Path(control_dir or AGENT_CONTROL_DIR)
    if apply and not routes["autopaint"]["apply"]:
        raise ValueError("autopaint apply is disabled in routing policy")
    lease = acquire_job(control, f"technical:{bucket}:{ticker}", timeout_s=7200)
    if lease["status"] != "acquired":
        return {"status": "busy", "ticker": ticker, "bucket": bucket,
                "reason": "same_card_already_running"}
    path = root / "task-jobs" / (uuid.uuid4().hex + ".json")
    job = {"job_kind": "card", "ticker": ticker, "bucket": bucket, "status": "running",
           "started_at": _stamp(), "pid": os.getpid(), "lease_until": lease.get("lease_until_ts"),
           "lease_id": lease["lease_id"]}
    try:
        atomic_write_json(path, job)
        capture = (capture_fn or capture_inputs)(tickers=[ticker], scope="card", bucket=bucket)
        snapshot = capture["snapshot"]
        if snapshot["data"]["bucket"] != bucket:
            raise ValueError("card membership changed before capture")
        market = capture["freshness"].get("market", {}).get(ticker, {})
        if market.get("status") != "current":
            job.update(status="needs_review", reason="completed_bars_not_current")
            return job
        task = ("为此单卡完成技术重画提案；每条线带完整收盘数据证据。保持当前授权与已有退出约束，"
                "财报后须同时更新thesis（只读情报归档、标FYI）。证据不足则明确弃权，"
                "不要仅为写入而重述旧卡。proposal只能包含受控writer白名单参数。")
        record = (run_role_fn or run_role)("technical", task, snapshot, routes=routes,
                                           run_dir=root, control_dir=control, review=True)
        job.update(status=record["status"], run_id=record["run_id"],
                   provider=record.get("provider"), snapshot_id=snapshot["snapshot_id"])
        proposal = (record.get("result") or {}).get("proposal")
        if record["status"] == "succeeded" and proposal:
            preview = (submit_fn or submit_run)(record["run_id"], run_dir=root, apply=False)
            job["preview"] = preview
            if not preview.get("can_apply"):
                job.update(status="needs_review", reason="submission_preview_blocked")
                return job
            if apply:
                if not owns_job(control, lease["lease_id"]):
                    raise ValueError("card business lease lost before submission")
                # Original card, evidence and policy are already immutable run artifacts.
                # The extra backup is deliberately only this target's before image.
                backup = root / "card-backups" / (record["run_id"] + ".json")
                atomic_write_json(backup, {"ticker": ticker, "bucket": bucket,
                                          "card": snapshot["data"]["card"],
                                          "revision": snapshot["data"]["card_revision"],
                                          "run_id": record["run_id"], "created_at": _stamp()})
                backup.chmod(0o600)
                job["submission"] = (submit_fn or submit_run)(record["run_id"], run_dir=root,
                                                               apply=True, automatic=True)
                job["status"] = job["submission"]["status"]
            elif not preview.get("can_apply"):
                job["status"] = "needs_review"
        elif not proposal and record["status"] == "succeeded":
            job.update(status="needs_review", reason="model_abstained",
                       unknowns=(record.get("result") or {}).get("unknowns", []))
        return job
    except Exception as exc:
        job.update(status="failed", error=type(exc).__name__ + ": " + str(exc))
        return job
    finally:
        job["finished_at"] = _stamp()
        try:
            atomic_write_json(path, job)
        finally:
            release_job(control, lease["lease_id"])


def run_job(kind: str, *, apply=False, routes=None, run_dir=None, control_dir=None,
            capture_fn=None, workflow_fn=None, run_role_fn=None, card_fn=None,
            scheduled=False, now=None) -> dict:
    if kind not in TASKS and kind != "heartbeat":
        raise ValueError("unknown domain job kind")
    if scheduled:
        from quant.core.market_sessions import scheduled_session_due
        due = scheduled_session_due(kind, now or datetime.now(timezone.utc))
        if not due["due"]:
            return {"status": "failed" if due["reason"].startswith("calendar_unavailable") else "skipped",
                    "job_kind": kind, "calendar": due}
        if kind == "chart":
            from quant.ops.agent_dependency import wait_for_job
            dependency = wait_for_job(Path(run_dir or AGENT_RUNS_DIR), "postclose", due["session"], timeout_s=1800)
            if dependency["status"] != "settled":
                return {"status": "failed", "job_kind": kind, "dependency": dependency}
    routes = routes or load_routes()
    root, control = Path(run_dir or AGENT_RUNS_DIR), Path(control_dir or AGENT_CONTROL_DIR)
    if kind == "heartbeat":
        return {"status": "completed", "job_kind": kind, "health": update_health(root)}
    lease = acquire_job(control, "domain-job:" + kind, timeout_s=10800)
    if lease["status"] != "acquired":
        return {"status": "busy", "job_kind": kind, "reason": "same_job_already_running"}
    job_id = uuid.uuid4().hex
    path = root / "task-jobs" / (job_id + ".json")
    job = {"job_id": job_id, "job_kind": kind, "status": "running", "started_at": _stamp(),
           "pid": os.getpid(), "stage": "capture", "lease_id": lease["lease_id"],
           "lease_until": lease.get("lease_until_ts")}
    try:
        atomic_write_json(path, job)
        capture = (capture_fn or capture_inputs)(scope=kind if kind != "chart" else "portfolio")
        job["inputs"] = capture["summary"]
        job["session"] = capture["summary"].get("latest_completed_session")
        source = root / "captures" / (job_id + ".json")
        atomic_write_json(source, capture["snapshot"])
        job.update(stage="research", snapshot_id=capture["snapshot"]["snapshot_id"])
        atomic_write_json(path, job)
        if kind in ("briefing", "weekly"):
            def dispatch(*args, **kwargs):
                return (run_role_fn or run_role)(*args, **kwargs, control_dir=control)
            workflow = (workflow_fn or run_workflow)(TASKS[kind], capture["snapshot"],
                                                     routes=routes, run_dir=root, run_role_fn=dispatch,
                                                     role_snapshots=capture["snapshots"])
            artifact = root / "workflows" / (job_id + ".json")
            atomic_write_json(artifact, workflow)
            job.update(status=workflow["status"], workflow_artifact=str(artifact),
                       reports={role: {"run_id": r.get("run_id"), "status": r["status"],
                                       "provider": r.get("provider"), "summary": (r.get("result") or {}).get("summary"),
                                       "unknowns": (r.get("result") or {}).get("unknowns", [])}
                                for role, r in workflow["reports"].items()},
                       total_calls=workflow["total_calls"])
        else:
            reports = {}
            for role in (("technical", "risk") if kind == "postclose" else ("technical",)):
                record = (run_role_fn or run_role)(role, TASKS[kind], capture["snapshots"][role],
                                                  routes=routes, run_dir=root, control_dir=control)
                reports[role] = {"run_id": record["run_id"], "status": record["status"],
                                 "provider": record.get("provider"), "summary": (record.get("result") or {}).get("summary"),
                                 "unknowns": (record.get("result") or {}).get("unknowns", [])}
            job.update(reports=reports, status="completed" if all(r["status"] == "succeeded" for r in reports.values()) else "partial")
        if apply and kind in ("postclose", "briefing"):
            job["stage"] = "reviewed_repaint"
            atomic_write_json(path, job)
            targets = repaint_targets(capture)
            results = []
            # A bound per scheduled run; the full queue stays visible in the receipt.
            for target in targets[:2]:
                if not owns_job(control, lease["lease_id"]):
                    raise ValueError("domain job lease lost")
                results.append((card_fn or run_card)(target["ticker"], target["bucket"], apply=True,
                                                     routes=routes, run_dir=root, control_dir=control))
            job["cards"] = results
            job["pending_cards"] = targets[2:]
            if job["pending_cards"] or any(r["status"] not in ("applied", "completed") for r in results):
                job["status"] = "partial"
        job["stage"] = "finished"
        return job
    except Exception as exc:
        job.update(status="failed", error=type(exc).__name__ + ": " + str(exc))
        return job
    finally:
        job["finished_at"] = _stamp()
        try:
            atomic_write_json(path, job)
        finally:
            release_job(control, lease["lease_id"])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)
    due = sub.add_parser("due")
    due.add_argument("--kind", required=True, choices=("heartbeat", "briefing", "postclose", "ladder", "chart", "weekly"))
    run = sub.add_parser("run")
    run.add_argument("--kind", required=True, choices=(*TASKS, "heartbeat"))
    run.add_argument("--apply", action="store_true")
    run.add_argument("--scheduled", action="store_true", help="enforce exchange calendar/time window before any work")
    run.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if args.command == "due":
        from quant.core.market_sessions import scheduled_session_due
        result = scheduled_session_due(args.kind, datetime.now(timezone.utc))
    else:
        result = run_job(args.kind, apply=args.apply, scheduled=args.scheduled)
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 1 if result.get("status") in ("failed", "partial", "needs_review") else 0


if __name__ == "__main__":
    raise SystemExit(main())
