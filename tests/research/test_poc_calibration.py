"""poc_calibration 机械件验证 (合成数据)。

钉住会驱动裁决的语义:
  - label 的三种成立方式 (下带变 / 上带变 / identity 消失新生) 与"没变"
  - AUC 的两套实现等价 (bootstrap 用 numpy 版, 点估计用 python 版 — 分叉了就是两个量)
  - 阈值选法: FPR ≤30% 的候选里取 Youden's J 最大, 平手取**更大**的 T
  - 判据四条与 final_verdict
  - drift.py 的 POC_MIN_SESSIONS 守卫: k<10 一律无 POC 读数 (所以 POC 结构上答不了
    "3 天后要不要重画")
"""
import numpy as np
import pytest

from quant.research import poc_calibration as pc


# ---------- label ----------

def _band(lo, hi):
    return {"lo": lo, "hi": hi, "turnover": 1.0, "share_pct": 1.0}


def test_band_unchanged_is_label_0():
    assert pc._band_changed(_band(100, 101), _band(100, 101), 1.0) is False


def test_band_edge_move_of_one_bucket_flips_label():
    assert pc._band_changed(_band(100, 101), _band(101, 102), 1.0) is True
    # 不足一个 bucket 不算
    assert pc._band_changed(_band(100, 101), _band(100.5, 101.5), 1.0) is False


def test_band_identity_birth_or_death_flips_label():
    assert pc._band_changed(None, _band(100, 101), 1.0) is True
    assert pc._band_changed(_band(100, 101), None, 1.0) is True


def test_both_sides_absent_is_not_a_change():
    assert pc._band_changed(None, None, 1.0) is False


# ---------- AUC ----------

def test_auc_perfect_and_random():
    assert pc.auc([1, 2, 3, 4], [0, 0, 1, 1]) == 1.0
    assert pc.auc([1, 2, 3, 4], [1, 1, 0, 0]) == 0.0
    assert pc.auc([1, 1, 1, 1], [0, 1, 0, 1]) == 0.5      # 全并列 = 掷硬币


def test_auc_none_when_one_class_missing():
    assert pc.auc([1, 2, 3], [1, 1, 1]) is None


@pytest.mark.parametrize("seed", range(20))
def test_numpy_auc_matches_python_auc(seed):
    """bootstrap 用 numpy 版, 点估计用 python 版 —— 两者分叉 = 报的是两个量。"""
    rng = np.random.default_rng(seed)
    n = int(rng.integers(4, 80))
    sc = rng.integers(0, 5, size=n).astype(float)         # 大量并列
    lb = rng.integers(0, 2, size=n)
    a = pc.auc(list(sc), list(lb))
    b = pc._auc_np(sc, lb.astype(np.int8))
    if a is None:
        assert b is None
    else:
        assert a == pytest.approx(b, abs=1e-12)


# ---------- 阈值选择 ----------

def _rows(pairs, key="poc60"):
    return [{"ticker": "T", "ia": i, "k": 10, "label": l, key: s}
            for i, (s, l) in enumerate(pairs)]


def test_threshold_respects_fpr_ceiling():
    """FPR > 30% 的候选一律不合格, 哪怕 J 更大。"""
    rows = _rows([(3.0, 1)] * 10 + [(2.0, 0)] * 10)
    pick = pc.pick_threshold(rows, "poc60")
    assert pick["best"]["fpr"] <= pc.FPR_MAX
    for r in pick["table"]:
        assert r["eligible"] == (r["fpr"] <= pc.FPR_MAX)


def test_threshold_tie_prefers_larger_t():
    """平手取更大的 T (更保守 = 更少重画) —— 预注册明写。"""
    rows = _rows([(4.0, 1)] * 10 + [(0.05, 0)] * 10)
    best = pc.pick_threshold(rows, "poc60")["best"]
    ties = [r for r in pc.pick_threshold(rows, "poc60")["table"]
            if r["eligible"] and r["J"] == best["J"]]
    assert best["T"] == max(r["T"] for r in ties)


# ---------- 判据 ----------

def test_criteria_all_pass_path(monkeypatch):
    rows = _rows([(2.0, 1)] * 60 + [(0.1, 0)] * 60)
    monkeypatch.setattr(pc, "bootstrap_delta_auc", lambda *a, **k: [0.06, 0.20])
    res = pc.evaluate(rows, "poc60")
    assert res["crit"]["c1_auc"] and res["crit"]["c3_fpr"] and res["crit"]["c4_range"]


def test_threshold_outside_range_fails_criterion_4(monkeypatch):
    """判别力再好, T 落在 [0.5,3.0] 之外也不采纳 (预注册 §5 判据 4)。"""
    rows = _rows([(0.3, 1)] * 60 + [(0.0, 0)] * 60)
    monkeypatch.setattr(pc, "bootstrap_delta_auc", lambda *a, **k: [0.06, 0.20])
    res = pc.evaluate(rows, "poc60")
    assert res["chosen"]["T"] < pc.T_RANGE[0]
    assert res["crit"]["c4_range"] is False
    assert res["crit"]["all_pass"] is False


def test_verdict_not_adopted_when_nothing_passes():
    fake = [{"key": "poc20", "auc_poc": 0.5, "chosen": {"T": 1.0},
             "crit": {"all_pass": False}}]
    v = pc.final_verdict(fake)
    assert v["adopt"] is False and "advisory-only" in v["text"]


def test_verdict_picks_highest_auc_among_passers():
    fake = [{"key": "poc20", "auc_poc": 0.70, "auc_time": 0.6, "chosen": {"T": 1.0},
             "crit": {"all_pass": True}},
            {"key": "poc60", "auc_poc": 0.80, "auc_time": 0.6, "chosen": {"T": 1.2},
             "crit": {"all_pass": True}}]
    v = pc.final_verdict(fake)
    assert v["adopt"] is True and v["key"] == "poc60" and v["T"] == 1.2


# ---------- drift.py 守卫 ----------

def test_poc_min_sessions_guard_blanks_short_horizons():
    """k < POC_MIN_SESSIONS(10) → 无 POC 读数。这不是 bug, 是 drift.py 的守卫 ——
    但它意味着 POC 维**结构上答不了「3 天后要不要重画」**, 报告必须说出来。"""
    bars = [{"high": 101.0, "low": 99.0, "close": 100.0, "turnover": 1000.0}] * 80
    atr20 = [2.0] * 80
    assert pc.poc_shift_n(bars, 60, 63, atr20, 20, {}) is None      # k=3
    assert pc.poc_shift_n(bars, 60, 65, atr20, 20, {}) is None      # k=5
    assert pc.poc_shift_n(bars, 60, 70, atr20, 20, {}) is not None  # k=10


def test_preregistered_constants_are_pinned():
    assert pc.K_VALUES == (3, 5, 10, 15, 20, 30)
    assert pc.POC_WINDOWS == (20, 60)
    assert pc.LABEL_WINDOW == 60 and pc.ANCHOR_STRIDE == 5
    assert (pc.AUC_MIN, pc.BASELINE_MARGIN, pc.FPR_MAX) == (0.65, 0.05, 0.30)
    assert pc.T_RANGE == (0.5, 3.0)
