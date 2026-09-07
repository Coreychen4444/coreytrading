#!/usr/bin/env python3
"""structure_drift.py — 每日观测所有卡片的结构漂移, 落库供日后校准。

**只观测, 不裁决。**输出进 brief 的"待重画"清单 (advisory), 不参与任何交易判定。
阈值是未校准初值 —— 接 hard STALE 之前要先跑校准 (zone_timeline 挖历史卡片 +
level_map 机械重画, 以"重画会不会改变 entry_guard 决策"作 label)。

落 portfolio/observations/structure_drift.jsonl (每卡每交易日一行, 幂等)。这条序列本身
就是校准的观测样本 —— 先攒数据, 再谈阈值。

CLI:
    .venv/moomoo/bin/python3 quant/decision/structure_drift.py [--tickers NBIS MU] [--log] [--json]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 直接 `python3 quant/<层>/<模块>.py` 时让 `quant.*` 可 import

from quant.core import env, technical  # noqa: F401  (.env → os.environ)

import argparse
import json
from typing import Dict, List

from quant.core.drift import drift_line, structure_drift  # noqa: E402
from quant.core.io_json import append_jsonl, iter_jsonl, read_json  # noqa: E402
from quant.core.paths import KLINE_CACHE_DIR, POSITIONS_PATH, WATCHLIST_PATH  # noqa: E402
from quant.core.timeutil import et_today, now_et_iso  # noqa: E402

from quant.core.paths import STRUCTURE_DRIFT_PATH as DRIFT_PATH


def _rows(ticker: str) -> List[dict]:
    p = KLINE_CACHE_DIR / f"{ticker}.jsonl"
    return list(iter_jsonl(p)) if p.exists() else []


def cards() -> Dict[str, dict]:
    """两个 bucket 的活卡。期权/非美股跳过 (无 kline)。"""
    out: Dict[str, dict] = {}
    for path, bucket in ((POSITIONS_PATH, "position"), (WATCHLIST_PATH, "watchlist")):
        if not path.exists():
            continue
        for tk, body in (read_json(path).get("tickers") or {}).items():
            agent = body.get("agent") or {}
            if not agent.get("zones") or "." in tk or len(tk) > 6:
                continue
            broker = body.get("broker") or {}
            if bucket == "position" and not (broker.get("qty") or 0):
                continue
            out.setdefault(tk.upper(), {"agent": agent, "bucket": bucket})
    return out


def run(tickers: List[str] | None = None) -> Dict[str, dict]:
    todo = cards()
    if tickers:
        todo = {t: v for t, v in todo.items() if t in {x.upper() for x in tickers}}
    out: Dict[str, dict] = {}
    for tk, c in sorted(todo.items()):
        rows = _rows(tk)
        if not rows:
            continue
        d = structure_drift(c["agent"], rows, technical.atr(rows, 20))
        if d:
            out[tk] = {**d, "bucket": c["bucket"]}
    return out


def log(results: Dict[str, dict], today: str) -> int:
    """幂等: 同一 (date, ticker) 只落一行。"""
    seen = {(r.get("date"), r.get("ticker")) for r in iter_jsonl(DRIFT_PATH)}
    n = 0
    DRIFT_PATH.parent.mkdir(parents=True, exist_ok=True)
    for tk, d in results.items():
        if (today, tk) in seen or d.get("insufficient"):
            continue
        append_jsonl(DRIFT_PATH, {
            "date": today, "logged_at": now_et_iso(), "ticker": tk,
            "bucket": d.get("bucket"), "anchor_date": d.get("anchor_date"),
            "sessions_since": d.get("sessions_since"),
            "price_drift_n": d.get("price_drift_n"),
            "out_of_range_frac": d.get("out_of_range_frac"),
            "gate_reach_n": d.get("gate_reach_n"), "vol_ratio": d.get("vol_ratio"),
            "advisory": d.get("advisory"), "breaches": d.get("breaches"),
        })
        n += 1
    return n


def render(results: Dict[str, dict]) -> str:
    L = ["📐 结构漂移观测 (advisory — 只观测不裁决, 阈值未校准)",
         "   ⚠️代理锚 = 卡片无 anchored_on, 退用 last_reviewed(\"谁碰过这张卡\")当锚 —— "
         "改任意字段都会把漂移时钟归零, 重画时补 --anchored-on 即可"]
    if not results:
        return "\n".join(L + ["   无可评估卡片"])
    L.append(f"   {'ticker':<7}{'锚定':<12}{'d':>4}{'离锚N':>7}"
             f"{'区外%':>7}{'gateN':>7}{'vol×':>6}  状态")
    for tk, d in results.items():
        proxy = " ⚠️代理锚" if d.get("anchor_source") == "last_reviewed_proxy" else ""
        if d.get("insufficient"):
            L.append(f"   {tk:<7}{d['anchor_date']:<12}{d['sessions_since']:>4}"
                     f"{'—':>7}{'—':>7}{'—':>7}{'—':>6}  {d.get('note', '')}{proxy}")
            continue
        f = lambda v, w=7: (f"{v:>{w}.2f}" if isinstance(v, (int, float)) else f"{'—':>{w}}")  # noqa: E731
        oor = d.get("out_of_range_frac")
        L.append(f"   {tk:<7}{d['anchor_date']:<12}{d['sessions_since']:>4}"
                 f"{f(d.get('price_drift_n'))}"
                 f"{(f'{oor * 100:>6.0f}%' if oor is not None else f'{chr(8212):>7}')}"
                 f"{f(d.get('gate_reach_n'))}{f(d.get('vol_ratio'), 6)}"
                 f"  {'⚠️ 待重画' if d.get('advisory') else '✅'}{proxy}")
    flagged = [drift_line(t, d) for t, d in results.items()]
    for line in [x for x in flagged if x]:
        L.append("   " + line)
    return "\n".join(L)


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description="结构漂移观测 (advisory)")
    ap.add_argument("--tickers", nargs="*", default=None)
    ap.add_argument("--log", action="store_true", help="落 portfolio/observations/structure_drift.jsonl")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    res = run(a.tickers)
    today = et_today()
    logged = log(res, today) if a.log else 0
    if a.json:
        print(json.dumps({"date": today, "logged": logged, "tickers": res},
                         ensure_ascii=False))
    else:
        print(render(res))
        if a.log:
            print(f"   → 落库 {logged} 行 ({DRIFT_PATH.name})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
