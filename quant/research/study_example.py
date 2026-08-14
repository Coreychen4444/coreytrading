#!/usr/bin/env python3
"""Synthetic paired-study example for the private research bootstrap.

The example contains no market observations and performs no work on import.
Copy the mechanics only after locking a private preregistration.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from statistics import mean, median
from typing import Iterable


@dataclass(frozen=True)
class PairedObservation:
    """One synthetic observation evaluated under baseline and candidate rules."""

    observation_id: str
    baseline_metric: float
    candidate_metric: float

    @property
    def delta(self) -> float:
        return self.candidate_metric - self.baseline_metric


def summarize(observations: Iterable[PairedObservation]) -> dict:
    """Return deterministic descriptive statistics without selecting a threshold."""
    rows = list(observations)
    if not rows:
        return {
            "n": 0,
            "mean_delta": None,
            "median_delta": None,
            "positive_rate": None,
        }
    deltas = [row.delta for row in rows]
    return {
        "n": len(rows),
        "mean_delta": mean(deltas),
        "median_delta": median(deltas),
        "positive_rate": sum(delta > 0 for delta in deltas) / len(deltas),
    }


def decide(summary: dict, *, minimum_sample: int, minimum_mean_delta: float) -> str:
    """Apply thresholds supplied by a locked protocol, never by this example."""
    if summary["n"] < minimum_sample:
        return "inconclusive"
    if summary["mean_delta"] is not None and summary["mean_delta"] >= minimum_mean_delta:
        return "support"
    return "no_support"


def synthetic_observations() -> list[PairedObservation]:
    """Return neutral records used only to demonstrate the output schema."""
    return [
        PairedObservation("synthetic-a", 1.0, 1.2),
        PairedObservation("synthetic-b", 0.8, 0.7),
        PairedObservation("synthetic-c", 1.1, 1.3),
        PairedObservation("synthetic-d", 0.9, 1.0),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Render the synthetic research example")
    parser.add_argument("--json", action="store_true", help="render machine-readable output")
    args = parser.parse_args()

    rows = synthetic_observations()
    summary = summarize(rows)
    payload = {
        "synthetic": True,
        "observations": [asdict(row) for row in rows],
        "summary": summary,
        "verdict": decide(summary, minimum_sample=4, minimum_mean_delta=0.0),
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print("Synthetic example only; lock private thresholds before using private data.")
        print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
