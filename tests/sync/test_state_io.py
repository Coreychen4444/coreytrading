"""The system invariant: broker sync must NEVER destroy agent blocks/lots."""
import json

import pytest

from quant.sync import state_io
from quant.core import io_json


@pytest.fixture()
def tmp_state(tmp_path, monkeypatch):
    pos = tmp_path / "positions.json"
    monkeypatch.setattr(state_io, "POSITIONS_PATH", pos)
    monkeypatch.setattr(io_json, "STATE_LOCK_PATH", tmp_path / ".state.lock")
    return pos


def _seed(pos_path):
    doc = {
        "schema_version": 2,
        "generated_at": "2026-06-11T00:00:00Z",
        "tickers": {
            "MU": {
                "instrument": {"type": "stock"},
                "broker": {"source": "moomoo_opend", "qty": 9.0, "avg_cost": 387.38, "currency": "USD", "as_of": "old"},
                "status": "active",
                "agent": {
                    "thesis": {"summary": "DRAM supercycle — DO NOT LOSE ME", "status": "intact", "conviction": "high", "last_reviewed": "2026-06-11T00:00:00Z"},
                    "action": {"intent": "hold", "reason": "core", "confidence": "high"},
                    "current_regime": {"name": "R_hold", "cap_pct_nav": 0.05,
                                       "participation": "zone_eligible",
                                       "since": "2026-06-11", "source": "auto"},
                    "zones": {"accumulation_zone_1": [820.0, 860.0], "stop_level": 810.0},
                },
                "lots": {"base": [{"qty": 9.0, "avg_cost": 378.11, "notes": "precious"}], "position": []},
                "position_summary": {"base_qty": 9.0},
                "pending_orders": [],
            }
        },
    }
    pos_path.write_text(json.dumps(doc), encoding="utf-8")
    return doc


def test_missing_file_raises_not_skeleton(tmp_state):
    with pytest.raises(FileNotFoundError):
        state_io.read_positions()
    # explicit bootstrap still possible
    doc = state_io.read_positions(allow_missing=True)
    assert doc["tickers"] == {}


def test_broker_sync_preserves_agent_and_lots(tmp_state):
    _seed(tmp_state)
    state_io.update_broker_blocks({
        "MU": {"qty": 9.0, "avg_cost": 387.38, "currency": "USD", "current_price": 918.08},
    })
    doc = json.loads(tmp_state.read_text())
    mu = doc["tickers"]["MU"]
    assert mu["agent"]["thesis"]["summary"] == "DRAM supercycle — DO NOT LOSE ME"
    assert mu["agent"]["zones"]["stop_level"] == 810.0
    assert mu["lots"]["base"][0]["notes"] == "precious"
    assert mu["broker"]["current_price"] == 918.08
    assert mu["broker"]["source"] == "moomoo_opend"  # defaulted


def test_new_ticker_gets_stub_for_review(tmp_state):
    _seed(tmp_state)
    state_io.update_broker_blocks({"NVDA": {"qty": 1.0, "avg_cost": 100.0, "currency": "USD"}})
    doc = json.loads(tmp_state.read_text())
    nvda = doc["tickers"]["NVDA"]
    assert "NEW POSITION" in nvda["agent"]["thesis"]["summary"]
    assert nvda["lots"] == {"base": [], "position": []}


def test_option_stub_parses_occ(tmp_state):
    _seed(tmp_state)
    state_io.update_broker_blocks({"NOK270115C15000": {"qty": 16.0, "avg_cost": 2.647, "currency": "USD"}})
    doc = json.loads(tmp_state.read_text())
    inst = doc["tickers"]["NOK270115C15000"]["instrument"]
    assert inst["type"] == "option"
    assert inst["option"]["underlying"] == "NOK"
    assert inst["option"]["strike"] == 15.0
    assert inst["option"]["expiry"] == "2027-01-15"


def test_schema_violation_aborts_write(tmp_state):
    doc = _seed(tmp_state)
    doc["tickers"]["MU"]["agent"]["thesis"]["summary"] = ""  # structural breakage on disk
    tmp_state.write_text(json.dumps(doc), encoding="utf-8")
    before = tmp_state.read_text()
    with pytest.raises(RuntimeError, match="schema validation"):
        state_io.update_broker_blocks({"MU": {"qty": 9.0, "currency": "USD"}})
    assert tmp_state.read_text() == before  # nothing persisted


def test_watchlist_roundtrip_and_validation(tmp_path, monkeypatch):
    """ 裁决 A: watchlist 自动切需要合法读写路径 —— write_positions
    不能写 watchlist, 镜像 helper 必须同样过 schema 校验。"""
    wl = tmp_path / "watchlist.json"
    monkeypatch.setattr(state_io, "WATCHLIST_PATH", wl)
    doc = {"schema_version": 2, "generated_at": "t", "tickers": {}}
    state_io.write_watchlist(doc)
    assert state_io.read_watchlist() == doc


# ── 券商不再返回的卡 = 已平仓 (, NBIS 源案例) ──────────────

def _card(qty, source="moomoo_opend"):
    """一张 schema 过得去的最简活跃卡。"""
    return {
        "instrument": {"type": "stock"},
        "broker": {"source": source, "qty": qty, "avg_cost": 100.0,
                   "currency": "USD", "current_price": 100.0, "as_of": "2026-08-12"},
        "status": "active",
        "agent": {
            "thesis": {"summary": "t", "status": "intact", "conviction": "high",
                       "last_reviewed": "2026-08-12T00:00:00Z"},
            "action": {"intent": "hold", "reason": "r", "confidence": "high"},
            "current_regime": {"name": "R_x", "cap_pct_nav": 0.40,
                               "participation": "zone_eligible",
                               "since": "2026-08-12", "source": "auto"},
            "zones": {"accumulation_zone_1": [90.0, 95.0], "stop_level": 85.0},
        },
        "lots": {"base": [], "position": []},
        "position_summary": {},
        "pending_orders": [],
    }


def _seed_cards(pos_path, cards):
    doc = {"schema_version": 2, "generated_at": "2026-08-14T00:00:00Z", "tickers": cards}
    pos_path.write_text(json.dumps(doc), encoding="utf-8")
    return doc


def test_absent_position_is_zeroed_because_the_broker_stopped_listing_it(tmp_state):
    """券商这一轮没返回 NBIS = 它已经平掉了 → qty 归零, 让 close_reconcile 当轮接上。

    源案例 : NBIS 19:59 ET (盘后) 卖掉最后 1 股 —— 落在当天最后一次
    快照 (18:26 ET) **之后**。次日券商列表里整行消失, 而旧实现是"按 ticker 合并",
    没返回的卡一个字节都不碰 ⇒ qty 永远停在 1, 两天里它一直是"活跃持仓 1 股",
    拿着 gate1 授权、占着冲击预算, 且 lot_gap 比的是 lots(1) 与 broker(1) = 静默。
    """
    _seed_cards(tmp_state, {"INTC": _card(29.0), "NBIS": _card(1.0)})
    state_io.update_broker_blocks(
        {"INTC": {"qty": 29.0, "current_price": 105.0, "currency": "USD"}},
        zero_absent=True)
    out = json.loads(tmp_state.read_text())["tickers"]
    assert out["NBIS"]["broker"]["qty"] == 0.0
    assert out["NBIS"]["broker"]["qty_source"] == "absent_from_broker"
    assert out["INTC"]["broker"]["qty"] == 29.0
    # agent/lots 照旧不许动 (本模块的系统不变量)
    assert out["NBIS"]["agent"]["zones"]["stop_level"] == 85.0


def test_zero_absent_is_off_by_default(tmp_state):
    """默认关 —— 调用方要能确认"这一轮拿到的是完整持仓列表"才准开。
    `fetch_positions` 失败会回落读卡片自己的旧值, 拿那份去判"谁不在里面"会把
    整本书清零, 而下游 close_reconcile 会跟着清 lots。"""
    _seed_cards(tmp_state, {"INTC": _card(29.0), "NBIS": _card(1.0)})
    state_io.update_broker_blocks({"INTC": {"qty": 29.0, "currency": "USD"}})
    assert json.loads(tmp_state.read_text())["tickers"]["NBIS"]["broker"]["qty"] == 1.0


def test_zero_absent_never_touches_another_brokers_position(tmp_state):
    """IBKR 的持仓根本不在 moomoo 的响应里 —— 碰它等于每轮把它清零一次。"""
    _seed_cards(tmp_state, {"INTC": _card(29.0),
                            "SIVE": _card(5.0, source="ibkr_stockholm")})
    state_io.update_broker_blocks({"INTC": {"qty": 29.0, "currency": "USD"}},
                                  zero_absent=True)
    assert json.loads(tmp_state.read_text())["tickers"]["SIVE"]["broker"]["qty"] == 5.0


# ── 写闸的归责口径 ────────────────────────────────────────────────
#
# 「整份文件必须干净」的实际效果是: 一张早就不合规的卡冻结这个文件的所有
# 写路径 —— 一个空 cap 就能让整本零转移, 连带同日达标的其他卡也没落卡,
# 外面只看到「无 regime 转移」。判据 = 这次写入有没有把文件弄坏。

def _two_card_book(pos_path):
    doc = _seed(pos_path)
    doc["tickers"]["SYNB"] = json.loads(json.dumps(doc["tickers"]["MU"]))
    pos_path.write_text(json.dumps(doc), encoding="utf-8")
    return doc


def test_untouched_broken_card_does_not_block_another_cards_write(tmp_state, capsys):
    doc = _two_card_book(tmp_state)
    doc["tickers"]["SYNB"]["agent"]["thesis"]["summary"] = ""      # 坏卡, 本次不碰
    tmp_state.write_text(json.dumps(doc), encoding="utf-8")
    doc["tickers"]["MU"]["agent"]["action"]["reason"] = "updated"  # 只动 MU
    state_io.write_positions(doc)
    on_disk = json.loads(tmp_state.read_text())
    assert on_disk["tickers"]["MU"]["agent"]["action"]["reason"] == "updated"
    assert "既存错误未被本次写入触及" in capsys.readouterr().err


def test_touching_a_broken_card_still_aborts(tmp_state):
    """「没碰就不用修」不能变成绕过校验的后门: 动了这张卡就得把它留在合规状态。"""
    doc = _two_card_book(tmp_state)
    doc["tickers"]["SYNB"]["agent"]["thesis"]["summary"] = ""
    tmp_state.write_text(json.dumps(doc), encoding="utf-8")
    before = tmp_state.read_text()
    doc["tickers"]["SYNB"]["agent"]["action"]["reason"] = "touched it"
    with pytest.raises(RuntimeError, match="schema validation"):
        state_io.write_positions(doc)
    assert tmp_state.read_text() == before


def test_a_newly_introduced_error_always_aborts(tmp_state):
    doc = _seed(tmp_state)
    before = tmp_state.read_text()
    doc["tickers"]["MU"]["agent"]["current_regime"]["cap_pct_nav"] = None
    with pytest.raises(RuntimeError, match="schema validation"):
        state_io.write_positions(doc)
    assert tmp_state.read_text() == before


def test_a_document_level_error_is_never_waived(tmp_state):
    """非 ticker 归属的错误是整本的结构, 不归任何单卡 —— 即使是旧错也一律拦。"""
    tmp_state.write_text(json.dumps({"schema_version": 2}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="schema validation"):
        state_io.write_positions({"schema_version": 2, "generated_at": "2026-06-12T00:00:00Z"})
