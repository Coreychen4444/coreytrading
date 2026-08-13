"""The AI bootstrap contract must remain discoverable and safe by default."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AGENT_ENTRY = ROOT / "AGENTS.md"
BOOTSTRAP = ROOT / "AI_BOOTSTRAP.md"
BOOTSTRAP_CN = ROOT / "AI_BOOTSTRAP_CN.md"


def test_non_claude_entry_preserves_the_complete_contract():
    text = AGENT_ENTRY.read_text(encoding="utf-8")
    assert "read `CLAUDE.md` in full" in text
    assert "Do not rename, delete, summarize, or duplicate `CLAUDE.md`" in text
    assert "AI_BOOTSTRAP.md" in text


def test_bootstrap_rejects_instruction_file_renaming():
    text = BOOTSTRAP.read_text(encoding="utf-8")
    assert "do not rename `CLAUDE.md`" in text
    assert "`CLAUDE.md` is the complete, authoritative project contract" in text
    for runtime in ("Claude Code", "OpenAI Codex", "Gemini CLI", "GitHub Copilot"):
        assert runtime in text


def test_bootstrap_covers_required_infrastructure_and_safety_domains():
    text = BOOTSTRAP.read_text(encoding="utf-8")
    required_sections = (
        "## Phase 0 — Preflight and scope",
        "## Phase 1 — Python infrastructure",
        "## Phase 2 — Offline verification baseline",
        "## Phase 3 — Private trading workspace",
        "## Phase 4 — Secrets and local configuration",
        "## Phase 5 — Optional OpenD and broker infrastructure",
        "## Phase 6 — Optional operations infrastructure",
        "## Completion gate and handoff report",
        "## Common questions and answers",
    )
    for section in required_sections:
        assert section in text

    required_anchors = (
        "python -m pip install -e '.[dev]'",
        "python -m pytest -q",
        "git diff --check",
        "trade/README.md",
        "git check-ignore -v",
        ".env.example",
        ".mcp.json.example",
        "never unlock trading through the SDK",
        "Never register all tasks automatically",
        "Do I need to rename `CLAUDE.md`",
        "What if a private workspace already exists?",
        "Can setup place a paper-trading order",
        "May the AI commit or push",
    )
    for anchor in required_anchors:
        assert anchor in text


def test_readmes_expose_the_ai_bootstrap_entrypoint():
    for name in ("README.md", "README_CN.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "AI_BOOTSTRAP.md" in text
        assert "AGENTS.md" in text
        assert "CLAUDE.md" in text

    assert "AI_BOOTSTRAP_CN.md" in (ROOT / "README_CN.md").read_text(encoding="utf-8")


def test_chinese_bootstrap_preserves_structure_and_operational_anchors():
    english = BOOTSTRAP.read_text(encoding="utf-8")
    chinese = BOOTSTRAP_CN.read_text(encoding="utf-8")

    assert "[English](AI_BOOTSTRAP.md)" in chinese
    assert "[中文版](AI_BOOTSTRAP_CN.md)" in english
    assert english.count("\n### ") == chinese.count("\n### ")

    required_sections = (
        "## Agent 指令文件：不要重命名 `CLAUDE.md`",
        "## 阶段 0 — Preflight 与范围确认",
        "## 阶段 1 — Python 基础设施",
        "## 阶段 2 — 离线验证基线",
        "## 阶段 3 — 私有交易工作区",
        "## 阶段 4 — Secrets 与本地配置",
        "## 阶段 5 — 可选 OpenD 与 broker 基础设施",
        "## 阶段 6 — 可选 Operations 基础设施",
        "## 完成门槛与交接报告",
        "## 常见问题与回答",
    )
    for section in required_sections:
        assert section in chinese

    shared_anchors = (
        "python -m pip install -e '.[dev]'",
        "python -m pytest -q",
        "git diff --check",
        "trade/README.md",
        "git check-ignore -v",
        ".env.example",
        ".mcp.json.example",
        "AGENTS.md",
        "CLAUDE.md",
    )
    for anchor in shared_anchors:
        assert anchor in english
        assert anchor in chinese
