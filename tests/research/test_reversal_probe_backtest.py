"""reversal_probe_backtest 只做一件事的守卫: 回测的信号 = core.reversal 同一份判别式
(渲染口径 = 判定口径)。数字本身不测 (探索定价, 非预注册)。"""
from quant.core import reversal
from quant.research import reversal_probe_backtest as rb


def _bar(t, o, h, l, c):
    return {"date": t, "time": t, "open": o, "high": h, "low": l, "close": c, "volume": 1000}


def test_backtest_signal_scan_is_the_core_detector():
    rows = []
    for i in range(41):
        c = 100 + i * 0.5
        rows.append(_bar(f"2025-06-{i:03d}", c - 0.2, c + 0.5, c - 0.5, c))
    for k in range(1, 21):
        c = 120 - 18 * k / 20
        rows.append(_bar(f"2025-08-{k:03d}", c + 0.3, c + 0.5, c - 0.5, c))
    rows.append(_bar("2025-09-001", 102.5, 107.5, 102.2, 107.0))
    ind = {"ma50": [1.0] * len(rows), "hv20": [0.3] * len(rows), "atr20": [2.0] * len(rows)}
    assert rb.rev_signals(rows, ind, "R1") == [len(rows) - 1]
    assert reversal.signal_at(rows, len(rows) - 1, 2.0)["in_domain"]
    assert rb.rev_signals(rows, ind, "R2") == []
    assert rb.WINDOW_START < "2025-09-001"
