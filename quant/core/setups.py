"""setups.py — 两种交易模式的**判读层**判别式（抄底 / 趋势确认）。零授权效力。

════════════════════════════════════════════════════════════════════════
设计目的（本模块存在的理由）
════════════════════════════════════════════════════════════════════════

    系统只聚焦两种固定的交易模式，其他模式不做：
      1. 抄底：止跌底部确认后做反弹或反转行情，之后根据实际走势判断反弹还是反转
         （反弹尽早了结，反转继续持有）。
      2. 趋势确认：等站稳 gate 后进场。
    盘后判读仅关注这两种形态已形成或即将形成的标的，未形成的不需要关注。

    止跌不用道氏结构判断，只用最基本的价格行为判断空头力量衰竭：
      次日没有继续新低，或收出长下影线；止损放在下影线低点。

    判读层的总目的：每天收盘后，根据 K 线、成交量与指标分析出当前所处结构及形态，
    进而分析出潜在的交易机会 —— 形态只剩上述两种，机会只在这两种形态上。

════════════════════════════════════════════════════════════════════════
模式 1 抄底 `bottom_setup` —— 最基本的价格行为：空头力量衰竭
════════════════════════════════════════════════════════════════════════

  底      顶 = 近 `LOW_LOOKBACK`(20) 根的最高 high（并列取最近）；**L = 顶之后的最低 low**（止跌低 =
          下影线低点 = **本类的 stop**）。顶定了下跌才定，底才定：反弹里的分形高不算顶，回踩不会被当成新底
          （「窗内最低 low」会把更早的旧低当成底；「最近分形高之后的低」会把反弹里的回踩当成底；顶锚两种都对）。
          L 距 60 日高 ≥ `DD_MIN`(10%)，否则不是「跌下来的」，没有底可抄 → none。
  衰竭证据（两条，缺一即可）：
    次日未新低  L 之后至少有一根完成日线且它没有跌破 L（由 L 是窗内最低这个构造保证：
                「L 不是最后一根」就等价于「第二天没有继续新低」）。信号日 = L 的次日。
    长下影      L 那根本身收出长下影 —— 判别式复用 `technical.detect_hammer`
                （下影 > 实体 ×2 且上影 < 实体 ×0.5，锤子线的
                量化口径；本仓已知它没有「下影占区间比」这一维，事实一并播出）。信号日 = L 当日。
  阶段    ⏳今日新低（即将形成）= 今日创 20 日新低且不是长下影 → 「明日不再新低 = 止跌」
          ✓止跌（已形成）      = 任一衰竭证据成立，且此后没有收盘跌破 L
          ✗反弹结束            = 收盘 < 昨日框架的 L（只能从昨日框架判：今日收破则 L 已不是窗内最低）
          盘中破 L 收回（下影再探）= 今日框架的新 L，标 `undercut_prev`；本类止损在下影线低点，
          盘中失守是不是止损归执行层，这里只把事实播出。

  反弹 vs 反转（根据后续实际走势判断）—— 两个最基本的事实，不用道氏四步：
    越前高       收盘 > 前高（L 之前最近一个 5 根分形高 = 该段下跌最后一个 lower high）
    回踩更高低点  反弹高之后的回踩低 P > L，且 P 之后至少一根没再跌破 P（对回踩套同一条
                「次日未新低」）
      反转 = 两者皆有；反转候选 = 其一；反弹 = 皆无（仍在前高之下）。
  这是判读，不是退出指令：「反弹就尽早清仓」的机械口径归 exit_guard，须先过
  `exit_rules_backtest`（改退出参数前须先跑回测）。

  顶锚的已知形状：收盘创 20 日新高 = 下跌已被整段收复 → 框架自然解体（那是模式 2 的世界）；顶滑出 20 日窗
  后框架重锚到窗内新的顶。顶那根自己的 low 也算「顶之后」（一根大阴线反转日既是顶也是底的候选）。

════════════════════════════════════════════════════════════════════════
模式 2 趋势确认 —— 卡片 gate（转移机）的接近度
════════════════════════════════════════════════════════════════════════

  判别式住 `report/chart_read.trend_setup`：它要读卡片 gate 与量腿（decision 层），
  core 不能向上 import。本模块只提供 `gate_proximity`（纯数字：距离 / 计数 → 状态）。

════════════════════════════════════════════════════════════════════════
🚨 判读层，零授权效力（`tests/report/test_chart_read.py` + `tests/core/test_setups.py` 钉）
════════════════════════════════════════════════════════════════════════

  能不能进、进多少永远以 `entry_guard` / 卡片 zones / `sizing.entry_cap` 为准；
  本模块不 import sizing / entry_guard / exit_guard，不读文件，不联网，纯函数。
  授权层的「抄底」目前只有 `core/reversal`（反转探针，）一条窄通道；它与本模块的
  判据不同（一根阳线吃掉 5 日 LH + 20 日新低 + 只开 R1），`probe_marker` 把它的信号
  当 [FYI] 标在抄底行上，两者并列，不互相解释。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from . import reversal
from .technical import detect_hammer, swing_points

# 与反转探针共用同一套窗口词汇（20 日低 / 60 日高 / 10% 回撤门）—— 判读框架与授权信号
# 不各说各话；改 reversal.PARAMS（要重跑回测）时本模块自动跟随。
LOW_LOOKBACK = int(reversal.PARAMS["newlow_lookback"])    # 止跌低 = 近 20 日最低 low
HIGH_LOOKBACK = int(reversal.PARAMS["high_lookback"])     # 回撤参照 = 60 日高
DD_MIN = float(reversal.PARAMS["drawdown_min"])           # L 距 60 日高 ≥10% 才有底可抄
LH_LOOKBACK = HIGH_LOOKBACK      # L 之前找前高的回看窗（同 60 日框架）
SWING_WING = 2                   # 5 根分形（technical.swing_points 缺省, 与 gate 画线同尺）
MIN_BARS = HIGH_LOOKBACK + 1     # 60 日高 + 至少一根: 少于它判不了「跌下来的」

MODE_BOTTOM = "bottom"           # 抄底
MODE_TREND = "trend"             # 趋势确认
MODE_CN = {MODE_BOTTOM: "抄底", MODE_TREND: "趋势确认"}

# 阶段 → 显示名 / 已形成？ / 排序（越小越先）。单一真源，渲染层不许再写一份。
STAGES: Dict[str, Dict] = {
    "formed": {"cn": "✓止跌", "formed": True, "rank": 0,
               "desc": "空头衰竭：止跌低之后未再新低，或止跌日收出长下影；止损 = 止跌低"},
    "watch": {"cn": "⏳今日新低", "formed": False, "rank": 1,
              "desc": "今日创 20 日新低且不是长下影；明日不再新低 = 止跌"},
    "dead": {"cn": "✗反弹结束", "formed": False, "rank": 2,
             "desc": "收盘跌破止跌低，昨日框架作废"},
    "none": {"cn": "无", "formed": False, "rank": 9, "desc": "回撤不足 10%，没有底可抄"},
}
LISTED_STAGES = ("formed", "watch", "dead")     # 进推送的阶段（未形成的不列）
RECENT_SIGNAL_BARS = 10      # ✓止跌 只在信号 ≤10 根内算「刚形成」; 更老的归趋势确认表 / 反弹反转判读
SIGNAL_HAMMER = "长下影"
SIGNAL_HELD = "次日未新低"


def _f(row: dict, key: str) -> float:
    return float(row.get(key) or 0.0)


def _date(bar: dict) -> str:
    return str(bar.get("time") or bar.get("date") or "")[:10]


def shadow_facts(bar: dict) -> Dict:
    """一根 K 线的影线事实（占区间比）—— 给「长下影」判定配上可核对的数字。"""
    o, h, l, c = _f(bar, "open"), _f(bar, "high"), _f(bar, "low"), _f(bar, "close")
    rng = h - l
    if rng <= 0:
        return {"lower_pct": None, "upper_pct": None, "body_pct": None, "close_pos": None}
    return {"lower_pct": round((min(o, c) - l) / rng * 100),
            "upper_pct": round((h - max(o, c)) / rng * 100),
            "body_pct": round(abs(c - o) / rng * 100),
            "close_pos": round((c - l) / rng, 2)}


def last_lower_high(bars: List[dict], i_low: int, lookback: int = LH_LOOKBACK,
                    wing: int = SWING_WING) -> Optional[Dict]:
    """止跌低之前最近一个 5 根分形摆动高（前高）→ {idx, price, date} | None。"""
    lo = max(0, i_low - lookback)
    sh, _ = swing_points(bars[lo:i_low + 1], wing)
    if not sh:
        return None
    j, p = sh[-1]
    return {"idx": lo + j, "price": float(p), "date": _date(bars[lo + j])}


def _frame(bars: List[dict]) -> Dict:
    """一天的抄底框架（无前视：只用传入的 bars）。"""
    n = len(bars)
    if n < MIN_BARS:
        return {"stage": "none", "why": f"K 线不足（{n} 根 < {MIN_BARS}）"}
    hi = max(_f(b, "high") for b in bars[-HIGH_LOOKBACK:])
    close = _f(bars[-1], "close")
    win = range(n - LOW_LOOKBACK, n)
    h_idx = max(win, key=lambda i: (_f(bars[i], "high"), i))       # 顶 = 20 日最高 high (并列取最近)
    i_low = min(range(h_idx, n), key=lambda i: _f(bars[i], "low"))  # 底 = 顶之后的最低 low
    lbar = bars[i_low]
    low = _f(lbar, "low")
    dd_at_low = (low / hi - 1.0) if hi > 0 else 0.0
    out: Dict = {
        "low": low, "low_date": _date(lbar), "low_idx": i_low,
        "top": _f(bars[h_idx], "high"), "top_date": _date(bars[h_idx]),
        "high_60d": hi, "dd_at_low": dd_at_low,
        "dd_now": (close / hi - 1.0) if hi > 0 else 0.0,
        "close": close,
    }
    if -dd_at_low < DD_MIN:
        out.update(stage="none", why=f"20 日低距 60 日高仅 {dd_at_low:+.1%}（<{DD_MIN:.0%}）")
        return out
    lh = last_lower_high(bars, i_low)
    out["lh"] = lh["price"] if lh else None
    out["lh_date"] = lh["date"] if lh else None
    hammer = bool(detect_hammer(lbar))
    held_days = n - 1 - i_low                     # L 之后的完成日线数 (>0 = 次日未新低)
    vols = [_f(b, "volume") for b in bars[max(0, i_low - 20):i_low]]
    avg_vol = (sum(vols) / len(vols)) if vols else 0.0
    out.update({"hammer": hammer, "shadow": shadow_facts(lbar), "held_days": held_days,
                # 事实, 不是判据 (止跌确认的辅助读数):
                "low_bar_high": _f(lbar, "high"),
                "above_low_bar_high": bool(close > _f(lbar, "high")),        # 收盘站上止跌日最高点
                "low_bar_vol_ratio": (round(_f(lbar, "volume") / avg_vol, 2) if avg_vol > 0 else None)})
    if not hammer and held_days == 0:
        out.update(stage="watch", why="今日创 20 日新低；明日不再新低 = 止跌")
        return out
    sigs = ([SIGNAL_HAMMER] if hammer else []) + ([SIGNAL_HELD] if held_days > 0 else [])
    sig_idx = i_low if hammer else i_low + 1
    out.update({
        "stage": "formed",
        "signal": "+".join(sigs),
        "signal_date": _date(bars[sig_idx]), "bars_since_signal": n - 1 - sig_idx,
    })
    # 反弹事实：反弹高（L 之后的最高 high；长下影当日 = 止跌日自己的 high）/ 反弹幅度 /
    # 回踩低 —— 「更高的低点」= 任一回踩低 P > L 且 P 之后至少一根没再跌破 P（对回踩套同一条
    # 「次日未新低」）；回踩之后再创反弹新高的（已完成的回踩）同样算，不因新高而丢掉。
    start = i_low + 1 if held_days > 0 else i_low
    b_idx = max(range(start, n), key=lambda t: _f(bars[t], "high"))
    out.update({"bounce_high": _f(bars[b_idx], "high"), "bounce_high_date": _date(bars[b_idx]),
                "bounce_pct": (close / low - 1.0) if low > 0 else None,
                "pullback_low": None, "pullback_low_date": None, "higher_low": False})
    run_hi, run_idx = _f(bars[start], "high"), start
    p_last: Optional[int] = None
    hl_event: Optional[int] = None                       # 「更高的低点」成立的那根 (回踩低的次日)
    for t in range(start + 1, n):
        if _f(bars[t], "high") > run_hi:
            if t > run_idx + 1:                          # 两个高点之间有回踩 → 已完成的回踩
                p_idx = min(range(run_idx + 1, t), key=lambda k: _f(bars[k], "low"))
                if _f(bars[p_idx], "low") > low:
                    out["higher_low"], p_last, hl_event = True, p_idx, p_idx + 1
            run_hi, run_idx = _f(bars[t], "high"), t
    if run_idx < n - 1:                                  # 当前回踩（反弹高之后）
        p_idx = min(range(run_idx + 1, n), key=lambda k: _f(bars[k], "low"))
        p_last = p_idx
        if _f(bars[p_idx], "low") > low and p_idx < n - 1:
            out["higher_low"], hl_event = True, p_idx + 1
    if p_last is not None:
        out.update({"pullback_low": _f(bars[p_last], "low"), "pullback_low_date": _date(bars[p_last])})
    out["higher_low_date"] = _date(bars[hl_event]) if hl_event is not None else None
    out["bars_since_higher_low"] = (n - 1 - hl_event) if hl_event is not None else None
    out["above_lh"] = bool(lh and close > lh["price"])
    return out


def bottom_setup(bars: List[dict]) -> Dict:
    """模式 1 抄底：今日框架 + 「昨日框架今日死了没 / 盘中再探」。→ dict，永远带 `stage` / `cn` / `formed`。

    只吃**已完成日线**（盘中未完成 bar 不能当收盘）。"""
    today = _frame(bars)
    today["undercut_prev"] = None
    if len(bars) > MIN_BARS:
        prev = _frame(bars[:-1])
        if prev.get("stage") == "formed":
            c, lo = _f(bars[-1], "close"), _f(bars[-1], "low")
            if c < float(prev["low"]):
                today = {**prev, "stage": "dead", "close": c, "undercut_prev": None,
                         "died_on": _date(bars[-1]),
                         "why": f"收 {c:g} < 止跌低 {prev['low']:g}，昨日框架作废"}
            elif lo < float(prev["low"]):
                today["undercut_prev"] = {"low": prev["low"], "date": prev["low_date"],
                                          "cn": f"盘中破前止跌低 {prev['low']:g}（{prev['low_date'][5:]}）后收回"}
    meta = STAGES[today["stage"]]
    today["cn"], today["formed"], today["rank"] = meta["cn"], meta["formed"], meta["rank"]
    # 「刚形成」= 止跌信号 ≤ RECENT_SIGNAL_BARS。二次探底由顶锚自然处理:
    # 顶之后的最低 low 就是那个二次低点, 不需要再按回踩深度把老底挂回来
    today["listed"] = today["stage"] in LISTED_STAGES and not (
        today["stage"] == "formed" and (today.get("bars_since_signal") or 0) > RECENT_SIGNAL_BARS)
    today["verdict"] = bounce_or_reversal(today)
    return today


def bounce_or_reversal(setup: Dict) -> Optional[str]:
    """反弹 vs 反转（判读，不是退出指令）。None = 不适用（无框架 / 未止跌）。"""
    st = setup.get("stage")
    if st == "dead":
        return "反弹结束（收破止跌低）"
    if st != "formed":
        return None
    hh, hl = bool(setup.get("above_lh")), bool(setup.get("higher_low"))
    if hh and hl:
        return "反转（越前高 + 回踩更高低点）"
    if hh:
        return "反转候选（已越前高，回踩未验）"
    if hl:
        return "反转候选（回踩更高低点，前高未越）"
    return "反弹（未越前高）"


def probe_marker(bars: List[dict], atr20: Optional[float]) -> Optional[str]:
    """[FYI] 反转探针（授权层的窄信号）是否同时在场 → `🔁探针R1` / `🔁探针R2域外` / None。
    只播不判：探针的 PLAN / VETO 归 entry_guard。"""
    sig = reversal.detect(bars, atr20)
    if not sig:
        return None
    return (f"🔁探针{sig['domain']}" + ("" if sig["in_domain"] else "域外")
            + (f"·远{sig['dist_n']:g}N" if sig.get("too_far") else "")     # 离底太远 (ATR 上限) 也只播
            + f"·{sig['date'][5:]}")


# ── 模式 2 的纯数字部分 ────────────────────────────────────────────

NEAR_GATE_ATR = 1.0      # 「即将形成」= 收盘在 gate 之下 ≤1N（一根 ATR 之内 = 明天就够得到）

TREND_STATES: Dict[str, Dict] = {
    "transitioned": {"cn": "✓已转移", "formed": True, "rank": 0},
    "standing": {"cn": "✅站稳·量腿未过", "formed": True, "rank": 1},
    "counting": {"cn": "⏳计数中", "formed": False, "rank": 2},
    "near": {"cn": "▫️逼近", "formed": False, "rank": 3},
}


def gate_proximity(close: Optional[float], atr: Optional[float], level: Optional[float],
                   streak: int, need: int) -> Optional[Dict]:
    """收盘相对一道 gate 的接近度 → {state, dist_pct, dist_n} | None（远 = 不列）。

    state: standing（连续收盘 ≥ need 日，量腿另判）/ counting（1 ≤ streak < need）/
    near（gate 之下 ≤ NEAR_GATE_ATR）。已转移由调用方按 regime_history 另判。"""
    if close is None or level is None or not close:
        return None
    dist_pct = (level / close - 1.0) * 100.0
    dist_n = ((level - close) / atr) if atr else None
    out = {"dist_pct": round(dist_pct, 2), "dist_n": (round(dist_n, 2) if dist_n is not None else None)}
    if streak >= need and need > 0:
        return {**out, "state": "standing"}
    if streak >= 1:
        return {**out, "state": "counting"}
    if close < level and dist_n is not None and dist_n <= NEAR_GATE_ATR:
        return {**out, "state": "near"}
    return None
