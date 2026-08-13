"""today_group_sync v3 纯函数回归: 授权判据 = regime cap 显式>0 / 行使即出组 / 吊销必报 / 整组所有权。不碰 OpenD。

v3 语义 (用户): 判据是**授权状态**(gate 达标 → current_regime.cap_pct_nav > 0),
不是执行时点 —— v2 锚 entry_guard GO verdict, 有 cap 等回踩的票 (WAIT) 整晚不进组, 已废。
2026-08-13 轮语义: 成交**不再**终止授权 (本轮可用 = 名义 − 本轮已投, 从 lots 算);
2026-08-13 二次定版: 额度按**爬过几个给 cap 的 gate**给 (gate1 2.5%/gate2 4%/gate3 5%),
判据 = 卡上的 regime_history 不是 lots; 扣减 = 当前敞口市值; 台账漂移只播报不拦。
v3.1 的 fills 管道已删。"""
import pytest

from quant.sync import today_group_sync as tgs

TODAY = "2026-08-05"


@pytest.fixture(autouse=True)
def no_earnings_today(monkeypatch):
    """本模块钉的是**授权判据**与 grant 的组合层取小, 与财报反应日缩仓无关 ——
    所以给它一份确定的空日历, 别读 live。

    🚨 `grant_actual_cap` 的 `calendar=None` 默认值会去读 live 的
    `market/earnings_calendar.json`, 且 `today=None` 会退到真实 `et_today()`
    (不是本模块那个 `TODAY = `)。于是这三条用例
    (`..._applies_the_portfolio_layer` / `..._probe_stays_flat...` / `today_labels...`)
    的数字, 取决于 MSFT/PRBX/ADDX 那天在不在真实财报日历上 —— 今天不在, 纯属运气。
     三世界扫盘 (真日历 / 今天没人发 / 今天全员发) 把它们抓出来:
    前两个世界全绿, 第三个世界三条同时红。同一颗雷在 test_portfolio_brief_cards
    上已经真炸过一次 (NBIS 8/12 BMO)。
    """
    monkeypatch.setattr(tgs.earnings, "load_calendar",
                        lambda *a, **k: {"as_of": TODAY, "tickers": {}})
    # 同一颗雷的第二发 (实炸): `grant_actual_cap` 还会去读
    # `book.power_room(et_today())` = **live 账户快照 − 当日已成交 BUY**。于是
    # 一轮 heartbeat 在 session 中途落了三笔 COHR 成交 (spent_today → $14,063),
    # 购买力余额掉到 $3,467, 本模块两条钉"轮预算/regime cap 谁 binding"的用例
    # 当场变红 —— 与被测语义毫无关系, 且第二天它自己又会绿。
    # ⑥购买力臂本身在 tests/core/test_book.py (3 条, tmp_path 全隔离) 与
    # tests/core/test_sizing.py 里钉着, 这里钉的是授权判据, 所以给它一个够大的
    # 定值让它不 binding。
    monkeypatch.setattr(tgs.book, "power_room",
                        lambda *a, **k: {"power": 1e9, "room": 1e9, "spent_today": 0.0,
                                         "as_of": TODAY, "implied_cash": 1e9,
                                         "leveraged": False})

POS = {"tickers": {
    "INTC": {"broker": {"qty": 4},          # qty ≡ lots 之和: 对不上是台账守卫的用例, 不是这里的
             "lots": {"position": [
                 {"qty": 2, "avg_cost": 98.7, "entry_period": "2026-08-06"},
                 {"qty": 2, "avg_cost": 97.8, "entry_period": "2026-08-06"}]},
             "agent": {"current_regime": {"name": "B_repair_probe_98", "cap_pct_nav": 0.02, "since": "2026-08-04"},
                       "zones": {"valid_until": ""}}},           # 持仓修复路径达标
    "NBIS": {"broker": {"qty": 1}, "agent": {}},                           # 持仓无 regime → 稳态
    "DEAD": {"broker": {"qty": 0},
             "agent": {"current_regime": {"cap_pct_nav": 0.05}}},           # qty=0 非持仓
}}

WL = {"tickers": {
    "MRVL": {"status": "watching", "moomoo_listed": True,
             "agent": {"current_regime": {"name": "B_repair_probe_189", "cap_pct_nav": 0.02, "since": "2026-08-04"},
                       "zones": {"valid_until": "2026-08-18"}}},           # 达标 + 干净
    "MSFT": {"status": "watching", "moomoo_listed": True,
             "agent": {"current_regime": {"name": "R_突破_473", "cap_pct_nav": 0.05, "since": "2026-08-04"},
                       "zones": {"valid_until": "2026-08-18"}}},           # 达标 + 干净
    "LITE": {"status": "watching", "moomoo_listed": True,
             "agent": {"current_regime": {"name": "B_repair_probe_716", "cap_pct_nav": 0.02},
                       "zones": {"valid_until": None}}},                   # valid_until 缺省 → 不拦
    "AAOI": {"status": "watching", "moomoo_listed": True,
             "agent": {"current_regime": {"name": "常规参与", "cap_pct_nav": 0.05,
                                          "participation": "zone_eligible",
                                          "since": "2026-08-01", "source": "card"},
                       "zones": {"valid_until": "2026-08-14"}}},           # card 源常规参与 (天花板 cap)
    "RPAIR": {"status": "watching", "moomoo_listed": True,
              "agent": {"current_regime": {"name": "修复观察", "cap_pct_nav": 0,
                                           "participation": "gate_only",
                                           "since": "2026-08-01", "source": "card"},
                        "zones": {"valid_until": "2026-08-14",
                                  "reclaim_gates": [{"level": 92.5}, {"level": 98.0}]}}},  # 修复路径 cap 0
    "STUB": {"status": "watching", "moomoo_listed": True,
             "agent": {"zones": None}},                                    # stub 无 zones 无 regime
    "DEXP": {"status": "watching", "moomoo_listed": True,
             "agent": {"current_regime": {"name": "常规参与", "cap_pct_nav": 0.05,
                                          "participation": "zone_eligible",
                                          "since": "2026-07-20", "source": "card"},
                       "zones": {"valid_until": "2026-08-01"}}},           # 够格但 zones 过期
    "OBSV": {"status": "watching", "moomoo_listed": True,
             "agent": {"current_regime": {"name": "observation", "cap_pct_nav": 0},
                       "zones": {"valid_until": "2026-08-14"}}},           # cap 显式 0 = 不授权
    "LOCK": {"status": "watching", "moomoo_listed": True,
             "agent": {"current_regime": {"name": "R_突破_100", "cap_pct_nav": 0.02},
                       "pending_repaint": {"reason": "补画锁"},
                       "zones": {"valid_until": "2026-08-14"}}},           # 达标但锁着
    "EXPD": {"status": "watching", "moomoo_listed": True,
             "agent": {"current_regime": {"name": "R_突破_50", "cap_pct_nav": 0.02},
                       "zones": {"valid_until": "2026-08-01"}}},           # 达标但 zones 过期
    "GONE": {"status": "watching", "moomoo_listed": False,
             "agent": {"current_regime": {"name": "R_突破_10", "cap_pct_nav": 0.02},
                       "zones": {"valid_until": "2026-08-14"}}},           # 移出自选
    "DONE": {"status": "closed",
             "agent": {"current_regime": {"name": "R_突破_20", "cap_pct_nav": 0.02}}},  # 非 watching
}}


def test_membership_is_pure_field_read():
    """regime 统一后零推断: cap>0 进, cap 0 / 无 regime 稳态静默。
     分类轴退役: label 统一 `<name> cap X%NAV`, 无分类措辞。"""
    members, notes = tgs.desired_members(POS, WL, TODAY)
    assert sorted(members) == ["AAOI", "INTC", "LITE", "MRVL", "MSFT"]
    assert "cap 2" in members["MRVL"] and "B_repair_probe_189" in members["MRVL"]
    assert "cap 5" in members["MSFT"]
    assert "常规参与" in members["AAOI"] and "cap 5" in members["AAOI"]
    # 稳态不进也不报: 修复观察 cap 0(RPAIR) / stub / cap 显式 0 / 持仓无 regime
    joined = "\n".join(notes)
    for quiet in ("RPAIR", "STUB", "NBIS", "OBSV", "DEAD", "DONE", "GONE"):
        assert quiet not in joined, quiet


def test_repair_candidate_enters_after_gate_transition():
    """修复观察 cap 0 不进; 同一张卡 gate 达标后 (转移机写 cap>0) → 进。"""
    members, _ = tgs.desired_members(POS, WL, TODAY)
    assert "RPAIR" not in members
    wl2 = {"tickers": {"RPAIR": {
        "status": "watching", "moomoo_listed": True,
        "agent": {"current_regime": {"name": "B_repair_probe_98", "cap_pct_nav": 0.02,
                                     "since": "2026-08-10", "source": "auto"},
                  "zones": {"valid_until": "2026-08-20",
                            "reclaim_gates": [{"level": 92.5}]}}}}}
    members2, _ = tgs.desired_members({"tickers": {}}, wl2, "2026-08-11")
    assert "RPAIR" in members2


def test_capped_but_locked_or_expired_is_reported_not_silent():
    """cap>0 但补画锁/过期 = "授权在路上被拦" — 必须报出来, 不静默丢。"""
    _, notes = tgs.desired_members(POS, WL, TODAY)
    joined = "\n".join(notes)
    assert "LOCK" in joined and "pending_repaint" in joined
    assert "EXPD" in joined and "过期" in joined


def test_closed_or_delisted_capped_cards_never_enter():
    members, _ = tgs.desired_members(POS, WL, TODAY)
    assert "DEAD" not in members                   # qty=0, cap 残留也不算持仓授权
    assert "GONE" not in members                   # 移出自选 = 用户 已表态不跟
    assert "DONE" not in members                   # status ≠ watching


def test_empty_state_means_empty_group():
    members, notes = tgs.desired_members({"tickers": {}}, {"tickers": {}}, TODAY)
    assert members == {} and notes == []           # 空 = 无 regime 授权在生效, 常态


# 结构判别式注入 (真实现要读 kline 缓存 —— 测试不该碰 live 数据)
UP = lambda t, c, bars=None: (False, None)               # noqa: E731  结构干净
DOWN = lambda t, c, bars=None: (True, "结构未修复")       # noqa: E731  结构 VETO


def test_structure_veto_zeroes_the_grant_but_keeps_membership():
    """ 二值裁决 (用户「结构降级反转立马 cap 0 即可」): 结构未修复 →
    实际 cap $0 + veto 标记, membership 不动 (授权状态 ≠ 执行时点, v3.1)。
    旧「降级 probe 2%」中间态作废 —— 同一张卡换个结构结论 = 全额 vs 0。"""
    card = WL["tickers"]["MSFT"]
    up = tgs.grant_actual_cap("MSFT", card, 20000.0, None, QUOTE, veto_of=UP)
    down = tgs.grant_actual_cap("MSFT", card, 20000.0, None, QUOTE, veto_of=DOWN)
    assert up["cap_usd"] == 1000.0 and not up["structure_veto"]
    assert down["cap_usd"] == 0.0 and down["structure_veto"]
    assert down["binding"] == "structure_veto"
    assert "未修复" in down["structure_veto_why"]


def test_missing_klines_veto_not_full_grant():
    """无 K 线 = 结构判不了 → **VETO 不 grant**。判别式本身对空结构不 VETO
    (语义 = "没有降级理由"), entry_guard 有 NEEDS_CARD 挡在它前面, 这里没有 ——
    照搬就成了"缓存缺失 = 全额放行"的 fail-open (旧「无K线→probe」的二值版)。"""
    vetoed, why = tgs.structure_veto_of("NOBARS", WL["tickers"]["AAOI"], lambda t: [])
    assert vetoed is True and "无 K 线" in why


QUOTE = lambda t: (3.0, 100.0)      # noqa: E731  低波动 → 冲击预算不 binding


def test_grant_actual_cap_applies_the_portfolio_layer():
    """名义分数 → 实际 cap (用户): 组合层的冲击预算/gross 余额也算数。"""
    card = WL["tickers"]["MSFT"]                          # 机器源 cap 5%
    free = tgs.grant_actual_cap("MSFT", card, 20000.0, None, QUOTE, veto_of=UP)
    assert free["binding"] == "regime" and free["cap_usd"] == 1000.0
    tight = tgs.grant_actual_cap("MSFT", card, 20000.0,
                                 {"used_budget": 0.14, "used_gross": 1.09}, QUOTE,
                                 veto_of=UP)
    assert tight["binding"] == "gross"                    # 余 1% NAV < 名义 5%
    assert tight["cap_usd"] == pytest.approx(200.0)
    assert tgs.grant_actual_cap("RPAIR", WL["tickers"]["RPAIR"], 20000.0,
                                None, QUOTE, veto_of=UP) is None   # 无授权仍然是 None


def test_grant_shrinks_on_the_earnings_reaction_day():
    """反应日当天, 手机备注上那个数也要跟着缩 —— 响铃那一刻能用的是**那一天**
    的钱 (用户 : 备注上的数必须是算出来的, 不是抄卡片百分比)。

    这条行为此前**零测试**, 所以它才能悄悄改掉隔壁三条用例的数字, 而红的时候
    没有任何一条断言指向真凶。现在两个世界各钉一条。
    """
    from quant.core.earnings import shrink_ratio
    card = WL["tickers"]["MSFT"]
    flat = tgs.grant_actual_cap("MSFT", card, 20000.0, None, QUOTE, today=TODAY,
                                calendar={"as_of": TODAY, "tickers": {}}, veto_of=UP)
    shrunk = tgs.grant_actual_cap(
        "MSFT", card, 20000.0, None, QUOTE, today=TODAY, veto_of=UP,
        calendar={"as_of": TODAY, "tickers": {"MSFT": {"next_reaction_day": TODAY,
                                                       "x_earn": 5.563, "n_prior": 19}}})

    assert flat["cap_usd"] == 1000.0
    assert shrunk["cap_usd"] == pytest.approx(1000.0 * shrink_ratio(5.563), abs=0.01)
    assert shrunk["cap_usd"] < flat["cap_usd"]                 # 只收紧, 永不放大


def test_today_labels_carry_the_actual_cap_and_flag_zero_room():
    """进组票的 label 要带实际 cap; 余额被吃光时必须说"进组≠下得了单"
    —— 组里躺着一张读起来很宽的卡而钱早没了, 正是"标签一套真值一套"。"""
    members, _ = tgs.desired_members(POS, WL, TODAY)
    notes = tgs.annotate_actual_caps(dict(members) and members, POS, WL, 20000.0,
                                     {"used_budget": 0.0, "used_gross": 0.20}, QUOTE,
                                     veto_of=UP)
    assert "实际 $1,000" in members["MSFT"] and notes == []
    drained = {"MSFT": "x"}
    notes2 = tgs.annotate_actual_caps(drained, POS, WL, 20000.0,
                                      {"used_budget": 0.0, "used_gross": 1.10}, QUOTE,
                                      veto_of=UP)
    assert "gross 余额 binding" in drained["MSFT"]
    assert any("进组≠下得了单" in n for n in notes2)


def test_settled_lots_keep_membership_after_a_fill():
    """ 轮语义 (用户「将已有仓位并入 R1 仍算 R1」): 成交**不再**终止
    授权 —— 本轮可用 = 本轮名义 − 本轮已投, 打满显 $0 等下一个 gate。INTC 持仓,
    lots 已落账且与 broker 对得上 → 留组。v3.1「成交即行使出组」退役。"""
    members, notes = tgs.desired_members(POS, WL, TODAY)
    assert "INTC" in members
    assert not any("INTC" in n for n in notes)


def _ledger_doc(qty, lots_rows):
    return {"tickers": {"UNBK": {
        "broker": {"qty": qty},
        "lots": {"position": lots_rows},
        "agent": {"current_regime": {"name": "R_突破_50", "cap_pct_nav": 0.4,
                                     "since": "2026-08-04"},
                  "zones": {"valid_until": "2026-08-20"}}}}}


def test_ledger_drift_is_reported_but_never_blocks():
    """台账漂移 **只播报不拦**(二次定版)。

    它当初拦人的理由是「lots 记的比券商少 ⇒ 本轮已投低估 ⇒ 给全额 = fail-open」。
    那条理由随扣减口径一起消失了: 额度现在减的是 `book.exposure_of` =
    **broker.qty × 现价**, 根本不读 lots —— 而 `lot_gap > 0` 的字面意思恰恰是
    "broker 已经反映了这笔成交、只是 lots 还没落账", 也就是敞口**已经**被正确扣掉。
    继续拦 = 拿参与权去赌 lot_reconcile 什么时候跑完, 且告警里写着一句不成立的话。
    """
    for qty, lots in ((4, []),                                            # broker 多
                      (1, [{"qty": 3, "avg_cost": 50.0,
                            "entry_period": "2026-08-05"}])):             # lots 多
        members, notes = tgs.desired_members(_ledger_doc(qty, lots), {"tickers": {}}, TODAY)
        assert "UNBK" in members, (qty, lots)
        assert any("UNBK" in n and "INFRA:lot_drift" in n for n in notes)
        assert not any("已投低估" in n for n in notes), "别再说那句已经不成立的话"


def test_closed_then_readded_candidate_is_admitted(monkeypatch):
    """用户  裁: 平仓 = campaign 结束, 回来的候选卡就是普通候选,
    下一笔是**新 campaign 的 R1**→ 照常进组。

    AAOI 实况 (8/12 平仓 → 8/13 watchlist_sync 自动回候选并原样搬回卡):
    positions 留一张 qty=0 的 closed 卡 (lots 已归档进 exit_info.closed_lots),
    watchlist 有带 cap 的carried 卡。旧判据把"归档后 lots 空"读成"成交没落账",
    于是 entry_guard 给 PLAN(R1) 而白名单说没授权 —— 同一张卡两个数。"""
    pos = {"tickers": {"AAOI": {
        "broker": {"qty": 0}, "lots": {"base": [], "position": []},
        "exit_info": {"closed_at": "2026-08-12T13:29:54-04:00",
                      "closed_lots": {"position": [{"qty": 4, "avg_cost": 124.0}]}},
        "agent": {"current_regime": {"name": "B_repair_probe_120.5",
                                     "cap_pct_nav": 0.4, "since": "2026-08-05"}}}}}
    wl = {"tickers": {"AAOI": {
        "status": "watching", "moomoo_listed": True, "broker": None,
        "readd_from_position": {"closed_at": "2026-08-12T13:29:54-04:00"},
        "agent": {"current_regime": {"name": "B_repair_probe_120.5", "cap_pct_nav": 0.4,
                                     "participation": "zone_eligible",
                                     "since": "2026-08-05", "source": "auto"},
                  "zones": {"accumulation_zone_1": [120.9, 124.8],
                            "valid_until": "2026-08-20"}}}}}
    members, notes = tgs.desired_members(pos, wl, TODAY)
    assert "AAOI" in members
    assert not any("待落账" in n or "未落账" in n for n in notes)
    # 而且档位回到第一档 —— `gate_count` 的参与流锚点含 exit_info.closed_at,
    # 与 entry_guard 读的是同一个函数 (不再有"域特判")
    from quant.core.sizing import gate_count
    assert gate_count(pos["tickers"]["AAOI"]) == 1


def test_gate_count_is_the_shared_judgment_for_both_sides():
    """判据 = 卡上的 gate 转移史 (`sizing.gate_count`), 入场侧与加仓侧同一个函数。
     二次定版: lots 推轮号的两个近似退役 (数不出第三档 / 没买过整档丢失)。"""
    from quant.core.sizing import gate_count
    up = lambda d: {"date": d, "direction": "up", "cap_pct_nav": 0.40}   # noqa: E731
    assert gate_count({"agent": {"regime_history": [up("2026-08-04")]}}) == 1
    assert gate_count({"agent": {"regime_history": [up("2026-08-04"),
                                                    up("2026-08-12")]}}) == 2
    import quant.core.sizing as _s
    for dead in ("round_no_from_lots_since", "campaign_round_no", "lots_deployed_since"):
        assert not hasattr(_s, dead), f"{dead} 该退役 —— 别再从成交记录里猜过没过 gate"


def test_entry_cap_exposure_reduces_the_budget_and_never_advances_the_gate():
    """同一个 gate 内可分多笔加 (用户: 「gate1 的 cap 给了 8k, 第一笔加了 4000,
    第二次余额显示只剩 4000」): ② = 本档上限 − 当前敞口, **不自动进档**。"""
    from quant.core import sizing
    FULL_A = {"current_regime": {"name": "x", "cap_pct_nav": 0.40}}
    common = dict(atr=3.0, price=100.0, stop=75.0, gate_no=1)
    fresh = sizing.entry_cap(20000.0, FULL_A, exposure=0.0, **common)
    part = sizing.entry_cap(20000.0, FULL_A, exposure=500.0, **common)
    maxed = sizing.entry_cap(20000.0, FULL_A, exposure=99999.0, **common)
    assert fresh["n_unit_cap"] == pytest.approx(2000.0)          # gate1 = 2.5% 名义
    assert part["n_unit_cap"] == pytest.approx(1500.0)           # 上限 − 敞口
    assert part["gate_no"] == 1                                  # 还在第一档, 不进位
    assert maxed["n_unit_cap"] == 0.0 and maxed["final"] == 0.0  # 打满 = 停


def test_no_fills_plumbing_left_in_the_authorization_path():
    """源码棘轮: v3.1「成交即行使」的 fills 管道 2026-08-13 整条删除。

    语义  就退役了, 但那套管道作为台账守卫的判据又多活了一天并制造了
    AAOI 那次误判 —— 死掉的语义留着活的实现, 下一个读代码的人会以为它还是规矩。
    授权路径上的判据只剩 lots (轮预算) 与 lot_gap (台账), 不再读事件流。"""
    from pathlib import Path
    for mod in ("today_group_sync", "price_reminder_sync"):
        src = (Path(tgs.__file__).parent / f"{mod}.py").read_text(encoding="utf-8")
        code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
        head, _, body = code.partition('"""')
        body = body.partition('"""')[2] or code          # 掐掉模块 docstring 里的历史注
        assert "buy_fill_dates" not in body, f"{mod}.py 还在用 fills 管道"
        assert "iter_events" not in body, f"{mod}.py 还在读事件流判授权"


def test_plan_ops_full_group_ownership():
    """Today 是授权白名单不是收藏夹: 手动加的票(含非美股)一律被对账清掉。"""
    adds, dels = tgs.plan_ops(
        ["US.MRVL", "US.MSFT"],
        ["US.MSFT", "US.NVDA", "HK.00700"],        # NVDA/腾讯 = 手动加的
)
    assert adds == ["US.MRVL"]
    assert dels == ["HK.00700", "US.NVDA"]


def test_plan_ops_empty_desired_clears_group():
    adds, dels = tgs.plan_ops([], ["US.MU", "US.INTC"])
    assert adds == [] and dels == ["US.INTC", "US.MU"]


# ── 期权选约进组 (用户 : 重画授权时合约一并加到 Today) ──

def _opt_plan(expiry="2027-01-15"):
    return {"MSFT": {"code": "MSFT270115C460000", "strike": 460.0, "expiry": expiry,
                     "tiers": [{"premium": 55.0, "contracts": 1}]}}


def _authorized_card():
    return {"status": "watching", "moomoo_listed": True, "agent": {
        "zones": {"accumulation_zone_1": [430.0, 445.0], "valid_until": "2099-01-01"},
        "current_regime": {"name": "常规参与", "cap_pct_nav": 0.40,
                           "participation": "zone_eligible", "since": "2026-08-01",
                           "source": "card"}}}


def test_option_contract_joins_when_underlying_authorized():
    from quant.sync.today_group_sync import desired_members
    members, _ = desired_members({"tickers": {}}, {"tickers": {"MSFT": _authorized_card()}},
                                 "2026-08-10", option_plans=_opt_plan())
    assert "MSFT" in members
    assert "MSFT270115C460000" in members, "授权正股的选约合约必须一并进组"
    assert "1 张" in members["MSFT270115C460000"]


def test_option_contract_stays_out_without_underlying_grant():
    """正股无授权 (不在 members) → 合约不进 — 选约文件可能滞后一晚, 组语义以授权为准。"""
    from quant.sync.today_group_sync import desired_members
    members, _ = desired_members({"tickers": {}}, {"tickers": {}}, "2026-08-10",
                                 option_plans=_opt_plan())
    assert members == {}


def test_expired_contract_not_added():
    from quant.sync.today_group_sync import desired_members
    members, notes = desired_members({"tickers": {}},
                                     {"tickers": {"MSFT": _authorized_card()}},
                                     "2026-08-10",
                                     option_plans=_opt_plan(expiry="2026-08-01"))
    assert "MSFT270115C460000" not in members
    assert any("已到期" in n for n in notes)


# ── 加仓侧轮预算封顶 (risk_allocation , 三轮 50/30/20 of T=5%) ──────────

def _pos_card(cap, lots_dates):
    return {"broker": {"qty": len(lots_dates) or 1},
            "agent": {"current_regime": {"name": "促升", "cap_pct_nav": cap,
                                         "since": "2026-08-01"},
                      "zones": {"valid_until": "2026-08-30"}},
            "lots": {"base": [], "position": [
                {"qty": 1, "avg_cost": 100.0, "entry_period": d} for d in lots_dates]}}


def _wide_stop(card, stop=75.0):
    """给卡一条真 stop (价 100 → 距 25%), 让 ② 轮预算臂咬得过 40% 的 regime cap ——
    否则低波动口径下 ② 恒被 regime 先咬, 测不到本节要测的东西。"""
    card["agent"]["zones"]["stop_level"] = stop
    return card


def _gates(card, n, caps=None):
    """给卡 n 条 gate 转移史; caps 可逐条指定授予的 cap (None = 全部 0.40)。"""
    caps = caps or [0.40] * n
    card["agent"]["regime_history"] = [
        {"date": f"2026-08-{3 + i:02d}", "direction": "up", "cap_pct_nav": caps[i]}
        for i in range(n)]
    return card


def test_grant_first_gate_deducts_current_exposure():
    """第一档 = 2.5% NAV risk, 减当前敞口 (市值口径, 不是 lots 成本)。"""
    card = _gates(_wide_stop(_pos_card(0.40, ["2026-08-01 09:30 ET"])), 1)
    g = tgs.grant_actual_cap("ADDX", card, 20000.0, None, QUOTE, veto_of=UP,
                             exposure_of=lambda t: 100.0)         # 1 股 @ 100 市值
    assert g["gate_no"] == 1
    # gate1 = 2.5%: 20000×0.025×100/25 = 2,000; −敞口 100 = 1,900 (regime 臂 7,900)
    assert g["binding"] == "n_unit" and g["cap_usd"] == pytest.approx(1900.0)


def test_grant_second_gate_unlocks_the_cumulative_cap():
    """用户 :「gate2 max 4%」—— 爬到第二道线拿的是**累计**4%,
    不是"第二轮的 1.5%"。判据是转移史, 与买没买过无关 (LITE 形状)。"""
    card = _gates(_wide_stop(_pos_card(0.40, ["2026-08-01"])), 2)
    g = tgs.grant_actual_cap("ADDX", card, 20000.0, None, QUOTE, veto_of=UP,
                             exposure_of=lambda t: 100.0)
    assert g["gate_no"] == 2
    assert tgs.binding_label(g) == "gate2 预算上限"            # 标签跟着语义换
    assert g["binding"] == "n_unit"
    assert g["cap_usd"] == pytest.approx(20000 * 0.040 / 25 * 100 - 100)   # $3,100
    # 旧"每轮独立"规则会给 1.5% = $1,200 —— 结转正是这两者的差
    assert g["cap_usd"] > 20000 * 0.015 / 25 * 100


def test_grant_cap_zero_gate_does_not_advance_the_step():
    """用户 :「不是任何一个 gate 都算次数, 只有给 cap 的 gate 才开始算」
    —— 落在 cap 0 的中间站不吃掉一档预算。"""
    card = _gates(_wide_stop(_pos_card(0.40, ["2026-08-01"])), 2, caps=[0.0, 0.40])
    g = tgs.grant_actual_cap("ADDX", card, 20000.0, None, QUOTE, veto_of=UP,
                             exposure_of=lambda t: 100.0)
    assert g["gate_no"] == 1
    assert g["cap_usd"] == pytest.approx(20000 * 0.025 / 25 * 100 - 100)   # $1,900


def test_grant_budget_maxed_is_zero_not_advance():
    """敞口吃光本档预算 → $0 等下一个 gate, 不自动进档。"""
    card = _gates(_wide_stop(_pos_card(0.40, ["2026-08-01"])), 1)
    g = tgs.grant_actual_cap("EXHX", card, 20000.0, None, QUOTE, veto_of=UP,
                             exposure_of=lambda t: 2500.0)    # > gate1 名义 2,000
    assert g["gate_no"] == 1 and g["binding"] == "n_unit" and g["n_unit_cap"] == 0.0
    wl = tgs.grant_actual_cap("MSFT", WL["tickers"]["MSFT"], 20000.0, None, QUOTE,
                              veto_of=UP, exposure_of=lambda t: 0.0)
    assert wl["binding"] == "regime" and wl["cap_usd"] == 1000.0   # watchlist 原路
