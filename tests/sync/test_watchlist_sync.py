"""watchlist_sync 对账回归: 已持仓自动移出分组 () / 平仓自动加回
(2026-08-13) / stub / drift。不碰 OpenD。"""
import json

import pytest

from quant.sync import watchlist_sync as ws


class _NullLock:
    def __enter__(self): return None
    def __exit__(self, *exc): return False


def _card(*, itype="stock", source="moomoo_opend", qty=0.0, status="closed"):
    """schema 合法的最小卡 —— stamp_readd 写 positions 走真校验器, 半张卡整轮
    盖章都会被拒 (与 write_positions 的 all-or-nothing 同语义)。"""
    return {
        "instrument": {"type": itype},
        "broker": {"source": source, "qty": qty},
        "status": status,
        "agent": stub_agent(),
        "lots": {"base": [], "position": []},
        "position_summary": {},
        "pending_orders": [],
    }


def analysis_agent():
    """一张画过的卡的 agent 块 (AAOI 8/6 那版的形状)。"""
    return {
        "thesis": {"summary": "800G 光模块需求", "status": "intact",
                   "last_reviewed": "2026-08-06T23:16:55Z"},
        "zones": {"accumulation_zone_1": [120.9, 124.8],
                  "accumulation_zone_2": [113.1, 120.9],
                  "trim_zone": [136.5, 140.4], "no_chase_above": 127.0,
                  "stop_level": 112.35, "valid_until": "2026-08-20",
                  "anchored_on": {"date": "2026-08-06", "basis": "pivot ×4 + HVN"}},
        "current_regime": {"name": "B_repair_probe_120.5", "cap_pct_nav": 0.4,
                           "participation": "zone_eligible", "since": "2026-08-05",
                           "source": "auto"},
        "action": {"intent": "hold", "reason": "持 4 股 @124.00"},
        "regime_history": [{"name": "B_repair_probe_120.5", "at": "2026-08-05"}],
    }


def stub_agent():
    return {"thesis": {"summary": "[stub] x", "status": "needs_card"}, "zones": None,
            "action": {"intent": "needs_card", "reason": "x"}}


def closed_card(closed_at, *, marker=None, itype="stock", source="moomoo_opend",
                qty=0.0, exit_price=None, pnl=None, agent=None):
    card = _card(itype=itype, source=source, qty=qty)
    if agent is not None:
        card["agent"] = agent
    exit_info = {}
    if closed_at is not None:
        exit_info = {"closed_at": closed_at, "exit_price": exit_price,
                     "realized_pnl_usd": pnl, "reconciled_by": "close_reconcile"}
    if marker is not None:
        exit_info[ws.READD_MARKER] = {"closed_at": marker, "at": "x", "group": "Watchlist"}
    if exit_info:
        card["exit_info"] = exit_info
    return card


@pytest.fixture
def env(tmp_path, monkeypatch):
    """moomoo 分组内容 + 两份状态文件都可注入; 两个分组写操作只记录不联网。"""
    calls = {"removed": [], "added": []}

    def _setup(group_rows, watchlist_tickers, held_tickers, positions=None):
        wl = tmp_path / "watchlist.json"
        pos = tmp_path / "positions.json"
        tickers = {t: _card(qty=1.0, status="active") for t in held_tickers}
        tickers.update(positions or {})
        wl.write_text(json.dumps({"schema_version": 2, "tickers": watchlist_tickers}))
        pos.write_text(json.dumps({"tickers": tickers}))
        monkeypatch.setattr(ws, "WATCHLIST_PATH", wl)
        monkeypatch.setattr(ws, "POSITIONS_PATH", pos)
        monkeypatch.setattr(ws, "state_lock", lambda *a, **k: _NullLock())
        monkeypatch.setattr(ws, "fetch_moomoo_group", lambda g: group_rows)
        monkeypatch.setattr(ws, "remove_from_group",
                            lambda g, codes: calls["removed"].append((g, codes)))
        monkeypatch.setattr(ws, "add_to_group",
                            lambda g, codes: calls["added"].append((g, codes)))
        return calls, wl, pos
    return _setup


def _row(code, stype="STOCK"):
    return {"code": code, "stock_type": stype}


def test_held_tickers_are_removed_from_group(env):
    """成交 → 不再是入场候选 → 移出分组; 未持仓的照常留组建 stub。"""
    calls, _, _ = env([_row("US.NBIS"), _row("US.GOOGL")], {}, held_tickers=["NBIS"])
    out = ws.sync("Watchlist")
    assert calls["removed"] == [("Watchlist", ["US.NBIS"])]
    assert out["removed_held"] == ["NBIS"]
    assert out["skipped_holdings"] == []          # 移除成功 → 不再列 skipped
    assert out["added_stubs"] == ["GOOGL"]        # 候选照常 stub


def test_remove_failure_degrades_to_report_not_abort(env, monkeypatch):
    """DEL 失败不阻塞对账 (下轮幂等重试): 票留在 skipped_holdings, 错误显式报。"""
    _, _, _ = env([_row("US.NBIS")], {}, held_tickers=["NBIS"])
    monkeypatch.setattr(ws, "remove_from_group",
                        lambda g, codes: (_ for _ in ()).throw(RuntimeError("api down")))
    out = ws.sync("Watchlist")
    assert out["removed_held"] == []
    assert out["skipped_holdings"] == ["NBIS"]
    assert any("api down" in e for e in out["remove_errors"])


def test_no_held_tickers_means_no_group_write(env):
    """系统对 用户的分组只有两个写操作 (移出持仓票 / 加回平仓票) — 两件都没
    发生时一次都不该调, 其余 membership 归他的手。"""
    calls, _, _ = env([_row("US.GOOGL")], {}, held_tickers=["MSFT"])   # MSFT 不在组里
    ws.sync("Watchlist")
    assert calls["removed"] == []
    assert calls["added"] == []


def test_drift_marking_survives_alongside_removal(env):
    """原有对账语义不被移除逻辑破坏: 系统有卡 + moomoo 无 → moomoo_listed=false。"""
    card = {"status": "watching", "moomoo_listed": True,
            "agent": {"thesis": {"summary": "x"},
                      "current_regime": {"name": "常规参与", "cap_pct_nav": 0.05,
                                         "participation": "zone_eligible",
                                         "since": "2026-08-01", "source": "card"},
                      "zones": {"accumulation_zone_1": [1.0, 2.0]}}}
    _, wl, _ = env([_row("US.NBIS")], {"GONE": card}, held_tickers=["NBIS"])
    out = ws.sync("Watchlist")
    doc = json.loads(wl.read_text())
    assert doc["tickers"]["GONE"]["moomoo_listed"] is False
    assert out["drift_system_only"] == ["GONE"]


# ── : 出组 op 必须 MOVE_OUT, 不是 DEL ────────────────────
#
# 实测 (祭品 US.F 在 Optical+Energy 双组): DEL 的 group 参数是摆设 — 从自选
# 整体删除, 所有分组连带消失; MOVE_OUT 才是"只出这个组"。源案例: MRVL 8/5
# 成交后被 remove_from_group DEL, 用户 自建的 Optical 组连带清空。
# 系统对分组的授权只有自己管的两个 (Watchlist/Today), 其他组一个字段不许碰。

def test_remove_from_group_uses_move_out_not_del(monkeypatch):
    """穿透到 moomoo ctx 层: 记录真实传给 modify_user_security 的 op。"""
    import sys
    import types
    from moomoo import ModifyUserSecurityOp

    recorded = []

    class _Ctx:
        def modify_user_security(self, group, op, codes):
            recorded.append((group, op, codes))
            return 0, None

    fake_common = types.ModuleType("common")
    fake_common.create_quote_context = lambda: _Ctx()
    fake_common.safe_close = lambda ctx: None
    fake_common.RET_OK = 0
    monkeypatch.setitem(sys.modules, "common", fake_common)
    monkeypatch.setattr(ws, "add_openapi_path", lambda: None)

    ws.remove_from_group("Watchlist", ["US.MRVL"])

    assert recorded == [("Watchlist", ModifyUserSecurityOp.MOVE_OUT, ["US.MRVL"])]


def test_no_del_op_token_in_group_sync_sources():
    """源码棘轮: 两个分组同步模块永不引用 ModifyUserSecurityOp.DEL。
    (DEL 的唯一合法用途是"从自选整体删除" — 本系统没有这个业务。)"""
    from pathlib import Path
    for mod in ("watchlist_sync", "today_group_sync"):
        src = (Path(ws.__file__).parent / f"{mod}.py").read_text(encoding="utf-8")
        assert "ModifyUserSecurityOp.DEL" not in src, f"{mod}.py 引用了整删语义的 DEL"


# ──  用户: 平仓 → 自动加回自选分组 ──────────────────────
#
# 推翻 08-05 的后半句 ("平仓后想继续跟 = App 手动加回")。默认从"平了就没了"
# 改成"平了回候选池"; 僵尸候选靠 exit_info.watchlist_readd 盖章防 —— 加回
# **一次**, 之后 用户 删掉它系统不再端回来。

def test_closed_ticker_is_added_back_with_its_card_verbatim(env):
    """平仓卡不在组里 → ADD + 当轮建卡 + 盖章 (三件缺一个都不算接上), 且卡是
    **原样搬**不是空 stub (用户 : "卡的话原样保留即可 待过期重画")。"""
    agent = analysis_agent()
    calls, wl, pos = env([_row("US.NVDA")], {}, held_tickers=["NVDA"],
                         positions={"AAOI": closed_card("2026-08-12T13:29:54-04:00",
                                                        exit_price=140.0, pnl=64.0,
                                                        agent=agent)})
    out = ws.sync("Watchlist")

    assert calls["added"] == [("Watchlist", ["US.AAOI"])]
    assert out["readded_closed"] == ["AAOI"] and out["readd_stamped"] == ["AAOI"]
    assert out["carried_from_closed"] == ["AAOI"] and out["added_stubs"] == []

    card = json.loads(wl.read_text())["tickers"]["AAOI"]
    assert card["status"] == "watching" and card["moomoo_listed"] is True
    assert card["agent"] == agent          # 逐字 —— zones/regime/thesis 一个字段都没动
    # 尤其这三个: 授权跟着图走 / 漂移时钟不归零 / 锚不被"刷新"成今天
    assert card["agent"]["current_regime"]["cap_pct_nav"] == 0.4
    assert card["agent"]["thesis"]["last_reviewed"] == "2026-08-06T23:16:55Z"
    assert card["agent"]["zones"]["anchored_on"]["date"] == "2026-08-06"
    assert card["readd_from_position"]["exit_price"] == 140.0   # 来历只写在信封层
    marker = json.loads(pos.read_text())["tickers"]["AAOI"]["exit_info"][ws.READD_MARKER]
    assert marker["closed_at"] == "2026-08-12T13:29:54-04:00"


def test_carried_card_is_a_copy_not_a_reference():
    """搬过去的是 deepcopy 不是同一个对象 —— 两张卡此后各自演化 (候选卡会被
    重画, positions 那张是带 lots/exit_info 的归档, 是 cost basis 的家)。
    直接测纯函数: 走 sync() 的话两份 doc 各自读盘, 别名根本照不出来。"""
    agent = analysis_agent()
    card = ws.carried_card("AAOI", "2026-08-13T00:00:00Z",
                           {"agent": agent, "exit_info": {"closed_at": "x"}})
    card["agent"]["zones"]["stop_level"] = 999
    card["agent"]["thesis"]["summary"] = "改过了"
    assert agent["zones"]["stop_level"] == 112.35
    assert agent["thesis"]["summary"] == "800G 光模块需求"


def test_existing_stub_is_upgraded_by_the_closed_card(env):
    """AAOI 8/13 实况: 昨天先落了一张空 stub, 今天平仓卡的分析补上来。
    卡内容是**对账**不是一次性事件 —— 与 membership 盖章分开, 所以补得上。"""
    calls, wl, _ = env([_row("US.AAOI")], {"AAOI": {"status": "watching",
                                                    "moomoo_listed": True,
                                                    "agent": stub_agent()}},
                       held_tickers=[],
                       positions={"AAOI": closed_card("2026-08-12T13:29:54-04:00",
                                                      marker="2026-08-12T13:29:54-04:00",
                                                      agent=analysis_agent())})
    out = ws.sync("Watchlist")
    assert out["carried_from_closed"] == ["AAOI"]
    assert calls["added"] == []             # 已盖章 → membership 不再动
    card = json.loads(wl.read_text())["tickers"]["AAOI"]
    assert card["agent"]["zones"]["accumulation_zone_1"] == [120.9, 124.8]


def test_existing_zoned_card_is_never_overwritten(env):
    """已有 zones 的候选卡永不被平仓卡盖掉 (促升守卫 2026-08-11 的反方向同款):
    它可能比平仓那张更新, 覆盖 = 拿旧图换新图。"""
    live = {"status": "watching", "moomoo_listed": True,
            "agent": {"thesis": {"summary": "现役分析", "status": "intact"},
                      "current_regime": {"name": "常规参与", "cap_pct_nav": 0.4,
                                         "participation": "zone_eligible",
                                         "since": "2026-08-11", "source": "card"},
                      "zones": {"accumulation_zone_1": [200.0, 210.0]}}}
    _, wl, _ = env([_row("US.MU")], {"MU": live}, held_tickers=[],
                   positions={"MU": closed_card("2026-07-29T15:51:55-04:00",
                                                agent=analysis_agent())})
    out = ws.sync("Watchlist")
    assert out["carried_from_closed"] == []
    card = json.loads(wl.read_text())["tickers"]["MU"]
    assert card["agent"]["zones"]["accumulation_zone_1"] == [200.0, 210.0]
    assert card["agent"]["thesis"]["summary"] == "现役分析"


def test_closed_card_without_analysis_falls_back_to_stub(env):
    """平仓卡自己就是空的 (促升 stub 没建过卡) → 没什么可搬, 回落 stub。"""
    _, wl, _ = env([_row("US.NVDA")], {}, held_tickers=["NVDA"],
                   positions={"AAOI": closed_card("2026-08-12T13:29:54-04:00",
                                                  exit_price=140.0, pnl=64.0)})
    out = ws.sync("Watchlist")
    assert out["carried_from_closed"] == [] and out["added_stubs"] == ["AAOI"]
    summary = json.loads(wl.read_text())["tickers"]["AAOI"]["agent"]["thesis"]["summary"]
    assert "平仓后自动回入场候选" in summary and "@ 140" in summary


def test_uncarriable_card_degrades_to_stub_not_a_dead_sync(env):
    """搬不动的卡 (校验不过) 只报错回落 stub —— 一张坏卡不许炸掉整轮对账。"""
    broken = analysis_agent()
    broken["current_regime"]["since"] = "八月五号"      # 非 YYYY-MM-DD
    _, wl, _ = env([_row("US.NVDA")], {}, held_tickers=["NVDA"],
                   positions={"AAOI": closed_card("2026-08-12T13:29:54-04:00",
                                                  agent=broken)})
    out = ws.sync("Watchlist")
    assert out["carried_from_closed"] == [] and out["added_stubs"] == ["AAOI"]
    assert any("AAOI" in e for e in out["carry_errors"])


def test_manual_readd_is_stamped_without_api_call(env):
    """用户 手动加回过 (AAOI  实况) → 不重复调 API, **但照样盖章**:
    不盖的话他之后删掉它, 下一轮系统就会端回来 = 删不掉的僵尸候选。"""
    calls, _, pos = env([_row("US.AAOI")], {}, held_tickers=[],
                        positions={"AAOI": closed_card("2026-08-12T13:29:54-04:00")})
    out = ws.sync("Watchlist")

    assert calls["added"] == []
    assert out["readd_already_listed"] == ["AAOI"] and out["readd_stamped"] == ["AAOI"]
    assert ws.READD_MARKER in json.loads(pos.read_text())["tickers"]["AAOI"]["exit_info"]


def test_stamped_close_is_never_readded_again(env):
    """盖过章 = 这次平仓的加回额度用完。用户 从组里删掉它, 系统不许再加。"""
    calls, _, _ = env([_row("US.NVDA")], {}, held_tickers=["NVDA"],
                      positions={"AAOI": closed_card("2026-08-12T13:29:54-04:00",
                                                     marker="2026-08-12T13:29:54-04:00")})
    out = ws.sync("Watchlist")
    assert calls["added"] == [] and out["readded_closed"] == []


def test_reentry_then_new_close_readds_again(env):
    """重进后再平 = 新的 closed_at ≠ 旧盖章 → 再加回一次 (每次平仓一次额度)。"""
    calls, _, _ = env([_row("US.NVDA")], {}, held_tickers=["NVDA"],
                      positions={"AAOI": closed_card("2026-09-20T10:00:00-04:00",
                                                     marker="2026-08-12T13:29:54-04:00")})
    out = ws.sync("Watchlist")
    assert calls["added"] == [("Watchlist", ["US.AAOI"])]
    assert out["readded_closed"] == ["AAOI"]


def test_no_recorded_close_means_no_readd(env):
    """触发器是**平仓记录**(exit_info.closed_at), 不是"这张卡看起来平了"。

    两种形状都不许加回:
      · 老卡 —— close_reconcile () 之前平的, 整个 exit_info 都没有。
        上线首日不会把 NOK/LITE/DRAM/RKLB/AXTI 端回自选, 也就不需要 backfill。
      · closed_at=None —— close_reconcile 找不到对应 episode 时写出的形状
        (它同时写 note 要人工查 events/)。带着旧盖章时尤其要挡: 盖章比对
        None≠旧值 会让一张"账本对不上"的卡被当成新平仓加回。
    """
    legacy = closed_card(None)
    assert "exit_info" not in legacy
    unknown = _card()
    unknown["exit_info"] = {"closed_at": None, "note": "事件流里没有对应的已平 episode",
                            ws.READD_MARKER: {"closed_at": "2026-07-01T10:00:00-04:00"}}
    plan = ws.readd_plan({"tickers": {"NOK": legacy, "RKLB": unknown}},
                         held=set(), in_group=set())
    assert plan == []


def test_option_ibkr_and_synthetic_cards_never_readd():
    """加回的域 = held_bare 那一类 (裸正股)。期权 OCC code 不是自选成员; 持有
    AAOI call 本来就不会让 AAOI 出组, 所以平掉它也没有"加回"这回事。"""
    cards = {
        "AAOI270617C140000": closed_card("2026-08-12T00:00:00-04:00", itype="option"),
        "SIVE.ST": closed_card("2026-08-12T00:00:00-04:00", source="ibkr_stockholm"),
    }
    synth = closed_card("2026-08-12T00:00:00-04:00")
    synth["broker"]["synthetic"] = True
    cards["GOOGL"] = synth
    assert ws.readd_plan({"tickers": cards}, held=set(), in_group=set()) == []


def test_held_ticker_with_stale_exit_info_is_not_readded():
    """INTC 实况: 平过一次又重进, 卡上留着旧 exit_info 而现在 qty=4 —— 仍持有
    的票不是入场候选, 判据是 held_bare 不是 exit_info 在不在。"""
    card = closed_card("2026-08-03T08:15:50-04:00", qty=4.0)
    plan = ws.readd_plan({"tickers": {"INTC": card}}, held={"INTC"}, in_group=set())
    assert plan == []


def test_add_failure_leaves_no_stamp_for_retry(env, monkeypatch):
    """ADD 失败 → 不盖章 (盖了就永远加不回来了), 显式报错, 下轮幂等重试。
    对账不被阻塞: 同轮的 stub/drift 照常完成。"""
    _, wl, pos = env([_row("US.GOOGL")], {}, held_tickers=[],
                     positions={"AAOI": closed_card("2026-08-12T13:29:54-04:00")})
    monkeypatch.setattr(ws, "add_to_group",
                        lambda g, codes: (_ for _ in ()).throw(RuntimeError("api down")))
    out = ws.sync("Watchlist")

    assert out["readded_closed"] == [] and out["readd_stamped"] == []
    assert any("api down" in e for e in out["readd_errors"])
    assert ws.READD_MARKER not in json.loads(pos.read_text())["tickers"]["AAOI"]["exit_info"]
    assert out["added_stubs"] == ["GOOGL"]        # 其余对账照跑


def test_add_to_group_uses_add_op(monkeypatch):
    """穿透到 moomoo ctx 层 (MOVE_OUT 那条测试的镜像): 加回用 ADD —— 入指定组,
    不碰 用户 其他分组。"""
    import sys
    import types
    from moomoo import ModifyUserSecurityOp

    recorded = []

    class _Ctx:
        def modify_user_security(self, group, op, codes):
            recorded.append((group, op, codes))
            return 0, None

    fake_common = types.ModuleType("common")
    fake_common.create_quote_context = lambda: _Ctx()
    fake_common.safe_close = lambda ctx: None
    fake_common.RET_OK = 0
    monkeypatch.setitem(sys.modules, "common", fake_common)
    monkeypatch.setattr(ws, "add_openapi_path", lambda: None)

    ws.add_to_group("Watchlist", ["US.AAOI"])

    assert recorded == [("Watchlist", ModifyUserSecurityOp.ADD, ["US.AAOI"])]


def test_etf_in_group_is_a_traded_stub_stamped_as_fund(env):
    """ETF 作为交易标的进 Watchlist 组 = 照常建 stub (type 仍 stock, 写卡 /
    受控重画路径只认它), 另盖 security_type —— 财报日历 / 公司画像 / 估值据此跳过。"""
    _, wl, _ = env([_row("US.ETFX", "ETF"), _row("US.NVDA")], {}, held_tickers=[])
    out = ws.sync("Watchlist")
    cards = json.loads(wl.read_text())["tickers"]
    assert out["added_stubs"] == ["ETFX", "NVDA"] and out["fund_stamped"] == ["ETFX"]
    assert cards["ETFX"]["instrument"] == {"type": "stock", "security_type": "ETF"}
    assert cards["ETFX"]["agent"]["action"]["intent"] == "needs_card"
    assert cards["NVDA"]["instrument"] == {"type": "stock"}            # 正股卡零 diff
    assert ws.sync("Watchlist")["fund_stamped"] == []                  # 幂等


def test_fund_stamp_is_restored_on_a_card_carried_back_from_a_close(env):
    """carried_card 换信封会把 instrument 重置成 {type: stock} —— 章由每轮对账补回。"""
    pos = {"ETFX": closed_card("2026-10-20T15:00:00-04:00", agent=analysis_agent())}
    _, wl, _ = env([_row("US.ETFX", "ETF")], {}, held_tickers=[], positions=pos)
    out = ws.sync("Watchlist")
    assert out["carried_from_closed"] == ["ETFX"] and out["fund_stamped"] == ["ETFX"]
    assert json.loads(wl.read_text())["tickers"]["ETFX"]["instrument"]["security_type"] == "ETF"
