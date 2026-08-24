"""短期道氏结构状态机的四步合成覆盖。

合成 bar 约定：每根写成 (high, low, close)，open = close（open 不进判别式）。
转折 K 线 = 收盘越过**前一根**的极值（阴线 = close < prev low；阳线 = close > prev high）。
"""
import datetime as dt

import pytest

from quant.core import dow as D

# ══════════════════════════════════════════════════════════════
# 合成数据
# ══════════════════════════════════════════════════════════════


def _bars(path, start="2026-01-01", vol=None):
    d0 = dt.date.fromisoformat(start)
    out = []
    for i, (h, lo, c) in enumerate(path):
        out.append({"time": f"{(d0 + dt.timedelta(days=i)).isoformat()} 00:00:00",
                    "open": c, "high": h, "low": lo, "close": c,
                    "volume": (vol[i] if vol else 1_000_000)})
    return out


def _mirror(bars, pivot=400.0):
    return [{**b, "open": pivot - b["open"], "close": pivot - b["close"],
             "high": pivot - b["low"], "low": pivot - b["high"]} for b in bars]


def _types(x):
    return [e["type"] for e in x["events"]]


# 合成例 100 → 200 → 180 → 190 → 170，逐根写开：
RISE = [(101, 99, 100), (111, 101, 110), (121, 111, 120), (131, 121, 130), (141, 131, 140),
        (151, 141, 150), (161, 151, 160), (171, 161, 170), (181, 171, 180), (191, 181, 190),
        (200, 191, 198)]                         # idx10 = 顶 200
PULLBACK = [(196, 185, 186),                     # idx11 阴线: 186 < 191 → 观察①
            (188, 180, 181)]                     # idx12 回调低点 180（候选 lsl）
BOUNCE = [(190, 182, 189),                       # idx13 阳线: 189 > 188 → lsl 180 成形（观察②）
          (190, 185, 188)]                       # idx14 反弹到 190 = 候选 LH
FAIL = [(187, 181, 182)]                         # idx15 阴线: 182 < 185 → 反弹失败 LH 190（观察③）
BREAK = [(183, 169, 170)]                        # idx16 收 170 < lsl 180 → 终止确认
SCENARIO = RISE + PULLBACK + BOUNCE + FAIL + BREAK


# ══════════════════════════════════════════════════════════════
# 1. 四步各一例
# ══════════════════════════════════════════════════════════════


def test_rise_alone_is_advancing_with_no_observation():
    st = D.structure_state(_bars(RISE))
    assert st["trend"] is None or st["phase"] == 0      # 没出过阴线 → 还没进观察期


def test_first_bearish_bar_after_the_top_opens_observation_1():
    """「一根阴线破坏了该结构」= 收破前一根低点；lsh 就此定格为 200。"""
    st = D.structure_state(_bars(RISE + PULLBACK[:1]))
    assert st["trend"] == "up" and st["phase"] == 1
    assert st["imp"]["price"] == 200 and st["obs_start"]["broke"] == 191
    assert st["candidate"]["role"] == "lsl" and st["candidate"]["price"] == 185
    assert "一根阳线收上 196" in st["candidate"]["confirm_if"]


def test_observation_1_tracks_the_running_low_until_a_bullish_bar_forms_lsl():
    """第 1 步：等 lsl 成形 —— 成形 = 一根阳线收上前一根高点。"""
    st = D.structure_state(_bars(RISE + PULLBACK))
    assert st["phase"] == 1 and st["candidate"]["price"] == 180
    st2 = D.structure_state(_bars(RISE + PULLBACK + BOUNCE[:1]))
    assert st2["phase"] == 2 and st2["obs"]["price"] == 180
    assert st2["watch"]["terminate_level"] == 180 and st2["watch"]["resume_level"] == 200
    assert "obs2" in _types(st2)


def test_bounce_that_reclaims_lsh_resumes_the_trend():
    """第 2 步：反弹破 lsh = 洗盘后延续，lsl 升格为 HL。"""
    bars = _bars(RISE + PULLBACK + BOUNCE + [(203, 195, 202)])
    st = D.structure_state(bars)
    assert st["trend"] == "up" and st["phase"] == 0
    assert st["last_resolution"]["type"] == "resume" and st["last_resolution"]["bars_ago"] == 0
    assert st["ret"]["price"] == 180 and st["imp"]["price"] == 203


def test_failed_bounce_fixes_lh_and_break_of_lsl_terminates():
    """第 3/4 步：反弹失败（LH 190）→ 跌破 lsl 180 = 终止确认，趋势翻面。"""
    st3 = D.structure_state(_bars(RISE + PULLBACK + BOUNCE + FAIL))
    assert st3["phase"] == 3 and st3["cnt"]["price"] == 190
    st = D.structure_state(_bars(SCENARIO))
    assert st["trend"] == "down" and st["phase"] == 0
    res = st["last_resolution"]
    assert res["type"] == "terminate" and res["from_phase"] == 3 and res["level"] == 180
    assert "转下跌" in res["cn"] and res["cnt"]["label"] == "LH"
    # 新下跌的 refs：冲动极值 = 破位那根的低点，要守的 LH = 反弹高点
    assert st["imp"]["price"] == 169 and st["ret"]["price"] == 190
    assert st["labels"] == "LH+?"                         # 第一个 lsl 没有前一个可比


def test_break_straight_from_phase_2_terminates_with_bounce_high_as_lh():
    """反弹后第一根阴线就收破 lsl —— 不经过③，反弹极值直接记为 LH。"""
    st = D.structure_state(_bars(RISE + PULLBACK + BOUNCE + [(186, 169, 172)]))
    assert st["trend"] == "down"
    assert st["last_resolution"]["from_phase"] == 2 and st["last_resolution"]["cnt"]["price"] == 190


def test_straight_crash_stays_in_phase_1_and_never_terminates():
    """「几根大阴线直接砸穿」：没有阳线就没有 lsl，机器一直停在①、候选跟着新低走 —— 归 stop。"""
    crash = RISE + [(196, 185, 186), (184, 170, 171), (169, 150, 151), (149, 130, 131)]
    st = D.structure_state(_bars(crash))
    assert st["phase"] == 1 and st["candidate"]["price"] == 130
    assert "terminate" not in _types(st)


def test_v_reversal_in_phase_1_resumes_without_an_lsl():
    bars = _bars(RISE + PULLBACK + [(205, 183, 204)])        # 一根直接收复 200
    st = D.structure_state(bars)
    assert st["phase"] == 0 and st["last_resolution"]["from_phase"] == 1
    assert st["ret"]["price"] == 180                         # 候选低点直接升格为 HL


def test_bullish_bar_in_phase_3_reopens_the_bounce():
    bars = _bars(RISE + PULLBACK + BOUNCE + FAIL + [(189, 184, 188)])   # 阳线 188 > 187
    st = D.structure_state(bars)
    assert st["phase"] == 2 and _types(st)[-1] == "obs2_again"
    assert st["cnt"]["price"] == 190                          # LH 候选仍是反弹最高


def test_intrabar_dip_below_lsl_without_close_moves_lsl_down_not_terminate():
    bars = _bars(RISE + PULLBACK + BOUNCE + [(189, 178, 186)])   # low 178 < 180, close 186
    st = D.structure_state(bars)
    assert st["phase"] == 2 and st["obs"]["price"] == 178
    assert "terminate" not in _types(st)


def test_red_candle_that_holds_the_prior_low_is_not_a_structure_break():
    """收阴 ≠ 破坏结构：没收破前一根低点就还在推进中。"""
    st = D.structure_state(_bars(RISE + [(199, 192, 193)]))   # 193 > 191
    assert st["trend"] is None or st["phase"] == 0


# ══════════════════════════════════════════════════════════════
# 2. 对称 / 无前视 / 标签
# ══════════════════════════════════════════════════════════════


def test_downtrend_is_the_exact_mirror():
    """「反之下跌趋势亦然」：价格翻面 → 趋势翻面、阶段与事件逐根相同。"""
    up = D.replay(_bars(SCENARIO))
    dn = D.replay(_mirror(_bars(SCENARIO)))
    flip = {"up": "down", "down": "up", None: None}
    assert [(flip[t], p) for t, p in up["timeline"]] == dn["timeline"]
    assert [(e["idx"], e["type"]) for e in up["events"]] == \
        [(e["idx"], e["type"]) for e in dn["events"]]
    st = D.structure_state(_mirror(_bars(SCENARIO)))
    assert st["trend"] == "up" and "转上涨" in st["last_resolution"]["cn"]
    assert st["last_resolution"]["cnt"]["label"] == "HL"


def test_no_lookahead_prefix_state_equals_replay_timeline():
    """第 t 根只用 bars[≤t]：对每个前缀单独跑，必须与全量回放在那一根的读数逐字相同。"""
    bars = _bars(SCENARIO + [(172, 160, 161), (175, 162, 174), (171, 165, 166)])
    full = D.replay(bars)
    for t in range(2, len(bars)):
        st = D.structure_state(bars[:t + 1])
        assert (st["trend"], st["phase"]) == full["timeline"][t], t
        want = [(e["idx"], e["type"]) for e in full["events"] if e["idx"] <= t]
        got = [(e["idx"], e["type"]) for e in D.replay(bars[:t + 1])["events"]]
        assert got == want, t


def test_labels_follow_the_structural_swings():
    """两个完整周期：150 顶 / 130 底 / 收复 → 200 顶 / 180 底 → HH+HL；终止后 LH+HL。"""
    cycle1 = [(101, 99, 100), (126, 101, 125), (150, 126, 149),          # 顶 150
              (146, 136, 137),                                           # 阴线 → ①
              (140, 130, 131),                                           # 低 130
              (144, 133, 143),                                           # 阳线 → lsl 130 成形
              (160, 145, 158)]                                           # 收复 150 → 恢复 HL=130
    cycle2 = [(180, 160, 179), (200, 181, 198)] + PULLBACK + BOUNCE      # 顶 200 / lsl 180
    st = D.structure_state(_bars(cycle1 + cycle2))
    assert st["phase"] == 2 and st["labels"] == "HH+HL"
    st = D.structure_state(_bars(cycle1 + cycle2 + FAIL + BREAK))
    assert st["trend"] == "down" and st["labels"] == "LH+HL"
    assert st["retrace"] is None                                         # 终止后新段无折返


def test_retrace_depth_is_reported_against_the_last_leg():
    """回撤深度 = (lsh − lsl)/(lsh − HL)；跌破前 HL 要单独标出来。"""
    cycle1 = [(101, 99, 100), (126, 101, 125), (150, 126, 149), (146, 136, 137),
              (140, 130, 131), (144, 133, 143), (160, 145, 158)]
    bars = _bars(cycle1 + [(200, 181, 198)] + PULLBACK)                 # 180 vs 段 130→200
    st = D.structure_state(bars)
    r = st["retrace"]
    assert r["pct"] == round((200 - 180) / (200 - 130), 2) and r["broke_prev_ret"] is False
    deep = _bars(cycle1 + [(200, 181, 198)] + PULLBACK + [(179, 125, 126)])
    assert D.structure_state(deep)["retrace"]["broke_prev_ret"] is True


# ══════════════════════════════════════════════════════════════
# 3. 输出面
# ══════════════════════════════════════════════════════════════


def test_stage_names_cover_every_state():
    for tr in ("up", "down"):
        for ph in range(4):
            assert (tr, ph) in D.STAGE_CN
    assert (None, 0) in D.STAGE_CN


def test_describe_names_the_lines_with_their_meaning():
    st = D.structure_state(_bars(RISE + PULLBACK + BOUNCE + FAIL))      # 观察③
    txt = "\n".join(D.describe(st))
    assert "收复 200" in txt and "跌破 180" in txt and "终止确认" in txt
    st1 = D.structure_state(_bars(RISE + PULLBACK))                     # 观察①
    assert "一根阳线收上 188" in "\n".join(D.describe(st1))


def test_candidate_in_phase_2_is_the_bounce_high_with_its_failure_bar():
    st = D.structure_state(_bars(RISE + PULLBACK + BOUNCE))
    c = st["candidate"]
    assert c["role"] == "LH" and c["price"] == 190 and "一根阴线收破 185" in c["confirm_if"]


def test_leg_volume_compares_attempt_to_retrace():
    vol = [1_000_000] * 11 + [2_000_000] * 2 + [1_000_000] * 2 + [3_000_000]
    bars = _bars(RISE + PULLBACK + BOUNCE + FAIL, vol=vol)      # 回调腿 idx11-12 放量
    lv = D.structure_state(bars)["leg_volume"]
    assert lv and lv["retrace_bars"] == 2 and lv["attempt_bars"] == 2 and lv["ratio"] == 0.5


def test_short_history_is_undefined_not_a_crash():
    st = D.structure_state(_bars([(101, 99, 100), (102, 100, 101)]))
    assert st["trend"] is None and st["stage_cn"] == D.STAGE_CN[(None, 0)]
    assert D.describe(st) == [D.STAGE_CN[(None, 0)]]


@pytest.mark.parametrize("mod", ["quant.core.sizing", "quant.core.technical", "quant.decision",
                                 "quant.report", "quant.sync"])
def test_core_dow_imports_nothing_else(mod):
    from pathlib import Path
    assert mod not in Path(D.__file__).read_text()


# ══════════════════════════════════════════════════════════════
# 4. Rhea 回撤带
# ══════════════════════════════════════════════════════════════


def test_retrace_bands_are_rheas_thirds():
    """阈值是 Rhea 发表的 1/3 / 2/3（+1 = 吐回整段），不是拟合的；带名覆盖全部区间。"""
    assert [lim for lim, _ in D.RETRACE_BANDS] == [1 / 3, 2 / 3, 1.0]
    assert D.retrace_band(0.1) == "<1/3" and D.retrace_band(1 / 3) == "1/3-2/3"
    assert D.retrace_band(2 / 3) == "1/3-2/3" and D.retrace_band(0.9) == "2/3-1"
    assert D.retrace_band(1.0) == "2/3-1" and D.retrace_band(1.2) == ">1"
    assert D.retrace_band(None) is None
    for name in ("<1/3", "1/3-2/3", "2/3-1", ">1"):
        assert name in D.RETRACE_BAND_CN


def test_lsl_formation_records_the_retrace_against_the_last_leg():
    """回撤比例在 lsl 成形时定格；终止事件带着它的带名。"""
    cycle1 = [(101, 99, 100), (126, 101, 125), (150, 126, 149), (146, 136, 137),
              (140, 130, 131), (144, 133, 143), (160, 145, 158)]
    bars = _bars(cycle1 + [(200, 181, 198)] + PULLBACK + BOUNCE)
    st = D.structure_state(bars)
    rf = st["retrace_at_form"]
    assert rf["pct"] == round((200 - 180) / (200 - 130), 4) and rf["band"] == "<1/3"
    assert set(rf) == {"pct", "band", "band_cn"}
    term = D.structure_state(_bars(cycle1 + [(200, 181, 198)] + PULLBACK + BOUNCE + FAIL + BREAK))
    assert term["last_resolution"]["band"] == "<1/3"
    assert term["retrace_at_form"] is None                    # 新段还没有折返


def test_first_cycle_has_no_retrace_band():
    """没有前一段（seed 之后第一次回调）→ 分母不存在，不编一个数。"""
    st = D.structure_state(_bars(RISE + PULLBACK + BOUNCE))
    assert st["retrace_at_form"] is None


# ══════════════════════════════════════════════════════════════
# 5. 盘前清单排序键
# ══════════════════════════════════════════════════════════════


def _st(trend, phase, res=None):
    return {"trend": trend, "phase": phase, "last_resolution": res,
            "stage_cn": D.STAGE_CN[(trend, phase)]}


def test_priority_tiers_follow_structural_ordering():
    """优先 = 上涨推进中 / 反转向上已确认 / 洗盘后延续；次位 = 观察期；末位 = 下跌推进中。"""
    assert D.priority(_st("up", 0))["tier"] == 1
    rev = D.priority(_st("up", 0, {"type": "terminate", "bars_ago": 2, "date": "2026-08-14"}))
    assert rev["tier"] == 1 and "反转向上已确认" in rev["cn"] and "08-14" in rev["cn"]
    wash = D.priority(_st("up", 0, {"type": "resume", "bars_ago": 1, "date": "2026-08-21"}))
    assert wash["tier"] == 1 and "洗盘后延续" in wash["cn"]
    for ph in (1, 2, 3):
        p = D.priority(_st("up", ph))
        assert p["tier"] == 2 and f"终止观察{'①②③'[ph - 1]}" in p["cn"]
    for ph in (2, 3):
        p = D.priority(_st("down", ph))
        assert p["tier"] == 2 and "反转未确认" in p["cn"]
    assert D.priority(_st("down", 0))["tier"] == 3 and D.priority(_st("down", 1))["tier"] == 3
    assert D.priority({"trend": None, "phase": 0})["tier"] == 3


def test_priority_sort_key_breaks_ties_with_the_weekly_level():
    """同档内周线级打平局：周线 上涨·推进中 > 上涨·观察期 > 下跌 > 未定。"""
    a = D.priority(_st("up", 0), weekly=_st("up", 0))
    b = D.priority(_st("up", 0), weekly=_st("up", 2))
    c = D.priority(_st("up", 0), weekly=_st("down", 0))
    d = D.priority(_st("up", 0), weekly=None)
    assert a["sort_key"] < b["sort_key"] < c["sort_key"] < d["sort_key"]
    assert a["weekly_cn"] == "上涨·推进中" and d["weekly_cn"] is None
    # 次位的周线级再好也排在优先之后
    assert D.priority(_st("up", 2), weekly=_st("up", 0))["sort_key"] > c["sort_key"]


def test_priority_of_bars_runs_both_levels_without_lookahead():
    """一步到位接口：日线级 + 走完的周 K；短序列 → 末位·方向未定而不是崩。"""
    bars = _bars(SCENARIO)
    pr = D.priority_of_bars(bars, weekly_bars=None)
    assert pr["tier"] == 3 and "下跌推进中" in pr["cn"] and pr["stage_cn"] == D.STAGE_CN[("down", 0)]
    pr2 = D.priority_of_bars(_bars([(101, 99, 100), (102, 100, 101)]))
    assert pr2["tier"] == 3 and "方向未定" in pr2["cn"]
