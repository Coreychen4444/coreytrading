"""vacuum.py — 关键位之间的**真空带**（成交稀薄的价格区间）与**筹码密集区**。判读件，**零授权效力**。

用途：让 operator 看见两个关键位之间哪一段成交稀薄、哪一段成交堆积，上下行都标；
只做展示，不进排序、不进授权。

════════════════════════════════════════════════════════════════════════
定义 —— 全部复用既有尺子，**零新旋钮**
════════════════════════════════════════════════════════════════════════

  窗口      `WINDOW` = 250 根（PIVOT_LOOKBACK）：关键位（pivot 簇 / 远端 gate / ⭐）从哪个窗口来，
            真空就在哪个窗口量。**不用画卡的 60 根 profile** —— 60 根窗口把「近 60 日没到过」的价位
            全报成真空，而几个月前套在那里的库存还在：短窗口报成 LVN 的一段，在长窗口里可能占
            一成以上成交额，是上行路上要先消化的供给，不是真空。
  稀薄      桶成交额 ≤ 25%×POC，或桶缺席（没有任何一根 bar 的区间覆盖过）= `technical.find_lvn`
            同一条判据、同一个缺省值；桶宽 = `technical.auto_bucket`（≈0.25×ATR14）。
            边缘带**不掐**（`keep_edges`）：这里要的恰恰是顶着窗口最高 / 最低价的那条。
  真空带    相邻稀薄桶合并，按窗口最高 / 最低价截断，宽 ≥ `MIN_WIDTH_ATR`(1)×ATR14 才出
            （= setups.NEAR_GATE_ATR「一根 ATR 之内 = 明天就够得到」：窄于一根日均波幅的带一根
            K 线就走完了，谈不上「穿越」）。
  近端边线   上方的带 = 下沿，下方的带 = 上沿 —— 价格先碰到的那条。它被测过几次：
            `technical.rejections_at` / `supports_at`（level_interactions / future_gates 同判据，
            容差一个桶宽）。
  带内地标   落在带内的 pivot 簇上沿（≥2 触碰，future_gates / key_levels 同一把簇尺）。

════════════════════════════════════════════════════════════════════════
读法 —— **真空 ≠ 无阻力**（同 `level_map.lvn_rejection_walls`）
════════════════════════════════════════════════════════════════════════

  量的维度只回答「带内有没有**库存**」，回答不了「带内有没有**卖方**」。真空的成因常常正是
  反复快速拒绝或跳空：几根 K 线快速穿过后跳空离开的一段，成交占比很低，但稀薄只是因为停留短，
  带内的成交至今全部套在里面。所以每条带同时播报：成交占比（≈几个日均量）、近端边线被拒 /
  承接次数、带内地标 —— 三个事实一起读，不许只读「量少」。

  **未定价**：「上方真空 → 走得更快 / 更远」没有回测过。本模块只陈述分布事实，不进排序、
  不进授权、不改 trim / stop 的画法；退出栈（stop / 棘轮 / 趋势死亡线 / G-01）不因「前面是真空」
  放宽一个字。要让它驱动画线，先走预注册回测。

════════════════════════════════════════════════════════════════════════
筹码密集区 —— `dense()`
════════════════════════════════════════════════════════════════════════

  同一份 250 根分布的另一端：成交堆起来的峰。上方的峰 = 套牢盘 / 卖方库存（阻力区），
  下方的峰 = 持有者成本（支撑区）。

  峰        桶成交额是自己 ±1N（`MIN_WIDTH_ATR`，按桶宽折算）之内的最大值，且不是稀薄桶
            （> 25%×POC —— 同一个点不能既是真空又是密集区）。平台段只取第一个。
  区        以峰为中心向两侧延伸，直到 ① 跌破峰高的一半（`DENSE_HALF` = 半高宽 FWHM，信号处理的
            标准宽度定义）② 碰到稀薄桶 ③ 越过与相邻峰之间的谷底（分水岭：相邻两区不重叠）。
  播报      峰价、范围、距离、区内成交占比（≈几个日均量）、近端边线被拒 / 承接次数（同真空带）。

  密集区与卡上 pivot 簇 gate 是同一量级的阻力 / 支撑证据，**不应假定更可靠**；本区只标注不画线、
  不进授权。若要比较两者的拒绝能力，先在私有研究工作区预注册判据。
  换手衰减：传 `float_shares` 时每个区另给 `decay_pct` = 通达信 CYQ 口径的筹码占比 —— 按**股数**
  （筹码 = 股，不是成交额），第 t 天的筹码权重 = r_t × Π_{s>t}(1 − r_s)，r = 当日成交量 ÷ 流通股
  （衰减系数 1，CYQ 缺省；= Grinblatt & Han 2005 参考价的权重）。`half_life` = 往回数第几根买入的
  筹码留到今天不到一半。它的假设是**卖出均匀地来自各个成本位**，两处偏向「高估换手」：处置效应
  （Odean 1998：亏损仓比盈利仓更不愿卖 —— 套牢盘恰恰最不肯割）+ 美股成交量里有大量日内高频来回、
  不是持有人换手。所以真实的剩余筹码在「等权」与「衰减」两个数之间。换手率决定差距有多大：
  低换手的票衰减前后几乎不变，「已经换手」只在高换手的票上成立。
  报表层只播日换手这一个读数；衰减占比与半衰期未经验证不进日报，`chip_decay` 保留给研究复现
  与日换手读数。

🚨 零授权：不 import sizing / entry_guard / exit_guard / regime_transition，不读文件，不联网，纯函数。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from . import technical
from .bottom_evidence import CLUSTER_TOL_ATR14, MIN_TOUCHES, PIVOT_LOOKBACK
from .setups import NEAR_GATE_ATR, SWING_WING

WINDOW = PIVOT_LOOKBACK          # 250 根 = 簇 / 远端 gate / ⭐ 同一个回看
MIN_WIDTH_ATR = NEAR_GATE_ATR    # 带宽 ≥ 1×ATR14（窄于一根日均波幅 = 一根 K 线内走完）; 峰的邻域同此
DENSE_HALF = 0.5                 # 密集区宽度 = 半高宽 (FWHM): 区内每桶 ≥ 峰高 × 0.5
_MIN_BARS = 60                   # 与 future_gates / key_levels 同：数据充分性守卫


def _day(bar: dict) -> str:
    return str(bar.get("time") or bar.get("date") or "")[:10]


def _base(bars: List[dict]) -> Optional[Dict]:
    """两种区共用的底: 窗口 / ATR14 / 桶宽 / 分布 / 窗口极值 / pivot 簇。不足 → None。"""
    seg = bars[-WINDOW:]
    if len(seg) < _MIN_BARS:
        return None
    atr_s = technical.atr(seg, period=14)
    atr14 = atr_s[-1] if atr_s and atr_s[-1] else None
    if not atr14:
        return None
    spot = float(seg[-1]["close"])
    bucket = technical.auto_bucket(spot, atr14)
    prof = technical.volume_profile(seg, bucket)
    total = sum(b["turnover"] for b in prof)
    if total <= 0:
        return None
    highs, lows = technical.swing_points(seg, wing=SWING_WING)
    pivots = ([{"price": p, "date": _day(seg[i]), "kind": "high"} for i, p in highs]
              + [{"price": p, "date": _day(seg[i]), "kind": "low"} for i, p in lows])
    clusters = [c for c in (technical.cluster_pivots(pivots, CLUSTER_TOL_ATR14 * atr14)
                            if pivots else []) if c["touches"] >= MIN_TOUCHES]
    return {"seg": seg, "atr14": atr14, "spot": spot, "bucket": bucket, "prof": prof,
            "total": total, "w_lo": min(float(r["low"]) for r in seg),
            "w_hi": max(float(r["high"]) for r in seg), "clusters": clusters}


def _zone(B: Dict, lo: float, hi: float, grid_lo: float, grid_hi: float) -> Dict:
    """一个价格区间的共同事实 (真空带 / 密集区同一套): 位置 / 宽 / 成交占比 / 近端边线测试。
    `grid_lo/hi` = 未截断的桶网格边界 (取成交额用); lo / hi = 截断到窗口极值后的边界。"""
    seg, spot, atr14, bucket = B["seg"], B["spot"], B["atr14"], B["bucket"]
    share = sum(p["turnover"] for p in B["prof"]
                if p["lo"] >= grid_lo - 1e-6 and p["hi"] <= grid_hi + 1e-6) / B["total"] * 100
    # 严格不等: 只是高点 / 低点贴到边线的 K 线没有在带内成交过 (那是「被拒 / 承接」, 另计)
    through = [r for r in seg if float(r["high"]) > lo and float(r["low"]) < hi]
    side = "above" if lo > spot else ("below" if hi < spot else "inside")
    z = {
        "lo": round(lo, 2), "hi": round(hi, 2),
        "edge": ("window_high" if grid_hi >= B["w_hi"] - 1e-6
                 else "window_low" if grid_lo <= B["w_lo"] + 1e-6 else None),
        "side": side,
        "dist_pct": round({"above": lo / spot - 1, "below": hi / spot - 1,
                           "inside": 0.0}[side] * 100, 1),
        "width_pct": round((hi - lo) / spot * 100, 1),
        "width_n": round((hi - lo) / atr14, 1),
        "share_pct": round(share, 1),
        "days_eq": round(share / 100 * len(seg), 1),
        "bars_through": len(through),
        "last_through": _day(through[-1]) if through else None,
        "inner": [{"level": float(c["hi"]), "touches": int(c["touches"]), "kinds": c["kinds"]}
                  for c in B["clusters"] if lo < c["hi"] < hi],
    }
    if side == "inside":
        z["up_pct"] = round((hi / spot - 1) * 100, 1)
        z["down_pct"] = round((lo / spot - 1) * 100, 1)
        z["tests"], z["last_test"] = None, None
    else:
        hits = (technical.rejections_at(seg, lo, bucket) if side == "above"
                else technical.supports_at(seg, hi, bucket))
        z["tests"], z["last_test"] = len(hits), (hits[-1] if hits else None)
    return z


def _nearest(B: Dict, key: str, out: List[Dict]) -> Dict:
    above = [b for b in out if b["side"] == "above"]
    below = [b for b in out if b["side"] == "below"]
    inside = [b for b in out if b["side"] == "inside"]
    return {"asof": _day(B["seg"][-1]), "spot": B["spot"], "atr14": B["atr14"],
            "bucket": B["bucket"], "window": len(B["seg"]), key: out,
            "up": min(above, key=lambda b: b["lo"]) if above else None,
            "down": max(below, key=lambda b: b["hi"]) if below else None,
            "inside": inside[0] if inside else None}


def scan(bars: List[dict]) -> Optional[Dict]:
    """全部真空带（升序）+ 现价上 / 下最近一条。bars 不足 / ATR 算不出 → None（不编数）。

    返回 {asof, spot, atr14, bucket, window, bands, up, down, inside}；每条带：
      lo / hi          按窗口最高 / 最低价截断后的边界
      edge             'window_high' | 'window_low' | None —— 带顶着窗口极值（再往外没有 tape）
      side             'above' | 'below' | 'inside'（现价在带内）
      dist_pct         现价到近端边线的距离 %（inside = 0）；inside 另给 up_pct / down_pct
      width_pct / width_n
      share_pct        带内占窗口成交额 %；days_eq = 折合几个日均成交额
      bars_through     区间伸进过这条带的 K 线根数（只贴到边线的不算）；last_through = 最近一根的日期
      tests / last_test 近端边线被拒（above）/ 承接（below）次数与最近日期；inside 不计
      inner            带内 pivot 簇上沿 [{level, touches, kinds}]
    """
    B = _base(bars)
    if B is None:
        return None
    out: List[Dict] = []
    for b in technical.find_lvn(B["prof"], B["bucket"], keep_edges=True):
        lo, hi = max(float(b["lo"]), B["w_lo"]), min(float(b["hi"]), B["w_hi"])
        if hi - lo < MIN_WIDTH_ATR * B["atr14"]:
            continue
        out.append(_zone(B, lo, hi, float(b["lo"]), float(b["hi"])))
    return _nearest(B, "bands", out)


def chip_decay(seg: List[dict], float_shares: Optional[float]) -> Optional[Dict]:
    """CYQ 换手衰减权重（见模块 docstring）。流通股缺 / 非正 → None（不编数）。

    返回 {weights, turnover_pct（窗口日均换手 %）, half_life（往回第几根买入的筹码留到今天 < 50%；
    窗口内不到一半 → None）}。"""
    if not float_shares or float_shares <= 0:
        return None
    rates = [min(1.0, max(0.0, float(r.get("volume") or 0.0)) / float_shares) for r in seg]
    weights = [0.0] * len(seg)
    surv, half = 1.0, None
    for i in range(len(seg) - 1, -1, -1):
        weights[i] = rates[i] * surv
        surv *= 1.0 - rates[i]
        if half is None and surv < 0.5:
            half = len(seg) - i
    return {"weights": weights, "turnover_pct": round(sum(rates) / len(rates) * 100, 2),
            "half_life": half}


def dense(bars: List[dict], float_shares: Optional[float] = None) -> Optional[Dict]:
    """全部筹码密集区（升序）+ 现价上 / 下最近一个 + 现价所在的那个。不足 → None（不编数）。

    返回 {asof, spot, atr14, bucket, window, zones, up, down, inside, chips}；每个区 = `scan` 的同一套字段
    （`_zone`）+ peak（峰所在桶的中点）/ peak_pct_poc（峰高 ÷ POC %）/ decay_pct（换手衰减后的筹码占比，
    缺流通股 = None）。`chips` = {turnover_pct, half_life} 或 None。区的**识别**只用等权分布 ——
    衰减只改占比读数，不改区在哪儿。
    """
    B = _base(bars)
    if B is None:
        return None
    cd = chip_decay(B["seg"], float_shares)
    dprof, dtot = None, 0.0
    if cd:
        dprof = technical.volume_profile([dict(r, turnover=w) for r, w in zip(B["seg"], cd["weights"])],
                                         B["bucket"])
        dtot = sum(b["turnover"] for b in dprof)
    prof, bucket = B["prof"], B["bucket"]
    poc = max(b["turnover"] for b in prof)
    thin = poc * technical.LVN_POC_FRAC
    by_lo = {round(b["lo"], 4): b["turnover"] for b in prof}
    first = prof[0]["lo"]
    n = int(round((prof[-1]["lo"] - first) / bucket)) + 1
    grid = [(round(first + i * bucket, 4), by_lo.get(round(first + i * bucket, 4), 0.0))
            for i in range(n)]                       # 缺席的桶 = 0 (完全真空), 网格不断
    k = max(1, round(MIN_WIDTH_ATR * B["atr14"] / bucket))

    peaks: List[int] = []
    for i, (_, t) in enumerate(grid):
        if t <= thin or t < max(x for _, x in grid[max(0, i - k):i + k + 1]):
            continue
        if peaks and i - peaks[-1] <= k:
            continue                                 # 平台段只取第一个
        peaks.append(i)
    if not peaks:
        res = _nearest(B, "zones", [])
        res["chips"] = ({"turnover_pct": cd["turnover_pct"], "half_life": cd["half_life"]} if cd else None)
        return res
    # 分水岭: 相邻两峰之间的谷底 (最先出现的最小值) 归左边, 两个区互不越界
    cuts = [min(range(a + 1, b), key=lambda j: grid[j][1]) if b - a > 1 else a
            for a, b in zip(peaks, peaks[1:])]
    out: List[Dict] = []
    for j, p in enumerate(peaks):
        left = cuts[j - 1] + 1 if j > 0 else 0
        right = cuts[j] if j < len(cuts) else n - 1
        floor = max(grid[p][1] * DENSE_HALF, thin)
        a = b = p
        while a - 1 >= left and grid[a - 1][1] >= floor:
            a -= 1
        while b + 1 <= right and grid[b + 1][1] >= floor:
            b += 1
        g_lo, g_hi = grid[a][0], grid[b][0] + bucket
        lo, hi = max(g_lo, B["w_lo"]), min(g_hi, B["w_hi"])
        z = _zone(B, lo, hi, g_lo, g_hi)
        z["peak"] = round(min(max(grid[p][0] + bucket / 2, lo), hi), 2)
        z["peak_pct_poc"] = round(grid[p][1] / poc * 100)
        z["decay_pct"] = (round(sum(b["turnover"] for b in dprof
                                    if b["lo"] >= g_lo - 1e-6 and b["hi"] <= g_hi + 1e-6) / dtot * 100, 1)
                          if dprof and dtot > 0 else None)
        out.append(z)
    res = _nearest(B, "zones", out)
    res["chips"] = ({"turnover_pct": cd["turnover_pct"], "half_life": cd["half_life"]} if cd else None)
    return res
