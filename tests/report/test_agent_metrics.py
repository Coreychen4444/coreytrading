"""report/agent_metrics —— 领域 Agent 层周基线: 只读 var/agent-runs 收据的读时汇总 (合成数据, 零真实文件)。"""
import json
from datetime import datetime, timezone

import pytest

from quant.report import agent_metrics as am

NOW = datetime(2026, 9, 10, 2, 0, tzinfo=timezone.utc)  # SGT 09-10 10:00


def _run(run_id, *, role="technical", status="succeeded", provider="claude", started="2026-09-08T20:14:37+00:00",
         prompt_bytes=190, estimated=None, attempts=None, transport=None, unknowns=(), error=None):
    im = {"prompt_utf8_bytes": prompt_bytes, "schema_utf8_bytes": 10}
    if estimated is not None:
        im["estimated_tokens"] = estimated
    d = {
        "run_id": run_id, "role": role, "status": status, "provider": provider,
        "started_at": started, "finished_at": started, "input_metrics": im,
        "attempts": attempts if attempts is not None else [
            {"provider": provider, "model": "m", "status": "succeeded", "elapsed_s": 10.5}],
        "result": {"unknowns": list(unknowns), "observations": [], "hypotheses": [], "risks": [], "requests": []},
    }
    if transport:
        d["execution_transport"] = transport
    if error:
        d["error"] = error
    return d


def _card(ticker, status, *, error=None, reason=None, run_id=None):
    d = {"job_kind": "card", "ticker": ticker, "bucket": "watchlist", "status": status,
         "started_at": "2026-09-08T20:23:19+00:00", "finished_at": "2026-09-08T20:29:44+00:00"}
    if error:
        d["error"] = error
    if reason:
        d["reason"] = reason
    if run_id:
        d["run_id"] = run_id
    return d


def _job(job_id, kind, *, started="2026-09-08T20:14:35+00:00", finished="2026-09-08T20:44:35+00:00",
         reports=None, cards=(), pending=(), session="2026-09-08", status="partial"):
    return {"job_id": job_id, "job_kind": kind, "status": status, "started_at": started, "finished_at": finished,
            "session": session, "inputs": {"payload_bytes": 1}, "reports": reports or {},
            "cards": list(cards), "pending_cards": list(pending), "total_calls": 1}


def _write(tmp_path, jobs=(), runs=()):
    (tmp_path / "task-jobs").mkdir(exist_ok=True)
    (tmp_path / "runs").mkdir(exist_ok=True)
    for j in jobs:
        (tmp_path / "task-jobs" / f"{j['job_id']}.json").write_text(json.dumps(j))
    for r in runs:
        (tmp_path / "runs" / f"{r['run_id']}.json").write_text(json.dumps(r))
    return tmp_path


def test_window_filters_by_started_at_and_rows_sorted(tmp_path):
    jobs = [
        _job("late00000000", "chart", started="2026-09-09T20:43:21+00:00", finished="2026-09-09T20:50:00+00:00"),
        _job("old000000000", "briefing", started="2026-08-20T10:00:00+00:00", finished="2026-08-20T10:30:00+00:00"),
        _job("early0000000", "postclose"),
        _card("BE", "applied"),  # 独立 card 文件不算轮
    ]
    jobs[-1]["job_id"] = "cardfile0000"
    rep = am.collect(_write(tmp_path, jobs), days=7, now=NOW)
    assert [r["job_id"] for r in rep["rounds"]] == ["early000", "late0000"]
    assert rep["rounds"][0]["duration_s"] == 1800.0
    assert set(rep["days"]) == {"2026-09-09", "2026-09-10"}  # SGT 日: 20:14Z 09-08 → 09-09 04:14 SGT
    assert rep["totals"]["rounds"] == 2 and rep["skipped_files"] == 0


@pytest.mark.parametrize("text,cls", [
    ("provider_session_limit", "quota"),
    ("global_model_limit:window_budget", "quota"),
    ("HTTP 429 rate_limit", "quota"),
    ("MarketSessionError: exchange-calendars dependency unavailable; no weekday fallback", "environment"),
    ("SubmitError: --next-regime: cap must use the existing binary policy", "submit_contract"),
    ("SubmitError: revision conflict: CRWV in watchlist changed since proposal", "revision_conflict"),
    ("timeout", "timeout"),
    ("model_abstained", "model_abstained"),
    ("provider_exit_1", "other"),
    ("", "other"),
])
def test_classify_failure_each_class(text, cls):
    assert am.classify_failure(text) == cls


def test_round_failure_classes_fall_back_to_linked_run(tmp_path):
    runs = [
        _run("r-quota", status="failed", provider=None, attempts=[
            {"provider": "claude", "model": "m", "status": "failed", "elapsed_s": 2.0, "error": "provider_session_limit"}]),
        _run("r-timeout", status="failed", provider=None, attempts=[
            {"provider": "claude", "model": "m", "status": "timeout", "elapsed_s": 600.2, "error": "timeout"}]),
        _run("r-budget", status="failed", provider=None, attempts=[], error="global_model_limit:window_budget"),
        _run("r-ok"),
    ]
    cards = [
        _card("A", "failed", run_id="r-quota"),                      # card.error 空 → run attempts
        _card("B", "failed", run_id="r-timeout"),
        _card("C", "failed", run_id="r-budget"),                     # 0 attempts → run.error
        _card("D", "failed", error="MarketSessionError: x"),
        _card("E", "failed", error="SubmitError: revision conflict: E changed"),
        _card("F", "failed", error="SubmitError: --next-regime.zones: missing"),
        _card("G", "needs_review", reason="model_abstained", run_id="r-ok"),
        _card("H", "applied", run_id="r-ok"),
    ]
    job = _job("j00000000000", "postclose", cards=cards, pending=[{"ticker": "Z", "bucket": "watchlist", "reasons": ["pending_repaint"]}])
    rep = am.collect(_write(tmp_path, [job], runs), days=7, now=NOW)
    row = rep["rounds"][0]
    assert row["cards"] == {"targets": 9, "applied": 1, "failed": 6, "needs_review": 1, "pending": 1}
    assert row["failure_classes"] == {
        "environment": 1, "model_abstained": 1, "quota": 2, "revision_conflict": 1, "submit_contract": 1, "timeout": 1}
    assert rep["totals"]["sla_hit_rate"] == pytest.approx(1 / 9, abs=1e-4)  # 落库四舍五入到 4 位
    assert rep["totals"]["cards_failed"] == 6


def test_token_estimation_fallback_and_by_provider(tmp_path):
    runs = [
        _run("r1", provider="claude", prompt_bytes=1900),                 # 无 estimated → 1900/1.9 = 1000
        _run("r2", provider="codex", prompt_bytes=1900, estimated=777),   # 有 estimated 用它
        _run("r3", provider="codex", prompt_bytes=380, attempts=[
            {"provider": "codex", "status": "failed", "elapsed_s": 1}, {"provider": "codex", "status": "succeeded", "elapsed_s": 2}]),
    ]
    assert am.estimate_tokens(runs[0]) == 1000 and am.estimate_tokens(runs[1]) == 777
    assert am.estimate_tokens({"input_metrics": {}}) is None
    rep = am.collect(_write(tmp_path, [], runs), days=7, now=NOW)
    t = rep["totals"]
    assert t["model_calls"] == 4 and t["prompt_bytes_total"] == 4180 and t["estimated_tokens_total"] == 1977
    assert t["by_provider"] == {"claude": {"calls": 1, "estimated_tokens": 1000}, "codex": {"calls": 3, "estimated_tokens": 977}}
    assert [p["run_id"] for p in t["largest_prompts"]] == ["r1", "r2", "r3"]
    assert t["largest_prompts"][0] == {"run_id": "r1", "role": "technical", "bytes": 1900}


def test_role_rows_reuse_flag_transport_and_unknowns(tmp_path):
    runs = [
        _run("r-tech", role="technical", transport="desktop", unknowns=["a", "b"], prompt_bytes=3800, attempts=[
            {"provider": "claude", "status": "failed", "elapsed_s": 1.5}, {"provider": "claude", "status": "succeeded", "elapsed_s": 20.0}]),
        _run("r-macro", role="macro", provider="codex", unknowns=["x"], started="2026-09-07T10:00:00+00:00"),
    ]
    reports = {
        "technical": {"run_id": "r-tech", "status": "succeeded", "provider": "claude", "summary": "s", "unknowns": ["a", "b"]},
        "macro": {"run_id": "r-macro", "status": "succeeded", "provider": "codex", "summary": "s", "unknowns": ["x"],
                  "reused_from_run_id": "r-macro"},
    }
    rep = am.collect(_write(tmp_path, [_job("j00000000000", "briefing", reports=reports)], runs), days=7, now=NOW)
    roles = rep["rounds"][0]["roles"]
    assert list(roles) == ["macro", "technical"]
    assert roles["technical"] == {"status": "succeeded", "provider": "claude", "transport": "desktop", "prompt_bytes": 3800,
                                  "estimated_tokens": 2000, "elapsed_s": 21.5, "unknowns": 2, "reused": False}
    assert roles["macro"]["reused"] is True and roles["macro"]["transport"] == "cli"
    assert rep["totals"]["unknowns_total"] == 3


def test_sla_null_with_zero_targets(tmp_path):
    rep = am.collect(_write(tmp_path, [_job("j00000000000", "chart", status="completed")]), days=7, now=NOW)
    assert rep["rounds"][0]["cards"]["targets"] == 0
    assert rep["totals"]["sla_hit_rate"] is None
    assert rep["days"]["2026-09-09"]["sla_hit_rate"] is None


def test_unparseable_files_are_skipped_not_raised(tmp_path):
    _write(tmp_path, [_job("j00000000000", "chart", status="completed")], [_run("r1")])
    (tmp_path / "task-jobs" / "broken.json").write_text("{not json")
    (tmp_path / "runs" / "list.json").write_text("[1, 2]")
    rep = am.collect(tmp_path, days=7, now=NOW)
    assert rep["skipped_files"] == 2 and rep["totals"]["rounds"] == 1 and rep["totals"]["model_calls"] == 1


def test_missing_dir_is_empty_report(tmp_path):
    rep = am.collect(tmp_path / "nope", days=7, now=NOW)
    assert rep["rounds"] == [] and rep["days"] == {} and rep["totals"]["rounds"] == 0


def test_markdown_renders_both_tables(tmp_path):
    runs = [_run("r-tech", transport="desktop")]
    reports = {"technical": {"run_id": "r-tech", "status": "succeeded", "provider": "claude", "unknowns": [],
                             "reused_from_run_id": "r-tech"}}
    job = _job("abcdef123456", "postclose", reports=reports, cards=[_card("BE", "failed", error="SubmitError: x")])
    _write(tmp_path, [job], runs)
    (tmp_path / "runs" / "bad.json").write_text("nope")
    md = am.render_markdown(am.collect(tmp_path, days=7, now=NOW))
    assert "| 日 | 轮 | 调用 | prompt | tokens≈ | 卡 applied/targets | SLA | unknowns | provider |" in md
    assert "| job | kind | session | status | 时长 | roles | 卡 a/f/r/p | 失败类 |" in md
    assert "| 09-09 | 1 | 1 | 190 | 100 | 0/1 | 0% | 0 | claude:1 |" in md
    assert "| abcdef12 | postclose | 2026-09-08 | partial | 30m | tech✓↻d | 0/1/0/0 | submit_contract:1 |" in md
    assert "跳过无法解析文件 1 个" in md


def test_cli_json_and_markdown(tmp_path, capsys):
    _write(tmp_path, [_job("j00000000000", "chart", status="completed")])
    assert am.main(["--run-dir", str(tmp_path), "--days", "3650", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["totals"]["rounds"] == 1 and out["window"]["days"] == 3650
    assert am.main(["--run-dir", str(tmp_path), "--days", "3650"]) == 0
    assert "Agent 层周基线" in capsys.readouterr().out
