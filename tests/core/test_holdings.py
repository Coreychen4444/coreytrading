"""谁算持仓 —— status 不参与判定。

两个方向都出过事, 所以两个方向都要钉住:
  qty>0  + status="closed"  INTC  实际持有 2 股被静音一整天
  qty==0 + status="active"  NBIS  平仓后当上次日简报的头号异动
"""
from quant.core.holdings import (
    flat_with_lots, held_bare, held_incl_option_underlyings, live_tickers, lot_gap,
    lot_qty, underlying_cards_missing_zones)


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
    """GOOGL LEAPS 在持, GOOGL 正股是 qty=0 的 synthetic 行 (2026-08 实况)。"""
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
    """GOOGL 2026-08-06: 正股卡 zones 被 stub 清空, 而它 qty==0 (synthetic 行)
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
