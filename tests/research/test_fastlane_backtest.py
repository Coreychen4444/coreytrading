"""fastlane_backtest 机械件验证 (合成数据; 真数据只跑一次)。

钉住的是**预注册里会驱动裁决的那几条语义**:
  - 三臂 + 无幅度对照臂各自的入场日 (c+1 vs c+2 vs 不入场)
  - SLOW 的失败分支 = 持现金 (设计稿字面), 与 _rescan 变体的差别
  - R-multiple 的定义与未入场取 0
  - final_verdict: 任一判据不满足 → 不采纳; 两 k 都过 → 取更严者
"""
import pytest

from quant.research import fastlane_backtest as fb


# ---------- 三臂的入场日 ----------

def _atr(n, v=1.0):
    return [v] * n


def test_qualified_fast_enters_next_day_slow_waits_one_more():
    """gate=100, ATR=1, k=1.0。c=2 收 102 (>101 达标) → fast 进 c+1=3;
    c+1 也 > gate → slow 进 c+2=4。"""
    closes = [90, 95, 102, 103, 104] + [104] * 20
    w, first_c = fb.build_worlds(0, 20, closes, 100.0, _atr(len(closes)), 1.0, rescan=False)
    assert first_c == 2
    assert w["fast_hybrid"][0] == 3 and w["fast_pure"][0] == 3
    assert w["slow"][0] == 4
    assert w["fast_nomag"][0] == 3


def test_unqualified_fast_hybrid_falls_back_to_slow():
    """c=2 收 100.5 (< gate+1N) → 未达标: hybrid 回落 ×2 规则, pure 不入场。"""
    closes = [90, 95, 100.5, 100.6, 104] + [104] * 20
    w, _ = fb.build_worlds(0, 20, closes, 100.0, _atr(len(closes)), 1.0, rescan=False)
    assert w["fast_hybrid"][0] == 4 == w["slow"][0]     # 同一段 = 回落
    assert w["fast_pure"] is None                        # 纯 FAST 弃权
    assert w["fast_nomag"][0] == 3                       # 无幅度对照照进


def test_slow_holds_cash_when_second_day_falls_back_under_gate():
    """设计稿字面: 首次确认之后次日跌回 gate 下 → 不入场持现金 (不再等下一次)。"""
    closes = [90, 95, 100.5, 99, 104, 105] + [105] * 20
    w, _ = fb.build_worlds(0, 20, closes, 100.0, _atr(len(closes)), 1.0, rescan=False)
    assert w["slow"] is None and w["fast_hybrid"] is None


def test_rescan_variant_takes_the_next_opportunity():
    """_rescan 变体在同一条价格路径上会抓住第二次机会 —— 两者若给相反结论必须写出来。"""
    closes = [90, 95, 100.5, 99, 104, 105] + [105] * 20
    w, _ = fb.build_worlds(0, 20, closes, 100.0, _atr(len(closes)), 1.0, rescan=True)
    assert w["slow"] is not None and w["slow"][0] > 4


def test_no_gate_means_no_arm_ever_enters():
    closes = [100] * 30
    w, first_c = fb.build_worlds(0, 25, closes, None, _atr(30), 1.0, rescan=False)
    assert first_c is None and all(v is None for v in w.values())


def test_stop_is_gate_anchored_not_entry_anchored():
    """本回测与 escape_velocity 的关键差异: stop = gate − 0.5×ATR20@确认日。"""
    closes = [90, 95, 102, 103, 104] + [104] * 20
    w, _ = fb.build_worlds(0, 20, closes, 100.0, _atr(len(closes)), 1.0, rescan=False)
    assert w["fast_hybrid"][4] == pytest.approx(100.0 - 0.5 * 1.0)
    assert w["slow"][4] == pytest.approx(100.0 - 0.5 * 1.0)
    # 入场价不同, 但 stop 同一条线 —— 所以每股风险才会不同 (立论的所在)
    assert w["fast_hybrid"][1] != w["slow"][1]


# ---------- R-multiple ----------

def test_r_multiple_is_profit_over_per_share_risk():
    closes = [0] * 10 + [110.0]
    seg = (5, 100.0, None, None, 90.0)          # 入场 100, stop 90 → 风险 10
    assert fb.r_multiple(seg, 10, closes) == pytest.approx(1.0)


def test_r_multiple_zero_when_never_entered():
    assert fb.r_multiple(None, 10, [0] * 11) == 0.0
    assert fb.policy_return(None, 10, [0] * 11) == 0.0


def test_r_multiple_zero_when_entry_at_or_below_stop():
    """入场价 ≤ stop 时风险单位无意义 → 取 0, 不让分母塌成放大器。"""
    closes = [0] * 10 + [110.0]
    assert fb.r_multiple((5, 90.0, None, None, 90.0), 10, closes) == 0.0


def test_risk_n_is_atr_normalised():
    seg = (3, 100.0, None, None, 95.0)
    assert fb.risk_n(seg, [2.0] * 10) == pytest.approx(2.5)


# ---------- 裁决 ----------

def _res(k, all_pass, eras=None):
    return {"k": k, "crit": {"all_pass": all_pass},
            "eras": eras or {"e1": {"dR_mean": 0.1, "ruled": True}}}


def test_no_k_passes_means_not_adopted():
    """**本次真实结论所走的分支。**"""
    v = fb.final_verdict([_res(1.0, False), _res(1.5, False)])
    assert v["adopt"] is False and v["reason"] == "no_k_passes"
    assert "×2 确认保留" in v["text"]


def test_both_k_pass_picks_the_stricter_k():
    v = fb.final_verdict([_res(1.0, True), _res(1.5, True)])
    assert v["adopt"] is True and v["k"] == 1.5


def test_era_sign_flips_are_surfaced_not_swallowed():
    """§5b-D: 合池过但 ≥3/5 个可裁决 era 翻转 → 必须写出来。"""
    eras = {f"e{i}": {"dR_mean": (-0.1 if i < 3 else 0.1), "ruled": True} for i in range(5)}
    v = fb.final_verdict([_res(1.0, True, eras)])
    assert v["adopt"] is True and v["era_flips"] == 3 and "符号翻转" in v["text"]


def test_preregistered_constants_are_pinned():
    """改这些 = 改预注册, 必须先改这条测试。"""
    assert fb.K_VALUES == (1.0, 1.5)
    assert fb.DECISION_T == 60
    assert fb.BFLOOR_BUF == 0.5
    assert fb.STOP_CI_MAX == 0.08
    assert fb.KISH_MIN == 30
    assert fb.ARMS == ("slow", "fast_hybrid", "fast_pure", "fast_nomag")
