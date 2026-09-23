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
    # Mechanical staleness reads bars through book; an empty series keeps the
    # note-construction assertions independent from live market data.
    monkeypatch.setattr(tgs.book, "bars", lambda *args, **kwargs: [])


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
    for w in ("stop:trail", "stop:lock", "nochase"):
        assert prs._note_width(w) <= prs.NOTE_WIDTH_MAX


def test_owned_note_recognizes_merged_labels_segment_wise():
    """并入形态 "a/b" 的每段都必须是系统词：
    否则下轮对账把并入线当手动放过 (孤儿) 或把手动线误删 (越界), 两个方向都要钉。"""
    for owned in ("trim/nochase", "z1:11%:2k/nochase", "stop:card/gate:cur",
                  "gate:next/nochase"):
        assert prs._owned_note(owned), owned
    for manual in ("trim/我的备注", "nochase/买回提醒", "trim/", "/nochase"):
        assert not prs._owned_note(manual), manual


def test_note_width_counts_cjk_as_two():
    """服务端限「20 个中文字符」= 40 半宽单位 (US.KO 实测: ascii×40 OK/×41 拒,
    中×20 OK, 中×19+3A 拒) — 宽度函数按 2:1 折算。"""
    assert prs._note_width("A" * 40) == 40 == prs.NOTE_WIDTH_MAX
    assert prs._note_width("中" * 20) == 40
    assert prs._note_width("中" * 19 + "AAA") == 41 > prs.NOTE_WIDTH_MAX


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


def test_budget_format_stays_within_note_budget_cap():
    """预算段不超过 BUDGET_NOTE_MAX；金额 round $50，千元以上转 k。"""
    assert prs._fmt_budget(0.02, 22405) == "2%:450"
    assert prs._fmt_budget(0.05, 22500) == "5%:1.1k"       # ≥$1000 转 k
    assert prs._fmt_budget(0.05, 300000) == "5%:15k"       # 整千无小数位可显 (:g 去 .0)
    assert prs._fmt_budget(0.02, 1000) == "2%:50"          # 金额地板 $50
    for frac, nav in ((0.02, 22405), (0.05, 22500), (0.05, 300000),
                      (0.10, 22500), (0.15, 66000), (0.349, 21237), (0.40, 21237)):
        assert len("z1:" + prs._fmt_budget(frac, nav)) <= 3 + prs.BUDGET_NOTE_MAX, (frac, nav)


def test_budget_format_keeps_full_precision_after_relax():
    """一位小数 k 保留，不降成整 k 或丢金额：
    旧 7 字符 cap 下这三个都会被降级 ("10%:2k" / "15%:10k" / "35%:7k")。
    降级阶梯保留为超 BUDGET_NOTE_MAX 的保险丝, 现实格式永不触发。"""
    assert prs._fmt_budget(0.10, 22500) == "10%:2.2k"
    assert prs._fmt_budget(0.15, 66000) == "15%:9.9k"
    assert prs._fmt_budget(0.349, 21237) == "35%:7.4k"     # gross binding 的实际 cap
    # 保险丝路径仍在: 荒谬宽的输入才会走到降级
    fuse = prs._fmt_budget(0.35, 300_000_000_000)
    assert len(fuse) <= prs.BUDGET_NOTE_MAX


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
    assert full == {"MSFT": "40%:8.5k"}                    # 空书 → 名义 = 实际 ($8,500)
    squeezed = prs.z1_budgets({"tickers": {}}, wl, TODAY, nav=21237.0,
                              usage={"used_budget": 0.06, "used_gross": 0.75},
                              quote_of=QUOTE, veto_of=UP)
    assert squeezed == {"MSFT": "35%:7.5k"}                # gross 余额 binding ($7,450 → 一位小数显 7.5k)


def test_per_ticker_cap_keeps_priority_kinds():
    wl = {"tickers": {"XX": {"agent": {"zones": {
        "valid_until": "2099-01-01",
        "reclaim_gates": [{"level": float(i)}                          # 名额 +1 道 gate
                          for i in range(1, prs.MAX_PER_TICKER + 2)],
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
    assert all(prs._note_width(d["note"]) <= prs.NOTE_WIDTH_MAX for d in lines)  # 服务端宽限


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
                  "reclaim_gates": [{"level": float(i)}
                                    for i in range(1, prs.MAX_PER_TICKER + 3)],
                  "accumulation_zone_1": [0.5, 0.9]},
        "last_regime": {"repair_gate": {"level": 100.0}},
        "next_regime": {"entry_gate": {"level": 200.0}}}}}}
    lines, notes = prs.desired_lines({"tickers": {}}, wl, TODAY)
    notes_got = [d["note"] for d in lines]
    assert "gate:cur" in notes_got and "gate:next" in notes_got
    assert sum(1 for n in notes_got if not n.startswith("gate:")) == prs.MAX_PER_TICKER
    # moomoo 硬上限 = 每票每类型 20 (实测); 线会翻向 ⇒ 按全挤同侧算, 且给手动线留余量
    assert len(lines) == prs.MAX_PER_TICKER + 2 < prs.SERVER_MAX_PER_TYPE
    assert any("上限" in n for n in notes)


def test_pinned_gate_wins_dedup_against_same_priced_reclaim_rung():
    """同价时留下带 gate:cur/next 备注的那条 —— 否则常驻线被裸 gate 顶掉,
    pin 与备注一起丢失 (又一例"字段在转换中被丢")。"""
    wl = {"tickers": {"LITE": {"agent": {
        "zones": {"valid_until": "2099-01-01", "reclaim_gates": [{"level": 716.0}]},
        "last_regime": {"repair_gate": {"level": 716.0}}}}}}
    lines, _ = prs.desired_lines({"tickers": {}}, wl, TODAY)
    assert [(d["note"], d.get("pin")) for d in lines] == [("gate:cur", True)]


def test_collapse_same_price_merges_labels_and_says_so():
    """翻向后 gate:cur 可能与卡 stop 撞价同向；高优先级 kind 当 head，
    被让位方标签并入，丢线不丢标签。"""
    desired = [
        {"code": "US.TST", "ticker": "TST", "kind": "stop", "type": "PRICE_DOWN",
         "value": 100.0, "note": "stop:card"},
        {"code": "US.TST", "ticker": "TST", "kind": "gate", "type": "PRICE_DOWN",
         "value": 100.0, "note": "gate:cur", "pin": True},
        {"code": "US.TST", "ticker": "TST", "kind": "gate", "type": "PRICE_UP",
         "value": 130.0, "note": "gate:next", "pin": True},     # 不同方向 → 不撞
    ]
    kept, notes = prs.collapse_same_price(desired)
    assert [d["note"] for d in kept] == ["stop:card/gate:cur", "gate:next"]
    assert kept[0]["kind"] == "stop"                        # head = 高优先级 kind
    assert prs._owned_note(kept[0]["note"])                 # 并入后仍可认领
    assert any("同价同向" in n and "标签并入" in n for n in notes)

    # 顺序颠倒结果相同 (不是"谁在前谁赢")
    kept2, _ = prs.collapse_same_price([desired[1], desired[0]])
    assert [d["note"] for d in kept2] == ["stop:card/gate:cur"]


def test_collapse_falls_back_to_yield_when_merge_impossible():
    """并不进去的两种回退让位, 照旧播报「该价位仍有线」: ①同 kind 真重复
    (reclaim 梯撞 gate:cur — 并入无新信息) ②宽度超 40 半宽单位。"""
    dup = [
        {"code": "US.X", "ticker": "X", "kind": "gate", "type": "PRICE_UP",
         "value": 100.0, "note": "gate:cur", "pin": True},
        {"code": "US.X", "ticker": "X", "kind": "gate", "type": "PRICE_UP",
         "value": 100.0, "note": "gate"},
    ]
    kept, notes = prs.collapse_same_price(dup)
    assert [d["note"] for d in kept] == ["gate:cur"]
    assert any("该价位仍有线" in n for n in notes)

    wide = [
        {"code": "US.X", "ticker": "X", "kind": "z1", "type": "PRICE_UP",
         "value": 100.0, "note": "z1:" + "9" * 33},          # 36 宽 + "/nochase" = 44 > 40
        {"code": "US.X", "ticker": "X", "kind": "nochase", "type": "PRICE_UP",
         "value": 100.0, "note": "nochase"},
    ]
    kept2, notes2 = prs.collapse_same_price(wide)
    assert [d["kind"] for d in kept2] == ["z1"] and "/" not in kept2[0]["note"]
    assert any("该价位仍有线" in n for n in notes2)


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


def test_option_gate_stop_draws_on_underlying_code_with_gate_note():
    """S5g (用户): exit_guard 写在 guard 上的 opt_gate_stop (授予 cap 的
    gate) 是 underlying 基 → 与 trail/卡 stop 同基取 max 挂正股 code, note stop:gate;
    exit_floor_map 只在期权 guard 有它时带 key。"""
    occ = "META270115C560000"
    pos = {"tickers": {occ: {
        "broker": {"qty": 1, "current_price": 60.0},
        "lots": {"base": [], "position": [{"qty": 1, "avg_cost": 71.0}]},
        "agent": {"zones": {"stop_level": 542.59, "stop_level_basis": "underlying"}}}}}
    guards = {"positions": {occ: {"ratchet": {"floor": None}, "trail": {"trail": 530.0},
                                  "opt_gate_stop": {"level": 553.0, "days": 1}}}}
    floors = prs.exit_floor_map(guards, pos)
    assert floors == {occ: {"lock": None, "trail": 530.0, "gate": 553.0}}
    lines, _ = prs.desired_lines(pos, {"tickers": {}}, TODAY, floors=floors)
    by = {(d["ticker"], d["kind"]): d for d in lines}
    und = by[("META", "stop")]
    assert (und["code"], und["value"], und["note"]) == ("US.META", 553.0, "stop:gate")
    assert (occ, "stop") not in by                              # 合约 code 无同基候选


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


def test_held_without_grant_gets_bare_z1_line():
    """持仓与 watchlist 同义 (画线尽可能完整, 取代旧的「无授权持仓不画裸 z1」):
    裸 "z1" = 无授权观察线 (不是动作触发点), 带预算 = 有授权。
    授权判据不动 (预算仍只给 Today 成员), 变的只是看得见。"""
    positions, _ = _held_with_z1(cap=False)
    lines, _ = prs.desired_lines(positions, {"tickers": {}}, "2026-08-09",
                                 budgets={})
    z1 = [d for d in lines if d["kind"] == "z1"]
    assert [(d["value"], d["note"]) for d in z1] == [(124.8, "z1")], "裸线, 不带预算 = 无授权"
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
    assert prs._note_width("nochase") <= prs.NOTE_WIDTH_MAX  # 全称不缩写


def test_no_chase_is_not_pinned_but_does_not_disappear():
    """No-chase consumes the regular cap but flips direction after crossing."""
    lines, _ = prs.desired_lines(_NC_POS, {"tickers": {}}, TODAY)
    nc = [d for d in lines if d["kind"] == "nochase"]
    assert nc and not any(d.get("pin") for d in nc)
    kept, notes = prs.orient_sides(nc, {"US.MRVL": 230.0})
    assert [(d["kind"], d["type"], d["value"]) for d in kept] == [
        ("nochase", "PRICE_DOWN", 222.0)]
    assert any("翻向" in note for note in notes)


def test_z1_budget_and_nochase_merge_after_orient():
    """Synthetic collision: no_chase equals the z1 upper bound,
    初始异侧 (z1 DOWN / nochase UP) 不撞, 现价在其下 → 翻向后同价同向 →
    collapse 并入 "z1:11%:2k/nochase" — 预算 head 保留, 标签不丢。"""
    desired = [
        {"code": "US.TST", "ticker": "TST", "kind": "z1", "type": "PRICE_DOWN",
         "value": 110.0, "note": "z1:11%:2k"},
        {"code": "US.TST", "ticker": "TST", "kind": "nochase", "type": "PRICE_UP",
         "value": 110.0, "note": "nochase"},
    ]
    oriented, _ = prs.orient_sides(desired, {"US.TST": 105.0})
    assert {d["type"] for d in oriented} == {"PRICE_UP"}     # 都翻到现价上方侧
    kept, notes = prs.collapse_same_price(oriented)
    assert len(kept) == 1 and kept[0]["note"] == "z1:11%:2k/nochase"
    assert kept[0]["kind"] == "z1" and prs._owned_note(kept[0]["note"])
    assert any("标签并入" in n for n in notes)


def test_no_chase_equal_to_trim_low_merges_into_trim_label():
    """单调律允许 no_chase == trim 下沿 —— 同价同向时 trim 当 head, nochase
    标签并入 "trim/nochase"（全称不缩写），且必须播报
    (只在持仓桶可能撞: watchlist 不画 trim)。"""
    pos = {"tickers": {"XX": {"broker": {"qty": 3}, "agent": {"zones": {
        "stop_level": 500.0, "no_chase_above": 593.6,
        "trim_zone": [593.6, 601.0]}}}}}
    lines, notes = prs.desired_lines(pos, {"tickers": {}}, TODAY)
    at = [d for d in lines if d["value"] == 593.6]
    assert len(at) == 1 and at[0]["kind"] == "trim"
    assert at[0]["note"] == "trim/nochase" and prs._owned_note(at[0]["note"])
    assert any("同价同向" in n and "nochase" in n for n in notes), "并入必须播报, 不静默"


def test_no_chase_equal_to_gate_merges_into_gate_label():
    """`gate == no_chase` 是正常设计 (core.structure: 「在追价上限处确认突破」) ——
    撞价时 gate 当 head (pin 的状态转移轴), nochase 标签并入 "gate:next/nochase"。"""
    wl = {"tickers": {"NVDA": {"moomoo_listed": True, "agent": {
        "zones": {"valid_until": "2099-01-01", "no_chase_above": 214.0},
        "next_regime": {"entry_gate": {"level": 214.0}}}}}}
    lines, notes = prs.desired_lines({"tickers": {}}, wl, TODAY)
    at = [d for d in lines if d["value"] == 214.0]
    assert len(at) == 1 and at[0]["kind"] == "gate" and at[0].get("pin")
    assert at[0]["note"] == "gate:next/nochase" and prs._owned_note(at[0]["note"])
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


def test_z1_budget_follows_staleness_eviction(monkeypatch):
    """机械 stale 卡出白名单 ⇒ z1 预算备注同拍消失 ( 用户: 接进
    today_group + z1 画线 — 一处闸两处生效)。MU 8/25 案: 手机备注 950
    `z1:40%:7.5k` 在 entry_guard 已判 STALE 之后仍在邀请按旧图入场。"""
    wl = {"tickers": {
        "MU": {"status": "watching", "moomoo_listed": True,
               "agent": {"current_regime": {"name": "R_突破_945", "cap_pct_nav": 0.4,
                                            "since": "2026-08-14", "source": "card"},
                         "zones": {"accumulation_zone_1": [940.5, 950.0],
                                   "accumulation_zone_2": [921.5, 940.5],
                                   "trim_zone": [990.0, 1008.0],
                                   "valid_until": "2026-08-28"}}},
    }}
    bd = [{"time": "2026-08-21", "close": 966.78, "high": 970, "low": 960},
          {"time": "2026-08-24", "close": 910.43, "high": 968, "low": 905}]
    monkeypatch.setattr(tgs.book, "bars", lambda t: bd)
    assert prs.z1_budgets({"tickers": {}}, wl, "2026-08-25", nav=18651.0,
                          quote_of=QUOTE, veto_of=UP) == {}
    fresh = [{"time": "2026-08-21", "close": 966.78, "high": 970, "low": 960},
             {"time": "2026-08-24", "close": 945.0, "high": 968, "low": 940}]
    monkeypatch.setattr(tgs.book, "bars", lambda t: fresh)
    assert "MU" in prs.z1_budgets({"tickers": {}}, wl, "2026-08-25", nav=18651.0,
                                  quote_of=QUOTE, veto_of=UP)


def test_reversal_probe_oz1_line_does_not_need_the_stock_whitelist():
    """反转探针 : 合约首档限价照画, 不看 budgets (正股 cap 0); 信号过期不画。"""
    plan = {"NBIS": {"code": "NBIS270618C90000", "expiry": "2027-06-18",
                     "entry_class": "reversal_probe", "valid_until": "2099-01-01",
                     "tiers": [{"premium": 12.5, "contracts": 1}]}}
    lines, _ = prs.desired_lines(POS, WL, TODAY, budgets={}, option_plans=plan)
    oz = [d for d in lines if d["kind"] == "oz1"]
    assert [(d["ticker"], d["value"], d["note"]) for d in oz] == [("NBIS270618C90000", 12.5, "oz1:1")]
    plan["NBIS"]["valid_until"] = "2000-01-01"
    lines, notes = prs.desired_lines(POS, WL, TODAY, budgets={}, option_plans=plan)
    assert not [d for d in lines if d["kind"] == "oz1"] and any("信号已过期" in n for n in notes)


def test_bottom_stock_first_rung_line_is_drawn_with_the_stop_in_the_note():
    """抄底正股腿 : 首档限价线 (kind z1) 不看 budgets, 备注 = 股数 + 止损 (止跌低); 过期不画。"""
    plan = {"NBIS": {"entry_class": "reversal_probe", "valid_until": "2099-01-01", "support": 78.4,
                     "stock": {"tiers": [{"price": 84.0, "shares": 5}], "cap_frac": 0.05}}}
    lines, _ = prs.desired_lines(POS, WL, TODAY, budgets={}, option_plans=plan)
    z1 = [d for d in lines if d["ticker"] == "NBIS" and d["kind"] == "z1"]
    assert [(d["value"], d["note"]) for d in z1] == [(84.0, "抄底首档:5股 止损78.4")]
    assert all(prs._owned_note(d["note"]) for d in z1)       # 写出的备注必须能被认领, 否则重锚留孤儿线
    plan["NBIS"]["valid_until"] = "2000-01-01"
    lines, notes = prs.desired_lines(POS, WL, TODAY, budgets={}, option_plans=plan)
    assert not [d for d in lines if d["ticker"] == "NBIS" and d["kind"] == "z1"]
    assert any("信号已过期" in n for n in notes)


def test_bottom_first_rung_line_is_owned_so_reanchor_replaces_it():
    """抄底首档备注必须在所有权词表内: 写 `抄底首档:` 却不认领 →
    首档随收盘重锚时新线 ADD、旧线被当手动永不 DEL, 残线越积越多。"""
    note = "抄底首档:9股 止损342.3305"
    assert prs._owned_note(note)
    for manual in ("抄底", "抄底首档", "我的抄底首档:1股", "抄底首档:9股/我的备注"):
        assert not prs._owned_note(manual), manual
    rows = [{"code": "US.SYN", "reminder_type": "PRICE_UP", "value": 357.89, "note": note}]
    desired = [{"code": "US.SYN", "type": "PRICE_DOWN", "value": 344.72, "note": note}]
    adds, dels, _, kept_manual = prs.plan_ops(desired, rows)
    assert [d["value"] for d in adds] == [344.72] and [r["value"] for r in dels] == [357.89]
    assert kept_manual == 0
    # 计划整张消失 (信号过期 / 票不再入选) → 残线同样收走
    adds, dels, _, _ = prs.plan_ops([], rows)
    assert not adds and [r["value"] for r in dels] == [357.89]


# ── 远端 gate (把阶梯顶之后的几条 gate 也画出来) ──────────────

def _fg_card(preview=None):
    nr = {"entry_gate": {"level": 200.0}}
    if preview is not None:
        nr["next_gate_preview"] = preview
    return {"tickers": {"XX": {"moomoo_listed": True, "agent": {
        "zones": {"valid_until": "2099-01-01"}, "next_regime": nr}}}}


FAR = [{"n": 3, "level": 230.0, "touches": 4, "kinds": "high+low", "rejections": 14, "source": "cluster"},
       {"n": 4, "level": 260.0, "touches": 1, "kinds": "high", "rejections": 1, "source": "window_high"}]


def test_future_gates_number_on_from_the_card_ladder():
    """卡上的 preview = 裸 "gate:+2"; 机械候选接着往上数, 备注带收据 (= 来源编码)。"""
    lines, _ = prs.desired_lines({"tickers": {}}, _fg_card({"level": 215.0}), TODAY,
                                 far_gates={"XX": FAR})
    got = {d["note"]: (d["value"], d["type"], d["kind"], bool(d.get("pin"))) for d in lines}
    assert got["gate:next"] == (200.0, "PRICE_UP", "gate", True)
    assert got["gate:+2"] == (215.0, "PRICE_UP", "fgate", False)
    assert got["gate:+3 簇×4HL 拒14"] == (230.0, "PRICE_UP", "fgate", False)
    assert got["gate:+4 窗高"] == (260.0, "PRICE_UP", "fgate", False)
    assert all(prs._owned_note(n) for n in got)                  # 下一轮对账认得出是自己的
    assert all(prs._note_width(n) <= prs.NOTE_WIDTH_MAX for n in got)


def test_stale_preview_is_not_drawn_and_mechanical_takes_plus_two():
    """`stale: true` = 降级时搬运的旧上行阶梯 (「价位已失效待重画」) —— 不画成线。"""
    lines, _ = prs.desired_lines({"tickers": {}}, _fg_card({"level": 215.0, "stale": True}),
                                 TODAY, far_gates={"XX": [{**FAR[0], "n": 2}]})
    notes = {d["note"]: d["value"] for d in lines if d["kind"] == "fgate"}
    assert notes == {"gate:+2 簇×4HL 拒14": 230.0}


def test_far_gates_default_to_card_only():
    lines, _ = prs.desired_lines({"tickers": {}}, _fg_card({"level": 215.0}), TODAY)
    assert [d["note"] for d in lines if d["kind"] == "fgate"] == ["gate:+2"]


def test_future_gates_are_cut_first_and_farthest_first():
    """优先级垫底 + 非 pin: 名额不够时先裁它, 由远及近; cur/next 两条轴不受影响。"""
    wl = _fg_card({"level": 215.0})
    wl["tickers"]["XX"]["agent"]["zones"]["reclaim_gates"] = [
        {"level": float(i)} for i in range(1, prs.MAX_PER_TICKER)]          # 占掉 MAX−1 个名额
    lines, notes = prs.desired_lines({"tickers": {}}, wl, TODAY, far_gates={"XX": FAR})
    fg = [d["note"] for d in lines if d["kind"] == "fgate"]
    assert fg == ["gate:+2"]                                                # 只剩一个名额 → 最近那条
    assert "gate:next" in [d["note"] for d in lines]
    assert sum("超每票上限" in n for n in notes) == 2


def test_future_gate_flips_instead_of_vanishing():
    lines, _ = prs.desired_lines({"tickers": {}}, _fg_card({"level": 215.0}), TODAY,
                                 far_gates={"XX": FAR})
    kept, _ = prs.orient_sides(lines, {"US.XX": 240.0})
    sides = {d["note"]: d["type"] for d in kept if d["kind"] == "fgate"}
    assert sides == {"gate:+2": "PRICE_DOWN", "gate:+3 簇×4HL 拒14": "PRICE_DOWN",
                     "gate:+4 窗高": "PRICE_UP"}


def test_future_gate_map_reads_the_live_card_and_the_card_ladder_top(monkeypatch):
    """卡源判别式与 z1_budgets 同拍 (live → positions 卡); 锚 = 未 stale 的 preview, 缺则 next。"""
    pos = {"tickers": {"AA": {"broker": {"qty": 3}, "agent": {
        "next_regime": {"entry_gate": {"level": 50.0}, "next_gate_preview": {"level": 60.0}}}}}}
    wl = {"tickers": {
        "AA": {"agent": {"next_regime": {"entry_gate": {"level": 999.0}}}},   # 被 live 卡遮住
        "BB": {"agent": {"next_regime": {"entry_gate": {"level": 70.0},
                                         "next_gate_preview": {"level": 80.0, "stale": True}}}},
        "CC": {"agent": {"zones": {}}}}}                                      # 没有轴 → 不出
    seen = {}

    def fake_candidates(bars, above, n):
        seen[bars] = (above, n)
        return [{"level": above + 1, "touches": 2, "kinds": "high", "rejections": 0,
                 "source": "cluster"}]

    monkeypatch.setattr(prs.future_gates, "candidates", fake_candidates)
    out = prs.future_gate_map(pos, wl, bars_of=lambda t: t)
    depth = prs.future_gates.FUTURE_GATES_MAX
    assert seen == {"AA": (60.0, depth), "BB": (70.0, depth)}
    assert {t: [(c["level"], c["n"]) for c in v] for t, v in out.items()} == {
        "AA": [(61.0, 3)],          # 未 stale 的 preview 占了 +2 → 机械从 +3 起
        "BB": [(71.0, 2)]}          # preview stale → 机械接 +2


def test_write_throttle_keeps_every_window_under_the_server_rate_limit():
    """一次上百条的迁移会撞「每 30 秒 60 次」, 窗口满之后其余 ADD 全拒 ——
    换血是先删后加, 被删的旧线 (含 stop) 在服务端消失。旧节流「每 10 次停 0.6s」只在差异 <60 时碰巧够用。"""
    t, slept = [0.0], []

    def sleep(s):
        slept.append(s)
        t[0] += s

    cap = prs.SERVER_SET_MAX_PER_WINDOW - 5
    th = prs._Throttle(cap, prs.SERVER_SET_WINDOW_S, clock=lambda: t[0], sleep=sleep)
    stamps = []
    for _ in range(118):                        # 首轮实际规模
        th.wait()
        stamps.append(t[0])
        t[0] += 0.05                            # 一次 API 往返
    worst = max(sum(1 for s in stamps if a <= s < a + prs.SERVER_SET_WINDOW_S) for a in stamps)
    assert worst <= cap < prs.SERVER_SET_MAX_PER_WINDOW == 60
    assert len(slept) == 2                      # 118 = 55 + 55 + 8: 只在撞顶时睡
    t[0], slept[:] = 1000.0, []
    small = prs._Throttle(cap, prs.SERVER_SET_WINDOW_S, clock=lambda: t[0], sleep=sleep)
    for _ in range(10):                         # 平时每轮 5–10 条: 一次都不等
        small.wait()
    assert slept == []


def test_server_limit_leaves_room_for_manual_lines():
    """实测: 每票每类型 20 条。线会翻向 ⇒ 最坏全挤同侧。
    pin 至多 4 条/票 (gate:cur / gate:next / ⭐待破 / ⭐已破) 不占名额。"""
    pins = 4
    assert prs.MAX_PER_TICKER + pins < prs.SERVER_MAX_PER_TYPE == 20
    assert 1 + prs.future_gates.FUTURE_GATES_MAX < prs.MAX_PER_TICKER   # 远端 gate 吃不完可裁名额


# ── 画线补全 + ⭐ 箱顶 / 止跌低 (无论是否有持仓, 所有线, 并标注极其关键的位置) ──

_FULL_ZONES = {"valid_until": "2099-01-01", "stop_level": 90.0, "trim_zone": [130.0, 135.0],
               "no_chase_above": 112.0, "accumulation_zone_1": [108.0, 110.0],
               "accumulation_zone_2": [100.0, 108.0], "reclaim_gates": [{"level": 115.0}]}


def test_watchlist_card_now_draws_stop_trim_and_z2():
    """候选卡此前只有 z1 / nochase / gate —— stop 与 trim 只在持仓画; z2 两桶都没有。"""
    wl = {"tickers": {"XX": {"moomoo_listed": True, "agent": {"zones": dict(_FULL_ZONES)}}}}
    lines, _ = prs.desired_lines({"tickers": {}}, wl, TODAY)
    got = {(d["kind"], d["value"], d["type"], d["note"]) for d in lines}
    assert ("stop", 90.0, "PRICE_DOWN", "stop:card") in got      # 卡片 regime 地板, 不是持仓止损
    assert ("trim", 130.0, "PRICE_UP", "trim") in got
    assert ("z2", 100.0, "PRICE_DOWN", "z2") in got              # 下沿 (上沿 = z1 下沿, 不重复)
    assert ("z1", 110.0, "PRICE_DOWN", "z1") in got and ("gate", 115.0, "PRICE_UP", "gate") in got


def test_held_card_now_draws_z2_and_reclaim_rungs():
    pos = {"tickers": {"XX": {"broker": {"qty": 3}, "agent": {"zones": dict(_FULL_ZONES)}}}}
    lines, _ = prs.desired_lines(pos, {"tickers": {}}, TODAY, budgets={})
    got = {(d["kind"], d["value"], d["note"]) for d in lines}
    assert {("z2", 100.0, "z2"), ("gate", 115.0, "gate"), ("z1", 110.0, "z1"),
            ("stop", 90.0, "stop:card"), ("trim", 130.0, "trim")} <= got


_TOP = {"level": 120.3, "lo": 119.8, "touches": 3, "kinds": "high", "tests": 3,
        "test_dates": ["2026-07-01", "2026-08-01", "2026-09-01"], "bucket": 0.5}
_BRK = {"level": 104.4, "lo": 103.9, "touches": 2, "kinds": "high", "tests": 2, "date": "2026-09-18",
        "intact": True, "held": True, "bucket": 0.5}


def _key_card(next_gate=None):
    ag = {"zones": dict(_FULL_ZONES)}
    if next_gate:
        ag["next_regime"] = {"entry_gate": {"level": next_gate}}
    return {"tickers": {"XX": {"moomoo_listed": True, "agent": ag}}}


def test_key_line_snaps_onto_the_card_line_on_the_same_wall_and_is_pinned():
    """箱带 (下沿 − 桶宽 … 上沿 + 桶宽) 里已有卡片线 → 吸附到它的价位, 同价并入后 ⭐ 当 head;
    同一堵墙不画两遍 (与 future_gates 同一去重口径)。"""
    lines, _ = prs.desired_lines({"tickers": {}}, _key_card(next_gate=120.0), TODAY,
                                 key_map={"XX": {"top": _TOP, "brk": None, "stoplow": None}})
    at = [d for d in lines if d["value"] == 120.0]
    assert len(at) == 1 and at[0]["note"] == "⭐箱顶 测3/gate:next" and at[0]["pin"]
    assert not [d for d in lines if d["value"] == 120.3], "簇上沿没有第二条线"


def test_key_line_standalone_when_no_card_line_is_near():
    lines, _ = prs.desired_lines({"tickers": {}}, _key_card(), TODAY,
                                 key_map={"XX": {"top": _TOP, "brk": {**_BRK, "intact": False},
                                                 "stoplow": None}})
    keys = {d["note"]: (d["value"], d["kind"], bool(d.get("pin"))) for d in lines
            if d["note"].startswith(prs.KEY_NOTE_PREFIX)}
    assert keys == {"⭐箱顶 测3": (120.3, "key", True), "⭐假突破 09-18": (104.4, "key", True)}


def test_key_line_merges_with_a_down_line_after_orientation():
    """⭐已破 (线在现价下方) 吸附到 z1 上沿: 定向前 ⭐ 是 PRICE_UP、z1 是 PRICE_DOWN,
    翻向后同价同向 → 并成一条, ⭐ 在前。"""
    lines, _ = prs.desired_lines({"tickers": {}}, _key_card(), TODAY,
                                 key_map={"XX": {"top": None, "stoplow": None,
                                                 "brk": {**_BRK, "level": 110.3, "lo": 109.9}}})
    lines, _ = prs.orient_sides(lines, {"US.XX": 125.0})
    lines, _ = prs.collapse_same_price(lines)
    at = [d for d in lines if d["value"] == 110.0]
    assert [(d["type"], d["note"]) for d in at] == [("PRICE_DOWN", "⭐已破 09-18/z1")]


def test_mechanical_lines_survive_an_expired_card_but_not_delisting():
    """⭐ / 止跌低 来自 K 线不来自卡: 卡过期照画 (卡片线仍跳过并报出); 移出自选 = 不画。"""
    wl = {"tickers": {
        "OLD": {"agent": {"zones": {**_FULL_ZONES, "valid_until": "2026-01-01"}}},
        "GONE": {"moomoo_listed": False, "agent": {"zones": dict(_FULL_ZONES)}}}}
    km = {t: {"top": _TOP, "brk": None, "stoplow": {"level": 95.0, "signal_date": "2026-09-17"}}
          for t in ("OLD", "GONE")}
    lines, notes = prs.desired_lines({"tickers": {}}, wl, TODAY, key_map=km)
    assert {(d["ticker"], d["note"]) for d in lines} == {("OLD", "⭐箱顶 测3"), ("OLD", "止跌低 ✓09-17")}
    assert any("OLD" in n and "过期" in n for n in notes)


def test_new_notes_are_owned_and_fit_the_server_width():
    for n in ("z2", "⭐箱顶 测3", "⭐已破 09-22", "⭐假突破 09-09", "止跌低 ✓09-17",
              "⭐已破 09-22/gate:cur/nochase/z1"):                 # 最宽的现实并入形态
        assert prs._owned_note(n), n
        assert prs._note_width(n) <= prs.NOTE_WIDTH_MAX, n
    for manual in ("⭐", "止跌", "关键位", "z2手动"):
        assert prs._owned_note(manual) == manual.startswith(("⭐", "止跌低")), manual
    # 并不进 40 宽 → 回退让位: 丢的是被并入那段, ⭐ 恒为 head (优先级最高) 不会被挤掉
    win = {"note": "⭐已破 09-22/gate:cur/nochase", "kind": "key"}
    assert prs._merged_note(win, {"note": "z1:11%:2.2k", "kind": "z1"}) is None
    assert prs.PRIORITY["key"] < min(v for k, v in prs.PRIORITY.items() if k != "key")


def test_key_level_map_eligibility_and_formed_only_stoplow(monkeypatch):
    """资格与 future_gate_map 同拍 (live → positions; 自选内的候选; moomoo_listed=False 不画);
    止跌低只在 ✓止跌 (formed) 时画 —— ⏳ 的 L 每天在动, 画了只会每轮换血。"""
    pos = {"tickers": {"AA": {"broker": {"qty": 1}}, "AA270115C100000": {"broker": {"qty": 1}}}}
    wl = {"tickers": {"BB": {}, "CC": {"moomoo_listed": False}, "DD": {}}}
    stage = {"AA": {"stage": "formed", "low": 95.0, "signal_date": "2026-09-17"},
             "BB": {"stage": "watch", "low": 50.0}, "DD": {"stage": "none"}}
    monkeypatch.setattr(prs.key_levels, "box_top", lambda bars: {"level": 1.0} if bars == "BB" else None)
    monkeypatch.setattr(prs.key_levels, "last_breakout", lambda bars: None)
    monkeypatch.setattr(prs.setups, "bottom_setup", lambda bars: stage[bars])
    out = prs.key_level_map(pos, wl, bars_of=lambda t: t)
    assert out == {"AA": {"top": None, "brk": None,
                          "stoplow": {"level": 95.0, "signal_date": "2026-09-17"}},
                   "BB": {"top": {"level": 1.0}, "brk": None, "stoplow": None}}
