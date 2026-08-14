#!/usr/bin/env python3
"""Replay classified private episodes under alternative NAV cap schedules.

The public module contains generic mechanics only. Initial NAV and any option
spread-leg merge rules are explicit private inputs. Importing this module does
not read state or execute a study.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from quant.core.ledger import closed_episodes  # noqa: E402


SCHEDULES = [
    ("low", {"probe": 0.01, "confirmed": 0.025, "early_risky": 0.0}),
    ("medium", {"probe": 0.025, "confirmed": 0.05, "early_risky": 0.0}),
    ("high", {"probe": 0.05, "confirmed": 0.10, "early_risky": 0.0}),
    ("concentrated", {"probe": 0.10, "confirmed": 0.15, "early_risky": 0.0}),
    ("confirmed_only_high", {"probe": 0.0, "confirmed": 0.10, "early_risky": 0.0}),
    ("confirmed_only_low", {"probe": 0.0, "confirmed": 0.05, "early_risky": 0.0}),
    ("no_veto", {"probe": 0.05, "confirmed": 0.10, "early_risky": 0.05}),
]


def entry_tag(episode: dict, field: str):
    for event in episode.get("entries", []):
        value = event.get(field)
        if value:
            return value
    return None


def load_private_config(path: Path | None) -> list[tuple[str, str]]:
    """Load option spread pairs to merge as ``[long_symbol, short_symbol]``."""
    if path is None:
        return []
    if not path.exists():
        raise FileNotFoundError(f"private config not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    pairs = payload.get("merge_spreads")
    if not isinstance(pairs, list):
        raise ValueError("private config requires merge_spreads as a JSON list")
    out = []
    for index, pair in enumerate(pairs):
        if not isinstance(pair, list) or len(pair) != 2 or not all(
            isinstance(item, str) and item for item in pair
        ):
            raise ValueError(f"merge_spreads[{index}] must contain two symbols")
        out.append((pair[0], pair[1]))
    return out


def build_sample(merge_pairs: list[tuple[str, str]]) -> tuple[list[dict], list[tuple]]:
    """Build classified long episodes, applying only preregistered spread merges."""
    all_episodes = closed_episodes()
    raw = {episode["ticker"]: episode for episode in all_episodes}
    long_to_short = dict(merge_pairs)
    short_symbols = {short for _, short in merge_pairs}
    sample = []
    skipped = []
    for episode in all_episodes:
        classification = entry_tag(episode, "classification")
        if not classification or episode["ticker"] in short_symbols:
            continue
        pnl = episode.get("realized_pnl")
        cost = episode.get("cost_in")
        short_symbol = long_to_short.get(episode["ticker"])
        if short_symbol and short_symbol in raw:
            short = raw[short_symbol]
            pnl = (pnl or 0) + (short.get("realized_pnl") or 0)
            cost = (cost or 0) - (short.get("cost_in") or 0)
        direction_ok = episode.get("direction") == "long" or short_symbol is not None
        if pnl is None or not cost or cost <= 0 or not direction_ok:
            skipped.append((episode["ticker"], classification, episode.get("direction"), cost, pnl))
            continue
        sample.append({
            "ticker": episode["ticker"],
            "classification": classification,
            "open": str(episode["opened_at"])[:10],
            "close": str(episode["closed_at"])[:10],
            "cost": cost,
            "pnl": pnl,
            "return": pnl / cost,
        })
    sample.sort(key=lambda row: (row["open"], row["close"]))
    return sample, skipped


def replay(sample: list[dict], cap_map: dict, *, nav0: float, loss_mult: float = 1.0) -> dict:
    """Chronologically replay cap-sized episodes on a realized-basis NAV path."""
    nav = nav0
    path = [nav]
    deployed = 0.0
    peak_concentration = 0.0
    worst_episode = 0.0
    events = []
    for episode in sample:
        events.append((episode["open"], "open", episode))
        events.append((episode["close"], "close", episode))
    events.sort(key=lambda row: (row[0], 0 if row[1] == "close" else 1))
    sizes = {}
    for _, kind, episode in events:
        key = id(episode)
        if kind == "open":
            cap = cap_map.get(episode["classification"], 0.0)
            size = min(cap * nav, max(0.0, nav - deployed))
            sizes[key] = size
            deployed += size
            peak_concentration = max(
                peak_concentration, deployed / nav if nav > 0 else 0.0
            )
            continue
        size = sizes.pop(key, 0.0)
        deployed -= size
        realized_return = episode["return"]
        if realized_return < 0:
            realized_return *= loss_mult
        pnl = size * realized_return
        worst_episode = min(worst_episode, pnl / nav if nav > 0 else 0.0)
        nav += pnl
        path.append(nav)
    peak = path[0]
    max_drawdown = 0.0
    for value in path:
        peak = max(peak, value)
        max_drawdown = min(max_drawdown, (value - peak) / peak)
    return {
        "end": nav,
        "pnl": nav - nav0,
        "return": nav / nav0 - 1,
        "max_drawdown": max_drawdown,
        "peak_concentration": peak_concentration,
        "worst_episode": worst_episode,
    }


def class_stats(sample: list[dict]) -> dict:
    """Return per-class counts and empirical win/loss summaries."""
    grouped = defaultdict(list)
    for episode in sample:
        grouped[episode["classification"]].append(episode)
    result = {}
    for classification, rows in sorted(grouped.items()):
        wins = [row["return"] for row in rows if row["pnl"] > 0]
        losses = [row["return"] for row in rows if row["pnl"] <= 0]
        result[classification] = {
            "n": len(rows),
            "win_rate": len(wins) / len(rows),
            "average_win": sum(wins) / len(wins) if wins else None,
            "average_loss": sum(losses) / len(losses) if losses else None,
        }
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Replay private episodes under cap schedules")
    parser.add_argument("--nav", type=float, required=True, help="private starting NAV")
    parser.add_argument("--config", type=Path, help="ignored JSON config with merge_spreads")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.nav <= 0:
        parser.error("--nav must be positive")

    merge_pairs = load_private_config(args.config)
    sample, skipped = build_sample(merge_pairs)
    output = {
        "sample_size": len(sample),
        "skipped_count": len(skipped),
        "class_stats": class_stats(sample),
        "scenarios": {},
    }
    for loss_mult in (1.0, 1.5, 2.0):
        output["scenarios"][str(loss_mult)] = {
            name: replay(sample, caps, nav0=args.nav, loss_mult=loss_mult)
            for name, caps in SCHEDULES
        }
    if args.json:
        print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"classified episodes: {len(sample)}; skipped: {len(skipped)}")
        print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
