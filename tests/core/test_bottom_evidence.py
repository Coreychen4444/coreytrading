"""底部确认判读件（`core/bottom_evidence`）—— 底部确认概率的零授权判读层。

钉四件事：随机游走基准的数学（两个级数一致 + 对日收盘监控的蒙特卡洛）/ 锚收敛只用止跌低之前的 bar /
基准新低索引的暖机与语义 / 判读件不 import 授权模块。
"""
import datetime as dt
import math
import random
from pathlib import Path

import pytest

from quant.core import bottom_evidence as BE
from quant.core import technical


def _bar(t, o, h, l, c, v=1000):
    return {"time": f"{t} 00:00:00", "open": o, "high": h, "low": l, "close": c, "volume": v, "turnover": c * v}


def _day(i):
    return (dt.date(2025, 1, 1) + dt.timedelta(days=i)).isoformat()


# ── 随机游走基准 ────────────────────────────────────────────────

def test_rw_boundaries_and_bad_inputs():
    assert BE.confirm_prob_rw(111, 95, 110, 0.02) == 1.0          # 已收在前高之上 = 已确认
    assert BE.confirm_prob_rw(94, 95, 110, 0.02) == 0.0           # 已收破止跌低
    for bad in ((100, 95, 94, 0.02), (100, 95, 110, 0.0), (100, None, 110, 0.02), (100, 95, 110, None)):
        assert BE.confirm_prob_rw(*bad) is None                   # 前高不在底之上 / σ 非正 / 缺
    assert BE.confirm_prob_rw(100, 95, 110, 0.02, horizon=0) is None


def test_rw_is_monotone_in_position_and_bounded():
    ps = [BE.confirm_prob_rw(c, 95, 110, 0.025) for c in (95.5, 98, 101, 104, 107, 109.5)]
    assert all(0.0 <= p <= 1.0 for p in ps)
    assert ps == sorted(ps) and ps[0] < 0.1 < 0.5 < ps[-1]         # 离底越近越低、离前高越近越高
    # 时间越长, 两边都更可能被触到: 贴近前高的一侧概率随 horizon 上升
    assert BE.confirm_prob_rw(107, 95, 110, 0.02, 5) < BE.confirm_prob_rw(107, 95, 110, 0.02, 40)


def test_rw_long_horizon_tends_to_the_gamblers_ruin_ratio():
    """无限时间 = 赌徒破产: P → (ln C − a)/(b − a)（含离散监控外移的两条线）。"""
    s = 0.02
    a, b = math.log(95) - BE.BGK_BETA * s, math.log(110) + BE.BGK_BETA * s
    ruin = (math.log(100) - a) / (b - a)
    assert BE.confirm_prob_rw(100, 95, 110, s, horizon=100000) == pytest.approx(ruin, abs=1e-9)


@pytest.mark.parametrize("d,w,var", [(0.05, 0.20, 0.004), (0.10, 0.25, 0.006), (0.02, 0.08, 0.0007)])
def test_fourier_and_image_series_agree(d, w, var):
    lam = math.pi ** 2 * var / (2 * w * w)
    assert 0.1 < lam < 1.2                                         # 两个级数都收敛良好的区间
    assert BE._p_fourier(d, w, lam) == pytest.approx(BE._p_image(d, w, var), abs=1e-9)


@pytest.mark.parametrize("close,low,lh,sigma,h", [(100, 95, 110, 0.03, 20), (100, 98, 103, 0.02, 20),
                                                 (100, 90, 104, 0.025, 10)])
def test_rw_matches_daily_close_monte_carlo(close, low, lh, sigma, h):
    """对「日收盘监控、先收盘 > 前高 且此前没有收盘 < 止跌低」直接模拟: 离散修正后误差 ≤ 2pp。"""
    rnd = random.Random(20260914)
    n_paths, hit = 20000, 0
    lo_, hi_, x0 = math.log(low), math.log(lh), math.log(close)
    for _ in range(n_paths):
        x = x0
        for _ in range(h):
            x += rnd.gauss(0.0, sigma)
            if x < lo_:
                break
            if x > hi_:
                hit += 1
                break
    assert BE.confirm_prob_rw(close, low, lh, sigma, h) == pytest.approx(hit / n_paths, abs=0.02)


def test_daily_sigma_is_hv20_over_sqrt252():
    rnd = random.Random(3)
    px, bars = 100.0, []
    for i in range(80):
        px *= 1 + rnd.gauss(0, 0.02)
        bars.append(_bar(_day(i), px, px * 1.01, px * 0.99, px))
    hv = technical.hv(bars, 20)
    assert BE.daily_sigma(bars) == pytest.approx(hv[-1] / math.sqrt(252))
    assert BE.daily_sigma(bars, 40) == pytest.approx(hv[40] / math.sqrt(252))
    assert BE.daily_sigma(bars[:10]) is None


# ── 锚收敛 ──────────────────────────────────────────────────────

def _cluster_low_bars():
    """垫子 300 根 (价 100) → 两次 90 的分形低 (簇 touches 2) → 顶 121 → 跌到 90.2 = 止跌低 (贴簇) → 次日未新低。"""
    rows = [_bar(_day(i), 100, 101, 99, 100) for i in range(300)]
    path = [(98, 99, 90, 92), (96, 97, 94, 95), (99, 100, 97, 98), (100, 101, 98, 99), (100, 101, 99, 100),
            (97, 98, 92, 93), (93, 94, 90, 91), (94, 95, 92, 94), (97, 98, 95, 96), (99, 100, 97, 98),
            (100, 106, 99, 105), (106, 121, 104, 120), (110, 112, 100, 102), (104, 105, 100, 101),
            (100, 101, 96, 97), (96, 97, 90.2, 92), (93, 96, 92, 95)]
    for k, (o, h, l, c) in enumerate(path):
        rows.append(_bar(_day(300 + k), o, h, l, c))
    return rows


def test_anchor_confluence_hits_the_pivot_cluster_and_names_it():
    rows = _cluster_low_bars()
    i_low = len(rows) - 2
    a = BE.anchor_confluence(rows, i_low, atr20=2.0, atr14=2.0)
    assert a["low"] == 90.2 and a["pivot"] and a["pivot_touches"] >= 2
    assert a["tol"] == max(BE.ANCHOR_TOL_ATR * 2.0, a["bucket"])
    assert a["dims"] == int(a["pivot"]) + int(a["profile"]) + int(a["ma"])
    assert a["cn"].startswith(f"锚{a['dims']}/3") and "簇" in a["cn"]


def test_anchor_confluence_never_reads_bars_after_the_low():
    rows = _cluster_low_bars()
    i_low = len(rows) - 2
    a = BE.anchor_confluence(rows, i_low, 2.0, 2.0)
    wild = rows[:i_low + 1] + [_bar("2099-01-01", 1, 1000, 0.5, 500, v=10**9)] * 30
    b = BE.anchor_confluence(wild, i_low, 2.0, 2.0)
    assert (a["pivot"], a["profile"], a["ma"], a["dims"]) == (b["pivot"], b["profile"], b["ma"], b["dims"])


def test_anchor_confluence_needs_both_atrs_and_a_valid_index():
    rows = _cluster_low_bars()
    assert BE.anchor_confluence(rows, len(rows) - 2, None, 2.0) is None
    assert BE.anchor_confluence(rows, len(rows) - 2, 2.0, 0) is None
    assert BE.anchor_confluence(rows, None, 2.0, 2.0) is None
    assert BE.anchor_confluence(rows, len(rows), 2.0, 2.0) is None


def test_anchor_constants_are_the_preregistered_values():
    """预注册锁定值 —— 改任一项 = 重新预注册。"""
    assert (BE.ANCHOR_TOL_ATR, BE.CLUSTER_TOL_ATR14, BE.PIVOT_LOOKBACK, BE.PROFILE_BARS, BE.HVN_TOP_N,
            BE.MIN_TOUCHES) == (0.25, 0.35, 250, 60, 5, 2)
    assert BE.CONFIRM_HORIZON == 20


# ── 基准新低 ────────────────────────────────────────────────────

def test_bench_low_index_marks_twenty_day_new_lows_after_warmup():
    bars = [_bar(_day(i), 10, 11, 10 - i * 0.01, 10) for i in range(25)]      # 逐日新低
    idx = BE.bench_low_index(bars)
    assert _day(0) not in idx and _day(18) not in idx and idx[_day(19)] is True   # 前 19 根暖机
    bars[-1]["low"] = 12.0
    assert BE.bench_low_index(bars)[_day(24)] is False
    assert BE.bench_new_low(idx, _day(24)) is True and BE.bench_new_low(idx, "2099-01-01") is None
    assert BE.bench_new_low(None, _day(24)) is None


# ── 边界 ────────────────────────────────────────────────────────

def test_evidence_module_is_judgment_only():
    import ast
    src = Path(BE.__file__).read_text()
    names = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.ImportFrom):
            names.add((node.module or ""))
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
    assert not {n for n in names if any(f in n for f in ("sizing", "entry_guard", "exit_guard", "io_json", "paths"))}
    calls = {n.func.id for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "open" not in calls and "Path" not in calls                    # 不读文件
