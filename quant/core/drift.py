"""drift.py — 结构快照的**漂移度量**: 卡片描述的结构, 还是市场在交易的结构吗?

为什么存在（用户问题："如果仅靠日期去更新是不是不够合理"）：
    zone/gate 此前的 staleness 只有两类判据 ——
      · 时间   valid_until / last_reviewed > 14d
      · 价格   连续 2 日破 trim 上沿 或 z2 下沿
    时间是**上界不是判据**: 它保证 staleness 有天花板, 但完全不知道结构变没变。
    价格穿越只看边界: 股票可以在区间内把结构换掉, 也可以穿出去又回来。
    两者都回答不了那个真问题。

四个维度, 全部 ATR 归一 (唯一 scale-free 的写法):

  ① price_drift_n     |close − anchor_close| / ATR20@锚      走了多远
  ③ out_of_range_frac 锚定以来收盘落在 [z2_lo, trim_hi] 外的比例
  ④ gate_reach_n      (最近上行 gate − close) / ATR20        锚还够不够得着
  ⑤ vol_ratio         ATR20_now / ATR20@锚                   zone **宽度**还对吗

⚠️ **②POC 维已摘除**（编号保留空缺，避免旧日志字段错位）。
它曾被称作"核心维", 但 `poc_window` round-2 用**解耦标签**(pivot 锚的 zone 组,
与 volume profile 零共用) 实测: poc20 AUC **0.5975**、poc60 **0.5524**, 而
「距锚天数」0.6158、①price_drift **0.6992**—— **两个窗口都打不赢任何一个基线**,
①本身就比它好得多。②不是独立维度, 是①的一个更差的影子。
round-1 那个 0.812 是自变量与因变量共用同一份 60 根 profile 的产物, 已证伪。
④是"300→100 之后 gate 234 变成永久禁入"的度量。

⚠️ **本模块只观测, 不裁决。**
   THRESHOLDS 是**未校准的**保守初值, 只用来生成 advisory 清单 (进 brief 的
   "待重画"待办), **不参与任何交易判定**。分档 (fresh/advisory/stale) 的权重与
   阈值要靠 analytics 校准后才接 hard STALE —— 校准方法见 zone-maintenance.md:
   用 zone_timeline 挖历史卡片 + level_map 机械重画, 以"重画会不会改变
   entry_guard 的决策"作 label。
   先只说话不动手，避免未经校准的判别式直接影响交易。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from .num import coerce_float  # noqa: E402
from .structure import anchor_date, gates_of  # noqa: E402

# 未校准的保守初值。任一超阈 → advisory, **不拦交易**。
THRESHOLDS = {
    "price_drift_n": 2.0,
    "out_of_range_frac": 0.40,
    "gate_reach_n": 4.0,
    "vol_ratio_lo": 0.60,
    "vol_ratio_hi": 1.60,
    "min_sessions": 3,        # 锚定后不足这么多根 K, 漂移无意义
}


def structure_drift(agent: Optional[dict], rows: List[dict],
                    atr_series: List[Optional[float]]) -> Optional[Dict]:
    """四维漂移 (②POC 已摘除, 见模块 docstring)。None = 无法判定 (无锚 / K 线不足) —— 沉默好过编一个数。"""
    agent = agent or {}
    anc = anchor_date(agent)
    if not anc or not rows:
        return None
    # anchored_on 缺失时退到 thesis.last_reviewed —— 但那是"谁碰过这张卡", 不是
    # "结构哪天画的"。实测 (LITE/NVDA): 只是加了个 --vehicle 字段,
    # last_reviewed 就跳到今天, 漂移时钟归零。所以这里必须把代理身份报出来。
    explicit = bool(((agent.get("zones") or {}).get("anchored_on") or {}).get("date"))
    base = {"anchor_date": anc,
            "anchor_source": "anchored_on" if explicit else "last_reviewed_proxy"}
    dates = [str(r.get("time", ""))[:10] for r in rows]
    ia = next((i for i, d in reversed(list(enumerate(dates))) if d <= anc), None)
    if ia is None or ia >= len(rows) - 1:
        # 锚定日 ≥ 最后一根已完成收盘 → 还没有漂移可言。**报出来, 不静默丢弃**。
        return {**base, "sessions_since": 0, "insufficient": True,
                "note": f"锚定于 {anc}, 最后收盘 {dates[-1] if dates else '?'} — 尚无漂移窗口"}
    since = rows[ia:]
    if len(since) - 1 < THRESHOLDS["min_sessions"]:
        return {**base, "sessions_since": len(since) - 1, "insufficient": True,
                "note": f"锚后仅 {len(since) - 1} 根 K, 不足 {THRESHOLDS['min_sessions']} 根"}

    atr_a = atr_series[ia] if ia < len(atr_series) else None
    atr_n = atr_series[-1] if atr_series else None
    c_a = coerce_float(rows[ia].get("close"))
    c_n = coerce_float(rows[-1].get("close"))
    if not (atr_a and atr_n and c_a and c_n):
        return None

    zones = agent.get("zones") or {}
    z2 = zones.get("accumulation_zone_2") or zones.get("accumulation_zone_1")
    trim = zones.get("trim_zone")
    lo = coerce_float(z2[0]) if isinstance(z2, list) and z2 else None
    hi = coerce_float(trim[1]) if isinstance(trim, list) and len(trim) == 2 else None

    closes = [coerce_float(r.get("close")) for r in since]
    closes = [c for c in closes if c]
    outside = None
    if lo is not None and hi is not None and closes:
        outside = sum(1 for c in closes if not (lo <= c <= hi)) / len(closes)

    up = [g for g in gates_of(agent) if g["kind"] in ("up", "reclaim")
          and isinstance(g.get("level"), (int, float)) and g["level"] > c_n]
    gate = min((g["level"] for g in up), default=None)

    out = {
        **base, "anchor_close": round(c_a, 2), "close": round(c_n, 2),
        "sessions_since": len(since) - 1, "insufficient": False,
        "price_drift_n": round(abs(c_n - c_a) / atr_a, 2),
        "out_of_range_frac": round(outside, 3) if outside is not None else None,
        "range": [lo, hi] if (lo is not None and hi is not None) else None,
        "nearest_up_gate": gate,
        "gate_reach_n": round((gate - c_n) / atr_n, 2) if gate else None,
        "atr_anchor": round(atr_a, 2), "atr_now": round(atr_n, 2),
        "vol_ratio": round(atr_n / atr_a, 2),
    }
    out["breaches"] = breaches(out)
    out["advisory"] = bool(out["breaches"])
    return out


def breaches(d: Dict) -> List[str]:
    """哪几维超了未校准初值。**advisory 用, 不裁决交易。**"""
    T, out = THRESHOLDS, []
    if d.get("insufficient"):
        return out
    v = d.get("price_drift_n")
    if v is not None and v > T["price_drift_n"]:
        out.append(f"价格离锚 {v:g}N > {T['price_drift_n']:g}N")
    v = d.get("out_of_range_frac")
    if v is not None and v > T["out_of_range_frac"]:
        out.append(f"区间外收盘 {v * 100:.0f}% > {T['out_of_range_frac'] * 100:.0f}% "
                   "— 卡在描述一个股票已经离开的区间")
    v = d.get("gate_reach_n")
    if v is not None and v > T["gate_reach_n"]:
        out.append(f"最近上行 gate {d.get('nearest_up_gate'):g} 距现价 {v:g}N > "
                   f"{T['gate_reach_n']:g}N — 够不着的锚不是闸门是禁入")
    v = d.get("vol_ratio")
    if v is not None and not (T["vol_ratio_lo"] <= v <= T["vol_ratio_hi"]):
        out.append(f"波动率 {v:g}× (ATR {d.get('atr_anchor')}→{d.get('atr_now')}) "
                   "— zone 宽度按旧波动率画的, 位置对宽度也可能错")
    return out


def drift_line(ticker: str, d: Optional[Dict]) -> Optional[str]:
    """一行播报。None = 不值得占版面。"""
    if not d or d.get("insufficient") or not d.get("advisory"):
        return None
    return (f"📐 {ticker} 结构漂移 (锚 {d['anchor_date']}, {d['sessions_since']}d): "
            + "; ".join(d["breaches"])
            + " → 建议下次 /trading-analysis 重画 (advisory, 未拦交易)")
