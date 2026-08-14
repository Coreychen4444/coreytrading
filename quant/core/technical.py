#!/usr/bin/env python3
"""Technical indicator calculations and bottom signal detection.

Pure computation — no API calls. Takes kline data (list of dicts from
market_data.get_kline) and returns indicators/signals.

Designed for bottom-confirmation-signals.md workflow:
  1. Fetch kline via market_data.get_kline()
  2. Feed into this module for RSI/MACD/Bollinger/volume analysis
  3. Run detect_bottom_signals() for signal scoring

Usage:
    from quant.data.market_data import get_kline
    from quant.core.technical import analyze, detect_bottom_signals

    klines = get_kline("NVDA", ktype="1d", num=120)
    result = analyze(klines)
    signals = detect_bottom_signals(klines)

CLI:
    .venv/moomoo/bin/python3 quant/core/technical.py NVDA [--num 120] [--ktype 1d] [--json]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 直接 `python3 quant/<层>/<模块>.py` 时让 `quant.*` 可 import

import json
import math
from typing import Dict, List, Optional, Tuple


# ── Helper ─────────────────────────────────────────────────────

def _extract(klines: List[dict], key: str) -> List[float]:
    """Extract a numeric series from kline records."""
    return [float(r.get(key, 0)) for r in klines]


def resample_weekly(klines: List[dict], drop_partial: bool = True) -> List[dict]:
    """日线 → 周线（自然周，ISO 周一起算）。**精确重采样，不是新数据源。**

    为什么要有它（`technical_engine_landing_plan.md` §5 阶段 1）：跨**时间尺度**组合
    是三种「结合指标」形态里**唯一有顶刊证据**的一种 —— Han, Zhou & Zhu (*JFE* 2016)
    把短/中/长期均线信息合成一个 trend factor，夏普比是三个单趋势因子的两倍以上；
    而同一尺度上换算法（RSI vs 布林%B vs MA 偏离）实测 ρ≈0.9 = 同一个读数四个名字。

    `drop_partial=True` 丢掉尾部**未走完的那一周**—— 与「未完成 bar 不是收盘」
    同一条纪律（`heartbeat.md` §盘中运行）。周三读一根「本周」K 线，
    和盘中读当日 bar 是同一种错误，只是慢了五倍。
    """
    if not klines:
        return []
    import datetime as _dt

    buckets: Dict[str, List[dict]] = {}
    order: List[str] = []
    for r in klines:
        t = str(r.get("time", ""))[:10]
        if len(t) < 10:
            continue
        try:
            d = _dt.date(int(t[:4]), int(t[5:7]), int(t[8:10]))
        except ValueError:
            continue
        monday = (d - _dt.timedelta(days=d.weekday())).isoformat()
        if monday not in buckets:
            buckets[monday] = []
            order.append(monday)
        buckets[monday].append(r)

    out: List[dict] = []
    for k in order:
        rows = buckets[k]
        out.append({
            "time": f"{k} 00:00:00",
            "open": float(rows[0].get("open", 0)),
            "high": max(float(r.get("high", 0)) for r in rows),
            "low": min(float(r.get("low", 0)) for r in rows),
            "close": float(rows[-1].get("close", 0)),
            "volume": sum(float(r.get("volume") or 0) for r in rows),
            "turnover": sum(float(r.get("turnover") or 0) for r in rows),
            "_days": len(rows),
        })
    if drop_partial and out and out[-1]["_days"] < 5:
        out = out[:-1]
    return out


# ── Moving Average ─────────────────────────────────────────────

def sma(values: List[float], period: int) -> List[Optional[float]]:
    """Simple Moving Average. Returns list same length as input, None for insufficient data."""
    result: List[Optional[float]] = [None] * len(values)
    if len(values) < period:
        return result
    window_sum = sum(values[:period])
    result[period - 1] = window_sum / period
    for i in range(period, len(values)):
        window_sum += values[i] - values[i - period]
        result[i] = window_sum / period
    return result


def ema(values: List[float], period: int) -> List[Optional[float]]:
    """Exponential Moving Average."""
    result: List[Optional[float]] = [None] * len(values)
    if len(values) < period:
        return result
    # Seed with SMA
    seed = sum(values[:period]) / period
    result[period - 1] = seed
    mult = 2.0 / (period + 1)
    for i in range(period, len(values)):
        result[i] = (values[i] - result[i - 1]) * mult + result[i - 1]
    return result


# ── RSI ────────────────────────────────────────────────────────

def rsi(closes: List[float], period: int = 14) -> List[Optional[float]]:
    """Relative Strength Index using Wilder's smoothing.

    Returns list same length as closes, None where insufficient data.
    """
    result: List[Optional[float]] = [None] * len(closes)
    if len(closes) < period + 1:
        return result

    gains = []
    losses = []
    for i in range(1, len(closes)):
        delta = closes[i] - closes[i - 1]
        gains.append(max(delta, 0))
        losses.append(max(-delta, 0))

    # First average
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    if avg_loss == 0:
        result[period] = 100.0
    else:
        rs = avg_gain / avg_loss
        result[period] = 100 - (100 / (1 + rs))

    # Subsequent values (Wilder's smoothing)
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            result[i + 1] = 100.0
        else:
            rs = avg_gain / avg_loss
            result[i + 1] = 100 - (100 / (1 + rs))

    return result


# ── MACD ───────────────────────────────────────────────────────

def macd(
    closes: List[float],
    fast: int = 12,
    slow: int = 26,
    signal_period: int = 9,
) -> Dict[str, List[Optional[float]]]:
    """MACD (Moving Average Convergence Divergence).

    Returns:
        {"dif": [...], "dea": [...], "histogram": [...]}
        DIF = EMA(fast) - EMA(slow)
        DEA = EMA(signal) of DIF
        Histogram = (DIF - DEA) * 2
    """
    ema_fast = ema(closes, fast)
    ema_slow = ema(closes, slow)
    n = len(closes)

    dif: List[Optional[float]] = [None] * n
    for i in range(n):
        if ema_fast[i] is not None and ema_slow[i] is not None:
            dif[i] = ema_fast[i] - ema_slow[i]

    # DEA = EMA of DIF values (skip Nones)
    dif_start = next((i for i in range(n) if dif[i] is not None), n)
    dif_values = [dif[i] for i in range(dif_start, n)]
    dea_raw = ema(dif_values, signal_period)

    dea: List[Optional[float]] = [None] * n
    for j, val in enumerate(dea_raw):
        dea[dif_start + j] = val

    histogram: List[Optional[float]] = [None] * n
    for i in range(n):
        if dif[i] is not None and dea[i] is not None:
            histogram[i] = (dif[i] - dea[i]) * 2

    return {"dif": dif, "dea": dea, "histogram": histogram}


# ── Bollinger Bands ────────────────────────────────────────────

def bollinger(
    closes: List[float],
    period: int = 20,
    std_mult: float = 2.0,
) -> Dict[str, List[Optional[float]]]:
    """Bollinger Bands.

    Returns:
        {"upper": [...], "middle": [...], "lower": [...], "bandwidth": [...]}
    """
    n = len(closes)
    middle = sma(closes, period)
    upper: List[Optional[float]] = [None] * n
    lower: List[Optional[float]] = [None] * n
    bandwidth: List[Optional[float]] = [None] * n

    for i in range(period - 1, n):
        if middle[i] is None:
            continue
        window = closes[i - period + 1: i + 1]
        mean = middle[i]
        variance = sum((x - mean) **2 for x in window) / period
        std = math.sqrt(variance)
        upper[i] = mean + std_mult * std
        lower[i] = mean - std_mult * std
        bandwidth[i] = (upper[i] - lower[i]) / mean * 100 if mean else None

    return {"upper": upper, "middle": middle, "lower": lower, "bandwidth": bandwidth}


# ── Volume Analysis ────────────────────────────────────────────

def volume_ratio(volumes: List[int], period: int = 20) -> List[Optional[float]]:
    """Volume relative to N-day average. >1.5 = 放量, <0.6 = 缩量."""
    avg = sma([float(v) for v in volumes], period)
    result: List[Optional[float]] = [None] * len(volumes)
    for i in range(len(volumes)):
        if avg[i] and avg[i] > 0:
            result[i] = volumes[i] / avg[i]
    return result


def is_shrink_volume(vol_ratio: Optional[float], threshold: float = 0.6) -> bool:
    """缩量判定: volume < 20日均量的60%."""
    return vol_ratio is not None and vol_ratio < threshold


def is_heavy_volume(vol_ratio: Optional[float], threshold: float = 1.5) -> bool:
    """放量判定: volume > 20日均量的150%."""
    return vol_ratio is not None and vol_ratio > threshold


# ── MA Deviation ───────────────────────────────────────────────

def true_range(klines: List[dict]) -> List[float]:
    """TR_i = max(H-L, |H-prevC|, |L-prevC|) — 后两项抓跳空
    (隔夜 gap 是持有人的真实波动, 只看日内振幅会低估; 例: NOK 2026-06-05
    日内振幅 1.67 但 |L-prevC| = 2.62)。首根无 prevC, TR = H-L。"""
    out: List[float] = []
    prev_close: Optional[float] = None
    for r in klines:
        h, l = float(r.get("high", 0)), float(r.get("low", 0))
        if prev_close is None:
            out.append(h - l)
        else:
            out.append(max(h - l, abs(h - prev_close), abs(l - prev_close)))
        prev_close = float(r.get("close", 0))
    return out


def atr(klines: List[dict], period: int = 20) -> List[Optional[float]]:
    # 口径分工 (定, canonical: trend-exit-system.md §2b):
    #   ATR20 = **算钱**— stop 距离 / 吊灯 trail / N-unit sizing / band 体检 / 回测
    #   ATR14 = **读盘**— level_map 桶宽与容差 / regime_score / zone-touch A·B 判别
    # sizing 用的 N 必须和 stop 距离用的 N 是同一个数, 否则 0.75%NAV 的风险预算
    # 本身就是错的。 MU 卡手算 2N=758 (ATR14 83.6) 而 entry_guard
    # sizing 用 ATR20 80.07 → 2N=765, 两个"2N"不是同一个数。
    # **调用点一律显式传 period, 不要吃这个默认值。**
    """Average True Range, Wilder RMA 平滑。趋势死亡线 (chandelier exit,
    trend-exit-system.md) 的波动尺: trail = highest_close - k×ATR。"""
    trs = true_range(klines)
    out: List[Optional[float]] = [None] * len(trs)
    if len(trs) < period:
        return out
    first = sum(trs[:period]) / period
    out[period - 1] = first
    prev = first
    for i in range(period, len(trs)):
        prev = (prev * (period - 1) + trs[i]) / period
        out[i] = prev
    return out


def range_contraction(klines: List[dict], window: int = 10, period: int = 14) -> Optional[float]:
    """波动收缩比 = ATR(近 window 根) / ATR(前 window 根)。<1 = 收缩，>1 = 扩张。

    这是「把形态名降解成可测量」的执行（landing plan §5 阶段 4）：
    VCP / squeeze / 三角收敛 / 三周收紧 的**共同可观测核心**就是波动在收缩 ——
    与其实现四个没有正式检验的形态名，不如实现它们共有的那一个读数。
    （NR4/NR7/BB Width/Keltner 与它同轴同源 = 一票，不另外实现。）

    ⚠️ 它是**轴 C（前置状态）**的读数，不是触发器：收缩本身不给方向，
    方向由突破给（Lo 族的 TTOP/TBOT 恰好是"收敛"的两种解决方式，
    而它们是 NYSE 全族最弱的两个 p=0.393 / 0.185 —— 收缩容易看见，方向不容易）。
    """
    need = window * 2 + period
    if len(klines) < need:
        return None
    recent = atr(klines[-(window + period):], period)[-1]
    prior = atr(klines[-(window * 2 + period):-window], period)[-1]
    if not recent or not prior or prior <= 0:
        return None
    return round(recent / prior, 3)


def auto_bucket(spot: float, atr14: float | None) -> float:
    """Profile bucket width: ~ATR/4, floored at 0.4% of spot and 0.1."""
    b = (atr14 / 4) if atr14 and atr14 > 0 else spot * 0.02
    b = max(b, spot * 0.004, 0.1)
    return float(round(b, 1)) if b < 10 else float(round(b))


EDGE_WEIGHT = 0.05          # bar 极值处的相对权重下限 (价格在极值停留最短)


def volume_profile(bars: List[dict], bucket: float, spread: bool = True) -> List[dict]:
    """Turnover-weighted volume-at-price, ascending price bands.

    **每根 bar 的成交额按它真实走过的 [low, high] 铺开**, 权重三角形、峰在
    typical price ((H+L+C)/3), 两端降到 EDGE_WEIGHT —— 承认价格在区间极值停留
    最短、在 TP 附近成交最密。

     由"点质量"改成"铺开"(用户: gate 精准依赖这些数据, 需要认真梳理):
    旧法把**整根日线的全部成交额**记进 TP 落入的**那一个**桶。日内 range 常达
    10%+ (AAOI 8/6 是 118.88-134.29), 于是一整天的分布被压成一个点, 相邻价位
    凭空变成 0.0%。实测代价 (10 票 × 60 日, 以同窗口 5m 分布为真值, 判据 = 总变差 TVD):

        A 日线 TP 点质量 (旧)   TVD 21.5%   POC 对 7/10
        B 日线均匀铺开          TVD  7.3%   POC 对 7/10
        C 日线三角铺开 (本实现)  TVD  7.2%   POC 对 **10/10**

    单票最坏 AAOI 38.2% → 13.0%: 60 日里 **30/63 个桶**被旧法报成"完全真空 0.0%"
    而实际有成交 (最高 3.7%), POC 也报错一档 (旧法 175-177.5 占 11.4%, 实际那格
    只有 3.5%)。**LVN/HVN/gate 全部长在这个分布上**, 所以这不是显示问题。
    高波动票改善最大 (AAOI/NBIS/MRVL/INTC 都 -20pp 以上), 正是画 gate 最吃精度那批。

    `spread=False` 保留旧口径, 仅供回归对照 —— 不要用它做决策。
    喂**分钟线**时铺开近似退化为恒等 (5m range 通常 < 1 bucket), 所以同一函数
    对两种粒度都正确: 有分钟数据就准, 没有就比旧法好 3 倍。
    """
    prof: dict[float, float] = {}

    def add(lo: float, v: float) -> None:
        prof[lo] = prof.get(lo, 0.0) + v

    for r in bars:
        to = float(r.get("turnover") or 0.0)
        hi, lo_p = float(r["high"]), float(r["low"])
        tp = (hi + lo_p + float(r["close"])) / 3
        if not spread or hi <= lo_p:
            add(round(math.floor(tp / bucket) * bucket, 4), to)
            continue
        half = (hi - lo_p) / 2 or 1e-9
        cells: list[tuple[float, float]] = []
        for i in range(int(math.floor(lo_p / bucket)), int(math.floor(hi / bucket)) + 1):
            c_lo, c_hi = i * bucket, (i + 1) * bucket
            overlap = min(hi, c_hi) - max(lo_p, c_lo)
            if overlap <= 0:
                continue
            mid = (max(lo_p, c_lo) + min(hi, c_hi)) / 2
            w = overlap * max(EDGE_WEIGHT, 1.0 - abs(mid - tp) / half)
            cells.append((round(i * bucket, 4), w))
        tw = sum(w for _, w in cells) or 1.0
        for k, w in cells:
            add(k, to * w / tw)
    total = sum(prof.values()) or 1.0
    return [
        {"lo": lo, "hi": round(lo + bucket, 4), "turnover": v,
         "share_pct": round(v / total * 100, 1)}
        for lo, v in sorted(prof.items())
    ]


VALUE_AREA_FRAC = 0.70   # 拍卖理论的**定义常数**（Steidlmayer/Market Profile），不是我们调的参数


def value_area(profile: List[dict], frac: float = VALUE_AREA_FRAC) -> Optional[Dict]:
    """价值区 —— 从 POC 向两侧扩张，直到累计成交额占比达 `frac`（≈70%）。

    → `{"poc_lo","poc_hi","val","vah","covered_pct","n_buckets"}`；profile 空 → None。

    **为什么要它**（`technical_engine_landing_plan.md` §5 阶段 5）：我们已有 POC / HVN / LVN，
    但缺拍卖理论的**坐标系**—— 「区内 = 平衡（均值回复语义）/ 区外 = 失衡（趋势语义）」。
    一根 bar 的含义取决于它相对价值区的位置，这是位置轴（轴 A）的底座，不是一个信号。

    **扩张规则**（Market Profile 标准做法）：每步比较 POC 上方与下方**相邻**桶，
    取成交额大的那侧并入；一侧到顶则只能往另一侧走。

    ⚠️ **加法，不改 `volume_profile` 的返回形状**—— 它有 5 个消费者
    （level_map 的 gate 画线 / chart_read / 三个回测），改形状会一次打到全部。
    ⚠️ 70% 是**定义**不是拍的：改它 = 离开拍卖理论的词汇表，那时它就是一个自由度了。
    ⚠️ `[原典]`：Market Profile 的**统计检验我没有找到**（找到的都是教学材料）——
    采用它的**坐标**，不采用它的胜率。
    """
    if not profile:
        return None
    tot = sum(b["turnover"] for b in profile)
    if tot <= 0:
        return None
    k = max(range(len(profile)), key=lambda i: profile[i]["turnover"])
    lo = hi = k
    acc = profile[k]["turnover"]
    target = tot * frac
    while acc < target and (lo > 0 or hi < len(profile) - 1):
        up = profile[hi + 1]["turnover"] if hi < len(profile) - 1 else -1.0
        dn = profile[lo - 1]["turnover"] if lo > 0 else -1.0
        if up >= dn:
            hi += 1
            acc += profile[hi]["turnover"]
        else:
            lo -= 1
            acc += profile[lo]["turnover"]
    return {
        "poc_lo": profile[k]["lo"], "poc_hi": profile[k]["hi"],
        "val": profile[lo]["lo"], "vah": profile[hi]["hi"],
        "covered_pct": round(acc / tot * 100, 1), "n_buckets": hi - lo + 1,
    }


def swing_points(klines: List[dict], wing: int = 2) -> Tuple[List[Tuple[int, float]], List[Tuple[int, float]]]:
    """5-bar fractal pivots (wing=2): swing high = 高点两侧各 wing 根都更低。
    返回 ([(idx, swing_high)], [(idx, swing_low)])。

    ⚠️ **已知方法论债（2026-08-12 登记，登记不等于要改）**：
    Lo, Mamaysky & Wang (*JF* 2000) **明确反对**在原始价上直接取极值 —— 原话是那样
    「识别出太多极值，而且产出的形态与技术分析师认为可信的形态不一致」；他们的工序是
    **先核回归平滑、在平滑序列上找一阶导变号，再回原始价取真实高低点**。
    本函数的 5-bar 分形正是他反对的那一类。

    **判读层已经绕开它**：`quant/core/patterns.py` 自己走核回归那条路（它不 import 本函数），
    所以「更贴 Lo 定义」的收益已经落袋在需要它的那一层。

    🚨 **换实现的爆炸半径是全仓最大的一档之一**（消费链 2026-08-12 核实）：
        本函数 → swing_structure → trend → 结构修复 VETO 判别式（**改判别式 = 改授权**）
                                         → core/structure.structure_repair_veto
                                         → exit_guard 结构标签 / gate_ladder_report / gate_audit
               → level_map.cluster_pivots（**pivot 簇 = gate 画在哪条线上**）
               → regime_transition 破位后自动生成的 z1/z2/stop
               → 私人研究必须 import 本函数，不能复制 pivot 语义
    修改前必须先锁定私人研究协议、运行相应验证并通过 production tests。
    注: 最近 wing 根 K 线内的新极值尚不能确认为 pivot (需右侧 wing 根), 属固有滞后。"""
    highs = [float(r.get("high", 0)) for r in klines]
    lows = [float(r.get("low", 0)) for r in klines]
    sh: List[Tuple[int, float]] = []
    sl: List[Tuple[int, float]] = []
    for i in range(wing, len(klines) - wing):
        win_h = highs[i - wing:i + wing + 1]
        win_l = lows[i - wing:i + wing + 1]
        if highs[i] == max(win_h) and win_h.count(highs[i]) == 1:
            sh.append((i, highs[i]))
        if lows[i] == min(win_l) and win_l.count(lows[i]) == 1:
            sl.append((i, lows[i]))
    return sh, sl


def swing_structure(klines: List[dict], wing: int = 2) -> Dict:
    """道氏结构标签: 最近两个 swing high → HH/LH, 最近两个 swing low → HL/LL。
    trend: HH+HL=up / LH+LL=down / 混合=transition。
    trend-exit-system 操作手册的机械结构判定 (LH = 趋势病危通知, LL = 确认)。
    注: 最近 wing 根 K 线内的新极值尚不能确认为 pivot (需右侧 wing 根), 属固有滞后。"""
    sh, sl = swing_points(klines, wing)
    closes = [float(r.get("close", 0)) for r in klines]
    out: Dict = {
        "high_label": None, "low_label": None, "trend": "insufficient",
        "last_swing_high": sh[-1][1] if sh else None,
        "prev_swing_high": sh[-2][1] if len(sh) >= 2 else None,
        "last_swing_low": sl[-1][1] if sl else None,
        "prev_swing_low": sl[-2][1] if len(sl) >= 2 else None,
        "last_close": closes[-1] if closes else None,
        "close_below_last_swing_low": None,
    }
    if len(sh) >= 2:
        out["high_label"] = "HH" if sh[-1][1] > sh[-2][1] else "LH"
    if len(sl) >= 2:
        out["low_label"] = "HL" if sl[-1][1] > sl[-2][1] else "LL"
    if out["last_swing_low"] is not None and out["last_close"] is not None:
        out["close_below_last_swing_low"] = out["last_close"] < out["last_swing_low"]
    if out["high_label"] and out["low_label"]:
        out["trend"] = {"HHHL": "up", "LHLL": "down"}.get(
            out["high_label"] + out["low_label"], "transition")
    return out


def ma_deviation(closes: List[float], period: int = 20) -> List[Optional[float]]:
    """Price deviation from MA as percentage. Negative = below MA."""
    ma = sma(closes, period)
    result: List[Optional[float]] = [None] * len(closes)
    for i in range(len(closes)):
        if ma[i] and ma[i] > 0:
            result[i] = (closes[i] - ma[i]) / ma[i] * 100
    return result


# ── Trend observation layer (立项 trend_slots_design, [FYI] 观察层) ──

def anchored_vwap(klines: List[dict], start_idx: int) -> List[Optional[float]]:
    """AVWAP 锚定成交均价线: 从 start_idx 起累计 Σturnover/Σvolume。

    turnover/volume 就是该 bar 的真实成交均价, 不需要 typical-price 近似;
    bar 缺 turnover 时回落 typical×volume——不能塌成 0, 否则锚线被静默拉偏
    (与 level_map.cached_bars 的 turnover 兜底同一理由)。
    → 与 klines 等长, start_idx 之前为 None。
    """
    n = len(klines)
    out: List[Optional[float]] = [None] * n
    if not 0 <= start_idx < n:
        return out
    cum_to = 0.0
    cum_vol = 0.0
    for i in range(start_idx, n):
        r = klines[i]
        vol = float(r.get("volume") or 0)
        to = float(r.get("turnover") or 0)
        if to <= 0 and vol > 0:
            tp = (float(r.get("high", 0)) + float(r.get("low", 0))
                  + float(r.get("close", 0))) / 3
            to = tp * vol
        cum_to += to
        cum_vol += vol
        out[i] = cum_to / cum_vol if cum_vol > 0 else None
    return out


def rs_series(closes: List[float], bench_closes: List[float]) -> List[Optional[float]]:
    """RS 相对强度线 = close / bench_close, 首个双方有效点归一为 1.0。

    输入须已按日期对齐——对齐是调用方的事, 两条序列日期错位会让 RS 全线失真
    (RelVR 的价格兄弟; 基准映射同一张冻结表 core/sectors)。
    """
    n = min(len(closes), len(bench_closes))
    out: List[Optional[float]] = [None] * n
    base: Optional[float] = None
    for i in range(n):
        c, b = closes[i], bench_closes[i]
        if c and b and b > 0:
            ratio = c / b
            if base is None:
                base = ratio
            out[i] = ratio / base
    return out


# ── K-Line Pattern Detection ──────────────────────────────────

# ── ⚠️ 以下三个单 bar / 少数 bar 形状：证据等级 `[实证-负面为主]`（补注）──
#
# 它们是本仓**唯三没有出处**的技术判别式（其余判别式都能追到一篇论文或一次自家回测）。
# 公开文献的主要无条件检验：
#   Marshall, Young & Rose, *J. Banking & Finance* 30(9) 2006 —— DJIA 成分 1992-2002,
#     扩展 bootstrap（**随机生成 OHLC 四价**作零分布）⇒ K 线策略对 DJIA 股票**没有价值**
#   Marshall, Young & Cahan (2008) —— 东证前 100, 1975-2004 ⇒ **无正的异常收益**
#   Horton (2009) 美股选股 ⇒ 负面
#   Caginalp & Laurent (1998) S&P500 三日反转形态 ⇒ **正面**（唯一一篇像样的正面证据）
#   Bulkowski（长上影线一类的先验频率）⇒ 59% ≈「near random」, rank 55/103,
#     作者自陈「这根 K 线看起来比它的表现好」
#
# **诚实读法**：这些检验测的是**无条件**信号（全样本每次出现都算一次）。它们**不能**否定
# 「在一条有证据的线上 + 相对放量 + 明确前置趋势」这种**条件化**版本 —— 但它们把先验压得很低，
# 举证责任在提案方。
#
# 现在的位置（核实的完整消费链）：
#   detect_* → detect_bottom_signals().patterns → regime_score.q_c_exhaustion → synthesize
#   而 **`regime_score` 不在 `pipeline.py`、也不被任何定时任务调用**—— 它只在
#   `.claude/skills/trading-analysis` 与 `trading-discipline.md`「加仓前先跑 Q-A→Q-D」
#   的**人工路径**上。⇒ 影响面 = 一个手工建议工具里的**共振计分里的一票**，不是自动授权。
#
# 🚫 **不要单独删改它们**：Q-C **从未被单独回测过**，改计票就是改它的行为。
#    要动就整体回测 Q-C（`price_action_framework_design` §13.6 给了三条具体建议）。
def detect_hammer(kline: dict) -> bool:
    """锤子线: 下影线 > 实体 * 2, 上影线短。

    `[实证-负面为主]` —— 出处与消费链见上方区块注释。**不作为独立触发器。**
    阈值 2.0 / 0.5 无出处，是本仓自定义（与 Bulkowski 的形状定义也不一致）。
    """
    o, h, l, c = kline['open'], kline['high'], kline['low'], kline['close']
    body = abs(c - o)
    if body == 0:
        body = 0.001
    lower_shadow = min(o, c) - l
    upper_shadow = h - max(o, c)
    return lower_shadow > body * 2 and upper_shadow < body * 0.5


def detect_engulfing_bullish(prev: dict, curr: dict) -> bool:
    """看涨吞没: 前阴后阳, 阳线完全吞没阴线。

    `[实证-负面为主]` —— 出处与消费链见 `detect_hammer` 上方区块注释。**不作为独立触发器。**
    """
    if prev['close'] >= prev['open']:  # prev not bearish
        return False
    if curr['close'] <= curr['open']:  # curr not bullish
        return False
    return curr['open'] <= prev['close'] and curr['close'] >= prev['open']


def detect_morning_star(k1: dict, k2: dict, k3: dict) -> bool:
    """早晨之星: 阴线 + 小实体/十字星 + 阳线。

    `[实证-负面为主]`，但**这一族是唯一有正面证据的**：Caginalp & Laurent (1998) 测的正是
    **三日反转形态**（S&P500，1990s 中期），结论是多数三日反转形态产生可观利润。
    出处与消费链见 `detect_hammer` 上方区块注释。**仍不作为独立触发器。**
    30% 实体比阈值无出处，本仓自定义。
    """
    # k1: bearish
    if k1['close'] >= k1['open']:
        return False
    # k2: small body (< 30% of k1 body)
    body1 = abs(k1['close'] - k1['open'])
    body2 = abs(k2['close'] - k2['open'])
    if body1 == 0 or body2 > body1 * 0.3:
        return False
    # k3: bullish, closes above k1 midpoint
    if k3['close'] <= k3['open']:
        return False
    midpoint = (k1['open'] + k1['close']) / 2
    return k3['close'] > midpoint


# ── Divergence Detection ──────────────────────────────────────

def _find_local_lows(values: List[Optional[float]], window: int = 5) -> List[int]:
    """Find indices of local minimums."""
    lows = []
    for i in range(window, len(values) - window):
        if values[i] is None:
            continue
        is_low = True
        for j in range(i - window, i + window + 1):
            if j == i or values[j] is None:
                continue
            if values[j] <= values[i]:
                is_low = False
                break
        if is_low:
            lows.append(i)
    return lows


def detect_rsi_divergence(
    closes: List[float],
    rsi_values: List[Optional[float]],
    lookback: int = 30,
) -> Optional[dict]:
    """Detect RSI bullish divergence (price lower low, RSI higher low).

    Returns:
        {"type": "bullish_divergence", "price_low1": ..., "price_low2": ...,
         "rsi_low1": ..., "rsi_low2": ..., "idx1": ..., "idx2": ...}
        or None.
    """
    n = len(closes)
    start = max(0, n - lookback)
    price_lows = _find_local_lows(closes[start:], window=3)
    rsi_lows = _find_local_lows(
        [rsi_values[start + i] for i in range(n - start)], window=3,
)

    # Need at least 2 price lows and 2 RSI lows
    if len(price_lows) < 2 or len(rsi_lows) < 2:
        return None

    # Check last two price lows
    i1, i2 = price_lows[-2], price_lows[-1]
    abs_i1, abs_i2 = start + i1, start + i2

    if closes[abs_i2] >= closes[abs_i1]:  # Price not making lower low
        return None

    r1 = rsi_values[abs_i1]
    r2 = rsi_values[abs_i2]
    if r1 is None or r2 is None:
        return None
    if r2 <= r1:  # RSI also making lower low — no divergence
        return None

    return {
        "type": "bullish_divergence",
        "price_low1": closes[abs_i1],
        "price_low2": closes[abs_i2],
        "rsi_low1": r1,
        "rsi_low2": r2,
        "idx1": abs_i1,
        "idx2": abs_i2,
    }


def detect_macd_divergence(
    closes: List[float],
    macd_hist: List[Optional[float]],
    lookback: int = 30,
) -> Optional[dict]:
    """Detect MACD histogram bullish divergence."""
    n = len(closes)
    start = max(0, n - lookback)
    price_lows = _find_local_lows(closes[start:], window=3)

    if len(price_lows) < 2:
        return None

    i1, i2 = price_lows[-2], price_lows[-1]
    abs_i1, abs_i2 = start + i1, start + i2

    if closes[abs_i2] >= closes[abs_i1]:
        return None

    h1 = macd_hist[abs_i1]
    h2 = macd_hist[abs_i2]
    if h1 is None or h2 is None:
        return None
    # Histogram less negative (higher) on second low = divergence
    if h2 <= h1:
        return None

    return {
        "type": "bullish_divergence",
        "price_low1": closes[abs_i1],
        "price_low2": closes[abs_i2],
        "hist_low1": h1,
        "hist_low2": h2,
        "idx1": abs_i1,
        "idx2": abs_i2,
    }


# ── Full Analysis ─────────────────────────────────────────────

def analyze(klines: List[dict], rsi_period: int = 14) -> Dict:
    """Run all technical indicators on kline data.

    Args:
        klines: List of dicts from market_data.get_kline()

    Returns:
        Dict with all indicator values for the latest bar, plus full series.
    """
    if not klines:
        return {"error": "no data"}

    closes = _extract(klines, "close")
    volumes = [int(r.get("volume", 0)) for r in klines]
    n = len(klines)
    last = n - 1

    # Indicators
    rsi_vals = rsi(closes, rsi_period)
    macd_vals = macd(closes)
    boll_vals = bollinger(closes)
    vol_ratio = volume_ratio(volumes)
    ma60_dev = ma_deviation(closes, 60)
    ma5 = sma(closes, 5)
    ma10 = sma(closes, 10)
    ma20 = sma(closes, 20)
    ma60 = sma(closes, 60)
    ma250 = sma(closes, 250)

    # K-line patterns (latest bars)
    patterns = []
    if n >= 1 and detect_hammer(klines[last]):
        patterns.append("hammer")
    if n >= 2 and detect_engulfing_bullish(klines[last - 1], klines[last]):
        patterns.append("bullish_engulfing")
    if n >= 3 and detect_morning_star(klines[last - 2], klines[last - 1], klines[last]):
        patterns.append("morning_star")

    # Divergences
    rsi_div = detect_rsi_divergence(closes, rsi_vals)
    macd_div = detect_macd_divergence(closes, macd_vals["histogram"])

    return {
        "latest": {
            "time": klines[last].get("time", ""),
            "close": closes[last],
            "volume": volumes[last],
            "rsi": rsi_vals[last],
            "macd_dif": macd_vals["dif"][last],
            "macd_dea": macd_vals["dea"][last],
            "macd_hist": macd_vals["histogram"][last],
            "boll_upper": boll_vals["upper"][last],
            "boll_middle": boll_vals["middle"][last],
            "boll_lower": boll_vals["lower"][last],
            "boll_bandwidth": boll_vals["bandwidth"][last],
            "volume_ratio": vol_ratio[last],
            "ma5": ma5[last],
            "ma10": ma10[last],
            "ma20": ma20[last],
            "ma60": ma60[last],
            "ma250": ma250[last],
            "ma60_deviation": ma60_dev[last],
        },
        "patterns": patterns,
        "rsi_divergence": rsi_div,
        "macd_divergence": macd_div,
        "series": {
            "rsi": rsi_vals,
            "macd": macd_vals,
            "bollinger": boll_vals,
            "volume_ratio": vol_ratio,
            "ma60_deviation": ma60_dev,
        },
    }


# ── Bottom Signal Detection ──────────────────────────────────

def detect_bottom_signals(klines: List[dict], lookback: int = 5) -> Dict:
    """Evaluate bottom confirmation signals per bottom-confirmation-signals.md.

    Checks:
      1. Volume: shrink + heavy stop
      2. RSI/MACD divergence
      3. K-line patterns
      4. Bollinger lower band touch
      5. MA deviation extreme
      6. Veto conditions

    Returns:
        {"signals": [...], "vetoes": [...], "score": "A"/"B"/"C",
         "recommendation": str, "details": {...}}
    """
    if len(klines) < 30:
        return {"error": "need at least 30 bars", "signals": [], "vetoes": [],
                "score": "C", "recommendation": "数据不足，无法判断"}

    analysis = analyze(klines)
    if "error" in analysis:
        return {"error": analysis["error"], "signals": [], "vetoes": [],
                "score": "C", "recommendation": "分析失败"}

    closes = _extract(klines, "close")
    volumes = [int(r.get("volume", 0)) for r in klines]
    vol_ratios = analysis["series"]["volume_ratio"]
    latest = analysis["latest"]
    n = len(klines)
    last = n - 1

    signals: List[str] = []
    vetoes: List[str] = []
    details: Dict = {}

    # ── Signal 1: Volume (缩量下跌 → 放量止跌) ──────────────
    # Check if recent bars show volume shrinkage followed by heavy volume stop
    recent_vols = vol_ratios[max(0, last - lookback):last + 1]
    recent_closes = closes[max(0, last - lookback):last + 1]

    had_shrink = any(v is not None and v < 0.6 for v in recent_vols[:-1])
    latest_heavy = is_heavy_volume(vol_ratios[last])
    latest_shrink = is_shrink_volume(vol_ratios[last])
    # Stop = close above low (not making new low on heavy volume)
    latest_held = len(recent_closes) >= 2 and recent_closes[-1] >= min(recent_closes[:-1])

    if had_shrink and latest_heavy and latest_held:
        signals.append("缩量下跌后放量止跌（量价最强信号）")
        details["volume"] = "shrink_then_heavy_stop"
    elif had_shrink:
        signals.append("缩量下跌（卖压衰竭，但未见承接）")
        details["volume"] = "shrink_only"
    elif latest_shrink:
        details["volume"] = "still_shrinking"

    # Volume veto: heavy volume breaking down
    if latest_heavy and len(recent_closes) >= 2 and recent_closes[-1] < min(recent_closes[:-1]):
        vetoes.append("放量破位（恐慌性下跌，非承接）")
        details["volume_veto"] = True

    # ── Signal 2: RSI ────────────────────────────────────────
    rsi_val = latest["rsi"]
    if rsi_val is not None:
        details["rsi"] = round(rsi_val, 2)
        if rsi_val < 30:
            details["rsi_oversold"] = True

    if analysis.get("rsi_divergence"):
        signals.append(f"RSI 底背离（价格新低但 RSI 未新低）")
        details["rsi_divergence"] = analysis["rsi_divergence"]

    # ── Signal 3: MACD ───────────────────────────────────────
    macd_hist = latest["macd_hist"]
    if macd_hist is not None:
        details["macd_hist"] = round(macd_hist, 4)
        # Histogram shortening (green bars getting shorter)
        hist_series = analysis["series"]["macd"]["histogram"]
        recent_hist = [h for h in hist_series[max(0, last - lookback):last + 1] if h is not None]
        if len(recent_hist) >= 3 and all(h < 0 for h in recent_hist):
            if recent_hist[-1] > recent_hist[-2]:
                signals.append("MACD 柱缩短（空头动能减弱）")
                details["macd_hist_shortening"] = True

    if analysis.get("macd_divergence"):
        signals.append("MACD 底背离")
        details["macd_divergence"] = analysis["macd_divergence"]

    # ── Signal 4: Bollinger lower band ───────────────────────
    boll_lower = latest["boll_lower"]
    close = latest["close"]
    if boll_lower is not None and close is not None:
        if close <= boll_lower:
            signals.append("触及布林带下轨")
            details["boll_touch_lower"] = True
        # Check if bounced back inside
        if len(klines) >= 2:
            prev_close = closes[last - 1]
            prev_lower = analysis["series"]["bollinger"]["lower"][last - 1]
            if prev_lower and prev_close <= prev_lower and close > boll_lower:
                signals.append("布林带下轨反包（触及后收回带内）")
                details["boll_bounce"] = True

    # ── Signal 5: MA deviation extreme (60MA = 决策线) ────────
    dev = latest["ma60_deviation"]
    if dev is not None:
        details["ma60_deviation"] = round(dev, 2)
        if dev < -15:
            signals.append(f"严重偏离60日均线（{dev:.1f}%），均值回归动力强")
            details["ma_extreme_deviation"] = True

    # ── Signal 5b: Near 60MA support ─────────────────────────
    ma60_val = latest.get("ma60")
    if ma60_val and close and ma60_val > 0:
        gap_pct = (close - ma60_val) / ma60_val * 100
        details["ma60_gap_pct"] = round(gap_pct, 2)
        if -3 <= gap_pct <= 3:
            signals.append(f"回踩60日决策线附近（偏离{gap_pct:.1f}%）")
            details["near_ma60"] = True

    # ── Signal 6: K-line patterns ────────────────────────────
    if analysis["patterns"]:
        for p in analysis["patterns"]:
            label = {
                "hammer": "锤子线",
                "bullish_engulfing": "看涨吞没",
                "morning_star": "早晨之星",
            }.get(p, p)
            signals.append(f"K线形态: {label}")
        details["kline_patterns"] = analysis["patterns"]

    # ── Signal 7: Long lower shadow ──────────────────────────
    last_k = klines[last]
    body = abs(last_k["close"] - last_k["open"])
    lower_shadow = min(last_k["open"], last_k["close"]) - last_k["low"]
    if body > 0 and lower_shadow > body * 2:
        if "hammer" not in analysis["patterns"]:
            signals.append("长下影线（低位有买盘承接）")
            details["long_lower_shadow"] = True

    # ── Veto: Rebound failure (假底) ─────────────────────────
    if len(closes) >= 10:
        recent_low = min(closes[last - 9:last])
        if closes[last] < recent_low:
            vetoes.append("跌破近期低点（假底风险）")

    # ── Veto: Dead cat bounce characteristics ────────────────
    if len(closes) >= 10 and len(vol_ratios) >= 10:
        # Check if recent bounce was on declining volume near MA resistance
        recent_high = max(closes[last - 4:last + 1])
        recent_range_low = min(closes[max(0, last - 9):last - 4]) if last >= 5 else None
        ma10_val = latest.get("ma10")

        if recent_range_low and recent_high > recent_range_low:
            bounce_pct = (recent_high - recent_range_low) / recent_range_low * 100
            # Fibonacci 38.2% of prior drop
            prior_drop_high = max(closes[max(0, last - 20):max(0, last - 9)])
            prior_drop = prior_drop_high - recent_range_low if prior_drop_high > recent_range_low else 0
            fib_382 = recent_range_low + prior_drop * 0.382 if prior_drop > 0 else None

            if (fib_382 and recent_high < fib_382 and
                    ma10_val and recent_high <= ma10_val * 1.01 and
                    closes[last] < recent_high):
                bounce_vol = vol_ratios[last - 2:last + 1] if last >= 2 else []
                if any(v is not None and v < 0.8 for v in bounce_vol):
                    vetoes.append("Dead cat bounce 特征（反弹缩量+未过38.2%回撤+均线压制）")

    # ── Scoring ──────────────────────────────────────────────
    signal_count = len(signals)
    veto_count = len(vetoes)

    if veto_count > 0:
        score = "C"
        recommendation = "存在否决信号，不操作"
    elif signal_count >= 3:
        score = "A"
        recommendation = "强确认（3+信号共振），确认仓可加"
    elif signal_count >= 2:
        score = "B"
        recommendation = "初步确认（2信号），仅价格仓"
    else:
        score = "C"
        recommendation = "信号不足，仅观察"

    return {
        "signals": signals,
        "vetoes": vetoes,
        "score": score,
        "signal_count": signal_count,
        "recommendation": recommendation,
        "latest_indicators": {
            "close": latest["close"],
            "rsi": latest["rsi"],
            "macd_hist": latest["macd_hist"],
            "volume_ratio": latest["volume_ratio"],
            "ma60": latest.get("ma60"),
            "ma60_deviation": latest.get("ma60_deviation"),
            "ma250": latest.get("ma250"),
            "boll_lower": latest["boll_lower"],
        },
        "details": details,
    }


# ── CLI Entry Point ──────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Technical analysis & bottom signal detection")
    parser.add_argument("ticker", help="Stock ticker, e.g. NVDA")
    parser.add_argument("--num", type=int, default=120, help="Number of K-line bars (default: 120)")
    parser.add_argument("--ktype", default="1d", help="K-line type (default: 1d)")
    parser.add_argument("--start", default=None, help="History start date YYYY-MM-DD")
    parser.add_argument("--end", default=None, help="History end date YYYY-MM-DD")
    parser.add_argument("--json", action="store_true", dest="output_json", help="JSON output")
    parser.add_argument("--signals-only", action="store_true", help="Only show bottom signals")
    args = parser.parse_args()

    # Import market_data for fetching
    from quant.data.market_data import get_kline
    klines = get_kline(args.ticker, ktype=args.ktype, num=args.num,
                       start=args.start, end=args.end)

    if not klines:
        print(f"Failed to fetch kline data for {args.ticker}", file=sys.stderr)
        sys.exit(1)

    if args.signals_only:
        result = detect_bottom_signals(klines)
    else:
        analysis = analyze(klines)
        bottom = detect_bottom_signals(klines)
        result = {
            "ticker": args.ticker.upper(),
            "ktype": args.ktype,
            "bars": len(klines),
            "analysis": {
                "latest": analysis["latest"],
                "patterns": analysis["patterns"],
                "rsi_divergence": analysis.get("rsi_divergence"),
                "macd_divergence": analysis.get("macd_divergence"),
            },
            "bottom_signals": bottom,
        }

    if args.output_json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        ticker = args.ticker.upper()
        if args.signals_only:
            _print_signals(ticker, result)
        else:
            _print_full(ticker, result)


def _print_signals(ticker: str, result: Dict):
    score = result["score"]
    score_label = {"A": "强确认", "B": "初步确认", "C": "信号不足/有否决"}.get(score, score)
    print(f"{'=' * 60}")
    print(f"  {ticker} 见底信号检测  评级: {score} ({score_label})")
    print(f"{'=' * 60}")

    ind = result.get("latest_indicators", {})
    print(f"  RSI: {ind.get('rsi', '?'):.1f}" if ind.get('rsi') else "  RSI: N/A", end="")
    print(f"  |  MACD柱: {ind.get('macd_hist', '?'):.4f}" if ind.get('macd_hist') else "  |  MACD柱: N/A", end="")
    print(f"  |  量比: {ind.get('volume_ratio', '?'):.2f}" if ind.get('volume_ratio') else "  |  量比: N/A", end="")
    print(f"  |  60MA偏离: {ind.get('ma60_deviation', '?'):.1f}%" if ind.get('ma60_deviation') else "  |  60MA偏离: N/A")
    print()

    if result["signals"]:
        print("  确认信号:")
        for s in result["signals"]:
            print(f"    + {s}")
    else:
        print("  确认信号: 无")

    if result["vetoes"]:
        print("  否决信号:")
        for v in result["vetoes"]:
            print(f"    ! {v}")

    print(f"\n  建议: {result['recommendation']}")
    print(f"{'=' * 60}")


def _print_full(ticker: str, result: Dict):
    a = result["analysis"]["latest"]
    print(f"{'=' * 60}")
    print(f"  {ticker} 技术分析  ({result['ktype']}, {result['bars']} bars)")
    print(f"{'=' * 60}")
    print(f"  收盘: {a['close']:.2f}  MA20: {a.get('ma20', 'N/A')}" if a.get('ma20') else f"  收盘: {a['close']:.2f}")
    ma60_str = f"{a['ma60']:.2f}" if a.get('ma60') else "N/A"
    ma250_str = f"{a['ma250']:.2f}" if a.get('ma250') else "N/A"
    print(f"  60MA(决策线): {ma60_str}  250MA(结构线): {ma250_str}")
    print(f"  RSI({14}): {a['rsi']:.2f}" if a.get('rsi') else "  RSI: N/A")
    print(f"  MACD DIF: {a.get('macd_dif', 'N/A')}  DEA: {a.get('macd_dea', 'N/A')}  柱: {a.get('macd_hist', 'N/A')}")
    print(f"  布林: {a.get('boll_upper', 'N/A')} / {a.get('boll_middle', 'N/A')} / {a.get('boll_lower', 'N/A')}")
    print(f"  量比: {a.get('volume_ratio', 'N/A')}")
    print(f"  60MA偏离: {a['ma60_deviation']:.2f}%" if a.get('ma60_deviation') else "  60MA偏离: N/A")

    if result["analysis"]["patterns"]:
        print(f"  K线形态: {', '.join(result['analysis']['patterns'])}")
    if result["analysis"].get("rsi_divergence"):
        print(f"  RSI背离: 是")
    if result["analysis"].get("macd_divergence"):
        print(f"  MACD背离: 是")

    print()
    _print_signals(ticker, result["bottom_signals"])


if __name__ == "__main__":
    main()
