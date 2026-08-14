"""stop_cost_guard 机械件验证 (合成数据; 真数据待 T 锁定后只跑一次)。

四件要钉的:
  1. **代价算式**—— gpt.measure 的 `stop_cost`: 实现值含滑点 / 权重 / 未止损=0 / 负值不夹
  2. **配对谓词**—— 每笔已入场 = 判据① 同一条 (SLOW 未入场整条退出, 不再稀释)
  3. **两种 T 的真值表**—— A_strict 读上界 / B_harm_veto 读下界, 不许混
  4. **未锁拒跑**—— T_MODE is None ⟹ main 退出码 2, 一个数字都不算
"""
import pytest

from quant.research import gate_path_taxonomy_backtest as gpt
from quant.research import stop_cost_guard_backtest as sg
from quant.research.gate_backtest2 import SLIP


# ── 1. 代价算式 ──────────────────────────────────────────────────

def test_stop_cost_is_the_realized_loss_with_slip():
    """单档全仓, 第 2 天收盘破 stop, 第 3 天收盘出场:
    cost = (px入 − px出·(1−SLIP)) / N —— 手算逐位对上。"""
    closes = [100.0, 80.0, 70.0] + [70.0] * 20
    m = gpt.measure([(1.0, 0, 100.0)], stop=90.0, n_common=5.0, term=20, closes=closes)
    exit_px = 70.0 * (1 - SLIP)
    assert m["stopped"] is True
    assert m["stop_cost"] == pytest.approx((100.0 - exit_px) / 5.0)
    assert m["stop_cost"] > 0


def test_stop_cost_is_zero_when_not_stopped():
    """未止损 ⟹ 代价恒 0 —— 即使这一腿**亏着**收场。

    ⚠️ 亏损收场是关键: 第一版用全平序列 (r=0), 把"未止损也计代价"的变异漏过了 ——
    那个变异会把持有到期的浮亏也记成止损代价, 守卫就变成了收益判据的复读机。
    """
    down = [100.0, 95.0, 92.0] + [91.0] * 22          # 跌但从未收破 stop=90
    m = gpt.measure([(1.0, 0, 100.0)], stop=90.0, n_common=5.0, term=20, closes=down)
    assert m["stopped"] is False and m["stop_cost"] == 0.0
    assert m["ret"] < 0, "fixture 要亏着收场, 否则这条又测不到东西"

    up = [100.0, 105.0, 120.0] + [125.0] * 22          # 盈利收场同样恒 0
    m2 = gpt.measure([(1.0, 0, 100.0)], stop=90.0, n_common=5.0, term=20, closes=up)
    assert m2["stopped"] is False and m2["stop_cost"] == 0.0


def test_stop_cost_is_tranche_weighted():
    """半仓止损 ⟹ 代价折半 —— 权重必须进算式。"""
    closes = [100.0, 80.0, 70.0] + [70.0] * 20
    full = gpt.measure([(1.0, 0, 100.0)], stop=90.0, n_common=5.0, term=20, closes=closes)
    half = gpt.measure([(0.5, 0, 100.0)], stop=90.0, n_common=5.0, term=20, closes=closes)
    assert half["stop_cost"] == pytest.approx(full["stop_cost"] * 0.5)


def test_stop_cost_gap_up_profit_is_negative_not_clamped():
    """止损触发次日跳空高开 → 实现为盈利 → cost 为负, **不夹取**(设计稿 §3)。
    夹到 0 会单侧美化 FAST —— 对称性靠不夹来保证。"""
    closes = [100.0, 80.0, 130.0] + [130.0] * 20     # 收盘 80 破 stop 90, 次日收 130
    m = gpt.measure([(1.0, 0, 100.0)], stop=90.0, n_common=5.0, term=20, closes=closes)
    assert m["stopped"] is True
    assert m["stop_cost"] < 0, "实现为盈利的止损必须记负代价, 不许夹到 0"


def test_stop_cost_uses_the_common_denominator():
    closes = [100.0, 80.0, 70.0] + [70.0] * 20
    a = gpt.measure([(1.0, 0, 100.0)], stop=90.0, n_common=5.0, term=20, closes=closes)
    b = gpt.measure([(1.0, 0, 100.0)], stop=90.0, n_common=10.0, term=20, closes=closes)
    assert a["stop_cost"] == pytest.approx(b["stop_cost"] * 2)


def test_new_key_does_not_disturb_the_old_keys():
    """只加新键 (设计稿 §6): 既有键一个都不许变 —— 两轮旧裁决的可复现性靠这条。"""
    closes = [100.0, 80.0, 70.0] + [70.0] * 20
    m = gpt.measure([(1.0, 0, 100.0)], stop=90.0, n_common=5.0, term=20, closes=closes)
    assert set(m) == {"dep", "ret", "riskN", "risk", "stopped", "R", "stop_cost"}


# ── 2. 配对谓词 ──────────────────────────────────────────────────

def _row(month, fast_cost, slow_cost, slow_entered=True):
    slow_riskN = 1.2 if slow_entered else None
    return {"ticker": "T", "date": "2020-01-01", "month": month, "year": 2020,
            "cls": "repair", "trend": "up", "gate_over_ath": 0.9, "over_n": 0.8,
            "win_eps": 1, "win_eps_below": 1, "flat_days": 3, "slow_entered": slow_entered,
            "fast_tranche2": True, "fast_tranche3": True,
            "FAST": {"dep": 1.0, "ret": 0.02, "riskN": 1.0, "risk": 0.1,
                     "stopped": fast_cost > 0, "R": 0.2, "stop_cost": fast_cost},
            "SLOW": {"dep": 1.0 if slow_entered else 0.0, "ret": 0.02,
                     "riskN": slow_riskN, "risk": 0.1 if slow_entered else 0.0,
                     "stopped": slow_cost > 0, "R": 0.2,
                     "stop_cost": slow_cost if slow_entered else 0.0}}


def test_pairing_excludes_episodes_where_slow_did_not_enter():
    """C = 每笔已入场: SLOW 没入场的 episode 整条退出配对 —— 不再以 stopped=False 稀释。
    构造: 配对内两臂代价相同 (差=0); 再塞 30 条 SLOW 未入场且 FAST 大代价的 —— 若谓词
    失守, 这 30 条会把点估计推成正的。"""
    rows = [_row(f"2020-{m:02d}", 0.5, 0.5) for m in range(1, 13)] * 5
    rows += [_row(f"2020-{m % 12 + 1:02d}", 5.0, 0.0, slow_entered=False) for m in range(30)]
    g = sg.cost_guard(rows, "B_harm_veto", draws=500)
    assert g["n_pair"] == 60
    assert g["cost_point"] == pytest.approx(0.0)


def test_pairing_predicate_matches_criterion_one():
    """谓词必须与判据① 同一条 (riskN is not None), 不是另一个近义写法。"""
    import inspect
    src = inspect.getsource(sg.cost_guard)
    assert 'r["FAST"]["riskN"] is not None and r["SLOW"]["riskN"] is not None' in src


# ── 3. 两种 T 的真值表 ───────────────────────────────────────────

def _straddle_rows(shift):
    """逐月正负交替 + shift → 可控地把 CI 摆在零点附近。"""
    rows = []
    for k in range(240):
        m = k % 24
        cost = (1.2 if m % 2 == 0 else -1.0) + shift
        rows.append(_row(f"20{20 + m // 12}-{m % 12 + 1:02d}", max(cost, 0.0) + 0.001,
                         max(-cost, 0.0) + 0.001))
    return rows


def test_A_strict_requires_significantly_cheaper():
    """A: 上界 < 0 才过。CI 骑零 → A 否, B 过 —— 两模式在同一份数据上必须分得开。"""
    rows = _straddle_rows(0.0)
    ga = sg.cost_guard(rows, "A_strict", draws=2000)
    gb = sg.cost_guard(rows, "B_harm_veto", draws=2000)
    lo, hi = ga["cost_ci90"]
    assert lo < 0 < hi, "样本没把 CI 摆到骑零, 这条测不出模式差"
    assert ga["pass"] is False and gb["pass"] is True


def test_B_fails_when_significantly_more_expensive():
    rows = [_row(f"2020-{m % 12 + 1:02d}", 2.0, 0.1) for m in range(120)]
    gb = sg.cost_guard(rows, "B_harm_veto", draws=1000)
    assert gb["cost_ci90"][0] > 0 and gb["pass"] is False


def test_A_passes_when_significantly_cheaper():
    rows = [_row(f"2020-{m % 12 + 1:02d}", 0.1, 2.0) for m in range(120)]
    ga = sg.cost_guard(rows, "A_strict", draws=1000)
    assert ga["cost_ci90"][1] < 0 and ga["pass"] is True


def test_unknown_mode_raises():
    with pytest.raises(ValueError):
        sg.cost_guard([_row("2020-01", 0.5, 0.5)], "C_tolerance")


# ── 4. 重判接线 ──────────────────────────────────────────────────

def _base(all_pass=True, kish_ok=True):
    return {"n": 200, "kish": 100.0 if kish_ok else 5.0,
            "crit": {"c1_riskN_down": all_pass, "c2_R_up": all_pass, "c3_deploy": True,
                     "c4_abs_exp": True, "c5_stop_guard": False, "c6_kish": kish_ok,
                     "all_pass": False}}


def test_rejudge_swaps_only_criterion_five():
    """①②③④⑥ 原样、⑤ 换代价守卫 —— 旧⑤ (次数) 的失败不再参与, 但要报出来。"""
    v = sg.rejudge(_base(), {"pass": True})
    assert v["decision"] == "support"
    assert v["crit"]["c5_cost_guard"] is True and "c5_stop_guard" not in v["crit"]
    assert v["old_c5_count_guard"] is False       # 旧⑤的读数保留展示, 不参与判定


def test_rejudge_still_fails_when_other_criteria_fail():
    """代价守卫过了也救不了判据②不过的格 (ANCHOR_VOL 的形状)。"""
    v = sg.rejudge(_base(all_pass=False), {"pass": True})
    assert v["decision"] == "reject" and "c2_R_up" in v["text"]


def test_rejudge_insufficient_stays_insufficient():
    v = sg.rejudge(_base(kish_ok=False), {"pass": True})
    assert v["decision"] == "insufficient"


def test_verdict_texts_keep_the_recommendation_only_semantics():
    """两个方向都只出建议，不能自动修改 production。"""
    assert "不自动实施" in sg.rejudge(_base(), {"pass": True})["text"]
    assert "不自动实施" in sg.rejudge(_base(), {"pass": False})["text"]
    assert "撤下" in sg.rejudge(_base(), {"pass": False})["text"]


# ── 5. 未锁拒跑 ──────────────────────────────────────────────────

def test_T_is_locked_to_harm_veto():
    """T 由协议锁定为 B_harm_veto；修改它必须创建新预注册版本。

    (锁定前这条钉的是「T_MODE is None ⟹ main 退出码 2 拒跑」—— 那半机制保留在
    main_cli 里作为未来预注册的模板, 本轮已走过它。)"""
    assert sg.T_MODE == "B_harm_veto"


def test_seed_is_independent():
    """成本守卫使用独立的固定 seed，不复用对照回测抽样序列。"""
    assert sg.BOOT_SEED == 374_166
    assert sg.BOOT_SEED != gpt.BOOT_SEED
