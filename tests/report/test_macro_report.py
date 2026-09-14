"""Causal reports preserve uncertainty, source bindings and initial/follow-up scope."""
from copy import deepcopy
import json

from quant.core.agent_contracts import validate_result
from quant.report.macro_report import build_digest, main, render
from tests.core.test_macro_analysis import case, chain


def record():
    s, p = case()
    p["macro_diagnosis"]["causal_hypotheses"] = [chain()]
    return {"role": "macro", "status": "succeeded", "run_id": "synthetic", "result": validate_result("macro", p, snapshot=s)}


def test_report_keeps_unknown_synthesis_conditional_feedback_and_evidence():
    source = record()
    original = deepcopy(source)
    digest = build_digest(source)
    report = render(digest)
    assert "未知" in report and "主假设：未选定" in report
    assert "保证金约束" in report and "竞争解释" in report and "证伪条件" in report
    assert "price-1" in report and "receipt-1" in report
    assert "核验截止：未核实日期" in report
    assert digest["independent_evidence"] is False
    digest["hypotheses"][0]["feedback"]["trigger"] = "mutated"
    assert source == original


def test_failure_or_legacy_does_not_reuse_an_old_successful_result():
    source = record()
    source["status"] = "failed"
    assert build_digest(source)["status"] == "unavailable"
    assert "不可用" in render(build_digest(source))
    source["status"] = "succeeded"
    source["result"]["schema_version"] = "1.2.0"
    assert build_digest(source)["status"] == "unavailable"


def test_downstream_index_does_not_duplicate_complete_peer_chains():
    source = record()
    compact = build_digest(source, compact=True)
    assert "hypotheses" not in compact and "market_structure" not in compact
    assert compact["hypothesis_index"][0]["id"] == source["result"]["macro_diagnosis"]["causal_hypotheses"][0]["id"]
    assert compact["synthesis"]["status"] == "unknown"
    assert "not additional evidence" in compact["detail_source"]


def test_cli_reads_both_initial_and_followup_without_overwriting_initial(tmp_path, capsys):
    initial, followup = record(), record()
    followup["result"]["summary"] = "Synthetic follow-up <script>"
    path = tmp_path / "workflow.json"
    path.write_text(json.dumps({"reports": {"macro": initial}, "followups": {"macro": followup}}))
    before = path.read_bytes()
    assert main([str(path)]) == 0
    text = capsys.readouterr().out
    assert "未重算下游估值" in text and "&lt;script&gt;" in text
    assert "<script>" not in text
    assert path.read_bytes() == before
