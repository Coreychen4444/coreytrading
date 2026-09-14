"""Fail fast when a production entry point starts under the wrong Python.

A scheduled job started with an interpreter that lacks the project dependencies
fails later with a misleading error (for example the exchange calendar reports
itself unavailable). This module names the real cause before any business work
starts. Pure check, no side effects.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

from .paths import ROOT

PROJECT_VENV = ROOT / ".venv" / "moomoo"
REQUIRED_MODULES = ("exchange_calendars", "moomoo")
OVERRIDE_ENV = "COREYTRADING_ALLOW_ANY_PYTHON"


def interpreter_report(executable: str | None = None, *, env: dict | None = None) -> dict:
    """Describe the running interpreter; never raises."""
    env = os.environ if env is None else env
    exe = Path(executable or sys.executable)
    # Compare unresolved paths: the venv's python3 is a symlink to the base
    # interpreter, so resolve() would point outside the venv for a correct run.
    inside = PROJECT_VENV in exe.parents
    if executable is None:
        # The running process: the conventional project venv, or any active virtualenv
        # (a public checkout may keep its environment elsewhere). Dependencies are still required.
        inside = inside or Path(sys.prefix) == PROJECT_VENV or sys.prefix != sys.base_prefix
    missing = [name for name in REQUIRED_MODULES if importlib.util.find_spec(name) is None]
    overridden = bool(env.get(OVERRIDE_ENV))
    return {"executable": str(exe), "project_venv": str(PROJECT_VENV),
            "inside_project_venv": inside, "missing_modules": missing,
            "override": overridden, "ok": overridden or (inside and not missing)}


def require_project_interpreter(executable: str | None = None) -> dict:
    """Exit with an actionable message unless the project interpreter is in use."""
    report = interpreter_report(executable)
    if report["ok"]:
        return report
    reason = ("missing modules " + ", ".join(report["missing_modules"])
              if report["missing_modules"] else "not the project virtualenv")
    raise SystemExit(
        f"wrong interpreter: {report['executable']} ({reason}). "
        f"Run with {PROJECT_VENV / 'bin' / 'python3'} (set {OVERRIDE_ENV}=1 to bypass).")
