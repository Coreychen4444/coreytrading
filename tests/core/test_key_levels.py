"""⭐ 箱顶（`core/key_levels`）—— 标注极其关键的位置：前期震荡行情下多次打到的阻力位。

钉六件事：箱顶 = 近 20 根最高价所在的簇且近 60 根被测 ≥2 次（测 1 次 / 箱体中间的簇都不算）/
⭐突破 = 收盘 ×2 且无前视 / 突破后收破 线−0.5N = 失败（之后站回来也不再是「完好」）/
位置判读的优先级（新箱子覆盖旧突破）/ 零新旋钮（窗口全是既有常量的别名）/ 零授权 import。
"""
import ast
import datetime as dt
from pathlib import Path

from quant.core import bottom_evidence as BE
from quant.core import key_levels as K
from quant.core import setups as S


def _bar(i, o, h, l, c, v=1000):
    t = (dt.date(2025, 1, 1) + dt.timedelta(days=i)).isoformat()
    return {"time": f"{t} 00:00:00", "open": o, "high": h, "low": l, "close": c,
            "volume": v, "turnover": c * v}


def _series(peaks, base=100.0, n=120):
    """平底 base 上按 {bar 序号: 高点} 插 5 根分形尖峰 (两侧各 2 根更低 = swing high)。
    每根真实波幅 ≈2 ⇒ ATR14 ≈2、聚类容差 ≈0.7、桶宽 ≈0.5。"""
    bars = [_bar(i, base, base + 1, base - 1, base) for i in range(n)]
    for i, hi in peaks.items():
        bars[i] = _bar(i, base, hi, base - 1, base)
    return bars


def _run(bars, closes):
    """在序列尾部接若干根收盘 (高 = 收 + 0.5, 低 = 收 − 0.5)。"""
    n = len(bars)
    return bars + [_bar(n + k, c, c + 0.5, c - 0.5, c) for k, c in enumerate(closes)]


BOX = {70: 110.0, 90: 110.3}          # 近 60 根内两次被打到 110 一带 → 箱顶 110.3 ×2


def test_box_top_is_the_multi_tested_ceiling():
    top = K.box_top(_series(BOX))
    assert top and top["level"] == 110.3 and top["tests"] == 2 and top["kinds"] == "high"
    assert top["test_dates"] == ["2025-03-12", "2025-04-01"]
    assert K.evidence_tag(top) == "测2" and "/" not in K.evidence_tag(top)   # '/' 是备注并入分隔符


def test_one_recent_test_is_not_a_key_level():
    """簇 ×2 但近 60 根只被测 1 次 (另一次在窗外) → 不是 ⭐。"""
    assert K.box_top(_series({30: 110.0, 90: 110.3})) is None


def test_a_level_below_a_fresher_high_is_inside_the_range_not_the_top():
    """近 20 根有更高的高点 → 110.3 在箱体中间 (定价里最差的一格), 不是箱顶;
    115 只触碰一次也不是 → 此刻没有 ⭐箱顶。"""
    assert K.box_top(_series({**BOX, 110: 115.0})) is None


def test_breakout_needs_two_closes_and_sees_no_future():
    bars = _run(_series(BOX), [108.0, 111.5])            # 只站上一根
    assert K.key_breakouts(bars) == []
    bars = _run(_series(BOX), [108.0, 111.5, 112.0])     # 第二根仍在线上 = 确认
    ev = K.key_breakouts(bars)
    assert [(e["level"], e["tests"], e["date"]) for e in ev] == [(110.3, 2, bars[-1]["time"][:10])]
    # 无前视: 砍掉确认之后的 K 线重放, 事件原样; 确认日之前任何一天都看不到它
    more = _run(bars, [113.0, 114.0])
    assert [e["date"] for e in K.key_breakouts(more)] == [e["date"] for e in ev]
    assert K.key_breakouts(bars[:-1]) == []


def test_second_close_back_under_the_line_is_not_a_breakout():
    """×2 = 两根都站上; 第二根收回线下 (仍高于前一根) = 没确认 —— 不能只靠「后面还有一根」。"""
    assert K.key_breakouts(_run(_series(BOX), [108.0, 111.5, 109.0])) == []


def test_a_support_pivot_in_the_cluster_is_not_a_test_from_below():
    """「多次打到」数的是分形**高点**: 簇里一个 pivot 低 (在 110 一带的平台上回踩) + 一个 pivot 高
    = 从下方只被打过 1 次 → 不是 ⭐ (定价口径同: recent_tests 只数 high)。"""
    bars = _series({90: 110.3})
    for i in range(60, 81):                                   # 112 平台, 第 70 根回踩到 110.0 = pivot 低
        bars[i] = _bar(i, 112.0, 113.0, 110.0 if i == 70 else 111.0, 112.0)
    top = K.box_top(bars)
    assert top is None, top


def test_breakout_that_was_not_a_key_level_is_ignored():
    bars = _run(_series({30: 110.0, 90: 110.3}), [108.0, 111.5, 112.0])   # 近 60 根只测 1 次
    assert K.key_breakouts(bars) == []


def test_failed_breakout_stays_failed_even_after_reclaiming():
    base = _run(_series(BOX), [108.0, 111.5, 112.0])
    ok = K.last_breakout(_run(base, [111.0, 112.5]))
    assert ok["intact"] and ok["held"] and ok["level"] == 110.3
    assert ok["floor"] < 110.3                                   # 线 − 0.5×ATR20
    bad = K.last_breakout(_run(base, [105.0, 112.5]))            # 收破地板后又站回来
    assert not bad["intact"] and bad["held"]
    assert bad["first_date"] < bad["date"]


def test_position_priority():
    brk = {"level": 100.0, "intact": True, "held": True, "first_date": "2026-09-01"}
    old_top = {"level": 120.0, "test_dates": ["2026-07-01", "2026-08-01"]}
    new_top = {"level": 120.0, "test_dates": ["2026-09-05", "2026-09-15"]}
    assert K.position(110.0, 3.0, None, brk)["state"] == "broken"
    assert K.position(110.0, 3.0, old_top, brk)["state"] == "broken"        # 头顶的是突破前就在的老压力
    assert K.position(110.0, 3.0, new_top, brk)["state"] == "mid"           # 突破后又被压成新箱子
    assert K.position(118.0, 3.0, new_top, brk)["state"] == "near_top"      # ≤1N
    assert K.position(110.0, 3.0, None, {**brk, "intact": False})["state"] == "failed"
    assert K.position(95.0, 3.0, None, {**brk, "held": False})["state"] == "broken"   # 地板之内的回踩
    assert K.position(110.0, 3.0, new_top, None, at_bottom=True)["state"] == "bottom"
    assert K.position(110.0, 3.0, None, {**brk, "intact": False}, at_bottom=True)["state"] == "bottom"
    assert K.position(110.0, 3.0, None, None)["state"] == "none"
    assert {K.position(110.0, 3.0, None, None)["cn"]} <= {v["cn"] for v in K.POSITIONS.values()}


def test_no_new_knobs_and_module_grants_nothing():
    """窗口全是既有常量的别名 (零新旋钮); 判读件不许 import 授权 / sizing / 转移模块。"""
    assert K.RANGE_BARS == S.LOW_LOOKBACK and K.TEST_BARS == S.LH_LOOKBACK
    assert K.MIN_TESTS is BE.MIN_TOUCHES
    assert K.CLUSTER_TOL_ATR14 is BE.CLUSTER_TOL_ATR14 and K.PIVOT_LOOKBACK is BE.PIVOT_LOOKBACK
    tree = ast.parse(Path(K.__file__).read_text())
    mods = {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert mods <= {"__future__", "typing", "", "bottom_evidence", "setups"}, mods


def test_too_little_history_gives_nothing():
    short = _series({10: 110.0, 30: 110.3}, n=40)          # 形状对, 但 40 根 < 簇的最少历史
    assert K.box_top(short) is None and K.key_breakouts(short) == [] and K.last_breakout(short) is None
