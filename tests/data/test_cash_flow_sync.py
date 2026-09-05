"""data/cash_flow_sync —— 券商流水 → 外部流账本 (只收出入金)。

合成流水: USD 换汇 OUT −1500 / 货基赎回 IN 1500 / SGD 换汇 IN 2010 / SGD 银证转出 −2010 ——
四行只有最后一行是出入金, 折 USD 要靠同日换汇备注里的 1.34。
"""
import contextlib
import json

import pytest

from quant.data import cash_flow_sync as cfs

RAW_0828 = [
    {"cashflow_id": "40001", "clearing_date": "2026-08-28", "settlement_date": "2026-08-28", "currency": "USD",
     "cashflow_type": "Fund Redemption", "cashflow_direction": "IN", "cashflow_amount": 2.5,
     "cashflow_remark": "Fund Redemption#SYNTH USD Money Market Fund"},
    {"cashflow_id": "40002", "clearing_date": "2026-08-28", "settlement_date": "2026-08-31", "currency": "USD",
     "cashflow_type": "Others", "cashflow_direction": "IN", "cashflow_amount": 3000.0, "cashflow_remark": ""},
]
RAW_0831 = [
    {"cashflow_id": "40011", "clearing_date": "2026-08-31", "settlement_date": "2026-08-31", "currency": "USD",
     "cashflow_type": "Currency Exchange", "cashflow_direction": "OUT", "cashflow_amount": -1500.0,
     "cashflow_remark": "TRANSFER FROM UNIVERSAL SECURITIES ACCOUNT (USD -> SGD 1.34)"},
    {"cashflow_id": "40012", "clearing_date": "2026-08-31", "settlement_date": "2026-08-31", "currency": "USD",
     "cashflow_type": "Fund Redemption", "cashflow_direction": "IN", "cashflow_amount": 1500.0,
     "cashflow_remark": "Fund Redemption#SYNTH USD Money Market Fund"},
    {"cashflow_id": "40013", "clearing_date": "2026-08-31", "settlement_date": "2026-08-31", "currency": "SGD",
     "cashflow_type": "Currency Exchange", "cashflow_direction": "IN", "cashflow_amount": 2010.0,
     "cashflow_remark": "TRANSFER TO UNIVERSAL SECURITIES ACCOUNT (USD -> SGD 1.34)"},
    {"cashflow_id": "40014", "clearing_date": "2026-08-31", "settlement_date": "2026-08-31", "currency": "SGD",
     "cashflow_type": "Bank Transfer Withdrawals", "cashflow_direction": "OUT", "cashflow_amount": -2010.0,
     "cashflow_remark": ""},
]
BOOK = {"2026-08-28": RAW_0828, "2026-08-31": RAW_0831}


@pytest.fixture
def env(tmp_path, monkeypatch):
    ledger = tmp_path / "cash_flows.jsonl"
    side = tmp_path / "cash_flows.sync.json"
    monkeypatch.setattr(cfs, "CASH_FLOWS_PATH", ledger)
    monkeypatch.setattr(cfs, "CASH_FLOWS_SYNC_PATH", side)
    return ledger, side


def _rows(path):
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()] if path.exists() else []


def test_only_external_flows_land_and_are_priced_in_usd(env):
    ledger, side = env
    calls = []

    def fetch(day):
        calls.append(day)
        return BOOK.get(day, [])

    res = cfs.sync(["2026-08-28", "2026-08-29", "2026-08-30", "2026-08-31"], fetch, synced_at="t0")
    assert calls == ["2026-08-28", "2026-08-29", "2026-08-30", "2026-08-31"]
    rows = _rows(ledger)
    assert len(rows) == 1 and rows[0]["usd_amount"] == -1500.0 and rows[0]["currency"] == "SGD"
    assert rows[0]["date"] == "2026-08-31" and rows[0]["type"] == "Bank Transfer Withdrawals"
    assert rows[0]["key"] == "2026-08-31:40014" and rows[0]["source"] == cfs.SOURCE
    assert "1.34" in rows[0]["usd_basis"]
    assert res["seen_types"] == {"Fund Redemption": 2, "Others": 1, "Currency Exchange": 2,
                                 "Bank Transfer Withdrawals": 1}
    assert res["unpriced"] == [] and res["errors"] == {}
    assert json.loads(side.read_text())["synced_through"] == "2026-08-31" == res["synced_through"]


def test_rerun_is_idempotent_and_synced_through_never_moves_back(env):
    ledger, side = env
    cfs.sync(["2026-08-31"], lambda d: BOOK.get(d, []), synced_at="t0")
    res = cfs.sync(["2026-08-28", "2026-08-31"], lambda d: BOOK.get(d, []), synced_at="t1")
    assert res["new"] == [] and res["existing"] == 1 and len(_rows(ledger)) == 1
    res = cfs.sync(["2026-08-20"], lambda d: [], synced_at="t2")      # 回看更早的日子不倒退
    assert res["synced_through"] == "2026-08-31"


def test_fetch_error_stops_synced_through_at_the_last_good_day(env):
    ledger, side = env

    def fetch(day):
        if day == "2026-08-30":
            raise RuntimeError("boom")
        return BOOK.get(day, [])

    res = cfs.sync(["2026-08-28", "2026-08-29", "2026-08-30", "2026-08-31"], fetch, synced_at="t0")
    assert res["queried"] == ["2026-08-28", "2026-08-29"] and "2026-08-30" in res["errors"]
    assert res["synced_through"] == "2026-08-29" and _rows(ledger) == []   # 8/31 那笔没查到, 不能假装同步过


def test_unpriced_external_flow_is_written_and_flagged(env):
    ledger, _ = env
    raw = [dict(RAW_0831[3], cashflow_id="9")]                      # 只有 SGD 转出, 没有同日换汇
    res = cfs.sync(["2026-08-31"], lambda d: raw, synced_at="t0")
    assert len(res["unpriced"]) == 1 and _rows(ledger)[0]["usd_amount"] is None
    assert "未折算" in _rows(ledger)[0]["usd_basis"]


def test_manual_entry_is_deduped_and_marked(env):
    ledger, _ = env
    r1 = cfs.append_manual("2026-07-01", 500.0, "入金 (人工补录)", synced_at="t0")
    r2 = cfs.append_manual("2026-07-01", 500.0, "again", synced_at="t1")
    assert r1["written"] and not r2["written"]
    rows = _rows(ledger)
    assert len(rows) == 1 and rows[0]["source"] == "manual" and rows[0]["usd_amount"] == 500.0
    assert rows[0]["direction"] == "IN" and rows[0]["key"] == "manual:2026-07-01:+500.00"


def test_cli_days_window_and_manual(env, monkeypatch, capsys):
    ledger, side = env
    seen = []

    @contextlib.contextmanager
    def fake_fetcher():
        def fetch(day):
            seen.append(day)
            return BOOK.get(day, [])
        yield fetch

    monkeypatch.setattr(cfs, "opend_fetcher", fake_fetcher)
    monkeypatch.setattr(cfs, "et_today", lambda: "2026-09-05")
    assert cfs.main(["--days", "7", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert seen == cfs.day_range("2026-08-30", "2026-09-05") and out["queried"][-1] == "2026-09-05"
    assert len(_rows(ledger)) == 1                                   # 8/31 那笔已在 7 日窗内落账
    assert cfs.main(["--start", "2026-08-28", "--end", "2026-08-31"]) == 0
    assert "已有 1" in capsys.readouterr().out and len(_rows(ledger)) == 1   # 幂等
    assert cfs.main(["--manual", "--date", "2026-07-01", "--usd-amount", "-100", "--note", "x", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["written"] is True


def test_cli_without_opend_reports_and_returns_nonzero(env, monkeypatch, capsys):
    @contextlib.contextmanager
    def dead():
        raise ConnectionRefusedError("OpenD down")
        yield  # noqa: unreachable — contextmanager 需要生成器

    monkeypatch.setattr(cfs, "opend_fetcher", dead)
    assert cfs.main(["--json"]) == 1
    assert "connect" in json.loads(capsys.readouterr().out)["errors"]


def test_day_range_is_inclusive_and_order_insensitive():
    assert cfs.day_range("2026-08-30", "2026-09-01") == ["2026-08-30", "2026-08-31", "2026-09-01"]
    assert cfs.day_range("2026-09-01", "2026-08-30") == ["2026-08-30", "2026-08-31", "2026-09-01"]


def test_rate_limit_is_self_throttled_and_high_frequency_error_retried_once(env):
    """券商限频 20 次 / 30 秒 ( 回填实测第 21 日即被拒)。滑动窗自限 + 撞限频等一窗重试一次;
    仍失败才停 (synced_through 不越过失败日)。"""
    ledger, side = env
    clock = [0.0]
    slept = []

    def now():
        return clock[0]

    def sleep(sec):
        slept.append(sec)
        clock[0] += sec

    days = cfs.day_range("2026-07-01", "2026-07-25")                 # 25 日 > 20 次窗
    calls = []
    res = cfs.sync(days, lambda d: calls.append(d) or [], synced_at="t", now=now, sleep=sleep)
    assert len(calls) == 25 and res["queried"] == days and res["errors"] == {}
    assert len(slept) == 1 and slept[0] >= cfs.RATE_WINDOW_S            # 第 21 次前睡满一窗

    hits = {"n": 0}

    def flaky(day):
        hits["n"] += 1
        if hits["n"] == 1:
            raise RuntimeError("Get Cash Flow Summary request failed due to high frequency. Maximum 20 times per 30 seconds.")
        return []

    slept.clear()
    res = cfs.sync(["2026-08-01"], flaky, synced_at="t", now=now, sleep=sleep)
    assert res["queried"] == ["2026-08-01"] and hits["n"] == 2 and slept == [cfs.RATE_WINDOW_S + 0.5]

    def always(day):
        raise RuntimeError("high frequency")

    res = cfs.sync(["2026-08-02"], always, synced_at="t", now=now, sleep=sleep)
    assert res["queried"] == [] and "2026-08-02" in res["errors"]
