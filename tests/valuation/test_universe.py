"""valuation/universe —— 估值层默认范围 = 持仓 ∪ 候选, 不是 inputs.json 全表 ( 幽灵标的案)。"""
from quant.sync import state_io
from quant.valuation import universe as U


def test_coverage_tickers_reads_state_and_drops_tombstones(monkeypatch):
    pos = {"tickers": {"NBIS": {"broker": {"qty": 20}}, "NVDA270319C220000": {"broker": {"qty": 1}},
                       "AMZN": {"status": "active", "broker": {"qty": 0}},          # 期权已平, status 没人改
                       "SPCX": {"broker": {"qty": 1}, "instrument": {"role": "commemorative_hold"}}}}
    wl = {"tickers": {"AMKR": {"status": "watching", "moomoo_listed": False},      # 移出自选的墓碑
                      "MU": {"status": "watching", "moomoo_listed": True},
                      "PLTR": {"status": "watching"}}}
    monkeypatch.setattr(state_io, "read_positions", lambda **k: pos)
    monkeypatch.setattr(state_io, "read_watchlist", lambda **k: wl)
    assert U.coverage_tickers() == ["MU", "NBIS", "NVDA", "PLTR"]


def test_coverage_tickers_drops_funds_even_when_held_through_an_option(monkeypatch):
    """基金票 (ETF) 没有 EPS 可估 —— 留着 = 日更每天一行「不出数」。"""
    pos = {"tickers": {"ETFX270617C560000": {"broker": {"qty": 1}}, "NBIS": {"broker": {"qty": 20}}}}
    wl = {"tickers": {"ETFX": {"status": "watching", "instrument": {"type": "stock", "security_type": "ETF"}},
                      "MU": {"status": "watching"}}}
    monkeypatch.setattr(state_io, "read_positions", lambda **k: pos)
    monkeypatch.setattr(state_io, "read_watchlist", lambda **k: wl)
    assert U.coverage_tickers() == ["MU", "NBIS"]
