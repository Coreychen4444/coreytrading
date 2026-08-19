"""Synthetic coverage for unresolved exit decisions after a demotion."""
from quant.core.structure import latest_demotion, trading_days_after, unresolved_demotions


def _card(qty=20.0, hist=None, stop=88.0, instrument=None):
    return {
        "instrument": instrument or {"type": "stock"},
        "broker": {"qty": qty, "current_price": 96.0},
        "agent": {
            "zones": {"stop_level": stop},
            "regime_history": hist if hist is not None else [
                {"date": "2026-08-12", "direction": "up", "level": 100.0},
                {"date": "2026-08-19", "direction": "down", "level": 95.0,
                 "reason": "收盘 94.0 < 卡片 stop 95.0 连续 1 日", "cap_pct_nav": 0},
            ],
        },
    }


def _doc(**tickers):
    return {"tickers": tickers}


# ── latest_demotion: down 条目才算, 且必须是最新一条 ──────────────


def test_latest_demotion_reads_only_the_tail_entry():
    agent = _card()["agent"]
    d = latest_demotion(agent)
    assert d and d["level"] == 95.0 and d["date"] == "2026-08-19"
    # 其后有 up 转移 = 结构已收复, 裁决自然失效
    agent["regime_history"].append({"date": "2026-08-21", "direction": "up", "level": 100.0})
    assert latest_demotion(agent) is None
    assert latest_demotion({}) is None
    assert latest_demotion(None) is None


# ── 判据 ①: 敞口 ─────────────────────────────────────────────────


def test_demoted_and_held_with_no_action_is_reported():
    rows = unresolved_demotions(_doc(TST=_card()), events=[], scores=[])
    assert len(rows) == 1
    r = rows[0]
    assert r["ticker"] == "TST" and r["date"] == "2026-08-19"
    assert r["old_stop"] == 95.0      # down 条目的 level = 被破的旧 stop (卡上已读不到)
    assert r["new_stop"] == 88.0      # 降级换图后的新 stop
    assert r["qty"] == 20.0


def test_flat_card_without_options_is_not_an_exit_problem():
    """候选卡 (watchlist 快照 qty 0) 降级不进来 —— 没有要执行的退出。"""
    assert unresolved_demotions(_doc(TST=_card(qty=0.0)), events=[], scores=[]) == []


def test_option_exposure_on_demoted_underlying_counts(monkeypatch):
    """Option exposure keeps the underlying card's exit decision active."""
    doc = _doc(
        UND=_card(qty=0.0),
        UND270115C390000={"instrument": {"type": "option"},
                            "broker": {"qty": 1.0}, "agent": {}},
    )
    rows = unresolved_demotions(doc, events=[], scores=[])
    assert len(rows) == 1
    assert rows[0]["opt_tickers"] == ["UND270115C390000"]
    assert rows[0]["qty"] == 0.0


# ── 判据 ③: 降级收盘之后的卖出/平仓事件 = 已动作 ──────────────────


def _sell(ts, ticker="TST", event_type="fill", side="SELL"):
    return {"event_type": event_type, "timestamp": ts, "ticker": ticker,
            "side": side, "qty": 5.0, "price": 93.0}


def test_sell_after_the_demotion_close_silences():
    ev = [_sell("2026-08-19T16:30:00-04:00")]        # 降级当晚盘后卖 = 执行了
    assert unresolved_demotions(_doc(TST=_card()), events=ev, scores=[]) == []


def test_intraday_sell_before_the_close_does_not_silence():
    """降级由**收盘**裁定 (16:00 ET); 当天盘中的卖出发生在裁决之前, 不算响应。"""
    ev = [_sell("2026-08-19T10:15:00-04:00")]
    assert len(unresolved_demotions(_doc(TST=_card()), events=ev, scores=[])) == 1


def test_close_event_on_the_option_leg_silences_the_underlying_card():
    doc = _doc(
        UND=_card(qty=0.0),
        UND270115C390000={"instrument": {"type": "option"},
                            "broker": {"qty": 1.0}, "agent": {}},
    )
    ev = [_sell("2026-08-20T10:43:00-04:00", ticker="UND270115C390000",
                event_type="close")]
    assert unresolved_demotions(doc, events=ev, scores=[]) == []


def test_buy_fill_after_demotion_is_not_an_exit_action():
    ev = [{"event_type": "fill", "timestamp": "2026-08-20T09:35:00-04:00",
           "ticker": "TST", "side": "BUY", "qty": 2.0, "price": 120.0}]
    assert len(unresolved_demotions(_doc(TST=_card()), events=ev, scores=[])) == 1


# ── 判据 ④: scores.jsonl 注解 = 书面 override 已落库 ──────────────


def test_score_row_on_or_after_the_demotion_date_silences():
    sc = [{"date": "2026-08-20", "ticker": "TST", "score": "B", "phase": "exit",
           "notes": "override: 半导体普跌回声, 按 macro-context 等 2 日确认"}]
    assert unresolved_demotions(_doc(TST=_card()), events=[], scores=sc) == []


def test_stale_score_row_from_before_the_demotion_does_not_silence():
    sc = [{"date": "2026-08-12", "ticker": "TST", "score": "C", "phase": "entry"}]
    assert len(unresolved_demotions(_doc(TST=_card()), events=[], scores=sc)) == 1


# ── D+N 计数 (消费者自带 bar 序列) ────────────────────────────────


def test_trading_days_after_counts_completed_bars_strictly_later():
    bars = ["2026-08-18", "2026-08-19"]
    assert trading_days_after(bars, "2026-08-19") == 0      # 降级当晚 = D+0
    assert trading_days_after(bars + ["2026-08-20"], "2026-08-19") == 1
    # bar time 带时分秒也认, 重复日期去重
    assert trading_days_after(["2026-08-20 00:00:00", "2026-08-20"], "2026-08-19") == 1
    assert trading_days_after([], "2026-08-19") == 0
