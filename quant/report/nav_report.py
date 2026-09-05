#!/usr/bin/env python3
"""nav_report.py — NAV 区间变动 (剔除外部现金流) = 周复盘 §2 与任何「NAV 涨跌」播报的**唯一口径**。

为什么存在: 周复盘 prompt 若让 agent 自己拿 nav_history 首尾两行相减, 一笔提款就会被报成当周亏损,
然后追问「是提款还是结算调整」—— 而它在券商流水里明明白白 (cash_flow_sync 落账), 剔除后当周其实是正收益。读时计算: nav_history (data/nav_snapshot) × cash_flows.jsonl
(data/cash_flow_sync) → core/nav.change_report; 本模块只渲染 + 加「账本可能漏计」的警告行。

播报两个数, 各有用处 (core/nav docstring): Dietz 型 (ΔNAV − Σ流) / NAV_start 回答「这周钱多了多少」;
TWR 逐日连乘回答「对标指数跑赢多少」(流的时点权重不同, 两者可差 0.5pp, 都不是错)。
零授权效力: 只算收益率; cap 分母仍取 nav_history 最后一行 (book.latest_nav), 提款后分母变小是对的。

CLI (系统 python3 即可, 只读两个 jsonl):
    python3 quant/report/nav_report.py [--days 7 | --start D --end D] [--json]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 直接 `python3 quant/<层>/<模块>.py` 时让 `quant.*` 可 import

import argparse  # noqa: E402
import json  # noqa: E402
from typing import Dict, List, Optional  # noqa: E402

from quant.core.io_json import iter_jsonl, read_json  # noqa: E402
from quant.core.nav import change_report  # noqa: E402
from quant.core.paths import CASH_FLOWS_PATH, CASH_FLOWS_SYNC_PATH, NAV_HISTORY_PATH  # noqa: E402


def build(*, days: Optional[int] = 7, start: Optional[str] = None, end: Optional[str] = None,
          nav_path: Optional[Path] = None, flows_path: Optional[Path] = None,
          sync_path: Optional[Path] = None) -> Dict:
    nav_rows = list(iter_jsonl(Path(nav_path or NAV_HISTORY_PATH)))
    flow_rows = list(iter_jsonl(Path(flows_path or CASH_FLOWS_PATH)))
    rep = change_report(nav_rows, flow_rows, days=days, start=start, end=end)
    side = Path(sync_path or CASH_FLOWS_SYNC_PATH)
    meta = (read_json(side) if side.exists() else {}) or {}
    warnings: List[str] = []
    if rep is None:
        return {"ok": False, "reason": "NAV 快照不足两行 (nav_history 空或区间内只有一行)", "warnings": warnings}
    through = str(meta.get("synced_through") or "")
    if not through:
        warnings.append("现金流账本从未同步 (cash_flow_sync 没跑过) — 区间内的出入金可能漏计, 下面的剔除后数字只当账面")
    elif through < rep["end"]["date"]:
        warnings.append(f"现金流账本只同步到 {through} < 区间末 {rep['end']['date']} — 之后的出入金可能漏计")
    if rep["unpriced_flows"]:
        warnings.append(f"{len(rep['unpriced_flows'])} 笔外部流未折算 USD (无同日换汇汇率), 剔除后变动未含它们")
    return {"ok": True, **rep, "synced_through": through or None, "warnings": warnings}


def _usd(x: float) -> str:
    return f"{'+' if x >= 0 else '-'}${abs(x):,.0f}"


def _pct(x: Optional[float]) -> str:
    return f"{x:+.2f}%" if x is not None else "n/a"


def _flow_item(f: dict) -> str:
    amt = f.get("amount")
    head = f"{str(f.get('date'))[5:]} {f.get('type')} {f.get('currency')}" + (f" {amt:+,.2f}" if amt is not None else "")
    usd = f.get("usd_amount")
    return head + (f" = USD {usd:+,.2f}" if usd is not None else " (未折算)") \
        + (f" [{f.get('source')}]" if f.get("source") == "manual" else "")


def render(rep: Dict) -> str:
    if not rep.get("ok"):
        return f"💰 NAV 周变: {rep.get('reason')}"
    s, e = rep["start"], rep["end"]
    lines = [f"💰 NAV {s['date']} → {e['date']} (外部现金流已剔除; 区间内 {rep['snapshots']} 个快照日)", "",
             "| 项 | 值 |", "|---|---|",
             f"| NAV | ${s['nav']:,.0f} → ${e['nav']:,.0f} |",
             f"| 账面变动 | {_usd(rep['raw_change_usd'])} ({_pct(rep['raw_change_pct'])}) |"]
    if rep["flows"]:
        lines.append(f"| 外部现金流 | {_usd(rep['flow_usd_total'])} · " + " · ".join(_flow_item(f) for f in rep["flows"]) + " |")
    else:
        lines.append("| 外部现金流 | 无 |")
    lines.append(f"| 剔除后变动 | {_usd(rep['adjusted_change_usd'])} ({_pct(rep['adjusted_change_pct'])})"
                 f" · TWR {_pct(rep['twr_pct'])} |")
    b = rep["benchmarks"]
    lines.append(f"| QQQ / SPY 同期 | {_pct(b['qqq_pct'])} / {_pct(b['spy_pct'])} |")
    for w in rep["warnings"]:
        lines.append(f"⚠️ {w}")
    lines.append("出入金不是盈亏; 对标指数看 TWR。cap 分母仍取最新 NAV (提款后分母变小是对的)。")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="NAV 区间变动 (剔除外部现金流)")
    ap.add_argument("--days", type=int, default=7, help="区间 = 最近 N 个日历日 (端点取不晚于该日的 NAV 快照)")
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    rep = build(days=None if a.start else a.days, start=a.start, end=a.end)
    print(json.dumps(rep, ensure_ascii=False) if a.json else render(rep))
    return 0 if rep.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
