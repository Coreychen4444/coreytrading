"""iv_logger: 链接口限频 / 失败重试 / 漏记可见 / LEAPS 序列分位。行情本身 (OpenD) 不测。

get_option_chain 限频 10 次 / 30 秒: 不节流时每天只有按字母序前 10 只票落库, 其余静默失败,
IV 序列不可重拉 —— 这几条测试钉的是「不再静默」与「不再撞线」。"""
import json

import pytest

from quant.data import iv_logger


class _Clock:
    def __init__(self):
        self.t = 1000.0
        self.sleeps = []

    def monotonic(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


class _Ctx:
    def __init__(self, clock, fail_first=0):
        self.clock, self.calls, self.fail_first = clock, [], fail_first

    def get_option_chain(self, code, start=None, end=None, option_type=None):
        self.calls.append(self.clock.t)
        if len(self.calls) <= self.fail_first:
            return -1, "frequency limit"
        return 0, "chain"


@pytest.fixture
def clock(monkeypatch):
    c = _Clock()
    monkeypatch.setattr(iv_logger.time, "monotonic", c.monotonic)
    monkeypatch.setattr(iv_logger.time, "sleep", c.sleep)
    monkeypatch.setattr(iv_logger, "_last_chain", [0.0])
    return c


def test_chain_calls_are_spaced_under_the_rate_limit(clock):
    ctx = _Ctx(clock)
    for _ in range(12):
        assert iv_logger._option_chain(ctx, "US.X", "2026-11-06")[0] == 0
    gaps = [b - a for a, b in zip(ctx.calls, ctx.calls[1:])]
    assert min(gaps) >= iv_logger.CHAIN_MIN_INTERVAL_S - 1e-9
    # 任意 30 秒窗口内 ≤ 10 次
    assert all(sum(1 for t in ctx.calls if c <= t < c + 30) <= 10 for c in ctx.calls)


def test_chain_failure_waits_a_window_and_retries_once(clock):
    ctx = _Ctx(clock, fail_first=1)
    ret, _ = iv_logger._option_chain(ctx, "US.X", "2026-11-06")
    assert ret == 0 and len(ctx.calls) == 2
    assert iv_logger.CHAIN_RETRY_WAIT_S in clock.sleeps


def test_chain_gives_up_after_one_retry(clock):
    ctx = _Ctx(clock, fail_first=5)
    ret, _ = iv_logger._option_chain(ctx, "US.X", "2026-11-06")
    assert ret != 0 and len(ctx.calls) == 2


def test_log_today_reports_misses(tmp_path, monkeypatch):
    monkeypatch.setattr(iv_logger, "IV_HISTORY_DIR", tmp_path)
    monkeypatch.setattr(iv_logger, "et_today", lambda: "2026-10-12")
    monkeypatch.setattr(iv_logger, "fetch_atm_iv",
                        lambda t: None if t == "BAD" else {"date": "2026-10-12", "ticker": t, "atm_iv_pct": 40.0})
    missed = []
    rows = iv_logger.log_today(["AAA", "BAD", "CCC"], missed)
    assert [r["ticker"] for r in rows] == ["AAA", "CCC"] and missed == ["BAD"]
    assert (tmp_path / "AAA.jsonl").exists() and not (tmp_path / "BAD.jsonl").exists()


def test_main_exits_nonzero_when_any_ticker_missed(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(iv_logger, "IV_HISTORY_DIR", tmp_path)
    monkeypatch.setattr(iv_logger, "et_today", lambda: "2026-10-12")
    monkeypatch.setattr(iv_logger, "fetch_atm_iv", lambda t: None)
    monkeypatch.setattr("sys.argv", ["iv_logger.py", "--tickers", "AAA"])
    assert iv_logger.main() == 2
    assert "漏记" in capsys.readouterr().err


def test_percentile_reads_the_leaps_series_separately(tmp_path, monkeypatch):
    monkeypatch.setattr(iv_logger, "IV_HISTORY_DIR", tmp_path)
    monkeypatch.setattr(iv_logger, "et_today", lambda: "2026-10-12")
    rows = [{"date": f"2026-10-0{i}", "atm_iv_pct": 30.0 + i,
             **({"leaps": {"iv_pct": 50.0 - i}} if i >= 3 else {})} for i in range(1, 6)]
    (tmp_path / "X.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    atm = iv_logger.percentile("X")
    leaps = iv_logger.percentile("X", series="leaps")
    assert atm["samples"] == 5 and atm["latest_iv_pct"] == 35.0 and atm["iv_percentile"] == 100.0
    assert leaps["samples"] == 3 and leaps["latest_iv_pct"] == 45.0 and leaps["iv_percentile"] == pytest.approx(33.3)
