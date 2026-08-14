"""Recent buy fills without the era-appropriate authorization tag must alert."""
from datetime import datetime, timedelta, timezone

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
    monkeypatch.setattr(pb, "iter_jsonl", lambda p: list(rows))
    # Calendar state is an external input and must not change this test's result.
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
