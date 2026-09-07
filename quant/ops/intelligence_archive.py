"""Resolve old intelligence evidence paths without editing archived records.

Read-only convenience for report/source references embedded in pre-migration
snapshots and receipts. Does not collect evidence, publish state or send reports.
"""
from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import argparse
import json

from quant.core.paths import resolve_intelligence_reference


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reference", help="current or archived repo-local data path")
    args = parser.parse_args(argv)
    try:
        path = resolve_intelligence_reference(args.reference)
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    exists = path.is_file()
    print(json.dumps({"ok": exists, "path": str(path), "exists": exists,
                      "error": None if exists else "evidence_missing"}))
    return 0 if exists else 1


if __name__ == "__main__":
    raise SystemExit(main())
