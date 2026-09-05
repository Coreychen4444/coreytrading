"""core/book —— 组合层风控上下文的**唯一**装配点。

为什么值得单独钉:  之前 NAV / K 线 / 组合占用 的读法在 entry_guard、
portfolio_brief、price_reminder_sync 里各存一份 (期权 delta 更是各自 BS 反解),
读同一批文件却是三份代码。这个文件测的不是"能读出数", 是"读法只有一份"。
"""
import json

import pytest

from quant.core import book


def _write(tmp_path, ticker, closes, high_pad=1.0):
    p = tmp_path / f"{ticker}.jsonl"
    with p.open("w") as f:
        for i, c in enumerate(closes):
            f.write(json.dumps({"time": f"2026-01-{i + 1:02d} 00:00:00",
                                "open": c, "high": c + high_pad,
                                "low": c - high_pad, "close": c}) + "\n")
    return p


@pytest.fixture(autouse=True)
def _clean():
    book.clear_cache()
    yield
    book.clear_cache()


def test_price_and_atr_from_kline_cache(tmp_path):
    _write(tmp_path, "AAOI", [100.0 + i for i in range(40)])
    assert book.price_of("AAOI", tmp_path) == 139.0
    atr = book.atr20_of("AAOI", tmp_path)
    assert atr is not None and atr > 0


def test_atr_returns_none_rather_than_a_makeshift_number(tmp_path):
    """序列不够 Wilder 暖机 → None。凑合的 ATR 会直接变成一个错的 size 上限。"""
    _write(tmp_path, "NEW", [10.0, 11.0, 12.0])
    assert book.atr20_of("NEW", tmp_path) is None
    assert book.price_of("NEW", tmp_path) == 12.0


def test_missing_cache_is_empty_not_an_exception(tmp_path):
    assert book.bars("NOPE", tmp_path) == []
    assert book.price_of("NOPE", tmp_path) is None
    assert book.atr20_of("NOPE", tmp_path) is None


def test_load_usage_reads_broker_delta_not_a_bs_reverse_solve(tmp_path):
    """期权敞口用**落库的券商 delta**。

     之前只有 entry_guard 会 BS 反解, 别的 caller 拿不到 delta 就按
    1.0 保守计 —— 同一本书在两个模块里算出两个 gross。现在 delta 住在
    broker block 里, 谁读都是同一个数。
    """
    _write(tmp_path, "GOOGL", [350.0 + (i % 5) for i in range(40)])
    nav = 20000.0
    doc = {"tickers": {"GOOGL270115C390000": {
        "instrument": {"type": "option",
                       "option": {"underlying": "GOOGL", "strike": 390.0,
                                  "expiry": "2027-01-15", "side": "C"}},
        "broker": {"qty": 1, "current_price": 20.5, "delta": 0.40}}}}
    u = book.load_usage(doc, nav, kdir=tmp_path)
    spot = book.price_of("GOOGL", tmp_path)
    atr = book.atr20_of("GOOGL", tmp_path)
    assert u["assumed_full_delta"] == []
    #  双口径: gross = premium 市值; broker delta 只进冲击占用
    assert u["used_gross"] == pytest.approx(100 * 20.5 / nav, rel=1e-9)
    assert u["used_budget"] == pytest.approx(
        (0.40 * 100 * spot / nav) * 1.88 * (atr / spot), rel=1e-6)


def test_load_usage_without_delta_stays_conservative_and_says_so(tmp_path):
    """没落到 delta = 冲击占用按 1.0 计 (高估) **并报出来**, 不是静默跳过。"""
    _write(tmp_path, "GOOGL", [350.0 + (i % 5) for i in range(40)])
    doc = {"tickers": {"GOOGL270115C390000": {
        "instrument": {"type": "option",
                       "option": {"underlying": "GOOGL", "strike": 390.0,
                                  "expiry": "2027-01-15", "side": "C"}},
        "broker": {"qty": 1, "current_price": 20.5}}}}
    u = book.load_usage(doc, 20000.0, kdir=tmp_path)
    assert u["assumed_full_delta"] == ["GOOGL270115C390000"]
    # 保守 1.0 只作用于冲击占用 (delta=1.0 的标的名义); gross = premium 市值
    assert u["used_budget"] > book.load_usage({"tickers": {}}, 20000.0, kdir=tmp_path)["used_budget"]
    assert u["used_gross"] == pytest.approx(100 * 20.5 / 20000.0, rel=1e-9)


# ── 购买力余额 = 第⑥条臂 (用户  Q2) ──────────────────────────────

def _nav_row(**kw):
    row = {"date": "2026-08-11", "total_assets": 21000.0, "cash": 0.0,
           "market_val": 3800.0, "power": 17200.0}
    row.update(kw)
    return row


def _write_nav(tmp_path, monkeypatch, row):
    p = tmp_path / "nav_history.jsonl"
    p.write_text(json.dumps(row) + "\n")
    monkeypatch.setattr(book, "NAV_HISTORY_PATH", p)


def test_power_room_subtracts_todays_buys(tmp_path, monkeypatch):
    """快照是 eod_only —— 盘中买完它不动, 不减掉当天成交同一笔钱会被授权两次。"""
    _write_nav(tmp_path, monkeypatch, _nav_row())
    ev = tmp_path / "2026-08" / "trades.jsonl"
    ev.parent.mkdir()
    ev.write_text("\n".join(json.dumps(r) for r in [
        {"timestamp": "2026-08-11T10:00:00-04:00", "event_type": "fill", "side": "BUY",
         "ticker": "AMZN", "qty": 10, "price": 200.0},
        {"timestamp": "2026-08-11T11:00:00-04:00", "event_type": "fill", "side": "SELL",
         "ticker": "AMZN", "qty": 5, "price": 210.0},          # 卖出不加回 (保守)
        {"timestamp": "2026-08-10T10:00:00-04:00", "event_type": "fill", "side": "BUY",
         "ticker": "MSFT", "qty": 1, "price": 500.0},          # 昨天的已在快照里
    ]) + "\n")
    out = book.power_room("2026-08-11", events_dir=tmp_path)
    assert out["spent_today"] == 2000.0
    assert out["room"] == pytest.approx(15200.0)


def test_power_room_flags_leverage_and_ignores_broken_cash_field(tmp_path, monkeypatch):
    """power 是券商口径购买力不是现金: 一旦带杠杆这条臂会自动变宽, 必须置位报出来。
    `cash` 字段不可用 ( 读 0.0 / 历史出现负数), 这里钉的是它不进计算。"""
    _write_nav(tmp_path, monkeypatch, _nav_row(power=34000.0, cash=-1223.0))
    lev = book.power_room("2026-08-11", events_dir=tmp_path)
    assert lev["leveraged"] is True
    assert lev["implied_cash"] == pytest.approx(17200.0)     # total − market_val
    assert lev["room"] == pytest.approx(34000.0)             # cash 字段没参与
    _write_nav(tmp_path, monkeypatch, _nav_row(cash=-1223.0))
    assert book.power_room("2026-08-11", events_dir=tmp_path)["leveraged"] is False


def test_power_room_missing_snapshot_is_unknown_not_zero(tmp_path, monkeypatch):
    _write_nav(tmp_path, monkeypatch, _nav_row(power=None))
    assert book.power_room("2026-08-11", events_dir=tmp_path)["room"] is None


def test_power_room_frozen_inputs_match_disk_calculator(tmp_path, monkeypatch):
    account = _nav_row()
    events = [{"timestamp": "2026-08-11T10:00:00-04:00", "event_type": "fill", "side": "BUY",
               "ticker": "SYN270115C100000", "qty": 2, "price": 3.0},
              {"timestamp": "2026-08-11T11:00:00-04:00", "event_type": "fill", "side": "SELL",
               "ticker": "SYN", "qty": 4, "price": 100.0}]
    _write_nav(tmp_path, monkeypatch, account)
    directory = tmp_path / "2026-08"
    directory.mkdir()
    (directory / "trades.jsonl").write_text("\n".join(json.dumps(e) for e in events))
    expected = book.power_room("2026-08-11", events_dir=tmp_path)

    def forbidden(*args, **kwargs):
        raise AssertionError("frozen power calculator read disk defaults")

    monkeypatch.setattr(book, "latest_account", forbidden)
    monkeypatch.setattr(book, "iter_events", forbidden)
    assert book.power_room("2026-08-11", account=account, events=events) == expected
    assert expected["spent_today"] == 600
    assert book.power_room("2026-08-11", account={}, events=[])["room"] is None
