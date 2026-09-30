from datetime import date, timedelta

from quant.valuation import research_target as rt


def quarters(eps_by_q, *, start=date(2022, 1, 1), gains=None, ni_scale=1e9):
    out = []
    for i, eps in enumerate(eps_by_q):
        end = start + timedelta(days=91 * i)
        gain = (gains or {}).get(i)
        out.append({"period_end": end.isoformat(), "diluted_eps": eps, "net_income": eps * ni_scale,
                    "gain_on_securities": gain})
    return out


def closes(price, *, start=date(2022, 1, 1), until=date(2026, 9, 28)):
    out, d = {}, start
    while d <= until:
        if d.weekday() < 5:
            out[d] = price(d)
        d += timedelta(days=1)
    return out


def research(base=5.0, down=4.0, up=6.0, basis="gaap"):
    def win(e):
        return {"next_four_quarters": {"start": "2026-07-01", "end": "2027-06-30",
                                       "earnings_basis": basis, "eps": e}}
    return {"scenarios": {"downside": {"earnings_windows": win(down)},
                          "base": {"earnings_windows": win(base)},
                          "upside": {"earnings_windows": win(up)}}}


AS_OF = "2026-09-29T00:04:49+00:00"


def test_constant_multiple_center_and_band():
    q = quarters([1.0] * 19)              # TTM 4.0 throughout
    c = closes(lambda d: 80.0)            # PE 20 throughout
    r = rt.research_target("SYN", company_research=research(), input_state="current",
                           quarters=q, price=80.0, closes=c, as_of=AS_OF)
    assert r["status"] == "ok"
    assert abs(r["pe_now"] - 20) < 1e-9
    assert abs(r["target_12m"]["center"] - 100.0) < 1e-9          # 5.0 × 20
    assert abs(r["target_12m"]["eps_scenarios"]["downside"] - 80.0) < 1e-9
    assert abs(r["target_12m"]["multiple_band"]["p25"] - 100.0) < 1e-9
    assert r["validation"] == "unvalidated" and r["authority"] == "analysis_only"


def test_securities_gains_removed_from_ttm():
    # 最近一季含 3.0/股 的证券收益（税后 2.37）—— 不剔除时 TTM 翻倍、当前 PE 腰斩
    q = quarters([1.0] * 18 + [4.0], gains={18: 3.0 * 1e9})
    r = rt.research_target("SYN", company_research=research(), input_state="current",
                           quarters=q, price=80.0, closes=closes(lambda d: 80.0), as_of=AS_OF)
    assert abs(r["ttm_eps"] - (7.0 - 3.0 * 0.79)) < 1e-6


def test_negative_forward_eps_withholds():
    r = rt.research_target("SYN", company_research=research(base=-1.0), input_state="current",
                           quarters=quarters([1.0] * 19), price=80.0,
                           closes=closes(lambda d: 80.0), as_of=AS_OF)
    assert r["status"] == "unavailable" and r["reason"] == "forward_eps_not_positive"


def test_loss_history_center_only_and_breakeven_guard():
    q = quarters([-1.0] * 15 + [1.0] * 4)   # 只有最近一年转正
    ok = rt.research_target("SYN", company_research=research(), input_state="current",
                            quarters=q, price=80.0, closes=closes(lambda d: 80.0), as_of=AS_OF)
    assert ok["status"] == "ok" and ok["target_12m"]["multiple_band"] is None
    assert "no_pe_band" in ok["flags"]
    q2 = quarters([-1.0] * 15 + [0.01] * 4)  # 微利 → PE 2000
    bad = rt.research_target("SYN", company_research=research(), input_state="current",
                             quarters=q2, price=80.0, closes=closes(lambda d: 80.0), as_of=AS_OF)
    assert bad["status"] == "unavailable" and bad["reason"] == "near_breakeven_pe"


def test_missing_quarter_breaks_ttm():
    q = quarters([1.0] * 19)
    del q[-2]
    assert rt.ttm_eps_series(q)[-1][0] < date(2026, 9, 1)


def test_build_all_recomputes_expired_package_at_freeze_time():
    receipt = {"as_of": AS_OF, "receipt_hash": "h",
               "tickers": {"SYN": {"reference_quote": {"price": 80.0}}}}
    frozen = {"company_models": {"SYN": {"known_at": "2026-09-08T10:00:00+00:00"}},
              "fundamentals": {"SYN": {"quarters": quarters([1.0] * 19)}}}
    seen = {}

    def builder(package, *, as_of):
        seen["as_of"] = as_of
        return research()

    out = rt.build_all(receipt, frozen, package_builder=builder, closes_fn=lambda t, until: closes(lambda d: 80.0))
    assert seen["as_of"] == "2026-09-08T10:00:00+00:00"
    row = out["tickers"]["SYN"]
    assert row["status"] == "ok" and row["input_state"].startswith("expired")
    assert out["counts"] == {"ok": 1, "total": 1}
    assert "SYN" in rt.render(out)


def fcf_quarters(n=19, fcf=2.0e9, eps=1.0):
    q = quarters([eps] * n)
    for row in q:
        row["fcf"] = fcf
    return q


def test_special_items_added_back_per_scenario():
    package = {"scenarios": [{"id": s, "quarters": [
        {"start": "2026-07-01", "end": "2026-09-30",
         "cash": {"income_cash_timing": {"legal": {"expense": 10000.0, "expense_is_in_ebit": True}}}},
        {"start": "2027-07-01", "end": "2027-09-30",   # 窗外, 不回加
         "cash": {"income_cash_timing": {"legal": {"expense": 5000.0, "expense_is_in_ebit": True}}}}]}
        for s in ("downside", "base", "upside")]}
    res = research()
    for s in res["scenarios"].values():
        s["earnings_windows"]["next_four_quarters"]["weighted_diluted_shares"] = 2500.0
    eps = rt.scenario_eps(res, as_of=date(2026, 9, 29), package=package)
    assert abs(eps["eps"]["base"] - (5.0 + 4.0)) < 1e-9
    r = rt.research_target("SYN", company_research=res, input_state="current", quarters=quarters([1.0] * 19),
                           price=80.0, closes=closes(lambda d: 80.0), as_of=AS_OF, package=package)
    assert "special_items_added_back" in r["flags"]


def test_fcf_lens_and_divergence_flags():
    # 净利 1e9/季, FCF 0.2e9/季 → FCF/净利 0.2 = 背离
    r = rt.research_target("SYN", company_research=research(), input_state="current",
                           quarters=fcf_quarters(fcf=0.2e9), price=80.0, closes=closes(lambda d: 80.0), as_of=AS_OF)
    assert abs(r["fcf"]["conversion"] - 0.2) < 1e-9
    assert abs(r["fcf"]["yield_now"] - 0.8 / 80.0) < 1e-9          # 每股 FCF = 0.2 × 4
    assert "cash_earnings_divergence" in r["flags"]
    neg = rt.research_target("SYN", company_research=research(), input_state="current",
                             quarters=fcf_quarters(fcf=-1e9), price=80.0, closes=closes(lambda d: 80.0), as_of=AS_OF)
    assert "fcf_negative" in neg["flags"] and neg["status"] == "ok"   # 只播不改中枢


def test_implied_growth_round_trips_and_bounds():
    price = rt.dcf_value(4.0, 0.12, 0.094)
    g = rt.implied_growth(core_eps=4.0, price=price, rate=0.094)
    assert g["status"] == "ok" and abs(g["implied_growth"] - 0.12) < 1e-6
    assert rt.implied_growth(core_eps=4.0, price=1.0, rate=0.094)["status"] == "below_range"
    assert rt.implied_growth(core_eps=4.0, price=1e7, rate=0.094)["reason"] == "earnings_base_too_low"
    assert rt.implied_growth(core_eps=-1.0, price=80.0, rate=0.094)["status"] == "unavailable"
    assert rt.implied_growth(core_eps=4.0, price=80.0, rate=None)["status"] == "unavailable"
    r = rt.research_target("SYN", company_research=research(), input_state="current",
                           quarters=quarters([1.0] * 19), price=80.0, closes=closes(lambda d: 80.0),
                           as_of=AS_OF, required_return=0.094)
    assert r["implied"]["status"] == "ok"


def test_consensus_gap_flag():
    r = rt.research_target("SYN", company_research=research(base=5.0), input_state="current",
                           quarters=quarters([1.0] * 19), price=80.0, closes=closes(lambda d: 80.0),
                           as_of=AS_OF, consensus_eps=8.0)
    assert "consensus_gap" in r["flags"]


def test_adjusted_window_recomputed_on_gaap_rows():
    def row(i, gaap, adj):
        start = date(2026, 7, 1) + timedelta(days=91 * i)
        return {"id": f"Q{i}", "start": start.isoformat(), "end": (start + timedelta(days=90)).isoformat(),
                "parent_income": gaap, "adjusted_parent_income": adj, "adjusted_definition": "x",
                "basic_weighted_shares": 100.0, "incremental_dilutive_shares": 0.0, "revenue": 1.0}
    rows = [row(i, 100.0, 150.0) for i in range(4)]
    win = {"next_four_quarters": {"start": rows[0]["start"], "end": rows[-1]["end"],
                                  "earnings_basis": "adjusted", "eps": 6.0}}
    res = {"scenarios": {s: {"earnings_windows": win, "quarters": rows} for s in ("downside", "base", "upside")}}
    eps = rt.scenario_eps(res, as_of=date(2026, 9, 29))
    assert eps["earnings_basis"] == "gaap" and abs(eps["eps"]["base"] - 4.0) < 1e-9
    assert eps["basis_note"] == "gaap_recomputed_from_adjusted_window"


def test_package_superseded_by_new_report():
    q = [{"period_end": "2026-06-30"}]
    assert not rt.package_superseded(q, "2026-09-30")
    assert rt.package_superseded(q + [{"period_end": "2026-09-27"}], "2026-09-30")
    assert not rt.package_superseded(q, None)


def test_fyi_line_and_ledger_rows():
    r = rt.research_target("SYN", company_research=research(), input_state="current",
                           quarters=fcf_quarters(fcf=-1e9), price=80.0, closes=closes(lambda d: 80.0),
                           as_of=AS_OF, required_return=0.094)
    line = rt.fyi_line(r)
    assert line.startswith("📐[FYI] 研究级12月 中枢 $100") and "FCF为负" in line and "零授权" in line
    result = {"as_of": AS_OF, "version": rt.VERSION, "required_return": 0.094, "tickers": {"SYN": r}}
    [entry] = rt.ledger_rows(result, run_id="abc")
    assert entry["center"] == r["target_12m"]["center"] and entry["horizon_end"] == "2027-09-29"
    assert entry["run_id"] == "abc" and entry["flags"] == r["flags"]


def test_load_latest_rejects_failed_or_stale_runs(tmp_path):
    import json
    from datetime import datetime, timezone
    run = tmp_path / "runs" / "d"
    run.mkdir(parents=True)
    (run / "research_targets.json").write_text(json.dumps({"tickers": {"SYN": {}}}))
    job = {"status": "completed", "day_sgt": "2026-09-29", "artifact": str(run / "job.json")}
    (tmp_path / "latest.json").write_text(json.dumps(job))
    now = datetime(2026, 9, 30, tzinfo=timezone.utc)
    assert rt.load_latest(root=tmp_path, now=now)["tickers"] == {"SYN": {}}
    assert rt.load_latest(root=tmp_path, now=datetime(2026, 10, 9, tzinfo=timezone.utc)) is None
    (tmp_path / "latest.json").write_text(json.dumps({**job, "status": "failed"}))
    assert rt.load_latest(root=tmp_path, now=now) is None


def _ok_row(price, center, *, flags=(), band=True):
    return {"status": "ok", "price": price, "input_state": "current", "flags": list(flags),
            "target_12m": {"center": center, "eps_scenarios": {"downside": center * 0.8, "base": center,
                                                               "upside": center * 1.2},
                           "multiple_band": {"p25": center * 0.9, "p50": center, "p75": center * 1.1} if band else None},
            "upside_center": center / price - 1, "pe_now": 20.0, "pe_now_percentile_3y": 40.0 if band else None,
            "eps": {"eps": {"base": 5.0}}, "ttm_eps": 4.0, "eps_growth": 0.25, "consensus_eps": None,
            "implied": {"status": "ok", "implied_growth": 0.12}, "fcf": {"status": "ok", "yield_now": 0.03}}


def test_render_tg_is_tables_sorted_by_upside_with_warnings_and_folds():
    from quant.ops import tg_rich
    result = {"version": rt.VERSION, "counts": {"ok": 2, "total": 4}, "required_return_basis": "10Y 5.00% + ERP 4.00%，β=1",
              "tickers": {"LOW": _ok_row(100.0, 105.0), "HIGH": _ok_row(50.0, 80.0, flags=["no_pe_band"], band=False),
                          "NEG": {"status": "unavailable", "reason": "forward_eps_not_positive", "input_state": "current"},
                          "OLD": {"status": "unavailable", "reason": "company_model_eps_missing",
                                  "input_state": "expired:2026-09-08"}}}
    text = rt.tg_message({"day_sgt": "2026-09-30", "status": "completed",
                          "released_counts": {"fair_value": 0, "market_target_12m": 0}}, result)
    assert text.startswith("# 📐 每日估值 2026-09-30\n") and "**2/4** 出数 · 获准发布 0" in text
    assert text.index("| HIGH ⚠️ |") < text.index("| LOW |")          # 空间大的在前
    assert "- **HIGH** 无倍数带" in text and "| HIGH | 20.0 | — |" in text
    assert "- **NEG** 未来四季 EPS ≤ 0" in text and "输入过期，按冻结时点 2026-09-08" in text
    kinds = [b["type"] for b in tg_rich.compile_blocks(text)]
    assert kinds.count("table") == 2 and "details" in kinds          # 逐票 EPS / 口径进折叠块
