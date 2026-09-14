"""lib/structure —— gate 与 zone 同生死、同锚、自洽。

源 (用户): "gate 也应该具有时效性, 需要根据近期的数据 K 线走势
pivot HVN 等等维度进行定期更新; gate 和 zone 必须保持同步"。

实测动机 (同一方法, 同一只票 NBIS, 只换画卡日期):
    07-14 收 194.09 → confirmed 锚 ≈ 198
    07-21 收 216.92 → confirmed 锚 ≈ 234   ← 卡上这个
    07-27 收 187.88 → confirmed 锚 ≈ 198
234 不是 NBIS 的属性, 是"7/21"这个日期的属性。
"""

from quant.core import structure
from quant.core.structure import (# noqa: E402
    anchor_date, gate_audit_gaps, gate_coherence, gate_staleness, gates_of,
    structure_lines,
)


def card(*, anchored="2026-07-21", gate_anchored="2026-07-21", gate_level=234.0,
         no_chase=229.0, stop=176.0, gate_valid_until=None):
    ga = {"date": gate_anchored, "basis": "226.8-234.4 pivot×4 + HVN 8.2%"} if gate_anchored else None
    if ga and gate_valid_until:
        ga["valid_until"] = gate_valid_until
    return {
        "thesis": {"last_reviewed": f"{anchored}T00:00:00Z"},
        "zones": {"accumulation_zone_1": [205, 216], "no_chase_above": no_chase,
                  "stop_level": stop, "valid_until": "2026-08-05",
                  "anchored_on": {"date": anchored, "basis": "level_map volume profile"}},
        "next_regime": {"name": "C_confirmed_reclaim_234",
                        "entry_gate": {"level": gate_level, "days": 2,
                                       "unlocks": "confirmed", "volume_ratio_min": 1.2,
                                       **({"anchored_on": ga} if ga else {})}},
    }


# ── 收集 ──────────────────────────────────────────────────────────

def test_gates_of_collects_all_three_kinds():
    a = card()
    a["zones"]["reclaim_gates"] = [{"level": 845, "days": 2}]
    a["last_regime"] = {"name": "obs", "repair_gate": {"level": 176, "days": 2}}
    kinds = {g["kind"] for g in gates_of(a)}
    assert kinds == {"reclaim", "up", "down"}


def test_anchor_date_prefers_explicit_over_last_reviewed():
    a = card(anchored="2026-07-21")
    a["thesis"]["last_reviewed"] = "2026-07-01T00:00:00Z"
    assert anchor_date(a) == "2026-07-21"
    del a["zones"]["anchored_on"]
    assert anchor_date(a) == "2026-07-01"


# ── 第 1 层: 同生死 ───────────────────────────────────────────────

def test_zone_stale_forces_gate_stale():
    """两者锚在同一份结构上, 不存在 zone 过期而 gate 仍有效。"""
    out = gate_staleness(card(), "2026-07-29", ["valid_until 2026-07-25 已过期"])
    assert any("zone 已 stale" in r and "gate 同步失效" in r for r in out)


def test_fresh_zone_and_matching_anchor_is_clean():
    assert gate_staleness(card(), "2026-07-29", []) == []


# ── 第 2 层: 同锚 ─────────────────────────────────────────────────

def test_half_redraw_is_detected():
    """只重画 zone 不重画 gate → 两边日期分叉, 这本身就是告警。"""
    out = gate_staleness(card(anchored="2026-07-29", gate_anchored="2026-07-21"),
                         "2026-07-29", [])
    assert any("只重画了一半" in r for r in out)


def test_missing_anchored_on_is_advisory_not_stale():
    """缺审计字段是文档欠账, 不是"结构已失效"。2026-07-29 之前画的卡全都没有 ——
    判 STALE 会让全部 5 张卡当场作废、当晚入场计划整段空掉。"""
    from quant.core.structure import gate_audit_gaps
    a = card(gate_anchored=None)
    assert gate_staleness(a, "2026-07-29", []) == []
    assert any("无 anchored_on" in r for r in gate_audit_gaps(a))
    assert gate_audit_gaps(card()) == []


def test_same_day_drift_tolerated():
    """容忍跨午夜写入, 但真正的半吊子重画会差好几天。"""
    assert gate_staleness(card(anchored="2026-07-21", gate_anchored="2026-07-22"),
                          "2026-07-29", []) == []


def test_gate_own_valid_until_expires():
    out = gate_staleness(card(gate_valid_until="2026-07-25"), "2026-07-29", [])
    assert any("已过期" in r for r in out)


# ── 第 3 层: 自洽 ─────────────────────────────────────────────────

def test_up_gate_below_no_chase_is_incoherent():
    """把 confirmed 锚重画到 198 而 no_chase 仍是 7/21 的 229 → gate 掉到了
    "本 regime 都不许追"的价位之下。这是只重画一半最先出现的症状。"""
    out = gate_coherence(card(gate_level=198.0, no_chase=229.0))
    assert out and "倒挂" in out[0] and "只重画了 gate 没重画 zone" in out[0]


def test_coherent_gate_passes():
    assert gate_coherence(card(gate_level=234.0, no_chase=229.0)) == []


def test_gate_equal_to_no_chase_is_by_design():
    """"在追价上限处确认突破" 是正常设计, 不是倒挂。
    NVDA 实卡: gate 214 / no_chase 214, label "no-chase 突破确认→重锚再评"。
    首版写 <= 把它判成不自洽, 差点让一张健康的卡整天 STALE。"""
    assert gate_coherence(card(gate_level=214.0, no_chase=214.0)) == []


def test_repair_gate_below_stop_is_incoherent():
    a = card()
    a["last_regime"] = {"name": "obs", "repair_gate": {"level": 150.0, "days": 2}}
    a["zones"]["stop_level"] = 176.0
    assert any("还没跌到那儿就先谈修复" in r for r in gate_coherence(a))


# ── 播报 ──────────────────────────────────────────────────────────

def test_structure_lines_quiet_when_healthy():
    assert structure_lines(card(), "2026-07-29", []) == []


def test_structure_lines_show_anchor_date_when_flagging():
    txt = "\n".join(structure_lines(card(gate_level=198.0), "2026-07-29", []))
    assert "结构锚定于 2026-07-21" in txt and "倒挂" in txt


def test_card_without_gates_is_not_flagged():
    """没有 regime 机制的卡不受影响。"""
    a = {"zones": {"accumulation_zone_1": [1, 2]}, "thesis": {}}
    assert gate_staleness(a, "2026-07-29", ["随便什么"]) == []
    assert gate_coherence(a) == []


# ── derive_card_regime (regime 统一 , 方案 a) ──────────────

def _zoned_agent(**z):
    zones = {"accumulation_zone_1": [80.0, 84.0], "valid_until": "2099-01-01",
             "anchored_on": {"date": "2026-08-04", "basis": "test"}}
    zones.update(z)
    return {"thesis": {"summary": "x", "last_reviewed": "2026-08-03T10:00:00Z"},
            "zones": zones}


def test_derive_clean_watchlist_card_waits_for_a_gate():
    """干净候选 → 候选·等 gate, cap 0 (两种模式: 「常规参与 cap 40%」无 gate 的第三路径退役;
    入场 = gate 转移 或 抄底计划)。since = 结构锚定日, 不是执行日 (铁则 2)。"""
    from quant.core.structure import derive_card_regime
    a = _zoned_agent()
    assert derive_card_regime(a, bucket="watchlist", today="2026-08-06") == "derived_wait_gate"
    cr = a["current_regime"]
    assert cr["cap_pct_nav"] == 0 and cr["participation"] == "gate_only"
    assert cr["name"] == "候选·等 gate" and "常规参与退役" in cr["reason"]
    assert cr["since"] == "2026-08-04"                # anchored_on, 不是 today
    assert cr["source"] == "card"


def test_derive_repair_candidate_gets_cap_zero():
    from quant.core.structure import derive_card_regime
    a = _zoned_agent(reclaim_gates=[{"level": 92.5, "days": 2}])
    assert derive_card_regime(a, bucket="watchlist", today="2026-08-06") == "derived_repair"
    cr = a["current_regime"]
    assert cr["cap_pct_nav"] == 0 and cr["participation"] == "gate_only"
    assert cr["name"] == "修复观察"


def test_derive_position_bucket_is_no_add_authorization():
    """持仓无机器 regime = 入场已行使, 加仓待 gate (NBIS 规则显式化)。"""
    from quant.core.structure import derive_card_regime
    a = _zoned_agent()
    assert derive_card_regime(a, bucket="positions", today="2026-08-06") == "derived_no_add"
    assert a["current_regime"]["cap_pct_nav"] == 0
    assert a["current_regime"]["name"] == "持仓·无加仓授权"


def test_derive_never_touches_machine_source_regimes():
    """auto/demoted/promotion = 转移机/促升所有 — 重画不许覆盖 (所有权判别式)。"""
    from quant.core.structure import derive_card_regime
    a = _zoned_agent()
    a["current_regime"] = {"name": "B_repair_probe_189", "cap_pct_nav": 0.02,
                           "participation": "zone_eligible", "since": "2026-08-04",
                           "source": "auto"}
    assert derive_card_regime(a, bucket="watchlist", today="2026-08-06") == "kept_machine"
    assert a["current_regime"]["name"] == "B_repair_probe_189"    # 原样


def test_derive_card_source_is_recomputed_on_structure_change():
    """card 源 regime 跟结构走: 干净候选画出修复梯 → 常规参与降为修复观察。"""
    from quant.core.structure import derive_card_regime
    a = _zoned_agent()
    derive_card_regime(a, bucket="watchlist", today="2026-08-06")
    assert a["current_regime"]["name"] == "候选·等 gate"
    a["zones"]["reclaim_gates"] = [{"level": 92.5}]
    derive_card_regime(a, bucket="watchlist", today="2026-08-07")
    assert a["current_regime"]["name"] == "修复观察"


def test_derive_clears_regime_when_zones_gone():
    """同生同死: zones 清空 → card 源 regime 一并清 (机器源仍不触碰)。"""
    from quant.core.structure import derive_card_regime
    a = _zoned_agent()
    derive_card_regime(a, bucket="watchlist", today="2026-08-06")
    a["zones"] = None
    assert derive_card_regime(a, bucket="watchlist", today="2026-08-06") == "cleared"
    assert "current_regime" not in a


# ── 结构修复 VETO 判别式 (, 前身 = 入场档位判别式 08-11) ──────────

def test_repair_veto_clean_uptrend_passes_without_any_gate():
    """「上升结构可以不等 gate 直接 R1 入场」—— 判别式里不许出现 gate。
    这条钉的是: 结构干净 = 不 VETO, 与 next_regime / reclaim_gates 无关。"""
    st = {"trend": "up", "high_label": "HH", "low_label": "HL", "last_swing_high": 270.0}
    vetoed, why = structure.structure_repair_veto(None, st, 278.0)
    assert vetoed is False and why is None


def test_repair_veto_only_unrepaired_structure():
    """VETO = 结构未修复 (down/transition 且未收回 last swing high)。
    收回了 = 修复完成 → 放行, 不因 trend 字符串还写着 down 就压着。
    (内核与旧「降级 probe」逐字同构 — structure_downgrade_backtest 证据继续有效。)"""
    st = {"trend": "transition", "high_label": "HH", "low_label": "LL",
          "last_swing_high": 100.0}
    vetoed, why = structure.structure_repair_veto(None, st, 95.0)
    assert vetoed is True and "未修复" in why
    vetoed2, _ = structure.structure_repair_veto(None, st, 105.0)
    assert vetoed2 is False


def test_repair_veto_insufficient_structure_does_not_veto():
    """结构数据不足不 VETO (判别式层); 数据缺失的保守回落归调用方
    (today_group 无 K 线 → 不 grant; entry_guard 无 rows → NEEDS_CARD)。"""
    assert structure.structure_repair_veto(None, {}, 100.0) == (False, None)
    assert structure.structure_repair_veto(None, {"trend": "insufficient"}, 100.0) == (False, None)


def test_repair_veto_matches_today_group():
    """判别式单一真源: entry_guard 与 today_group 必须读同一个函数。
    两份实现时 AMZN 在入场计划与手机备注上差 19× —— 别再回去。"""
    from quant.sync import today_group_sync as tgs
    rows = [{"close": 100.0}] * 3
    card = {"agent": {}}
    direct = structure.structure_repair_veto(None, {"trend": "up"}, 100.0)
    via_group = tgs.structure_veto_of("X", card, lambda t: rows)
    assert direct[0] == via_group[0] is False


def test_machine_source_cap_is_exempt_from_runtime_veto():
    """用户  二裁:「之前给了 cap 的 probe 仓位…应该是直接允许 R1 入场」。
    机器源 (auto/demoted) 的 cap = 转移机在 gate 收复 ×2+量腿后授予, 修复裁决已
    做完 —— 运行时 swing 标签 (常挂历史 LL) 不得复审推翻 (NVDA/MU 案: transition
    标签把刚收复的修复票 VETO 成 0)。降级权归转移机 (破 stop → cap 0)。
    card 源照旧适用 (META down 结构常规卡拦得对)。"""
    from quant.sync import today_group_sync as tgs
    bad_st = {"trend": "transition", "high_label": "HH", "low_label": "LL",
              "last_swing_high": 300.0}      # 未收回 → card 源会 VETO 的形状
    machine = {"current_regime": {"name": "B_repair_probe_204", "cap_pct_nav": 0.40,
                                  "source": "auto"}}
    card_src = {"current_regime": {"name": "常规参与", "cap_pct_nav": 0.40,
                                   "source": "card"}}
    assert structure.structure_repair_veto(machine, bad_st, 217.5) == (False, None)
    assert structure.structure_repair_veto(card_src, bad_st, 217.5)[0] is True
    # today_group 同拍: 机器源连 K 线都不需要 (source 字段读), 无缓存也豁免
    assert tgs.structure_veto_of("NVDA", {"agent": machine}, lambda t: [])[0] is False
    assert tgs.structure_veto_of("META", {"agent": card_src}, lambda t: [])[0] is True


# ── 同锚破裂: 单一判别式, 两个消费者 () ──────────────────
#
# `gate_staleness` 的唯一调用方 entry_guard **只评估 watchlist**⇒ 持仓卡的
# 同锚破裂零出口。884411d (收据复核) 一笔造出两个方向相反的破裂:
# MU (watchlist) zone 8/06 而 entry_gate 8/11 → 天天喊 STALE;
# MRVL (持仓) zone 8/11 而 entry_gate 8/06 → 一声不响。
# 抽出 gate_anchor_desync 供两边共读 —— 不是在 brief 里再写一遍 (同一个概念
# 两份判别式 =  GOOGL 卡被清空 6 天的病根)。

def _desync_card(zone_date="2026-08-06", gate_date="2026-08-11", **extra):
    return {"status": "watching", "instrument": {"type": "stock"},
            "agent": {"zones": {"accumulation_zone_1": [848.0, 861.0],
                                "anchored_on": {"date": zone_date, "basis": "b"}},
                      "next_regime": {"name": "R_x", "entry_gate": {
                          "level": 945.0, "days": 2,
                          "anchored_on": {"date": gate_date, "basis": "g"}}}},
            **extra}


def test_desync_is_detected_in_both_directions():
    from quant.core.structure import gate_anchor_desync
    late = gate_anchor_desync(_desync_card("2026-08-06", "2026-08-11")["agent"])
    early = gate_anchor_desync(_desync_card("2026-08-11", "2026-08-06")["agent"])
    assert late and "锚于 2026-08-11, zone 锚于 2026-08-06" in late[0]     # MU
    assert early and "锚于 2026-08-06, zone 锚于 2026-08-11" in early[0]   # MRVL


def test_same_anchor_and_one_day_drift_are_quiet():
    """容差 1 日 = 同一轮分析跨午夜写入; 真正的半重画差好几天。"""
    from quant.core.structure import ANCHOR_DRIFT_TOLERANCE_DAYS, gate_anchor_desync
    assert ANCHOR_DRIFT_TOLERANCE_DAYS == 1
    assert gate_anchor_desync(_desync_card("2026-08-06", "2026-08-06")["agent"]) == []
    assert gate_anchor_desync(_desync_card("2026-08-06", "2026-08-07")["agent"]) == []
    assert gate_anchor_desync(_desync_card("2026-08-06", "2026-08-08")["agent"]) != []


def test_missing_anchors_are_not_desync():
    """缺 anchored_on 归 gate_audit_gaps (提示级); zone 无锚整张跳过 ——
    2026-07-29 前的卡全都没有, 混进判定实测会让全部卡当场作废。"""
    from quant.core.structure import gate_anchor_desync
    no_zone_anchor = _desync_card()["agent"]
    no_zone_anchor["zones"].pop("anchored_on")
    assert gate_anchor_desync(no_zone_anchor) == []
    no_gate_anchor = _desync_card()["agent"]
    no_gate_anchor["next_regime"]["entry_gate"].pop("anchored_on")
    assert gate_anchor_desync(no_gate_anchor) == []


def test_gate_staleness_still_reports_desync_through_the_shared_discriminant():
    """抽出去了但没丢 —— entry_guard 的 STALE 照旧看得见 (共读一份)。"""
    from quant.core.structure import gate_staleness
    out = gate_staleness(_desync_card()["agent"], "2026-08-12")
    assert any("只重画了一半" in r for r in out)


def test_book_scan_covers_positions_not_just_watchlist():
    """🚨 本节存在的理由: MRVL 那半住在 positions, entry_guard 够不着。"""
    from quant.core.structure import cards_with_gate_anchor_desync
    pos = {"tickers": {"MRVL": _desync_card("2026-08-11", "2026-08-06", status="active")}}
    wl = {"tickers": {"MU": _desync_card("2026-08-06", "2026-08-11")}}
    out = cards_with_gate_anchor_desync(pos, wl)
    assert len(out) == 2
    assert any(r.startswith("MRVL ") for r in out) and any(r.startswith("MU ") for r in out)


def test_book_scan_skips_closed_option_and_zoneless_cards():
    from quant.core.structure import cards_with_gate_anchor_desync
    doc = {"tickers": {
        "A": _desync_card(status="closed"),
        "B": _desync_card(instrument={"type": "option"}),
        "C": {"status": "watching", "instrument": {"type": "stock"},
              "agent": {"zones": None}},
        "D": _desync_card(instrument={"type": "stock", "role": "commemorative_hold"}),
    }}
    assert cards_with_gate_anchor_desync(doc) == []


# ── zone_mech_stale (机械 staleness 单一真源, 下沉) ──────
#
# 源案例 MU: entry_guard 判 STALE (breakdown 当日口径) 拒绝给计划,
# 而 today_group 白名单照常授权 $7,460、moomoo z1 备注画 950 (40%:7.5k)、brief
# 还发 📥 触碰告警 —— 同一张卡四个数。判别式此前只活在 entry_guard, 下沉后
# entry_guard 委托、today_group/_admit 与 brief wl_zone_touch 读同一份。

def _mech_rows(closes, start_day=1):
    return [{"time": f"2026-07-{start_day + i:02d}", "close": c,
             "high": c + 1, "low": c - 1} for i, c in enumerate(closes)]


def test_mech_stale_accepts_both_key_shapes():
    """原始卡片键名 (accumulation_zone_2/trim_zone) 与 zone_bands 产物 (z2/trim)
    结论必须一致 —— entry_guard 手里是后者, sync 层是前者, 委托边界不做逆映射。"""
    from quant.core.structure import zone_mech_stale
    rows = _mech_rows([112, 111, 108])          # 最后收盘 108 < z2 下沿 110
    raw = {"accumulation_zone_1": [110, 115], "accumulation_zone_2": [100, 110],
           "trim_zone": [150, 160], "valid_until": "2099-01-01"}
    zb = {"z1": [110.0, 115.0], "z2": [100.0, 110.0], "trim": [150, 160],
          "valid_until": "2099-01-01"}
    # 两种键名同判 fresh… 等等 — 108 > z2 下沿 100, fresh ✓
    assert zone_mech_stale(raw, {}, rows, "2026-07-04") == \
        zone_mech_stale(zb, {}, rows, "2026-07-04") == []
    rows_bd = _mech_rows([112, 111, 99])        # 收盘 99 < z2 下沿 100 = breakdown
    r1 = zone_mech_stale(raw, {}, rows_bd, "2026-07-04")
    r2 = zone_mech_stale(zb, {}, rows_bd, "2026-07-04")
    assert r1 == r2 and any("breakdown stale" in x for x in r1)


def test_mech_stale_breakdown_is_same_day_not_confirmed():
    """破位当日口径 (用户): 收盘 < z2 下沿**当日**即 stale, 不等 ×2。"""
    from quant.core.structure import zone_mech_stale
    zones = {"accumulation_zone_1": [940.5, 950.0], "accumulation_zone_2": [921.5, 940.5],
             "trim_zone": [990.0, 1008.0], "valid_until": "2026-08-28"}
    rows = _mech_rows([966.78, 910.43])         # MU 8/21 → 8/24 实况: 单日破 z2 下沿
    hits = zone_mech_stale(zones, {}, rows, "2026-08-25")
    assert any("breakdown stale" in h for h in hits)


def test_mech_stale_breakout_needs_two_closes():
    from quant.core.structure import zone_mech_stale
    zones = {"accumulation_zone_1": [110, 115], "accumulation_zone_2": [100, 110],
             "trim_zone": [150, 160], "valid_until": "2099-01-01"}
    one = _mech_rows([140, 161])                # 只 1 日 > trim 上沿
    assert not any("breakout" in h for h in zone_mech_stale(zones, {}, one, "2026-07-04"))
    two = _mech_rows([161, 162])                # 连续 2 日
    assert any("breakout stale" in h for h in zone_mech_stale(zones, {}, two, "2026-07-04"))


def test_mech_stale_quiet_legs_on_missing_inputs():
    """判不了的腿不拦: prose-only 卡 (无价格带) 只剩时间腿; 无 K 线只剩时间腿;
    全 fresh = 空清单。"""
    from quant.core.structure import zone_mech_stale
    assert zone_mech_stale(None, None, [], "2026-08-25") == []
    assert zone_mech_stale({"valid_until": "2026-08-01"}, {}, [], "2026-08-25") == \
        ["valid_until 2026-08-01 已过期"]
    hits = zone_mech_stale({}, {"last_reviewed": "2026-08-01"}, [], "2026-08-25")
    assert hits and "last_reviewed" in hits[0]


# ── 期权 BUY 的正股价位 flags (期权锚定正股回踩 z1, 不追高) ──

_ZB = {"z1": [216.6, 220.4], "z2": [209.0, 212.8], "no_chase": 220.4,
       "trim": [232.0, 236.3], "stop": 200.15}


def test_option_fill_flags_whole_bar_above_z1_is_a_chase():
    flags, amb = structure.option_fill_flags(221.0, 223.5, _ZB)
    assert "above_z1" in flags and "above_no_chase" in flags and amb is False


def test_option_fill_flags_inside_z1_is_clean():
    assert structure.option_fill_flags(217.0, 219.9, _ZB) == ([], False)


def test_option_fill_flags_bar_straddling_z1_top_is_ambiguous_not_flagged():
    """保守: 整根 bar 在带外才判, 跨带归人判 (不冤枉也不放过)。"""
    flags, amb = structure.option_fill_flags(219.0, 222.0, _ZB)
    assert flags == [] and amb is True


def test_option_fill_flags_gap_between_zones_is_not_a_chase():
    assert structure.option_fill_flags(213.5, 215.0, _ZB) == ([], False)


def test_option_fill_flags_whole_bar_below_z2_is_breakdown_buy():
    flags, amb = structure.option_fill_flags(205.0, 208.5, _ZB)
    assert flags == ["below_z2"] and amb is False
    flags, amb = structure.option_fill_flags(208.0, 210.0, _ZB)      # 跨 z2 下沿
    assert flags == [] and amb is True


def test_option_fill_flags_trim_and_no_chase_semantics_match_stock():
    flags, _ = structure.option_fill_flags(233.0, 235.0, _ZB)
    assert set(flags) == {"above_z1", "above_no_chase", "in_trim_zone"}
    zb = dict(_ZB, no_chase=None, trim=None)
    assert structure.option_fill_flags(221.0, 223.5, zb) == (["above_z1"], False)


def test_cards_missing_bottom_stamp_flags_unstamped_bottom_positions():
    """抄底成交落了 lot (entry_class + 止损 = 止跌低) 但持仓卡没盖章 / 卡 stop ≠ 止跌低 → 点名;
    盖好了不点; 已平仓 (qty 0) 不点。"""
    from quant.core.structure import cards_missing_bottom_stamp
    lot = {"qty": 10, "avg_cost": 205.0, "entry_class": "reversal_probe", "stop_loss": 194.8}
    unstamped = {"tickers": {"TST": {"broker": {"qty": 10}, "lots": {"position": [lot]},
                                      "agent": {"zones": {"stop_level": 180.0}}}}}
    out = cards_missing_bottom_stamp(unstamped)
    assert len(out) == 1 and out[0].startswith("TST: lot 止损 194.8 vs 卡 stop 180.0")
    stamped = {"tickers": {"TST": {"broker": {"qty": 10}, "lots": {"position": [lot]},
                                    "agent": {"entry_class": "reversal_probe", "zones": {"stop_level": 194.8}}}}}
    assert cards_missing_bottom_stamp(stamped) == []
    flat = {"tickers": {"TST": {"broker": {"qty": 0}, "lots": {"position": [lot]}, "agent": {}}}}
    assert cards_missing_bottom_stamp(flat) == []
    assert cards_missing_bottom_stamp({"tickers": {"X": {"broker": {"qty": 3}, "lots": {"position": [{"qty": 3}]},
                                                          "agent": {}}}}) == []
