"""Public research keeps reusable Python while excluding private workbench files."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
RESEARCH = ROOT / "quant" / "research"
ALLOWED_DOCUMENTS = {"README.md", "README_CN.md", "study.example.md"}
PRIVATE_MARKERS = (
    "用户",
    "研究负责人",
    "我的",
    "我们",
    "源案例",
    "真实结论",
    "已执行裁决",
    "已进 memory",
    "/Users/",
    "/home/",
)
OCC_LITERAL = re.compile(r"(?<![A-Z0-9])[A-Z]{1,6}\d{6}[CP]\d{4,8}(?![A-Z0-9])")


def test_research_contains_only_python_readmes_and_synthetic_templates():
    invalid = sorted(
        path.relative_to(RESEARCH).as_posix()
        for path in RESEARCH.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
        and path.suffix != ".py" and path.name not in ALLOWED_DOCUMENTS
    )
    assert invalid == []


def test_research_python_has_no_private_history_markers_or_concrete_occ_symbols():
    violations = []
    for path in sorted(RESEARCH.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        markers = [marker for marker in PRIVATE_MARKERS if marker in text]
        if markers or OCC_LITERAL.search(text):
            violations.append({
                "path": path.relative_to(ROOT).as_posix(),
                "markers": markers,
                "occ_literal": bool(OCC_LITERAL.search(text)),
            })
    assert violations == []
