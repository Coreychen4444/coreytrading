"""Atomic JSON I/O + cross-process state lock — single implementation.

Every writer of state/positions.json or state/watchlist.json must hold
state_lock() around its full read→modify→write cycle. Writes are
tmp+fsync+rename atomic; the lock serializes concurrent merges (heartbeat
broker sync vs session zone update)."""
from __future__ import annotations

import fcntl
import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .paths import STATE_LOCK_PATH


def atomic_write_json(path: Path, data: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def read_json(path: Path) -> Any:
    """Read JSON, returning ``None`` when an optional runtime file is absent.

    Public and freshly initialized workspaces intentionally start without
    account state or generated market files. Callers already use ``or {}``
    where an empty document is valid; absence should not turn an offline test
    or first-run report into a traceback.
    """
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None


@contextmanager
def state_lock(lock_path: Path | None = None):
    lock_path = Path(lock_path or STATE_LOCK_PATH)  # resolved at call time (testable)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


def append_jsonl(path: Path, obj: dict, *, lock: bool = True) -> None:
    """Append one JSON line. Lock by default so concurrent appenders
    (heartbeat sync vs session) can't interleave partial lines."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(obj, ensure_ascii=False) + "\n"
    if lock:
        with state_lock():
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)
    else:
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)


def iter_jsonl(path: Path):
    path = Path(path)
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue
