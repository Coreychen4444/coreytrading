"""level_map pure-core regressions — profile bucketing, HVN merge, pivot
clustering, level interaction semantics (built , AAOI 128/160 源案例)."""
from quant.decision import level_map


def _bar(t, o, h, l, c, vol=1_000_000, to=None):
    return {"time": t, "open": o, "high": h, "low": l, "close": c,
            "volume": vol, "turnover": to if to is not None else vol * c}


PROFILE_BARS = [
    _bar("2026-01-01", 100, 102, 98, 100, to=1e9),   # tp=100, range 98-102
    _bar("2026-01-02", 100, 103, 99, 101, to=1e9),   # tp=101, range 99-103
    _bar("2026-01-03", 110, 112, 108, 110, to=2e9),  # tp=110, range 108-112
]


def test_profile_legacy_point_mass_mode_is_unchanged():
    """spread=False =  之前的口径, 保留只为回归对照 (别用它做决策)。"""
    prof = level_map.make_profile(PROFILE_BARS, bucket=4.0, spread=False)
    assert [b["lo"] for b in prof] == [100.0, 108.0]
    assert prof[0]["turnover"] == 2e9 and prof[1]["turnover"] == 2e9
    assert prof[0]["share_pct"] == 50.0


def test_profile_spreads_turnover_across_the_bar_range():
    """默认铺开: 成交额落在 bar **真实走过**的价位上, 而不是全压在 TP 那一格。
    旧法把日内 10%+ 的 range 压成一个点, 相邻价位凭空变 0.0% (AAOI 60 日里
    30/63 桶被误报成完全真空) —— LVN/HVN/gate 全长在这个分布上。"""
    prof = level_map.make_profile(PROFILE_BARS, bucket=4.0)
    los = [b["lo"] for b in prof]

    assert los == [96.0, 100.0, 108.0], "低于 TP 但 bar 走到过的价位必须分到量"
    assert abs(sum(b["turnover"] for b in prof) - 4e9) < 1e-6, "铺开不许凭空增减总量"
    assert prof[0]["turnover"] < prof[1]["turnover"], "远离 TP 的一侧该分得更少"


def test_profile_triangle_peaks_at_typical_price():
    """单根 bar 的权重形状: 峰在 TP 所在桶, 两端衰减 —— 价格在区间极值停留最短。"""
    one = [_bar("2026-01-01", 100, 112, 88, 106, to=1e9)]     # tp=102, range 88-112
    prof = level_map.make_profile(one, bucket=4.0)
    peak = max(prof, key=lambda b: b["turnover"])

    assert peak["lo"] == 100.0, f"峰该在 TP=102 那格, 实际 {peak['lo']}"
    assert prof[0]["turnover"] < peak["turnover"] > prof[-1]["turnover"]
    assert abs(sum(b["turnover"] for b in prof) - 1e9) < 1e-6


def test_profile_minute_bar_stays_within_two_buckets():
    """喂分钟线时 range 远小于 1 bucket → 至多落在相邻两桶 (跨桶边界时), 且质量
    集中在 TP 那格。同一函数对两种粒度都正确: 有分钟数据就准, 没有就比旧法好 3 倍。"""
    minute = [_bar("2026-01-01", 100, 100.3, 99.8, 100.1, to=1e9)]
    prof = level_map.make_profile(minute, bucket=4.0)

    assert len(prof) <= 2
    assert max(prof, key=lambda b: b["turnover"])["lo"] == 100.0
    inside = [b for b in prof if b["lo"] == 100.0][0]
    assert inside["share_pct"] >= 70.0


def test_top_hvn_merges_adjacent_buckets():
    prof = [
        {"lo": 100.0, "hi": 104.0, "turnover": 5.0, "share_pct": 25.0},
        {"lo": 104.0, "hi": 108.0, "turnover": 6.0, "share_pct": 30.0},
        {"lo": 120.0, "hi": 124.0, "turnover": 9.0, "share_pct": 45.0},
    ]
    hvn = level_map.top_hvn(prof, n=3)
    # 100-104 + 104-108 contiguous → merged band 100-108 (11.0) beats 120-124 (9.0)
    assert hvn[0]["lo"] == 100.0 and hvn[0]["hi"] == 108.0
    assert hvn[0]["turnover"] == 11.0
    assert hvn[1]["lo"] == 120.0


def test_find_lvn_voids_thin_and_edge_trim():
    """LVN (2026-08-06, FVG 评估产物): 缺失桶=真空(profile 里本来不可见),
    ≤25%×POC=稀薄, 边缘带掐掉。"""
    prof = [
        {"lo": 100.0, "hi": 104.0, "turnover": 1.0, "share_pct": 3.0},    # 边缘稀薄 → 掐
        {"lo": 104.0, "hi": 108.0, "turnover": 10.0, "share_pct": 31.0},  # POC
        {"lo": 108.0, "hi": 112.0, "turnover": 8.0, "share_pct": 25.0},
        # 112-116 缺失 → 真空 LVN
        {"lo": 116.0, "hi": 120.0, "turnover": 9.0, "share_pct": 28.0},
        {"lo": 120.0, "hi": 124.0, "turnover": 2.0, "share_pct": 6.0},    # 稀薄 (2.0 ≤ 2.5)
        {"lo": 124.0, "hi": 128.0, "turnover": 2.2, "share_pct": 7.0},    # 稀薄相邻 → 合并
        {"lo": 128.0, "hi": 132.0, "turnover": 9.5, "share_pct": 30.0},   # 尾桶厚 → 不是边缘带
    ]
    bands = level_map.find_lvn(prof, bucket=4.0)
    assert [(b["lo"], b["hi"]) for b in bands] == [(112.0, 116.0), (120.0, 128.0)]
    assert bands[0]["void"] is True                       # 全真空
    assert bands[1]["void"] is False                      # 稀薄非真空
    assert bands[1]["share_pct"] == 13.0                  # 6.0 + 7.0 合并


def test_find_lvn_void_thin_adjacent_merge_and_guards():
    # 真空+稀薄相邻合并成一条带, void 归 False (只要有一格有量)
    prof = [
        {"lo": 100.0, "hi": 104.0, "turnover": 10.0, "share_pct": 40.0},
        # 104-108 缺失 (真空)
        {"lo": 108.0, "hi": 112.0, "turnover": 1.0, "share_pct": 4.0},    # 稀薄, 与真空相邻
        {"lo": 112.0, "hi": 116.0, "turnover": 9.0, "share_pct": 36.0},
        {"lo": 116.0, "hi": 120.0, "turnover": 5.0, "share_pct": 20.0},
    ]
    bands = level_map.find_lvn(prof, bucket=4.0)
    assert [(b["lo"], b["hi"], b["void"]) for b in bands] == [(104.0, 112.0, False)]
    # 网格 <5 格 → 无"内部"可言, 空手而归
    assert level_map.find_lvn(prof[:2], bucket=4.0) == []
    assert level_map.find_lvn([], bucket=4.0) == []


def test_build_map_annotates_lvn_docking_hvn():
    """build_map 给每条 LVN 带标停靠 HVN (回踩滑到哪/反弹顶在哪)。单日大 bar
    穿越留下的真空 = FVG 想抓的东西, 用真 turnover 分布直接找到。"""
    bars = ([_bar(f"2026-01-{d:02d}", 100, 102, 98, 100, to=5e9) for d in range(1, 11)]
            + [_bar("2026-01-11", 100, 140, 100, 140, to=1e9)]        # 大 bar 单日穿越
            + [_bar(f"2026-01-{d:02d}", 140, 142, 138, 140, to=5e9) for d in range(12, 22)])
    m = level_map.build_map(bars, bucket=4.0)
    assert m["lvn_bands"], "大 bar 穿越必须留下 LVN 带"
    band = m["lvn_bands"][0]
    assert band["hvn_below"] and band["hvn_below"][0] <= 100.0 + 4.0   # 下停靠 = 100 区
    assert band["hvn_above"] and band["hvn_above"][1] >= 140.0         # 上顶 = 140 区
    assert m["lvn_frac"] == 0.25


def test_load_zone_context_closed_position_does_not_shadow_watchlist(tmp_path):
    """卡源判别式 = qty: closed 残卡 (qty 0) 不遮 watchlist 真源 (INTC 2026-08-06 实证);
    live 持仓 (qty≠0) 仍以 positions 卡为准。"""
    import json
    pos = {"tickers": {
        "INTC": {"status": "closed", "broker": {"qty": 0},
                 "agent": {"zones": {"accumulation_zone_1": [79.6, 84.0]}}},
        "NBIS": {"status": "active", "broker": {"qty": 1},
                 "agent": {"zones": {"accumulation_zone_1": [197.0, 204.6]}}}}}
    wl = {"tickers": {
        "INTC": {"status": "watching",
                 "agent": {"zones": {"accumulation_zone_1": [95.5, 98.7]}}}}}
    pp, wp = tmp_path / "p.json", tmp_path / "w.json"
    pp.write_text(json.dumps(pos))
    wp.write_text(json.dumps(wl))
    intc = level_map.load_zone_context("INTC", pp, wp)
    assert intc["source"] == "watchlist"
    assert intc["zones"]["accumulation_zone_1"] == [95.5, 98.7]
    nbis = level_map.load_zone_context("NBIS", pp, wp)
    assert nbis["source"] == "positions"


def test_cluster_pivots_merges_within_tol_and_counts_kinds():
    pivots = [
        {"price": 150.1, "date": "2026-05-19", "kind": "low"},
        {"price": 151.0, "date": "2026-06-09", "kind": "low"},
        {"price": 152.4, "date": "2026-06-26", "kind": "high"},  # support→resistance flip
        {"price": 170.0, "date": "2026-06-04", "kind": "high"},
    ]
    out = level_map.cluster_pivots(pivots, tol=2.0)
    assert len(out) == 2
    band = out[0]
    assert band["touches"] == 3
    assert band["kinds"] == "high+low"
    assert band["last_touch"] == "2026-06-26"
    assert out[1]["touches"] == 1


def test_level_interactions_rejections_and_streak():
    bars = [
        _bar("2026-07-06", 120, 129.8, 116, 123.4),   # reject (high near 130, close below)
        _bar("2026-07-07", 118, 126.2, 111, 114.4),   # no touch (126.2 < 130*0.985=128.05)
        _bar("2026-07-09", 119, 128.5, 118, 122.2),   # reject
        _bar("2026-07-14", 119, 131.0, 117, 132.0),   # close above
        _bar("2026-07-15", 132, 135.0, 130, 133.0),   # close above
    ]
    r = level_map.level_interactions(bars, 130.0)
    assert r["closes_above"] == 2
    assert r["n_rejections"] == 2
    assert r["closing_streak_above"] == 2
    assert r["recent_rejections"][0]["date"] == "2026-07-06"


def test_auto_bucket_scales():
    assert level_map.auto_bucket(125.0, 14.0) == 3.5      # ATR/4
    assert level_map.auto_bucket(1000.0, None) == 20.0    # fallback 2% of spot
    assert level_map.auto_bucket(5.0, 0.04) >= 0.1        # floor


def test_build_map_smoke():
    bars = [_bar(f"2026-04-{i:02d}", 100 + i, 102 + i, 98 + i, 100 + i) for i in range(1, 31)] \
         + [_bar(f"2026-05-{i:02d}", 130 - i, 132 - i, 128 - i, 130 - i) for i in range(1, 11)]
    m = level_map.build_map(bars, levels=[125.0], profile_days=30)
    assert m["spot"] == 120.0
    assert m["atr14"] and m["ma20"]
    assert m["levels"][0]["level"] == 125.0
    assert isinstance(m["hvn_bands"], list) and m["hvn_bands"]


def test_chandelier_since_filter_and_parabolic():
    bars = [
        _bar("2026-06-01", 100, 105, 99, 104),
        _bar("2026-06-10", 130, 141, 128, 140),   # HWM close 140
        _bar("2026-07-01", 120, 125, 115, 118),
        _bar("2026-07-14", 108, 110, 105, 107),
    ]
    ch = level_map.chandelier(bars, n=9.0, since="2026-06-01")
    assert ch["hwm_close"] == 140 and ch["trail_normal"] == 113.0   # 140 - 27
    assert ch["trail_parabolic"] == round(141 * 0.92, 2)
    ch2 = level_map.chandelier(bars, n=9.0, since="2026-07-01")     # HWM 只看 7 月
    assert ch2["hwm_close"] == 118


def test_stop_sanity_bands():
    assert level_map.stop_sanity(100, 95, 10)["verdict"].startswith("too_tight")   # 0.5N
    assert level_map.stop_sanity(100, 88, 10)["verdict"] == "healthy(0.7-2N)"      # 1.2N
    assert level_map.stop_sanity(100, 70, 10)["verdict"].startswith("too_far")     # 3N
    assert level_map.stop_sanity(100, 105, 10)["verdict"] == "already_below"


def test_build_stops_flags_broken_chandelier():
    bars = [_bar("2026-06-01", 100, 102, 98, 100)] * 5 \
         + [_bar("2026-06-10", 138, 142, 136, 140)] \
         + [_bar(f"2026-07-{i:02d}", 108, 112, 104, 107) for i in range(1, 22)]
    s = level_map.build_stops(bars, entry_price=100, entry_date="2026-06-01", zone_stop=100)
    assert s["n_atr20"] and s["chandelier"]["hwm_close"] == 140
    assert s["chandelier_broken"] is True     # 107 < 140 - 3N
    # derived from unrounded N — allow rounding drift vs displayed n_atr20
    assert abs(s["turtle_2n_from_entry"] - (100 - 2 * s["n_atr20"])) <= 0.05
    assert s["structure_stop_sanity"]["verdict"] in (
        "healthy(0.7-2N)", "too_tight_noise(<0.7N)", "too_far(>2N)_shrink_size_or_nearer_structure")


# ── 真空 ≠ 无阻力: LVN 带内拒绝墙 (用户 提出, AAOI 140 实证) ──────

def _reject_series():
    """复现"真空由反复拒绝造成"的真实机制 (AAOI 140 形状)。

    峰之所以是峰, 靠的是**有 bar 收在两条线之间**: 收 137 的 bar 对 140 记拒绝、
    对 134 记 closes_above —— 没有这层, 所有低位线的计数会一样高 (低线被更多
    bar 路过), 扫出来是平台不是墙。
    """
    bars = []
    for i in range(20):                     # 旧高位段: 反复在 152 被拒 (计数最多, 时间最旧)
        bars.append(_bar(f"2026-06-{i % 28 + 1:02d}", 148, 152.5, 143, 145, to=1e9))
    for i in range(14):                     # 中段: 冲 141 收 137 → 只对 ~140 记拒绝
        bars.append(_bar(f"2026-07-{i % 28 + 1:02d}", 138, 141.0, 135, 137, to=1e9))
    for i in range(10):                     # 近段: 冲 131 收 126 (与中段留出一个窗口宽度)
        bars.append(_bar(f"2026-08-{i % 28 + 1:02d}", 128, 131.0, 124, 126, to=1e9))
    return bars


def test_lvn_rejection_wall_ladder_is_nearest_first_not_strongest_first():
    """强度排序会把"先撞上的那堵"挤出榜 —— 下跌趋势里越高越旧的线计数天然越多。
    阶梯的语义是"依次会撞上哪几堵", 所以按价位由近及远。"""
    bars = _reject_series()
    bands = [{"lo": 128.0, "hi": 160.0, "share_pct": 5.0, "void": False}]
    level_map.lvn_rejection_walls(bars, bands, bucket=4.0, spot=126.0)
    walls = bands[0]["rejection_walls"]

    assert walls, "带内有反复拒绝却报不出墙"
    levels = [w["level"] for w in walls]
    assert levels == sorted(levels), "阶梯必须按价位升序 (由近及远)"
    assert any(137 <= lv <= 142 for lv in levels), f"中段那堵 (~140) 被挤掉了: {levels}"
    strongest = max(walls, key=lambda w: w["n"])
    assert strongest is not walls[0], "强度最高的那堵不该排在最前 — 阶梯按距离不按名气"
    assert strongest["level"] == max(w["level"] for w in walls)   # 最强的恰是最远最旧那堵


def test_lvn_rejection_wall_needs_min_count_and_survives_no_spot():
    bars = _reject_series()
    quiet = [{"lo": 200.0, "hi": 230.0, "share_pct": 1.0, "void": True}]
    level_map.lvn_rejection_walls(bars, quiet, bucket=4.0, spot=126.0)
    assert quiet[0]["rejection_walls"] == []          # 从没碰过的高位 = 无墙

    bands = [{"lo": 128.0, "hi": 160.0, "share_pct": 5.0, "void": False}]
    level_map.lvn_rejection_walls(bars, bands, bucket=4.0)   # spot 缺省不炸
    assert bands[0]["rejection_walls"]


def test_build_map_attaches_rejection_walls_to_lvn_bands():
    """接线检查: build_map 必须把墙挂到 lvn_bands 上 —— 两个信号分居两处、
    从不交叉, 正是 2026-08-06 把 140 读成"畅通区"的原因。"""
    m = level_map.build_map(_reject_series(), profile_days=60)
    assert any("rejection_walls" in b for b in m["lvn_bands"])


def test_rejection_window_is_one_bucket_and_is_reported():
    """判定窗口 = 一个 profile 桶宽 (≈0.25N), 且必须**随输出播报口径**。

     由固定 1.5% 改来 (用户 裁决): 旧口径折算成各票自己的 ATR 跨度 11 倍
    (SPY 1.26N ↔ AAOI 0.11N), 同一个"拒绝"在不同票上意思完全不同, 而 gate 的证据
    强度必须跨票可比。ATR 有自反性 (波动放大→窗口变宽→计数变多), 所以口径不播报
    就没人能判断为什么这条成立那条不成立 —— 同  per-ticker 告警阈值的规矩。
    """
    bars = [_bar(f"2026-07-{d:02d}", 100, 108, 96, 99) for d in range(1, 25)]
    m = level_map.build_map(bars, levels=[110.0], bucket=4.0)

    assert m["near_window"] == 4.0                       # = bucket, 不是 level×1.5%
    assert m["levels"][0]["near"] == 4.0                 # 逐条价位都带出口径
    assert m["near_window_n"] is not None                # 折算成 N, 给人读


def test_absolute_near_overrides_the_legacy_percentage():
    """near 优先于 near_frac; 两者都不传才走旧口径 (向后兼容, 不是推荐用法)。"""
    bars = [_bar("2026-07-01", 100, 106, 96, 99)]        # high 106, close 99

    assert level_map.level_interactions(bars, 110.0, near=5.0)["n_rejections"] == 1
    assert level_map.level_interactions(bars, 110.0, near=2.0)["n_rejections"] == 0
    legacy = level_map.level_interactions(bars, 110.0)   # 110×1.5% = 1.65 → 够不到
    assert legacy["n_rejections"] == 0 and legacy["near"] == 1.65


def test_window_is_comparable_across_tickers_in_atr_terms():
    """跨票一致性 = 本次改动的目的: 同样的 ATR 形状 → 同样的 N 窗口, 与价格无关。
    旧口径下低价高波动票 (0.11N) 与高价低波动票 (1.26N) 差 11 倍。"""
    cheap = [_bar(f"2026-07-{d:02d}", 100, 104, 96, 100) for d in range(1, 25)]
    rich = [_bar(f"2026-07-{d:02d}", 1000, 1040, 960, 1000) for d in range(1, 25)]
    n_cheap = level_map.build_map(cheap, levels=[110.0])["near_window_n"]
    n_rich = level_map.build_map(rich, levels=[1100.0])["near_window_n"]

    assert abs(n_cheap - n_rich) < 0.02, f"同形状不同价位的窗口应等宽: {n_cheap} vs {n_rich}"


# ── intraday profile 通路 + pivot 滞后可见化 () ──────────

def test_profile_bars_override_only_changes_signal2():
    """intraday 只换 signal2 的输入; pivot/ATR/拒绝计数仍是**日线概念**, 不许被换掉。"""
    daily = [_bar(f"2026-07-{d:02d}", 100, 108, 96, 104) for d in range(1, 25)]
    intraday = [_bar(f"2026-07-{d:02d}", 100, 101, 99, 100) for d in range(1, 25)] * 7
    m = level_map.build_map(daily, levels=[110.0], bucket=4.0,
                            profile_bars=intraday, profile_source="60m")

    assert m["profile_source"] == "60m"
    assert m["profile_days"] == len(intraday)          # profile 用了 intraday
    assert m["atr14"] is not None                      # ATR 仍来自日线
    assert m["levels"][0]["near"] == 4.0               # 拒绝窗口仍是日线桶宽
    # profile 的价格范围来自 intraday (99-101), 不是日线的 96-108
    assert max(b["hi"] for b in m["profile"]) <= 104.0


def test_pending_pivots_surface_the_wing_lag():
    """swing_points 要求右侧 wing 根, 所以最近 wing 根永远不成 pivot ——
    事件后当日重画时刚创下的高低点对聚类不可见 (AAOI 8/5 的 140.34 实证)。
    不改判据 (放宽 wing 会引入噪音 pivot), 而是把它单列成"待确认"。"""
    bars = [_bar(f"2026-07-{d:02d}", 100, 104, 96, 100) for d in range(1, 21)]
    bars.append(_bar("2026-07-21", 100, 140, 99, 138))      # 最后一根创新高
    m = level_map.build_map(bars, bucket=4.0, wing=2)

    pending = {p["kind"]: p for p in m["pivot_pending"]}
    assert pending["high"]["price"] == 140.0
    assert pending["high"]["bars_needed"] > 0
    # 且它确实还没进 confirmed 簇
    assert all(c["hi"] < 140.0 for c in m["pivot_clusters_above"] + m["pivot_clusters_below"])


# ── [信号5] 趋势观察 (立项, FYI 观察层) ─────────────────────────

def _obs_bars():
    """200 根: 130 根@100 → 20 根滑落至 80 (含一根破位: 收 60d 最低且 <MA60)
    → 50 根修复回 90。破位锚由 core.episodes 判, 与 gate_path 同一份定义。"""
    bars = [_bar(f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}", 100, 101, 99, 100.0)
            for i in range(130)]
    px = 100.0
    for i in range(20):
        px -= 1.0
        bars.append(_bar(f"2026-06-{1 + i:02d}", px + 1, px + 1, px - 0.5, px))
    for i in range(50):
        px += 0.2                             # 缓慢修复: 最后 20 根仍在爬 (chg20 > 0)
        bars.append(_bar(f"2026-{7 + i // 28:02d}-{1 + i % 28:02d}", px, px + 0.5, px - 0.5, px))
    bars[-30]["turnover"] = 1e12          # 60d 窗内最大量日 ≠ 破位日
    return bars


def test_build_trend_obs_anchors_and_rs():
    bars = _obs_bars()
    bench = [dict(b, close=100.0) for b in bars]     # 基准恒定 → RS 形状 = 自身价格
    obs = level_map.build_trend_obs(bars, bench, "QQQ")
    labels = {a["label"] for a in obs["anchors"]}
    assert "破位日" in labels                        # episodes_of 找到了那次滑落
    assert "60d最大量日" in labels
    for a in obs["anchors"]:
        assert a["avwap"] > 0 and a["date"].startswith("2026-")
    rs = obs["rs"]
    assert rs and rs["bench"] == "QQQ"
    assert rs["chg20_pct"] > 0                       # 修复段 20d RS 走高
    # 60d 窗 (=最近 60 根) 已不含破位前的 100 平台, 单调修复段的当前值就是窗内新高
    assert rs["new_60d_high"] is True
    assert obs.get("rs_warn") is None


def test_build_trend_obs_degrades_loudly_without_bench():
    obs = level_map.build_trend_obs(_obs_bars(), [], "QQQ")
    assert obs["rs"] is None
    assert "RS 跳过" in obs["rs_warn"]               # 降级必须说出口, 不静默
