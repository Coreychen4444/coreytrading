"""远端 gate 候选（`core/future_gates`）—— 卡片阶梯之上的机械地标。

钉五件事：只出阶梯顶之上的多触碰簇上沿 / 与阶梯顶同一个地标不画两遍 / 不看现价（线不因穿越消失）/
窗口最高价单触碰也算 / 判读件不 import 授权模块、口径与 bottom_evidence 同一份。
"""
import ast
import datetime as dt
from pathlib import Path

from quant.core import bottom_evidence as BE
from quant.core import future_gates as FG


def _bar(i, o, h, l, c, v=1000):
    t = (dt.date(2025, 1, 1) + dt.timedelta(days=i)).isoformat()
    return {"time": f"{t} 00:00:00", "open": o, "high": h, "low": l, "close": c,
            "volume": v, "turnover": c * v}


def _series(peaks, base=100.0, n=120):
    """平底 base 上按 {bar 序号: 高点} 插 5 根分形尖峰 (两侧各 2 根更低 = swing high)。
    每根真实波幅 ≈2 ⇒ ATR14 ≈2、聚类容差 ≈0.7、桶宽 ≈0.5。"""
    bars = [_bar(i, base, base + 1, base - 1, base) for i in range(n)]
    for i, hi in peaks.items():
        bars[i] = _bar(i, base, hi, base - 1, base)
    return bars


def test_only_multi_touch_clusters_above_the_ladder_top_in_ascending_order():
    bars = _series({10: 110.0, 20: 110.4,            # 簇 ×2 上沿 110.4
                    30: 120.0, 40: 120.3, 50: 120.5,  # 簇 ×3 上沿 120.5
                    60: 115.0,                        # 单触碰 → 不是地标
                    70: 105.0, 80: 105.2})            # 簇 ×2, 在阶梯顶之下
    out = FG.candidates(bars, above=107.0, n=5)
    assert [(c["level"], c["touches"], c["source"]) for c in out] == [
        (110.4, 2, "cluster"), (120.5, 3, "cluster")]   # 105.2 在下方 / 115 单触碰 / 120.5 = 窗高已被簇覆盖
    assert FG.candidates(bars, above=107.0, n=1)[0]["level"] == 110.4      # 由近及远截断
    assert FG.candidates(bars, above=107.0, n=0) == []


def test_same_landmark_as_the_ladder_top_is_not_drawn_twice():
    bars = _series({10: 110.0, 20: 110.4, 30: 120.0, 40: 120.5})
    # 阶梯顶就是簇上沿 (一个桶宽内) → 该簇不再出
    assert [c["level"] for c in FG.candidates(bars, above=110.3, n=5)] == [120.5]
    # 阶梯顶画在簇**内** (agent 常见画法) → 同一堵墙, 不再出上沿
    # 簇 110.0–110.9 ×3: 上沿 110.9 > 110.2 + 桶宽 0.5, 只有「簇带盖住阶梯顶」这条能挡住它
    wide = _series({10: 110.0, 20: 110.6, 25: 110.9, 30: 120.0, 40: 120.5})
    assert [c["level"] for c in FG.candidates(wide, above=110.2, n=5)] == [120.5]
    assert [c["level"] for c in FG.candidates(wide, above=109.0, n=5)] == [110.9, 120.5]   # 顶在簇下方 → 照出


def test_spot_does_not_move_the_ladder():
    """线不因价格穿越而消失: 现价越过候选之后候选原地不动。"""
    bars = _series({10: 110.0, 20: 110.4, 30: 120.0, 40: 120.5})
    before = [c["level"] for c in FG.candidates(bars, above=105.0, n=5)]
    ran = bars + [_bar(120 + i, 116, 117, 115, 116) for i in range(6)]      # 现价 116 > 110.4
    assert [c["level"] for c in FG.candidates(ran, above=105.0, n=5)] == before == [110.4, 120.5]


def test_window_high_counts_even_with_a_single_touch():
    bars = _series({10: 110.0, 20: 110.4, 60: 130.0})
    out = FG.candidates(bars, above=105.0, n=5)
    assert [(c["level"], c["source"]) for c in out] == [(110.4, "cluster"), (130.0, "window_high")]
    assert FG.evidence_tag(out[1]) == "窗高"
    assert FG.evidence_tag(out[0]).startswith("簇×2H")
    assert all("/" not in FG.evidence_tag(c) for c in out)        # '/' 是备注并入分隔符


def test_rejections_use_the_level_interactions_rule():
    bars = _series({10: 110.0, 20: 110.4})
    c = FG.candidates(bars, above=105.0, n=1)[0]
    assert c["rejections"] == 2            # 两根尖峰都够到 110.4 下一个桶宽、收盘都没站上


def test_too_little_history_or_no_axis_gives_nothing():
    assert FG.candidates(_series({10: 110.0, 20: 110.4}, n=40), above=105.0, n=3) == []
    assert FG.candidates(_series({10: 110.0, 20: 110.4}), above=None, n=3) == []


def test_ladder_top_prefers_live_preview_and_skips_stale_one():
    card = {"next_regime": {"entry_gate": {"level": 100.0},
                            "next_gate_preview": {"level": 110.0}}}
    assert FG.ladder_top(card) == 110.0
    card["next_regime"]["next_gate_preview"]["stale"] = True      # 降级时搬运的旧阶梯: 不当锚
    assert FG.ladder_top(card) == 100.0
    assert FG.ladder_top({"next_regime": {"entry_gate": {"level": 100.0}}}) == 100.0
    assert FG.ladder_top({"next_regime": None}) is None and FG.ladder_top(None) is None


def test_for_card_numbers_on_from_the_card_ladder():
    """编号是两个消费者 (moomoo 备注 `gate:+N` / TG 阶梯表 `远端 +N`) 对得上号的唯一依据。"""
    bars = _series({10: 110.0, 20: 110.4, 30: 120.0, 40: 120.5, 60: 130.0})
    with_pv = {"next_regime": {"entry_gate": {"level": 104.0},
                               "next_gate_preview": {"level": 106.0}}}
    assert [(c["n"], c["level"]) for c in FG.for_card(with_pv, bars)] == [
        (3, 110.4), (4, 120.5), (5, 130.0)]
    with_pv["next_regime"]["next_gate_preview"]["stale"] = True
    assert [(c["n"], c["level"]) for c in FG.for_card(with_pv, bars)] == [
        (2, 110.4), (3, 120.5), (4, 130.0)]
    assert len(FG.for_card(with_pv, bars)) <= FG.FUTURE_GATES_MAX
    assert FG.for_card({"zones": {}}, bars) == []                 # 没有 next gate = 没有轴可接


def test_calibre_is_shared_not_copied_and_module_grants_nothing():
    """同概念双判别式是本仓惯犯形状 —— 簇口径必须是 bottom_evidence 那一份对象, 不是抄来的数;
    判读件不许 import 任何授权 / sizing / 转移模块。"""
    assert FG.CLUSTER_TOL_ATR14 is BE.CLUSTER_TOL_ATR14
    assert FG.MIN_TOUCHES is BE.MIN_TOUCHES and FG.PIVOT_LOOKBACK is BE.PIVOT_LOOKBACK
    tree = ast.parse(Path(FG.__file__).read_text())
    mods = {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert mods <= {"__future__", "typing", "", "bottom_evidence", "setups"}, mods
