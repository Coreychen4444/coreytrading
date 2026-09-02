"""exit_guard 核心算钱逻辑回归 — 源案例 NOK270115C15000 (episode C):
G-01 +100% 触发未执行 + 6/26 power $655 静默 → margin 强平 -$2,295。
这些测试锁定: 棘轮地板只升不降、两段式触发一次性、趋势线 ratchet、TR 抓跳空。"""
import json

import pytest

from quant.decision import exit_guard
from quant.core import technical
from quant.decision.exit_guard import compute_floor, compute_trail, g01_crossings, PARAMS, parse_risk_level, process_reentry_watches


def _bar(t, o, h, l, c):
    return {"time": t, "open": o, "high": h, "low": l, "close": c}


# ── ATR / True Range ────────────────────────────────────────────

def test_true_range_captures_gap_down():
    # NOK  → 06-05 实盘: prevC 16.62, 当日 H 15.67 L 14.00
    # 日内振幅 1.67 但 |L-prevC| = 2.62 — 跳空必须计入
    k = [_bar("2026-06-04", 16.0, 16.7, 15.9, 16.62),
         _bar("2026-06-05", 15.66, 15.67, 14.00, 14.38)]
    trs = technical.true_range(k)
    assert trs[1] == pytest.approx(2.62, abs=0.01)


def test_atr_wilder_basic():
    k = [_bar(f"d{i}", 10, 11, 9, 10) for i in range(25)]  # TR 恒为 2
    series = technical.atr(k, 20)
    assert series[18] is None          # 不足 period 前沉默
    assert series[19] == pytest.approx(2.0)
    assert series[-1] == pytest.approx(2.0)


# ── 利润棘轮 ────────────────────────────────────────────────────

def test_floor_inactive_below_50pct():
    assert compute_floor(2.0, 2.9, None) == ("inactive", None)


def test_floor_breakeven_at_50pct():
    stage, floor = compute_floor(2.0, 3.0, None)
    assert stage == "breakeven" and floor == 2.0


def test_floor_locked_keeps_two_thirds():
    # 成本 2.541, HWM 5.25 (NOK 6/2 实值): floor = 2.541 + 2.709×(2/3)
    stage, floor = compute_floor(2.541, 5.25, None)
    assert stage == "locked"
    assert floor == pytest.approx(2.541 + (5.25 - 2.541) * 2 / 3, rel=1e-6)


def test_floor_tightens_below_90_dte():
    _, wide = compute_floor(2.0, 5.0, 200)
    _, tight = compute_floor(2.0, 5.0, 60)
    assert tight > wide  # 回吐 1/3 → 1/4, 地板更高


# ── G-01 档位 ───────────────────────────────────────────────────

def test_g01_fires_each_level_once():
    lots = {"position": [{"qty": 5, "avg_cost": 2.47, "entry_date": "2026-05-04"}],
            "base": []}
    crossed = {}
    hits = g01_crossings(lots, 5.25, crossed)  # +112% → 只过 +100% 档
    assert [h["level"] for h in hits] == ["+100%"]
    assert hits[0]["trim_pct"] == 30
    assert g01_crossings(lots, 5.30, crossed) == []          # 同档不重复
    hits2 = g01_crossings(lots, 7.5, crossed)                 # +203% → +200% 档
    assert [h["level"] for h in hits2] == ["+200%"]


# ── 趋势死亡线 ──────────────────────────────────────────────────

def _trend_klines(n=30, start=10.0, step=0.2):
    ks, px = [], start
    for i in range(n):
        ks.append(_bar(f"2026-05-{i+1:02d}" if i < 30 else f"2026-06-{i-29:02d}",
                       px, px + 0.3, px - 0.3, px + step))
        px += step
    return ks


def test_trail_ratchets_up_only():
    ks = _trend_klines()
    t1 = compute_trail(ks, "2026-05-01", None, "normal", None)
    assert t1["trail"] is not None and not t1["broken"]
    # 价格回落但未破线: trail 不下移
    ks2 = ks + [_bar("2026-06-01", 15, 15.1, 14.8, t1["trail"] + 0.1)]
    t2 = compute_trail(ks2, "2026-05-01", None, "normal", t1["trail"])
    assert t2["trail"] >= t1["trail"] and not t2["broken"]


def test_trail_break_on_close_below():
    ks = _trend_klines()
    t1 = compute_trail(ks, "2026-05-01", None, "normal", None)
    ks2 = ks + [_bar("2026-06-01", 15, 15.1, 13.0, t1["trail"] - 0.5)]
    t2 = compute_trail(ks2, "2026-05-01", None, "normal", t1["trail"])
    assert t2["broken"]


def test_trail_parabolic_uses_5d_high():
    ks = _trend_klines()
    t = compute_trail(ks, "2026-05-01", None, "parabolic", None)
    five_high = max(k["high"] for k in ks[-5:])
    assert t["trail"] == pytest.approx(five_high * PARAMS["parabolic_trail_pct"], rel=1e-6)


def test_trail_arm_mid_crash_falls_back_not_broken():
    # 崩盘中段首次布防: 5d high 嵌着崩盘前峰值 → parabolic 线在价上。
    # 历史破位不追溯告警: 回落到 ATR fresh-start 线, 且不判 broken (MU 7/4 实况)。
    ks = _trend_klines()
    crash_close = ks[-1]["close"] * 0.80  # -20%, 破 5d×0.92 必然
    ks.append(_bar("2026-06-02", crash_close * 1.05, crash_close * 1.06,
                   crash_close * 0.98, crash_close))
    t = compute_trail(ks, "2026-06-02", crash_close, "parabolic", None)
    assert not t["broken"]
    assert t["trail"] is None or t["trail"] < crash_close
    # 次轮 (prev_trail 已存在) 继续正常 ratchet, 不再 fallback
    if t["trail"] is not None:
        t2 = compute_trail(ks, "2026-06-02", crash_close, "parabolic", t["trail"])
        assert t2["trail"] >= t["trail"]


def test_trail_never_ratchets_above_price():
    # 回归: fallback 修好的线, 次轮 5d high(嵌旧峰)×0.92 仍在价上 →
    # 不允许抬线越过 close (否则永久 BROKEN), 保持 prev 且不误判破位
    ks = _trend_klines()
    crash_close = ks[-1]["close"] * 0.80
    ks.append(_bar("2026-06-02", crash_close * 1.05, crash_close * 1.06,
                   crash_close * 0.98, crash_close))
    prev = crash_close * 0.9  # fallback 落的线 (价下)
    t = compute_trail(ks, "2026-06-02", crash_close, "parabolic", prev)
    assert t["trail"] == pytest.approx(prev)
    assert not t["broken"]


# ── evaluate 端到端 (NOK 剧本重演) ──────────────────────────────

def _doc(mark, avg_cost=2.541, qty=16, lots=None):
    return {"tickers": {
        "NOK": {"broker": {"qty": 0, "synthetic": True, "current_price": 14.0},
                "agent": {"zones": {"methodology": "normal"}}},
        "NOK270115C15000": {
            "broker": {"qty": qty, "avg_cost": avg_cost, "current_price": mark,
                       "synthetic": False},
            "lots": lots or {"base": [],
                             "position": [{"qty": qty, "avg_cost": avg_cost,
                                       "entry_date": "2026-05-04"}]},
        },
    }}


@pytest.fixture()
def kline_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(exit_guard, "KLINE_CACHE_DIR", tmp_path)
    ks = _trend_klines()
    (tmp_path / "NOK.jsonl").write_text(
        "\n".join(json.dumps(k) for k in ks) + "\n", encoding="utf-8")
    return tmp_path


def test_nok_replay_stage1_then_stage2(kline_dir):
    guards = {"positions": {}}
    # Day 1: mark 5.25 = +107% → 布防 + G-01 +100% 一枪 + locked floor
    guards, shots, _, _ = exit_guard.evaluate(_doc(5.25), guards, "2026-06-02")
    assert any(s["type"] == "g01_+100%" for s in shots)
    g = guards["positions"]["NOK270115C15000"]
    floor = g["ratchet"]["floor"]
    assert g["ratchet"]["stage"] == "locked" and floor > 2.541

    # Day 2: 跌破地板 → stage1 一枪: 卖 50% + no-add lock
    guards, shots, _, _ = exit_guard.evaluate(_doc(floor - 0.1), guards, "2026-06-05")
    assert [s["type"] for s in shots] == ["ratchet_stage1"]
    g = guards["positions"]["NOK270115C15000"]
    assert g["ratchet"]["fired_stage1"] and g["no_add_lock"]
    assert "卖 8" in shots[0]["text"]  # 16 张的 50%

    # Day 3: 同价不重复 (一次性)
    guards, shots, _, _ = exit_guard.evaluate(_doc(floor - 0.1), guards, "2026-06-06")
    assert shots == []

    # Day 4: 破保本 → stage2 清仓, 也只一枪
    guards, shots, _, _ = exit_guard.evaluate(_doc(2.40), guards, "2026-06-18")
    assert [s["type"] for s in shots] == ["ratchet_stage2"]
    guards, shots, _, _ = exit_guard.evaluate(_doc(2.30), guards, "2026-06-19")
    assert shots == []


def test_stale_mark_breach_rearms_on_recovery(kline_dir):
    #  MRVL 案: 假期陈旧 mark 边缘破线 fire → 周一 live mark 收复地板
    # +2% → stage1 重新武装 (地板原位), 再破线 → 再次触发。重测是系统原生行为。
    guards = {"positions": {}}
    guards, _, _, _ = exit_guard.evaluate(_doc(5.25), guards, "2026-07-01")
    floor = guards["positions"]["NOK270115C15000"]["ratchet"]["floor"]
    guards, shots, _, _ = exit_guard.evaluate(_doc(floor - 0.05), guards, "2026-07-02")
    assert [s["type"] for s in shots] == ["ratchet_stage1"]  # 边缘破线 fire
    guards, shots, _, lines = exit_guard.evaluate(_doc(floor * 1.03), guards, "2026-07-06")
    assert shots == []  # 收复 → 无新告警
    g = guards["positions"]["NOK270115C15000"]
    assert not g["ratchet"]["fired_stage1"]          # 重新武装
    assert g["ratchet"]["floor"] >= floor            # 地板原位不降
    assert any("重新武装" in ln for ln in lines)
    guards, shots, _, _ = exit_guard.evaluate(_doc(floor - 0.05), guards, "2026-07-07")
    assert [s["type"] for s in shots] == ["ratchet_stage1"]  # 再破再触发


def test_new_leg_rearms_after_new_high(kline_dir):
    guards = {"positions": {}}
    guards, _, _, _ = exit_guard.evaluate(_doc(5.25), guards, "2026-06-02")
    floor = guards["positions"]["NOK270115C15000"]["ratchet"]["floor"]
    guards, shots, _, _ = exit_guard.evaluate(_doc(floor - 0.1), guards, "2026-06-05")
    assert shots  # stage1 fired
    # HWM 新高 >1% → 重新武装, 地板随新 HWM 抬高, 可再次触发
    guards, _, _, lines = exit_guard.evaluate(_doc(6.0), guards, "2026-06-10")
    g = guards["positions"]["NOK270115C15000"]
    assert not g["ratchet"]["fired_stage1"]
    assert g["ratchet"]["floor"] > floor


def test_breakeven_tier_full_exit_alert(kline_dir):
    # 峰值只到 +60% (未到 +100%): 破保本 → 一段式全平告警
    guards = {"positions": {}}
    guards, shots, _, _ = exit_guard.evaluate(_doc(4.1), guards, "2026-06-02")
    assert shots == []  # +61%, 地板=保本, 未破
    guards, shots, _, _ = exit_guard.evaluate(_doc(2.50), guards, "2026-06-10")
    assert [s["type"] for s in shots] == ["ratchet_breakeven"]
    assert "全平" in shots[0]["text"]


def test_closed_position_guard_pruned(kline_dir):
    guards = {"positions": {}}
    guards, _, _, _ = exit_guard.evaluate(_doc(5.25), guards, "2026-06-02")
    assert "NOK270115C15000" in guards["positions"]
    doc = _doc(5.25)
    doc["tickers"]["NOK270115C15000"]["broker"]["qty"] = 0  # 已平仓
    guards, _, _, _ = exit_guard.evaluate(doc, guards, "2026-07-02")
    assert "NOK270115C15000" not in guards["positions"]


# ── 道氏结构标签 ────────────────────────────────────────────────

def test_swing_structure_uptrend_hh_hl():
    # 上升结构: 两个递升的 swing high + 两个递升的 swing low
    vals = [10, 11, 12, 11.5, 10.8, 11.5, 12.5, 13.5, 12.8, 12.2,
            13.0, 14.0, 15.0, 14.2, 13.8, 14.5, 15.5]
    ks = [_bar(f"d{i}", v, v + 0.1, v - 0.1, v) for i, v in enumerate(vals)]
    st = technical.swing_structure(ks)
    assert st["high_label"] == "HH" and st["low_label"] == "HL"
    assert st["trend"] == "up"


def test_swing_structure_nok_june_lh_ll():
    # NOK 6 月剧本: 17.45 峰 → 13.18 低 → 15.07 反弹(LH) → 12.16(LL) = 下降确认
    vals = [14, 15, 16, 17.45, 16.5, 15.5, 14.5, 13.6, 13.18, 13.8,
            14.5, 15.07, 14.6, 13.9, 13.4, 12.8, 12.16, 12.9, 13.0]
    ks = [_bar(f"d{i}", v, v + 0.05, v - 0.05, v) for i, v in enumerate(vals)]
    st = technical.swing_structure(ks)
    assert st["high_label"] == "LH" and st["low_label"] == "LL"
    assert st["trend"] == "down"
    assert st["last_swing_high"] == pytest.approx(15.07 + 0.05)
    assert st["prev_swing_high"] == pytest.approx(17.45 + 0.05)


# ── Margin guard ────────────────────────────────────────────────

def test_parse_risk_level():
    assert parse_risk_level("LEVEL6") == 6
    assert parse_risk_level("LEVEL3") == 3
    assert parse_risk_level("") is None


def test_margin_fallback_nav_history(tmp_path, monkeypatch):
    nav = tmp_path / "nav.jsonl"
    # 6/26 实值: power 654.76 / market_val 53513 = 1.2% → critical
    nav.write_text(json.dumps({
        "date": "2026-06-26", "total_assets": 48326.22, "cash": -5187.7,
        "market_val": 53513.92, "power": 654.76, "risk_status": "LEVEL3",
    }) + "\n", encoding="utf-8")
    monkeypatch.setattr(exit_guard, "NAV_HISTORY_PATH", nav)
    monkeypatch.setattr(exit_guard, "_fetch_live_account", lambda: None)
    acct, conds, lines = exit_guard.margin_check(live_account=True)
    types = {c["type"] for c in conds}
    assert "margin_power_crit" in types      # 1.2% < 2% — 6/26 那天本该尖叫
    assert "nav_stale" in types              # 数据日期久远 → 盲飞告警
    assert any("power" in ln for ln in lines)


# ── 重进协议机械化 (reentry_watch,) ──────────────────
# 源: exit_rules_backtest — MU 2/4 被趋势线洗出, 4/13 结构修复无信号 → 反事实
# 少赚 ~$5k (全部出场变体中最大单项)。watch 盯收盘, 收复 → 🔓 一次性告警。


def _close_fn(mapping):
    return lambda und: mapping.get(und)


def test_reentry_fires_on_close_above_ref_and_removes():
    watches = {"MU": {"ref": 437.62, "since": "2026-02-04", "src": "MU"}}
    fired = process_reentry_watches(watches, set(), _close_fn({"MU": 441.0}), "2026-04-13")
    assert len(fired) == 1 and fired[0]["und"] == "MU"
    assert watches == {}                        # 一次性: 触发即移除


def test_reentry_holds_below_ref():
    watches = {"MU": {"ref": 437.62, "since": "2026-02-04", "src": "MU"}}
    fired = process_reentry_watches(watches, set(), _close_fn({"MU": 430.0}), "2026-02-20")
    assert fired == [] and "MU" in watches      # 未修复: 继续盯


def test_reentry_dropped_when_position_reopened():
    watches = {"NOK": {"ref": 16.85, "since": "2026-06-05", "src": "NOK270115C15000"}}
    fired = process_reentry_watches(watches, {"NOK"}, _close_fn({"NOK": 12.0}), "2026-06-16")
    assert fired == [] and watches == {}        # 已重新建仓: 正常 guard 接管


def test_reentry_expires_after_max_age():
    watches = {"AAOI": {"ref": 223.1, "since": "2026-05-14", "src": "AAOI270617C140000"}}
    fired = process_reentry_watches(watches, set(), _close_fn({"AAOI": 150.0}),
                                    "2026-09-20", max_age_days=90)
    assert fired == [] and watches == {}        # >90d 静默过期


def test_reentry_bad_ref_removed_silently():
    watches = {"X": {"ref": None, "since": "2026-07-01", "src": "X"}}
    fired = process_reentry_watches(watches, set(), _close_fn({}), "2026-07-02")
    assert fired == [] and watches == {}


# ── 摘要行必须并列两条 stop 并标 binding () ───────────
#
# 只印 trail 的后果: 下游拿到的唯一 stop 形状数字就是它, 而它锚在布防后最高
# 收盘、跟本仓位成本无关。MU 7/25 trail 750.84 比卡片 stop 800 松 6.2%,
# heartbeat 播的"trail 未破"字面无错却把会先触发的线藏了。
# overnight-briefing 的操作手册直接读 summary_lines, 所以必须修在源头。

def _doc_with_card_stop(stop=800.0, days=1):
    zones = {"stop_level": stop, "stop_level_days": days} if stop is not None else {}
    return {"tickers": {"MU": {
        "instrument": {"type": "stock"}, "status": "active",
        "broker": {"qty": 1.0, "avg_cost": 925.0, "current_price": 912.33},
        "agent": {"thesis": {"summary": "t"}, "action": {"intent": "hold"}, "zones": zones},
        "lots": {"base": [], "position": []}, "position_summary": {}, "pending_orders": [],
    }}}


def _summary(monkeypatch, tmp_path, stop=800.0, trail=750.84, days=1, lock=None):
    guards = {"positions": {"MU": {
        "armed_date": "2026-07-25", "hwm_mark": 977.21, "seed_close": 990.21,
        "ratchet": {"stage": "stage1" if lock is not None else "inactive", "floor": lock},
        "trail": {"trail": trail, "mode": "normal", "broken": False,
                  "highest_close": 990.21, "latest_close": 920.95},
        "structure": {"high_label": "LH", "low_label": "LL", "trend": "down"},
    }}}
    monkeypatch.setattr(exit_guard, "KLINE_CACHE_DIR", tmp_path)      # 无 K 线 → trail 不重算
    _, _, _, lines = exit_guard.evaluate(_doc_with_card_stop(stop, days), guards, "2026-07-25")
    return next(ln for ln in lines if ln.startswith("MU:"))


# 显示合同 (用户): 各基折 max **只显示一个止损值 + 来源**。
# 前史 「只印 trail 藏了会先触发的线」— 单值合同显示的恒是 max =
# 会先触发的那条, 病根同样修掉; trail/棘轮照常运转, 折进 max 的只是显示。

def test_summary_line_folds_to_single_binding_stop(monkeypatch, tmp_path):
    line = _summary(monkeypatch, tmp_path)

    assert "止损 800.00 (card 收盘)" in line
    assert "trail 750.84" not in line          # 非 binding 基不再并列

def test_ratcheted_trail_takes_over_the_single_value(monkeypatch, tmp_path):
    line = _summary(monkeypatch, tmp_path, stop=800.0, trail=880.0)

    assert "止损 880.00 (trail normal)" in line
    assert "800.00" not in line


def test_lock_floor_beats_card_stop_when_armed(monkeypatch, tmp_path):
    """棘轮地板武装后高于卡 stop → 单值必须是它 (来源词表 lock, 与 reminder 同源)。
    旧双线版根本不显示棘轮地板 —— 三基折 max 是本合同的新增覆盖。"""
    line = _summary(monkeypatch, tmp_path, stop=800.0, trail=750.84, lock=850.0)

    # stage 字符串由 evaluate 按当前 mark/HWM 重算, 不钉; 钉的是折 max 选中 lock 基
    assert "止损 850.00 (lock 棘轮[" in line


def test_multi_day_window_is_spelled_out(monkeypatch, tmp_path):
    assert "止损 800.00 (card 收盘×2日)" in _summary(monkeypatch, tmp_path, days=2)


def test_card_without_stop_still_reports_trail(monkeypatch, tmp_path):
    line = _summary(monkeypatch, tmp_path, stop=None)

    assert "card" not in line
    assert "止损 750.84 (trail normal)" in line


# ── 布防口径与回测对齐 () ─────────────────────────────
#
# 生产曾是"exit_guard 第一次看到这个仓位那天布防, seed 取布防前一日收盘",
# 回测 (analytics/exit_rules_backtest.py:267) 是 armed_date = entry_date +
# seed 取入场当日收盘。两个不同的模型 → 实盘 trail 和被验证过的那条不是同
# 一条线 (MU 7/24 @925: 生产 seed 990.21 → trail 750.84; 回测 seed 920.95
# → 680.70, 差 $70)。对齐 = 让生产跑回已验证的模型。

def _events(tmp_path, monkeypatch, rows):
    ev = tmp_path / "events" / "2026-07"
    ev.mkdir(parents=True, exist_ok=True)
    (ev / "trades.jsonl").write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    monkeypatch.setattr(exit_guard, "EVENTS_DIR", tmp_path / "events")


def _klines(tmp_path, monkeypatch, ticker, bars):
    kdir = tmp_path / "klines"
    kdir.mkdir(exist_ok=True)
    (kdir / f"{ticker}.jsonl").write_text(
        "\n".join(json.dumps({"time": f"{d} 00:00:00", "close": c}) for d, c in bars),
        encoding="utf-8")
    monkeypatch.setattr(exit_guard, "KLINE_CACHE_DIR", kdir)


def _buy(d, px, ticker="MU"):
    return {"event_type": "fill", "ticker": ticker, "side": "BUY", "qty": 1.0,
            "price": px, "timestamp": f"{d}T10:17:00-04:00"}


def test_arming_anchors_on_the_entry_day(tmp_path, monkeypatch):
    _events(tmp_path, monkeypatch, [_buy("2026-07-24", 925.0)])
    _klines(tmp_path, monkeypatch, "MU", [("2026-07-23", 990.21), ("2026-07-24", 920.95)])

    assert exit_guard.campaign_entry("MU", "MU") == ("2026-07-24", 920.95)


def test_seed_is_the_entry_day_close_not_the_day_before(tmp_path, monkeypatch):
    """990.21 是建仓**前一天**的收盘 —— 这笔仓位从没见过那个价。"""
    _events(tmp_path, monkeypatch, [_buy("2026-07-24", 925.0)])
    _klines(tmp_path, monkeypatch, "MU", [("2026-07-23", 990.21), ("2026-07-24", 920.95)])

    assert exit_guard.campaign_entry("MU", "MU")[1] != 990.21


def test_reentry_arms_from_the_new_campaign(tmp_path, monkeypatch):
    """7/16 全清 → 7/24 重进: 布防日必须是新 campaign 的入场日。"""
    _events(tmp_path, monkeypatch, [
        _buy("2026-07-01", 800.0),
        {"event_type": "close", "ticker": "MU", "side": "SELL", "qty": 9.0,
         "price": 868.01, "timestamp": "2026-07-16T09:30:00-04:00"},
        _buy("2026-07-24", 925.0)])
    _klines(tmp_path, monkeypatch, "MU", [("2026-07-24", 920.95)])

    assert exit_guard.campaign_entry("MU", "MU")[0] == "2026-07-24"


def test_missing_events_falls_back_rather_than_refusing_to_arm(tmp_path, monkeypatch):
    """老仓位 / 手工补录的持仓不能因为缺一条事件就布不了防。"""
    _events(tmp_path, monkeypatch, [])
    _klines(tmp_path, monkeypatch, "MU", [("2026-07-24", 920.95)])

    assert exit_guard.campaign_entry("MU", "MU") == (None, None)


def test_option_seed_uses_the_underlying_series(tmp_path, monkeypatch):
    _events(tmp_path, monkeypatch, [_buy("2026-07-24", 31.0, ticker="AAOI270617C200000")])
    _klines(tmp_path, monkeypatch, "AAOI", [("2026-07-24", 107.0)])

    assert exit_guard.campaign_entry("AAOI270617C200000", "AAOI") == ("2026-07-24", 107.0)


# ── 期权亏损侧 overlay S1/S2/S4 (用户  终裁: -25% / (20,25) / 90) ──

def _ov_doc(mark, qty=2.0, avg_cost=35.0):
    return {"tickers": {"GOOGL270115C390000": {
        "status": "active",
        "instrument": {"type": "option"},
        "broker": {"qty": qty, "current_price": mark, "avg_cost": avg_cost},
        "lots": {},
    }}}


def _flat_klines(end="2026-07-31", base=350.0):
    # 横盘平日序列  起 (入场 6/2 前 ≥21 根 → ATR20@入场已暖机);
    # 文件按 end 截断 — S2 的 days/und_close 取缓存最新根, 语义 = 缓存 ≈ today
    from datetime import date as _D, timedelta as _TD
    out, dd = [], _D(2026, 5, 1)
    while dd.isoformat() <= end:
        if dd.weekday() < 5:
            out.append({"time": f"{dd.isoformat()} 00:00:00",
                        "open": base, "high": base + 2, "low": base - 2,
                        "close": base, "volume": 1e6})
        dd += _TD(days=1)
    return out


def _ov_setup(tmp_path, monkeypatch, klines):
    monkeypatch.setattr(exit_guard, "KLINE_CACHE_DIR", tmp_path)
    monkeypatch.setattr(exit_guard, "IV_HISTORY_DIR", tmp_path / "iv")
    monkeypatch.setattr(exit_guard, "campaign_entry",
                        lambda t, u: ("2026-06-02", 350.0))
    (tmp_path / "GOOGL.jsonl").write_text(
        "\n".join(json.dumps(k) for k in klines) + "\n", encoding="utf-8")


def test_opt_s1_fires_once_and_rearms(tmp_path, monkeypatch):
    """S1: mark ≤ 成本×0.80 ( −25→−20) → 一枪; 同价不重复; 收复 +2% 重新武装再破再响。"""
    _ov_setup(tmp_path, monkeypatch, _flat_klines("2026-06-05"))
    assert exit_guard.PARAMS["opt_s1_stop"] == -0.20
    guards = {"positions": {}}
    guards, shots, _, _ = exit_guard.evaluate(_ov_doc(27.9), guards, "2026-06-05")
    assert [s["type"] for s in shots] == ["opt_s1_stop"], \
        f"35×0.80=28.0, mark 27.9 应触发 S1, got {[s['type'] for s in shots]}"
    assert "清仓" in shots[0]["text"]
    guards, shots, _, _ = exit_guard.evaluate(_ov_doc(27.5), guards, "2026-06-06")
    assert shots == [], "S1 fired flag 应去重"
    # 收复 28.0×1.02=28.56 之上 → 重武装 → 再破再响
    guards, shots, _, _ = exit_guard.evaluate(_ov_doc(28.6), guards, "2026-06-07")
    assert shots == []
    guards, shots, _, _ = exit_guard.evaluate(_ov_doc(27.9), guards, "2026-06-08")
    assert [s["type"] for s in shots] == ["opt_s1_stop"]
    # 旧档 −25 的 26.2 现在当然也触发, 但 27.9 在旧档不会 —— 这条断言是档位变异守卫
    guards2, shots2, _, _ = exit_guard.evaluate(_ov_doc(28.1), {"positions": {}}, "2026-06-05")
    assert not any(s["type"] == "opt_s1_stop" for s in shots2)


def _ov_doc_with_gate(mark, gate_level=360.0, qty=1.0, avg_cost=35.0, history=None):
    """期权 + underlying 卡 (qty 0, 只提供 regime_history = 授予 cap 的 gate)。"""
    doc = _ov_doc(mark, qty=qty, avg_cost=avg_cost)
    doc["tickers"]["GOOGL"] = {
        "status": "active", "instrument": {"type": "stock"},
        "broker": {"qty": 0.0}, "lots": {},
        "agent": {"regime_history": history if history is not None else [
            {"date": "2026-05-20", "direction": "down", "level": gate_level, "cap_pct_nav": 0},
            {"date": "2026-05-28", "direction": "up", "level": gate_level, "cap_pct_nav": 0.40}]}}
    return doc


def test_opt_gate_stop_fires_on_close_below_granting_gate_and_rearms(tmp_path, monkeypatch):
    """S5g (用户, META 553 案): underlying 收盘 ×1 日 < 授予 cap 的 gate
    → 清仓一枪 (不等 bfloor); 收回 gate 之上重新武装; guard 上写回 opt_gate_stop 供
    brief/画线读 (underlying 基)。"""
    ks = _flat_klines("2026-06-05")            # close 350 恒 < gate 360
    _ov_setup(tmp_path, monkeypatch, ks)
    guards = {"positions": {}}
    guards, shots, _, _ = exit_guard.evaluate(_ov_doc_with_gate(33.0), guards, "2026-06-05")
    types = [s["type"] for s in shots]
    assert "opt_gate_stop" in types, f"close 350 < gate 360 应触发 S5g, got {types}"
    shot = next(s for s in shots if s["type"] == "opt_gate_stop")
    assert "清仓 1 张" in shot["text"] and "360" in shot["text"] and "S5g" in shot["text"]
    g = guards["positions"]["GOOGL270115C390000"]
    assert g["opt_gate_stop"] == {"level": 360.0, "days": 1}
    assert g["opt_overlay"]["gate_fired"] is True
    guards, shots, _, _ = exit_guard.evaluate(_ov_doc_with_gate(33.0), guards, "2026-06-06")
    assert not any(s["type"] == "opt_gate_stop" for s in shots), "fired flag 应去重"
    # 收回 gate 之上 → 重武装; 再跌破 → 再响
    for k in ks[-1:]:
        k["close"] = 365.0
    _ov_setup(tmp_path, monkeypatch, ks)
    guards, shots, _, lines = exit_guard.evaluate(_ov_doc_with_gate(33.0), guards, "2026-06-08")
    assert guards["positions"]["GOOGL270115C390000"]["opt_overlay"]["gate_fired"] is False
    assert any("S5g 重新武装" in ln for ln in lines)
    for k in ks[-1:]:
        k["close"] = 350.0
    _ov_setup(tmp_path, monkeypatch, ks)
    guards, shots, _, _ = exit_guard.evaluate(_ov_doc_with_gate(33.0), guards, "2026-06-09")
    assert any(s["type"] == "opt_gate_stop" for s in shots)


def test_opt_gate_stop_absent_without_granting_gate(tmp_path, monkeypatch):
    """常规参与卡 (没爬过线) / 参与流被 down 打断 → 无 gate 线, 退回卡 stop (S5), 不误响。"""
    _ov_setup(tmp_path, monkeypatch, _flat_klines("2026-06-05"))
    guards, shots, _, _ = exit_guard.evaluate(_ov_doc_with_gate(33.0, history=[]),
                                              {"positions": {}}, "2026-06-05")
    assert not any(s["type"] == "opt_gate_stop" for s in shots)
    assert guards["positions"]["GOOGL270115C390000"]["opt_gate_stop"] is None
    broken = [{"date": "2026-05-28", "direction": "up", "level": 360.0, "cap_pct_nav": 0.40},
              {"date": "2026-06-01", "direction": "down", "level": 340.0, "cap_pct_nav": 0}]
    guards, shots, _, _ = exit_guard.evaluate(_ov_doc_with_gate(33.0, history=broken),
                                              {"positions": {}}, "2026-06-05")
    assert not any(s["type"] == "opt_gate_stop" for s in shots)


def test_opt_s2_half_then_full_on_flat_underlying(tmp_path, monkeypatch):
    """S2: 入场后 d20 未收上 seed+1N → 减半 (qty≥2); d25 → 清仓。横盘 fixture 恒 flat。"""
    _ov_setup(tmp_path, monkeypatch, _flat_klines("2026-06-30"))  # 6/2 后 20 根
    guards = {"positions": {}}
    guards, shots, _, _ = exit_guard.evaluate(_ov_doc(33.0), guards, "2026-06-30")
    assert [s["type"] for s in shots] == ["opt_s2_half"]
    assert "减半" in shots[0]["text"]
    # 再推进到 d25+ → 清仓档
    _ov_setup(tmp_path, monkeypatch, _flat_klines("2026-07-08"))
    guards, shots, _, _ = exit_guard.evaluate(_ov_doc(33.0), guards, "2026-07-08")
    assert [s["type"] for s in shots] == ["opt_s2_full"]
    assert "清仓" in shots[0]["text"]


def test_opt_s2_single_contract_skips_half(tmp_path, monkeypatch):
    """单张不可拆: d20 减半档降级为提示行, 不出告警 (整股约束哲学)。"""
    _ov_setup(tmp_path, monkeypatch, _flat_klines("2026-06-30"))
    guards = {"positions": {}}
    guards, shots, _, lines = exit_guard.evaluate(_ov_doc(33.0, qty=1.0), guards, "2026-06-30")
    assert not any(s["type"] == "opt_s2_half" for s in shots)
    assert any("单张不可拆" in l for l in lines)


def test_opt_s2_not_flat_no_fire(tmp_path, monkeypatch):
    """underlying 已收上 seed+1N → S2 不触发 (在涨的仓不碰 — 42 真实重放零误伤的机制)。"""
    ks = _flat_klines("2026-06-30")
    for k in ks[-3:]:
        k["close"] = 358.0   # seed 350 + ATR≈4 → 354 之上
    _ov_setup(tmp_path, monkeypatch, ks)
    guards = {"positions": {}}
    guards, shots, _, _ = exit_guard.evaluate(_ov_doc(33.0), guards, "2026-06-30")
    assert not any(s["type"].startswith("opt_s2") for s in shots)


def test_opt_s4_dte_floor_fires_once(tmp_path, monkeypatch):
    """S4: DTE ≤ 90 → close-or-roll 一枪。expiry 2027-01-15, today 2026-10-20 → dte 87。"""
    _ov_setup(tmp_path, monkeypatch, _flat_klines("2026-07-31"))
    guards = {"positions": {}}
    guards, shots, _, _ = exit_guard.evaluate(_ov_doc(33.0), guards, "2026-10-20")
    types = [s["type"] for s in shots]
    assert "opt_s4_dte" in types, f"dte 87 ≤ 90 应触发 S4, got {types}"
    guards, shots, _, _ = exit_guard.evaluate(_ov_doc(33.0), guards, "2026-10-21")
    assert not any(s["type"] == "opt_s4_dte" for s in shots), "S4 fired flag 应去重"


def test_atrisk_option_uses_s1_fraction():
    """at-risk 期权分支 = 保费 × |S1| (0.20 自; 0.25 是 放宽裁决的
    档位), 且与 S1 档位钉死相等。"""
    from quant.core import sizing
    assert sizing.OPTION_ATRISK_LOSS_FRAC == abs(exit_guard.PARAMS["opt_s1_stop"]), \
        "at-risk 放宽系数必须锚 S1 档位 — 改一处必须同改另一处"
    from quant.report.portfolio_brief import at_risk_breach_signal
    # 保费 mark $2,000/张 ×2 张 = $4,000, ×0.20 = $800 at-risk; cap 5%×$21k=$1,050 → 不超
    item = {"ticker": "GOOGL270115C390000", "qty": 2, "current_price": 20.0}
    assert at_risk_breach_signal(item, None, None, 21000.0) is None
    # 全额口径下 $4,000 > $1,050 早超了 — 本断言在旧口径必红 (变异守卫)
    sig = at_risk_breach_signal({"ticker": "GOOGL270115C390000", "qty": 9,
                                 "current_price": 20.0}, None, None, 21000.0)
    assert sig and "S1 premium stop" in sig["stop_basis"]
