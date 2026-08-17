#!/usr/bin/env python3
"""Start a detached card-building agent for eligible cards without zones.

The pipeline must not wait for structural chart analysis. This module detects
empty cards, starts a narrowly scoped headless Claude session, records enough
state to suppress duplicate launches, and returns immediately. A completed card
naturally leaves the target set because it now has zones.

CLI:
    python3 quant/ops/card_autopaint.py [--json] [--dry-run]
                                        [--max-spawn N] [--tickers T ...] [--force]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import argparse  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402
import time  # noqa: E402
from typing import Dict, List, Optional  # noqa: E402

from quant.core.holdings import cardless_tickers  # noqa: E402
from quant.core.io_json import atomic_write_json, read_json  # noqa: E402
from quant.core.paths import POSITIONS_PATH, ROOT, TMP_DIR, WATCHLIST_PATH  # noqa: E402
from quant.core.timeutil import et_today  # noqa: E402

STATE_PATH = TMP_DIR / "card_autopaint.json"
LOG_DIR = TMP_DIR / "card_autopaint"
COOLDOWN_S = 6 * 3600
MAX_SPAWN_PER_RUN = 2
SPAWN_TIMEOUT_S = 30 * 60


def _claude_bin() -> Optional[str]:
    """Resolve the Claude CLI for non-interactive card construction."""
    explicit = os.environ.get("CLAUDE_BIN")
    if explicit and Path(explicit).exists():
        return explicit
    found = shutil.which("claude")
    if found:
        return found
    fallback = Path.home() / ".local" / "bin" / "claude"
    return str(fallback) if fallback.exists() else None


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
    except (OSError, TypeError, ValueError):
        return False
    return True


def load_state() -> Dict:
    try:
        return read_json(STATE_PATH) or {}
    except Exception:  # noqa: BLE001 - unreadable launch state must not block detection
        return {}


def due(targets: List[str], state: Dict, now: float, force: bool = False) -> tuple:
    """Return due targets and targets skipped by liveness or cooldown checks."""
    jobs = state.get("jobs") or {}
    ready, skipped = [], []
    for ticker in targets:
        job = jobs.get(ticker) or {}
        if force:
            ready.append(ticker)
            continue
        if _alive(job.get("pid")):
            skipped.append((ticker, f"previous card agent is still running (pid {job.get('pid')})"))
            continue
        age = now - float(job.get("spawned_at_ts") or 0)
        if age < COOLDOWN_S:
            skipped.append((ticker, f"last launch was {age / 3600:.1f}h ago; cooldown active"))
            continue
        ready.append(ticker)
    return ready, skipped


def build_prompt(ticker: str, bucket: str, today: str) -> str:
    """Build a self-contained prompt for a fresh, narrowly scoped session."""
    command_bucket = "current" if bucket == "positions" else "watchlist"
    return f"""Build the missing structural card for {ticker} in {bucket}.json.

The card currently has no zones, so downstream entry, gate and reminder checks
cannot operate. Work only on this one card.

Read these canonical instructions before acting:
- `.claude/rules/zone-maintenance.md`
- `.claude/rules/macro-context-check.md`
- `.claude/rules/position-tiers.md`
- `.claude/skills/trading-analysis/SKILL.md`

Requirements:
1. Use only completed daily bars. Refresh the local cache if necessary.
2. Run `quant/decision/level_map.py` and `quant/decision/tape_check.py` for {ticker}.
3. Treat catalysts and macro context as information, never structural authorization.
4. Define z1, z2, trim, no-chase and stop. Provide a defensible multi-source gate
   and a prepared `next_regime.entry_gate`; never invent a level to fill a field.
5. Write through the controlled helper only:
   `.claude/skills/trading-analysis/scripts/update_position_zones.py`
   with `--ticker {ticker} --bucket {command_bucket}` and the required zone,
   anchor, validity, methodology and next-regime arguments. The structural anchor
   must match the most recent completed bar, which is expected near {today}.
6. Re-run `quant/decision/entry_guard.py --tickers {ticker}` and confirm the result
   is no longer NEEDS_CARD. Run the card audit and report any unresolved advisory.

Hard boundaries:
- Modify only the {ticker} card in the selected ignored runtime file.
- If zones already exist when the session starts, exit without overwriting them.
- Do not place orders, change broker groups, edit strategy prose or modify code.
- Do not commit or publish populated runtime state.
"""


def _timeout_argv(seconds: int) -> List[str]:
    """Resolve a portable wall-clock guard for the detached process."""
    for name in ("timeout", "gtimeout"):
        found = shutil.which(name)
        if found:
            return [found, str(seconds)]
    perl = shutil.which("perl") or "/usr/bin/perl"
    if Path(perl).exists():
        return [perl, "-e", "alarm shift; exec @ARGV", str(seconds)]
    return []


def spawn(ticker: str, bucket: str, today: str, binary: str) -> Dict:
    """Start one detached agent and return without waiting."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log = LOG_DIR / f"{ticker}-{time.strftime('%Y%m%dT%H%M%S')}.log"
    wrapper = _timeout_argv(SPAWN_TIMEOUT_S)
    argv = [*wrapper, binary, "-p", build_prompt(ticker, bucket, today),
            "--permission-mode", "bypassPermissions"]
    with log.open("wb") as handle:
        process = subprocess.Popen(
            argv,
            cwd=str(ROOT),
            stdin=subprocess.DEVNULL,
            stdout=handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    return {
        "ticker": ticker,
        "bucket": bucket,
        "pid": process.pid,
        "log": _display_path(log),
        "timeout_enforced": bool(wrapper),
        "spawned_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "spawned_at_ts": time.time(),
    }


def run(dry_run: bool = False, max_spawn: int = MAX_SPAWN_PER_RUN,
        only: Optional[List[str]] = None, force: bool = False) -> Dict:
    positions = read_json(POSITIONS_PATH)
    watchlist = read_json(WATCHLIST_PATH)
    targets = cardless_tickers(positions, watchlist)
    if only:
        selected = {ticker.upper() for ticker in only}
        targets = [ticker for ticker in targets if ticker in selected]
    in_positions = set((positions.get("tickers") or {}))

    state = load_state()
    ready, skipped = due(targets, state, time.time(), force=force)
    capped, ready = ready[max_spawn:], ready[:max_spawn]
    skipped += [(ticker, f"per-run launch limit is {max_spawn}") for ticker in capped]

    result: Dict = {
        "cardless": targets,
        "spawned": [],
        "skipped": [{"ticker": ticker, "reason": reason} for ticker, reason in skipped],
        "errors": [],
    }
    if dry_run:
        result["dry_run"] = [
            {"ticker": ticker, "bucket": "positions" if ticker in in_positions else "watchlist"}
            for ticker in ready
        ]
        return result

    binary = _claude_bin()
    if ready and not binary:
        result["errors"].append("Claude CLI was not found; set CLAUDE_BIN for scheduled runs")
        return result

    jobs = state.setdefault("jobs", {})
    today = et_today()
    for ticker in ready:
        bucket = "positions" if ticker in in_positions else "watchlist"
        try:
            job = spawn(ticker, bucket, today, binary)  # type: ignore[arg-type]
        except Exception as exc:  # noqa: BLE001 - one launch must not abort the pipeline
            result["errors"].append(f"{ticker}: launch failed ({exc.__class__.__name__}: {exc})")
            continue
        jobs[ticker] = job
        result["spawned"].append(job)

    state["as_of"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    if result["spawned"]:
        TMP_DIR.mkdir(parents=True, exist_ok=True)
        atomic_write_json(STATE_PATH, state)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="start detached agents for cards without zones")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-spawn", type=int, default=MAX_SPAWN_PER_RUN)
    parser.add_argument("--tickers", nargs="*")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    result = run(args.dry_run, args.max_spawn, args.tickers, args.force)
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if not result["cardless"]:
        print("card_autopaint: no eligible cards are missing zones")
        return 0
    print("Missing cards: " + ", ".join(result["cardless"]))
    for item in result.get("dry_run", []):
        print(f"  dry-run: {item['ticker']} [{item['bucket']}]")
    for item in result["spawned"]:
        print(f"  started {item['ticker']} pid={item['pid']} log={item['log']}")
    for item in result["skipped"]:
        print(f"  skipped {item['ticker']}: {item['reason']}")
    for error in result["errors"]:
        print(f"  warning: {error}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
