"""Independent daily valuation job: refresh, freeze, calculate, review.

There is no macro model stage (macro = code-rendered core/macro_lens;
its prior rides in every frozen capture; the risk-free rate reads the macro daily cache).

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

from quant.core.io_json import append_jsonl, atomic_write_json, read_json, iter_jsonl, state_lock
from quant.core.paths import (AGENT_CONTROL_DIR, AGENT_RUNS_DIR, VALUATION_RUNS_DIR, KLINE_CACHE_DIR,
                              VALUATION_EPS_MODELS_PATH, VALUATION_FORWARD_PATH)
from quant.core.market_sessions import latest_completed_session
from quant.core.valuation_contracts import finite
from quant.ops.agent_health import acquire_job, owns_job, release_job
from quant.ops.agent_inputs import capture_inputs
from quant.ops.agent_runtime import load_routes, run_role
from quant.ops.agent_workflow import run_workflow
from quant.report.valuation_report import coverage_summary, fyi_map, released_fyi
from quant.ops.valuation_release import production_release
from quant.valuation.assessment import build
from quant.valuation.model import publication_policy
from quant.valuation import eps_model, research_target
from quant.valuation.universe import coverage_tickers

TASK = ("完成独立每日估值研究。宿主计算后，估值角色核查并解释公允价值、12个月EPS与PE目标、"
        "1/5/20交易日短期研究估计及缺口。政策降息25bp不等于长端利率下降25bp。"
        "当前估值方法未通过验收，DCF和同路径EPS/PE仅供诊断，不得称为可用公允价或市场目标。"
        "以publication边界为准，重点检查经营/融资假设、独立预测依据与数据缺口。"
        "只引用宿主收据中的价格；缺数据明确未知。不得提出写卡、交易或发送消息。")


def price_cache_status(ticker, *, expected_session, cache_dir=KLINE_CACHE_DIR):
    """Zero appended bars is healthy only when the completed close is present."""
    path = Path(cache_dir) / (ticker + ".jsonl")
    rows = list(iter_jsonl(path)) if path.exists() else []
    last = max(rows, key=lambda r: str(r.get("time", "")), default={})
    day = str(last.get("time") or "")[:10] or None
    close = last.get("close")
    status = "current" if day == expected_session and finite(close) and close > 0 else "unavailable"
    return {"status": status, "expected_session": expected_session,
            "cached_session": day, "close": close if finite(close) else None,
            "basis": "latest_completed_XNYS_close", "source": str(path)}


def refresh_inputs(tickers):
    """Refresh owned company/price caches; macro intelligence remains read-only."""
    from quant.valuation.fundamentals import refresh
    from quant.data.kline_cache import update_ticker
    out = {"fundamentals": {}, "prices": {}, "price_status": {}, "errors": {}}
    try:
        out["fundamentals"] = refresh(tickers)
        out["errors"].update({"fundamentals:" + t: e
                              for t, e in out["fundamentals"].get("errors", {}).items()})
    except Exception as exc:
        out["errors"]["fundamentals"] = f"{type(exc).__name__}: {exc}"
    for ticker in tickers:
        try:
            out["prices"][ticker] = update_ticker(ticker, 365)
            expected = latest_completed_session(datetime.now(timezone.utc)).isoformat()
            status = price_cache_status(ticker, expected_session=expected)
            out["price_status"][ticker] = status
            if status["status"] != "current":
                out["errors"]["prices:" + ticker] = (
                    f"completed close unavailable: expected {expected}, cached {status['cached_session']}")
        except Exception as exc:
            out["errors"]["prices:" + ticker] = f"{type(exc).__name__}: {exc}"
    return out


def publication_snapshot(receipt, *, release_fn=None):
    """Select current exact releases bound to this calculation's company inputs."""
    publications = {}
    for ticker, row in receipt['tickers'].items():
        frozen_hash = (row.get('company_research') or {}).get('input_hash')
        publications[ticker] = {}
        for kind in ('fair_value', 'market_target_12m'):
            publications[ticker][kind] = ((release_fn or production_release)(
                ticker, kind, expected_input_hash=frozen_hash)
                if frozen_hash else {'status': 'withheld', 'ticker': ticker, 'kind': kind,
                                     'authority': 'analysis_only', 'reason': 'no_current_company_calculation'})
    counts = {kind: sum(r[kind]['status'] == 'released' for r in publications.values())
              for kind in ('fair_value', 'market_target_12m')}
    return publications, counts


def research_snapshot(receipt, frozen, *, research_fn=None, eps_models_path=None, write=True):
    """研究级 12 个月目标：与逐公司验收通道并存，失败只记错不拖垮日更。
    先按本轮冻结的基本面机械滚动精简 EPS 包（新财报出列），write=False（preview）只在内存里滚。"""
    try:
        valuation = frozen["snapshot"]["data"]["valuation"]
        path = Path(eps_models_path or VALUATION_EPS_MODELS_PATH)
        with state_lock():
            models, events = eps_model.roll_all(eps_model.load(path), valuation.get("fundamentals") or {})
            if events and write:
                eps_model.save(models, path)
        result = (research_fn or research_target.build_all)(receipt, valuation, eps_models=models)
        result["eps_model_rolls"] = events
        text = research_target.render(result)
        if events:
            text += "\n\nEPS 包财报后机械滚动（待复核）：" + "；".join(
                f"{t} {e[-1]['reported_end']} 营收实际/预测 {e[-1]['ratio']:.2f}" for t, e in sorted(events.items()))
        return result, text
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        return {"status": "failed", "error": error, "counts": {"ok": 0, "total": 0}}, f"研究级目标计算失败：{error}"


def run_daily(*, preview=False, retry=False, root=None, control_dir=None,
              run_dir=None, routes=None, now=None, universe_fn=None,
              refresh_fn=None, capture_fn=None, workflow_fn=None, run_role_fn=None, release_fn=None,
              research_fn=None, forward_path=None, eps_models_path=None):
    stamp = now or datetime.now(timezone.utc)
    if stamp.tzinfo is None:
        raise ValueError("daily run requires timezone-aware time")
    day = stamp.astimezone(ZoneInfo("Asia/Singapore")).date().isoformat()
    capture = capture_fn or capture_inputs
    if preview:
        frozen = capture(scope="portfolio", now=stamp)
        receipt = build(frozen["snapshot"]["data"], as_of=frozen["snapshot"]["as_of"])
        publications, counts = publication_snapshot(receipt, release_fn=release_fn)
        research, research_text = research_snapshot(receipt, frozen, research_fn=research_fn,
                                                    eps_models_path=eps_models_path, write=False)
        return {"status": "preview", "day_sgt": day, "authority": "analysis_only",
                "inputs": frozen["summary"],
                "valuation_calculations": receipt, "fyi": fyi_map(receipt),
                "publications": publications, "released_counts": counts,
                "research_targets": research, "research_text": research_text}
    # 前向账本跟着 root 走: 生产写 observations (随 state 提交), 显式 root (测试/演练) 写进 root, 不污染真账本。
    forward = Path(forward_path) if forward_path else (VALUATION_FORWARD_PATH if root is None
                                                        else Path(root) / "valuation_forward.jsonl")
    root_default = root is None
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
        coverage = coverage_summary(receipt)
        # The exact-release channel is distinct from diagnostic computation.
        # Re-read live trust, inputs, implementation, revocations and expiry;
        # bind accepted outputs to this run's frozen company packet as well.
        publications, counts = publication_snapshot(receipt, release_fn=release_fn)
        atomic_write_json(directory / 'publication.json', publications)
        research, research_text = research_snapshot(
            receipt, frozen, research_fn=research_fn,
            eps_models_path=eps_models_path or (None if root_default else root / "eps_models.json"))
        atomic_write_json(directory / 'research_targets.json', research)
        if research.get("tickers"):
            for entry in research_target.ledger_rows(research, run_id=run_id):
                append_jsonl(forward, entry)
        release_text = '\n\n'.join(released_fyi(selected) for values in publications.values()
                                   for selected in values.values() if selected['status'] == 'released')
        report = (f"每日估值 · {day} SGT\n估值时点：{receipt['as_of']}\n"
                  f"逐票状态：条件经营价值可审阅 {coverage['conditional_operating']}；"
                  f"旧经营路径不可估 {coverage['operating_path_unestimable']}；"
                  f"输入过期 {coverage['expired_inputs']}；"
                  f"获准发布条件公允价值 {counts['fair_value']}；"
                  f"12个月目标 {counts['market_target_12m']}。\n"
                  "未取得逐公司、逐输出验收的价格暂不发布；各股研究缺口见下。\n"
                  "研究判断，零授权效力。条件情景与短期研究估计不代表已验证预测。\n\n"
                  + research_text + "\n\n"
                  + ("获准输出（本次发布快照；当前有效性须通过 valuation_release.py status 核验）\n\n"
                     + release_text + "\n\n" if release_text else "")
                  + "\n\n".join(t + "\n" + line for t, line in lines.items()))
        (directory / "report.md").write_text(report + "\n")
        job.update(status=workflow["status"], stage="finished", total_calls=workflow["total_calls"],
                   report=str(directory / "report.md"), receipt=str(directory / "valuation.json"),
                   workflow=str(directory / "workflow.json"), receipt_hash=receipt["receipt_hash"],
                   publication=publication_policy(), coverage=coverage)
        job.update(publication_receipt=str(directory / 'publication.json'), released_counts=counts,
                   research_targets=str(directory / 'research_targets.json'),
                   research_counts=research.get("counts"))
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
