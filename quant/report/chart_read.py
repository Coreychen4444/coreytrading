#!/usr/bin/env python3
"""chart_read.py — **每日收盘后判读**：两种模式（抄底 / 趋势确认）已形成或即将形成的标的。

════════════════════════════════════════════════════════════════════════
🎯 核心目的（这是本模块存在的唯一理由）
════════════════════════════════════════════════════════════════════════

    每天收盘后，根据 K 线、成交量与指标等，分析出当前所处结构及形态，
    进而分析出潜在的交易机会。

    收窄：盘后判读不再关注多种形态，仅关注这两种形态已形成或即将形成的标的，
      未形成的不需要关注。
      两种形态 = ① 抄底（止跌底部确认后做反弹或反转）② 趋势确认（等站稳 gate 后的进场）。
      止跌不用道氏结构判断，只用最基本的价格行为判断空头力量衰竭：
      次日没有继续新低或收出长下影线，止损放在下影线低点。
      判别式真源 `quant/core/setups.py`（抄底 = 空头衰竭：次日未新低 / 长下影，止损 = 止跌低；
      趋势确认 = 卡片 gate 接近度）。

    分析链条仍是：Context → Structure → Price Action → Volume → Indicators → Trade
    —— 前五步是**事实**（详情页照给，它们喂「确认度」），第六步只剩两种形态。
    Lo (2000) 形态族（双顶 / 楔形 / 头肩 …）已**退出每日判读**（`core/patterns`
    保留给研究层，本模块不再 import）；「后续倾向三栏」随之退役。

**改动本模块前先问**：它是否让「每天收盘后看懂这只票在不在两种模式里、到哪一步了」
这件事做得更好？不是「是否更严谨/更全面/更像个系统」。

════════════════════════════════════════════════════════════════════════
🚨 边界：判读层，零授权效力（`tests/report/test_chart_read.py` 钉它）
════════════════════════════════════════════════════════════════════════

  - **不写任何 state**（positions/watchlist/exit_guards/heartbeat-state 一个都不碰）；
  - **不产出 cap / 股数 / 下单价 / 档位**；「阶段」只说结构走到哪一步，不说能不能进
    —— 能不能进、进多少永远以 `entry_guard` / 卡片 zones / `sizing.entry_cap` 为准；
  - 反弹 vs 反转是**判读**，「反弹就尽早清仓」的机械口径归 exit_guard（须先过
    `exit_rules_backtest`），本模块只播裁量点。

这与 v3 分工同构：**授权归结构机器，判断权归人工，agent 供弹药**。

════════════════════════════════════════════════════════════════════════
数据纪律
════════════════════════════════════════════════════════════════════════

  - 只吃**已完成日线**（`market/klines/` + `market/klines_deep/` 合并去重）。
    盘中跑 = 未完成 bar 当收盘，本仓栽过（`heartbeat.md` §盘中运行，NBIS 7/28）。
    → `--asof` 可显式截断到某个交易日，用于复盘与测试。
  - 一切读数**归一化**（ATR 尺 / 相对量比 / 百分比），不用全池固定阈值。
  - 指标**一轴一票**：只报一个「离均值多远」与一个「动量背离」，不堆四个装共振。

Usage:
    python3 quant/report/chart_read.py --book --digest      # 推送：两种模式清单
    python3 quant/report/chart_read.py --tickers <T1> <T2>  # 详情
    python3 quant/report/chart_read.py --tickers <T> --asof <YYYY-MM-DD> --json
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 直接 `python3 quant/<层>/<模块>.py` 时让 `quant.*` 可 import

import argparse  # noqa: E402
import json  # noqa: E402
from typing import Dict, List, Optional  # noqa: E402

from quant.core import earnings as earn  # noqa: E402
from quant.core import dow, setups, technical  # noqa: E402
from quant.core import structure as st  # noqa: E402
from quant.core.episodes import (BROKE_WINDOW, DELTA_ATR, breakdown_in_window,  # noqa: E402
                                 episodes_of, gate_path, merge_bars_by_date)
from quant.core.holdings import (is_candidate, is_managed, is_traded,  # noqa: E402
                                 live_tickers)
from quant.core.io_json import iter_jsonl, read_json  # noqa: E402
from quant.core.num import coerce_float  # noqa: E402
from quant.core.paths import (EARNINGS_CALENDAR_PATH, KLINE_CACHE_DIR,  # noqa: E402
                              KLINE_DEEP_DIR, POSITIONS_PATH, WATCHLIST_PATH)
from quant.core.sectors import benchmark_for  # noqa: E402
from quant.decision.level_map import cluster_pivots, find_lvn, top_hvn  # noqa: E402
from quant.decision.regime_transition import (rel_volume, streak_above,  # noqa: E402
                                              volume_leg)
from quant.report.gate_ladder_report import (PATH_TAGS, fastlane_state,  # noqa: E402  同层: gate 行的渲染辅助, 不另造
                                             ladder_of)

PROFILE_BARS = 60        # 与卡片画线同口径 (level_map / volume_profile 现行 60 根)
PIVOT_BARS = 250         # pivot 簇回看
RS_WINDOW = 20           # 相对强度窗口 (交易日)
NEAR_ATR = 3.0           # 只报现价 ±3N 内的墙 (再远今天交互不到)

# 力/果的格子边界 —— 与 price_action_framework_design §7.2 同值, 且
# close_pos 与 tape_check 的 60m 口径对齐 (0.40/0.70), 不另立一套。
# 🚫 EFFORT_HI 1.2 与**授权量腿**同数不同身份: 这里是判读层
#    Wyckoff 力/果分格, 零授权效力; 授权阈值 = RelVR ≥1.1
#    (`rt.RECLAIM_VOL_RATIO_MIN`)。裁的是量腿, 没有证据说这条判读格该跟着动 → 不动。
EFFORT_HI, EFFORT_LO = 1.2, 0.8
RESULT_HI, RESULT_LO = 1.0, 0.6
CLOSE_WEAK, CLOSE_STRONG = 0.40, 0.70

# 「最近」= 一周 —— 判读的时间尺度（短期一周内的结构）。
# gate 转移 ≤5 根前 = 「已形成」还算现在；再早的是历史。
RECENT_BARS = 5


def effort_label(val: Optional[float]) -> Optional[str]:
    """RelVR → 高力/中力/低力。调用方传**播出去的那个数**（已 round）。"""
    if val is None:
        return None
    return "高力" if val >= EFFORT_HI else ("低力" if val <= EFFORT_LO else "中力")


# ══════════════════════════════════════════════════════════════════
# 数据
# ══════════════════════════════════════════════════════════════════

def _rows(path: Path) -> List[dict]:
    if not path.exists():
        return []
    out = []
    for r in iter_jsonl(path):
        if all(k in r for k in ("open", "high", "low", "close")):
            r.setdefault("time", r.get("date", ""))
            if not r.get("turnover"):
                # 不能填 0: profile 全零会让 POC=0 → LVN/HVN 静默失真
                r["turnover"] = float(r.get("volume") or 0) * float(r.get("close") or 0)
            out.append(r)
    return out


_BAR_CACHE: Dict[tuple, tuple] = {}      # 全书跑一轮时基准会被读 N 次, 缓存纯是省 IO


def load_bars(ticker: str, asof: Optional[str] = None):
    """深度缓存 + 常规缓存按日期去重合并 → (bars, note)。

    必须合并: `gate_path` 的 ath 要样本内历史最高收盘、`breakdown_in_window` 要
    180 根才算得满，而常规缓存只有 ~1 年。直接拼接会让重叠段污染 MA60
    （实证，分类当场算错）—— 所以走 `merge_bars_by_date`，由旧到新。
    """
    t = ticker.upper()
    key = (t, asof)
    if key in _BAR_CACHE:
        return _BAR_CACHE[key]
    bars, note = merge_bars_by_date(_rows(KLINE_DEEP_DIR / f"{t}.jsonl"),
                                    _rows(KLINE_CACHE_DIR / f"{t}.jsonl"))
    if asof:
        bars = [b for b in bars if str(b.get("time", ""))[:10] <= asof]
    _BAR_CACHE[key] = (bars, note)
    return bars, note


def load_card(ticker: str) -> Optional[Dict]:
    """卡片（含 agent 全块）。判据同 `level_map.load_zone_context`：
    live → positions 卡优先，否则 watchlist 优先 —— positions 里的 closed 残卡
    不得遮真源（ INTC 显示已平仓旧 zone 的同型 bug）。"""
    docs = {}
    for path, bucket in ((POSITIONS_PATH, "positions"), (WATCHLIST_PATH, "watchlist")):
        try:
            docs[bucket] = read_json(path) or {}
        except Exception:                                    # noqa: BLE001
            docs[bucket] = {}
    t = ticker.upper()
    live = set(live_tickers(docs.get("positions") or {}))
    for bucket in (("positions", "watchlist") if t in live else ("watchlist", "positions")):
        body = ((docs.get(bucket) or {}).get("tickers") or {}).get(t)
        if body and body.get("agent"):
            return {"bucket": bucket, "agent": body["agent"],
                    "status": body.get("status"), "qty": (body.get("broker") or {}).get("qty")}
    return None


def book_tickers() -> List[str]:
    """全书 = 有 zones 的**活着的**正股卡（持仓 + watchlist），与简报同一个人群。

    判据全部来自 `core.holdings`，本函数不自己写一份：

      持仓侧   `live_tickers` (broker.qty 单一真源 + 在持期权的 synthetic 行)
               ∩ `is_managed` (结构豁免层「不进任何 task 输出」)
      候选侧   `is_candidate` (status=watching **且** moomoo_listed≠false)
               ∩ `is_traded`  (参考票 SPY/QQQ/SMH/SOXX 不出块)

    ⚠️ **`status` 不是判据** —— 它是手工字段, 本仓两个方向都出过事
    (`core/holdings` 模块顶部)。旧实现只排除 `status=="closed"` 并要求有 zones,
    于是两类死卡照读:

      ① 移出 moomoo 自选 "Watchlist" 分组的墓碑卡 —— `watchlist_sync` **不删卡**
         (thesis/zones 是资产), 只把 `moomoo_listed` 翻成 false, `status` 一动不动
         仍是 "watching"。 收盘判读实况: 好几张墓碑卡占了整整一栏。
         正是 `is_candidate` docstring 记的那个洞 (消费端手抄判别式) —— 那次收进了
         `is_candidate`, 本模块是漏网的一处:
         `grep -rn moomoo_listed quant/` 的名单里, 八个消费端都查, 只有这里不查。
      ② positions 里 `status=active` 但 qty=0 的残卡 (期权已平/清仓)。

    判读层零授权效力不代表人群可以糊 —— 读一张已经不在书上的图, 与读错一张图
    的代价一样: 它占的是每天那一屏的注意力。
    """
    out: List[str] = []
    try:
        pos_doc = read_json(POSITIONS_PATH) or {}
    except Exception:                                        # noqa: BLE001
        pos_doc = {}
    try:
        wl_doc = read_json(WATCHLIST_PATH) or {}
    except Exception:                                        # noqa: BLE001
        wl_doc = {}

    def _has_zones(body: dict) -> bool:
        return bool(((body.get("agent") or {}).get("zones") or {}).get("accumulation_zone_1"))

    pos_cards = pos_doc.get("tickers") or {}
    for t in live_tickers(pos_doc):
        body = pos_cards.get(t) or {}
        if t.isalpha() and is_managed(body) and _has_zones(body) and t not in out:
            out.append(t)

    for t, body in (wl_doc.get("tickers") or {}).items():
        if t.isalpha() and is_candidate(body) and is_traded(body) and _has_zones(body) and t not in out:
            out.append(t)

    return sorted(out)


HEADER = ("[FYI] 收盘判读 — 观察层，零授权效力\n"
          "链条 Context → Structure → Price Action → Volume → Indicators → Trade\n"
          "两种模式：🔁 抄底（空头衰竭：次日未新低 / 长下影，止损 = 止跌低）｜ 🚀 趋势确认（站稳 gate ×N + 量腿）")


# ══════════════════════════════════════════════════════════════════
# ① Context
# ══════════════════════════════════════════════════════════════════

def _chg(bars: List[dict], n: int = 1) -> Optional[float]:
    if len(bars) <= n:
        return None
    a, b = float(bars[-1 - n]["close"]), float(bars[-1]["close"])
    return round((b / a - 1) * 100, 2) if a else None


def _aligned_ret(bars: List[dict], bench: List[dict], n: int) -> Optional[float]:
    """按日期对齐后的 n 日相对收益（个股 − 基准，百分点）。

    对齐是调用方的事：两条序列日期错位会让相对强度全线失真
    （`technical.rs_series` docstring 的同一条警告）。
    """
    bd = {str(b.get("time", ""))[:10]: float(b["close"]) for b in bench}
    common = [d for d in (str(b.get("time", ""))[:10] for b in bars) if d in bd]
    if len(common) <= n:
        return None
    sc = {str(b.get("time", ""))[:10]: float(b["close"]) for b in bars}
    d0, d1 = common[-1 - n], common[-1]
    if not (sc.get(d0) and bd.get(d0)):
        return None
    return round(((sc[d1] / sc[d0]) - (bd[d1] / bd[d0])) * 100, 2)


_STALE_CACHE: Dict[Optional[str], Dict[str, str]] = {}


def earnings_stale_cards(asof: Optional[str] = None) -> Dict[str, str]:
    """哪些卡的锚**早于自己的财报反应日**→ {TICKER: 原因}。

    **复用既有判据**`core.earnings.cards_stale_after_print`（brief 的
    `INFRA:card_stale_earnings` 用的同一份）—— 不重新实现，零新自由度。

    为什么判读层要管这个：判读读的是**卡片的 zone / gate / regime**。
    卡锚停在财报之前 = 这张图**不含反应日的信息**，而反应日常常是 ±10% 的一根。
    此时照常打印「next gate X」会显得很确定，但那是一张已知过期的地图
    —— **不说出来就是本仓最熟的那种静默**。
    """
    if asof in _STALE_CACHE:
        return _STALE_CACHE[asof]
    out: Dict[str, str] = {}
    try:
        rows = earn.cards_stale_after_print(
            read_json(POSITIONS_PATH) or {}, read_json(WATCHLIST_PATH) or {},
            read_json(EARNINGS_CALENDAR_PATH) or {},
            today=asof or _et_today())
        for line in rows or []:
            out[str(line).split()[0]] = str(line)
    except Exception:                                    # noqa: BLE001
        pass                                             # 日历缺失/接口变动 → 不阻断判读
    _STALE_CACHE[asof] = out
    return out


def _et_today() -> str:
    from quant.core.timeutil import et_today  # noqa: PLC0415
    return et_today()


_BREADTH_CACHE: Dict[Optional[str], Dict] = {}


def _tail_jsonl(path: Path, n: int) -> List[dict]:
    """只读文件尾部 n 行 —— 宽度要扫 167 个深度文件，整读会把 1.3 秒的全书跑变成几十秒。"""
    try:
        with path.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            block = min(size, n * 220 + 4096)      # 每行 ~200B，留余量
            f.seek(size - block)
            lines = f.read().decode("utf-8", "ignore").splitlines()
    except OSError:
        return []
    out = []
    for line in lines[-n:]:
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def market_breadth(asof: Optional[str] = None) -> Dict:
    """市场宽度代理 —— **只播报数值，不设阈值，不判定**（DoF = 0）。

    起步版**刻意不设阈值** —— 一旦给它一条线，它就从观察层变成会改仓位的东西，
    那要走预注册。

    🚨 **两个口径必须播报，否则就是「全池固定阈值 = 没有阈值」的同款错误**：
      ① `book` = 这本书自己 —— 样本极小，但**它才是我们的暴露**；
      ② `pool` = `klines_deep` 去相关池（~167 票）—— **不是任何指数的成分**，
         是为回测去相关自己挑的，所以它是**有偏代理**，不是「市场」；
         而且 `klines_deep` 不入 git、**无刷新 SLA** ⇒ 必须报 as_of。
    """
    if asof in _BREADTH_CACHE:
        return _BREADTH_CACHE[asof]
    out: Dict = {"book": None, "pool": None, "pool_asof": None, "pool_stale_n": 0}

    def pct_above(paths, need: int = 60) -> tuple[Optional[float], int, Optional[str]]:
        above = total = 0
        newest: Optional[str] = None
        for p in paths:
            rows = _tail_jsonl(p, need + 5)
            if asof:
                rows = [r for r in rows if str(r.get("time", ""))[:10] <= asof]
            closes = [float(r["close"]) for r in rows if r.get("close")]
            if len(closes) < 50:
                continue
            ma = technical.sma(closes, 50)[-1]
            if not ma:
                continue
            total += 1
            above += 1 if closes[-1] > ma else 0
            d = str(rows[-1].get("time", ""))[:10]
            newest = d if newest is None or d > newest else newest
        return (round(above / total * 100, 1) if total else None), total, newest

    book = book_tickers()
    if book:
        pct, n, _ = pct_above([KLINE_CACHE_DIR / f"{t}.jsonl" for t in book])
        out["book"] = {"pct_above_50dma": pct, "n": n}
    deep = sorted(KLINE_DEEP_DIR.glob("*.jsonl"))
    if deep:
        pct, n, newest = pct_above(deep)
        out["pool"] = {"pct_above_50dma": pct, "n": n}
        out["pool_asof"] = newest
    _BREADTH_CACHE[asof] = out
    return out


def context_block(ticker: str, bars: List[dict], asof: Optional[str]) -> Dict:
    bench = benchmark_for(ticker)
    out: Dict = {"benchmark": bench, "index": {}, "rs_20d": None, "vix": None}
    for idx in ("QQQ", "SPY", bench):
        ib, _ = load_bars(idx, asof)
        if ib:
            out["index"][idx] = {"chg_1d": _chg(ib, 1), "chg_5d": _chg(ib, 5)}
    bb, _ = load_bars(bench, asof)
    if bb:
        out["rs_20d"] = _aligned_ret(bars, bb, RS_WINDOW)
    vb, _ = load_bars("VIX", asof)
    if vb:
        out["vix"] = round(float(vb[-1]["close"]), 2)
        out["vix_chg"] = _chg(vb, 1)
    out["breadth"] = market_breadth(asof)
    return out


# ══════════════════════════════════════════════════════════════════
# ② Structure
# ══════════════════════════════════════════════════════════════════

def structure_block(ticker: str, bars: List[dict], card: Optional[Dict]) -> Dict:
    closes = [float(b["close"]) for b in bars]
    atr20 = technical.atr(bars, 20)[-1]
    daily = technical.swing_structure(bars)
    weekly_bars = technical.resample_weekly(bars)
    weekly = technical.swing_structure(weekly_bars) if len(weekly_bars) >= 12 else None

    out: Dict = {
        "close": round(closes[-1], 2), "atr20": round(atr20, 2) if atr20 else None,
        "daily": {"trend": daily["trend"], "labels": f"{daily['high_label'] or '?'}+{daily['low_label'] or '?'}",
                  "last_swing_high": daily["last_swing_high"],
                  "last_swing_low": daily["last_swing_low"]},
        "weekly": ({"trend": weekly["trend"],
                    "labels": f"{weekly['high_label'] or '?'}+{weekly['low_label'] or '?'}"}
                   if weekly else None),
        "regime": None, "next_gate": None, "zone_position": None, "walls": {},
    }
    if weekly and weekly["trend"] != "insufficient" and daily["trend"] != "insufficient":
        out["timeframe_conflict"] = weekly["trend"] != daily["trend"]
    # 短期道氏：四步状态机（core/dow）。快照标签只有「最近两个摆动点」且不记序列 ——
    # 看不见「一周内处在趋势终止的第几步」。
    out["dow"] = dow_block(ticker, bars, atr20)

    # 卡片：regime / gate / zone 位置（**只读，不判定授权**）
    if card:
        agent = card["agent"]
        cr = agent.get("current_regime") or {}
        out["regime"] = {"name": cr.get("name"), "cap_pct_nav": cr.get("cap_pct_nav"),
                         "since": cr.get("since"), "source": cr.get("source"),
                         "bucket": card["bucket"]}
        for g in st.gates_of(agent):
            if g["kind"] == "up":
                lvl = coerce_float(g.get("level"))
                if lvl:
                    out["next_gate"] = {
                        "level": lvl, "regime": g.get("regime"),
                        "dist_pct": round((lvl / closes[-1] - 1) * 100, 2),
                        "dist_n": round((lvl - closes[-1]) / atr20, 2) if atr20 else None,
                    }
                break
        zb = st.zone_bands(agent.get("zones"))
        if zb:
            out["zone_position"] = st.zone_position(closes[-1], zb)
            out["zones"] = zb

    # gate 的结构身份（描述性标签，不携带授权含义）
    if out["next_gate"] and atr20 and len(closes) >= 180:
        ma60 = technical.sma(closes, 60)
        eps = episodes_of(closes, ma60)
        broke, _, _ = breakdown_in_window(eps, closes, len(closes) - 1,
                                          out["next_gate"]["level"], BROKE_WINDOW)
        out["next_gate"]["path"] = gate_path(broke, daily["trend"],
                                             out["next_gate"]["level"],
                                             max(closes), DELTA_ATR * atr20)

    # 上下最近的墙（轴 A：这条线上有没有"货"）
    out["walls"] = walls_block(bars, closes[-1], atr20)
    out["dow"]["line_support"] = dow_line_support(out["dow"], out["walls"].get("all", []), atr20)
    return out


def _gap_of(level: Optional[float], close: float, atr20: Optional[float]) -> Optional[Dict]:
    if level is None or not close:
        return None
    return {"level": level, "pct": round((level / close - 1) * 100, 2),
            "n": round(abs(level - close) / atr20, 2) if atr20 else None}


def dow_block(ticker: str, bars: List[dict], atr20: Optional[float]) -> Dict:
    """短期道氏结构（`core/dow` 状态机）→ 判读视图：阶段 + 要看的线（带距离）+ 事件日的力。

    回答「现在处在趋势终止四步的第几步、下一条要看的线在哪、离现价多远」。
    量只做**限定词**（破坏日 / 终止日的 RelVR，反弹腿 vs 回调腿的均量比），不驱动阶段
    —— 阶段由价格序列给，这与「力果不携带方向」同一条纪律。
    """
    state = dow.structure_state(bars)
    close = float(bars[-1]["close"])
    view: Dict = {k: state.get(k) for k in (
        "trend", "phase", "stage_cn", "labels", "bars_in_phase", "candidate",
        "leg_volume", "obs_start", "last_resolution", "imp", "ret", "obs", "cnt",
        "retrace", "swings")}
    view["retrace_at_form"] = state.get("retrace_at_form")
    view["lines"] = {k: _gap_of(v, close, atr20) for k, v in (state.get("watch") or {}).items()}
    # 嵌套第二级：同一台机器跑**走完的**周 K（resample_weekly 丢未完成周 = 无前视）。
    # 道氏本来就是 primary / secondary / minor 三层；只有一层时「终止」分不清回调与反转。
    wk_bars = technical.resample_weekly(bars)
    wk = dow.structure_state(wk_bars) if len(wk_bars) >= 10 else None
    view["weekly"] = ({"trend": wk["trend"], "phase": wk["phase"], "stage_cn": wk["stage_cn"],
                       "labels": wk["labels"], "watch": wk["watch"],
                       "last_resolution": wk["last_resolution"]}
                      if wk and wk.get("trend") else None)
    view["nested"] = nested_reading(view)
    view["effort"] = {}
    n = len(bars)
    for e in (state.get("events") or [])[-4:]:
        if e["type"] in ("obs1", "obs2", "obs3", "terminate", "resume") \
                and n - 1 - e["idx"] <= 2 * RECENT_BARS:
            val, _, _ = rel_volume(bars[:e["idx"] + 1], ticker)
            if val is not None:
                r = round(val, 2)
                view["effort"][e["type"]] = {"date": e["date"], "relvr": r,
                                             "label": effort_label(r)}
    res = view.get("last_resolution")
    view["recent_resolution"] = res if (res and res["bars_ago"] <= RECENT_BARS) else None
    return view


def nested_reading(dv: Dict) -> Optional[Dict]:
    """两级合读：日线级在观察期 / 刚终止时，周线级处在哪一步决定「回调还是反转」的先验。"""
    wk = dv.get("weekly")
    if not wk or not dv.get("trend"):
        return None
    active = dv.get("phase", 0) >= 1 or (dv.get("recent_resolution") or {}).get("type") == "terminate"
    wt, wp = wk["trend"], wk["phase"]
    up = dv["trend"] == "up"
    wk = {**wk, "stage_cn": wk["stage_cn"].split("（")[0]}      # 嵌套句里只要短名
    if not active:
        return {"key": "quiet", "cn": f"周线级 {wk['stage_cn']}"}
    if up:
        if wt == "up" and wp in (0, 1):
            return {"key": "pullback_prior",
                    "cn": (f"周线级仍在{'推进' if wp == 0 else '刚回调'}（{wk['stage_cn']}）→ 日线级的终止"
                           "优先按回调/洗盘假设复核")}
        if wt == "up":
            return {"key": "both_obs",
                    "cn": (f"周线级也在观察期（{wk['stage_cn']}）→ 两级同向收缩，日线级终止分量更重："
                           "等待两级各自确认线")}
        return {"key": "higher_down",
                "cn": (f"周线级已是下跌（{wk['stage_cn']}）→ 日线级终止 = 大级别的延续："
                       "优先按趋势延续假设复核")}
    # 日线级在下跌里做止跌观察 / 刚反转：镜像读法。
    if wt == "down" and wp in (0, 1):
        return {"key": "bounce_prior",
                "cn": f"周线级仍在{'下跌推进' if wp == 0 else '刚反弹'}（{wk['stage_cn']}）→ 日线级的止跌先验是反弹，不是反转"}
    if wt == "down":
        return {"key": "both_obs",
                "cn": f"周线级也在止跌观察（{wk['stage_cn']}）→ 两级同向，日线级反转确认分量更重"}
    return {"key": "higher_up",
            "cn": f"周线级已是上涨（{wk['stage_cn']}）→ 日线级的止跌 = 大级别回调结束候选"}


LINE_TOL_ATR = 0.25     # 道氏线 ↔ 墙的邻域 = cluster_pivots 同口径 0.25N


def dow_line_support(dv: Dict, walls_all: List[Dict], atr20: Optional[float]) -> Dict:
    """把道氏的线（价格一维）与有 depth 的线（pivot 簇 / HVN / LVN）对上 ——
    标志性价位可能与 pivot/HVN/LVN 收敛；这里只标「谁挨着谁」，不改任何阶段。"""
    out: Dict = {}
    if not dv or not dv.get("trend") or not atr20 or not walls_all:
        return out
    up = dv["trend"] == "up"
    roles = (("obs", "lsl" if up else "lsh"), ("imp", "lsh" if up else "lsl"),
             ("cnt", "LH" if up else "HL"), ("ret", "HL" if up else "LH"))
    for key, name in roles:
        pt = dv.get(key)
        if not pt or pt.get("price") is None:
            continue
        near = [w for w in walls_all if abs(w["level"] - pt["price"]) <= LINE_TOL_ATR * atr20]
        if near:
            w = min(near, key=lambda x: abs(x["level"] - pt["price"]))
            out[name] = {"price": pt["price"], "wall": w["level"], "kind": w["kind"],
                         "detail": w["detail"],
                         "gap_n": round(abs(w["level"] - pt["price"]) / atr20, 2)}
    return out


def _band_line(dv: Dict) -> Optional[str]:
    rf = dv.get("retrace_at_form")
    if not rf:
        return None
    return f"回撤带 {rf['band']}（{rf['band_cn']}，lsl 成形时回撤 {rf['pct']:.0%}）"


def _g(gp: Optional[Dict]) -> str:
    """距离尾巴：（+6.5%/0.5N）—— 让「守 HL 127.5」自明。"""
    if not gp:
        return ""
    n = f"/{gp['n']}N" if gp.get("n") is not None else ""
    return f"（{gp['pct']:+.1f}%{n}）"


def _res_mark(res: Dict, trend: Optional[str]) -> str:
    if res["type"] == "resume":
        return "↩"
    return "🔻" if trend == "down" else "🔺"       # terminate 之后的新趋势方向


def _retrace_note(dv: Dict) -> Optional[str]:
    """回撤深度（相对最后一段冲动）：整段吐回 / 整段收复单独标；过 2/3 = Rhea 次级折返上界。

    方向要写死在动词里：
    上涨里是**回调跌破**前 HL（上一段涨幅整段吐回），下跌里是**反弹站上**前 LH
    （上一段跌幅整段收复）—— 一个「破」字两个方向，读的人猜不出来。
    """
    r = dv.get("retrace")
    if not r:
        return None
    up = dv["trend"] == "up"
    move, leg = ("回调", "涨幅") if up else ("反弹", "跌幅")
    ln = dv["lines"].get("prev_ret")
    lvl = f" {ln['level']:g}" if ln else ""
    if r["broke_prev_ret"]:
        return (f"{move}已跌破前 HL{lvl}（回调 >100%，上一段涨幅整段吐回）" if up
                else f"{move}已站上前 LH{lvl}（反弹 >100%，上一段跌幅整段收复）")
    if r["pct"] > 2 / 3:
        return f"{move}已达最后一段{leg}的 {r['pct']:.0%}（过 2/3 = Rhea 次级折返上界）"
    return f"{move} = 最后一段{leg}的 {r['pct']:.0%}"


def dow_lines(dv: Optional[Dict]) -> List[str]:
    """详情页的短期道氏段 —— 每条 = 线 + 语义 + 距离。"""
    if not dv or not dv.get("trend"):
        return [f"短期道氏 {(dv or {}).get('stage_cn', '?')}"]
    up = dv["trend"] == "up"
    ln = dv["lines"]
    L = [f"短期道氏 {dv['stage_cn']}　[{dv['labels']}]　本阶段 {dv['bars_in_phase']} 根"]
    if dv["phase"] == 0:
        L.append(f"　{'lsh' if up else 'lsl'} {ln['imp_level']['level']:g}{_g(ln['imp_level'])} 刷新中；"
                 f"一根{'阴' if up else '阳'}线收{'破' if up else '上'} {ln['trigger_ref']['level']:g}"
                 f"（前一根{'低' if up else '高'}点）= 进入{'终止' if up else '止跌'}观察①"
                 + (f"；前 {'HL' if up else 'LH'} {ln['prev_ret']['level']:g}{_g(ln['prev_ret'])}"
                    if ln.get("prev_ret") else ""))
    else:
        ob = dv.get("obs_start")
        if ob:
            eff = (dv["effort"] or {}).get("obs1")
            L.append(f"　破坏 {ob['date']} 收 {ob['close']:g} {'<' if up else '>'} 前一根"
                     f"{'低' if up else '高'}点 {ob['broke']:g}"
                     + (f"　当日 RelVR {eff['relvr']}={eff['label']}" if eff else ""))
        L.append(f"　{'收复' if up else '跌破'} {ln['resume_level']['level']:g}"
                 f"{_g(ln['resume_level'])} = "
                 + ("结构恢复（洗盘后延续上涨）" if up else "下跌恢复（反弹失败，延续下跌）"))
        if ln.get("terminate_level"):
            L.append(f"　{'跌破' if up else '站上'} {ln['terminate_level']['level']:g}"
                     f"{_g(ln['terminate_level'])} = "
                     + ("趋势终止确认（LH+LL）→ 提前离场的裁量点" if up
                        else "下跌终止确认（HL+HH）"))
        if dv["phase"] == 3 and ln.get("cnt_level"):
            L.append(f"　{'LH' if up else 'HL'} {ln['cnt_level']['level']:g}{_g(ln['cnt_level'])} 已定格"
                     f"（再出{'阳' if up else '阴'}线收{'上' if up else '破'}前一根"
                     f"{'高' if up else '低'}点 = {'反弹' if up else '回踩'}重启）")
        cand = dv.get("candidate")
        if cand:
            L.append(f"　候选 {cand['role']} {cand['price']:g}（{cand['date']}）：{cand['confirm_if']}")
        rn = _retrace_note(dv)
        if rn:
            L.append("　" + rn)
        lv = dv.get("leg_volume")
        if lv:
            tag = ("缩量（需求弱）" if lv["ratio"] <= EFFORT_LO else
                   "放量" if lv["ratio"] >= EFFORT_HI else "量能相当")
            if not up:
                tag = tag.replace("需求弱", "供给弱")
            L.append(f"　{'反弹' if up else '回踩'}量/{'回调' if up else '反弹'}量 {lv['ratio']}"
                     f"（{lv['attempt_bars']}/{lv['retrace_bars']} 根）→ "
                     f"{'反弹' if up else '回踩'}{tag}")
    bl = _band_line(dv)
    if bl:
        L.append("　" + bl)
    res = dv.get("recent_resolution")
    if res:
        eff = (dv["effort"] or {}).get(res["type"])
        L.append(f"　{_res_mark(res, dv['trend'])} {res['date']} {res['cn']}（{res['bars_ago']} 根前）"
                 + (f"　当日 RelVR {eff['relvr']}={eff['label']}" if eff else ""))
    nested = dv.get("nested")
    if nested:
        L.append("　" + nested["cn"])
    sup = dv.get("line_support") or {}
    if dv["phase"] >= 1 or dv.get("recent_resolution"):
        if sup:
            L.append("　线上有货：" + "；".join(
                f"{k} {v['price']:g} ← {v['kind']} {v['wall']:g}（{v['gap_n']}N，{v['detail']}）"
                for k, v in sup.items()))
        else:
            L.append(f"　道氏线 ±{LINE_TOL_ATR:g}N 内无墙 —— 真空里的线，证据只有价格一维")
    return L


def walls_block(bars: List[dict], spot: float, atr20: Optional[float]) -> Dict:
    """现价上下最近的一堵墙。三类锚同源于本仓既有实现，不新造。"""
    if len(bars) < PROFILE_BARS or not atr20:
        return {}
    atr14 = technical.atr(bars, 14)[-1]
    bucket = technical.auto_bucket(spot, atr14)
    prof = technical.volume_profile(bars[-PROFILE_BARS:], bucket)
    va = technical.value_area(prof)
    cands: List[Dict] = []
    for b in top_hvn(prof, 3):
        cands.append({"level": round((b["lo"] + b["hi"]) / 2, 2), "kind": "HVN",
                      "detail": f"{b['share_pct']:.1f}% 成交堆积"})
    for b in find_lvn(prof, bucket)[:3]:
        cands.append({"level": round((b["lo"] + b["hi"]) / 2, 2), "kind": "LVN",
                      "detail": "低量/真空带（回踩滑过，不是无阻力）"})
    sh, sl = technical.swing_points(bars[-PIVOT_BARS:], 2)
    pv = ([{"price": p, "date": str(bars[-PIVOT_BARS:][i].get("time", ""))[:10], "kind": "high"}
           for i, p in sh]
          + [{"price": p, "date": str(bars[-PIVOT_BARS:][i].get("time", ""))[:10], "kind": "low"}
             for i, p in sl])
    for c in cluster_pivots(pv, atr20 * 0.25):
        if c["touches"] >= 3:
            cands.append({"level": c["center"], "kind": "pivot",
                          "detail": f"×{c['touches']} [{c['kinds']}] 末次 {c['last_touch']}"})
    near = [c for c in cands if abs(c["level"] - spot) <= NEAR_ATR * atr20]
    above = sorted([c for c in near if c["level"] > spot], key=lambda c: c["level"])
    below = sorted([c for c in near if c["level"] <= spot], key=lambda c: -c["level"])
    fmt = lambda c: {**c, "dist_n": round(abs(c["level"] - spot) / atr20, 2)}  # noqa: E731
    out = {"above": [fmt(c) for c in above[:2]], "below": [fmt(c) for c in below[:2]],
           "all": [fmt(c) for c in near]}
    if va:
        # 拍卖理论的坐标系：区内 = 平衡（均值回复语义）/ 区外 = 失衡（趋势语义）
        out["value_area"] = {
            **va,
            "where": ("区内" if va["val"] <= spot <= va["vah"]
                      else ("区上(失衡·趋势语义)" if spot > va["vah"] else "区下(失衡·趋势语义)")),
        }
    return out


# ══════════════════════════════════════════════════════════════════
# ③ Price Action
# ══════════════════════════════════════════════════════════════════

def price_action_block(bars: List[dict], atr20: Optional[float]) -> Dict:
    last = bars[-1]
    h, l, c, o = (float(last["high"]), float(last["low"]),
                  float(last["close"]), float(last["open"]))
    rng = h - l
    prev_c = float(bars[-2]["close"]) if len(bars) >= 2 else None
    out: Dict = {
        "date": str(last.get("time", ""))[:10],
        "close": round(c, 2), "chg_pct": _chg(bars, 1),
        "range_n": round(rng / atr20, 2) if atr20 else None,
        "close_pos": round((c - l) / rng, 2) if rng > 0 else None,
        "body_pct": round(abs(c - o) / rng * 100) if rng > 0 else None,
        "gap_pct": (round((o / prev_c - 1) * 100, 2) if prev_c else None),
    }
    cp = out["close_pos"]
    out["close_label"] = ("强收" if cp is not None and cp >= CLOSE_STRONG else
                          "弱收" if cp is not None and cp <= CLOSE_WEAK else "中性收")
    # 上/下影线相对实体 —— 只作为**观测特征**，不作为独立信号
    if rng > 0:
        upper, lower = h - max(o, c), min(o, c) - l
        out["wick"] = {"upper_pct": round(upper / rng * 100), "lower_pct": round(lower / rng * 100)}
    # 近 10 日轨迹：只报**可机械描述**的三件事
    win = bars[-11:] if len(bars) >= 11 else bars
    ups = sum(1 for i in range(1, len(win)) if float(win[i]["close"]) > float(win[i - 1]["close"]))
    out["last10"] = {
        "up_days": ups, "down_days": len(win) - 1 - ups,
        "net_pct": _chg(bars, min(10, len(bars) - 1)),
        "highest_close": round(max(float(b["close"]) for b in win), 2),
        "lowest_close": round(min(float(b["close"]) for b in win), 2),
    }
    return out


# ══════════════════════════════════════════════════════════════════
# ④ Volume（力 × 果 = 2×2，方向不由这一格给）
# ══════════════════════════════════════════════════════════════════

def volume_block(ticker: str, bars: List[dict], pa: Dict) -> Dict:
    val, note, degraded = rel_volume(bars, ticker)
    out: Dict = {"relvr": round(val, 2) if val else None, "note": note,
                 "degraded": degraded}
    if val is None:
        return out
    # ⚠️ 用**播出去的那个数**分档，不用原始值：RelVR 1.1997 显示成 1.2 却标「中力」，
    # 读者只能看到「1.2 = 中力」这个自相矛盾的组合（本仓「显示与判定两条线」的小型版）。
    val = out["relvr"]
    effort = effort_label(val)
    rn = pa.get("range_n")
    result = ("大果" if rn is not None and rn >= RESULT_HI else
              "小果" if rn is not None and rn <= RESULT_LO else "中果")
    cell = {
        ("高力", "大果"): "驱动 —— Wyckoff SOS/SOW；Osler 的止损级联观测面",
        ("高力", "小果"): "吸收 / 派发候选 —— 力大果小（Wyckoff 法则3 的背离格）",
        ("低力", "大果"): "真空移动 —— 穿过 LVN，「轻松」≠「强」，回踩无库存接",
        ("低力", "小果"): "平衡 —— no demand / no supply",
    }.get((effort, result), "中间态（不落格）")
    out.update({"effort": effort, "result": result, "cell": cell})
    return out


# ══════════════════════════════════════════════════════════════════
# ⑤ Indicators（一轴一票，不堆）
# ══════════════════════════════════════════════════════════════════

def indicator_block(bars: List[dict]) -> Dict:
    closes = [float(b["close"]) for b in bars]
    dev = technical.ma_deviation(closes, 20)[-1]
    out: Dict = {
        "ma20_dev_pct": round(dev, 2) if dev is not None else None,
        "axis_note": "轴 B′ 一票（RSI / 布林%B / 20日区间位置 与它 ρ≈0.9，不另计）",
        "range_contraction": technical.range_contraction(bars, 10, 14),
    }
    rc = out["range_contraction"]
    out["vol_state"] = (None if rc is None else
                        "收缩" if rc < 0.85 else "扩张" if rc > 1.15 else "平")
    try:
        rsi_d = technical.detect_rsi_divergence(bars)
        macd_d = technical.detect_macd_divergence(bars)
        hit = bool((rsi_d or {}).get("detected")) or bool((macd_d or {}).get("detected"))
        out["divergence"] = {
            "detected": hit,
            "note": "动量背离 = **一票**（rsi_div~macd_div 共现提升 ×7.25 = 同一件事）",
        }
    except Exception:                                        # noqa: BLE001
        out["divergence"] = None
    ma = {p: technical.sma(closes, p)[-1] for p in (20, 60, 200)}
    out["ma_stack"] = {f"MA{p}": (round(v, 2) if v else None) for p, v in ma.items()}
    out["above_ma"] = [f"MA{p}" for p, v in ma.items() if v and closes[-1] > v]
    return out


# ══════════════════════════════════════════════════════════════════
# ⑥ 两种模式
# ══════════════════════════════════════════════════════════════════

def _pct_n(level: Optional[float], close: float, atr: Optional[float]) -> str:
    """「距现价 −3.2%/1.2N」—— 线到现价的有符号距离（线在下为负）。"""
    if level is None or not close:
        return ""
    pct = (level / close - 1) * 100
    n = f"/{abs(level - close) / atr:.1f}N" if atr else ""
    return f"{pct:+.1f}%{n}"


def bottom_block(bars: List[dict], atr20: Optional[float], dv: Optional[Dict]) -> Dict:
    """模式 1 抄底：`core/setups.bottom_setup` + 周线级先验 + 探针 [FYI] + 墙。"""
    b = setups.bottom_setup(bars)
    close = float(bars[-1]["close"])
    b["dist"] = {
        "low": _pct_n(b.get("low"), close, atr20),
        "bounce_high": _pct_n(b.get("bounce_high"), close, atr20),
        "pullback_low": _pct_n(b.get("pullback_low"), close, atr20),
        "lh": _pct_n(b.get("lh"), close, atr20),
    }
    b["probe"] = setups.probe_marker(bars, atr20) if b.get("listed") else None
    wk = (dv or {}).get("weekly")
    if wk:
        wph = "推进中" if wk["phase"] == 0 else f"观察{'①②③'[wk['phase'] - 1]}"
        b["weekly_cn"] = f"周线级{TREND_CN[wk['trend']]}·{wph}"
        b["weekly_prior"] = ("反弹先验" if wk["trend"] == "down" and wk["phase"] <= 1 else
                             "两级共振" if wk["trend"] == "down" else
                             "大级别回调结束候选")
    else:
        b["weekly_cn"], b["weekly_prior"] = None, None
    return b


def _last_up_transition(agent: Optional[Dict]) -> Optional[Dict]:
    hist = ((agent or {}).get("regime_history") or [])
    for h in reversed(hist):
        if str(h.get("direction")) == "up" and h.get("date"):
            return h
    return None


def _bars_since(bars: List[dict], day: str) -> Optional[int]:
    """`day` 之后有几根完成日线（0 = day 就是最后一根）。day 不在序列内 → 按日期比较。"""
    d = str(day)[:10]
    later = sum(1 for b in bars if str(b.get("time", ""))[:10] > d)
    if not any(str(b.get("time", ""))[:10] <= d for b in bars):
        return None
    return later


def trend_setup(ticker: str, agent: Optional[Dict], bars: List[dict], atr20: Optional[float],
                gate_cls: Optional[str] = None) -> Dict:
    """模式 2 趋势确认：卡片的转移轴（`next_regime.entry_gate`）走到哪一步。

    状态（`core/setups.TREND_STATES`）：
      transitioned  最近一次上行转移 ≤ RECENT_BARS 根前（已形成；卡上的 gate 已是下一道）
      standing      连续收盘 > gate ≥ N 日但量腿未过（价腿成立、转移未发生）
      counting      1 ≤ streak < N（⏳ m/n）
      near          收盘在 gate 之下 ≤ 1N（即将形成）
    量腿 / streak / 快车道全部读 `regime_transition` 与 `gate_ladder_report` 同一份函数，
    本模块不另造。无卡 / 无转移轴 → {"state": None}。
    """
    out: Dict = {"state": None, "gate": None}
    if not agent or not bars:
        return out
    close = coerce_float(bars[-1].get("close"))
    entry = next((g for g in ladder_of(agent) if g.get("kind") == "entry"), None)
    last_up = _last_up_transition(agent)
    if last_up:
        since = _bars_since(bars, last_up["date"])
        if since is not None and since <= RECENT_BARS:
            out.update({"state": "transitioned", "since_bars": since,
                        "level": coerce_float(last_up.get("level")),
                        "date": str(last_up.get("date"))[:10],
                        "fastlane": bool(last_up.get("fastlane")),
                        "reason": str(last_up.get("reason") or "")[:80]})
    if entry:
        lvl = float(entry["level"])
        need = int(entry.get("days") or 2)
        streak = streak_above(bars, lvl)
        prox = setups.gate_proximity(close, atr20, lvl, streak, need)
        vol_ok, vol_note = (volume_leg(bars, entry.get("vol_min"), ticker, streak=streak)
                            if streak >= 1 else (None, None))
        tag = PATH_TAGS.get(gate_cls, entry.get("tag") or "收复线")
        out["gate"] = {"level": lvl, "need": need, "streak": streak, "tag": tag,
                       "label": entry.get("label"), "vol_min": entry.get("vol_min"),
                       "vol_ok": vol_ok, "vol_note": vol_note,
                       "dist_pct": prox["dist_pct"] if prox else round((lvl / close - 1) * 100, 2) if close else None,
                       "dist_n": prox["dist_n"] if prox else (round((lvl - close) / atr20, 2) if (atr20 and close) else None)}
        if streak == 1 and atr20 and close is not None:
            out["gate"]["fastlane"] = fastlane_state(bars, close, atr20, entry, 1, ticker, gate_cls)
        if out["state"] is None and prox:
            out["state"] = prox["state"]
    if out["state"]:
        meta = setups.TREND_STATES[out["state"]]
        out.update(cn=meta["cn"], formed=meta["formed"], rank=meta["rank"])
    return out


def _next_step(b: Dict) -> str:
    """抄底框架里「下一根要看什么」—— 阶段 → 确认 / 证伪条件。"""
    stg = b.get("stage")
    if stg == "watch":
        return f"明日不再新低 = ✓止跌（止损 = 今日低 {b['low']:g}）；再新低 = 框架顺延到新低"
    if stg == "formed":
        hh, hl = bool(b.get("above_lh")), bool(b.get("higher_low"))
        want = []
        if not hh and b.get("lh"):
            want.append(f"收盘越前高 {b['lh']:g}")
        if not hl:
            want.append("回踩低不破止跌低且次日未再新低 = 更高的低点")
        tail = "；".join(want) if want else "两个反转事实都已成立 → 此后看 gate（模式 2）"
        return f"{tail}；收破止跌低 {b['low']:g} = ✗反弹结束"
    return "—"


def setups_lines(r: Dict) -> List[str]:
    """详情页的「⑥ 两种模式」段。"""
    b, t = r["setups"]["bottom"], r["setups"]["trend"]
    L: List[str] = []
    # 抄底
    if b.get("listed"):
        L.append(f"🔁 抄底 {b['cn']}（{'已形成' if b['formed'] else '即将形成'}）"
                 f"　止跌低 {b['low']:g}（{b['low_date'][5:]}，{b['dist']['low']}）= 止损（下影线低点）"
                 f"　距 60 日高 {b['dd_at_low']:+.0%}@低 / {b['dd_now']:+.0%}@现价")
        sh = b.get("shadow") or {}
        shadow_txt = (f"下影 {sh['lower_pct']}% / 实体 {sh['body_pct']}% / 收盘位 {sh['close_pos']}"
                      if sh.get("lower_pct") is not None else "影线不可算")
        if b.get("stage") == "dead":
            L.append(f"　　{b['why']}")
        elif b.get("stage") == "watch":
            L.append(f"　　今日 K 线：{shadow_txt}（不是长下影）")
            L.append("　　下一步：" + _next_step(b))
        else:
            L.append(f"　　衰竭证据 {b['signal']}（信号日 {b['signal_date'][5:]}，{b['bars_since_signal']} 根前）"
                     f"　止跌日 K 线 {shadow_txt}"
                     + (f"　量比 {b['low_bar_vol_ratio']}" if b.get("low_bar_vol_ratio") is not None else "")
                     + f"　收盘{'已' if b.get('above_low_bar_high') else '未'}站上止跌日高 {b['low_bar_high']:g}")
            L.append(f"　　反弹 {b['bounce_pct']:+.1%}：反弹高 {b['bounce_high']:g}（{b['bounce_high_date'][5:]}，{b['dist']['bounce_high']}）"
                     + (f"　回踩低 {b['pullback_low']:g}（{b['pullback_low_date'][5:]}，{b['dist']['pullback_low']}"
                        + (f"，更高的低点 {str(b.get('higher_low_date') or '')[5:]} 守住" if b.get("higher_low") else "，回踩进行中")
                        + "）" if b.get("pullback_low") else "")
                     + (f"　前高 {b['lh']:g}（{b['lh_date'][5:]}，{b['dist']['lh']}）" if b.get("lh") else "　前高无（60 日内无分形高）"))
            L.append("　　下一步：" + _next_step(b))
        if b.get("undercut_prev"):
            L.append(f"　　⚠️ {b['undercut_prev']['cn']} —— 止损在下影线低点的仓位盘中已被扫，归执行层裁")
        if b.get("verdict"):
            L.append(f"　　反弹 vs 反转：{b['verdict']}"
                     + (f"　｜ {b['weekly_cn']}（{b['weekly_prior']}）" if b.get("weekly_cn") else ""))
        if b.get("probe"):
            L.append(f"　　[FYI] {b['probe']} —— 授权层的反转探针信号（PLAN/VETO 看 entry_guard）")
    else:
        why = b.get("why") or setups.STAGES[b["stage"]]["desc"]
        L.append(f"🔁 抄底：不在形态内（{why}）")
    # 趋势确认
    g = t.get("gate")
    if t.get("state"):
        head = f"🚀 趋势确认 {t['cn']}（{'已形成' if t['formed'] else '即将形成'}）"
        if t["state"] == "transitioned":
            head += (f"　{t['date'][5:]} 站稳 {t['level']:g}（{t['since_bars']} 根前"
                     + ("，⚡快车道" if t.get("fastlane") else "") + "）")
        L.append(head)
    else:
        L.append("🚀 趋势确认：不在形态内（无转移轴或 gate 在 1N 之外）" if not g else
                 f"🚀 趋势确认：不在形态内（gate {g['level']:g} 距 {g['dist_pct']:+.1f}%/{g['dist_n']}N，>1N）")
    if g:
        st_txt = (f"✅ {g['streak']}/{g['need']}" if g["streak"] >= g["need"] else
                  f"⏳ {g['streak']}/{g['need']}" if g["streak"] >= 1 else "▫️ 0/" + str(g["need"]))
        vol = ("" if g["vol_ok"] is None else
               f"　量腿 {'✓' if g['vol_ok'] else '✗'}" + (f"（{g['vol_note']}）" if g.get("vol_note") else ""))
        L.append(f"　　{'下一' if t.get('state') == 'transitioned' else ''}gate {g['level']:g} {g['tag']}"
                 f"　{st_txt}　距 {g['dist_pct']:+.1f}%/{g['dist_n']}N{vol}")
        fl = g.get("fastlane")
        if fl and fl.get("domain") == "repair":
            L.append(f"　　⚡快车道 首日超 {fl['over_n']:.2f}N（门槛 0.5N）"
                     f" 幅度{'✓' if fl['mag_ok'] else '✗'} 量腿{'✓' if fl['vol_ok'] else '✗'}")
    return L


# ══════════════════════════════════════════════════════════════════
# 装配 + 渲染
# ══════════════════════════════════════════════════════════════════

def read(ticker: str, asof: Optional[str] = None) -> Dict:
    t = ticker.upper()
    bars, merge_note = load_bars(t, asof)
    if len(bars) < 30:
        return {"ticker": t, "error": f"日线不足（{len(bars)} 根）"}
    card = load_card(t)
    atr20 = technical.atr(bars, 20)[-1]
    struct = structure_block(t, bars, card)
    struct["_ticker"] = t
    ctx = context_block(t, bars, asof)
    struct["_rs"] = ctx.get("rs_20d")
    pa = price_action_block(bars, atr20)
    vol = volume_block(t, bars, pa)
    ind = indicator_block(bars)
    bottom = bottom_block(bars, atr20, struct.get("dow"))
    trend = trend_setup(t, (card or {}).get("agent"), bars, atr20,
                        gate_cls=((struct.get("next_gate") or {}).get("path")))
    return {
        "ticker": t, "asof": pa["date"], "bars": len(bars),
        # ⚠️ `--asof` 只截断**价格**，卡片读的仍是**今天**那张（zones/regime/gate 都是现在的值）。
        # 拿今天的卡去跑过去的 bar = 前视，只能当「输出面长什么样」的样张，**不能当证据**。
        "card_stale_earnings": earnings_stale_cards(asof).get(t),
        "lookahead_warning": ("卡片是**今天**那张（zones/regime/gate 未回溯）= 前视，"
                              "复盘样张可用，作证据不可用") if asof else None,
        "merge_note": merge_note, "context": ctx,
        "structure": struct, "price_action": pa, "volume": vol,
        "indicators": ind,
        "setups": {"bottom": bottom, "trend": trend},
        "disclaimer": DISCLAIMER,
    }


DISCLAIMER = ("以上是判读，不是授权。能不能下、下多少，"
              "以 entry_guard / 卡片 zones / sizing.entry_cap 为准。")

TREND_CN = {"up": "上涨", "down": "下跌", "transition": "震荡", "insufficient": "方向未定"}


def render(r: Dict) -> str:
    if r.get("error"):
        return f"── {r['ticker']}　{r['error']}"
    L: List[str] = []
    pa, s, v, i = r["price_action"], r["structure"], r["volume"], r["indicators"]
    L.append(f"━━━ {r['ticker']}　{r['asof']} 收盘 ${pa['close']}（{pa['chg_pct']:+.2f}%） ━━━")
    if r.get("merge_note"):
        L.append(f"　⚠️ {r['merge_note']}（序列不连续，MA60 可能失真）")
    if r.get("lookahead_warning"):
        L.append(f"　🚫 {r['lookahead_warning']}")
    if r.get("card_stale_earnings"):
        L.append(f"　🚨 **卡片待重画（财报 SLA）**：{r['card_stale_earnings']}")
        L.append("　　→ 下面的 gate / regime 来自**不含反应日的旧图**，重画前只当价格读数看")

    c = r["context"]
    idx = "　".join(f"{k} {vv['chg_1d']:+.2f}%(5d {vv['chg_5d']:+.1f}%)"
                    for k, vv in c["index"].items() if vv.get("chg_1d") is not None)
    rs = (f"　RS{RS_WINDOW}d vs {c['benchmark']} {c['rs_20d']:+.1f}pp"
          f"（{'领先' if c['rs_20d'] > 0 else '落后'}）" if c.get("rs_20d") is not None else "")
    L.append(f"① Context　{idx}　VIX {c.get('vix')}{rs}")
    br = c.get("breadth") or {}
    if br.get("book") or br.get("pool"):
        bits = []
        if br.get("book") and br["book"]["pct_above_50dma"] is not None:
            bits.append(f"本书 {br['book']['pct_above_50dma']:.0f}% 站上MA50 (n={br['book']['n']})")
        lag = ""
        if br.get("pool_asof") and r.get("asof") and br["pool_asof"] < r["asof"]:
            import datetime as _dt
            try:
                d = (_dt.date.fromisoformat(r["asof"])
                     - _dt.date.fromisoformat(br["pool_asof"])).days
                lag = f" 🚫落后{d}天 —— 这是 {br['pool_asof']} 的宽度，不是今天的"
            except ValueError:
                lag = " 🚫as_of 不可解析"
        if br.get("pool") and br["pool"]["pct_above_50dma"] is not None:
            bits.append(f"深度池 {br['pool']['pct_above_50dma']:.0f}% (n={br['pool']['n']})"
                        + lag)
        L.append("　　宽度〔观察层·无阈值〕" + "　".join(bits))
        L.append("　　⚠️ 深度池**不是任何指数的成分**（为回测去相关自选），是有偏代理；"
                 "且不入 git、无刷新 SLA")

    wk = f"周线 {s['weekly']['labels']} {s['weekly']['trend']}" if s.get("weekly") else "周线样本不足"
    L.append(f"② Structure　日线 {s['daily']['labels']} {s['daily']['trend']}　|　{wk}"
             + ("　⚠️分歧" if s.get("timeframe_conflict") else ""))
    L.extend("　　" + x for x in dow_lines(s.get("dow")))
    if s.get("regime"):
        rg = s["regime"]
        L.append(f"　　regime {rg['name']}　since {rg['since']}　[{rg['bucket']}]")
    if s.get("next_gate"):
        g = s["next_gate"]
        L.append(f"　　next gate {g['level']}（{g['dist_pct']:+.1f}% / {g['dist_n']}N，"
                 f"身份 {g.get('path', '未判')}）")
    va = (s.get("walls") or {}).get("value_area")
    if va:
        L.append(f"　　价值区 {va['val']}–{va['vah']}（{va['covered_pct']:.0f}% 成交 / "
                 f"POC {va['poc_lo']}–{va['poc_hi']}）　现价**{va['where']}**")
    for side, arrow in (("above", "头顶"), ("below", "脚下")):
        for wl in (s.get("walls") or {}).get(side, []):
            L.append(f"　　{arrow} {wl['level']}（{wl['kind']}, {wl['dist_n']}N）{wl['detail']}")

    L.append(f"③ 行为　　range {pa['range_n']}N　收盘位 {pa['close_pos']} {pa['close_label']}"
             f"　实体 {pa['body_pct']}%"
             + (f"　gap {pa['gap_pct']:+.1f}%" if pa.get("gap_pct") else "")
             + (f"　上影 {pa['wick']['upper_pct']}%/下影 {pa['wick']['lower_pct']}%"
                if pa.get("wick") else ""))
    l10 = pa["last10"]
    L.append(f"　　近10日 {l10['up_days']}涨/{l10['down_days']}跌　净 {l10['net_pct']:+.1f}%"
             f"　区间 {l10['lowest_close']}–{l10['highest_close']}")

    L.append(f"④ 量　　　RelVR {v.get('relvr')}　{v.get('effort', '?')}×{v.get('result', '?')}"
             f" → {v.get('cell', '?')}")
    if v.get("degraded"):
        L.append(f"　　⚠️ 降级：{v['note']}")

    L.append(f"⑤ 指标　　离MA20 {i['ma20_dev_pct']:+.1f}%"
             f"　站上 {'/'.join(i['above_ma']) or '无'}"
             f"　波动 {i.get('vol_state') or '?'}({i.get('range_contraction')})"
             + ("　动量背离 ✓" if (i.get("divergence") or {}).get("detected") else ""))
    L.append(f"　　{i['axis_note']}")

    L.append("⑥ 两种模式（判读层，零授权效力）")
    L.extend("　" + x for x in setups_lines(r))
    L.append("　" + r["disclaimer"])
    return "\n".join(L)


# ══════════════════════════════════════════════════════════════════
# 推送：两种模式清单（只列这两种形态已形成或即将形成的标的）
# ══════════════════════════════════════════════════════════════════

BOTTOM_TABLE_HEADER = ("| 票 | 止跌 | 止跌低=stop | 反弹 | 前高 | 反弹/反转 |",
                       "|---|---|---|---|---|---|")
TREND_TABLE_HEADER = ("| 票 | 状态 | gate | 计数 | 量腿 | 距离 |",
                      "|---|---|---|---|---|---|")


def _cell(x) -> str:
    """表格单元格: 竖线转义 (tg_rich 认 `\\|`), 不许有换行。"""
    return str(x).replace("|", "\\|").replace("\n", " · ").strip()


def _row(cells) -> str:
    return "| " + " | ".join(_cell(c) for c in cells) + " |"


def bottom_row(r: Dict) -> str:
    """抄底表一行 = 一只票：止跌（证据 + 信号日）/ 止跌低（= stop）/ 反弹（幅度 + 反弹高 + 回踩低）/
    前高 / 反弹 vs 反转（+ 周线级先验 + 探针 FYI）。"""
    who = f"**{r['ticker']}**" + ("  🚨待重画" if r.get("card_stale_earnings") else "")
    b = r["setups"]["bottom"]
    sh = b.get("shadow") or {}
    if b["stage"] == "formed":
        stage = f"{b['cn']} {b['signal_date'][5:]} {b['signal']}"
        if sh.get("lower_pct") is not None:
            stage += f"(下影{sh['lower_pct']}%)"
        stage += " · 收上止跌日高" if b.get("above_low_bar_high") else " · 未收上止跌日高"
    elif b["stage"] == "dead":
        stage = f"{b['cn']} 收{b['close']:g}<{b['low']:g}"
    else:
        stage = f"{b['cn']}" + (f"(下影{sh['lower_pct']}%)" if sh.get("lower_pct") is not None else "")
    if b.get("undercut_prev"):
        stage += f" ⚠️盘中破{b['undercut_prev']['low']:g}收回"
    low = f"{b['low']:g} ({b['dist']['low']})"
    if b["stage"] == "formed":
        bh = f"{b['bounce_pct']:+.1%} · 高 {b['bounce_high']:g}"
        if b.get("pullback_low"):
            bh += f" · 回踩低 {b['pullback_low']:g}" + (
                f"↑{str(b.get('higher_low_date') or '')[5:]}" if b.get("higher_low") else "")
    else:
        bh = "—"
    lh = f"{b['lh']:g} ({b['dist']['lh']})" if b.get("lh") else "—"
    verdict = b.get("verdict") or ("明日不再新低=止跌" if b["stage"] == "watch" else "")
    if b.get("weekly_cn"):
        verdict += f" ｜ {b['weekly_cn']}"
    if b.get("probe"):
        verdict += f" ｜ {b['probe']}"
    return _row((who, stage, low, bh, lh, verdict))


def trend_row(r: Dict) -> str:
    """趋势确认表一行：状态 / gate（线名）/ 计数 m/n / 量腿 / 距离。"""
    who = f"**{r['ticker']}**" + ("  🚨待重画" if r.get("card_stale_earnings") else "")
    t = r["setups"]["trend"]
    g = t.get("gate") or {}
    state = t["cn"]
    if t["state"] == "transitioned":
        state += f" {t['date'][5:]}@{t['level']:g}" + ("⚡" if t.get("fastlane") else "")
    gate = "—"
    if g:
        same = (t["state"] == "transitioned" and t.get("level") is not None
                and g["level"] <= float(t["level"]))
        gate = (f"{g['level']:g} {g['tag']}（卡上仍是刚过的线，待重画）" if same
                else f"{'下一 ' if t['state'] == 'transitioned' else ''}{g['level']:g} {g['tag']}")
    cnt = (f"{g['streak']}/{g['need']}" if g else "—")
    vol = ("—" if not g or g.get("vol_ok") is None else
           ("✓" if g["vol_ok"] else "✗") + (f" {g['vol_note'].rsplit(' ', 1)[-1]}" if g.get("vol_note") else ""))
    dist = (f"{g['dist_pct']:+.1f}%/{g['dist_n']}N" if g and g.get("dist_n") is not None else "—")
    fl = g.get("fastlane") if g else None
    if fl and fl.get("domain") == "repair" and fl.get("first_day"):
        dist += f" ⚡{fl['over_n']:+.2f}N"
    return _row((who, state, gate, cnt, vol, dist))


def _bottom_key(r: Dict):
    """✓止跌 → ⏳今日新低 → ✗；✓ 内最新的信号在前（最新鲜的止跌最可操作），同日票名序。"""
    b = r["setups"]["bottom"]
    return (b["rank"], b.get("bars_since_signal") or 0, r["ticker"])


def _trend_key(r: Dict):
    t = r["setups"]["trend"]
    return (t["rank"], r["ticker"])


def digest(reports: List[Dict], asof: Optional[str] = None) -> str:
    """**唯一那条推送**：两张表（🔁 抄底 / 🚀 趋势确认），只列已形成或即将形成的票；
    其余票名单独一行（dead-man：证明全书扫过，没有静默丢票）。"""
    ok = [r for r in reports if not r.get("error")]
    errs = [r for r in reports if r.get("error")]
    bottoms = sorted((r for r in ok if r["setups"]["bottom"].get("listed")), key=_bottom_key)
    trends = sorted((r for r in ok if r["setups"]["trend"].get("state")), key=_trend_key)
    listed = {r["ticker"] for r in bottoms} | {r["ticker"] for r in trends}
    rest = sorted(r["ticker"] for r in ok if r["ticker"] not in listed)
    L = [f"## [FYI] 收盘判读 {asof or ''} · 两种模式 · 判读层，零授权效力".replace("  ", " "),
         "> 🔁 抄底 = 近 20 日低距 60 日高 ≥10%，空头衰竭：✓止跌 = 次日未新低 或 止跌日长下影（已形成）；⏳今日新低 = 明日不再新低即止跌（即将形成）；"
         "✗ = 收破止跌低。止跌低 = 下影线低点 = 止损。反弹/反转 = 两个事实：越前高（该段下跌最后一个 lower high）/ 回踩更高低点",
         "> 🚀 趋势确认 = 收盘站上卡片 gate ×N + 量腿 RelVR≥1.1 → 转移；只列 ≤5 根内已转移 / 站稳量腿未过 / 计数中 / gate 之下 ≤1N。"
         "已形成 = ✓已转移·✅站稳；即将形成 = ⏳▫️",
         ""]
    L.append(f"### 🔁 抄底（{len(bottoms)}）")
    if bottoms:
        L.extend(BOTTOM_TABLE_HEADER)
        L.extend(bottom_row(r) for r in bottoms)
    else:
        L.append("无")
    L.append("")
    L.append(f"### 🚀 趋势确认（{len(trends)}）")
    if trends:
        L.extend(TREND_TABLE_HEADER)
        L.extend(trend_row(r) for r in trends)
    else:
        L.append("无")
    L.append("")
    L.append(f"其余 {len(rest)} 票不在两种形态内（不列）：{'、'.join(rest) if rest else '—'}")
    for r in errs:
        L.append(f"⚠️ {r['ticker']} {r['error']}")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description="每日收盘后判读：两种模式（抄底 / 趋势确认）")
    ap.add_argument("--tickers", nargs="*", help="默认 = 全书")
    ap.add_argument("--book", action="store_true", help="全书（有 zones 的活跃正股卡）")
    ap.add_argument("--asof", help="截断到该交易日（复盘用；不传 = 缓存最后一根完成日线）")
    ap.add_argument("--digest", action="store_true",
                    help="只出两种模式的清单（推 TG 的那一条）")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    tickers = [t.upper() for t in (a.tickers or [])] or book_tickers()
    if not tickers:
        print("没有可判读的标的（全书为空？）", file=sys.stderr)
        return 1
    reports = [read(t, a.asof) for t in tickers]
    if a.json:
        print(json.dumps({"header": HEADER, "reports": reports},
                         ensure_ascii=False, indent=2))
    elif a.digest:
        print(digest(reports, a.asof or (reports[0].get("asof") if reports else None)))
    else:
        print(HEADER + "\n")
        print("\n\n".join(render(r) for r in reports))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
