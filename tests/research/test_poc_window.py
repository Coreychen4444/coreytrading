"""poc_window (round-2) 机械件验证 (合成数据)。

本轮的全部意义在于**把自变量与因变量解耦**—— round-1 里 poc60 的 AUC 0.812 是
"POC 与 label 共用同一份 profile" 的产物。所以这里钉的第一件事就是: label 只由
**价格转折点**产生, 与 volume profile 无关。
"""
import pytest

from quant.research import poc_window as pw


# ---------- label = pivot 锚的 zone 组 ----------

def test_zone_legs_takes_three_confirmed_lows_below_reference():
    lows = [10, 8, 12, 11, 9, 13, 12, 7, 14, 15, 16, 17]
    swl = pw._swing_lows(lows, wing=2)
    legs = pw.zone_legs(swl, idx=len(lows) - 1, ref_close=20.0)
    assert legs == tuple(sorted(legs, reverse=True))     # 由高到低
    assert len(legs) <= pw.ZONE_LEGS


def test_zone_legs_excludes_unconfirmed_pivots_no_lookahead():
    """pivot 必须 idx+wing ≤ 当日才算已确认 —— 否则是前视。"""
    lows = [10, 9, 5, 9, 10, 11, 12]
    swl = pw._swing_lows(lows, wing=2)
    assert swl and swl[0][0] == 2
    assert pw.zone_legs(swl, idx=3, ref_close=20.0) == ()        # 还没确认
    assert pw.zone_legs(swl, idx=4, ref_close=20.0) == (5.0,)    # idx+2 = 4, 确认


def test_zone_legs_only_below_reference_price():
    lows = [5, 4, 1, 4, 5, 4, 9, 4, 20, 4, 5, 6]
    swl = pw._swing_lows(lows, wing=2)
    hi = pw.zone_legs(swl, idx=len(lows) - 1, ref_close=100.0)
    lo = pw.zone_legs(swl, idx=len(lows) - 1, ref_close=2.0)
    assert all(v < 100.0 for v in hi)
    assert all(v < 2.0 for v in lo)


def test_zone_changed_needs_a_full_bucket():
    assert pw.zone_changed((10.0, 8.0, 6.0), (10.4, 8.0, 6.0), bucket=0.5) == 0
    assert pw.zone_changed((10.0, 8.0, 6.0), (10.6, 8.0, 6.0), bucket=0.5) == 1


def test_zone_changed_on_leg_count_change():
    """pivot 消失/新生 = 结构变了, 与边缘移动同等对待。"""
    assert pw.zone_changed((10.0, 8.0), (10.0, 8.0, 6.0), bucket=0.5) == 1
    assert pw.zone_changed((), (), bucket=0.5) == 0


def test_label_source_is_price_only_not_volume():
    """回归守卫: label 只吃 lows, 不碰 turnover —— round-1 的耦合不得复现。"""
    import inspect
    src = inspect.getsource(pw.zone_legs) + inspect.getsource(pw.zone_changed)
    for forbidden in ("turnover", "volume_profile", "top_hvn", "_poc"):
        assert forbidden not in src, forbidden


# ---------- 估计量一致性 (冒烟时抓到的真 bug) ----------

def test_all_comparisons_run_on_the_common_scorable_subset():
    """POC_MIN_SESSIONS 让 k=3/5 无读数; 点估计在全样本、CI 在子集 = 两个估计量。

    冒烟实测点估计 −0.1603 落在自己的 CI [−0.039,−0.033] 之外, 正是这个错。
    """
    rows = []
    for k, poc in ((3, None), (5, None), (10, 1.0), (20, 2.0)):
        for lab, ticker in ((1, "A"), (0, "B")):
            rows.append({"ticker": ticker, "k": k, "label": lab,
                         "price_drift_n": float(k), "poc20": poc, "poc60": poc})
    res = pw.evaluate(rows)
    assert res["n_units"] == 8 and res["n_common"] == 4
    # 共同子集里只剩 k∈{10,20}, 所以时间基线的 AUC 必须按那个子集算
    assert res["auc_k"] == pytest.approx(0.5)
    assert res["n_k"] == 4


def test_per_k_table_still_reports_the_unscorable_buckets():
    """k=3/5 必须出现在分 k 表里并显示"可评分 0" —— 静默消失会让人以为覆盖到了。"""
    rows = [{"ticker": "A", "k": k, "label": i % 2, "price_drift_n": 1.0,
             "poc20": (None if k < 10 else 1.0), "poc60": (None if k < 10 else 1.0)}
            for k in pw.K_VALUES for i in range(4)]
    res = pw.evaluate(rows)
    assert res["per_k"][3]["scored"] == 0 and res["per_k"][3]["n"] == 4
    assert res["per_k"][10]["scored"] == 4


# ---------- 动作 ----------

def _res(keep, switch):
    return {"window": {"switch_to_60": switch},
            "keep": {"keep_dimension": keep, "chosen_T": {"T": 1.2}}}


def test_failing_keep_criteria_drops_the_dimension():
    # 文案  改已裁决态 (ledger R5): drop 分支 = 复算确认无待办;
    # 两个 keep 分支 = 与已执行裁决相反, 必须标翻案警告而不是产出待办。
    a = pw.final_action(_res(keep=False, switch=True))
    assert a["action"] == "drop_dimension" and "已裁决" in a["text"]
    assert "无待办" in a["text"]
    for keep_res in (_res(keep=True, switch=True), _res(keep=True, switch=False)):
        assert "与  已执行裁决" in pw.final_action(keep_res)["text"]


def test_keep_plus_window_switch():
    assert pw.final_action(_res(keep=True, switch=True))["action"] == "switch_window_60"


def test_keep_without_window_switch():
    assert pw.final_action(_res(keep=True, switch=False))["action"] == "keep_window_20"


def test_action_is_always_one_of_three_never_status_quo_limbo():
    """预注册明令: 二选一, 不允许"既不改也不摘"继续挂着。"""
    seen = {pw.final_action(_res(k, s))["action"]
            for k in (True, False) for s in (True, False)}
    assert seen == {"drop_dimension", "switch_window_60", "keep_window_20"}


def test_preregistered_constants_are_pinned():
    assert pw.POC_WINDOWS == (20, 60)
    assert (pw.SWING_WING, pw.PIVOT_LOOKBACK, pw.ZONE_LEGS) == (2, 120, 3)
    assert (pw.WINDOW_MARGIN, pw.TIME_MARGIN, pw.PRICE_MARGIN) == (0.05, 0.05, 0.03)
