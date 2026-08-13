"""Auto-load the repo-root `.env` into os.environ.

Import for side effect, first thing in any module that talks to OpenD:
    from quant.core import env  # noqa: F401

Existing environment variables always win — this only fills gaps.
"""
from __future__ import annotations

import os

from .paths import ENV_PATH


def _load_dotenv() -> None:
    if not ENV_PATH.exists():
        return
    with ENV_PATH.open() as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip()
            if not os.environ.get(key):        # never override the real environment
                os.environ[key] = val


_load_dotenv()
