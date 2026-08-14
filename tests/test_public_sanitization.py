"""Regression checks for public attribution and annotation sanitization."""

from __future__ import annotations

import ast
import io
from pathlib import Path
import re
import tokenize


ROOT = Path(__file__).resolve().parents[1]
DATE = re.compile(r"(?<!\d)20\d{2}-(?:0[1-9]|1[0-2])(?:-(?:0[1-9]|[12]\d|3[01]))?(?!\d)")
PERSONAL_TOKEN = re.compile("co" + "rey", re.IGNORECASE)
BRAND_TOKEN = re.compile(
    r"(?<![A-Za-z0-9])" + "co" + r"reytrading(?![A-Za-z0-9])",
    re.IGNORECASE,
)
EXCLUDED_PARTS = {".git", "." + "co" + "reytrading-sync", ".pytest_cache", "__pycache__"}
TEXT_SUFFIXES = {".md", ".py", ".json", ".jsonl", ".toml", ".txt", ".yaml", ".yml", ".sh"}


def _public_files():
    for path in ROOT.rglob("*"):
        if not path.is_file() or any(part in EXCLUDED_PARTS for part in path.relative_to(ROOT).parts):
            continue
        yield path


def _docstring_spans(tree: ast.AST) -> set[tuple[int, int, int, int]]:
    spans = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list) or not body:
            continue
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
                and isinstance(first.value.value, str):
            value = first.value
            spans.add((value.lineno, value.col_offset, value.end_lineno, value.end_col_offset))
    return spans


def _contains_personal_token_outside_brand(value: str) -> bool:
    return PERSONAL_TOKEN.search(BRAND_TOKEN.sub("", value)) is not None


def test_brand_exception_is_exact():
    assert not _contains_personal_token_outside_brand("coreytrading")
    assert not _contains_personal_token_outside_brand("COREYTRADING_STATE_LOCK_PATH")
    assert _contains_personal_token_outside_brand("co" + "rey")
    assert _contains_personal_token_outside_brand("co" + "reytradingextra")


def test_public_paths_and_text_do_not_contain_personal_token_outside_brand():
    violations = []
    for path in _public_files():
        relative = path.relative_to(ROOT).as_posix()
        if _contains_personal_token_outside_brand(relative):
            violations.append(relative)
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        text = path.read_text(encoding="utf-8")
        if _contains_personal_token_outside_brand(text):
            violations.append(relative)
    assert violations == []


def test_public_markdown_has_no_literal_calendar_annotations():
    violations = []
    for path in _public_files():
        if path.suffix.lower() != ".md":
            continue
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if DATE.search(line):
                violations.append(f"{path.relative_to(ROOT)}:{line_no}")
    assert violations == []


def test_python_comments_and_docstrings_have_no_literal_calendar_annotations():
    violations = []
    for path in _public_files():
        if path.suffix.lower() != ".py":
            continue
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text)
        docstrings = _docstring_spans(tree)
        for token in tokenize.generate_tokens(io.StringIO(text).readline):
            span = (token.start[0], token.start[1], token.end[0], token.end[1])
            is_annotation = token.type == tokenize.COMMENT or (
                token.type == tokenize.STRING and span in docstrings
)
            if is_annotation and DATE.search(token.string):
                violations.append(f"{path.relative_to(ROOT)}:{token.start[0]}")
    assert violations == []
