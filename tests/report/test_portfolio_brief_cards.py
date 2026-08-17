"""Position cards: ladder budget, dual stop lines, zone-hint semantics.

All three come from the same  failure: the heartbeat Telegram text was
LLM prose written off a brief JSON that carried none of the binding facts.

    ⚠️ HEARTBEAT — MU 异动
    MU -7.86% @ $912.33 ... 仍在首档吸纳区 885-925 内，trail stop $750.84 未破

Three defects in one line: (a) the probe ladder was spent (1 share @925 against a
$227 cap) so the zone was location, not a buy signal; (b) the card's own stop was
800 — 6.2% tighter than the quoted trail, and the trail is anchored on a
pre-entry high the position never earned; (c) the card's gates (880 ✅, 1008 →
confirmed) never reached the JSON at all — dropped by the legacy-zone whitelist.
"""
import json

import pytest

from quant.core.sizing import NOTIONAL_CAP
from quant.report import portfolio_brief as pb

NAV = 22666.53
#  分类轴退役: 预算行的 cap 唯一来源 = cfg.current_regime.cap_pct_nav。
# CAP2 = 2% regime cap 场景 (整股约束/超顶数学的既有算例沿用这个量级)。
CAP2 = NAV * 0.02
CFG2 = {"current_regime": {"name": "小额场景", "cap_pct_nav": 0.02}}
CFG_FULL = {"current_regime": {"name": "常规参与", "cap_pct_nav": NOTIONAL_CAP}}


@pytest.fixture(autouse=True)
def no_earnings_today(monkeypatch):
    """本模块钉的是 cap 的**累计 / 市值**语义, 与财报反应日缩仓无关 —— 所以给它一份
    确定的空日历, 别读 live。

    🚨  实证的坑: `position_budget` 的 `calendar=None` **默认值会去读 live
    的 `market/earnings_calendar.json`**。`test_leftover_smaller_than_one_share_is_exhausted`
    拿 NBIS 当样本, 而 NBIS 的真实财报反应日恰好是那天 (8/12 BMO) → 缩仓 ×0.7 把
    probe cap 压到低于当前敞口 → `remaining_usd` 归 0, 断言当场红。**测试没写错,
    它只是不知道自己在读 live。**

    注入点本来就有 (`position_budget(calendar=)` / `event_state(calendar=)`), 缺的是
    默认值的安全方向: `None` = 悄悄读 live。autouse 挡在模块级, 而不是逐个用例
    monkeypatch —— 下一个人在这里新写一个用例, 不会再踩同一颗雷。
    要测缩仓本身的用例请显式传 calendar (见 §1 末尾那两条)。
    """
    monkeypatch.setattr(pb.earnings, "load_calendar",
                        lambda *a, **k: {"as_of": pb.et_today(), "tickers": {}})


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Throwaway events dir + nav history + kline cache."""
    def _apply(fills=(), nav=22666.53, closes=None):
        events = tmp_path / "events"
        (events / "2026-07").mkdir(parents=True, exist_ok=True)
        (events / "2026-07" / "trades.jsonl").write_text(
            "\n".join(json.dumps(f) for f in fills), encoding="utf-8")
        monkeypatch.setattr(pb, "EVENTS_DIR", events)

        nav_path = tmp_path / "nav_history.jsonl"
        nav_path.write_text(json.dumps({"total_assets": nav}) if nav else "",
                            encoding="utf-8")
        monkeypatch.setattr(pb, "NAV_HISTORY_PATH", nav_path)

        kdir = tmp_path / "klines"
        kdir.mkdir(exist_ok=True)
        for ticker, bars in (closes or {}).items():
            (kdir / f"{ticker}.jsonl").write_text(
                "\n".join(json.dumps({"time": d, "close": c}) for d, c in bars),
                encoding="utf-8")
        monkeypatch.setattr(pb, "KLINE_CACHE_DIR", kdir)
    return _apply


def _fill(ticker="MU", qty=1.0, price=925.0, cls="probe", tier="position",
          event_type="fill", side="BUY"):
    return {"event_type": event_type, "ticker": ticker, "side": side, "qty": qty,
            "price": price, "classification": cls, "tier": tier}


HELD = {"ticker": "MU", "qty": 1.0, "current_price": 912.33, "is_option": False,
        "synthetic": False}


# ── 1. ladder budget ──────────────────────────────────────────

def test_over_cap_whole_share_leaves_no_budget(env):
    """MU: 1 share @925 over a 2% regime cap (2.0x), written exception."""
    env([_fill()])
    b = pb.position_budget("MU", HELD, pb._latest_nav(), CFG2)

    assert b["regime_cap_frac"] == 0.02
    assert b["deployed_usd"] == 925.0
    # C4 (): cap 是**累计**的 —— 净掉当前敞口 (1 × 912.33) 后为 0
    assert b["exposure_usd"] == pytest.approx(912.33, abs=0.01)
    assert b["class_cap_usd"] == pytest.approx(CAP2, abs=0.01)   # 名义仍在
    assert b["cap_usd"] == 0.0
    assert b["remaining_usd"] == 0.0
    assert b["exhausted"] is True


def test_leftover_smaller_than_one_share_is_exhausted(env):
    """NBIS: 2 shares @216 of a 2% cap. The ~$21 left buys nothing at $188 —
    under the whole-share constraint that is the same as zero."""
    env([_fill(ticker="NBIS", qty=2.0, price=216.0)])
    item = {**HELD, "ticker": "NBIS", "qty": 2.0, "current_price": 188.49}
    b = pb.position_budget("NBIS", item, pb._latest_nav(), CFG2)

    # C4: 减数是**市值**(2 × 188.49 = 376.98), 不是成本 432 —— cap 跟着 NAV
    # mark-to-market, 减数冻在成本上就是同一个减法两边活在不同时间 (用户)
    assert b["exposure_usd"] == pytest.approx(376.98, abs=0.01)
    assert b["remaining_usd"] == pytest.approx(CAP2 - 376.98, abs=0.01)
    assert b["remaining_usd"] < 188.49
    assert b["addable_shares"] == 0
    assert b["exhausted"] is True


def test_budget_left_reports_addable_shares(env):
    env([_fill(ticker="INTC", qty=1.0, price=92.0)])
    item = {**HELD, "ticker": "INTC", "current_price": 92.0}
    b = pb.position_budget("INTC", item, pb._latest_nav(), CFG2)

    assert b["addable_shares"] == int((CAP2 - 92.0) // 92.0)   # 敞口 1×92
    assert b["exhausted"] is False


def test_gate_budget_arm_binds_position_budget(env):
    """A stock budget row must use the same gate-risk arm as entry authorization."""
    env([_fill(ticker="TSTX", qty=5.0, price=200.0)])
    item = {**HELD, "ticker": "TSTX", "qty": 5.0, "current_price": 200.0}
    cfg = {**CFG_FULL, "stop_level": 100.0}
    budget = pb.position_budget("TSTX", item, pb._latest_nav(), cfg)

    assert budget["portfolio_binding"] == "n_unit"
    assert budget["cap_usd"] == pytest.approx(0.025 * NAV * 2 - 1000.0, abs=0.01)
    assert budget["addable_shares"] == 0
    assert budget["exhausted"] is True


def test_position_budget_prices_capacity_at_z1_upper(env):
    env([_fill(ticker="TSTX", qty=5.0, price=200.0)])
    item = {**HELD, "ticker": "TSTX", "qty": 5.0, "current_price": 200.0}
    cfg = {**CFG_FULL, "stop_level": 100.0,
           "accumulation_zone_1": [190.0, 195.0]}
    budget = pb.position_budget("TSTX", item, pb._latest_nav(), cfg)

    assert budget["cap_usd"] == pytest.approx(
        0.025 * NAV * 195 / 95 - 1000.0, abs=0.01)


def test_cap_comes_from_regime_only_and_counts_all_fills(env):
    """ 分类轴退役: cap 唯一来源 = regime cap; campaign 内**全部**BUY
    计入投入 (旧「按分类分轮计账」的过滤随轴退役 —— 本 campaign 的钱就是本
    campaign 的钱)。累计语义 (A1/C4) 不变: 净掉当前敞口。"""
    env([_fill(price=925.0, cls="probe"), _fill(price=1008.0, cls="confirmed")])
    b = pb.position_budget("MU", HELD, pb._latest_nav(), CFG_FULL)
    nominal = pb._latest_nav() * NOTIONAL_CAP

    assert b["regime_cap_frac"] == NOTIONAL_CAP
    assert b["deployed_usd"] == 1933.0                       # 925 + 1008 全计
    assert b["gate_no"] == 1                                  # 无转移史 = 第一档
    assert b["class_cap_usd"] == pytest.approx(nominal, abs=0.01)   # 名义天花板
    assert b["cap_usd"] == pytest.approx(nominal - 912.33, abs=0.01)  # 净掉敞口


def test_cap_zero_regime_renders_a_zero_budget_row(env):
    """cap 0 卡**照出预算行**(额度 0 显式化) —— 旧逻辑反查分类失败 return None,
    预算行消失 = can_add 失败为开的另一半。"""
    env([_fill()])
    b = pb.position_budget("MU", HELD, pb._latest_nav(),
                           {"current_regime": {"name": "持仓·无加仓授权", "cap_pct_nav": 0}})
    assert b is not None
    assert b["cap_usd"] == 0.0 and b["exhausted"] is True


def test_close_event_resets_the_campaign(env):
    """7/16 全清 → 7/24 重进: 上一轮的 size 不占用新 campaign 的预算。"""
    env([_fill(qty=9.0, price=868.01, event_type="close", side="SELL"), _fill()])
    b = pb.position_budget("MU", HELD, pb._latest_nav(), CFG2)

    assert b["deployed_usd"] == 925.0


def test_base_tier_is_exempt_from_the_trading_budget(env):
    env([_fill(tier="base", price=366.5), _fill(price=925.0)])
    b = pb.position_budget("MU", HELD, pb._latest_nav(), CFG2)

    assert b["deployed_usd"] == 925.0


def test_budget_is_silent_without_fills_or_nav_or_regime(env):
    env([])
    assert pb.position_budget("MU", HELD, pb._latest_nav(), CFG2) is None
    env([_fill()], nav=None)
    assert pb.position_budget("MU", HELD, pb._latest_nav(), CFG2) is None
    env([_fill()])
    # 无 regime = 无法判定 (沉默好过编数) —— schema 保证活跃卡必有, 残卡才会走到这
    assert pb.position_budget("MU", HELD, pb._latest_nav(), {}) is None


# ── 1b. 财报反应日缩仓: 从"隐形副作用"改成两条显式用例 ──────────
#
# 这一段是  那次红的产物。缩仓行为本身**此前零测试**—— 正因如此它
# 才能悄悄改掉隔壁用例的数字而没人知道是谁干的。现在把两个世界都钉死。

def _cal_today(ticker, x_earn=5.563):
    return {"as_of": pb.et_today(),
            "tickers": {ticker: {"next_reaction_day": pb.et_today(),
                                 "x_earn": x_earn, "n_prior": 19}}}


def test_earnings_reaction_day_shrinks_the_actual_cap(env):
    """T+0 缩仓压的是**实际**cap (`cap_usd` / `remaining_usd`), 名义
    `class_cap_usd` 不动 —— 改的是"那天能多大"不是"能不能进", sizing 层, v3 不变。"""
    from quant.core.earnings import shrink_ratio
    env([_fill(ticker="INTC", qty=1.0, price=92.0)])
    item = {**HELD, "ticker": "INTC", "current_price": 92.0}
    shrink = shrink_ratio(5.563)

    flat = pb.position_budget("INTC", item, pb._latest_nav(), CFG2,
                              calendar={"as_of": pb.et_today(), "tickers": {}})
    shrunk = pb.position_budget("INTC", item, pb._latest_nav(), CFG2,
                                calendar=_cal_today("INTC"))

    assert flat["event_shrink"] is None and flat["earnings_reaction_day"] is None
    assert shrunk["event_shrink"] == pytest.approx(shrink)
    assert shrunk["earnings_reaction_day"] == pb.et_today()
    # 名义天花板两边同一个数 (缩仓不改名义, 只改额度)
    for b in (flat, shrunk):
        assert b["class_cap_usd"] == pytest.approx(CAP2, abs=0.01)
    # 额度: 名义 ×缩仓 − 当前敞口 (1 × 92)
    assert flat["cap_usd"] == pytest.approx(CAP2 - 92.0, abs=0.01)
    assert shrunk["cap_usd"] == pytest.approx(CAP2 * shrink - 92.0, abs=0.01)
    assert shrunk["cap_usd"] < flat["cap_usd"]                 # 只收紧, 永不放大


def test_an_injected_calendar_beats_the_live_file(env, monkeypatch):
    """**本模块所有其他用例的地基**: 传了 calendar 就不许再去看 live 文件。

    验收口径 (用户): 把 live 换成"今天发财报"与"今天没人发", 结果
    必须逐字节相同 —— 证明它不再看日历。反过来 (不传 calendar) 就会看, 那正是
    8/12 那次红的机制, 所以本模块用 autouse fixture 把默认路径也钉住了。
    """
    env([_fill(ticker="INTC", qty=1.0, price=92.0)])
    item = {**HELD, "ticker": "INTC", "current_price": 92.0}
    injected = {"as_of": pb.et_today(), "tickers": {}}

    outs = set()
    for live in ({}, _cal_today("INTC"), _cal_today("INTC", x_earn=99.0)):
        monkeypatch.setattr(pb.earnings, "load_calendar", lambda *a, _l=live, **k: _l)
        outs.add(json.dumps(pb.position_budget("INTC", item, pb._latest_nav(), CFG2,
                                               calendar=injected), sort_keys=True))
    assert len(outs) == 1, "live 日历漏进来了 — 注入没有优先"


def _ohlc(ticker="MU", n=40, base=900.0, rng=9.0):
    """env 的 closes 只写 close, 而 ATR 要 high/low —— 缺了会让 TR 退化成 ~prev_close,
    ATR≈价格本身, 冲击预算凭空 binding。组合层的数只有喂真 OHLC 才有意义。"""
    (pb.KLINE_CACHE_DIR / f"{ticker}.jsonl").write_text("\n".join(
        json.dumps({"time": f"2026-06-{i % 28 + 1:02d}", "open": base + i,
                    "high": base + i + rng / 2, "low": base + i - rng / 2,
                    "close": base + i}) for i in range(n)), encoding="utf-8")


def test_budget_cap_is_squeezed_by_the_portfolio_layer(env):
    """用户 : cap 不再等于 nav × 分类比例 —— 组合层的冲击预算余额与
    gross 余额也要减掉, 而且 binding 必须点名 (否则无法解释 40% 为什么只给 3%)。"""
    env([_fill(price=925.0, cls="confirmed")])
    _ohlc()
    free = pb.position_budget("MU", HELD, pb._latest_nav(), CFG_FULL)
    assert free["portfolio_binding"] is None
    assert free["cap_usd"] == pytest.approx(NAV * NOTIONAL_CAP - 912.33, abs=0.01)

    tight = pb.position_budget("MU", HELD, pb._latest_nav(), CFG_FULL,
                               {"used_budget": 0.0, "used_gross": 1.07})
    assert tight["portfolio_binding"] == "gross"
    assert tight["cap_usd"] == pytest.approx(NAV * 0.03, abs=0.01)   # 110% − 107%
    assert tight["class_cap_usd"] == pytest.approx(NAV * NOTIONAL_CAP, abs=0.01)
    # 同一笔仓位, 只因为书堆满了: 从"还可加 8 股"变成"本轮已超支"。
    assert free["exhausted"] is False and tight["exhausted"] is True
    assert tight["cap_usd"] < free["cap_usd"]


def test_option_budget_never_mixes_premium_with_notional(env):
    """期权行的 deployed 是**保费**, 组合层那两条是**标的名义**—— 不能直接取小。

    有 delta 就换算 (sizing.notional_to_premium_cap), 拿不到就只报分类 cap 并在
    portfolio_binding 里写明没接。**宁可少一条约束, 不给一个单位错的数。**
    """
    occ = "MU270115C900000"
    env([_fill(ticker=occ, qty=1.0, price=35.0, cls="confirmed")])
    _ohlc()
    item = {"ticker": occ, "qty": 1.0, "current_price": 35.0, "is_option": True,
            "synthetic": False}
    usage = {"used_budget": 0.0, "used_gross": 1.07}       # 名义余额 3% NAV = $680
    no_delta = pb.position_budget(occ, item, pb._latest_nav(), CFG_FULL, usage)
    assert "未接" in no_delta["portfolio_binding"]
    # C4 (): 期权敞口按**保费市值**(1 × 35 × 100) 净掉, 与 8/10 gross 裁决同口径
    assert no_delta["cap_usd"] == pytest.approx(
        NAV * NOTIONAL_CAP - 3500.0, abs=0.01)

    with_delta = pb.position_budget(occ, item, pb._latest_nav(),
                                    {**CFG_FULL, "delta": 0.5}, usage)
    spot = pb.book.price_of("MU", pb.KLINE_CACHE_DIR)
    assert with_delta["portfolio_binding"] == "notional→premium"
    assert with_delta["cap_usd"] == pytest.approx(NAV * 0.03 * 35.0 / (0.5 * spot), abs=0.01)
    # 不换算的话会把 $680 名义当保费花 —— 那笔的名义敞口是它的 spot/premium×delta 倍
    assert with_delta["cap_usd"] < NAV * 0.03


# ── 2. dual stop lines ────────────────────────────────────────

GUARD = {"trail": {"trail": 750.8403, "mode": "normal"},
         "structure": {"high_label": "LH", "low_label": "LL", "trend": "down"}}


# 显示合同 (用户): 各基折 max 只显示一个止损值 + 来源。
# trail/棘轮照常运转 — 折进 max 的是显示, 不是执行。

def test_card_stop_wins_the_fold_when_tighter_than_trail():
    st = pb.stop_status("MU", HELD, {"stop_level": 800.0, "stop_level_days": 2}, GUARD)

    assert st["source"] == "card"
    assert st["value"] == 800.0
    assert st["dist_pct"] == pytest.approx(-12.31, abs=0.01)
    assert not st["breached"]
    text = pb.format_stop_status(st)
    assert "800 (card 收盘×2日)" in text
    assert "trail" not in text                       # 非 binding 基不再并列


def test_trail_takes_the_fold_when_it_has_ratcheted_above_the_card_stop():
    guard = {"trail": {"trail": 880.0, "mode": "normal"}}
    st = pb.stop_status("MU", HELD, {"stop_level": 800.0}, guard)

    assert st["source"] == "trail"
    assert st["value"] == 880.0


def test_lock_floor_joins_the_fold_when_ratchet_armed():
    """棘轮地板此前根本不在 stop_status 的基里 —— 三基折 max 是新覆盖,
    词表 lock 与 price_reminder 同源。"""
    guard = {"trail": {"trail": 750.84, "mode": "normal"},
             "ratchet": {"stage": "stage1", "floor": 850.0}}
    st = pb.stop_status("MU", HELD, {"stop_level": 800.0}, guard)

    assert st["source"] == "lock" and st["value"] == 850.0
    assert "棘轮" in pb.format_stop_status(st)


def test_stop_status_skips_commemorative_and_flat():
    assert pb.stop_status("SPCX", HELD, {"stop_level": 150.0,
                                         "role": "commemorative_hold"}, None) is None
    assert pb.stop_status("MU", {**HELD, "qty": 0}, {"stop_level": 800.0}, GUARD) is None


def test_stop_status_reports_card_alone_when_unarmed():
    st = pb.stop_status("MU", HELD, {"stop_level": 800.0}, None)

    assert st["source"] == "card"
    assert "trail" not in pb.format_stop_status(st)


def test_option_stop_folds_in_premium_space_not_mixed_bases():
    """2026-08-10 修的量纲错: 旧实现拿期权 mark (20.48) 比正股 stop (358.5),
    距离恒 +1500% — 一个永远显示同一件事的数字等于没有数字。新合同全部折进
    保费空间: S1 保费地板原生, 正股 stop 经 BS/delta 投影。"""
    item = {"qty": 1.0, "is_option": True, "synthetic": False, "avg_cost": 35.0,
            "current_price": 20.48, "ticker": "GOOGL270115C390000",
            "strike": 390.0, "delta": 0.405, "iv_pct": 34.1}
    cfg = {"stop_level": 358.5, "stop_level_days": 2, "stop_level_basis": "underlying"}
    guard = {"trail": {"trail": 340.55, "mode": "normal"}}
    st = pb.stop_status("GOOGL270115C390000", item, cfg, guard,
                        und_spot=355.60, today="2026-08-10")

    assert st["source"] == "s1"                       # 26.25 > card 投影 (~21.6) > trail 投影
    assert st["value"] == pytest.approx(35.0 * 0.75)
    assert st["breached"]                             # mark 20.48 已在线下
    assert abs(st["dist_pct"]) < 100, "保费空间距离, 不是 +1500% 的混基读数"


def test_option_trim_projects_underlying_band_into_premium():
    item = {"qty": 1.0, "is_option": True, "synthetic": False, "avg_cost": 35.0,
            "current_price": 20.48, "ticker": "GOOGL270115C390000",
            "strike": 390.0, "delta": 0.405, "iv_pct": 34.1}
    t = pb.trim_status(item, {}, und_spot=355.60,
                       und_cfg={"trim_zone": [392.0, 405.0]}, today="2026-08-10")

    assert t and t["kind"] == "proj" and t["und_level"] == 392.0
    assert t["premium"] > 20.48                       # trim 在上方 → 投影保费高于 mark
    assert t["method"] in ("bs", "delta")
    assert "投影" in pb.format_trim_status(t)


# ── 3. zone hints: entry semantics vs location semantics ──────

def test_held_and_exhausted_downgrades_accumulation_wording(env):
    item = {**HELD, "zone_hints": []}
    pb.enrich_with_zone_context(item, {"MU": {"accumulation_zone_1": [885.0, 925.0]}},
                                budget={"exhausted": True, "classification": "probe",
                                        "deployed_usd": 925, "cap_usd": 227,
                                        "remaining_usd": 0})

    assert any(h.startswith("位于首档吸纳区") for h in item["zone_hints"])
    assert not any(h.startswith("进入") for h in item["zone_hints"])
    assert any("非加仓信号" in h for h in item["zone_hints"])


def test_candidate_keeps_entry_wording(env):
    """候选卡 (无仓位) 没有预算是**正常的**—— 入场措辞照常可执行。
    2026-08-06 收紧 can_add 时这条差点被误伤: fixture 原本带 qty=1 (其实是持仓),
    名字却写 candidate。真需求是"候选", 所以按候选构造。"""
    item = {**HELD, "qty": 0, "zone_hints": []}
    pb.enrich_with_zone_context(item, {"MU": {"accumulation_zone_1": [885.0, 925.0]}})

    assert any(h.startswith("进入首档吸纳区") for h in item["zone_hints"])


def test_held_position_with_undeterminable_budget_fails_closed(env):
    """**持仓**判不出预算 → 吸纳区不得渲染成可执行加仓提示。

     INTC 实证: 注解规则改成"只给规则外成交"后, 计划内成交不带
    classification → position_budget 返回 None → 旧写法 `not (budget or {}).get(
    "exhausted")` 恰好为 True, 于是加仓通道其实已关 ($393/$429 买不起 1 股) 的卡
    仍在提示"接近首档吸纳区"。**判不出来 ≠ 可以加。**
    """
    item = {**HELD, "zone_hints": []}                 # qty=1 = 持仓
    pb.enrich_with_zone_context(item, {"MU": {"accumulation_zone_1": [885.0, 925.0]}})

    assert not any(h.startswith("进入") for h in item["zone_hints"])
    assert any(h.startswith("位于首档吸纳区") for h in item["zone_hints"])


def test_trim_zone_stays_actionable_when_budget_is_spent(env):
    """减仓区是退出侧信号 — 加不加得动仓跟它无关。"""
    item = {**HELD, "current_price": 1040.0, "zone_hints": []}
    pb.enrich_with_zone_context(item, {"MU": {"trim_zone": [1020.0, 1070.0]}},
                                budget={"exhausted": True, "classification": "probe",
                                        "deployed_usd": 925, "cap_usd": 227,
                                        "remaining_usd": 0})

    assert any(h.startswith("进入减仓观察区") for h in item["zone_hints"])


def test_entry_advice_hints_are_suppressed_when_exhausted(env):
    """'低于首档吸纳区：可按两段式计划…' 是入场建议, 没预算就别发。"""
    item = {**HELD, "current_price": 700.0, "zone_hints": []}
    cfg = {"accumulation_zone_1": [885.0, 925.0], "accumulation_zone_2": [810.0, 855.0]}
    pb.enrich_with_zone_context(item, {"MU": cfg},
                                budget={"exhausted": True, "classification": "probe",
                                        "deployed_usd": 925, "cap_usd": 227,
                                        "remaining_usd": 0})

    assert not any("两段式" in h or "接飞刀" in h for h in item["zone_hints"])


# ── 4. gates must survive the v2 → legacy zone conversion ─────

def test_reclaim_gates_survive_zone_conversion():
    gates = [{"level": 880, "days": 2, "label": "60MA 收复"}]
    body = {"agent": {"zones": {"reclaim_gates": gates}}}

    assert pb._agent_block_to_legacy_zone(body)["reclaim_gates"] == gates


def test_gate_status_marks_met_and_unmet(env):
    env([], closes={"MU": [("2026-07-23", 990.21), ("2026-07-24", 920.95)]})
    cfg = {"reclaim_gates": [{"level": 880, "days": 2, "label": "60MA 收复→资格增强"},
                             {"level": 1008, "days": 2, "label": "结构修复→升 confirmed"}]}
    got = pb.gate_status("MU", cfg)

    assert got[0].startswith("880×2日 ✅")
    assert got[1].startswith("1008×2日 ✗")


def test_gate_status_is_unknown_without_enough_bars(env):
    env([], closes={"MU": [("2026-07-24", 920.95)]})

    assert pb.gate_status("MU", {"reclaim_gates": [{"level": 880, "days": 2}]}) == ["880×2日 ?"]


# ── 5. the card itself ────────────────────────────────────────

def test_card_carries_every_fact_the_prose_version_lost(env):
    env([_fill()], closes={"MU": [("2026-07-23", 990.21), ("2026-07-24", 920.95)]})
    item = {**HELD, "change_pct": -7.86, "avg_cost": 925.0, "unrealized_pl": -12.59,
            "price_source": "moomoo_after", "zone_hints": ["位于首档吸纳区 885.00-925.00"]}
    cfg = {"stop_level": 800.0, "stop_level_days": 2,
           "current_regime": {"name": "小额场景", "cap_pct_nav": 0.02},
           "reclaim_gates": [{"level": 1008, "days": 2, "label": "升 confirmed"}]}
    card = pb.build_position_card(item, cfg,
                                  pb.position_budget("MU", item, pb._latest_nav(), cfg),
                                  GUARD, headline="⚠️")

    assert "⚠️ MU -7.86%" in card
    assert "预算 [regime cap 2%" in card and "买不起 1 股" in card      # ladder spent
    # 显示合同 : 单值 = 各基 max = 会先触发的那条 (card 800 > trail 750.84)
    assert "止损 800 (card 收盘×2日)" in card
    assert "trail 750.84" not in card                          # 非 binding 基折进 max
    assert "位于首档吸纳区" in card                             # location, not "进入"
    assert "1008×2日 ✗" in card                                # gate reached the text
    assert "LH+LL(下降)" in card


def test_card_omits_sections_it_has_no_data_for(env):
    env([])
    item = {"ticker": "SPCX", "qty": 1.0, "current_price": 114.85, "change_pct": -2.87,
            "avg_cost": 160.0, "is_option": False, "synthetic": False, "zone_hints": []}
    card = pb.build_position_card(item, {"role": "commemorative_hold"}, None, None)

    assert "预算" not in card and "止损" not in card and "gate" not in card
    assert card.startswith("SPCX -2.87%")


def test_budget_reads_regime_cap_when_fills_are_untagged(env, monkeypatch):
    """计划内成交不注解 (2026-08-06 规则) → fill 上没有任何标签。cap 来源本来
    就是 regime (分类轴退役后唯一来源), 预算行不依赖 fill 标签;
    未注解的 fill 也要计入 deployed —— 漏掉它们会把预算算成还有余额。"""
    monkeypatch.setattr(pb, "campaign_fills", lambda t: [
        {"qty": 2, "price": 98.7},      # 计划内, 无标签
        {"qty": 2, "price": 97.8},
    ])
    item = {"ticker": "INTC", "qty": 4.0, "current_price": 99.81, "is_option": False}
    bud = pb.position_budget("INTC", item, nav=21469.0,
                             cfg={"current_regime": {"name": "reclaim_98",
                                                     "cap_pct_nav": 0.02}})

    assert bud is not None, "预算行整条消失 (2026-08-06 INTC 的病)"
    assert bud["regime_cap_frac"] == 0.02
    assert bud["deployed_usd"] == 393.0        # 两笔未注解 fill 都算进去了
    assert bud["exhausted"] is True


# ── 事件流净持仓 vs broker.qty (安全网, NBIS 源案例) ──────────

def test_qty_desync_catches_what_lot_drift_structurally_cannot(tmp_path, monkeypatch):
    """NBIS 2026-08-12: 券商不再返回它 → broker.qty 停在 1, lots 也是 1 ——
    `lot_drift` 比的正是这两个, 差为 0, **两天零告警**。事件流里那笔 SELL 才是
    唯一说真话的地方。"""
    import json
    ev = tmp_path / "2026-08"; ev.mkdir(parents=True)
    (ev / "trades.jsonl").write_text("\n".join(json.dumps(r) for r in [
        {"ticker": "NBIS", "side": "BUY", "qty": 5, "price": 189.0},
        {"ticker": "NBIS", "side": "SELL", "qty": 6, "price": 185.0},
        {"ticker": "NBIS", "side": "BUY", "qty": 2, "price": 190.0},
        {"ticker": "NBIS", "side": "SELL", "qty": 1, "price": 254.0},   # 平掉最后 1 股
        {"ticker": "INTC", "side": "BUY", "qty": 29, "price": 98.0},
    ]) + "\n", encoding="utf-8")
    pos = tmp_path / "positions.json"
    pos.write_text(json.dumps({"tickers": {
        "NBIS": {"broker": {"source": "moomoo_opend", "qty": 1.0},
                 "lots": {"position": [{"qty": 1, "avg_cost": 189.0}]}},
        "INTC": {"broker": {"source": "moomoo_opend", "qty": 29.0}},
        # 事件流覆盖不到的老仓 (早于  / 手工迁移) —— 跳过, 不是噪音源
        "OLD":  {"broker": {"source": "moomoo_opend", "qty": 3.0}},
        # 别的券商 / synthetic 影子行都不进
        "SIVE": {"broker": {"source": "ibkr_stockholm", "qty": 5.0}},
        "GOOGL": {"broker": {"source": "moomoo_opend", "qty": 0.0, "synthetic": True}},
    }}), encoding="utf-8")
    monkeypatch.setattr(pb, "EVENTS_DIR", tmp_path)
    monkeypatch.setattr(pb, "STATE_POSITIONS_PATH", pos)
    rows = pb.qty_desync()
    assert len(rows) == 1 and rows[0].startswith("NBIS: 事件流净 0 股 != broker 1 股")
    # 对齐之后自动闭嘴 (对账式, 不需要人去清标记)
    pos.write_text(pos.read_text().replace('"qty": 1.0', '"qty": 0.0'), encoding="utf-8")
    assert pb.qty_desync() == []
