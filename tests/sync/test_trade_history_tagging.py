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
    monkeypatch.setattr(th, "_option_plans", lambda: {})     # 反转探针授权文件: 默认空
    # 成本闸读「这笔之前」的同票事件 —— 读的是本测试写进 `out` 的, 永远不读 live 账本
    monkeypatch.setattr(th, "_events_of",
                        lambda t: [e for e in out if str(e.get("ticker")).upper() == t.upper()])
    return out


def _fill(ts, qty, price, ticker="INTC", side="BUY"):
    return {"event_type": "fill" if side == "BUY" else "close", "timestamp": ts, "ticker": ticker,
            "side": side, "qty": qty, "price": price}


def test_cost_gate_flags_a_later_order_below_average_cost_but_not_the_opening_ladder(written):
    """成本闸 (成本不向下摊): 建仓 20 @52 之后几天新下单 10 @49 = 摊低 → below_avg_cost + 均价收据;
    同一轮建仓计划的低档 (空仓时下的单, 或与首笔同日补挂) 成交在首档之下 = 豁免 (三档阶梯本来就逐档向下)。"""
    written.append(_fill("2025-03-10T09:41:00-04:00", 20.0, 52.0))
    th.append_new_fills_to_events([
        {**_row(qty=10.0, price=49.0, order_id="LATE"), "create_time": "2025-03-14 11:41:00",
         "updated_time": "2025-03-14 11:42:00"},
        {**_row(qty=5.0, price=50.5, order_id="OPEN3"), "create_time": "2025-03-10 06:40:00",
         "updated_time": "2025-03-12 10:00:00"},                    # 建仓那天挂的第三档, 两天后成交
    ])
    late, open3 = (next(e for e in written if e.get("order_id") == k) for k in ("LATE", "OPEN3"))
    assert "below_avg_cost" not in (open3.get("price_flags") or [])
    assert "below_avg_cost" in late["price_flags"]
    assert late["cost_at_fill"]["avg_cost"] == pytest.approx((20 * 52 + 5 * 50.5) / 25, abs=1e-3)
    assert [e["order_id"] for e in written[1:]] == ["OPEN3", "LATE"]       # 按成交时刻落库, 早的先写盘


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


# ── 期权 BUY 的正股价位盖章 (期权锚定正股回踩 z1, 不追高) ──
# 买在 no_chase 之上、转移确认之前的期权成交, 此前一律不盖 = 只能手写注解。

CARDS["SYN"] = {"instrument": {"type": "stock"},
                "agent": {"current_regime": {"name": "reclaim_93", "cap_pct_nav": _CONF,
                                             "since": "2026-08-26"},
                          "regime_history": [{"date": "2026-08-26", "direction": "up",
                                              "level": 93.0, "cap_pct_nav": _CONF}],
                          "zones": {"accumulation_zone_1": [101.9, 112.0],
                                    "accumulation_zone_2": [93.0, 101.9],
                                    "trim_zone": [125.0, 133.6],
                                    "no_chase_above": 117.0, "stop_level": 82.59}}}
CARDS["NOZONE"] = {"instrument": {"type": "stock"},
                   "agent": {"current_regime": {"name": "x", "cap_pct_nav": _CONF}}}


def _opt_row(code="SYN270115C100000", side="BUY", price=12.0, order_id="OPT1",
             when="2026-08-26 13:37:46"):
    return {"code": code, "side": side, "status": "FILLED_ALL", "dealt_qty": 1.0,
            "dealt_avg_price": price, "order_id": order_id, "updated_time": when}


def _bar(low, high, close, basis="60m", time="2026-08-26 14:30:00"):
    return {"time": time, "low": low, "high": high, "close": close, "basis": basis}


def test_option_buy_above_z1_is_stamped_from_the_underlying_bar(written, monkeypatch):
    monkeypatch.setattr(th, "underlying_bar_at", lambda und, ts: _bar(117.2, 120.5, 118.4))
    th.append_new_fills_to_events([_opt_row()])
    e = written[0]
    assert e["underlying_at_fill"]["price"] == 118.4 and e["underlying_at_fill"]["basis"] == "60m"
    assert e["underlying_at_fill"]["ambiguous"] is False
    assert e["zone_at_fill"] == "above_no_chase"
    assert set(e["price_flags"]) == {"above_z1", "above_no_chase"}


def test_option_buy_inside_z1_carries_no_flags(written, monkeypatch):
    monkeypatch.setattr(th, "underlying_bar_at", lambda und, ts: _bar(105.0, 110.0, 108.0))
    th.append_new_fills_to_events([_opt_row()])
    e = written[0]
    assert e["zone_at_fill"] == "zone1" and "price_flags" not in e


def test_option_buy_bar_straddling_z1_top_is_ambiguous_not_flagged(written, monkeypatch):
    monkeypatch.setattr(th, "underlying_bar_at", lambda und, ts: _bar(110.0, 115.0, 114.0))
    th.append_new_fills_to_events([_opt_row()])
    e = written[0]
    assert "price_flags" not in e and e["underlying_at_fill"]["ambiguous"] is True


def test_option_buy_without_a_bar_is_marked_unknown_for_a_human(written, monkeypatch):
    monkeypatch.setattr(th, "underlying_bar_at", lambda und, ts: None)
    th.append_new_fills_to_events([_opt_row()])
    e = written[0]
    assert e["underlying_at_fill"]["basis"] == "unknown" and e["underlying_at_fill"]["price"] is None
    assert "price_flags" not in e and "zone_at_fill" not in e


def test_option_buy_on_underlying_without_zones_or_sell_side_is_not_stamped(written, monkeypatch):
    monkeypatch.setattr(th, "underlying_bar_at", lambda und, ts: _bar(1.0, 2.0, 1.5))
    th.append_new_fills_to_events([_opt_row(code="NOZONE270115C10000", order_id="OPT2"),
                                   _opt_row(side="SELL", order_id="OPT3")])
    assert all("underlying_at_fill" not in e and "price_flags" not in e for e in written)


def test_underlying_bar_at_picks_the_60m_bar_ending_after_the_fill(monkeypatch, tmp_path):
    import sys, types
    bars = [{"time": "2026-08-26 10:30:00", "low": 101.9, "high": 130.3, "close": 106.0},
            {"time": "2026-08-26 13:30:00", "low": 112.0, "high": 123.0, "close": 118.0},
            {"time": "2026-08-26 14:30:00", "low": 116.5, "high": 128.8, "close": 125.0},
            {"time": "2026-08-26 16:00:00", "low": 114.0, "high": 126.0, "close": 116.14}]
    fake = types.SimpleNamespace(get_kline=lambda *a, **k: bars)
    monkeypatch.setitem(sys.modules, "quant.data.market_data", fake)
    b = th.underlying_bar_at("SYN", "2026-08-26T13:37:46-04:00")
    assert b["time"] == "2026-08-26 14:30:00" and b["basis"] == "60m"    # label = 结束时刻
    b = th.underlying_bar_at("SYN", "2026-08-26T17:05:00-04:00")        # 盘后 → 最后一根
    assert b["time"] == "2026-08-26 16:00:00"


def test_underlying_bar_at_falls_back_to_daily_cache_then_none(monkeypatch, tmp_path):
    import sys, types

    def boom(*a, **k):
        raise RuntimeError("OpenD down")
    monkeypatch.setitem(sys.modules, "quant.data.market_data", types.SimpleNamespace(get_kline=boom))
    monkeypatch.setattr(th, "KLINE_CACHE_DIR", tmp_path)
    (tmp_path / "SYN.jsonl").write_text(
        '{"time": "2026-08-26 00:00:00", "open": 130.31, "high": 133.34, "low": 101.88, "close": 116.14}\n')
    b = th.underlying_bar_at("SYN", "2026-08-26T13:37:46-04:00")
    assert b["basis"] == "1d" and b["low"] == 101.88 and b["high"] == 133.34
    assert th.underlying_bar_at("SYN", "2026-08-27T13:37:46-04:00") is None


# ── reversal_probe 盖章: 授权住 option_plans.json, 锚 = 信号日收盘 ──

def _rp_plans(valid_until="2026-08-28"):
    return {"NBIS": {"code": "NBIS270115C90000", "entry_class": "reversal_probe",
                     "signal_date": "2026-08-25", "signal_close": 80.0, "support": 76.0,
                     "valid_until": valid_until, "cap_frac": 0.09, "expiry": "2027-01-15",
                     "tiers": [{"premium": 12.0, "contracts": 1}]}}


def test_reversal_probe_fill_gets_auth_from_the_plan_and_the_signal_close_anchor(written, monkeypatch):
    monkeypatch.setattr(th, "_option_plans", lambda: _rp_plans())
    monkeypatch.setattr(th, "underlying_bar_at", lambda und, ts: _bar(81.0, 83.0, 82.5))
    th.append_new_fills_to_events([_opt_row(code="NBIS270115C90000", order_id="RP1",
                                            when="2026-08-26 10:05:00", price=12.0)])
    e = written[0]
    assert e["auth"]["entry_class"] == "reversal_probe" and e["auth"]["cap_pct_nav"] == 0.09
    assert e["auth"]["gate_no"] == 1
    assert e["price_flags"] == ["above_signal_close"]              # 整根 bar 在信号收盘 80 之上 = 追高
    assert e["underlying_at_fill"]["anchor"] == "signal_close" and e["underlying_at_fill"]["anchor_price"] == 80.0
    assert "zone_at_fill" not in e                                 # 本类不看 z1


def test_reversal_probe_fill_inside_or_straddling_the_anchor_is_clean_or_ambiguous(written, monkeypatch):
    monkeypatch.setattr(th, "_option_plans", lambda: _rp_plans())
    monkeypatch.setattr(th, "underlying_bar_at", lambda und, ts: _bar(79.0, 81.0, 80.5))
    th.append_new_fills_to_events([_opt_row(code="NBIS270115C90000", order_id="RP2",
                                            when="2026-08-26 10:05:00")])
    e = written[0]
    assert "price_flags" not in e and e["underlying_at_fill"]["ambiguous"] is True


def test_reversal_probe_fill_after_validity_has_no_auth(written, monkeypatch):
    monkeypatch.setattr(th, "_option_plans", lambda: _rp_plans(valid_until="2026-08-20"))
    monkeypatch.setattr(th, "underlying_bar_at", lambda und, ts: _bar(81.0, 83.0, 82.5))
    th.append_new_fills_to_events([_opt_row(code="NBIS270115C90000", order_id="RP3",
                                            when="2026-08-26 10:05:00")])
    e = written[0]
    assert "auth" not in e                                         # 过期 = 无授权成交, 归人判


def test_bottom_stock_fill_gets_auth_from_the_plan_and_flags_a_chase(written, monkeypatch):
    """抄底正股 BUY : auth 来自计划 stock 段 (cap_frac / 止跌低 / vehicle 正股);
    成交价 > 信号收盘 = above_signal_close (追高); 在其下不标。"""
    plans = {"INTC": {"entry_class": "reversal_probe", "signal_date": "2026-08-05", "signal_close": 98.0,
                      "support": 92.0, "valid_until": "2026-08-08", "stock": {"tiers": [{"price": 98.0, "shares": 3}],
                                                                             "cap_frac": 0.04}}}
    monkeypatch.setattr(th, "_option_plans", lambda: plans)
    th.append_new_fills_to_events([_row(code="INTC", order_id="BT1", price=98.7)])
    e = written[0]
    assert e["auth"]["entry_class"] == "reversal_probe" and e["auth"]["cap_pct_nav"] == 0.04
    assert e["auth"]["support"] == 92.0 and e["auth"]["vehicle"] == "正股"
    assert "above_signal_close" in e["price_flags"]
    th.append_new_fills_to_events([_row(code="INTC", order_id="BT2", price=97.5)])
    assert "above_signal_close" not in (written[1].get("price_flags") or [])
