"""valuation_sync: 账本最新行 → agent.valuation 机器区 (对账式幂等 / 不适用不写 / schema 词表与出处 / fyi 零授权词汇)。"""
import json
import re

import pytest

from quant.core.schema import validate_positions_doc, validate_watchlist_doc
from quant.sync import valuation_sync as VS


def _row(ticker="GOOGL", ts="2026-09-01T18:16:55-04:00", **kw):
    r = {"timestamp": ts, "ticker": ticker, "price": 335.02, "fair_value": 262.77, "verdict": "高估", "ratio": 0.275,
         "confidence": "中", "archetype": "platform", "implied_cagr": 0.18, "implied_target_margin": 0.47,
         "implied_cagr_at_current_margin": None, "growth_creates_value": True, "spec": {"horizon_years": 10},
         "news": {"applied": [{"id": "o1"}]}}
    r.update(kw)
    return r


def _docs(pos_status="active", wl_status="watching"):
    pos = {"tickers": {"GOOGL": {"status": pos_status, "instrument": {"type": "stock"}, "broker": {"qty": 5}, "agent": {"zones": None}},
                       "NVDA270319C220000": {"status": "active", "instrument": {"type": "option"}, "broker": {"qty": 1}, "agent": {}}}}
    wl = {"tickers": {"MU": {"status": wl_status, "instrument": {"type": "stock"}, "broker": {"qty": 0}, "agent": {}},
                      "MSTR": {"status": "watching", "instrument": {"type": "stock"}, "broker": {"qty": 0}, "agent": {}}}}
    return pos, wl


def test_latest_rows_keeps_last_non_skipped(tmp_path):
    p = tmp_path / "v.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in [_row(ts="a", fair_value=1.0), _row(ts="b", fair_value=2.0),
                                                     {"ticker": "MSTR", "skipped": True}, {"ticker": "X", "timestamp": "c"}]) + "\n")
    rows = VS.latest_rows(p)
    assert set(rows) == {"GOOGL"} and rows["GOOGL"]["fair_value"] == 2.0


def test_fyi_line_and_block_are_judgment_only():
    line = VS.fyi_line(_row())
    assert line.startswith("📐[FYI] 估值 高估 +28% (置信中)") and "公允 $263 vs $335" in line
    assert "要求年2起 CAGR +18%" in line and "新闻 1 条" in line and line.endswith("零授权")
    for banned in (r"\bcap\b", "股数", "挂单", r"\bGO\b", r"\bVETO\b"):
        assert not re.search(banned, line)
    nog = VS.fyi_line(_row(growth_creates_value=False, implied_cagr=None))
    assert "增长不创造价值" in nog
    flat = VS.fyi_line(_row(implied_cagr_at_current_margin=0.3, spec={"horizon_years": 10, "target_margin": 0.33, "margin_start": 0.33, "revenue_level_ratio": 1.0}))
    assert "峰值不回归" not in flat, "利润率不回归的票这句是重复"
    cyc = VS.fyi_line(_row(implied_cagr_at_current_margin=0.25, spec={"horizon_years": 7, "target_margin": 0.20, "margin_start": 0.66, "revenue_level_ratio": 1.0}))
    assert "峰值不回归需 +25%" in cyc
    b = VS.block_from_row(_row())
    assert b["as_of"] == "2026-09-01" and b["implied_horizon_years"] == 10 and b["news_applied"] == 1
    assert b["source"] == "valuations.jsonl 2026-09-01T18:16:55-04:00" and b["synced_at"]


def test_sync_cards_writes_targets_only_idempotent_and_reports_missing():
    pos, wl = _docs()
    rows = {"GOOGL": _row(), "MU": _row(ticker="MU", price=933.0, fair_value=230.0, verdict="高估", ratio=3.05, confidence="低", archetype="semi_cyclical")}
    res = VS.sync_cards(pos, wl, rows)
    assert res["synced"] == ["positions:GOOGL", "watchlist:MU"] and res["touched"] == {"positions": True, "watchlist": True}
    assert res["missing"] == ["watchlist:MSTR"]
    assert "valuation" not in pos["tickers"]["NVDA270319C220000"]["agent"], "期权卡归 underlying"
    assert pos["tickers"]["GOOGL"]["agent"]["valuation"]["verdict"] == "高估"
    def verr(errs):
        return [e for e in errs if "valuation" in e]
    assert verr(validate_positions_doc(pos)[0]) == [] and verr(validate_watchlist_doc(wl)[0]) == [], "合成卡缺的其他字段与本块无关"
    again = VS.sync_cards(pos, wl, rows)
    assert again["synced"] == [] and again["touched"] == {"positions": False, "watchlist": False}
    rows["GOOGL"] = _row(ts="2026-09-08T10:00:00-04:00", fair_value=270.0)
    third = VS.sync_cards(pos, wl, rows, only={"GOOGL"})
    assert third["synced"] == ["positions:GOOGL"] and pos["tickers"]["GOOGL"]["agent"]["valuation"]["as_of"] == "2026-09-08"
    closed, _ = _docs(pos_status="closed")
    assert VS.sync_cards(closed, {"tickers": {}}, rows)["synced"] == [], "closed 卡不写"


def test_schema_rejects_bad_valuation_block():
    pos, _ = _docs()
    good = VS.block_from_row(_row())

    def verr():
        return [e for e in validate_positions_doc(pos)[0] if "valuation" in e]
    pos["tickers"]["GOOGL"]["agent"]["valuation"] = dict(good)
    assert verr() == []
    for bad, needle in (({"verdict": "买入"}, "verdict"), ({"confidence": "very"}, "confidence"), ({"as_of": "yesterday"}, "as_of"),
                        ({"fair_value": "263"}, "fair_value"), ({"source": ""}, "source"), ({"fyi": ""}, "fyi")):
        pos["tickers"]["GOOGL"]["agent"]["valuation"] = {**good, **bad}
        errs = verr()
        assert errs and any(needle in e for e in errs), (bad, errs)
    pos["tickers"]["GOOGL"]["agent"]["valuation"] = "x"
    assert verr()
    pos["tickers"]["GOOGL"]["agent"].pop("valuation")
    assert verr() == []


def test_cli_main_syncs_via_state_io(tmp_path, monkeypatch, capsys):
    p = tmp_path / "v.jsonl"
    p.write_text(json.dumps(_row()) + "\n")
    pos, wl = _docs()
    written = {}
    monkeypatch.setattr(VS, "VALUATIONS_PATH", p)
    monkeypatch.setattr(VS.state_io, "read_positions", lambda **k: pos)
    monkeypatch.setattr(VS.state_io, "read_watchlist", lambda **k: wl)
    monkeypatch.setattr(VS.state_io, "write_positions", lambda d: written.setdefault("pos", d))
    monkeypatch.setattr(VS.state_io, "write_watchlist", lambda d: written.setdefault("wl", d))
    monkeypatch.setattr(VS, "state_lock", lambda *a, **k: __import__("contextlib").nullcontext())
    assert VS.main(["--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["synced"] == ["positions:GOOGL"] and "pos" in written and "wl" not in written
