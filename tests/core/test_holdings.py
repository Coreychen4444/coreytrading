"""谁算持仓 —— status 不参与判定。

两个方向都出过事, 所以两个方向都要钉住:
  qty>0  + status="closed"  INTC  实际持有 2 股被静音一整天
  qty==0 + status="active"  NBIS  平仓后当上次日简报的头号异动
"""
from quant.core.holdings import (
    cards_missing_archetype, coverage_universe, flat_with_lots, held_bare, held_incl_option_underlyings, intel_universe, is_candidate,
    is_scannable_card,
    live_tickers, lot_gap, lot_qty, underlying_cards_missing_zones, watchlist_candidates)


def _doc(**tickers):
    return {"tickers": tickers}


def _t(qty=0.0, status="active", synthetic=False, lots=None, zones=..., role=None,
       linked=None):
    body = {"status": status, "broker": {"qty": qty}}
    if synthetic:
        body["broker"]["synthetic"] = True
    if lots is not None:
        body["lots"] = lots
    if zones is not ...:
        body.setdefault("agent", {})["zones"] = zones
    if linked is not None:
        body.setdefault("agent", {})["linked_underlying"] = linked
    if role is not None:
        body["instrument"] = {"role": role}
    return body


def test_qty_beats_a_stale_closed_status():
    assert live_tickers(_doc(INTC=_t(qty=2.0, status="closed"))) == {"INTC"}


def test_qty_beats_an_unmaintained_active_status():
    assert live_tickers(_doc(NBIS=_t(qty=0.0, status="active"))) == set()


def test_synthetic_row_lives_only_while_its_option_is_held():
    """stock_fetch 给在持期权的标的补一条 qty=0 行情行, 它会活得比期权久 ——
    六条 (NOK/AAOI/MRVL/DRAM/RKLB/AXTI) 至今挂着 synthetic=true 而期权早平了。"""
    dead = _doc(MRVL=_t(synthetic=True), MRVL270115C150000=_t(qty=0.0))
    assert live_tickers(dead) == set()

    alive = _doc(MRVL=_t(synthetic=True), MRVL270115C150000=_t(qty=1.0))
    assert live_tickers(alive) == {"MRVL", "MRVL270115C150000"}


def test_synthetic_row_of_an_unrelated_underlying_is_not_revived():
    doc = _doc(MRVL=_t(synthetic=True), NVDA280121C160000=_t(qty=1.0))
    assert live_tickers(doc) == {"NVDA280121C160000"}


def test_lot_qty_sums_both_tiers():
    body = _t(lots={"base": [{"qty": 2}], "position": [{"qty": 1}, {"qty": 3.5}]})
    assert lot_qty(body) == 6.5
    assert lot_qty(_t(lots={"base": [], "position": []})) == 0
    assert lot_qty(_t()) == 0


def test_lot_gap_has_a_direction_and_it_means_two_different_things():
    """broker − lots。**方向有语义**, 授权侧只有一边是 fail-open:

      >0  买入未落账 —— 敞口真实存在但账上看不见 → "本轮已投"低估 → 给全额
      <0  部分卖出未分配 —— 账本多记 → 已投高估 → cap 偏小 = 保守

    今天两个消费者: portfolio_brief.lot_drift (两个方向都报) 与
    today_group_sync._admit (只拦 >0)。抽出来之前那个减法手写在 brief 里,
    而授权侧另用一套 fill 日期判据 —— 对平仓卡得出"已成交待落账"的错误结论
    (AAOI)。同一个概念两份判别式是本仓惯犯。
    """
    assert lot_gap(_t(qty=4, lots={"base": [], "position": [{"qty": 4}]})) == 0
    assert lot_gap(_t(qty=4, lots={"base": [], "position": []})) == 4       # 未落账
    assert lot_gap(_t(qty=1, lots={"base": [], "position": [{"qty": 3}]})) == -2  # 未分配
    assert lot_gap(_t(qty=2, lots={"base": [{"qty": 2}], "position": []})) == 0   # base 也算
    # 平仓卡: lots 已归档进 exit_info → 0 股对 0 lot, 台账是**对得上**的
    assert lot_gap(_t(qty=0, status="closed", lots={"base": [], "position": []})) == 0
    # synthetic 行没有自己的 lots, 恒 0 (否则期权 underlying 行天天报漂移)
    assert lot_gap(_t(qty=100, synthetic=True, lots={"base": [], "position": []})) == 0


def test_flat_with_lots_finds_unfinished_close_cleanup():
    doc = _doc(
        NBIS=_t(qty=0.0, status="active", lots={"base": [], "position": [{"qty": 1}]}),
        MU=_t(qty=1.0, lots={"base": [], "position": [{"qty": 1}]}),
        NOK=_t(qty=0.0, status="closed", lots={"base": [], "position": []}),
)
    assert flat_with_lots(doc) == ["NBIS"]


# ── 三个"持仓"判别式的差异 (GOOGL 卡被清空 6 天) ─────────────
#
# 病根不是某一个判别式写错了, 是其中两个被**手抄在两个 sync 文件里**、
# 谁都没写明它们不同:
#   watchlist_sync    held_bare                    → GOOGL 非持仓 → 建空 stub 卡
#   watchlist_migrate held_incl_option_underlyings → GOOGL 是持仓 → 促升覆盖真卡
# 两侧单独读都自洽。所以这里钉的是**差异本身**, 不是某一侧的值 ——
# 下一个人"顺手对齐"两边时必须先弄红这几条。

def test_the_three_held_predicates_are_deliberately_different():
    """GOOGL LEAPS 在持, GOOGL 正股是 qty=0 的 synthetic 行 ( 实况)。"""
    doc = _doc(GOOGL=_t(synthetic=True), GOOGL270115C390000=_t(qty=1.0))

    assert held_bare(doc) == {"GOOGL270115C390000"}                # 裸: 正股不算
    assert live_tickers(doc) == {"GOOGL270115C390000", "GOOGL"}    # + 存在的 synthetic 行
    assert held_incl_option_underlyings(doc) == {"GOOGL270115C390000", "GOOGL"}


def test_underlying_name_counts_even_without_a_row():
    """held_incl_option_underlyings 收的是 underlying **名字**;
    live_tickers 要求 doc 里真有那一行。这是后两者唯一的分歧点。"""
    doc = _doc(GOOGL270115C390000=_t(qty=1.0))                     # 没有 GOOGL 行

    assert live_tickers(doc) == {"GOOGL270115C390000"}
    assert held_incl_option_underlyings(doc) == {"GOOGL270115C390000", "GOOGL"}


def test_held_predicate_chain_is_an_invariant():
    """held_bare ⊆ live_tickers ⊆ held_incl_option_underlyings —— 恒等式。

    三者可以各有语义, 但顺序不能乱: 谁要是被改成"取或""取交"这条就断。"""
    docs = [
        _doc(GOOGL=_t(synthetic=True), GOOGL270115C390000=_t(qty=1.0)),
        _doc(GOOGL270115C390000=_t(qty=1.0)),
        _doc(MRVL=_t(synthetic=True), MRVL270115C150000=_t(qty=0.0)),   # 期权已平
        _doc(INTC=_t(qty=2.0, status="closed")),
        _doc(),
    ]
    for doc in docs:
        assert held_bare(doc) <= live_tickers(doc) <= held_incl_option_underlyings(doc)


# ── 期权 underlying 的正股卡不许是空的 ───────────────────────────────

def test_missing_underlying_zones_is_visible_although_qty_is_zero():
    """GOOGL : 正股卡 zones 被 stub 清空, 而它 qty==0 (synthetic 行)
    → 进不了 load_rows_from_state → 简报 6 天一声没响。
    **不是"检查不会触发", 是"被检查的对象进不了数据链"**—— 判据必须直扫 doc。"""
    doc = _doc(
        GOOGL=_t(synthetic=True, zones=None),
        GOOGL270115C390000=_t(qty=1.0, zones={"stop_level": 351.0,
                                              "stop_level_basis": "underlying"}),
)
    assert underlying_cards_missing_zones(doc) == ["GOOGL"]


def test_underlying_with_zones_is_silent():
    doc = _doc(
        GOOGL=_t(synthetic=True, zones={"accumulation_zone_1": [341.0, 347.2]}),
        GOOGL270115C390000=_t(qty=1.0),
)
    assert underlying_cards_missing_zones(doc) == []


def test_underlying_row_missing_entirely_is_reported():
    """连 synthetic 行都没有 = 更彻底的同一个洞。"""
    assert underlying_cards_missing_zones(_doc(GOOGL270115C390000=_t(qty=1.0))) == ["GOOGL"]


def test_linked_underlying_beats_the_occ_parse():
    """卡片显式声明的 linked_underlying 优先 (GOOG→GOOGL 迁移那类改名场景)。"""
    doc = _doc(
        GOOGL=_t(synthetic=True, zones={"stop_level": 351.0}),
        GOOG=_t(synthetic=True, zones=None),
        GOOG270115C390000=_t(qty=1.0, linked="GOOGL"),
)
    assert underlying_cards_missing_zones(doc) == []


def test_closed_option_does_not_demand_a_card():
    doc = _doc(GOOGL=_t(synthetic=True, zones=None), GOOGL270115C390000=_t(qty=0.0))
    assert underlying_cards_missing_zones(doc) == []


def test_unmanaged_option_stays_out():
    """结构豁免层整个不在管辖内 (position-tiers.md: 不进任何 task 输出)。"""
    doc = _doc(
        GOOGL=_t(synthetic=True, zones=None),
        GOOGL270115C390000=_t(qty=1.0, role="commemorative_hold"),
)
    assert underlying_cards_missing_zones(doc) == []


# ── 谁算候选: 读分组 membership, 不读 status ────────────────────────────────

def _wl(status="watching", **over):
    body = {"status": status, "agent": {"zones": None}}
    body.update(over)
    return body


def test_candidate_predicate_reads_group_membership_not_status():
    """移出券商侧候选分组只翻 moomoo_listed, 卡的 status 仍是 watching —— 一张退出
    候选很久的墓碑卡照样被只读 status 的消费者列出来, 就是这个形状。"""
    assert is_candidate(_wl(moomoo_listed=True))
    assert is_candidate(_wl())                                  # 没这个字段 = 从没被移出过
    assert not is_candidate(_wl(moomoo_listed=False))           # 墓碑
    assert not is_candidate(_wl(status="passed", moomoo_listed=True))
    assert not is_candidate({}) and not is_candidate(None)


def test_watchlist_candidates_is_the_doc_level_of_the_same_predicate():
    doc = _doc(GONE=_wl(moomoo_listed=False), CAND=_wl(moomoo_listed=True), FRESH=_wl(),
               OLD=_wl(status="passed"))
    assert watchlist_candidates(doc) == {"CAND", "FRESH"}


# ── 情报层动态范围: 消费者只抄它, 提示词里不再有第二份判别式 ─────────────────

POS_LIVE = _doc(
    KEEP=_t(qty=1.0, role="commemorative_hold"),                 # 结构豁免层: 不进任何 task 输出
    BBB=_t(qty=20.0),
    AAA=_t(synthetic=True),                                      # 在持期权的 underlying 行
    AAA270115C001000=_t(qty=1.0),
    FLATA=_t(qty=0.0, status="active"),                          # 期权已平, status 没人改
    FLATB=_t(qty=0.0, status="active"),
    ARCH=_t(qty=0.0, status="closed"),                           # 平仓归档: 常态, 不列
)
WL_LIVE = _doc(
    GONEA=_wl(moomoo_listed=False), GONEB=_wl(moomoo_listed=False),
    CAND=_wl(moomoo_listed=True), FLATA=_wl(moomoo_listed=True), FLATB=_wl(moomoo_listed=True),
    AAA=_wl(moomoo_listed=True), FRESH=_wl(),
    REF=_wl(moomoo_listed=True, instrument={"role": "volume_reference"}),
)


def test_intel_universe_active_is_qty_based_with_options_mapped_to_underlying():
    """真持仓只有 BBB 与一张 AAA call; 结构豁免层不进, qty=0 的残卡不是持仓 ——
    读 status 会把这三类一起当成覆盖范围。"""
    u = intel_universe(POS_LIVE, WL_LIVE)
    assert u["active"] == ["AAA", "BBB"]
    assert "KEEP" in u["excluded"]["positions"]
    assert "commemorative_hold" in u["excluded"]["positions"]["KEEP"]
    assert set(u["excluded"]["positions"]) == {"KEEP", "FLATA", "FLATB"}  # ARCH (closed) 不列
    assert "qty=0" in u["excluded"]["positions"]["FLATA"]


def test_intel_universe_watchlist_drops_tombstones_and_dedupes_active():
    u = intel_universe(POS_LIVE, WL_LIVE)
    assert u["watchlist"] == ["CAND", "FLATA", "FLATB", "FRESH"]
    assert "GONEA" not in u["watchlist"] and "GONEB" not in u["watchlist"]
    assert "moomoo_listed=false" in u["excluded"]["watchlist"]["GONEA"]
    assert "AAA" in u["excluded"]["watchlist"]                   # 已在 active, 不重复
    assert "volume_reference" in u["excluded"]["watchlist"]["REF"]


def test_intel_universe_honours_linked_underlying_and_empty_docs():
    doc = _doc(XYZ270115C001000=_t(qty=1.0, linked="XYZW"))
    assert intel_universe(doc, _doc())["active"] == ["XYZW"]
    assert intel_universe({}, {}) == {"active": [], "watchlist": [],
                                      "excluded": {"positions": {}, "watchlist": {}}}


def test_intel_universe_carries_no_trading_authority():
    """输出只有名字: 没有价格、仓位、cap —— 情报层零授权效力。"""
    u = intel_universe(POS_LIVE, WL_LIVE)
    flat = str(u)
    for forbidden in ("cap", "qty", "price", "stop", "zone"):
        assert forbidden not in flat.replace("qty=0", "").replace("broker.qty", ""), forbidden


def test_coverage_universe_is_active_plus_candidates_flat_and_sorted():
    """「全量扫一遍」型任务 (估值一览) 曾按 inputs.json 全表跑, 把墓碑卡与纪念仓一起列了 —— 范围 = 持仓 ∪ 候选, 从这里抄, 不由各消费端自己解读 state。"""
    u = intel_universe(POS_LIVE, WL_LIVE)
    cov = coverage_universe(POS_LIVE, WL_LIVE)
    assert cov == sorted(set(u["active"]) | set(u["watchlist"]))
    assert {"AAA", "BBB", "CAND", "FRESH"} <= set(cov)
    assert {"FLATA", "FLATB"} <= set(cov)          # qty=0 但仍在自选分组 = 候选, 不是幽灵
    for ghost in ("KEEP", "GONEA", "GONEB", "REF", "ARCH"):
        assert ghost not in cov, ghost
    assert coverage_universe({}, {}) == []


def test_scannable_card_drops_watchlist_tombstones_and_keeps_position_semantics():
    """INFRA 卡片体检点名了已移出分组的墓碑 (status 仍 watching)。"""
    assert is_scannable_card({"status": "watching"})
    assert not is_scannable_card({"status": "watching", "moomoo_listed": False})
    assert not is_scannable_card({"status": "passed", "moomoo_listed": True})     # 非候选
    assert is_scannable_card({"status": "active"}) and is_scannable_card({})
    assert not is_scannable_card({"status": "closed"})


def test_cards_missing_archetype_flags_painted_cards_only():
    """新候选卡画完一周, 估值人工区没有它们。空 stub / 墓碑 / 纪念仓 / 期权都不进。"""
    zones = {"agent": {"zones": {"accumulation_zone_1": [1.0, 2.0]}}}
    pos = _doc(BBB={**_t(qty=20.0), **zones},
               KEEP={**_t(qty=1.0, role="commemorative_hold"), **zones},
               AAA270319C220000=_t(qty=1.0),
               AAA={**_t(synthetic=True), **zones},
               FLATA={**_t(qty=0.0, status="active"), **zones})
    wl = _doc(CAND={**_wl(moomoo_listed=True), **zones}, FRESH={**_wl(), **zones},
              GONEA={**_wl(moomoo_listed=False), **zones},
              STUB=_wl(),                                                  # 空 stub → autopaint 会一并归
              REF={**_wl(instrument={"role": "volume_reference"}), **zones})
    inputs = {"tickers": {"BBB": {"archetype": "ai_infra_capex"}, "aaa": {}}}
    assert cards_missing_archetype(pos, wl, inputs) == ["CAND", "FRESH"]
    assert cards_missing_archetype(pos, wl, {}) == ["AAA", "BBB", "CAND", "FRESH"]
    assert cards_missing_archetype({}, {}, {}) == []
