#!/usr/bin/env python3
"""level_map.py — 多指标共振价位图（strike-triangulation 信号 2+3+4 自动化）

为什么存在: zone 重锚 / entry ladder / override 条件 / strike 选择都需要回答
"这个价位是不是真结构位"。此前靠手搓（源案例  AAOI: 验证 128 为四次
拒绝带、"128→160" 中间还有 141/152 两道供给墙）。本脚本固化四信号中的三个:

  信号2 volume  — 成交额加权 volume profile（HVN 墙 / 低量带）。每根 bar 的成交额按其
                  真实区间三角铺开（峰在 typical price）, 不是压成一个点 —— 见
                  core/technical.volume_profile 与 research/volume_profile_accuracy.md
  信号2b LVN    — 低量带/真空带（立项, FVG 评估的产物: FVG 是无 volume
                  数据时用蜡烛几何猜 LVN 的代理, 我们有真分布直接找。缺失桶 = 完全
                  真空【没有任何一根 bar 的区间覆盖过】, ≤25%×POC = 稀薄; 掐两端边缘带。
                  用法: zone 边界别画在 LVN 上; 回踩预期滑过 LVN 停在停靠 HVN）
  信号3 OI      — 期权链 per-strike OI 墙（--oi, 消耗行情配额, 默认关）
  信号4 K线结构 — swing pivot 聚类（多次触碰 = 强位）+ MA20/50 + ATR 噪音标定

信号1 (zone) 从 positions/watchlist 的 agent.zones 读出一并打印, 四信号同屏对照。
指定 --levels 时输出每个价位的交互史（拒绝次数/收盘上方次数/当前连续站上天数）,
用于"站上 X"类 override 条件的证据检查。

数据: OpenD 日线（authoritative）; --cached 用 market/klines/ 缓存（离线, 可能缺最新 bar）。
纯函数（profile/cluster/interaction）无 OpenD 依赖, 可直接 pytest。

CLI:
    .venv/moomoo/bin/python3 quant/decision/level_map.py AAOI
    .venv/moomoo/bin/python3 quant/decision/level_map.py AAOI --days 90 --levels 128 130 160
    .venv/moomoo/bin/python3 quant/decision/level_map.py AAOI --oi --json
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 直接 `python3 quant/<层>/<模块>.py` 时让 `quant.*` 可 import

import argparse
import json
import math
from datetime import date, timedelta

from quant.core import technical  # noqa: E402  (pure math, tested in tests/core/test_technical.py)
from quant.core.io_json import iter_jsonl, read_json  # noqa: E402
from quant.core.timeutil import et_today  # noqa: E402
from quant.core.paths import (# noqa: E402
    KLINE_CACHE_DIR, POSITIONS_PATH, WATCHLIST_PATH, add_openapi_path,
)


# ── Pure core (testable without OpenD) ─────────────────────────

# 桶宽/profile 的数学都住 core/technical —— drift.py 也要用同一份
# (: drift 各抄过一份 profile 与一份 bucket 公式, 且 bucket 那份
#  没有 rounding, 于是同一只票在两处算出的桶宽不一样)
auto_bucket = technical.auto_bucket
# volume profile 的数学住 core/technical (drift.py 也要用同一份 ——
# 发现 drift._poc 抄了一份点质量版本, 没跟上三角铺开修正)
make_profile = technical.volume_profile
EDGE_WEIGHT = technical.EDGE_WEIGHT


def top_hvn(profile: list[dict], n: int = 5) -> list[dict]:
    """Top-n buckets merged into contiguous HVN bands, strongest first."""
    if not profile:
        return []
    top = sorted(sorted(profile, key=lambda x: -x["turnover"])[:n], key=lambda x: x["lo"])
    bands: list[dict] = []
    for b in top:
        if bands and abs(b["lo"] - bands[-1]["hi"]) < 1e-6:
            bands[-1]["hi"] = b["hi"]
            bands[-1]["turnover"] += b["turnover"]
            bands[-1]["share_pct"] = round(bands[-1]["share_pct"] + b["share_pct"], 1)
        else:
            bands.append(dict(b))
    return sorted(bands, key=lambda x: -x["turnover"])


def find_lvn(profile: list[dict], bucket: float, poc_frac: float = 0.25) -> list[dict]:
    """信号2b: LVN 低量带(纯函数)。网格铺满 [first_lo, last_hi]; profile 里缺席的桶
    = **完全真空(void)**, 存在但 turnover ≤ poc_frac×POC = 稀薄。相邻合并成带,
    **掐掉触到两端的边缘带**(range 尽头天然低量, 是边界不是穿越真空)。

    ⚠️ void 的含义在 2026-08-06 变强了: profile 改成按 bar 区间铺开之后, 一个桶缺席
    意味着**没有任何一根 bar 的 [low, high] 覆盖过它**—— 价格从未到过那里。
    旧口径下它只意味着"没有 bar 的 typical price 落进来", 而 TP 只是一根 bar 的
    代表价、不是成交发生的地方, 于是一根 13% 振幅的日线能让它两边的真实成交价位
    集体显示 0.0% (AAOI 60 日里 30/63 个桶如此)。"""
    if not profile or bucket <= 0:
        return []
    poc = max(b["turnover"] for b in profile)
    if poc <= 0:
        return []
    by_lo = {round(b["lo"], 4): b for b in profile}
    first_lo = profile[0]["lo"]
    n = int(round((profile[-1]["lo"] - first_lo) / bucket))
    if n + 1 < 5:                       # 网格太窄, 无"内部"可言
        return []
    bands: list[dict] = []
    for i in range(n + 1):
        lo = round(first_lo + i * bucket, 4)
        b = by_lo.get(lo)
        if b is not None and b["turnover"] > poc * poc_frac:
            continue                     # 非 LVN 桶
        cell = {"lo": lo, "hi": round(lo + bucket, 4),
                "share_pct": round(b["share_pct"], 1) if b else 0.0,
                "void": b is None}
        if bands and abs(cell["lo"] - bands[-1]["hi"]) < 1e-6:
            last = bands[-1]
            last["hi"] = cell["hi"]
            last["share_pct"] = round(last["share_pct"] + cell["share_pct"], 1)
            last["void"] = last["void"] and cell["void"]
        else:
            bands.append(cell)
    lo_edge, hi_edge = first_lo, round(first_lo + n * bucket + bucket, 4)
    return [b for b in bands
            if b["lo"] > lo_edge + 1e-6 and b["hi"] < hi_edge - 1e-6]


def cluster_pivots(pivots: list[dict], tol: float) -> list[dict]:
    """Merge swing pivots within `tol` of running cluster mean into bands.
    pivots: [{price, date, kind('high'|'low')}]. Multi-touch + mixed-kind
    (support-turned-resistance) bands are the strong ones."""
    if not pivots:
        return []
    pts = sorted(pivots, key=lambda p: p["price"])
    clusters: list[dict] = []
    for p in pts:
        if clusters and abs(p["price"] - clusters[-1]["_mean"]) <= tol:
            c = clusters[-1]
            c["prices"].append(p["price"])
            c["dates"].append(p["date"])
            c["kinds"].add(p["kind"])
            c["_mean"] = sum(c["prices"]) / len(c["prices"])
        else:
            clusters.append({"prices": [p["price"]], "dates": [p["date"]],
                             "kinds": {p["kind"]}, "_mean": p["price"]})
    return [
        {"lo": round(min(c["prices"]), 2), "hi": round(max(c["prices"]), 2),
         "center": round(c["_mean"], 2), "touches": len(c["prices"]),
         "kinds": "+".join(sorted(c["kinds"])), "last_touch": max(c["dates"])}
        for c in clusters
    ]


def level_interactions(bars: list[dict], level: float, near: float | None = None,
                       near_frac: float = 0.015) -> dict:
    """一条价位的交互史: rejection = 最高价够到该位 (容差 near) 但收盘没站上去。

    `near` = **绝对**容差, 优先; 缺省才退回 `near_frac × level` 的旧口径。
    调用方应当传 `near=auto_bucket(...)` —— 即**一个 profile 桶宽**(≈0.25N)。

     由固定 1.5% 改为传桶宽 (用户 裁决)。旧口径是"全池一个百分比",
    折算成各票自己的 ATR 后跨度 **11 倍**: SPY 1.26N / QQQ 0.70N / NVDA 0.42N /
    MU 0.16N / AAOI 0.11N —— 同一个"拒绝"在 SPY 上意思是"差了 1.26 个日均波幅也算
    测试过", 在 AAOI 上是"差 1/8 个日均波幅就不算"。而 gate 的证据强度恰恰要跨票可比。
    误差方向也不是"保守": 低波动票被**虚报**得最狠 —— 同一条线 SPY 39→16 (2.4 倍)、
    QQQ 28→17、NVDA 23→17; 高波动票则被压制 (AAOI 8→9, MRVL 6→8)。

    为什么用桶宽而不是新定一个 ATR 倍数: `auto_bucket` 已经是系统对"一个价位有多宽"
    的既有回答 —— profile 按它分桶、LVN 墙按它的一半步长扫描并按 ±1 桶去重。
    让判定窗口也等于一个桶, **信号 2/2b/4 就在同一分辨率上说话**; 且它自带下限
    (0.4%×spot, 0.1), 挡得住 ATR 塌向 0 的退化情形, 手写 0.25×ATR 没有这层。

    ⚠️ ATR 有自反性: 波动放大 → 窗口变宽 → 计数变多, 即**刚出过事的票每条线都显得更强**。
    所以返回值带 `near` / `near_n`, 调用方必须把口径播报出去 (同  per-ticker
    告警阈值的规矩: 阈值 per-ticker 之后不播报口径, 就没人能判断为什么这条成立那条不成立)。
    """
    near = float(near) if near else level * near_frac
    rejections: list[dict] = []
    closes_above = 0
    for r in bars:
        if r["close"] > level:
            closes_above += 1
        elif r["high"] >= level - near:
            rejections.append({"date": str(r["time"])[:10],
                               "high": round(r["high"], 2), "close": round(r["close"], 2)})
    streak = 0
    for r in reversed(bars):
        if r["close"] > level:
            streak += 1
        else:
            break
    return {"level": level, "closes_above": closes_above, "near": round(near, 4),
            "n_rejections": len(rejections), "recent_rejections": rejections[-6:],
            "closing_streak_above": streak}


def lvn_rejection_walls(bars: list[dict], bands: list[dict], bucket: float,
                        spot: float | None = None, min_rejections: int = 3) -> None:
    """LVN 带内的**拒绝墙**—— 真空 ≠ 无阻力 (用户 提出, 实证 AAOI 140)。

    真空的成因常常正是"反复快速拒绝": 价格一到就被打回、收盘远在下方, 于是这根
    bar 的 TP 永远落不进那个桶 → 量能记 0。所以「带内无库存」与「带内有硬阻力」
    完全可以同时成立, **而且互为因果**。

    源案例 AAOI : 136.5-140.4 桶 60 日 0.0% (TP 从未落入), 而 level 140
    在同一窗口被拒 **8 次**(4/28 / 6/25 / 6/26 / 7/1 / 7/2 / 8/5 + 盘后 141.98) ——
    比 gate 133 的 5 次还多。只报量不报拒绝, 会把最硬的头顶供给读成"畅通区",
    我当天就是这么把 trim 的锚说成"真空带内最大 bin"而漏掉真正的理由。

    区别在用途 (两者不可互换):
      HVN/LVN  = 有没有**库存**→ 回踩会停在哪 (持有者按成本位防守)
      拒绝计数 = 有没有**卖方**→ 上攻会卡在哪 (卖压在那个价位出现)

    原地给每个 band 加 `rejection_walls`: 带内**由低到高的阻力阶梯**(最多 3 堵)。

    ⚠️ **不能按强度排序取 top-N**: 原始计数在下跌趋势里天然偏向更高、更旧的价位
    —— 每次反弹失败都会在沿途每条线上各记一笔, 越高的线被路过得越多。AAOI 实测
    按强度取前三 = 148.2 ×10 / 152.1 ×12 / 165.75 ×10 (拒绝全在 6 月), 而真正要
    **先**撞上的 140 ×8 (含 8/5 与 8/6 盘后) 被挤出榜外 —— 那正是要回答的那一堵。

    所以: 先按价位分段取峰 (相邻 <1 bucket 的扫描点算同一堵, 取其中计数最大者),
    再**由近及远**输出 —— 阶梯的语义是"你会依次撞上哪几堵", 不是"哪堵最有名"。
    """
    if not bands or bucket <= 0:
        return
    step = max(bucket / 2.0, 0.01)
    for b in bands:
        scan = []
        lv = b["lo"]
        while lv <= b["hi"] + 1e-9:
            it = level_interactions(bars, round(lv, 2), near=bucket)
            if it["n_rejections"] >= min_rejections:
                scan.append(it)
            lv += step
        # ① 局部峰: ±1 bucket 窗口内计数最大者 = 一堵墙 (扫描步长 < bucket,
        #    所以不能按"相邻间隔"分段 —— 那样整条带会并成一段, 只剩全带最大值)
        peaks: list[dict] = []
        for i, it in enumerate(scan):
            win = [x for x in scan if abs(x["level"] - it["level"]) <= bucket]
            if it["n_rejections"] < max(x["n_rejections"] for x in win):
                continue
            if peaks and it["level"] - peaks[-1]["level"] <= bucket:
                continue                      # 平台段只取第一个
            peaks.append(it)
        # ② 由近及远 (spot 之上先给最低的那几堵 = 会先撞上的)
        if spot is not None:
            above = sorted([p for p in peaks if p["level"] > spot],
                           key=lambda p: p["level"])[:3]
            below = sorted([p for p in peaks if p["level"] <= spot],
                           key=lambda p: -p["level"])[:1]
            peaks = sorted(above + below, key=lambda p: p["level"])
        else:
            peaks = peaks[:3]
        b["rejection_walls"] = [{"level": p["level"], "n": p["n_rejections"],
                                 "recent": p["recent_rejections"][-3:]} for p in peaks]


def turtle_n(bars: list[dict], period: int = 20) -> float | None:
    """海龟 N = ATR(period)，与 exit_guard 同源 (technical.atr)。"""
    s = technical.atr(bars, period=period)
    return s[-1] if s and s[-1] else None


# 趋势死亡线的两个数 —— **exit_guard 的有意复制**（提名为常量并加 pin 测试）。
# 本模块是**画线时刻的体检**，exit_guard 是**持仓期的 live 强制**：用途分开是有意的
# （`[0.7N,2N]` 体检持仓期跑 = 劝退止损机），但**数必须是同一个**——
# 漂了的后果是画线时看到一条死亡线、live 触发的是另一条，
# 即本仓「两条线并存，报了不 binding 那条」的既有失效形状。
# 改这两个数 → `exit_guard.PARAMS` 同改，`tests/test_constant_pins.py` 会拦。
TRAIL_ATR_MULT = 3.0          # = exit_guard.PARAMS["atr_multiple"]
TRAIL_PARABOLIC_PCT = 0.92    # = exit_guard.PARAMS["parabolic_trail_pct"]


def chandelier(bars: list[dict], n: float, since: str | None = None,
               mult: float = TRAIL_ATR_MULT) -> dict | None:
    """吊灯止损 = HWM(收盘, since 起或全窗口) − mult×ATR。与 trend-exit-system §2
    normal 模式同构 (3×ATR20 锚收盘)。parabolic 变体 = trailing 5d high × 0.92。

    ⚠️ 与 `exit_guard.trend_trail` **公式与常数完全一致**，差异只在**谁提供 ATR 与
    `since` 锚**（这里由调用方传，exit_guard 用布防日与自己的 ATR）—— 那是用途分工，
    不是分叉。核实记录见 `quant/research/pending_work_impact_scope.md` §11。
    """
    sel = [b for b in bars if not since or str(b["time"])[:10] >= since]
    if not sel or not n:
        return None
    hwm = max(b["close"] for b in sel)
    hi5 = max(b["high"] for b in sel[-5:])
    return {"hwm_close": round(hwm, 2), "since": since or str(sel[0]["time"])[:10],
            "trail_normal": round(hwm - mult * n, 2),
            "trail_parabolic": round(hi5 * TRAIL_PARABOLIC_PCT, 2)}


def stop_sanity(spot: float, stop_level: float, n: float) -> dict:
    """结构 stop 距离体检: 健康带 [0.7N, 2N]。<0.7N = 噪音区必被扫;
    >2N = 太远, 按 2N 缩 size 或找更近结构位 (trend-exit-system §2b)。"""
    dist = spot - stop_level
    ratio = dist / n if n else None
    if ratio is None:
        verdict = "no_atr"
    elif ratio < 0:
        verdict = "already_below"
    elif ratio < 0.7:
        verdict = "too_tight_noise(<0.7N)"
    elif ratio <= 2.0:
        verdict = "healthy(0.7-2N)"
    else:
        verdict = "too_far(>2N)_shrink_size_or_nearer_structure"
    return {"stop": stop_level, "distance": round(dist, 2),
            "n_ratio": round(ratio, 2) if ratio is not None else None, "verdict": verdict}


def build_stops(bars: list[dict], entry_price: float | None = None,
                entry_date: str | None = None,
                zone_stop: float | None = None) -> dict:
    """止损锚三家族数字 (trend-exit-system §2b): 2N 海龟 / 吊灯 / 结构位体检。"""
    spot = bars[-1]["close"]
    n = turtle_n(bars, 20)
    out: dict = {"n_atr20": round(n, 2) if n else None,
                 "n_pct": round(n / spot * 100, 1) if n else None}
    if n:
        out["turtle_2n_from_spot"] = round(spot - 2 * n, 2)
        if entry_price:
            out["turtle_2n_from_entry"] = round(entry_price - 2 * n, 2)
        ch = chandelier(bars, n, since=entry_date)
        if ch:
            out["chandelier"] = ch
            out["chandelier_broken"] = spot < ch["trail_normal"]
        if zone_stop:
            out["structure_stop_sanity"] = stop_sanity(spot, zone_stop, n)
    return out


def build_map(bars: list[dict], levels: list[float] | None = None,
              profile_days: int = 60, wing: int = 2, hvn_n: int = 5,
              bucket: float | None = None, lvn_frac: float = 0.25,
              profile_bars: list[dict] | None = None,
              profile_source: str = "1d") -> dict:
    """Assemble the full level map from bars (pure).

    `profile_bars` = 信号2 专用输入 (intraday, 含盘前盘后)。**只有 volume profile
    换粒度**: pivot 聚类 / ATR / 拒绝计数都是**日线概念**(swing 结构、收盘口径),
    换成分钟线会变成另一套语义。缺省 = 用日线, 由铺开近似兜底。"""
    closes = [b["close"] for b in bars]
    spot = closes[-1]
    atr_series = technical.atr(bars, period=14)
    atr14 = atr_series[-1] if atr_series and atr_series[-1] else None
    ma20_s = technical.sma(closes, 20)
    ma50_s = technical.sma(closes, 50)
    bkt = bucket or auto_bucket(spot, atr14)

    pbars = profile_bars if profile_bars else bars[-profile_days:]
    profile = make_profile(pbars, bkt)
    hvn = top_hvn(profile, n=hvn_n)
    lvn = find_lvn(profile, bkt, poc_frac=lvn_frac)
    for b in lvn:                        # 停靠 HVN: 回踩滑过 LVN 落在哪 / 反弹顶在哪
        below = [h for h in hvn if h["hi"] <= b["lo"] + 1e-6]
        above = [h for h in hvn if h["lo"] >= b["hi"] - 1e-6]
        hb = max(below, key=lambda h: h["hi"]) if below else None
        ha = min(above, key=lambda h: h["lo"]) if above else None
        b["hvn_below"] = [hb["lo"], hb["hi"]] if hb else None
        b["hvn_above"] = [ha["lo"], ha["hi"]] if ha else None
    # 真空 ≠ 无阻力: 带内扫拒绝墙 (量的维度看不见卖方, 见函数 docstring)
    lvn_rejection_walls(bars, lvn, bkt, spot=spot)

    # ⏳ 未确认极值: swing_points 要求右侧 wing 根更低/更高, 所以**最近 wing 根永远
    # 不可能成为 pivot**—— 事件后当日重画时, 刚创下的高低点对聚类完全不可见
    # (实证: AAOI 8/5 的 140.34 高点没资格入列, 于是 pivot 簇里找不到
    #  那条被拒 8 次的线)。这里把它单列出来标"待确认", 不混进 confirmed 簇。
    tail = bars[-(wing + 1):]
    pending = []
    if len(bars) > wing:
        hi_b = max(tail, key=lambda r: r["high"])
        lo_b = min(tail, key=lambda r: r["low"])
        for b, kind, px in ((hi_b, "high", hi_b["high"]), (lo_b, "low", lo_b["low"])):
            need = wing - (len(bars) - 1 - bars.index(b))
            pending.append({"price": round(px, 2), "kind": kind,
                            "date": str(b.get("time", ""))[:10],
                            "bars_needed": max(0, need)})
    highs, lows = technical.swing_points(bars, wing=wing)
    pivots = ([{"price": p, "date": str(bars[i]["time"])[:10], "kind": "high"} for i, p in highs]
              + [{"price": p, "date": str(bars[i]["time"])[:10], "kind": "low"} for i, p in lows])
    tol = (atr14 or spot * 0.02) * 0.35
    clusters = cluster_pivots(pivots, tol)

    return {
        "asof": str(bars[-1]["time"])[:10],
        "n_bars": len(bars),
        "spot": spot,
        "atr14": round(atr14, 2) if atr14 else None,
        "atr14_pct": round(atr14 / spot * 100, 1) if atr14 else None,
        "ma20": round(ma20_s[-1], 2) if ma20_s[-1] else None,
        "ma50": round(ma50_s[-1], 2) if ma50_s[-1] else None,
        "bucket": bkt,
        "profile_days": len(pbars),
        "profile_source": profile_source,
        "profile": profile,
        "hvn_bands": hvn,
        "lvn_bands": lvn,
        "lvn_frac": lvn_frac,
        "pivot_pending": pending,
        "pivot_clusters_below": [c for c in clusters if c["center"] <= spot],
        "pivot_clusters_above": [c for c in clusters if c["center"] > spot],
        # 判定窗口 = 一个 profile 桶宽 (≈0.25N), 与信号 2/2b 同分辨率; 见 level_interactions
        "levels": [level_interactions(bars, lv, near=bkt) for lv in (levels or [])],
        "near_window": bkt,
        "near_window_n": round(bkt / atr14, 2) if atr14 else None,
    }


# ── [信号5] 趋势观察 (立项, [FYI] 观察层) ─────────────

OBS_DAYS = 260   # AVWAP/RS 观察窗: 破位锚要 60d-low+MA60 暖机, 260 根 ≈ 1 年


def build_trend_obs(bars: list[dict], bench_bars: list[dict], bench_name: str,
                    explicit_anchor: str | None = None) -> dict:
    """[FYI 观察层] AVWAP 成本锚 + RS 相对强度。**零授权效力**—— 不进
    gate/zone/cap/stop 任何判定, 只供画卡与复盘读盘 (trend_slots_design §2)。

    AVWAP 锚三选: ①最近结构破位日 (`core.episodes` 绝对口径: 收 60 日最低收盘
    且 close<MA60 —— 与 gate_path/快车道同一份定义, 不另造) ②窗口 60 根内最大
    成交额日 (机构换手锚) ③explicit_anchor 显式日期。破位日与最大量日重合时去重。
    RS = close/基准close 首日归一; 基准 = core.sectors 冻结表 (半导体→SMH 其余→QQQ)。
    基准缺数据 → 跳过并把降级说出口 (rs_warn), 不静默。
    """
    from quant.core import episodes  # noqa: PLC0415
    closes = [float(r.get("close", 0)) for r in bars]
    dates = [str(r.get("time", ""))[:10] for r in bars]
    out: dict = {"anchors": [], "rs": None, "bench": bench_name}
    if not closes:
        return out
    spot = closes[-1]

    anchor_idx: list[tuple[str, int]] = []
    ma60 = technical.sma(closes, 60)
    eps = episodes.episodes_of(closes, ma60)
    if eps:
        anchor_idx.append(("破位日", eps[-1]))
    if bars:
        w = bars[-60:]
        off = len(bars) - len(w)
        mx = max(range(len(w)),
                 key=lambda i: float(w[i].get("turnover") or 0))
        anchor_idx.append(("60d最大量日", off + mx))
    if explicit_anchor:
        try:
            anchor_idx.append(("显式", dates.index(explicit_anchor)))
        except ValueError:
            out["anchor_warn"] = f"--avwap {explicit_anchor} 不在 {len(bars)} 根观察窗内"
    seen: set[int] = set()
    for label, idx in anchor_idx:
        if idx in seen:
            continue
        seen.add(idx)
        v = technical.anchored_vwap(bars, idx)[-1]
        if v:
            out["anchors"].append({
                "label": label, "date": dates[idx], "avwap": round(v, 2),
                "spot_vs_pct": round((spot / v - 1) * 100, 1),
                "days": len(bars) - idx,
            })

    if bench_bars:
        bmap = {str(r.get("time", ""))[:10]: float(r.get("close", 0))
                for r in bench_bars}
        pair = [(c, bmap[d]) for c, d in zip(closes, dates) if bmap.get(d)]
        if len(pair) >= 30:
            rs = technical.rs_series([p[0] for p in pair], [p[1] for p in pair])
            vals = [v for v in rs if v is not None]
            if vals:
                now = vals[-1]
                d20 = vals[-21] if len(vals) >= 21 else vals[0]
                w60 = vals[-60:]
                out["rs"] = {
                    "bench": bench_name,
                    "chg20_pct": round((now / d20 - 1) * 100, 1),
                    "vs_60d_high_pct": round((now / max(w60) - 1) * 100, 1),
                    "new_60d_high": now >= max(w60) - 1e-12,
                    "n_days": len(vals),
                }
        else:
            out["rs_warn"] = f"基准 {bench_name} 对齐后仅 {len(pair)} 日 (<30) — RS 跳过"
    else:
        out["rs_warn"] = f"基准 {bench_name} 无缓存 — RS 跳过"
    return out


def print_trend_obs(obs: dict | None) -> None:
    if not obs or not (obs.get("anchors") or obs.get("rs")
                       or obs.get("rs_warn") or obs.get("anchor_warn")):
        return
    print("\n[信号5 趋势观察 · FYI] (AVWAP 成本锚 + RS 相对强度 — 观察层, 零授权效力)")
    for a_ in obs.get("anchors") or []:
        side = "上方" if a_["spot_vs_pct"] >= 0 else "下方"
        print(f"  AVWAP@{a_['label']} {a_['date']} ({a_['days']}根): ${a_['avwap']:g} "
              f"— 现价在锚线{side} {a_['spot_vs_pct']:+.1f}%")
    if obs.get("anchor_warn"):
        print(f"  ⚠️ {obs['anchor_warn']}")
    rs = obs.get("rs")
    if rs:
        nh = " 🆕RS 60d 新高" if rs["new_60d_high"] else ""
        print(f"  RS vs {rs['bench']}: 20d {rs['chg20_pct']:+.1f}% | "
              f"距 60d 高点 {rs['vs_60d_high_pct']:+.1f}%{nh}")
    if obs.get("rs_warn"):
        print(f"  ⚠️ {obs['rs_warn']}")


# ── Data fetch (OpenD / cache) ─────────────────────────────────

def fetch_bars(ticker: str, days: int) -> list[dict]:
    """Daily bars via OpenD (lazy import; exits if OpenD down — see market_data)."""
    from quant.data.market_data import get_kline  # noqa: PLC0415
    _t = date.fromisoformat(et_today())          # kline 日期是 ET, 取窗同口径
    start = (_t - timedelta(days=int(days * 1.55) + 10)).isoformat()
    bars = get_kline(ticker, ktype="1d", start=start, end=_t.isoformat())
    return bars[-days:]


PROFILE_KTYPE = "60m"        # 2 次分页 / 0.2s / TVD 3.1% —— 15m 只多 1.4pp 却 2 倍调用
PROFILE_SESSIONS_PER_DAY = 7  # 60m bar/交易日 (含盘前盘后), 用于估算取数窗口


def fetch_profile_bars(ticker: str, days: int) -> tuple[list[dict], str]:
    """volume profile 的**首选**输入: intraday bar (含盘前盘后)。→ (bars, source)。

    为什么不用日线: 日线只能把一整天的成交额按 bar 区间**近似**铺开 (三角权重),
    对真实分布的总变差 ~11%; 真 60m 是 3.1%, 且 top-5 HVN 全中 (日线近似中 4/5)。
    代价 = 2 次分页请求 / 0.2 秒 / **零新增额度**(历史 K 线额度按标的×30 天计,
    各周期共用一份, 书里的票早已在名单上)。

    ⚠️ `extended_time=True` 不可省: RTH-only 的分钟线加总只有日线成交额的 89.5%,
    缺的 10.5% 正是 gap 与财报反应发生的地方。

    拉不到 → 返回 ([], 原因), **由调用方回落到日线并把降级说出口**——
    静默降级是本仓反复记的失效形状。
    """
    try:
        from quant.data.market_data import get_kline  # noqa: PLC0415
        _t = date.fromisoformat(et_today())
        start = (_t - timedelta(days=int(days * 1.55) + 10)).isoformat()
        bars = get_kline(ticker, ktype=PROFILE_KTYPE, start=start, end=_t.isoformat(),
                         num=1000, extended_time=True)
    except SystemExit:
        return [], "OpenD 不可达"
    except Exception as e:                      # noqa: BLE001
        return [], f"取数失败 ({type(e).__name__})"
    if len(bars) < days * PROFILE_SESSIONS_PER_DAY * 0.5:
        return [], f"{PROFILE_KTYPE} bar 仅 {len(bars)} 根, 不足半个窗口"
    return bars[-(days * PROFILE_SESSIONS_PER_DAY * 2):], PROFILE_KTYPE


def cached_bars(ticker: str, days: int) -> list[dict]:
    path = KLINE_CACHE_DIR / f"{ticker.upper()}.jsonl"
    if not path.exists():
        return []
    rows = []
    for r in iter_jsonl(path):
        if all(k in r for k in ("open", "high", "low", "close")):
            r.setdefault("time", r.get("date", ""))
            # turnover 缺失 → 用 volume×close 兜底。**不能填 0**: profile 全零时
            # POC=0 → find_lvn 直接返回空、top_hvn 退化成任意桶, 而且一声不响
            # (静默降级 = data_discipline 反复记的形状)。
            if not r.get("turnover"):
                r["turnover"] = float(r.get("volume") or 0) * float(r.get("close") or 0)
            rows.append(r)
    return rows[-days:]


def load_zone_context(ticker: str, positions_path: Path = None,
                      watchlist_path: Path = None) -> dict | None:
    """信号1: agent.zones。卡源判别式 = live_tickers (qty 判据): live → positions 卡,
    否则 watchlist 卡优先 — positions 里的 closed 残卡不得遮真源
    (2026-08-06 z1_budgets 同型 bug, INTC [信号1] 曾显示已平仓旧 zone)。"""
    from quant.core.holdings import live_tickers  # noqa: PLC0415
    docs = {}
    for path, bucket in ((positions_path or POSITIONS_PATH, "positions"),
                         (watchlist_path or WATCHLIST_PATH, "watchlist")):
        try:
            docs[bucket] = read_json(path)
        except Exception:
            docs[bucket] = {}
    t = ticker.upper()
    live = set(live_tickers(docs.get("positions") or {}))
    for bucket in (("positions", "watchlist") if t in live
                   else ("watchlist", "positions")):
        body = ((docs.get(bucket) or {}).get("tickers") or {}).get(t)
        if body:
            zones = (body.get("agent") or {}).get("zones")
            if zones:
                return {"source": bucket, "zones": zones}
    return None


def fetch_oi_walls(ticker: str, spot: float, band: float = 0.25,
                   max_expiries: int = 2, top_n: int = 8) -> list[dict]:
    """信号3: per-strike OI walls (nearest expiries within 7-90 DTE, strikes
    within ±band of spot). Costs snapshot quota — keep behind --oi."""
    from quant.core import env  # noqa: F401  (.env → os.environ)
    add_openapi_path()  # openapi skill scripts (`common`) 上 sys.path
    from common import RET_OK, create_quote_context, safe_close, safe_float, safe_get  # noqa: PLC0415

    code = f"US.{ticker.upper()}"
    ctx = None
    try:
        ctx = create_quote_context()
        ret, exps = ctx.get_option_expiration_date(code)
        if ret != RET_OK or exps is None or exps.empty:
            return []
        today = date.fromisoformat(et_today())   # expiry 是 ET 日历日, DTE 必须同口径
        chosen: list[str] = []
        for _, r in exps.iterrows():
            ds = str(safe_get(r, "strike_time", default=""))[:10]
            try:
                dte = (date.fromisoformat(ds) - today).days
            except ValueError:
                continue
            if 7 <= dte <= 90:
                chosen.append(ds)
            if len(chosen) >= max_expiries:
                break

        walls: dict[tuple, dict] = {}
        for exp in chosen:
            ret, chain = ctx.get_option_chain(code, start=exp, end=exp)
            if ret != RET_OK or chain is None or chain.empty:
                continue
            codes, meta = [], {}
            for _, r in chain.iterrows():
                k = safe_float(safe_get(r, "strike_price"), default=None)
                oc = str(safe_get(r, "code", default=""))
                ot = str(safe_get(r, "option_type", default="")).upper()
                if not k or not oc or abs(k - spot) / spot > band:
                    continue
                codes.append(oc)
                meta[oc] = (k, "PUT" if "PUT" in ot else "CALL", exp)
            for i in range(0, len(codes), 100):
                ret, snap = ctx.get_market_snapshot(codes[i:i + 100])
                if ret != RET_OK or snap is None:
                    continue
                for _, s in snap.iterrows():
                    oc = str(safe_get(s, "code", default=""))
                    if oc not in meta:
                        continue
                    oi = safe_get(s, "option_open_interest", default=None)
                    if oi is None:
                        oi = safe_get(s, "open_interest", default=0)
                    k, side, exp_ = meta[oc]
                    w = walls.setdefault((exp_, k), {"expiry": exp_, "strike": k,
                                                     "call_oi": 0, "put_oi": 0})
                    w["call_oi" if side == "CALL" else "put_oi"] += int(oi or 0)

        ranked = sorted(walls.values(), key=lambda w: -(w["call_oi"] + w["put_oi"]))[:top_n]
        for w in ranked:
            w["total_oi"] = w["call_oi"] + w["put_oi"]
        return sorted(ranked, key=lambda w: (w["expiry"], w["strike"]))
    except Exception as e:  # noqa: BLE001
        print(f"[level_map] OI fetch failed: {type(e).__name__}: {e}", file=sys.stderr)
        return []
    finally:
        try:
            safe_close(ctx)
        except Exception:  # noqa: BLE001
            pass


# ── Output ─────────────────────────────────────────────────────

def print_human(ticker: str, m: dict, zone_ctx: dict | None, oi: list[dict] | None) -> None:
    print(f"=== {ticker} level map — asof {m['asof']} ({m['n_bars']} bars) ===")
    print(f"spot {m['spot']:.2f}   ATR14 {m['atr14']} ({m['atr14_pct']}%)   "
          f"MA20 {m['ma20']}   MA50 {m['ma50']}   bucket ${m['bucket']}")

    if zone_ctx:
        z = zone_ctx["zones"]
        vu = z.get("valid_until", "?")
        stale = " ⚠️STALE" if (isinstance(vu, str) and vu and vu < et_today()) else ""
        print(f"\n[信号1 zone] ({zone_ctx['source']}, valid_until {vu}{stale})")
        for k in ("accumulation_zone_1", "accumulation_zone_2", "trim_zone",
                  "no_chase_above", "stop_level"):
            if z.get(k) is not None:
                print(f"  {k}: {z[k]}")

    lvn_bands = m.get("lvn_bands") or []

    def _in(b_lo: float, b_hi: float, bands: list[dict]) -> bool:
        return any(h["lo"] - 1e-6 <= b_lo and b_hi <= h["hi"] + 1e-6 for h in bands)

    print(f"\n[信号2 volume profile] {m['profile_source']} × {m['profile_days']} bars, turnover-weighted, "
          f"█=HVN ░=LVN(真空行补 0)")
    rows = [dict(b) for b in m["profile"]]
    have_lo = {round(b["lo"], 4) for b in rows}
    for lb in lvn_bands:                 # 真空桶不在 profile 里, 显示层补出来
        lo = lb["lo"]
        while lo < lb["hi"] - 1e-6:
            if round(lo, 4) not in have_lo:
                rows.append({"lo": round(lo, 4), "hi": round(lo + m["bucket"], 4),
                             "share_pct": 0.0})
            lo = round(lo + m["bucket"], 4)
    for b in sorted(rows, key=lambda x: x["lo"]):
        mark = ("█" if _in(b["lo"], b["hi"], m["hvn_bands"])
                else "░" if _in(b["lo"], b["hi"], lvn_bands) else " ")
        bar = "#" * max(0 if b["share_pct"] == 0 else 1, int(b["share_pct"] * 2.5))
        print(f" {mark} {b['lo']:>8.1f}-{b['hi']:<8.1f} {b['share_pct']:>4.1f}%  {bar}")

    if lvn_bands:
        nn = f" = {m['near_window_n']}N" if m.get("near_window_n") else ""
        print(f"\n[信号2b LVN 低量带] (≤{m.get('lvn_frac', 0.25) * 100:g}%×POC; "
              "**真空 = 没有任何一根 bar 的区间覆盖过**; zone 边界别画在带内, "
              "回踩滑过 LVN 停在停靠 HVN)")
        print(f"  ⚠️ 真空 ≠ 无阻力: 量的维度看不见卖方 —— 反复快速拒绝正是真空的成因"
              f" | 拒绝判定窗口 ±${m.get('near_window', 0):g}{nn}")
        for b in lvn_bands:
            kind = "真空" if b["void"] else f"{b['share_pct']:.1f}%"
            hb = (f"↓停靠 {b['hvn_below'][0]:.1f}-{b['hvn_below'][1]:.1f}"
                  if b.get("hvn_below") else "↓无 HVN")
            ha = (f"↑顶 {b['hvn_above'][0]:.1f}-{b['hvn_above'][1]:.1f}"
                  if b.get("hvn_above") else "↑无 HVN")
            print(f"  {b['lo']:>8.1f}-{b['hi']:<8.1f} {kind:>5}  {hb}  {ha}")
            for w in (b.get("rejection_walls") or []):
                dates = ", ".join(r["date"] for r in w.get("recent") or [])
                tag = "⛔" if w["level"] > m["spot"] else "·"
                print(f"           {tag} 带内拒绝墙 {w['level']:g} ×{w['n']} "
                      f"(近 {dates}) — 无库存但有卖方")

    print("\n[信号4 pivot clusters] (touches≥2 = 强位; H+L = 支撑阻力互换位)")
    for pd_ in (m.get("pivot_pending") or []):
        if pd_["bars_needed"] > 0:
            print(f"  ⏳ 待确认 {pd_['kind']:>4} {pd_['price']:>8.2f} ({pd_['date']}) "
                  f"— 右侧还差 {pd_['bars_needed']} 根才够格入簇; **事件后重画时最新结构"
                  f"对聚类不可见**, 需要时显式 --levels 带上它")
    for label, key in (("above", "pivot_clusters_above"), ("below", "pivot_clusters_below")):
        rows = [c for c in m[key] if c["touches"] >= 2] or m[key][:3]
        for c in sorted(rows, key=lambda c: c["center"], reverse=(label == "above")):
            print(f"  {label:>5} {c['lo']:>8.2f}-{c['hi']:<8.2f} x{c['touches']} "
                  f"[{c['kinds']}] last {c['last_touch']}")

    for lv in m["levels"]:
        nn = f" = {m['near_window_n']}N" if m.get("near_window_n") else ""
        print(f"\n[level {lv['level']}] closes_above {lv['closes_above']} | "
              f"rejections {lv['n_rejections']} | 当前连续站上 {lv['closing_streak_above']} 日"
              f" | 判定窗口 ±${lv['near']:g}{nn}")
        for r in lv["recent_rejections"]:
            print(f"    reject {r['date']}: high {r['high']} → close {r['close']}")

    if oi:
        print("\n[信号3 OI walls] (±25% strikes, 近月)")
        for w in oi:
            print(f"  {w['expiry']} {w['strike']:>8.1f}  C {w['call_oi']:>7,}  "
                  f"P {w['put_oi']:>7,}  Σ {w['total_oi']:>7,}")


def print_stops(s: dict) -> None:
    if not s.get("n_atr20"):
        print("\n[stops] ATR 不可得")
        return
    print(f"\n[stops 三家族] N=ATR20 ${s['n_atr20']} ({s['n_pct']}%/日)")
    line = f"  海龟 2N: 现价−2N = ${s['turtle_2n_from_spot']}"
    if s.get("turtle_2n_from_entry") is not None:
        line += f" | entry−2N = ${s['turtle_2n_from_entry']}"
    print(line)
    ch = s.get("chandelier")
    if ch:
        broken = "  ⚠️已破" if s.get("chandelier_broken") else ""
        print(f"  吊灯: HWM收盘 ${ch['hwm_close']} (since {ch['since']}) − 3N = "
              f"${ch['trail_normal']}{broken} | parabolic 5dH×0.92 = ${ch['trail_parabolic']}")
    ss = s.get("structure_stop_sanity")
    if ss:
        print(f"  结构 stop ${ss['stop']} 距离 {ss['distance']} = {ss['n_ratio']}N → {ss['verdict']}")


def main() -> None:
    p = argparse.ArgumentParser(description="多指标共振价位图 (volume/OI/K线结构/zone 四信号)")
    p.add_argument("ticker")
    p.add_argument("--days", type=int, default=90, help="结构分析窗口 (默认 90 bars)")
    p.add_argument("--profile-days", type=int, default=60, help="volume profile 窗口 (默认 60)")
    p.add_argument("--levels", nargs="*", type=float, help="要验证交互史的价位")
    p.add_argument("--wing", type=int, default=2, help="swing pivot 宽度 (默认 2)")
    p.add_argument("--hvn", type=int, default=5, help="HVN 桶数 (默认 5)")
    p.add_argument("--lvn-frac", type=float, default=0.25,
                   help="LVN 阈值 = 该分数×POC turnover (默认 0.25)")
    p.add_argument("--bucket", type=float, help="手动指定 profile 桶宽")
    p.add_argument("--oi", action="store_true", help="拉期权链 OI 墙 (耗配额)")
    p.add_argument("--stops", action="store_true", help="输出止损三家族 (2N/吊灯/结构体检)")
    p.add_argument("--entry-price", type=float, help="--stops 用: 入场价 (算 entry−2N)")
    p.add_argument("--entry-date", help="--stops 用: 入场日 YYYY-MM-DD (吊灯 HWM 起点)")
    p.add_argument("--cached", action="store_true", help="用 kline 缓存 (离线, 可能缺最新 bar)")
    p.add_argument("--avwap", help="[信号5] AVWAP 显式锚日 YYYY-MM-DD (默认锚: 最近破位日 + 60d最大量日)")
    p.add_argument("--no-obs", action="store_true", help="跳过 [信号5] 趋势观察段 (AVWAP/RS)")
    p.add_argument("--json", action="store_true")
    a = p.parse_args()

    ticker = a.ticker.upper()
    bars = cached_bars(ticker, a.days) if a.cached else fetch_bars(ticker, a.days)
    if len(bars) < 30:
        print(f"[level_map] insufficient bars for {ticker}: {len(bars)}", file=sys.stderr)
        sys.exit(1)

    # 信号2 首选 intraday 真实分布; 拿不到就回落日线近似, **并把降级说出口**
    pbars, psrc = ([], "--cached (离线)") if a.cached else fetch_profile_bars(ticker, a.profile_days)
    if not pbars:
        print(f"[level_map] ⚠️ volume profile 回落日线铺开近似 ({psrc}) — "
              f"分布对真值的偏差约 3.6x 于 {PROFILE_KTYPE}", file=sys.stderr)
        psrc = "1d(铺开近似)"
    m = build_map(bars, levels=a.levels, profile_days=a.profile_days,
                  wing=a.wing, hvn_n=a.hvn, bucket=a.bucket, lvn_frac=a.lvn_frac,
                  profile_bars=pbars or None, profile_source=psrc)
    zone_ctx = load_zone_context(ticker)
    oi = fetch_oi_walls(ticker, m["spot"]) if a.oi else None
    stops = None
    if a.stops:
        zone_stop = (zone_ctx or {}).get("zones", {}).get("stop_level")
        stops = build_stops(bars, entry_price=a.entry_price,
                            entry_date=a.entry_date, zone_stop=zone_stop)

    obs = None
    if not a.no_obs:
        from quant.core.sectors import benchmark_for  # noqa: PLC0415
        obs_bars = (bars if len(bars) >= OBS_DAYS
                    else (cached_bars(ticker, OBS_DAYS) if a.cached
                          else fetch_bars(ticker, OBS_DAYS)))
        bench = benchmark_for(ticker)
        obs = build_trend_obs(obs_bars, cached_bars(bench, OBS_DAYS), bench,
                              explicit_anchor=a.avwap)

    if a.json:
        out = {"ticker": ticker, **m, "zone_context": zone_ctx, "oi_walls": oi,
               "stops": stops, "trend_obs": obs}
        print(json.dumps(out, ensure_ascii=False, indent=1))
    else:
        print_human(ticker, m, zone_ctx, oi)
        print_trend_obs(obs)
        if stops:
            print_stops(stops)


if __name__ == "__main__":
    main()
