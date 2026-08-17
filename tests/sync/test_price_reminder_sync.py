"""price_reminder_sync 纯函数回归: 期望集构造 / 对账 diff / 所有权边界。不碰 OpenD。"""
import pytest

from quant.sync import price_reminder_sync as prs
from quant.sync import today_group_sync as tgs

TODAY = "2026-08-05"


@pytest.fixture(autouse=True)
def pin_external_capacity_inputs(monkeypatch):
    """Keep reminder tests independent from live calendar and account snapshots."""
    monkeypatch.setattr(
        tgs.earnings, "load_calendar",
        lambda *args, **kwargs: {"as_of": TODAY, "tickers": {}},
    )
    monkeypatch.setattr(
        tgs.book, "power_room",
        lambda *args, **kwargs: {
            "power": 1e9, "room": 1e9, "spent_today": 0.0,
            "as_of": TODAY, "implied_cash": 1e9, "leveraged": False,
        },
    )


POS = {"tickers": {
    "MU": {"broker": {"qty": 5},
           "agent": {"zones": {"stop_level": 800.0, "trim_zone": [1000.0, 1100.0]},
                     "next_regime": {"entry_gate": {"level": 1050.0, "days": 2}}}},
    "NBIS": {"broker": {"qty": 0}, "agent": {"zones": {"stop_level": 175.0}}},              # 平仓票
    "AAOI270617C140000": {"broker": {"qty": 2}, "agent": {"zones": {"stop_level": 1.0}}},   # OCC 期权
}}

WL = {"tickers": {
    "INTC": {"moomoo_listed": True, "agent": {"zones": {
        "accumulation_zone_1": [79.6, 84.0], "valid_until": "2026-08-14",
        "reclaim_gates": [{"level": 88.5}, {"level": 92.5}, {"level": 98.0}]}}},
    "LITE": {"agent": {"zones": {"accumulation_zone_1": [700.0, 716.0],
                                 "valid_until": "2026-08-01",
                                 "reclaim_gates": [{"level": 716.0}]}}},                    # 过期卡
    "MU": {"agent": {"zones": {"valid_until": "2026-08-14",
                               "reclaim_gates": [{"level": 900.0}]}}},                      # 已是持仓
    "QQQ": {"agent": {"zones": None}},                                                      # 无 zone
    "MSFT": {"moomoo_listed": False,
             "agent": {"zones": {"accumulation_zone_1": [1.0, 2.0],
                                 "valid_until": "2026-08-14"}}},                            # 移出自选
}}


def test_desired_lines_positions_and_watchlist():
    lines, notes = prs.desired_lines(POS, WL, TODAY)
    by = {(d["ticker"], d["kind"], d["value"]): d["type"] for d in lines}
    assert by[("MU", "stop", 800.0)] == "PRICE_DOWN"
    assert by[("MU", "trim", 1000.0)] == "PRICE_UP"
    assert by[("MU", "gate", 1050.0)] == "PRICE_UP"     # 突破线 (next_regime 入口) 也有线
    assert by[("INTC", "gate", 98.0)] == "PRICE_UP"
    assert by[("INTC", "z1", 84.0)] == "PRICE_DOWN"
    tickers = {d["ticker"] for d in lines}
    assert "NBIS" not in tickers                       # qty=0 不是持仓 (判据 broker.qty)
    assert "AAOI270617C140000" not in tickers          # 合约无期权基候选 → 无合约线
    assert by[("AAOI", "stop", 1.0)] == "PRICE_DOWN"   # 期权卡 underlying 基 stop → 正股线
    assert "MSFT" not in tickers                       # moomoo_listed=False
    assert "LITE" not in tickers                       # 过期卡跳过 …
    assert any("LITE" in n for n in notes)             # … 且必须说出来, 不静默
    mu_kinds = {d["kind"] for d in lines if d["ticker"] == "MU"}
    assert mu_kinds == {"stop", "trim", "gate"}        # watchlist 的 MU 卡不重复进; gate = 突破线


def test_notes_are_owned_vocabulary():
    """note = 词表本身 (cc: 前缀已废): 裸 kind / stop:来源 / z1:预算,
    全部能被 _owned_note 认领; 自定义文字/空 = 手动。"""
    lines, _ = prs.desired_lines(POS, WL, TODAY)
    assert lines and all(prs._owned_note(d["note"]) for d in lines)
    bare = {d["note"] for d in lines
            if not (d["note"].startswith("stop:") or d["note"].startswith("gate:"))}
    assert bare <= set(prs.KINDS)
    assert {d["note"] for d in lines if d["kind"] == "stop"} == {"stop:card"}  # 无 floors → 卡 stop binding
    for owned in ("stop", "stop:card", "stop:lock", "stop:trail", "g01", "cc:z1"):
        assert prs._owned_note(owned), owned               # 裸 stop = 旧词, 认旧不写旧
    for manual in ("", "手动", "z1手动", "我的线", "nan", "stopX"):
        assert not prs._owned_note(manual), manual
    assert len("stop:trail") <= 10 and len("stop:lock") <= 10  # moomoo note 上限 10 字符


def test_z1_note_carries_budget_for_today_members():
    """Today 白名单成员的 z1 备注带比例与起步预算: 响铃即见
    "这轮能下多少"; 非成员保持裸 z1 = 无授权观察线。"""
    lines, _ = prs.desired_lines(POS, WL, TODAY, budgets={"INTC": "2%:450"})
    notes = {(d["ticker"], d["kind"]): d["note"] for d in lines}
    assert notes[("INTC", "z1")] == "z1:2%:450"
    assert notes[("INTC", "gate")] == "gate"               # 预算只上 z1, gate 不动
    assert all(prs._owned_note(n) for n in notes.values())
    # 不传 budgets (NAV 缺失兜底) → 全裸线, 老行为
    lines2, _ = prs.desired_lines(POS, WL, TODAY)
    assert {d["note"] for d in lines2 if d["kind"] == "z1"} == {"z1"}


def test_budget_format_stays_within_moomoo_note_limit():
    """moomoo note 上限 10 字符; "z1:" 占 3 → 预算段 ≤7。比例+数值,
    金额 round $50 防 NAV churn, ≥$1000 转 k。"""
    assert prs._fmt_budget(0.02, 22405) == "2%:450"
    assert prs._fmt_budget(0.05, 22500) == "5%:1.1k"       # ≥$1000 转 k (用户)
    assert prs._fmt_budget(0.05, 300000) == "5%:15k"       # ≥$10k 整 k
    assert prs._fmt_budget(0.02, 1000) == "2%:50"          # 金额地板 $50
    for frac, nav in ((0.02, 22405), (0.05, 22500), (0.05, 300000),
                      (0.10, 22500), (0.15, 66000), (0.349, 21237), (0.40, 21237)):
        assert len("z1:" + prs._fmt_budget(frac, nav)) <= 10, (frac, nav)


def test_budget_format_degrades_precision_before_dropping_the_money():
    """超 7 字符时先降 k 的精度, **最后**才丢金额 ()。

    响铃那一刻能用的是钱, 比例只是它的来历。旧写法一超限就退成纯比例,
    恰好在金额位数变大 (= 额度变大) 时把唯一可执行的那个数扔掉。
    """
    assert prs._fmt_budget(0.10, 22500) == "10%:2k"        # "10%:2.2k"(8) → 降精度
    assert prs._fmt_budget(0.15, 66000) == "15%:10k"       # "15%:9.9k"(8) → 10k
    assert prs._fmt_budget(0.349, 21237) == "35%:7k"       # gross binding 的实际 cap


def test_budget_format_zero_room_is_not_papered_over_with_a_floor():
    """组合层余额用尽 = 真的 $0。$50 地板是防 rounding, 不是防真相。"""
    assert prs._fmt_budget(0.0, 21237) == "0%:0"


def test_sub_one_percent_is_marked_not_rounded_to_zero():
    """财报反应日缩仓后 2%×0.24 = 0.47% —— 四舍五入成 "0%" 会和后面的金额
    自相矛盾 ("0%:100" 读起来像坏了)。<1% 用明确记号。"""
    s = prs._fmt_budget(0.0047, 21237)
    assert s == "<1%:100" and len("z1:" + s) <= 10


def test_plan_ops_note_change_swaps_line():
    """价位同而 note 不同 (预算变了/行使出组/legacy cc: 迁移) → DEL+ADD 换血,
    不算 kept — note 是对账对象, 否则备注落后就永远落后。"""
    desired = [
        {"code": "US.INTC", "ticker": "INTC", "kind": "z1", "type": "PRICE_DOWN",
         "value": 84.0, "note": "z1:2%:450"},
        {"code": "US.MU", "ticker": "MU", "kind": "stop", "type": "PRICE_DOWN",
         "value": 800.0, "note": "stop"},
    ]
    current = [
        {"code": "US.INTC", "reminder_type": "PRICE_DOWN", "value": 84.0,
         "note": "z1", "key": 7},                          # 预算新出现 → 换血
        {"code": "US.MU", "reminder_type": "PRICE_DOWN", "value": 800.0,
         "note": "cc:stop", "key": 8},                     # legacy 前缀 → 一轮迁移
    ]
    adds, dels, kept, kept_manual = prs.plan_ops(desired, current)
    assert sorted(d["note"] for d in adds) == ["stop", "z1:2%:450"]
    assert sorted(r["key"] for r in dels) == [7, 8]
    assert kept == 0 and kept_manual == 0


QUOTE = lambda t: (3.0, 100.0)      # noqa: E731  低波动 → 冲击预算不 binding


UP = lambda t, c, bars=None: (False, None)               # noqa: E731  结构干净
DOWN = lambda t, c, bars=None: (True, "结构未修复")       # noqa: E731  结构 VETO


def test_z1_budgets_uses_actual_cap_and_nav():
    """z1_budgets: Today 成员 × **实际 cap**× NAV; NAV 缺失 → 空 dict (裸线兜底)。
     分类轴退役: cap 唯一来源 = regime; 结构 VETO (与 entry_guard 同一
    判别式) → 该票预算 $0 —— 备注上的数必须等于入场计划上的数。"""
    wl = {"tickers": {
        "AAOI": {"status": "watching", "moomoo_listed": True,
                 "agent": {"current_regime": {"name": "常规参与", "cap_pct_nav": 0.05,
                                              "since": "2026-08-01", "source": "card"},
                           "zones": {"valid_until": "2026-08-14"}}},
        "MSFT": {"status": "watching", "moomoo_listed": True,
                 "agent": {"current_regime": {"name": "R_突破_473", "cap_pct_nav": 0.05,
                                              "since": "2026-08-04"},
                           "zones": {"valid_until": "2026-08-18"}}},
        "OBSV": {"status": "watching", "moomoo_listed": True,
                 "agent": {"current_regime": {"name": "修复观察", "cap_pct_nav": 0},
                           "zones": {"valid_until": "2026-08-14"}}},
    }}
    # 结构干净 → 两张卡各自 regime cap 5% binding
    out = prs.z1_budgets({"tickers": {}}, wl, TODAY, nav=22500.0, quote_of=QUOTE,
                         veto_of=UP)
    assert out == {"AAOI": "5%:1.1k", "MSFT": "5%:1.1k"}   # ≥$1k 转 k
    # 结构未修复 → VETO, 实际 cap $0 (二值世界, 无 2% 打折中间态)
    down = prs.z1_budgets({"tickers": {}}, wl, TODAY, nav=22500.0, quote_of=QUOTE,
                          veto_of=DOWN)
    assert down == {"AAOI": "0%:0", "MSFT": "0%:0"}
    assert prs.z1_budgets({"tickers": {}}, wl, TODAY, nav=None, veto_of=UP) == {}


def test_z1_budgets_closed_position_card_does_not_shadow_watchlist():
    """Positions 有 closed 残卡 (qty 0 无 regime) 时,
    授权真源是 watchlist 卡 — 卡源判别式 = live_tickers (qty), 不是 dict 有无 key。
    修前: 死卡遮真源 → 组里有授权、备注却裸线。"""
    pos = {"tickers": {"AAOI": {"status": "closed", "broker": {"qty": 0}, "agent": {}}}}
    wl = {"tickers": {
        "AAOI": {"status": "watching", "moomoo_listed": True,
                 "agent": {"current_regime": {"name": "B_repair_probe_120.5",
                                              "cap_pct_nav": 0.02, "since": "2026-08-05"},
                           "zones": {"valid_until": "2026-08-13"}}}}}
    assert prs.z1_budgets(pos, wl, TODAY, nav=22500.0, quote_of=QUOTE,
                          veto_of=UP) == {"AAOI": "2%:450"}


def test_z1_budget_is_the_actual_cap_not_the_card_percentage():
    """用户 : 「2%/40% 是默认值, 实际 cap 要靠 NAV 15% 冲击预算与
    gross 110% 的余额算出来」—— 手机上响铃看到的必须是**算出来的那个数**。

    这里 gross 已用 75%, 余 35% → 一张 40% 的卡备注写 35%, 不是 40%。

    ⚠️ `veto_of` 必须注入: 不注入 → `structure_veto_of` 去读 `market/klines/MSFT.jsonl`
    (**gitignored 且每天变**), 本例就变成"MSFT 今天的真实结构说了算"。
    分类轴退役接线后当场红: MSFT 真实 trend=transition 且 close 492.43 < lsh 513.73
    → VETO → `0%:0`。本用例问的是**组合层余额算术**, 结构那一维要钉死。
    """
    wl = {"tickers": {"MSFT": {
        "status": "watching", "moomoo_listed": True,
        "agent": {"current_regime": {"name": "R_突破_473", "cap_pct_nav": 0.40,
                                     "since": "2026-08-04"},
                  "zones": {"valid_until": "2026-08-18"}}}}}
    full = prs.z1_budgets({"tickers": {}}, wl, TODAY, nav=21237.0, quote_of=QUOTE,
                          veto_of=UP)
    assert full == {"MSFT": "40%:8k"}                      # 空书 → 名义 = 实际 ($8,500)
    squeezed = prs.z1_budgets({"tickers": {}}, wl, TODAY, nav=21237.0,
                              usage={"used_budget": 0.06, "used_gross": 0.75},
                              quote_of=QUOTE, veto_of=UP)
    assert squeezed == {"MSFT": "35%:7k"}                  # gross 余额 binding


def test_per_ticker_cap_keeps_priority_kinds():
    wl = {"tickers": {"XX": {"agent": {"zones": {
        "valid_until": "2099-01-01",
        "reclaim_gates": [{"level": float(i)} for i in range(1, 8)],   # 7 道 gate
        "accumulation_zone_1": [0.5, 0.9]}}}}}
    lines, notes = prs.desired_lines({"tickers": {}}, wl, TODAY)
    assert len(lines) == prs.MAX_PER_TICKER
    assert all(d["kind"] == "gate" for d in lines)     # gate 优先级高于 z1 → z1 被丢
    assert any("上限" in n for n in notes)


def test_plan_ops_add_del_keep_and_manual_untouched():
    desired = [
        {"code": "US.MU", "ticker": "MU", "kind": "stop", "type": "PRICE_DOWN",
         "value": 800.0, "note": "stop"},
        {"code": "US.INTC", "ticker": "INTC", "kind": "gate", "type": "PRICE_UP",
         "value": 98.0, "note": "gate"},
        {"code": "US.INTC", "ticker": "INTC", "kind": "z1", "type": "PRICE_DOWN",
         "value": 84.0, "note": "z1"},
    ]
    current = [
        {"code": "US.MU", "reminder_type": "PRICE_DOWN", "value": 800.0,
         "note": "stop", "key": 1},                                        # 不变
        {"code": "US.MRVL", "reminder_type": "PRICE_UP", "value": 189.0,
         "note": "gate", "key": 2},                                        # 离开期望 → 删
        {"code": "US.INTC", "reminder_type": "PRICE_UP", "value": 98.0,
         "note": "", "key": 3},                                            # 用户 手动同价 → 不加不删
        {"code": "US.NVDA", "reminder_type": "PRICE_DOWN", "value": 150.0,
         "note": "手动", "key": 4},                                        # 手动 → 永不删
    ]
    adds, dels, kept, kept_manual = prs.plan_ops(desired, current)
    assert [d["value"] for d in adds] == [84.0]
    assert [r["key"] for r in dels] == [2]
    assert kept == 1 and kept_manual == 1


def test_orient_sides_never_drops_a_line():
    desired = [
        {"code": "US.INTC", "ticker": "INTC", "kind": "gate", "type": "PRICE_UP",
         "value": 92.5, "note": "gate"},
        {"code": "US.INTC", "ticker": "INTC", "kind": "z1", "type": "PRICE_DOWN",
         "value": 84.0, "note": "z1"},
        {"code": "US.NBIS", "ticker": "NBIS", "kind": "trim", "type": "PRICE_UP",
         "value": 226.8, "note": "trim"},
    ]
    kept, notes = prs.orient_sides(desired, {"US.INTC": 100.0, "US.NBIS": 217.0})
    assert len(kept) == len(desired)
    assert [(d["ticker"], d["kind"], d["type"]) for d in kept] == [
        ("INTC", "gate", "PRICE_DOWN"),
        ("INTC", "z1", "PRICE_DOWN"),
        ("NBIS", "trim", "PRICE_UP"),
    ]
    assert any("翻向" in note for note in notes)
    assert desired[0]["type"] == "PRICE_UP"


def test_orient_sides_passes_through_when_price_missing():
    desired = [{"code": "US.X", "ticker": "X", "kind": "gate", "type": "PRICE_UP",
                "value": 10.0, "note": "gate"}]
    kept, notes = prs.orient_sides(desired, {})
    assert kept == desired and notes == []


def test_both_gates_are_drawn_in_both_buckets():
    """current gate + next gate 常驻线 (用户)。立此条前 current gate
    **根本没有线**—— 代码只画 reclaim 梯与 next gate。"""
    pos = {"tickers": {"NBIS": {"broker": {"qty": 1}, "agent": {
        "zones": {"stop_level": 175.0},
        "last_regime": {"repair_gate": {"level": 175.1}},
        "next_regime": {"entry_gate": {"level": 231.0}}}}}}
    wl = {"tickers": {"NVDA": {"agent": {
        "zones": {"valid_until": "2099-01-01", "accumulation_zone_1": [209.0, 212.8]},
        "last_regime": {"repair_gate": {"level": 204.0}},
        "next_regime": {"entry_gate": {"level": 228.0}}}}}}
    lines, _ = prs.desired_lines(pos, wl, TODAY)
    got = {(d["ticker"], d["note"]): d["value"] for d in lines}
    assert got[("NBIS", "gate:cur")] == 175.1 and got[("NBIS", "gate:next")] == 231.0
    assert got[("NVDA", "gate:cur")] == 204.0 and got[("NVDA", "gate:next")] == 228.0
    assert all(d["pin"] for d in lines if d["note"].startswith("gate:"))
    # 备注必须能被认领, 否则对账把它当手动线 → 永不删 + 每轮重复添加
    assert all(prs._owned_note(d["note"]) for d in lines)
    assert all(len(d["note"]) <= 10 for d in lines)      # moomoo note 上限


def test_card_source_regime_has_no_current_gate():
    """🚨 用户  (NBIS 源案例): card 源 regime = 卡被**重画/促升**放在这里,
    没爬过任何 gate。它携带的 last_regime 是一个从未经转移离开的旧观察态, 其
    repair_gate = 破位线 = 8/06 裁决保持不动的 **stop 本身**—— 画出来是 stop 的
    复制品不是转移轴。auto/demoted 源才是真被转移送上来的(regime 就叫这个名字)。"""
    def card(source, repair, stop):
        return {"broker": {"qty": 1}, "agent": {
            "zones": {"stop_level": stop},
            "current_regime": {"name": "x", "source": source, "cap_pct_nav": 0},
            "last_regime": {"repair_gate": {"level": repair}},
            "next_regime": {"entry_gate": {"level": 231.0}}}}

    pos = {"tickers": {"NBIS": card("card", 175.1, 175.1),      # 促升/重画 → 无 cur
                       "NVDA": card("auto", 204.0, 200.15)}}    # 转移收复 → 有 cur
    lines, _ = prs.desired_lines(pos, {"tickers": {}}, TODAY)
    got = {(d["ticker"], d["note"]) for d in lines if d["note"].startswith("gate:")}
    assert got == {("NBIS", "gate:next"), ("NVDA", "gate:cur"), ("NVDA", "gate:next")}
    # NBIS 那个价位仍有线 —— 是 stop, 本来就该是 stop
    assert ("NBIS", "stop:card") in {(d["ticker"], d["note"]) for d in lines}


def test_current_gate_falls_back_to_regime_history():
    """老卡没有 last_regime.repair_gate → 退同一次转移的 regime_history 记录。"""
    wl = {"tickers": {"X": {"agent": {
        "zones": {"valid_until": "2099-01-01"},
        "regime_history": [{"date": "2026-07-01", "level": 90.0},
                           {"date": "2026-08-05", "level": 98.0}]}}}}
    lines, _ = prs.desired_lines({"tickers": {}}, wl, TODAY)
    assert [(d["note"], d["value"]) for d in lines] == [("gate:cur", 98.0)]


def test_all_gate_lines_flip_direction_instead_of_vanishing():
    """Pinned and ordinary gate lines both remain visible after a crossing."""
    desired = [
        {"code": "US.NVDA", "ticker": "NVDA", "kind": "gate", "type": "PRICE_UP",
         "value": 204.0, "note": "gate:cur", "pin": True},    # 现价 220 上方 → 失守线
        {"code": "US.NVDA", "ticker": "NVDA", "kind": "gate", "type": "PRICE_UP",
         "value": 228.0, "note": "gate:next", "pin": True},   # 现价下方 → 突破线不变
        {"code": "US.NVDA", "ticker": "NVDA", "kind": "gate", "type": "PRICE_UP",
         "value": 196.0, "note": "gate"},
    ]
    kept, notes = prs.orient_sides(desired, {"US.NVDA": 220.0})
    assert [(d["note"], d["type"]) for d in kept] == [
        ("gate:cur", "PRICE_DOWN"), ("gate:next", "PRICE_UP"),
        ("gate", "PRICE_DOWN")]
    assert any("翻向" in n for n in notes)
    assert desired[0]["type"] == "PRICE_UP"          # 入参不被就地改写

    # If market falls below every level, all reminders point upward.
    kept2, _ = prs.orient_sides(desired, {"US.NVDA": 190.0})
    assert {(d["note"], d["type"]) for d in kept2} == {
        ("gate:cur", "PRICE_UP"), ("gate:next", "PRICE_UP"), ("gate", "PRICE_UP")}

    # If market rises above every level, all reminders point downward.
    kept3, _ = prs.orient_sides(desired, {"US.NVDA": 240.0})
    assert [(d["note"], d["type"]) for d in kept3] == [
        ("gate:cur", "PRICE_DOWN"), ("gate:next", "PRICE_DOWN"),
        ("gate", "PRICE_DOWN")]


def test_pinned_gates_survive_the_per_ticker_cap():
    """上限只裁可裁的线 —— pin 不占名额, 也不被丢。"""
    wl = {"tickers": {"XX": {"agent": {
        "zones": {"valid_until": "2099-01-01",
                  "reclaim_gates": [{"level": float(i)} for i in range(1, 9)],
                  "accumulation_zone_1": [0.5, 0.9]},
        "last_regime": {"repair_gate": {"level": 100.0}},
        "next_regime": {"entry_gate": {"level": 200.0}}}}}}
    lines, notes = prs.desired_lines({"tickers": {}}, wl, TODAY)
    notes_got = [d["note"] for d in lines]
    assert "gate:cur" in notes_got and "gate:next" in notes_got
    assert sum(1 for n in notes_got if not n.startswith("gate:")) == prs.MAX_PER_TICKER
    assert len(lines) == prs.MAX_PER_TICKER + 2 <= 10      # moomoo 硬上限内, 留手动余量
    assert any("上限" in n for n in notes)


def test_pinned_gate_wins_dedup_against_same_priced_reclaim_rung():
    """同价时留下带 gate:cur/next 备注的那条 —— 否则常驻线被裸 gate 顶掉,
    pin 与备注一起丢失 (又一例"字段在转换中被丢")。"""
    wl = {"tickers": {"LITE": {"agent": {
        "zones": {"valid_until": "2099-01-01", "reclaim_gates": [{"level": 716.0}]},
        "last_regime": {"repair_gate": {"level": 716.0}}}}}}
    lines, _ = prs.desired_lines({"tickers": {}}, wl, TODAY)
    assert [(d["note"], d.get("pin")) for d in lines] == [("gate:cur", True)]


def test_collapse_same_price_keeps_the_higher_priority_kind_and_says_so():
    """翻向后 gate:cur 可能与卡 stop 撞价同向 (NBIS 实况 175.1)。此前 plan_ops 的
    dict 是后者覆盖 → 低优先级的 gate 反把 stop 顶掉且一声不响。"""
    desired = [
        {"code": "US.NBIS", "ticker": "NBIS", "kind": "stop", "type": "PRICE_DOWN",
         "value": 175.1, "note": "stop:card"},
        {"code": "US.NBIS", "ticker": "NBIS", "kind": "gate", "type": "PRICE_DOWN",
         "value": 175.1, "note": "gate:cur", "pin": True},
        {"code": "US.NBIS", "ticker": "NBIS", "kind": "gate", "type": "PRICE_UP",
         "value": 231.0, "note": "gate:next", "pin": True},     # 不同方向 → 不撞
    ]
    kept, notes = prs.collapse_same_price(desired)
    assert [d["note"] for d in kept] == ["stop:card", "gate:next"]
    assert any("同价同向" in n and "该价位仍有线" in n for n in notes)

    # 顺序颠倒结果相同 (不是"谁在前谁赢")
    kept2, _ = prs.collapse_same_price([desired[1], desired[0]])
    assert [d["note"] for d in kept2] == ["stop:card"]


def test_plan_ops_tolerates_stringly_typed_api_values():
    desired = [{"code": "US.A", "ticker": "A", "kind": "stop", "type": "PRICE_DOWN",
                "value": 93.9, "note": "stop"}]
    current = [{"code": "US.A", "reminder_type": "PRICE_DOWN", "value": "93.90",
                "note": "stop", "key": 9}]
    adds, dels, kept, _ = prs.plan_ops(desired, current)
    assert not adds and not dels and kept == 1


# ── 下方线 = 三者 max + binding 来源 (用户 裁决) ──────────

def test_stop_line_is_max_of_three_with_binding_note():
    """max(卡 stop, 棘轮地板, trail), note = binding 来源;
    同价并列 → 静态源优先 (card > lock > trail)。"""
    pos = {"tickers": {"MU": {"broker": {"qty": 5},
                              "agent": {"zones": {"stop_level": 800.0}}}}}

    def stop_of(floors):
        lines, _ = prs.desired_lines(pos, {"tickers": {}}, TODAY, floors=floors)
        (d,) = [x for x in lines if x["kind"] == "stop"]
        return d["value"], d["note"]

    assert stop_of(None) == (800.0, "stop:card")
    assert stop_of({"MU": {"lock": 850.0, "trail": 830.0}}) == (850.0, "stop:lock")
    assert stop_of({"MU": {"lock": 810.0, "trail": 880.5}}) == (880.5, "stop:trail")
    assert stop_of({"MU": {"lock": None, "trail": 800.0}}) == (800.0, "stop:card")


def test_option_position_splits_bases_underlying_vs_contract():
    """期权持仓 basis 分开 (不同基价格不可比): trail(underlying 基) + underlying
    卡 stop → 正股 code; 棘轮地板(期权 mark 基) → 合约 code; g01 = lot cost 下一档。"""
    occ = "GOOGL270115C390000"
    pos = {"tickers": {occ: {
        "broker": {"qty": 1, "current_price": 40.0},
        "lots": {"base": [], "position": [{"qty": 1, "avg_cost": 33.0}]},
        "agent": {"zones": {"stop_level": 380.0, "stop_level_basis": "underlying"}}}}}
    lines, _ = prs.desired_lines(pos, {"tickers": {}}, TODAY,
                                 floors={occ: {"lock": 45.0, "trail": 390.0}})
    by = {(d["ticker"], d["kind"]): d for d in lines}
    und = by[("GOOGL", "stop")]
    assert (und["code"], und["value"], und["note"]) == ("US.GOOGL", 390.0, "stop:trail")
    ct = by[(occ, "stop")]
    assert (ct["code"], ct["value"], ct["note"]) == (f"US.{occ}", 45.0, "stop:lock")
    g01 = by[(occ, "g01")]
    assert (g01["type"], g01["value"], g01["note"]) == ("PRICE_UP", 66.0, "g01")


def test_option_price_basis_card_stop_stays_on_contract():
    occ = "AAOI270617C140000"
    pos = {"tickers": {occ: {"broker": {"qty": 2, "current_price": 30.0},
                             "agent": {"zones": {"stop_level": 50.0,
                                                 "stop_level_basis": "option_price"}}}}}
    lines, _ = prs.desired_lines(pos, {"tickers": {}}, TODAY,
                                 floors={occ: {"lock": 42.0, "trail": None}})
    by = {(d["ticker"], d["kind"]): d for d in lines}
    ct = by[(occ, "stop")]
    assert (ct["value"], ct["note"]) == (50.0, "stop:card")   # 同基内 max(50, 42)
    assert ("AAOI", "stop") not in by                          # underlying 无候选 → 无正股线


def test_g01_next_tier_math():
    lots = {"base": [], "position": [{"qty": 1, "avg_cost": 33.0}]}
    assert prs.g01_next_tier(lots, 40.0) == 66.0     # +100%
    assert prs.g01_next_tier(lots, 70.0) == 99.0     # +200%
    assert prs.g01_next_tier(lots, None) == 66.0     # mark 缺失 → 最低档
    assert prs.g01_next_tier(lots, 140.0) is None    # 已过 +300% (132)
    multi = {"base": [{"qty": 1, "avg_cost": 20.0}],
             "position": [{"qty": 1, "avg_cost": 33.0}]}
    assert prs.g01_next_tier(multi, 41.0) == 60.0    # min(lot20 的 60, lot33 的 66)
    assert prs.g01_next_tier({}, 40.0) is None


def test_exit_floor_map_reads_state_and_filters_dead():
    """floors 单一真源 = exit_guards.json 已算好的值; 已平仓残留按 live 过滤。"""
    guards = {"as_of": "2026-08-05T00:00:00Z", "positions": {
        "MU": {"ratchet": {"floor": 850.0}, "trail": {"trail": 830.0}},
        "NBIS": {"ratchet": {"floor": 200.0}, "trail": {"trail": 190.0}},   # 已平仓残留
        "SPCX": {"ratchet": {"floor": None}, "trail": {}},                  # 无线
    }}
    pos = {"tickers": {"MU": {"broker": {"qty": 5}}, "SPCX": {"broker": {"qty": 1}},
                       "NBIS": {"broker": {"qty": 0}}}}
    assert prs.exit_floor_map(guards, pos) == {"MU": {"lock": 850.0, "trail": 830.0}}
    assert prs.exit_floor_map(None, pos) == {}


def test_plan_ops_migrates_bare_stop_note():
    """裸 "stop" =  前旧词: 同价位 note 换血成 stop:type, 一轮迁移。"""
    desired = [{"code": "US.AAOI", "ticker": "AAOI", "kind": "stop", "type": "PRICE_DOWN",
                "value": 112.35, "note": "stop:card"}]
    current = [{"code": "US.AAOI", "reminder_type": "PRICE_DOWN", "value": 112.35,
                "note": "stop", "key": 11}]
    adds, dels, kept, _ = prs.plan_ops(desired, current)
    assert [d["note"] for d in adds] == ["stop:card"]
    assert [r["key"] for r in dels] == [11]
    assert kept == 0


# ── 持仓票 z1 回踩线 (补洞, 用户 点名) ────────────────

def _held_with_z1(cap=True):
    positions = {"tickers": {"AAOI": {
        "broker": {"qty": 4, "current_price": 135.0},
        "agent": {"zones": {"accumulation_zone_1": [120.9, 124.8],
                            "stop_level": 112.0},
                  "current_regime": {"name": "B_repair_probe_120.5",
                                     "cap_pct_nav": 0.02, "since": "2026-08-08",
                                     "source": "auto"}}}}}
    budgets = {"AAOI": "2%:450"} if cap else {}
    return positions, budgets


def test_held_today_member_gets_z1_line_with_budget():
    """结构洞的修复本体: z1_budgets 为持仓成员算了预算, desired_lines 必须有线可挂。
    (此前 watchlist 分支 `t in live: continue` 把持仓跳过 —— 预算算了没线挂。)"""
    positions, budgets = _held_with_z1(cap=True)
    lines, _ = prs.desired_lines(positions, {"tickers": {}}, "2026-08-09",
                                 budgets=budgets)
    z1 = [d for d in lines if d["kind"] == "z1"]
    assert len(z1) == 1 and z1[0]["ticker"] == "AAOI"
    assert z1[0]["value"] == 124.8 and z1[0]["type"] == "PRICE_DOWN"
    assert z1[0]["note"] == "z1:2%:450", "持仓成员的 z1 必须带预算, 不是裸线"


def test_held_without_grant_gets_no_z1_line_not_even_bare():
    """无授权的持仓**不画裸 z1**(与 watchlist 的裸观察线有意不同):
    cap 0 时回踩不是任何动作的触发点, 响铃只会邀请未授权加仓 (8/3 夜盘的形状)。"""
    positions, _ = _held_with_z1(cap=False)
    lines, _ = prs.desired_lines(positions, {"tickers": {}}, "2026-08-09",
                                 budgets={})
    assert not [d for d in lines if d["kind"] == "z1"]
    assert [d for d in lines if d["kind"] == "stop"], "下行保护仍由 stop 线负责"


def test_watchlist_bare_z1_observation_line_is_unchanged():
    """watchlist 非成员的裸 z1 观察线不受本次补洞影响 (行为回归守卫)。"""
    wl = {"tickers": {"LITE": {"agent": {"zones": {
        "accumulation_zone_1": [700.0, 720.0]}}}}}
    lines, _ = prs.desired_lines({"tickers": {}}, wl, "2026-08-09", budgets={})
    z1 = [d for d in lines if d["kind"] == "z1"]
    assert len(z1) == 1 and z1[0]["note"] == "z1"


# ── nochase: 当前 regime 的追价上限 (用户) ──

_NC_POS = {"tickers": {"MRVL": {"broker": {"qty": 2}, "agent": {"zones": {
    "stop_level": 179.8, "no_chase_above": 222.0, "trim_zone": [244.0, 251.6]}}}}}
_NC_WL = {"tickers": {"AMZN": {"moomoo_listed": True, "agent": {"zones": {
    "valid_until": "2099-01-01", "no_chase_above": 273.5,
    "accumulation_zone_1": [269.0, 271.5], "trim_zone": [292.5, 298.0]}}}}}


def test_no_chase_line_is_drawn_in_both_buckets():
    lines, _ = prs.desired_lines(_NC_POS, _NC_WL, TODAY)
    nc = {d["ticker"]: d for d in lines if d["kind"] == "nochase"}
    assert set(nc) == {"MRVL", "AMZN"}, "持仓与候选都要有 —— 追价上限是卡片属性不是持仓属性"
    assert nc["MRVL"]["type"] == "PRICE_UP" and nc["MRVL"]["value"] == 222.0
    assert nc["AMZN"]["value"] == 273.5
    assert all(prs._owned_note(d["note"]) for d in nc.values())
    assert {d["note"] for d in nc.values()} == {"nochase"}
    assert len("nochase") <= 10, "moomoo note 上限 10 字符"


def test_no_chase_is_not_pinned_but_does_not_disappear():
    """No-chase consumes the regular cap but flips direction after crossing."""
    lines, _ = prs.desired_lines(_NC_POS, {"tickers": {}}, TODAY)
    nc = [d for d in lines if d["kind"] == "nochase"]
    assert nc and not any(d.get("pin") for d in nc)
    kept, notes = prs.orient_sides(nc, {"US.MRVL": 230.0})
    assert [(d["kind"], d["type"], d["value"]) for d in kept] == [
        ("nochase", "PRICE_DOWN", 222.0)]
    assert any("翻向" in note for note in notes)


def test_no_chase_equal_to_trim_low_collapses_to_trim_and_says_so():
    """单调律允许 no_chase == trim 下沿 —— 同价同向时留高优先级的 trim,
    但那个价位仍然有线, 且必须播报 (只在持仓桶可能撞: watchlist 不画 trim)。"""
    pos = {"tickers": {"XX": {"broker": {"qty": 3}, "agent": {"zones": {
        "stop_level": 500.0, "no_chase_above": 593.6,
        "trim_zone": [593.6, 601.0]}}}}}
    lines, notes = prs.desired_lines(pos, {"tickers": {}}, TODAY)
    at = [d for d in lines if d["value"] == 593.6]
    assert len(at) == 1 and at[0]["kind"] == "trim"
    assert any("同价同向" in n and "nochase" in n for n in notes), "让位必须播报, 不静默"


def test_no_chase_equal_to_gate_collapses_to_gate():
    """`gate == no_chase` 是正常设计 (core.structure: 「在追价上限处确认突破」) ——
    撞价时留 gate (它是 pin 的状态转移轴), nochase 让位, 价位仍有线。"""
    wl = {"tickers": {"NVDA": {"moomoo_listed": True, "agent": {
        "zones": {"valid_until": "2099-01-01", "no_chase_above": 214.0},
        "next_regime": {"entry_gate": {"level": 214.0}}}}}}
    lines, notes = prs.desired_lines({"tickers": {}}, wl, TODAY)
    at = [d for d in lines if d["value"] == 214.0]
    assert len(at) == 1 and at[0]["kind"] == "gate" and at[0].get("pin")
    assert any("同价同向" in n and "nochase" in n for n in notes)


# ── oz1: 期权选约首档限价线 (用户) ──

def test_oz1_line_on_contract_code_for_authorized_underlying():
    from quant.sync.price_reminder_sync import desired_lines, _owned_note
    plans = {"MSFT": {"code": "MSFT270115C460000", "expiry": "2027-01-15",
                      "tiers": [{"premium": 55.0, "contracts": 1}]}}
    lines, _ = desired_lines({"tickers": {}}, {"tickers": {}}, "2026-08-10",
                             budgets={"MSFT": "40%:8.5k"}, option_plans=plans)
    oz = [d for d in lines if d["kind"] == "oz1"]
    assert len(oz) == 1
    assert oz[0]["ticker"] == "MSFT270115C460000", "oz1 挂合约 code (期权 basis 分开, 同 g01)"
    assert oz[0]["type"] == "PRICE_DOWN" and oz[0]["value"] == 55.0
    assert oz[0]["note"] == "oz1:1"
    assert _owned_note("oz1:1"), "oz1 note 必须在所有权词表内, 否则下轮换血会当手动线放过"


def test_oz1_skipped_without_grant_or_expired():
    from quant.sync.price_reminder_sync import desired_lines
    plans = {"MSFT": {"code": "MSFT270115C460000", "expiry": "2027-01-15",
                      "tiers": [{"premium": 55.0, "contracts": 1}]}}
    # 无授权 (budgets 里没有 MSFT) → 不画 (裸 z1 补洞同拍)
    lines, _ = desired_lines({"tickers": {}}, {"tickers": {}}, "2026-08-10",
                             budgets={}, option_plans=plans)
    assert not any(d["kind"] == "oz1" for d in lines)
    # 已到期 → 不画且报出来
    plans["MSFT"]["expiry"] = "2026-08-01"
    lines, notes = desired_lines({"tickers": {}}, {"tickers": {}}, "2026-08-10",
                                 budgets={"MSFT": "40%:8.5k"}, option_plans=plans)
    assert not any(d["kind"] == "oz1" for d in lines)
    assert any("已到期" in n for n in notes)
