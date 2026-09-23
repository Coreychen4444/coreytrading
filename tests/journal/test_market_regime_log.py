"""大盘状态账本（`journal/market_regime_log`）：深度 + 缓存接缝、(date, bench) 幂等、账本自对账。"""
import datetime as dt
import json

from quant.core import io_json
from quant.core import market_regime as MR
from quant.journal import market_regime_log as L


def _write(path, bars):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(b) + "\n" for b in bars))


def _series(n, start="2020-01-01", base=100.0):
    d0 = dt.date.fromisoformat(start)
    out, c = [], base
    for i in range(n):
        c += 1.0 if (i // 30) % 2 == 0 else -0.7
        out.append({"time": f"{(d0 + dt.timedelta(days=i)).isoformat()} 00:00:00",
                    "open": c, "high": c + 0.6, "low": c - 0.6, "close": c, "volume": 1})
    return out


def test_stitch_deep_then_cache_without_duplicates(tmp_path):
    bars = _series(320)
    _write(tmp_path / "deep" / "QQQ.jsonl", bars[:300])
    _write(tmp_path / "cache" / "QQQ.jsonl", bars[280:])          # 重叠 20 根
    got = L.load_bench_bars("QQQ", deep_dir=tmp_path / "deep", cache_dir=tmp_path / "cache")
    days = [L._day(b) for b in got]
    assert days == sorted(set(days)) and len(got) == 320
    assert L._day(L.load_bench_bars("QQQ", "2020-02-01", deep_dir=tmp_path / "deep",
                                    cache_dir=tmp_path / "cache")[-1]) == "2020-02-01"


def test_cache_only_when_deep_missing(tmp_path):
    _write(tmp_path / "cache" / "SPY.jsonl", _series(300))
    got = L.load_bench_bars("SPY", deep_dir=tmp_path / "deep", cache_dir=tmp_path / "cache")
    assert len(got) == 300


def test_update_is_idempotent_and_reports_short_history(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(io_json, "STATE_LOCK_PATH", tmp_path / ".state.lock")   # 不碰仓库 var/
    for t in MR.BENCHMARKS:
        _write(tmp_path / "cache" / f"{t}.jsonl", _series(400 if t != "SMH" else 100))
    led = tmp_path / "ledger.jsonl"
    kw = dict(path=led, deep_dir=tmp_path / "deep", cache_dir=tmp_path / "cache")
    assert L.update(**kw) == 2                                    # SMH 日线不足 → 跳过并报
    assert "SMH 日线不足" in capsys.readouterr().err
    assert L.update(**kw) == 0                                    # 同日重跑不重复写
    rows = [json.loads(x) for x in led.read_text().splitlines()]
    assert {r["bench"] for r in rows} == {"SPY", "QQQ"}
    assert all(r["state"] in MR.STATES + (MR.UNKNOWN,) for r in rows)


def test_calibration_joins_later_snapshots(tmp_path):
    led = tmp_path / "ledger.jsonl"
    states = [MR.RANGE] * 6 + [MR.TREND_UP] * 4
    with open(led, "w") as f:
        for i, s in enumerate(states):
            f.write(json.dumps({"date": f"2026-01-{i + 1:02d}", "bench": "QQQ", "state": s,
                                "rates": {"5": {"rates": {MR.RANGE: 1.0}}}}) + "\n")
    cal = L.calibration(led, horizons=(5,))["QQQ"][5]
    assert cal["n"] == 5
    # t=0..4 → t+5 = RANGE(5) TREND_UP(6..9): 只有 t=0 同状态
    assert abs(cal["persist"] - 1 / 5) < 1e-9
    assert cal["brier"] is not None
