"""宏观透镜发布层：组合暴露口径、日历合并、情报命中、账本幂等、推送表格完整性。全部注入临时文件，不读 live。"""
import json

from quant.core import macro_lens as L
from quant.journal import macro_lens_log as M
from tests.core.test_macro_lens import synthetic


def _world(tmp_path):
    s = synthetic(drive="OIL", beta=-0.8)
    kdir = tmp_path / "klines"
    kdir.mkdir()
    for tk in ("AAA", "BBB"):
        rows = [{"time": f"{d} 00:00:00", "close": v * (1.5 if tk == "AAA" else 0.4)} for d, v in s["SPY"]]
        (kdir / f"{tk}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    doc = {"tickers": {
        "AAA270319C360000": {"broker": {"qty": 1.0, "delta": 0.5}},
        "BBB": {"broker": {"qty": 10.0}},
        "KEEP": {"broker": {"qty": 1.0}, "instrument": {"role": "commemorative_hold"}},
        "FLAT": {"broker": {"qty": 0.0}},
    }}
    asof = s["SPY"][-1][0]
    cal = tmp_path / "calendar.json"
    cal.write_text(json.dumps({"events": [
        {"event_id": "x1", "name": "美国9月就业报告", "scheduled_at_et": f"{asof}T08:30:00-04:00"},   # 当日不进
        {"event_id": "x2", "name": "美国8月个人收入与支出/PCE", "scheduled_at_et": "2099-01-01T08:30:00-04:00"},
    ]}))
    big3 = tmp_path / "big3.json"
    big3.write_text(json.dumps({"events": {"FOMC": [], "CPI": [], "NFP": []}}))
    reports = tmp_path / "trade" / "intelligence" / "reports"
    (reports / asof[:7]).mkdir(parents=True)
    (reports / asof[:7] / f"{asof}-postclose.md").write_text(
        "# 报告\n\n**1．霍尔木兹提案被拒，布伦特原油盘中跳涨。**（来源）\n正文提到原油但不是标题\n"
        "**2．SYN 发布新品。**\n")
    return s, doc, kdir, cal, big3, reports


def _build(tmp_path):
    s, doc, kdir, cal, big3, reports = _world(tmp_path)
    return M.build(series=s, doc=doc, nav=10_000.0, kdir=kdir, cal_path=cal, big3_path=big3, reports_dir=reports)


def test_book_weights_use_delta_notional_and_skip_unmanaged(tmp_path):
    s, doc, kdir, *_ = _world(tmp_path)
    w = M.book_weights(doc, 10_000.0, kdir)
    assert set(w) == {"AAA", "BBB"}                          # KEEP 结构豁免、FLAT 零股
    aaa_px = s["SPY"][-1][1] * 1.5
    assert w["AAA"] == 1 * 100 * 0.5 * aaa_px / 10_000.0


def test_build_carries_book_catalysts_and_archive_hit(tmp_path):
    st = _build(tmp_path)
    assert st["schema"] == L.SCHEMA and st["authority"] == "none"
    assert st["drivers"]["dominant"] == "OIL"
    assert "OIL" in st["book"]["factors"]
    names = [c["name"] for c in st["catalysts"]]
    assert names == []                                         # 当日事件不进；2099 年超窗
    hits = st["triggers"]["OIL"]
    assert len(hits) == 1 and hits[0]["headline"].startswith("霍尔木兹")


def test_publish_is_idempotent_per_asof(tmp_path):
    st = _build(tmp_path)
    out = tmp_path / "macro"
    kw = dict(current=out / "current.json", ledger=out / "ledger.jsonl", reports=out / "reports",
              lock_path=out / ".lock")
    assert M.publish(st, **kw)["ledger_appended"] is True
    assert M.publish(st, **kw)["ledger_appended"] is False
    assert len((out / "ledger.jsonl").read_text().splitlines()) == 1
    assert json.loads((out / "current.json").read_text())["asof"] == st["asof"]
    assert (out / "reports" / st["asof"][:7] / f"{st['asof']}.md").exists()


def test_digest_tables_are_rectangular_and_carry_receipts(tmp_path):
    st = _build(tmp_path)
    text = M.digest(st)
    assert "六盏风险灯" in text and "市场在交易" in text and "未来方向" in text
    block = []
    for line in text.splitlines() + [""]:
        if line.startswith("|"):
            block.append(line)
            continue
        if block:
            widths = {row.count("|") for row in block}
            assert len(widths) == 1, block                      # 单元格里不许混进竖线
            block = []
    assert "持续性（历史收据" in text and "不承诺延续" in text
    full = M.report(st)
    assert "### 背离" in full and "价涨波升" in full and "底部不确认" in full and "已检验不采用" in full


def test_catalysts_dedupe_official_calendar_against_intelligence_calendar(tmp_path):
    cal = tmp_path / "c.json"
    cal.write_text(json.dumps({"events": [
        {"name": "美国9月就业报告", "scheduled_at_et": "2026-10-02T08:30:00-04:00"},
        {"name": "美国每周初请失业金", "scheduled_at_et": "2026-10-01T08:30:00-04:00"}]}))
    big3 = tmp_path / "b.json"
    big3.write_text(json.dumps({"events": {
        "NFP": [{"date": "2026-10-02", "scheduled": True}],
        "FOMC": [{"date": "2026-10-28", "scheduled": True}],
        "CPI": [{"date": "2026-12-30", "scheduled": True}]}}))
    rows = M.catalysts("2026-09-28", cal_path=cal, big3_path=big3)
    assert [(r["date"], r["tier"]) for r in rows] == [("2026-10-01", "C"), ("2026-10-02", "A"), ("2026-10-28", "A")]


def test_brief_is_four_lines_with_injected_overnight(tmp_path):
    st = _build(tmp_path)
    text = M.brief(st, live=[{"cn": "WTI", "chg": -0.021}])
    assert text.splitlines()[0].startswith("🌐 宏观")
    assert "WTI -2.1%" in text and len(text.splitlines()) <= 4


def test_week_review_tracks_regime_and_reconciles_last_weeks_driver(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    rows = []
    for i in range(10):
        dom = "OIL" if i < 7 else "R10"
        rows.append({"asof": f"2026-09-{10 + i:02d}", "tier": {"tier": "留意" if i < 8 else "警戒", "on": ["信用"]},
                     "dominant": dom, "challenger": "R10" if i >= 6 else None,
                     "theme": "供给冲击 / 滞胀" if i < 8 else "长端利率 / 估值压缩",
                     "drivers": [{"factor": "OIL", "rho": -0.6 if i != 9 else 0.4, "r2": 0.36},
                                 {"factor": "R10", "rho": -0.5, "r2": 0.25}]})
    ledger.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    text = M.week(ledger)
    assert "留意 → 警戒" in text
    assert "最强驱动：原油 2/5 日" in text and "长端利率 3/5 日" in text
    assert "读法翻转（|ρ|≥0.30 的符号变过）：原油" in text
    assert "上周末最强驱动 原油 → 本周末解释力 36%（常态 17%）✓" in text
    assert "账本为空" in M.week(tmp_path / "missing.jsonl")


def test_overnight_change_uses_the_last_completed_close():
    # 期货新交易日刚开：最后一根收盘为空 → 上一收盘 = 最后一个有效收盘
    assert M.overnight_change(99.0, [100.0, 101.0, 100.0, None]) == 99.0 / 100.0 - 1
    # 盘中：最后一根已有进行中的收盘 → 上一收盘 = 倒数第二个
    assert M.overnight_change(99.0, [100.0, 101.0, 99.0]) == 99.0 / 101.0 - 1
    assert M.overnight_change(None, [1.0]) is None and M.overnight_change(1.0, [None]) is None


def test_update_with_session_never_publishes_a_stale_reading(tmp_path, monkeypatch, capsys):
    """macro-lens 定时任务传 due 的 session：SPY 日线没到今天 → 不写任何文件、退出码 3（不拿昨天冒充今天）。"""
    st = _build(tmp_path)
    published = []
    monkeypatch.setattr(M, "build", lambda asof=None, **k: st)
    monkeypatch.setattr(M, "publish", lambda s, **k: published.append(s) or
                        {"current": "current.json", "ledger_appended": True, "report": "r.md"})
    assert M.main(["--update", "--no-refresh", "--session", "2099-01-02"]) == 3
    out = capsys.readouterr().out
    assert published == [] and out.startswith("⚠️ 宏观透镜未发布") and st["asof"] in out
    assert M.main(["--update", "--no-refresh", "--session", st["asof"]]) == 0
    assert published == [st] and "账本+1" in capsys.readouterr().out


def test_data_gaps_flag_lagging_yahoo_series_but_not_fred(tmp_path):
    s, doc, kdir, cal, big3, reports = _world(tmp_path)
    s["MOVE"] = s["MOVE"][:-1]                                  # MOVE 收定最晚（close+30m），今天没到
    s["DGS10"] = s["SPY"][:-2]                                  # FRED 次日发布：天然滞后，不算缺口
    st = M.build(series=s, doc=doc, nav=10_000.0, kdir=kdir, cal_path=cal, big3_path=big3,
                 reports_dir=reports, refresh_failed=["MOVE", "DGS10"])
    gaps = st["data_gaps"]
    assert [x["key"] for x in gaps["lagging"]] == ["MOVE"] and gaps["lagging"][0]["last"] == s["MOVE"][-1][0]
    line = M.digest(st).splitlines()[1]                         # 紧跟标题：先说数据缺口，再说读数
    assert line.startswith("⚠️ 数据：日线未到") and "MOVE（" in line and "刷新失败 DGS10" in line
    assert "刷新失败 MOVE" not in line                          # 同一个缺口只说一次
    assert M.ledger_row(st)["data_gaps"] == {"lagging": ["MOVE"], "refresh_failed": ["DGS10", "MOVE"]}
    (tmp_path / "clean").mkdir()
    clean = _build(tmp_path / "clean")
    assert clean["data_gaps"] == {"lagging": [], "refresh_failed": []}
    assert "⚠️ 数据" not in M.digest(clean)
