"""「Watchlist plans」按 cap 分流。

`build_watchlist_zone_lines` 此前只读 zones, **从不看 current_regime.cap_pct_nav**
—— 于是一张 entry_guard 判 VETO 的 cap 0 观察卡, 与真挂单计划在简报里长得一模一样。

典型: 多张卡降级后, cap 0 的观察卡还带着「有效期至」= 一个 tell 都没有。
渲染口径必须等于判定口径。

判据与 wl_zone_touch / z2_breach_buy_freeze 同款 fail-closed: None 与 0 都不是计划。
"""
from quant.report import portfolio_brief as pb

PLAN_WORDS = ("首档", "二档", "减仓", "不追价", "有效期至")
OBS_WORDS = ("⛔ cap 0", "观察带不是买带", "落点①", "落点②", "图作废")


def _row(ticker, cap, candidate=True):
    return {"ticker": ticker, "is_candidate": candidate, "cap_pct_nav": cap}


def _zones(**over):
    cfg = {
        "accumulation_zone_1": [98.0, 100.0],
        "accumulation_zone_2": [94.7, 98.0],
        "trim_zone": [107.2, 111.1],
        "no_chase_above": 105.2,
        "stop_level": 90.34,
        "valid_until": "2026-09-23",
        "current_regime": {"name": "observation_below_103", "cap_pct_nav": 0,
                           "participation": "gate_only", "source": "auto"},
        "next_regime": {"name": "修复观察", "cap_pct_nav": 0,
                        "entry_gate": {"level": 102.61, "days": 1}},
    }
    cfg.update(over)
    return cfg


def _line(ticker, cap, **over):
    out = pb.build_watchlist_zone_lines([_row(ticker, cap)], {ticker: _zones(**over)})
    return out[0] if out else None


def test_cap_positive_keeps_plan_vocabulary():
    line = _line("BE", 0.40)
    assert line is not None
    for w in PLAN_WORDS:
        assert w in line, w
    assert "观察带不是买带" not in line


def test_cap_zero_renders_observation_not_plan():
    line = _line("SYN", 0)
    assert line is not None
    for w in OBS_WORDS:
        assert w in line, w
    # 仓位层词汇一个都不许出现 —— 这才是这个测试存在的理由
    for w in PLAN_WORDS:
        assert w not in line, w
    assert "[observation_below_103]" in line
    assert "解除=收复 102.61×1" in line


def test_cap_missing_fails_closed_to_observation():
    """current_regime 缺失 / cap 未记录 → 当 0 (sizing.regime_cap_frac 消费侧约定)。"""
    line = _line("X", None, current_regime={}, next_regime={})
    assert line is not None
    assert "⛔ cap 0" in line
    for w in PLAN_WORDS:
        assert w not in line, w


def test_reclaim_gate_cap_is_spelled_out():
    """解除之后拿到多少 cap 要说出来 —— 「收复就有 40%」和「收复只是中间站」不是一回事。"""
    line = _line("NVDA", 0,
                 next_regime={"name": "R_收复_228", "cap_pct_nav": 0.40,
                              "entry_gate": {"level": 228.0, "days": 2}})
    assert "解除=收复 228×2 → cap 40%" in line
    # 中间站 (收复后仍 cap 0) 不许印出一个假的 cap
    assert "→ cap" not in _line("SYN", 0)


def test_non_candidate_gets_no_line_at_all():
    """墓碑卡 (移出 moomoo 分组) 连观察行都不发 — 与触碰/冻结/stale 同源。"""
    assert pb.build_watchlist_zone_lines(
        [_row("AAOI", 0, candidate=False)], {"AAOI": _zones()}) == []
