#!/usr/bin/env python3
"""market_regime_log.py — 大盘状态卡（C1）每日快照账本 + chart-read 推送段（判读层，零授权效力）。

判别式真源 = `quant/core/market_regime.py`：目前是震荡还是单边趋势行情；出现哪些信息确认会使行情如何转变。

  --update  收盘后：SPY / QQQ / SMH 各写一行当日状态快照（状态 / ER 档 / 道氏阶段 / 列出的转移线 / 历史频率）。
            追加写，(date, bench) 幂等，漏跑下一轮补不回当天（快照只在当天有意义；缺的日子在对账里记缺）。
  --digest  只读渲染一段，接在 chart-read 清单**之前**同一条推送（先看大盘，再看个股两种模式）。
  --json    结构化状态。

「预测」的问责：账本每天一行 = 当天列出的状态与转移线；h 根之后的实际状态就是 h 根之后那天的快照，
对账 = 两行 join（`calibration`），不另写结算行。回测里状态分不出两种模式的好坏：状态只作判读，不进排序、不进授权。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 直接 `python3 quant/<层>/<模块>.py` 时让 `quant.*` 可 import

import argparse  # noqa: E402
import json  # noqa: E402
from datetime import datetime  # noqa: E402
from typing import Dict, List, Optional  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

from quant.core import market_regime as MR  # noqa: E402
from quant.core.io_json import append_jsonl, iter_jsonl  # noqa: E402
from quant.core.paths import KLINE_CACHE_DIR, KLINE_DEEP_DIR, MARKET_REGIME_PATH  # noqa: E402

DESIGN_DOC = "quant/core/market_regime.py"   # 账本行记录判别式出处
CALIBRATION_HORIZONS = (5, 20)


def _day(bar: dict) -> str:
    return str(bar.get("time") or bar.get("date") or "")[:10]


def _rows(path: Path) -> List[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def load_bench_bars(ticker: str, asof: Optional[str] = None, *,
                    deep_dir: Path = KLINE_DEEP_DIR, cache_dir: Path = KLINE_CACHE_DIR) -> List[dict]:
    """深度日线（回测池，不入 git，可能缺）+ 缓存日线接在其后。接缝处两边收盘差很小（ETF，同一复权框架）。
    缓存由 `kline_cache.py` 维护，源头已丢弃未完成 bar —— 本函数不再判盘中。"""
    deep = sorted(_rows(deep_dir / f"{ticker}.jsonl"), key=_day)
    cache = sorted(_rows(cache_dir / f"{ticker}.jsonl"), key=_day)
    last = _day(deep[-1]) if deep else ""
    bars = deep + [b for b in cache if _day(b) > last]
    if asof:
        bars = [b for b in bars if _day(b) <= asof]
    return bars


def cards(asof: Optional[str] = None, benches=MR.BENCHMARKS, *,
          deep_dir: Path = KLINE_DEEP_DIR, cache_dir: Path = KLINE_CACHE_DIR) -> List[Dict]:
    out = []
    for t in benches:
        bars = load_bench_bars(t, asof, deep_dir=deep_dir, cache_dir=cache_dir)
        if len(bars) < MR.ER_WINDOW + MR.PCTL_WINDOW + 1:
            out.append({"bench": t, "error": f"日线不足（{len(bars)} 根）"})
            continue
        out.append({"bench": t, **MR.state(bars)})
    return out


def _ledger_row(card: Dict) -> Dict:
    return {
        "date": card["date"], "bench": card["bench"], "state": card["state"],
        "er": card["er"], "pctl": card["pctl"], "band": card["band"],
        "er_thresholds": card.get("er_thresholds"),
        "dow": card["dow"],
        "rates": {str(h): r for h, r in (card.get("rates") or {}).items()},
        "captured_at": datetime.now(ZoneInfo("America/New_York")).isoformat(timespec="seconds"),
        "design": DESIGN_DOC,
    }


def update(asof: Optional[str] = None, path: Path = MARKET_REGIME_PATH, *,
           deep_dir: Path = KLINE_DEEP_DIR, cache_dir: Path = KLINE_CACHE_DIR) -> int:
    have = {(r.get("date"), r.get("bench")) for r in iter_jsonl(path)} if path.exists() else set()
    n = 0
    for c in cards(asof, deep_dir=deep_dir, cache_dir=cache_dir):
        if c.get("error"):
            print(f"⚠️ {c['bench']} {c['error']}", file=sys.stderr)
            continue
        key = (c["date"], c["bench"])
        if key in have:
            continue
        append_jsonl(path, _ledger_row(c))
        n += 1
    return n


def calibration(path: Path = MARKET_REGIME_PATH, horizons=CALIBRATION_HORIZONS) -> Dict:
    """账本自对账：t 日列出的状态 vs t+h 那天快照的实际状态（按账本行序数 h，不是交易日历）。"""
    by_bench: Dict[str, List[Dict]] = {}
    for r in (iter_jsonl(path) if path.exists() else []):
        by_bench.setdefault(r["bench"], []).append(r)
    out: Dict[str, Dict] = {}
    for b, rows in by_bench.items():
        rows.sort(key=lambda r: r["date"])
        res = {}
        for h in horizons:
            pairs = [(rows[i], rows[i + h]) for i in range(len(rows) - h)]
            if not pairs:
                continue
            same = sum(1 for a, z in pairs if a["state"] == z["state"])
            # 列出的历史频率 vs 实际：Brier（频率当预报；只报，不裁）
            briers = []
            for a, z in pairs:
                rates = ((a.get("rates") or {}).get(str(h)) or {}).get("rates")
                if rates:
                    briers.append(sum((rates.get(s, 0.0) - (1.0 if z["state"] == s else 0.0)) ** 2 for s in MR.STATES))
            res[h] = {"n": len(pairs), "persist": same / len(pairs),
                      "brier": (sum(briers) / len(briers)) if briers else None}
        out[b] = res
    return out


def digest(asof: Optional[str] = None) -> str:
    cs = cards(asof)
    day = next((c["date"] for c in cs if not c.get("error")), asof or "")
    L = [f"## [FYI] 大盘状态 {day} · 判读层，零授权效力",
         "> 状态 = ER20（|20 日净位移| ÷ 20 日路径）在自身 252 日分位的档（<1/3 震荡 / ≥2/3 单边）× 短期道氏（推进 / 观察期）。"
         "「预测」= 条件线 + 历史频率（频率不是概率）。2010–2026 回测：状态分不出两种模式的好坏（H1 不成立），只作环境判读，不排序、不授权。",
         ""]
    for c in cs:
        if c.get("error"):
            L.append(f"⚠️ {c['bench']} {c['error']}")
            continue
        lines = MR.describe(c)
        L.append(f"**{c['bench']}** {lines[0]}")
        L.extend(f"- {x.strip()}" for x in lines[1:])
        L.append("")
    cal = calibration()
    tail = []
    for b, res in cal.items():
        for h, r in sorted(res.items()):
            if r["n"] >= 20:
                tail.append(f"{b} {h} 根后仍同状态 {r['persist']:.0%}（n={r['n']}）"
                            + (f" · Brier {r['brier']:.2f}" if r.get("brier") is not None else ""))
    if tail:
        L.append("账本对账：" + "；".join(tail))
    return "\n".join(L).rstrip()


def main() -> int:
    ap = argparse.ArgumentParser(description="大盘状态卡（C1）账本 / 推送段 —— 判读层，零授权效力")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--update", action="store_true", help="写当日快照（幂等）")
    g.add_argument("--digest", action="store_true", help="chart-read 推送段")
    g.add_argument("--json", action="store_true")
    ap.add_argument("--asof", help="截断到该交易日（复盘用）")
    a = ap.parse_args()
    if a.update:
        n = update(a.asof)
        print(f"market_regime: +{n} 行 → {MARKET_REGIME_PATH.relative_to(MARKET_REGIME_PATH.parents[2])}")
    elif a.digest:
        print(digest(a.asof))
    else:
        print(json.dumps({"cards": cards(a.asof), "calibration": calibration()}, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
