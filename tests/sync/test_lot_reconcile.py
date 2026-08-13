"""买入落账的自动化 —— 它会写 positions.json, 所以每条路径都要钉住。

源案例 : AAOI (2@123) 与 INTC (2@98.7 + 2@97.8) 的 fill 当晚就进了
trades.jsonl、broker.qty 当晚就对, 唯独"补一条 lot + 更新 position_summary"
没有模块负责 (取消计划内成交的注解之后, 它是规则内成交剩下的唯一
人工动作), 于是两张卡的台账错了一整天。schema 报了 `lots qty != broker qty`,
但那条 warn 只 print 到 stderr。

这里最该守的不是"能加 lot", 是**不该动手时不动手**: 部分卖出、数量对不上、
期权、结构豁免层 —— 全部只报不写。自动写台账的模块, 保守方向才是安全方向。
"""
import json

import pytest

from quant.sync import lot_reconcile as lr

ZONES = {
    "accumulation_zone_1": [95.5, 98.7],
    "accumulation_zone_2": [94.0, 95.5],
    "trim_zone": [105.0, 109.6],
    "no_chase_above": 102.4,
    "stop_level": 93.9,
}
REGIME = {"name": "B_repair_probe_98", "cap_pct_nav": 0.02,
          "participation": "zone_eligible", "source": "auto", "since": "2026-08-05"}


def _body(qty=4.0, lots=None, zones=ZONES, role=None, synthetic=False):
    broker = {"qty": qty}
    if synthetic:
        broker["synthetic"] = True
    instrument = {"type": "stock"}
    if role:
        instrument["role"] = role
    return {
        "instrument": instrument,
        "status": "active",
        "broker": broker,
        "agent": {"thesis": {"summary": "t"}, "action": {"intent": "hold"},
                  "zones": dict(zones) if zones else {}, "current_regime": dict(REGIME)},
        "lots": lots if lots is not None else {"base": [], "position": []},
        "position_summary": {},
    }


def _fill(qty, price, order_id, ts):
    return {"event_type": "fill", "side": "BUY", "ticker": "INTC", "qty": qty,
            "price": price, "order_id": order_id, "timestamp": ts}


ENTRIES = [_fill(2.0, 98.7, "OID_A", "2026-08-06T09:00:17-04:00"),
           _fill(2.0, 97.8, "OID_B", "2026-08-06T09:30:04-04:00")]


def _episode(entries=None, exits=None, direction="long", status="open"):
    return {"INTC": [{
        "ticker": "INTC", "direction": direction, "status": status,
        "opened_at": "2026-08-06T09:00:17-04:00", "closed_at": None,
        "entries": list(ENTRIES if entries is None else entries),
        "exits": list(exits or []),
    }]}


def _doc(body=None):
    return {"tickers": {"INTC": body if body is not None else _body()}}


# ── 该动手的: 多笔 fill 合并成台账 ─────────────────────────────────

def test_two_fills_become_two_lots_with_correct_arithmetic():
    doc = _doc()
    (item,) = lr.plan(doc, _episode())

    assert item["action"] == "add_lots"
    assert item["qty_added"] == 4 and item["cost_added"] == 393.0
    assert item["order_ids"] == ["OID_A", "OID_B"]        # 按成交时间排

    lr.apply([item], doc)
    lots = doc["tickers"]["INTC"]["lots"]["position"]
    assert [(l["qty"], l["avg_cost"]) for l in lots] == [(2, 98.7), (2, 97.8)]
    ps = doc["tickers"]["INTC"]["position_summary"]
    assert (ps["position_qty"], ps["position_cost_total"]) == (4, 393.0)
    assert (ps["total_qty"], ps["total_cost"]) == (4, 393.0)
    assert ps["base_qty"] == 0 and ps["base_cost_total"] == 0.0


def test_partial_fills_of_one_order_merge_into_one_lot():
    """一个 order_id → 一条 lot。分两笔成交时按数量加权 —— 否则 order_id 与 lot
    不再一一对应, 幂等判定 (已入账的 order 不再加) 就失去锚。"""
    doc = _doc(_body(qty=3.0))
    eps = _episode([_fill(1.0, 100.0, "OID_X", "2026-08-06T09:00:00-04:00"),
                    _fill(2.0, 97.0, "OID_X", "2026-08-06T09:05:00-04:00")])
    (item,) = lr.plan(doc, eps)

    assert len(item["lots"]) == 1
    assert item["lots"][0]["qty"] == 3
    assert item["lots"][0]["avg_cost"] == pytest.approx(98.0)


def test_plan_fields_are_derived_from_the_card_not_invented():
    doc = _doc()
    (item,) = lr.plan(doc, _episode())
    lot = item["lots"][0]

    assert lot["stop_loss"] == 93.9                  # ← zones.stop_level
    assert lot["target_exit"] == "trim 105-109.6"    # ← zones.trim_zone
    assert lot["order_id"] == "OID_A" and "OID_A" in lot["notes"]
    assert lot["entry_period"] == "2026-08-06 09:00 ET"
    assert "z1 内" in lot["zone_logic"] and "z1 95.5-98.7" in lot["zone_logic"]
    assert "B_repair_probe_98" in lot["zone_logic"] and "2.0%" in lot["zone_logic"]
    assert "catalyst" not in lot, "catalyst 要判断 —— 空着比编强"


def test_contemporaneous_zone_tag_beats_the_current_card():
    """成交当时记下的 zone_at_fill 是**那一刻**的读数; 卡片是**现在**的。
    有前者就用前者 —— 台账要记的是当时的判断。"""
    doc = _doc(_body(qty=2.0))
    eps = _episode([{**_fill(2.0, 125.0, "OID_T", "2026-08-05T21:01:42-04:00"),
                     "zone_at_fill": "zone1"}])
    (item,) = lr.plan(doc, eps)

    zl = item["lots"][0]["zone_logic"]
    assert "z1 内" in zl and "成交时标注" in zl
    assert "no_chase 之上" not in zl, "125 按当前卡片会读成别的区 — 应该以当时标注为准"


def test_backfill_after_a_repaint_says_it_is_retrospective():
    """卡片重画过之后再补的 lot, 用的是新 zone 读旧成交 (AAOI 8/5 成交在当时
    z1 上沿, 8/6 print 后重画, 同一笔按新卡读成"等待区")。回溯读数可以用,
    但不能冒充当时的判断 —— 必须自报口径。"""
    body = _body(qty=2.0)
    body["agent"]["zones"]["anchored_on"] = {"date": "2026-08-07", "basis": "print 后重画"}
    eps = _episode([_fill(2.0, 98.7, "OID_R", "2026-08-06T09:00:17-04:00")])
    (item,) = lr.plan(_doc(body), eps)

    zl = item["lots"][0]["zone_logic"]
    assert "回溯定位" in zl and "2026-08-07" in zl and "2026-08-06" in zl


def test_same_day_fill_carries_no_retrospective_caveat():
    """正常路径 (pipeline ≤6h 接上, 卡没重画过) 不该加这句噪音。"""
    body = _body(qty=2.0)
    body["agent"]["zones"]["anchored_on"] = {"date": "2026-08-05"}
    eps = _episode([_fill(2.0, 98.7, "OID_S", "2026-08-06T09:00:17-04:00")])
    (item,) = lr.plan(_doc(body), eps)
    assert "回溯" not in item["lots"][0]["zone_logic"]


def test_zone_logic_reports_a_fill_above_no_chase():
    """落点不粉饰: 买在 no_chase 之上就写在台账上, 复盘时看得见。"""
    doc = _doc(_body(qty=2.0))
    eps = _episode([_fill(2.0, 110.0, "OID_C", "2026-08-06T10:00:00-04:00")])
    (item,) = lr.plan(doc, eps)
    assert "no_chase 之上" in item["lots"][0]["zone_logic"]


def test_card_without_numeric_zones_still_gets_the_lot():
    """zone 是台账的注脚, 不是它的前提。没有 zone 也要记账 —— 少记一笔钱
    比少记一句 zone 语义严重得多。"""
    doc = _doc(_body(zones={}))
    (item,) = lr.plan(doc, _episode())

    assert item["qty_added"] == 4
    lot = item["lots"][0]
    assert "未定位" in lot["zone_logic"]
    assert "stop_loss" not in lot and "target_exit" not in lot


# ── 幂等 ──────────────────────────────────────────────────────────

def test_second_pass_adds_nothing():
    doc = _doc()
    lr.apply(lr.plan(doc, _episode()), doc)
    snapshot = json.dumps(doc, sort_keys=True)

    assert lr.plan(doc, _episode()) == []          # qty 已对齐
    assert json.dumps(doc, sort_keys=True) == snapshot


def test_order_id_written_in_prose_notes_counts_as_accounted():
    """2026-08-06 之前手写的 lot 把 order id 写在 notes 散文里。只认结构化字段
    的话, 第一次跑就会把那些 fill 再加一遍 = 台账翻倍。"""
    hand = {"base": [], "position": [
        {"qty": 2, "avg_cost": 98.7, "notes": "order OID_A; 计划内 ladder 首档"}]}
    doc = _doc(_body(qty=4.0, lots=hand))
    (item,) = lr.plan(doc, _episode())

    assert item["action"] == "add_lots"
    assert item["order_ids"] == ["OID_B"]           # A 已在手写 lot 里
    lr.apply([item], doc)
    ps = doc["tickers"]["INTC"]["position_summary"]
    assert (ps["position_qty"], ps["position_cost_total"]) == (4, 393.0)


def test_aligned_card_is_never_touched():
    """判据第一关是 sum(lots) == broker.qty —— 绝大多数轮次在这里就返回。"""
    aligned = {"base": [], "position": [{"qty": 4, "avg_cost": 98.25}]}
    assert lr.plan(_doc(_body(lots=aligned)), _episode()) == []


# ── 不该动手的: 只报不写 ──────────────────────────────────────────

def test_partial_sell_is_reported_not_guessed():
    """本轮 campaign 已有卖出 → 哪个 lot 被卖掉是判断不是对账 (close_reconcile
    划的同一条界)。真实案例: NBIS 8/1 买 7 股卖 6 股。"""
    doc = _doc(_body(qty=1.0))
    (item,) = lr.plan(doc, _episode(exits=[{"qty": 3.0, "price": 99.0}]))

    assert item["action"] == "needs_human"
    assert "部分卖出" in item["reason"]
    lr.apply([item], doc)
    assert doc["tickers"]["INTC"]["lots"]["position"] == []


def test_lots_exceeding_broker_is_reported_not_silently_trimmed():
    """卖出没落账 = lots 比 broker 多。自动删 lot 就是自动销毁 cost basis。"""
    heavy = {"base": [], "position": [{"qty": 6, "avg_cost": 98.0, "order_id": "OLD"}]}
    (item,) = lr.plan(_doc(_body(qty=4.0, lots=heavy)), _episode())

    assert item["action"] == "needs_human" and "卖出未落账" in item["reason"]


def test_quantity_mismatch_degrades_instead_of_writing_a_guess():
    """broker 说缺 4 股, 事件流只有 2 股未入账 —— 差额来源不明 (成交没落库?
    手工划转?), 不猜。"""
    doc = _doc()
    (item,) = lr.plan(doc, _episode([ENTRIES[0]]))

    assert item["action"] == "needs_human"
    assert item["pending_qty"] == 2 and item["gap"] == 4
    lr.apply([item], doc)
    assert doc["tickers"]["INTC"]["lots"]["position"] == []


def test_gap_without_any_open_episode_is_reported():
    doc = _doc()
    (item,) = lr.plan(doc, {})
    assert item["action"] == "needs_human" and "没有进行中的 episode" in item["reason"]


def test_short_campaign_is_not_auto_booked():
    (item,) = lr.plan(_doc(), _episode(direction="short"))
    assert item["action"] == "needs_human" and "增量 BUY" in item["reason"]


def test_structural_exemption_layer_is_skipped():
    """base 的语义是"exit_guard 不管它", 归属由结构授予而非成交产生
    (position-tiers.md)。自动把它记成 position = 悄悄把纪念仓拉进交易层。"""
    doc = {"tickers": {"SPCX": _body(qty=2.0, role="commemorative_hold")}}
    (item,) = lr.plan(doc, {})
    assert item["action"] == "skip" and "结构豁免" in item["reason"]


def test_option_card_is_skipped_with_a_reason():
    """期权 lot 要 Greeks 入场快照 + stop 基准判断 —— 都是判断, 不是对账。"""
    doc = {"tickers": {"GOOGL270115C390000": _body(qty=1.0)}}
    (item,) = lr.plan(doc, {})
    assert item["action"] == "skip" and "期权" in item["reason"]


def test_flat_card_is_left_to_close_reconcile():
    """qty == 0 是另一条自动化的辖区。两个模块的判据必须互斥, 否则同一张卡
    会被两边各写一半。"""
    stale = {"base": [], "position": [{"qty": 1, "avg_cost": 216.0}]}
    assert lr.plan(_doc(_body(qty=0.0, lots=stale)), _episode()) == []


# ── run(): dry-run 绝不写盘 ───────────────────────────────────────

@pytest.fixture
def state(tmp_path, monkeypatch):
    path = tmp_path / "positions.json"
    path.write_text(json.dumps(_doc()), encoding="utf-8")
    monkeypatch.setattr(lr.state_io, "read_positions", lambda **_: json.loads(path.read_text()))
    monkeypatch.setattr(lr.state_io, "write_positions",
                        lambda doc: path.write_text(json.dumps(doc), encoding="utf-8"))
    monkeypatch.setattr(lr._ledger, "build_episodes", lambda **_: _episode())
    return path


def test_dry_run_reports_without_writing(state):
    before = state.read_text()
    res = lr.run(dry_run=True)

    assert len(res["planned"]) == 1 and res["reconciled"] == []
    assert state.read_text() == before      # "预览"能改交易状态本身就是设计错误


def test_run_writes_and_then_has_nothing_left_to_do(state):
    res = lr.run()
    assert len(res["reconciled"]) == 1

    body = json.loads(state.read_text())["tickers"]["INTC"]
    assert body["position_summary"]["total_cost"] == 393.0
    assert lr.run()["reconciled"] == []


def test_render_names_blocked_cards(state):
    doc = _doc(_body(qty=1.0))
    txt = lr.render({"planned": lr.plan(doc, _episode(exits=[{"qty": 3.0, "price": 99.0}]))})
    assert "⚠️" in txt and "未动手" in txt
