"""Frozen evidence stays detached and never promotes an unfinished close."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from quant.core.agent_contracts import validate_snapshot
from quant.ops import agent_snapshots as snapshots


def test_snapshot_is_detached_and_binds_all_data():
    data = {"fact": {"value": 12}}
    packet = snapshots.make_snapshot(data)
    data["fact"]["value"] = 99
    assert packet["data"]["fact"]["value"] == 12
    validate_snapshot(packet, now=datetime.now(timezone.utc))
    packet["data"]["fact"]["value"] = 42
    with pytest.raises(ValueError, match="hash"):
        validate_snapshot(packet)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), object()])
def test_snapshot_rejects_non_json_values(bad):
    with pytest.raises(ValueError):
        snapshots.make_snapshot({"bad": bad})


def _bar(day, **extra):
    return {"time": day, "open": 100.0, "high": 104.0, "low": 99.0,
            "close": 102.0, "volume": 10000, **extra}


def test_intraday_daily_bar_is_not_a_completed_close():
    # Summer 18:00 UTC = 14:00 ET.
    now = datetime(2026, 9, 4, 18, tzinfo=timezone.utc)
    rows = [_bar("2026-09-03"), _bar("2026-09-04"), _bar("2026-09-05")]
    assert [b["time"] for b in snapshots.completed_bars(rows, now)] == ["2026-09-03"]
    assert len(snapshots.completed_bars(rows, now.replace(hour=21))) == 2


def test_explicit_incomplete_bar_is_rejected_even_after_close():
    assert snapshots.completed_bars([_bar("2026-09-04", complete=False)],
                                    datetime(2026, 9, 5, tzinfo=timezone.utc)) == []


def test_holiday_and_weekend_bars_are_never_promoted_as_completed_sessions():
    rows = [_bar("2026-09-04"), _bar("2026-09-05"), _bar("2026-09-07")]
    now = datetime(2026, 9, 7, 22, tzinfo=timezone.utc)
    assert [b["time"] for b in snapshots.completed_bars(rows, now)] == ["2026-09-04"]


def test_half_day_bar_is_completed_at_actual_exchange_close():
    rows = [_bar("2026-11-25"), _bar("2026-11-26"), _bar("2026-11-27")]
    before = datetime(2026, 11, 27, 17, 59, 59, tzinfo=timezone.utc)
    assert [b["time"] for b in snapshots.completed_bars(rows, before)] == ["2026-11-25"]
    assert [b["time"] for b in snapshots.completed_bars(rows, before + timedelta(seconds=1))] == ["2026-11-25", "2026-11-27"]
    with pytest.raises(ValueError, match="timezone"):
        snapshots.completed_bars(rows, before.replace(tzinfo=None))


def test_duplicate_or_inconsistent_bars_are_not_silently_repaired():
    now = datetime(2026, 9, 5, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="increasing"):
        snapshots.completed_bars([_bar("2026-09-03"), _bar("2026-09-03")], now)
    with pytest.raises(ValueError, match="OHLC"):
        snapshots.completed_bars([_bar("2026-09-03", low=110)], now)


def test_capture_uses_only_explicit_fixture_paths_and_does_not_write(tmp_path):
    card = {"instrument": {"type": "stock"}, "agent": {"zones": None}, "status": "watching"}
    watchlist = tmp_path / "watchlist.json"
    watchlist.write_text(json.dumps({"tickers": {"SYNTH": card}}))
    kdir = tmp_path / "klines"
    kdir.mkdir()
    bars = [_bar((datetime(2026, 5, 1) + timedelta(days=i)).date().isoformat()) for i in range(80)]
    (kdir / "SYNTH.jsonl").write_text("\n".join(json.dumps(bar) for bar in bars))
    before = {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    result = snapshots.capture_card("SYNTH", "watchlist", watchlist_path=watchlist,
                                    kline_dir=kdir, macro_path=tmp_path / "missing.json",
                                    now=datetime(2026, 7, 21, tzinfo=timezone.utc))
    assert result["data"]["level_map"] is not None
    assert "macro_prior_unavailable" in result["data"]["capture_warnings"]
    assert {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before


def test_ticker_cannot_escape_evidence_directory():
    with pytest.raises(ValueError, match="ticker"):
        snapshots.capture_card("../private", "watchlist")


def _capture_with_macro(tmp_path, macro, *, now):
    card = {"instrument": {"type": "stock"},
            "agent": {"zones": None, "valuation": {"as_of": "2099-08-31", "fair_value": 100}},
            "status": "watching"}
    watchlist = tmp_path / "watchlist.json"
    watchlist.write_text(json.dumps({"tickers": {"SYN": card}}))
    path = tmp_path / "current.json"
    path.write_text(macro if isinstance(macro, str) else json.dumps(macro))
    return snapshots.capture_card("SYN", "watchlist", watchlist_path=watchlist,
                                  kline_dir=tmp_path / "missing_klines", macro_path=path, now=now)


MACRO_EXAMPLE = Path("ai_news/examples/macro-state.example.json")


def _macro_example():
    if not MACRO_EXAMPLE.exists():
        pytest.skip("optional intelligence workspace (schema + example) is not part of this repository")
    macro = json.loads(MACRO_EXAMPLE.read_text())
    macro["example_only"] = False
    return macro


@pytest.mark.parametrize("macro", ["{bad JSON", {"risk_environment": {"level": "supportive"}},
                                    {"generated_at": "2099-09-09T00:00:00Z"}])
def test_malformed_macro_is_unavailable_instead_of_becoming_fresh_evidence(macro, tmp_path):
    output = _capture_with_macro(tmp_path, macro, now=datetime(2099, 9, 10, tzinfo=timezone.utc))
    assert output["data"]["macro"] is None
    assert "macro_prior_invalid" in output["data"]["capture_warnings"]
    assert output["data"]["source_times"]["macro_status"] == "invalid"
    validate_snapshot(output)


def test_macro_schema_authority_failure_is_rejected_at_capture(tmp_path):
    macro = _macro_example()
    macro["authority"]["may_write_caps"] = True
    output = _capture_with_macro(tmp_path, macro, now=datetime(2099, 9, 10, tzinfo=timezone.utc))
    assert output["data"]["macro"] is None
    assert "macro_prior_invalid" in output["data"]["capture_warnings"]


def test_future_macro_is_not_accepted_even_if_its_schema_is_valid(tmp_path):
    macro = _macro_example()
    generated = datetime.fromisoformat(macro["generated_at"]["utc"].replace("Z", "+00:00"))
    output = _capture_with_macro(tmp_path, macro, now=generated - timedelta(seconds=1))
    assert output["data"]["macro"] is None
    assert "macro_prior_future" in output["data"]["capture_warnings"]


def test_capture_preserves_source_clocks_without_inventing_a_ttl_policy(tmp_path):
    macro = _macro_example()
    generated = datetime.fromisoformat(macro["generated_at"]["utc"].replace("Z", "+00:00"))
    # Keeping an old prior does not make its contents new; no new age threshold
    # or trading veto is invented by the adapter.
    now = generated + timedelta(days=4)
    output = _capture_with_macro(tmp_path, macro, now=now)
    times = output["data"]["source_times"]
    assert output["data"]["macro"] == macro
    assert times["macro_status"] == "valid"
    assert times["captured_at"] == output["as_of"] == now.isoformat()
    assert times["macro_generated_at"] == macro["generated_at"]["utc"]
    assert times["macro_evidence_cutoff"] == macro["data_quality"]["cutoff_utc"]
    assert times["valuation_as_of"] == "2099-08-31"
    assert times["cache_not_refreshed"] is times["snapshot_as_of_is_capture_time"] is True


def test_example_marker_and_nonfinite_macro_never_become_real_evidence(tmp_path):
    macro = _macro_example()
    generated = datetime.fromisoformat(macro["generated_at"]["utc"].replace("Z", "+00:00"))
    macro["example_only"] = True
    output = _capture_with_macro(tmp_path, macro, now=generated)
    assert output["data"]["source_times"]["macro_status"] == "invalid"
    macro["example_only"] = False
    macro["risk_environment"]["score"] = float("nan")
    output = _capture_with_macro(tmp_path, macro, now=generated)
    assert output["data"]["macro"] is None
    assert "macro_prior_invalid" in output["data"]["capture_warnings"]
