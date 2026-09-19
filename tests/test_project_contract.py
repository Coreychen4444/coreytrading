"""The project-level agent contract must remain complete and operational."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "AGENTS.md"


def test_claude_contract_is_not_replaced_by_a_summary():
    text = CONTRACT.read_text(encoding="utf-8")
    # Character count: the contract is Chinese-heavy, so bytes would overstate it.
    assert len(text) >= 5_000
    assert len(text.splitlines()) >= 70


def test_claude_contract_covers_project_operating_domains():
    text = CONTRACT.read_text(encoding="utf-8")
    required_sections = (
        "## 身份与风格",
        "## 隐私与公开仓边界",
        "## 真源与指针",
        "## 常设裁决",
        "## 定时任务",
        "## Telegram",
        "## Session Behavior",
        "## 验证与发布门",
    )
    for section in required_sections:
        assert section in text


def test_claude_contract_names_critical_single_sources_of_truth():
    text = CONTRACT.read_text(encoding="utf-8")
    required_anchors = (
        "CLAUDE.md",
        "AI_BOOTSTRAP.md",
        "AI_BOOTSTRAP_CN.md",
        "trade/README.md",
        "quant/README.md",
        "agents/README.md",
        "agents/routing.json",
        "quant/core/holdings.py",
        "quant/core/sizing.py",
        "entry_cap()",
        "gate_count(card)",
        "quant/decision/entry_guard.py",
        "quant/decision/exit_guard.py",
        "quant/ops/pipeline.py",
        "quant/ops/agent_jobs.py",
        "quant/core/market_sessions.py",
        "quant/report/nav_report.py",
        "update_position_zones.py",
        "--read-only",
        "tests/test_public_sanitization.py",
    )
    for anchor in required_anchors:
        assert anchor in text
