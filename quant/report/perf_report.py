#!/usr/bin/env python3
"""perf_report.py — 真实绩效报告：events ledger + scores.jsonl 读时计算。

复盘从叙事升级为数据 (process>outcome 哲学的测量层)：
- Realized PnL: 总额 / 按月 / 按 underlying / 按 tier (FIFO 归因) / 胜率与盈亏分布
- 按 classification (Confirmed/Probe/Early-risky) 的胜率 — 入场标签 vs 实际结果
- Process × Outcome 矩阵 + "D 级但赚钱" 检测 (variance≠skill 警报)
- Open episodes + 标注覆盖率 (诚实显示多少历史未标注)

派生值永不落盘 — 每次从原始事实重算，无二次漂移。

CLI:
    python3 quant/report/perf_report.py            # text report
    python3 quant/report/perf_report.py --json
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 直接 `python3 quant/<层>/<模块>.py` 时让 `quant.*` 可 import

import argparse
import json
from collections import defaultdict

from quant.core.io_json import iter_jsonl, read_json  # noqa: E402
from quant.core.ledger import build_episodes  # noqa: E402
from quant.core.occ import parse_occ  # noqa: E402
from quant.core.paths import POSITIONS_PATH, SCORES_PATH  # noqa: E402
from quant.core.timeutil import parse_ts  # noqa: E402


def _underlying(ticker: str) -> str:
    p = parse_occ(ticker)
    return p["underlying"] if p else ticker


def _currency_of(ep: dict) -> str:
    for e in ep["entries"] + ep["exits"]:
        if e.get("currency"):
            return e["currency"]
    return "USD"


def _holding_days(ep: dict) -> float | None:
    a, b = parse_ts(ep.get("opened_at") or ""), parse_ts(ep.get("closed_at") or "")
    if a and b:
        return round((b - a).total_seconds() / 86400, 1)
    return None


def _entry_tag(ep: dict, field: str):
    for e in ep["entries"]:
        if e.get(field):
            return e[field]
    return None


def collect(positions_doc: dict | None = None) -> dict:
    episodes = build_episodes(warn=lambda m: None)
    closed = [e for lst in episodes.values() for e in lst if e["status"] == "closed"]
    open_eps = [e for lst in episodes.values() for e in lst if e["status"] == "open"]
    closed.sort(key=lambda e: e.get("closed_at") or "")

    out: dict = {"by_currency": {}}
    for ccy in sorted({_currency_of(e) for e in closed}):
        eps = [e for e in closed if _currency_of(e) == ccy]
        known = [e for e in eps if e["realized_pnl"] is not None]
        wins = [e for e in known if e["realized_pnl"] > 0]
        losses = [e for e in known if e["realized_pnl"] <= 0]
        hold = [d for e in known if (d := _holding_days(e)) is not None]

        by_month = defaultdict(float)
        by_under = defaultdict(float)
        by_tier = defaultdict(float)
        for e in known:
            by_month[str(e["closed_at"])[:7]] += e["realized_pnl"]
            by_under[_underlying(e["ticker"])] += e["realized_pnl"]
            for x in e.get("exit_pnls", []):
                if x["realized_fifo"] is not None:
                    by_tier[x["tier"] or "untagged"] += x["realized_fifo"]

        # by_classification = 历史口径 (标签  停盖, 只读旧事件);
        # by_gate = 新口径 (auth.gate_no = 成交时爬过几个 gate)。跨期对比时两个
        # 分桶各自完整, 不拼接。`round_no` 是 08-13 当天早些时候那一版的键
        # (lots 近似轮号, 两条历史事件带着它), 一并读进来但按 gate 归桶 —— 它
        # 问的是同一件事的更差近似, 分成两个桶只会让 n 更小。
        by_class = {}
        for cls in ("confirmed", "probe", "early_risky", None):
            sub = [e for e in known if _entry_tag(e, "classification") == cls]
            if not sub:
                continue
            w = [e for e in sub if e["realized_pnl"] > 0]
            by_class[cls or "untagged"] = {
                "episodes": len(sub),
                "win_rate": round(len(w) / len(sub), 3),
                "pnl": round(sum(e["realized_pnl"] for e in sub), 2),
            }
        by_gate = {}
        for e in known:
            tag = _entry_tag(e, "auth")
            gno = (tag.get("gate_no", tag.get("round_no"))
                   if isinstance(tag, dict) else None)
            key = f"gate{gno}" if gno else None
            if key is None:
                continue
            b = by_gate.setdefault(key, {"episodes": 0, "wins": 0, "pnl": 0.0})
            b["episodes"] += 1
            b["wins"] += 1 if e["realized_pnl"] > 0 else 0
            b["pnl"] = round(b["pnl"] + e["realized_pnl"], 2)
        for b in by_gate.values():
            b["win_rate"] = round(b.pop("wins") / b["episodes"], 3)

        out["by_currency"][ccy] = {
            "closed_episodes": len(eps),
            "known_basis": len(known),
            "orphan_basis": len(eps) - len(known),
            "realized_total": round(sum(e["realized_pnl"] for e in known), 2),
            "win_rate": round(len(wins) / len(known), 3) if known else None,
            "avg_win": round(sum(e["realized_pnl"] for e in wins) / len(wins), 2) if wins else None,
            "avg_loss": round(sum(e["realized_pnl"] for e in losses) / len(losses), 2) if losses else None,
            "median_holding_days": sorted(hold)[len(hold) // 2] if hold else None,
            "by_month": dict(sorted(by_month.items())),
            "by_underlying": dict(sorted(by_under.items(), key=lambda kv: -kv[1])),
            "by_tier_fifo": dict(sorted(by_tier.items(), key=lambda kv: -kv[1])),
            "by_classification": by_class,
            "by_gate": dict(sorted(by_gate.items())),
            "top_winners": [
                {"ticker": e["ticker"], "pnl": round(e["realized_pnl"], 2), "closed": str(e["closed_at"])[:10]}
                for e in sorted(known, key=lambda x: -x["realized_pnl"])[:5]
            ],
            "top_losers": [
                {"ticker": e["ticker"], "pnl": round(e["realized_pnl"], 2), "closed": str(e["closed_at"])[:10]}
                for e in sorted(known, key=lambda x: x["realized_pnl"])[:5]
            ],
        }

    # ---- process × outcome (scores.jsonl ⨝ episodes by ref order_id / ticker+date) ----
    scores = list(iter_jsonl(SCORES_PATH))
    matrix = defaultdict(int)
    d_wins, joins = [], 0
    closed_by_id: dict[str, dict] = {}
    for e in closed:
        for ev in e["entries"] + e["exits"]:
            if ev.get("order_id"):
                closed_by_id[str(ev["order_id"])] = e
    for s in scores:
        ep = closed_by_id.get(str(s.get("ref")))
        if ep is None:
            cands = [e for e in closed if e["ticker"] == s.get("ticker")]
            ep = cands[-1] if cands else None
        if ep is None or ep["realized_pnl"] is None:
            continue
        joins += 1
        outcome = "win" if ep["realized_pnl"] > 0 else "loss"
        letter = (s.get("score") or "?")[0]
        matrix[f"{letter}/{outcome}"] += 1
        if letter == "D" and outcome == "win":
            d_wins.append({"ticker": ep["ticker"], "pnl": round(ep["realized_pnl"], 2), "score_note": s.get("notes")})
    out["process_outcome"] = {
        "scored_rows": len(scores),
        "joined_to_closed_episodes": joins,
        "matrix": dict(sorted(matrix.items())),
        "d_grade_wins": d_wins,
        "warning": "🚨 D-grade win = variance not skill — 下次同样赌会失败" if d_wins else None,
    }

    # ---- open episodes + unrealized via positions.json broker marks ----
    pos = positions_doc if positions_doc is not None else (read_json(POSITIONS_PATH) if POSITIONS_PATH.exists() else {"tickers": {}})
    open_rows = []
    for e in open_eps:
        broker = (pos["tickers"].get(e["ticker"]) or {}).get("broker") or {}
        mark = broker.get("current_price")
        open_qty = sum(en.get("qty", 0) for en in e["entries"]) - sum(x.get("qty", 0) for x in e["exits"])
        # simple unrealized only for episodes with no partial exits (else basis
        # split is ambiguous at this layer — positions.json lots are the truth)
        unreal = None
        if mark is not None and not e["exits"] and not e["orphan"]:
            unreal = round(mark * open_qty * e["multiplier"] - e["cost_in"], 2)
        open_rows.append({
            "ticker": e["ticker"], "opened": str(e["opened_at"])[:10],
            "open_qty": round(open_qty, 4), "cost_in": round(e["cost_in"], 2),
            "mark": mark, "unrealized_simple": unreal,
            "tier": _entry_tag(e, "tier"), "orphan": e["orphan"],
        })
    out["open_episodes"] = open_rows

    # ---- annotation coverage ----
    all_events = [ev for lst in episodes.values() for e in lst for ev in e["entries"] + e["exits"]]
    out["coverage"] = {
        "events": len(all_events),
        "tier_tagged": sum(1 for ev in all_events if ev.get("tier")),
        "classification_tagged": sum(1 for ev in all_events if ev.get("classification")),
        "auth_tagged": sum(1 for ev in all_events if ev.get("auth")),
        "note": "Jan-Mar 为纪律体系建立前的历史, 未标注是诚实状态不是缺陷",
    }
    return out


def fmt(d: dict) -> str:
    L = ["=" * 64, "PERFORMANCE REPORT (read-time computed from events ledger)", "=" * 64]
    for ccy, s in d["by_currency"].items():
        L.append(f"\n── Realized ({ccy}) ──")
        L.append(f"  closed episodes : {s['closed_episodes']} (known basis {s['known_basis']}, orphan {s['orphan_basis']})")
        L.append(f"  realized total  : {s['realized_total']:+,.2f}")
        if s["win_rate"] is not None:
            aw = f"{s['avg_win']:+,.2f}" if s["avg_win"] is not None else "n/a"
            al = f"{s['avg_loss']:+,.2f}" if s["avg_loss"] is not None else "n/a"
            L.append(f"  win rate        : {s['win_rate']:.0%}   avg win {aw} / avg loss {al}")
            L.append(f"  median holding  : {s['median_holding_days']} days")
        L.append("  by month        : " + "  ".join(f"{m} {v:+,.0f}" for m, v in s["by_month"].items()))
        L.append("  by underlying   : " + "  ".join(f"{k} {v:+,.0f}" for k, v in list(s["by_underlying"].items())[:8]))
        L.append("  by tier (FIFO)  : " + "  ".join(f"{k} {v:+,.0f}" for k, v in s["by_tier_fifo"].items()))
        if s["by_classification"]:
            L.append("  by classification (历史口径, 标签 2026-08-13 停盖):")
            for k, v in s["by_classification"].items():
                L.append(f"    {k:12s} n={v['episodes']:<3} win {v['win_rate']:.0%}  pnl {v['pnl']:+,.2f}")
        if s.get("by_gate"):
            L.append("  by gate (auth.gate_no, 2026-08-13 起; 含 round_no 老键):")
            for k, v in s["by_gate"].items():
                L.append(f"    {k:12s} n={v['episodes']:<3} win {v['win_rate']:.0%}  pnl {v['pnl']:+,.2f}")
        L.append("  top winners     : " + ", ".join(f"{w['ticker']} {w['pnl']:+,.0f}" for w in s["top_winners"][:3]))
        L.append("  top losers      : " + ", ".join(f"{w['ticker']} {w['pnl']:+,.0f}" for w in s["top_losers"][:3]))
    po = d["process_outcome"]
    L.append("\n── Process × Outcome ──")
    L.append(f"  scored {po['scored_rows']} rows, joined {po['joined_to_closed_episodes']} to closed episodes")
    L.append("  matrix          : " + "  ".join(f"{k}:{v}" for k, v in po["matrix"].items()))
    if po["d_grade_wins"]:
        L.append(f"  🚨 D-grade wins: {po['d_grade_wins']}")
        L.append(f"  {po['warning']}")
    L.append("\n── Open episodes ──")
    for o in d["open_episodes"]:
        L.append(f"  {o['ticker']:22s} qty {o['open_qty']:<8} cost {o['cost_in']:>10,.0f} tier {o['tier'] or '-':8s} since {o['opened']}{' ⚠orphan' if o['orphan'] else ''}")
    c = d["coverage"]
    L.append(f"\n── Coverage ── events {c['events']} | tier {c['tier_tagged']} | classification {c['classification_tagged']} (legacy) | auth {c['auth_tagged']}")
    L.append(f"  ({c['note']})")
    L.append("=" * 64)
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description="绩效报告 (events ledger 读时计算)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    d = collect()
    print(json.dumps(d, ensure_ascii=False, indent=2) if a.json else fmt(d))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
