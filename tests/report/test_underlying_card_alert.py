"""期权 underlying 的正股卡不许是空的 —— 而且这条必须真的响。

: watchlist stub 覆盖了 GOOGL 正股卡 (zones → null), 卡空了 **6 天**,
期间 GOOGL270115C390000 (15.9% NAV 保费) 的退出线 `stop_level_basis: underlying`
一直锚在一张空卡上, 而 8/7+8/10 连破原线 358.5、8/11 退出线下移至 351 两个决策
都是在无卡状态下手画的。

**零告警的原因不是没写检查, 是被检查的对象进不了数据链**: GOOGL 正股 qty==0
(期权的 synthetic 行), 而 `load_rows_from_state` 只产出 qty≠0 的行 —— 简报卡片 /
异动榜 / zone 行 / staleness 全都看不见它。所以这两条告警必须直扫 positions doc,
而不是挂在 rows 上; 端到端那条测试钉的就是这一点 (形状#17:
**一个不会触发的具体检查, 比没有这条检查更危险**)。
"""
import json

import pytest

from quant.report import portfolio_brief as pb
from quant.sync import watchlist_migrate as wm


@pytest.fixture(autouse=True)
def quiet_neighbours(monkeypatch, tmp_path, request):
    """别让隔壁 INFRA 告警的真实状态混进断言。"""
    if request.node.get_closest_marker("live_data"):
        return
    positions = tmp_path / "positions.json"
    watchlist = tmp_path / "watchlist.json"
    for path in (positions, watchlist):
        path.write_text(json.dumps({"tickers": {}}), encoding="utf-8")
    nav = tmp_path / "nav_history.jsonl"
    nav.write_text("", encoding="utf-8")
    klines = tmp_path / "klines"
    klines.mkdir()
    events = tmp_path / "events"
    events.mkdir()
    monkeypatch.setattr(pb, "STATE_POSITIONS_PATH", positions)
    monkeypatch.setattr(pb, "STATE_WATCHLIST_PATH", watchlist)
    monkeypatch.setattr(pb, "NAV_HISTORY_PATH", nav)
    monkeypatch.setattr(pb, "KLINE_CACHE_DIR", klines)
    monkeypatch.setattr(pb, "EVENTS_DIR", events)
    monkeypatch.setattr(pb, "STATE_PATH", tmp_path / "heartbeat-state.json")
    monkeypatch.setattr(pb.book, "POSITIONS_PATH", positions)
    monkeypatch.setattr(pb.book, "NAV_HISTORY_PATH", nav)
    monkeypatch.setattr(pb.book, "KLINE_CACHE_DIR", klines)
    monkeypatch.setattr(pb, "lot_drift", lambda *a, **k: [])
    # 财报日历也要钉 —— build_brief 会读它 (缩仓 + INFRA:earnings_calendar_stale +
    # INFRA:card_stale_earnings)，不能让财报季改变本模块的告警断言。
    monkeypatch.setattr(pb.earnings, "load_calendar",
                        lambda *a, **k: {"as_of": pb.et_today(), "tickers": {}})
    monkeypatch.setattr(pb, "untagged_fills", lambda *a, **k: [])
    from quant.decision import exit_guard
    monkeypatch.setattr(exit_guard, "run_checks", lambda **k: {
        "summary_lines": [], "account": None, "guards": {},
        "one_shot": [], "persistent": []})


def _brief():
    return pb.build_brief([], "ticker", mutate_state=False)


# ── INFRA:underlying_card_missing ────────────────────────────────────

def test_missing_underlying_card_reaches_the_brief(monkeypatch):
    """函数返回行 ≠ 告警会响。"""
    monkeypatch.setattr(pb, "underlying_cards_missing_zones", lambda *a, **k: ["GOOGL"])

    out = _brief()

    hit = [a for a in out.get("alerts") or [] if "underlying 正股卡缺 zones" in a]
    assert hit, "underlying_cards_missing_zones 有输出但 brief 的 alerts 里没有它"
    assert "GOOGL" in hit[0]
    assert any(t.get("type") == "underlying_card_missing"
               for t in out.get("triggers") or [])


def test_alert_points_at_prior_campaigns(monkeypatch):
    """告警文本要说出下一步 —— 卡"曾经存在"时, 证据就躺在 prior_campaigns 里。"""
    monkeypatch.setattr(pb, "underlying_cards_missing_zones", lambda *a, **k: ["GOOGL"])
    (hit,) = [a for a in _brief()["alerts"] if "underlying 正股卡缺 zones" in a]
    assert "prior_campaigns" in hit and "trading-analysis" in hit


def test_healthy_book_is_quiet(monkeypatch):
    monkeypatch.setattr(pb, "underlying_cards_missing_zones", lambda *a, **k: [])
    assert not [a for a in _brief().get("alerts") or []
                if "underlying 正股卡缺 zones" in a]


@pytest.mark.live_data
def test_live_state_is_clean():
    """真账本上这条不该响 —— 响了就是现在有一张空的 underlying 卡。

    (它是安全网, 不是常态噪音;  GOOGL 建卡后应恒为空。)"""
    from quant.core.holdings import underlying_cards_missing_zones
    doc = pb._load_state_doc(pb.STATE_POSITIONS_PATH)
    assert underlying_cards_missing_zones(doc) == []


# ── INFRA:promote_blocked ────────────────────────────────────────────

def test_blocked_promotion_reaches_the_brief(monkeypatch):
    monkeypatch.setattr(pb, "underlying_cards_missing_zones", lambda *a, **k: [])
    monkeypatch.setattr(wm, "blocked_promotions", lambda *a, **k: ["GOOGL"])

    out = _brief()

    hit = [a for a in out.get("alerts") or [] if "促升被守卫拦下" in a]
    assert hit, "blocked_promotions 有输出但 brief 的 alerts 里没有它"
    assert "GOOGL" in hit[0]
    assert any(t.get("type") == "promote_blocked" for t in out.get("triggers") or [])


def test_no_blocked_promotion_is_quiet(monkeypatch):
    monkeypatch.setattr(pb, "underlying_cards_missing_zones", lambda *a, **k: [])
    monkeypatch.setattr(wm, "blocked_promotions", lambda *a, **k: [])
    assert not [a for a in _brief().get("alerts") or [] if "促升被守卫拦下" in a]


def test_migration_failure_does_not_take_the_brief_down(monkeypatch):
    """守卫的探测器坏了不许连累整份简报 —— 简报比这条告警重要。"""
    monkeypatch.setattr(pb, "underlying_cards_missing_zones", lambda *a, **k: [])

    def _boom(*a, **k):
        raise RuntimeError("watchlist.json 损坏")
    monkeypatch.setattr(wm, "blocked_promotions", _boom)

    out = _brief()                       # 不抛
    assert not [a for a in out.get("alerts") or [] if "促升被守卫拦下" in a]


# ── INFRA:card_missing_next_gate ─────────────────────────────────────

def test_missing_next_gate_reaches_the_brief(monkeypatch):
    """端到端: 检测器有输出 ≠ 告警会响。"""
    monkeypatch.setattr(pb, "underlying_cards_missing_zones", lambda *a, **k: [])
    monkeypatch.setattr(pb.structure, "cards_missing_next_gate",
                        lambda *a, **k: ["COHR [常规参与 cap 40%] — 只有一轮 (R2/R3 无授权路径)"])

    out = _brief()

    hit = [a for a in out.get("alerts") or [] if "缺上行状态转移轴" in a]
    assert hit, "cards_missing_next_gate 有输出但 brief 的 alerts 里没有它"
    assert "COHR" in hit[0] and "--next-regime" in hit[0]
    assert any(t.get("type") == "card_missing_next_gate"
               for t in out.get("triggers") or [])


def test_all_cards_have_an_axis_is_quiet(monkeypatch):
    monkeypatch.setattr(pb, "underlying_cards_missing_zones", lambda *a, **k: [])
    monkeypatch.setattr(pb.structure, "cards_missing_next_gate", lambda *a, **k: [])
    assert not [a for a in _brief().get("alerts") or [] if "缺上行状态转移轴" in a]


# ── INFRA:card_stale_earnings ────────────────────────────────────────
#
#  用户 抓: LITE 卡 update_trigger 逐字写着「 AMC FQ4 print 落地
# 当日全重画 (v3 SLA)」, earnings_calendar 也知道 pub_day 8/11 / AMC / 反应日 8/12
# —— 中间**没有任何代码路径**。日历只喂 event_state 缩仓, SLA 只活在 prose 里。
# 卡锚停在 8/06 过了四个交易日, 每轮 heartbeat 与 briefing 都一声没有。

def test_stale_after_print_reaches_the_brief(monkeypatch):
    """端到端: 检测器有输出 ≠ 告警会响 ( 形状#17)。"""
    monkeypatch.setattr(pb, "underlying_cards_missing_zones", lambda *a, **k: [])
    monkeypatch.setattr(pb.earnings, "cards_stale_after_print",
                        lambda *a, **k: ["LITE print 2026-08-11 AMC → 反应日 "
                                         "2026-08-12, 卡锚停在 2026-08-06"])

    out = _brief()

    hit = [a for a in out.get("alerts") or [] if "财报已落地但卡未重画" in a]
    assert hit, "cards_stale_after_print 有输出但 brief 的 alerts 里没有它"
    assert "LITE" in hit[0]
    assert any(t.get("type") == "card_stale_earnings"
               for t in out.get("triggers") or [])


def test_alert_says_wait_for_the_reaction_day_close(monkeypatch):
    """告警必须自带时点, 否则读到的人会在盘前照 print 画 —— 未完成 bar 当收盘,
    外加 repaint_trigger 裁决 A 的 40% 白画率。"""
    monkeypatch.setattr(pb, "underlying_cards_missing_zones", lambda *a, **k: [])
    monkeypatch.setattr(pb.earnings, "cards_stale_after_print", lambda *a, **k: ["LITE ..."])
    (hit,) = [a for a in _brief()["alerts"] if "财报已落地但卡未重画" in a]
    assert "反应日收盘" in hit
    assert "授权不受影响" in hit          # v3: 报的是地图欠账, 不是参与权


def test_repainted_book_is_quiet(monkeypatch):
    monkeypatch.setattr(pb, "underlying_cards_missing_zones", lambda *a, **k: [])
    monkeypatch.setattr(pb.earnings, "cards_stale_after_print", lambda *a, **k: [])
    assert not [a for a in _brief().get("alerts") or [] if "财报已落地但卡未重画" in a]


# ── INFRA:gate_anchor_desync ─────────────────────────────────────────
#
# entry_guard 的 STALE 只覆盖 watchlist ⇒ 持仓卡的同锚破裂零出口。
# : 884411d 一笔造出两个方向相反的破裂, MU (watchlist) 天天喊,
# MRVL (持仓) 一声不响 —— 「检查够不着一半对象」的又一例。

def test_gate_anchor_desync_reaches_the_brief(monkeypatch):
    monkeypatch.setattr(pb, "underlying_cards_missing_zones", lambda *a, **k: [])
    monkeypatch.setattr(pb.structure, "cards_with_gate_anchor_desync",
                        lambda *a, **k: ["MRVL gate 223 锚于 2026-08-06, "
                                         "zone 锚于 2026-08-11 —— 只重画了一半"])

    out = _brief()

    hit = [a for a in out.get("alerts") or [] if "gate 与 zone 锚不同步" in a]
    assert hit, "cards_with_gate_anchor_desync 有输出但 brief 的 alerts 里没有它"
    assert "MRVL" in hit[0] and "复核 ≠ 重锚" in hit[0]
    assert any(t.get("type") == "gate_anchor_desync" for t in out.get("triggers") or [])


def test_synced_book_is_quiet_on_anchors(monkeypatch):
    monkeypatch.setattr(pb, "underlying_cards_missing_zones", lambda *a, **k: [])
    monkeypatch.setattr(pb.structure, "cards_with_gate_anchor_desync", lambda *a, **k: [])
    assert not [a for a in _brief().get("alerts") or [] if "gate 与 zone 锚不同步" in a]
