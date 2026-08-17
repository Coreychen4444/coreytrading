"""落库即盖章 —— tier / auth 在成交写入事件流的那一刻就定下来。

为什么这是同步层的活 (用户  批):  把注解规则改成"只给规则外
成交"之后, 计划内成交没人会去跑 `annotate_event` —— 而推导只活在那个手动 CLI 里。
结果 8/6 INTC 两笔计划内成交裸着落库: 没有标签 → `position_budget`
返回 None → 卡上预算行整条消失, 且 `can_add` 失败为开, 把一个加仓通道已关的仓位
播成"接近首档吸纳区"。**手工步骤 = 这一步实际上不存在**(data_discipline)。

判别式住 core (holdings.derive_tier / sizing.derive_auth), 因为
sync(2) 够不着 journal(5) —— 下沉之前, 同步层想盖章就得反向 import。
 分类轴退役: 盖 `auth` {cap_pct_nav, gate_no}, 不再盖 classification。
"""
import pytest

from quant.sync import trade_history_sync as th

from quant.core.sizing import NOTIONAL_CAP as _CONF

CARDS = {
    # cap 满额, 爬过两道给 cap 的 gate → 本笔 = gate2 (与 lots 无关)
    "INTC": {"instrument": {"type": "stock"},
             "agent": {"current_regime": {"name": "reclaim_98",
                                          "cap_pct_nav": _CONF, "since": "2026-08-05"},
                       "regime_history": [
                           {"date": "2026-08-05", "direction": "up", "cap_pct_nav": _CONF},
                           {"date": "2026-08-11", "direction": "up", "cap_pct_nav": _CONF}]},
             "lots": {"position": [
                 {"qty": 2, "avg_cost": 98.7, "entry_period": "2026-08-06"},
                 {"qty": 2, "avg_cost": 96.0, "entry_period": "2026-08-07"}]}},
    # cap 满额, 无转移史 (常规参与) → gate1
    "MSFT": {"instrument": {"type": "stock"},
             "agent": {"current_regime": {"name": "R_突破_473", "cap_pct_nav": _CONF}}},
    # cap 0 = 未授权 → 推不出来, 归人
    "NBIS": {"instrument": {"type": "stock"},
             "agent": {"current_regime": {"name": "持仓·无加仓授权", "cap_pct_nav": 0.0}}},
    # 结构豁免层
    "SPCX": {"instrument": {"type": "stock", "role": "commemorative_hold"},
             "agent": {"current_regime": {"name": "x", "cap_pct_nav": 0.02}}},
    "TSTX": {
        "instrument": {"type": "stock"},
        "agent": {
            "current_regime": {"name": "synthetic", "cap_pct_nav": _CONF},
            "zones": {
                "accumulation_zone_1": [70.0, 72.0],
                "accumulation_zone_2": [66.0, 70.0],
                "trim_zone": [78.0, 80.0],
                "no_chase_above": 75.0,
                "stop_level": 65.0,
            },
        },
    },
}


def _row(code="INTC", side="BUY", qty=2.0, price=98.7, order_id="OID1"):
    return {"code": code, "side": side, "status": "FILLED_ALL", "dealt_qty": qty,
            "dealt_avg_price": price, "order_id": order_id,
            "updated_time": "2026-08-06 09:00:17"}


@pytest.fixture
def written(monkeypatch):
    """拦住真正的账本写入 —— 成交账本不可回滚, 测试永远不碰它。"""
    out = []
    monkeypatch.setattr(th.state_io, "append_trade_event", lambda e: out.append(e))
    monkeypatch.setattr(th, "_cards", lambda: CARDS)
    return out


def test_buy_fill_gets_both_tags(written):
    th.append_new_fills_to_events([_row()])
    assert len(written) == 1
    assert written[0]["tier"] == "position"
    assert written[0]["auth"]["cap_pct_nav"] == _CONF
    assert written[0]["auth"]["gate_no"] == 2
    assert written[0]["auth"]["stamped_at"] == th.et_today()
    assert written[0]["stamp_stale_card_risk"] is True
    assert "classification" not in written[0]      #  停盖


def test_auth_gate_counts_from_card_transitions_not_lots(written):
    """盖的是**爬过几个给 cap 的 gate**—— MSFT 无转移史 (常规参与) = gate1,
    哪怕它将来有一堆 lots 也还是 gate1 (2026-08-13 二次定版, 判据换 regime_history)。"""
    th.append_new_fills_to_events([_row(code="MSFT", order_id="OID2")])
    assert written[0]["auth"]["cap_pct_nav"] == _CONF
    assert written[0]["auth"]["gate_no"] == 1


def test_unauthorized_fill_keeps_tier_but_leaves_auth_empty(written):
    """cap 0 = 无授权成交的形状。留空是**判断**不是失败 —— 由 brief 告警点名归人判。

    盖一个假授权比留空更坏: 它会让这笔成交读起来像在额度内, 而它本来就不在任何
    授权额度里。
    """
    th.append_new_fills_to_events([_row(code="NBIS", order_id="OID3")])
    assert written[0]["tier"] == "position"
    assert "auth" not in written[0]
    assert "需复核" in written[0]["auth_note"]


def test_same_day_stamp_has_no_stale_marker(written, monkeypatch):
    monkeypatch.setattr(th, "et_today", lambda: "2026-08-06")
    th.append_new_fills_to_events([_row(order_id="OID7")])
    assert written[0]["auth"]["stamped_at"] == "2026-08-06"
    assert "stamp_stale_card_risk" not in written[0]
    assert "auth_note" not in written[0]


def test_price_flags_are_stamped_from_card_zones(written):
    from quant.core.schema import validate_event

    th.append_new_fills_to_events([
        _row(code="TSTX", price=75.5, order_id="P1"),
        _row(code="TSTX", price=79.0, order_id="P2"),
        _row(code="TSTX", price=71.0, order_id="P3"),
    ])
    by_order = {event["order_id"]: event for event in written}
    assert by_order["P1"]["zone_at_fill"] == "above_no_chase"
    assert by_order["P1"]["price_flags"] == ["above_no_chase"]
    assert by_order["P2"]["price_flags"] == ["above_no_chase", "in_trim_zone"]
    assert by_order["P3"]["zone_at_fill"] == "zone1"
    assert "price_flags" not in by_order["P3"]
    assert all(validate_event(event) == [] for event in by_order.values())


def test_cards_without_zones_skip_price_stamping(written):
    th.append_new_fills_to_events([_row(order_id="OID8")])
    assert "zone_at_fill" not in written[0]
    assert "price_flags" not in written[0]


def test_structural_exemption_gets_base_tier(written):
    th.append_new_fills_to_events([_row(code="SPCX", order_id="OID4")])
    assert written[0]["tier"] == "base"


@pytest.mark.parametrize("side", ["SELL", "BUY_BACK"])
def test_sell_side_events_carry_no_auth(written, side):
    """auth 是**入场授权**快照, 卖出没有这个语义 (与既有历史事件一致)。"""
    th.append_new_fills_to_events([_row(side=side, order_id="OID5")])
    assert written[0]["tier"] == "position"
    assert "auth" not in written[0]


def test_unreadable_cards_do_not_crash_the_sync(monkeypatch):
    """读不到卡片 = 不盖章, 不是崩。成交落库比盖章重要 —— 账本先保住。"""
    out = []
    monkeypatch.setattr(th.state_io, "append_trade_event", lambda e: out.append(e))
    monkeypatch.setattr(th, "_cards", lambda: {})
    th.append_new_fills_to_events([_row(order_id="OID6")])
    assert len(out) == 1
    assert out[0]["tier"] == "position"          # 无卡片 → 默认层, 仍可推
    assert "auth" not in out[0]                  # 但授权推不出来 → 留空


def test_cards_merge_watchlist_so_first_entries_get_stamped(tmp_path, monkeypatch):
    """2026-08-13 COHR 案: watchlist 首次入场的授权住 watchlist 卡 (促升在成交
    之后), _cards 只读 positions 时每笔首次入场都裸落库、再被 fill_untagged
    冤枉。合并读取, positions 优先 (促升后它是真源)。"""
    import json
    pos = tmp_path / "positions.json"
    wl = tmp_path / "watchlist.json"
    pos.write_text(json.dumps({"tickers": {
        "DUP": {"agent": {"current_regime": {"name": "pos版", "cap_pct_nav": 0.40}}}}}))
    wl.write_text(json.dumps({"tickers": {
        "COHR": {"agent": {"current_regime": {"name": "常规参与", "cap_pct_nav": 0.40}}},
        "DUP": {"agent": {"current_regime": {"name": "wl版", "cap_pct_nav": 0.02}}}}}))
    monkeypatch.setattr(th, "POSITIONS_PATH", pos)
    monkeypatch.setattr(th, "WATCHLIST_PATH", wl)
    cards = th._cards()
    assert "COHR" in cards, "watchlist 卡没进盖章判据 — 首次入场又要裸落库"
    assert cards["DUP"]["agent"]["current_regime"]["name"] == "pos版"   # positions 优先
