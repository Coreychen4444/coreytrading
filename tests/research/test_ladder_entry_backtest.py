"""ladder_entry_backtest 机械件验证 (合成数据)。

钉住预注册里会驱动裁决的语义:
  - 四条臂各自的入场日与档位 (含 B2 的两条回落分支, §3b)
  - 二三档限价的 gate 地板 (无地板会靠缩小分母虚增 R)
  - 判据五条与 final_verdict (含"只有一个 k 过"的边界)
"""
import pytest

from quant.research import ladder_entry_backtest as lb


def _flat(n, price=100.0, low=None, op=None):
    closes = [price] * n
    lows = [low if low is not None else price] * n
    opens = [op if op is not None else price] * n
    return closes, lows, opens


def test_arm_A_is_full_size_at_d1_close():
    closes, lows, opens = _flat(40, 110.0)
    arms, stop, _ = lb.build_arms([{}] * 40, closes, lows, opens, d1=5,
                                  gate=100.0, N=10.0, atr14=[10.0] * 40, term=35, k=0.5)
    assert arms["A"] == [(1.0, 5, 110.0)]
    assert stop == pytest.approx(100.0 - 0.5 * 10.0)


def test_arm_B1_holds_cash_when_d2_falls_back_under_gate():
    closes, lows, opens = _flat(40, 110.0)
    closes[6] = 99.0                      # D2 收在 gate 之下
    arms, _s, _f = lb.build_arms([{}] * 40, closes, lows, opens, d1=5, gate=100.0,
                                 N=10.0, atr14=[10.0] * 40, term=35, k=0.5)
    assert arms["B1"] == []
    assert arms["B2"] == []               # B1 不入场时 B2 也无从确认


def test_arm_B2_falls_back_to_B1_when_no_hvn_band_below():
    """§3b-B: 没有量能货架时 live 的人仍会入场, 让 B2 空手 = 又一个稻草人。"""
    closes, lows, opens = _flat(40, 110.0)
    bars = [{"high": 110.0, "low": 110.0, "close": 110.0, "turnover": 0.0}] * 40
    arms, _s, fell = lb.build_arms(bars, closes, lows, opens, d1=5, gate=100.0,
                                   N=10.0, atr14=[10.0] * 40, term=35, k=0.5)
    assert fell is True and arms["B2"] == arms["B1"] != []


def test_ladder_first_tranche_is_participation_at_d1_close():
    """greedy-bid trap: 首档必须在参与位 (D1 收盘), 不是深回调位。"""
    closes, lows, opens = _flat(40, 110.0)
    arms, _s, _f = lb.build_arms([{}] * 40, closes, lows, opens, d1=5, gate=100.0,
                                 N=10.0, atr14=[10.0] * 40, term=35, k=0.5)
    w, j, px = arms["C"][0]
    assert (w, j, px) == (0.5, 5, 110.0)


def test_ladder_lower_tranches_fill_on_dip_and_respect_gate_floor():
    """二档限价 = max(D1收 − 0.5N, gate); 三档同式 —— 地板挡住"买在 gate 之下"。"""
    closes, lows, opens = _flat(40, 110.0)
    for j in range(6, 12):
        lows[j] = 90.0                    # 深跌, 两档都会触及
        opens[j] = 108.0                  # 开盘价高于限价 → 按限价成交
    arms, _s, _f = lb.build_arms([{}] * 40, closes, lows, opens, d1=5, gate=100.0,
                                 N=10.0, atr14=[10.0] * 40, term=35, k=0.5)
    prices = [px for _w, _j, px in arms["C"]]
    assert prices[0] == 110.0
    assert prices[1] == pytest.approx(105.0)      # 110 − 0.5×10
    assert prices[2] == pytest.approx(100.0)      # max(110 − 1.0×10, gate=100) = 100
    assert all(p >= 100.0 for p in prices)        # 没有一档买在 gate 之下


def test_ladder_gate_floor_binds_when_d1_close_is_near_gate():
    closes, lows, opens = _flat(40, 103.0)
    for j in range(6, 12):
        lows[j] = 80.0
        opens[j] = 102.0
    arms, _s, _f = lb.build_arms([{}] * 40, closes, lows, opens, d1=5, gate=100.0,
                                 N=10.0, atr14=[10.0] * 40, term=35, k=0.5)
    # 110−0.5N 与 110−1.0N 都远低于 gate → 两档都被地板夹到 gate
    assert [round(px, 2) for _w, _j, px in arms["C"]][1:] == [100.0, 100.0]


def test_unfilled_tranche_stays_cash_and_lowers_deployment():
    closes, lows, opens = _flat(40, 110.0)   # 从不下跌 → 二三档永不成交
    arms, stop, _f = lb.build_arms([{}] * 40, closes, lows, opens, d1=5, gate=100.0,
                                   N=10.0, atr14=[10.0] * 40, term=35, k=0.5)
    dep, _ret, _rk, _risk, _st = lb.measure(arms["C"], stop, 10.0, 35, closes)
    assert len(arms["C"]) == 1 and dep == pytest.approx(0.5)


def test_measure_blended_risk_is_weighted_entry_minus_stop():
    closes = [100.0] * 40
    legs = [(0.5, 5, 110.0), (0.5, 7, 100.0)]     # 混合入场 105
    dep, _ret, riskN, _risk, _st = lb.measure(legs, stop=95.0, N=10.0, term=35,
                                              closes=closes)
    assert dep == pytest.approx(1.0)
    assert riskN == pytest.approx((105.0 - 95.0) / 10.0)


def test_measure_empty_arm_is_all_cash():
    assert lb.measure([], stop=95.0, N=10.0, term=35, closes=[100.0] * 40) \
        == (0.0, 0.0, None, 0.0, False)


# ---------- 裁决 ----------

def _res(k, **flags):
    base = {"c1_riskN_down": True, "c2_R_beats_both": True, "c3_deploy": True,
            "c4_abs_exp": True, "c5_kish": True}
    base.update(flags)
    base["all_pass"] = all(base[x] for x in
                           ("c1_riskN_down", "c2_R_beats_both", "c3_deploy",
                            "c4_abs_exp", "c5_kish"))
    return {"k": k, "crit": base}


def test_both_k_pass_takes_the_stricter_k():
    v = lb.final_verdict([_res(0.5), _res(1.0)])
    assert v["adopt"] is True and v["k"] == 1.0 and "⚠️" not in v["text"]


def test_only_loose_k_passes_is_adopted_with_an_explicit_boundary():
    """**本次真实结论所走的分支**: k=0.5 过 / k=1.0 不过 → 采纳但必须写明边界。"""
    v = lb.final_verdict([_res(0.5), _res(1.0, c2_R_beats_both=False)])
    assert v["adopt"] is True and v["k"] == 0.5 and "⚠️" in v["text"]


def test_no_k_passes_keeps_incumbent():
    v = lb.final_verdict([_res(0.5, c1_riskN_down=False),
                          _res(1.0, c2_R_beats_both=False)])
    assert v["adopt"] is False and "现行确认+单点入场保留" in v["text"]


def test_criterion_2_requires_beating_both_baselines():
    """只赢 B1 (稻草人) 不算 —— 判据 2 的 c2 由 B1 与 B2 同时决定。"""
    assert _res(0.5, c2_R_beats_both=False)["crit"]["all_pass"] is False


def test_deployment_floor_kills_the_null_hypothesis():
    """H0: ladder 靠不成交刷 R。部署率地板不过 → 整格 FAIL。"""
    assert _res(0.5, c3_deploy=False)["crit"]["all_pass"] is False


def test_preregistered_constants_are_pinned():
    assert lb.K_VALUES == (0.5, 1.0)
    assert lb.LADDER == ((0.5, 0.0), (0.3, 0.5), (0.2, 1.0))   # 50/30/20 锁死
    assert lb.LIMIT_DAYS == 10 and lb.DECISION_T == 60
    assert lb.BFLOOR_BUF == 0.5 and lb.DEPLOY_MIN == 0.80 and lb.KISH_MIN == 30
    assert lb.ARMS == ("A", "B1", "B2", "C")
