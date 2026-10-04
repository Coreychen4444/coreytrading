"""关键位之间的真空带（`core/vacuum`）—— 两个关键位之间的成交稀薄区，上下行都标，只做展示零授权。

钉六件事：两个成交密集区之间的快速穿越段被认成一条带 / 现价在哪一侧决定 ↑↓ 与近端边线 /
顶着窗口最高价的带不被掐掉且按窗高截断 / 窄于 1N 的稀薄带不出 / 数据不足不编数 /
判读件不 import 授权模块、窗口与宽度门槛全是既有常量别名。
"""
import ast
import datetime as dt
from pathlib import Path

from quant.core import bottom_evidence as BE
from quant.core import setups, technical
from quant.core import vacuum as V


def _bar(i, lo, hi, c, v=1000):
    t = (dt.date(2025, 1, 1) + dt.timedelta(days=i)).isoformat()
    return {"time": f"{t} 00:00:00", "open": c, "high": hi, "low": lo, "close": c,
            "volume": v, "turnover": c * v}


def _two_shelves(end_at_top: bool):
    """100 一带横 80 根 → 5 根走完 100→130 → 130 一带横 80 根 → (可选) 5 根走回 100 再横 20 根。
    每根波幅 2 ⇒ ATR14 ≈2、桶宽 ≈0.5；中间那段每根波幅 6、只走过一次 = 稀薄。"""
    bars, i = [], 0
    for _ in range(80):
        bars.append(_bar(i, 99, 101, 100)); i += 1
    for k in range(5):
        bars.append(_bar(i, 100 + 6 * k, 106 + 6 * k, 105 + 6 * k)); i += 1
    for _ in range(80):
        bars.append(_bar(i, 129, 131, 130)); i += 1
    if not end_at_top:
        for k in range(5):
            bars.append(_bar(i, 124 - 6 * k, 130 - 6 * k, 125 - 6 * k)); i += 1
        for _ in range(20):
            bars.append(_bar(i, 99, 101, 100)); i += 1
    return bars


def test_fast_traverse_between_two_shelves_is_one_band_above_spot():
    r = V.scan(_two_shelves(end_at_top=False))
    assert r["spot"] == 100 and r["inside"] is None and r["down"] is None
    up = r["up"]
    assert up is not None and [b for b in r["bands"]] == [up]      # 整段只有这一条
    assert 100.5 <= up["lo"] <= 102 and 128 <= up["hi"] <= 129.5    # 两个平台之间
    assert up["side"] == "above" and up["edge"] is None
    assert up["dist_pct"] > 0 and up["width_n"] >= 10
    assert up["share_pct"] < 10                                     # 185 根里只有 10 根走过
    assert up["days_eq"] == round(up["share_pct"] / 100 * r["window"], 1) or abs(
        up["days_eq"] - up["share_pct"] / 100 * r["window"]) < 0.2
    # 近端边线 = 下沿: 下方平台每根的高点都够到它、收盘都没站上 → 被拒, 最近一次是最后一根
    assert up["tests"] >= 20 and up["last_test"] == r["asof"]


def test_spot_on_the_upper_shelf_flips_the_band_below():
    r = V.scan(_two_shelves(end_at_top=True))
    assert r["spot"] == 130 and r["up"] is None and r["inside"] is None
    dn = r["down"]
    assert dn["side"] == "below" and dn["dist_pct"] < 0
    assert 100.5 <= dn["lo"] <= 102 and 128 <= dn["hi"] <= 129.5
    # 近端边线 = 上沿: 上方平台每根低点探到它、收盘没跌破 → 承接
    assert dn["tests"] >= 20 and dn["last_test"] == r["asof"]


def test_band_touching_the_window_high_is_kept_and_clipped():
    """几根 K 线冲上去、跳空回来 —— 带顶着窗口最高价。
    `find_lvn` 缺省会把它当边缘带掐掉, 这里要的恰恰是它。"""
    bars = [_bar(i, 99, 101, 100) for i in range(100)]
    for k, (lo, hi) in enumerate([(100, 108), (108, 118), (118, 127.3)]):
        bars.append(_bar(100 + k, lo, hi, hi - 1))
    bars += [_bar(103 + i, 99, 101, 100) for i in range(40)]         # 跳空回到平台
    r = V.scan(bars)
    up = r["up"]
    assert up is not None and up["edge"] == "window_high"
    assert up["hi"] == 127.3                                         # 按窗高截断, 不是桶网格的上沿
    assert up["bars_through"] == 3 and up["last_through"] == "2025-04-13"   # 只有冲上去的那三根, 最后一根是第 102 根
    assert r["down"] is None


def test_thin_band_narrower_than_one_atr_is_not_reported():
    """两个平台只隔 1 个点 (0.5N): 中间确实稀薄, 但一根 K 线就走完 —— 不是「穿越」。"""
    bars = [_bar(i, 99, 101, 100) for i in range(80)]
    bars += [_bar(80 + i, 102, 104, 103) for i in range(80)]
    r = V.scan(bars)
    assert r is not None and r["bands"] == []
    assert r["up"] is None and r["down"] is None and r["inside"] is None


def test_spot_inside_a_band_reports_both_edges_and_no_edge_tests():
    bars = _two_shelves(end_at_top=True)
    bars += [_bar(len(bars) + k, 124 - 4 * k, 128 - 4 * k, 125 - 4 * k) for k in range(3)]   # 掉进带内, 收 117
    r = V.scan(bars)
    b = r["inside"]
    assert b is not None and b["side"] == "inside" and b["dist_pct"] == 0.0
    assert b["up_pct"] > 0 > b["down_pct"]
    assert b["tests"] is None and b["last_test"] is None


def test_not_enough_history_returns_none_instead_of_a_guess():
    assert V.scan([_bar(i, 99, 101, 100) for i in range(V._MIN_BARS - 1)]) is None
    assert V.scan([]) is None


def test_only_the_last_window_bars_are_measured():
    """窗口之外的历史不进分布: 250 根之前的那次快速穿越看不见。"""
    old = _two_shelves(end_at_top=False)[:170]                       # 含 100→130 那一段, 停在 130
    flat = [_bar(1000 + i, 129, 131, 130) for i in range(V.WINDOW)]
    assert V.scan(old + flat)["bands"] == []


def test_judgment_only_no_authorization_imports_and_no_new_knobs():
    src = Path(V.__file__).read_text()
    names = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
    banned = {"sizing", "entry_guard", "exit_guard", "regime_transition", "book", "structure"}
    assert not (banned & {n.split(".")[-1] for n in names})
    # 窗口 / 宽度门槛 / 簇尺 = 既有常量的别名; 稀薄判据 = find_lvn 的缺省 (不在本模块另存一份)
    assert V.WINDOW == BE.PIVOT_LOOKBACK
    assert V.MIN_WIDTH_ATR == setups.NEAR_GATE_ATR
    assert "poc_frac" not in src and technical.LVN_POC_FRAC == 0.25


# ── 筹码密集区 dense(): 标注关键阻力与支撑区域 ──

def test_dense_zones_are_the_two_shelves_and_the_spot_sits_in_one():
    r = V.dense(_two_shelves(end_at_top=False))
    assert [round(z["peak"]) for z in r["zones"]] == [100, 130]
    low, high = r["zones"]
    assert r["inside"] is low and r["up"] is high and r["down"] is None
    assert low["lo"] < 100 < low["hi"] and high["lo"] < 130 < high["hi"]
    # 分布按成交额 (价 × 量) 记: 上方平台 80 根 × 130 > 下方 100 根 × 100 → POC 在上方
    assert high["peak_pct_poc"] == 100 and 90 <= low["peak_pct_poc"] < 100
    assert high["side"] == "above" and high["dist_pct"] > 0
    # 近端边线 = 上方区的下沿: 回到下方平台之后没再碰过它 → 被拒次数来自 130 平台之前那段
    assert high["tests"] is not None and high["last_test"] is not None


def test_dense_and_vacuum_never_overlap():
    """同一个点不能既是真空又是密集区: 区的延伸碰到稀薄桶就停 (> 25%×POC)。"""
    bars = _two_shelves(end_at_top=False)
    zones, bands = V.dense(bars)["zones"], V.scan(bars)["bands"]
    assert zones and bands
    for z in zones:
        for b in bands:
            assert z["hi"] <= b["lo"] + 1e-6 or z["lo"] >= b["hi"] - 1e-6, (z, b)


def test_adjacent_peaks_split_at_the_valley_floor():
    """两个峰之间的谷不够稀薄 (不是真空) —— 仍是两个区, 按谷底分开, 互不越界。"""
    bars, i = [], 0
    for _ in range(80):
        bars.append(_bar(i, 99, 101, 100)); i += 1
    for _ in range(25):
        bars.append(_bar(i, 101, 105, 103)); i += 1                  # 谷: 有成交但比两边薄
    for _ in range(80):
        bars.append(_bar(i, 105, 107, 106)); i += 1
    r = V.dense(bars)
    peaks = [z["peak"] for z in r["zones"]]
    assert len(peaks) == 2 and abs(peaks[0] - 100) <= 1 and abs(peaks[1] - 106) <= 1
    a, b = r["zones"]
    assert a["hi"] <= b["lo"] + 1e-6
    assert r["inside"] is b and r["down"] is a and a["side"] == "below" and a["tests"] is not None


def test_dense_not_enough_history_returns_none():
    assert V.dense([_bar(i, 99, 101, 100) for i in range(V._MIN_BARS - 1)]) is None


def test_dense_half_is_the_fwhm_definition():
    assert V.DENSE_HALF == 0.5


# ── 换手衰减: 上方旧筹码可能早已换手 ──

def test_chip_decay_weights_are_turnover_times_survival_to_today():
    seg = [_bar(i, 99, 101, 100, v=v) for i, v in enumerate([100, 200, 100])]
    cd = V.chip_decay(seg, float_shares=1000)                         # r = 0.1 / 0.2 / 0.1
    assert [round(w, 4) for w in cd["weights"]] == [round(0.1 * 0.8 * 0.9, 4), round(0.2 * 0.9, 4), 0.1]
    assert cd["turnover_pct"] == round((0.1 + 0.2 + 0.1) / 3 * 100, 2)
    assert cd["half_life"] is None                                    # 0.9 × 0.8 × 0.9 = 0.648 > 0.5
    assert V.chip_decay(seg, float_shares=300)["half_life"] == 2      # r = 1/3, 2/3, 1/3 → 两根就 < 0.5
    assert V.chip_decay(seg, None) is None and V.chip_decay(seg, 0) is None


def test_high_turnover_moves_the_old_upper_shelf_out_but_not_the_zones():
    """旧平台在上、新平台在下: 低换手几乎不动占比, 高换手把上方旧筹码迁走 ——
    区在哪儿不变 (识别只用等权分布), 变的只是占比读数。"""
    bars, i = [], 0
    for _ in range(80):
        bars.append(_bar(i, 129, 131, 130)); i += 1                  # 旧: 130 平台
    for k in range(5):
        bars.append(_bar(i, 124 - 6 * k, 130 - 6 * k, 125 - 6 * k)); i += 1
    for _ in range(80):
        bars.append(_bar(i, 99, 101, 100)); i += 1                    # 新: 100 平台, 现价在这
    eq = V.dense(bars)
    slow = V.dense(bars, float_shares=1_000_000)                      # 日换手 0.1%
    fast = V.dense(bars, float_shares=10_000)                         # 日换手 10%
    assert [z["peak"] for z in eq["zones"]] == [z["peak"] for z in fast["zones"]]
    assert eq["chips"] is None and all(z["decay_pct"] is None for z in eq["zones"])
    up_eq, up_slow, up_fast = eq["up"], slow["up"], fast["up"]
    assert abs(up_slow["decay_pct"] - up_eq["share_pct"]) < 10          # 低换手: 两数接近 (单位差: 股数 vs 成交额)
    assert up_fast["decay_pct"] < 1 < up_eq["share_pct"]               # 高换手: 上方旧筹码基本迁走
    assert fast["chips"]["half_life"] == 7 and slow["chips"]["half_life"] is None
