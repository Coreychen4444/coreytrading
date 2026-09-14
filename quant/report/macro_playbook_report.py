"""事件剧本渲染：Playbook JSON → 固定版式的 Markdown 段 + 发布用 scenarios。

只读、零模型调用、零授权。雷达 / 事件复评在写报告前跑：

    python3 quant/report/macro_playbook_report.py <playbook.json>                 # 校验 + 打印 Markdown
    python3 quant/report/macro_playbook_report.py <playbook.json> --json          # 校验结果 JSON
    python3 quant/report/macro_playbook_report.py <playbook.json> --schema-scenarios out.json
    python3 quant/report/macro_playbook_report.py --horizons trade/intelligence/macro/current.json

版式固定（读者永远在同一个位置找同一件事）：

    一句话判断 → 矛盾 → 三条路径（已计价 / 我预测 / 我认为损害最小）→ 冻结基准表
    → 联合条件 → 每个情景一张卡（结果条件 / 政策回应 / 第一根 / 第二根 / 谁受伤 / 滑向）
    → 判别线位表 → 剩余分支 → 组合含义 → 未知

`render_horizons()` 把 current.json 的三个期限压成一张窄表，给固定轮报告用；
schema 枚举在这里翻成人话，正文不再出现 `mixed_transition` 这类内部词。
"""
from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import argparse
import json

from quant.core.macro_playbook import (
    Level, Playbook, from_dict, to_schema_scenarios, validate,
)

RISK_ICON = {"supportive": "🟢", "neutral": "⚪", "adverse": "🔴", "crisis": "🚨",
             "mixed": "🟡", "unknown": "⚫"}
RISK_CN = {"supportive": "支持", "neutral": "中性", "adverse": "不利", "crisis": "危机",
           "mixed": "冲突", "unknown": "未知"}
POLICY_CN = {"hold": "按兵不动", "one_and_done": "一次调整后看数据", "continued": "连续路径",
             "cut": "降息", "other": "其他"}
REGIME_CN = {
    "mixed_transition": "混合过渡", "stagflation": "滞胀候选", "policy_tightening": "政策收紧",
    "disinflationary_growth": "去通胀增长", "growth_scare": "增长担忧", "credit_stress": "信用压力",
    "reflation": "再通胀", "soft_landing": "软着陆", "recession": "衰退", "easing": "政策宽松",
}
STAGE_CN = {
    "stable": "稳定", "termination_warning": "终止预警", "clearance_interval": "空档期，等下一证据",
    "candidate_phase": "候选，待验证", "confirmed_phase": "已确认", "failed_transition": "转移失败",
}
HORIZON_CN = {"event_window": "事件窗口", "short_1_10d": "1–10 交易日", "medium_1_3m": "1–3 个月"}


def _cell(text) -> str:
    return str(text if text not in (None, "") else "—").replace("|", "／").replace("\n", " ")


def _num(value) -> str:
    return "—" if value is None else f"{value:g}"


def _level_row(lv: Level) -> str:
    return f"| {_cell(lv.instrument)} | {lv.value:g}{_cell(lv.unit)} | {_cell(lv.meaning)} | {_cell(lv.source)}，{_cell(lv.as_of)} | {_cell(lv.review_by)} |"


def render(p: Playbook, *, heading_level: int = 2) -> str:
    h = "#" * heading_level
    hh = "#" * (heading_level + 1)
    lines: list[str] = []
    lines += [f"{h} 事件剧本｜{p.name}", "",
              f"**{p.verdict}**", "",
              f"发布 {p.scheduled_at_et}（ET）；剧本写于 {p.authored_at}。", "",
              f"{hh} 矛盾", "", p.tension, "",
              f"{hh} 三条路径", "",
              "| | |", "|---|---|",
              f"| 市场已计价 | {_cell(p.priced_path)} |",
              f"| 我预测央行会做 | {_cell(p.predicted_path)} |",
              f"| 我认为损害最小 | {_cell(p.preferred_path)} |", ""]

    lines += [f"{hh} 冻结基准", "", "| 指标 | 前值 | 共识 | 分项预估 | 来源 |", "|---|---|---|---|---|"]
    for b in p.baselines:
        src = f"{_cell(b.source)}，{_cell(b.as_of)}" + (f"；{_cell(b.note)}" if b.note else "")
        lines.append(f"| {_cell(b.name)}（{_cell(b.unit)}） | {_num(b.prior)} | {_num(b.consensus)} | {_num(b.estimate)} | {src} |")
    lines.append("")

    if p.joint_conditions:
        lines += [f"{hh} 联合条件", ""]
        for jc in p.joint_conditions:
            rule = "同时成立" if jc.rule == "all" else "任一成立"
            lines.append(f"**{jc.name}** = 以下{len(jc.parts)}件事{rule}" + (f"（{jc.note}）" if jc.note else "") + "：")
            lines += [f"{i}. {part}" for i, part in enumerate(jc.parts, 1)]
            lines.append("")

    lines += [f"{hh} 情景", ""]
    for i, s in enumerate(p.scenarios, 1):
        icon = RISK_ICON.get(s.risk_environment, "")
        prob = ("概率未给：" + s.probability_basis) if s.probability is None else (
            f"概率 {s.probability[0]:g}–{s.probability[1]:g}%（{s.probability_basis}）")
        paths = " vs ".join(POLICY_CN.get(x, x) for x in s.policy_paths)
        lines += [f"**（{i}）{s.label}** {icon} {RISK_CN.get(s.risk_environment, s.risk_environment)}", "",
                  f"结果条件：{s.condition}", "",
                  f"政策回应（{paths}）：{s.policy_note}", "",
                  f"第一根（{s.first_leg.window}）：{s.first_leg.expected} 看 {s.first_leg.check}。", "",
                  f"第二根（{s.second_leg.window}）：{s.second_leg.expected}",
                  f"- 接受 = {s.second_leg.accepted_if}",
                  f"- 拒绝 = {s.second_leg.rejected_if}", "",
                  "谁受伤 / 被迫调仓："]
        lines += [f"- {x}" for x in s.hurt]
        target = "剩余分支" if s.flip_to == "residual" else f"情景 {s.flip_to}"
        lines += ["", f"滑向{target}：{s.flip_if}", ""]
        if s.portfolio_note:
            lines += [f"组合含义：{s.portfolio_note}", ""]
        lines += [prob, ""]

    all_levels = [*p.shared_levels, *(lv for s in p.scenarios for lv in s.levels)]
    if all_levels:
        lines += [f"{hh} 判别线位", "", "| 品种 | 线位 | 过线意味着 | 出处 | 复核 |", "|---|---|---|---|---|"]
        lines += [_level_row(lv) for lv in all_levels]
        lines.append("")

    lines += [f"{hh} 剩余分支", "", p.residual, "",
              f"{hh} 组合含义", "", p.portfolio, ""]
    if p.unknowns:
        lines += [f"{hh} 仍未知", ""]
        lines += [f"- {u}" for u in p.unknowns]
        lines.append("")
    lines.append("宏观方向先验，不是交易授权。")
    return "\n".join(lines).rstrip() + "\n"


def render_horizons(current: dict) -> str:
    """current.json 三个期限 → 一张窄表。只翻译枚举，不改判断。"""
    horizons = current.get("horizons") or {}
    rows = ["| 期限 | 当前检验 | 方向 | 阶段 | 命题 | 截止 |", "|---|---|---|---|---|---|"]
    for key in ("event_window", "short_1_10d", "medium_1_3m"):
        hz = horizons.get(key)
        if not hz:
            continue
        cand = hz.get("next_candidate_regime")
        testing = REGIME_CN.get(cand, cand) if cand else REGIME_CN.get(hz.get("regime"), hz.get("regime"))
        prior = hz.get("directional_prior")
        conf = {"low": "低", "medium": "中", "high": "高"}.get(hz.get("confidence"), hz.get("confidence"))
        windows = {c.get("window") for c in (hz.get("confirmation_conditions") or []) if c.get("window")}
        rows.append("| " + " | ".join(_cell(x) for x in (
            HORIZON_CN.get(key, key), testing,
            f"{RISK_ICON.get(prior, '')} {RISK_CN.get(prior, prior)}（{conf}）",
            STAGE_CN.get(hz.get("transition_stage"), hz.get("transition_stage")),
            hz.get("thesis"), "；".join(sorted(windows)))) + " |")
    return "\n".join(rows) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("playbook", nargs="?", type=Path, help="playbook JSON（作者格式）")
    parser.add_argument("--json", action="store_true", help="只输出校验结果 JSON")
    parser.add_argument("--schema-scenarios", type=Path, help="把情景编译成 macro-state scenarios 写到此文件")
    parser.add_argument("--horizons", type=Path, help="渲染 current.json 的三期限窄表")
    parser.add_argument("--heading-level", type=int, default=2)
    args = parser.parse_args(argv)

    if args.horizons:
        print(render_horizons(json.loads(args.horizons.read_text(encoding="utf-8"))), end="")
        if not args.playbook:
            return 0
    if not args.playbook:
        parser.error("playbook path or --horizons required")

    try:
        playbook = from_dict(json.loads(args.playbook.read_text(encoding="utf-8")))
    except (ValueError, TypeError, OSError) as exc:
        print(json.dumps({"ok": False, "errors": [str(exc)], "path": str(args.playbook)}, ensure_ascii=False, indent=2))
        return 1
    errors = validate(playbook)
    if args.json or errors:
        print(json.dumps({"ok": not errors, "errors": errors, "path": str(args.playbook),
                          "scenarios": [s.id for s in playbook.scenarios],
                          "authority": "macro_prior_only", "network_send": False}, ensure_ascii=False, indent=2))
        if errors:
            return 1
    if args.schema_scenarios:
        args.schema_scenarios.parent.mkdir(parents=True, exist_ok=True)
        args.schema_scenarios.write_text(json.dumps(to_schema_scenarios(playbook), ensure_ascii=False, indent=2) + "\n",
                                         encoding="utf-8")
    if not args.json:
        print(render(playbook, heading_level=args.heading_level), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
