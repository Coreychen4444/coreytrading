"""Pattern-detection regressions — morning star window was off by one
(checked the 3 bars ending at the PREVIOUS bar, so a star completing on the
latest bar was never reported; fixed)."""
from quant.core import technical


def _bar(o, h, l, c, vol=1000):
    return {"open": o, "high": h, "low": l, "close": c, "volume": vol, "time": "t"}


def _morning_star_triplet():
    # k1 big bearish, k2 small body gapping down, k3 bullish closing into k1 body
    k1 = _bar(100, 101, 89.5, 90)     # -10 body
    k2 = _bar(88, 88.5, 86.9, 87.5)   # small body below k1 close
    k3 = _bar(88, 96.5, 87.8, 96)     # strong bull above k1 midpoint (95)
    return k1, k2, k3


def test_detector_recognizes_triplet():
    k1, k2, k3 = _morning_star_triplet()
    assert technical.detect_morning_star(k1, k2, k3)


def test_morning_star_on_latest_bar_is_reported():
    # the regression: star completing on the LATEST bar must be detected
    klines = [_bar(100 + i * 0.1, 101.5, 99, 100.2 + i * 0.1) for i in range(30)]
    klines += list(_morning_star_triplet())
    out = technical.analyze(klines)
    assert "morning_star" in out["patterns"]


def test_no_false_positive_when_star_not_latest():
    klines = [_bar(100, 101.5, 99, 100.2) for _ in range(30)]
    klines += list(_morning_star_triplet())
    klines.append(_bar(96, 97, 95, 96.5))  # one more neutral bar AFTER the star
    out = technical.analyze(klines)
    assert "morning_star" not in out["patterns"]


def test_min_bars_no_negative_wrap():
    # old code at n=3 indexed klines[last-3] == klines[-1] (silent wrap to the
    # newest bar). Fixed indices are [0,1,2]: a clean triplet at n=3 must hit.
    k1, k2, k3 = _morning_star_triplet()
    out = technical.analyze([k1, k2, k3])
    assert "morning_star" in out["patterns"]


# ── volume profile 单一真源 (: drift 曾抄一份点质量版本悄悄分叉) ──

def test_volume_profile_formula_lives_in_exactly_one_place():
    """源码棘轮: typical-price 点质量公式只允许出现在 technical.volume_profile。

     实证: drift._poc 手抄了一版, 注释写着"与 level_map.make_profile 同法",
    于是 level_map 改成三角铺开当天, 这份副本原地不动 —— 而 POC 迁移是 structure_drift
    的核心维。同法就该是同一个函数。
    """
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[2] / "quant"
    offenders = []
    for p in root.rglob("*.py"):
        if p.name in {"technical.py"} or "research" in p.parts:
            continue
        src = p.read_text(encoding="utf-8")
        # 公式指纹: (high+low+close)/3 后紧跟着按 bucket 取整累加 turnover
        if '["high"] + r["low"] + r["close"]) / 3' in src or "(h + l + c) / 3" in src:
            offenders.append(f"{p.relative_to(root.parent)} (profile 公式)")
        # 桶宽公式同理: drift 曾抄一份没有 rounding 的, 于是同一只票两处桶宽不一样
        if "* 0.004" in src and "/ 4" in src:
            offenders.append(f"{p.relative_to(root.parent)} (auto_bucket 公式)")
    assert not offenders, (f"分布/桶宽公式被抄到 {offenders} — "
                           "用 technical.volume_profile() / technical.auto_bucket()")


def test_drift_and_level_map_agree_on_bucket_width():
    """同一只票在 drift 与 level_map 里必须得到**同一个**桶宽。

     前 drift 抄了一份 `max(atr/4, price*0.004, 0.1)` —— 少了 level_map
    的 rounding, 于是两处算出的桶不同; 而桶宽现在同时决定 profile 分辨率、LVN 扫描
    步长、拒绝判定窗口三件事。
    """
    from quant.decision import level_map
    for price, atr in ((124.22, 15.63), (881.47, 84.98), (714.65, 15.40), (5.0, 0.02)):
        assert technical.auto_bucket(price, atr) == level_map.auto_bucket(price, atr)
        assert technical.auto_bucket(price, atr) >= 0.1


# ── Trend observation layer (立项 trend_slots_design) ──────────

def test_anchored_vwap_exact_turnover_and_fallback():
    """turnover/volume = bar 真实均价; turnover 缺失回落 typical×volume 而非 0。"""
    bars = [
        {"high": 10, "low": 10, "close": 10, "volume": 100, "turnover": 1000},
        {"high": 20, "low": 20, "close": 20, "volume": 100, "turnover": 2000},
        {"high": 30, "low": 30, "close": 30, "volume": 100, "turnover": 0},  # 兜底 tp=30 → 3000
    ]
    out = technical.anchored_vwap(bars, 1)
    assert out[0] is None                      # 锚前 = None
    assert out[1] == 20.0
    assert out[2] == (2000 + 3000) / 200       # = 25.0, 兜底没有塌成 0
    # 越界锚 → 全 None, 不抛
    assert technical.anchored_vwap(bars, 99) == [None, None, None]


def test_rs_series_normalizes_at_first_valid_point():
    rs = technical.rs_series([1.0, 2.0, 4.0], [1.0, 1.0, 2.0])
    assert rs == [1.0, 2.0, 2.0]
    # 前导无效点跳过, 归一锚顺延到首个双方有效点
    rs2 = technical.rs_series([0.0, 3.0, 6.0], [1.0, 2.0, 2.0])
    assert rs2[0] is None and rs2[1] == 1.0 and rs2[2] == 2.0


# ── HV20 与分位 (IV percentile 的代理) ──

def test_hv_warms_up_then_is_annualized():
    closes = [100 * (1.01 if i % 2 else 0.99) ** (i % 3) for i in range(40)]
    rows = [_bar(c, c + 1, c - 1, c) for c in closes]
    hv = technical.hv(rows, 20)
    assert hv[19] is None and hv[20] is not None and hv[-1] > 0
    assert technical.hv([_bar(1, 1, 1, 1)] * 5, 20) == [None] * 5


def test_hv_percentile_needs_enough_samples_and_is_a_fraction():
    series = [None] * 20 + [float(i % 50) for i in range(300)]
    assert technical.hv_percentile(series, 60, 252, 120) is None      # 只有 40 个有效样本
    p = technical.hv_percentile(series, len(series) - 1, 252, 120)
    assert p is not None and 0.0 <= p <= 1.0
    assert technical.hv_percentile(series, 5, 252, 120) is None       # series[5] 是 None
