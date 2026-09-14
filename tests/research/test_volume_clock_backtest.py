"""volume_clock_backtest 合成数据回归 — 量的钟机械件 + 裁决规则 + 回归锚 (真数据只跑一次)。

预注册的每条机械语义在这里钉死:
K=2.0 单点 / 反应日贡献 0 (分母不剔) / 跌回归零 / 自身 ADV 不除基准 / 快车道镜像 live 常量 /
裁决三判据 + Kish 门 + 回归复现前置。场景钉住的形状: 两个平均量的收盘
站上 gate, 完成日 RelVR 因基准放量不过 → W1 / W1F 空手, W4 当日。
"""
import json
from datetime import date, timedelta

from quant.decision import regime_transition as rt
from quant.research import relative_volume_backtest as rv
from quant.research import volume_clock_backtest as vc
from quant.research.gate_backtest2 import atr_series


# ---------- 1. 锁定项字面量 + live 同源 ----------

def test_locked_constants_are_literal_and_live_sourced():
    assert vc.K_PRIMARY == 2.0
    assert vc.K_ROBUST == (1.5, 3.0)
    assert vc.FASTLANE_K == rt.PARAMS["fastlane_k"] == 0.5
    assert vc.VOL_RATIO == rv.VOL_RATIO == 1.2          # 历史世界冻结在裁决当时的 live 值
    assert rt.RECLAIM_VOL_RATIO_MIN == 1.1             # live threshold = 1.1
    assert vc.VOL_RATIO != rt.RECLAIM_VOL_RATIO_MIN    # 解耦是**故意**的: 跟随 = 回归门不可复现
    assert vc.REL_FLOOR == rt.REL_FLOOR == rv.REL_FLOOR == 0.4     # V6: 两份常量对齐由此钉
    assert vc.STOP_GUARD == rv.STOP_GUARD == 0.03
    assert vc.BASE == "W1F" and vc.BASE2 == "W1"
    assert vc.DECISION_T == 60 and vc.KISH_MIN == 30
    assert vc.CMPS == ("W4", "W4_k15", "W4_k30", "W4_vap", "W4_dnx", "W4_noE")
    assert vc.WORLDS == ("W0", "W1", "W3", "W1F", "W4", "W4_k15", "W4_k30",
                         "W4_vap", "W4_dnx", "W4_noE")
    assert vc.BOOT_SEED == 20260908


# ---------- 2. 钟的机械语义 ----------

def test_clock_two_average_days_equal_current_x2_exactly():
    closes = [100, 101, 102, 103, 104]
    contrib = [None, 1.0, 1.0, 1.0, 1.0]
    assert vc.clock_scan(0, 4, closes, contrib, 100.5, 2.0) == 2      # 1.0+1.0 恰 = 2.0 → 触发 (EPS)
    assert vc.clock_scan(0, 4, closes, [None, 0.99, 1.0, 1.0, 1.0], 100.5, 2.0) == 3


def test_clock_big_day_fires_day_one_and_quiet_days_accumulate():
    closes = [100, 103, 104, 105, 106, 107]
    assert vc.clock_scan(0, 5, closes, [None, 2.5, 0.5, 0.5, 0.5, 0.5], 101, 2.0) == 1   # 大量日当日
    assert vc.clock_scan(0, 5, closes, [None, 0.5, 0.5, 0.5, 0.5, 0.5], 101, 2.0) == 4   # 四个半量日
    assert vc.clock_scan(0, 5, closes, [None, 0.5, 0.5, 0.5, 0.5, 0.5], 101, 3.0) is None


def test_clock_resets_on_close_at_or_below_gate_and_none_counts_zero():
    closes = [100, 103, 100, 103, 104]
    contrib = [None, 1.5, 1.5, 1.0, 1.0]
    # j=1 acc 1.5; j=2 收盘 == gate → 归零; j=3 1.0; j=4 2.0 → 触发
    assert vc.clock_scan(0, 4, closes, contrib, 100, 2.0) == 4
    assert vc.clock_scan(0, 4, [100, 103, 104, 105, 106], [None, 1.0, None, 1.0, 1.0], 100, 2.0) == 3


def test_clock_contribs_reaction_day_zero_and_vap_fraction():
    ratios = [1.0, 2.0, 1.5, 0.8]
    e_mask = [False, True, False, False]
    assert vc.clock_contribs(ratios, e_mask) == [1.0, 0.0, 1.5, 0.8]          # E+0 → 0
    assert vc.clock_contribs(ratios, e_mask, no_e=True) == [1.0, 2.0, 1.5, 0.8]
    highs, lows, gate = [110, 110, 106, 100], [90, 100, 102, 90], 100
    got = vc.clock_contribs(ratios, e_mask, highs=highs, lows=lows, gate=gate, vap=True)
    assert got[0] == 0.5            # 条一半在 gate 之上
    assert got[1] == 0.0            # E+0 仍归零
    assert got[2] == 1.5            # 整条在上 → 占比 1
    assert got[3] == 0.0            # high == gate → 占比 0
    assert vc.clock_contribs([None, 1.0], [False, False]) == [None, 1.0]


def test_ratio_series_dnx_excludes_reaction_day_from_denominator_only_in_that_arm():
    vols = [100] * 19 + [1000, 100]                 # j=19 反应日放量 10×, j=20 平量
    e_mask = [False] * 19 + [True, False]
    plain = rv.ratio_series(vols)
    dnx = vc.ratio_series_dnx(vols, e_mask)
    assert abs(plain[20] - 100 / 145) < 1e-12       # 主格分母含反应日 → 0.69 (方向保守)
    assert abs(dnx[20] - 1.0) < 1e-12               # dnx 分母剔它 → 1.0
    assert dnx[18] is None


# ---------- 3. 快车道镜像 (live 首日语义) ----------

def test_fastlane_scan_mirrors_live_first_day_semantics():
    atr20 = [2.0] * 4
    relvr = [None, 1.5, 1.5, 1.5]
    gate = 100.5
    assert vc.fastlane_scan(0, 3, [100, 99, 102, 103], atr20, relvr, gate) == 2   # 首日 + 幅度 + 量腿
    assert vc.fastlane_scan(0, 3, [100, 99, 101.2, 103], atr20, relvr, gate) is None  # 幅度 ≤ gate+0.5N
    assert vc.fastlane_scan(0, 3, [100, 99, 102, 103], atr20, [None, 1.5, 1.0, 1.5], gate) is None
    assert vc.fastlane_scan(1, 3, [100, 101, 104, 105], atr20, relvr, gate) is None  # 非首日不补票
    assert vc.fastlane_scan(0, 3, [100, 99, 102, 103], [None] * 4, relvr, gate) is None  # ATR 缺 → 不发


# ---------- 4. 反应日解析 ----------

def test_reaction_days_of_resolution(tmp_path):
    dates = ["2020-01-06", "2020-01-07", "2020-01-08", "2020-01-09", "2020-01-10",
             "2020-01-13", "2020-01-14"]
    rows = [
        {"pub_trading_day_str": "2020-01-06", "trading_day_str": "2020-01-07", "pub_type": 2},
        {"pub_trading_day_str": "2020-01-08", "pub_type": 2, "pub_time_str": "2020-01-08 16:05:00"},
        {"pub_trading_day_str": "2020-01-10", "pub_type": 1, "pub_time_str": "2020-01-10 07:30:00"},
        {"pub_trading_day_str": "2020-01-13", "pub_type": 0, "pub_time_str": "2020-01-13 00:00:00"},
        {"pub_trading_day_str": "", "pub_type": 1},
    ]
    (tmp_path / "T.json").write_text(json.dumps({"data": rows}))
    got = vc.reaction_days_of("T", dates, tmp_path)
    # vendor 已解析取 01-07; AMC 未解析 → 次一交易日 01-09; BMO → 当日; 未知 → 当日+次日
    assert got == {"2020-01-07", "2020-01-09", "2020-01-10", "2020-01-13", "2020-01-14"}
    assert vc.reaction_days_of("NOPE", dates, tmp_path) == set()


# ---------- 5. 世界 (立项源头形状) ----------

def _bars(closes, vols):
    highs = [c + 1 for c in closes]
    lows = [c - 1 for c in closes]
    return highs, lows


def test_world_entries_nvda_shape():
    """两个收盘>gate 各 ~1.05× 自身量, 完成日 RelVR 因基准放量不过 → W0/W1/W1F 空手,
    W4 当日 (≈1.05+1.05 ≥ 2), W3 = p, VAP 更严 (首日半条在下) 未到。"""
    closes = [100.0] * 22 + [99.0, 101.0, 102.0]
    vols = [1_000_000] * 23 + [1_050_000, 1_050_000]
    highs, lows = _bars(closes, vols)
    ratios = rv.ratio_series(vols)
    relvr = [None] * 23 + [1.27, 0.77]
    atr20 = atr_series(highs, lows, closes, 20)
    e_mask = [False] * len(closes)
    gate, i, end = 100.5, 22, 24
    q = vc.world_entries(i, end, closes, gate, vols, relvr, ratios,
                         vc.ratio_series_dnx(vols, e_mask), e_mask, highs, lows, atr20)
    assert q["W3"] == 24 and q["W1"] is None and q["W0"] is None and q["W1F"] is None
    assert q["W4"] == 24 and q["W4_k15"] == 24 and q["W4_k30"] is None
    assert q["W4_noE"] == 24 and q["W4_dnx"] == 24
    assert q["W4_vap"] is None                     # 首日条 100–102 只有 0.75 在 gate 上 → 1.83 < 2


def test_world_entries_fastlane_makes_w1f_earlier_than_w1():
    closes = [100.0] * 22 + [99.0, 104.0, 104.5]     # 首日跳过 gate + 0.5N
    vols = [1_000_000] * 23 + [2_000_000, 1_000_000]
    highs, lows = _bars(closes, vols)
    ratios = rv.ratio_series(vols)
    relvr = [None] * 23 + [1.9, 0.9]
    atr20 = atr_series(highs, lows, closes, 20)
    e_mask = [False] * len(closes)
    q = vc.world_entries(22, 24, closes, 100.5, vols, relvr, ratios,
                         vc.ratio_series_dnx(vols, e_mask), e_mask, highs, lows, atr20)
    assert q["W1"] is None and q["W1F"] == 23 and q["W3"] == 24
    assert q["W4"] == 24 and q["W4_k15"] == 23        # 首日 ≈1.9× 未到 2, K=1.5 当日


# ---------- 6. collect 端到端 (合成票, 与 relative_volume 同款构造) ----------

def _write_jsonl(path, rows):
    path.write_text("\n".join(json.dumps(r) for r in rows))


def _mk_bars(closes, vols, start="2020-01-01"):
    d0 = date.fromisoformat(start)
    out = []
    for k, (c, v) in enumerate(zip(closes, vols)):
        out.append({"time": (d0 + timedelta(days=k)).isoformat() + " 00:00:00",
                    "open": c, "high": c + 1, "low": c - 1, "close": c, "volume": v})
    return out


def _quiet_grind_pool(tmp_path, reaction_day=None):
    n_pad, n_dec, n_rec = 130, 60, 100
    closes = [100.0] * n_pad
    closes += [104, 106, 108, 110, 108, 106, 104, 106, 108, 110.4,
               108, 106, 104, 105, 104, 105, 104, 105, 104, 105]
    closes += [103.5 - 0.5 * k for k in range(n_dec)]
    lo = closes[-1]
    closes += [lo + 1.5 * k for k in range(1, n_rec + 1)]
    vols = [1_000_000] * len(closes)                                 # 平量 (1.0×) 全程
    bars = _mk_bars(closes, vols)
    _write_jsonl(tmp_path / "TICK.jsonl", bars)
    _write_jsonl(tmp_path / "QQQ.jsonl", _mk_bars(closes, vols))     # tape 也平量 → RelVR 1.0
    _write_jsonl(tmp_path / "SMH.jsonl", _mk_bars(closes, vols))
    earn = tmp_path / "earn"
    earn.mkdir()
    if reaction_day:
        (earn / "TICK.json").write_text(json.dumps({"data": [
            {"pub_trading_day_str": reaction_day, "trading_day_str": reaction_day, "pub_type": 1}]}))
    p_expected = n_pad + 20 + n_dec + 25
    return bars, earn, p_expected


def test_collect_quiet_grind_is_rescued_by_the_clock(tmp_path):
    """安静爬升 (个股与 tape 全程 1.0×): W0/W1/W1F 永不放行, W4 在 p 当日 (1.0+1.0=2.0)。"""
    bars, earn, p_expected = _quiet_grind_pool(tmp_path)
    eps, outside, drops = vc.collect(kdir=tmp_path, earn_dir=earn)
    assert len(eps) == 1, (eps, drops)
    r = eps[0]
    assert r["ticker"] == "TICK" and r["semi"] is False and r["e_tag"] is False
    assert drops["no_earnings_file"] == 1 and outside == []
    assert r["lat"]["W3"] == 0 and r["lat"]["W4"] == 0 and r["lat"]["W4_noE"] == 0
    assert r["lat"]["W1"] is None and r["lat"]["W1F"] is None and r["lat"]["W0"] is None
    assert r["lat"]["W4_k15"] == 0 and r["lat"]["W4_k30"] == 1
    for T in (20, 60):
        assert r["entered"][("W4", T)] and not r["entered"][("W1F", T)]
        assert r["pv"][("W1F", T)] == 0.0            # ITT: 未入场 = 现金
        assert r["pv"][("W4", T)] > 0                # 单边修复段, 入场即赚
        assert r["pv"][("W4", T)] == r["pv"][("W3", T)]


def test_collect_reaction_day_inside_streak_delays_the_clock(tmp_path):
    """反应日 = 首个收盘>gate 日: 它的量不计 → W4 比 W4_noE 晚一日; 价格腿照常。"""
    bars, earn, p_expected = _quiet_grind_pool(tmp_path, reaction_day=None)
    first_above = bars[p_expected - 1]["time"][:10]
    (earn / "TICK.json").write_text(json.dumps({"data": [
        {"pub_trading_day_str": first_above, "trading_day_str": first_above, "pub_type": 1}]}))
    eps, outside, drops = vc.collect(kdir=tmp_path, earn_dir=earn)
    assert len(eps) == 1
    r = eps[0]
    assert r["lat"]["W4_noE"] == 0 and r["lat"]["W4"] == 1 and r["lat"]["W4_dnx"] == 1
    assert r["e_tag"] is True                         # p 落 E±1 → 分层 tag


# ---------- 7. 统计 / 裁决 / 回归锚 ----------

def _cell(dmean_ci, dstop_ci, dcover_ci, kish=100.0, base="W1F"):
    return {"n": 500, "kish": kish, "base": base,
            "dmean": sum(dmean_ci) / 2, "dmean_ci": dmean_ci,
            "dstop": sum(dstop_ci) / 2, "dstop_ci": dstop_ci,
            "dcover": sum(dcover_ci) / 2, "dcover_ci": dcover_ci,
            "dp10": 0.0, "dp10_ci": (0.0, 0.0), "lat_w": 0.3, "lat_b": 0.1,
            "cover_w": 0.9, "cover_b": 0.85, "stop_w": 0.5, "stop_b": 0.49,
            "mean_w": 0.01, "mean_b": 0.005}


def _summary(w4_cell, reg_ok=True):
    cells = {w: _cell((-0.01, 0.01), (-0.01, 0.01), (-0.01, 0.01)) for w in vc.CMPS}
    cells["W4"] = w4_cell
    return {"regression": {"ok": reg_ok}, "full": {60: {"vs_base": cells}}}


def test_verdict_three_criteria_and_gates():
    good = _cell((0.0, 0.01), (-0.01, 0.029), (0.001, 0.02))
    assert vc.verdict(_summary(good))["branch"] == "V"
    assert vc.verdict(_summary(_cell((-0.0001, 0.01), (-0.01, 0.029), (0.001, 0.02))))["branch"] == "K"
    assert vc.verdict(_summary(_cell((0.0, 0.01), (-0.01, 0.030), (0.001, 0.02))))["branch"] == "K"
    assert vc.verdict(_summary(_cell((0.0, 0.01), (-0.01, 0.029), (0.0, 0.02))))["branch"] == "K"
    assert vc.verdict(_summary(_cell((0.0, 0.01), (-0.01, 0.029), (0.001, 0.02), kish=29.9)))["branch"] == "no_call"
    assert vc.verdict(_summary(good, reg_ok=False))["branch"] == "no_call"
    assert vc.verdict({"regression": {"ok": True}, "full": {60: {"n": 3, "insufficient": True}}})["branch"] == "no_call"
    v = vc.verdict(_summary(good))
    assert set(v["robust_flags"]) == set(vc.CMPS) - {"W4"}     # 稳健性臂只报 flags


def test_regression_anchor_is_the_0806_verdict_literal_and_check_bites():
    assert vc.REGRESSION_ROUND0 == {"n_total": 2874, "n60": 2843, "kish": 168.0,
                                  "dmean": 0.0048, "dmean_ci": (0.0016, 0.0079),
                                  "dstop": 0.0095, "dcover": 0.037}
    ok = dict(vc.REGRESSION_ROUND0)
    assert vc._regression_ok(ok)
    for key, bad in (("dmean", 0.0049), ("dstop", 0.0094), ("dcover", 0.0376), ("n_total", 2873),
                     ("n60", 2842), ("kish", 168.1)):
        mut = dict(ok)
        mut[key] = bad
        assert not vc._regression_ok(mut), key
    mut = dict(ok)
    mut["dmean_ci"] = (0.0017, 0.0079)
    assert not vc._regression_ok(mut)


def test_timing_stats_and_summarize_shapes():
    rows = [{"q": {"W4": 5, "W1F": 6}}, {"q": {"W4": 6, "W1F": 6}}, {"q": {"W4": 8, "W1F": 6}},
            {"q": {"W4": 4, "W1F": None}}, {"q": {"W4": None, "W1F": 9}}, {"q": {"W4": None, "W1F": None}}]
    tm = vc.timing_stats(rows, "W4", "W1F")
    assert tm["n_both"] == 3 and tm["only_w"] == 1 and tm["only_base"] == 1 and tm["neither"] == 1
    assert abs(tm["earlier"] - 1 / 3) < 1e-12 and abs(tm["later"] - 1 / 3) < 1e-12
    assert abs(tm["mean_dq"] - (-1 + 0 + 2) / 3) < 1e-12
    eps = []
    for k in range(3):
        eps.append({"ticker": "T", "month": f"2020-0{k + 1}", "semi": False, "e_tag": False,
                    "q": {w: 1 for w in vc.WORLDS},
                    "pv": {(w, T): 0.01 for w in vc.WORLDS for T in (20, 60)},
                    "stopped": {(w, T): False for w in vc.WORLDS for T in (20, 60)},
                    "entered": {(w, T): True for w in vc.WORLDS for T in (20, 60)},
                    "lat": {w: 0 for w in vc.WORLDS}})
    s = vc.summarize(eps, outside=[{"pv": 0.02, "stopped": False}])
    assert s["full"][60]["insufficient"] and s["full"][60]["n"] == 3
    assert s["regression"]["ok"] is False and s["outside"]["n"] == 1
    assert vc.verdict(s)["branch"] == "no_call"
