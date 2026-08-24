#!/usr/bin/env python3
"""chart_read.py — **每日收盘后判读**：结构 / 形态 / 潜在机会。

════════════════════════════════════════════════════════════════════════
🎯 核心目的（用户  逐字定版 —— 这是本模块存在的唯一理由）
════════════════════════════════════════════════════════════════════════

    「每天收盘后 你根据 k 线 交易量 指标等，分析出当前所处结构及形态，
      例如这可能是底部 这可能见顶 这会继续延续 这后续会震荡
      这会形成双顶 楔形 头肩形态，然后进而分析出潜在的交易机会」

    链条（用户 给的图）:
        Context → Structure → Price Action → Volume → Indicators → Trade

本模块的输出 = 上面六步各出一段**事实**，第七段出**形态假设**（带证伪条件与
先验强度），第八段指出**潜在机会在哪个既有闸门上**。

**改动本模块前先问**：它是否让「每天收盘后看懂这只票现在处在什么结构里」
这件事做得更好？不是「是否更严谨/更全面/更像个系统」。

════════════════════════════════════════════════════════════════════════
🚨 边界：判读层，零授权效力（`tests/report/test_chart_read.py` 钉它）
════════════════════════════════════════════════════════════════════════

  - **不写任何 state**（positions/watchlist/exit_guards/heartbeat-state 一个都不碰）；
  - **不产出 cap / 股数 / 下单价 / 档位**；「机会」段只能**指向**既有闸门
    （`entry_guard` / 卡片 zones / gate），不自己算；
  - 形态永远以「假设 + 确认条件 + 证伪条件 + 先验强度」四件套出现，
    不以「这是顶 / 这是底」出现。Lo (2000) 自己的限制句：形态提供
    **信息含量**，「does not necessarily imply … excess trading profits」。

这与 v3 分工同构：**授权归结构机器，判断权归 用户，agent 供弹药**。

════════════════════════════════════════════════════════════════════════
数据纪律
════════════════════════════════════════════════════════════════════════

  - 只吃**已完成日线**（`market/klines/` + `market/klines_deep/` 合并去重）。
    盘中跑 = 未完成 bar 当收盘，本仓栽过（`heartbeat.md` §盘中运行，NBIS 7/28）。
    → `--asof` 可显式截断到某个交易日，用于复盘与测试。
  - 一切读数**归一化**（ATR 尺 / 相对量比 / 百分比），不用全池固定阈值
    （`portfolio_brief` 固定 5% 的教训：全池固定阈值 = 没有阈值）。
  - 指标**一轴一票**：`landing_plan` §4 的实测 —— RSI / 布林%B / MA20 偏离 /
    20 日区间位置 ρ≈0.89–0.95 = 同一个读数四个名字；rsi_div~macd_div 共现提升 ×7.25。
    所以这里**只报一个**「离均值多远」与**一个**「动量背离」，不堆四个装共振。

Usage:
    python3 quant/report/chart_read.py --book                 # 全书
    python3 quant/report/chart_read.py --tickers COHR NBIS
    python3 quant/report/chart_read.py --tickers COHR --asof  --json
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 直接 `python3 quant/<层>/<模块>.py` 时让 `quant.*` 可 import

import argparse  # noqa: E402
import json  # noqa: E402
from typing import Dict, List, Optional, Tuple  # noqa: E402

from quant.core import earnings as earn  # noqa: E402
from quant.core import dow, patterns, technical  # noqa: E402
from quant.core import structure as st  # noqa: E402
from quant.core.episodes import (BROKE_WINDOW, DELTA_ATR, breakdown_in_window,  # noqa: E402
                                 episodes_of, gate_path, merge_bars_by_date)
from quant.core.holdings import live_tickers  # noqa: E402
from quant.core.io_json import iter_jsonl, read_json  # noqa: E402
from quant.core.num import coerce_float  # noqa: E402
from quant.core.paths import (EARNINGS_CALENDAR_PATH, KLINE_CACHE_DIR,  # noqa: E402
                              KLINE_DEEP_DIR, POSITIONS_PATH, WATCHLIST_PATH)
from quant.core.sectors import benchmark_for  # noqa: E402
from quant.decision.level_map import cluster_pivots, find_lvn, top_hvn  # noqa: E402
from quant.decision.regime_transition import rel_volume  # noqa: E402

PROFILE_BARS = 60        # 与卡片画线同口径 (level_map / volume_profile 现行 60 根)
PIVOT_BARS = 250         # pivot 簇回看
RS_WINDOW = 20           # 相对强度窗口 (交易日)
NEAR_ATR = 3.0           # 只报现价 ±3N 内的墙 (再远今天交互不到)

# 力/果的格子边界 —— 与 price_action_framework_design §7.2 同值, 且
# close_pos 与 tape_check 的 60m 口径对齐 (0.40/0.70), 不另立一套。
EFFORT_HI, EFFORT_LO = 1.2, 0.8
RESULT_HI, RESULT_LO = 1.0, 0.6
CLOSE_WEAK, CLOSE_STRONG = 0.40, 0.70

# 「最近」= 一个交易周的短期结构窗口。
# 短期道氏的终止/恢复只在这个窗口内进「后续倾向」与「潜在机会」，再早的是历史。
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
    不得遮真源（2026-08-06 INTC 显示已平仓旧 zone 的同型 bug）。"""
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
    """全书 = 有 zones 的活跃正股卡（持仓 + watchlist），与简报同一个人群。"""
    out: List[str] = []
    for path in (POSITIONS_PATH, WATCHLIST_PATH):
        try:
            doc = read_json(path) or {}
        except Exception:                                    # noqa: BLE001
            continue
        for t, body in (doc.get("tickers") or {}).items():
            if (body.get("status") or "active") in ("closed",):
                continue
            if ((body.get("agent") or {}).get("zones") or {}).get("accumulation_zone_1"):
                if t.isalpha() and t not in out:
                    out.append(t)
    return sorted(out)


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
    此时照常打印「zone位置 zone1 / next gate 231」会显得很确定，
    但那是一张已知过期的地图 —— **不说出来就是本仓最熟的那种静默**。
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

    landing plan §5 阶段 3：Context 是九节主链里唯一真弱的一节，而宽度是它的核心读数。
    起步版**刻意不设阈值**—— 一旦给它一条线，它就从观察层变成会改仓位的东西，
    那要走预注册（§2 的 DoF 预算）。跑 ≥8 周再谈。

    🚨 **两个口径必须播报，否则就是「全池固定阈值 = 没有阈值」的同款错误**：
      ① `book` = 这本书自己（12 张活跃卡）—— 样本极小，但**它才是我们的暴露**；
      ② `pool` = `klines_deep` 去相关池（~167 票）—— **不是任何指数的成分**，
         是为回测去相关自己挑的，所以它是**有偏代理**，不是「市场」；
         而且 `klines_deep` 不入 git、**无刷新 SLA**（BACKLOG 已登记）⇒ 必须报 as_of。
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
    # 短期道氏：四步状态机（core/dow）。快照标签只有「最近两个摆动点」且不记序列，
    # 形态语法最小尺度 35 根 —— 两者都看不见「一周内处在趋势终止的第几步」。
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
    st = dow.structure_state(bars)
    close = float(bars[-1]["close"])
    view: Dict = {k: st.get(k) for k in (
        "trend", "phase", "stage_cn", "labels", "bars_in_phase", "candidate",
        "leg_volume", "obs_start", "last_resolution", "imp", "ret", "obs", "cnt",
        "retrace", "swings")}
    view["retrace_at_form"] = st.get("retrace_at_form")
    view["lines"] = {k: _gap_of(v, close, atr20) for k, v in (st.get("watch") or {}).items()}
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
    for e in (st.get("events") or [])[-4:]:
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
    # （Bulkowski: 长上影 59% ≈ 近随机 rank 55/103；Marshall 等对整类 K 线负面）
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
# ⑦ 判读 + ⑧ 机会
# ══════════════════════════════════════════════════════════════════

def synthesize(struct: Dict, pa: Dict, vol: Dict, ind: Dict, pats: List[Dict]) -> Dict:
    """机械判读：把六步事实压成两三句。**不加入任何本函数之外的信息。**

    这一段是 `heartbeat` 推 `fired_cards` 原文那条规矩的同款：会被人当结论读的
    文本必须由代码渲染，叙述者只能在它**之上**追加，不能替代它。
    """
    lines: List[str] = []
    d, w = struct["daily"]["trend"], (struct["weekly"] or {}).get("trend")
    tf = {"up": "上升", "down": "下降", "transition": "混合(HH+LL 类)",
          "insufficient": "样本不足"}
    if w:
        lines.append(f"结构：日线{tf.get(d, d)} / 周线{tf.get(w, w)}"
                     + ("　⚠️ 两级分歧 —— 大周期没转，小周期先动（或反之）"
                        if struct.get("timeframe_conflict") else "　两级同向"))
    else:
        lines.append(f"结构：日线{tf.get(d, d)}（周线样本不足）")
    dv = struct.get("dow") or {}
    if dv.get("trend"):
        lines.append("短期道氏：" + dv["stage_cn"]
                     + (f"　{_res_mark(dv['recent_resolution'], dv['trend'])} {dv['recent_resolution']['date']} "
                        f"{dv['recent_resolution']['cn']}" if dv.get("recent_resolution") else ""))

    zp = struct.get("zone_position")
    if zp:
        lines.append("位置：" + {"zone1": "在吸纳带 z1 内", "zone2": "在吸纳带 z2 内",
                                "trim": "在减仓带内", "waiting": "等待区（z1 与 trim 之间）",
                                "above_no_chase": "已越过不追价上限",
                                "gap_z1_z2": "两条吸纳带之间",
                                "below_zones": "在全部吸纳带之下"}.get(zp, zp))
    ab = (struct.get("walls") or {}).get("above") or []
    be = (struct.get("walls") or {}).get("below") or []
    if ab or be:
        lines.append("　　头顶 " + (f"{ab[0]['level']}({ab[0]['kind']}, {ab[0]['dist_n']}N)" if ab else "±3N 内无墙")
                     + " / 脚下 " + (f"{be[0]['level']}({be[0]['kind']}, {be[0]['dist_n']}N)" if be else "±3N 内无墙"))

    if vol.get("cell"):
        lines.append(f"力果：{vol['effort']}×{vol['result']} → {vol['cell']}")
        lines.append("　　⚠️ 这一格**不携带方向**—— 方向由位置（在哪条线上）与前置状态给。"
                     "同一根放量在大市值低价差票上偏反转、在小市值高价差票上偏延续（LMSW02）")

    live = [p for p in pats if p["state"] != "invalidated"]
    if live:
        p = live[0]
        lines.append(f"形态假设：{p['cn']}（{patterns.STATE_CN[p['state']]}）"
                     f"　[{p['evidence']}]")
        lines.append(f"　　确认 = {p['confirm_if']}")
        lines.append(f"　　证伪 = {p['invalidate_if']}")
    else:
        lines.append(f"形态假设：{patterns.summarize(pats)}")
    return {"lines": lines}


REVERSAL_NAMES = {"HS", "IHS", "DTOP", "DBOT", "RWEDGE", "FWEDGE"}
RANGE_NAMES = {"RTOP", "RBOT", "TTOP", "TBOT"}


def posture(struct: Dict, pa: Dict, vol: Dict, ind: Dict, pats: List[Dict]) -> Dict:
    """**「后续会怎么走」的三个候选，各自列出支持它的观测 —— 不给概率。**

    这一段直接回答核心目的里的「这会继续延续 / 这后续会震荡 / 这可能见顶」。

    🚨 **为什么没有概率**：给概率就要有校准，我们从来没校准过这三类的先验。
    本仓已经为「未经校准的分数」付过一次学费 —— `sss_backtest` 的连续评分
    在 123,576 个样本上非但没有单调性，高分带还轻微反向。所以这里给的是
    **支持观测的清单**，让 用户 自己权衡；三栏可以同时有勾，那不是矛盾，
    是「这只票现在确实处在一个两可的位置」的诚实表达。
    """
    d = struct["daily"]["trend"]
    w = (struct["weekly"] or {}).get("trend")
    walls = struct.get("walls") or {}
    up_w = (walls.get("above") or [None])[0]
    dn_w = (walls.get("below") or [None])[0]
    live = [p for p in pats if p["state"] in ("shaped", "triggered")]
    cont: List[str] = []
    rng: List[str] = []
    rev: List[str] = []

    if d in ("up", "down"):
        cont.append(f"日线有方向（{d}）")
        if w == d:
            cont.append("周线同向（两级共振）")
    if d == "up" and "MA20" in ind["above_ma"] and "MA60" in ind["above_ma"]:
        cont.append("站上 MA20 与 MA60")
    if d == "down" and not ind["above_ma"]:
        cont.append("MA 全线之下")
    if vol.get("cell", "").startswith("驱动"):
        cont.append("力果落在驱动格（放量走出果）")
    if (struct.get("_rs") or 0) > 0 and d == "up":
        cont.append("相对基准领先")

    if d == "transition":
        rng.append("日线结构混合（HH+LL 类）")
    rc = ind.get("range_contraction")
    if rc is not None and rc < 0.85:
        rng.append(f"波动收缩（{rc}）")
    if up_w and dn_w and up_w["dist_n"] <= 1.0 and dn_w["dist_n"] <= 1.0:
        rng.append(f"上下两堵墙都在 1N 内（{dn_w['level']} / {up_w['level']}）")
    for p in live:
        if p["name"] in RANGE_NAMES:
            rng.append(f"{p['cn']} 已成立（区间/收敛形状）")
    if struct.get("zone_position") in ("waiting", "gap_z1_z2"):
        rng.append("价格在两带之间的无人区")

    for p in live:
        if p["name"] in REVERSAL_NAMES:
            rev.append(f"{p['cn']}（{patterns.STATE_CN[p['state']]}）[{p['evidence']}]")
    if w and d != "insufficient" and w != "insufficient" and w != d:
        rev.append(f"日线({d})与周线({w})分歧")
    if vol.get("cell", "").startswith("吸收") and up_w and up_w["dist_n"] <= 1.0:
        rev.append("力大果小 且 头顶 1N 内有墙（供给吸收候选）")
    if (ind.get("divergence") or {}).get("detected"):
        rev.append("动量背离（rsi_div/macd_div 合并计一票）")
    if d == "up" and pa.get("close_label") == "弱收" and up_w and up_w["dist_n"] <= 0.5:
        rev.append("上升中在墙下弱收")
    if d == "down" and pa.get("close_label") == "强收" and dn_w and dn_w["dist_n"] <= 0.5:
        rev.append("下降中在墙上强收（止跌候选）")

    # 短期道氏四步（core/dow）：推进中 → 延续；观察期 → 反转栏；②③夹在两条线之间 → 震荡栏
    dv = struct.get("dow") or {}
    if dv.get("trend"):
        up = dv["trend"] == "up"
        res = dv.get("recent_resolution")
        if dv["phase"] == 0:
            if dv["trend"] == d:
                cont.append(f"短期道氏推进中（{dv['labels']}，尚无{'阴' if up else '阳'}线破坏）")
            elif d in ("up", "down"):
                rev.append(f"短期道氏序列为{TREND_CN[dv['trend']]}（与 5 根分形快照 {d} 相反）")
        if res and res["type"] == "resume":
            cont.append(f"观察期内{'收复 lsh' if up else '跌破 lsl'}（洗盘后延续，{res['date']}）")
        if dv["phase"] in (2, 3) and dv["lines"].get("terminate_level"):
            rng.append(f"观察期内夹在 {dv['lines']['terminate_level']['level']:g} 与 "
                       f"{dv['lines']['resume_level']['level']:g} 之间")
        if dv["phase"] >= 1:
            rev.append(f"短期道氏 {dv['stage_cn']}")
        if res and res["type"] == "terminate":
            rev.append(f"短期道氏 {res['cn']}（{res['date']}）")
        nk = (dv.get("nested") or {}).get("key")
        if nk in ("pullback_prior", "bounce_prior"):
            cont.append("周线级结构未动（日线级观察 = 回调/洗盘先验）")
        elif nk in ("both_obs", "higher_down", "higher_up"):
            rev.append("周线级同向（两级共振）" if nk == "both_obs" else "周线级已翻面（大级别延续）")

    return {"continuation": cont, "range": rng, "reversal": rev,
            "note": "三栏可同时有勾 = 位置本身两可；**不给概率**（我们没校准过）"}


def _resolved_hypothesis(p: Dict, atr20: Optional[float]) -> str:
    """已破确认线的形态怎么写 —— **已然式 + 破哪边 + 破了多远 + 现在的证伪线**。

    用户  抓：「收盘突破最近高点 211.08 = 向上解决」印在一张收 224.09
    的卡上，读起来像在等一件 12 根前就发生完的事，而且把「往哪边解决的」这个
    唯一有用的信息丢了 —— 容器型形态的方向**只**由破哪边决定，不写就等于没判读。
    """
    lvl, close = p.get("confirm_level"), p.get("last_close")
    side = {"up": "向上", "down": "向下"}.get(p.get("resolved_side") or "", "")
    bits = []
    if close is not None and lvl:
        bits.append(f"收 {close} vs 线 {lvl}")
        if atr20:
            bits.append(f"已走 {abs(close - lvl) / atr20:.1f}N")
    if p.get("bars_since_trigger") is not None:
        bits.append(f"破线 {p['bars_since_trigger']} 根前")
    # 原来那条 invalidate_level 是**破线之前**的证伪条件，破了之后它已经过期
    # （NVDA 三角的 195.44 距现价 -3.8N，印出来是噪音）。破线之后唯一还活着的
    # 证伪就是走回线的另一侧 = 假突破。
    back = "之下" if p.get("resolved_side") == "up" else "之上"
    fail = f"收盘回到 {lvl} {back} = 解决失败（假突破）" if lvl else "走回线的另一侧 = 假突破"
    return (f"已{side}解决" + (f"（{'，'.join(bits)}）" if bits else "")
            + f" —— 已发生，不是待确认条件；{fail}")


def _hypothesis_tail(p: Dict, atr20: Optional[float]) -> str:
    """一个形态的假设正文（**不含形态名**）—— 名字抽出去是为了让共线的两个
    形态能合并成一行，见 `pattern_hypotheses`。"""
    if p["state"] == "triggered":
        return _resolved_hypothesis(p, atr20)
    if p["name"] in patterns.DIRECTIONAL:
        side = "下行" if p["direction"] == "bearish" else "上行"
        return (f"若成立 = {side}方向：{p['confirm_if']}；"
                f"若 {p['invalidate_if']} 则这条假设作废")
    # 容器型形态（矩形/三角/扩散/楔形）方向未定 —— 不许套用名字里的 TOP/BOT
    return f"：方向未定，由突破选边 —— {p['confirm_if']}；{p['invalidate_if']}"


def pattern_hypotheses(pats: List[Dict], atr20: Optional[float] = None) -> List[str]:
    """形态假设三件套：**若成立 = 什么方向 · 确认条件 · 证伪条件**。

    用户  指定这就是扫描表上「潜在机会」要的全部内容 —— 只说假设，
    不说能不能进。两个消费者（详情的 `opportunity` 段 + 扫描表）读同一份，
    抄第二遍的那天两处就会开始各说各话。

    **共线的形态合并成一行**（用户  裁）：AMZN 的收敛三角底与双底
    确认线同为 258.08（同一个 pivot），逐字印两遍在推送里就是噪音 —— 而
    「两个独立形态在同一条线上确认」这件事本身是信息（收敛），值得说出口。
    合并判据 = **正文逐字相同**（不是「线相近」）：正文里已经带了线、方向、
    破线年龄、证伪条件，全等就意味着这两行本来就要说同一句话，合并零信息损失；
    只要有一个字不同（方向不同 / 一个破了一个没破）就不合并。**没有容差参数。**
    分组在截断**之前**做，所以合并腾出的位置能让第三个形态露出来。
    """
    groups: List[Tuple[str, List[Dict]]] = []
    for p in [x for x in pats if x["state"] != "invalidated"]:
        tail = _hypothesis_tail(p, atr20)
        for t, members in groups:
            if t == tail:
                members.append(p)
                break
        else:
            groups.append((tail, [p]))
    out: List[str] = []
    for tail, members in groups[:2]:
        names = " + ".join(m["cn"] for m in members)
        mark = "" if len(members) == 1 else \
            f"（{'两' if len(members) == 2 else len(members)}个形态共线）"
        out.append(f"{names}{mark}{tail}")
    return out


def opportunity(struct: Dict, pats: List[Dict]) -> Dict:
    """潜在机会 —— **只指路，不发牌**。

    这里刻意不算 cap / 股数 / 限价：那是 `entry_guard` 与 `sizing.entry_cap` 的活，
    第二份实现会立刻变成第二个真源（本仓最贵的一类 bug）。
    """
    items: List[str] = []
    reg = struct.get("regime") or {}
    cap = reg.get("cap_pct_nav")
    gate = struct.get("next_gate")
    zp = struct.get("zone_position")

    if gate:
        items.append(f"上行转移轴：{gate['level']}（距 {gate['dist_pct']:+.1f}% / "
                     f"{gate['dist_n']}N，身份 {gate.get('path', '未判')}）"
                     " —— 收盘越过 ×N 日 + 量腿成立 = 整张图上移，不只是 cap 变")
    else:
        items.append("⚠️ 这张卡没有上行转移轴（`next_regime.entry_gate` 缺）"
                     " —— 「没有 gate」= 这张图还没读完，它只能活一轮")

    if zp in ("zone1", "zone2"):
        items.append(f"现价在{zp} —— 入场计划与实际额度跑 "
                     f"`entry_guard.py --tickers {struct.get('_ticker', '<T>')}`（那里才是授权）")
    elif zp == "trim":
        items.append("现价在减仓带 —— 加仓方向的机会不在这里；退出栈告警看 brief")
    elif zp == "above_no_chase":
        items.append("现价已越过不追价上限 —— 追价不是机会，等回踩或等 gate 转移后的新图")

    if cap == 0:
        items.append("⛔ 当前 regime cap 0（无加仓授权）—— 上面的形态判读**不改变这一点**，"
                     "解除只能靠 gate 转移或重画")

    # 短期道氏：终止确认是裁量复核点；观察期播报恢复线和终止线。
    dv = struct.get("dow") or {}
    res = dv.get("recent_resolution")
    if res and res["type"] == "terminate":
        if dv["trend"] == "down":
            items.append(f"🔻 短期道氏：{res['cn']}（{res['date']}）—— "
                         "这是提前复核的裁量点；"
                         "stop/棘轮/趋势死亡线仍是硬线（退出栈看 brief / exit_guard），判读层不替它下裁决")
        else:
            items.append(f"🔺 短期道氏：{res['cn']}（{res['date']}）—— 修复路径的早信号；"
                         "授权仍只看 gate 转移，这条不改变 cap")
    elif dv.get("phase", 0) >= 1:
        ln = dv["lines"]
        tail = (f" / {'终止' if dv['trend'] == 'up' else '反转'}确认线 "
                f"{ln['terminate_level']['level']:g}{_g(ln['terminate_level'])}"
                if ln.get("terminate_level") else " / 等新折返点成形")
        items.append(f"短期道氏观察期{'①②③'[dv['phase'] - 1]}：恢复线 "
                     f"{ln['resume_level']['level']:g}{_g(ln['resume_level'])}" + tail)

    items += pattern_hypotheses(pats, struct.get("atr20"))
    return {"items": items,
            "disclaimer": "以上是判读，不是授权。能不能下、下多少，"
                          "以 entry_guard / 卡片 zones / sizing.entry_cap 为准。"}


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
    pats = patterns.detect_multi(bars)
    return {
        "ticker": t, "asof": pa["date"], "bars": len(bars),
        # ⚠️ `--asof` 只截断**价格**，卡片读的仍是**今天**那张（zones/regime/gate 都是现在的值）。
        # 拿今天的卡去跑过去的 bar = 前视，只能当「输出面长什么样」的样张，**不能当证据**。
        # （design §11.5 注 1 同款：COHR 的 zones 是 8/11 重画的，却被用来读 8/07 的 bar。）
        "card_stale_earnings": earnings_stale_cards(asof).get(t),
        "lookahead_warning": ("卡片是**今天**那张（zones/regime/gate 未回溯）= 前视，"
                              "复盘样张可用，作证据不可用") if asof else None,
        "merge_note": merge_note, "context": ctx,
        "structure": struct, "price_action": pa, "volume": vol,
        "indicators": ind, "patterns": pats,
        "read": synthesize(struct, pa, vol, ind, pats),
        "posture": posture(struct, pa, vol, ind, pats),
        "opportunity": opportunity(struct, pats),
    }


def _gap(level: Optional[float], pa: Dict, s: Dict) -> str:
    """「距现价还有多远」—— 让「未触发」这个词自明。

     用户 当场问「未触发是什么意思」：状态词本身不自明，
    但「确认线 432.50，距现价 +31.6% / 3.1N」是自明的 ——
    **一条 3N 之外的确认线和一条 0.2N 之外的确认线，是完全不同的两件事**，
    而状态词把它们说成同一个「未触发」。
    """
    close, atr = pa.get("close"), s.get("atr20")
    if not level or not close:
        return ""
    pct = (level / close - 1) * 100
    n = f" / {abs(level - close) / atr:.1f}N" if atr else ""
    return f"　〔距现价 {pct:+.1f}%{n}〕"


def render(r: Dict) -> str:
    if r.get("error"):
        return f"── {r['ticker']}　{r['error']}"
    L: List[str] = []
    pa, s, v, i = r["price_action"], r["structure"], r["volume"], r["indicators"]
    L.append(f"━━━ {r['ticker']}　{r['asof']} 收盘 ${pa['close']}"
             f"（{pa['chg_pct']:+.2f}%）　{len(r['patterns'])} 个形态假设 ━━━")
    if r.get("merge_note"):
        L.append(f"　⚠️ {r['merge_note']}（序列不连续，MA60/形态可能失真）")
    if r.get("lookahead_warning"):
        L.append(f"　🚫 {r['lookahead_warning']}")
    if r.get("card_stale_earnings"):
        L.append(f"　🚨 **卡片待重画（财报 SLA）**：{r['card_stale_earnings']}")
        L.append("　　→ 下面的 zone / gate / regime 来自**不含反应日的旧图**，"
                 "重画前只当价格读数看")

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
        L.append(f"　　regime {rg['name']}　cap {rg['cap_pct_nav']}　"
                 f"since {rg['since']}　[{rg['bucket']}]"
                 + (f"　zone位置 {s['zone_position']}" if s.get("zone_position") else ""))
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

    L.append(f"⑥ 形态　　{patterns.summarize(r['patterns'])}")
    for p in r["patterns"]:
        mark = patterns.STATE_CN[p["state"]]
        scale = f"{p['window']}日窗" + ("" if p.get("lo_calibrated") else "·尺度外推")
        L.append(f"　　{mark}　{p['cn']}（{p['name']}）　关键位 {p['key_level']}"
                 f"　{scale}　[{p['evidence']}]")
        L.append(f"　　　　确认 → {p['confirm_if']}{_gap(p.get('confirm_level'), pa, s)}")
        L.append(f"　　　　证伪 → {p['invalidate_if']}{_gap(p.get('invalidate_level'), pa, s)}")
        pts = "　".join(f"{x['date']}@{x['price']}" for x in p["points"])
        L.append(f"　　　　极值 {pts}")
        if p["note"]:
            L.append(f"　　　　注 {p['note']}")

    L.append("━━ 判读 ━━")
    L.extend("　" + x for x in r["read"]["lines"])
    ps = r["posture"]
    L.append("━━ 后续倾向（各栏列支持它的观测；**不给概率**）━━")
    for key, label in (("continuation", continuation_label(s)), ("range", "震荡"),
                       ("reversal", "反转/见顶底")):
        items = ps[key]
        L.append(f"　{label}({len(items)})　" + ("　".join("✓" + x for x in items) if items else "—"))
    L.append(f"　{ps['note']}")
    L.append("━━ 潜在机会（判读层，零授权效力）━━")
    L.extend("　• " + x for x in r["opportunity"]["items"])
    L.append("　" + r["opportunity"]["disclaimer"])
    return "\n".join(L)


TREND_CN = {"up": "上涨", "down": "下跌", "transition": "震荡", "insufficient": "方向未定"}


def continuation_label(struct: Dict) -> str:
    """「延续」延续的是**什么**—— 用户：「延续的话是延续上涨还是
    延续下跌还是延续震荡」。方向不是新判断，就是当前日线趋势本身；
    光写「延续 3」等于让读的人自己回去翻结构。"""
    return "延续" + TREND_CN.get(struct["daily"]["trend"], "方向未定")


def dow_digest_line(dv: Optional[Dict], label: bool = True) -> str:
    """扫描表上的结构行 —— 一行：第几步 + 要看的线 + 距离。

    `label=False` 去掉「结构 」前缀：票头下面只有这一行时，标签就是重复。
    """
    tag = "结构 " if label else ""
    if not dv or not dv.get("trend"):
        return tag + "方向未定（K 线不足）"
    up = dv["trend"] == "up"
    ln, ph = dv["lines"], dv["phase"]
    head = f"{tag}{TREND_CN[dv['trend']]}·"
    if ph == 0:
        body = (f"推进中 {dv['labels']} ｜ {'lsh' if up else 'lsl'} {ln['imp_level']['level']:g}"
                f"{_g(ln['imp_level'])} ｜ {'阴' if up else '阳'}线收{'破' if up else '上'}"
                f"{ln['trigger_ref']['level']:g}=观察①")
    else:
        parts = [f"{'终止观察' if up else '止跌观察'}{'①②③'[ph - 1]}"]
        ob = dv.get("obs_start")
        cand = dv.get("candidate")
        if ph == 1:
            if ob:
                parts.append(f"{ob['date'][5:]}{'阴' if up else '阳'}线破坏")
            if cand:
                parts.append(f"候选{cand['role']}{cand['price']:g}({cand['confirm_if']}即成形)")
            parts.append(f"{'收复' if up else '跌破'}{ln['resume_level']['level']:g}"
                         f"{_g(ln['resume_level'])}=延续")
        elif ph == 2:
            parts.append(f"{'lsl' if up else 'lsh'} {ln['terminate_level']['level']:g} 已成形")
            parts.append(f"{'反弹' if up else '回踩'}看{'lsh' if up else 'lsl'} "
                         f"{ln['resume_level']['level']:g}{_g(ln['resume_level'])}")
            parts.append(f"{'跌破' if up else '站上'}{ln['terminate_level']['level']:g}"
                         f"={'终止确认' if up else '反转确认'}")
        else:
            parts.append(f"{'LH' if up else 'HL'} {ln['cnt_level']['level']:g} 已定格" if ln.get("cnt_level")
                         else ("反弹失败" if up else "回踩守住"))
            parts.append(f"{'跌破' if up else '站上'}{ln['terminate_level']['level']:g}"
                         f"{_g(ln['terminate_level'])}={'终止确认' if up else '反转确认'}")
        r = dv.get("retrace")
        rf = dv.get("retrace_at_form")
        if r and r["broke_prev_ret"]:
            # 动词带方向：上涨 = 回调跌破前 HL；下跌 = 反弹站上前 LH。
            pr = ln.get("prev_ret")
            lvl = f" {pr['level']:g}" if pr else ""
            parts.append(f"回调已跌破前HL{lvl}(整段吐回)" if up else f"反弹已站上前LH{lvl}(整段收复)")
        elif rf:
            parts.append(f"回撤带{rf['band']}")
        body = " ｜ ".join(parts)
    wk = dv.get("weekly")
    if wk:
        nk = (dv.get("nested") or {}).get("key")
        tag = {"pullback_prior": "洗盘先验", "both_obs": "两级共振", "higher_down": "大级别延续",
               "bounce_prior": "反弹先验", "higher_up": "大级别回调结束候选"}.get(nk, "")
        wph = "推进中" if wk["phase"] == 0 else f"观察{'①②③'[wk['phase'] - 1]}"
        body += f" ｜ 周线级{TREND_CN[wk['trend']]}·{wph}" + (f"({tag})" if tag else "")
    res = dv.get("recent_resolution")
    if res:
        body += f"　{_res_mark(res, dv['trend'])}{res['date'][5:]} {res['cn']}"
    return head + body


def digest_block(r: Dict) -> List[str]:
    """一只票在扫描表上的全部内容 = 票头 + **一行短期道氏结构**。

    力果 / 形态 / 倾向 / 机会保留在 `--tickers <T>` 详情页，只有结构留在摘要。
    **收盘价 / 涨跌 / 日周 / 授权 cap / zone 位置照旧不进**
    —— 判读层搬来授权层的东西，只会让人以为这里能下单。
    """
    if r.get("error"):
        return [f"━━ {r['ticker']} ━━", f"  {r['error']}"]
    head = f"━━ {r['ticker']} ━━" + ("  🚨待重画" if r.get("card_stale_earnings") else "")
    return [head, dow_digest_line((r.get("structure") or {}).get("dow"), label=False)]


def _digest_sort_key(r: Dict):
    """顺序 = 与盘前挂单清单**同一把尺** `dow.priority`：优先（上涨推进中 / 洗盘后延续 /
    反转向上已确认）→ 次位（观察期）→ 末位（下跌推进中 / 止跌① / 未定），同档周线级打平局，
    再同则票名。形态距离那套排序随四件退回详情页一起退役 —— 表上只剩结构行，
    按看不见的东西排等于乱序。只排序不授权。"""
    dv = (r.get("structure") or {}).get("dow") or {}
    return (*dow.priority(dv, dv.get("weekly"))["sort_key"], r.get("ticker") or "")


def digest(reports: List[Dict], asof: Optional[str] = None) -> str:
    """摘要推送：每票两行（票头 + 结构行），只保留四步图例与排序说明。"""
    L = [f"[FYI] 收盘判读 {asof or ''} · 判读层，零授权效力".rstrip(),
         "结构 = 短期道氏四步：推进中 → 一根阴线收破前一根低点=观察① 等lsl成形 → ② 看反弹vs lsh → ③ 反弹失败LH 看lsl → 跌破=终止确认；"
         "｜周线级 = 同一台机器跑周K（回调还是反转的先验）；回撤带 = Rhea 1/3–2/3",
         "顺序 = 优先(上涨推进/反转向上已确认) → 次位(观察期) → 末位(下跌)，与盘前清单同尺；只排序不授权"]
    for r in sorted(reports, key=_digest_sort_key):
        L += [""] + digest_block(r)
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description="每日收盘后判读：结构 / 形态 / 潜在机会")
    ap.add_argument("--tickers", nargs="*", help="默认 = 全书")
    ap.add_argument("--book", action="store_true", help="全书（有 zones 的活跃正股卡）")
    ap.add_argument("--asof", help="截断到该交易日（复盘用；不传 = 缓存最后一根完成日线）")
    ap.add_argument("--digest", action="store_true",
                    help="只出一票一行的扫描表（推 TG 的第一条；单条装得下）")
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
