"""bottom_stage_backtest 只做一件事的守卫: 回测的信号 = core.setups._frame 同一份判别式
(判读口径 = 定价口径); 显式 support 走进 simulate 的止损线。数字本身不测 (探索定价, 非预注册)。"""
from quant.core import setups
from quant.research import bottom_stage_backtest as bb
from quant.research import reversal_probe_backtest as rb


def _bar(t, h, l, c):
    return {"date": t, "time": t, "open": c, "high": h, "low": l, "close": c, "volume": 1000}


def _rows():
    # 垫子 265: 距顶 281 不足 10%, 垫子本身不会成为一个「底」(否则 20 日低会落在垫子里)
    rows = [_bar(f"2025-01-{i:03d}", 265.5, 264.5, 265.0) for i in range(setups.MIN_BARS)]
    path = [(281, 275, 280), (270, 250, 252), (255, 235, 238),
            (250, 232, 245), (258, 240, 255), (252, 236, 240),   # 中途更低的分形高 258 = 前高 (< 顶 281)
            (240, 218, 220), (222, 205, 208),
            (210, 194, 199),                       # 止跌低 194 (非锤子: 上影长)
            (206, 196, 205),                       # 次日未新低 → ✓止跌 (信号日)
            (226, 210, 224), (240, 222, 238), (256, 236, 254),
            (250, 232, 235), (236, 224, 226),      # 回踩低 224
            (238, 225, 237),                       # 更高的低点
            (266, 252, 264)]                       # 收 264 > 前高 258 = 越前高 (高 266 < 顶, 框架仍在)
    for k, (h, l, c) in enumerate(path):
        rows.append(_bar(f"2025-06-{k:03d}", h, l, c))
    return rows


def test_stage_signals_are_the_setups_frame(monkeypatch):
    monkeypatch.setattr(bb, "COOLDOWN", 0)             # 只测「信号 = setups 框架」, 冷却另测
    rows = _rows()
    n = len(rows)
    ind = {"ma50": [1.0] * n, "hv20": [0.3] * n, "atr20": [2.0] * n}
    sig = bb.stage_signals(rows, ind)
    # 顶 281 之后: 中途反弹 (232 守住一天) 是一个 ✓止跌, 再破到 194 后次日未新低是第二个 —— 两个都是框架事实
    assert [(i, low) for i, low, *_ in sig["all"]] == [(n - 13, 232.0), (n - 8, 194.0)]
    i_all, i_lh = sig["all"][1][0], sig["above_lh"][0][0]
    assert (i_all, i_lh) == (n - 8, n - 1)
    assert sig[setups.SIGNAL_HELD] == sig["all"] and sig[setups.SIGNAL_HAMMER] == []
    fr = setups._frame(rows[:i_all + 1])
    assert fr["stage"] == "formed" and fr["bars_since_signal"] == 0 and fr["signal"] == setups.SIGNAL_HELD
    assert setups._frame(rows[:i_lh + 1])["above_lh"]
    assert bb.WINDOW_START == rb.WINDOW_START and rb.COOLDOWN == 20


def test_explicit_support_is_the_stop_line():
    """support 显式传入 → 收盘跌破它就是「支撑破」; 不传 = 探针原行为 (本文件不动那条)。"""
    rows = _rows()
    n = len(rows)
    # ATR20 = 30: 趋势死亡线 (hwm − 3N) 不先于支撑触发, 这条测的是支撑线本身
    ind = {"ma50": [1.0] * n, "hv20": [0.3] * n, "atr20": [30.0] * n}
    i0 = n - 8
    crash = rows[:n - 1] + [_bar("2025-07-001", 240, 215, 220)]      # 收 220 < 224, > 止跌低 194
    ind2 = {k: v[:n - 1] + [v[-1]] for k, v in ind.items()}
    r_hl = rb.simulate(crash, ind2, i0, entry_dte=270, delta_target=0.70, iv_daily=True,
                       support=224.0, stock=True)
    r_low = rb.simulate(crash, ind2, i0, entry_dte=270, delta_target=0.70, iv_daily=True,
                        support=194.0, stock=True)
    assert r_hl["exit"] == "支撑破" and r_low["exit"] != "支撑破"


def test_time_stop_and_hold_if_reversal_are_default_off():
    """max_days / hold_above 缺省不传 = 探针原行为; 传了 = T+N 收盘时间止损, 越前高则不平。"""
    rows = _rows()
    n = len(rows)
    ind = {"ma50": [1.0] * n, "hv20": [0.3] * n, "atr20": [30.0] * n}
    i0 = n - 8                                             # ✓止跌 信号日 (收 205)
    plain = rb.simulate(rows, ind, i0, entry_dte=270, delta_target=0.70, stock=True, support=194.0)
    assert plain["exit"] == "数据尾截断"
    t2 = rb.simulate(rows, ind, i0, entry_dte=270, delta_target=0.70, stock=True, support=194.0, max_days=2)
    assert t2["exit"] == "时间止损" and t2["days"] == 2
    held = rb.simulate(rows, ind, i0, entry_dte=270, delta_target=0.70, stock=True, support=194.0,
                       max_days=2, hold_above=230.0)          # T+2 收 238 > 230 → 不平
    assert held["exit"] != "时间止损" or held["days"] > 2
