"""The project-level agent contract must remain complete and operational."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "CLAUDE.md"


def test_claude_contract_is_not_replaced_by_a_summary():
    text = CONTRACT.read_text(encoding="utf-8")
    assert len(text) >= 25_000
    assert len(text.splitlines()) >= 350


def test_claude_contract_covers_project_operating_domains():
    text = CONTRACT.read_text(encoding="utf-8")
    required_sections = (
        "## Privacy and public-repository boundary",
        "## Workspace and data contracts",
        "## Architecture and dependency direction",
        "## Core layer: executable single sources of truth",
        "## Sync layer: broker truth and reconciliation",
        "## Decision layer",
        "## Ops pipeline and stage order",
        "## Optional scheduled tasks",
        "## Core decision model",
        "## Trading discipline",
        "## State mutation and ownership",
        "## Session behavior",
        "## Verification and release gate",
    )
    for section in required_sections:
        assert section in text


def test_claude_contract_names_critical_single_sources_of_truth():
    text = CONTRACT.read_text(encoding="utf-8")
    required_anchors = (
        "AGENTS.md",
        "AI_BOOTSTRAP.md",
        "AI_BOOTSTRAP_CN.md",
        "trade/README.md",
        "quant/README.md",
        "quant/core/paths.py",
        "quant/core/io_json.py",
        "quant/core/sizing.py",
        "sizing.entry_cap()",
        "sizing.gate_count(card)",
        "held_bare ⊆ live_tickers ⊆ held_incl_option_underlyings",
        "quant/sync/watchlist_sync.py",
        "lot_reconcile.py",
        "close_reconcile.py",
        "price_reminder_sync.py",
        "today_group_sync.py",
        "quant/decision/entry_guard.py",
        "quant/decision/exit_guard.py",
        "quant/ops/pipeline.py",
        "tests/test_public_sanitization.py",
    )
    for anchor in required_anchors:
        assert anchor in text
