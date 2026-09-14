"""Interpreter guard: names the real cause of dependency failures."""
import pytest

from quant.core import interpreter


def test_project_interpreter_is_accepted():
    report = interpreter.require_project_interpreter()
    assert report["ok"] and report["inside_project_venv"] and not report["missing_modules"]


def test_foreign_interpreter_is_named_in_the_exit_message(monkeypatch):
    monkeypatch.delenv(interpreter.OVERRIDE_ENV, raising=False)
    report = interpreter.interpreter_report("/usr/bin/python3")
    assert report["ok"] is False and report["inside_project_venv"] is False
    with pytest.raises(SystemExit, match="wrong interpreter: /usr/bin/python3"):
        interpreter.require_project_interpreter("/usr/bin/python3")


def test_missing_dependency_is_reported_even_inside_venv(monkeypatch):
    monkeypatch.setattr(interpreter, "REQUIRED_MODULES", ("definitely_not_installed_xyz",))
    report = interpreter.interpreter_report()
    assert report["missing_modules"] == ["definitely_not_installed_xyz"] and report["ok"] is False
    with pytest.raises(SystemExit, match="missing modules definitely_not_installed_xyz"):
        interpreter.require_project_interpreter()


def test_explicit_override_is_recorded_not_silent(monkeypatch):
    monkeypatch.setenv(interpreter.OVERRIDE_ENV, "1")
    report = interpreter.interpreter_report("/usr/bin/python3")
    assert report["ok"] is True and report["override"] is True
