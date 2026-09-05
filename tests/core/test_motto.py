"""core/motto —— 可选尾行: 未配置 = 所有 helper 原样返回; 配置了 = 幂等追加。"""
import importlib

from quant.core import motto


def test_unconfigured_is_a_no_op(monkeypatch):
    monkeypatch.setattr(motto, "MOTTO", "")
    monkeypatch.setattr(motto, "MOTTO_LINE", "")
    assert not motto.configured()
    assert motto.with_motto("body") == "body" and motto.with_motto("") == ""
    assert not motto.has_motto("anything")


def test_configured_line_is_appended_once(monkeypatch):
    monkeypatch.setattr(motto, "MOTTO", "synthetic reminder line")
    monkeypatch.setattr(motto, "MOTTO_LINE", motto.PREFIX + "synthetic reminder line")
    out = motto.with_motto("body")
    assert out == "body\n\n" + motto.MOTTO_LINE and motto.has_motto(out)
    assert motto.with_motto(out) == out                      # idempotent
    assert motto.with_motto("") == motto.MOTTO_LINE


def test_line_comes_from_the_environment_at_import(monkeypatch):
    monkeypatch.setenv(motto.ENV_VAR, "  from env  ")
    try:
        fresh = importlib.reload(motto)
        assert fresh.MOTTO == "from env" and fresh.MOTTO_LINE == fresh.PREFIX + "from env"
    finally:
        monkeypatch.delenv(motto.ENV_VAR, raising=False)
        importlib.reload(motto)
        assert not motto.configured()
