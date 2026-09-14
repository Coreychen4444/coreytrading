"""adaptive_volume_leg (round-3 量腿阈值) 机械件 + 扩展点恒等 + 步降裁决 + 剂量-反应 (真数据只跑一次)。"""
import json
import random

import pytest

from quant.decision import regime_transition as rt
from quant.research import relative_volume_backtest as rv
from quant.research import volume_clock_backtest as vc
from quant.research import volume_leg_align_backtest as va
from quant.research import adaptive_volume_leg_backtest as al
from quant.research.gate_backtest import episodes_of, sma_series, confirmed_swing_highs
from quant.research.gate_backtest2 import atr_series, confirmed_swing_lows, cluster_gate
from tests.research.test_volume_clock_backtest import _quiet_grind_pool, _mk_bars, _write_jsonl


# ---------- 1. 锁定项 ----------

def test_locked_items():
    assert al.THETA_LIVE == al.VOL_RATIO == rv.VOL_RATIO == 1.2                  # 被测的那个数 = 裁决当时的 live
    assert rt.RECLAIM_VOL_RATIO_MIN == 1.1                                       # round-3 之后 live = 1.1
    assert al.theta_of(al.DECISION_SEQ[0]) == rt.RECLAIM_VOL_RATIO_MIN           # 接线值 = 序列第 1 级 = 采纳的 θ
    assert al.THETA_SEQ == (1.1, 1.0) and al.THETA_ARMS == (1.15, 1.3, 1.5, 0.0)
    assert al.DECISION_SEQ == ("W5_t110", "W5_t100")
    assert al.CURVE_ARMS == ("W5_t115", "W5_t130", "W5_t150", "W5_t000")
    assert al.theta_of("W5_t115") == 1.15 and al.theta_world(1.0) == "W5_t100" and al.theta_of("W5_t000") == 0.0
    assert al.BASE == "W5" and al.BASE_PREV == "W1F"
    assert al.NEW_WORLDS == al.THETA_WORLDS + al.SCALE_ARMS and al.WORLDS_EXT == va.WORLDS_EXT + al.NEW_WORLDS
    assert len(set(al.WORLDS_EXT)) == len(al.WORLDS_EXT)                            # 无重名
    assert al.BOOT_SEED not in (vc.BOOT_SEED, va.BOOT_SEED, rv.BOOT_SEED)
    assert al.FASTLANE_K == rt.PARAMS["fastlane_k"] == 0.5 and al.MAG_N == 2 * al.FASTLANE_K
    assert al.REL_FLOOR == rt.REL_FLOOR == rv.REL_FLOOR and al.STOP_GUARD == rv.STOP_GUARD == 0.03
    assert al.DMEAN_NONINF == 0.0010                                                 # 决策点 3 锁 (b): 非劣效 0.10pp
    assert al.REGRESSION_CUR is va.REGRESSION_CUR
    assert al.REGRESSION_R2["n60"] == 2866 and al.REGRESSION_R2["dstop_ci"] == (0.0045, 0.0140)
    assert al.RELVR_BUCKETS == (0.8, 1.0, 1.1, 1.2, 1.5)


# ---------- 2. 纯函数 ----------

def _one_day(close, gate, atr, abs_r, bench, r_stock, r_bench):
    """单日数组 (k=0) → {world: ok}"""
    relvr = [abs_r / max(bench, al.REL_FLOOR)]
    return {w: al.day_ok(w, [close], gate, [abs_r], relvr, [atr], [bench], [r_stock], [r_bench])(0)
            for w in ("W5",) + al.NEW_WORLDS}


def test_theta_worlds_intc_0908_relvr_118():
    """RelVR 1.18 形状: θ≤1.15 全过, 1.2 (live) / 1.3 / 1.5 不过; t000 恒过。"""
    o = _one_day(close=104.47, gate=98.0, atr=5.96, abs_r=1.41, bench=1.19, r_stock=0.0904, r_bench=0.0119)
    assert not o["W5"]
    assert o["W5_t110"] and o["W5_t100"] and o["W5_t115"] and o["W5_t000"]
    assert not o["W5_t130"] and not o["W5_t150"]
    assert o["W6"] and not o["W6_m15"] and o["W7"] and o["W7_1s"] and o["W2any"]   # 尺轴臂照旧


def test_theta_worlds_monotone_and_none_handling():
    rng = random.Random(3)
    for _ in range(300):
        o = _one_day(close=100 + rng.uniform(-3, 12), gate=100.0, atr=rng.uniform(1, 6),
                     abs_r=rng.uniform(0.3, 3.0), bench=rng.uniform(0.3, 3.0),
                     r_stock=rng.uniform(-0.1, 0.1), r_bench=rng.uniform(-0.03, 0.03))
        chain = ["W5_t000", "W5_t100", "W5_t110", "W5_t115", "W5", "W5_t130", "W5_t150"]   # 越右越严
        for a, b in zip(chain, chain[1:]):
            assert not (o[b] and not o[a]), (a, b, o)                # 严的过 ⇒ 松的过
    ok = al.day_ok("W5_t110", [100.0], 90.0, [None], [None], [1.0], [None], [None], [None])
    assert not ok(0)                                                    # RelVR 不可算 → 不过
    assert al.day_ok("W5_t000", [100.0], 90.0, [None], [None], [1.0], [None], [None], [None])(0)   # 无量腿


def test_attribution_weight_and_relvr_weighted():
    assert al.attribution_weight(0.0904, 0.0119) == pytest.approx(0.1316, abs=1e-3)
    assert al.attribution_weight(0.0012, -0.0104) == 1.0 and al.attribution_weight(0.05, 0.0) == 0.0
    assert al.attribution_weight(None, 0.01) is None
    assert al.relvr_weighted(1.41, 1.19, 1.0) == pytest.approx(1.41 / 1.19)
    assert al.relvr_weighted(1.41, 1.19, 0.0) == pytest.approx(1.41)
    assert al.relvr_weighted(0.74, 0.2, 1.0) == pytest.approx(0.74 / 0.4)


def test_scale_arms_supersets_and_w7_two_sided_loses_quiet_tape_rescue():
    o = _one_day(close=228.41, gate=228.0, atr=7.37, abs_r=1.07, bench=0.84, r_stock=0.0178, r_bench=0.0039)   # 突破日
    assert o["W5"] and not o["W7"] and o["W7_1s"] and o["W6"]
    rng = random.Random(7)
    for _ in range(300):
        o = _one_day(close=100 + rng.uniform(-3, 12), gate=100.0, atr=rng.uniform(1, 6),
                     abs_r=rng.uniform(0.3, 3.0), bench=rng.uniform(0.3, 3.0),
                     r_stock=rng.uniform(-0.1, 0.1), r_bench=rng.uniform(-0.03, 0.03))
        if o["W5"]:
            for w in ("W6", "W6_m05", "W6_m15", "W6_waive", "W2any", "W7_1s"):
                assert o[w], (w, o)
        assert not (o["W6"] and not o["W2any"])


def test_relvr_bucket_edges():
    assert al.relvr_bucket(None) == "NA" and al.relvr_bucket(0.5) == "<0.8"
    assert al.relvr_bucket(0.8) == "0.8-1" and al.relvr_bucket(1.0) == "1-1.1"
    assert al.relvr_bucket(1.18) == "1.1-1.2" and al.relvr_bucket(1.2) == "1.2-1.5" and al.relvr_bucket(2.0) == "≥1.5"


def test_streak_max_relvr_uses_only_the_streak():
    closes = [100, 103, 101, 103, 104]
    relvr = [2.0, 1.5, 0.9, 0.8, 1.1]
    assert al.streak_max_relvr(4, closes, 102.0, relvr) == 1.1        # streak = idx 3,4 (idx 2 跌回)
    assert al.streak_max_relvr(1, closes, 102.0, relvr) == 1.5
    assert al.streak_max_relvr(4, closes, 102.0, [None] * 5) is None


# ---------- 3. 扫描恒等 (谓词参数化版 = round-1/2 原函数) ----------

def test_scans_reproduce_round12_functions_with_rel_predicate():
    closes = [100, 103, 104, 105, 101, 103, 106, 107, 108]
    atr20 = [2.0] * len(closes)
    for relvr in ([None, 1.5, 0.8, 0.7, 0.7, 0.8, 0.9, 1.3, 0.9],
                  [None, 0.8, 1.5, 0.7, 0.7, 0.8, 0.9, 0.9, 0.9],
                  [None, 0.9, 0.9, 0.9, 0.9, 1.5, 0.9, 0.9, 0.9],
                  [None] + [0.5] * 8):
        rel_ok = lambda k, r=relvr: r[k] is not None and r[k] >= al.VOL_RATIO
        for gate in (102.0, 102.5, 105.5):
            assert al.fire_scan_any_ok(0, 8, closes, gate, rel_ok) == va.fire_scan_any(0, 8, closes, relvr, gate)
            assert al.fastlane_scan_ok(0, 8, closes, atr20, gate, rel_ok) == vc.fastlane_scan(0, 8, closes, atr20, relvr, gate)


def test_fire_scan_any_ok_forgets_after_dip_and_fastlane_needs_first_day():
    closes = [100, 103, 101, 103, 104]
    ok = lambda k: k == 1
    assert al.fire_scan_any_ok(0, 4, closes, 102.0, ok) is None          # day-1 的量随跌回作废
    assert al.fastlane_scan_ok(0, 4, closes, [1.0] * 5, 102.0, ok) == 1
    assert al.fastlane_scan_ok(0, 4, closes, [3.0] * 5, 102.0, ok) is None


# ---------- 4. 合成池: 扩展点恒等 + 差一点形状 (RelVR 1.185) 只有 θ≤1.15 与尺轴臂救 ----------

def _intc_shape_pool(tmp_path, gap_close=115.0, stock_spike=1_441_100, tape_spike=1_202_100):
    """箱体 → 破位 → 修复爬到 gate 之下 → 一根 +7.5% 缺口日收在 gate 111.4 + ~1.27N (绝对 1.41× / tape 1.19×
    → RelVR 1.185 ✗) → 之后平量缓升。tape (QQQ) 价格全程 100 不动 (r_bench = 0)。"""
    n_pad, n_dec = 130, 60
    closes = [100.0] * n_pad
    closes += [104, 106, 108, 110, 108, 106, 104, 106, 108, 110.4,
               108, 106, 104, 105, 104, 105, 104, 105, 104, 105]
    closes += [103.5 - 0.5 * k for k in range(n_dec)]          # → 74.0
    lo = closes[-1]
    closes += [lo + 1.5 * k for k in range(1, 23)]              # → 107.0 (gate 111.4 之下)
    gap_idx = len(closes)
    closes += [gap_close]                                        # 缺口日
    closes += [gap_close + 0.2 * k for k in range(1, 101)]       # 平量缓升, 站住
    vols = [1_000_000] * len(closes)
    vols[gap_idx] = stock_spike
    bars = _mk_bars(closes, vols)
    _write_jsonl(tmp_path / "TICK.jsonl", bars)
    tv = [1_000_000] * len(closes)
    tv[gap_idx] = tape_spike
    _write_jsonl(tmp_path / "QQQ.jsonl", _mk_bars([100.0] * len(closes), tv))
    _write_jsonl(tmp_path / "SMH.jsonl", _mk_bars([100.0] * len(closes), tv))
    earn = tmp_path / "earn"
    earn.mkdir()
    return bars, earn, gap_idx


def test_intc_shape_pool_geometry(tmp_path):
    bars, earn, gap_idx = _intc_shape_pool(tmp_path)
    closes = [b["close"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    i = episodes_of(closes, sma_series(closes, 60))[0]
    gate = cluster_gate(i, closes[i], confirmed_swing_highs(highs), confirmed_swing_lows(lows),
                        atr_series(highs, lows, closes, 14)[i])
    assert gate == pytest.approx(111.4)                             # 簇上沿 = swing high (high = close+1)
    assert closes[gap_idx - 1] < gate < closes[gap_idx]
    atr = atr_series(highs, lows, closes, 20)[gap_idx]
    mag = (closes[gap_idx] - gate) / atr
    assert 1.0 <= mag < 1.5, mag
    rs = rv.ratio_series([b["volume"] for b in bars])
    assert rs[gap_idx] == pytest.approx(1.41, abs=0.005)
    tr = rv.ratio_series([1_000_000] * gap_idx + [1_202_100] + [1_000_000] * (len(bars) - gap_idx - 1))
    assert tr[gap_idx] == pytest.approx(1.19, abs=0.005)
    assert 1.15 <= rs[gap_idx] / tr[gap_idx] < al.VOL_RATIO         # RelVR 1.185: θ=1.15 过, 1.2 不过


def test_collect_intc_shape_theta_worlds(tmp_path):
    bars, earn, gap_idx = _intc_shape_pool(tmp_path)
    eps, drops = al.collect(kdir=tmp_path, earn_dir=earn)
    assert len(eps) == 1, (drops,)
    r = eps[0]
    assert r["q"]["W5"] is None and r["q"]["W1F"] is None and r["q"]["W1"] is None
    assert r["q"]["W5_t110"] == gap_idx == r["q"]["W5_t100"] == r["q"]["W5_t115"] == r["q"]["W5_t000"]   # 快车道首日
    assert r["q"]["W5_t130"] is None and r["q"]["W5_t150"] is None
    assert r["q"]["W6"] == gap_idx == r["q"]["W7_1s"] == r["q"]["W2any"] and r["q"]["W6_m15"] is None
    assert r["lat"]["W5_t110"] == -1                                   # 早于 p (×2 完成日) 一天
    assert r["relvr_p"] == pytest.approx(1.185, abs=0.005)             # p 日 streak 内最大 = 缺口日
    for T in (20, 60):
        assert r["entered"][("W5_t110", T)] and not r["entered"][("W5", T)]
        assert r["pv"][("W5", T)] == 0.0                               # ITT: 未入场 = 现金
    d = r["day"]
    assert d["W5_t110_gain"] == 1 and d["W5_t110_loss"] == 0 and d["W5_t130_gain"] == 0
    assert d["W5_t000_gain"] >= d["W5_t100_gain"] >= d["W5_t110_gain"]


def test_collect_keeps_round12_worlds_untouched(tmp_path):
    """扩展点恒等: 十二个旧世界的 q / pv 与 va.collect 逐字相同; 新世界只多不改。"""
    for mk in (_quiet_grind_pool, _intc_shape_pool):
        sub = tmp_path / mk.__name__
        sub.mkdir()
        out = mk(sub)
        earn = out[1]
        eps0, _, _ = va.collect(kdir=sub, earn_dir=earn)
        eps1, _ = al.collect(kdir=sub, earn_dir=earn)
        assert len(eps0) == len(eps1) == 1
        assert set(eps1[0]["q"]) == set(al.WORLDS_EXT)
        for w in va.WORLDS_EXT:
            assert eps1[0]["q"][w] == eps0[0]["q"][w], (mk.__name__, w)
            for T in (20, 60):
                assert eps1[0]["pv"][(w, T)] == eps0[0]["pv"][(w, T)]


def test_w7_two_sided_loses_quiet_tape_rescue_in_collect(tmp_path):
    bars, earn, gap_idx = _intc_shape_pool(tmp_path, stock_spike=1_000_000, tape_spike=700_000)
    eps, _ = al.collect(kdir=tmp_path, earn_dir=earn)
    r = eps[0]
    assert r["q"]["W5"] == gap_idx and r["q"]["W7_1s"] == gap_idx
    assert r["q"]["W7"] is None or r["q"]["W7"] > gap_idx
    assert r["day"]["W7_loss"] >= 1 and r["day"]["W7_1s_loss"] == 0


# ---------- 5. 统计 / 步降裁决 / 剂量-反应 / 回归锚 ----------

def _cell(dmean_ci, dstop_ci, dcover_ci, kish=100.0, base="W5"):
    return {"n": 500, "kish": kish, "base": base,
            "dmean": sum(dmean_ci) / 2, "dmean_ci": dmean_ci,
            "dstop": sum(dstop_ci) / 2, "dstop_ci": dstop_ci,
            "dcover": sum(dcover_ci) / 2, "dcover_ci": dcover_ci,
            "dp10": 0.0, "dp10_ci": (0.0, 0.0), "lat_w": 0.3, "lat_b": 0.1,
            "cover_w": 0.9, "cover_b": 0.85, "stop_w": 0.5, "stop_b": 0.49,
            "mean_w": 0.01, "mean_b": 0.005}


GOOD = ((-0.0009, 0.01), (-0.01, 0.029), (0.001, 0.02))       # Δmean 下界 −0.09pp: 非劣效内
BAD = ((-0.0011, 0.01), (-0.01, 0.029), (0.001, 0.02))        # −0.11pp: 超边际


def test_flags_noninferiority_margin_and_guards():
    assert al.flags(_cell(*GOOD)) == {"exp_ok": True, "stop_ok": True, "cover_ok": True}
    assert not al.flags(_cell(*BAD))["exp_ok"]
    assert al.flags(_cell((-0.0010, 0.01), (-0.01, 0.029), (0.001, 0.02)))["exp_ok"]          # 恰在边际 = 过
    assert not vc._flags(_cell(*GOOD))["exp_ok"]                                              # 前三轮 superiority 会判 ✗
    assert not al.flags(_cell((0.0, 0.01), (-0.01, 0.030), (0.001, 0.02)))["stop_ok"]         # 守卫 +3pp 不动
    assert not al.flags(_cell((0.0, 0.01), (-0.01, 0.029), (0.0, 0.02)))["cover_ok"]


def _summary(c110, c100, reg_ok=True):
    filler = _cell(*GOOD)
    return {"regression": {"ok": reg_ok},
            "full": {60: {"seq": {"W5_t110": c110, "W5_t100": c100},
                          "curve": {w: filler for w in al.CURVE_ARMS},
                          "scale": {w: filler for w in al.SCALE_ARMS}}}}


def test_verdict_fixed_sequence_step_down():
    good, bad = _cell(*GOOD), _cell(*BAD)
    assert al.verdict(_summary(good, good))["branch"] == "V:θ=1"        # 两级都过 → 最松的 1.0
    assert al.verdict(_summary(good, bad))["branch"] == "V:θ=1.1"
    v = al.verdict(_summary(bad, good))
    assert v["branch"] == "K" and len(v["sequence"]) == 1                # 第一级不过即停, 1.0 不看
    assert al.verdict(_summary(good, good, reg_ok=False))["branch"] == "no_call"
    assert al.verdict(_summary(_cell(*GOOD, kish=29.9), good))["branch"] == "no_call"
    assert al.verdict({"regression": {"ok": True}, "full": {60: {"n": 3, "insufficient": True}}})["branch"] == "no_call"
    assert set(v["curve_flags"]) == set(al.CURVE_ARMS) and set(v["scale_flags"]) == set(al.SCALE_ARMS)   # 臂只报


def test_regression_r2_anchor_tolerance():
    ok = {k: (tuple(v) if isinstance(v, tuple) else v) for k, v in al.REGRESSION_R2.items()}
    assert al.regression_r2_ok(ok)
    for key, delta in (("dstop", 0.0001), ("dmean", 0.0001), ("dcover", 0.001)):
        bad = dict(ok)
        bad[key] = ok[key] + delta
        assert not al.regression_r2_ok(bad), key
    bad = dict(ok)
    bad["dstop_ci"] = (0.0045, 0.0141)
    assert not al.regression_r2_ok(bad)
    bad = dict(ok)
    bad["n60"] = 2865
    assert not al.regression_r2_ok(bad)


def test_dose_response_buckets_w3_outcomes():
    rows = [{"relvr_p": 1.15, "stopped": {("W3", 60): True}, "pv": {("W3", 60): -0.05}, "entered": {("W3", 60): True}},
            {"relvr_p": 1.18, "stopped": {("W3", 60): False}, "pv": {("W3", 60): 0.10}, "entered": {("W3", 60): True}},
            {"relvr_p": 1.30, "stopped": {("W3", 60): False}, "pv": {("W3", 60): 0.02}, "entered": {("W3", 60): True}},
            {"relvr_p": None, "stopped": {("W3", 60): True}, "pv": {("W3", 60): -0.01}, "entered": {("W3", 60): True}}]
    d = al.dose_response(rows, 60)
    assert d["1.1-1.2"]["n"] == 2 and d["1.1-1.2"]["stop_rate"] == 0.5 and d["1.1-1.2"]["mean_pv"] == pytest.approx(0.025)
    assert d["1.2-1.5"]["n"] == 1 and d["NA"]["n"] == 1 and d["1.1-1.2"]["share"] == 0.5


def test_population_counts_only_zero_forward_returns(tmp_path):
    bars, earn, gap_idx = _intc_shape_pool(tmp_path)
    eps, _ = al.collect(kdir=tmp_path, earn_dir=earn)
    pop = al.population(eps)
    assert pop["n_total"] == 1 and pop["worlds"]["W5_t110"]["only_w"] == 1 and pop["worlds"]["W5_t110"]["changed"] == 1
    assert pop["relvr_p_hist"] == {"1.1-1.2": 1}
    assert "pv" not in json.dumps(pop) and "stop" not in json.dumps(pop)   # 筛查段不携带前向收益
