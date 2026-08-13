"""lib/drift —— 结构漂移五维观测。

源 (用户): "仅靠日期去更新是不是不够合理" —— 时间是**上界不是判据**。

两个 bug 是首版实跑立刻暴露的, 各有回归测试:
  · POC 窗口不对称: 锚前 40 根 vs 锚后 4 根 → MRVL 报 3.25N "供需重心搬家",
    纯属窗口长度差异的产物
  · 锚定日 ≥ 最后收盘时**静默丢弃**整张卡 → LITE/NVDA 从观测里消失
"""

from quant.core.drift import (# noqa: E402
    THRESHOLDS, breaches, drift_line, structure_drift,
)


def bars(n, start=100.0, step=0.0, vol=1e6, first_date=1):
    """n 根日线, 收盘从 start 每根 +step。"""
    out = []
    for i in range(n):
        c = start + step * i
        out.append({"time": f"2026-{(first_date + i) // 28 + 1:02d}-"
                           f"{(first_date + i) % 28 + 1:02d} 00:00:00",
                    "open": c, "high": c * 1.01, "low": c * 0.99, "close": c,
                    "volume": vol, "turnover": vol * c})
    return out


def agent(anchor="2026-01-05", *, explicit=True, z2=(80, 94), trim=(130, 140),
          gate=None):
    z = {"accumulation_zone_1": [95, 105], "accumulation_zone_2": list(z2),
         "trim_zone": list(trim)}
    if explicit:
        z["anchored_on"] = {"date": anchor, "basis": "test"}
    a = {"zones": z, "thesis": {"last_reviewed": f"{anchor}T00:00:00Z"}}
    if gate:
        a["next_regime"] = {"name": "g", "entry_gate": {"level": gate, "days": 2}}
    return a


def atr_of(rows, v=5.0):
    return [v] * len(rows)


# ── 两个实跑抓到的 bug ────────────────────────────────────────────

def test_anchor_at_or_after_last_close_is_reported_not_dropped():
    """LITE/NVDA 2026-07-29: 锚 7/28, 最后收盘 7/27 → 首版返回 None,
    整张卡从观测里消失。静默丢弃是本轮一直在修的病。"""
    rows = bars(30)
    d = structure_drift(agent("2099-01-01"), rows, atr_of(rows))
    assert d is not None and d["insufficient"] is True
    assert "尚无漂移窗口" in d["note"]


def test_proxy_anchor_is_labelled():
    """无 anchored_on 时退用 last_reviewed —— 那是"谁碰过这张卡"不是"结构哪天画的"。
    实测: 只给 NVDA 加了个 --vehicle 字段, 漂移时钟就归零了。"""
    rows = bars(30)
    anc = rows[5]["time"][:10]
    assert structure_drift(agent(anc, explicit=False), rows,
                           atr_of(rows))["anchor_source"] == "last_reviewed_proxy"
    assert structure_drift(agent(anc, explicit=True), rows,
                           atr_of(rows))["anchor_source"] == "anchored_on"


# ── 五维本身 ──────────────────────────────────────────────────────

def test_price_drift_is_atr_normalised():
    rows = bars(30, start=100.0, step=1.0)            # 每根 +1, ATR=5
    anc = rows[5]["time"][:10]
    d = structure_drift(agent(anc), rows, atr_of(rows, 5.0))
    assert d["price_drift_n"] == round((rows[-1]["close"] - rows[5]["close"]) / 5.0, 2)


def test_flat_tape_has_no_drift():
    rows = bars(40, start=100.0, step=0.0)
    d = structure_drift(agent(rows[5]["time"][:10]), rows, atr_of(rows))
    assert d["price_drift_n"] == 0.0
    assert d["advisory"] is False and d["breaches"] == []


def test_out_of_range_fraction():
    rows = bars(30, start=200.0)                      # 远高于 trim 上沿 140
    d = structure_drift(agent(rows[5]["time"][:10]), rows, atr_of(rows))
    assert d["out_of_range_frac"] == 1.0


def test_gate_reachability_picks_nearest_gate_above_price():
    rows = bars(30, start=100.0)
    d = structure_drift(agent(rows[5]["time"][:10], gate=140.0), rows, atr_of(rows, 5.0))
    assert d["nearest_up_gate"] == 140.0 and d["gate_reach_n"] == 8.0


def test_gate_below_price_is_not_a_reachability_problem():
    rows = bars(30, start=100.0)
    d = structure_drift(agent(rows[5]["time"][:10], gate=80.0), rows, atr_of(rows))
    assert d["nearest_up_gate"] is None and d["gate_reach_n"] is None


def test_vol_ratio_uses_anchor_vs_now():
    rows = bars(30)
    a = atr_of(rows, 5.0)
    a[-1] = 10.0
    d = structure_drift(agent(rows[5]["time"][:10]), rows, a)
    assert d["vol_ratio"] == 2.0


# ── advisory 判定 (未校准初值, 只观测) ─────────────────────────────

def test_unreachable_gate_breaches():
    """300→100 之后 gate 234 = 永久禁入, 这一维就是为它建的。"""
    b = breaches({"gate_reach_n": 12.0, "nearest_up_gate": 234})
    assert b and "够不着的锚不是闸门是禁入" in b[0]


def test_thresholds_are_documented_as_uncalibrated():
    """接 hard STALE 前必须先校准 —— 这条不变量用测试钉住。"""
    import quant.core.drift as m
    assert "只观测, 不裁决" in m.__doc__ and "未校准" in m.__doc__
    assert set(THRESHOLDS) >= {"price_drift_n", "out_of_range_frac",
                               "gate_reach_n", "vol_ratio_lo", "vol_ratio_hi"}


def test_drift_line_quiet_when_clean():
    assert drift_line("X", {"insufficient": False, "advisory": False}) is None
    assert drift_line("X", {"insufficient": True}) is None
    assert drift_line("X", None) is None


def test_drift_line_says_advisory_not_blocking():
    line = drift_line("AMKR", {"insufficient": False, "advisory": True,
                               "anchor_date": "2026-06-11", "sessions_since": 30,
                               "breaches": ["价格离锚 2.83N > 2N"]})
    assert "advisory, 未拦交易" in line and "AMKR" in line


# ── ②POC 维已摘除 (, poc_window round-2 裁决) ──────────────

def test_poc_dimension_stays_removed():
    """摘掉的维不许悄悄长回来。

    poc_window round-2 用**解耦标签**实测: poc20 AUC 0.5975 / poc60 0.5524,
    而时间基线 0.6158、①price_drift 0.6992 —— 两个窗口都打不赢任何一个基线,
    ②不是独立维度而是①的更差影子。round-1 那个 0.812 是自变量与因变量共用
    同一份 60 根 profile 的产物。要复活它, 先拿出**新标签下**打赢两条基线的证据。
    """
    import quant.core.drift as m
    assert "poc_shift_n" not in THRESHOLDS
    assert not hasattr(m, "_poc") and not hasattr(m, "POC_WINDOW")
    assert not hasattr(m, "POC_MIN_SESSIONS")
    assert "②POC 维已摘除" in m.__doc__


def test_drift_output_carries_no_poc_fields():
    rows = bars(40)
    d = structure_drift(agent(rows[5]["time"][:10]), rows, atr_of(rows))
    assert not any(k.startswith("poc") for k in d), sorted(d)
