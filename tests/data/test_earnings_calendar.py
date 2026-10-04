"""data/earnings_calendar —— 前视日历的**纯函数**部分 (不碰 OpenD)。

这个模块存在的理由本身值得钉一条: earnings_window 路 A  裁决时,
`market/earnings_deep/` 只是历史存档、**没有前视日历**—— 规则在 live 无法触发。
所以这里测的是"未来那一期能不能被认出来", 以及先验不足时会不会假装知道。
"""
import pytest

from quant.data import earnings_calendar as ec
from quant.core.earnings import MIN_PRIOR, POOL_X_EARN


def test_shrink_estimator_falls_back_to_pool_when_prior_is_thin():
    """先验 <8 → 池级 p90, 且 `x_source` 写明是回退 (§4 预注册)。"""
    x, n, src = ec.estimate_x_earn([2.0] * (MIN_PRIOR - 1))
    assert x == POOL_X_EARN and n == MIN_PRIOR - 1 and "pool_p90" in src


def test_shrink_estimator_is_the_C_arm():
    """C 臂: λ=n/(n+10) 向池级收缩。选 C 而非 B, 是因为它对少样本更稳。"""
    drops = [1.0] * 19 + [8.0]           # p98 = 最坏那一个
    x, n, src = ec.estimate_x_earn(drops)
    lam = n / (n + 10)
    assert "C_shrink" in src
    assert x == pytest.approx(lam * 8.0 + (1 - lam) * POOL_X_EARN)
    assert POOL_X_EARN < x < 8.0         # 收缩了, 没有直接吃"最坏那一个"


def test_event_drops_are_atr_normalised_and_signed_down_positive(tmp_path, monkeypatch):
    monkeypatch.setattr(ec, "KLINE_DEEP_DIR", tmp_path)
    monkeypatch.setattr(ec, "KLINE_CACHE_DIR", tmp_path)
    import json
    rows = [{"time": f"2026-01-{i + 1:02d}", "open": 100.0, "high": 105.0,
             "low": 95.0, "close": 100.0} for i in range(60)]
    rows[50]["close"] = 90.0             # 反应日 -10, ATR ≈ 10 → 跌幅 ≈ 1N
    (tmp_path / "XX.jsonl").write_text("\n".join(json.dumps(r) for r in rows))
    drops = ec.event_drops("XX", [rows[50]["time"]])
    assert len(drops) == 1 and drops[0] == pytest.approx(1.0, abs=0.15)  # 跌为正


def test_missing_archive_is_no_prior_not_a_crash(tmp_path, monkeypatch):
    """无存档的票 (SPCX / 新股) 不该炸整轮 —— 它只是没有先验。"""
    monkeypatch.setattr(ec, "EARNINGS_DEEP_DIR", tmp_path)
    assert ec.archive_reaction_days("NOPE") == []
    assert ec.estimate_x_earn(ec.event_drops("NOPE", []))[0] == POOL_X_EARN


def test_card_tickers_skips_closed_cards_and_volume_references(tmp_path, monkeypatch):
    """平仓残卡每轮白吃一次 API 配额并留一行错 (DRAM 是 ETN, 直接被拒)。"""
    import json
    pos = tmp_path / "p.json"
    wl = tmp_path / "w.json"
    pos.write_text(json.dumps({"tickers": {
        "AAOI": {"broker": {"qty": 4}},
        "DRAM": {"broker": {"qty": 0}},                       # 平仓残卡
        "GOOGL270115C390000": {"broker": {"qty": 1}},         # 期权 → 归标的
    }}))
    wl.write_text(json.dumps({"tickers": {
        "MU": {"broker": None, "status": "watching"},         # 无快照 stub 也算在场
        "SPY": {"broker": None, "status": "watching",
                "instrument": {"role": "volume_reference"}},  # 不交易, 无财报语义
        "OLD": {"broker": None, "status": "closed"},
        # : 移出自选分组只翻 moomoo_listed, status 仍 watching —— 墓碑卡不排财报
        "AMKR": {"broker": {"qty": 0, "current_price": 47.7}, "status": "watching",
                 "moomoo_listed": False},
        #  起 watchlist 卡携带报价快照 (broker block, qty 恒 0)。
        # 旧判据 `broker is None` 会把这类卡整个排除 —— 快照功能上线当天日历
        # 只剩 positions, COHR  print 前夜被 用户 抓漏。回归钉:
        "COHR": {"broker": {"qty": 0, "current_price": 325.15},
                 "status": "watching"},
    }}))
    monkeypatch.setattr(ec, "POSITIONS_PATH", pos)
    monkeypatch.setattr(ec, "WATCHLIST_PATH", wl)
    assert ec.card_tickers() == ["AAOI", "COHR", "GOOGL", "MU"]


# ── 前视判据 = 反应日还没过, 不是"公布日在未来" (INFRA-2) ──

def _row(pub, ptype, close=None, im=None):
    return {"pub_trading_day_str": pub, "pub_type": ptype,
            "close_price": close, "predict_vola_ratio_newest": im}


def _ntd(day):
    """测试用下一交易日: 跳过周末即可 (live 走 OpenD 交易日历)。"""
    import datetime as dt
    d = dt.date.fromisoformat(day) + dt.timedelta(days=1)
    while d.weekday() >= 5:
        d += dt.timedelta(days=1)
    return d.isoformat()


def test_amc_leg_survives_its_own_publication_day():
    """🚨 源案例 LITE : 旧判据 `pub_day > today` 在公布日当天清空整条腿。

    AMC 的反应日是 T+1, 而 T+0 缩仓要在**反应日**那天生效 —— 腿在公布日就消失且
    此后永不回来 ⇒ AMC 票的缩仓永远不触发。`core.earnings.event_state` 只认
    `next_reaction_day == today`, 读不到时连"缺失"都不会报 (字段合法地不存在)。
    """
    rows = [_row("2026-08-11", 2, im=13.5)]                 # 今晚 AMC print
    got = ec.pick_upcoming(rows, "2026-08-11", _ntd)        # 公布日当天
    assert got["next_reaction_day"] == "2026-08-12" and got["implied_move_pct"] == 13.5

    got = ec.pick_upcoming(rows, "2026-08-12", _ntd)        # 反应日当天 —— 缩仓要用它
    assert got["next_reaction_day"] == "2026-08-12"

    assert ec.pick_upcoming(rows, "2026-08-13", _ntd) is None   # 反应日已过 → 退场


def test_bmo_reacts_same_day_and_leaves_the_day_after():
    rows = [_row("2026-08-12", 1)]
    assert ec.pick_upcoming(rows, "2026-08-12", _ntd)["next_reaction_day"] == "2026-08-12"
    assert ec.pick_upcoming(rows, "2026-08-13", _ntd) is None


def test_stale_pending_row_does_not_shadow_the_real_next_period():
    """vendor 没回填收盘的历史期不能顶掉真正的前视那一期 (min() 会先撞上它)。"""
    rows = [_row("2026-05-01", 2), _row("2026-08-11", 2, im=13.5)]
    got = ec.pick_upcoming(rows, "2026-08-11", _ntd)
    assert got["pub_day"] == "2026-08-11"


def test_friday_amc_reaction_day_is_monday():
    """跨周末: 回看窗口必须够宽, 否则周一那天腿又没了。"""
    rows = [_row("2026-08-07", 2)]                          # 周五 AMC
    assert ec.pick_upcoming(rows, "2026-08-10", _ntd)["next_reaction_day"] == "2026-08-10"


def test_reacted_period_with_close_is_never_upcoming():
    assert ec.pick_upcoming([_row("2026-08-11", 2, close=705.0)], "2026-08-11", _ntd) is None


# ── last_print 粘住 () ─────────────────────────────────────
#
# `pick_upcoming` 的 `rday < today` 让前视条目在反应日**次日**滚成下一季。
# 重画 SLA 告警要一直吵到卡真被重画 —— 所以已落地那一期必须留下存根。

TODAY = "2026-08-12"


def test_landed_entry_becomes_a_stub():
    """反应日已到 → 归一成存根; 还没到 → 不是"已落地", 不留。"""
    entry = {"next_reaction_day": TODAY, "pub_day": "2026-08-11", "pub_type": 2,
             "implied_move_pct": 10.56}
    assert ec._landed_stub(entry, TODAY) == {
        "pub_day": "2026-08-11", "pub_type": 2, "reaction_day": TODAY,
        "implied_move_pct": 10.56}
    assert ec._landed_stub(entry, "2026-08-11") is None
    assert ec._landed_stub({"x_earn": 5.5}, TODAY) is None


def test_stub_survives_the_entry_rolling_to_next_quarter():
    """次日: 新条目已是 11 月那期, 旧存根必须还在。"""
    prev = {"tickers": {"LITE": {"next_reaction_day": TODAY, "pub_day": "2026-08-11",
                                 "pub_type": 2}}}
    out = {"tickers": {"LITE": {"next_reaction_day": "2026-11-04",
                                "pub_day": "2026-11-03", "pub_type": 2}}}
    ec.merge_last_print(out, prev, "2026-08-13", ["LITE"])
    assert out["tickers"]["LITE"]["last_print"]["reaction_day"] == TODAY
    assert out["tickers"]["LITE"]["next_reaction_day"] == "2026-11-04"  # 前视没被动


def test_stub_only_moves_forward():
    """三来源取 max。一次抓错不会把已知的 print 倒退回去。"""
    prev = {"tickers": {"LITE": {"last_print": {"reaction_day": TODAY,
                                                "pub_day": "2026-08-11"}}}}
    out = {"tickers": {"LITE": {"next_reaction_day": "2026-05-06",
                                "pub_day": "2026-05-05", "pub_type": 2}}}
    ec.merge_last_print(out, prev, "2026-08-25", ["LITE"])
    assert out["tickers"]["LITE"]["last_print"]["reaction_day"] == TODAY


def test_a_failed_fetch_does_not_erase_the_stub():
    """**静默失效形状**: 拉失败 → 该票整条不在 out 里 → 存根跟着没 → 告警消失。

    补回来的存根只有 last_print, 没有 next_reaction_day —— 不假装知道缩仓。
    """
    prev = {"tickers": {"LITE": {"last_print": {"reaction_day": TODAY,
                                                "pub_day": "2026-08-11"}}}}
    out = {"tickers": {}, "errors": ["LITE: timeout"]}
    ec.merge_last_print(out, prev, "2026-08-13", ["LITE"])
    assert out["tickers"]["LITE"]["last_print"]["reaction_day"] == TODAY
    assert "next_reaction_day" not in out["tickers"]["LITE"]


def test_tickers_outside_this_round_are_not_resurrected():
    """`--tickers LITE` 子集调用不该把别的票凭空补回来。"""
    prev = {"tickers": {"MU": {"last_print": {"reaction_day": "2026-06-25"}}}}
    out = {"tickers": {"LITE": {}}}
    ec.merge_last_print(out, prev, TODAY, ["LITE"])
    assert "MU" not in out["tickers"]


def test_no_prior_file_is_not_a_crash():
    out = {"tickers": {"LITE": {"next_reaction_day": TODAY, "pub_day": "2026-08-11",
                               "pub_type": 2}}}
    ec.merge_last_print(out, None, TODAY, ["LITE"])
    assert out["tickers"]["LITE"]["last_print"]["reaction_day"] == TODAY


def test_card_tickers_skips_funds_including_via_their_option_legs(tmp_path, monkeypatch):
    """基金票 (ETF) 照常交易但没有财报日: 接口直接拒 ("only supports equities")。
    章在 underlying 那张卡上 —— 持有基金 call 时期权卡 body 不带章, 按标的名字也要剔掉。"""
    import json
    pos = tmp_path / "p.json"
    wl = tmp_path / "w.json"
    pos.write_text(json.dumps({"tickers": {
        "ETFX270617C560000": {"broker": {"qty": 1}},
        "NVDA270319C220000": {"broker": {"qty": 1}},
    }}))
    wl.write_text(json.dumps({"tickers": {
        "ETFX": {"broker": None, "status": "watching",
                "instrument": {"type": "stock", "security_type": "ETF"}},
        "MU": {"broker": None, "status": "watching"},
    }}))
    monkeypatch.setattr(ec, "POSITIONS_PATH", pos)
    monkeypatch.setattr(ec, "WATCHLIST_PATH", wl)
    assert ec.card_tickers() == ["MU", "NVDA"]
