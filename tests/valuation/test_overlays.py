"""overlays.py: 契约校验 / 去重与撤销 / 到期解析 / 可靠度门 / MACRO scope / CLI append-only。"""
import json

import pytest

from quant.valuation import overlays as O


def rec(**kw):
    base = {"id": "r1", "date": "2026-09-01", "ticker": "AMZN", "source": "https://x", "reliability": "filing",
            "channel": "pe", "driver": "pe_multiplier", "delta": -0.05, "valid_until": "2026-12-01", "note": "折价"}
    base.update(kw)
    return base


def test_valid_record_passes_and_id_is_deterministic():
    O.validate_record(rec())
    r = rec(); r.pop("id")
    assert O.make_id(r) == O.make_id(dict(r)) and O.make_id(r).startswith("2026-09-01-AMZN-pe_multiplier-")


@pytest.mark.parametrize("bad, msg", [
    (rec(channel="eps", driver="pe_multiplier"), "不属于 eps"),
    (rec(channel="pe", driver="growth_start"), "不属于 pe"),
    (rec(delta=5.0), "上限"),
    (rec(delta="0.1"), "delta 必须是数"),
    (rec(reliability="blog"), "reliability"),
    (rec(valid_until="soon"), "valid_until"),
    (rec(valid_until="2026-08-01"), "早于 date"),
    (rec(ticker="amzn"), "大写"),
    (rec(scope={"archetypes": ["platform"]}), "只有 MACRO"),
    (rec(ticker="MACRO", scope={"archetypes": ["nope"]}), "scope.archetypes"),
    (rec(note=""), "缺 note"),
    (rec(direction=3), "direction"),
    ({"id": "c", "kind": "cancel"}, "缺 note"),
])
def test_validation_rejects(bad, msg):
    with pytest.raises(ValueError, match=msg):
        O.validate_record(bad)


def test_collapse_last_wins_and_cancel_removes():
    rows = [rec(id="a", delta=-0.05), rec(id="a", delta=-0.10), rec(id="b"), {"id": "b", "kind": "cancel", "note": "撤"}]
    c = O.collapse(rows)
    assert set(c) == {"a"} and c["a"]["delta"] == -0.10


def test_active_filters_by_ticker_date_expiry_reliability_and_scope():
    rows = [
        rec(id="ok"),
        rec(id="other", ticker="META"),
        rec(id="future", date="2026-09-05"),
        rec(id="expired", valid_until="2026-09-01"),
        rec(id="old", date="2026-01-01", valid_until="2026-12-31"),
        rec(id="rumor", reliability="rumor"),
        rec(id="np", channel="eps", driver="revenue_base", delta=0.05, valid_until="next_print"),
        rec(id="macro_all", ticker="MACRO", driver="erp", delta=0.005, valid_until="next_tier1"),
        rec(id="macro_scoped", ticker="MACRO", driver="erp", delta=0.01, scope={"archetypes": ["semi_cyclical"]}),
        rec(id="macro_tk", ticker="MACRO", driver="scenario_tilt", delta=-0.1, scope={"tickers": ["AMZN"]}),
    ]
    a = O.active_overlays(rows, ticker="AMZN", archetype="platform", as_of="2026-09-02",
                          next_print_day="2026-10-30", next_tier1_day="2026-09-04")
    ids = {o["id"] for o in a["applied"]}
    assert ids == {"ok", "macro_tk"}
    assert {i["id"]: i["reason"] for i in a["ignored"]}.keys() == {"expired", "old", "rumor", "np", "macro_all"}
    assert a["flags"] == []
    # 未固定的期限不应用；固定期限不会被最新日历顺延。
    b = O.active_overlays([rec(id="np", channel="eps", driver="revenue_base", delta=0.05, valid_until="next_print")],
                          ticker="AMZN", archetype="platform", as_of="2026-09-02")
    assert b["applied"] == [] and b["ignored"]
    c = O.active_overlays([rec(id="np", channel="eps", driver="revenue_base", delta=0.05, valid_until="next_print", resolved_until="2026-10-30")],
                          ticker="AMZN", archetype="platform", as_of="2026-10-31", next_print_day="2027-02-01")
    assert c["applied"] == [] and c["ignored"][0]["reason"].startswith("已到期")


def test_manual_records_follow_same_contract():
    m = O.manual_records("MU", [{"date": "2026-09-01", "source": "s", "driver": "growth_start", "delta": 0.02, "note": "n"},
                               {"date": "2026-09-01", "source": "s", "driver": "erp", "delta": 0.01, "note": "n", "valid_until": "2026-10-01"}])
    with pytest.raises(ValueError, match="valid_until"):
        O.validate_record(m[0])
    O.validate_record(m[1])
    assert m[0]["channel"] == "eps" and m[0]["reliability"] == "manual" and m[0]["valid_until"] is None
    assert m[1]["channel"] == "pe" and m[1]["valid_until"] == "2026-10-01"


def test_manual_finite_effects_keep_baseline_and_cannot_arrive_early():
    rows = O.manual_records("SYN", [{"date": "2026-09-01", "source": "synthetic", "driver": "net_interest_after_tax",
        "delta": 100, "note": "synthetic", "valid_until": "2026-10-01", "as_of": "2026-09-02T13:00:00Z",
        "baseline_id": "baseline", "start_year": 1, "end_year": 1}])
    assert rows[0]["end_year"] == 1
    assert not O.active_overlays(rows, ticker="SYN", archetype="platform", as_of="2026-09-02T12:00:00Z", baseline_id="baseline")["applied"]
    assert O.active_overlays(rows, ticker="SYN", archetype="platform", as_of="2026-09-02T14:00:00Z", baseline_id="baseline")["applied"]
    assert not O.active_overlays(rows, ticker="SYN", archetype="platform", as_of="2026-09-02T14:00:00Z", baseline_id="other")["applied"]


def test_add_and_cancel_are_append_only_and_validated(tmp_path):
    p = tmp_path / "overlays.jsonl"
    r = O.add_record({"ticker": "AMZN", "date": "2026-09-01", "source": "https://x", "reliability": "filing", "channel": "pe",
                      "driver": "pe_multiplier", "delta": -0.05, "valid_until": "2026-12-01", "note": "n"}, path=p)
    assert r["id"] and r["as_of"]
    with pytest.raises(ValueError):
        O.add_record({"ticker": "AMZN", "date": "2026-09-01", "source": "s", "reliability": "filing", "channel": "pe",
                      "driver": "growth_start", "delta": 0.1, "valid_until": "2026-12-01", "note": "n"}, path=p)
    assert len(p.read_text().splitlines()) == 1, "校验失败不许写"
    O.cancel_record(r["id"], "撤销", path=p)
    rows = [json.loads(l) for l in p.read_text().splitlines()]
    assert len(rows) == 2 and rows[1]["kind"] == "cancel"
    assert O.collapse(rows) == {}


def test_cli_add_list_cancel(tmp_path, monkeypatch, capsys):
    p = tmp_path / "overlays.jsonl"
    monkeypatch.setattr(O, "VALUATION_OVERLAYS_PATH", p)
    assert O.main(["add", "--ticker", "macro", "--date", "2026-09-01", "--source", "https://fed", "--reliability", "official_data",
                   "--channel", "pe", "--driver", "erp", "--delta", "0.005", "--valid-until", "2026-12-01", "--note", "adverse",
                   "--scope-archetypes", "semi_cyclical", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ticker"] == "MACRO" and out["scope"] == {"archetypes": ["semi_cyclical"]}
    assert O.main(["add", "--ticker", "AMZN", "--date", "2026-09-01", "--source", "s", "--reliability", "filing",
                   "--channel", "eps", "--driver", "pe_multiplier", "--delta", "0.1", "--valid-until", "2026-12-01", "--note", "n"]) == 2
    assert "未写入" in capsys.readouterr().err
    assert O.main(["list", "--json"]) == 0
    assert len(json.loads(capsys.readouterr().out)) == 1
    assert O.main(["cancel", "--id", out["id"], "--note", "撤"]) == 0
    capsys.readouterr()
    assert O.main(["list", "--json"]) == 0 and json.loads(capsys.readouterr().out) == []


def test_future_cancellation_cannot_rewrite_historical_visibility():
    rows = [rec(id="timed", as_of="2026-09-01T12:00:00Z"),
            {"id": "timed", "kind": "cancel", "note": "withdrawn",
             "as_of": "2026-09-03T12:00:00Z"}]
    before = O.active_overlays(rows, ticker="AMZN", archetype="platform", as_of="2026-09-02")
    after = O.active_overlays(rows, ticker="AMZN", archetype="platform", as_of="2026-09-04")
    assert len(before["applied"]) == 1 and after["applied"] == []


def test_finite_effect_requires_matching_baseline_and_fixed_expiry(tmp_path):
    record = rec(driver="revenue_base", channel="eps", delta=.1, start_year=1, end_year=2, baseline_id="a")
    result = O.active_overlays([record], ticker="AMZN", archetype="platform",
                              as_of="2026-09-02", baseline_id="b")
    assert result["applied"] == [] and result["ignored"]
    with pytest.raises(ValueError):
        O.add_record(rec(valid_until="next_print"), path=tmp_path / "events.jsonl")
    assert not (tmp_path / "events.jsonl").exists()
