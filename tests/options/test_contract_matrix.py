"""contract_matrix 纯计算层: 误差带 / S1 投影线 / 先触发止损 / 随机游走三结局 / 张数 / Δ 网格 / ★。
行情层 fetch_chains 不测 (要 OpenD)。"""
import math

import pytest

from quant.core.sizing import AT_RISK_CAP_FRAC, OPTION_ATRISK_LOSS_FRAC, option_premium_cap_frac, \
    reversal_probe_premium_cap
from quant.options import contract_matrix as cm
from quant.options import option_plan as op
from quant.options.strike_compare import bs_call


def _chain(spot=100.0, dte=300, iv=0.40, strikes=range(60, 141, 5), oi=500.0, spread=0.02):
    """BS 合成的一段链 → option_plan.enrich 之后的行 (与 live 同一条加工路径)。"""
    T = dte / 365.0
    raw = []
    for k in strikes:
        p = bs_call(spot, k, T, iv, cm.R)
        raw.append({"code": f"US.TEST{k}", "strike": float(k), "bid": p * (1 - spread / 2),
                    "ask": p * (1 + spread / 2), "oi": oi})
    return op.enrich(raw, spot, dte)


def test_move_bucket_boundaries():
    assert cm.move_bucket(-0.10) == "≤−10%"
    assert cm.move_bucket(-0.0999) == "−10~−3%"
    assert cm.move_bucket(-0.03) == "−10~−3%"
    assert cm.move_bucket(0.0) == "−3~+3%"
    assert cm.move_bucket(0.03) == "+3~+10%"
    assert cm.move_bucket(0.10) == "≥+10%"


def test_band_is_actual_minus_projection_and_snaps_to_nearest_horizon():
    p10, p90 = cm.BANDS[20]["≥+10%"]
    assert cm.band(25, 0.15) == (-p90, -p10)          # 25 日 → 实测只有 20 日档
    lo, hi = cm.band(60, 0.20)
    assert lo < 0 < hi and hi > 0.20                  # 大涨: 投影低估收益 → 区间偏上


def test_s1_level_prices_the_contract_at_the_s1_loss():
    S = cm.s1_level(10.0, 100.0, 300, 0.4, 0.20, 5, 100.0)
    assert S is not None and S < 100.0
    T = cm.t_after(300, 5)
    assert bs_call(S, 100.0, T, 0.4, cm.R) == pytest.approx(8.0, rel=1e-6)


def test_s1_level_none_when_entry_premium_already_above_any_projection():
    # 入场权利金 (100) 远高于 BS 值: 正股不用跌, 投影就在 S1 之下 → 无线
    assert cm.s1_level(100.0, 100.0, 300, 0.4, 0.20, 5, 100.0) is None


def test_first_trigger_s1_first_when_exit_line_is_far():
    p0 = bs_call(100.0, 90.0, 300 / 365, 0.4, cm.R)
    ft = cm.first_trigger(p0, 90.0, 300, 0.4, 100.0, stop_line=70.0, s1_frac=0.20)
    assert ft["by"] == "S1" and ft["loss"] == pytest.approx(0.20)
    assert 70.0 < ft["level"] < 100.0


def test_first_trigger_line_first_when_exit_line_is_close():
    p0 = bs_call(100.0, 90.0, 300 / 365, 0.4, cm.R)
    ft = cm.first_trigger(p0, 90.0, 300, 0.4, 100.0, stop_line=98.0, s1_frac=0.20)
    assert ft["by"] == "线" and ft["level"] == 98.0 and 0 < ft["loss"] < 0.20


def test_first_trigger_bottom_mode_has_no_s1():
    p0 = bs_call(100.0, 90.0, 300 / 365, 0.4, cm.R)
    ft = cm.first_trigger(p0, 90.0, 300, 0.4, 100.0, stop_line=70.0, s1_frac=None)
    assert ft["by"] == "线" and ft["loss"] > 0.20     # 无 S1: 远止损就是大亏损


def test_rw_three_is_a_distribution_and_symmetric_in_log_space():
    pr = cm.rw_three(100.0, 90.0, 100.0 * 100.0 / 90.0, 0.02, 25)
    assert pr["up"] + pr["down"] + pr["neither"] == pytest.approx(1.0)
    assert pr["up"] == pytest.approx(pr["down"], abs=1e-9)


def test_rw_three_closer_stop_means_more_stop_outs():
    near = cm.rw_three(100.0, 98.0, 110.0, 0.02, 25)
    far = cm.rw_three(100.0, 90.0, 110.0, 0.02, 25)
    assert near["down"] > far["down"] and near["up"] < far["up"]


def test_rw_three_rejects_lines_on_the_wrong_side():
    assert cm.rw_three(100.0, 101.0, 110.0, 0.02, 25) is None
    assert cm.rw_three(100.0, 90.0, 99.0, 0.02, 25) is None


def test_ev_target_prefers_gate_then_trim_then_2n():
    assert cm.ev_target(100.0, {"gate": 110.0, "trim": 120.0, "n2": 105.0}) == 110.0
    assert cm.ev_target(100.0, {"gate": 95.0, "trim": 120.0, "n2": 105.0}) == 120.0
    assert cm.ev_target(100.0, {"gate": 95.0, "trim": 99.0, "n2": 105.0}) == 105.0
    assert cm.ev_target(100.0, {}) is None


def test_trend_cap_is_live_premium_cap_and_at_risk_ceiling_min():
    rows = _chain()
    r = cm.contract_row(rows[5], dte=300, spot=100.0, anchor=100.0,
                        lines={"stop": 95.0, "gate": 110.0}, mode="trend", nav=20000.0, gate_no=2,
                        sigma_d=0.025)
    want = min(option_premium_cap_frac(2) * 20000.0, AT_RISK_CAP_FRAC * 20000.0 / OPTION_ATRISK_LOSS_FRAC)
    assert r["cap"] == pytest.approx(want)
    assert r["n"] == int(want // (r["p0"] * 100))


def test_bottom_cap_is_reversal_probe_cap_per_contract():
    rows = _chain()
    r = cm.contract_row(rows[5], dte=300, spot=100.0, anchor=100.0,
                        lines={"stop": 92.0, "gate": 110.0}, mode="bottom", nav=20000.0, gate_no=1,
                        sigma_d=0.025)
    assert r["cap"] == pytest.approx(reversal_probe_premium_cap(20000.0, r["first_trigger"]["loss"]))


def test_scenarios_have_sane_signs_and_r_is_gain_over_stop_loss():
    rows = _chain()
    r = cm.contract_row(rows[6], dte=300, spot=100.0, anchor=100.0,
                        lines={"stop": 95.0, "gate": 110.0, "trim": 120.0, "n2": 106.0},
                        mode="trend", nav=20000.0, gate_no=1, sigma_d=0.025)
    sc = r["scen"]
    assert sc["stop"] < 0 and sc["s2"] < 0 < sc["n2"] < sc["gate"]
    assert r["R"]["gate"] == pytest.approx(sc["gate"] / -sc["stop"])
    ev = r["ev"]
    assert ev and ev["target"] == 110.0 and set(ev["probs"]) == {"up", "down", "neither"}


def test_lines_below_anchor_drop_out_of_scenarios():
    rows = _chain()
    r = cm.contract_row(rows[6], dte=300, spot=100.0, anchor=100.0,
                        lines={"stop": 95.0, "gate": 99.0, "trim": 98.0, "n2": 106.0},
                        mode="trend", nav=20000.0, gate_no=1, sigma_d=0.025)
    assert "gate" not in r["scen"] and "trim" not in r["scen"] and "n2" in r["scen"]
    assert r["ev"]["target"] == 106.0


def test_stock_row_zero_drift_ev_is_near_zero():
    sr = cm.stock_row(100.0, {"stop": 95.0, "gate": 100.0 * 100.0 / 95.0}, 0.02)
    assert sr["ev"]["pct"] == pytest.approx(0.0, abs=0.003)


def test_pick_expiries_nearest_live_plus_term_targets():
    cands = [("a", 200), ("b", 280), ("c", 343), ("d", 469), ("e", 700)]
    assert cm.pick_expiries(cands) == [("b", 280), ("c", 343), ("d", 469)]
    assert cm.pick_expiries([("a", 200)]) == []


def test_grid_rows_prefers_liquid_and_keeps_live_pick():
    rows = [{"strike": 90.0, "delta": 0.70}, {"strike": 91.0, "delta": 0.71}, {"strike": 50.0, "delta": 0.99}]
    liquid = {id(rows[1])}
    got = cm.grid_rows(rows, liquid, keep={id(rows[2])})
    assert rows[1] in got and rows[0] not in got and rows[2] in got


def test_build_matrix_stars_only_eligible_contracts():
    rows = _chain(oi=500.0)
    rows[0]["oi"] = 10.0                                # 最深 ITM 档流动性不合格
    m = cm.build_matrix("TEST", 100.0, [{"expiry": "2027-08-20", "dte": 300, "strikes": rows}],
                        anchor=100.0, lines={"stop": 95.0, "gate": 110.0, "n2": 106.0}, mode="trend",
                        nav=20000.0, gate_no=1, iv_ctx={"eff_pct": 35.0}, sigma_d=0.025,
                        live_expiry="2027-08-20", show_all=True)
    e = m["expiries"][0]
    eligible = {r["code"] for r in e["rows"] if r["eligible"]}
    assert eligible and set(m["stars"].values()) <= eligible
    assert sum(r["live_pick"] for r in e["rows"]) == 1
    assert all(cm.PARAMS["delta_show"][0] <= r["delta"] <= cm.PARAMS["delta_show"][1] for r in e["rows"])
    assert m["stock"] and m["bands"]["stop"][0] < 0 < m["bands"]["stop"][1]
