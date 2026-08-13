"""Three-state regime window: last (observation) / current / next (authorized).

用户 : "系统的核心是 gate — gate 是状态转移锚点, 每个 gate 都会有
自己的一套完整的 zone"。Before this, only the up branch existed (reclaim_gates on
8 cards, next_regime on exactly 1). The down branch was undefined, so a broken
stop left the system with no successor state and the only available move was to
redraw the line by hand — which is how a losing position's stop ends up being
moved *down*, the one direction the ratchet forbids.

The load-bearing invariant here: `last` carries cap 0 and never authorizes entry.
A `last` with budget would be a structural averaging-down machine — buy the
breakdown, automatically, forever.
"""
import json
import os

import pytest

from quant.decision import regime_transition as rt
from quant.core import schema

from quant.core.sizing import NOTIONAL_CAP as _CONF   #  分类轴退役: cap 二值上界


@pytest.fixture(autouse=True)
def _no_bench(monkeypatch):
    """量腿基准隔离: fixture 日期是真实 2026-07 日 — 不隔离会读到 live 缓存的
    SMH/QQQ 真数据, 测试随缓存漂移。默认置 None = 绝对量比兜底 (老口径);
    相对口径的用例各自显式注入。"""
    monkeypatch.setattr(rt, "_bench_ratio", lambda bench, day: None)


def _bars(closes, start_day=1):
    """close-only bars are enough for close-based gates; give H/L for ATR."""
    return [{"time": f"2026-07-{start_day + i:02d} 00:00:00", "open": c, "high": c * 1.01,
             "low": c * 0.99, "close": c, "volume": 1_000_000} for i, c in enumerate(closes)]


def _card(stop=800.0, days=2, next_regime=None, last_regime=None):
    agent = {"thesis": {"summary": "t"}, "action": {"intent": "hold"},
             "current_regime": {"name": "R_hold", "cap_pct_nav": _CONF,
                                "participation": "zone_eligible",
                                "since": "2026-07-01", "source": "auto"},
             "zones": {"accumulation_zone_1": [885.0, 925.0], "stop_level": stop,
                       "stop_level_days": days}}
    if next_regime is not None:
        agent["next_regime"] = next_regime
    if last_regime is not None:
        agent["last_regime"] = last_regime
    return {"instrument": {"type": "stock"}, "status": "active",
            "broker": {"qty": 1.0}, "agent": agent,
            "lots": {"base": [], "position": []}, "position_summary": {},
            "pending_orders": []}


NEXT = {"name": "B_confirmed", "entry_gate": {"level": 1008.0, "days": 2, "unlocks": "confirmed"},
        "cap_pct_nav": _CONF, "provisional": ["accumulation_zone_1", "trim_zone"],
        "zones": {"accumulation_zone_1": [1010.0, 1040.0], "stop_level": 960.0},
        "next_gate_preview": {"level": 1150.0, "days": 2, "cap_pct_nav": _CONF}}


# ── 1. detection is mechanical and close-based ────────────────

def test_down_transition_needs_the_full_confirmation_window():
    card = _card(stop=800.0, days=2)
    one_below = _bars([850.0, 795.0])

    assert rt.detect_transition("MU", card, one_below) is None


def test_down_transition_fires_on_the_confirming_close():
    tr = rt.detect_transition("MU", _card(), _bars([799.0, 795.0]))

    assert tr["direction"] == "down" and tr["level"] == 800.0


def test_intraday_wick_below_the_stop_is_not_a_transition():
    """卡片写的是收盘口径 — 插针不算 (bar low 远低于 stop, close 在上)。"""
    bars = _bars([810.0, 805.0])
    for b in bars:
        b["low"] = 700.0

    assert rt.detect_transition("MU", _card(), bars) is None


def test_up_transition_requires_the_gate_and_volume():
    card = _card(next_regime={**NEXT, "entry_gate": {**NEXT["entry_gate"],
                                                     "volume_ratio_min": 1.2}})
    thin = _bars([1010.0] * 25)                       # 量比 = 1.0 < 1.2

    assert rt.detect_transition("MU", card, thin) is None
    heavy = _bars([1010.0] * 25)
    heavy[-1]["volume"] = 5_000_000
    assert rt.detect_transition("MU", card, heavy)["direction"] == "up"


def test_up_transition_relative_volume_leg_rescues_quiet_tape(monkeypatch):
    """INTC 8/4-8/5 形 (relative_volume 裁决 R,): 绝对量比 1.0 < 1.2
    但板块同缩量 (SMH 0.5) → RelVR = 1.0/0.5 = 2.0 过闸, reason 带相对口径收据。"""
    monkeypatch.setattr(rt, "_bench_ratio", lambda bench, day: 0.5)
    card = _card(next_regime={**NEXT, "entry_gate": {**NEXT["entry_gate"],
                                                     "volume_ratio_min": 1.2}})
    thin = _bars([1010.0] * 25)                       # 自身量比 = 1.0, 老口径拦
    tr = rt.detect_transition("MU", card, thin)
    assert tr and tr["direction"] == "up"
    assert "rel SMH" in tr["reason"] and "2.00" in tr["reason"]


def test_volume_leg_floor_and_fallback(monkeypatch):
    """分母地板 0.4 (基准 0.2 不放大成 5×); 基准缺日 → 绝对兜底且 note 说出口。"""
    monkeypatch.setattr(rt, "_bench_ratio", lambda bench, day: 0.2)
    ok, note = rt.volume_leg(_bars([100.0] * 25), 1.2, "MU")
    assert ok and "2.50" in note                      # 1.0 / max(0.2, 0.4)
    monkeypatch.setattr(rt, "_bench_ratio", lambda bench, day: None)
    ok2, note2 = rt.volume_leg(_bars([100.0] * 25), 1.2, "MU")
    assert not ok2 and "abs兜底" in note2
    assert rt.volume_leg(_bars([100.0] * 25), None, "MU") == (True, None)


def test_down_wins_when_both_could_read_as_triggered():
    """风险先行 — 而且这两件事实际互斥, 同时成立说明数据有问题。"""
    card = _card(stop=1100.0, next_regime=NEXT)
    tr = rt.detect_transition("MU", card, _bars([1010.0, 1020.0]))

    assert tr["direction"] == "down"


# ── 2. the rolling window ─────────────────────────────────────

def test_up_transition_rolls_the_window():
    card = _card(next_regime=NEXT)
    bars = _bars([1010.0, 1020.0])
    tr = rt.detect_transition("MU", card, bars)
    rt.apply_transition(card, tr, bars)
    agent = card["agent"]

    assert agent["zones"]["accumulation_zone_1"] == [1010.0, 1040.0]   # current ← next
    assert agent["last_regime"]["zones"]["stop_level"] == 800.0        # last ← 旧 current
    assert agent["next_regime"]["entry_gate"]["level"] == 1150.0       # next ← preview 骨架
    assert agent["next_regime"]["zones"] is None                       # 骨架没有 zone


def test_up_transition_writes_bfloor_buf_stop():
    """bfloor_buf 写入强制点 (ledger A3): 上行转移后的地板 = 收复线 − 0.5×ATR20
    (转移日), 收盘 ×1 日 — round-2b (2026-07-30) 批的约定, 此前只活在文档里,
    转移机原样搬画卡旧 stop (NEXT 写 960, 与约定差多少全看画卡的人)。"""
    from quant.core import technical
    card = _card(next_regime=NEXT)
    bars = _bars([1000.0] * 24 + [1010.0, 1020.0])
    tr = rt.detect_transition("MU", card, bars)
    rt.apply_transition(card, tr, bars)
    z = card["agent"]["zones"]

    atr20 = technical.atr(bars, rt.PARAMS["atr_period"])[-1]
    assert z["stop_level"] == pytest.approx(round(1008.0 - 0.5 * atr20, 2))
    assert z["stop_level_days"] == 1                       # 确认口径 ×1 日
    assert "bfloor_buf" in z["stop_derivation"]
    assert "覆盖画卡值 960" in z["stop_derivation"]          # 被换掉的旧 stop 留痕


def test_up_transition_without_atr_keeps_card_stop():
    """序列不足算不出 ATR → 不编数, 保持画卡值 (旧行为), stop 留给重画。"""
    card = _card(next_regime=NEXT)
    bars = _bars([1010.0, 1020.0])                          # 2 根, ATR20 = None
    rt.apply_transition(card, rt.detect_transition("MU", card, bars), bars)
    z = card["agent"]["zones"]

    assert z["stop_level"] == 960.0
    assert "stop_derivation" not in z


def test_demoted_current_loses_its_budget_on_the_way_down():
    """旧 current 变成 last 的那一刻资格归零 —— 数值不变, cap 变 0。"""
    card = _card(next_regime=NEXT)
    bars = _bars([1010.0, 1020.0])
    rt.apply_transition(card, rt.detect_transition("MU", card, bars), bars)
    last = card["agent"]["last_regime"]

    assert last["cap_pct_nav"] == 0
    assert last["participation"] == "gate_only"
    assert last["repair_gate"]["level"] == 1008.0     # 回来的路 = 刚过的那道 gate


def test_up_transition_carries_fired_gate_anchor_into_repair_gate():
    """8/5 MU/INTC 实证: 转移机造 repair_gate 时把源 gate 的 anchored_on 丢了,
    落地即欠审计账 (📋 无 anchored_on)。锚要跟着 gate 走, 不是跟着容器走。"""
    anchored = {"date": "2026-07-29", "basis": "987-1008 HVN 墙"}
    card = _card(next_regime={**NEXT, "entry_gate": {**NEXT["entry_gate"],
                                                     "anchored_on": anchored}})
    bars = _bars([1010.0, 1020.0])
    rt.apply_transition(card, rt.detect_transition("MU", card, bars), bars)

    assert card["agent"]["last_regime"]["repair_gate"]["anchored_on"] == anchored


def test_down_transition_lands_in_the_observation_regime():
    obs = rt.derive_observation_regime(_bars([900, 880, 860, 870, 850, 840, 860, 830]), 800.0)
    card = _card(last_regime=obs)
    bars = _bars([799.0, 795.0])
    rt.apply_transition(card, rt.detect_transition("MU", card, bars), bars)
    agent = card["agent"]

    assert agent["zones"] == obs["zones"]                       # current ← 旧 last
    assert agent["action"]["intent"] == "wait_for_structure"    # 资格没了
    assert agent["next_regime"]["entry_gate"]["level"] == 800.0  # 回去的目标 = 破掉的线
    assert agent["next_regime"]["zones"]["stop_level"] == 800.0  # 旧 current 原样留着


def test_a_new_observation_layer_is_derived_below():
    card = _card(last_regime=rt.derive_observation_regime(
        _bars([900, 880, 860, 870, 850, 840, 860, 830]), 800.0))
    bars = _bars([799.0, 795.0])
    rt.apply_transition(card, rt.detect_transition("MU", card, bars), bars)

    assert card["agent"]["last_regime"]["cap_pct_nav"] == 0
    assert card["agent"]["last_regime"]["source"] == "auto"


# ── 3. pending repaint ────────────────────────────────────────

def test_up_transition_marks_provisional_slots_for_repaint():
    card = _card(next_regime=NEXT)
    bars = _bars([1010.0, 1020.0])
    rt.apply_transition(card, rt.detect_transition("MU", card, bars), bars)
    pr = card["agent"]["pending_repaint"]

    assert pr["slots"] == ["current", "next"]
    assert "NEEDS_CARD" in pr["reason"]


def test_down_transition_needs_no_authorization_grade_repaint():
    """新 current 的 cap 是 0 — 本来就不该入场, 没有要补的授权。"""
    card = _card(last_regime=rt.derive_observation_regime(_bars([900, 850, 830]), 800.0))
    bars = _bars([799.0, 795.0])
    rt.apply_transition(card, rt.detect_transition("MU", card, bars), bars)

    assert "pending_repaint" not in card["agent"]


def test_transition_is_recorded_in_history():
    card = _card(next_regime=NEXT)
    bars = _bars([1010.0, 1020.0])
    rt.apply_transition(card, rt.detect_transition("MU", card, bars), bars)

    assert card["agent"]["regime_history"][-1]["direction"] == "up"


# ── 4. mechanical observation-grade derivation ────────────────

def test_observation_zones_are_all_provisional_and_flagged_auto():
    obs = rt.derive_observation_regime(_bars([900, 880, 860, 870, 850]), 800.0)

    assert obs["source"] == "auto"
    assert set(obs["provisional"]) >= {"accumulation_zone_1", "accumulation_zone_2"}
    assert "未查宏观 context" in obs["derivation"]


def test_observation_zones_are_ordered_even_without_pivots():
    """没有 pivot 时按 ATR 退档, 不能吐出 lo >= hi 的区间。"""
    obs = rt.derive_observation_regime(_bars([900.0] * 30), 800.0)
    z = obs["zones"]

    assert z["accumulation_zone_1"][0] < z["accumulation_zone_1"][1]
    assert z["accumulation_zone_2"][0] < z["accumulation_zone_2"][1]
    assert z["stop_level"] < z["accumulation_zone_2"][0]


def test_air_pocket_is_flagged_when_support_is_far_below():
    """破位下方没有近端结构才是该被看见的事, 不是"下一档在 X"。"""
    bars = _bars([900, 880, 500, 890, 870])          # 唯一的低点在 -37%
    obs = rt.derive_observation_regime(bars, 800.0)

    assert obs["air_pocket"] is True
    assert "空气区" in obs["derivation"]


def test_repair_gate_points_back_at_the_broken_line():
    obs = rt.derive_observation_regime(_bars([900, 850, 830]), 800.0)

    assert obs["repair_gate"]["level"] == 800.0


# ── 5. schema is the backstop ─────────────────────────────────

def _doc(last):
    card = _card(last_regime=last)
    return {"tickers": {"MU": card}}


def test_schema_rejects_a_last_regime_with_budget():
    bad = rt.derive_observation_regime(_bars([900, 850, 830]), 800.0)
    bad["cap_pct_nav"] = 0.01
    errors, _ = schema.validate_positions_doc(_doc(bad))

    assert any("cap_pct_nav must be exactly 0" in e for e in errors)


def test_schema_rejects_a_tradeable_last_regime():
    bad = rt.derive_observation_regime(_bars([900, 850, 830]), 800.0)
    bad["participation"] = "zone_eligible"
    errors, _ = schema.validate_positions_doc(_doc(bad))

    assert any("participation must be 'gate_only'" in e for e in errors)


def test_schema_rejects_observation_zones_presented_as_settled():
    bad = rt.derive_observation_regime(_bars([900, 850, 830]), 800.0)
    bad["provisional"] = []
    errors, _ = schema.validate_positions_doc(_doc(bad))

    assert any("provisional must cover" in e for e in errors)


def test_schema_accepts_a_well_formed_observation_regime():
    good = rt.derive_observation_regime(_bars([900, 880, 860, 870, 850]), 800.0)

    assert schema.validate_positions_doc(_doc(good))[0] == []


def test_schema_caps_next_regime_at_the_confirmed_ceiling():
    # 上界跟着 CAP_FRACS["confirmed"] 走 (: 0.05 → 0.40), 所以超界值也得跟着抬
    card = _card(next_regime={**NEXT, "cap_pct_nav": _CONF + 0.10})
    errors, _ = schema.validate_positions_doc({"tickers": {"MU": card}})

    assert any("cap_pct_nav must be numeric in" in e for e in errors)


@pytest.mark.skipif(os.environ.get("TRADINGOPS_CHECK_LIVE_STATE") != "1",
                    reason="set TRADINGOPS_CHECK_LIVE_STATE=1 to scan local account state")
def test_live_last_regime_blocks_carry_cap_zero_and_a_coherent_gate():
    """活跃持仓的 `last_regime` = 下行分支: **cap 恒 0**+ 一条不与 stop 倒挂的修复 gate。

    cap 0 是本文件开头那条 load-bearing invariant (带预算的 last = 自动
    averaging down 机器)。gate 一侧复用 `structure.gate_coherence` 的 down 分支
    (修复 gate < stop = 倒挂) —— **判断只留一份**, 不在测试里另写一套。

     重写, 原版是 `for tk in ("MU", "NBIS")` + `repair_gate == stop_level`,
    两半都过期了:
    - **MU 已 closed**(qty 0), 早就不在"活跃持仓"这个域里;
    - **等号恰恰是向上转移后不该成立的**。等号只在 `derive_observation_regime`
      的下行构造里成立 (被破掉的那条线**既**是要收复的 gate **又**是 stop)。
      NBIS 8/12 向上转移后走 `bfloor_buf` 约定 = 刚收复的 gate − 0.5×ATR20@转移日:
      231 − 0.5×25.77 = **218.11**= 卡上的 stop。live 三张转移过的卡
      (INTC 98/93.9 · MRVL 189/179.8 · NBIS 231/218.11) 全是这个形状, 没有一张等号。
    canonical 只要求 `修复 gate ≥ 本 regime stop`(`core.structure.gate_coherence`),
    收紧成等号 = 测试自己发明了一条比系统更严的规矩。
    """
    from quant.core import holdings, structure
    doc = json.loads((rt.state_io.POSITIONS_PATH).read_text(encoding="utf-8"))
    checked = []
    for tk in sorted(holdings.live_tickers(doc)):
        agent = (doc["tickers"][tk].get("agent") or {})
        last = agent.get("last_regime")
        if not isinstance(last, dict) or not last:
            continue                    # 从未向下转移过的卡没有 last —— 不是缺陷
        assert last.get("cap_pct_nav") == 0, f"{tk}: last_regime 带预算 = 自动 averaging down"
        assert (last.get("repair_gate") or {}).get("level") is not None, \
            f"{tk}: last_regime 没有修复 gate — 下行分支没有回来的路"
        assert not [w for w in structure.gate_coherence(agent) if "修复 gate" in w], \
            f"{tk}: 修复 gate 与 stop 倒挂 — {structure.gate_coherence(agent)}"
        checked.append(tk)
    assert checked, "一张带 last_regime 的活跃卡都没有 — 判据可能挑错了 (检查静默为空)"


@pytest.mark.skipif(os.environ.get("TRADINGOPS_CHECK_LIVE_STATE") != "1",
                    reason="set TRADINGOPS_CHECK_LIVE_STATE=1 to scan local account state")
def test_live_current_regime_caps_are_binary():
    """cap 二值不变量 (分类轴退役 + D7 存量迁移): 全书活跃卡的
    current_regime.cap_pct_nav ∈ {0, NOTIONAL_CAP} —— 出现第三个值 = 有人在
    造中间档 (probe 的影子), 或迁移漏卡。"""
    from quant.core.paths import WATCHLIST_PATH
    allowed = {0, 0.0, _CONF}
    for path in (rt.state_io.POSITIONS_PATH, WATCHLIST_PATH):
        doc = json.loads(path.read_text(encoding="utf-8"))
        for tk, card in (doc.get("tickers") or {}).items():
            cr = (card.get("agent") or {}).get("current_regime") or {}
            cap = cr.get("cap_pct_nav")
            if cap is None:
                continue                      # closed/期权卡无 regime, schema 管
            assert cap in allowed, \
                f"{path.name}:{tk} cap={cap} 不在二值集 {{0, {_CONF}}} — 中间档回潮?"


# ── watchlist 桶 (用户 裁决 A: 自动切) ─────────────

def test_run_scans_watchlist_bucket_too(monkeypatch):
    """此前 run() 只扫 positions —— "约定由文件位置决定, 不由规则决定",
    8/4 首夜 LITE/MRVL/MSFT 三笔达标未切 (open ruling → 裁决 A 自动切)。"""
    card = _card(next_regime={k: (v.copy() if isinstance(v, dict) else v)
                              for k, v in NEXT.items()})
    bars = _bars([1010.0] * 25)
    bars[-1]["volume"] = 5_000_000
    wl = {"tickers": {"LITE": card}}
    written = {}
    monkeypatch.setattr(rt.state_io, "read_positions", lambda **k: {"tickers": {}})
    monkeypatch.setattr(rt.state_io, "read_watchlist", lambda **k: wl)
    monkeypatch.setattr(rt.state_io, "write_positions",
                        lambda d: written.setdefault("pos", d))
    monkeypatch.setattr(rt.state_io, "write_watchlist",
                        lambda d: written.setdefault("wl", d))
    monkeypatch.setattr(rt, "_klines", lambda t: bars)

    out = rt.run(apply=True)

    assert [t["bucket"] for t in out["transitions"]] == ["watchlist"]
    assert "wl" in written and "pos" not in written        # 只写有转移的桶
    agent = wl["tickers"]["LITE"]["agent"]
    assert agent["zones"]["accumulation_zone_1"] == [1010.0, 1040.0]  # next 已接管


def test_run_watchlist_skips_delisted_cards(monkeypatch):
    card = _card(next_regime=NEXT)
    card["moomoo_listed"] = False                          # 移出自选的卡不参与
    monkeypatch.setattr(rt.state_io, "read_positions", lambda **k: {"tickers": {}})
    monkeypatch.setattr(rt.state_io, "read_watchlist",
                        lambda **k: {"tickers": {"X": card}})
    monkeypatch.setattr(rt, "_klines", lambda t: _bars([1010.0] * 25))
    assert rt.run(apply=False)["transitions"] == []


# ── 期权合约永不进状态机 (INFRA-1) ─────────────────

def test_option_contracts_never_enter_the_state_machine():
    """源案例 GOOGL270115C390000 : 保费收盘比 underlying 基准的 stop。

    8/10 起活仓合约进 `kline_cache` 刷新集 → 合约有了自己的日线 (**保费**20.70 /
    21.26), 而卡片 stop 358.5 是 `stop_level_basis="underlying"`。本模块无期权分支,
    于是造出一条假破位; apply 后 zones 丢掉 `stop_level_basis`, 整个 positions 桶的
    写入被 schema 拦下 → **当日零转移被应用, 全票状态机变黑**。

    regime 是正股层语义 (`schema.regime_shape` 对期权卡直接 return), 且
    `derive_observation_regime` 结构上造不出带 basis 的期权 zones —— 所以判据不是
    "换个 series 比", 是"这类卡根本不参与"。期权退出归 exit_guard 的期权 overlay。
    """
    card = _card(stop=358.5, days=2)
    card["instrument"] = {"type": "option"}
    card["agent"]["zones"]["stop_level_basis"] = "underlying"
    premium_bars = _bars([20.70, 21.26])          # 合约自己的日线 = 保费口径

    assert rt.detect_transition("GOOGL270115C390000", card, premium_bars) is None
    # 正股同样的数字照常触发 —— 挡的是 instrument, 不是这两个数
    assert rt.detect_transition("GOOGL", _card(stop=358.5, days=2),
                                premium_bars)["direction"] == "down"


def test_option_card_cannot_black_out_the_whole_bucket(monkeypatch):
    """爆炸半径回归钉: 一张期权卡不得让同桶正股的转移一起消失。

    写入是整桶原子的 —— 坏卡让 write 抛异常时, 死的不是那张卡, 是**当日全部转移**。
    """
    opt = _card(stop=358.5, days=2)
    opt["instrument"] = {"type": "option"}
    opt["agent"]["zones"]["stop_level_basis"] = "underlying"
    stock = _card(stop=800.0, days=2)
    written = {}
    pos = {"tickers": {"GOOGL270115C390000": opt, "MU": stock}}
    monkeypatch.setattr(rt.state_io, "read_positions", lambda **k: pos)
    monkeypatch.setattr(rt.state_io, "read_watchlist", lambda **k: {"tickers": {}})
    monkeypatch.setattr(rt.state_io, "write_positions",
                        lambda d: written.setdefault("pos", d))
    # 合约读自己的日线 (保费), 正股读正股的 —— 两条都"破了各自的 stop"
    monkeypatch.setattr(rt, "_klines", lambda t: _bars(
        [20.70, 21.26] if t.startswith("GOOGL2") else [795.0, 790.0]))

    out = rt.run(apply=True)

    assert [t["ticker"] for t in out["transitions"]] == ["MU"]      # 正股照常转移
    assert written["pos"] is pos
    assert not schema.validate_positions_doc(pos)[0]               # 写得进去
