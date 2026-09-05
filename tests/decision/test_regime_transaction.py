"""Synthetic state interleavings: regime changes must not erase broker/card updates."""
import fcntl
import json
from contextlib import contextmanager

import pytest

from quant.core import io_json
from quant.decision import regime_transition as rt


@pytest.fixture
def state(monkeypatch, tmp_path):
    pos, wl = tmp_path / "positions.json", tmp_path / "watchlist.json"
    lock = tmp_path / ".state.lock"
    card = {"instrument": {"type": "stock"}, "status": "active", "broker": {"qty": 1},
            "agent": {"marker": "old"}, "lots": {"base": [], "position": []},
            "pending_orders": []}
    pos.write_text(json.dumps({"schema_version": 2, "tickers": {"SYN": card}}))
    wl.write_text(json.dumps({"schema_version": 2, "tickers": {}}))
    monkeypatch.setattr(io_json, "STATE_LOCK_PATH", lock)
    monkeypatch.setattr(rt.state_io, "POSITIONS_PATH", pos)
    monkeypatch.setattr(rt.state_io, "WATCHLIST_PATH", wl)
    monkeypatch.setattr(rt, "is_managed", lambda card: True)
    monkeypatch.setattr(rt, "detect_transition", lambda ticker, card, **kw: {"ticker": ticker})
    monkeypatch.setattr(rt, "apply_transition", lambda card, tr: card["agent"].update(marker="new"))
    # This test isolates transaction ownership; schema arithmetic is tested elsewhere.
    monkeypatch.setattr(rt.state_io, "write_positions", lambda doc: io_json.atomic_write_json(pos, doc))
    return pos, wl, lock


def test_read_and_write_are_both_inside_real_state_lock(state, monkeypatch):
    pos, _, lock = state
    original = rt.state_io.read_positions
    reads = []

    def read():
        with lock.open("a") as probe:
            with pytest.raises(BlockingIOError):
                fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
        reads.append(True)
        return original()

    monkeypatch.setattr(rt.state_io, "read_positions", read)
    assert rt.run(apply=True)["applied"] is True
    assert reads and json.loads(pos.read_text())["tickers"]["SYN"]["agent"]["marker"] == "new"


def test_broker_update_before_lock_acquisition_is_preserved(state, monkeypatch):
    pos, _, _ = state
    real_lock = rt.state_lock
    first = True

    @contextmanager
    def broker_wins_lock():
        nonlocal first
        with real_lock():
            if first:
                first = False
                doc = json.loads(pos.read_text())
                doc["tickers"]["SYN"]["broker"]["qty"] = 9
                doc["tickers"]["SYN"]["agent"]["independent_note"] = "keep"
                pos.write_text(json.dumps(doc))
            yield

    monkeypatch.setattr(rt, "state_lock", broker_wins_lock)
    rt.run(apply=True)
    card = json.loads(pos.read_text())["tickers"]["SYN"]
    assert card["broker"]["qty"] == 9
    assert card["agent"] == {"marker": "new", "independent_note": "keep"}


def test_preview_does_not_create_lock_or_write_state(state, monkeypatch):
    pos, wl, lock = state
    before = (pos.read_bytes(), wl.read_bytes())
    monkeypatch.setattr(rt, "state_lock", lambda: pytest.fail("preview must not lock/write"))
    out = rt.run(apply=False)
    assert out["transitions"] and not out["applied"]
    assert before == (pos.read_bytes(), wl.read_bytes())
    assert not lock.exists()
