"""Mechanical risk receipt rendering: deterministic, authority-free, tolerant of gaps."""
import json

from quant.report import risk_receipt


def _data():
    return {
        "account_snapshot": {"total_assets": 20000, "power": 5000},
        "power_room_receipt": {"room": 4200},
        "portfolio_risk_receipts": {
            "receipt_id": "r" * 64, "as_of": "2026-09-09T00:00:00+00:00",
            "freshness": {"status": "current"},
            "usage": {"status": "computed", "values": {"used_budget": 0.0466, "used_gross": 0.298},
                      "assumptions": ["delta_assumed_one:X"], "unknowns": []},
            "entries": {
                "SYN": {"status": "computed", "values": {"final": 6572.9, "binding": "power"},
                        "inputs": {"gate_no": 1, "price": 100.0, "binding_stop": 90.0, "exposure": 0,
                                   "event_shrink": 1}, "unknowns": []},
                "GAP": {"status": "unknown", "values": None, "inputs": {},
                        "unknowns": ["binding_stop_missing_or_invalid"]},
            },
            "batch_allocation": {"status": "unknown"},
            "unknowns": ["pending_order_reservations_not_computed"],
        },
        "holding_risk_inputs": {
            "positions": {"SYN": {"membership": "holding", "card": {"broker": {"qty": 20}},
                                  "exit_guard_receipt": {"ratchet": {"stage": "inactive", "floor": None},
                                                         "trail": {"trail": 182.41, "broken": False},
                                                         "no_add_lock": False, "g01_crossed": {},
                                                         "structure": {"trend": "down"}},
                                  "unknowns": []}},
            "portfolio_at_risk": {"status": "unknown"}, "unknowns": [],
        },
    }


def test_build_is_structured_and_authority_free():
    view = risk_receipt.build(_data())
    assert view["authority"] == "analysis_only" and view["kind"] == "mechanical_risk_receipt"
    assert view["book"]["nav"] == 20000 and view["book"]["power_room"] == 4200
    entries = {e["ticker"]: e for e in view["entries"]}
    assert entries["SYN"]["final"] == 6572.9 and entries["SYN"]["binding"] == "power"
    assert entries["GAP"]["status"] == "unknown" and entries["GAP"]["final"] is None
    assert view["exits"][0]["trail"] == 182.41 and view["exits"][0]["g01_crossed"] is False
    assert view["portfolio_at_risk"] == "unknown" and view["not_provided_by_design"]
    assert "pending_order_reservations_not_computed" in view["unknowns"]


def test_render_lists_every_entry_and_holding_without_inventing_numbers():
    text = risk_receipt.render(risk_receipt.build(_data()))
    assert "| SYN | 1 | 6,573 | power | 100.00 | 90.00 | 0 | computed |" in text
    assert "| GAP | - | unknown | - | unknown | unknown | unknown | unknown |" in text
    assert "| SYN | 20 | inactive | unknown | 182.41 | 否 | off | 否 | down |" in text
    assert "零授权效力" in text and "不可相加" in text


def test_empty_snapshot_renders_unknowns_not_defaults():
    view = risk_receipt.build({})
    assert view["book"]["status"] == "unknown" and view["entries"] == [] and view["exits"] == []
    text = risk_receipt.render(view)
    assert "NAV unknown" in text and "0" not in text.split("\n")[1].replace("0%", "")


def test_cli_accepts_run_record_and_snapshot(tmp_path, capsys):
    snapshot = tmp_path / "snap.json"
    snapshot.write_text(json.dumps({"snapshot": {"data": _data()}}))
    assert risk_receipt.main([str(snapshot), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["entries"][0]["ticker"] == "GAP"
    plain = tmp_path / "data.json"
    plain.write_text(json.dumps({"data": _data()}))
    assert risk_receipt.main([str(plain)]) == 0
    assert "机械风控收据" in capsys.readouterr().out
