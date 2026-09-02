"""结构性退出自动评分 ( 用户 立, NBIS 案)。

钉四件事: 时延档 (D+0=A / D+1=B / D+2+=C, 周末不计迟) / 判据门 (止盈不评 /
历史不回填 / 平仓早于降级不评) / 幂等消音 (已评不双记) / kline 缺失不给错分。
判别式与 unresolved_demotions 是对偶: 那边点名无动作, 这边记分有动作。
"""
from quant.journal import exit_score as es


def _card(closed_at, demote_date, order_id="ORD1", exit_price=210.7,
          pnl=-216.1, status="closed", direction="down"):
    return {
        "status": status,
        "broker": {"qty": 0},
        "exit_info": {"closed_at": closed_at, "order_id": order_id,
                      "exit_price": exit_price, "realized_pnl_usd": pnl},
        "agent": {"regime_history": [
            {"direction": direction, "date": demote_date, "level": 218.11,
             "reason": "收盘破 stop"}]},
    }


def _bars(dates):
    return [{"time": f"{d} 00:00:00", "close": 100.0} for d in dates]


# ── 时延档 ──────────────────────────────────────────────────────

def test_same_day_after_hours_exit_scores_a():
    """NBIS 实况: 8/24 收盘裁决 → 8/24 20:02 ET 盘后全平 = D+0 = A。"""
    doc = {"tickers": {"NBIS": _card("2026-08-24T20:02:38-04:00", "2026-08-24")}}
    out = es.plan(doc, scores=[], bars_of=lambda t: _bars(["2026-08-24"]))
    assert len(out["rows"]) == 1
    r = out["rows"][0]
    assert (r["ticker"], r["score"], r["phase"]) == ("NBIS", "A", "exit")
    assert r["ref"] == "ORD1" and r["source"] == es.SOURCE
    assert "D+0" in r["notes"] and "218.11" in r["notes"]


def test_next_trading_day_scores_b_weekend_not_counted():
    """降级周五 → 周一执行: 中间无完成 bar = D+1 = B (周末不计迟)。"""
    doc = {"tickers": {"X": _card("2026-08-31T10:00:00-04:00", "2026-08-28")}}
    out = es.plan(doc, scores=[],
                  bars_of=lambda t: _bars(["2026-08-27", "2026-08-28"]))
    assert out["rows"][0]["score"] == "B"
    assert "D+1" in out["rows"][0]["notes"]


def test_two_plus_days_late_scores_c():
    """隔 2 个完成 bar 才执行 = exit_overdue 形状 = C。"""
    doc = {"tickers": {"X": _card("2026-08-27T10:00:00-04:00", "2026-08-24")}}
    out = es.plan(doc, scores=[], bars_of=lambda t: _bars(
        ["2026-08-24", "2026-08-25", "2026-08-26"]))
    assert out["rows"][0]["score"] == "C"
    assert "exit_overdue" in out["rows"][0]["notes"]


# ── 判据门 ──────────────────────────────────────────────────────

def test_profit_taking_close_without_demotion_is_not_scored():
    """止盈清仓 (无 down 在案) 归人评, 不进机械分。"""
    doc = {"tickers": {"X": _card("2026-08-24T20:02:38-04:00", "2026-08-24",
                                  direction="up")}}
    out = es.plan(doc, scores=[], bars_of=lambda t: _bars(["2026-08-24"]))
    assert out["rows"] == [] and out["skipped"] == []


def test_history_before_effective_from_is_not_backfilled():
    """裁决向前有效 (用户「8/20 那个 B 不追改」): COHR 8/19 /
    INTC 8/21 不回填 —— 迟到发生在 override 退役裁决之前。"""
    doc = {"tickers": {"INTC": _card("2026-08-21T10:00:00-04:00", "2026-08-19")}}
    out = es.plan(doc, scores=[], bars_of=lambda t: _bars(
        ["2026-08-19", "2026-08-20"]))
    assert out["rows"] == []


def test_close_before_demotion_is_not_bell_driven():
    doc = {"tickers": {"X": _card("2026-08-24T10:00:00-04:00", "2026-08-25")}}
    out = es.plan(doc, scores=[], bars_of=lambda t: _bars(["2026-08-24"]))
    assert out["rows"] == []


def test_active_cards_are_ignored():
    doc = {"tickers": {"X": _card("2026-08-24T10:00:00-04:00", "2026-08-24",
                                  status="active")}}
    assert es.plan(doc, scores=[], bars_of=lambda t: [])["rows"] == []


# ── 幂等消音 ────────────────────────────────────────────────────

def test_existing_exit_score_silences_rescoring():
    """人工已评 (或上轮已评) → 不双记; 判据 = ticker + phase=exit + date ≥ 降级日
    (与 unresolved_demotions ④「落库即消音」同款)。"""
    doc = {"tickers": {"NBIS": _card("2026-08-24T20:02:38-04:00", "2026-08-24")}}
    prior = [{"ticker": "NBIS", "phase": "exit", "date": "2026-08-24",
              "score": "A", "ref": "ORD1"}]
    assert es.plan(doc, scores=prior, bars_of=lambda t: [])["rows"] == []
    # 降级日**之前**的旧 exit 评分 (上一个 campaign) 不消音本笔
    old = [{"ticker": "NBIS", "phase": "exit", "date": "2026-08-03",
            "score": "C", "ref": "OLD"}]
    assert len(es.plan(doc, scores=old,
                       bars_of=lambda t: _bars(["2026-08-24"]))["rows"]) == 1


# ── kline 缺失 ──────────────────────────────────────────────────

def test_missing_klines_skip_with_pointer_not_wrong_score():
    """closed ≠ demote 日且无 bar 序列 → D+N 算不出, 报 skip 不给分。"""
    doc = {"tickers": {"X": _card("2026-08-26T10:00:00-04:00", "2026-08-25")}}
    out = es.plan(doc, scores=[], bars_of=lambda t: [])
    assert out["rows"] == []
    assert len(out["skipped"]) == 1 and "kline 缺失" in out["skipped"][0]
