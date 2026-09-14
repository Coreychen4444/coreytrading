"""Recent buy fills without the era-appropriate authorization tag must alert."""
from datetime import datetime, timedelta, timezone
import json

import pytest

from quant.report import portfolio_brief as pb


def _fill(ticker="TEST", days_ago=1, cls=None, auth=None,
          side="BUY", event_type="fill"):
    ts = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    row = {"event_type": event_type, "timestamp": ts, "ticker": ticker,
           "side": side, "qty": 2.0, "price": 187.0, "order_id": "X"}
    if cls:
        row["classification"] = cls
    if auth:
        row["auth"] = auth
    return row


@pytest.fixture
def cutover(monkeypatch):
    """Move the authorization cutover relative to now so tests pin rules, not dates."""
    def set_offset(offset_days: int):
        value = (datetime.now(timezone.utc) + timedelta(days=offset_days)).date().isoformat()
        monkeypatch.setattr(pb, "AUTH_TAG_SINCE", value)
        return value
    return set_offset


@pytest.fixture
def events(monkeypatch, tmp_path):
    """把事件流换成内存里的一份 —— 真账本不参与测试。"""
    rows: list[dict] = []
    month = tmp_path / datetime.now(timezone.utc).strftime("%Y-%m")
    month.mkdir()
    (month / "trades.jsonl").touch()
    monkeypatch.setattr(pb, "EVENTS_DIR", tmp_path)
    monkeypatch.setattr(pb, "iter_jsonl", lambda p: list(rows) if p.name == "trades.jsonl" else [])
    positions = tmp_path / "positions.json"
    watchlist = tmp_path / "watchlist.json"
    for path in (positions, watchlist):
        path.write_text(json.dumps({"tickers": {}}), encoding="utf-8")
    nav = tmp_path / "nav_history.jsonl"
    nav.write_text("", encoding="utf-8")
    klines = tmp_path / "klines"
    klines.mkdir()
    monkeypatch.setattr(pb, "STATE_POSITIONS_PATH", positions)
    monkeypatch.setattr(pb, "STATE_WATCHLIST_PATH", watchlist)
    monkeypatch.setattr(pb, "NAV_HISTORY_PATH", nav)
    monkeypatch.setattr(pb, "KLINE_CACHE_DIR", klines)
    monkeypatch.setattr(pb, "STATE_PATH", tmp_path / "heartbeat-state.json")
    monkeypatch.setattr(pb.book, "POSITIONS_PATH", positions)
    monkeypatch.setattr(pb.book, "NAV_HISTORY_PATH", nav)
    monkeypatch.setattr(pb.book, "KLINE_CACHE_DIR", klines)
    monkeypatch.setattr(pb, "lot_drift", lambda *a, **k: [])
    from quant.decision import exit_guard
    monkeypatch.setattr(exit_guard, "run_checks", lambda **k: {
        "summary_lines": [], "account": None, "guards": {},
        "one_shot": [], "persistent": []})
    # 同上: build_brief 读真日历 = 断言跟着财报季红绿 ( 立规)。
    monkeypatch.setattr(pb.earnings, "load_calendar",
                        lambda *a, **k: {"as_of": pb.et_today(), "tickers": {}})
    return rows


def test_recent_unclassified_buy_is_named(events):
    events.append(_fill())
    out = pb.untagged_fills()
    assert len(out) == 1
    assert "TEST" in out[0] and "无授权标签" in out[0]


def test_auth_tagged_fill_is_quiet(events):
    events.append(_fill(auth={"cap_pct_nav": 0.4, "gate_no": 1}))
    assert pb.untagged_fills() == []


def test_cutover_decides_which_tag_counts(events, cutover):
    """New fills require auth; classification remains valid only before cutover."""
    events.append(_fill(cls="probe"))

    cutover(-30)
    assert [line for line in pb.untagged_fills() if "TEST" in line]

    cutover(+1)
    assert pb.untagged_fills() == []


def test_history_outside_the_window_is_quiet(events):
    """Old untagged history stays outside the operational alert window."""
    events.append(_fill(days_ago=pb.UNTAGGED_FILL_WINDOW_DAYS + 1))
    assert pb.untagged_fills() == []


@pytest.mark.parametrize("kwargs", [
    {"side": "SELL", "event_type": "close"},
    {"side": "SELL"},
])
def test_sell_side_is_not_a_missing_tag(events, kwargs):
    """classification 是入场档位标签, 卖出没有这个语义 —— 不是缺失, 是不适用。"""
    events.append(_fill(**kwargs))
    assert pb.untagged_fills() == []


def test_alert_reaches_the_brief_not_just_the_helper(events, monkeypatch):
    """Pin the end-to-end alert path, not only the helper return value."""
    monkeypatch.setattr(pb, "untagged_fills", lambda *a, **k: [
        "TEST 1.0@1.0 (synthetic) 无授权标签 (auth)"])
    out = pb.build_brief([], "ticker", mutate_state=False)
    hit = [a for a in out.get("alerts") or [] if "成交无授权标签" in a]
    assert hit, "untagged_fills 有输出但 brief 的 alerts 里没有它"
    assert any(t.get("type") == "fill_untagged" for t in out.get("triggers") or [])


def test_scored_fill_is_quiet_even_without_auth(events, monkeypatch):
    """cap 0 的成交按 schema 不能盖 auth 章 → 「注解 + 评分」是它唯一的判定形状。
    cap 0 regime 里买回的规则外成交裁「直接评 C, 不再点名」: 此前告警文案写着「判定后 annotate_event 补注 + 落评分」, 但判据只认
    auth 章 —— 评完分照样响到 14 天窗口滑出。评分行 (ref = order_id) 收口。
    """
    events.append(_fill())                                       # order_id "X", 无 auth
    monkeypatch.setattr(pb, "scored_fill_refs", lambda: {"X"})
    assert pb.untagged_fills() == []
    monkeypatch.setattr(pb, "scored_fill_refs", lambda: {"OTHER"})
    assert len(pb.untagged_fills()) == 1, "别的成交的评分不能替这一笔收口"


def test_scored_fill_refs_reads_ref_column(monkeypatch):
    rows = [{"ticker": "NBIS", "score": "C", "ref": "FS1D09"},
            {"ticker": "MRVL", "score": "B", "ref": "FS1D03"},
            {"ticker": "X", "score": "A"}]                       # 无 ref 的行不算
    monkeypatch.setattr(pb, "iter_jsonl", lambda p: list(rows))
    assert pb.scored_fill_refs() == {"FS1D09", "FS1D03"}
    monkeypatch.setattr(pb, "iter_jsonl", lambda p: (_ for _ in ()).throw(OSError("gone")))
    assert pb.scored_fill_refs() == set(), "账本读不到 = 没人判过, 照常点名 (不是崩)"


# ── INFRA:fill_price_breach (INFRA-5(b)) ─────────
#
# fill_untagged 只点名**没章的** —— 带 auth 章的价位违规此前完全静默
# (一笔带合规章的成交踩了 no_chase+trim 双线)。flags 由同步层落库即盖,
# 这里只读不重算 (重算 = 用今天的卡评历史的价, 正是盖章要冻结的漂移)。

def test_price_breach_fill_is_named_even_with_auth_tag(events):
    events.append({**_fill(auth={"cap_pct_nav": 0.4, "gate_no": 1}),
                   "price_flags": ["above_no_chase", "in_trim_zone"]})
    out = pb.price_breach_fills()
    assert len(out) == 1
    assert "TEST" in out[0] and "above_no_chase+in_trim_zone" in out[0]


def test_price_breach_marks_cross_day_stamp(events):
    events.append({
        **_fill(), "price_flags": ["above_no_chase"],
        "stamp_stale_card_risk": True,
    })
    assert "跨日盖章" in pb.price_breach_fills()[0]


def test_clean_and_stale_price_fills_are_quiet(events):
    events.append(_fill(auth={"cap_pct_nav": 0.4, "gate_no": 1}))
    events.append({
        **_fill(days_ago=pb.UNTAGGED_FILL_WINDOW_DAYS + 1),
        "price_flags": ["in_trim_zone"],
    })
    assert pb.price_breach_fills() == []


def test_price_breach_alert_reaches_the_brief(events, monkeypatch):
    monkeypatch.setattr(pb, "price_breach_fills", lambda: [
        "TEST 1.0@1.0 (synthetic) above_no_chase"])
    out = pb.build_brief([], "ticker", mutate_state=False)
    assert any("成交价位违规" in alert for alert in out.get("alerts") or [])
    assert any(t.get("type") == "fill_price_breach" for t in out.get("triggers") or [])


def test_option_breach_line_shows_the_underlying_price(events):
    """期权 BUY 的价位违规: flag 是 above_z1, 行尾带正股成交时刻价与来源。"""
    events.append({**_fill(ticker="SYN270115C100000"), "price_flags": ["above_z1", "above_no_chase"],
                   "underlying_at_fill": {"price": 118.4, "basis": "60m", "ambiguous": False}})
    events.append({**_fill(ticker="NVDA270319C220000"), "price_flags": ["above_z1"],
                   "underlying_at_fill": {"price": 231.2, "basis": "1d", "ambiguous": True}})
    out = pb.price_breach_fills()
    assert len(out) == 2
    assert "above_z1+above_no_chase" in out[0] and "正股≈118.4@60m" in out[0] and "跨带" not in out[0]
    assert "正股≈231.2@1d (bar 跨带)" in out[1]
