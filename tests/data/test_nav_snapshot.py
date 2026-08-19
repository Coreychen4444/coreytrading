"""Synthetic tests for one-row-per-day NAV refresh semantics."""
import json

import pytest

from quant.data import nav_snapshot as ns


@pytest.fixture
def env(tmp_path, monkeypatch):
    path = tmp_path / "nav_history.jsonl"
    monkeypatch.setattr(ns, "NAV_HISTORY_PATH", path)
    monkeypatch.setattr(ns, "fetch_benchmarks",
                        lambda: {"qqq_close": 500.0, "spy_close": 600.0})

    def _run(day, total):
        monkeypatch.setattr(ns, "et_today", lambda: day)
        monkeypatch.setattr(ns, "fetch_account_usd", lambda: {
            "total_assets": total, "cash": 0.0, "market_val": total,
            "power": total, "risk_status": "SAFE", "currency": "USD"})
        monkeypatch.setattr(ns.sys, "argv", ["nav_snapshot.py"])
        assert ns.main() == 0
        return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]

    return _run


def test_first_write_of_the_day_appends(env):
    rows = env("2026-08-19", 10000.0)
    assert len(rows) == 1
    assert rows[0]["date"] == "2026-08-19" and rows[0]["total_assets"] == 10000.0
    assert "refreshed" not in rows[0]


def test_same_day_rerun_replaces_instead_of_skipping(env):
    """postclose 的收盘读数必须覆盖 briefing 的盘前占位 —— 每日恒一行。"""
    env("2026-08-19", 10000.0)            # briefing 06:31 ET 盘前
    rows = env("2026-08-19", 9800.0)     # postclose 16:10 ET 收盘
    assert len(rows) == 1, "同日刷新不许变两行 (每 ET 日恒一行)"
    assert rows[0]["total_assets"] == 9800.0, "后写胜出: 收盘读数覆盖盘前占位"
    assert rows[0]["refreshed"] is True


def test_new_day_appends_and_keeps_history(env):
    env("2026-08-19", 10000.0)
    env("2026-08-19", 9800.0)
    rows = env("2026-08-20", 10100.0)
    assert [r["date"] for r in rows] == ["2026-08-19", "2026-08-20"]
    assert rows[0]["total_assets"] == 9800.0, "历史行不许被新日期触碰"


def test_account_fetch_failure_leaves_file_untouched(env, monkeypatch):
    env("2026-08-19", 10000.0)
    monkeypatch.setattr(ns, "fetch_account_usd", lambda: None)
    monkeypatch.setattr(ns, "et_today", lambda: "2026-08-19")
    monkeypatch.setattr(ns.sys, "argv", ["nav_snapshot.py"])
    assert ns.main() == 1
    rows = [json.loads(l) for l in ns.NAV_HISTORY_PATH.read_text().splitlines() if l.strip()]
    assert len(rows) == 1 and rows[0]["total_assets"] == 10000.0
