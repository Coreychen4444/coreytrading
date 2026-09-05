"""Synthetic regression tests for lots that cannot be linked to order ids."""

import pytest

from quant.sync import lot_reconcile as lr



@pytest.mark.live_data
def test_no_untagged_lots_in_live_ledger():
    offenders = {t: q for t, q in _offenders().items() if t not in ALLOWLIST}
    assert not offenders, (
        "这些卡上有挂不上号的 lot (无 order_id, notes 里也没有) —— 下一次增量 BUY "
        f"会被 lot_reconcile 判「对不上, 不猜」并且每轮重复: {offenders}. "
        "从事件流认领 order_id 回填该 lot; 确无对应 order 才进 ALLOWLIST 并写明理由。"
    )


@pytest.mark.live_data
def test_allowlist_only_shrinks():
    """名单里的名字必须仍然是真 offender —— 修好了就要删掉, 否则棘轮会锈死:
    一个过期的豁免名会让下一条同样的雷悄悄躲在它后面 (同 ticker 再犯不会红)。"""
    stale = [t for t in ALLOWLIST if t not in _offenders()]
    assert not stale, f"ALLOWLIST 里这些卡已经没有挂不上号的 lot 了, 删掉: {stale}"


@pytest.mark.parametrize("lot,expected", [
    ({"qty": 2, "avg_cost": 213.0}, True),                                  # 裸 lot
    ({"qty": 2, "avg_cost": 213.0, "order_id": "OID_A"}, False),            # 结构化字段
    ({"qty": 2, "avg_cost": 213.0, "notes": "order OID_A; 首档"}, False),   # 手写在散文里
])
def test_untagged_lot_predicate(lot, expected):
    body = {"lots": {"base": [], "position": [lot]}}

    assert bool(lr.untagged_lots(body, {"OID_A"})) is expected


def test_quantity_mismatch_identifies_the_untagged_lot_without_writing():
    body = {
        "instrument": {"type": "stock"},
        "status": "active",
        "broker": {"qty": 12},
        "agent": {},
        "lots": {
            "base": [],
            "position": [{
                "qty": 2,
                "avg_cost": 50.0,
                "entry_period": "synthetic-period",
            }],
        },
        "position_summary": {},
    }
    entries = [
        {"side": "BUY", "ticker": "SYN", "qty": 2, "price": 50,
         "order_id": "OID_OLD", "timestamp": "2025-01-02T10:00:00-05:00"},
        {"side": "BUY", "ticker": "SYN", "qty": 10, "price": 55,
         "order_id": "OID_NEW", "timestamp": "2025-01-03T10:00:00-05:00"},
    ]
    episodes = {"SYN": [{
        "ticker": "SYN",
        "direction": "long",
        "status": "open",
        "entries": entries,
        "exits": [],
    }]}

    snapshot = {"tickers": {"SYN": body}}
    (item,) = lr.plan(snapshot, episodes)

    assert item["action"] == "needs_human"
    assert item["gap"] == 10
    assert item["pending_qty"] == 12
    assert item["untagged_qty"] == 2
    assert "lot 缺 order_id" in item["reason"]
    assert body["lots"]["position"] == [{
        "qty": 2,
        "avg_cost": 50.0,
        "entry_period": "synthetic-period",
    }]
