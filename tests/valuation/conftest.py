"""估值层测试的合成原料: 稳态增长公司的三表 + 快照, 不碰 OpenD / live 缓存 / live inputs。"""
import math

import pytest

from quant.valuation import fundamentals as F

FIELD = {**{k: ("inc", v) for k, v in F.INCOME_FIELDS.items()},
         **{k: ("bal", v) for k, v in F.BALANCE_FIELDS.items()},
         **{k: ("cf", v) for k, v in F.CASHFLOW_FIELDS.items()}}


def _report(period, ftype, year, ts, items):
    return {"period_text": period, "financial_type": ftype, "fiscal_year": year, "date_time": ts,
            "date_time_str": f"{year}-{ftype*3:02d}-30",
            "item_list": [{"field_id": fid, "data": val} for fid, val in items.items()]}


def make_reports(n_q=50, rev0=1e9, g_annual=0.10, margin=0.30, tax=0.21, shares=100e6,
                 net_interest_q=1e7, cash=2e9, debt=5e8, equity=3e9, goodwill=0.0,
                 capex_ratio=0.05, start_year=2014, margin_series=None, buyback_q=1e7):
    """按季复利增长的公司。返回 (income, balance, cashflow) 三个 report_list (升序)。"""
    qg = (1 + g_annual) ** 0.25 - 1
    inc, bal, cf = [], [], []
    rev = rev0
    for i in range(n_q):
        year, q = start_year + i // 4, i % 4 + 1
        period, ts = f"{year}/Q{q}", year * 10000 + q * 100
        m = margin_series[i] if margin_series is not None else margin
        op = rev * m
        pre = op + net_interest_q
        tx = pre * tax if pre > 0 else 0.0
        ni = pre - tx
        da = rev * 0.04
        capex = -rev * capex_ratio
        inc.append(_report(period, q, year, ts, {
            8001: rev, 8004: rev * 0.6, 8010: rev * 0.1, 8017: op, 8018: net_interest_q, 8019: net_interest_q,
            8020: 0.0, 8022: 0.0, 8023: 0.0, 8034: pre, 8035: tx, 8046: ni, 8048: ni / shares, 8049: 0.0}))
        bal.append(_report(period, q, year, ts, {
            8003: cash, 8028: 0.0, 8024: 1e9, 8039: goodwill, 8001: 6e9, 8048: 3e9, 8002: 3e9, 8049: 1e9,
            8058: 0.0, 8069: debt, 8060: 0.0, 8070: 0.0, 8081: equity}))
        cf.append(_report(period, q, year, ts, {
            8015: ni + da, 8019: da, 8044: capex, 8046: capex, 8072: ni + da + capex, 8061: 0.0,
            8059: -buyback_q, 8058: 0.0}))
        rev *= 1 + qg
    return inc, bal, cf


def make_bundle(ticker="SYN", price=50.0, pe_hist=None, forward=None, **kw):
    inc, bal, cf = make_reports(**kw)
    rows = F.parse_quarters(inc, bal, cf)
    shares = kw.get("shares", 100e6)
    return {
        "ticker": ticker, "fetched_at": "2026-09-02T00:00:00Z", "source": "synthetic",
        "quarters": rows,
        "snapshot": {"last_price": price, "update_time": "2026-09-01T16:00:00-04:00", "pe_ttm": 20.0, "pe_static": 22.0,
                     "pb": 5.0, "total_market_val": price * shares, "outstanding_shares": shares, "issued_shares": shares,
                     "eps_ttm_snapshot": 2.5, "dividend_ratio_ttm": 0.0, "high_52w": price * 1.2, "low_52w": price * 0.8},
        "valuation_pe": pe_hist if pe_hist is not None else
            {"type": "PE", "interval": "5Y", "current": 20.0, "average": 20.0, "minus_1sd": 16.0, "plus_1sd": 24.0,
             "percentile": 50.0, "forward": 18.0, "last_update": "2026-09-01"},
        "forward": forward,      # None = 无一致预期 (回测同款); 传 dict 走一致预期锚定
        "consensus": {"highest": 70.0, "average": 60.0, "lowest": 45.0, "rating": 4, "total": 10,
                      "update_time_str": "2026-09-01", "buy": 80.0, "hold": 20.0, "sell": 0.0},
    }


class FakeSource:
    """OpenD 数据源替身: 记录调用, 可指定某票抛错。"""

    def __init__(self, fail=()):
        self.calls, self.fail, self.closed = [], set(fail), False

    def statements(self, code, statement_type, num):
        self.calls.append(("statements", code, statement_type))
        t = code.split(".")[-1]
        if t in self.fail:
            raise RuntimeError("boom")
        inc, bal, cf = make_reports(n_q=min(num, 50))
        return {1: inc, 2: bal, 3: cf}[statement_type]

    def snapshot(self, code):
        self.calls.append(("snapshot", code))
        return {"last_price": 50.0, "update_time": "2026-09-01 16:00:00", "pe_ttm_ratio": 20.0, "pe_ratio": 22.0,
                "pb_ratio": 5.0, "total_market_val": 50.0 * 100e6, "outstanding_shares": 100e6, "issued_shares": 100e6,
                "earning_per_share": 2.5, "dividend_ratio_ttm": float("nan"), "highest52weeks_price": 60.0,
                "lowest52weeks_price": 40.0}

    def valuation(self, code, valuation_type=1, interval_type=6):
        self.calls.append(("valuation", code, valuation_type))
        if valuation_type == 3:      # PS: forward 4.0 → 前瞻收入 = 市值 / 4
            return {"valuation_type": "PS", "last_update_time_str": "2026-09-01",
                    "trend": {"current_value": 5.0, "average_value": 5.0, "forward_value": 4.0}}
        return {"valuation_type": "PE", "last_update_time_str": "2026-09-01",
                "trend": {"current_value": 20.0, "average_value": 20.0, "avg_minus_1_stddev": 16.0,
                          "avg_plus_1_stddev": 24.0, "valuation_percentile": 50.0, "forward_value": 18.0}}

    def consensus(self, code):
        self.calls.append(("consensus", code))
        return {"highest": 70.0, "average": 60.0, "lowest": 45.0, "rating": 4, "total": 10,
                "update_time_str": "2026-09-01", "buy": 80.0, "hold": 20.0, "sell": 0.0}

    def close(self):
        self.closed = True


def make_klines(n=260, beta=1.5, start_price=100.0, seed=0):
    """SPY 与个股日线: 个股对数收益 = beta × 市场 (无噪声 → 回归 β 精确)。"""
    import datetime as dt
    import random
    rng = random.Random(seed)
    d0 = dt.date(2025, 1, 1)
    spy, tk = [], []
    ps, pt = start_price, start_price
    for i in range(n):
        rm = rng.gauss(0.0004, 0.01)
        ps *= math.exp(rm)
        pt *= math.exp(beta * rm)
        t = (d0 + dt.timedelta(days=i)).isoformat() + " 00:00:00"
        spy.append({"time": t, "close": ps})
        tk.append({"time": t, "close": pt})
    return tk, spy


@pytest.fixture
def bundle():
    return make_bundle()


@pytest.fixture
def inputs_doc():
    return {"tickers": {
        "SYN": {"archetype": "platform", "rationale": "合成平台公司"},
        "CYC": {"archetype": "semi_cyclical", "rationale": "合成周期公司"},
        "NA1": {"archetype": "not_applicable", "rationale": "合成不适用"},
    }}


@pytest.fixture
def fake_source():
    return FakeSource()
