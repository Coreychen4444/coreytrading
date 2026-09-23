"""One project contract: `AGENTS.md`. `CLAUDE.md` is a one-line `@AGENTS.md` import shim.

Codex and Claude Code >= 2.1.277 read AGENTS.md natively (Codex auto-loads at most 32 KiB and
truncates silently above that). Older Claude Code runtimes do not discover AGENTS.md at all and
reach the contract only through the shim. Under Claude Code's default mode any CLAUDE.md with
content of its own shadows AGENTS.md completely, so the shim must stay exactly one line. The shim
may be deleted once every Claude Code runtime in use is >= 2.1.277; this file allows "absent",
never "present but not the shim".
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENTS = ROOT / "AGENTS.md"
SHIM = ROOT / "CLAUDE.md"
CODEX_AUTO_LOAD_LIMIT = 32 * 1024


def test_agents_md_is_the_full_contract_not_a_pointer():
    text = AGENTS.read_text(encoding="utf-8")
    assert text.startswith("# coreytrading"), "AGENTS.md must open with the contract title, not a pointer"
    assert "Standing directives" in text and "## Session Behavior" in text, (
        "AGENTS.md must carry the contract body itself, not only a title")
    assert "read `CLAUDE.md` in full" not in text


def test_claude_md_if_present_is_only_an_import_shim():
    if not SHIM.exists():
        return
    assert SHIM.read_text(encoding="utf-8").strip() == "@AGENTS.md", (
        "CLAUDE.md must be exactly `@AGENTS.md`: any other content shadows AGENTS.md under "
        "Claude Code's default mode and the two contracts drift. Edit AGENTS.md instead.")


def test_no_second_instruction_file_under_dot_claude():
    for rel in (".claude/CLAUDE.md", ".claude/AGENTS.md"):
        assert not (ROOT / rel).exists(), f"{rel} would be a second project contract"


def test_agents_md_fits_codex_auto_load_budget():
    size = AGENTS.stat().st_size
    assert size <= CODEX_AUTO_LOAD_LIMIT, f"AGENTS.md {size} B exceeds Codex's 32 KiB auto-load limit (silent truncation)"
