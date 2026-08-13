"""未分类成交必须响 —— 空字段不会自己举手。

 起同步层落库即盖章, 所以 classification 为空只剩一个含义: 推导返回了
None = regime cap 0 / 无 current_regime = **这笔成交没有系统授权的形状**。那正是
CLAUDE.md §Trading Discipline 要人注解的那一类。

没有这条告警, "推不出来"就退化成一个空字段: 预算行降级、注解清单少一笔, 谁也不会
发现 —— 与  NBIS 那次同一个形状 (schema 报了 drift, 但只 print 到没人
看的 stderr)。
"""
from datetime import datetime, timedelta, timezone

import pytest

from quant.report import portfolio_brief as pb


def _fill(ticker="NBIS", days_ago=1, cls=None, side="BUY", event_type="fill"):
    ts = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    row = {"event_type": event_type, "timestamp": ts, "ticker": ticker,
           "side": side, "qty": 2.0, "price": 187.0, "order_id": "X"}
    if cls:
        row["classification"] = cls
    return row


@pytest.fixture
def events(monkeypatch, tmp_path):
    """把事件流换成内存里的一份 —— 真账本不参与测试。"""
    rows: list[dict] = []
    month = tmp_path / "2026-08"
    month.mkdir()
    (month / "trades.jsonl").touch()
    monkeypatch.setattr(pb, "EVENTS_DIR", tmp_path)
    monkeypatch.setattr(pb, "iter_jsonl", lambda p: list(rows))
    # 同上: build_brief 读真日历 = 断言跟着财报季红绿 (立规)。
    monkeypatch.setattr(pb.earnings, "load_calendar",
                        lambda *a, **k: {"as_of": pb.et_today(), "tickers": {}})
    return rows


def test_recent_unclassified_buy_is_named(events):
    events.append(_fill())
    out = pb.untagged_fills()
    assert len(out) == 1
    assert "NBIS" in out[0] and "无授权标签" in out[0]


def test_classified_fill_is_quiet(events):
    events.append(_fill(cls="probe"))
    assert pb.untagged_fills() == []


def test_history_outside_the_window_is_quiet(events):
    """2026-08-06 之前的 60 笔裸成交是既成事实。天天点名 = 训练人无视这条告警。"""
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
    """端到端: 函数返回行 ≠ 告警会响。

     的教训是**不会触发的具体检查比没有这条检查更危险**—— 大盘补充
    那条检查写在持仓循环里, 而 QQQ/SPY 永远进不了那个循环, 文档描述了它半年。
    """
    monkeypatch.setattr(pb, "untagged_fills", lambda *a, **k: ["FAKE 1.0@1.0 (2026-08-07) 无授权标签 (auth)"])
    out = pb.build_brief([], "ticker", mutate_state=False)
    hit = [a for a in out.get("alerts") or [] if "成交无授权标签" in a]
    assert hit, "untagged_fills 有输出但 brief 的 alerts 里没有它"
    assert any(t.get("type") == "fill_untagged" for t in out.get("triggers") or [])
