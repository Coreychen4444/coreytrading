"""财报缩仓 T-1 减仓信号 + 日历陈旧告警 (ledger A2 + BACKLOG §3.5-4,)。

earnings_window 裁决「反应日前一交易日收盘前减完」此前只有入场半边
(available_cap 的 event_shrink 只压新买)。这里钉减仓半边:
  1. 只在 T-1 响 (T-2 早, T+0 晚 — AMC print 在 T-1 盘后)
  2. 周五 → 周一反应日照样算 T-1 (工作日口径)
  3. 敞口 ≤ 缩仓后上限不响; 基数 = **名义上限 40% × NAV**, 不是 regime cap
  4. 日历陈旧 > 2 个工作日 → INFRA 告警 (周末不累积)

首个 live 案例 = LITE  (反应日 8/12, X_earn 5.56N → ×0.24)。

§基数 (修, COHR 实证) 钉的是: **regime cap 不得进这条计算**。
regime cap 答"能不能进/还能加多少", 本条答"那一天能有多大" —— 两个轴。
"""
import json

import pytest

from quant.core import earnings
from quant.core.sizing import NOTIONAL_CAP
from quant.report import portfolio_brief as pb

# LITE 实卡数字: X_earn 5.56N / 19 事件 → shrink = 1.88/5.56 × 0.7 ≈ 0.2367
CAL = {"as_of": "2026-08-08",
       "tickers": {"LITE": {"next_reaction_day": "2026-08-12", "x_earn": 5.56,
                            "n_prior": 19}}}
ITEM = {"ticker": "LITE", "qty": 4, "current_price": 100.0}
NAV = 22000.0


def test_fires_on_t_minus_1_with_notional_cap_math():
    big = {"ticker": "LITE", "qty": 100, "current_price": 100.0}     # $10k 敞口
    sig = pb.earnings_trim_signal(big, CAL, "2026-08-11", nav=NAV)
    assert sig is not None
    shrink = earnings.shrink_ratio(5.56)
    assert sig["cap_usd"] == pytest.approx(NAV * NOTIONAL_CAP * shrink, abs=0.01)
    assert sig["exposure_usd"] == pytest.approx(10_000.0)
    assert sig["excess_usd"] == pytest.approx(10_000.0 - NAV * NOTIONAL_CAP * shrink,
                                              abs=0.01)
    assert sig["trim_units"] == 80                     # ceil(7,917.4 / 100)
    assert sig["cap_basis"] == "名义上限 40%"


def test_silent_on_t_minus_2_and_t_plus_0():
    big = {"ticker": "LITE", "qty": 100, "current_price": 100.0}
    assert pb.earnings_trim_signal(big, CAL, "2026-08-10", nav=NAV) is None  # T-2
    assert pb.earnings_trim_signal(big, CAL, "2026-08-12", nav=NAV) is None  # T+0 归缩仓行


def test_friday_counts_as_t_minus_1_for_monday_reaction():
    cal = {"tickers": {"X": {"next_reaction_day": "2026-08-17"}}}   # 周一
    item = {"ticker": "X", "qty": 100, "current_price": 100.0}
    assert pb.earnings_trim_signal(item, cal, "2026-08-14",
                                   nav=NAV) is not None              # 周五 = T-1
    # 周四不是: 中间还隔着周五一个工作日
    assert pb.earnings_trim_signal(item, cal, "2026-08-13", nav=NAV) is None


def test_under_cap_is_silent():
    assert pb.earnings_trim_signal(ITEM, CAL, "2026-08-11", nav=NAV) is None  # $400


def test_no_nav_is_silent_not_invented():
    """基数只有一个来源, 它拿不到 = 不知道, 不编数。"""
    big = {"ticker": "LITE", "qty": 100, "current_price": 100.0}
    assert pb.earnings_trim_signal(big, CAL, "2026-08-11", nav=None) is None


def test_option_rows_use_premium_units_for_exposure():
    cal = {"tickers": {"LITE": {"next_reaction_day": "2026-08-12", "x_earn": 5.56}}}
    opt = {"ticker": "LITE260918C100000", "underlying": "LITE", "qty": 4,
           "current_price": 80.0}                                   # 保费市值 $32k
    sig = pb.earnings_trim_signal(opt, cal, "2026-08-11", nav=NAV)
    assert sig is not None and sig["is_option"]
    assert sig["exposure_usd"] == pytest.approx(32_000.0)
    assert sig["trim_units"] == 4                                   # 张
    # ⚠️ 基数仍是标的名义 —— 期权单位偏松是已知未修 (docstring), 不是没想到


def test_no_calendar_row_is_silent():
    assert pb.earnings_trim_signal({"ticker": "AAOI", "qty": 4, "current_price": 120.0},
                                   CAL, "2026-08-11", nav=NAV) is None


# ── §基数: cap 0 的持仓卡 (回归, COHR 实证) ──────────────

def test_cap_zero_holding_does_not_collapse_target_to_zero(tmp_path, monkeypatch):
    """cap 0 的持仓卡**不得**被算成"反应日必须清仓"。

     live: COHR 卡 [持仓·无加仓授权] cap 0, 20 股 $6,854, 反应日 8/13
    → 告警播的是"减 $6,854 ≈ 20 股" = 清仓。裁决口径 (名义上限 40% × NAV × 0.44)
    是减到 ≈$3,534 ≈ 10 股。

    病根 = 基数取了 `position_budget.class_cap_usd`, 而 08-13 分类轴退役当天
    `position_budget` 改成 cap 0 也照出预算行 (0.0 而不是 None) → 为 cap 0 写的
    `if base is None` 回退再没触发过。本用例连着 `position_budget` 一起跑,
    所以任何人把基数重新接回预算行, 这里当场红。
    """
    events = tmp_path / "events"
    (events / "2026-08").mkdir(parents=True)
    (events / "2026-08" / "trades.jsonl").write_text(json.dumps(
        {"event_type": "fill", "ticker": "COHR", "side": "BUY", "qty": 10.0,
         "price": 360.0, "tier": "position"}), encoding="utf-8")
    monkeypatch.setattr(pb, "EVENTS_DIR", events)
    monkeypatch.setattr(pb, "KLINE_CACHE_DIR", tmp_path / "klines")

    nav, cal = 20076.76, {"tickers": {"COHR": {"next_reaction_day": "2026-08-13",
                                               "x_earn": 3.0}}}
    item = {"ticker": "COHR", "qty": 20.0, "current_price": 342.675,
            "synthetic": False}
    held_card = {"current_regime": {"name": "持仓·无加仓授权", "cap_pct_nav": 0}}

    # 前提复现: cap 0 的持仓卡照出预算行, class_cap_usd 是 0.0 而**不是**None
    budget = pb.position_budget("COHR", item, nav, held_card, calendar=cal)
    assert budget is not None and budget["class_cap_usd"] == 0.0

    sig = pb.earnings_trim_signal(item, cal, "2026-08-12", nav=nav)
    shrink = earnings.shrink_ratio(3.0)                       # 1.88/3.0 × 0.7 ≈ 0.4387
    assert sig is not None                                    # 仍然响 —— 修的是量级不是静音
    assert sig["cap_usd"] == pytest.approx(nav * NOTIONAL_CAP * shrink, abs=0.01)
    assert sig["cap_usd"] > 0                                 # ← 回归点: 不是 $0
    assert sig["trim_units"] == 10                            # 不是 20 = 清仓
    assert sig["excess_usd"] < sig["exposure_usd"]            # 减的不是全部敞口


# ── 日历陈旧 INFRA (§3.5-4) ─────────────────────────────────────

def test_calendar_stale_note_weekday_arithmetic():
    # 周五建 → 周一读 = 1 个工作日: 不响 (周末不累积)
    assert pb.calendar_stale_note({"as_of": "2026-08-07"}, "2026-08-10") is None
    # 周五建 → 周三读 = 3 个工作日 > 2: 响
    note = pb.calendar_stale_note({"as_of": "2026-08-07"}, "2026-08-12")
    assert note and "3 个工作日" in note
    # 恰好 2 个工作日: 不响 (阈值含 2)
    assert pb.calendar_stale_note({"as_of": "2026-08-07"}, "2026-08-11") is None


def test_calendar_missing_or_unparsable_rings():
    assert "缺失" in pb.calendar_stale_note({}, "2026-08-11")
    assert "缺失" in pb.calendar_stale_note(None, "2026-08-11")
    assert pb.calendar_stale_note({"as_of": "garbage"}, "2026-08-11") is not None
