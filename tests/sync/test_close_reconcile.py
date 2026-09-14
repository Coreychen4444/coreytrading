"""平仓收尾的自动化 —— 它会写 positions.json, 所以每条路径都要钉住。

源案例 NBIS : 事件落库了、broker.qty 归零了、regime 也降档了, 唯独
"清 lots + 标 closed" 没有模块负责 (position-tiers.md 把它写成手工步骤), 于是卡片
带着 1 股 lot 和 status="active" 过了一整天, 让一只 0 股的票当上次日简报的头号异动。
"""
import json

import pytest

from quant.core import io_json
from quant.sync import close_reconcile as cr


def _body(qty=0.0, status="active", lots=None, synthetic=False):
    broker = {"qty": qty}
    if synthetic:
        broker["synthetic"] = True
    return {
        "instrument": {"type": "stock"},
        "status": status,
        "broker": broker,
        "lots": lots if lots is not None else {"base": [], "position": []},
        "position_summary": {},
    }


def _episode(ticker="NBIS", pnl=-47.98, order_id="EXIT1", price=168.02):
    return {ticker: [{
        "ticker": ticker, "status": "closed", "closed_at": "2026-07-28T16:48:17-04:00",
        "realized_pnl": pnl,
        "exits": [{"order_id": order_id, "price": price, "timestamp": "2026-07-28T16:48:17-04:00"}],
    }]}


LOTS = {"base": [], "position": [{"qty": 1, "avg_cost": 216.0, "notes": "probe"}]}


# ── plan(): 谁该被收尾 ────────────────────────────────────────────

def test_flat_card_with_lots_is_planned():
    doc = {"tickers": {"NBIS": _body(lots=LOTS)}}
    (item,) = cr.plan(doc, _episode())

    assert item["ticker"] == "NBIS"
    assert item["lot_qty"] == 1
    assert item["prev_status"] == "active"
    assert item["realized_pnl_usd"] == -47.98
    assert item["exit_order_id"] == "EXIT1"
    assert item["episode_found"] is True


def test_held_card_is_never_touched():
    """qty>0 = 还持有, 哪怕 status 写着 closed (再入场留下的陈旧标记)。"""
    doc = {"tickers": {"MU": _body(qty=1.0, status="closed", lots=LOTS)}}
    assert cr.plan(doc, {}) == []


def test_flat_card_without_lots_is_already_clean():
    doc = {"tickers": {"NOK": _body(status="closed")}}
    assert cr.plan(doc, {}) == []


def test_live_synthetic_underlying_is_not_reconciled():
    doc = {"tickers": {
        "MRVL": _body(synthetic=True, lots=LOTS),
        "MRVL270115C150000": _body(qty=1.0),
    }}
    assert cr.plan(doc, {}) == []


def test_missing_episode_is_flagged_not_guessed():
    """broker 说 0 股但事件流对不上 —— 照样清理 (lots 归档不丢), 但把矛盾说出来。"""
    doc = {"tickers": {"NBIS": _body(lots=LOTS)}}
    (item,) = cr.plan(doc, {})

    assert item["episode_found"] is False
    assert item["realized_pnl_usd"] is None

    cr.apply([item], doc)
    assert "人工查" in doc["tickers"]["NBIS"]["exit_info"]["note"]


# ── apply(): 搬家, 不是删除 ────────────────────────────────────────

def test_apply_archives_lots_instead_of_deleting_them():
    doc = {"tickers": {"NBIS": _body(lots=LOTS)}}
    cr.apply(cr.plan(doc, _episode()), doc)
    body = doc["tickers"]["NBIS"]

    assert body["lots"] == {"base": [], "position": []}
    assert body["status"] == "closed"
    assert body["position_summary"]["total_qty"] == 0
    # cost basis 留在文件里, 不是只留在 git 历史里 —— 这是敢自动写的前提
    assert body["exit_info"]["closed_lots"] == LOTS
    assert body["exit_info"]["realized_pnl_usd"] == -47.98
    assert body["exit_info"]["reconciled_by"] == "close_reconcile"


def test_apply_is_idempotent():
    doc = {"tickers": {"NBIS": _body(lots=LOTS)}}
    cr.apply(cr.plan(doc, _episode()), doc)
    snapshot = json.dumps(doc, sort_keys=True)

    assert cr.plan(doc, _episode()) == []      # 第二轮无事可做
    assert json.dumps(doc, sort_keys=True) == snapshot


def test_apply_archives_the_agent_block():
    """收尾只动 lots/status/position_summary/exit_info。thesis 与 zone 是历史,
    留着给复盘和重进判断用。"""
    doc = {"tickers": {"NBIS": _body(lots=LOTS)}}
    doc["tickers"]["NBIS"]["agent"] = {"thesis": {"summary": "neocloud"},
                                       "zones": {"stop_level": 176}}
    cr.apply(cr.plan(doc, _episode()), doc)

    assert doc["tickers"]["NBIS"]["exit_info"]["closed_agent"]["zones"]["stop_level"] == 176
    assert not doc["tickers"]["NBIS"]["agent"].get("zones")


# ── run(): dry-run 绝不写盘 ───────────────────────────────────────

@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(io_json, "STATE_LOCK_PATH", tmp_path / ".state.lock")
    path = tmp_path / "positions.json"

    def _write(doc):
        path.write_text(json.dumps(doc), encoding="utf-8")

    _write({"tickers": {"NBIS": _body(lots=LOTS)}})
    monkeypatch.setattr(cr.state_io, "read_positions", lambda **_: json.loads(path.read_text()))
    monkeypatch.setattr(cr.state_io, "write_positions", lambda doc: _write(doc))
    monkeypatch.setattr(cr._ledger, "build_episodes", lambda **_: _episode())
    return path


def test_dry_run_reports_without_writing(state):
    before = state.read_text()
    res = cr.run(dry_run=True)

    assert len(res["planned"]) == 1 and res["reconciled"] == []
    assert state.read_text() == before      # "预览"能改交易状态本身就是设计错误


def test_run_writes_and_then_has_nothing_left_to_do(state):
    res = cr.run()
    assert len(res["reconciled"]) == 1

    body = json.loads(state.read_text())["tickers"]["NBIS"]
    assert body["status"] == "closed" and body["lots"]["position"] == []
    assert cr.run()["reconciled"] == []


def test_empty_active_card_is_closed_and_idempotent():
    doc = {"tickers": {"NBIS": _body()}}
    cr.apply(cr.plan(doc, _episode()), doc)
    assert doc["tickers"]["NBIS"]["exit_info"]["closed_at"] == "2026-07-28T16:48:17-04:00"
    assert cr.plan(doc, _episode()) == []


def test_shadow_archive_preserves_exit_and_can_return_to_watchlist():
    from quant.sync.watchlist_sync import closed_analysis
    body = _body(status="closed")
    body["agent"] = {"thesis": {"summary": "history"}, "zones": {"stop_level": 10},
                     "current_regime": {"cap_pct_nav": .4}}
    body["exit_info"] = {"closed_at": "2026-07-28", "closed_lots": LOTS,
                         "watchlist_readd": {"closed_at": "2026-07-28"}}
    doc = {"tickers": {"NBIS": body}}
    cr.apply(cr.plan(doc, {}), doc)
    assert not body["agent"].get("current_regime")
    assert body["exit_info"]["closed_lots"] == LOTS
    assert body["exit_info"]["watchlist_readd"] == {"closed_at": "2026-07-28"}
    assert closed_analysis(doc, set())["NBIS"]["agent"]["current_regime"]["cap_pct_nav"] == .4
    assert cr.plan(doc, {}) == []


def test_unknown_broker_quantity_is_not_flat():
    body = _body(lots=LOTS)
    body["broker"] = {}
    assert cr.plan({"tickers": {"NBIS": body}}, {}) == []


def test_archived_card_remains_schema_valid_even_when_broker_reports_reentry():
    from quant.core.schema import validate_positions_doc
    body = _body(lots=LOTS)
    body.update(pending_orders=[], agent={"thesis": {"summary": "x"},
                "action": {"intent": "hold"}, "zones": {"stop_level": 176}})
    doc = {"tickers": {"NBIS": body}}
    cr.apply(cr.plan(doc, _episode()), doc)
    assert validate_positions_doc(doc)[0] == []
    body["broker"]["qty"] = 1
    assert validate_positions_doc(doc)[0] == []
