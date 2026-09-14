"""volume_leg_align (round-2 量腿对齐日) 机械件 + 扩展点恒等 + 裁决规则 (真数据只跑一次)。"""
from quant.decision import regime_transition as rt
from quant.research import relative_volume_backtest as rv
from quant.research import volume_clock_backtest as vc
from quant.research import volume_leg_align_backtest as va
from tests.research.test_volume_clock_backtest import _quiet_grind_pool


def test_locked_items():
    assert va.CAND == "W5" and va.BASE == "W1F" and va.PURE == "W1any" and va.PURE_BASE == "W1"
    assert va.WORLDS_EXT == vc.WORLDS + ("W1any", "W5")
    assert va.BOOT_SEED != vc.BOOT_SEED
    assert va.REGRESSION_CUR["n_total"] == 2897 and va.REGRESSION_CUR["n60"] == 2866
    assert vc.VOL_RATIO == rv.VOL_RATIO == 1.2                   # round-2 只动对齐日, 阈值 = 当时 live 1.2
    assert rt.RECLAIM_VOL_RATIO_MIN == 1.1                       # live threshold = 1.1 (round-3)


def test_fire_scan_any_accepts_day_one_volume_once_x2_completes():
    closes = [100, 103, 104, 105]
    relvr = [None, 1.5, 0.8, 0.7]                    # 突破日放量, 跟进日平量
    assert rv.fire_scan_v(0, 3, closes, 102, 2, lambda j: relvr[j] is not None and relvr[j] >= 1.2) is None
    assert va.fire_scan_any(0, 3, closes, relvr, 102) == 2         # ×2 完成日即触发, 量来自 day-1
    assert va.fire_scan_any(0, 3, closes, [None, 0.8, 1.5, 0.7], 102) == 2   # 完成日自己过也行 (⊇ W1)
    assert va.fire_scan_any(0, 3, closes, [None, 0.8, 0.9, 1.5], 102) == 3


def test_fire_scan_any_forgets_day_one_volume_after_a_dip():
    closes = [100, 103, 101, 103, 104]              # j=2 跌回 gate 之下 → streak 归零
    relvr = [None, 1.5, 0.8, 0.8, 0.8]
    assert va.fire_scan_any(0, 4, closes, relvr, 102) is None


def test_world_entries_ext_adds_two_worlds_and_keeps_round1_untouched(tmp_path):
    """扩展点恒等: 不传 world_fn 的 collect 与 round-1 逐字相同; 传了只多两个键。"""
    bars, earn, p_expected = _quiet_grind_pool(tmp_path)
    eps0, _, _ = vc.collect(kdir=tmp_path, earn_dir=earn)
    eps1, _, _ = va.collect(kdir=tmp_path, earn_dir=earn)
    assert set(eps0[0]["q"]) == set(vc.WORLDS)
    assert set(eps1[0]["q"]) == set(va.WORLDS_EXT)
    for w in vc.WORLDS:
        assert eps1[0]["q"][w] == eps0[0]["q"][w]
        for T in (20, 60):
            assert eps1[0]["pv"][(w, T)] == eps0[0]["pv"][(w, T)]
    r = eps1[0]
    assert r["q"]["W1any"] is None and r["q"]["W5"] is None      # 全程平量: 任一日也没有 1.2


def test_verdict_uses_round1_criteria_and_current_pool_anchor():
    def cell(dmean_ci, dstop_ci, dcover_ci, kish=100.0):
        return {"n": 500, "kish": kish, "base": "W1F", "dmean": 0, "dmean_ci": dmean_ci,
                "dstop": 0, "dstop_ci": dstop_ci, "dcover": 0, "dcover_ci": dcover_ci,
                "dp10": 0, "dp10_ci": (0, 0), "lat_w": 0, "lat_b": 0, "cover_w": 0, "cover_b": 0,
                "stop_w": 0, "stop_b": 0, "mean_w": 0, "mean_b": 0}
    good = cell((0.0, 0.01), (-0.01, 0.029), (0.001, 0.02))
    base = {"regression": {"ok": True}, "full": {60: {"cand": good, "pure": good, "clock_ref": good}}}
    assert va.verdict(base)["branch"] == "V"
    bad = dict(base, full={60: {"cand": cell((0.0, 0.01), (-0.01, 0.031), (0.001, 0.02)), "pure": good, "clock_ref": good}})
    assert va.verdict(bad)["branch"] == "K"
    assert va.verdict(dict(base, regression={"ok": False}))["branch"] == "no_call"
    ok = dict(va.REGRESSION_CUR)
    assert va._regression_ok(ok)
    ok["dstop"] = 0.009772
    assert not va._regression_ok(ok)
