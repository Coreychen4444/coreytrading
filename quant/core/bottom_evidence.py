"""bottom_evidence.py — 抄底信号的「底部确认概率」判读件。**零授权效力。**

**底部确认** = ✓止跌 之后 H 根内**先收盘越前高、后才收破止跌低**（道氏语义：该段下跌最后一个 lower high
被收复，而底没有破）。它是一个**概率**，不是多维计分。

════════════════════════════════════════════════════════════════════════
三件事（纯函数；不读文件、不联网；bars / 基准 bars / ATR / σ 由调用方传入）
════════════════════════════════════════════════════════════════════════

  confirm_prob_rw    随机游走基准，**零拟合参数**：无漂移对数布朗运动（日 σ = HV20/√252），
                     H 根内先触前高、后触止跌低的概率；日收盘监控用 Broadie–Glasserman–Kou 修正
                     （两条线各向外移 0.5826σ，定义常数）。离底越近 → 越低；离前高越近 → 越高。
                     「收上止跌日高」「超卖」之类事实的原始效应，绝大部分就是这条几何。
  anchor_confluence  止跌低贴着 L **之前已存在**的地标的维数 0–3：pivot 簇（≥2 触碰）/
                     成交分布（top-5 HVN 带或价值区下沿 VAL）/ 均线（MA50 或 MA200）。
                     容差 = max(0.25×ATR20, profile 桶宽)（既有价位交互口径）。
  bench_low_index    基准（QQQ / SMH）逐日「是否创 20 日新低」；止跌低那天基准也新低 = 系统性下跌，
                     否 = 个股独立的底（macro-context-check：宏观驱动的反转形态打折）。

════════════════════════════════════════════════════════════════════════
使用边界
════════════════════════════════════════════════════════════════════════

  P_rw 零参数，判读层显示它。锚收敛 / 基准新低只作定价件（回测复现用），判读层不显示：
  它们的原始差异大多可由「离前高更近」的几何解释；重新启用须先预注册并验证增量。

🚨 判读层零授权效力：本模块不 import sizing / entry_guard / exit_guard；entry_guard 只把读数印进
🔁 PLAN 块，verdict / cap / 阶梯 / 止损与本模块无关（`tests/decision/test_entry_guard.py` 钉）。
**不是排序键**：离止损越近概率越低、赔率越高，两者是同一件几何（按它排 = 系统性挑赔率差的笔）。
回测与判读应 import 同一份函数（判读口径 = 定价口径）。
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional

from . import technical
from .setups import LOW_LOOKBACK, SWING_WING

CONFIRM_HORIZON = LOW_LOOKBACK   # 20 根 = 抄底框架同窗（setups.LOW_LOOKBACK）
ANCHOR_TOL_ATR = 0.25            # 锚容差 max(0.25×ATR20, 桶宽) —— interaction_census 同口径
CLUSTER_TOL_ATR14 = 0.35         # pivot 聚类容差 = level_map.build_map 同值
PIVOT_LOOKBACK = 250             # pivot 回看 = interaction_census 同值
PROFILE_BARS = 60                # 成交分布窗 = level_map profile_days 同值
HVN_TOP_N = 5                    # top HVN 带数 = level_map hvn_n 同值
MIN_TOUCHES = 2                  # 簇 ≥2 触碰才算地标
BGK_BETA = 0.5825971579390106    # −ζ(1/2)/√(2π)：离散监控修正（Broadie–Glasserman–Kou 1997，定义常数）
_EPS = 1e-15                     # 级数截断（数值精度，不是旋钮）


def _day(bar: dict) -> str:
    return str(bar.get("time") or bar.get("date") or "")[:10]


def confirm_prob_rw(close, low, lh, sigma_d, horizon=CONFIRM_HORIZON) -> Optional[float]:
    """随机游走基准：H 根内**先收盘 > 前高 lh、后才收盘 < 止跌低 low** 的概率（无漂移对数布朗运动）。

    → [0, 1] | None（输入缺 / 前高不在止跌低之上 / σ 非正）。已收在前高之上 = 1.0；已收破止跌低 = 0.0。
    两个级数表示同一个量，按收敛速度选：大时间用 Fourier（absorbing 区间特征函数展开），
    小时间用镜像（reflection principle）；二者在切换点一致（测试钉），并对日收盘监控的蒙特卡洛核过。"""
    try:
        c, lo, hi, s, h = float(close), float(low), float(lh), float(sigma_d), float(horizon)
    except (TypeError, ValueError):
        return None
    if not (c > 0 and lo > 0 and hi > lo and s > 0 and h > 0):
        return None
    if c > hi:
        return 1.0
    if c < lo:
        return 0.0
    a = math.log(lo) - BGK_BETA * s
    b = math.log(hi) + BGK_BETA * s
    x = math.log(c)
    w, d = b - a, x - a
    var = s * s * h
    lam = math.pi ** 2 * var / (2.0 * w * w)
    p = _p_fourier(d, w, lam) if lam >= 0.25 else _p_image(d, w, var)
    return min(1.0, max(0.0, p))


def _p_fourier(d: float, w: float, lam: float) -> float:
    """区间 (0, w) 吸收布朗运动从 d 出发、时间内先出上界的概率 —— 特征函数展开（大时间收敛快）。
    P = d/w − Σ_{n≥1} (2/(nπ)) (−1)^{n+1} sin(nπd/w) · exp(−λ n²)，λ = π²σ²T/(2w²)。"""
    acc, n = 0.0, 1
    while True:
        e = math.exp(-lam * n * n)
        if e < _EPS:
            break
        acc += (2.0 / (n * math.pi)) * (1.0 if n % 2 else -1.0) * math.sin(n * math.pi * d / w) * e
        n += 1
    return d / w - acc


def _p_image(d: float, w: float, var: float) -> float:
    """同一个量的镜像级数（小时间收敛快）：u = w − d 为距上界，r = √(2σ²T)。
    P = Σ_{k≥0} [erfc((u + 2kw)/r) − erfc((u + 2d + 2kw)/r)]。"""
    u, r = w - d, math.sqrt(2.0 * var)
    p, k = 0.0, 0
    while True:
        t1 = math.erfc((u + 2.0 * k * w) / r)
        p += t1 - math.erfc((u + 2.0 * d + 2.0 * k * w) / r)
        if t1 < _EPS:
            break
        k += 1
    return p


def daily_sigma(bars: List[dict], i: Optional[int] = None) -> Optional[float]:
    """第 i 根（缺省最后一根）的日 σ = HV20 / √252（technical.hv 同定义：对数收益样本标准差）。"""
    if not bars:
        return None
    series = technical.hv(bars, 20)
    j = (len(bars) - 1) if i is None else i
    v = series[j] if 0 <= j < len(series) else None
    return (v / math.sqrt(252.0)) if v else None


def _mean_close(bars: List[dict], i: int, n: int) -> Optional[float]:
    if i + 1 < n:
        return None
    return sum(float(b["close"]) for b in bars[i - n + 1:i + 1]) / n


def anchor_confluence(bars: List[dict], i_low: Optional[int], atr20: Optional[float],
                      atr14: Optional[float]) -> Optional[Dict]:
    """止跌低 `bars[i_low].low` 贴着几类**之前已存在**的地标 → {dims 0–3, pivot, profile, ma, tol, …} | None。

    全部只用 `bars[:i_low]`（均线含止跌日收盘，与定价同口径）—— 不偷看止跌低之后。"""
    if i_low is None or not (0 <= i_low < len(bars)) or not atr20 or not atr14:
        return None
    L = float(bars[i_low]["low"])
    bucket = technical.auto_bucket(L, atr14)
    tol = max(ANCHOR_TOL_ATR * float(atr20), bucket)
    seg = bars[max(0, i_low - PIVOT_LOOKBACK):i_low]
    sh, sl = technical.swing_points(seg, wing=SWING_WING)
    pivots = ([{"price": p, "date": _day(seg[j]), "kind": "high"} for j, p in sh]
              + [{"price": p, "date": _day(seg[j]), "kind": "low"} for j, p in sl])
    clusters = technical.cluster_pivots(pivots, CLUSTER_TOL_ATR14 * float(atr14)) if pivots else []
    piv = [c for c in clusters if c["touches"] >= MIN_TOUCHES and c["lo"] - tol <= L <= c["hi"] + tol]
    pb = [dict(r, turnover=(float(r.get("turnover") or 0.0) or float(r["close"]) * float(r.get("volume") or 0)))
          for r in bars[max(0, i_low - PROFILE_BARS):i_low]]
    prof = technical.volume_profile(pb, bucket) if pb else []
    hvn = technical.top_hvn(prof, n=HVN_TOP_N) if prof else []
    va = technical.value_area(prof) if prof else None
    hvn_hit = any(bd["lo"] - tol <= L <= bd["hi"] + tol for bd in hvn)
    val_hit = va is not None and abs(L - float(va["val"])) <= tol
    ma50, ma200 = _mean_close(bars, i_low, 50), _mean_close(bars, i_low, 200)
    ma_hits = [nm for nm, v in (("MA50", ma50), ("MA200", ma200)) if v is not None and abs(L - v) <= tol]
    dims = int(bool(piv)) + int(hvn_hit or val_hit) + int(bool(ma_hits))
    tags = ((["簇"] if piv else []) + (["量"] if (hvn_hit or val_hit) else []) + (["均"] if ma_hits else []))
    return {"low": L, "tol": tol, "bucket": bucket, "dims": dims,
            "pivot": bool(piv), "profile": bool(hvn_hit or val_hit), "ma": bool(ma_hits),
            "pivot_touches": (max(c["touches"] for c in piv) if piv else 0),
            "pivot_band": ([min(c["lo"] for c in piv), max(c["hi"] for c in piv)] if piv else None),
            "hvn": hvn_hit, "val": (float(va["val"]) if val_hit else None), "ma_names": ma_hits,
            "tags": tags, "cn": f"锚{dims}/3" + (f"（{'·'.join(tags)}）" if tags else "")}


def bench_low_index(bench_bars: List[dict], lookback: int = LOW_LOOKBACK) -> Dict[str, bool]:
    """基准逐日「是否创 lookback 日新低」（low ≤ 前 lookback−1 根最低 low）→ {date: bool}；暖机不足的日不出。"""
    lows = [float(b.get("low") or 0.0) for b in bench_bars]
    out: Dict[str, bool] = {}
    for i, b in enumerate(bench_bars):
        prev = lows[max(0, i - lookback + 1):i]
        if len(prev) < lookback - 1:
            continue
        out[_day(b)] = lows[i] <= min(prev)
    return out


def bench_new_low(index: Optional[Dict[str, bool]], day: Optional[str]) -> Optional[bool]:
    """止跌低那天基准是否同创 20 日新低 → True（系统性）/ False（个股独立）/ None（基准缺该日）。"""
    if not index or not day:
        return None
    return index.get(str(day)[:10])
