"""Read-only macro research digest; renders claims without inventing conclusions."""
from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import argparse
from copy import deepcopy
import json

from quant.core.macro_analysis import MACRO_RESULT_SCHEMA_VERSION


def build_digest(record, *, compact=False):
    """Project an already validated runtime record, preserving uncertainty.

    Failed or legacy records remain unavailable. This digest is neither another
    model judgment nor independent evidence. Follow-up reports are separate.
    """
    result = record.get("result") or {}
    digest = {"status": "unavailable", "run_id": record.get("run_id"), "authority": "analysis_only",
              "independent_evidence": False, "reason": "macro_result_missing_failed_or_legacy"}
    if (record.get("status") != "succeeded" or record.get("role") != "macro" or
            result.get("schema_version") != MACRO_RESULT_SCHEMA_VERSION):
        return digest
    diagnosis, structure = result["macro_diagnosis"], result["market_structure"]
    digest.update(status="available", reason=None, as_of=result["generated_at"],
                  expires_at=result["expires_at"], summary=result["summary"],
                  dominant_hypothesis_id=diagnosis["dominant_hypothesis_id"],
                  synthesis=deepcopy(diagnosis["synthesis"]),
                  hypotheses=deepcopy(diagnosis["causal_hypotheses"]),
                  market_structure=deepcopy(structure), unknowns=list(result["unknowns"]))
    if compact:
        # Original peer JSON already contains the complete chains. A navigation
        # index avoids sending the same long research twice to downstream LLMs.
        digest["hypothesis_index"] = [{"id": chain["id"], "shock": chain["shock"], "horizon": chain["horizon"],
                                       "upstream_status": chain["upstream"]["status"],
                                       "feedback_status": chain["feedback"]["status"], "due_at": chain["due_at"]}
                                      for chain in diagnosis["causal_hypotheses"]]
        digest["detail_source"] = "workflow_context.peer_reports: macro result; index is not additional evidence"
        digest.pop("hypotheses")
        digest.pop("market_structure")
    return digest


def _text(value):
    # Source/model prose remains text inside the host-owned report layout.
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", " ")


def _assessment(value):
    status = {"observed": "观测", "inferred": "推断", "unknown": "未知"}[value["status"]]
    refs = "、".join(value["evidence_refs"]) or "无"
    return f"{status}：{_text(value['finding'])}；局限：{_text(value['limitation'])}；来源：{_text(refs)}"


def render(digest):
    """Render the causal path, competing explanation and checks in one place."""
    if digest["status"] != "available":
        return "宏观研究不可用：本轮失败、缺失或历史契约不兼容。不得回退为中性或沿用旧结论。\n"
    lines = ["# 宏观因果与市场结构研究", "", _text(digest["summary"]), "",
             f"证据截止：{digest['as_of']}；结果有效期：{digest['expires_at']}。",
             "宏观方向先验，零交易授权。摘要与原始研究同源，不增加一份确认。", "",
             "综合：" + _assessment(digest["synthesis"]),
             "主假设：" + _text(digest["dominant_hypothesis_id"] or "未选定；保留竞争解释")]
    for chain in digest["hypotheses"]:
        up = chain["upstream"]
        lines += ["", f"## {_text(chain['id'])} · {_text(chain['shock'])}", "",
                  "上游候选：" + _assessment(up),
                  f"信息可知时间：{up['known_at'] or '未知'}；事件发生时间：{up['occurred_at'] or '未知'}。",
                  "事前基准：" + _assessment(up["baseline"]),
                  "预期差：" + _assessment(up["surprise"])]
        for response in chain["market_responses"]:
            lines += [f"价格角色 {_text(response['variable'])} / {response['role']}：" + _assessment(response),
                      f"观察窗口：{response['window_start'] or '未知'} → {response['window_end'] or '未知'}。"]
        lines += ["利率渠道：" + _text(chain["rate_channel"]), "现金流渠道：" + _text(chain["cash_flow_channel"]),
                  "风险补偿渠道：" + _text(chain["risk_premium_channel"]),
                  "条件反馈：" + _assessment(chain["feedback"]),
                  "参与者 / 约束 / 触发：" + " / ".join(_text(chain["feedback"][key]) for key in ("actor", "constraint", "trigger"))]
        for effect in chain["asset_implications"]:
            lines.append(f"资产 {_text(effect['asset'])} / {effect['channel']} / {effect['direction']}：" + _assessment(effect))
        lines += ["竞争解释：" + _text(chain["alternative"]), "确认条件：" + _text(chain["confirmation"]),
                  "证伪条件：" + _text(chain["invalidation"]), "下一判别观测：" + _text(chain["next_observation"]),
                  f"研究期限：{chain['horizon']}；核验截止：{chain['due_at'] or '未核实日期，按下一观测说明'}。"]
    lines += ["", "## 市场结构与缺口", ""]
    for key, assessment in digest["market_structure"]["dimensions"].items():
        lines.append(f"- {key}：" + _assessment(assessment))
    lines += ["", "宏观互动：" + _assessment(digest["market_structure"]["macro_interaction"]), ""]
    lines += ["- " + _text(value) for value in digest["unknowns"]]
    return "\n\n".join(lines).strip() + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("record", type=Path, help="saved runtime record or workflow JSON; no model calls")
    args = parser.parse_args(argv)
    document = json.loads(args.record.read_text())
    record = document.get("reports", {}).get("macro", document)
    print(render(build_digest(record)), end="")
    followup = document.get("followups", {}).get("macro")
    if followup:
        print("\n# 后续追问（未重算下游估值，不覆盖初始研究）\n")
        print(render(build_digest(followup)), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
