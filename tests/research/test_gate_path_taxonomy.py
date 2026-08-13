"""gate_path_taxonomy_backtest 机械件验证 (合成数据; 真数据只跑一次)。

预注册 `gate_path_taxonomy_design.md` A–G 全部锁定。one-shot 纪律下，机制正确性
**只能**靠这份对拍把关 —— 跑完是不许改判据的，所以错必须在跑之前发现。

三处是这份文件的重点，其余是常规:

  1. **等价体对拍**—— `gate_from_sorted` ≡ `cluster_gate`，
     `trend_before` ≡ `technical.swing_structure(bars[:i])["trend"]`。
     为跑得动而重写的两件必须与 incumbent 逐点相同，不能"看起来一样"。
  2. **预筛必要性**—— `collect` 里为省算力加的两道数值预筛，与
     **暴力版逐日画 gate**必须给出同一批 D1。漏一个 D1 = 样本被静默改写。
  3. **锁定值**—— 判据/δ/k/权重/回看窗改一个数就红。round-2 房规靠这条落地。
"""
import json
import random

import pytest

from quant.core import technical
from quant.core.episodes import COOLDOWN, LOOKBACK
from quant.research import gate_path_taxonomy_backtest as gp
from quant.research.gate_backtest import WING, confirmed_swing_highs, sma_series
from quant.research.gate_backtest2 import atr_series, cluster_gate, confirmed_swing_lows


# ── 1. 锁定值 (改一个数就红) ─────────────────────────────────────────

def test_locked_parameters():
    """A–G 锁定值。这条红 = 有人在跑完之后动了判据，或跑之前没按锁定值实现。"""
    assert gp.K_MAG == 0.5                     # §4 幅度门槛, ladder_entry 定版
    assert gp.DELTA_ATR == 0.5                 # §3b-A δ = 0.5×ATR20
    assert gp.BROKE_WINDOW == COOLDOWN == 120  # §3c-F 回看窗
    assert gp.WARMUP == COOLDOWN + LOOKBACK == 180
    assert gp.LADDER == ((0.5, 0.0), (0.3, 0.5), (0.2, 1.0))   # 50/30/20 锁死
    assert gp.LIMIT_DAYS == 10
    assert gp.BFLOOR_BUF == 0.5
    assert gp.DECISION_T == 60 and gp.REPORT_T == 20
    assert gp.DEPLOY_MIN == 0.80               # 判据 3
    assert gp.STOP_GUARD == 0.05               # 判据 5 (escape_velocity 同阈值)
    assert gp.KISH_MIN == 30                   # 判据 6
    assert gp.VOL_RATIO == 1.2                 # relative_volume 裁决 R
    assert gp.CONTROL_ONLY == "repair"         # §3b-D ① 不重新裁决


def test_ladder_and_episode_defs_are_imported_not_re_typed():
    """采纳物只有一份实现: ladder 规格来自 ladder_entry, episode 来自 core。"""
    from quant.core import episodes as core_ep
    from quant.research import ladder_entry_backtest as le
    assert gp.LADDER is le.LADDER and gp.LIMIT_DAYS is le.LIMIT_DAYS
    assert gp.BFLOOR_BUF is le.BFLOOR_BUF
    assert gp.episodes_of is core_ep.episodes_of


# ── 2. 等价体对拍 ────────────────────────────────────────────────────

def _series(seed, n=400):
    """合成日线。日期唯一且递增 —— 量比基准是按日期 join 的，日期重复会静默串味。"""
    from datetime import date, timedelta
    rg = random.Random(seed)
    d0 = date(2015, 1, 1)
    px, out = 100.0, []
    for k in range(n):
        px = max(2.0, px * (1 + rg.gauss(0, 0.02)))
        hi = px * (1 + abs(rg.gauss(0, 0.01)))
        lo = px * (1 - abs(rg.gauss(0, 0.01)))
        op = lo + (hi - lo) * rg.random()
        out.append({"time": f"{d0 + timedelta(days=k)} 00:00:00", "open": round(op, 4),
                    "high": round(hi, 4), "low": round(lo, 4),
                    "close": round(px, 4), "volume": rg.randint(10 **5, 10 **7)})
    return out


@pytest.mark.parametrize("seed", range(8))
def test_gate_from_sorted_equals_cluster_gate(seed):
    """增量有序表版 ≡ incumbent 逐点。分组是从最低价贪心累加的，**不能局部化**——
    所以这条不是形式主义，写错会静默给出另一条 gate。"""
    bars = _series(seed)
    closes = [b["close"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    a14 = atr_series(highs, lows, closes, 14)
    sh, sl = confirmed_swing_highs(highs), confirmed_swing_lows(lows)

    import bisect
    prices, fed, checked = [], -1, 0
    for i in range(30, len(bars)):
        while fed < i:
            fed += 1
            for idx, p in sh + sl:
                if idx + WING == fed:
                    bisect.insort(prices, p)
        want = cluster_gate(i, closes[i], sh, sl, a14[i])
        got = gp.gate_from_sorted(prices, closes[i], a14[i])
        assert (want is None) == (got is None), i
        if want is not None:
            assert abs(want - got) < 1e-9, i
            checked += 1
    assert checked > 20, "样本里几乎没有 gate，这条没在测东西"


@pytest.mark.parametrize("seed", range(6))
def test_trend_before_equals_swing_structure_on_prefix(seed):
    bars = _series(seed)
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    sh, sl = confirmed_swing_highs(highs), confirmed_swing_lows(lows)
    seen = set()
    for i in range(20, len(bars)):
        want = technical.swing_structure(bars[:i], wing=WING)["trend"]
        got = gp.trend_before(sh, sl, i)
        assert want == got, (i, want, got)
        seen.add(got)
    assert len(seen) >= 2, "只见到一种 trend，这条没在测东西"


# ── 3. §3 判别式 ────────────────────────────────────────────────────

def test_classify_truth_table():
    """四类互斥且穷尽; broke 是第一道，其余三类都是它的补集。"""
    d = 1.0
    # broke 压倒一切 —— 无论 trend / gate 在哪
    for trend in ("up", "down", "transition", "insufficient"):
        for gate in (50.0, 100.0):
            assert gp.gate_path(True, trend, gate, 100.0, d) == "repair"
    # ¬broke: gate 在 ATH 邻域内 → ④，且同样压倒 trend
    for trend in ("up", "down", "transition"):
        assert gp.gate_path(False, trend, 99.5, 100.0, d) == "ath"   # 100−1 = 99 ≤ 99.5
        assert gp.gate_path(False, trend, 99.0, 100.0, d) == "ath"   # 边界含等号
    # ¬broke ∧ gate < ATH−δ: trend 分 ②/③
    assert gp.gate_path(False, "up", 98.99, 100.0, d) == "uptrend"
    for trend in ("down", "transition", "insufficient"):
        assert gp.gate_path(False, trend, 98.99, 100.0, d) == "range_top"


def test_delta_is_atr_scaled_not_a_percentage():
    """§3b-A 的理由: 2% 在高波动票上是 0.16N、低波动票上是 0.67N —— 全池一把尺。
    δ 必须随 ATR 走, 所以同一个 gate/ATH 比值在不同波动率下可以落进不同类。"""
    ath, gate = 100.0, 97.0
    assert gp.gate_path(False, "up", gate, ath, gp.DELTA_ATR * 8.0) == "ath"
    assert gp.gate_path(False, "up", gate, ath, gp.DELTA_ATR * 2.0) == "uptrend"


def test_broke_before_window_boundaries():
    """窗 = [D1−120, D1)，且窗内 episode 还必须 close < gate 才算破位。"""
    closes = [0.0] * 500
    for e in (100, 200, 300):
        closes[e] = 10.0
    eps = [100, 200, 300]
    # D1=300: 窗 [180,300) —— 200 在内, 300 自己不在 (半开右端)
    assert gp.breakdown_in_window(eps, closes, 300, gate=50.0) == (True, 1, 1)
    # D1=321: 窗 [201,321) —— 200 刚出窗, 只剩 300
    assert gp.breakdown_in_window(eps, closes, 321, gate=50.0) == (True, 1, 1)
    assert gp.breakdown_in_window(eps, closes, 320, gate=50.0)[1] == 2            # 200+300 都在
    assert gp.breakdown_in_window(eps, closes, 421, gate=50.0) == (False, 0, 0)   # 300 已出窗
    # 窗内有 episode 但 close ≥ gate → 不算破位 (§3d 的 MRVL/MU 形状)
    assert gp.breakdown_in_window(eps, closes, 321, gate=5.0) == (False, 1, 0)


# ── 4. 两世界机械件 ─────────────────────────────────────────────────

def test_ladder_second_and_third_tranche_have_the_gate_floor():
    """「宁可少一档，不可买在刚收复的 gate 之下」—— ladder_entry 接线时抓到的那个 bug。"""
    closes = [100.0] * 30
    lows = [90.0] * 30          # 深下影, 任何限价都能成交
    opens = [100.0] * 30
    legs = gp.ladder_legs(0, gate=99.0, n_arm=5.0, term=20,
                          closes=closes, lows=lows, opens=opens)
    assert len(legs) >= 2
    for _, _, px in legs:
        assert px >= 99.0 - 1e-9, "有档位成交在 gate 之下"


def test_limit_orders_expire_after_ten_trading_days():
    closes = [100.0] * 40
    opens = [100.0] * 40
    lows = [100.0] * 40
    lows[gp.LIMIT_DAYS + 1] = 1.0            # 第 11 个交易日才触及 → 过期
    legs = gp.ladder_legs(0, gate=1.0, n_arm=10.0, term=30,
                          closes=closes, lows=lows, opens=opens)
    assert len(legs) == 1, "过期的限价档不该成交"

    lows2 = [100.0] * 40
    lows2[gp.LIMIT_DAYS] = 1.0               # 第 10 个交易日触及 → 有效
    legs2 = gp.ladder_legs(0, gate=1.0, n_arm=10.0, term=30,
                           closes=closes, lows=lows2, opens=opens)
    assert len(legs2) == 3


def test_measure_uses_the_common_denominator_not_the_arm_atr():
    """风险N 的分母两臂统一 ATR20[D1] (§3e 锁定) —— 否则配对差的单位不同。"""
    closes = [100.0] * 30
    legs = [(1.0, 0, 100.0)]
    a = gp.measure(legs, stop=90.0, n_common=5.0, term=20, closes=closes)
    b = gp.measure(legs, stop=90.0, n_common=10.0, term=20, closes=closes)
    assert a["riskN"] == pytest.approx(2.0) and b["riskN"] == pytest.approx(1.0)


def test_no_entry_is_cash_not_missing():
    """ITT: 未入场 = 现金 = 收益 0 / R 0 / 不计止损, 不是丢样本。"""
    m = gp.measure([], stop=90.0, n_common=5.0, term=20, closes=[100.0] * 30)
    assert m == {"dep": 0.0, "ret": 0.0, "riskN": None, "risk": 0.0,
                 "stopped": False, "R": 0.0, "stop_cost": 0.0}


def test_slow_arm_does_not_enter_when_d2_falls_back_under_gate():
    closes = [100.0] * 10 + [110.0, 99.0] + [105.0] * 80
    lows = [c * 0.999 for c in closes]
    opens = list(closes)
    atr20 = [5.0] * len(closes)
    w = gp.build_worlds(10, gate=100.0, closes=closes, lows=lows, opens=opens,
                        atr20=atr20, term=70)
    assert w["FAST"][0], "FAST 应当在 D1 收盘入场"
    assert w["SLOW"][0] == [], "D2 收回 gate 之下 → SLOW 持现金 (ITT)"


def test_both_arms_use_ladder_so_the_only_difference_is_one_day():
    """§4 明写唯一自由度是「早一天」—— 两臂档数配方必须相同。"""
    closes = [100.0] * 10 + [110.0, 111.0] + [112.0] * 80
    lows = [c * 0.90 for c in closes]        # 两臂的二三档都能成交
    opens = list(closes)
    atr20 = [5.0] * len(closes)
    w = gp.build_worlds(10, gate=100.0, closes=closes, lows=lows, opens=opens,
                        atr20=atr20, term=70)
    assert [round(x[0], 2) for x in w["FAST"][0]] == [0.5, 0.3, 0.2]
    assert [round(x[0], 2) for x in w["SLOW"][0]] == [0.5, 0.3, 0.2]
    assert w["FAST"][0][0][1] == 10 and w["SLOW"][0][0][1] == 11   # 差的就是一天


# ── 5. 预筛必要性: 与暴力版对拍 ─────────────────────────────────────

def _write(dirpath, name, bars):
    (dirpath / f"{name}.jsonl").write_text(
        "\n".join(json.dumps(b) for b in bars) + "\n")


def _brute_force(bars, bench_bars, term_len):
    """暴力版: 逐日画 gate, **不用任何数值预筛**。→ [(date, cls, gate, over_n)]。"""
    from quant.research.relative_volume_backtest import relvr_series
    closes = [b["close"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    dates = [b["time"][:10] for b in bars]
    n = len(closes)
    ma60 = sma_series(closes, 60)
    a20 = atr_series(highs, lows, closes, 20)
    a14 = atr_series(highs, lows, closes, 14)
    sh, sl = confirmed_swing_highs(highs), confirmed_swing_lows(lows)
    eps = gp.episodes_of(closes, ma60)

    brs = gp.ratio_series([x.get("volume") or 0 for x in bench_bars])
    bench = {bench_bars[k]["time"][:10]: brs[k]
             for k in range(len(bench_bars)) if brs[k] is not None}
    rel = relvr_series(gp.ratio_series([x.get("volume") or 0 for x in bars]),
                       dates, bench)

    out, last = [], -10 **9
    for j in range(gp.WARMUP, n):
        if j - last < COOLDOWN or j + term_len > n - 1 or j + 1 > n - 1:
            continue
        if not a20[j] or a14[j - 1] is None or not a20[j + 1]:
            continue
        if rel[j] is None or rel[j] < gp.VOL_RATIO:
            continue
        gate = cluster_gate(j - 1, closes[j - 1], sh, sl, a14[j - 1])
        if gate is None or closes[j] <= gate + gp.K_MAG * a20[j]:
            continue
        cls = gp.gate_path(gp.breakdown_in_window(eps, closes, j, gate)[0],
                          gp.trend_before(sh, sl, j), gate, max(closes[:j]),
                          gp.DELTA_ATR * a20[j])
        out.append((dates[j], cls, round(gate, 9),
                    round((closes[j] - gate) / a20[j], 9)))
        last = j
    return out


@pytest.mark.parametrize("seed", range(6))
def test_prefilters_do_not_drop_any_real_d1(tmp_path, seed):
    """省算力的两道数值预筛必须是**严格的必要条件**。

    `closes[j] > closes[j-1] + k·N` 之所以必要: gate = G(j-1) > closes[j-1] 由
    cluster_gate 的构造保证 (`max(g) > close_i`)，而幅度腿要求
    closes[j] > gate + k·N ⟹ closes[j] > closes[j-1] + k·N。

    漏一个 D1 = 样本被静默改写，而真数据只跑一次，没有第二次发现的机会。
    """
    bars = _series(seed, n=520)
    bench = _series(seed + 77, n=520)
    _write(tmp_path, "TESTX", bars)
    for b in ("QQQ", "SMH"):
        _write(tmp_path, b, bench)

    rows, _ = gp.collect(tmp_path, gp.DECISION_T)
    mine = [(r["date"], r["cls"], round(r["gate_over_ath"], 9), round(r["over_n"], 9))
            for r in rows]
    brute = _brute_force(bars, bench, gp.DECISION_T)

    assert len(mine) == len(brute), (
        f"预筛改变了样本量: collect {len(mine)} vs 暴力 {len(brute)}")
    for (d1, c1, _, o1), (d2, c2, _, o2) in zip(mine, brute):
        assert (d1, c1) == (d2, c2) and o1 == pytest.approx(o2)


def test_the_magnitude_prefilter_would_be_caught_if_it_were_not_necessary(tmp_path):
    """反向自查: 把预筛改成**充分而不必要**的形状 (门槛抬高一倍), 上面那条必须红。

    没有这条, `test_prefilters_do_not_drop_any_real_d1` 可能只是"两边都没样本"。
    """
    seed = 3
    bars, bench = _series(seed, n=520), _series(seed + 77, n=520)
    _write(tmp_path, "TESTX", bars)
    for b in ("QQQ", "SMH"):
        _write(tmp_path, b, bench)
    baseline, _ = gp.collect(tmp_path, gp.DECISION_T)
    assert baseline, "合成样本里一个 D1 都没有 —— 对拍测试是空转的"

    orig = gp.K_MAG
    try:
        gp.K_MAG = orig * 4          # 预筛与真判定同时收紧 → 样本必然变少
        tighter, _ = gp.collect(tmp_path, gp.DECISION_T)
    finally:
        gp.K_MAG = orig
    assert len(tighter) < len(baseline), "改门槛不影响样本 = 这条链没接上"


# ── 6. 判据接线 ─────────────────────────────────────────────────────

def _rows(n, months=40, **over):
    out = []
    for k in range(n):
        base = {"ticker": "T", "date": "2020-01-01", "month": f"20{20 + k % 4}-{k % months + 1:02d}",
                "year": 2020, "cls": "uptrend", "trend": "up", "gate_over_ath": 0.9,
                "over_n": 0.8, "win_eps": 0, "win_eps_below": 0, "flat_days": 5,
                "slow_entered": True, "fast_tranche2": True, "fast_tranche3": True,
                "FAST": {"dep": 1.0, "ret": 0.05, "riskN": 1.0, "risk": 0.1,
                         "stopped": False, "R": 0.5},
                "SLOW": {"dep": 1.0, "ret": 0.03, "riskN": 1.4, "risk": 0.1,
                         "stopped": False, "R": 0.3}}
        base.update(over)
        out.append(base)
    return out


def test_all_criteria_pass_on_a_clean_positive_sample():
    r = gp.analyse(_rows(200), "uptrend", draws=500)
    assert r["crit"]["all_pass"] and r["verdict"] == "adopt"


@pytest.mark.parametrize("field,bad,flag", [
    ("FAST", {"dep": 0.5, "ret": 0.05, "riskN": 1.0, "risk": 0.1,
              "stopped": False, "R": 0.5}, "c3_deploy"),
    ("FAST", {"dep": 1.0, "ret": -0.05, "riskN": 1.9, "risk": 0.1,
              "stopped": True, "R": -0.5}, "c1_riskN_down"),
])
def test_each_criterion_can_veto(field, bad, flag):
    r = gp.analyse(_rows(200, **{field: bad}), "uptrend", draws=500)
    assert not r["crit"][flag] and not r["crit"]["all_pass"]
    assert r["verdict"] == "reject"


def test_stop_rate_guard_vetoes_even_when_returns_look_good():
    """判据 5 是本轮新增的那条 —— ②③ 在 escape_velocity 上正是栽在止损率。
    收益全对、风险也降了，只要止损率差超 +5pp 就必须否。"""
    rows = _rows(240)
    for k, r in enumerate(rows):
        r["FAST"] = dict(r["FAST"], stopped=k % 4 == 0)     # 25%
        r["SLOW"] = dict(r["SLOW"], stopped=False)          # 0%  → 差 +25pp
    r = gp.analyse(rows, "uptrend", draws=500)
    assert r["crit"]["c1_riskN_down"] and r["crit"]["c2_R_up"]
    assert not r["crit"]["c5_stop_guard"] and r["verdict"] == "reject"


def _straddling(mutate, months=20, n=240):
    """逐月正负**不对称**交替 → CI90 两端异号，**且点估计落在"看起来对"的那一侧**。

    ⚠️ 不对称是关键。第一版用对称的 ±d，点估计正好 0，于是把判据从
    「读 CI 上/下界」改成「读点估计」**一条都不红**—— 只能区分上界与下界，
    区分不了 CI 与点估计。判据的全部意义就是「点估计不算数」，测试必须能钉住这句。
    """
    rows = []
    for k in range(n):
        r = _rows(1)[0]
        m = k % months
        r["month"] = f"2020-{m:02d}"
        mutate(r, m % 2 == 0)
        rows.append(r)
    return rows


def test_criterion1_riskN_reads_the_ci_upper_bound_not_the_point():
    """①「风险N(F−S) CI90 **上界**< 0」= 风险确实降了。
    点估计为负但 CI 含 0 → **不算降**。"""
    def m(r, up):
        r["FAST"] = dict(r["FAST"], riskN=0.45 if up else 1.45)   # 差 −0.55 / +0.45
        r["SLOW"] = dict(r["SLOW"], riskN=1.0)
    res = gp.analyse(_straddling(m), "uptrend", draws=4000)
    lo, hi = res["riskN_diff_ci90"]
    assert lo < 0 < hi, "造的样本没让上下界分开"
    assert res["riskN_diff_point"] < 0, "点估计要落在'看起来通过'的一侧"
    assert not res["crit"]["c1_riskN_down"]


def test_criterion2_R_reads_the_ci_lower_bound_not_the_point():
    """②「R(F−S) CI90 **下界**> 0」= 收益确实更好。点估计为正但 CI 含 0 → 不算。"""
    def m(r, up):
        r["FAST"] = dict(r["FAST"], R=1.05 if up else 0.0)        # 差 +0.55 / −0.50
        r["SLOW"] = dict(r["SLOW"], R=0.5)
    res = gp.analyse(_straddling(m), "uptrend", draws=4000)
    lo, hi = res["R_diff_ci90"]
    assert lo < 0 < hi, "造的样本没让上下界分开"
    assert res["R_diff_point"] > 0, "点估计要落在'看起来通过'的一侧"
    assert not res["crit"]["c2_R_up"]


def test_criterion4_reads_the_upper_bound_it_only_bans_significantly_negative():
    """④是「**不显著为负**」(上界 > 0), 不是「显著为正」。
    点估计略负但 CI 含 0 → 该过 —— 读成下界或点估计都会把合格的判死。"""
    def m(r, up):
        r["FAST"] = dict(r["FAST"], ret=0.09 if up else -0.11)    # 点估计 ≈ −1%
    res = gp.analyse(_straddling(m), "uptrend", draws=4000)
    lo, hi = res["fast_ret_ci90"]
    assert lo < 0 < hi, "造的样本没让上下界分开"
    assert res["fast_ret_point"] < 0, "点估计要落在'看起来不过'的一侧"
    assert res["crit"]["c4_abs_exp"], "收益 CI 含 0 = 不显著为负 = 该过"


def test_stop_guard_reads_the_upper_bound_not_the_point_or_lower_bound():
    """判据 5 是「**CI90 上界**< +5pp」—— 点估计接近 0 但**噪音大到上界越线**时必须否。

    ⚠️ 立此条的原因: 第一版守卫测试造的是 +25pp 的巨大差异, CI 两端同号,
    于是把判据写成"读下界"**一条都不红**—— 那时的测试没能区分上界与下界。
    这里让逐月的止损率差正负交替 (点估计 ≈0、月间方差大), 上界与下界必然异号。
    """
    rows = []
    for k in range(240):
        r = _rows(1)[0]
        m = k % 40
        r["month"] = f"2020-{m:02d}"
        fast_stops = m % 2 == 0                     # 单月里 FAST/SLOW 一边倒
        r["FAST"] = dict(r["FAST"], stopped=fast_stops)
        r["SLOW"] = dict(r["SLOW"], stopped=not fast_stops)
        rows.append(r)
    res = gp.analyse(rows, "uptrend", draws=2000)
    lo, hi = res["stop_diff_ci90"]
    assert lo < gp.STOP_GUARD < hi, "造的样本没让上下界分开，这条测不到东西"
    assert abs(res["stop_diff_point"]) < gp.STOP_GUARD, "点估计也该在守卫内"
    assert not res["crit"]["c5_stop_guard"], "上界越线就必须否 —— 不许读点估计或下界"


def test_kish_floor_reports_insufficient_not_reject():
    """§3b-C: Kish<30 是**数据不足不裁决**, 不是"不采纳", 也不并类凑样本。"""
    rows = _rows(20, months=1)          # 全挤在一个月 → Kish 低
    r = gp.analyse(rows, "ath", draws=500)
    assert r["kish"] < gp.KISH_MIN and r["verdict"] == "insufficient"
    v = gp.verdicts({"ath": r})
    assert v["ath"]["decision"] == "insufficient"
    assert "不并类" in v["ath"]["text"]


def test_repair_is_control_only_even_when_it_passes():
    """§3b-D: ①样本已被 ladder_entry 用过, 同一批数据不裁两次。"""
    r = gp.analyse(_rows(200, cls="repair"), "repair", draws=500)
    assert r["crit"]["all_pass"]
    v = gp.verdicts({"repair": r})
    assert v["repair"]["decision"] == "control_only"


def test_classes_are_adjudicated_independently():
    """一类过不外推到别类 (§5 末句)。"""
    good = gp.analyse(_rows(200, cls="uptrend"), "uptrend", draws=500)
    bad = gp.analyse(_rows(200, cls="range_top",
                           FAST={"dep": 1.0, "ret": -0.05, "riskN": 2.0,
                                 "risk": 0.1, "stopped": True, "R": -0.9}),
                     "range_top", draws=500)
    v = gp.verdicts({"uptrend": good, "range_top": bad})
    assert v["uptrend"]["decision"] == "adopt"
    assert v["range_top"]["decision"] == "reject"
    assert v["ath"]["decision"] == "insufficient"       # 缺席 = 不裁决, 不是默认采纳
