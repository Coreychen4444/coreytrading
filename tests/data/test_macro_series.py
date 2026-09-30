"""宏观日线缓存（data/macro_series）：解析口径（复权 / 收益率 ×10 老口径 / 交易所时区）与尾部覆盖语义。"""
import json

import pytest

from quant.data import macro_series as MS


def _payload(sym, tz, ts, close, adj=None):
    ind = {"quote": [{"close": close}]}
    if adj is not None:
        ind["adjclose"] = [{"adjclose": adj}]
    return {"chart": {"result": [{"meta": {"symbol": sym, "exchangeTimezoneName": tz},
                                  "timestamp": ts, "indicators": ind}]}}


def test_parse_yahoo_prefers_adjclose_for_etfs_and_skips_nulls():
    p = _payload("HYG", "America/New_York", [1790000000, 1790086400, 1790172800],
                 [80.0, None, 81.0], adj=[70.0, None, 71.0])
    assert [r["value"] for r in MS.parse_yahoo(p, "adj")] == [70.0, 71.0]
    assert [r["value"] for r in MS.parse_yahoo(p, "close")] == [80.0, 81.0]


def test_parse_yahoo_normalizes_times_ten_yield_convention():
    p = _payload("^TNX", "America/New_York", [1790000000, 1790086400], [46.3, 4.63])
    assert [r["value"] for r in MS.parse_yahoo(p, "close")] == [pytest.approx(4.63)] * 2


def test_parse_yahoo_dates_in_exchange_timezone_and_last_bar_wins():
    # 23:00 UTC = 伦敦次日 00:00（夏令）；同日两根取最后一根
    ts = [1790031600, 1790031600 + 3600]
    p = _payload("USDJPY=X", "Europe/London", ts, [150.0, 151.0])
    rows = MS.parse_yahoo(p, "close")
    assert rows == [{"date": "2026-09-22", "value": 151.0}]


def test_parse_fred_skips_missing_dots():
    rows = MS.parse_fred("observation_date,DGS10\n2026-09-24,5.10\n2026-09-25,.\n2026-09-26,5.17\n")
    assert rows == [{"date": "2026-09-24", "value": 5.10}, {"date": "2026-09-26", "value": 5.17}]


def test_merge_overwrites_tail_and_drops_withdrawn_bars():
    old = [{"date": "2026-09-20", "value": 1.0}, {"date": "2026-09-21", "value": 2.0},
           {"date": "2026-09-22", "value": 99.0}]           # 22 日是当时的未定型值
    fresh = [{"date": "2026-09-21", "value": 2.5}, {"date": "2026-09-23", "value": 3.0}]
    assert MS.merge(old, fresh) == [{"date": "2026-09-20", "value": 1.0}, *fresh]
    assert MS.merge(old, []) == old


def test_update_backfills_then_refetches_only_the_tail(tmp_path, monkeypatch):
    calls = []

    def fake_fetch(key, start):
        calls.append(start)
        if len(calls) == 1:
            return [{"date": "2026-09-01", "value": 1.0}, {"date": "2026-09-28", "value": 2.0}]
        return [{"date": "2026-09-28", "value": 2.2}, {"date": "2026-09-29", "value": 3.0}]

    monkeypatch.setattr(MS, "fetch", fake_fetch)
    assert MS.update("WTI", backfill="2026-09-01", root=tmp_path) == 2
    assert MS.update("WTI", root=tmp_path) == 2
    assert calls[1] == "2026-09-16"                       # 最后一日 − TAIL_DAYS
    assert MS.load("WTI", tmp_path) == [("2026-09-01", 1.0), ("2026-09-28", 2.2), ("2026-09-29", 3.0)]
    lines = (tmp_path / "WTI.jsonl").read_text().splitlines()
    assert [json.loads(x)["date"] for x in lines] == ["2026-09-01", "2026-09-28", "2026-09-29"]
