#!/usr/bin/env python3
"""Pull deep daily bars for a preregistered private universe.

The public script contains no symbol list or study window. Both are explicit
private inputs. Output remains in the ignored deep-cache directory and is never
treated as a public fixture.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from quant.core import env  # noqa: E402,F401
from quant.core.paths import KLINE_DEEP_DIR as OUT  # noqa: E402


def load_universe(path: Path) -> list[str]:
    """Load a non-empty JSON list of private symbols."""
    if not path.exists():
        raise FileNotFoundError(f"private universe manifest not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or not payload:
        raise ValueError("universe manifest must be a non-empty JSON list")
    if not all(isinstance(symbol, str) and symbol for symbol in payload):
        raise ValueError("every universe entry must be a non-empty string")
    return list(dict.fromkeys(symbol.upper() for symbol in payload))


def first_session(path: Path) -> str | None:
    """Return the first cached session without loading the entire file."""
    if not path.exists():
        return None
    first = path.read_text(encoding="utf-8").split("\n", 1)[0]
    if not first.strip():
        return None
    return str(json.loads(first).get("time") or "")[:10] or None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Pull deep bars for a private study")
    parser.add_argument("--universe", type=Path, required=True, help="ignored JSON symbol list")
    parser.add_argument("--start", required=True, help="locked study start session")
    parser.add_argument("--end", required=True, help="locked study end session")
    parser.add_argument("--delay", type=float, default=0.6, help="provider request spacing")
    args = parser.parse_args(argv)
    universe = load_universe(args.universe)

    # Provider SDK import is deliberately delayed so import and ``--help`` remain
    # read-only even when the SDK configures local logging at import time.
    from quant.data.market_data import get_kline

    OUT.mkdir(parents=True, exist_ok=True)
    ok = fail = skip = 0
    for index, symbol in enumerate(universe, 1):
        path = OUT / f"{symbol}.jsonl"
        first = first_session(path)
        if first and first <= args.start:
            skip += 1
            continue
        try:
            rows = get_kline(symbol, ktype="1d", start=args.start, end=args.end)
        except Exception as error:  # noqa: BLE001
            print(
                f"[{index}/{len(universe)}] {symbol}: ERROR "
                f"{type(error).__name__}: {error}",
                flush=True,
            )
            fail += 1
            continue
        if not rows:
            print(f"[{index}/{len(universe)}] {symbol}: no data", flush=True)
            fail += 1
            continue
        path.write_text(
            "\n".join(
                json.dumps(
                    {
                        key: row.get(key)
                        for key in ("time", "open", "high", "low", "close", "volume", "turnover")
                    },
                    ensure_ascii=False,
                )
                for row in rows
            )
            + "\n",
            encoding="utf-8",
        )
        print(
            f"[{index}/{len(universe)}] {symbol}: {len(rows)} bars "
            f"{rows[0]['time'][:10]} → {rows[-1]['time'][:10]}",
            flush=True,
        )
        ok += 1
        time.sleep(max(0.0, args.delay))
    print(f"done: {ok} pulled, {skip} already deep, {fail} failed → {OUT}")
    return 0 if ok or skip else 1


if __name__ == "__main__":
    raise SystemExit(main())
