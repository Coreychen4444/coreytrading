"""core/nav —— NAV 区间变动剔除外部现金流。

合成源案例: 区间首日 NAV 20,000 → 末日 18,900, 首尾相减报 −5.50%; 期间一笔提款 USD 1,500
(SGD 2,010.00 @1.34), 剔除后其实是 +2.00%。口径三条: 外部流只认银证转账 / 非 USD 按同日换汇
备注汇率折 / 非快照日的流归到下一个快照日。
"""
import pytest

from quant.core import nav

FX_USD = {"currency": "USD", "type": "Currency Exchange", "amount": -1500.0,
          "remark": "TRANSFER FROM UNIVERSAL SECURITIES ACCOUNT (USD -> SGD 1.34)"}
FX_SGD = {"currency": "SGD", "type": "Currency Exchange", "amount": 2010.0,
          "remark": "TRANSFER TO UNIVERSAL SECURITIES ACCOUNT (USD -> SGD 1.34)"}
WITHDRAW = {"currency": "SGD", "type": "Bank Transfer Withdrawals", "amount": -2010.0, "remark": ""}
REDEEM = {"currency": "USD", "type": "Fund Redemption", "amount": 1500.0,
          "remark": "Fund Redemption#SYNTH USD Money Market Fund"}
SETTLE = {"currency": "USD", "type": "Others", "amount": 3000.0, "remark": ""}
DAY_0831 = [FX_USD, REDEEM, FX_SGD, WITHDRAW]

NAV = [  # 合成 nav_history (total_assets / QQQ / SPY)
    {"date": "2026-08-28", "total_assets": 20000.0, "qqq_close": 500.00, "spy_close": 600.00},
    {"date": "2026-08-31", "total_assets": 18300.0, "qqq_close": 501.00, "spy_close": 598.80},
    {"date": "2026-09-01", "total_assets": 18100.0, "qqq_close": 497.50, "spy_close": 596.40},
    {"date": "2026-09-02", "total_assets": 18500.0, "qqq_close": 499.00, "spy_close": 599.40},
    {"date": "2026-09-03", "total_assets": 18700.0, "qqq_close": 503.50, "spy_close": 602.40},
    {"date": "2026-09-04", "total_assets": 18900.0, "qqq_close": 502.50, "spy_close": 601.20},
]
FLOW_0831 = {"date": "2026-08-31", "usd_amount": -1500.0, "currency": "SGD", "amount": -2010.0,
             "type": "Bank Transfer Withdrawals"}


def test_only_bank_transfers_are_external_flows():
    """换汇两腿 / 货币基金申赎 / 交易结算都不是出入金 —— 它们要么 NAV 中性, 要么本来就是盈亏。"""
    assert nav.is_external_flow(WITHDRAW)
    assert nav.is_external_flow({"type": "Bank Transfer Deposits", "amount": 100.0, "currency": "USD"})
    for r in (FX_USD, FX_SGD, REDEEM, SETTLE):
        assert not nav.is_external_flow(r), r["type"]


def test_sgd_withdrawal_is_priced_by_the_paired_fx_leg():
    """提款前那一次 USD→SGD 换汇就是为这笔提款做的: SGD 腿同金额, 备注里就是汇率 → USD −1,500.00 整。"""
    usd, basis = nav.usd_equivalent(WITHDRAW, DAY_0831)
    assert usd == -1500.0 and "同日换汇配对" in basis and "1.34" in basis


def test_fx_fallback_uses_any_same_day_rate_and_no_rate_means_none():
    partial = {"currency": "SGD", "type": "Bank Transfer Withdrawals", "amount": -1000.0}
    usd, basis = nav.usd_equivalent(partial, [FX_USD])          # 金额配不上, 但同日有 USD↔SGD 汇率
    assert usd == pytest.approx(-1000.0 / 1.34, abs=0.01) and "未配对" in basis
    usd, basis = nav.usd_equivalent(partial, [SETTLE, REDEEM])  # 没有任何换汇腿 → 不猜
    assert usd is None and "未折算" in basis
    usd, basis = nav.usd_equivalent(partial, [{"currency": "HKD", "type": "Currency Exchange", "amount": 1.0,
                                               "remark": "(USD -> HKD 7.8)"}])   # 汇率不是这个币种对
    assert usd is None


def test_usd_row_needs_no_fx_and_broker_raw_keys_are_accepted():
    assert nav.usd_equivalent({"currency": "USD", "type": "Bank Transfer Deposits", "amount": 500.0}, []) == (500.0, "USD 原币")
    raw = {"currency": "SGD", "cashflow_type": "Bank Transfer Withdrawals", "cashflow_amount": -2010.0,
           "cashflow_remark": ""}
    raw_fx = {"currency": "SGD", "cashflow_type": "Currency Exchange", "cashflow_amount": 2010.0,
              "cashflow_remark": "(USD -> SGD 1.34)"}
    assert nav.is_external_flow(raw)
    assert nav.usd_equivalent(raw, [raw_fx])[0] == -1500.0


def test_change_report_matches_the_synthetic_case():
    """账面 −5.50% 是提款; 剔除后 +$400 (+2.00%), TWR +2.25% vs QQQ +0.50% / SPY +0.20%。"""
    r = nav.change_report(NAV, [FLOW_0831], days=7)
    assert (r["start"]["date"], r["end"]["date"]) == ("2026-08-28", "2026-09-04")
    assert r["raw_change_pct"] == -5.5 and r["raw_change_usd"] == pytest.approx(-1100.0)
    assert r["flow_usd_total"] == -1500.0 and len(r["flows"]) == 1
    assert r["adjusted_change_usd"] == pytest.approx(400.0) and r["adjusted_change_pct"] == 2.0
    assert r["twr_pct"] == pytest.approx(2.25, abs=0.01)
    assert r["benchmarks"] == {"qqq_pct": 0.5, "spy_pct": 0.2}
    assert r["snapshots"] == 5 and r["unpriced_flows"] == []
    # 没有账本 = 提款被记成亏损 —— 这正是要修的形状
    assert nav.change_report(NAV, [], days=7)["adjusted_change_pct"] == -5.5


def test_weekend_flow_rolls_into_next_snapshot_for_twr():
    """周六清算的入金没有对应 NAV 行, 归到周一那一步; TWR 与把它记在周一完全相同。"""
    sat = dict(FLOW_0831, date="2026-08-29")
    assert nav.change_report(NAV, [sat], days=7)["twr_pct"] == nav.change_report(NAV, [FLOW_0831], days=7)["twr_pct"]
    # 区间外的流不计
    assert nav.change_report(NAV, [dict(FLOW_0831, date="2026-08-20")], days=7)["flow_usd_total"] == 0.0


def test_unpriced_flow_is_reported_not_silently_dropped():
    r = nav.change_report(NAV, [dict(FLOW_0831, usd_amount=None)], days=7)
    assert r["flow_usd_total"] == 0.0 and len(r["unpriced_flows"]) == 1 and r["adjusted_change_pct"] == -5.5


def test_window_selection_and_degenerate_inputs():
    s, e = nav.select_window(NAV, days=7)
    assert (s["date"], e["date"]) == ("2026-08-28", "2026-09-04")
    s, e = nav.select_window(NAV, start="2026-09-01", end="2026-09-03")
    assert (s["date"], e["date"]) == ("2026-09-01", "2026-09-03")
    s, e = nav.select_window(NAV, days=2, end="2026-09-02")          # 08-31 ≤ 09-02 − 2 天
    assert (s["date"], e["date"]) == ("2026-08-31", "2026-09-02")
    assert nav.select_window(NAV[:1], days=7) == (None, NAV[0])         # 一行 = 没有区间
    assert nav.change_report(NAV[:1], [], days=7) is None
    assert nav.change_report([], [], days=7) is None
    assert nav.select_window(NAV, end="2020-01-01") == (None, None)
