"""anchor_basis 收据校验 —— 卡片里引的数字必须和当下的 level_map 对得上。

 用户:「随便一指就发现了你的 gate 错误, 那让我怎么相信其他是对的呢」。
`gate_audit` 查的六条全是内部自洽, 一张锚错的卡照样全绿。本模块堵的是收据:
A 引了不存在的簇 / B 证据已衰减。**堵不住「引的是真的但选错了那一个」**——
那是判断, 归①族与人眼。

🚨 本文件里最重要的是**假阳性测试**: 首版无 spot 过滤 + 无 pivot 自证, 30 条
findings 里七成是垃圾 (`level 14 拒x8` 来自 ATR14; `740-760 x8` 是 VP 带)。
假阳性会训练人无视告警 —— 比没有这条检查更坏 (memory data_discipline 形状#17
的同族)。那几条钉在下面。
"""
from quant.report import anchor_receipts as ar


# ── 引用解析: 该抓的抓到 ─────────────────────────────────────────────

def test_cluster_citation_with_pivot_prefix():
    t = "601 = pivot 592.05-601.00 x6 [high+low] last 2026-08-05 的簇上沿"
    assert ar.cited_clusters(t) == [{"lo": 592.05, "hi": 601.0, "touches": 6}]


def test_cluster_citation_with_bracket_suffix():
    t = "372.08-376.00 x4 [high] last 2026-07-16 的上沿"
    assert ar.cited_clusters(t) == [{"lo": 372.08, "hi": 376.0, "touches": 4}]


def test_rejection_citation_needs_a_plausible_level():
    t = "339.3 拒x17 且当前连续站上 0 日"
    assert ar.cited_rejections(t, spot=328.57) == [{"level": 339.3, "count": 17}]


# ── 🚨 假阳性: 首版实测报出来的垃圾, 一条都不许再出现 ────────────────

def test_atr_and_percentages_are_not_levels():
    """`ATR14 ... 拒x8` 首版报成 `level 14 拒绝计数已衰减` —— 纯垃圾。"""
    t = "spot 134.33, ATR14 16.9 (12.6%); 头顶 152.1 拒x8"
    got = ar.cited_rejections(t, spot=134.33)
    assert got == [{"level": 152.1, "count": 8}]


def test_volume_profile_band_is_not_a_pivot_cluster():
    """`VP 740-760 8%` 不是簇引用。首版把它当簇报「算不出」。"""
    assert ar.cited_clusters("VP 740-760 8% 的节点上沿") == []


def test_zone_range_is_not_a_pivot_cluster():
    assert ar.cited_clusters("z1 95.5-98.7 x7") == []


def test_rejection_without_spot_context_is_unfiltered_by_design():
    """不给 spot 就没有价位可信区间 —— 调用方必须给 (audit_ticker 会给)。"""
    assert ar.cited_rejections("152.1 拒x8", spot=None)


# ── A: 引用物不存在 ─────────────────────────────────────────────────

LIVE = [{"lo": 106.85, "hi": 106.85, "center": 106.85, "touches": 1,
         "kinds": "high", "last_touch": "2026-07-22"}]


def test_missing_cluster_is_reported_with_the_nearest_live_one():
    """INTC  源案例: gate 109.6 锚在 pivot 106.85-109.61 x2 上,
    该簇现在只剩 x1 —— 支撑那条 gate 的证据已经不在了。"""
    (hit,) = ar.check_clusters([{"lo": 106.85, "hi": 109.61, "touches": 2}],
                               LIVE, tol=1.9)
    assert hit.startswith("A") and "106.85-106.85 ×1" in hit


# ── B: 证据衰减 ────────────────────────────────────────────────────

def test_decayed_touch_count():
    live = [{"lo": 250.52, "hi": 251.64, "center": 251.0, "touches": 2,
             "kinds": "high+low", "last_touch": "2026-07-09"}]
    (hit,) = ar.check_clusters([{"lo": 250.5, "hi": 251.6, "touches": 3}],
                               live, tol=1.0)
    assert hit.startswith("B") and "×3 → 现 ×2" in hit


def test_a_grown_or_merged_cluster_is_not_a_failure():
    """🚨 COHR 2026-08-11: 引 304.06-309.19 ×3, 现在是 291-309.19 ×5 ——
    边界变了但那堆 pivot 一个没少, 是**增强**不是消失。报它就是误报。"""
    live = [{"lo": 291.0, "hi": 309.19, "center": 300.0, "touches": 5,
             "kinds": "high+low", "last_touch": "2026-08-06"}]
    assert ar.check_clusters([{"lo": 304.06, "hi": 309.19, "touches": 3}],
                             live, tol=8.7) == []


def test_decayed_rejection_count():
    def iv(_):
        return {"n_rejections": 7, "recent_rejections": [{"date": "2026-07-29"}]}
    (hit,) = ar.check_rejections([{"level": 90.0, "count": 10}], iv, tol=1.0)
    assert "引 ×10 → 现 ×7" in hit and "2026-07-29" in hit


def test_growing_rejection_count_is_fine():
    """卡片自己的约定是「证据只增不减」—— 增了不报。"""
    def iv(_):
        return {"n_rejections": 19, "recent_rejections": []}
    assert ar.check_rejections([{"level": 392.0, "count": 17}], iv, tol=1.0) == []


def test_uncited_level_is_skipped():
    assert ar.check_rejections([{"level": 1.0, "count": 3}], lambda _: None, tol=1.0) == []


# ── 装配 ───────────────────────────────────────────────────────────

def test_audit_ticker_wires_basis_to_the_live_map():
    agent = {
        "zones": {"anchored_on": {"date": "2026-08-11",
                                  "basis": "pivot 106.85-109.61 x2 是 lsh"}},
        "next_regime": {"entry_gate": {"level": 109.6}},
    }
    m = {"near_window": 1.9, "pivot_clusters_below": LIVE,
         "pivot_clusters_above": [], "levels": []}
    (hit,) = ar.audit_ticker("INTC", agent, map_of=lambda t, lv: m,
                             spot_of=lambda t: 97.71)
    assert hit.startswith("[zones.anchored_on.basis] A")


def test_no_map_means_skip_not_fail():
    """拉不到 bars ≠ 不合规 (缺数据不判, 形状#11)。"""
    agent = {"zones": {"anchored_on": {"basis": "pivot 1-2 x3"}}}
    assert ar.audit_ticker("X", agent, map_of=lambda t, lv: None) == []


def test_card_without_basis_is_skipped():
    assert ar.audit_ticker("X", {"zones": {}}, map_of=lambda t, lv: {}) == []


def test_bar_counts_are_not_price_levels():
    """🚨 AAOI : 「90 根日线内拒 ×10」被读成 level 90 报假衰减。

    spot 133.93 让 [0.5x, 2x] 区间过滤没拦住 —— 带单位后缀的数字一律不是价位。
    """
    t = "120.5 = flip 位 —— 90 根日线内拒 ×10 (判定窗口 ±$3.9 = 0.25N)"
    assert ar.cited_rejections(t, spot=133.93) == []


def test_other_unit_suffixes_are_rejected_too():
    for unit in ("日", "个", "次", "%", "轮"):
        assert ar.cited_rejections(f"200{unit}内 189 之上 拒×6", spot=213.0) == \
            [{"level": 189.0, "count": 6}]
