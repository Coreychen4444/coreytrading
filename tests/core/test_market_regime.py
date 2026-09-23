"""大盘状态机 C1（`core/market_regime`）——
每条判别式一个会失败的用例：ER 的两端、分位只看过去、状态映射、无前视、频率表。
"""
import datetime as dt

from quant.core import market_regime as M


def _bars(closes, start="2020-01-01"):
    d0 = dt.date.fromisoformat(start)
    out = []
    for i, c in enumerate(closes):
        out.append({"time": f"{(d0 + dt.timedelta(days=i)).isoformat()} 00:00:00",
                    "open": c, "high": c + 0.5, "low": c - 0.5, "close": c, "volume": 1_000_000})
    return out


def test_er_straight_line_is_one_and_zigzag_is_zero():
    line = [100 + i for i in range(30)]
    assert M.er_series(line)[25] == 1.0
    zig = [100 + (i % 2) for i in range(30)]           # 20 根里来回 20 次, 净位移 0
    assert M.er_series(zig)[25] == 0.0


def test_er_undefined_before_window_and_on_flat_path():
    er = M.er_series([100.0] * 30)
    assert er[:M.ER_WINDOW] == [None] * M.ER_WINDOW
    assert er[25] is None                               # 零波动 = 无定义, 不是 0 也不是 1


def test_pctl_only_uses_prior_values():
    vals = [float(i) for i in range(M.PCTL_WINDOW + 5)]
    p = M.pctl_series(vals)
    assert p[M.PCTL_WINDOW - 1] is None                 # 前序不足 252 个
    assert p[M.PCTL_WINDOW] == 1.0                      # 严格大于之前全部 252 个
    # 追加未来值不改变过去的分位 (无前视)
    p2 = M.pctl_series(vals + [-1.0, 1e9])
    assert p2[:len(p)] == p


def test_band_edges():
    assert M.band_of(None) is None
    assert M.band_of(0.0) == "range"
    assert M.band_of(M.BAND_LOW - 1e-9) == "range"
    assert M.band_of(M.BAND_LOW) == "mixed"
    assert M.band_of(M.BAND_HIGH - 1e-9) == "mixed"
    assert M.band_of(M.BAND_HIGH) == "trend"


def test_classify_mapping():
    assert M.classify("range", "up", 0) == M.RANGE          # 震荡档与方向无关
    assert M.classify("range", "down", 2) == M.RANGE
    assert M.classify("trend", "up", 0) == M.TREND_UP
    assert M.classify("trend", "down", 0) == M.TREND_DOWN
    assert M.classify("trend", "up", 1) == M.TRANSITION     # 单边档但道氏在观察期
    assert M.classify("mixed", "up", 0) == M.TRANSITION
    assert M.classify(None, "up", 0) == M.UNKNOWN
    assert M.classify("trend", None, 0) == M.UNKNOWN


def _wave(n=400):
    # 前半段慢涨 (高 ER), 后半段窄幅来回 (低 ER)
    out, c = [], 100.0
    for i in range(n):
        c += 1.0 if i < n // 2 else (1.5 if i % 2 else -1.5)
        out.append(c)
    return out


def test_timeline_length_and_no_lookahead():
    bars = _bars(_wave())
    tl = M.timeline(bars)
    assert len(tl) == len(bars)
    t = 330
    prefix = M.timeline(bars[:t + 1])
    assert prefix[t] == tl[t]


def test_state_reads_range_after_zigzag():
    card = M.state(_bars(_wave()))
    assert card["band"] == "range"
    assert card["state"] == M.RANGE
    assert card["er_thresholds"] is not None
    lines = M.describe(card)
    assert lines[0].startswith("震荡")


def test_transition_rates_counts_forward_states():
    states = [M.RANGE, M.RANGE, M.TREND_UP, M.RANGE, M.TRANSITION]
    r = M.transition_rates(states, M.RANGE, 1)
    # RANGE 在 t=0,1,3 → t+1 = RANGE, TREND_UP, TRANSITION
    assert r["n"] == 3
    assert abs(r["rates"][M.RANGE] - 1 / 3) < 1e-9
    assert abs(r["rates"][M.TREND_UP] - 1 / 3) < 1e-9
    assert abs(r["rates"][M.TRANSITION] - 1 / 3) < 1e-9
    assert M.transition_rates(states, M.TREND_DOWN, 1) is None


def test_short_history_is_unknown():
    card = M.state(_bars([100 + i for i in range(50)]))
    assert card["state"] == M.UNKNOWN
    assert card["rates"] == {}
