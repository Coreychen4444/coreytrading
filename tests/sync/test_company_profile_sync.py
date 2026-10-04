"""company_profile 机器区对账 —— 它写 positions/watchlist 两个真源, 每条路径钉住。

立模块动机 (用户): 情报层利率传导链的公司暴露卡要读资产负债表,
雷达每轮只读两个 state JSON → 事实底座必须落在卡上。核心不变量:
  1. 人工区 (business_model/financing_notes/notes_as_of) 机器写永不触碰;
  2. TTM 不足 4 季 → null + 注明, 不年化不外推;
  3. 单票失败只报不炸整轮; 无离散季报不写空块。
"""
from quant.core.schema import validate_positions_doc, validate_watchlist_doc
from quant.sync import company_profile_sync as cps


# ── 报表构造器 ──────────────────────────────────────────────────

def _report(period, date_str, ftype, items, ts=None):
    return {
        "period_text": period, "date_time_str": date_str, "financial_type": ftype,
        "date_time": ts if ts is not None else int(date_str.replace("-", "")),
        "currency_code": "USD", "accounting_standards": "US_GAAP",
        "item_list": [{"field_id": fid, "data": val} for fid, val in items.items()],
    }


M = 1e6

BS_ITEMS = {
    8003: 8042.1 * M,   # cash & STI
    8018: 830.3 * M,    # restricted cash
    8028: 1620.5 * M,   # LT investments
    8058: 46.7 * M,     # current debt
    8069: 8499.0 * M,   # LT debt
    8060: 114.9 * M,    # current lease
    8070: 1510.4 * M,   # LT lease
    8024: 14900.3 * M,  # net PPE
    8001: 27961.5 * M,  # total assets
    8081: 10340.5 * M,  # total equity
}


def _income_q(period, date_str, ftype, revenue, int_inc=10.0, int_exp=20.0):
    return _report(period, date_str, ftype,
                   {8001: revenue * M, 8019: int_inc * M, 8020: int_exp * M})


def _cashflow_q(period, date_str, ftype, ocf, ppe_net, fcf):
    return _report(period, date_str, ftype,
                   {8015: ocf * M, 8046: ppe_net * M, 8072: fcf * M})


def _cashflow_q_direct(period, date_str, ftype, ocf, capex, fcf, also_net=None):
    items = {8015: ocf * M, 8044: capex * M, 8072: fcf * M}
    if also_net is not None:
        items[8046] = also_net * M
    return _report(period, date_str, ftype, items)


def _four_quarters(builder, **kw):
    return [
        builder("2026/Q2", "2026-06-29", 2, **kw),
        builder("2026/Q1", "2026-03-30", 1, **kw),
        builder("2025/Q4", "2025-12-31", 4, **kw),
        builder("2025/Q3", "2025-09-30", 3, **kw),
    ]


# ── build_balance_sheet ────────────────────────────────────────

def test_balance_sheet_maps_fields_and_derives_net_cash():
    reports = [_report("2026/Q2", "2026-06-29", 2, BS_ITEMS)]
    bs = cps.build_balance_sheet(reports)
    assert bs["period"] == "2026/Q2" and bs["as_of"] == "2026-06-29"
    assert bs["cash_and_sti_m"] == 8042.1
    assert bs["lt_debt_m"] == 8499.0
    assert bs["total_debt_m"] == 8545.7            # 46.7 + 8499.0
    assert bs["net_cash_ex_leases_m"] == -503.6    # 8042.1 - 8545.7 (租赁不进减数)
    assert bs["currency"] == "USD"


def test_sdk_named_quarters_build_the_same_balance_and_ttm():
    reports = _four_quarters(_income_q, revenue=100.0)
    expected = cps.build_fundamentals_ttm(reports, _four_quarters(_cashflow_q, ocf=30.0, ppe_net=-10.0, fcf=20.0))
    for report in reports:
        report["financial_type"] = f"Q{report['financial_type']}"
    assert cps.build_fundamentals_ttm(reports, _four_quarters(_cashflow_q, ocf=30.0, ppe_net=-10.0, fcf=20.0)) == expected
    assert cps.build_balance_sheet([_report("2026/Q2", "2026-06-29", "Q2", BS_ITEMS)]) == cps.build_balance_sheet([_report("2026/Q2", "2026-06-29", 2, BS_ITEMS)])


def test_balance_sheet_picks_latest_discrete_quarter_and_skips_annual():
    reports = [
        _report("2025/FY", "2025-12-31", 7, {8003: 1.0 * M}, ts=20251231),
        _report("2026/Q1", "2026-03-30", 1, BS_ITEMS, ts=20260330),
        _report("2025/Q4", "2025-12-31", 4, {8003: 2.0 * M}, ts=20251231),
    ]
    bs = cps.build_balance_sheet(reports)
    assert bs["period"] == "2026/Q1"


def test_balance_sheet_none_when_no_discrete_quarters():
    assert cps.build_balance_sheet([]) is None
    assert cps.build_balance_sheet(
        [_report("2025/FY", "2025-12-31", 7, BS_ITEMS)]) is None


def test_balance_sheet_missing_cash_leaves_derived_null():
    reports = [_report("2026/Q2", "2026-06-29", 2, {8069: 100.0 * M})]
    bs = cps.build_balance_sheet(reports)
    assert bs["cash_and_sti_m"] is None
    assert bs["total_debt_m"] is None and bs["net_cash_ex_leases_m"] is None


# ── build_fundamentals_ttm ─────────────────────────────────────

def test_ttm_sums_four_quarters_and_flips_capex_sign():
    inc = [_income_q(p, d, f, revenue=100.0) for p, d, f in
           [("2026/Q2", "2026-06-29", 2), ("2026/Q1", "2026-03-30", 1),
            ("2025/Q4", "2025-12-31", 4), ("2025/Q3", "2025-09-30", 3)]]
    cf = [_cashflow_q(p, d, f, ocf=50.0, ppe_net=-30.0, fcf=20.0) for p, d, f in
          [("2026/Q2", "2026-06-29", 2), ("2026/Q1", "2026-03-30", 1),
           ("2025/Q4", "2025-12-31", 4), ("2025/Q3", "2025-09-30", 3)]]
    ttm = cps.build_fundamentals_ttm(inc, cf)
    assert ttm["revenue_m"] == 400.0
    assert ttm["operating_cash_flow_m"] == 200.0
    assert ttm["capex_m"] == 120.0                  # −(净买卖) 符号翻转
    assert ttm["capex_basis"] == "net_ppe_purchase"
    assert ttm["free_cash_flow_m"] == 80.0
    assert ttm["interest_expense_m"] == 80.0
    assert ttm["capex_to_revenue_pct"] == 30.0
    assert ttm["capex_to_ocf_pct"] == 60.0
    assert ttm["window"] == "2025/Q3–2026/Q2"


def test_ttm_capex_prefers_direct_field_and_reports_basis():
    inc = _four_quarters(_income_q, revenue=100.0)
    # 8044 与 8046 并存时取 8044 (真 capex, MU 派披露)
    cf = _four_quarters(_cashflow_q_direct, ocf=50.0, capex=-40.0, fcf=10.0, also_net=-30.0)
    ttm = cps.build_fundamentals_ttm(inc, cf)
    assert ttm["capex_m"] == 160.0
    assert ttm["capex_basis"] == "capital_expenditure"


def test_ttm_capex_mixed_disclosure_labeled_mixed():
    inc = _four_quarters(_income_q, revenue=100.0)
    cf = [
        _cashflow_q_direct("2026/Q2", "2026-06-29", 2, ocf=50.0, capex=-40.0, fcf=10.0),
        _cashflow_q("2026/Q1", "2026-03-30", 1, ocf=50.0, ppe_net=-30.0, fcf=20.0),
        _cashflow_q("2025/Q4", "2025-12-31", 4, ocf=50.0, ppe_net=-30.0, fcf=20.0),
        _cashflow_q("2025/Q3", "2025-09-30", 3, ocf=50.0, ppe_net=-30.0, fcf=20.0),
    ]
    ttm = cps.build_fundamentals_ttm(inc, cf)
    assert ttm["capex_m"] == 130.0                  # 40 + 30×3
    assert ttm["capex_basis"] == "mixed"


def test_ttm_field_missing_in_one_quarter_yields_null_not_partial_sum():
    inc = _four_quarters(_income_q, revenue=100.0)
    cf = _four_quarters(_cashflow_q, ocf=50.0, ppe_net=-30.0, fcf=20.0)
    cf[2]["item_list"] = [it for it in cf[2]["item_list"] if it["field_id"] != 8015]
    ttm = cps.build_fundamentals_ttm(inc, cf)
    assert ttm["operating_cash_flow_m"] is None     # 3/4 季部分求和 = 静默少加, 禁止
    assert ttm["revenue_m"] == 400.0                # 其他字段不连坐
    assert ttm["capex_to_ocf_pct"] is None


def test_ttm_insufficient_history_yields_nulls_not_annualization():
    inc = [_income_q("2026/Q2", "2026-06-29", 2, revenue=100.0),
           _income_q("2026/Q1", "2026-03-30", 1, revenue=100.0)]
    cf = [_cashflow_q("2026/Q2", "2026-06-29", 2, ocf=50.0, ppe_net=-30.0, fcf=20.0),
          _cashflow_q("2026/Q1", "2026-03-30", 1, ocf=50.0, ppe_net=-30.0, fcf=20.0)]
    ttm = cps.build_fundamentals_ttm(inc, cf)
    assert ttm["revenue_m"] is None and ttm["capex_m"] is None
    assert "仅 2 季可得" in ttm["window"]


def test_ttm_negative_ocf_skips_capex_to_ocf_ratio():
    inc = _four_quarters(_income_q, revenue=100.0)
    cf = _four_quarters(_cashflow_q, ocf=-10.0, ppe_net=-30.0, fcf=-40.0)
    ttm = cps.build_fundamentals_ttm(inc, cf)
    assert ttm["capex_to_ocf_pct"] is None          # 负 OCF 上的比率是噪音
    assert ttm["capex_to_revenue_pct"] == 30.0


def test_ttm_dedupes_restated_period():
    inc = _four_quarters(_income_q, revenue=100.0)
    inc.append(_income_q("2026/Q2", "2026-06-29", 2, revenue=999.0))  # 重复期次
    cf = _four_quarters(_cashflow_q, ocf=50.0, ppe_net=-30.0, fcf=20.0)
    ttm = cps.build_fundamentals_ttm(inc, cf)
    assert ttm["revenue_m"] == 400.0


# ── apply_machine_update: 人工区不可侵犯 ───────────────────────

def test_apply_preserves_human_keys_and_overwrites_machine():
    agent = {"company_profile": {
        "business_model": "AI 云, 重资本",
        "financing_notes": "可转债 + 项目融资",
        "notes_as_of": "2026-09-01",
        "balance_sheet": {"as_of": "2026-03-30", "cash_and_sti_m": 1.0},
        "source": "old",
    }}
    cps.apply_machine_update(agent, {
        "balance_sheet": {"as_of": "2026-06-29", "cash_and_sti_m": 2.0},
        "fundamentals_ttm": {"revenue_m": 3.0},
        "source": "new", "fetched_at": "2026-09-01T00:00:00Z",
        # 防御: 即使 update 里混进人工键也不得覆盖
        "business_model": "机器不许写这里",
    })
    cp = agent["company_profile"]
    assert cp["business_model"] == "AI 云, 重资本"
    assert cp["financing_notes"] == "可转债 + 项目融资"
    assert cp["notes_as_of"] == "2026-09-01"
    assert cp["balance_sheet"]["as_of"] == "2026-06-29"
    assert cp["source"] == "new"


def test_apply_creates_skeleton_when_absent():
    agent = {}
    cps.apply_machine_update(agent, {"balance_sheet": {"as_of": "2026-06-29"},
                                     "fundamentals_ttm": {}, "source": "s",
                                     "fetched_at": "t"})
    assert agent["company_profile"]["balance_sheet"]["as_of"] == "2026-06-29"


# ── target_cards ───────────────────────────────────────────────

def _pos_doc():
    return {"tickers": {
        "NBIS": {"status": "active", "instrument": {"type": "stock"}},
        "SPCX": {"status": "active", "instrument": {"type": "stock"}},   # 结构豁免层也要数据
        "META270115C560000": {"status": "active", "instrument": {"type": "option"}},
        "AAOI": {"status": "closed", "instrument": {"type": "stock"}},
    }}


def _wl_doc():
    return {"tickers": {
        "MU": {"status": "watching"},
        "META": {"status": "watching"},
        "OLD": {"status": "dropped"},
    }}


def test_target_cards_selection():
    got = cps.target_cards(_pos_doc(), _wl_doc())
    assert ("positions", "NBIS") in got
    assert ("positions", "SPCX") in got             # 豁免的是 task 输出, 不是数据
    assert ("watchlist", "MU") in got and ("watchlist", "META") in got
    assert all(tk != "META270115C560000" for _, tk in got)   # 期权归 underlying
    assert all(tk not in ("AAOI", "OLD") for _, tk in got)


def test_target_cards_skips_funds():
    """基金票 (ETF) 没有三张表: 报表接口返回空表 → 每轮一行取数错。"""
    etf = {"type": "stock", "security_type": "ETF"}
    pos = {"tickers": {"ETFY": {"status": "active", "instrument": etf},
                       "NBIS": {"status": "active", "instrument": {"type": "stock"}}}}
    wl = {"tickers": {"ETFX": {"status": "watching", "instrument": etf}, "MU": {"status": "watching"}}}
    assert cps.target_cards(pos, wl) == [("positions", "NBIS"), ("watchlist", "MU")]


# ── sync_profiles: 降级与选择性 ────────────────────────────────

def _fake_fetch_ok(code, statement_type, num):
    if statement_type == cps.STATEMENT_BALANCE:
        return [_report("2026/Q2", "2026-06-29", 2, BS_ITEMS)]
    if statement_type == cps.STATEMENT_INCOME:
        return _four_quarters(_income_q, revenue=100.0)
    return _four_quarters(_cashflow_q, ocf=50.0, ppe_net=-30.0, fcf=20.0)


def test_sync_writes_machine_block_and_reports():
    pos, wl = _pos_doc(), _wl_doc()
    for d in (pos, wl):
        for c in d["tickers"].values():
            c["agent"] = {"thesis": {"summary": "x"}, "action": {"intent": "hold"}}
    result = cps.sync_profiles(pos, wl, fetch=_fake_fetch_ok)
    assert "positions:NBIS" in result["synced"] and "watchlist:MU" in result["synced"]
    assert result["touched"] == {"positions": True, "watchlist": True}
    cp = pos["tickers"]["NBIS"]["agent"]["company_profile"]
    assert cp["balance_sheet"]["total_debt_m"] == 8545.7
    assert cp["fundamentals_ttm"]["revenue_m"] == 400.0
    assert "OpenD" in cp["source"]


def test_sync_single_ticker_failure_does_not_kill_round():
    def fetch(code, statement_type, num):
        if code == "US.NBIS":
            raise RuntimeError("boom")
        return _fake_fetch_ok(code, statement_type, num)
    pos, wl = _pos_doc(), _wl_doc()
    result = cps.sync_profiles(pos, wl, fetch=fetch)
    assert any("NBIS" in e for e in result["errors"])
    assert "watchlist:MU" in result["synced"]


def test_sync_no_balance_sheet_reports_and_skips_write():
    def fetch(code, statement_type, num):
        if statement_type == cps.STATEMENT_BALANCE:
            return []
        return _fake_fetch_ok(code, statement_type, num)
    pos, wl = _pos_doc(), _wl_doc()
    result = cps.sync_profiles(pos, wl, fetch=fetch)
    assert result["synced"] == [] and result["touched"] == {"positions": False, "watchlist": False}
    assert all("无离散季度资产负债表" in e for e in result["errors"])
    assert "company_profile" not in (pos["tickers"]["NBIS"].get("agent") or {})


def test_sync_only_filter():
    pos, wl = _pos_doc(), _wl_doc()
    result = cps.sync_profiles(pos, wl, fetch=_fake_fetch_ok, only={"MU"})
    assert result["synced"] == ["watchlist:MU"]
    assert result["touched"] == {"positions": False, "watchlist": True}


# ── set_prose: 双桶同票一起写 ──────────────────────────────────

def test_set_prose_writes_all_buckets_for_dual_ticker(monkeypatch):
    from contextlib import nullcontext
    pos = {"tickers": {"META": {"status": "active", "instrument": {"type": "stock"},
                                "agent": {}}}}
    wl = {"tickers": {"META": {"status": "watching", "agent": {}}}}
    written = []
    monkeypatch.setattr(cps, "state_lock", lambda: nullcontext())
    monkeypatch.setattr(cps.state_io, "read_positions", lambda **kw: pos)
    monkeypatch.setattr(cps.state_io, "read_watchlist", lambda **kw: wl)
    monkeypatch.setattr(cps.state_io, "write_positions", lambda d: written.append("positions"))
    monkeypatch.setattr(cps.state_io, "write_watchlist", lambda d: written.append("watchlist"))
    result = cps.set_prose("META", business_model="社交+广告", financing_notes=None)
    assert sorted(result["buckets"]) == ["positions", "watchlist"]
    assert pos["tickers"]["META"]["agent"]["company_profile"]["business_model"] == "社交+广告"
    assert wl["tickers"]["META"]["agent"]["company_profile"]["business_model"] == "社交+广告"
    assert sorted(written) == ["positions", "watchlist"]


# ── schema: 结构性破坏被拦, 合法块通过 ─────────────────────────

def _pos_card_with_profile(profile):
    return {"schema_version": 2, "tickers": {"NBIS": {
        "instrument": {"type": "stock"},
        "broker": {"qty": 0.0, "synthetic": True},
        "status": "closed",
        "agent": {"thesis": {"summary": "x"}, "action": {"intent": "hold"},
                  "company_profile": profile},
        "lots": {"base": [], "position": []},
        "position_summary": {}, "pending_orders": [],
    }}}


def _valid_profile():
    return {
        "business_model": "AI 云",
        "financing_notes": "债务扩张",
        "notes_as_of": "2026-09-01",
        "balance_sheet": {"as_of": "2026-06-29", "cash_and_sti_m": 8042.1,
                          "lt_debt_m": 8499.0},
        "fundamentals_ttm": {"revenue_m": 400.0, "capex_m": 120.0},
        "source": "OpenD get_financials_statements (US.NBIS, quarterly, USD)",
        "fetched_at": "2026-09-01T00:00:00Z",
    }


def test_schema_accepts_valid_profile_both_docs():
    errors, _ = validate_positions_doc(_pos_card_with_profile(_valid_profile()))
    assert errors == []
    wl = {"tickers": {"MU": {"status": "candidate",
                             "agent": {"thesis": {"summary": "x"},
                                       "company_profile": _valid_profile()}}}}
    errors, _ = validate_watchlist_doc(wl)
    assert errors == []


def test_schema_rejects_bad_as_of_and_missing_source():
    p = _valid_profile()
    p["balance_sheet"]["as_of"] = "Q2"
    errors, _ = validate_positions_doc(_pos_card_with_profile(p))
    assert any("as_of" in e for e in errors)

    p2 = _valid_profile()
    p2.pop("source")
    errors, _ = validate_positions_doc(_pos_card_with_profile(p2))
    assert any("source missing" in e for e in errors)


def test_schema_rejects_non_numeric_and_non_dict():
    p = _valid_profile()
    p["fundamentals_ttm"]["revenue_m"] = "400"
    errors, _ = validate_positions_doc(_pos_card_with_profile(p))
    assert any("revenue_m" in e for e in errors)

    errors, _ = validate_positions_doc(_pos_card_with_profile("not-a-dict"))
    assert any("must be a dict" in e for e in errors)
