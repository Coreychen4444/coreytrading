"""修复 gate 收复后的入场执行 (ladder_entry 回测裁决 k=0.5 落地)。

钉三件事:
  1. regime_transition 的幅度快车道 —— 三个条件缺一不可 (首日 / 幅度 / 量腿)
  2. entry_guard 的 repair_reclaim ladder 锚 + **gate 地板**
  3. 地板与"相邻档下压 1%"的交互 —— 实现期抓到的真 bug
"""
import pytest

from quant.decision import entry_guard as eg
from quant.decision import regime_transition as rt


def _k(closes, vol=1e7):
    """合成日线: 给定收盘序列, high/low 贴住收盘 (ATR 由调用方用长序列控制)。"""
    return [{"time": f"2026-07-{i + 1:02d}", "open": c, "high": c + 1, "low": c - 1,
             "close": c, "volume": vol, "turnover": c * vol} for i, c in enumerate(closes)]


def _card(gate=100.0, days=2):
    return {"agent": {"zones": {}, "next_regime": {
        "name": "R_up", "entry_gate": {"level": gate, "days": days}}}}


# ── 1. 快车道四条件 (⓪域  加) ─────────────────────────
#
# ⚠️ 条件隔离纪律: 测 ①②③ 的用例必须把 ⓪域垫成 True —— 否则短 fixture 会被
# 域判据先拦下, 测试"过了"但测的不再是自己声称的那条 (拿掉①②③任何一条都不红)。
# ⓪ 自己的测试在下面单独一节, 不垫。


def _vol(monkeypatch, ok, note=""):
    """量腿判定的**真实接缝** = `volume_leg_detail`；`volume_leg` 只是它的二元视图。
     结构化收据把计算搬进 detail 后, 打桩必须打在计算那一侧 ——
    打在视图上会静默失效 (本文件 11 处曾同时中招, 由 fastlane 那条断言抓出)。"""
    detail = {"ok": ok, "note": note, "required": 1.2, "actual": None, "mode": "relative",
              "benchmark": None, "benchmark_ratio": None, "own_ratio": None,
              "aligned_date": None, "aligned_streak_day": 1 if ok else None,
              "streak": 1, "degraded": False}
    monkeypatch.setattr(rt, "volume_leg_detail", lambda *a, **k: dict(detail))
    monkeypatch.setattr(rt, "volume_leg", lambda *a, **k: (ok, note))

def _domain_ok(monkeypatch):
    monkeypatch.setattr(rt, "_repair_domain", lambda *a: (True, "域=①修复(test)"))


def test_fastlane_fires_on_first_day_with_enough_magnitude(monkeypatch):
    _domain_ok(monkeypatch)
    _vol(monkeypatch, True)
    closes = [90.0] * 40 + [99.0, 112.0]          # 昨在 gate 下, 今大幅站上
    tr = rt.detect_transition("T", _card(), _k(closes), today="2026-08-07")
    assert tr and tr["direction"] == "up" and tr["fastlane"] is True
    assert tr["days"] == 1 and tr["fastlane_over_n"] > 0.5
    assert "⚡快车道" in tr["reason"]


def test_fastlane_needs_magnitude_not_just_clearing_the_gate(monkeypatch):
    _domain_ok(monkeypatch)
    _vol(monkeypatch, True)
    closes = [90.0] * 40 + [99.0, 100.4]          # 站上了, 但远不到 +0.5N
    assert rt.detect_transition("T", _card(), _k(closes), today="2026-08-07") is None


def test_fastlane_is_first_day_only_no_retroactive_ticket(monkeypatch):
    """已经站上去几天却没凑满确认日数的, **不许事后补一张快车票**——
    回测测的是"首日幅度达标", 没测过"站了几天才想起来走快车道"。

    ⚠️ 这条只有在卡片要求 days ≥3 时才可达: days=2 时 ×2 原路径会先命中,
    `_fastlane` 根本走不到。第一版测试用 days=2 写, 变异实测**不会红**——
    钉了个到不了的分支。
    """
    _domain_ok(monkeypatch)
    _vol(monkeypatch, True)
    card = _card(days=3)
    closes = [90.0] * 40 + [99.0, 112.0, 113.0]   # 最近 3 日不全在上 → ×3 不成立
    tr = rt.detect_transition("T", card, _k(closes), today="2026-08-07")
    assert tr is None, "昨天就已站上 gate, 今天不该发快车票"


def test_fastlane_does_not_bypass_the_volume_leg(monkeypatch):
    """量腿是 relative_volume  独立裁决的维度, 快车道不豁免它。"""
    _domain_ok(monkeypatch)
    _vol(monkeypatch, False, "量比不足")
    closes = [90.0] * 40 + [99.0, 112.0]
    assert rt.detect_transition("T", _card(), _k(closes), today="2026-08-07") is None


def test_two_day_path_still_works_and_is_labelled(monkeypatch):
    _vol(monkeypatch, True)
    closes = [90.0] * 40 + [101.0, 102.0]
    tr = rt.detect_transition("T", _card(), _k(closes), today="2026-08-07")
    assert tr and tr["days"] == 2 and tr["fastlane"] is False


def test_down_transition_still_takes_priority(monkeypatch):
    _vol(monkeypatch, True)
    card = _card()
    card["agent"]["zones"] = {"stop_level": 200.0, "stop_level_days": 2}
    closes = [90.0] * 40 + [99.0, 112.0]
    tr = rt.detect_transition("T", card, _k(closes), today="2026-08-07")
    assert tr["direction"] == "down"


# ── 2. ladder 冻结锚 + 地板 (用户 收窄: 锚 = close@since, 不追价) ──

RC = {"gate": 100.0, "anchor_close": 120.0, "anchor_atr": 8.0, "since": "2026-08-05"}


def test_repair_reclaim_anchors_are_frozen_at_since_not_today():
    """锚冻结在 close@since — 今日 close 传 999 也不得影响档位 (变异守卫:
    实现若读回今日 close 即红)。参与权是突破那一天的价格, 不随后续上涨漂移。"""
    lad = eg.build_ladder("repair_reclaim", 999.0, [], None, reclaim=RC)
    assert lad == [120.0, 116.0, 112.0]           # close@since / −0.5N / −1.0N


def test_first_tranche_is_participation_price_not_a_discount():
    """greedy-bid trap: 首档必须买"突破那天的参与权"。"""
    lad = eg.build_ladder("repair_reclaim", 999.0, [], None, reclaim=RC)
    assert lad[0] == pytest.approx(120.0)


def test_gate_floor_binds_and_never_buys_below_the_reclaimed_gate():
    """**实现期抓到的真 bug**: 原有的"相邻档差<0.5% 就下压 1%"会把贴地板的档
    推到 gate 之下 (tiers=[110,100,100] → 第三档 99.0), 正是地板要防的事。
    宁可少一档, 不可买在刚收复的 gate 之下。"""
    for a_close in (103.0, 100.5, 100.05):
        lad = eg.build_ladder("repair_reclaim", a_close, [], None,
                              reclaim=dict(RC, anchor_close=a_close))
        assert all(p >= 100.0 for p in lad), (a_close, lad)
        assert len(lad) == len(set(lad))          # 夹平后不留重复档


def test_repair_reclaim_falls_back_when_reclaim_missing():
    zb = {"z1": [110, 118], "z2": [100, 110]}
    lad = eg.build_ladder("repair_reclaim", 120.0, [], zb, atr=8.0, reclaim=None)
    assert lad and lad[0] != 120.0                # 走 normal 分支, 不是崩


def test_normal_and_parabolic_modes_are_untouched():
    zb = {"z1": [110, 118], "z2": [100, 110]}
    assert eg.build_ladder("normal", 120.0, [], zb) == [118, 110, 105.0]
    rows = [{"high": 130.0, "low": 100.0, "close": 120.0}] * 12
    par = eg.build_ladder("parabolic", 120.0, rows, zb)
    assert par[0] == pytest.approx(120.0 * 0.98)


# ── 3. "刚收复"的判别式 (③ = repair_gate.fastlane 标记,  用户 两连纠偏:
#       第一版收窄「仅针对单日强势突破」→ 第二版「fastlane 落地时就该同步」——
#       消费侧读转移机落卡的标记, 不拿 closes 重推第二实现) ─────────────────

AG = {"current_regime": {"since": "2026-08-05"},
      "last_regime": {"repair_gate": {"level": 98.0, "fastlane": True}}}


def _warm(n=40, px=90.0):
    """since 前的暖机段 (ATR20 要 ≥21 根)。判别式只按字符串日期定位 since bar,
    暖机段日期只需单调且早于 since。"""
    return [{"time": f"2026-06-{i % 28 + 1:02d}" if i < 28 else f"2026-07-{i - 27:02d}",
             "open": px, "high": px + 2, "low": px - 2, "close": px} for i in range(n)]


def _rows_with_since(since_close=104.0):
    rows = _warm()
    rows.append({"time": "2026-08-04", "open": 96.0, "high": 98.0,
                 "low": 94.0, "close": 96.0})
    rows.append({"time": "2026-08-05", "open": 98.0, "high": since_close + 2,
                 "low": 96.0, "close": since_close})
    rows.append({"time": "2026-08-06", "close": since_close + 2,
                 "open": since_close, "high": since_close + 3, "low": since_close})
    return rows


def test_reclaimed_gate_fastlane_marker_returns_frozen_anchor():
    rc = eg.reclaimed_gate_of(AG, "2026-08-06", _rows_with_since())
    assert rc is not None
    assert rc["gate"] == 98.0
    assert rc["anchor_close"] == pytest.approx(104.0)   # 冻结在 close@since
    assert rc["anchor_atr"] and rc["anchor_atr"] > 0
    assert rc["since"] == "2026-08-05"


def test_reclaimed_gate_denies_without_fastlane_marker():
    """×2 慢磨转移落 fastlane=False; fastlane 落地前的历史转移根本没有这个字段。
    两者都不给参与权阶梯 → 照常 zone ladder (LITE 890 案的判别式)。
    变异守卫: 拿掉 ③ 标记判断, 本测试即红。"""
    rows = _rows_with_since()
    grind = {"current_regime": {"since": "2026-08-05"},
             "last_regime": {"repair_gate": {"level": 98.0, "fastlane": False}}}
    assert eg.reclaimed_gate_of(grind, "2026-08-06", rows) is None
    legacy = {"current_regime": {"since": "2026-08-05"},
              "last_regime": {"repair_gate": {"level": 98.0}}}   # 标记缺失
    assert eg.reclaimed_gate_of(legacy, "2026-08-06", rows) is None


def test_reclaimed_gate_requires_rows_and_since_bar():
    rows = _rows_with_since()
    assert eg.reclaimed_gate_of(AG, "2026-08-06") is None            # rows 必传 (冻结锚要 close@since)
    no_since = [r for r in rows if r["time"] != "2026-08-05"]
    assert eg.reclaimed_gate_of(AG, "2026-08-06", no_since) is None  # since bar 不在缓存
    assert eg.reclaimed_gate_of(AG, "2026-08-06", rows[-2:]) is None  # ATR 暖机不足 → 锚算不出
    assert eg.reclaimed_gate_of({"current_regime": {"since": "2026-08-05"}},
                                "2026-08-06", rows) is None          # 无 repair_gate
    assert eg.reclaimed_gate_of({}, "2026-08-06", rows) is None


def test_reclaimed_gate_yields_after_post_transition_repaint():
    """转移后的重画接管冻结锚；同日或更早的图仍允许 repair ladder。"""
    rows = _rows_with_since()
    repainted = {**AG, "zones": {"anchored_on": {"date": "2026-08-06"}}}
    assert eg.reclaimed_gate_of(repainted, "2026-08-06", rows) is None
    same_day = {**AG, "zones": {"anchored_on": {"date": "2026-08-05"}}}   # 转移当日补画 provisional
    assert eg.reclaimed_gate_of(same_day, "2026-08-06", rows) is not None
    pre_transition = {**AG, "zones": {"anchored_on": {"date": "2026-08-01"}}}
    assert eg.reclaimed_gate_of(pre_transition, "2026-08-06", rows) is not None
    no_anchor = {**AG, "zones": {}}                                       # legacy card without anchor
    assert eg.reclaimed_gate_of(no_anchor, "2026-08-06", rows) is not None


def test_reclaim_window_counts_trading_days_not_calendar_days():
    """钉**单位**不只钉数字 (ledger V3): repair_reclaim_days=10 数的是 kline bars。
    构造自然日 14 天 / 交易日恰 10 根的窗口 —— 自然日口径判超窗, 交易日口径在窗内,
    两种实现给出相反答案, 本测试因此能区分单位。"""
    rows = _rows_with_since()[:-1]                      # 到 since 为止
    # since 8/5 (周三) 之后的 10 个交易日: 8/6 8/7 8/10 ... 8/19
    trading = ["2026-08-06", "2026-08-07", "2026-08-10", "2026-08-11", "2026-08-12",
               "2026-08-13", "2026-08-14", "2026-08-17", "2026-08-18", "2026-08-19"]
    rows += [{"time": d, "close": 100.0} for d in trading]
    assert eg.reclaimed_gate_of(AG, "2026-08-19", rows) is not None   # 第 10 交易日在窗内
    rows_11 = rows + [{"time": "2026-08-20", "close": 100.0}]
    assert eg.reclaimed_gate_of(AG, "2026-08-20", rows_11) is None    # 第 11 根超窗
    assert eg.reclaimed_gate_of(AG, "2026-08-01", rows) is None       # since 在未来
    # since 之后 0 根 (当日) 也在窗内
    assert eg.reclaimed_gate_of(AG, "2026-08-05", _rows_with_since()[:-1]) is not None


def test_preregistered_constants_are_pinned():
    assert rt.PARAMS["fastlane_k"] == 0.5
    assert eg.PARAMS["repair_reclaim_offsets"] == (0.0, 0.5, 1.0)
    assert eg.PARAMS["repair_reclaim_days"] == 10
    assert eg.PARAMS["ladder_weights"] == (0.50, 0.30, 0.20)


# ── 1b. ⓪ 适用域 (接线, 用户 批「接判别式」) ─────────
#
# 证据链: gate_path_taxonomy 四类全否 (③显著负 R) + ladder_timing/stop_cost_guard
# ①六判据全过 ⟹ 快车道只给①修复路径。判据只有 broke 一道, 与回测 import 同一份
# core/episodes (决策点 E 当初就是为这一天立的)。

def _repair_shape(n_recover=60):
    """崩塌 + 收复: 破位日落在 120 日窗内 ⟹ ①修复。总长 ≥ MIN_BARS(180)。"""
    return [200.0] * 140 + [80.0] + [82.0 + i * 0.4 for i in range(n_recover)]


def _box_shape():
    """抬高低点的收敛箱体: 永不创 60 日新低 ⟹ 无破位 ⟹ 域外。总长 ≥ 180。"""
    out = []
    for k in range(10):
        lo, hi = 100 + 1.8 * k, 140 - 1.2 * k
        out += [lo + (hi - lo) * x / 9 for x in range(10)]
        out += [hi - (hi - lo) * x / 9 for x in range(10)]
    return out


def test_domain_denies_fastlane_on_a_box_gate(monkeypatch, tmp_path):
    """非①(箱体顶) 首日幅度+量腿全达标 → **仍然 ×2**, 不发快车票。
    这正是 MRVL 223 / MU 945 的形状 —— ③有显著负 R 反证的那一类。"""
    _vol(monkeypatch, True)
    monkeypatch.setattr(rt, "KLINE_DEEP_DIR", tmp_path)      # 无深度缓存 → 只用常规
    closes = _box_shape() + [124.0, 145.0]                   # 昨 124 < gate 130 < 今 145
    tr = rt.detect_transition("T", _card(gate=130.0), _k(closes), today="2026-08-09")
    assert tr is None, "域外的 gate 不许当日转移"


def test_domain_grants_fastlane_on_a_repair_gate(monkeypatch, tmp_path):
    _vol(monkeypatch, True)
    monkeypatch.setattr(rt, "KLINE_DEEP_DIR", tmp_path)
    closes = _repair_shape() + [104.0, 125.0]                # 昨在 gate 110 下, 今大幅上
    tr = rt.detect_transition("T", _card(gate=110.0), _k(closes), today="2026-08-09")
    assert tr and tr["fastlane"] is True
    assert "域=①修复" in tr["reason"], "转移注里必须播报判类依据"


def test_domain_denies_when_series_too_short(monkeypatch, tmp_path):
    """判不了 = 不给 (保守方向): 错拒的代价是等一天 ×2, 错放的代价是给③发当日授权。

    ⚠️ fixture 必须是「**有**破位但序列不满 180」—— 拿 42 根的序列测, MA60 都算不出,
    「拿掉 MIN_BARS 门槛」与「窗内无破位」给出同一个 None, 变异不红 (M3 实测漏网)。
    这里 103 根: 破位算得出 (若无门槛会判①放行), 唯独窗口算不满 → 必须拒。"""
    _vol(monkeypatch, True)
    monkeypatch.setattr(rt, "KLINE_DEEP_DIR", tmp_path)
    closes = [200.0] * 70 + [80.0] + [85.0 + i for i in range(30)] + [104.0, 125.0]
    assert len(closes) < rt.MIN_BARS
    ok, note = rt._repair_domain("T", _k(closes), 110.0)
    assert ok is False and "域不可判" in note and "180" in note
    assert rt.detect_transition("T", _card(gate=110.0), _k(closes),
                                today="2026-08-09") is None


def test_domain_uses_deep_cache_history(monkeypatch, tmp_path):
    """深度缓存补历史: 常规缓存只看到收复段(无破位), 深度里有崩塌 → 合并后判①。
    这就是 E 锁定「深度+常规按日期去重合并」在 live 的意义。"""
    import json as _json
    _vol(monkeypatch, True)
    monkeypatch.setattr(rt, "KLINE_DEEP_DIR", tmp_path)
    full = _repair_shape(n_recover=80) + [104.0, 125.0]
    all_bars = [{"time": f"2025-{1 + i // 28:02d}-{i % 28 + 1:02d} 00:00:00",
                 "open": c, "high": c + 1, "low": c - 1, "close": c,
                 "volume": 10 **7, "turnover": c * 10 **7} for i, c in enumerate(full)]
    regular = all_bars[-190:]                # 常规段自身无破位 (纯收复+今昨两根)
    (tmp_path / "T.jsonl").write_text("\n".join(_json.dumps(b) for b in all_bars) + "\n")

    tr = rt.detect_transition("T", _card(gate=110.0), regular, today="2026-08-09")
    assert tr and tr["fastlane"] is True and "域=①修复" in tr["reason"]


def test_domain_denies_on_a_gapped_series(monkeypatch, tmp_path):
    """深度尾与常规头之间有洞 → MA60/episodes 全失真 → 判不了 = 不给。"""
    import json as _json
    a = [{"time": f"2024-01-{i + 1:02d} 00:00:00", "close": 100.0} for i in range(20)]
    b = [{"time": f"2025-06-{i + 1:02d} 00:00:00", "close": 100.0} for i in range(20)]
    monkeypatch.setattr(rt, "KLINE_DEEP_DIR", tmp_path)
    (tmp_path / "T.jsonl").write_text("\n".join(_json.dumps(x) for x in a) + "\n")
    got, note = rt._repair_domain("T", b, 90.0)
    assert got is False and "域不可判" in note and "断层" in note


def test_domain_predicate_is_the_backtest_one_not_a_copy():
    """判别式必须与回测同一份 —— `grep episodes_of` 只许找到 core 那个实现。"""
    from quant.core import episodes as ce
    assert rt.episodes_of is ce.episodes_of
    assert rt.breakdown_in_window is ce.breakdown_in_window
    assert rt.MIN_BARS == 180


# ── 4. fastlane 标记持久化 (用户 抓漏: 落地时只活在 tr dict 里) ──

def _applied(monkeypatch, fastlane):
    closes = [90.0] * 40 + ([99.0, 112.0] if fastlane else [101.0, 102.0])
    card = _card()
    card["agent"]["next_regime"]["zones"] = {"accumulation_zone_1": [98.0, 103.0],
                                             "stop_level": 95.0}
    _vol(monkeypatch, True)
    if fastlane:
        monkeypatch.setattr(rt, "_repair_domain", lambda *a: (True, "域=①修复(test)"))
    tr = rt.detect_transition("T", card, _k(closes), today="2026-08-07")
    assert tr and tr["fastlane"] is fastlane
    rt.apply_transition(card, tr, klines=_k(closes))
    return card["agent"]


def test_apply_transition_persists_fastlane_into_repair_gate_and_history(monkeypatch):
    """消费者 = entry_guard.reclaimed_gate_of (③ 判据)。落卡即丢的标记等于
    没有标记 — 消费侧只好拿 closes 重推弱化版, 正是本轮修掉的第二实现。"""
    ag = _applied(monkeypatch, fastlane=True)
    assert ag["last_regime"]["repair_gate"]["fastlane"] is True
    assert ag["regime_history"][-1]["fastlane"] is True


def test_apply_transition_marks_two_day_path_false(monkeypatch):
    ag = _applied(monkeypatch, fastlane=False)
    assert ag["last_regime"]["repair_gate"]["fastlane"] is False
    assert ag["regime_history"][-1]["fastlane"] is False
