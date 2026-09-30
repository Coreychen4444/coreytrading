"""宏观透镜（quant/core/macro_lens.py）—— 每条判别式一个会失败的用例：
对齐与填充边界、因子口径、统计原语无前视、最强驱动 / 并列 / 接棒、六盏灯阈值、情景拒绝不稳定因子、日历量级。
"""
import datetime as dt
import math
import random

import pytest

from quant.core import macro_lens as L

N = 900                                     # ≥ PCT_WIN + W + 余量


def _dates(n=N, start="2022-01-03"):
    d, out = dt.date.fromisoformat(start), []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out


def _walk(rets, p0=100.0):
    out, p = [], p0
    for r in rets:
        p *= math.exp(r)
        out.append(p)
    return out


def synthetic(drive=None, beta=-0.8, last=60, seed=7, vix=None):
    """8 因子独立随机游走；drive 指定的因子在最后 `last` 天驱动 SPY（beta × 因子标准化收益）。"""
    rng = random.Random(seed)
    dates = _dates()
    f = {k: [rng.gauss(0, 0.01) for _ in dates] for k in L.FACTORS}
    spy = [rng.gauss(0.0003, 0.008) for _ in dates]
    if drive:
        for i in range(N - last, N):
            spy[i] = beta * f[drive][i] + rng.gauss(0, 0.003)
    lvl = lambda rets, p0=100.0: list(zip(dates, _walk(rets, p0)))     # noqa: E731
    s = {"SPY": lvl(spy, 400), "USO": lvl(f["OIL"], 80), "SHY": lvl([-x for x in f["R2"]], 82),
         "DXY": lvl(f["USD"], 100), "GLD": lvl(f["GOLD"], 380), "USDJPY": lvl(f["JPY"], 150),
         "COPPER": lvl(f["COPPER"], 4), "IEF": lvl([rng.gauss(0, 0.003) for _ in dates], 95),
         "TLT": lvl([rng.gauss(0, 0.008) for _ in dates], 90)}
    ief = [v for _, v in s["IEF"]]
    ief_r = [0.0] + [math.log(ief[i] / ief[i - 1]) for i in range(1, N)]
    s["HYG"] = lvl([c + 0.5 * r for c, r in zip(f["CREDIT"], ief_r)], 78)
    y, t10 = 4.0, []
    for x in f["R10"]:
        y += x * 5                                   # 1% 游走步长 → 5bp
        t10.append(y)
    s["US10Y"] = list(zip(dates, t10))
    v = vix or [15 + 2 * math.sin(i / 30) for i in range(N)]
    s["WTI"] = list(zip(dates, _walk(f["OIL"], 80)))              # 水平读数 / 油价冲击灯用期货
    s["VIX"] = list(zip(dates, v))
    s["VIX3M"] = list(zip(dates, [x + 2 for x in v]))
    s["MOVE"] = list(zip(dates, [90 + 5 * math.sin(i / 40) for i in range(N)]))
    for k in list(L.SECTORS) + ["QQQ"]:
        s[k] = lvl([a * 1.1 + rng.gauss(0, 0.004) for a in spy], 100)
    return s


# ── 对齐与因子口径 ────────────────────────────────────────────────

def test_align_ffills_levels_at_most_three_days_and_never_raw():
    s = {"SPY": [(f"2026-01-0{i}", 1.0) for i in range(1, 9)],
         "MOVE": [("2026-01-01", 90.0)]}
    fr = L.align(s)
    assert fr["raw"]["MOVE"] == [90.0] + [None] * 7
    assert fr["level"]["MOVE"][:5] == [90.0] * 4 + [None]


def test_factor_changes_units_and_bad_move_guard():
    dates = ["2026-01-01", "2026-01-02", "2026-01-05"]
    s = {"SPY": list(zip(dates, [100.0, 101.0, 102.0])), "US10Y": list(zip(dates, [4.00, 4.10, 4.05])),
         "SHY": list(zip(dates, [100.0, 99.0, 99.0])), "USO": list(zip(dates, [50.0, 80.0, 81.0])),
         "HYG": list(zip(dates, [80.0, 80.0, 80.8])), "IEF": list(zip(dates, [90.0, 90.0, 90.9]))}
    ch = L.changes(L.align(s))
    assert ch["R10"][1] == pytest.approx(10.0) and ch["R10"][2] == pytest.approx(-5.0)       # bp
    assert ch["R2"][1] == pytest.approx(-math.log(99 / 100))       # SHY 跌 = 2Y 收益率升 = 正
    assert ch["OIL"][1] is None                                   # ln(80/50)=0.47 > 0.4 = 换月坏点
    assert ch["CREDIT"][2] == pytest.approx(math.log(1.01) - 0.5 * math.log(1.01))


# ── 统计原语 ──────────────────────────────────────────────────────

def test_corr_uses_pairwise_valid_and_min_count():
    y = [float(i) for i in range(40)]
    x = [2.0 * i for i in range(40)]
    assert L.corr_at(y, x, 39, 40) == pytest.approx(1.0)
    x_gap = x[:]
    for i in range(0, 9):
        x_gap[i] = None                                           # 31 对 < 32
    assert L.corr_at(y, x_gap, 39, 40) is None


def test_r2_single_regressor_equals_corr_squared():
    rng = random.Random(1)
    x = [rng.gauss(0, 1) for _ in range(60)]
    y = [0.5 * a + rng.gauss(0, 1) for a in x]
    assert L.r2_at(y, [x], 59, 40) == pytest.approx(L.corr_at(y, x, 59, 40) ** 2)


def test_pct_only_looks_back_and_needs_full_window():
    vals = [float(i) for i in range(L.PCT_WIN + 3)]
    assert L.pct_at(vals, L.PCT_WIN - 1) is None
    assert L.pct_at(vals, L.PCT_WIN) == 1.0
    assert L.pct_at(vals + [-5.0, 1e9], L.PCT_WIN) == 1.0          # 未来值不改变过去


# ── 最强驱动 / 读法 / 接棒 ────────────────────────────────────────────

def test_driven_factor_becomes_dominant_with_its_reading():
    a = L.assess(synthetic(drive="OIL", beta=-0.8))
    assert a["drivers"]["dominant"] == "OIL"
    assert a["story"]["theme"].startswith("供给冲击")
    assert a["story"]["chain"][0]["factor"] == "OIL"
    assert a["drivers"]["challenger"] != "OIL"


def test_positive_oil_reading_is_growth_not_supply_shock():
    a = L.assess(synthetic(drive="OIL", beta=+0.8))
    assert a["drivers"]["dominant"] == "OIL"
    assert a["story"]["theme"].startswith("需求")


def test_no_dominant_when_every_factor_is_normal():
    a = L.assess(synthetic(drive=None))
    assert a["drivers"]["dominant"] is None
    assert a["forward"]["m_high"] is False


def test_reading_table_covers_every_factor_and_sign():
    for f in L.FACTORS:
        for s in (-1, 1):
            assert (f, s) in L.READING


def test_flip_watch_names_the_opposite_script():
    a = L.assess(synthetic(drive="OIL", beta=-0.8))
    assert any("需求 / 增长" in w for w in a["forward"]["flip_watch"])


# ── 六盏灯 ───────────────────────────────────────────────────────

def test_risk_lights_fire_on_vix_spike_and_backwardation():
    vix = [15.0] * (N - 1) + [40.0]
    s = synthetic(vix=vix)
    s["VIX3M"] = [(d, 20.0) for d, _ in s["VIX"]]
    st = L.assess(s)["risk"]["states"]
    assert st["S1"]["on"] is True and st["S2"]["on"] is True
    tier = L.risk_tier(st)
    assert tier["count"] >= 2 and tier["of"] == 6


def test_only_validated_states_count_as_lights():
    assert set(L.RISK_LIGHTS) == {"S1", "S2", "S3", "S4", "S5", "S8"}
    st = L.assess(synthetic())["risk"]["states"]
    assert {c for c, v in st.items() if v["validated"]} == set(L.RISK_LIGHTS)


def test_tier_receipts_are_monotone_in_risk_not_in_return():
    dd = [L.TIER_BASE[k]["dd"] for k in range(5)]
    rv = [L.TIER_BASE[k]["rv"] for k in range(5)]
    assert dd == sorted(dd, reverse=True) and rv == sorted(rv)
    assert min(L.TIER_BASE[k]["ret"] for k in range(5)) > 0     # 亮灯不预告下跌


# ── 情景 / 极值 / 日历 ────────────────────────────────────────────

def test_scenarios_refuse_factor_with_unstable_betas():
    fr, ch, t = L.context(synthetic(drive="JPY"))
    assert L.scenarios(ch, "JPY", t) is None
    sc = L.scenarios(ch, "OIL", t)
    assert sc and {r["ticker"] for r in sc["rows"]} == set(L.SCENARIO_TARGETS)


def test_since_extreme_reports_only_multi_year_levels():
    dates = _dates(400)
    vals = [5.0] + [4.0] * 398 + [4.9]
    ex = L.since_extreme(dates, vals, 399)
    assert ex == {"kind": "高", "since": dates[0], "record": False}
    assert L.since_extreme(dates, [4.0] * 399 + [4.1], 399)["record"] is True
    assert L.since_extreme(dates[:10], [1.0] * 9 + [2.0], 9) is None


def test_event_tier_and_channel():
    assert L.event_tier("美国9月就业报告") == "A" and L.event_channel("美国9月就业报告")[1] == "就业 → 利率"
    assert L.event_tier("FOMC 利率决议") == "A"
    assert L.event_tier("美国8月JOLTS") == "B"
    assert L.event_tier("美国每周初请失业金") == "C"
    assert L.event_channel("某公司财报") is None


def test_persistence_label_follows_the_h3_receipts():
    assert L.persistence_label("OIL") == "较持续"              # 0.260 / 0.173
    assert L.persistence_label("R2") == "之后通常回落"         # 0.076 / 0.138
    assert L.persistence_label("NOPE") is None


def test_explain_day_attributes_a_driven_move_and_drops_collinear_factors():
    s = synthetic(drive="OIL", beta=-0.8)
    fr, ch, t = L.context(s)
    ch["R10"] = [None if x is None else x * 400 for x in ch["OIL"]]   # 与原油完全共线的第二个因子
    e = L.explain_day(ch["SPY"], ch, L.drivers(ch, t - 1), t)
    assert [p["factor"] for p in e["parts"]].count("R10") + [p["factor"] for p in e["parts"]].count("OIL") == 1
    assert e["label"] in ("宏观驱动", "混合", "非宏观", "无波动")
    assert abs(e["explained"] + e["residual"] + e["intercept"] - e["actual"]) < 1e-12


# ── 背离（预注册研究；D2 / D4 描述态，D1 / D3 已删）──────────────

def _div_world(spy, vix, hyg=None, rsp=None):
    n = len(spy)
    days = [f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}" for i in range(n)]
    s = {"SPY": list(zip(days, spy)), "VIX": list(zip(days, vix)),
         "HYG": list(zip(days, hyg or [80.0] * n)), "IEF": list(zip(days, [95.0] * n)),
         "RSP": list(zip(days, rsp or [150.0] * n))}
    return L.align(s), n - 1


def test_divergence_d2_needs_price_up_and_vix_up():
    up = [100 + i * 0.1 for i in range(30)]
    fr, t = _div_world(up, [15 + i * 0.1 for i in range(30)])
    d = L.divergence_states(fr, t)["D2"]
    assert d["P"] is True and d["G"] is True and d["on"] is True
    fr, t = _div_world(up, [20 - i * 0.1 for i in range(30)])
    assert L.divergence_states(fr, t)["D2"]["on"] is False            # 涨 + 波降 = 正常，不是背离
    fr, t = _div_world([100 - i * 0.1 for i in range(30)], [15 + i * 0.1 for i in range(30)])
    d = L.divergence_states(fr, t)["D2"]
    assert d["P"] is False and d["on"] is False                      # 价格条件不成立 = 不成立（不看 G）


def test_divergence_d4_counts_non_confirmations_at_a_20_day_low():
    down = [100 - i * 0.5 for i in range(30)]                        # 每天都是 20 日新低
    # 信用上行、VIX 回落、等权相对走强 → 三项都没跟着创新 → 不确认
    fr, t = _div_world(down, [30 - i * 0.2 for i in range(30)], hyg=[80 + i * 0.1 for i in range(30)],
                       rsp=[150 - i * 0.2 for i in range(30)])
    d = L.divergence_states(fr, t)["D4"]
    assert d["P"] is True and d["G"] is True and d["on"] is True and "3/3" in d["text"]
    # 信用新低、VIX 新高、等权同步新低 → 全部确认 → 不是背离
    fr, t = _div_world(down, [15 + i * 0.5 for i in range(30)], hyg=[80 - i * 0.2 for i in range(30)],
                       rsp=[150 - i * 1.5 for i in range(30)])
    d = L.divergence_states(fr, t)["D4"]
    assert d["P"] is True and d["G"] is False and d["on"] is False
    fr, t = _div_world([100 + i * 0.5 for i in range(30)], [15.0] * 30)
    assert L.divergence_states(fr, t)["D4"]["on"] is False            # 不在 20 日新低 = 价格条件不成立


def test_divergence_missing_inputs_are_none_not_false():
    fr, t = _div_world([100.0] * 3, [15.0] * 3)                       # 不到 5 / 20 根
    d = L.divergence_states(fr, t)
    assert d["D2"]["on"] is None and d["D4"]["on"] is None

