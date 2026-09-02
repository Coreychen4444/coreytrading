"""valuate.py + 估值层不变量: 零授权效力 (源码级) / rf 来源 / 编排 / 渲染 / 账本。"""
import json
import re
from pathlib import Path

import pytest

from quant.core import sizing
from quant.decision import entry_guard, exit_guard
from quant.sync import today_group_sync
from quant.valuation import archetypes, fundamentals, model, valuate
from tests.valuation.conftest import FakeSource, make_klines

PKG = Path(valuate.__file__).parent
MODULES = sorted(PKG.glob("*.py"))
AUTHORITY_IMPORTS = ("quant.core.sizing", "quant.decision.entry_guard", "quant.decision.exit_guard",
                     "quant.sync.today_group_sync", "quant.decision.regime_transition", "quant.core.structure")
STATE_WRITES = ("write_positions", "write_watchlist", "update_position_zones", "POSITIONS_PATH", "WATCHLIST_PATH",
                "update_broker_blocks", "append_trade_event")


def test_valuation_layer_never_touches_authority_or_state():
    """判断层零授权效力 —— chart_read 同款守卫: 不 import 授权层, 不写 positions/watchlist。"""
    for p in MODULES:
        src = p.read_text(encoding="utf-8")
        for bad in AUTHORITY_IMPORTS:
            assert bad not in src, f"{p.name} 引了授权层 {bad}"
        for bad in STATE_WRITES:
            assert bad not in src, f"{p.name} 碰了 state 写路径 {bad}"


def test_authority_layer_never_imports_valuation():
    """反向: 估值再便宜不解除 cap 0 —— sizing/entry_guard/exit_guard/today_group 不许读估值。"""
    for mod in (sizing, entry_guard, exit_guard, today_group_sync):
        assert "quant.valuation" not in Path(mod.__file__).read_text(encoding="utf-8"), mod.__name__


def test_zero_authority_is_pinned_in_every_docstring():
    for p in MODULES:
        assert "零授权效力" in p.read_text(encoding="utf-8"), p.name


def test_risk_free_reads_latest_macro_snapshot_or_falls_back(tmp_path):
    assert valuate.risk_free_from_macro(tmp_path)["rf"] == valuate.FALLBACK_RF
    assert "兜底" in valuate.risk_free_from_macro(tmp_path)["basis"]
    d = tmp_path / "2026-09"
    d.mkdir()
    (d / "macro-20260901T000000Z.json").write_text(json.dumps({"metrics": {"US10Y": {"value": 4.10, "as_of": "2026-08-30", "source": {"identifier": "DGS10"}}}}))
    (d / "macro-20260902T000000Z.json").write_text(json.dumps({"metrics": {"US10Y": {"value": 4.75, "as_of": "2026-08-31", "source": {"identifier": "DGS10"}}}}))
    (d / "macro-20260903T000000Z.json").write_text("not json")
    r = valuate.risk_free_from_macro(tmp_path)
    assert r["rf"] == pytest.approx(0.0475) and "DGS10" in r["basis"] and "20260902" in r["basis"]


@pytest.fixture
def world(tmp_path, inputs_doc):
    kdir = tmp_path / "klines"
    kdir.mkdir()
    tk, spy = make_klines(beta=1.3)
    for name, rows in (("SYN", tk), ("CYC", tk), ("SPY", spy)):
        (kdir / f"{name}.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return {"cache": tmp_path / "fund", "kdir": kdir, "snap": tmp_path / "snap", "ledger": tmp_path / "valuations.jsonl",
            "inputs": inputs_doc}


def test_run_orchestrates_fetch_model_ledger_and_skips_not_applicable(world):
    src = FakeSource()
    out = valuate.run(["SYN", "CYC", "NA1", "UNKNOWN"], inputs=world["inputs"], source=src, cache_dir=world["cache"],
                      kdir=world["kdir"], snapshots_dir=world["snap"], ledger_path=world["ledger"], write=True, rf=0.045)
    by = {r["ticker"]: r for r in out["results"]}
    assert set(by) == {"SYN", "CYC", "NA1"} and by["NA1"]["skipped"]
    assert "UNKNOWN" in out["errors"] and "KeyError" in out["errors"]["UNKNOWN"]
    assert not any(c[1].endswith("NA1") for c in src.calls), "不适用的票不该去拉 OpenD"
    assert by["SYN"]["fair_value"] > 0 and by["SYN"]["cost_of_capital"]["beta"] == pytest.approx(1.30)   # 合成 K 线 13 个月 → 行业 β
    assert by["SYN"]["anchor"]["consensus_revenue"] and by["SYN"]["anchor"]["forward"]["forward_revenue"] == pytest.approx(50.0 * 100e6 / 4.0)
    assert by["SYN"]["rf_basis"] == "参数 --rf"
    rows = [json.loads(l) for l in world["ledger"].read_text().splitlines()]
    assert [r["ticker"] for r in rows] == ["SYN", "CYC"] and out["written_count"] == 2
    for k in ("price", "fair_value", "verdict", "ratio", "confidence", "scenarios", "spec", "drivers", "implied_cagr", "rf_basis"):
        assert k in rows[0]
    # 第二次跑走缓存, 不再拉 OpenD
    n = len(src.calls)
    valuate.run(["SYN"], inputs=world["inputs"], source=src, cache_dir=world["cache"], kdir=world["kdir"],
                snapshots_dir=world["snap"], ledger_path=world["ledger"], rf=0.045)
    assert len(src.calls) == n


def test_run_reports_fetch_failure_per_ticker(world):
    out = valuate.run(["SYN"], inputs=world["inputs"], source=FakeSource(fail={"SYN"}), cache_dir=world["cache"],
                      kdir=world["kdir"], snapshots_dir=world["snap"], rf=0.045)
    assert out["results"] == [] and "fundamentals" in out["errors"]["SYN"]


def test_render_and_path_table_carry_the_essentials(world):
    out = valuate.run(["SYN", "NA1"], inputs=world["inputs"], source=FakeSource(), cache_dir=world["cache"],
                      kdir=world["kdir"], snapshots_dir=world["snap"], rf=0.045)
    by = {r["ticker"]: r for r in out["results"]}
    txt = valuate.render(by["SYN"])
    for must in ("━━ SYN ━━", "引擎A FCF-DCF", "引擎B 卖方一致目标价", "交叉:", "反向:", "判定:", "出处:", "久期", "[FYI] 卖方目标价"):
        assert must in txt, must
    for banned in (r"\bcap\b", "股数", "挂单", r"\bGO\b", r"\bVETO\b", r"PLAN\("):
        assert not re.search(banned, txt), f"判断层输出不该出现授权层词汇: {banned}"
    assert "不适用" in valuate.render(by["NA1"])
    tbl = valuate.path_table(by["SYN"])
    assert tbl.count("\n") == 10 and "NOPAT" in tbl


def test_ledger_row_for_skipped(world):
    row = valuate.ledger_row({"ticker": "NA1", "skipped": True, "reason": "r"}, "rf")
    assert row["skipped"] and row["ticker"] == "NA1" and "timestamp" in row


def test_cli_main_runs_on_cache_without_opend(world, monkeypatch, capsys):
    valuate.run(["SYN"], inputs=world["inputs"], source=FakeSource(), cache_dir=world["cache"], kdir=world["kdir"],
                snapshots_dir=world["snap"], rf=0.045)
    monkeypatch.setattr(fundamentals, "FUNDAMENTALS_DIR", world["cache"])
    monkeypatch.setattr(valuate, "KLINE_CACHE_DIR", world["kdir"])
    monkeypatch.setattr(valuate, "MACRO_SNAPSHOTS_DIR", world["snap"])
    monkeypatch.setattr(archetypes, "VALUATION_INPUTS_PATH", world["cache"] / "none.json")
    monkeypatch.setattr(valuate.archetypes, "load_inputs", lambda path=None: world["inputs"])
    assert valuate.main(["--tickers", "SYN", "--rf", "0.045"]) == 0
    assert "判定:" in capsys.readouterr().out
    assert valuate.main(["--tickers", "SYN", "--rf", "0.045", "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["results"][0]["ticker"] == "SYN" and "dcf" in doc["results"][0] and "street" in doc["results"][0] and "base" not in doc["results"][0]
    assert model.valuate is not None


def test_macro_context_and_next_print_readers(tmp_path):
    assert valuate.macro_context_from_current(tmp_path / "none.json") is None
    p = tmp_path / "current.json"
    p.write_text(json.dumps({"run_id": "r", "risk_environment": {"level": "adverse", "score": -1},
                             "generated_at": {"et": "2026-09-01T10:22:20-04:00"},
                             "next_event": {"status": "upcoming", "scheduled_at_et": "2026-09-04T08:30:00-04:00"}}))
    mc = valuate.macro_context_from_current(p)
    assert mc["risk_level"] == "adverse" and mc["next_tier1_day"] == "2026-09-04" and mc["run_id"] == "r"
    p.write_text("broken")
    assert valuate.macro_context_from_current(p) is None
    assert valuate.next_print_day("SYN", {"tickers": {"SYN": {"next_reaction_day": "2026-10-30"}}}) == "2026-10-30"
    assert valuate.next_print_day("SYN", {"tickers": {}}) is None and valuate.next_print_day("SYN", None) is None


def test_run_applies_overlays_from_file_and_macro_tilt(world, tmp_path):
    ovp = tmp_path / "overlays.jsonl"
    ovp.write_text("\n".join(json.dumps(r) for r in [
        {"id": "o1", "date": "2026-09-01", "ticker": "SYN", "source": "https://ir", "reliability": "company_official",
         "channel": "eps", "driver": "revenue_base", "delta": 0.10, "valid_until": "next_print", "note": "订单 +10% 收入"},
        {"id": "o2", "date": "2026-09-01", "ticker": "SYN", "source": "blog", "reliability": "rumor",
         "channel": "pe", "driver": "pe_multiplier", "delta": -0.2, "valid_until": "2026-12-01", "note": "传闻"},
        {"id": "o3", "date": "2026-09-01", "ticker": "MACRO", "source": "https://bls", "reliability": "official_data",
         "channel": "pe", "driver": "scenario_tilt", "delta": -0.05, "valid_until": "next_tier1", "note": "宏观偏空"},
    ]) + "\n")
    mp = tmp_path / "current.json"
    mp.write_text(json.dumps({"run_id": "macro-x", "risk_environment": {"level": "adverse", "score": -1},
                              "next_event": {"status": "upcoming", "scheduled_at_et": "2026-09-04T08:30:00-04:00"}}))
    cal = {"tickers": {"SYN": {"next_reaction_day": "2026-10-30"}}}
    common = dict(inputs=world["inputs"], source=FakeSource(), cache_dir=world["cache"], kdir=world["kdir"],
                  snapshots_dir=world["snap"], rf=0.045, calendar=cal, as_of="2026-09-02")
    with_news = valuate.run(["SYN"], overlays_path=ovp, macro_current_path=mp, **common)
    without = valuate.run(["SYN"], overlays_path=tmp_path / "none.jsonl", macro_current_path=tmp_path / "none.json", **common)
    r, r0 = with_news["results"][0], without["results"][0]
    ids = {o["id"] for o in r["news"]["applied"]}
    assert ids == {"o1", "o3"} and r["news"]["ignored"][0]["id"] == "o2"
    assert r["news"]["applied"][0]["resolved_until"] in ("2026-10-30", "2026-09-04")
    assert r["cost_of_capital"]["erp_tilt"] == 0.005 and r["scenarios"]["probabilities"]["bear"] == pytest.approx(0.30)
    assert r["fair_value"] != r0["fair_value"] and r0["news"]["applied"] == []
    txt = valuate.render(r)
    assert "增量新闻: 2 条生效" in txt and "o2: 可靠度 rumor 不应用" in txt and "宏观→PE" in txt
    assert with_news["overlay_records"] == 3 and with_news["macro_context"]["run_id"] == "macro-x"
    row = valuate.ledger_row(r, "rf")
    assert {o["id"] for o in row["news"]["applied"]} == {"o1", "o3"} and row["news"]["macro_context"]["risk_level"] == "adverse"


def test_digest_is_one_line_per_ticker_cheapest_first_and_authority_free(world):
    out = valuate.run(["SYN", "CYC", "NA1"], inputs=world["inputs"], source=FakeSource(), cache_dir=world["cache"],
                      kdir=world["kdir"], snapshots_dir=world["snap"], rf=0.045)
    txt = valuate.digest(out["results"], rf=out["rf"], erp=out["erp"], macro={"risk_level": "adverse"})
    lines = txt.splitlines()
    assert lines[0].startswith("📐 估值一览") and "rf 4.50%" in lines[0] and "宏观 adverse" in lines[0]
    body = [l for l in lines[1:] if not l.startswith(("不适用", "零授权"))]
    assert len(body) == 2 and all(("公允" in l and "置信" in l) for l in body)
    ratios = [next(r for r in out["results"] if r["ticker"] == l.split()[0])["verdict"]["ratio"] for l in body]
    assert ratios == sorted(ratios), "最便宜的在前"
    assert "不适用: NA1" in txt and lines[-1].startswith("零授权")
    for banned in (r"\bcap\b", "股数", "挂单", r"\bGO\b(?!.*≠)", r"PLAN\(", r"\$[0-9,]+k"):
        assert not re.search(banned, "\n".join(body)), banned


def test_cli_digest_flag(world, monkeypatch, capsys):
    valuate.run(["SYN"], inputs=world["inputs"], source=FakeSource(), cache_dir=world["cache"], kdir=world["kdir"],
                snapshots_dir=world["snap"], rf=0.045)
    monkeypatch.setattr(fundamentals, "FUNDAMENTALS_DIR", world["cache"])
    monkeypatch.setattr(valuate, "KLINE_CACHE_DIR", world["kdir"])
    monkeypatch.setattr(valuate, "MACRO_SNAPSHOTS_DIR", world["snap"])
    monkeypatch.setattr(valuate.archetypes, "load_inputs", lambda path=None: world["inputs"])
    assert valuate.main(["--tickers", "SYN", "--rf", "0.045", "--digest"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("📐 估值一览") and "SYN" in out and "零授权" in out
