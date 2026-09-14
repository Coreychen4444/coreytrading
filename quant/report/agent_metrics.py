#!/usr/bin/env python3
"""agent_metrics.py — 领域 Agent 层的机械周基线 (只读 `var/agent-runs/` 收据, 零模型调用, 零 trade/ 读写)。

为什么存在: agent_jobs 每轮落 `task-jobs/<job_id>.json` (轮次 + 内嵌 card 子任务) 与 `runs/<run_id>.json`
(每次角色调用: attempts / input_metrics / result.unknowns)。周复盘要回答「这周 agent 层跑了几轮、烧了多少
prompt、卡片按时落了几张、失败是配额还是契约」—— 这些数字都已经在收据里, 只差一个读时汇总。
本模块不产生新状态: 不写文件、不改冷却、不碰 positions/watchlist。

口径 (数字带出处):
  * 轮 (round) = `task-jobs/` 里 job_kind != "card" 的记录 (briefing / postclose / weekly / chart), 窗口按 started_at。
    独立的 card task-job 文件与轮内 `cards` 是同一批子任务的两份拓本, 卡片统计只读轮内 `cards` + `pending_cards`。
  * targets = len(cards) + len(pending_cards) (pending = 本轮点名但没起跑); sla_hit_rate = applied / targets, 0 target → null。
  * 卡片失败分类先看 card.error, 再 card.reason, 都没有再回落到它链接的 run (run.error + attempts[].error) ——
    真实收据里 provider_session_limit / window_budget / timeout 只出现在 run 侧, card 侧 error 为空。
  * model_calls = run.attempts 条数 (每条 attempt 是一次真实的模型调用; budget_block 的 run 有 0 attempts)。
  * estimated_tokens = input_metrics.estimated_tokens, 缺则 prompt_utf8_bytes / 1.9 四舍五入 (粗尺, 只做同尺比较)。
    prompt_bytes / tokens 按 run 计一次 (提示词组装一次), by_provider 按 run.provider (缺则第一条 attempt 的 provider)。
  * 日 = started_at 的 Asia/Singapore 日期 (调度时区, 与 cron 同一个时区)。
  * transport 缺字段 = 老收据 = "cli" (execution_transport 是  desktop 交接后才写的)。
  * 解析不了的文件计 skipped_files, 绝不 raise —— 报表挂掉比少一行更糟。

CLI:
    python3 quant/report/agent_metrics.py [--days 7] [--json | --markdown]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 直接 `python3 quant/<层>/<模块>.py` 时让 `quant.*` 可 import

import argparse  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402
from collections import Counter  # noqa: E402
from datetime import datetime, timedelta, timezone  # noqa: E402
from typing import Any, Dict, List, Optional  # noqa: E402

from quant.core.paths import AGENT_RUNS_DIR  # noqa: E402
from quant.core.timeutil import SGT, parse_ts  # noqa: E402

TOKENS_PER_BYTE_DIVISOR = 1.9  # 无 estimated_tokens 时的粗估: utf8 bytes ÷ 1.9 (中英混排提示词的经验尺)
LARGEST_PROMPTS_N = 5
FAILURE_CLASSES = ("quota", "environment", "submit_contract", "revision_conflict", "timeout", "model_abstained", "other")

_QUOTA_RE = re.compile(r"provider_session_limit|rate_limit|window_budget|\b429\b", re.I)
_ENV_RE = re.compile(r"MarketSessionError|dependency unavailable", re.I)
_REVISION_RE = re.compile(r"revision conflict", re.I)
_SUBMIT_RE = re.compile(r"SubmitError", re.I)
_TIMEOUT_RE = re.compile(r"\btimeout\b|timed out", re.I)
_ABSTAIN_RE = re.compile(r"model_abstained", re.I)


# ── 分类 / 估算 ────────────────────────────────────────────────────────────────

def classify_failure(text: str) -> str:
    """卡片失败文本 → 七类之一。顺序有意义: revision conflict 也带 SubmitError 前缀, 先判它。"""
    t = text or ""
    if _REVISION_RE.search(t):
        return "revision_conflict"
    if _QUOTA_RE.search(t):
        return "quota"
    if _ENV_RE.search(t):
        return "environment"
    if _TIMEOUT_RE.search(t):
        return "timeout"
    if _ABSTAIN_RE.search(t):
        return "model_abstained"
    if _SUBMIT_RE.search(t):
        return "submit_contract"
    return "other"


def estimate_tokens(run: Dict[str, Any]) -> Optional[int]:
    im = run.get("input_metrics") or {}
    est = im.get("estimated_tokens")
    if isinstance(est, (int, float)):
        return int(round(est))
    b = im.get("prompt_utf8_bytes")
    if isinstance(b, (int, float)):
        return int(round(b / TOKENS_PER_BYTE_DIVISOR))
    return None


def _prompt_bytes(run: Dict[str, Any]) -> int:
    b = (run.get("input_metrics") or {}).get("prompt_utf8_bytes")
    return int(b) if isinstance(b, (int, float)) else 0


def _run_provider(run: Dict[str, Any]) -> Optional[str]:
    if run.get("provider"):
        return str(run["provider"])
    for a in run.get("attempts") or []:
        if isinstance(a, dict) and a.get("provider"):
            return str(a["provider"])
    return None


def _card_failure_text(card: Dict[str, Any], runs: Dict[str, Dict[str, Any]]) -> str:
    parts: List[str] = []
    if card.get("error"):
        parts.append(str(card["error"]))
    if card.get("reason"):
        parts.append(str(card["reason"]))
    if not parts:
        run = runs.get(str(card.get("run_id") or ""))
        if run:
            if run.get("error"):
                parts.append(str(run["error"]))
            for a in run.get("attempts") or []:
                if isinstance(a, dict) and (a.get("error") or a.get("status") in ("timeout", "invalid_result")):
                    parts.append(str(a.get("error") or a.get("status")))
    return " | ".join(parts)


# ── 读盘 ───────────────────────────────────────────────────────────────────────

def _load_dir(d: Path) -> tuple[List[Dict[str, Any]], int]:
    rows: List[Dict[str, Any]] = []
    skipped = 0
    if not d.is_dir():
        return rows, skipped
    for p in sorted(d.glob("*.json")):
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            skipped += 1
            continue
        if not isinstance(obj, dict):
            skipped += 1
            continue
        rows.append(obj)
    return rows, skipped


def _ts(s: Any) -> Optional[datetime]:
    dt = parse_ts(s) if s else None
    return dt.astimezone(timezone.utc) if dt else None


def _sgt_day(dt: datetime) -> str:
    return dt.astimezone(SGT).date().isoformat()


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


# ── 汇总 ───────────────────────────────────────────────────────────────────────

def _empty_bucket() -> Dict[str, Any]:
    return {
        "rounds": 0, "model_calls": 0, "prompt_bytes_total": 0, "estimated_tokens_total": 0,
        "cards_targets": 0, "cards_applied": 0, "cards_failed": 0, "sla_hit_rate": None,
        "unknowns_total": 0, "by_provider": {},
    }


def _finish_bucket(b: Dict[str, Any]) -> Dict[str, Any]:
    b["sla_hit_rate"] = round(b["cards_applied"] / b["cards_targets"], 4) if b["cards_targets"] else None
    b["by_provider"] = {k: b["by_provider"][k] for k in sorted(b["by_provider"])}
    return b


def _round_row(job: Dict[str, Any], runs: Dict[str, Dict[str, Any]], started: datetime) -> Dict[str, Any]:
    finished = _ts(job.get("finished_at"))
    roles: Dict[str, Any] = {}
    for role, rep in sorted((job.get("reports") or {}).items()):
        rep = rep if isinstance(rep, dict) else {}
        run = runs.get(str(rep.get("run_id") or ""), {})
        attempts = [a for a in (run.get("attempts") or []) if isinstance(a, dict)]
        unknowns = rep.get("unknowns")
        if unknowns is None:
            unknowns = (run.get("result") or {}).get("unknowns") or []
        roles[role] = {
            "status": rep.get("status") or run.get("status"),
            "provider": rep.get("provider") or _run_provider(run),
            "transport": run.get("execution_transport") or "cli",
            "prompt_bytes": _prompt_bytes(run),
            "estimated_tokens": estimate_tokens(run),
            "elapsed_s": round(sum(float(a.get("elapsed_s") or 0) for a in attempts), 3),
            "unknowns": len(unknowns) if isinstance(unknowns, list) else 0,
            "reused": bool(rep.get("reused_from_run_id")),
        }
    cards = [c for c in (job.get("cards") or []) if isinstance(c, dict)]
    pending = job.get("pending_cards") or []
    classes: Counter = Counter()
    counts = {"targets": len(cards) + len(pending), "applied": 0, "failed": 0, "needs_review": 0, "pending": len(pending)}
    for c in cards:
        st = c.get("status")
        if st == "applied":
            counts["applied"] += 1
        elif st == "needs_review":
            counts["needs_review"] += 1
            classes[classify_failure(_card_failure_text(c, runs))] += 1
        elif st in ("failed", "busy"):
            counts["failed"] += 1
            classes[classify_failure(_card_failure_text(c, runs))] += 1
    return {
        "job_id": str(job.get("job_id") or "")[:8],
        "job_kind": job.get("job_kind"),
        "session": job.get("session"),
        "status": job.get("status"),
        "started_at": _iso(started),
        "duration_s": round((finished - started).total_seconds(), 1) if finished else None,
        "roles": roles,
        "cards": counts,
        "failure_classes": dict(sorted(classes.items())),
    }


def collect(run_dir: Path | str | None = None, *, days: int = 7, now: Optional[datetime] = None) -> Dict[str, Any]:
    """窗口内 (started_at ≥ now − days) 的轮次明细 + 按 SGT 日汇总 + 窗口合计。永不 raise。"""
    base = Path(run_dir) if run_dir else AGENT_RUNS_DIR
    now_dt = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    since = now_dt - timedelta(days=days)

    jobs, sk1 = _load_dir(base / "task-jobs")
    run_list, sk2 = _load_dir(base / "runs")
    runs: Dict[str, Dict[str, Any]] = {str(r.get("run_id")): r for r in run_list if r.get("run_id")}

    day_buckets: Dict[str, Dict[str, Any]] = {}
    totals = _empty_bucket()

    def bucket(day: str) -> Dict[str, Any]:
        return day_buckets.setdefault(day, _empty_bucket())

    def bump(b: Dict[str, Any], key: str, n: int) -> None:
        b[key] += n

    # 轮次
    dated_rounds = []
    for job in jobs:
        if job.get("job_kind") == "card":
            continue
        started = _ts(job.get("started_at"))
        if started is None or started < since or started > now_dt:
            continue
        dated_rounds.append((started, str(job.get("job_id") or ""), job))
    dated_rounds.sort(key=lambda t: (t[0], t[1]))
    rounds: List[Dict[str, Any]] = []
    for started, _jid, job in dated_rounds:
        row = _round_row(job, runs, started)
        rounds.append(row)
        for b in (bucket(_sgt_day(started)), totals):
            bump(b, "rounds", 1)
            bump(b, "cards_targets", row["cards"]["targets"])
            bump(b, "cards_applied", row["cards"]["applied"])
            bump(b, "cards_failed", row["cards"]["failed"])

    # 调用 (所有 run, 不限于轮内引用的 —— 独立 card job 的 run 也是真实调用)
    prompts: List[Dict[str, Any]] = []
    for run in run_list:
        started = _ts(run.get("started_at"))
        if started is None or started < since or started > now_dt:
            continue
        attempts = [a for a in (run.get("attempts") or []) if isinstance(a, dict)]
        pb = _prompt_bytes(run)
        tok = estimate_tokens(run) or 0
        unk = (run.get("result") or {}).get("unknowns") or []
        prov = _run_provider(run)
        for b in (bucket(_sgt_day(started)), totals):
            bump(b, "model_calls", len(attempts))
            bump(b, "prompt_bytes_total", pb)
            bump(b, "estimated_tokens_total", tok)
            bump(b, "unknowns_total", len(unk) if isinstance(unk, list) else 0)
            if prov:
                p = b["by_provider"].setdefault(prov, {"calls": 0, "estimated_tokens": 0})
                p["calls"] += len(attempts)
                p["estimated_tokens"] += tok
        if pb:
            prompts.append({"run_id": str(run.get("run_id") or ""), "role": run.get("role"), "bytes": pb})

    prompts.sort(key=lambda r: (-r["bytes"], r["run_id"]))
    totals = _finish_bucket(totals)
    totals["largest_prompts"] = prompts[:LARGEST_PROMPTS_N]
    return {
        "window": {"days": days, "since": _iso(since), "until": _iso(now_dt), "tz_day": "Asia/Singapore"},
        "run_dir": str(base),
        "rounds": rounds,
        "days": {d: _finish_bucket(day_buckets[d]) for d in sorted(day_buckets)},
        "totals": totals,
        "skipped_files": sk1 + sk2,
    }


# ── 渲染 ───────────────────────────────────────────────────────────────────────

def _k(n: Optional[int]) -> str:
    if n is None:
        return "-"
    return f"{n / 1000:.0f}k" if abs(n) >= 1000 else str(n)


def _pct(x: Optional[float]) -> str:
    return "-" if x is None else f"{x * 100:.0f}%"


def _roles_cell(roles: Dict[str, Any]) -> str:
    mark = {"succeeded": "✓", "failed": "✗", "needs_review": "?", "superseded": "≈"}
    out = []
    for role, r in roles.items():
        m = mark.get(str(r.get("status")), "·")
        tag = f"{role[:4]}{m}"
        if r.get("reused"):
            tag += "↻"
        if r.get("transport") == "desktop":
            tag += "d"
        out.append(tag)
    return " ".join(out) or "-"


def render_markdown(rep: Dict[str, Any]) -> str:
    w = rep["window"]
    lines = [f"# Agent 层周基线 · {w['since'][:10]} → {w['until'][:10]} (SGT 日, 只读收据)", ""]
    lines += ["| 日 | 轮 | 调用 | prompt | tokens≈ | 卡 applied/targets | SLA | unknowns | provider |",
              "|---|---|---|---|---|---|---|---|---|"]
    for day, b in rep["days"].items():
        prov = " ".join(f"{p}:{v['calls']}" for p, v in b["by_provider"].items()) or "-"
        lines.append(f"| {day[5:]} | {b['rounds']} | {b['model_calls']} | {_k(b['prompt_bytes_total'])} | {_k(b['estimated_tokens_total'])} "
                     f"| {b['cards_applied']}/{b['cards_targets']} | {_pct(b['sla_hit_rate'])} | {b['unknowns_total']} | {prov} |")
    t = rep["totals"]
    prov = " ".join(f"{p}:{v['calls']}" for p, v in t["by_provider"].items()) or "-"
    lines.append(f"| **合计** | {t['rounds']} | {t['model_calls']} | {_k(t['prompt_bytes_total'])} | {_k(t['estimated_tokens_total'])} "
                 f"| {t['cards_applied']}/{t['cards_targets']} | {_pct(t['sla_hit_rate'])} | {t['unknowns_total']} | {prov} |")
    lines += ["", "| job | kind | session | status | 时长 | roles | 卡 a/f/r/p | 失败类 |", "|---|---|---|---|---|---|---|---|"]
    if not rep["rounds"]:
        lines.append("| - | - | - | 窗口内无轮次 | - | - | - | - |")
    for r in rep["rounds"]:
        c = r["cards"]
        dur = "-" if r["duration_s"] is None else f"{r['duration_s'] / 60:.0f}m"
        fc = " ".join(f"{k}:{v}" for k, v in r["failure_classes"].items()) or "-"
        lines.append(f"| {r['job_id']} | {r['job_kind']} | {r['session'] or '-'} | {r['status']} | {dur} | {_roles_cell(r['roles'])} "
                     f"| {c['applied']}/{c['failed']}/{c['needs_review']}/{c['pending']} | {fc} |")
    lp = t.get("largest_prompts") or []
    if lp:
        lines += ["", "最大 prompt: " + " · ".join(f"{p['role']} {p['run_id'][:8]} {_k(p['bytes'])}B" for p in lp)]
    if rep.get("skipped_files"):
        lines.append(f"⚠️ 跳过无法解析文件 {rep['skipped_files']} 个")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="领域 Agent 层周基线 (只读 var/agent-runs 收据)")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--run-dir", default=None, help="默认 core.paths.AGENT_RUNS_DIR")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--json", action="store_true")
    g.add_argument("--markdown", action="store_true")
    args = ap.parse_args(argv)
    rep = collect(args.run_dir, days=args.days)
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2, default=str))
    else:
        print(render_markdown(rep))
    return 0


if __name__ == "__main__":
    sys.exit(main())
