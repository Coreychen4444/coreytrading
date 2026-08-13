"""core/earnings —— 财报反应日缩仓 (earnings_window 路 A)。

三件事被钉住:
  1. 缩仓只在**反应日当天**生效 (T+0 单日, 不是"财报前一周")
  2. 缩仓比例来自 X_earn, 只收紧永不放大 (NVDA 类低波动票 → 只剩 m=0.7)
  3. 日历缺失/过期 **必须说出口**—— 静默不缩仓正是这条规则最可能的失效形态
"""
import json

import pytest

from quant.core import earnings as ea
from quant.core.sizing import SHOCK_X, available_cap

TODAY = "2026-08-12"


def _cal(as_of=TODAY, **tickers):
    return {"as_of": as_of, "tickers": tickers}


def test_shrink_ratio_only_tightens():
    """X_earn ≤ X_normal (NVDA 2.29N 之类) → 比例封顶 1.0, 只剩事件系数 0.7。"""
    assert ea.shrink_ratio(1.0) == pytest.approx(ea.EVENT_MULT)
    assert ea.shrink_ratio(SHOCK_X) == pytest.approx(ea.EVENT_MULT)
    # AAOI 7.19N: 1.88/7.19 × 0.7 ≈ 0.183 —— 40% 名义仓在那天等效 7.3%
    assert ea.shrink_ratio(7.193) == pytest.approx(SHOCK_X / 7.193 * ea.EVENT_MULT)
    assert ea.shrink_ratio(None) == pytest.approx(SHOCK_X / ea.POOL_X_EARN * ea.EVENT_MULT)


def test_only_the_reaction_day_is_shrunk():
    """T+0 单日。财报前一周缩仓在数据上找不到依据 (design §1), 不做。"""
    cal = _cal(AAOI={"next_reaction_day": "2026-08-12", "x_earn": 7.193, "n_prior": 19})
    on = ea.event_state("AAOI", "2026-08-12", cal)
    assert on["active"] is True and on["shrink"] < 0.2
    for other in ("2026-08-11", "2026-08-13"):
        off = ea.event_state("AAOI", other, cal)
        assert off["active"] is False and off["shrink"] == 1.0


def test_unknown_ticker_is_not_shrunk():
    st = ea.event_state("ZZZZ", TODAY, _cal(AAOI={"next_reaction_day": TODAY}))
    assert st["active"] is False and st["shrink"] == 1.0


def test_missing_calendar_says_so_instead_of_silently_not_shrinking():
    """**最危险的失效形态**: 日历读不到 → 什么都不缩, 且没人知道。

    40% 名义上限在财报日是 13–21% NAV 一天 (shock_budget §2.3b), 比 15% 的组合
    总预算还大。"没缩" 必须是一条看得见的告警, 不是一个安静的 no-op。
    """
    st = ea.event_state("AAOI", TODAY, {})
    assert st["active"] is False
    assert "财报日历缺失" in st["note"] and "未做财报缩仓" in st["note"]
    assert "财报日历缺失" in ea.event_line("AAOI", st)


def test_stale_calendar_is_flagged_but_still_used():
    cal = _cal(as_of="2026-07-01", AAOI={"next_reaction_day": TODAY, "x_earn": 7.193,
                                         "n_prior": 19})
    st = ea.event_state("AAOI", TODAY, cal)
    assert st["active"] is True                  # 旧日历也照用 (缩了比不缩强)
    assert "已 42 天" in st["note"]              # 但必须报出来: 反应日可能改期


def test_upcoming_day_is_announced_before_it_bites():
    cal = _cal(NVDA={"next_reaction_day": "2026-08-27", "x_earn": 2.219, "n_prior": 19})
    line = ea.event_line("NVDA", ea.event_state("NVDA", TODAY, cal))
    assert "下一个财报反应日 2026-08-27" in line


def test_shrink_composes_with_available_cap_and_never_widens():
    """接进单一真源: 反应日的分类 cap 被乘上缩仓比例, 其余三条不变。"""
    nav, cal = 21237.0, _cal(AAOI={"next_reaction_day": TODAY, "x_earn": 7.193,
                                   "n_prior": 19})
    sh = ea.event_state("AAOI", TODAY, cal)["shrink"]
    plain = available_cap(nav, 0.40, atr=3.0, price=100.0)
    shrunk = available_cap(nav, 0.40, atr=3.0, price=100.0, event_shrink=sh)
    assert shrunk["regime_cap"] == pytest.approx(plain["regime_cap"] * sh)
    assert shrunk["event_shrink"] == sh
    # 放大系数被夹住 —— 传 >1 也不许把 cap 撑大
    assert available_cap(nav, 0.40, 3.0, 100.0, event_shrink=2.0)["regime_cap"] \
        == pytest.approx(plain["regime_cap"])


# ── 重画 SLA: print 落地 vs 卡锚 (用户 抓) ──────────────
#
# 这一节钉的是一条**此前不存在**的判别式。日历天天在算缩仓 (它知道 print 哪天),
# 卡片自己也写着「print 落地当日全重画」—— 但两者之间没有代码, 只有 prose。
# LITE 8/11 AMC 印完, 卡锚停在 8/06, 四个交易日零告警。

def _card(anchor=None, zones=True, **extra):
    agent = {"zones": ({"anchored_on": {"date": anchor}} if anchor else {"x": 1})
             if zones else None}
    return {"status": "watching", "agent": agent, **extra}


def _doc(**tickers):
    return {"tickers": tickers}


def test_landed_print_prefers_the_newer_of_the_two_sources():
    """前视条目与粘住的存根取较新 —— 单向只能变新。"""
    row = {"next_reaction_day": "2026-08-12", "pub_day": "2026-08-11", "pub_type": 2,
           "last_print": {"reaction_day": "2026-05-06", "pub_day": "2026-05-05"}}
    assert ea.landed_print(row, TODAY)["reaction_day"] == "2026-08-12"
    # 反应日还没到 → 前视那半不算落地, 只剩旧存根
    assert ea.landed_print(row, "2026-08-11")["reaction_day"] == "2026-05-06"
    assert ea.landed_print({}, TODAY) is None


def test_landed_print_survives_the_forward_entry_rolling_away():
    """**这条是本节的心脏**: 反应日次日, 前视条目滚成下一季 (pick_upcoming 的
    `rday < today` 剔除), 只剩 `last_print` —— 没有它, 告警只活一天。"""
    rolled = {"next_reaction_day": "2026-11-04", "pub_day": "2026-11-03", "pub_type": 2,
              "last_print": {"reaction_day": "2026-08-12", "pub_day": "2026-08-11",
                             "pub_type": 2}}
    pr = ea.landed_print(rolled, "2026-08-25")
    assert pr and pr["reaction_day"] == "2026-08-12"   # 两周后照样吵


def test_card_anchored_before_the_print_is_flagged():
    cal = _cal(LITE={"next_reaction_day": TODAY, "pub_day": "2026-08-11", "pub_type": 2,
                     "implied_move_pct": 10.56})
    (hit,) = ea.cards_stale_after_print(_doc(LITE=_card("2026-08-06")),
                                        today=TODAY, calendar=cal)
    assert hit.startswith("LITE print 2026-08-11 AMC")
    assert "2026-08-06" in hit and "±10.56%" in hit


def test_repainting_on_the_reaction_day_silences_it():
    """锚 ≥ 反应日 = 已重画。自动消音, 不需要人去清 latch。"""
    cal = _cal(LITE={"next_reaction_day": TODAY, "pub_day": "2026-08-11", "pub_type": 2})
    for anchor in (TODAY, "2026-08-13"):
        assert ea.cards_stale_after_print(_doc(LITE=_card(anchor)),
                                          today=TODAY, calendar=cal) == []


def test_print_that_has_not_landed_yet_is_not_flagged():
    """未来的 print 不是欠账 —— NVDA 8/26 的卡今天不该被点名。"""
    cal = _cal(NVDA={"next_reaction_day": "2026-08-27", "pub_day": "2026-08-26",
                     "pub_type": 2})
    assert ea.cards_stale_after_print(_doc(NVDA=_card("2026-08-11")),
                                      today=TODAY, calendar=cal) == []


def test_bmo_lands_on_the_publication_day_itself():
    """BMO (pub_type 1) 盘前发 → 反应日 = 公布日当天, 不是 T+1 (NBIS 8/12)。"""
    cal = _cal(NBIS={"next_reaction_day": TODAY, "pub_day": TODAY, "pub_type": 1})
    (hit,) = ea.cards_stale_after_print(_doc(NBIS=_card("2026-08-06")),
                                        today=TODAY, calendar=cal)
    assert "BMO" in hit


def test_cards_without_zones_or_unmanaged_are_someone_elses_alert():
    """缺 zones 归 underlying_card_missing; 结构豁免层不进任何 task 输出。别双报。"""
    cal = _cal(LITE={"next_reaction_day": TODAY, "pub_day": "2026-08-11", "pub_type": 2},
               NOK={"next_reaction_day": TODAY, "pub_day": "2026-08-11", "pub_type": 2})
    doc = _doc(LITE=_card(zones=False),
               NOK=_card("2026-08-06", instrument={"role": "commemorative_hold"}))
    assert ea.cards_stale_after_print(doc, today=TODAY, calendar=cal) == []


def test_closed_and_option_cards_are_skipped():
    cal = _cal(LITE={"next_reaction_day": TODAY, "pub_day": "2026-08-11", "pub_type": 2})
    closed = _doc(LITE={**_card("2026-08-06"), "status": "closed"})
    opt = _doc(LITE={**_card("2026-08-06"), "instrument": {"type": "option"}})
    assert ea.cards_stale_after_print(closed, today=TODAY, calendar=cal) == []
    assert ea.cards_stale_after_print(opt, today=TODAY, calendar=cal) == []


def test_it_reports_and_never_gates():
    """🚨 v3 边界: 财报是情报层, **不冻结参与**。这条判别式只产字符串 ——
    它不许改卡、不许写 pending_repaint (那会让 entry_guard 转 NEEDS_CARD =
    每个反应日自动 VETO 一次, 正是 2026-07-31 AMZN 僵尸拦截的机器化版本)。"""
    cal = _cal(LITE={"next_reaction_day": TODAY, "pub_day": "2026-08-11", "pub_type": 2})
    doc = _doc(LITE=_card("2026-08-06"))
    before = json.dumps(doc, sort_keys=True)
    out = ea.cards_stale_after_print(doc, today=TODAY, calendar=cal)
    assert out and all(isinstance(x, str) for x in out)
    assert json.dumps(doc, sort_keys=True) == before        # 卡一个字节没动
    assert ea.event_state("LITE", TODAY, cal)["active"] is True   # 只有 sizing 收紧


def test_missing_calendar_is_quiet_here_not_noisy():
    """日历缺失已由 event_state 的告警负责说话, 本条不跟着刷屏。"""
    assert ea.cards_stale_after_print(_doc(LITE=_card("2026-08-06")),
                                      today=TODAY, calendar={}) == []
