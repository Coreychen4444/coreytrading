"""Real temporary Git history spans the rename without losing old snapshots."""
import json
import subprocess

import pytest

from quant.core.paths import POSITION_HISTORY_PATHS
from quant.data.position_history import list_position_commits, position_at
from quant.decision import exit_guard
from quant.research import zone_timeline


def git(root, *args):
    return subprocess.check_output(['git', *args], cwd=root, text=True).strip()


@pytest.fixture
def history(tmp_path, monkeypatch):
    monkeypatch.setenv('GIT_CONFIG_GLOBAL', '/dev/null')
    monkeypatch.setenv('GIT_CONFIG_SYSTEM', '/dev/null')
    monkeypatch.setenv('GIT_CONFIG_NOSYSTEM', '1')
    git(tmp_path, 'init', '-q', '--template=')
    git(tmp_path, 'config', 'user.name', 'Test')
    git(tmp_path, 'config', 'user.email', 'test@localhost')
    old = tmp_path / POSITION_HISTORY_PATHS[1]
    new = tmp_path / POSITION_HISTORY_PATHS[0]
    old.parent.mkdir(parents=True)
    old.write_text(json.dumps({'tickers': {'TEST': {'broker': {'current_price': 200, 'as_of': '2026-09-01'},
        'agent': {'zones': {'stop_level': 90}}}}}))
    git(tmp_path, 'add', '.'); git(tmp_path, 'commit', '-qm', 'old')
    first = git(tmp_path, 'rev-parse', 'HEAD')
    new.parent.mkdir(parents=True)
    git(tmp_path, 'mv', str(old), str(new))
    git(tmp_path, 'commit', '-qm', 'move')
    moved = git(tmp_path, 'rev-parse', 'HEAD')
    doc = json.loads(new.read_text())
    doc['tickers']['TEST']['broker']['current_price'] = 150
    doc['tickers']['TEST']['agent']['zones']['stop_level'] = 110
    new.write_text(json.dumps(doc))
    git(tmp_path, 'commit', '-qam', 'new')
    latest = git(tmp_path, 'rev-parse', 'HEAD')
    return tmp_path, (first, moved, latest)


def test_union_and_consumers_preserve_pre_migration_high(history, monkeypatch):
    root, commits = history
    assert [sha for sha, _ in list_position_commits(root, reverse=True)] == list(commits)
    assert position_at(commits[0], root) == position_at(commits[1], root)
    monkeypatch.setattr(exit_guard, 'ROOT', root)
    assert exit_guard.git_hwm_backfill() == {'TEST': (200.0, '2026-09-01')}
    monkeypatch.setattr(zone_timeline, 'ROOT', root)
    timeline = zone_timeline.build_timeline('TEST')
    assert [row['commit'] for row in timeline] == [commits[0][:9], commits[2][:9]]
    assert [row['zones']['stop_level'] for row in timeline] == [90, 110]


def test_conflicting_and_malformed_snapshots_fail(history):
    root, _ = history
    old = root / POSITION_HISTORY_PATHS[1]
    old.parent.mkdir(parents=True, exist_ok=True)
    old.write_text('{}')
    git(root, 'add', '.'); git(root, 'commit', '-qm', 'conflicting')
    with pytest.raises(ValueError, match='conflicting'):
        position_at(git(root, 'rev-parse', 'HEAD'), root)
    old.unlink()
    (root / POSITION_HISTORY_PATHS[0]).write_text('{invalid')
    git(root, 'add', '.'); git(root, 'commit', '-qm', 'malformed')
    with pytest.raises(ValueError):
        position_at(git(root, 'rev-parse', 'HEAD'), root)
    with pytest.raises(ValueError):
        position_at('--all', root)
