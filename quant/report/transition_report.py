#!/usr/bin/env python3
"""转移达标条件 — 每笔 regime 转移「凭什么成立」的代码渲染收据。

立项: 需要明确知道转移达标的条件 —— 例如某票转移,
是由于 ×2 加什么类型的量腿。此前达标证据只活在 `transitions[].reason` 的中文
散文里, 且格式随对齐日变化 —— 最后一日过量腿只印一个数字, 早前某日过才多一段
「MM-DD streak第N日 X」。要回答「靠什么达标」得去解析句子, 而**简报里那段话是
会话散文渲染的**, 两代格式一旦漂移没人看得出来。

本模块只读 `regime_transition` 已经写好的结构化字段 (`confirm` / `volume`),
一个数字都不自己算 —— 渲染口径 = 判定口径。授权语义一律不产生:
gate 达标 ≠ 已授权入场, provisional 带补画前 entry_guard 不给 ladder。

Usage:
  python3 quant/report/transition_report.py --pipeline var/agent-runs/desktop/controllers/<id>/output.json
  cat output.json | python3 quant/report/transition_report.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 直接 `python3 quant/<层>/<模块>.py`

import argparse  # noqa: E402
import json  # noqa: E402
from typing import Dict, List, Optional  # noqa: E402

HEADER = "## 🔀 转移达标条件"
LEGEND = ("> 机械转移收据, 零授权效力。gate 达标 ≠ 已授权入场；provisional 带补画前 "
          "entry_guard 不给 ladder。数字全部引自 `regime_transition` 的判定字段, 渲染层不另算。")

ARROW = {"up": "⬆", "down": "⬇"}


def _num(x) -> Optional[float]:
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def _money(x) -> str:
    v = _num(x)
    return f"${v:,.2f}" if v is not None else "—"


def _g(x) -> str:
    v = _num(x)
    return f"{v:g}" if v is not None else "—"


def _md(date: Optional[str]) -> str:
    return (date or "")[5:] or "?"


def closes_line(confirm: Dict) -> str:
    """达标收盘 = 日期 + 收盘价逐条列出 (不再让人回去翻 kline 缓存)。"""
    rows = confirm.get("closes") or []
    if not rows:
        return "—"
    return " · ".join(f"{_md(r.get('date'))} {_money(r.get('close'))}" for r in rows)


def confirm_line(tr: Dict) -> str:
    c = tr.get("confirm") or {}
    mode = c.get("mode") or ""
    if mode == "fastlane":
        over, need = _num(c.get("over_n")), _num(c.get("threshold_n"))
        txt = (f"⚡快车道 — 首日收盘超 gate +{need:g}N 替代第二日确认 (实际超出 {over:.2f}N)"
               if over is not None and need is not None else "⚡快车道 — 幅度替代第二日确认")
        return txt + (f" · {c['domain']}" if c.get("domain") else "")
    if mode == "stop_break":
        return f"收盘 ×{c.get('days')} 日 破卡片 stop (退出侧, 量腿不适用)"
    if mode.startswith("close_x"):
        streak = c.get("streak")
        tail = f" · 连续站上 {streak} 日" if isinstance(streak, int) and streak else ""
        return f"收盘 ×{c.get('days')} 日 (非快车道){tail}"
    return "—"


def volume_line(tr: Dict) -> str:
    """量腿那一行 —— 「什么类型的量腿」在这里明确回答。"""
    v = tr.get("volume")
    if not v:
        c = tr.get("confirm") or {}
        return "不适用（退出侧）" if c.get("mode") == "stop_break" else "—"
    if v.get("mode") == "skipped" or not v.get("required"):
        return "卡片未设 `volume_ratio_min` — 整条量腿跳过"
    need, got = _num(v.get("required")), _num(v.get("actual"))
    verdict = "✓" if v.get("ok") else "✗"
    head = (f"RelVR {got:.2f} {'≥' if v.get('ok') else '<'} 阈值 {need:g} {verdict}"
            if got is not None and need is not None else f"阈值 {_g(need)} {verdict}")
    if v.get("mode") == "absolute_fallback":
        kind = f"**绝对量比兜底**（基准 {v.get('benchmark') or '?'} 当日缺失, 降级不静默）"
    elif v.get("mode") == "relative":
        kind = f"相对口径 · 基准 {v.get('benchmark')} 当日量比 {_num(v.get('benchmark_ratio')):.2f}"
        own = _num(v.get("own_ratio"))
        if own is not None:
            kind += f" · 个股自身量比 {own:.2f}"
    else:
        kind = "量比不可算（K 线不足）"
    day, nth = v.get("aligned_date"), v.get("aligned_streak_day")
    align = ""
    if day:
        align = f" · 对齐日 {_md(day)}"
        if isinstance(nth, int):
            align += f"（streak 第 {nth} 日）"
    return f"{head} · {kind}{align}"


def render_one(tr: Dict) -> List[str]:
    arrow = ARROW.get(tr.get("direction") or "", "•")
    level, ticker = _g(tr.get("level")), tr.get("ticker") or "?"
    head = f"**{ticker}** {arrow} {level}"
    if tr.get("direction") == "down":
        head += " 破 stop"
    if tr.get("to"):
        head += f" → {tr['to']}"
    if tr.get("fastlane"):
        head += "  ⚡"
    out = [head]
    if not tr.get("confirm"):
        # 旧收据 (结构化字段之前) 只有散文, 原样播报并标明。
        out.append(f"- 达标条件（旧散文收据，无结构化字段）: {tr.get('reason') or '—'}")
        return out
    out.append(f"- 确认口径: {confirm_line(tr)}")
    out.append(f"- 达标收盘: {closes_line(tr['confirm'])}")
    out.append(f"- 量腿: {volume_line(tr)}")
    return out


def build(regime: Dict) -> str:
    """`stages.regime.data` → 原文推送用的转移收据。零转移日仍出一行 (dead-man)。"""
    transitions = regime.get("transitions") or []
    lines = [f"{HEADER} {regime.get('date') or ''}".rstrip(), "", LEGEND, ""]
    # 🚨 阶段失败 ≠ 零转移。regime 阶段 rc≠0 时这里拿不到 `data`, `transitions` 为空,
    # dead-man 那行会照常印出「盘后：无 regime 转移」—— 而真相可能是有卡达标、一笔未落。
    # **空结果与零结果必须长得不一样**, 否则最该喊的那天最安静。
    failure = regime.get("stage_failure")
    if failure:
        lines += [f"🚨 **regime 阶段本轮失败 (rc={failure.get('rc')})** — "
                  f"下面不是「零转移」, 是**没有结果**。",
                  "", "```", str(failure.get("error") or "").strip()[:800], "```", ""]
    if not transitions:
        lines.append("盘后：无 regime 转移。" if not failure
                     else "转移收据渲染不出来 —— 本轮没有判定产物, 不代表市场没达标。")
    else:
        for i, tr in enumerate(transitions):
            if i:
                lines.append("")
            lines.extend(render_one(tr))
    for rj in (regime.get("rejected") or []):
        lines += ["", f"⛔ **{rj.get('ticker')}** 达标但**未落卡** — {rj.get('error')}",
                  f"- 达标条件: {rj.get('reason') or '—'}",
                  "- 卡片状态未变 (cap / zones 维持原样); 修卡后由转移机重跑。"]
    stale = regime.get("preexisting_card_errors") or []
    if stale:
        lines += ["", "🩹 挡住本轮落库的**既存**卡片错误 (与本轮转移无关, 先修这些):"]
        lines += [f"- {e}" for e in stale[:8]]
    pending = regime.get("pending_repaint") or []
    if pending:
        names = ", ".join(sorted(str(p.get("ticker") if isinstance(p, dict) else p) for p in pending))
        lines += ["", f"⏳ provisional 待补画（补画前不授权入场）: {names}"]
    applied = regime.get("applied")
    if isinstance(applied, list) and len(applied) != len(transitions):
        lines += ["", f"⚠️ 检测到 {len(transitions)} 笔, 实际应用 {len(applied)} 笔 — 差额按回执逐笔核对。"]
    return "\n".join(lines)


def regime_data(doc: Dict) -> Dict:
    """接受 pipeline 完整 JSON 或 regime 段本身, 两种都不再让调用方手剥。

    阶段失败时 `stage.data` 根本不存在 —— 以前那会静默退化成 `{}`, 与「真的零
    转移」完全同形。现在把失败本身带出来 (`stage_failure`), 由 build 渲染成告警。
    """
    if "transitions" in doc:
        return doc
    stage = ((doc.get("stages") or {}).get("regime") or {})
    if not isinstance(stage, dict):
        return {}
    data = dict(stage.get("data") or {})
    if stage.get("ok") is False or (stage.get("rc") or 0) != 0:
        data["stage_failure"] = {"rc": stage.get("rc"),
                                 "error": (stage.get("stderr") or stage.get("error")
                                           or "stage reported failure without stderr")}
    return data


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pipeline", type=Path,
                    help="pipeline / 控制器 output.json（默认从 stdin 读）")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    raw = a.pipeline.read_text() if a.pipeline else sys.stdin.read()
    data = regime_data(json.loads(raw))
    text = build(data)
    print(json.dumps({"text": text}, ensure_ascii=False) if a.json else text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
