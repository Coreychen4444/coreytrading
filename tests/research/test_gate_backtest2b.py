"""round-2b 新机械件的合成数据验证 (stop_scan_ndays / variant_stop / 选择规则)。"""
import pytest

from quant.research import gate_backtest2b as g2b


def test_stop_scan_ndays_need1_matches_round2_semantics():
    closes = [100, 99, 97, 96, 98]
    assert g2b.stop_scan_ndays(0, 4, closes, 98, 1) == (2, 3, 96)


def test_stop_scan_ndays_streak_resets_on_bounce():
    #        0    1   2    3   4    5
    closes = [100, 97, 99, 97, 96, 95]
    # need=2: idx1 破(1), idx2 弹回清零, idx3 破(1), idx4 破(2) → 触发 4, 出场 5
    assert g2b.stop_scan_ndays(0, 5, closes, 98, 2) == (4, 5, 95)


def test_stop_scan_ndays_pending_at_end():
    closes = [100, 97, 96]
    assert g2b.stop_scan_ndays(0, 2, closes, 98, 2) is None   # 确认完成于终点日 → 悬置
    assert g2b.stop_scan_ndays(0, 2, closes, 98, 3) is None   # 凑不齐


def test_variant_stop_all_five():
    atr20 = [2.0] * 10
    swings_l = [(0, 95.0)]
    e = dict(entry_j=5, entry_px=100.0, gate_lvl=98.0, atr20=atr20, swings_l=swings_l)
    assert g2b.variant_stop("bfloor_x1", **e) == (98.0, 1)
    assert g2b.variant_stop("bfloor_x2", **e) == (98.0, 2)
    assert g2b.variant_stop("bfloor_buf", **e) == (97.0, 1)    # gate − 0.5×2
    assert g2b.variant_stop("two_n", **e) == (96.0, 1)
    assert g2b.variant_stop("structure", **e) == (95.0, 1)
    # gate 缺失 → bfloor 系回退 2N
    e2 = dict(e, gate_lvl=None)
    assert g2b.variant_stop("bfloor_x1", **e2) == (96.0, 1)


def test_bfloor_x2_survives_single_day_dip_that_kills_x1():
    #        0    1     2     3    4    5
    closes = [9.0, 10.5, 9.8, 10.5, 11.0, 11.5]   # 单日跌回 gate 下又收复
    atr20 = [1.0] * 6
    lvl_fn = (lambda j: 10.0)
    x1 = g2b.simulate_variant(0, 5, closes, lvl_fn, 1, None, False,
                              "bfloor_x1", atr20, [], 1)
    x2 = g2b.simulate_variant(0, 5, closes, lvl_fn, 1, None, False,
                              "bfloor_x2", atr20, [], 1)
    assert x1[0][2] == 3                     # ×1: idx2 9.8<10 触发, idx3 出场
    assert x2[0][2] is None                  # ×2: 单日 dip 不出场, 持有到底


def test_selection_rule_boundaries():
    out = {"cells": {"cluster_x2_vol": {
        "main": True,
        "replay_T60": {
            "bfloor_x1": {},
            "two_n": {"vs_inc_mean": 0.02, "vs_inc_ci90": [0.005, 0.03],
                      "p10_diff_ci90": [-0.005, 0.01]},          # 合格
            "bfloor_buf": {"vs_inc_mean": 0.03, "vs_inc_ci90": [-0.001, 0.05],
                           "p10_diff_ci90": [0.0, 0.02]},        # CI 含 0 → 不合格
            "structure": {"vs_inc_mean": 0.05, "vs_inc_ci90": [0.01, 0.08],
                          "p10_diff_ci90": [-0.02, 0.01]},       # p10 恶化超 1pp → 不合格
        },
        "single_T60": {"bfloor_x1": {},
                       "two_n": {"vs_inc_mean": 0.02, "vs_inc_ci90": [0.004, 0.03],
                                 "p10_diff_ci90": [-0.004, 0.01]}},
    }, "swing_x1_vol": {"main": False, "replay_T60": {
        "two_n": {"vs_inc_mean": 0.9, "vs_inc_ci90": [0.5, 1.0],
                  "p10_diff_ci90": [0.5, 1.0]}}},
    }}
    v = g2b.selection(out)
    assert list(v) == ["cluster_x2_vol"]                    # 附表格不裁决
    r = v["cluster_x2_vol"]
    assert [q["variant"] for q in r["qualify_replay"]] == ["two_n"]
    assert r["winner"]["variant"] == "two_n"
    assert r["modes_contradict"] is False


def test_collect_summarize_smoke(tmp_path):
    import json
    prices = [100.0] * 240 + [100 - k for k in range(1, 21)]
    prices += [80 + min(k, 25) * 0.8 for k in range(1, 101)]
    rows = []
    for d, c in enumerate(prices):
        rows.append({"time": f"20{20 + d // 336}-{1 + (d % 336) // 28:02d}-{1 + d % 28:02d} 00:00:00",
                     "open": c, "high": c + 1, "low": c - 1, "close": c,
                     "volume": 1000, "turnover": None})
    (tmp_path / "SYN.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    eps = g2b.collect(kdir=tmp_path)
    assert len(eps) >= 1
    out = g2b.summarize(eps)
    g2b.bootstrap(out, draws=50, p10_draws=20, seed=9)
    g2b.selection(out)
    g2b.strip_private(out)
    for cell in out["cells"].values():
        for rows_ in cell.values():
            if isinstance(rows_, dict):
                for row in rows_.values():
                    if isinstance(row, dict):
                        assert "_by_month" not in row and "_vals_bm" not in row
