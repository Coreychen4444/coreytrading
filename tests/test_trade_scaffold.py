"""Public `trade/` is an AI bootstrap contract, never a data/strategy bundle."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TRADE = ROOT / "trade"


def _public_files() -> list[Path]:
    return sorted(path for path in TRADE.rglob("*") if path.is_file())


def test_trade_contains_only_readmes_and_examples():
    files = _public_files()
    assert files, "trade bootstrap contract is missing"
    invalid = [
        path.relative_to(ROOT).as_posix()
        for path in files
        if path.name != "README.md" and ".example." not in path.name
    ]
    assert invalid == []


def test_machine_readable_examples_parse():
    for path in _public_files():
        if ".example." not in path.name:
            continue
        assert path.stat().st_size > 0, path
        if path.suffix == ".json":
            json.loads(path.read_text(encoding="utf-8"))
        elif path.suffix == ".jsonl":
            rows = [line for line in path.read_text(encoding="utf-8").splitlines() if line]
            assert rows, path
            for line in rows:
                json.loads(line)


def test_top_level_contract_is_ai_first_and_private_by_default():
    contract = (TRADE / "README.md").read_text(encoding="utf-8")
    for requirement in ("AI bootstrap procedure", "removing the `.example`", "ignored by Git"):
        assert requirement in contract
