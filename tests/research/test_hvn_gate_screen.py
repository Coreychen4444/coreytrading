"""hvn gate 定义的**预注册锁**（hvn_gate_design.md §2 「运行前锁定，不许跑完再调」）。

这些断言不是在测"代码没崩"，是在**把预注册的定义钉死**：主回测跑起来之后，任何
对 hvn_gate 取法的改动都会在这里变红。预注册的效力靠的就是"改了会被发现"。
"""
import pytest

from quant.research import hvn_gate_screen as hg


def _bars(spec):
    """spec: [(high, low, close, turnover)] → bar dicts (open 用 close 占位)。"""
    return [{"time": f"2026-01-{i + 1:03d}", "open": c, "high": h, "low": lo,
             "close": c, "volume": 1, "turnover": to}
            for i, (h, lo, c, to) in enumerate(spec)]


def _flat(n, price, turnover=1e6):
    return [(price + 1, price - 1, price, turnover)] * n


def test_hvn_gate_takes_upper_edge_of_nearest_band_above_price():
    """定义 = 价格**上方最近**那条 HVN 带的**上沿**（不是下沿、不是最强那条）。"""
    # 60 根: 前 40 根堆在 100 (最厚), 后 20 根堆在 130; 现价 90 → 上方两条带
    bars = _bars(_flat(40, 100.0, 5e6) + _flat(19, 130.0, 3e6) + [(91, 89, 90.0, 1e6)])
    i = len(bars) - 1
    gate = hg.hvn_gate(bars, i, close_i=90.0, atr14_i=4.0)

    assert gate is not None
    assert 98 <= gate <= 106, f"该取 100 那条带的上沿, 实际 {gate}"


def test_hvn_gate_ignores_bands_below_price():
    """价格上方没有 HVN → None（由调用方回退 cluster，回退比例是判据 4）。

    ⚠️ fixture 必须让下方**至少 5 个桶**都比当日那根重 —— 否则破位日自己那根 bar
    的桶会轻易挤进 top-5, gate 就贴在现价上方。真实数据里这种"贴脸 gate"只占
    2.6% (<0.2N, 全池实测), 中位在现价上方 0.90N, 所以那是 fixture 的人造极端,
    不是定义的退化 —— 定义已预注册锁定, 不因合成边界情形而改。
    """
    below = []
    for px in (100.0, 103.0, 106.0, 109.0, 112.0, 115.0):
        below += _flat(9, px, 5e6)
    bars = _bars(below[:59] + [(201, 199, 200.0, 1e3)])
    assert hg.hvn_gate(bars, len(bars) - 1, close_i=200.0, atr14_i=4.0) is None


def test_hvn_gate_needs_the_locked_window_and_atr():
    """窗口锁定 60 根、bucket 由 ATR14 决定 —— 缺任一个就不给数, 不猜。"""
    bars = _bars(_flat(70, 100.0))
    assert hg.hvn_gate(bars, 70 - 1, 90.0, atr14_i=None) is None     # 无 ATR → None
    assert hg.hvn_gate(bars, 10, 90.0, atr14_i=4.0) is None          # 不足 60 根 → None
    assert hg.PROFILE_BARS == 60 and hg.HVN_N == 5                   # 预注册锁定值


def test_hvn_gate_uses_the_shared_spread_profile_not_a_copy():
    """必须走 core/technical.volume_profile（三角铺开）—— 若有人把它换回点质量,
    或本模块自己抄一份, 这条会红。"""
    import inspect
    src = inspect.getsource(hg.hvn_gate)
    assert "technical.volume_profile" in src
    assert "(h + l + c) / 3" not in src and "high\"] + " not in src


def test_screen_report_verdict_threshold_is_the_registered_one():
    """早停门槛 = 重合率 >70% 停（预注册 §4 / §3b）。判据写在代码里, 不靠记忆。"""
    lo = hg.report([{"diff_bucket": 0.5, "diff_n": 0.1, "hvn": 2, "cluster": 1}] * 8
                   + [{"diff_bucket": 2.0, "diff_n": 1.0, "hvn": 2, "cluster": 1}] * 2)
    assert lo["coincidence_pct"] == 80.0 and lo["verdict"].startswith("STOP")

    hi = hg.report([{"diff_bucket": 2.0, "diff_n": 1.0, "hvn": 2, "cluster": 1}] * 8
                   + [{"diff_bucket": 0.5, "diff_n": 0.1, "hvn": 2, "cluster": 1}] * 2)
    assert hi["coincidence_pct"] == 20.0 and hi["verdict"].startswith("PROCEED")


def test_report_counts_undefined_gates_separately():
    """回退比例是判据 4 (≤20%), 必须单独可读, 不能混进重合率的分母。"""
    rep = hg.report([{"diff_bucket": 0.5, "diff_n": 0.1, "hvn": 2, "cluster": 1},
                     {"diff_bucket": None, "diff_n": None, "hvn": None, "cluster": 1}])
    assert rep["episodes"] == 2 and rep["both_defined"] == 1
    assert rep["hvn_undefined_pct"] == 50.0
    assert rep["coincidence_pct"] == 100.0        # 分母只算两边都有定义的
