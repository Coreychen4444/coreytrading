"""lib/sizing —— regime cap × gate 预算 + 整股档位分配。

 分类轴退役 (classification_removal_design.md): clamp/CAP_FRACS 段
的测试随机制移除; 新增退役播报、期权轮资格闸、fail-closed 不变量。
"""

import pytest

from quant.core import sizing
from quant.core.sizing import (# noqa: E402
    constraint_lines, read_constraints, share_ladder,
)


# ── 分类轴退役 () ──────────────────────────────────────

def test_classification_machinery_is_gone():
    """删掉的符号不许悄悄回来 —— 分类轴退役是裁决不是重构。"""
    for name in ("CAP_FRACS", "CLASS_ORDER", "clamp_classification",
                 "derive_classification", "classification_for_cap",
                 "effective_cap_frac"):
        assert not hasattr(sizing, name), f"sizing.{name} 该随分类轴退役"


def test_legacy_max_classification_is_reported_not_silent():
    """老卡遗留的 max_classification 不再 clamp, 但必须播报'已退役'——
    静默忽略会让人以为约束还在生效 (静默失效是本仓惯病)。"""
    c = {"max_classification": "probe", "reason": "遗留", "until": "2026-08-11"}
    out = "\n".join(constraint_lines(c, "2026-08-13"))
    assert "已退役" in out and "max_classification" in out


def test_no_constraints_is_passthrough():
    assert read_constraints({"action": {"reason": "prose only"}}) == {}
    assert read_constraints(None) == {}


def test_constraint_lines_render_protocol():
    c = {"touch_protocol": "B_passive_only"}
    out = "\n".join(constraint_lines(c, "2026-08-13"))
    assert "仅预挂被动" in out


def test_constraint_lines_flag_dead_window_fields():
    """ledger A1: 带时间窗的 constraints 无任何 clamp 效力 = 死字段, 必须响。"""
    dead = {"reason": "8/26 AMC 财报", "until": "2026-08-26", "from": "2026-08-19"}
    lines = constraint_lines(dead, "2026-08-09")
    assert any("死字段" in ln for ln in lines)
    # 纯 vehicle/touch_protocol (无窗口字段) 不许误伤
    assert not any("死字段" in ln for ln in
                   constraint_lines({"vehicle": "stock_first",
                                     "touch_protocol": "C_60m_first_bar"}, "2026-08-09"))


# ── 期权资格闸 (用户: gate1 起可期权, 翻 08-13「期权 R2 起」) ─────────

def test_option_min_gate_is_one_and_first_gate_has_premium_cap():
    """用户:「不再限制 gate2 才允许期权入场 …… 依然需要等见底条件」——
    见底条件 = 第一个给 cap 的 gate, 与正股同一套资格 ⇒ gate1 就有保费上限。"""
    assert sizing.OPTION_MIN_GATE == 1
    assert sizing.option_premium_cap_frac(1) == pytest.approx(0.125)
    assert sizing.option_premium_cap_frac(0) == 0.0


def test_option_premium_cap_uses_the_step_not_the_cumulative_cap():
    """保费上限 = **本档新增** risk ÷ |S1| 0.20: gate1 12.5% / gate2 7.5% / gate3 5% NAV。
    08-13「不跟结转」未动 —— 跟了就是 20%/25%。超 3 档 = 0。"""
    assert sizing.OPTION_ATRISK_LOSS_FRAC == pytest.approx(0.20)
    assert sizing.option_premium_cap_frac(2) == pytest.approx(0.075)
    assert sizing.option_premium_cap_frac(3) == pytest.approx(0.05)
    assert sizing.option_premium_cap_frac(4) == 0.0


def test_long_call_iv_gate_percentile_when_reliable_else_absolute():
    """IV 硬门槛 (用户): 可信 percentile <40 放行; 序列不足回落绝对
    ATM IV <50%; 无数据不放行 (fail-closed)。"""
    g = sizing.long_call_iv_gate(12.0, True, 88.0)
    assert g["allowed"] and g["basis"] == "pct"          # 自身低分位放行 (已知形状, 文档标明)
    g = sizing.long_call_iv_gate(40.0, True, 30.0)
    assert not g["allowed"] and g["basis"] == "pct"       # 含 40 = 不放行
    g = sizing.long_call_iv_gate(3.0, False, 67.0)
    assert not g["allowed"] and g["basis"] == "abs"       # 样本不足 → 绝对值兜底: MU 67% ≥ 50
    g = sizing.long_call_iv_gate(None, False, 39.0)
    assert g["allowed"] and g["basis"] == "abs"           # NVDA 39%
    g = sizing.long_call_iv_gate(None, False, None)
    assert not g["allowed"] and g["basis"] == "none"
    assert sizing.LONG_CALL_IV_PCT_MAX == 40.0 and sizing.LONG_CALL_IV_ABS_MAX == 50.0


def test_granting_gate_level_is_the_last_cap_granting_up_since_the_break():
    """期权 S5g 退出线 = 授予当前 cap 的那道 gate 本身 (META 553 案)。与 gate_count 数
    同一批条目; down 转移 / 平仓打断参与流后 = None (退回卡 stop)。"""
    up = lambda d, lvl, cap=0.40: {"date": d, "direction": "up", "level": lvl, "cap_pct_nav": cap}  # noqa: E731
    down = lambda d, lvl: {"date": d, "direction": "down", "level": lvl, "cap_pct_nav": 0}  # noqa: E731
    assert sizing.granting_gate_level(None) is None
    assert sizing.granting_gate_level({"agent": {}}) is None            # 常规参与卡: 没爬过线
    meta = {"agent": {"regime_history": [down("2026-08-19", 553.0), up("2026-08-26", 553.0)]}}
    assert sizing.granting_gate_level(meta) == 553.0
    two = {"agent": {"regime_history": [up("2026-08-05", 945.0), up("2026-08-14", 971.0)]}}
    assert sizing.granting_gate_level(two) == 971.0                     # 最近一道
    assert sizing.granting_gate_level({"agent": {"regime_history": [
        up("2026-08-05", 204.0), down("2026-08-24", 200.0)]}}) is None  # 破位后参与流断
    assert sizing.granting_gate_level({"agent": {"regime_history": [
        up("2026-08-05", 204.0, cap=0.0)]}}) is None                      # 不授权的中间站不算
    assert sizing.granting_gate_level({**two, "exit_info": {"closed_at": "2026-08-20T10:00:00-04:00"}}) is None


# ── 整股档位分配 ──────────────────────────────────────────────────

def test_share_ladder_three_tiers_when_affordable():
    sl = share_ladder(1000.0, [100.0, 98.0, 95.0], (0.5, 0.3, 0.2))
    assert sl["n_shares"] == 10 and sl["n_tiers"] == 3 and not sl["degraded"]
    assert [t["shares"] for t in sl["tiers"]] == [5, 3, 2]
    assert sum(t["shares"] for t in sl["tiers"]) == 10


def test_share_ladder_every_tier_gets_at_least_one_share():
    """3 档就是 3 档 —— 0 股的"档"不是档。"""
    for n in (3, 4, 5, 6, 7):
        sl = share_ladder(n * 100.0, [100.0, 98.0, 95.0], (0.5, 0.3, 0.2))
        assert sl["n_tiers"] == 3, f"n={n}"
        assert all(t["shares"] >= 1 for t in sl["tiers"]), f"n={n}"
        assert sum(t["shares"] for t in sl["tiers"]) == n, f"n={n}"


def test_share_ladder_degrades_to_two_tiers():
    sl = share_ladder(250.0, [100.0, 98.0, 95.0], (0.5, 0.3, 0.2))
    assert sl["n_shares"] == 2 and sl["n_tiers"] == 2 and sl["degraded"]
    assert [t["shares"] for t in sl["tiers"]] == [1, 1]


def test_share_ladder_single_tier_exempts_greedy_bid_ban():
    sl = share_ladder(150.0, [100.0, 98.0, 95.0], (0.5, 0.3, 0.2))
    assert sl["n_tiers"] == 1 and not sl["exception"]
    assert "豁免 greedy-bid" in sl["note"]


def test_share_ladder_zero_shares_forces_written_exception():
    """probe cap $225 / LITE $708 → 0 股。这是书面例外路径, 不是三档计划。"""
    sl = share_ladder(225.3, [708.4, 701.32, 694.31], (0.5, 0.3, 0.2))
    assert sl["n_shares"] == 0 and sl["exception"] is True
    assert sl["n_tiers"] == 1
    assert "decisions.jsonl" in sl["note"]
    assert sl["cap_multiple"] == 3.14


def test_share_ladder_lite_confirmed_case_no_fractional_tiers():
    """ 实际输出: $818 cap → 0.6/0.3/0.2 股。整股后应是单档 1 股。"""
    sl = share_ladder(817.59, [708.4, 701.32, 694.31], (0.5, 0.3, 0.2))
    assert sl["n_shares"] == 1 and sl["n_tiers"] == 1
    assert all(float(t["shares"]).is_integer() and t["shares"] >= 1 for t in sl["tiers"])


def test_share_ladder_option_multiplier():
    """期权按张×100 计, cap $1200 / (premium 5 × 100) = 2 张。"""
    sl = share_ladder(1200.0, [5.0, 4.5, 4.0], (0.5, 0.3, 0.2), multiplier=100)
    assert sl["n_shares"] == 2 and sl["n_tiers"] == 2


def test_share_ladder_none_when_no_cap():
    assert share_ladder(None, [100.0], (1.0,)) is None
    assert share_ladder(0.0, [100.0], (1.0,)) is None
    assert share_ladder(1000.0, [], (1.0,)) is None


# ── 共享层不变量 ──────────────────────────────────────────────────

def test_round_schedule_is_single_source_for_all_consumers():
    """gate 预算表全仓只此一份 —— entry_guard PARAMS 不再存任何 cap 数。"""
    from quant.decision import entry_guard
    assert sizing.GATE_RISK_CAPS == (0.025, 0.040, 0.050)
    for legacy in ("cap_confirmed", "cap_probe", "cap_early_risky"):
        assert legacy not in entry_guard.PARAMS, f"PARAMS.{legacy} 该随分类轴退役"


# ── 结构修复 VETO 判别式 (, 前身 = 结构降级判别式) ──
#
# 回测 analytics/structure_downgrade_backtest.py 定的边界 (内核逐字不动):
#   transition → VETO (统计上 = down; 旧输出「降级 probe」, 用户
#   「结构降级反转立马 cap 0 即可」)
#   close_below_last_swing_low 且 trend=up → **不**VETO (回调, 非破位)

def _synthetic_entry_context(monkeypatch):
    """非空合成书供结构用例算钱；日历/购买力不再隐式读私人数据。"""
    from quant.decision import entry_guard
    positions = {"tickers": {"TEST": {"instrument": {"type": "stock"},
                                      "broker": {"qty": 2, "current_price": 100}}}}
    exposure = entry_guard.book.exposure_of
    monkeypatch.setattr(entry_guard.book, "exposure_of", lambda ticker: exposure(ticker, positions_doc=positions))
    monkeypatch.setattr(entry_guard, "_power", lambda: {"room": 50000.0, "as_of": "2026-07-29"})
    return {"calendar": {"as_of": "2026-07-29", "tickers": {}},
            "usage": sizing.book_usage(positions, 100000, price_of=lambda ticker: 100,
                                       atr_of=lambda ticker: 2)}


def _synthetic_entry_context(monkeypatch):
    """非空合成书供结构用例算钱；日历/购买力不再隐式读私人数据。"""
    from quant.decision import entry_guard
    positions = {"tickers": {"TEST": {"instrument": {"type": "stock"},
                                      "broker": {"qty": 2, "current_price": 100}}}}
    exposure = entry_guard.book.exposure_of
    monkeypatch.setattr(entry_guard.book, "exposure_of", lambda ticker: exposure(ticker, positions_doc=positions))
    monkeypatch.setattr(entry_guard, "_power", lambda: {"room": 50000.0, "as_of": "2026-07-29"})
    return {"calendar": {"as_of": "2026-07-29", "tickers": {}},
            "usage": sizing.book_usage(positions, 100000, price_of=lambda ticker: 100,
                                       atr_of=lambda ticker: 2)}


def _eval_class(monkeypatch, structure, trend_repaired_close=None):
    """跑 entry_guard 的结构 VETO 段, 只喂结构 —— 其余走一张最简 intact 卡。"""
    from quant.decision import entry_guard
    zones = {"accumulation_zone_1": [95, 105], "accumulation_zone_2": [80, 94],
             "trim_zone": [130, 140], "no_chase_above": 125, "stop_level": 70,
             "valid_until": "2099-01-01"}
    card = {"agent": {"thesis": {"summary": "t", "status": "intact",
                                 "last_reviewed": "2099-01-01T00:00:00Z"},
                      "zones": zones, "action": {"intent": "wait_for_zone"},
                      "current_regime": {"name": "常规参与", "cap_pct_nav": 0.40,
                                         "since": "2026-07-01", "source": "card"}}}
    rows = [{"time": f"2026-01-{d:02d} 00:00:00", "open": 100, "high": 101,
             "low": 99, "close": 100} for d in range(1, 29)]
    monkeypatch.setattr(entry_guard, "_rows", lambda tk: rows)
    monkeypatch.setattr(entry_guard.technical, "swing_structure", lambda r, **k: structure)
    monkeypatch.setattr(entry_guard, "zone_staleness", lambda *a, **k: [])
    monkeypatch.setattr(entry_guard, "iv_gate_line", lambda tk: "")
    return entry_guard.evaluate_ticker("TEST", card, today="2026-07-29", nav=100000,
                                       **_synthetic_entry_context(monkeypatch))


def test_transition_vetoes(monkeypatch):
    """LITE  内核: HH+LL 曾逃过 trend=='down' 判别式。二值世界 = VETO。"""
    r = _eval_class(monkeypatch, {"trend": "transition", "high_label": "HH",
                                  "low_label": "LL", "last_swing_high": 150,
                                  "close_below_last_swing_low": False})
    assert r["verdict"] == "VETO"
    assert any("transition" in x for x in r["reasons"])
    assert any("重画" in x for x in r["reasons"])   # VETO 必须指路 (地图错误 → 重画)


def test_up_but_below_swing_low_still_plans(monkeypatch):
    """NVDA 同日案: 回测显示该桶对 up 干净不可区分、p10 更优 → 明确不 VETO。"""
    r = _eval_class(monkeypatch, {"trend": "up", "high_label": "HH", "low_label": "HL",
                                  "last_swing_high": 150,
                                  "close_below_last_swing_low": True})
    assert r["verdict"] == "PLAN"


def test_down_vetoes(monkeypatch):
    r = _eval_class(monkeypatch, {"trend": "down", "high_label": "LH", "low_label": "LL",
                                  "last_swing_high": 150,
                                  "close_below_last_swing_low": True})
    assert r["verdict"] == "VETO"


def test_clean_up_plans_full_r1(monkeypatch):
    """结构干净 = PLAN, size 由 gate 预算主导 —— 没有分类打折。"""
    r = _eval_class(monkeypatch, {"trend": "up", "high_label": "HH", "low_label": "HL",
                                  "last_swing_high": 150,
                                  "close_below_last_swing_low": False})
    assert r["verdict"] == "PLAN"
    assert r["caps"]["final"] is not None and r["caps"]["final"] > 0
    assert r["caps"]["regime_cap"] == 100000 * sizing.NOTIONAL_CAP - 200.0


def test_repaired_transition_is_not_vetoed(monkeypatch):
    """close > last_swing_high = 修复信号 → transition 不 VETO (判别式内核原语义)。"""
    r = _eval_class(monkeypatch, {"trend": "transition", "high_label": "HH",
                                  "low_label": "LL", "last_swing_high": 90,
                                  "close_below_last_swing_low": False})
    assert r["verdict"] == "PLAN"


# ── 当前 regime 的授权属性 (fix A,) ──────────────────────
#
# regime_transition 造 observation_below_* 时写 cap_pct_nav=0 + participation
# =gate_only, derivation 逐字写着"不授权任何入场"。但它成为 current 时,
# apply_transition 只搬 zones —— cap/participation 丢在 `agent["zones"] =
# dict(last.get("zones"))` 这一行。而 cap_pct_nav 全仓只有两处被读, 都只读
# next_regime。结果: 一个明确标着不授权的 regime, 只剩两条 accumulation 带
# 架在刚破的 stop 底下。

from quant.core.sizing import (# noqa: E402
    regime_cap_frac, regime_gate, regime_lines,
)

import pytest
import quant.core.sizing as sz  #  框架件按模块访问 (常数会变, 不逐个 import)

OBS = {"current_regime": {"name": "observation_below_176", "cap_pct_nav": 0,
                          "participation": "gate_only", "since": "2026-07-28",
                          "reason": "收盘 166.41 < 卡片 stop 176 连续 1 日"}}


def test_regime_cap_zero_means_no_authorization():
    assert regime_cap_frac(OBS) == 0.0


def test_absent_current_regime_fails_closed_in_entry_cap():
    """无 regime = 无授权 (fail-closed), 不是"不约束" ——  分类轴退役后
    regime cap 是唯一授权来源, None 当 0 处理 (schema 同生同死保证活跃卡必有)。"""
    assert regime_cap_frac({}) is None
    assert regime_cap_frac(None) is None
    assert regime_gate({"action": {}}) == {}
    ac = sizing.entry_cap(100000, {}, atr=3.0, price=100.0, stop=94.0)
    assert ac["regime_cap"] == 0.0
    assert ac["final"] == 0.0 and ac["binding"] == "regime"


def test_regime_lines_say_cap_zero_out_loud():
    """cap 0 必须显式说出来, 不能只体现为"金额小"。"""
    out = "\n".join(regime_lines(OBS))
    assert "cap 0" in out and "不授权任何入场/加仓" in out
    assert "observation_below_176" in out
    assert "gate_only" in out


def test_entry_guard_vetoes_inside_zero_cap_regime(monkeypatch):
    """在观察 regime 里, "价格在 z1/z2 内"只说明跌到哪了, 不是买点。"""
    from quant.decision import entry_guard
    zones = {"accumulation_zone_1": [95, 105], "accumulation_zone_2": [80, 94],
             "trim_zone": [130, 140], "no_chase_above": 125, "stop_level": 70,
             "valid_until": "2099-01-01"}
    agent = {"thesis": {"summary": "t", "status": "intact",
                        "last_reviewed": "2099-01-01T00:00:00Z"},
             "zones": zones, "action": {"intent": "wait_for_zone"}, **OBS}
    rows = [{"time": f"2026-01-{d:02d} 00:00:00", "open": 100, "high": 101,
             "low": 99, "close": 100} for d in range(1, 29)]
    monkeypatch.setattr(entry_guard, "_rows", lambda tk: rows)
    monkeypatch.setattr(entry_guard, "zone_staleness", lambda *a, **k: [])
    monkeypatch.setattr(entry_guard, "iv_gate_line", lambda tk: "")
    monkeypatch.setattr(entry_guard.technical, "swing_structure", lambda r, **k: {
        "trend": "up", "high_label": "HH", "low_label": "HL", "last_swing_high": 90,
        "close_below_last_swing_low": False})
    r = entry_guard.evaluate_ticker("TEST", {"agent": agent},
                                    today="2026-07-29", nav=100000,
                                    **_synthetic_entry_context(monkeypatch))
    assert r["verdict"] == "VETO", f"价格在 zone1 内但 regime cap 0 应 VETO, 实得 {r['verdict']}"
    assert any("cap 0" in x for x in r["reasons"])
    # 结构修复也不放行 —— 此前 wait_for_structure 轨修复后就按正常 cap 给 ladder
    assert r.get("ladder") is None


def test_size_caps_honours_regime(monkeypatch):
    from quant.decision import entry_guard
    normal = entry_guard.size_caps(100000, atr=3.0, t1=100.0,
                                   agent={"current_regime": {"cap_pct_nav": 0.40}})
    gated = entry_guard.size_caps(100000, atr=3.0, t1=100.0,
                                  agent={"current_regime": {"cap_pct_nav": 0.002}})
    assert gated["regime_cap"] < normal["regime_cap"]
    assert gated["regime_cap"] == 100000 * 0.002


def test_regime_transition_persists_cap_on_both_directions():
    """升档也要写 —— 否则上一次向下留下的 cap 0 会永久锁死已修复的仓位。"""
    from quant.decision import regime_transition as rt
    down = rt.apply_transition(
        {"agent": {"zones": {"accumulation_zone_1": [200, 216], "stop_level": 176},
                   "last_regime": {"name": "observation_below_176", "cap_pct_nav": 0,
                                   "participation": "gate_only",
                                   "zones": {"accumulation_zone_1": [164, 176],
                                             "stop_level": 154}},
                   "action": {}}},
        {"ticker": "T", "date": "2026-07-30", "direction": "down", "level": 176.0,
         "days": 1, "reason": "收盘 < 176"}, klines=[])
    cr = down["agent"]["current_regime"]
    assert cr["cap_pct_nav"] == 0 and cr["participation"] == "gate_only"
    assert cr["name"] == "observation_below_176" and cr["since"] == "2026-07-30"

    up = rt.apply_transition(
        {"agent": {"zones": {"stop_level": 154}, "current_regime": cr,
                   "next_regime": {"name": "reclaim_176", "cap_pct_nav": 0.01,
                                   "zones": {"stop_level": 176}},
                   "action": {}}},
        {"ticker": "T", "date": "2026-08-01", "direction": "up", "level": 176.0,
         "days": 2, "reason": "收复 176 x2"}, klines=[])
    cr2 = up["agent"]["current_regime"]
    assert cr2["cap_pct_nav"] == 0.01, "升档后 cap 0 残留 = 永久锁死"
    assert cr2["name"] == "reclaim_176"


# ── 缺口 1+2: 破位不该拆掉上行阶梯 () ────────────────────

def _down(agent, level=176.0, date="2026-07-30"):
    from quant.decision import regime_transition as rt
    return rt.apply_transition({"agent": agent},
                               {"ticker": "T", "date": date, "direction": "down",
                                "level": level, "days": 1, "reason": f"收盘 < {level}"},
                               klines=[])["agent"]


LADDER = {
    "zones": {"accumulation_zone_1": [205, 216], "stop_level": 176.0},
    "last_regime": {"name": "obs_176", "cap_pct_nav": 0, "participation": "gate_only",
                    "zones": {"accumulation_zone_1": [164, 176], "stop_level": 154}},
    "next_regime": {"name": "C_confirmed_reclaim_234", "cap_pct_nav": 0.05,
                    "entry_gate": {"level": 234, "days": 2, "unlocks": "confirmed"},
                    "next_gate_preview": {"level": 293, "days": 2, "unlocks": "rebase",
                                          "cap_pct_nav": 0.05}},
    "action": {},
}


def test_down_break_preserves_upper_ladder_structure():
    """缺口 1: 此前整体覆盖 next_regime, 通往上一档的路在破位那一刻被删掉。
    阶梯的**形状**(还有上行两档) 该活下来, 具体价位不该。
    : reclaim 落地 cap = NOTIONAL_CAP 满额 (用户「不再有 2% 的限制」),
    unlocks 死字段不再写入。"""
    a = _down({k: (v.copy() if isinstance(v, dict) else v) for k, v in LADDER.items()})
    nr = a["next_regime"]
    assert nr["name"] == "reclaim_176" and nr["cap_pct_nav"] == sz.NOTIONAL_CAP
    assert "unlocks" not in nr["entry_gate"], "unlocks 死字段 2026-08-13 停写"
    kept = nr["preserved_upper_ladder"]
    assert kept["name"] == "C_confirmed_reclaim_234"
    assert kept["stale"] is True, "保留的是 tier 结构, 不是这些数字"
    assert "待重画" in kept["note"]


def test_reclaim_regime_carries_next_gate_preview():
    """缺口 2: reclaim_* 此前不带 preview, 收复之后 next_regime = None ——
    爬回原位却头顶一个 gate 都没有。"""
    from quant.decision import regime_transition as rt
    a = _down({k: (v.copy() if isinstance(v, dict) else v) for k, v in LADDER.items()})
    assert a["next_regime"]["next_gate_preview"] is not None

    # 收复 176 → 升档, 头顶必须还有 gate
    up = rt.apply_transition({"agent": a},
                             {"ticker": "T", "date": "2026-08-05", "direction": "up",
                              "level": 176.0, "days": 2, "reason": "收复 176 ×2"},
                             klines=[])["agent"]
    assert up["next_regime"] is not None, "爬回来之后阶梯断了"
    assert up["next_regime"]["entry_gate"]["level"] == 234


def test_preserved_ladder_numbers_are_marked_stale_not_trusted():
    """带走的是形状。价位标 stale, 等 /trading-analysis 按新结构重画 ——
    234 是"7/21"这个日期的属性, 不是标的的属性。"""
    a = _down({k: (v.copy() if isinstance(v, dict) else v) for k, v in LADDER.items()})
    assert a["next_regime"]["next_gate_preview"]["stale"] is True


def test_card_without_upper_ladder_still_works():
    a = _down({"zones": {"stop_level": 176.0}, "action": {},
               "last_regime": {"name": "obs", "cap_pct_nav": 0,
                               "zones": {"stop_level": 154}}})
    assert a["next_regime"]["preserved_upper_ladder"] is None
    assert a["next_regime"]["next_gate_preview"] is None


# ── 冲击预算框架 (接线) ────────────────────────────────

def _book(**tickers):
    return {"tickers": tickers}


def _stock(qty, px):
    return {"instrument": {"type": "stock"},
            "broker": {"qty": qty, "current_price": px}}


def _call(qty, opt_px, und="GOOGL", strike=390.0):
    return {"instrument": {"type": "option",
                           "option": {"underlying": und, "strike": strike,
                                      "expiry": "2027-01-15", "side": "C"}},
            "broker": {"qty": qty, "current_price": opt_px}}


def test_option_without_delta_conservative_on_shock_budget_only():
    """期权拿不到 delta → 冲击占用按 delta=1.0 保守计并播报 (assumed_full_delta);
    **gross 不受 delta 影响**—  用户 裁决 gross = premium 市值
    (「押进去多少钱」, 损失有界=保费), 方向杠杆的信息保留在冲击预算口径。"""
    nav = 21468.54
    u = sz.book_usage(_book(GOOGL270115C390000=_call(1, 22.625)), nav,
                      price_of=lambda t: 357.75, atr_of=lambda t: 12.84,
                      delta_of=lambda t: None)
    assert u["assumed_full_delta"] == ["GOOGL270115C390000"]
    assert u["used_gross"] == pytest.approx(100 * 22.625 / nav, rel=1e-6)  # premium 市值
    # 冲击占用走 delta=1.0 的标的名义: w=100×357.75/nav, occ = w×1.88×(12.84/357.75)
    assert u["used_budget"] == pytest.approx(
        (100 * 357.75 / nav) * 1.88 * (12.84 / 357.75), rel=1e-6)


def test_option_dual_basis_gross_premium_shock_delta():
    """双口径: gross = premium 市值 (delta 无关); 冲击占用 = delta 名义 × X × ATR/价。"""
    nav = 21468.54
    u = sz.book_usage(_book(GOOGL270115C390000=_call(1, 22.625)), nav,
                      price_of=lambda t: 357.75, atr_of=lambda t: 12.84,
                      delta_of=lambda t: 0.427)
    assert u["assumed_full_delta"] == []
    assert u["used_gross"] == pytest.approx(100 * 22.625 / nav, rel=1e-6)
    assert u["used_budget"] == pytest.approx(
        (0.427 * 100 * 357.75 / nav) * 1.88 * (12.84 / 357.75), rel=1e-6)


def test_missing_atr_is_reported_not_silently_zero():
    """拿不到 ATR → 该笔不计入预算占用, 但必须进 missing_atr 让人看见。"""
    u = sz.book_usage(_book(AAOI=_stock(4, 120.0)), 21468.54,
                      price_of=lambda t: 120.0, atr_of=lambda t: None)
    assert u["missing_atr"] == ["AAOI"]
    assert u["used_budget"] == 0.0
    assert u["used_gross"] > 0            # gross 仍然算得出来


def test_unmanaged_positions_stay_out_of_every_risk_reading():
    """结构豁免层不进任何风控口径 (position-tiers.md §base)。"""
    body = _stock(100, 50.0)
    body["instrument"] = {"type": "stock", "role": "commemorative_hold"}
    u = sz.book_usage(_book(SPCX=body), 21468.54,
                      price_of=lambda t: 50.0, atr_of=lambda t: 2.0)
    assert u["used_gross"] == 0.0 and u["used_budget"] == 0.0


def test_shock_occupancy_formula():
    assert sz.shock_occupancy(0.40, 10.0, 100.0) == pytest.approx(0.40 * sz.SHOCK_X * 0.10)


def test_shock_budget_cap_returns_none_not_infinity_on_missing_data():
    """数据不足时**不能静默当"无限制"**—— 那会让约束消失。"""
    out = sz.shock_budget_cap(21468.54, atr=None, price=100.0)
    assert out["budget_cap"] is None
    assert out["gross_cap"] is not None          # gross 不依赖 ATR, 仍然算得出


def test_framework_constants_are_pinned():
    assert sz.NOTIONAL_CAP == 0.40
    assert sz.SHOCK_BUDGET == 0.15
    assert sz.SHOCK_X == 1.88
    assert sz.GROSS_CAP == 1.10


def test_option_delta_falls_back_to_broker_block(): # noqa: D103
    """delta 解析顺序 (冲击口径用): 注入 → broker.delta (sync 落库真 greeks) → 1.0。"""
    nav = 21468.54
    body = _call(1, 22.625)
    body["broker"]["delta"] = 0.41
    u = sz.book_usage(_book(GOOGL270115C390000=body), nav,
                      price_of=lambda t: 357.75, atr_of=lambda t: 12.84)
    assert u["assumed_full_delta"] == []
    assert u["used_budget"] == pytest.approx(
        (0.41 * 100 * 357.75 / nav) * 1.88 * (12.84 / 357.75), rel=1e-6)
    assert u["used_gross"] == pytest.approx(100 * 22.625 / nav, rel=1e-9)


# ── 实际 cap (用户 : 2%/40% 是名义, 实际要算) ─────────────

NAV = 21237.0


def test_available_cap_classification_binds_on_an_empty_book():
    """空书 + 低波动 → 组合层给的额度远大于 regime cap, 名义值就是实际值。"""
    ac = sz.available_cap(NAV, 0.02, atr=3.0, price=100.0, usage=None)
    assert ac["binding"] == "regime"
    assert ac["cap_usd"] == pytest.approx(NAV * 0.02)
    assert ac["cap_frac"] == pytest.approx(0.02)


def test_available_cap_gross_room_binds_and_is_named():
    """gross 已用 75% → 余 35%; 一张 40% 的卡实际只给 35%。

    这正是 用户  那句话的机械形态: 「实际情况需要靠计算得出 ——
    依赖 NAV 15% 的回撤幅度与 gross 110% 算出余额, 才是实际 cap」。
    binding 必须报出来, 否则无法解释 40% 的卡为什么只给了 35%。
    """
    ac = sz.available_cap(NAV, sz.NOTIONAL_CAP, atr=3.0, price=100.0,
                          usage={"used_budget": 0.06, "used_gross": 0.75})
    assert ac["binding"] == "gross"
    assert ac["gross_room"] == pytest.approx(sz.GROSS_CAP - 0.75)
    assert ac["cap_frac"] == pytest.approx(sz.GROSS_CAP - 0.75)
    assert ac["cap_usd"] < NAV * sz.NOTIONAL_CAP


def test_available_cap_shock_budget_binds_on_a_high_vol_name():
    """高波动票吃预算快 —— ATR/价 13% 时 15% 预算只装得下 ~61% NAV,
    书里已用掉大半后就轮到它 binding, 而不是 40% 名义上限。"""
    ac = sz.available_cap(NAV, sz.NOTIONAL_CAP, atr=13.0, price=100.0,
                          usage={"used_budget": 0.12, "used_gross": 0.20})
    assert ac["binding"] == "shock_budget"
    expect = NAV * (sz.SHOCK_BUDGET - 0.12) / (sz.SHOCK_X * 0.13)
    assert ac["cap_usd"] == pytest.approx(expect)


def test_available_cap_missing_nav_is_unknown_not_unlimited():
    ac = sz.available_cap(None, 0.02, atr=3.0, price=100.0)
    assert ac["cap_usd"] is None and ac["binding"] is None


def test_available_cap_without_atr_still_applies_gross():
    """缺 ATR → 冲击预算那一条算不出, 但 gross 不依赖 ATR, 不能一起消失。"""
    ac = sz.available_cap(NAV, sz.NOTIONAL_CAP, atr=None, price=100.0,
                          usage={"used_budget": 0.0, "used_gross": 1.05})
    assert ac["shock_budget_cap"] is None
    assert ac["binding"] == "gross"
    assert ac["cap_usd"] == pytest.approx(NAV * (sz.GROSS_CAP - 1.05))


def test_notional_to_premium_cap_is_the_unit_bridge():
    """名义上限 → 保费预算。不换算 = 名义敞口数倍于上限 (方向是放松约束)。"""
    # Δ0.41, spot 354, premium 20.5: 一张名义 $14,514 / 花 $2,050
    prem = sz.notional_to_premium_cap(7400.0, 0.41, 354.0, 20.5)
    assert prem == pytest.approx(7400.0 * 20.5 / (0.41 * 354.0))
    # 折算后买得起 0.5 张 —— 直接把 $7,400 当保费花会买到 3 张 = 名义 $43.5k
    assert prem / (20.5 * 100) < 1.0
    assert 7400.0 / (20.5 * 100) > 3.0


def test_notional_to_premium_cap_refuses_to_guess():
    assert sz.notional_to_premium_cap(7400.0, None, 354.0, 20.5) is None
    assert sz.notional_to_premium_cap(7400.0, 0.41, None, 20.5) is None
    assert sz.notional_to_premium_cap(None, 0.41, 354.0, 20.5) is None


def test_usage_lines_report_both_data_gaps_separately():
    """两个缺口方向相反 (delta 缺 → 高估敞口; ATR 缺 → 低估预算), 不能合并成一句。"""
    out = sz.usage_lines({"used_budget": 0.059, "used_gross": 0.751,
                          "assumed_full_delta": ["XC"], "missing_atr": ["YY"]}, NAV)
    assert "冲击预算 5.90%/15% NAV" in out[0] and "gross 75.1%/110% NAV" in out[0]
    assert any("敞口被高估" in x and "XC" in x for x in out[1:])
    assert any("预算被低估" in x and "YY" in x for x in out[1:])
    assert sz.usage_lines(None, NAV) == [] and sz.usage_lines({}, None) == []


def test_schema_ceiling_tracks_the_real_source_not_a_copy():
    """schema 曾把 0.05 抄了一份, 于是 confirmed 改成 40% 那一刻它把重画全拦死。"""
    from quant.core import schema
    assert schema.CAP_CONFIRMED_MAX == sz.NOTIONAL_CAP


# ── Campaign 风险预算递减分配 (risk_allocation_design  三点锁定) ────────

def test_gate_risk_caps_locked_values():
    """用户  逐字:「gate1 max 2.5% gate2 max 4% gate3 max 5% 就这样」。

    这张表是**累计上限**不是每档增量。来历仍是 08-10 的 T × 50/30/20 —— 那条
    provenance 在这里钉住, 否则换成累加式表达之后就没人记得这三个数是怎么来的。
    """
    from quant.core.sizing import (AT_RISK_CAP_FRAC, GATE_RISK_CAPS,
                                   gate_risk_cap_frac, gate_step_frac)
    assert GATE_RISK_CAPS == (0.025, 0.040, 0.050)
    # provenance: T = AT_RISK_CAP_FRAC, 分批 50/30/20 后累加
    steps = [AT_RISK_CAP_FRAC * f for f in (0.5, 0.3, 0.2)]
    assert list(GATE_RISK_CAPS) == pytest.approx(
        [sum(steps[:i + 1]) for i in range(3)])
    # 顶就是 T —— 一个 campaign 总共冒的险 = 单笔 at-risk 天花板, 同一个数
    assert GATE_RISK_CAPS[-1] == pytest.approx(AT_RISK_CAP_FRAC)
    assert [gate_risk_cap_frac(g) for g in (0, 1, 2, 3, 4)] == \
        pytest.approx([0.0, 0.025, 0.040, 0.050, 0.050])   # 第 4 个 gate 停在 T, 不归零
    # 增量只给期权保费用 (用户: 期权不跟结转)
    assert [gate_step_frac(g) for g in (1, 2, 3, 4)] == \
        pytest.approx([0.025, 0.015, 0.010, 0.0])
    # #2B 递减律由形状自动满足 (每档 ≤ 前一档 75%)
    assert 0.015 / 0.025 <= 0.75 and 0.010 / 0.015 <= 0.75


def test_gate_count_counts_only_cap_granting_gates_since_the_break():
    """用户 :「不是任何一个 gate 都算次数, 只有给 cap 的 gate 才开始算。」

    判据 = 卡上的 `regime_history`, **不是 lots**—— 「过了几道线」写在转移史里,
    不写在成交记录里 (旧 lots 近似的两个洞: 数不出第三档 / 没买过就整档丢失)。
    """
    from quant.core.sizing import gate_count
    up = lambda d, cap=0.40: {"date": d, "direction": "up", "cap_pct_nav": cap}   # noqa: E731
    assert gate_count(None) == 1                                  # 无卡 = 第一档
    assert gate_count({"agent": {}}) == 1                         # 常规参与: 没爬过线也有资格
    assert gate_count({"agent": {"regime_history": [up("2026-08-04")]}}) == 1
    assert gate_count({"agent": {"regime_history": [
        up("2026-08-04"), up("2026-08-12")]}}) == 2               # LITE 形状: 没买过也攒
    assert gate_count({"agent": {"regime_history": [
        up("2026-08-01"), up("2026-08-04"), up("2026-08-12"), up("2026-08-20")]}}) == 3   # 封顶
    # **cap 0 的 gate 不算次数**(修复路上的中间站)
    assert gate_count({"agent": {"regime_history": [
        up("2026-08-04", cap=0.0), up("2026-08-12")]}}) == 1
    assert gate_count({"agent": {"regime_history": [
        up("2026-08-04", cap=None), up("2026-08-12")]}}) == 1
    # 老条目 (无 cap_pct_nav) 计入 —— live 10 条已逐条查实为授权转移, 且是封闭集合
    assert gate_count({"agent": {"regime_history": [
        {"date": "2026-08-04", "direction": "up"},
        {"date": "2026-08-12", "direction": "up"}]}}) == 2
    # direction 缺失 = 数不出来 → 不计
    assert gate_count({"agent": {"regime_history": [
        {"date": "2026-08-04"}, up("2026-08-12")]}}) == 1


def test_gate_count_resets_on_breakdown_and_on_close():
    """参与流被打断 = 从第一档重来: 破位 (down 转移) 与平仓 (exit_info.closed_at)。
    平仓那条是 用户  早前的裁决「平仓 = campaign 结束」的同一件事。"""
    from quant.core.sizing import gate_count
    up = lambda d: {"date": d, "direction": "up", "cap_pct_nav": 0.40}   # noqa: E731
    down = lambda d: {"date": d, "direction": "down", "cap_pct_nav": 0.0}  # noqa: E731
    two_ups = {"agent": {"regime_history": [up("2026-08-04"), up("2026-08-12")]}}
    assert gate_count(two_ups) == 2
    # 中间破位 → 只数破位之后的
    assert gate_count({"agent": {"regime_history": [
        up("2026-08-04"), down("2026-08-08"), up("2026-08-12")]}}) == 1
    # 平仓 → 同样归零 (AAOI 形状: 8/12 13:29 ET 平仓, 当晚转移 R_突破_133)
    assert gate_count({**two_ups,
                       "exit_info": {"closed_at": "2026-08-12T13:29:54-04:00"}}) == 1
    # 平仓在更早 → 之后的 gate 照数
    assert gate_count({**two_ups,
                       "exit_info": {"closed_at": "2026-08-03T08:15:50-04:00"}}) == 2
    # 🚨 **平仓日在两个桶里记在不同字段**, 两个都要认: positions 的 closed 卡记
    # `exit_info.closed_at`, 而 watchlist 回来的候选卡记 `readd_from_position.
    # closed_at` —— grant/entry_guard 对未持仓票读的**正是后者**。只认前者时
    # AAOI (8/12 平仓 + 当晚转移) 在 live 上被数成 gate2 = 多给一整档。
    assert gate_count({**two_ups, "readd_from_position": {
        "closed_at": "2026-08-12T13:29:54.072000-04:00"}}) == 1
    assert gate_count({**two_ups, "readd_from_position": {
        "closed_at": "2026-08-03T08:15:50-04:00"}}) == 2


def test_binding_stop_takes_max_and_never_counts_2n_as_a_line():
    """用户  「stop 是多者取 max」+ 2N 只兜底不进 max。"""
    from quant.core.sizing import binding_stop
    # 卡 stop 90 / 棘轮地板 95 / trail 93 → 95 先被打到
    assert binding_stop(100.0, [90.0, 95.0, 93.0])[0] == 95.0
    # 卡 stop 比 2N 更远 (85 vs 100−2×4=92): 用**卡 stop**, 不许拿 92 去量风险
    stop, basis = binding_stop(100.0, [85.0], atr=4.0)
    assert stop == 85.0 and basis == "card/lock/trail max"
    # 一条真 stop 都没有 → 2N 兜底, 且 basis 说出口
    stop2, basis2 = binding_stop(100.0, [None], atr=4.0)
    assert stop2 == 92.0 and "2N 兜底" in basis2
    # 无效候选 (≥价 / ≤0) 不算数
    assert binding_stop(100.0, [120.0, -3.0], atr=None)[0] is None


def test_retired_round_helpers_are_gone():
    """lots 推轮号的两个近似  退役 —— 判据换成数卡上的 gate。
    源码棘轮: 它们回来就意味着"过没过 gate"又开始从成交记录里猜。"""
    for name in ("round_no_from_lots_since", "campaign_round_no",
                 "lots_deployed_since", "round_risk_frac", "ROUND_RISK_FRACS",
                 "unlocked_risk_frac", "round_risk_notional_cap",
                 "unlocked_risk_notional_cap", "OPTION_MIN_ROUND"):
        assert not hasattr(sizing, name), f"sizing.{name} 该随 gate 语义退役"


# ── entry_cap: 「这一笔实际能下多少」的单一真源 (用户  Q2) ──────────

FULL = {"current_regime": {"name": "常规参与", "cap_pct_nav": 0.40}}


def test_entry_cap_takes_the_min_of_six_arms_and_names_the_binder():
    """六条臂取小 + binding 必须说得出是谁 —— 「一张写 40% 的卡今天只给 34%」
    不写出来就没人解释得了。"""
    common = dict(atr=3.0, price=100.0, gate_no=1, stop=94.0)
    # 近 stop (距 6%): 轮预算 20000×2.5%×100/6 = 8,333 > regime 8,000 → regime 先咬
    free = sizing.entry_cap(20000.0, FULL, **common)
    assert free["binding"] == "regime"
    assert free["cap_usd"] == pytest.approx(8000.0)
    # 远 stop (距 25%) → 同一份风险预算只买得起 2,000, 轮预算先咬
    vol = sizing.entry_cap(20000.0, FULL, atr=3.0, price=100.0,
                           stop=75.0, gate_no=1)
    assert vol["binding"] == "n_unit"
    assert vol["cap_usd"] == pytest.approx(20000 * 0.025 * 100 / 25)
    # gross 打满 → gross 咬
    tight = sizing.entry_cap(20000.0, FULL,
                             usage={"used_budget": 0.0, "used_gross": 1.08}, **common)
    assert tight["binding"] == "gross" and tight["cap_usd"] == pytest.approx(400.0)
    # ⑥ 购买力: 零杠杆现金户里它比 gross 更紧 (用户 Q2 的立项理由)
    cash = sizing.entry_cap(20000.0, FULL, power_room=1234.0, **common)
    assert cash["binding"] == "power" and cash["cap_usd"] == pytest.approx(1234.0)


def test_entry_cap_power_arm_absent_is_not_unlimited():
    """power_room=None = 快照缺失, **不参与取小也不当无限制**—— 静默当无限制
    正是本仓反复出的失效形态。"""
    out = sizing.entry_cap(20000.0, FULL, atr=3.0, price=100.0)
    assert out["power_cap"] is None and out["binding"] == "regime"


def test_entry_cap_regime_arm_is_cumulative_per_ticker():
    """B7 (用户  A1): regime cap = **这个 ticker 总共能有多少**, 减掉
    当前敞口 —— 不再是"每轮重新给一份 40%"。已知后果 (用户 接受): 低波动票
    R1 就能吃掉整个 40%, campaign 只剩一轮。"""
    held = sizing.entry_cap(20000.0, FULL, atr=3.0, price=100.0,
                            stop=94.0, exposure=7000.0)
    assert held["regime_cap"] == pytest.approx(1000.0)   # 8000 − 7000
    assert held["binding"] == "regime" and held["cap_usd"] == pytest.approx(1000.0)
    # 打满 → 0, 不是负数, 也不是"这条臂消失"
    full = sizing.entry_cap(20000.0, FULL, atr=3.0, price=100.0,
                            stop=94.0, exposure=9999.0)
    assert full["regime_cap"] == 0.0 and full["cap_usd"] == 0.0


def test_entry_cap_nav_missing_returns_unknown_not_zero():
    out = sizing.entry_cap(None, FULL, atr=3.0, price=100.0)
    assert out["cap_usd"] is None and out["binding"] is None


def test_entry_cap_gate2_unlocks_the_cumulative_budget():
    """用户 :「gate2 max 4%」—— 同一张卡零敞口时 gate2 的 ② 臂 =
    2.5% 的 **1.6×**, 不是"第二轮增量 1.5%"(旧每轮独立规则 = 0.6×)。"""
    r1 = sizing.entry_cap(20000.0, FULL, atr=3.0, price=100.0, stop=75.0, gate_no=1)
    r2 = sizing.entry_cap(20000.0, FULL, atr=3.0, price=100.0, stop=75.0, gate_no=2)
    assert r1["binding"] == r2["binding"] == "n_unit"
    assert r2["cap_usd"] == pytest.approx(r1["cap_usd"] * 1.6)


def test_entry_cap_exposure_is_the_one_deduction_for_both_regime_and_gate_arms():
    """扣减口径 = **市值敞口**, 且 ①② 减的是同一个数 (用户  选市值,
    与 08-11 regime cap 臂同一条理由: 减数不能冻在成本上)。"""
    common = dict(atr=3.0, price=100.0, stop=75.0, gate_no=2)
    bare = sizing.entry_cap(20000.0, FULL, **common)
    held = sizing.entry_cap(20000.0, FULL, exposure=1000.0, **common)
    assert held["n_unit_cap"] == pytest.approx(bare["n_unit_cap"] - 1000.0)
    assert held["regime_cap"] == pytest.approx(bare["regime_cap"] - 1000.0)
    # 敞口吃光累计预算 → ② 归 **0.0 不是负数/None**(None 会让这条臂消失 = fail-open)
    maxed = sizing.entry_cap(20000.0, FULL, exposure=10**6, **common)
    assert maxed["n_unit_cap"] == 0.0 and maxed["cap_usd"] == 0.0


def test_entry_cap_gate_budget_never_exceeds_the_at_risk_ceiling():
    """② ≤ ③ 恒成立 (gate 上限的顶 = T = AT_RISK_CAP_FRAC), gate3 零敞口时相等
    —— ③ 在入场侧永不 binding 这条性质由构造保证, 不靠调参。"""
    for g in (1, 2, 3, 4):
        c = sizing.entry_cap(20000.0, FULL, atr=3.0, price=100.0, stop=75.0,
                             gate_no=g)
        assert c["n_unit_cap"] <= c["at_risk_cap"] + 1e-9, g
    r3 = sizing.entry_cap(20000.0, FULL, atr=3.0, price=100.0, stop=75.0, gate_no=3)
    assert r3["n_unit_cap"] == pytest.approx(r3["at_risk_cap"])


# ── IV 门的分位代理 + reversal_probe 倒推 cap + 盖章  ──

def test_long_call_iv_gate_hv_proxy_sits_between_percentile_and_absolute():
    """IV percentile 可信 > HV20 分位代理 > 绝对 IV 兜底 > 无数据; basis 必须说出来。"""
    g = sizing.long_call_iv_gate(12.0, True, 88.0, hv_pct=90.0)
    assert g["allowed"] and g["basis"] == "pct"                    # 可信 IVP 赢过代理
    g = sizing.long_call_iv_gate(3.0, False, 67.0, hv_pct=31.0)
    assert g["allowed"] and g["basis"] == "hv_pct" and "HV20 分位 31" in g["reason"]
    g = sizing.long_call_iv_gate(None, False, 30.0, hv_pct=55.0)
    assert not g["allowed"] and g["basis"] == "hv_pct"             # 代理分位高, 绝对 IV 低也不放
    g = sizing.long_call_iv_gate(None, False, 39.0, hv_pct=None)
    assert g["allowed"] and g["basis"] == "abs"                    # 没有任何分位才退绝对值
    assert sizing.long_call_iv_gate(None, False, None, hv_pct=None)["basis"] == "none"


def test_reversal_probe_premium_cap_is_gate1_risk_over_projected_loss():
    nav = 20000.0
    assert sizing.REVERSAL_PROBE_RISK_GATE == 1
    cap = sizing.reversal_probe_premium_cap(nav, 0.28)
    assert cap == pytest.approx(nav * sizing.gate_step_frac(1) / 0.28)
    assert sizing.reversal_probe_premium_cap(nav, 0.20) == pytest.approx(nav * 0.125)  # = gate 类 12.5% 那一点
    for bad in (None, 0.0, -0.1, 1.5):
        assert sizing.reversal_probe_premium_cap(nav, bad) is None
    assert sizing.reversal_probe_premium_cap(None, 0.3) is None


def test_derive_auth_reversal_probe_lives_in_option_plans_not_the_stock_card():
    cards = {"TST": {"agent": {"current_regime": {"name": "观察", "cap_pct_nav": 0.0}}}}
    plans = {"TST": {"entry_class": "reversal_probe", "code": "TST270115C90000",
                      "signal_date": "2026-08-25", "support": 78.4, "valid_until": "2026-08-28",
                      "cap_frac": 0.09}}
    auth, why = sizing.derive_auth("TST270115C90000", cards, today="2026-08-26", option_plans=plans)
    assert auth == {"cap_pct_nav": 0.09, "gate_no": 1, "entry_class": "reversal_probe",
                    "support": 78.4, "signal_date": "2026-08-25", "vehicle": "call"}
    assert "reversal_probe" in why and "止跌低 78.4" in why
    # 正股腿: key = 正股本身, cap 取 plan.stock.cap_frac; 没有 stock 段的计划正股不沾光
    auth, _ = sizing.derive_auth("TST", cards, today="2026-08-26", option_plans=plans)
    assert auth is None
    plans_s = {"TST": {**plans["TST"], "stock": {"tiers": [{"price": 80.0, "shares": 5}], "cap_frac": 0.05}}}
    auth, why = sizing.derive_auth("TST", cards, today="2026-08-26", option_plans=plans_s)
    assert auth == {"cap_pct_nav": 0.05, "gate_no": 1, "entry_class": "reversal_probe",
                    "support": 78.4, "signal_date": "2026-08-25", "vehicle": "正股"}
    assert sizing.derive_auth("TST", cards, today="2026-08-31", option_plans=plans_s)[0] is None
    auth, why = sizing.derive_auth("TST270115C90000", cards, today="2026-08-31", option_plans=plans)
    assert auth is None and "过期" in why                          # 过期成交 = 无授权
    auth, _ = sizing.derive_auth("TST270115C95000", cards, today="2026-08-26", option_plans=plans)
    assert auth is None                                            # 别的合约不沾光, 退回正股卡 cap 0
    auth, _ = sizing.derive_auth("TST270115C90000", cards, today="2026-08-26", option_plans=None)
    assert auth is None
