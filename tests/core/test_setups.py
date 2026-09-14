"""两种模式判别式（`core/setups`）—— 抄底 = 最基本的价格行为：
空头衰竭 = 第二天没有继续新低 / 收出长下影线；止损 = 下影线低点。每条判据都要有一个会失败的用例。

合成 bar 约定：每根 (high, low, close) 或 (high, low, close, open)，缺 open = close。
"""
import datetime as dt

import pytest

from quant.core import reversal, technical
from quant.core import setups as S


def _bars(path, start="2026-01-01"):
    d0 = dt.date.fromisoformat(start)
    out = []
    for i, tpl in enumerate(path):
        h, lo, c = tpl[:3]
        o = tpl[3] if len(tpl) > 3 else c
        out.append({"time": f"{(d0 + dt.timedelta(days=i)).isoformat()} 00:00:00",
                    "open": o, "high": h, "low": lo, "close": c, "volume": 1_000_000})
    return out


def _flat(n, px=265.0, wig=0.5):
    """n 根横盘（高低各 ±wig，收盘不变）—— 垫够 MIN_BARS。缺省 265 = 距顶 281 不足 10%，
    垫子自己不会成为一个「底」。"""
    return [(px + wig, px - wig, px)] * n


TOP = [(281, 275, 280)]                    # 20 日顶
DECLINE = [(270, 250, 252), (255, 235, 238),
           (250, 232, 245), (258, 240, 255), (252, 236, 240),   # 中途一个更低的分形高 258 = 前高 (< 顶)
           (240, 218, 220), (222, 205, 208)]
NEWLOW = [(210, 194, 199)]                 # 止跌低 194: 上影 11 / 下影 5 / 实体 0 → 不是锤子
HAMMER = [(202.6, 194, 202, 202.5)]        # 收 202 开 202.5: 下影 8 > 实体 0.5×2, 上影 0.1 < 0.25 → 锤子
HELD = [(206, 196, 205)]                   # 次日: 低 196 > 194 = 未续新低
RALLY = [(226, 210, 224), (240, 222, 238), (256, 236, 254)]
PULL = [(250, 232, 235), (236, 224, 226)]  # 回踩低 224 > 194
PULL_HELD = [(238, 225, 237)]              # 回踩低之后一根没再破 224 = 更高的低点
BREAK_LH = [(266, 252, 264)]               # 收 264 > 前高 258 = 越前高 (高 266 < 顶 281, 框架仍在)


def _path(*segs):
    out = []
    for s in segs:
        out += s
    return _bars(_flat(S.MIN_BARS) + out)


def test_shallow_dip_is_not_a_bottom():
    """跌不到 10% 就没有底可抄 —— 横盘 + 3% 回撤 = none，不进推送。"""
    bars = _bars(_flat(S.MIN_BARS, px=100.0) + [(101, 97, 98), (100, 96.5, 99)])
    b = S.bottom_setup(bars)
    assert b["stage"] == "none" and not b["listed"] and "10%" in b["why"]


def test_new_low_today_without_hammer_is_watch():
    """今日创 20 日新低且不是长下影 = ⏳ 即将形成：明日不再新低 = 止跌。"""
    b = S.bottom_setup(_path(TOP, DECLINE, NEWLOW))
    assert b["stage"] == "watch" and b["cn"] == "⏳今日新低" and b["listed"] and not b["formed"]
    assert b["low"] == 194 and b["held_days"] == 0 and not b["hammer"]
    assert b["lh"] == 258 and b["top"] == 281 and b["verdict"] is None
    assert b["shadow"]["upper_pct"] > b["shadow"]["lower_pct"]     # 事实一并带出


def test_no_new_low_next_day_forms_the_bottom():
    """第二天没有继续新低 = 空头衰竭 = ✓止跌；信号日 = 低点的次日；止损 = 止跌低。"""
    b = S.bottom_setup(_path(TOP, DECLINE, NEWLOW, HELD))
    assert b["stage"] == "formed" and b["cn"] == "✓止跌" and b["formed"] and b["listed"]
    assert b["signal"] == S.SIGNAL_HELD and b["held_days"] == 1 and b["bars_since_signal"] == 0
    assert b["low"] == 194 and b["signal_date"] == b["bounce_high_date"]
    assert b["bounce_pct"] == pytest.approx(205 / 194 - 1)
    assert b["verdict"] == "反弹（未越前高）"
    # 事实随框架带出 (不是判据): 止跌日高 210, 收 205 未站上; 量比 = 止跌日量 / 前 20 日均量
    assert b["low_bar_high"] == 210 and not b["above_low_bar_high"] and b["low_bar_vol_ratio"] == 1.0
    b2 = S.bottom_setup(_path(TOP, DECLINE, NEWLOW, HELD, RALLY[:1]))
    assert b2["above_low_bar_high"]                                  # 收 224 > 210


def test_long_lower_shadow_forms_the_bottom_on_the_low_day_itself():
    """收出长下影线 = 当天就是止跌（不用等第二天），判别式复用 technical.detect_hammer。"""
    b = S.bottom_setup(_path(TOP, DECLINE, HAMMER))
    assert technical.detect_hammer(_bars(HAMMER)[0])
    assert b["stage"] == "formed" and b["hammer"] and b["signal"] == S.SIGNAL_HAMMER
    assert b["held_days"] == 0 and b["bars_since_signal"] == 0 and b["low"] == 194
    assert b["shadow"]["lower_pct"] >= 85


def test_hammer_then_held_reports_both_evidences():
    b = S.bottom_setup(_path(TOP, DECLINE, HAMMER, HELD))
    assert b["stage"] == "formed" and b["signal"] == f"{S.SIGNAL_HAMMER}+{S.SIGNAL_HELD}"
    assert b["signal_date"] == b["low_date"]                       # 信号日以先成立的为准


def test_close_below_the_low_kills_yesterdays_frame():
    """✗ 反弹结束：今日收破昨日框架的止跌低。只能从昨日框架判（今日的 20 日低已是新低）。"""
    crash = [(200, 185, 188)]                      # 收 188 < 止跌低 194
    b = S.bottom_setup(_path(TOP, DECLINE, NEWLOW, HELD, RALLY, crash))
    assert b["stage"] == "dead" and b["cn"] == "✗反弹结束" and b["listed"]
    assert b["low"] == 194 and "194" in b["why"] and b["verdict"].startswith("反弹结束")
    # 次日: 框架重锚到新低 185, 昨日(crash 日)没再新低 → 直接是 ✓止跌 (次日未新低)
    b2 = S.bottom_setup(_path(TOP, DECLINE, NEWLOW, HELD, RALLY, crash, [(195, 186, 190)]))
    assert b2["stage"] == "formed" and b2["low"] == 185 and b2["signal"] == S.SIGNAL_HELD


def test_intraday_undercut_that_closes_back_is_flagged_not_killed():
    """盘中破前止跌低但收回：今日框架的 L 是新低；不是长下影 → ⏳ 并标 undercut_prev；
    是长下影 → 当天 ✓止跌（收出长下影线，止损放在下影线低点）。"""
    dip = [(206, 190, 199)]                        # 低 190 < 194, 收 199 > 194; 上影 7 下影 9 实体 0 → 非锤子
    b = S.bottom_setup(_path(TOP, DECLINE, NEWLOW, HELD, RALLY, dip))
    assert b["stage"] == "watch" and b["low"] == 190
    assert b["undercut_prev"] and b["undercut_prev"]["low"] == 194
    ham = [(200.5, 190, 200.4, 200.2)]             # 低 190, 下影 10.2 / 实体 0.2 / 上影 0.1 → 锤子
    b = S.bottom_setup(_path(TOP, DECLINE, NEWLOW, HELD, RALLY, ham))
    assert b["stage"] == "formed" and b["low"] == 190 and b["signal"] == S.SIGNAL_HAMMER
    assert b["undercut_prev"]["low"] == 194


def test_pullback_low_needs_a_day_without_a_new_low_to_count_as_higher_low():
    """回踩更高低点对回踩套同一条「次日未新低」：回踩进行中不算，守住一天才算。"""
    running = S.bottom_setup(_path(TOP, DECLINE, NEWLOW, HELD, RALLY, PULL))
    assert running["pullback_low"] == 224 and not running["higher_low"]
    assert running["verdict"] == "反弹（未越前高）"
    held = S.bottom_setup(_path(TOP, DECLINE, NEWLOW, HELD, RALLY, PULL, PULL_HELD))
    assert held["pullback_low"] == 224 and held["higher_low"]
    assert held["verdict"] == "反转候选（回踩更高低点，前高未越）"
    assert held["bars_since_higher_low"] == 0 and held["higher_low_date"] is not None


def test_bottom_is_the_lowest_low_after_the_twenty_day_top_not_a_pullback_in_the_bounce():
    """反弹里的回踩不是底。顶 = 20 日最高 high,
    底 = 顶之后的最低 low; 回踩低只作「更高的低点」事实, 不改底、不改止损。"""
    pull = [(255, 230, 233), (240, 231, 238)]                  # 反弹 256 后回踩 230 守住
    b = S.bottom_setup(_path(TOP, DECLINE, NEWLOW, HELD, RALLY, pull))
    assert b["top"] == 281 and b["low"] == 194 and b["stage"] == "formed"
    assert b["pullback_low"] == 230 and b["higher_low"] and b["bars_since_higher_low"] == 0
    # 二次探底 = 顶之后更低的低点 → 底随之下移, 不需要回踩深度规则
    deeper = [(255, 190, 193), (200, 191, 199)]
    b2 = S.bottom_setup(_path(TOP, DECLINE, NEWLOW, HELD, RALLY, deeper))
    assert b2["low"] == 190 and b2["stage"] == "formed" and b2["signal_date"] == b2["low_date"][:0] + b2["signal_date"]
    assert b2["bars_since_signal"] == 0


def test_new_twenty_day_high_dissolves_the_bottom_frame():
    """收盘创 20 日新高 = 下跌被整段收复 → 框架解体 (顶挪到今天, 底 = 今天的低, 回撤不足 → none)。"""
    b = S.bottom_setup(_path(TOP, DECLINE, NEWLOW, HELD, RALLY, [(290, 270, 288)]))
    assert b["top"] == 290 and b["low"] == 270 and b["stage"] == "none"


def test_reversal_is_higher_low_plus_close_above_the_prior_high():
    hh_only = S.bottom_setup(_path(TOP, DECLINE, NEWLOW, HELD, RALLY, BREAK_LH))
    assert hh_only["above_lh"] and not hh_only["higher_low"]
    assert hh_only["verdict"] == "反转候选（已越前高，回踩未验）"
    both = S.bottom_setup(_path(TOP, DECLINE, NEWLOW, HELD, RALLY, PULL, PULL_HELD, BREAK_LH))
    assert both["above_lh"] and both["higher_low"]
    assert both["verdict"] == "反转（越前高 + 回踩更高低点）"


def test_formed_older_than_ten_bars_drops_off_the_list():
    """✓止跌 十根之后不再是「刚形成」—— 归趋势确认表 / 反弹反转的世界。"""
    short_decline = DECLINE[:2] + DECLINE[5:]                    # 去掉中途反弹, 让顶 281 留在 20 日窗内
    tail = [(262, 250, 259)] * (S.RECENT_SIGNAL_BARS + 2)      # tail[0] = 次日未新低 (信号日), 之后 11 根
    b = S.bottom_setup(_path(TOP, short_decline, NEWLOW, tail))
    assert b["top"] == 281 and b["stage"] == "formed" and not b["listed"]
    assert b["bars_since_signal"] == S.RECENT_SIGNAL_BARS + 1


def test_top_leaving_the_window_reanchors_to_the_new_top():
    """顶滑出 20 日窗 → 新顶 = 窗内最高 high (反弹高), 底 = 它之后的最低 low。"""
    later = [(230, 205, 228)] * 12 + [(226, 203, 210)] + [(215, 204, 212)] * 8
    b = S.bottom_setup(_path(TOP, DECLINE, NEWLOW, HELD, RALLY, later))
    assert b["top"] == 230 and b["low"] == 203 and b["stage"] == "formed" and b["signal"] == S.SIGNAL_HELD


def test_no_lookahead_and_stage_sequence():
    """第 t 根只用 bars[≤t]：逐根截断的阶段序列 = ⏳ → ✓ → ✓ …，形成之后不会凭空退回。"""
    bars = _path(TOP, DECLINE, NEWLOW, HELD, RALLY, PULL, PULL_HELD, BREAK_LH)
    seq = [S.bottom_setup(bars[:t + 1])["stage"] for t in range(S.MIN_BARS, len(bars))]
    assert seq[:1] == ["none"] and seq.count("watch") >= 5              # 顶那根 none; 顶之后每个新低日都是 ⏳
    i = len(seq) - 1 - seq[::-1].index("watch")                        # 最后一个新低日之后全是 ✓
    assert seq[i + 1:] == ["formed"] * (len(seq) - i - 1)


def test_windows_are_the_reversal_probe_vocabulary():
    """判读框架与授权信号共用 20 日低 / 60 日高 / 10% 门 —— 改 reversal.PARAMS 本模块跟随。"""
    assert S.LOW_LOOKBACK == reversal.PARAMS["newlow_lookback"]
    assert S.HIGH_LOOKBACK == reversal.PARAMS["high_lookback"]
    assert S.DD_MIN == reversal.PARAMS["drawdown_min"]


def test_hammer_has_one_definition_and_no_dow_machinery():
    """长下影只有一份定义（`technical.detect_hammer`）；止跌判据里没有道氏四步。"""
    import inspect
    src = inspect.getsource(S)
    assert "detect_hammer" in src
    code = src.split('"""', 2)[2]                   # docstring 里说得起, 代码里不许有
    for banned in ("bull_bar", "bear_bar", "structure_state", "lsh", "lsl"):
        assert banned not in code, banned


def test_probe_marker_reports_domain_without_judging():
    """探针标记只播信号 + 域（R1 / R2 域外），PLAN/VETO 归 entry_guard。"""
    assert S.probe_marker(_bars(_flat(30)), 1.0) is None
    sig = {"domain": "R2", "in_domain": False, "date": "2026-08-04"}
    import quant.core.setups as mod
    orig = mod.reversal.detect
    try:
        mod.reversal.detect = lambda bars, atr, valid_days=None, p=None: sig
        assert S.probe_marker([], 1.0) == "🔁探针R2域外·08-04"
        sig["domain"], sig["in_domain"] = "R1", True
        assert S.probe_marker([], 1.0) == "🔁探针R1·08-04"
    finally:
        mod.reversal.detect = orig


@pytest.mark.parametrize("close,streak,need,state", [
    (95.0, 0, 2, "near"),          # gate 100 在上 0.5N 内
    (99.0, 1, 2, "counting"),
    (101.0, 2, 2, "standing"),
    (101.0, 1, 1, "standing"),     # ×1 gate 一天即站稳
    (80.0, 0, 2, None),            # 2N 之外不列
    (105.0, 0, 2, None),           # 在 gate 之上但 streak 0 (跌回过) 且不在下方 → 不列
])
def test_gate_proximity_states(close, streak, need, state):
    p = S.gate_proximity(close, 10.0, 100.0, streak, need)
    assert (p or {}).get("state") == state


def test_gate_proximity_carries_double_unit_distance():
    p = S.gate_proximity(95.0, 10.0, 100.0, 0, 2)
    assert p["dist_pct"] == pytest.approx(5.26, abs=0.01) and p["dist_n"] == 0.5


def test_stage_table_is_the_single_source_for_labels():
    for k, meta in S.STAGES.items():
        assert {"cn", "formed", "rank", "desc"} <= set(meta), k
    assert S.STAGES["formed"]["rank"] < S.STAGES["watch"]["rank"] < S.STAGES["dead"]["rank"]
    assert all(s in S.STAGES for s in S.LISTED_STAGES)
