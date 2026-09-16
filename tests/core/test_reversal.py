"""core/reversal — 抄底入场类 (reversal_probe) 的机械判别式。

现行信号: ✓止跌 = 止跌低之后次日未新低 或 止跌日长下影 (core.setups 同一份函数);
止损 = 止跌低; 域 = R0 (止跌低距 60 日高 ≥10%, 不封顶); 跌破止跌低即作废;
有效 = 存活 ∧ 收盘 ≤ 止跌低 + 2×ATR20 ∧ age < 10 (= setups.RECENT_SIGNAL_BARS, 授权不比判读活得久);
阶梯 = min(信号收盘, 最新收盘) / 止跌低之上 2/3 / 1/3, 降序且全在止跌低之上。
参数与 research/bottom_stage_backtest 同一份函数 —— 改判别式必须重跑定价。
"""
from quant.core import reversal, setups


def _bar(t, o, h, l, c):
    return {"time": t, "open": o, "high": h, "low": l, "close": c}


def _series(bottom=102.0, rev_close=107.0):
    """0..40 升到 120 (60 日高 120.5), 41..60 单边跌到 bottom (每日新低), 61 = 次日未新低的那根。"""
    rows = []
    for i in range(41):
        c = 100 + i * 0.5
        rows.append(_bar(f"2026-01-{i:03d}", c - 0.2, c + 0.5, c - 0.5, c))
    for k in range(1, 21):
        c = 120 - (120 - bottom) * k / 20
        rows.append(_bar(f"2026-02-{k:03d}", c + 0.3, c + 0.5, c - 0.5, c))
    rows.append(_bar("2026-03-01", bottom + 0.5, rev_close + 0.5, bottom + 0.2, rev_close))
    return rows


def test_signal_fields_are_the_setups_frame():
    rows = _series()
    sig = reversal.signal_at(rows, len(rows) - 1, atr20=2.0)
    assert sig and sig["in_domain"] and sig["domain"] == "R0"
    assert sig["signal"] == setups.SIGNAL_HELD and sig["date"] == "2026-03-01"
    assert sig["support"] == 101.5 and sig["low_date"] == "2026-02-02"    # 止跌低 = bottom − 0.5 (日期截 10 字符)
    assert sig["lower_high"] == 120.5                                        # 前高 = 顶
    assert sig["high_60d"] == 120.5 and abs(sig["drawdown"] - (101.5 / 120.5 - 1)) < 1e-9
    assert sig["stop_dist_n"] == round((107 - 101.5) / 2.0, 2)
    assert sig["above_low_bar_high"] and sig["close"] == 107.0
    fr = setups._frame(rows)
    assert fr["stage"] == "formed" and fr["low"] == sig["support"]


def test_no_signal_while_still_making_new_lows():
    rows = _series()[:-1]                                   # 最后一根仍是新低 → ⏳, 不是信号
    assert reversal.signal_at(rows, len(rows) - 1) is None
    assert setups._frame(rows)["stage"] == "watch"


def test_signal_day_is_the_formation_day_only():
    rows = _series() + [_bar("2026-03-02", 107, 108, 106, 107.5)]
    assert reversal.signal_at(rows, len(rows) - 1) is None     # 形成日是前一根, 今天 age 1
    assert reversal.signal_at(rows, len(rows) - 2) is not None


def test_no_signal_without_a_drawdown():
    rows = [_bar(f"d{i:03d}", 100 + i * 0.3, 100 + i * 0.3 + 0.5, 100 + i * 0.3 - 0.5, 100 + i * 0.3)
            for i in range(70)]
    rows.append(_bar("d999", 121.0, 125.0, 120.8, 124.5))
    assert reversal.signal_at(rows, len(rows) - 1) is None    # 上升趋势里的浅回撤, 没有底可抄


def test_shallow_drawdown_is_out_of_domain_deep_is_in():
    shallow = _series(bottom=112.8, rev_close=116.0)          # 止跌低 112.3 距 120.5 −6.8%
    assert setups._frame(shallow)["stage"] == "none"          # 人群门在 setups 就拦了
    assert reversal.signal_at(shallow, len(shallow) - 1) is None
    deep = _series(bottom=84.0, rev_close=92.5)                # −31%: R2 也在域内 (R0)
    s = reversal.signal_at(deep, len(deep) - 1)
    assert s and s["domain"] == "R0" and s["in_domain"]
    assert reversal.PARAMS["drawdown_max"] is None


def test_detect_age_expiry_and_void_on_support_break():
    rows = _series()
    sig = reversal.detect(rows)
    assert sig and sig["age"] == 0
    rows2 = rows + [_bar("a", 107, 108, 106, 107.5), _bar("b", 107.5, 108.5, 106.5, 108)]
    assert reversal.detect(rows2)["age"] == 2
    rows3 = rows2 + [_bar("c", 108, 109, 107, 108.2)]          # 第 3 根: 旧 3 日窗到此过期, 现在仍活
    assert reversal.detect(rows3)["age"] == 3
    rows10 = rows + [_bar(f"k{j}", 107, 108, 106, 107.5) for j in range(10)]   # 第 10 根 = 超过 valid_days 10 → 过期
    assert reversal.detect(rows10) is None
    assert reversal.detect(rows10, valid_days=12)["age"] == 10
    assert reversal.detect(rows10[:-1])["age"] == 9
    broken = rows + [_bar("x", 105, 105.5, 100.0, 101.0)]      # 收盘 < 止跌低 101.5 → 作废
    assert reversal.detect(broken, valid_days=10) is None


def test_detect_reports_distance_and_too_far_but_never_hides_the_signal():
    """有效期第二条件: 收盘距止跌低 ≤ max_dist_atr×ATR20。超过 = too_far (播不授权, 不返回 None);
    ATR 缺 = 判不了 (None), 调用方 fail-closed。"""
    rows = _series()                                            # 收 107, 止跌低 101.5 → 距 5.5
    far = reversal.detect(rows, atr20=2.0)                      # 2.75N > 2.0
    assert far and far["dist_n"] == 2.75 and far["too_far"] is True and far["last_close"] == 107.0
    near = reversal.detect(rows, atr20=4.0)                     # 1.38N
    assert near["dist_n"] == 1.38 and near["too_far"] is False
    unknown = reversal.detect(rows, atr20=None)
    assert unknown["dist_n"] is None and unknown["too_far"] is None
    # 距离随最新收盘走: 涨远了 too_far, 回落到上限内信号还在 (存活只看收盘 < 止跌低)
    up = rows + [_bar("u", 108, 113, 107.5, 112.0)]             # (112 − 101.5)/4 = 2.6N
    assert reversal.detect(up, atr20=4.0)["too_far"] is True
    back = up + [_bar("b", 111, 111.5, 105.5, 106.0)]           # 1.1N, 未收破 101.5
    assert reversal.detect(back, atr20=4.0)["too_far"] is False and reversal.detect(back, atr20=4.0)["age"] == 2
    assert reversal.PARAMS["max_dist_atr"] == 2.0


def test_valid_days_never_outlives_the_chart_read_window():
    """授权不比判读活得久: chart-read 只列 ✓止跌 ≤ RECENT_SIGNAL_BARS 根, 授权窗不得更长。"""
    assert reversal.PARAMS["valid_days"] == 10
    assert reversal.PARAMS["valid_days"] <= setups.RECENT_SIGNAL_BARS


def test_hammer_forms_on_the_low_day_itself():
    rows = _series()[:-1]
    rows[-1] = _bar("2026-02-020", 103.0, 103.0, 101.5, 103.0)   # 止跌日: 下影 1.5 / 实体 0 / 上影 0 → 锤子
    sig = reversal.signal_at(rows, len(rows) - 1)
    assert sig and sig["signal"] == setups.SIGNAL_HAMMER and sig["hammer"] and sig["support"] == 101.5


def test_ladder_descending_and_above_support():
    rows = _series()
    sig = reversal.signal_at(rows, len(rows) - 1)
    lad = reversal.ladder(sig)
    assert lad[0] == 107.0
    assert lad[1] == round(101.5 + 2 / 3 * (107 - 101.5), 2) and lad[2] == round(101.5 + 1 / 3 * (107 - 101.5), 2)
    assert lad == sorted(lad, reverse=True) and all(x > sig["support"] for x in lad)


def test_ladder_first_rung_never_sits_above_the_latest_close():
    rows = _series()
    sig = reversal.signal_at(rows, len(rows) - 1)
    assert reversal.ladder(sig, 104.0)[0] == 104.0            # 信号后回落: 首档跟收盘
    assert reversal.ladder(sig, 110.0)[0] == 107.0            # 涨走了: 首档仍是信号收盘, 不追
    assert reversal.ladder(sig, 101.0) == []                  # 收盘已在止跌低下 (detect 本会作废)
