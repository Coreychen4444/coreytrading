"""key_levels.py — ⭐ 箱顶：「前期震荡里被多次打到的阻力位」。判读件，**零授权效力**。

════════════════════════════════════════════════════════════════════════
为什么存在
════════════════════════════════════════════════════════════════════════

  需要足够多的关键价位来看一只标的大致处在哪个区间：底部、底部与关键阻力之间，还是已经突破关键阻力；
  并标出其中极其关键的位置（前期在方向不明的震荡里多次打到的阻力位），用来区分震荡与单边。

════════════════════════════════════════════════════════════════════════
定义 —— 全部复用既有尺子，**零新旋钮**
════════════════════════════════════════════════════════════════════════

  簇        future_gates / bottom_evidence 同一把尺：5 根分形 pivot（SWING_WING）→ 0.35×ATR14 聚类
            （CLUSTER_TOL_ATR14）→ 250 根回看（PIVOT_LOOKBACK）。线 = 簇**上沿**（画 gate 检查表 #2）。
  箱顶      近 `RANGE_BARS`(20) 根最高价之下没有比它更高的簇：簇上沿 + 容差 ≥ 20 根最高价
            （= 抄底框架「顶 = 20 日最高 high」同一个锚，setups.LOW_LOOKBACK）。
  多次打到   簇内近 `TEST_BARS`(60) 根有 ≥ `MIN_TESTS`(2) 个分形**高点**（从下方被测试 ≥2 次；
            60 = setups.LH_LOOKBACK 同窗；2 = bottom_evidence.MIN_TOUCHES「≥2 触碰才算地标」）。
  ⭐箱顶     两者皆有。收盘 ×2 站上它（首日前收 ≤ 上沿）= **⭐突破**。
  ⭐已破     近 `TEST_BARS` 根内最近一次 ⭐突破的那条线（突破后它翻成下方的支撑，跌回 = 假突破的事实）。

  近 60 根只被测过 1 次的簇（另一次触碰在窗外）按本尺**不是** ⭐。

════════════════════════════════════════════════════════════════════════
定价 —— 样本内看着有，**样本外没有**（预注册一次性判定 = REJECT）
════════════════════════════════════════════════════════════════════════

  样本内（多年深度日线池，×2 收盘确认，止损 = 线 − 0.5×ATR20 ×1，持有 60 根）：
    · 所有簇突破 60 根持有 ≈ 随机日：突破本身没有方向 edge（同「gate 是保险不是 alpha」）
    · 触碰次数单独看无信息（无单调）
    · ⭐ vs 其余：止损率略低、策略收益与 R 略好，**持有收益无差** ⇒ 好处只在「失败少、盈亏比好」
    · 被反复测试但**不在箱顶**（箱体中间）的线：持有收益显著更差
    · 突破前个股震荡度无增量；按年不稳
  **样本外**（判据先锁、数字后看）：⭐ 与其余的策略收益 / 止损率 / R 差异的置信区间都跨 0 → **REJECT**。
  样本内的「失败少、R 好」一格都没复现 —— 窗口与「测 ≥2」都是看过样本内之后选的，分叉路径的样子。
  ⇒ ⭐ 是**描述性标注**（「这条线是近期箱顶、被打过几次」这件事本身为真），**不是 edge**：不进排序、不进授权、
  不许拿「⭐ 突破更可靠」去论证任何一笔交易。本模块只做画线与位置判读（看得见区间）。

🚨 零授权：不 import sizing / entry_guard / exit_guard / regime_transition，不读文件，不联网，纯函数。
转移机只扫卡片 `next_regime.entry_gate`；本模块的线响了不授权任何动作。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from . import technical
from .bottom_evidence import CLUSTER_TOL_ATR14, MIN_TOUCHES, PIVOT_LOOKBACK
from .setups import LH_LOOKBACK, LOW_LOOKBACK, NEAR_GATE_ATR, SWING_WING, atr20_at

RANGE_BARS = LOW_LOOKBACK      # 箱顶 = 近 20 根最高价（抄底框架「顶」同锚）
TEST_BARS = LH_LOOKBACK        # 「多次打到」的回看 = 60 日框架同窗；⭐已破 的回看同窗
MIN_TESTS = MIN_TOUCHES        # ≥2 次从下方测试 = 「≥2 触碰才算地标」
_MIN_BARS = 60                 # 与 future_gates 同：簇要有历史才谈得上「反复」（数据充分性守卫）


def _day(bar: dict) -> str:
    return str(bar.get("time") or bar.get("date") or "")[:10]


def _clusters(bars: List[dict], upto: int) -> Optional[Dict]:
    """截至 bars[upto]（含）**已确认**的 pivot 簇 + 当时的 ATR14 / 容差（无前视）。

    分形 pivot 需要右侧 SWING_WING 根确认 ⇒ 只用 bars[upto-PIVOT_LOOKBACK+1 : upto+1] 段内
    swing_points 能确认的点（段尾 wing 根天然不出 pivot）。"""
    lo = max(0, upto - PIVOT_LOOKBACK + 1)
    seg = bars[lo:upto + 1]
    if len(seg) < _MIN_BARS:
        return None
    a = technical.atr(seg, period=14)
    atr14 = a[-1] if a and a[-1] else None
    if not atr14:
        return None
    tol = CLUSTER_TOL_ATR14 * atr14
    sh, sl = technical.swing_points(seg, wing=SWING_WING)
    pivots = ([{"price": p, "date": _day(seg[i]), "kind": "high", "idx": lo + i} for i, p in sh]
              + [{"price": p, "date": _day(seg[i]), "kind": "low", "idx": lo + i} for i, p in sl])
    if not pivots:
        return None
    out = []
    for c in technical.cluster_pivots(pivots, tol):
        # cluster_pivots 按两位小数回写 lo/hi —— 成员按半分宽容差认领 (与四舍五入同量级)
        mem = [p for p in pivots if c["lo"] - 0.006 <= p["price"] <= c["hi"] + 0.006]
        out.append({**c, "members": mem})
    return {"clusters": out, "atr14": atr14, "tol": tol,
            "bucket": technical.auto_bucket(float(bars[upto]["close"]), atr14)}


def _tests(c: Dict, upto: int) -> List[str]:
    """簇内近 TEST_BARS 根（截至 upto）的分形高点日期 = 从下方被测试的次数。"""
    return sorted(p["date"] for p in c["members"] if p["kind"] == "high" and p["idx"] > upto - TEST_BARS)


def _is_key(c: Dict, tol: float, max_high: float, upto: int) -> bool:
    return c["hi"] + tol >= max_high and len(_tests(c, upto)) >= MIN_TESTS


def _view(c: Dict, upto: int) -> Dict:
    tests = _tests(c, upto)
    return {"level": float(c["hi"]), "lo": float(c["lo"]), "touches": int(c["touches"]),
            "kinds": c["kinds"], "tests": len(tests), "test_dates": tests,
            "last_test": tests[-1] if tests else None}


def box_top(bars: List[dict], upto: Optional[int] = None) -> Optional[Dict]:
    """截至 bars[upto]（缺省最后一根）的**待破** ⭐箱顶 → {level, lo, touches, kinds, tests, test_dates,
    last_test, max_high} | None。

    = 收盘之上、上沿 + 容差 ≥ 近 RANGE_BARS 根最高价、近 TEST_BARS 根被测试 ≥MIN_TESTS 次的**最低**那条簇。
    没有 = 近期没有被多次打到的顶（不是箱体 / 或在趋势里一路新高）。"""
    n = len(bars)
    i = (n - 1) if upto is None else upto
    if i < RANGE_BARS or i >= n:
        return None
    cl = _clusters(bars, i)
    if cl is None:
        return None
    close = float(bars[i]["close"])
    max_high = max(float(b["high"]) for b in bars[i - RANGE_BARS + 1:i + 1])
    keys = [c for c in cl["clusters"] if c["hi"] > close and _is_key(c, cl["tol"], max_high, i)]
    if not keys:
        return None
    c = min(keys, key=lambda c: c["hi"])
    return {**_view(c, i), "max_high": max_high, "bucket": cl["bucket"], "atr14": cl["atr14"]}


def key_breakouts(bars: List[dict], lookback: int = TEST_BARS) -> List[Dict]:
    """近 `lookback` 根内的 ⭐突破（时间升序）→ [{level, lo, touches, kinds, tests, test_dates, date, idx}]。

    判据（无前视，逐日重放）：第 i 根收盘 > 上沿 ≥ 第 i−1 根收盘，且第 i+1 根收盘仍 > 上沿（×2）；
    上沿所在簇在 i−1 当天就是 ⭐箱顶（上沿 + 容差 ≥ i 之前 RANGE_BARS 根最高价，且被测试 ≥MIN_TESTS 次）。
    同一根上越过多条 ⭐ 取最高那条。`date` = 确认日（第 i+1 根）。"""
    n = len(bars)
    out: List[Dict] = []
    for i in range(max(RANGE_BARS + 1, n - lookback - 1), n - 1):
        c_prev, c_i, c_next = (float(bars[k]["close"]) for k in (i - 1, i, i + 1))
        if not (c_i > c_prev and c_next > c_prev):
            continue
        cl = _clusters(bars, i - 1)
        if cl is None:
            continue
        max_high = max(float(b["high"]) for b in bars[i - RANGE_BARS:i])
        crossed = [c for c in cl["clusters"]
                   if c_prev <= c["hi"] < c_i and c_next > c["hi"] and _is_key(c, cl["tol"], max_high, i - 1)]
        if not crossed:
            continue
        c = max(crossed, key=lambda c: c["hi"])
        out.append({**_view(c, i - 1), "date": _day(bars[i + 1]), "idx": i + 1,
                    "bucket": cl["bucket"]})
    return out


def last_breakout(bars: List[dict], lookback: int = TEST_BARS) -> Optional[Dict]:
    """近 `lookback` 根内最近一次 ⭐突破 + 现状 → dict | None。

    `intact` = 确认日之后没有一根收盘跌破 线 − 0.5×ATR20@确认日（= 系统的转移地板 bfloor_buf，
    定价里的止损同式）；跌破过 = 这次突破失败了（之后又站回来也是在线附近反复 = 震荡，不是单边）。
    `first_date` = 第一根站上的日子（确认日前一根），用来判「突破之后有没有长出新箱顶」。"""
    evs = key_breakouts(bars, lookback)
    if not evs:
        return None
    e = evs[-1]
    j = e["idx"]
    close = float(bars[-1]["close"])
    n20 = atr20_at(bars, j)
    # 0.5 = bfloor_buf (regime_transition 转移地板同式, 那边同样是内联 0.5×ATR20)
    floor = (e["level"] - 0.5 * n20) if n20 else e["level"]
    intact = all(float(b["close"]) >= floor for b in bars[j + 1:])
    return {**e, "first_date": _day(bars[j - 1]), "floor": round(floor, 2), "intact": intact,
            "held": close > e["level"], "bars_since": len(bars) - 1 - j,
            "gain_pct": round((close / e["level"] - 1.0) * 100.0, 2)}


POSITIONS = {
    "broken": {"cn": "⭐已破箱顶", "desc": "近 60 根 ⭐突破、此后没收破 线−0.5N、上方也还没长出新箱顶 = 单边"},
    "bottom": {"cn": "底部", "desc": "抄底框架 ✓止跌 / ⏳顶后新低 在场（调用方按 setups.bottom_setup 给）"},
    "near_top": {"cn": "箱顶下", "desc": "收盘在 ⭐箱顶之下 ≤1N（setups.NEAR_GATE_ATR，同「即将形成」）"},
    "mid": {"cn": "箱体中段", "desc": "在 ⭐箱顶之下 >1N 且不在底部 = 中间的震荡"},
    "failed": {"cn": "⭐突破失败", "desc": "近 60 根 ⭐突破后收破过 线−0.5N，上方没有新的 ⭐箱顶 = 回到震荡"},
    "none": {"cn": "无箱", "desc": "既没有待破的 ⭐箱顶，近 60 根也没有 ⭐突破"},
}


def position(close: Optional[float], atr20: Optional[float], top: Optional[Dict],
             brk: Optional[Dict], at_bottom: bool = False) -> Dict:
    """标的此刻在箱体结构里的位置（判读）→ {state, cn}。

    ⭐已破 须同时满足：突破完好（`intact`）且收盘仍在线上，且上方的待破 ⭐箱顶不是突破**之后**
    长出来的（≥MIN_TESTS 次测试晚于 `first_date` = 突破后又被压成一个新箱子 → 按新箱子判）。
    其余按 底部 > 箱顶下 > 箱体中段 > ⭐突破失败 > 无箱。"""
    new_box = bool(top and brk and sum(d >= brk.get("first_date", "") for d in top.get("test_dates") or [])
                   >= MIN_TESTS)
    if brk and brk.get("intact") and brk.get("held") and not new_box:
        st = "broken"
    elif at_bottom:
        st = "bottom"
    elif top and close and atr20 and (top["level"] - close) <= NEAR_GATE_ATR * atr20:
        st = "near_top"
    elif top:
        st = "mid"
    elif brk and not brk.get("intact"):
        st = "failed"
    elif brk:
        st = "broken"           # 完好但收盘暂回线下 (线 − 0.5N 之内的回踩)，上方无新箱顶
    else:
        st = "none"
    return {"state": st, "cn": POSITIONS[st]["cn"]}


def evidence_tag(k: Dict) -> str:
    """⭐ 线的收据短语（进 moomoo 备注，不含 '/' —— 那是并入分隔符）：`测3` = 近 60 根被从下方测试 3 次。"""
    return f"测{k['tests']}"
