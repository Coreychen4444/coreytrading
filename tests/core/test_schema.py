from quant.core.schema import validate_event, validate_positions_doc


def _minimal_ticker(**over):
    t = {
        "instrument": {"type": "stock"},
        "broker": {"source": "moomoo_opend", "qty": 9.0, "avg_cost": 387.38, "currency": "USD"},
        "status": "active",
        "agent": {
            "thesis": {"summary": "DRAM supercycle", "status": "intact", "conviction": "high", "last_reviewed": "2026-06-11T00:00:00Z"},
            "action": {"intent": "hold", "reason": "core", "confidence": "high"},
        },
        "lots": {"base": [{"qty": 9.0, "avg_cost": 378.11}], "position": []},
        "position_summary": {},
        "pending_orders": [],
    }
    t.update(over)
    return t


def _doc(t):
    return {"schema_version": 2, "tickers": {"MU": t}}


def test_valid_doc_passes():
    errors, warnings = validate_positions_doc(_doc(_minimal_ticker()))
    assert errors == []
    assert warnings == []


def test_missing_thesis_is_error():
    t = _minimal_ticker()
    t["agent"]["thesis"]["summary"] = ""
    errors, _ = validate_positions_doc(_doc(t))
    assert any("thesis.summary" in e for e in errors)


def test_lots_broker_mismatch_is_warning_not_error():
    t = _minimal_ticker()
    t["lots"]["base"][0]["qty"] = 5.0  # broker says 9
    errors, warnings = validate_positions_doc(_doc(t))
    assert errors == []
    assert any("lots qty" in w for w in warnings)


def test_synthetic_tracker_skips_lots_check():
    t = _minimal_ticker()
    t["broker"]["qty"] = 0.0
    t["broker"]["synthetic"] = True
    t["lots"] = {"base": [], "position": []}
    errors, warnings = validate_positions_doc(_doc(t))
    assert errors == [] and warnings == []


def test_option_stop_requires_basis():
    t = _minimal_ticker()
    t["instrument"] = {"type": "option"}
    t["agent"]["zones"] = {"stop_level": 10.42}
    doc = {"schema_version": 2, "tickers": {"NOK270115C15000": t}}
    errors, _ = validate_positions_doc(doc)
    assert any("stop_level_basis" in e for e in errors)
    t["agent"]["zones"]["stop_level_basis"] = "underlying"
    # lots mismatch warning aside, basis error must be gone
    errors, _ = validate_positions_doc(doc)
    assert not any("stop_level_basis" in e for e in errors)


def test_legacy_stop_key_is_error():
    t = _minimal_ticker()
    t["agent"]["zones"] = {"stop_level_underlying": 90.0}
    errors, _ = validate_positions_doc(_doc(t))
    assert any("stop_level_underlying" in e for e in errors)


def test_summary_arithmetic_warning_uses_option_multiplier():
    t = _minimal_ticker()
    t["instrument"] = {"type": "option"}
    t["lots"]["base"] = [{"qty": 16.0, "avg_cost": 2.6469}]
    t["broker"]["qty"] = 16.0
    t["position_summary"] = {"base_qty": 16.0, "base_cost_total": 4235.0}
    doc = {"schema_version": 2, "tickers": {"NOK270115C15000": t}}
    _, warnings = validate_positions_doc(doc)
    assert not any("cost_total" in w for w in warnings)  # 16×2.6469×100 ≈ 4235 within tolerance


# ---- events ----

def test_valid_fill_event():
    assert validate_event({
        "event_type": "fill", "timestamp": "2026-06-09T11:46:15.174000-04:00",
        "ticker": "INTC260612P100000", "side": "BUY", "qty": 2.0, "price": 1.59,
        "order_id": "FS1CA620C5B4D27000", "broker": "moomoo_opend",
    }) == []


def test_fake_naive_timestamp_rejected():
    errs = validate_event({
        "event_type": "fill", "timestamp": "2026-06-09 11:46:15",
        "ticker": "X", "side": "BUY", "qty": 1, "price": 1, "order_id": "A",
    })
    assert any("tz offset" in e for e in errs)


def test_enrichment_enums_enforced():
    base = {
        "event_type": "close", "timestamp": "2026-06-10T09:31:34-04:00",
        "ticker": "X", "side": "SELL", "qty": 1, "price": 1.3, "order_id": "B",
    }
    assert validate_event({**base, "tier": "position", "classification": "probe", "zone_at_fill": "zone1"}) == []
    assert any("tier" in e for e in validate_event({**base, "tier": "midcore"}))
    assert any("classification" in e for e in validate_event({**base, "classification": "yolo"}))


def test_expire_event_needs_no_order_id():
    assert validate_event({
        "event_type": "expire", "timestamp": "2026-06-12T16:00:00-04:00",
        "ticker": "INTC260612P100000", "qty": 1.0,
    }) == []


# ── tier taxonomy (: core+trading → position) ──────

def test_tier_taxonomy_is_base_and_position():
    from quant.core.schema import TIERS, LEGACY_TIER_MAP
    assert TIERS == {"base", "position"}
    assert LEGACY_TIER_MAP == {"core": "position", "trading": "position"}


def test_legacy_tier_is_rejected_on_new_events():
    """Old labels must not be writable — the ledger was migrated, and a
    straggler would silently split perf_report's tier attribution."""
    from quant.core.schema import validate_event
    base = {"event_type": "fill", "timestamp": "2026-07-24T10:17:10-04:00",
            "ticker": "MU", "side": "BUY", "qty": 1.0, "price": 925.0,
            "order_id": "FS1CDF59D1C63A1000"}
    assert validate_event({**base, "tier": "position"}) == []
    assert validate_event({**base, "tier": "base"}) == []
    for stale in ("core", "trading"):
        errors = validate_event({**base, "tier": stale})
        assert any("tier must be one of" in e for e in errors), f"{stale} should be rejected"


def test_lots_require_both_tiers_present():
    from quant.core.schema import validate_positions_doc
    doc = {"tickers": {"MU": {
        "instrument": {"type": "stock"}, "status": "active",
        "broker": {"qty": 1.0},
        "agent": {"thesis": {"summary": "t"}, "action": {"intent": "hold"}},
        "lots": {"position": [{"qty": 1.0, "avg_cost": 925.0}]},   # base missing
        "position_summary": {}, "pending_orders": [],
    }}}
    errors, _ = validate_positions_doc(doc)
    assert any("base/position" in e for e in errors)


# ── zones ↔ current_regime 同生同死 (regime 统一) ─────────

def _wl_doc(agent, status="watching"):
    return {"tickers": {"TST": {"status": status, "agent": agent}}}


def _regime(**kw):
    base = {"name": "常规参与", "cap_pct_nav": 0.05, "participation": "zone_eligible",
            "since": "2026-08-04", "source": "card"}
    base.update(kw)
    return base


def test_zoned_watching_card_without_regime_is_error():
    """授权语义不再允许活在读侧推断里 — 有 zones 必有 current_regime。"""
    from quant.core import schema
    errs, _ = schema.validate_watchlist_doc(_wl_doc(
        {"thesis": {"summary": "x"}, "zones": {"accumulation_zone_1": [1.0, 2.0]}}))
    assert any("current_regime missing" in e for e in errs)


def test_regime_without_zones_is_error():
    from quant.core import schema
    errs, _ = schema.validate_watchlist_doc(_wl_doc(
        {"thesis": {"summary": "x"}, "current_regime": _regime()}))
    assert any("without zones" in e for e in errs)


def test_conforming_card_passes_and_shape_is_enforced():
    from quant.core import schema
    ok = {"thesis": {"summary": "x"},
          "zones": {"accumulation_zone_1": [1.0, 2.0]}, "current_regime": _regime()}
    errs, _ = schema.validate_watchlist_doc(_wl_doc(ok))
    assert errs == []
    for bad, frag in ((_regime(cap_pct_nav=None), "cap_pct_nav"),
                      (_regime(cap_pct_nav=0.5), "cap_pct_nav"),
                      (_regime(participation="zone"), "participation"),
                      (_regime(since="8/4"), "since"),
                      (_regime(source=""), "source"),
                      # 枚举收死 (ledger N3/N4): 设计稿幽灵取值与 typo 都要被拦 ——
                      # 非 card 值让重画永不刷新该卡, 只查非空等于让 typo 静默锁卡
                      (_regime(source="transition"), "source"),
                      (_regime(source="promotion"), "source"),
                      (_regime(source="Card"), "source")):
        doc = _wl_doc({"thesis": {"summary": "x"},
                       "zones": {"accumulation_zone_1": [1.0, 2.0]}, "current_regime": bad})
        errs, _ = schema.validate_watchlist_doc(doc)
        assert any(frag in e for e in errs), frag


def test_non_watching_and_option_cards_are_exempt():
    """closed/stub 归档卡与期权卡不参与同生同死 (历史记录不强制迁移)。"""
    from quant.core import schema
    errs, _ = schema.validate_watchlist_doc(_wl_doc(
        {"thesis": {"summary": "x"}, "zones": {"accumulation_zone_1": [1.0, 2.0]}},
        status="closed"))
    assert not any("current_regime" in e for e in errs)
