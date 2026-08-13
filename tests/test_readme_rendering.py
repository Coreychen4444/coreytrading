"""Root README Markdown must render consistently under CommonMark rules."""

import re
import unicodedata
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOLD_SPAN = re.compile(r"\*\*([^*\n]+)\*\*")


def _has_ambiguous_bold_closer(line: str) -> bool:
    for match in BOLD_SPAN.finditer(line):
        following = line[match.end() : match.end() + 1]
        if not following:
            continue
        preceding_is_punctuation = unicodedata.category(match.group(1)[-1]).startswith("P")
        following_is_punctuation = unicodedata.category(following).startswith("P")
        if preceding_is_punctuation and not following.isspace() and not following_is_punctuation:
            return True
    return False


def test_bold_boundary_detector_matches_commonmark_punctuation_case():
    assert _has_ambiguous_bold_closer("**操作边界：**机器")
    assert not _has_ambiguous_bold_closer("**操作边界：** 机器")
    assert not _has_ambiguous_bold_closer("**plain text**word")


def test_root_readme_bold_closers_have_safe_boundaries():
    for filename in ("README.md", "README_CN.md"):
        lines = (ROOT / filename).read_text(encoding="utf-8").splitlines()
        violations = [
            (line_number, line)
            for line_number, line in enumerate(lines, start=1)
            if _has_ambiguous_bold_closer(line)
        ]
        assert not violations, f"{filename} has ambiguous bold boundaries: {violations}"
