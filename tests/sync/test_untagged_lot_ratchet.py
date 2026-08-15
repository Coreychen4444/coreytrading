"""Synthetic regression tests for lots that cannot be linked to order ids."""

import pytest

from quant.sync import lot_reconcile as lr


@pytest.mark.parametrize("lot, expected", [
    ({"qty": 2, "avg_cost": 50.0}, True),
    ({"qty": 2, "avg_cost": 50.0, "order_id": "OID_A"}, False),
    ({"qty": 2, "avg_cost": 50.0, "notes": "order OID_A; first tier"}, False),
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
