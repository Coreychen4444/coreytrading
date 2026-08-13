"""option_plan —— 期权 vehicle 的执行参数。

纯计算层的回归测试 (IV 闸门 / BS 反解 / 每档限价 / 张数)。行情层 fetch_chain
需要 OpenD, 不在单测范围。
"""

from quant.options import option_plan as op  # noqa: E402


# ── IV 闸门 (options-strategy-framework §六 canonical 的可执行副本) ──

def test_iv_gate_thresholds_match_canonical():
    assert op.iv_gate(85.0)["verdict"] == "禁 naked long"
    assert op.iv_gate(75.0)["verdict"] == "强 sell-premium tilt"
    assert op.iv_gate(65.0)["verdict"] == "偏 sell premium"
    assert op.iv_gate(45.0)["verdict"] == "中性"
    assert op.iv_gate(10.0)["verdict"] == "买 premium 友好"


def test_iv_gate_boundaries_are_inclusive():
    """canonical 写的是 ≥60 / ≥70 / ≥80, 边界值归上一档。"""
    assert op.iv_gate(60.0)["verdict"] == "偏 sell premium"
    assert op.iv_gate(59.9)["verdict"] == "中性"
    assert op.iv_gate(80.0)["verdict"] == "禁 naked long"


def test_unreliable_percentile_downgrades_to_provisional():
    """n<60 时 percentile 是噪声。NVDA 2026-07-29 实测 n=5 → percentile 100%,
    不该据此发出"禁 naked long"的定案。"""
    g = op.iv_gate(100.0, reliable=False)
    assert g["provisional"] is True
    assert "provisional" in g["verdict"]
    assert "这个闸门未定案" in g["note"]
    assert op.iv_gate(100.0, reliable=True).get("provisional") is None


def test_iv_gate_without_history():
    g = op.iv_gate(None)
    assert "不可得" in g["verdict"] and "spot IV 不是 percentile" in g["note"]


# ── BS 层 ─────────────────────────────────────────────────────────

def test_bs_delta_monotonic_and_bounded():
    d_itm = op.bs_delta(200, 135, 1.5, 0.46)
    d_atm = op.bs_delta(200, 200, 1.5, 0.46)
    d_otm = op.bs_delta(200, 280, 1.5, 0.46)
    assert 0 < d_otm < d_atm < d_itm < 1
    assert 0.75 < d_itm < 0.95, f"DITM delta 应在 stock-replacement 带, 实得 {d_itm}"


def test_implied_vol_roundtrips():
    from quant.options.strike_compare import bs_call
    S, K, T, sigma = 196.8, 135.0, 1.48, 0.458
    price = bs_call(S, K, T, sigma)
    back = op.implied_vol(price, S, K, T)
    assert back is not None and abs(back - sigma) < 0.005


def test_implied_vol_rejects_no_time_value():
    """深 ITM 报价穿透到 intrinsic 时无 IV 可解 —— 返回 None 而不是编一个。
    moomoo 对这些档常返回 option_implied_volatility=0.0, 直接信会把估值算成纯 intrinsic。"""
    assert op.implied_vol(65.0, 200.0, 135.0, 1.5) is None
    assert op.implied_vol(0.0, 200.0, 135.0, 1.5) is None


# ── 每档限价 ──────────────────────────────────────────────────────

CONTRACT = {"strike": 135.0, "iv": 45.8}


def test_rung_premiums_fall_with_underlying():
    r = op.rung_premiums(CONTRACT, [195.53, 193.57, 191.0], dte=542)
    assert len(r) == 3
    prem = [x["premium_flat_iv"] for x in r]
    assert prem == sorted(prem, reverse=True), "标的越低期权越便宜"


def test_iv_bump_scenario_is_always_richer():
    """回调伴随 IV 上抬 —— 恒 IV 的限价系统性偏低, 照它挂大概率不成交。"""
    for r in op.rung_premiums(CONTRACT, [195.53, 191.0], dte=542):
        assert r["premium_iv_bump"] > r["premium_flat_iv"]


def test_rung_premiums_empty_without_iv():
    assert op.rung_premiums({"strike": 135.0, "iv": 0}, [195.0], dte=542) == []
    assert op.rung_premiums({"strike": 135.0, "iv": None}, [195.0], dte=542) == []


# ── 选约: 流动性是硬否决 ──────────────────────────────────────────

def _s(strike, delta, oi, spread):
    return {"strike": strike, "delta": delta, "oi": oi, "spread_pct": spread,
            "bid": 10.0, "ask": 11.0, "mid": 10.5, "iv": 45.0, "code": f"C{strike:g}"}


def test_pick_strike_rejects_thin_chain():
    """薄链不是 vehicle —— 挂得进去挂不出来 (LITE 卡: 全链最大档仅 1.2k → 否决期权)。"""
    sel = op.pick_strike([_s(135, 0.80, 12, 3.0)], iv_pct=40.0)
    assert sel["chosen"] is None
    assert "OI" in sel["rejected"][0][1]


def test_pick_strike_rejects_wide_spread():
    sel = op.pick_strike([_s(135, 0.80, 5000, 40.0)], iv_pct=40.0)
    assert sel["chosen"] is None and "spread" in sel["rejected"][0][1]


def test_pick_strike_uses_deeper_band_when_iv_high():
    """IV≥70% → 往低 strike 压 = 多拿 intrinsic 少付 extrinsic (canonical §六)。"""
    cands = [_s(180, 0.72, 5000, 3.0), _s(135, 0.86, 5000, 3.0)]
    low_iv = op.pick_strike(cands, iv_pct=40.0)
    high_iv = op.pick_strike(cands, iv_pct=85.0)
    assert low_iv["delta_band"] == list(op.PARAMS["delta_band"])
    assert high_iv["delta_band"] == list(op.PARAMS["delta_band_high_iv"])
    assert high_iv["chosen"]["strike"] < low_iv["chosen"]["strike"]


def test_pick_strike_picks_mid_of_band():
    """主带 = §13.1 定稿 Δ0.60-0.80 (由旧 DITM 0.70-0.85 对齐 canonical),
    中点 0.70 → 选 0.71; 0.95 出带。"""
    cands = [_s(190, 0.71, 5000, 3.0), _s(160, 0.78, 5000, 3.0), _s(120, 0.95, 5000, 3.0)]
    sel = op.pick_strike(cands, iv_pct=40.0)
    assert sel["delta_band"] == [0.60, 0.80]
    assert sel["chosen"]["strike"] == 190
    assert any(st == 120 for st, _ in sel["rejected"])


def test_pick_strike_low_iv_relaxes_to_light_otm():
    """IV pct <30 → 放宽档 (0.55, 0.80): 轻度 OTM 0.58 入围 (§13.1「仅当 IV<30-40」
    取保守端 30); 主带下 0.58 必须被拒 — 两个 iv_pct 给相反答案, 钉的是分支。"""
    cands = [_s(200, 0.58, 5000, 3.0), _s(160, 0.78, 5000, 3.0)]
    assert op.pick_strike(cands, iv_pct=25.0)["delta_band"] == [0.55, 0.80]
    assert any(st == 200 for st, _ in op.pick_strike(cands, iv_pct=45.0)["rejected"])
    assert not any(st == 200 for st, _ in op.pick_strike(cands, iv_pct=25.0)["rejected"])


# ── 张数走共享层 ──────────────────────────────────────────────────

def test_contract_sizing_uses_shared_integer_ladder():
    """1 张 = 100 股, 与正股共用整股/降档/书面例外逻辑, 单位显示为「张」。"""
    from quant.core.sizing import share_ladder
    sl = share_ladder(1127.0, [79.05, 77.38, 75.21], (0.5, 0.3, 0.2),
                      multiplier=100, unit="张")
    assert sl["exception"] is True, "cap $1,127 买不起 $7,905 的一张 LEAPS"
    assert "1 张 = $7,905" in sl["note"] and "买 1 张" in sl["note"]
    # "股" 只允许出现在规则名引用里 (§整股约束), 不能当计量单位
    assert "1 股" not in sl["note"] and "0 股" not in sl["note"]
    assert sl["cap_multiple"] == 7.01


def test_contract_sizing_affordable_case():
    from quant.core.sizing import share_ladder
    sl = share_ladder(12000.0, [5.0, 4.5, 4.0], (0.5, 0.3, 0.2), multiplier=100, unit="张")
    assert sl["n_shares"] == 24 and sl["n_tiers"] == 3
    assert sum(t["shares"] for t in sl["tiers"]) == 24


# ── 到期选择 pick_expiry (§13.2 执行周期,  移除 Jan 限制) ──

def test_pick_expiry_floor_beats_nearest():
    """LEAPS 语义 target=min=270: 下限单独成参 —— 250d 距 270 比 310d 更近,
    但破下限, 必须选 310d。这条被改回「纯最近」= 下限失守, 变异会红。"""
    cands = [("2027-04-16", 250), ("2027-06-18", 310), ("2028-01-21", 525)]
    assert op.pick_expiry(cands, 270, min_dte=270) == "2027-06-18"


def test_pick_expiry_not_jan_locked():
    """移除「最近 Jan 周期」限制的行为钉: 旧规则(target 540)选 Jan'28,
    新规则(target=min=270)选最近的 ≥270 周期 —— 6 月合格照选, 不跳去 Jan。"""
    cands = [("2027-01-15", 155), ("2027-06-18", 310), ("2028-01-21", 525)]
    assert op.pick_expiry(cands, 270, min_dte=270) == "2027-06-18"
    assert op.pick_expiry(cands, 540, min_dte=7) == "2028-01-21"  # 旧行为对照


def test_pick_expiry_default_keeps_old_nearest():
    """min_dte 缺省 7 = 旧行为: 纯最近 + 只滤周内到期 (非 LEAPS 调用不受影响)。"""
    cands = [("2026-08-14", 3), ("2026-09-18", 38), ("2026-10-16", 66)]
    assert op.pick_expiry(cands, 45,) == "2026-09-18"
    assert op.pick_expiry([("2026-08-14", 3)], 45) is None  # 全被滤 → None
