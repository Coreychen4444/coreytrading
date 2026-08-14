"""relative_volume_backtest 合成数据回归 — 量腿四世界机械件 + collect 端到端。

预注册纪律: 实现期间只允许合成数据，私有真数据按独立协议运行。
合成场景：价格腿 ×2 成立、绝对量比不过、全场同缩量；RelVR 过，
因此 W1 进场而 W0 空手。
"""
import json

from quant.research import relative_volume_backtest as rv


def test_ratio_series_matches_vol_ok_convention():
    vols = [100] * 19 + [180]           # 完成日放量
    r = rv.ratio_series(vols)
    assert r[18] is None                # 窗不足
    base = (100 * 19 + 180) / 20        # 分母含当日 (与 vol_ok / live 同拍)
    assert abs(r[19] - 180 / base) < 1e-12
    assert rv.vol_ok(vols, 19, ratio=r[19] - 1e-9)


def test_relvr_floor_prevents_quiet_tape_inflation():
    dates = ["2020-01-01", "2020-01-02"]
    stock = [1.0, 1.0]
    # 基准量比 0.2 (假日前极端缩量) → 分母被地板 0.4 托住, 不放大成 5.0
    out = rv.relvr_series(stock, dates, {"2020-01-01": 0.2, "2020-01-02": 0.8})
    assert abs(out[0] - 1.0 / 0.4) < 1e-12
    assert abs(out[1] - 1.0 / 0.8) < 1e-12
    # 基准缺日 → None (覆盖规则的输入)
    assert rv.relvr_series(stock, dates, {"2020-01-01": 0.5})[1] is None


def test_fire_scan_v_resets_streak_on_dip():
    closes = [100, 105, 106, 99, 105, 106, 107]
    assert rv.fire_scan_v(0, 6, closes, 104, 2, lambda j: True) == 2   # ×2 于 j=2
    # j=3 跌回清零; 量腿拦住完成日 → 顺延到下一个满足日 (streak 继续累积)
    assert rv.fire_scan_v(2, 6, closes, 104, 2, lambda j: True) == 5   # 清零后重数
    assert rv.fire_scan_v(0, 6, closes, 104, 2, lambda j: j >= 6) == 6
    assert rv.fire_scan_v(0, 6, closes, 104, 2, lambda j: False) is None


def test_world_entries_relative_volume_shape():
    """价格腿共同锚 p; 绝对量平 (1.0×) 不过, RelVR 过 → W1=W3=p, W0=None。"""
    closes = [100] * 5 + [96, 103, 104, 105, 106, 107, 108]
    vols = [1000] * len(closes)
    relvr = [None] * len(closes)
    for j in range(6, len(closes)):
        relvr[j] = 2.0                   # tape 缩量 → 相对量比高
    p = rv.fire_scan_v(5, len(closes) - 1, closes, 102, 2, lambda j: True)
    assert p == 7
    qs = rv.world_entries(5, p, len(closes) - 1, closes, 102, vols, relvr)
    assert qs["W3"] == p and qs["W1"] == p
    assert qs["W0"] is None              # 平量永远到不了 1.2
    assert qs["W2"] == p                 # 取或 = 相对腿放行


def test_segs_for_entry_next_close_and_window_edge():
    closes = [100, 101, 102, 103, 104]
    atr20 = [2.0] * 5
    segs = rv.segs_for(1, 4, closes, atr20)
    assert segs[0][0] == 2 and segs[0][1] == 102          # 满足日次日收盘入场
    assert segs[0][4] == 102 - 2 * rv.STOP_MULT           # primary 2N
    assert rv.segs_for(4, 4, closes, atr20) == []         # q+1 超窗 = 未入场
    assert rv.segs_for(None, 4, closes, atr20) == []


def test_near_earnings_pm1_calendar():
    edays = {"2026-08-06"}
    assert rv.near_earnings(["2026-08-05"], edays)
    assert rv.near_earnings(["2026-08-07"], edays)
    assert not rv.near_earnings(["2026-08-09"], edays)
    assert not rv.near_earnings(["2026-08-05"], set())


def _write_jsonl(path, rows):
    path.write_text("\n".join(json.dumps(r) for r in rows))


def _mk_bars(closes, vols, start="2020-01-01"):
    from datetime import date, timedelta
    d0 = date.fromisoformat(start)
    out = []
    for k, (c, v) in enumerate(zip(closes, vols)):
        out.append({"time": (d0 + timedelta(days=k)).isoformat() + " 00:00:00",
                    "open": c, "high": c + 1, "low": c - 1, "close": c, "volume": v})
    return out


def test_collect_end_to_end_relative_leg_rescues_quiet_reclaim(tmp_path):
    """合成场景全链: 破位 episode → cluster gate → 价格腿 ×2 →
    绝对量平 (W0 永不进) / tape 缩量 RelVR 过 (W1 进) → 覆盖率与 pv 分离。"""
    n_pad, n_dec, n_rec = 130, 60, 100
    closes = [100.0] * n_pad
    # 震荡段全程 >100 (不触发过早 episode), 双 pivot 高点 110/110.4 成簇
    closes += [104, 106, 108, 110, 108, 106, 104, 106, 108, 110.4,
               108, 106, 104, 105, 104, 105, 104, 105, 104, 105]
    closes += [103.5 - 0.5 * k for k in range(n_dec)]                # 破位: 新低 + <MA60
    lo = closes[-1]
    closes += [lo + 1.5 * k for k in range(1, n_rec + 1)]            # 修复穿 gate
    vols = [1_000_000] * len(closes)                                 # 个股永远平量 (1.0×)
    bars = _mk_bars(closes, vols)

    # p 预期 = 修复段第 25 根 (74+1.5×25=111.5 过 gate 110.4 ×2)
    p_expected = n_pad + 20 + n_dec + 25
    qqq_vols = [1_000_000] * len(closes)
    for k in range(p_expected - 9, p_expected + 11):                 # 短缩量段 (10/20 日在窗内)
        qqq_vols[k] = 400_000       # tape SMA20 ≈0.7M → 基准量比 ≈0.57 → RelVR ≈1.75
    _write_jsonl(tmp_path / "TICK.jsonl", bars)
    _write_jsonl(tmp_path / "QQQ.jsonl", _mk_bars(closes, qqq_vols))
    _write_jsonl(tmp_path / "SMH.jsonl", _mk_bars(closes, qqq_vols))
    earn = tmp_path / "earn"
    earn.mkdir()

    eps, drops = rv.collect(kdir=tmp_path, earn_dir=earn)
    assert len(eps) == 1, (eps, drops)
    r = eps[0]
    assert r["ticker"] == "TICK" and r["semi"] is False and r["e_tag"] is False
    assert r["lat"]["W3"] == 0                       # W3 量腿恒真 → q=p
    assert r["lat"]["W1"] == 0                       # RelVR ≈ 1.0/0.41 ≥1.2 → 当日放行
    assert r["lat"]["W0"] is None                    # 平量修复, 绝对腿永不放行
    for T in (20, 60):
        assert r["entered"][("W1", T)] and not r["entered"][("W0", T)]
        assert r["pv"][("W0", T)] == 0.0             # ITT: 未入场 = 现金
        assert r["pv"][("W1", T)] > 0                # 单边修复段, 入场即赚
        assert r["pv"][("W1", T)] == r["pv"][("W3", T)] == r["pv"][("W2", T)]


def test_summarize_and_verdict_shapes(tmp_path):
    """summarize/d1_verdict 在最小样本上的结构完整性 (n<5 → insufficient → no_call)。"""
    eps = []
    for k in range(3):
        eps.append({"ticker": "T", "month": f"2020-0{k + 1}", "semi": False,
                    "e_tag": False,
                    "pv": {(w, T): 0.01 for w in rv.WORLDS for T in (20, 60)},
                    "stopped": {(w, T): False for w in rv.WORLDS for T in (20, 60)},
                    "entered": {(w, T): True for w in rv.WORLDS for T in (20, 60)},
                    "lat": {w: 0 for w in rv.WORLDS}})
    s = rv.summarize(eps)
    assert s["full"][60]["insufficient"] and s["full"][60]["n"] == 3
    assert rv.d1_verdict(s)["branch"] == "no_call"


def test_semi_mapping_frozen():
    """研究脚本复用 production 的冻结映射，不保存第二份私人清单。"""
    assert rv.SEMI_SMH is not None
    assert rv.SEMI_SMH == set(rv.SEMI_SMH)
    assert len(rv.SEMI_SMH) == 24
