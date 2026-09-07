"""Read committed position snapshots across the portfolio directory migration."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import json
import re
import subprocess

from quant.core.paths import ROOT, POSITION_HISTORY_PATHS


def list_position_commits(root: Path = ROOT, *, reverse: bool = False) -> list[tuple[str, str]]:
    args = ["git", "log", "--full-history", "--topo-order", "--format=%H %cI"]
    if reverse:
        args.append("--reverse")
    out = subprocess.check_output(args + ["--", *POSITION_HISTORY_PATHS], cwd=root,
                                  text=True, timeout=60)
    return [tuple(line.split(" ", 1)) for line in out.splitlines()]


def position_at(sha: str, root: Path = ROOT) -> dict:
    """Read the one snapshot present at a commit; conflicting copies fail closed."""
    if not re.fullmatch(r"[0-9a-fA-F]{40,64}", sha):
        raise ValueError("expected a full Git commit id")
    names = subprocess.check_output(
        ["git", "ls-tree", "-r", "--name-only", "-z", sha, "--", *POSITION_HISTORY_PATHS],
        cwd=root, timeout=30).decode().split("\0")
    blobs = [subprocess.check_output(["git", "show", f"{sha}:{name}"], cwd=root, timeout=30)
             for name in names if name in POSITION_HISTORY_PATHS]
    if not blobs:
        return {}
    if any(blob != blobs[0] for blob in blobs[1:]):
        raise ValueError("conflicting historical position snapshots")
    doc = json.loads(blobs[0])
    if not isinstance(doc, dict):
        raise ValueError("position snapshot must be an object")
    return doc
