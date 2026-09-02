"""calibrate.py: 对账口径 (归类/得分/偏差) / 当时截断不偷看 / 回测端到端 / 账本对账 pending / 汇总 / 去重写入。"""
import json

import pytest

from quant.valuation import archetypes as A
from quant.valuation import calibrate as C
from quant.valuation import model as M
from tests.valuation.conftest import make_bundle, make_klines


def test_classify_and_score():
    assert C.classify(0.10, 0.10, 0.05) == "base"
    assert C.classify(0.14, 0.10, 0.05) == "bull" and C.classify(0.06, 0.10, 0.05) == "bear"
    assert C.classify(0.20, 0.10, 0.05) == "above_bull" and C.classify(0.0, 0.10, 0.05) == "below_bear"
    assert C.classify(0.3, 0.1, 0.0) == "base"
    probs = {"bear": 0.25, "base": 0.5, "bull": 0.25}
    assert C.score_band("base", probs) == 0.5 and C.score_band("above_bull", probs) == 0.0


def test_realized_and_predicted_agree_on_synthetic_steady_state():
    b = make_bundle(g_annual=0.10, margin=0.30)
    rows = b["quarters"]
    then = rows[-5]["period_end"]
    real = C.realized_between(rows, then, rows[-1]["period_end"])
    assert real["quarters"] == 4 and real["growth_annualized"] == pytest.approx(0.10, abs=1e-6) and real["opm"] == pytest.approx(0.30)
    assert C.realized_between(rows, rows[-1]["period_end"], rows[-1]["period_end"]) is None
    spec = {"revenue_base": 1e9, "growth_start": 0.10, "terminal_growth": 0.10, "horizon_years": 5, "fade": "flat", "half_life": None,
            "margin_start": 0.30, "target_margin": 0.30, "margin_fade_years": 1, "tax_rate": 0.21, "sales_to_capital": 2.0,
            "shares": 1e6, "share_change_rate": 0.0, "net_interest_after_tax": 0.0, "dps": 0.0}
    pred = C.predicted_at(spec, 1.0)
    assert pred["growth_annualized"] == pytest.approx(0.10) and pred["opm"] == pytest.approx(0.30)
    half = C.predicted_at(spec, 0.5)
    assert 0.09 < half["growth_annualized"] < 0.11
    rec = C.reconcile_prediction(spec, {"growth": 0.05, "margin": 0.03}, {"bear": 0.25, "base": 0.5, "bull": 0.25}, rows, then, rows[-1]["period_end"])
    assert rec["band_growth"] == "base" and rec["band_opm"] == "base" and rec["score_growth"] == 0.5
    assert abs(rec["bias_growth"]) < 1e-6 and abs(rec["bias_opm"]) < 1e-9


def test_bundle_as_of_truncates_and_blinds():
    b = make_bundle(n_q=20)
    cut = C.bundle_as_of(b, "2018-08-15", 42.0)          # lag 45 天 → 只保留 period_end ≤
    assert all(r["period_end"] <= "2018-07-01" for r in cut["quarters"]) and len(cut["quarters"]) < 20
    assert cut["snapshot"]["last_price"] == 42.0 and cut["valuation_pe"] is None and cut["consensus"] is None


def test_prices_and_beta_asof(tmp_path):
    tk, spy = make_klines(n=2200, beta=1.2)                 # ≈6 年日线 → 月度回归够 36 个月
    d = tmp_path / "deep"; d.mkdir()
    k = tmp_path / "cache"; k.mkdir()
    (d / "SYN.jsonl").write_text("\n".join(json.dumps(r) for r in tk[:2000]) + "\n")
    (k / "SYN.jsonl").write_text("\n".join(json.dumps(r) for r in tk[1900:]) + "\n")
    (d / "SPY.jsonl").write_text("\n".join(json.dumps(r) for r in spy) + "\n")
    pt = C.load_prices("SYN", deep_dir=d, cache_dir=k)
    assert len(pt) == 2200
    day = tk[2100]["time"][:10]
    assert C.price_on(pt, day) == (day, tk[2100]["close"]) and C.price_on(pt, "2000-01-01") is None
    ps = C.load_prices("SPY", deep_dir=d, cache_dir=k)
    bt = C.beta_asof(pt, ps, day)
    assert bt["raw"] == pytest.approx(1.2, abs=1e-6) and day[:7] in bt["window"] and bt["window"].endswith("月度")
    assert C.beta_asof(pt, ps, tk[300]["time"][:10]) is None, "as-of 截断后不足 36 个月 → None"


def test_backtest_end_to_end_on_synthetic():
    b = make_bundle(n_q=50, g_annual=0.10, margin=0.30)
    tk, spy = make_klines(n=5000, beta=1.0)
    import datetime as dt
    # 把 K 线日期对齐到季度表的年份范围 (2014–2026)
    base = dt.date(2013, 1, 1)
    pt = {(base + dt.timedelta(days=i)).isoformat(): r["close"] for i, r in enumerate(tk)}
    ps = {(base + dt.timedelta(days=i)).isoformat(): r["close"] for i, r in enumerate(spy)}
    resolved = A.resolve("SYN", {"tickers": {"SYN": {"archetype": "platform", "rationale": "合成"}}})
    rows = C.backtest_ticker(b, resolved, prices=pt, spy=ps, years=3, horizon_q=4)
    good = [r for r in rows if not r.get("skipped")]
    assert len(good) >= 8
    r = good[-1]
    assert r["kind"] == "backtest" and r["variant"] == "actual" and r["horizon_q"] == 4 and r["quarters"] == 4
    assert r["band_growth"] in C.BANDS and r["band_opm"] in C.BANDS and r["price_return"] is not None
    assert r["quarters_used"] < 50, "当时重估只用当时已知的季度"
    agg = C.aggregate(good)
    assert agg["ALL"]["n"] == len(good) and 0 <= agg["ALL"]["coverage_growth"] <= 1
    assert "platform" in agg and abs(agg["platform"]["bias_opm_mean"]) < 0.02
    txt = C.render_aggregate(agg, "t")
    assert "[ALL]" in txt and "覆盖" in txt


def test_reconcile_ledger_pending_then_done(tmp_path):
    b = make_bundle(n_q=50)
    rows_full = b["quarters"]
    then = rows_full[-3]["period_end"]
    spec = {"revenue_base": 1e9, "growth_start": 0.10, "terminal_growth": 0.03, "horizon_years": 10, "fade": "linear", "half_life": None,
            "margin_start": 0.30, "target_margin": 0.30, "margin_fade_years": 1, "tax_rate": 0.21, "sales_to_capital": 2.0,
            "shares": 1e6, "share_change_rate": 0.0, "net_interest_after_tax": 0.0, "dps": 0.0}
    ledger = {"SYN": {"ticker": "SYN", "timestamp": "2026-01-15T10:00:00-05:00", "price": 50.0, "fair_value": 60.0, "verdict": "偏低",
                      "archetype": "platform", "spec": spec, "scenarios": {"probabilities": {"bear": 0.25, "base": 0.5, "bull": 0.25}},
                      "scenario_deltas": {"growth": 0.05, "margin": 0.03}, "drivers": {"latest_period_end": rows_full[-1]["period_end"]}}}
    pending = C.reconcile_ledger(ledger, {"SYN": b}, {"SYN": {}}, {}, "2026-09-02")
    assert pending[0]["pending"].startswith("预测于")
    ledger["SYN"]["drivers"]["latest_period_end"] = then
    done = C.reconcile_ledger(ledger, {"SYN": b}, {"SYN": {"2026-09-01": 55.0}}, {"2026-01-15": 100.0, "2026-09-01": 110.0}, "2026-09-02")
    r = done[0]
    assert r["kind"] == "reconcile" and r["quarters"] == 2 and r["price_return"] == pytest.approx(0.10)
    assert r["excess_return"] == pytest.approx(0.10 - 0.10)
    n = C.write_rows(done, tmp_path / "cal.jsonl")
    assert n == 1 and C.write_rows(done, tmp_path / "cal.jsonl") == 0, "同一预测同一对账点不重复写"
    assert C.reconcile_ledger({"SYN": ledger["SYN"]}, {}, {}, {}, "2026-09-02")[0]["pending"] == "无基本面缓存"


def test_latest_ledger_rows_keeps_last_with_spec(tmp_path):
    p = tmp_path / "v.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in [
        {"ticker": "SYN", "timestamp": "a", "spec": {"x": 1}}, {"ticker": "SYN", "timestamp": "b", "spec": {"x": 2}},
        {"ticker": "NA1", "skipped": True}, {"ticker": "OLD", "timestamp": "c"}]) + "\n")
    rows = C.latest_ledger_rows(p)
    assert set(rows) == {"SYN"} and rows["SYN"]["spec"] == {"x": 2}
    assert M.valuate is not None
