"""hvn_gate_backtest 的合成机械件验证。

本文件钉三类东西:
  1. **回归对照的效力前提**—— gb2 的扩展点默认恒等: 不传 kinds/extra_gates 时,
     collect 产出与基线构造相同，避免对照被扩展点污染。
  2. **裁决规则本身**—— final_verdict 是预注册 §3 全文 (含末句「平手 → 保留 cluster」)
     的代码强制点。判据全过 ≠ 采纳: H1 是非劣性假设, 非劣之后还要问"有没有更好"。
  3. regression_check 会红 —— 数字漂了必须失败 (变异测试), 否则它只是装饰。
"""
import json

import pytest

from quant.research import gate_backtest2 as g2
from quant.research import hvn_gate_backtest as hg


# ---------- 1. 扩展点默认恒等 (回归对照的效力前提) ----------

def _write_synth(tmp_path):
    """与 test_gate_backtest2 同款合成票: 平台 → 阴跌 (episode) → 缓慢修复。"""
    prices = [100.0] * 240 + [100 - k for k in range(1, 21)]
    prices += [80 + min(k, 25) * 0.8 for k in range(1, 101)]
    rows = []
    for d, c in enumerate(prices):
        rows.append({"time": (f"2020-{1 + d // 28:02d}-{1 + d % 28:02d} 00:00:00" if d < 300
                              else f"2021-{1 + (d - 300) // 28:02d}-{1 + (d - 300) % 28:02d} 00:00:00"),
                     "open": c, "high": c + 1, "low": c - 1, "close": c,
                     "volume": 1000, "turnover": 1000.0 * c})
    (tmp_path / "SYN.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")


def _cell_fingerprint(summary, keys):
    return {k: (summary["cells"][k]["fire_rate"],
                round(summary["cells"][k]["primary_T60"]["diff_mean"], 12),
                round(summary["cells"][k]["primary_T60"]["p10"], 12))
            for k in keys}


INCUMBENT_KINDS = ("swing", "ma60", "cluster")


def test_round2_kind_set_is_pinned_literally():
    """回归对照的对象是 round-2 的**那三种**画法。用 g2.KINDS 自比是同义反复 ——
    第一版测试就是那样写的, 把 KINDS 砍成两个 kind 都照样绿。"""
    assert g2.KINDS == INCUMBENT_KINDS
    assert hg.KINDS_EXT == INCUMBENT_KINDS + ("hvn",)


def test_extra_gates_default_is_identity(tmp_path):
    """默认路径 (无 kinds/extra_gates) 必须与显式传 round-2 kind 字面量逐字相同。"""
    _write_synth(tmp_path)
    a = g2.summarize(g2.collect(kdir=tmp_path))
    b = g2.summarize(g2.collect(kdir=tmp_path, kinds=INCUMBENT_KINDS, extra_gates=None),
                     kinds=INCUMBENT_KINDS)
    assert set(a["cells"]) == {f"{k}_x{n}{v}" for k in INCUMBENT_KINDS
                               for n in (1, 2, 3) for v in ("", "_vol")}
    assert set(a["cells"]) == set(b["cells"])
    assert _cell_fingerprint(a, a["cells"]) == _cell_fingerprint(b, b["cells"])


def test_adding_hvn_does_not_perturb_incumbent_cells(tmp_path):
    """加第 4 个 kind 不得改动原三 kind 的任何读数 —— 否则对照被污染。"""
    _write_synth(tmp_path)
    base = g2.summarize(g2.collect(kdir=tmp_path, kinds=INCUMBENT_KINDS),
                        kinds=INCUMBENT_KINDS)
    hg._FALLBACK.clear()
    ext = g2.summarize(g2.collect(kdir=tmp_path, kinds=INCUMBENT_KINDS + ("hvn",),
                                  extra_gates={"hvn": hg.hvn_builder}),
                       kinds=INCUMBENT_KINDS + ("hvn",))
    incumbent = set(base["cells"])
    assert incumbent < set(ext["cells"])                 # 严格新增
    assert any(k.startswith("hvn_") for k in ext["cells"])
    assert _cell_fingerprint(base, incumbent) == _cell_fingerprint(ext, incumbent)


def test_hvn_builder_falls_back_to_cluster_and_records(tmp_path):
    """上方无 HVN → 回退 cluster 的取法, 且回退这件事必须被记账 (判据 4 的数据源)。"""
    hg._FALLBACK.clear()
    ctx = {"bars": [], "i": 5, "close_i": 100.0, "atr14_i": None,   # i 太小 → hvn 无定义
           "cluster_gate": 123.0, "ticker": "SYN"}
    fn = hg.hvn_builder(ctx)
    assert fn(0) == 123.0 and hg._FALLBACK == [True]

    hg._FALLBACK.clear()
    ctx2 = dict(ctx, cluster_gate=None)
    assert hg.hvn_builder(ctx2)(0) is None               # 两者皆无 = gate 不存在, 永不触发
    assert hg._FALLBACK == [True]


# ---------- 2. add_gap_points ----------

def test_add_gap_points_is_notfired_minus_fired():
    """gap 点估计 = Σnf/n_nf − Σf/n_f, 不是 CI 中点 (那是另一个量)。"""
    summary = {"cells": {"x_x2": {"primary_T60": {
        # 槽位 [Σdiff, n, Σstop差, Σ A@notfired, n_nf, Σ A@fired, n_f]
        "_by_month": {"2020-01": [0.0, 10, 0, -1.0, 5, 0.5, 5],
                      "2020-02": [0.0, 10, 0, -1.0, 5, 0.5, 5]}}}}}
    hg.add_gap_points(summary)
    row = summary["cells"]["x_x2"]["primary_T60"]
    assert row["notfired_gap_point"] == pytest.approx(-0.2 - 0.1)


def test_add_gap_points_none_when_a_side_empty():
    summary = {"cells": {"x_x2": {"primary_T60": {
        "_by_month": {"2020-01": [0.0, 10, 0, -1.0, 5, 0.0, 0]}}}}}
    hg.add_gap_points(summary)
    assert summary["cells"]["x_x2"]["primary_T60"]["notfired_gap_point"] is None


# ---------- 3. 裁决规则 (预注册 §3 的代码强制点) ----------

def _verdicts(all_pass=True):
    return [{"suffix": s, "all_pass": all_pass} for s in ("_x1", "_x1_vol", "_x2", "_x2_vol")]


def test_criteria_pass_but_gap_ci_contains_zero_is_a_tie_and_keeps_cluster():
    """合成样本覆盖：四判据全过但 gap 差 CI 含 0 时保留 incumbent。"""
    h2h = {"_x2": {"gap_diff_ci90": [-0.0077, 0.0031], "exp_diff_ci90": [-0.0048, -0.0003]}}
    v = hg.final_verdict(_verdicts(True), h2h)
    assert v["adopt"] is False and v["reason"] == "tie"
    assert v["exp_significantly_worse"] is True
    assert "平手" in v["text"]


def test_gap_ci_strictly_negative_adopts():
    """拒绝权显著更强 (CI 全负 = hvn 挡掉的那批确实更差) → 采纳。"""
    h2h = {"_x2": {"gap_diff_ci90": [-0.05, -0.01], "exp_diff_ci90": [-0.001, 0.001]}}
    v = hg.final_verdict(_verdicts(True), h2h)
    assert v["adopt"] is True and v["reason"] == "gap_significant"


def test_gap_ci_strictly_positive_rejects():
    h2h = {"_x2": {"gap_diff_ci90": [0.01, 0.05], "exp_diff_ci90": [-0.001, 0.001]}}
    v = hg.final_verdict(_verdicts(True), h2h)
    assert v["adopt"] is False


def test_criteria_failure_short_circuits_before_tie_clause():
    h2h = {"_x2": {"gap_diff_ci90": [-0.05, -0.01], "exp_diff_ci90": [0.0, 0.0]}}
    v = hg.final_verdict(_verdicts(False), h2h)
    assert v["adopt"] is False and v["reason"] == "criteria_failed"


def test_decision_cell_is_x2_not_a_post_hoc_pick():
    """裁决格在跑之前锁死 (设计稿 §3a)。改它 = 改预注册, 必须先改这条测试。"""
    assert hg.DECISION_CELL == "_x2"
    assert (hg.DECISION_MODE, hg.DECISION_T) == ("primary", 60)


def test_preregistered_thresholds_are_pinned():
    assert (hg.PREMIUM_TOL, hg.FIRE_TOL, hg.FALLBACK_MAX) == (0.005, 0.15, 0.20)


# ---------- 4. 私人 baseline 比较机械件 (合成数据) ----------

BASELINE = {
    "n_episodes": 20,
    "months": 4,
    "kish_n": 3.5,
    "A_primary_T60": {"mean": 0.02, "median": 0.01, "p10": -0.10, "stop_rate": 0.25},
    "cells": {
        "swing_x1": [0.80, 0.01, -0.02, 0.03],
        "cluster_x2": [0.60, -0.01, -0.04, 0.02],
    },
}


def test_private_baseline_is_loaded_from_json(tmp_path):
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps(BASELINE), encoding="utf-8")
    assert hg.load_baseline(path) == BASELINE


def _baseline_shaped_summary():
    """Construct a summary that exactly matches the synthetic baseline."""
    cells = {}
    for key, (fire, dm, lo, hi) in BASELINE["cells"].items():
        cells[key] = {"fire_rate": fire,
                      "primary_T60": {"diff_mean": dm, "diff_ci90": [lo, hi]}}
    return {"n_episodes": BASELINE["n_episodes"], "months": BASELINE["months"],
            "kish_n": BASELINE["kish_n"],
            "A": {"primary_T60": dict(BASELINE["A_primary_T60"])}, "cells": cells}


def test_regression_check_passes_on_exact_private_baseline():
    ok, bad = hg.regression_check(_baseline_shaped_summary(), BASELINE)
    assert ok and bad == []


@pytest.mark.parametrize("mutate", [
    lambda s: s.update(n_episodes=21),
    lambda s: s.update(months=5),
    lambda s: s.update(kish_n=4.0),
    lambda s: s["A"]["primary_T60"].update(mean=0.03),
    lambda s: s["cells"]["cluster_x2"].update(fire_rate=0.70),
    lambda s: s["cells"]["cluster_x2"]["primary_T60"].update(diff_mean=-0.03),
    lambda s: s["cells"]["cluster_x2"]["primary_T60"].update(diff_ci90=[-0.05, -0.01]),
    lambda s: s["cells"].pop("swing_x1"),
])
def test_regression_check_goes_red_on_any_drift(mutate):
    """数字漂了必须失败 —— 否则「复现不了就中止」只是一句口号。"""
    s = _baseline_shaped_summary()
    mutate(s)
    ok, bad = hg.regression_check(s, BASELINE)
    assert not ok and bad
