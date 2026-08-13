"""gate_backtest2 机械件的合成数据验证。

预注册纪律: 实现期间只允许合成数据 (本文件), 真数据只跑一次。
每个测试对应 docstring 预注册里的一条机械语义。
"""
import pytest

from quant.research.gate_backtest import gate_level
from quant.research import gate_backtest2 as g2


# ---------- 序列基础 ----------

def test_atr_series_basic():
    highs = [11, 12, 13]
    lows = [9, 10, 11]
    closes = [10, 11, 12]
    atr = g2.atr_series(highs, lows, closes, 2)
    # TR: d0=2; d1=max(2, |12-10|, |10-10|)=2; d2=max(2, |13-11|, |11-11|)=2
    assert atr[0] is None and atr[1] == 2 and atr[2] == 2


def test_confirmed_swing_lows_mirror():
    lows = [5, 4, 3, 4, 5, 4, 5]
    out = g2.confirmed_swing_lows(lows, wing=2)
    assert out == [(2, 3)]           # 严格单谷, wing=2


def test_last_swing_high_array_equals_round1_gate_level():
    """swing level 预计算数组必须逐点等价 round-1 的 gate_level('swing')。"""
    highs = [10, 12, 11, 10, 13, 12, 11, 14, 13, 12, 15, 14, 13, 12, 16]
    closes = [h - 1 for h in highs]
    lows = [h - 2 for h in highs]
    ma60 = [None] * len(highs)
    swings = g2.confirmed_swing_highs(highs)
    arr = g2.last_swing_high_array(len(highs), swings)
    for j in range(len(highs)):
        assert arr[j] == gate_level("swing", j, closes, highs, ma60, swings), j


# ---------- vol / fire ----------

def test_vol_ok_needs_history_and_ratio():
    vols = [1.0] * 30
    vols[26] = 2.0
    assert not g2.vol_ok(vols, 10)          # 历史不足
    assert not g2.vol_ok(vols, 25)          # 1.0/1.0 < 1.2
    assert g2.vol_ok(vols, 26)              # 2.0/1.0 ≥ 1.2


def test_fire_scan_streak_reset_and_need():
    closes = [10, 11, 9, 11, 11, 11]
    fired = g2.fire_scan(0, 5, closes, lambda j: 10, need=2)
    assert fired == (4, 10)                 # j=2 跌回清零, j=3-4 连续两日


def test_fire_scan_none_level_resets_streak():
    closes = [10, 11, 11, 11]
    lvls = {1: 10, 2: None, 3: 10}
    fired = g2.fire_scan(0, 3, closes, lambda j: lvls[j], need=2)
    assert fired is None                    # None 打断 streak, 3 号只有 streak=1


def test_fire_scan_vol_filter_delays_fire():
    closes = [10.0] * 25 + [11.0] * 5
    vols = [1.0] * 30
    vols[27] = 2.0
    fired = g2.fire_scan(24, 29, closes, lambda j: 10, need=1, vols=vols, vol_on=True)
    assert fired == (27, 10)                # 25/26 量比不过, 27 才 fire


def test_fire_scan_cluster_none_never_fires():
    closes = [10, 11, 12, 13]
    assert g2.fire_scan(0, 3, closes, lambda j: None, need=1) is None


# ---------- stop / policy ----------

def test_stop_scan_close_below_then_next_close_exit():
    closes = [100, 99, 97, 96, 98]
    hit = g2.stop_scan(0, 4, closes, stop_lvl=98)
    assert hit == (2, 3, 96)                # 97<98 触发, 次日收盘 96 出场


def test_stop_scan_trigger_at_final_bar_is_pending():
    """终点日触发 = 出场悬置: 无次日可成交 → None, 按终点收盘 mark 不计滑点。"""
    closes = [100, 99, 90]
    assert g2.stop_scan(0, 2, closes, stop_lvl=95) is None
    segs = [(0, 100.0, None, None, 95.0)]
    assert g2.policy_value(segs, 2, closes) == pytest.approx(-0.10)   # 无滑点
    assert not g2.stopped_by(segs, 2)


def test_stop_scan_none_stop_never_triggers():
    assert g2.stop_scan(0, 3, [100, 1, 1, 1], None) is None


def test_policy_value_cash_stop_and_open():
    closes = [100, 100, 100, 105, 90, 90, 110]
    segs = [(2, 100.0, 4, 90.0, 95.0)]      # entry j=2 @100, 触发后 exit idx4 @90
    stopped = 90 * (1 - g2.SLIP) / 100 - 1
    assert g2.policy_value(segs, 6, closes) == pytest.approx(stopped)   # 止损后现金
    assert g2.policy_value(segs, 3, closes) == pytest.approx(0.05)      # 出场前按 mark
    assert g2.policy_value(segs, 1, closes) == 0.0                      # 入场前 = 现金
    assert g2.policy_value([], 6, closes) == 0.0                        # not fired = 0


def test_whipsaw_and_stopped_by():
    closes = [100] * 8
    segs = [(1, 100.0, 3, 90.0, 95.0), (5, 100.0, None, None, 95.0)]
    assert g2.stopped_by(segs, 7) and not g2.stopped_by(segs, 2)
    assert g2.whipsaw_cost(segs, 7) == pytest.approx(90 * (1 - g2.SLIP) / 100 - 1)


# ---------- D2 四口径 ----------

def test_stop_level_for_modes():
    atr20 = [2.0] * 10
    swings_l = [(0, 95.0), (2, 80.0)]
    assert g2.stop_level_for("primary", "A", 5, 100, atr20, None, swings_l) == 96
    assert g2.stop_level_for("sens3n", "A", 5, 100, atr20, None, swings_l) == 94
    assert g2.stop_level_for("bfloor", "B", 5, 100, atr20, 98.0, swings_l) == 98
    assert g2.stop_level_for("bfloor", "A", 5, 100, atr20, 98.0, swings_l) == 96
    # structure: 入场价下方最近的已确认 swing low (95 比 80 近)
    assert g2.stop_level_for("structure", "B", 5, 100, atr20, None, swings_l) == 95
    # 无下方 swing low → 回退 2N
    assert g2.stop_level_for("structure", "B", 5, 70, atr20, None, swings_l) == 66


def test_nearest_swing_low_requires_confirmation():
    # idx=4, wing=2 → 只在 j≥6 可用
    swings_l = [(4, 95.0)]
    assert g2.nearest_swing_low_below(5, 100, swings_l) is None
    assert g2.nearest_swing_low_below(6, 100, swings_l) == 95.0


# ---------- cluster gate ----------

def test_cluster_gate_groups_and_overhead():
    swings_h = [(0, 100.4), (5, 100.0)]
    swings_l = [(3, 99.8), (8, 110.0)]
    lvl = g2.cluster_gate(30, close_i=90, swings_h=swings_h, swings_l=swings_l, atr14_i=2.0)
    assert lvl == 100.4                     # 三触碰簇上沿; 110 单触碰不成簇
    assert g2.cluster_gate(30, 105, swings_h, swings_l, 2.0) is None   # 头顶无 ≥2 簇
    assert g2.cluster_gate(30, 90, [], [], 2.0) is None
    assert g2.cluster_gate(30, 90, swings_h, swings_l, None) is None


def test_cluster_gate_no_lookahead_pivot_confirmation():
    # pivot idx=29, wing=2 → 30 日画卡时未确认, 不可用
    swings_h = [(29, 100.0), (28, 100.3)]
    swings_l = [(27, 99.9)]
    lvl = g2.cluster_gate(30, 90, swings_h, swings_l, 2.0)
    assert lvl == 100.3                     # 只有 28/27 可用 (idx+2≤30), 29 被排除


# ---------- replay ----------

def test_simulate_segments_single_vs_replay():
    #        0   1     2    3    4    5
    closes = [9, 11, 8.5, 9.5, 11, 12]
    atr20 = [1.0] * 6
    single = g2.simulate_segments(0, 5, closes, lambda j: 10, 1, None, False,
                                  "primary", atr20, [], max_segments=1)
    assert len(single) == 1
    j, px, x, xp, stop = single[0]
    assert (j, px, stop) == (1, 11, 9.0) and (x, xp) == (3, 9.5)   # 8.5<9 触发, 次日 9.5 出

    replay = g2.simulate_segments(0, 5, closes, lambda j: 10, 1, None, False,
                                  "primary", atr20, [], max_segments=3)
    assert len(replay) == 2
    assert replay[1][0] == 4 and replay[1][2] is None              # 4 号重进, 未再止损


def test_replay_respects_max_segments():
    closes = [9] + [11, 8.5, 9.5] * 4       # 反复 whipsaw
    atr20 = [1.0] * len(closes)
    segs = g2.simulate_segments(0, len(closes) - 1, closes, lambda j: 10, 1, None,
                                False, "primary", atr20, [],
                                max_segments=g2.MAX_REPLAY_SEGMENTS)
    assert len(segs) <= g2.MAX_REPLAY_SEGMENTS


# ---------- bootstrap ----------

def test_bootstrap_degenerate_ci_collapses():
    # 槽位: [Σdiff, n, Σstopdiff, Σnf, n_nf, Σf, n_f]
    summary = {"cells": {"swing_x1": {
        "appendix": False,
        "primary_T60": {"_by_month": {"2020-01": [2.0, 2, -2, 0.0, 0, 2.0, 2],
                                      "2020-02": [1.0, 1, -1, 0.0, 0, 1.0, 1]}},
    }}}
    g2.bootstrap_cis(summary, modes=("primary",), draws=200, seed=1)
    row = summary["cells"]["swing_x1"]["primary_T60"]
    assert row["diff_ci90"] == [1.0, 1.0]              # 每月 diff 均值恒 1.0 → 塌缩
    assert row["stop_rate_diff_ci90"] == [-1.0, -1.0]  # 每月止损差恒 -1
    assert row["notfired_gap_ci90"] is None            # 无 notfired 样本 → None
    assert summary["bootstrap"]["draws"] > 0


def test_p10_diff_ci_single_month_collapses():
    b = list(range(20))            # p10 = v[max(0, int(2)-1)] = v[1] = 1
    a = [x + 5 for x in range(20)]  # p10 = 6
    summary = {"cells": {"swing_x1": {
        "appendix": False,
        "primary_T60": {"_by_month_vals": {"2020-01": (b, a)}},
    }}}
    g2.p10_diff_cis(summary, draws=50, seed=3)
    row = summary["cells"]["swing_x1"]["primary_T60"]
    assert row["p10_diff_point"] == -5.0
    assert row["p10_diff_ci90"] == [-5.0, -5.0]        # 单月重采样恒等 → 塌缩


# ---------- 端到端 (合成 jsonl → collect → summarize) ----------

def _write_synth(tmp_path):
    """240 平台 @100 → 20 日阴跌至 80 (episode) → 震荡修复。determinstic。"""
    prices = [100.0] * 240 + [100 - k for k in range(1, 21)]  # 241..260 跌到 80
    prices += [80 + min(k, 25) * 0.8 for k in range(1, 101)]  # 缓慢修复到 100
    rows = []
    for d, c in enumerate(prices):
        rows.append({"time": f"2020-{1 + d // 28:02d}-{1 + d % 28:02d} 00:00:00"
                     if d < 300 else f"2021-{1 + (d - 300) // 28:02d}-{1 + (d - 300) % 28:02d} 00:00:00",
                     "open": c, "high": c + 1, "low": c - 1, "close": c,
                     "volume": 1000, "turnover": None})
    p = tmp_path / "SYN.jsonl"
    import json
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return p


def test_collect_and_summarize_end_to_end(tmp_path):
    _write_synth(tmp_path)
    episodes = g2.collect(kdir=tmp_path)
    assert len(episodes) >= 1
    e = episodes[0]
    # World A primary: 破位日收盘入场
    a = e["A"]["primary"]
    assert a[0][0] == e["i"] and a[0][1] == pytest.approx(e["closes_ref"][e["i"]])
    summary = g2.summarize(episodes)
    assert summary["n_episodes"] == len(episodes)
    for key, cell in summary["cells"].items():
        assert 0.0 <= cell["fire_rate"] <= 1.0
        row = cell["primary_T60"]
        assert row["n"] > 0
        # policy 口径: not-fired 的 B = 0, 因此 B 的 policy 收益有界且非 NaN
        assert row["mean"] == row["mean"]
    g2.bootstrap_cis(summary, draws=100, seed=2)
    g2.p10_diff_cis(summary, draws=50, seed=3)
    summary["d1_flags"] = g2.d1_flags(summary)
    g2.strip_private(summary)
    for cell in summary["cells"].values():
        for row in cell.values():
            if isinstance(row, dict):
                assert "_by_month" not in row and "_by_month_vals" not in row
