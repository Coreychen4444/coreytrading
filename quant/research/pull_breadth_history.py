#!/usr/bin/env python3
"""Pull provider daily bars for an explicit private breadth universe.

Here ``breadth`` means study-universe width, not a market-breadth indicator.
The public module contains neither symbols nor a study window. Provider terms,
adjustment differences, authorization and survivorship limits must be locked in
the private preregistration. Output remains in the ignored deep-cache directory.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 直接 `python3 quant/<层>/<模块>.py` 时让 `quant.*` 可 import

import argparse
import json
import time
import urllib.request
from datetime import datetime, timezone

from quant.core.paths import KLINE_DEEP_DIR as OUT  # noqa: E402

URL = ("https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
       "?period1={p1}&period2={p2}&interval=1d")
UA = {"User-Agent": "Mozilla/5.0 (offline research backtest)"}


def load_universe(path: Path) -> list[str]:
    """Load a non-empty JSON list from the ignored private workspace."""
    if not path.exists():
        raise FileNotFoundError(f"private universe manifest not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or not payload:
        raise ValueError("universe manifest must be a non-empty JSON list")
    if not all(isinstance(symbol, str) and symbol for symbol in payload):
        raise ValueError("every universe entry must be a non-empty string")
    return list(dict.fromkeys(symbol.upper() for symbol in payload))


def _epoch(d: str) -> int:
    return int(datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())


def fetch(tk: str, start: str, end: str) -> list[dict]:
    req = urllib.request.Request(
        URL.format(sym=tk, p1=_epoch(start), p2=_epoch(end)), headers=UA)
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read().decode("utf-8", errors="replace"))
    result = (payload.get("chart") or {}).get("result") or []
    if not result:
        return []
    ts = result[0].get("timestamp") or []
    q = ((result[0].get("indicators") or {}).get("quote") or [{}])[0]
    rows = []
    for i, t in enumerate(ts):
        o, h, lo, c, v = (q.get(k, [None] * len(ts))[i]
                          for k in ("open", "high", "low", "close", "volume"))
        if c is None or o is None:
            continue
        day = time.strftime("%Y-%m-%d", time.gmtime(t))   # 美股开盘 13:30/14:30 UTC, gmtime 日期即交易日
        rows.append({
            "time": f"{day} 00:00:00",
            "open": round(o, 4), "high": round(h, 4),
            "low": round(lo, 4), "close": round(c, 4),
            "volume": int(v or 0),
            "turnover": None,   # Yahoo 无成交额; 消费端 close×volume 近似
        })
    return rows


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Pull provider bars for a private study")
    parser.add_argument("--universe", type=Path, required=True, help="ignored JSON symbol list")
    parser.add_argument("--start", required=True, help="locked study start session")
    parser.add_argument("--end", required=True, help="exclusive provider end session")
    parser.add_argument("--delay", type=float, default=0.8, help="provider request spacing")
    args = parser.parse_args(argv)
    universe = load_universe(args.universe)
    OUT.mkdir(parents=True, exist_ok=True)
    manifest_path = OUT / "_sources.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    for p in OUT.glob("*.jsonl"):
        manifest.setdefault(p.stem, "opend")   # 既有 OpenD 票登记来源, 不覆盖其数据

    ok = fail = skip = 0
    for i, tk in enumerate(universe, 1):
        path = OUT / f"{tk}.jsonl"
        if path.exists():
            skip += 1     # OpenD 票或已拉过的票, 都不动
            continue
        try:
            rows = fetch(tk, args.start, args.end)
        except Exception as e:                                   # noqa: BLE001
            print(f"[{i}/{len(universe)}] {tk}: ERROR {type(e).__name__}: {e}", flush=True)
            fail += 1
            time.sleep(1.0)
            continue
        if len(rows) < 300:
            print(f"[{i}/{len(universe)}] {tk}: only {len(rows)} bars — skip", flush=True)
            fail += 1
            time.sleep(1.0)
            continue
        path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                        encoding="utf-8")
        manifest[tk] = "yahoo"
        print(f"[{i}/{len(universe)}] {tk}: {len(rows)} bars "
              f"{rows[0]['time'][:10]} → {rows[-1]['time'][:10]}", flush=True)
        ok += 1
        time.sleep(max(0.0, args.delay))

    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                             encoding="utf-8")
    print(f"\ndone: {ok} pulled, {skip} existing, {fail} failed → {OUT}")
    return 0 if ok or skip else 1


if __name__ == "__main__":
    raise SystemExit(main())
