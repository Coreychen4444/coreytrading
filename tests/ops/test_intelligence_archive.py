"""Historical path compatibility uses synthetic data; never rewrites evidence."""
import json
from pathlib import Path

import pytest

from quant.core import paths
from quant.ops import intelligence_archive


@pytest.fixture
def roots(tmp_path, monkeypatch):
    data = tmp_path / "trade/intelligence"
    runtime = tmp_path / "var/intelligence"
    for name, value in (("ROOT", tmp_path), ("INTELLIGENCE_DIR", data),
                        ("INTELLIGENCE_STATE_DIR", runtime)):
        monkeypatch.setattr(paths, name, value)
    return data, runtime


def test_old_relative_absolute_and_current_paths_find_same_bytes(roots, tmp_path):
    data, _ = roots
    path = data / "macro/snapshots/2026-09/run.json"
    path.parent.mkdir(parents=True)
    original = b'{"source":"ai_news/sources/original.json", "known_at":null}\n'
    path.write_bytes(original)
    for ref in ("ai_news/macro/snapshots/2026-09/run.json",
                tmp_path / "ai_news/macro/snapshots/2026-09/run.json",
                "trade/us_stock/intelligence/macro/snapshots/2026-09/run.json",
                tmp_path / "trade/us_stock/intelligence/macro/snapshots/2026-09/run.json", path):
        assert paths.resolve_intelligence_reference(ref) == path
        assert path.read_bytes() == original
    assert not (tmp_path / "ai_news").exists()


def test_runtime_claim_reference_uses_only_new_root(roots):
    _, runtime = roots
    assert paths.resolve_intelligence_reference("ai_news/state/macro-event-request.json") == runtime / "macro-event-request.json"


@pytest.mark.parametrize("reference", [
    "ai_news/../.env", "ai_news/reports/../../holding/state/positions.json",
    "trade/portfolio/state/positions.json", "/outside/ai_news/sources/x.json",
    "ai_news/PROMPT.md", "ai_news/not-data/x.json", "https://example.com/source",
])
def test_unrelated_traversal_and_non_data_references_rejected(roots, reference):
    with pytest.raises(ValueError):
        paths.resolve_intelligence_reference(reference)


def test_symlink_cannot_escape_data_root(roots, tmp_path):
    data, _ = roots
    data.mkdir(parents=True)
    (data / "sources").symlink_to(tmp_path)
    with pytest.raises(ValueError):
        paths.resolve_intelligence_reference("ai_news/sources/private.json")


def test_missing_evidence_is_explicit_and_never_created(roots, capsys):
    assert intelligence_archive.main(["ai_news/sources/missing.json"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["error"] == "evidence_missing"
    assert not Path(result["path"]).exists()
