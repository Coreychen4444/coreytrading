"""core/open_orders — 在途挂单提醒 (防隔夜忘单开盘成交)。合成订单。"""
from quant.core.open_orders import card_status, render

ORDER = {"ticker": "SYNA270319C90000", "side": "BUY", "qty": 1.0, "dealt_qty": 0.0,
         "limit_price": 12.5, "status": "SUBMITTED", "created": "2026-11-02T15:42:10-05:00",
         "tif": "GTC", "card": "none"}


def test_no_orders_no_message():
    assert render([], "2026-11-03") is None


def test_orphan_overnight_order_is_flagged():
    """前一 ET 日挂的、合约与正股都没卡 → 隔夜 + 无卡 两个标都要亮。"""
    msg = render([ORDER], "2026-11-03")
    assert "在途挂单 1 张" in msg
    assert "SYNA270319C90000 BUY 1 @ 12.50 · GTC · 挂于 11-02 15:42 ET · ⏳隔夜 ⚠️无卡" in msg
    assert "规则外" in msg


def test_same_day_carded_order_has_no_flags():
    o = {**ORDER, "created": "2026-11-03T05:10:00-05:00", "card": "position",
         "qty": 3, "dealt_qty": 1}
    msg = render([o], "2026-11-03")
    assert "BUY 3 (已成 1) @ 12.50" in msg
    assert "隔夜" not in msg and "无卡" not in msg and "规则外" not in msg


def test_card_status_option_falls_back_to_underlying():
    assert card_status("SYNA270319C90000", {"SYNA"}, set()) == "position"
    assert card_status("SYNA270319C90000", set(), {"SYNA"}) == "watchlist"
    assert card_status("SYNA270319C90000", {"SYNA270319C90000"}, set()) == "position"
    assert card_status("SYNB", set(), set()) == "none"
