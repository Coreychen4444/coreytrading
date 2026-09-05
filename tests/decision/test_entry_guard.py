"""entry_guard 纯计算层回归 — 源 =  回测三发现:
early_risky 4/4 全亏 (分类必须绑 size cap) / IV 95% 买单腿 (闸门要有钩子) /
decisions.jsonl 空 (计划必须落盘)。这些测试锁定: verdict 分支、ladder 单调降序、
分类 cap × N-unit 取小、staleness 拒绝旧 zone。"""
import os

import pytest

from quant.core import io_json
from quant.decision import entry_guard
from quant.decision.entry_guard import (
    PARAMS, build_ladder, momentum_mode, size_caps, zone_bands,
    zone_position, zone_staleness,
)


@pytest.fixture(autouse=True)
def isolated_supporting_state(monkeypatch, tmp_path, request):
    if request.node.get_closest_marker("live_data") is not None:
        return
    calendar = tmp_path / "calendar.json"
    calendar.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(io_json, "STATE_LOCK_PATH", tmp_path / ".state.lock")
    monkeypatch.setattr(entry_guard.earnings, "EARNINGS_CALENDAR_PATH", calendar)
    monkeypatch.setattr(entry_guard, "IV_HISTORY_DIR", tmp_path / "iv_history")
    monkeypatch.setattr(entry_guard, "_power", lambda: {"room": 22000.0})
    monkeypatch.setattr(entry_guard.book, "exposure_of", lambda ticker: 0.0)


def _bar(t, c, h=None, low=None):
    return {"time": t, "open": c, "high": h or c * 1.01, "low": low or c * 0.99, "close": c}


def _ramp(n, start, daily):
    """n 根逐日 ×(1+daily) 的合成序列。"""
    rows, px = [], start
    for i in range(n):
        rows.append(_bar(f"2026-{(i//28)%12+1:02d}-{i%28+1:02d}", px))
        px *= 1 + daily
    return rows


# ── momentum mode ───────────────────────────────────────────────

def test_momentum_normal_flat_series():
    rows = [_bar(f"2026-03-{i%28+1:02d}", 100 + (i % 3)) for i in range(60)]
    assert momentum_mode(rows) == "normal"


def test_momentum_parabolic_all_three_yes():
    rows = _ramp(130, 10, 0.012)            # 6m +150%+ (Q1)
    last = rows[-1]["close"]
    rows.append(_bar("2026-07-01", last * 1.12))   # 单日 +12% (Q2), 新高 (Q3)
    assert momentum_mode(rows) == "parabolic"


def test_momentum_far_from_high_is_normal():
    rows = _ramp(130, 10, 0.012)
    peak = rows[-1]["close"]
    rows.append(_bar("2026-07-01", peak * 1.11))       # Q2 满足
    rows.append(_bar("2026-07-02", peak * 0.80))       # 距高点 >10% → Q3 NO
    assert momentum_mode(rows) == "normal"


# ── zone position / staleness ───────────────────────────────────

ZB = zone_bands({
    "accumulation_zone_1": [95.0, 100.0], "accumulation_zone_2": [84.0, 90.0],
    "trim_zone": [128.0, 138.0], "no_chase_above": 118.0, "stop_level": 84.0,
    "valid_until": "2026-12-31",
})


@pytest.mark.parametrize("px,expect", [
    (120.0, "above_no_chase"), (130.0, "above_no_chase"),  # no_chase 优先于 trim
    (110.0, "waiting"), (97.0, "zone1"), (92.0, "gap_z1_z2"),
    (87.0, "zone2"), (80.0, "below_zones"),
])
def test_zone_position_branches(px, expect):
    assert zone_position(px, ZB) == expect


def test_zone_bands_missing_returns_none():
    assert zone_bands(None) is None
    assert zone_bands({"reasoning_summary": "prose only"}) is None


def test_staleness_expired_and_breakdown():
    zb = dict(ZB, valid_until="2026-01-01")
    rows = [_bar("2026-07-14", 82.0), _bar("2026-07-15", 81.0)]
    hits = zone_staleness(zb, {"last_reviewed": "2026-07-10"}, rows, "2026-07-17")
    assert any("过期" in h for h in hits)
    assert any("breakdown" in h for h in hits)


def test_staleness_fresh_zone_silent():
    rows = [_bar("2026-07-14", 97.0), _bar("2026-07-15", 96.0)]
    assert zone_staleness(ZB, {"last_reviewed": "2026-07-10"}, rows, "2026-07-17") == []


def test_staleness_reviewed_too_old():
    rows = [_bar("2026-07-15", 97.0)]
    hits = zone_staleness(ZB, {"last_reviewed": "2026-06-01"}, rows, "2026-07-17")
    assert any("last_reviewed" in h for h in hits)


# ── ladder ──────────────────────────────────────────────────────

def test_ladder_normal_in_zone1_descending():
    rows = [_bar("2026-07-15", 97.0)]
    tiers = build_ladder("normal", 97.0, rows, ZB)
    assert len(tiers) == 3
    assert tiers[0] > tiers[1] > tiers[2]
    assert tiers[0] <= 97.0                # 首档不高于现价
    assert tiers[1] == pytest.approx(95.0)  # z1 下沿
    assert tiers[2] == pytest.approx(87.0)  # z2 中值


def test_ladder_parabolic_anchors():
    rows = [_bar(f"2026-07-{i:02d}", 100 + i, h=102 + i) for i in range(1, 11)]
    close = rows[-1]["close"]
    tiers = build_ladder("parabolic", close, rows, None)
    a, b, c = PARAMS["parabolic_anchors"]
    assert tiers[0] == pytest.approx(round(close * a, 2))
    h5 = max(r["high"] for r in rows[-5:])
    assert tiers[1] <= round(h5 * b, 2) + 0.01
    assert tiers[0] > tiers[1] > tiers[2]


def test_ladder_normal_without_bands_empty():
    assert build_ladder("normal", 100.0, [_bar("2026-07-15", 100.0)], None) == []


# ── size caps ───────────────────────────────────────────────────

FULL = {"current_regime": {"name": "常规参与", "cap_pct_nav": 0.40}}


def test_size_caps_cap_zero_regime_is_zero():
    caps = size_caps(24000, atr=3.0, t1=100.0,
                     agent={"current_regime": {"cap_pct_nav": 0}})
    assert caps["regime_cap"] == 0.0
    assert caps["final"] == 0.0            # cap 0 = VETO 域


def test_size_caps_binding_min_of_arms():
    """约束取小。② = gate 预算: 默认 gate_no=1 → 2.5% (用户
    「gate1 max 2.5%」)。"""
    from quant.core.sizing import GATE_RISK_CAPS
    assert GATE_RISK_CAPS[0] == 0.025, "本用例的算术依赖 gate1=2.5%"
    #  (用户 #3): ② 的分母是**真 stop 距离**, 不再是写死的 2N。
    # 近 stop (92, 距 8%): gate1 n_unit = 24k×2.5% ×100/8 = 7,500; regime cap = 9,600
    caps = size_caps(24000, atr=4.0, t1=100.0, risk_stop=92.0, agent=FULL)
    assert caps["final"] == pytest.approx(7500.0)
    assert caps["binding"] == "n_unit"
    # 远 stop (20, 距 80%) → 同一笔风险预算只买得起 1/10: 24k×2.5% ×100/80 = 750
    caps2 = size_caps(24000, atr=40.0, t1=100.0, risk_stop=20.0, agent=FULL)
    assert caps2["final"] == pytest.approx(750.0)
    assert caps2["binding"] == "n_unit"
    # ② 不再依赖 ATR —— ATR 缺失照样算得出 (旧实现 atr=None 时整条臂消失)
    assert size_caps(24000, atr=None, t1=100.0, risk_stop=92.0,
                     agent=FULL)["n_unit_cap"] == pytest.approx(7500.0)


def test_size_caps_shock_budget_and_gross_bind_when_book_is_full():
    """新增的两条组合层约束 (接线)。"""
    from quant.core.sizing import GROSS_CAP, SHOCK_BUDGET
    # 预算几乎用尽 → shock_budget binding
    caps = size_caps(24000, atr=4.0, t1=100.0, agent=FULL,
                     usage={"used_budget": SHOCK_BUDGET - 0.001, "used_gross": 0.0})
    assert caps["binding"] == "shock_budget"
    assert caps["final"] < 2250.0
    # gross 几乎用尽 → gross binding
    caps2 = size_caps(24000, atr=0.01, t1=100.0, agent=FULL,
                      usage={"used_budget": 0.0, "used_gross": GROSS_CAP - 0.001})
    assert caps2["binding"] == "gross"
    assert caps2["final"] == pytest.approx(24000 * 0.001)


def test_size_caps_without_usage_treats_book_as_empty():
    """不传 usage = 空书口径 —— 会**高估**可用额度, 所以调用方必须传。"""
    a = size_caps(24000, atr=4.0, t1=100.0, agent=FULL)
    b = size_caps(24000, atr=4.0, t1=100.0, agent=FULL,
                  usage={"used_budget": 0.0, "used_gross": 0.0})
    assert a["final"] == b["final"]


def test_size_caps_no_nav_none():
    assert size_caps(None, 3.0, 100.0, agent=FULL)["final"] is None


def test_size_caps_splits_deployment_from_notional():
    """期权上这两种钱不是同一种 (单位错误):

      regime cap                   = 你**花出去**多少 (保费)
      n_unit / 冲击预算 / gross     = **标的名义**敞口多少

    正股上两者重合, 所以 `final` 仍是各臂取小; 但给 option_plan 的必须分开传,
    否则名义上限被当保费花掉 → 名义敞口数倍于上限 (方向是放松约束)。
    """
    caps = size_caps(24000, atr=4.0, t1=100.0, risk_stop=92.0, agent=FULL,
                     usage={"used_budget": 0.0, "used_gross": 0.0})
    assert caps["deployment_cap"] == pytest.approx(24000 * 0.40)   # regime cap
    assert caps["notional_cap"] == pytest.approx(7500.0)           # R1 n_unit binding
    assert caps["final"] == min(caps["deployment_cap"], caps["notional_cap"])


def test_size_caps_agrees_with_available_cap_on_the_portfolio_layer():
    """entry_guard 不得自带第二份实际-cap 算法 —— 三条组合层数必须逐字来自
    core.sizing.available_cap (moomoo 备注和 brief 预算行读的是同一个函数)。"""
    from quant.core.sizing import available_cap, NOTIONAL_CAP
    usage = {"used_budget": 0.06, "used_gross": 0.75}
    caps = size_caps(21237, atr=3.0, t1=100.0, usage=usage, agent=FULL)
    ac = available_cap(21237, NOTIONAL_CAP, 3.0, 100.0, usage)
    for k in ("regime_cap", "shock_budget_cap", "gross_cap",
              "budget_room", "gross_room"):
        assert caps[k] == ac[k], k


def test_render_delisted_one_liner():
    # moomoo 已移出的卡: 不评估, 计划末尾一行带过 ()
    txt = entry_guard.render([], nav=None, today="2026-07-21",
                             delisted=["NBIS", "CRWV", "AMKR"])
    assert "已移出 moomoo 分组" in txt
    assert "NBIS, CRWV, AMKR" in txt
    assert "STALE" not in txt  # 不产生完整 verdict 块


def test_render_no_delisted_no_footer():
    txt = entry_guard.render([], nav=None, today="2026-07-21", delisted=[])
    assert "已移出" not in txt


def test_latest_iv_skips_zero_rows(tmp_path, monkeypatch):
    # 0.0 = 盘前空报价误写 (MRVL 案): 跳过取最后一条有效
    p = tmp_path / "MRVL.jsonl"
    p.write_text(
        '{"date": "2026-07-17", "atm_iv_pct": 93.2}\n'
        '{"date": "2026-07-20", "atm_iv_pct": 0.0}\n'
)
    monkeypatch.setattr(entry_guard, "IV_HISTORY_DIR", tmp_path)
    monkeypatch.setattr(entry_guard.iv_logger, "IV_HISTORY_DIR", tmp_path)
    row = entry_guard.latest_iv("MRVL")
    assert row["date"] == "2026-07-17"
    assert "93.2" in entry_guard.iv_gate_line("MRVL")
    # 硬门槛: 样本 1 (<60) → 绝对 ATM IV 93.2% ≥ 50% → VETO 单腿 long call
    assert "VETO" in entry_guard.iv_gate_line("MRVL")


def test_latest_iv_all_invalid_reads_none(tmp_path, monkeypatch):
    p = tmp_path / "X.jsonl"
    p.write_text('{"date": "2026-07-20", "atm_iv_pct": 0.0}\n')
    monkeypatch.setattr(entry_guard, "IV_HISTORY_DIR", tmp_path)
    monkeypatch.setattr(entry_guard.iv_logger, "IV_HISTORY_DIR", tmp_path)
    assert entry_guard.latest_iv("X") is None
    assert "未入库" in entry_guard.iv_gate_line("X")
    assert not entry_guard.option_iv_gate("X")["allowed"]      # 无数据 = 不放行


# ── next_regime: gate = 状态转移锚点 () ──────────────

def _bars(*closes):
    return [{"time": f"2026-07-{10 + i:02d}", "close": c} for i, c in enumerate(closes)]


NR = {
    "name": "B_repair_probe",
    "entry_gate": {"level": 104.5, "days": 2, "unlocks": "probe"},
    "cap_pct_nav": 0.01,
    "provisional": ["accumulation_zone_1"],
    "zones": {"accumulation_zone_1": [102.5, 105.0], "no_chase_above": 107.1,
              "stop_level": 100.0, "stop_level_days": 2},
}


def test_next_regime_counts_consecutive_closes_above_gate():
    from quant.decision.entry_guard import next_regime_lines
    lines, fired = next_regime_lines({"next_regime": NR}, _bars(95.0, 105.0, 106.0))
    assert fired
    assert any("✅ 2/2 已成立" in x for x in lines)


def test_next_regime_not_fired_below_gate():
    from quant.decision.entry_guard import next_regime_lines
    lines, fired = next_regime_lines({"next_regime": NR}, _bars(95.0, 105.0, 103.0))
    assert not fired
    assert any("0/2" in x for x in lines)


def test_next_regime_streak_resets_on_a_close_back_below():
    """中途跌破清零 —— 一次假突破不能靠事后凑够天数补上。"""
    from quant.decision.entry_guard import next_regime_lines
    _, fired = next_regime_lines({"next_regime": NR}, _bars(105.0, 103.0, 106.0))
    assert not fired


def test_next_regime_fired_line_declares_auto_switch_in_flight():
    """ 用户 裁决 A: gate ×N 成立 → regime_transition 自动切换
    (旧合同"人工确认再切"随裁决作废 —— 8/4 首夜三笔达标未切的 open ruling)。
    fired 行只会在"已达标、切换还没跑"的间隙里出现, 文案必须说清这是在途,
    并保留 provisional 补画前不授权的护栏。"""
    from quant.decision.entry_guard import next_regime_lines
    lines, fired = next_regime_lines({"next_regime": NR}, _bars(105.0, 106.0))
    assert fired
    assert any("自动切换" in x and "切换在途" in x for x in lines)
    assert any("补画前不授权" in x for x in lines)


def test_next_regime_marks_provisional_zones():
    from quant.decision.entry_guard import next_regime_lines
    lines, _ = next_regime_lines({"next_regime": NR}, _bars(95.0))
    switch = next(x for x in lines if "切换后" in x)
    assert "z1 102.5-105(provisional)" in switch
    assert "stop 100" in switch and "provisional" not in switch.split("stop 100")[1][:20]


@pytest.mark.live_data
def test_gate_sits_above_the_current_regime_no_chase():
    """核心不变式: gate 是下一个状态的入口, 按定义在本 regime 的 no_chase 之上。
    两者放同一坐标系比大小 → gate 成立即 above_no_chase → VETO 死锁
    ( INTC 卡原始写法就是这个)。

     起扫描 watchlist + positions 全部带 next_regime.entry_gate 的卡
    —— 旧版钉死 watchlist['INTC'], INTC promote 进 positions 当天就 KeyError:
    live 状态会流动, 不变式测试不能锚具体 ticker 在具体 bucket。"""
    import json
    from pathlib import Path
    checked = []
    for fname in ("watchlist.json", "positions.json"):
        doc = json.loads(Path(f"trade/us_stock/holding/state/{fname}").read_text())
        for ticker, card in (doc.get("tickers") or {}).items():
            agent = (card.get("agent") or {})
            nr = agent.get("next_regime") or {}
            gate = (nr.get("entry_gate") or {}).get("level")
            if gate is None or not (agent.get("zones") or {}).get("no_chase_above"):
                continue
            ctx = f"{fname}:{ticker}"
            # zone-maintenance 自洽条款: 上行 gate ≥ no_chase (等于 = "在追价上限处
            # 确认突破", 正常设计; 只有**低于**才是倒挂死锁)。NVDA 卡 gate==no_chase==204。
            assert gate >= agent["zones"]["no_chase_above"], ctx
            # 切换后的 no_chase 又高于 gate — 新 regime 内部自洽
            # 骨架 next_regime (转移机把 preview 提升后 zones=null, 待
            # trading-analysis 补画 —  NVDA/AAOI 夜间转移首例):
            # 内部自洽只对**已画好的**下一世界成立, 骨架没有可查的 zones。
            # 骨架不许卡死的守卫在别处 (pending_repaint / NEEDS_CARD)。
            if not nr.get("zones"):
                continue
            # 降级保留图的 no_chase 低于收复 gate 时被机器摘除待重画；
            # 原样搬进「收复后世界」会形成刚确认突破却不许追的矛盾。缺席 = 占位
            # 待重画, 无可查之线, 跳过; 在场则必须自洽。
            nc_next = nr["zones"].get("no_chase_above")
            if nc_next is not None:
                assert nc_next > gate, ctx
            # 入场侧棘轮：下一档地板 = gate − 0.5×ATR20,
            # **带缓冲低于 gate** —— 旧断言 (地板 ≥ 上一档 no_chase ≈ 贴 gate) 正是 bfloor
            # whipsaw 语义, 已被 gate_backtest2b 裁决废除。量级守卫: 缓冲不为零、也不脱锚。
            floor = nr["zones"]["stop_level"]
            assert floor < gate, ctx               # 有缓冲, 不贴 gate
            assert floor > gate * 0.85, ctx        # 缓冲有界 (~0.5N 量级, 不是脱锚的深位)
            checked.append(ctx)
    assert checked, "全库无一张带 next_regime.entry_gate 的卡 — 不变式失去被测对象"


def test_no_next_regime_is_silent():
    from quant.decision.entry_guard import next_regime_lines
    assert next_regime_lines({}, _bars(95.0)) == ([], False)


def test_the_unstructured_constraint_warning_is_gone():
    """「卡片约束未结构化」 摘除 (用户 定) —— 它已恒真。

    旧用例 test_partial_constraints_do_not_silence_the_ceiling_warning 钉的是
     的判据 (只填 vehicle 不算校验过, 必须看 max_classification)。
    那条判据没错, 错的是它守的东西在 v3 之后不存在了:
      card_gates = action.reason[:300]  → 每张卡都有 reason ⇒ 恒非空
      max_classification                → v3 () 废掉财报/thesis 语义,
                                          六张卡全拿掉, 全书 0 张
    ⇒  实测 **6/6 张 PLAN 全部触发**。100% 触发率 = 零信息量, 是
    「不会触发的检查比没有更危险」的另一端: 读的人会连着真信号一起学会略过。

    本用例反过来钉住"它不该回来" —— 真要重开, 先证明新判据不会重新变成恒真。
    [ 追记] clamp 机制已随分类轴退役; 卡片级收紧如需回归走数值字段另立。
    """
    from quant.decision.entry_guard import render
    base = {"ticker": "T", "verdict": "PLAN", "close": 100.0, "reasons": [], "lines": [],
            "card_gates": "卡片 prose 里写着某个天花板", "mode": "normal"}

    for constraints in ({}, {"vehicle": "leaps"}, {"max_classification": "probe"}):
        assert "卡片约束未结构化" not in render(
            [{**base, "constraints": constraints}], 1e5, "2026-08-12")


# ── regime 统一方案 a (用户 批): cap 0 VETO 保留修复地图 ──

def test_cap0_veto_still_renders_repair_map(monkeypatch):
    """cap 0 = 无授权 → VETO; 但阶梯/gate 行在 return 之前渲染 — 砍掉地图 =
    手册只剩一行 VETO, 看不到解除路径。VETO 与地图不矛盾。"""
    from quant.decision import entry_guard as eg
    rows = [{"time": f"2026-07-{d:02d} 00:00:00", "open": 90.0, "high": 95.0,
             "low": 88.0, "close": 90.0 + d * 0.1, "volume": 1e6} for d in range(1, 29)]
    monkeypatch.setattr(eg, "_rows", lambda tk: rows)
    monkeypatch.setattr(eg, "iv_gate_line", lambda tk: None)
    card = {"moomoo_listed": True, "agent": {
        "thesis": {"summary": "x", "status": "intact"},
        "action": {"intent": "wait_for_structure", "reason": ""},
        "current_regime": {"name": "修复观察", "cap_pct_nav": 0,
                           "participation": "gate_only", "since": "2026-08-04",
                           "source": "card", "reason": "修复路径"},
        "zones": {"accumulation_zone_1": [80.0, 84.0], "accumulation_zone_2": [70.0, 78.0],
                  "stop_level": 68.0, "valid_until": "2099-01-01",
                  "reclaim_gates": [{"level": 92.5, "days": 2}, {"level": 98.0, "days": 2}],
                  "anchored_on": {"date": "2026-08-04", "basis": "test"}},
        "next_regime": {"name": "B_repair_probe_98", "cap_pct_nav": 0.02,
                        "entry_gate": {"level": 98.0, "days": 2,
                                       "anchored_on": {"date": "2026-08-04"}}},
    }}
    r = eg.evaluate_ticker("TST", card, "2026-08-05", nav=22000.0)
    assert r["verdict"] == "VETO"
    assert any("cap 0" in x for x in r["reasons"])
    joined = "\n".join(r["lines"])
    assert "92.5" in joined, "reclaim 阶梯行被 cap-0 提前 return 砍掉了 (方案 a 回归)"
    assert "98" in joined


# ── 期权资格闸: gate1 起 + IV 硬门槛 (用户; 翻 08-13「R2 起」) ──

def _vehicle_card(cap_pct: float) -> dict:
    """干净候选卡, close 落 zone1 内 → PLAN。"""
    return {"moomoo_listed": True, "agent": {
        "thesis": {"summary": "x", "status": "intact"},
        "action": {"intent": "accumulate", "reason": "",
                   "constraints": {"vehicle": "leaps"}},
        "current_regime": {"name": "常规参与", "cap_pct_nav": cap_pct,
                           "participation": "zone_eligible", "since": "2026-08-01",
                           "source": "card", "reason": "test"},
        "zones": {"accumulation_zone_1": [88.0, 93.0], "accumulation_zone_2": [80.0, 87.0],
                  "trim_zone": [105.0, 110.0], "no_chase_above": 102.0,
                  "stop_level": 78.0, "valid_until": "2099-01-01",
                  "anchored_on": {"date": "2026-08-04", "basis": "test"}},
    }}


def _vehicle_rows() -> list:
    return [{"time": f"2026-07-{d:02d} 00:00:00", "open": 90.0, "high": 94.0,
             "low": 88.0, "close": 89.0 + d * 0.08, "volume": 1e6}
            for d in range(1, 29)]


def test_option_vehicle_iv_hard_gate_blocks_plan_and_says_so(monkeypatch):
    """gate1 可期权 (), 但 IV 硬门槛不过 = 不选约: 无 IV 数据 / 高 IV 都
    走 ⛔ 原文进 reasons 与渲染 (静默是本仓的病), option_plan.build 一次不调。"""
    from quant.decision import entry_guard as eg
    from quant.options import option_plan as op
    called = []
    monkeypatch.setattr(op, "build", lambda *a, **k: called.append(1) or {})
    monkeypatch.setattr(eg, "_rows", lambda tk: _vehicle_rows())
    monkeypatch.setattr(eg, "iv_gate_line", lambda tk: None)
    monkeypatch.setattr(eg, "option_iv_gate", lambda tk: {
        "allowed": False, "basis": "abs", "reason": "ATM IV 88% ≥ 50%", "samples": 51})
    r = eg.evaluate_ticker("TST", _vehicle_card(0.40), "2026-08-05", nav=22000.0)
    assert r["verdict"] == "PLAN"
    assert "option_plan" not in r and called == [], "IV 硬门槛被绕过"
    assert any("IV 硬门槛 VETO" in x and "ATM IV 88%" in x for x in r["reasons"])
    txt = eg.render([r], nav=22000.0, today="2026-08-05")
    assert "IV 硬门槛 VETO" in txt and "本档正股执行" in txt


def test_option_vehicle_past_iv_gate_builds_contract_plan_at_gate1(monkeypatch):
    """过了 IV 门的 gate1 vehicle 卡 → 当场翻成合约级计划 (08-13 前形状回归), 保费
    预算 = gate1 12.5% NAV, at-risk 保费上限 = 5%/0.20 = 25% NAV, stop 传正股
    max(卡 stop, 授予 cap 的 gate)。"""
    from quant.decision import entry_guard as eg
    from quant.options import option_plan as op
    seen = {}

    def fake_build(tk, ladder, cap, **kw):
        seen.update({"tk": tk, "cap": cap, **kw})
        return {"ticker": tk, "error": "链数据不可得 (测试)", "iv": {}, "gate": {"verdict": "?", "note": ""}}
    monkeypatch.setattr(op, "build", fake_build)
    monkeypatch.setattr(eg, "_rows", lambda tk: _vehicle_rows())
    monkeypatch.setattr(eg, "iv_gate_line", lambda tk: None)
    monkeypatch.setattr(eg, "option_iv_gate", lambda tk: {
        "allowed": True, "basis": "abs", "reason": "ATM IV 39% < 50%", "samples": 8})
    card = _vehicle_card(0.40)
    card["agent"]["regime_history"] = [{"date": "2026-08-04", "direction": "up",
                                        "level": 85.0, "cap_pct_nav": 0.40}]
    r = eg.evaluate_ticker("TST", card, "2026-08-05", nav=22000.0)
    assert r["verdict"] == "PLAN" and "option_plan" in r
    assert seen["tk"] == "TST" and seen["cap"] == pytest.approx(0.125 * 22000.0)
    assert seen["at_risk_premium_cap"] == pytest.approx(0.05 * 22000.0 / 0.20)
    assert seen["target_dte"] == 270 and seen["min_dte"] == 270
    assert seen["stop"] == 85.0, "期权正股退出线 = max(卡 stop 78, 授予 cap 的 gate 85)"
    assert any("期权 gate1 可用" in x and "12.5%" in x for x in r["reasons"])


def test_option_premium_cap_is_gate_scheduled():
    """保费上限随档 (sizing 单源, 用**本档新增**不用累计): gate1 = 12.5% /
    gate2 = 7.5% / gate3 = 5% NAV (|S1| 0.20,)。"""
    from quant.core.sizing import option_premium_cap_frac
    assert option_premium_cap_frac(1) == pytest.approx(0.125)
    assert option_premium_cap_frac(2) == pytest.approx(0.075)
    assert option_premium_cap_frac(3) == pytest.approx(0.05)


# ── 选约落库 write_option_plans (用户  Today/画线真源) ──

def test_write_option_plans_only_plan_with_sized_contracts(tmp_path, monkeypatch):
    from quant.decision import entry_guard as eg
    import quant.core.paths as paths
    out = tmp_path / "option_plans.json"
    monkeypatch.setattr(paths, "OPTION_PLANS_PATH", out)
    results = [
        {"ticker": "MSFT", "verdict": "PLAN", "option_plan_data": {
            "chosen": {"code": "US.MSFT270115C460000", "strike": 460.0, "delta": 0.71},
            "expiry": "2027-01-15", "dte": 523,
            "rungs": [{"premium_flat_iv": 55.0}, {"premium_flat_iv": 52.0}],
            "size": {"tiers": [{"shares": 1}, {"shares": 1}]}}},
        {"ticker": "AAOI", "verdict": "VETO", "option_plan_data": {
            "chosen": {"code": "US.AAOI270115C150000"},
            "rungs": [{"premium_flat_iv": 30.0}], "size": {"tiers": [{"shares": 1}]}}},
        {"ticker": "INTC", "verdict": "PLAN"},   # 无期权计划 (正股卡)
    ]
    n = eg.write_option_plans(results, "2026-08-10")
    assert n == 1
    import json as _json
    doc = _json.loads(out.read_text())
    assert list(doc["plans"]) == ["MSFT"], "VETO 不落 (授权语义), 无计划不落"
    p = doc["plans"]["MSFT"]
    assert p["code"] == "MSFT270115C460000", "US. 前缀必须剥掉 (state 存 OCC)"
    assert p["tiers"][0] == {"premium": 55.0, "contracts": 1}


# ── 第⑤条 at-risk 天花板 (N1  接线,  复查补测 — 此前零覆盖) ──

def test_at_risk_can_never_bind_on_the_entry_side_now():
    """ (用户 #3) 的结构性后果, 值得钉住:

    ②⑤ 现在**共用同一个 stop**, 于是它们的比恒为 gate_risk_cap_frac /
    AT_RISK_CAP_FRAC (gate1 = 0.5) —— 与 stop 距离无关。⑤ 在入场侧**可证明永不
    binding**, "stop >4N 才轮到 ⑤" 那条交叉点随 2N 分母一起消失了。保留 ⑤ 是为了
    表改动时不越 T + 与持仓侧同源; 真咬点在 at_risk_breach_signal (棘轮推 stop)。
    """
    from quant.core.sizing import AT_RISK_CAP_FRAC, GATE_RISK_CAPS
    for stop in (99.0, 92.0, 70.0, 20.0, 1.0):          # 近到极远, 全扫
        c = size_caps(21237, atr=4.0, t1=100.0, risk_stop=stop, agent=FULL)
        assert c["binding"] != "at_risk", f"stop={stop} 竟由 ⑤ binding"
        assert c["n_unit_cap"] <= c["at_risk_cap"] + 1e-9
    # 比值恒定 (不随 stop 变) —— 这就是 ⑤ 永不 binding 的原因
    c = size_caps(21237, atr=4.0, t1=100.0, risk_stop=70.0, agent=FULL)
    assert c["n_unit_cap"] / c["at_risk_cap"] == pytest.approx(
        GATE_RISK_CAPS[0] / AT_RISK_CAP_FRAC)
    assert c["at_risk_cap"] == pytest.approx(c["n_unit_cap"] * 2)


def test_at_risk_excluded_from_option_notional():
    """§b1: 标的 stop 不给期权折扣 — ⑤ (标的口径) 不得混进 notional_cap
    (期权的 at-risk 由 at_risk_premium_cap 保费口径单独管)。构造 ⑤ < n_unit
    的形状: 若 ⑤ 混进 notional, notional_cap 会等于它。"""
    c = size_caps(21237, atr=None, t1=100.0, risk_stop=70.0, agent=FULL,
                  usage={"used_budget": 0.0, "used_gross": 0.0})
    assert c["at_risk_cap"] is not None, "⑤ 必须仍被算出来 (只是不再 binding)"
    assert c["notional_cap"] is None or c["notional_cap"] != c["at_risk_cap"], \
        "标的口径 at-risk 混进了期权 notional 链 (§b1 违规)"


# ── verdict 重构 (用户: 移除 GO/WAIT — 挂 zone 限价单, 市价位置不驱动) ──

def _plan_card(z1=(88.0, 93.0), z2=(80.0, 87.0), trim=(105.0, 110.0),
               no_chase=102.0, stop=78.0) -> dict:
    return {"moomoo_listed": True, "agent": {
        "thesis": {"summary": "x", "status": "intact"},
        "action": {"intent": "accumulate", "reason": ""},
        "current_regime": {"name": "常规参与", "cap_pct_nav": 0.40,
                           "participation": "zone_eligible", "since": "2026-08-01",
                           "source": "card", "reason": "test"},
        "zones": {"accumulation_zone_1": list(z1), "accumulation_zone_2": list(z2),
                  "trim_zone": list(trim), "no_chase_above": no_chase,
                  "stop_level": stop, "valid_until": "2099-01-01",
                  "anchored_on": {"date": "2026-08-04", "basis": "test"}},
    }}


def _rows_ending_at(close: float) -> list:
    rows = [{"time": f"2026-07-{d:02d} 00:00:00", "open": 90.0, "high": 94.0,
             "low": 88.0, "close": 89.0 + d * 0.08, "volume": 1e6}
            for d in range(1, 29)]
    rows.append({"time": "2026-08-04 00:00:00", "open": close, "high": close + 1,
                 "low": close - 1, "close": close, "volume": 1e6})
    return rows


def _eval_at(monkeypatch, close, card=None):
    from quant.decision import entry_guard as eg
    monkeypatch.setattr(eg, "_rows", lambda tk: _rows_ending_at(close))
    monkeypatch.setattr(eg, "iv_gate_line", lambda tk: None)
    return eg.evaluate_ticker("TST", card or _plan_card(), "2026-08-05", nav=22000.0)


def test_waiting_position_now_plans(monkeypatch):
    """等待区不再 WAIT — 出 zone 限价单计划 (: 铃响那一刻手上要有计划)。"""
    r = _eval_at(monkeypatch, 97.0)
    assert r["verdict"] == "PLAN" and r["ladder"], r["reasons"]
    assert all(p <= 93.0 for p in r["ladder"]), "计划必须锚 zone, 不贴市价"
    assert any("📍" in x and "等待区" in x for x in r["lines"])


def test_above_no_chase_now_plans_with_position_line(monkeypatch):
    """超 no_chase 不再 VETO 整卡 — 它罚的是市价追买, 与 z1 限价单无关。"""
    r = _eval_at(monkeypatch, 104.0)
    assert r["verdict"] == "PLAN" and r["ladder"]
    assert any("no_chase" in x and "不追市价" in x for x in r["lines"])
    assert all(p <= 93.0 for p in r["ladder"])


def test_trim_position_now_plans(monkeypatch):
    card = _plan_card(no_chase=112.0, trim=(105.0, 110.0))   # trim 带在 no_chase 之下
    # 渐进抵达 trim: _rows_ending_at 的末根瞬移 +17.4% (2.6×ATR) 会正当触发
    # Use a gradual arrival so this test does not independently trigger volatility staleness.
    rows = _rows_ending_at(107.0)
    rows[-2].update(open=105.0, high=107.5, low=104.0, close=106.0)
    from quant.decision import entry_guard as eg
    monkeypatch.setattr(eg, "_rows", lambda tk: rows)
    monkeypatch.setattr(eg, "iv_gate_line", lambda tk: None)
    r = eg.evaluate_ticker("TST", card, "2026-08-05", nav=22000.0)
    assert r["verdict"] == "PLAN"
    assert any("trim 区" in x for x in r["lines"])


def test_below_z2_close_is_stale_day_one_not_just_veto(monkeypatch):
    """The first completed close below zone 2 makes the map stale."""
    r = _eval_at(monkeypatch, 79.0)
    assert r["verdict"] == "STALE"
    assert "breakdown" in str(r), r


def test_below_stop_close_is_stale_day_one(monkeypatch):
    """破 stop 收盘同理 (更深) — STALE 指向重画; 降级归收盘转移机。"""
    r = _eval_at(monkeypatch, 76.0)
    assert r["verdict"] == "STALE"
    assert "breakdown" in str(r), r


def test_plan_carries_single_stop_and_trim(monkeypatch):
    """显示合同: 一个建仓位 (ladder) / 一个止损位 (结构) / 一个止盈位 (trim)。
    2N 保留为 sizing 内部量, 不再是第二条止损线。"""
    r = _eval_at(monkeypatch, 91.0)
    assert r["verdict"] == "PLAN"
    assert r["stops"]["display"] == 78.0
    assert r["stops"]["display_basis"] == "结构位"
    assert r["trim"] == [105.0, 110.0]
    from quant.decision.entry_guard import render
    txt = render([r], nav=22000.0, today="2026-08-05")
    assert "预设止损: 78" in txt and "止盈 trim 105-110" in txt
    assert "双线先到先触发" not in txt


def test_log_decisions_only_on_change(tmp_path, monkeypatch):
    """PLAN 是常态后逐日全落 = 噪音; 变动才落 = 重锚审计轨迹。"""
    from quant.decision import entry_guard as eg
    dec = tmp_path / "decisions.jsonl"
    monkeypatch.setattr(eg, "DECISIONS_PATH", dec)
    r = {"ticker": "T", "verdict": "PLAN", "classification": "confirmed",
         "mode": "normal", "ladder": [93.0, 88.0, 83.5],
         "caps": {"final": 2000.0}, "stops": {"display": 78.0}, "constraints": {}}
    assert eg.log_decisions([r]) == 1
    assert eg.log_decisions([r]) == 0, "同一份计划不重复落库"
    r2 = dict(r, ladder=[94.0, 88.0, 83.5])          # 重锚 → 价位变 → 落
    assert eg.log_decisions([r2]) == 1
    lines = [ln for ln in dec.read_text().splitlines() if ln.strip()]
    assert len(lines) == 2


def test_entry_guard_calls_option_plan_only_past_the_iv_gate(monkeypatch):
    """ 起 gate1 可期权, 但 option_plan.build 的**唯一**入口在 IV 硬门槛之后:
    无 IV 数据 (测试环境 TST 无 iv_history) = 不放行 = 不调。"""
    from quant.decision import entry_guard as eg
    from quant.options import option_plan as op
    called = []
    monkeypatch.setattr(op, "build", lambda *a, **k: called.append(1) or {})
    monkeypatch.setattr(eg, "_rows", lambda tk: _vehicle_rows())
    monkeypatch.setattr(eg, "iv_gate_line", lambda tk: None)
    r = eg.evaluate_ticker("TST", _vehicle_card(0.40), "2026-08-05", nav=22000.0)
    assert r["verdict"] == "PLAN"
    assert called == [], "无 IV 数据仍调了 option_plan.build — 硬门槛被绕过"
    assert r["option_iv_gate"]["basis"] == "none"


# ── 当前 regime 的 no_chase 无条件印 (用户) ───────────
#
# 此前它只在 above_no_chase 那一支出现 = 被突破了才说, 而 next_regime 的整套
# 仓位层 (含它自己的 no_chase) 是**无条件**渲染的 ⇒ 块上唯一看得见的 no_chase
# 是还没生效的那条。COHR : 当前 339 / 下一档 374, 差 10%, 现价
# 351.62 正好夹在中间。

_ZB = {"z1": [313.5, 322.0], "z2": [304.0, 313.5], "trim": [348.0, 365.0],
       "no_chase": 339.0, "stop": 303.0}


@pytest.mark.parametrize("pos,close", [
    ("waiting", 328.57), ("zone1", 318.0), ("zone2", 308.0),
    ("gap_z1_z2", 313.5), ("trim", 350.0),
])
def test_current_no_chase_is_printed_in_every_position(pos, close):
    from quant.decision.entry_guard import _position_line
    assert "no_chase 339" in _position_line(pos, close, _ZB)


def test_above_no_chase_names_it_once_not_twice():
    """那一支本来就点名了它 —— 不重复成 'no_chase 339 ... | no_chase 339'。"""
    from quant.decision.entry_guard import _position_line
    line = _position_line("above_no_chase", 350.0, _ZB)
    assert line.count("no_chase") == 1 and "339" in line


def test_card_without_no_chase_stays_clean():
    from quant.decision.entry_guard import _position_line
    zb = {**_ZB, "no_chase": None}
    assert "no_chase" not in _position_line("waiting", 328.57, zb)


def test_the_position_line_keeps_its_own_content():
    """加尾巴不许把原信息挤掉 —— 距离/语义都还在。"""
    from quant.decision.entry_guard import _position_line
    line = _position_line("waiting", 328.57, _ZB)
    assert "等待区" in line and "距 z1 上沿" in line and "no_chase 339" in line


# ── breakdown and volatility staleness ──────────────────────────

def test_breakdown_stale_fires_on_day_one():
    """A day-one breakdown is stale even when the prior close was inside the map."""
    rows = [_bar("2026-07-14", 92.0), _bar("2026-07-15", 81.0)]   # 昨在带内, 今破 84
    hits = zone_staleness(ZB, {"last_reviewed": "2026-07-10"}, rows, "2026-07-17")
    assert any("breakdown" in h for h in hits), hits
    assert any("当日" in h for h in hits)


def test_volatility_stale_uses_per_ticker_atr_ruler():
    """|单日收收| > 2×ATR14 才 stale — 固定 10% 从未进代码且量不了两个池
    (LITE ATR 10.6% 的 ±10% 是日常, MSFT +15.5% = 3.8N 是地震)。"""
    calm = [_bar(f"2026-06-{i + 1:02d}", 100.0) for i in range(20)]   # ATR14 ≈ 2
    shock = calm + [_bar("2026-06-21", 92.0)]                          # -8% ≈ 3.2×ATR
    hits = zone_staleness(ZB, {"last_reviewed": "2026-06-15"}, shock, "2026-06-22")
    assert any("volatility stale" in h for h in hits), hits
    # 对照: -3% < 2×ATR14 → 沉默 (同一形状换个幅度, 杀"恒真"变体)
    mild = calm + [_bar("2026-06-21", 97.0)]
    hits2 = zone_staleness(ZB, {"last_reviewed": "2026-06-15"}, mild, "2026-06-22")
    assert not any("volatility" in h for h in hits2), hits2


def test_volatility_stale_silent_when_atr_unavailable():
    """序列不足以算 ATR14 → 波动腿沉默 (不造固定尺假触发), 其余腿照常。"""
    rows = [_bar("2026-07-14", 97.0), _bar("2026-07-15", 88.0)]   # -9.3%, 但仅 2 根
    hits = zone_staleness(ZB, {"last_reviewed": "2026-07-10"}, rows, "2026-07-17")
    assert not any("volatility" in h for h in hits), hits


# ══════════════════════════════════════════════════════════════
# 短期道氏结构位 = 清单第一排序键
# ══════════════════════════════════════════════════════════════

def _plan(ticker, tier, wrank=0, close=100.0):
    from quant.core import dow
    return {"ticker": ticker, "close": close, "verdict": "PLAN", "mode": "normal",
            "lines": [], "reasons": [], "ladder": None, "caps": {"gate_no": 1},
            "dow": {"tier": tier, "short": dow.PRIORITY_CN[tier], "cn": f"{dow.PRIORITY_CN[tier]}·x",
                    "sort_key": (tier, wrank), "weekly_cn": None, "stage_cn": "s"}}


def test_plan_blocks_are_ordered_by_structure_tier_then_weekly():
    """同为 PLAN：优先 → 次位 → 末位；同档周线级打平局。VETO 仍排在所有 PLAN 之后。"""
    from quant.decision import entry_guard as eg
    res = [_plan("C", 3), _plan("B", 2), _plan("A", 1, wrank=1), _plan("A0", 1, wrank=0),
           {"ticker": "V", "close": 50.0, "verdict": "VETO", "lines": [], "reasons": ["x"],
            "dow": {"tier": 1, "short": "优先", "cn": "优先·x", "sort_key": (1, 0)}}]
    txt = eg.render(res, nav=22000.0, today="2026-08-22")
    heads = [ln for ln in txt.splitlines() if ln.startswith(("✅", "⛔", "🟢", "🔴", "🚫", "⚠️", "🟡"))]
    order = [h.split()[1] for h in heads if h.split()[1] in ("A0", "A", "B", "C", "V")]
    assert order[:4] == ["A0", "A", "B", "C"] and order[-1] == "V"
    assert "PLAN(gate1)·优先" in txt and "PLAN(gate1)·次位" in txt and "PLAN(gate1)·末位" in txt


def test_structure_line_names_the_tier_and_both_levels():
    """块内「结构位」行 = 排序的依据，缺了它排序就不可解释。"""
    from quant.decision import entry_guard as eg
    pr = {"tier": 2, "short": "次位", "cn": "次位·终止观察②", "sort_key": (2, 0),
          "weekly_cn": "上涨·推进中", "stage_cn": "上涨·终止观察②（lsl 已成形，看反弹 vs lsh）"}
    ln = eg.dow_line(pr)
    assert ln.startswith("结构位 次位·终止观察②") and "周线级 上涨·推进中" in ln
    assert "周线级" not in eg.dow_line({**pr, "weekly_cn": None})


def test_structure_tier_never_changes_the_verdict_or_the_numbers(monkeypatch):
    """只排序不授权：评估结果里 dow 是附加键，verdict / ladder / caps 与没有它时逐字相同。"""
    from quant.decision import entry_guard as eg
    monkeypatch.setattr(eg, "_rows", lambda tk: _vehicle_rows())
    monkeypatch.setattr(eg, "iv_gate_line", lambda tk: None)
    r = eg.evaluate_ticker("TST", _vehicle_card(0.40), "2026-08-05", nav=22000.0)
    assert r["dow"]["tier"] in (1, 2, 3) and any(x.startswith("结构位 ") for x in r["lines"])
    monkeypatch.setattr(eg, "dow_priority", lambda rows: {"tier": 3, "short": "末位", "cn": "末位·x",
                                                          "sort_key": (3, 3), "weekly_cn": None,
                                                          "stage_cn": "s"})
    r2 = eg.evaluate_ticker("TST", _vehicle_card(0.40), "2026-08-05", nav=22000.0)
    assert (r2["verdict"], r2["ladder"], r2["caps"]) == (r["verdict"], r["ladder"], r["caps"])


# ── 一览表 = GFM 表 (, tg_notify --rich 渲染成 Telegram 原生表格) ──

def test_summary_line_is_a_pipe_table_row_with_escaped_bars():
    r = {"verdict": "VETO", "ticker": "MU", "close": 925.0,
         "reasons": ["结构未修复 | trend down"], "lines": []}
    row = entry_guard._summary_line(r)
    assert row.startswith("| ⛔ MU 925 | VETO | ") and row.endswith(" |")
    assert "结构未修复 \\| trend down" in row            # 竖线转义, 列不被切歪
    assert entry_guard.SUMMARY_HEADER[1] == "|---|---|---|"


def test_render_overview_emits_header_row_before_ticker_rows():
    rs = [{"verdict": "VETO", "ticker": "A", "close": 1.0, "reasons": ["x"], "lines": []},
          {"verdict": "VETO", "ticker": "B", "close": 2.0, "reasons": ["y"], "lines": []}]
    out = entry_guard.render(rs, None, "2026-09-04").splitlines()
    i = out.index("── 一览 ──────────────")
    assert out[i + 1:i + 3] == list(entry_guard.SUMMARY_HEADER)
    assert out[i + 3].startswith("| ⛔ A 1 | VETO | x |")
