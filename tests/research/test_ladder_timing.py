"""ladder_timing_backtest 机械件验证 (合成数据; 真数据只跑一次)。

两臂、判据、统计口径全部**复用**`gate_path_taxonomy_backtest`（已被那份测试钉住），
所以这里只测本轮新增的三件:

  1. **ANCHOR 构造**= `ladder_entry.collect` 的逐字骨架 —— 与暴力参照实现对拍
  2. **§3e 恒等式的经验自证**—— 破位锚构造产出的必须全是 ①repair
  3. **§5 重跑触发**—— 跑前锁定的那张真值表, 不许事后改
"""
import json
import random

import pytest

from quant.core.episodes import episodes_of
from quant.research import gate_path_taxonomy_backtest as gpt
from quant.research import ladder_timing_backtest as lt
from quant.research.gate_backtest import confirmed_swing_highs, sma_series
from quant.research.gate_backtest2 import WINDOW, atr_series, cluster_gate, confirmed_swing_lows


# ── 1. 锁定值: 全部继承自已锁定的上一轮, 不许本文件另存一份 ────────

def test_parameters_are_inherited_not_re_typed():
    """本轮不新增任何可调参数 —— 每个数都必须是上一轮那个对象。"""
    assert lt.K_MAG is gpt.K_MAG == 0.5
    assert lt.BFLOOR_BUF is gpt.BFLOOR_BUF == 0.5
    assert lt.DECISION_T is gpt.DECISION_T == 60
    assert lt.REPORT_T is gpt.REPORT_T == 20
    assert lt.MIN_TAIL == 50               # ladder_entry `if end - i < 50` 逐字同款
    assert lt.ADJUDICATING == "ANCHOR" and lt.REPLICATION == "ROLL"


def test_arms_and_criteria_come_from_the_previous_round():
    """两臂与判据不许在本文件重写 —— ①在两轮之间必须苹果对苹果。"""
    import inspect
    src = inspect.getsource(lt)
    for forbidden in ("def build_worlds", "def measure", "def ladder_legs", "def analyse"):
        assert forbidden not in src, f"{forbidden} 被重写了一份 —— 必须复用 gpt 的"


# ── 2. ANCHOR 构造与暴力参照对拍 ─────────────────────────────────

def _series(seed, n=600):
    from datetime import date, timedelta
    rg = random.Random(seed)
    d0 = date(2015, 1, 1)
    px, out = 100.0, []
    for k in range(n):
        px = max(2.0, px * (1 + rg.gauss(0, 0.025)))
        hi = px * (1 + abs(rg.gauss(0, 0.012)))
        lo = px * (1 - abs(rg.gauss(0, 0.012)))
        out.append({"time": f"{d0 + timedelta(days=k)} 00:00:00",
                    "open": lo + (hi - lo) * rg.random(), "high": round(hi, 4),
                    "low": round(lo, 4), "close": round(px, 4),
                    "volume": rg.randint(10 **5, 10 **7)})
    return out


def _write(dirpath, name, bars):
    (dirpath / f"{name}.jsonl").write_text("\n".join(json.dumps(b) for b in bars) + "\n")


def _brute_anchor(bars, term_len):
    """`ladder_entry.collect` 的骨架逐字重写一遍 → [(date, gate, over_n)]。"""
    closes = [b["close"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    dates = [b["time"][:10] for b in bars]
    n = len(closes)
    ma60 = sma_series(closes, 60)
    a20 = atr_series(highs, lows, closes, 20)
    a14 = atr_series(highs, lows, closes, 14)
    sh, sl = confirmed_swing_highs(highs), confirmed_swing_lows(lows)
    out = []
    for i in episodes_of(closes, ma60):
        end = min(i + WINDOW, n - 1)
        if end - i < lt.MIN_TAIL:
            continue
        gate = cluster_gate(i, closes[i], sh, sl, a14[i])
        if gate is None:
            continue
        d1 = next((j for j in range(i + 1, end + 1) if closes[j] > gate), None)
        if d1 is None or a20[d1] is None or d1 + 1 > end:
            continue
        N = a20[d1]
        if closes[d1] <= gate + lt.K_MAG * N:
            continue
        if d1 + term_len > n - 1 or not a20[d1 + 1]:
            continue
        if closes[d1] <= gate - lt.BFLOOR_BUF * N:
            continue
        out.append((dates[d1], round(gate, 9), round((closes[d1] - gate) / N, 9)))
    return out


@pytest.mark.parametrize("seed", range(5))
def test_anchor_collector_matches_the_ladder_entry_skeleton(tmp_path, seed):
    """ANCHOR 必须逐条等于 `ladder_entry` 的样本定义 —— 它就是 fastlane_k 的采纳域。

    D1 = **首个**close>gate 那天, 然后幅度不达就**丢弃 episode**
    (不是"首个满足幅度的日子")。这个区别决定样本, 不是细节。
    """
    bars = _series(seed)
    _write(tmp_path, "TESTX", bars)
    rows, _, _ = lt.collect_anchor(tmp_path, lt.DECISION_T, require_volume=False)
    brute = _brute_anchor(bars, lt.DECISION_T)
    assert len(rows) == len(brute), f"collect {len(rows)} vs 暴力 {len(brute)}"
    for r, (d, g, o) in zip(rows, brute):
        assert r["date"] == d and r["over_n"] == pytest.approx(o)
    _SEEN_ANCHOR.append(len(rows))


_SEEN_ANCHOR = []


def test_the_anchor_comparison_was_not_vacuous():
    """上面那条若两边都是 0 条就是空转 —— 至少要有一个种子造出真样本。

    (依赖 pytest 的文件内顺序执行; 与 taxonomy 那份的 `checked > 20` 同一种守卫。)
    """
    assert _SEEN_ANCHOR, "对拍测试一次都没跑到"
    assert sum(_SEEN_ANCHOR) > 0, f"五个种子一条 episode 都没造出来: {_SEEN_ANCHOR}"


def test_magnitude_is_judged_on_the_first_crossing_day_not_the_first_qualifying_day(tmp_path):
    """首个上穿日不达幅度 → **丢弃**, 不许往后找一个达标的日子。

    这条是 `ladder_entry` 的样本口径; 改成"往后找"会把样本悄悄扩大并偏向强势样本。
    """
    import inspect
    src = inspect.getsource(lt.collect_anchor)
    assert "d1 = next((j for j in range(i + 1, end + 1) if closes[j] > gate), None)" in src
    assert "if closes[d1] <= gate + K_MAG * N:" in src
    assert 'drops["magnitude"] += 1' in src and "continue" in src


# ── 3. §3e 恒等式的经验自证 ──────────────────────────────────────

@pytest.mark.parametrize("seed", range(5))
def test_anchor_construction_yields_only_repair(tmp_path, seed):
    """破位锚构造 100% 产出 ①repair —— §3e 那个恒等式的经验复核。

    推导: cluster_gate 保证 closes[i] < gate; D1 ∈ (i, i+120] ⟹ i ∈ [D1−120, D1);
    两条合起来正好是 `breakdown_in_window` 的判据。**这也正是②③④无处复验的原因。**
    """
    bars = _series(seed)
    _write(tmp_path, "TESTX", bars)
    rows, _, mix = lt.collect_anchor(tmp_path, lt.DECISION_T, require_volume=False)
    if not rows:
        pytest.skip("该随机种子没造出 episode")
    assert set(mix) == {"repair"}, f"破位锚构造出现了非 repair: {mix}"
    assert all(r["cls"] == "repair" for r in rows)


def test_roll_arm_keeps_only_the_repair_class(tmp_path):
    """复制臂只取①—— 它要与 ANCHOR 比的是同一个东西。"""
    bars = _series(1)
    _write(tmp_path, "TESTX", bars)
    for b in ("QQQ", "SMH"):
        _write(tmp_path, b, _series(77))
    rows, _ = lt.collect_roll(tmp_path, lt.DECISION_T, require_volume=False)
    assert all(r["cls"] == "repair" for r in rows)


def test_roll_arm_runs_without_the_volume_leg(tmp_path):
    """§3a: 两个构造格必须只差构造 ⟹ 复制臂也关量腿。

    关掉之后不再需要基准 —— 没有 QQQ/SMH 文件也要跑得动 (这正是恢复 1995+ 全池的机制)。
    """
    bars = _series(2)
    _write(tmp_path, "TESTX", bars)          # 故意不写 QQQ/SMH
    rows, _ = lt.collect_roll(tmp_path, lt.DECISION_T, require_volume=False)
    assert isinstance(rows, list)


def test_volume_flag_default_stays_on_so_the_previous_verdict_is_reproducible():
    """`gpt.collect` 的量腿默认必须仍是 True —— 改了上一轮 §12 的读数就不可复现。"""
    import inspect
    sig = inspect.signature(gpt.collect)
    assert sig.parameters["require_volume"].default is True


# ── 4. §5 重跑触发 (跑前锁定的真值表) ────────────────────────────

def _v(d):
    return {"decision": d, "text": ""}


@pytest.mark.parametrize("main,repl,expect", [
    ("support", "support", "hold"),
    ("reject", "reject", "hold"),
    ("support", "reject", "downgrade"),
    ("reject", "support", "downgrade"),
    ("insufficient", "support", "undetermined"),
    ("support", "insufficient", "undetermined"),
    ("no_data", "reject", "undetermined"),
])
def test_rerun_trigger_truth_table(main, repl, expect):
    """§5 锁定的表。看完①再定就是"跑完再挑" —— 这张表必须先于结果存在。"""
    assert lt.trigger_234(_v(main), _v(repl))[0] == expect


def test_downgrade_text_states_the_structural_limitation():
    """降级时必须写明②③④**无法在 ANCHOR 上复验**, 不能只说"存疑"。"""
    _, txt = lt.trigger_234(_v("support"), _v("reject"))
    assert "无法在 ANCHOR 上复验" in txt


# ── 5. 裁决方向: 这是对 incumbent 的检验 ─────────────────────────

def test_failure_recommends_retiring_the_incumbent_not_keeping_it():
    """§1: `fastlane_k=0.5` 已经在 live 且证据基座被证明混淆 ⟹
    「未获支持」= **建议撤下**, 不是「维持现状」。方向反了就把结论读反。"""
    res = gpt.analyse(_rows_failing(), "ANCHOR", draws=500)
    v = lt.verdict_of(res)
    assert v["decision"] == "reject"
    assert "撤下" in v["text"] and "等 用户" in v["text"]


def test_pass_says_the_live_parameter_is_supported():
    res = gpt.analyse(_rows_passing(), "ANCHOR", draws=500)
    v = lt.verdict_of(res)
    assert v["decision"] == "support" and "留在 live 有据" in v["text"]


def test_insufficient_is_not_a_rejection():
    res = gpt.analyse(_rows_passing(months=1, n=20), "ANCHOR", draws=500)
    v = lt.verdict_of(res)
    assert v["decision"] == "insufficient" and "撤下" not in v["text"]


def _rows_passing(months=40, n=200):
    out = []
    for k in range(n):
        out.append({"ticker": "T", "date": "2020-01-01", "month": f"2020-{k % months:02d}",
                    "year": 2020, "cls": "repair", "trend": "up", "gate_over_ath": 0.9,
                    "over_n": 0.8, "win_eps": 1, "win_eps_below": 1, "flat_days": 5,
                    "slow_entered": True, "fast_tranche2": True, "fast_tranche3": True,
                    "FAST": {"dep": 1.0, "ret": 0.05, "riskN": 1.0, "risk": 0.1,
                             "stopped": False, "R": 0.5},
                    "SLOW": {"dep": 1.0, "ret": 0.03, "riskN": 1.4, "risk": 0.1,
                             "stopped": False, "R": 0.3}})
    return out


def _rows_failing():
    rows = _rows_passing()
    for k, r in enumerate(rows):
        r["FAST"] = dict(r["FAST"], stopped=k % 3 == 0)     # 止损率差远超守卫
    return rows
