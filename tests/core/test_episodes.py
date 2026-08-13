"""core/episodes.py —— 结构破位 episode 的单一真源。

这份测试守两件事:
  1. **语义**（两条腿缺一不可 / cooldown / 无前视 / 边界）
  2. **不许再分叉**——  把 `episodes_of` 从 `research/gate_backtest`
     提到 `core/` 的全部理由就是「回测与 live import 同一份」
     (gate_path_taxonomy 预注册 E)。所以这里额外钉住:
       · `gate_backtest.episodes_of` 与 core 的**是同一个对象**，不是复制品
       · `technical.sma(closes, 60)` 与 `gate_backtest.sma_series` 逐点等价
         —— live 若用 technical.sma 喂它，结果必须和回测一致
"""
from quant.core import technical
from quant.core.episodes import COOLDOWN, LOOKBACK, episodes_of
from quant.research import gate_backtest


# ── 不许分叉 ──────────────────────────────────────────────────────

def test_research_reexports_the_same_object_not_a_copy():
    """`from quant.research.gate_backtest import episodes_of` 必须拿到 core 那一份。

    变异确认: 在 gate_backtest 里重新 `def episodes_of` 会让这条红。
    """
    assert gate_backtest.episodes_of is episodes_of
    assert gate_backtest.LOOKBACK == LOOKBACK == 60
    assert gate_backtest.COOLDOWN == COOLDOWN == 120


def test_sma_series_and_technical_sma_are_pointwise_equal():
    """喂 ma60 的两条路必须给同一串数 —— 否则 live 与回测在同一天分叉。"""
    closes = [100 + (i * 7 % 23) - (i % 5) for i in range(200)]
    a = gate_backtest.sma_series(closes, 60)
    b = technical.sma(closes, 60)
    assert len(a) == len(b) == len(closes)
    for i, (x, y) in enumerate(zip(a, b)):
        if x is None or y is None:
            assert x is None and y is None, i
        else:
            assert abs(x - y) < 1e-9, i


# ── 语义 ─────────────────────────────────────────────────────────

def _ma(closes, n=60):
    return technical.sma(closes, n)


def test_needs_both_legs_new_low_alone_is_not_a_breakdown():
    """创 60 日最低收盘但仍在 MA60 之上 → 不算破位。

    这是「上升趋势里的浅回调」那一桶 (structure_downgrade_backtest 实测
    p10 −5.97% 全场最好)，把它算进破位就是砍掉最好的一桶。
    """
    closes = [100.0 + i for i in range(100)]      # 单调上行, 一直在 MA60 之上
    closes += [closes[-1] - 0.5]                  # 只回一档: 不是 60 日最低
    assert episodes_of(closes, _ma(closes)) == []

    # 造一个"创 60 日最低收盘、但 close 仍 > MA60"的形状: 陡升后小幅回落
    rise = [100.0 + i * 5 for i in range(80)]
    dip = [rise[-1] - 1.0]                        # 高于 MA60, 但不是新低
    closes2 = rise + dip
    ma = _ma(closes2)
    i = len(closes2) - 1
    assert closes2[i] > ma[i]                     # 前提: 还在 MA60 之上
    assert episodes_of(closes2, ma) == []


def test_needs_both_legs_below_ma60_alone_is_not_a_breakdown():
    """跌破 MA60 但没创 60 日最低收盘 → 不算破位。"""
    closes = [100.0] * 60 + [90.0] + [95.0] * 40   # 90 是最低, 后面 95 在 MA60 下但非新低
    ma = _ma(closes)
    eps = episodes_of(closes, ma)
    assert eps, "90 那天本身应当是 episode"
    for i in eps:
        assert closes[i] <= min(closes[i - LOOKBACK:i])


def test_new_low_leg_is_tight_not_approximate():
    """「创 60 日最低收盘」是**严格**的: 比前低高一分就不算, 高 2% 更不算。

    ⚠️ 立此条的原因: 第一版测试全绿, 但把判据放宽成 `min(...) * 1.05`
    (= 允许"离前低 5% 以内"都算破位) **一条都不红**—— 那时的测试只钉了
    "已触发的确实是新低", 没钉住"没到新低的不许触发"。
    这里直接喂 ma60 数组把 MA60 腿常真, 单独隔离出最低收盘腿。
    """
    always_below = [None] * LOOKBACK + [10 **6]      # MA60 腿恒真, 只考最低收盘腿

    # 窗口 = 前 60 根, 最低 80.0
    at_low = [100.0] * (LOOKBACK - 1) + [80.0] + [80.0]
    assert episodes_of(at_low, always_below) == [LOOKBACK], "平前低 = 新低, 该触发"

    one_tick_above = [100.0] * (LOOKBACK - 1) + [80.0] + [80.01]
    assert episodes_of(one_tick_above, always_below) == [], "高一分就不是新低"

    two_pct_above = [100.0] * (LOOKBACK - 1) + [80.0] + [81.6]
    assert episodes_of(two_pct_above, always_below) == [], "离前低 2% 不算破位"

    five_pct_above = [100.0] * (LOOKBACK - 1) + [80.0] + [84.0]
    assert episodes_of(five_pct_above, always_below) == [], "离前低 5% 更不算"


def test_ma60_leg_is_tight_not_approximate():
    """MA60 腿同样严格: close == MA60 不算, 高一分不算; 低一分才算。"""
    new_low = [100.0] * LOOKBACK + [50.0]            # 最低收盘腿恒真
    assert episodes_of(new_low, [None] * LOOKBACK + [50.0]) == [], "close == MA60 不算"
    assert episodes_of(new_low, [None] * LOOKBACK + [49.99]) == [], "close > MA60 不算"
    assert episodes_of(new_low, [None] * LOOKBACK + [50.01]) == [LOOKBACK], "close < MA60 才算"


def test_fires_when_both_legs_hold():
    closes = [100.0] * 80 + [70.0]
    ma = _ma(closes)
    assert episodes_of(closes, ma) == [80]


def test_cooldown_suppresses_the_second_hit():
    """cooldown 内的第二次破位不重复计数; 满 COOLDOWN 之后可以再计。"""
    closes = [100.0] * 80 + [70.0] + [69.0] * 5 + [60.0]     # 第二次在 6 日后
    assert episodes_of(closes, _ma(closes)) == [80]

    far = [100.0] * 80 + [70.0] + [69.0] * (COOLDOWN - 1) + [50.0]
    got = episodes_of(far, _ma(far))
    assert got[0] == 80
    assert got[-1] == 80 + COOLDOWN
    assert len(got) == 2


def test_no_lookahead_prefix_stability():
    """把序列往后延长, 已产出的 episode 下标不许变 —— 无前视的操作性定义。"""
    base = [100.0] * 80 + [70.0] + [72.0, 75.0, 71.0] * 30
    ma = _ma(base)
    prefix = episodes_of(base[:150], _ma(base[:150]))
    full = episodes_of(base, ma)
    assert full[:len(prefix)] == prefix


def test_ma60_none_never_fires():
    """MA60 不可算的那段 (前 59 根) 永远不产 episode。"""
    closes = [100.0] * 59 + [1.0]
    ma = _ma(closes)
    assert ma[59] is not None and ma[58] is None
    for i in episodes_of(closes, ma):
        assert ma[i] is not None


def test_starts_at_lookback_not_earlier():
    closes = [100.0] * 200
    closes[10] = 1.0                       # 早于 LOOKBACK, 且窗口不足
    assert all(i >= LOOKBACK for i in episodes_of(closes, _ma(closes)))


def test_strict_comparison_ties_do_not_fire():
    """`closes[i] > min(...)` 是严格号: 平最低收盘算破位, 高一分不算。"""
    flat = [100.0] * 80 + [100.0]
    ma = _ma(flat)
    # 全平: close == min 且 close == MA60 → `closes[i] >= ma60[i]` 挡掉
    assert episodes_of(flat, ma) == []

    tie = [100.0] * 40 + [80.0] + [100.0] * 39 + [80.0]   # 第二个 80 与前低持平
    ma2 = _ma(tie)
    assert 80 in episodes_of(tie, ma2)
