"""Synthetic coverage for breakdown and ATR-scaled staleness triggers."""
import json

import pytest

from quant.report import portfolio_brief as pb


@pytest.fixture
def kdir(tmp_path, monkeypatch):
    d = tmp_path / "klines"
    d.mkdir()
    monkeypatch.setattr(pb, "KLINE_CACHE_DIR", d)

    def _write(ticker, bars):
        d.joinpath(f"{ticker}.jsonl").write_text(
            "\n".join(json.dumps(b) for b in bars), encoding="utf-8")
    return _write


def _bar(t, c):
    return {"time": t, "open": c, "high": c * 1.01, "low": c * 0.99, "close": c}


CFG = {"accumulation_zone_1": [95.0, 100.0], "accumulation_zone_2": [84.0, 90.0],
       "trim_zone": [128.0, 138.0], "valid_until": "2099-01-01"}


def test_breakdown_stale_on_day_one(kdir):
    kdir("TST", [_bar("2026-08-18", 92.0), _bar("2026-08-19", 81.0)])
    reasons = pb.zone_staleness("TST", CFG)
    assert any("Breakdown stale" in r and "当日" in r for r in reasons), reasons


def test_breakout_stale_still_needs_two_closes(kdir):
    """上行保持 ×2 — 挂单只是不成交, 无执行危害, 确认避免 churn。"""
    kdir("UPT", [_bar("2026-08-18", 120.0), _bar("2026-08-19", 140.0)])
    assert not any("Breakout" in r for r in pb.zone_staleness("UPT", CFG))
    kdir("UPT", [_bar("2026-08-18", 139.0), _bar("2026-08-19", 140.0)])
    assert any("Breakout stale" in r for r in pb.zone_staleness("UPT", CFG))


def test_volatility_stale_scaled_by_atr(kdir):
    calm = [_bar(f"2026-06-{i + 1:02d}", 100.0) for i in range(20)]
    kdir("VOL", calm + [_bar("2026-06-21", 92.0)])      # -8% ≈ 3.2×ATR14(~2%)
    assert any("Volatility stale" in r for r in pb.zone_staleness("VOL", CFG))
    kdir("VOL", calm + [_bar("2026-06-21", 97.0)])      # -3% < 2×ATR14 → 沉默
    assert not any("Volatility" in r for r in pb.zone_staleness("VOL", CFG))


def test_no_kline_no_false_triggers(kdir):
    assert pb.zone_staleness("GHOST", CFG) == []
