"""转移装上来的 zones 缺 anchored_on —— 锚被 fallback 凭空补新鲜度。

`apply_transition` 的上行分支是 `agent["zones"] = dict(nr.get("zones") or {})`,
**整体替换**; 而 `next_regime.zones` 是画卡时预先声明的价位块, 里面从来没有
`anchored_on` (它不是一次画卡, 是一份对下一个状态的预案)。转移一发生:

    zones.anchored_on → 消失
    anchor_date()     → 退到 thesis.last_reviewed = 最近碰过卡的时间
    ⇒ 以锚为判据的检查 (财报重画 SLA / gate 同锚层) 全部当它是刚画的

比"盖错锚"更隐蔽: 盖锚至少有一步写了那个日期, 这里没有任何一步写过。而它专挑
"刚发生结构大事所以刚转移"的时刻发作 —— 正是最需要重画的那一刻。

源案例  (用户 抓): NBIS  BMO 跳空 +28%, 当晚快车道转移在即;
模拟转移后 anchored_on=None / anchor_date()→, 而那套 zones 是 8/01 建、
8/06 按**跳空前**分布重推的。
"""
from quant.core.structure import anchor_date, cards_missing_zone_anchor


def _card(*, source="auto", anchor=None, zones=True, last_reviewed="2026-08-12T20:00:00Z",
          status="active", itype="stock", regime="C_trend_repair_231", since="2026-08-12"):
    z = {"stop_level": 218.46, "accumulation_zone_1": [221.0, 231.0]} if zones else None
    if z is not None and anchor:
        z["anchored_on"] = {"date": anchor, "basis": "..."}
    agent = {"zones": z, "thesis": {"last_reviewed": last_reviewed}}
    if source is not None:
        agent["current_regime"] = {"name": regime, "source": source,
                                   "since": since, "cap_pct_nav": 0.4}
    return {"status": status, "instrument": {"type": itype}, "agent": agent}


def _doc(**t):
    return {"tickers": t}


# ── 该被点名的 ──────────────────────────────────────────────────────

def test_machine_installed_zones_without_anchor_are_named():
    (row,) = cards_missing_zone_anchor(_doc(NBIS=_card(source="auto")))
    assert row.startswith("NBIS")
    assert "C_trend_repair_231" in row and "source=auto" in row
    # 必须把 fallback 顶上来的那个假日期显示出来 —— 这是最有说服力的证据
    assert "2026-08-12" in row and "没有任何一步写过这个日期" in row


def test_demoted_source_counts_too():
    """降级同样是转移机整体替换 zones 的路径。"""
    assert len(cards_missing_zone_anchor(
        _doc(X=_card(source="demoted", regime="demoted_at_2026-08-12")))) == 1


def test_scans_both_docs_and_sorts():
    rows = cards_missing_zone_anchor(_doc(NBIS=_card(), MU=_card()),
                                     _doc(LITE=_card()))
    assert [r.split()[0] for r in rows] == ["MU", "NBIS", "LITE"]


# ── 该安静的 ────────────────────────────────────────────────────────

def test_card_with_an_explicit_anchor_is_quiet():
    assert cards_missing_zone_anchor(_doc(NBIS=_card(anchor="2026-08-12"))) == []


def test_card_source_regime_is_exempt():
    """card 源 = 人画的; 2026-07-29 前的卡全都没锚, zone-maintenance 明定提示级。
    它们的 last_reviewed 本来就是自己那次画卡, 不存在"凭空变新鲜"。"""
    assert cards_missing_zone_anchor(_doc(GOOGL=_card(source="card"))) == []


def test_regime_source_absent_is_exempt():
    assert cards_missing_zone_anchor(_doc(OLD=_card(source=None))) == []


def test_option_and_closed_and_zoneless_are_exempt():
    assert cards_missing_zone_anchor(_doc(
        GOOGL270115C390000=_card(itype="option"))) == []
    assert cards_missing_zone_anchor(_doc(NOK=_card(status="closed"))) == []
    assert cards_missing_zone_anchor(_doc(SOXX=_card(zones=False))) == []


# ── 出口是告警, 不是补锚 ─────────────────────────────────────────────

def test_the_fallback_itself_is_left_alone():
    """`anchor_date` 的 fallback **不改**—— 老卡靠它才有锚可读。
    要修的是"没人知道它在兜底", 不是兜底本身。"""
    agent = _card(source="auto")["agent"]
    assert agent["zones"].get("anchored_on") is None
    assert anchor_date(agent) == "2026-08-12"          # 行为原样保留
    assert cards_missing_zone_anchor(_doc(NBIS=_card(source="auto")))  # 但会被点名
