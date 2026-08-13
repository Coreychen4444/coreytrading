"""skhy_premium math + parse regressions. 基准数字 = 2026-07-15 实盘:
000660 W2,101,000 / FX 1470 → FV $142.93; SKHY $193.92 → premium +35.7%."""
import pytest

from quant.data import skhy_premium as sp


def test_compute_premium_reference_case():
    fv, prem = sp.compute_premium(193.92, 2_101_000, 1470.0)
    assert fv == pytest.approx(142.925, abs=0.01)
    assert prem == pytest.approx(0.3568, abs=0.001)


def test_compute_premium_parity_and_discount():
    fv, prem = sp.compute_premium(142.93, 2_101_000, 1470.0)
    assert prem == pytest.approx(0.0, abs=1e-4)
    _, disc = sp.compute_premium(130.0, 2_101_000, 1470.0)
    assert disc < 0


def test_compute_premium_rejects_bad_input():
    with pytest.raises(ValueError):
        sp.compute_premium(0, 2_101_000, 1470.0)


def test_classify_gate_boundaries():
    assert sp.classify_gate(0.05) == "A_trigger(<=5%)"
    assert sp.classify_gate(0.08) == "approaching(5-10%)"
    assert sp.classify_gate(0.20) == "structural_band(10-20%)"
    assert sp.classify_gate(0.35) == "flow_premium(>20%)"


def test_parse_yahoo_chart_takes_last_non_null_in_exchange_tz():
    doc = {"chart": {"result": [{
        "meta": {"exchangeTimezoneName": "Asia/Seoul"},
        #  15:30 KST = 06:30 UTC (1784010600), 7-15 same time next day
        "timestamp": [1784010600, 1784097000],
        "indicators": {"quote": [{"close": [1_913_000.0, None]}]},
    }]}}
    d, c = sp.parse_yahoo_chart(doc)
    assert c == 1_913_000.0
    assert d == "2026-07-14"


def test_parse_yahoo_chart_raises_on_empty():
    doc = {"chart": {"result": [{"timestamp": [], "indicators": {"quote": [{}]}}]}}
    with pytest.raises(ValueError):
        sp.parse_yahoo_chart(doc)
