"""Independent daily valuation job: refresh, freeze, macro, calculate, review.

Artifacts are private research records. No pipeline, trading writers or courier.
Run with --run; --preview reads cached inputs without writes or model calls.
"""
from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import argparse
from datetime import datetime, timezone
import json
import uuid
from zoneinfo import ZoneInfo

from quant.core.io_json import atomic_write_json, read_json
from quant.core.paths import AGENT_CONTROL_DIR, AGENT_RUNS_DIR, VALUATION_RUNS_DIR
from quant.ops.agent_health import acquire_job, owns_job, release_job
from quant.ops.agent_inputs import capture_inputs
from quant.ops.agent_runtime import load_routes, run_role
from quant.ops.agent_workflow import run_workflow
from quant.report.valuation_report import fyi_map
from quant.valuation.assessment import build
from quant.valuation.universe import coverage_tickers

TASK = ("完成独立每日估值研究。宏观先从冻结的宏观资料、公司业务及融资资料提出有来源、"
        "单位、期限的情景；宿主计算后，估值角色核查并解释公允价值、12个月EPS与PE目标、"
        "1/5/20交易日短期研究估计及缺口。政策降息25bp不等于长端利率下降25bp。"
        "只引用宿主收据中的价格；缺数据明确未知。不得提出写卡、交易或发送消息。")


def refresh_inputs(tickers):
    """Refresh owned company/price caches; macro intelligence remains read-only."""
    from quant.valuation.fundamentals import refresh
    from quant.data.kline_cache import update_ticker
    out = {"fundamentals": {}, "prices": {}, "errors": {}}
    try:
        out["fundamentals"] = refresh(tickers)
        out["errors"].update({"fundamentals:" + t: e
                              for t, e in out["fundamentals"].get("errors", {}).items()})
    except Exception as exc:
        out["errors"]["fundamentals"] = f"{type(exc).__name__}: {exc}"
    for ticker in tickers:
        try:
            out["prices"][ticker] = update_ticker(ticker, 365)
        except Exception as exc:
            out["errors"]["prices:" + ticker] = f"{type(exc).__name__}: {exc}"
    return out


def run_daily(*, preview=False, retry=False, root=None, control_dir=None,
              run_dir=None, routes=None, now=None, universe_fn=None,
              refresh_fn=None, capture_fn=None, workflow_fn=None, run_role_fn=None):
    stamp = now or datetime.now(timezone.utc)
    if stamp.tzinfo is None:
        raise ValueError("daily run requires timezone-aware time")
    day = stamp.astimezone(ZoneInfo("Asia/Singapore")).date().isoformat()
    capture = capture_fn or capture_inputs
    if preview:
        frozen = capture(scope="portfolio", now=stamp)
        receipt = build(frozen["snapshot"]["data"], as_of=frozen["snapshot"]["as_of"])
        return {"status": "preview", "day_sgt": day, "authority": "analysis_only",
                "macro_research": "not_run", "inputs": frozen["summary"],
                "valuation_calculations": receipt, "fyi": fyi_map(receipt)}
    root = Path(root or VALUATION_RUNS_DIR)
    control = Path(control_dir or AGENT_CONTROL_DIR)
    lease = acquire_job(control, "valuation-daily", timeout_s=10800)
    if lease["status"] != "acquired":
        return {"status": "busy", "day_sgt": day, "reason": "valuation_already_running"}
    latest, daily = root / "latest.json", root / "days" / (day + ".json")
    job = None
    try:
        if daily.exists() and not retry:
            previous = read_json(daily)
            return {"status": "skipped", "day_sgt": day, "reason": "already_attempted_today",
                    "previous_status": previous["status"], "artifact": previous.get("artifact"),
                    "retry_requires": "--run --retry"}
        run_id = uuid.uuid4().hex
        directory = root / "runs" / day / run_id
        directory.mkdir(parents=True, mode=0o700)
        job = {"run_id": run_id, "day_sgt": day, "status": "running", "stage": "refresh",
               "authority": "analysis_only", "started_at": stamp.isoformat(),
               "artifact": str(directory / "job.json")}

        def save():
            for path in (directory / "job.json", daily, latest):
                atomic_write_json(path, job)
                path.chmod(0o600)

        save()  # A new attempt immediately replaces the current-status pointer.
        tickers = (universe_fn or coverage_tickers)()
        if not tickers:
            raise ValueError("empty_valuation_universe")
        job["refresh"] = (refresh_fn or refresh_inputs)(tickers)
        job["stage"] = "capture"
        save()
        # Cutoff follows refresh. Do not stamp newly fetched evidence into the past.
        frozen = capture(scope="portfolio", tickers=tickers)
        atomic_write_json(directory / "capture.json", frozen)
        job.update(stage="research", as_of=frozen["snapshot"]["as_of"], inputs=frozen["summary"])
        save()

        def dispatch(*args, **kwargs):
            return (run_role_fn or run_role)(*args, **kwargs, control_dir=control)

        workflow = (workflow_fn or run_workflow)(
            TASK, frozen["snapshot"], routes=routes or load_routes(),
            run_dir=Path(run_dir or AGENT_RUNS_DIR), run_role_fn=dispatch,
            role_snapshots=frozen["snapshots"], purpose="valuation")
        atomic_write_json(directory / "workflow.json", workflow)
        receipt = workflow["valuation_calculations"]
        lines = fyi_map(receipt)  # Validate hash/authority before publishing prices.
        if not owns_job(control, lease["lease_id"]):
            raise ValueError("valuation_lease_lost")
        atomic_write_json(directory / "valuation.json", receipt)
        report = (f"每日估值 · {day} SGT\n估值时点：{receipt['as_of']}\n"
                  "研究判断，零授权效力。条件情景与短期研究估计不代表已验证预测。\n\n"
                  + "\n\n".join(t + "\n" + line for t, line in lines.items()))
        (directory / "report.md").write_text(report + "\n")
        job.update(status=workflow["status"], stage="finished", total_calls=workflow["total_calls"],
                   report=str(directory / "report.md"), receipt=str(directory / "valuation.json"),
                   workflow=str(directory / "workflow.json"), receipt_hash=receipt["receipt_hash"],
                   coverage={"total": len(receipt.get("tickers", {})),
                             "computed": sum(r["status"] == "computed"
                                             for r in receipt.get("tickers", {}).values())})
        if job["status"] == "completed" and job["refresh"].get("errors"):
            job["status"] = "partial"
        return job
    except Exception as exc:
        if job is None:
            raise
        job.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        return job
    finally:
        try:
            if job is not None:
                job["finished_at"] = datetime.now(timezone.utc).isoformat()
                save()
        finally:
            release_job(control, lease["lease_id"])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--run", action="store_true", help="refresh caches and run independent research")
    mode.add_argument("--preview", action="store_true", help="cached calculations only; no writes/models")
    parser.add_argument("--retry", action="store_true", help="explicitly repeat today's attempt")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.retry and not args.run:
        parser.error("--retry requires --run")
    result = run_daily(preview=args.preview, retry=args.retry)
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return int(result["status"] in ("failed", "partial"))


if __name__ == "__main__":
    raise SystemExit(main())
