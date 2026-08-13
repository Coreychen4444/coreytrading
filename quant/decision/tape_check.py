#!/usr/bin/env python3
"""tape_check.py — 60m 量价下沉: 单个 session 的日内 tape 机械判读。

为什么存在(TV 评估关闭后的替代立项 b, 用户 批; 需求记档):
    异动日/入场日只看日线一根 bar, 分不清「冲高出货」和「回踩吸筹」——
    climax vs fade 的证据在日内量价分布里。OpenD 本身有 60m kline,
    缺的从来不是数据源, 是把下沉写成机械判读(适配系统, 不接 TV)。

定位: **情报/监控层 [FYI]**—— 不参与授权判定(授权计数只认收盘 bar,
适配系统约束  入册)。60m 现拉不落盘(OpenD 可重拉, 不入 git)。

机械判读(阈值全部显式, PARAMS; 首条命中即标签, 全不命中 = mixed 合法输出):
  climax_fade   首根量 ≥35% 且收在日内区间下 40% 且收盘<VWAP  → 冲高派发形
  drive_hold    首根量 ≥35% 且收在区间上 70% 且收盘≥VWAP      → 开盘驱动+持住
  absorption    后半量 ≥55% 且收在区间上 70% 且收盘>VWAP      → 午后吸筹/延续
  steady_trend  无单根 >30% 且收在区间上 60%                   → 匀速趋势
  mixed         以上全不命中 —— 不硬贴标签(空集允许)
辅助: 午后新高但该根量 <15% → weak_high(无量新高)标注。

CLI:
    python3 quant/decision/tape_check.py INTC                  # 最近一个 session
    python3 quant/decision/tape_check.py INTC --date  [--json]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 直接 `python3 quant/<层>/<模块>.py` 时让 `quant.*` 可 import

import argparse
import json
from typing import Dict, List, Optional

from quant.core.io_json import iter_jsonl  # noqa: E402
from quant.core.num import coerce_float  # noqa: E402
from quant.core.paths import KLINE_CACHE_DIR  # noqa: E402
from quant.core.timeutil import et_today  # noqa: E402

PARAMS = {
    "first_share_climax": 0.35,   # 首根量占比 → 开盘驱动/派发候选
    "close_pos_fade": 0.40,       # 收盘位 ≤ → 回落
    "close_pos_strong": 0.70,     # 收盘位 ≥ → 强收
    "back_half_absorb": 0.55,     # 后半 session 量占比 ≥ → 午后吸筹
    "even_bar_max": 0.30,         # 无单根超此 → 匀速
    "steady_close_pos": 0.60,
    "weak_high_share": 0.15,      # 新高 bar 量占比 < → 无量新高
}


def session_bars(bars: List[dict], date_str: str) -> List[dict]:
    """按日期前缀过滤出单个 session 的 60m bar。"""
    return [b for b in bars if str(b.get("time", "")).startswith(date_str)]


def tape_metrics(bars: List[dict], prev_close: Optional[float] = None) -> Dict:
    """单 session 量价指标 + 机械标签(纯函数)。bars = 60m [{time,open,high,low,close,volume,turnover}]。"""
    if not bars:
        return {"ok": False, "reason": "无 bar"}
    vols = [max(coerce_float(b.get("volume")) or 0.0, 0.0) for b in bars]
    total = sum(vols)
    if total <= 0:
        return {"ok": False, "reason": "volume 全零"}
    shares = [v / total for v in vols]
    highs = [coerce_float(b.get("high")) or 0.0 for b in bars]
    lows = [coerce_float(b.get("low")) or 0.0 for b in bars]
    o = coerce_float(bars[0].get("open")) or 0.0
    c = coerce_float(bars[-1].get("close")) or 0.0
    lows_pos = [x for x in lows if x > 0]
    h, l = max(highs), (min(lows_pos) if lows_pos else min(lows))
    close_pos = (c - l) / (h - l) if h > l else 0.5
    turn = sum(max(coerce_float(b.get("turnover")) or 0.0, 0.0) for b in bars)
    vwap = turn / total if turn > 0 else sum(
        (coerce_float(b.get("close")) or 0.0) * v for b, v in zip(bars, vols)) / total
    n = len(bars)
    back_half = sum(shares[n // 2:])
    hi_idx = max(range(n), key=lambda i: highs[i])
    weak_high = hi_idx >= n // 2 and shares[hi_idx] < PARAMS["weak_high_share"]

    p = PARAMS
    if shares[0] >= p["first_share_climax"] and close_pos <= p["close_pos_fade"] and c < vwap:
        label = "climax_fade"
    elif shares[0] >= p["first_share_climax"] and close_pos >= p["close_pos_strong"] and c >= vwap:
        label = "drive_hold"
    elif back_half >= p["back_half_absorb"] and close_pos >= p["close_pos_strong"] and c > vwap:
        label = "absorption"
    elif max(shares) <= p["even_bar_max"] and close_pos >= p["steady_close_pos"]:
        label = "steady_trend"
    else:
        label = "mixed"

    out = {"ok": True, "bars": n, "open": o, "close": c, "high": h, "low": l,
           "close_pos": round(close_pos, 3), "vwap": round(vwap, 3),
           "close_vs_vwap": round((c / vwap - 1) * 100, 2) if vwap else None,
           "first_share": round(shares[0], 3), "last_share": round(shares[-1], 3),
           "back_half_share": round(back_half, 3), "max_bar_share": round(max(shares), 3),
           "shares": [round(s, 3) for s in shares],
           "weak_high": weak_high, "label": label}
    if prev_close:
        out["gap_pct"] = round((o / prev_close - 1) * 100, 2)
        out["day_change_pct"] = round((c / prev_close - 1) * 100, 2)
    return out


LABEL_ZH = {"climax_fade": "冲高派发形(首根放量+弱收+VWAP 下)",
            "drive_hold": "开盘驱动+持住(首根放量+强收+VWAP 上)",
            "absorption": "午后吸筹/延续(后半放量+强收)",
            "steady_trend": "匀速趋势(量均匀+强收)",
            "mixed": "混合 — 不硬贴标签(空集允许)"}


def render(ticker: str, date_str: str, m: Dict, bars: List[dict],
           in_progress: bool = False) -> str:
    if not m.get("ok"):
        return f"[tape] {ticker} {date_str}: 无法判读 ({m.get('reason')})"
    out = [f"🎞 60m tape — {ticker} {date_str}"
           + (" ⚠️ session 可能未收盘, 判读随后续 bar 变化" if in_progress else "")]
    for b, s in zip(bars, m["shares"]):
        t = str(b.get("time", ""))[11:16]
        out.append(f"   {t} o{coerce_float(b.get('open')):g} c{coerce_float(b.get('close')):g} "
                   f"量占 {s * 100:.0f}%")
    gap = f" gap {m['gap_pct']:+.1f}%," if "gap_pct" in m else ""
    out.append(f"   全日:{gap} 收盘位 {m['close_pos']:.2f} | VWAP {m['vwap']:g} "
               f"(收盘 {m['close_vs_vwap']:+.1f}%) | 首根量 {m['first_share'] * 100:.0f}% "
               f"/ 后半 {m['back_half_share'] * 100:.0f}%")
    if m.get("weak_high"):
        out.append("   ⚠️ 无量新高: 午后新高 bar 量占比 <15%")
    out.append(f"   判读: **{m['label']}**— {LABEL_ZH[m['label']]}")
    out.append("   [FYI] 情报/监控层 — 不参与授权判定(授权计数只认收盘 bar)")
    return "\n".join(out)


def prev_daily_close(ticker: str, date_str: str) -> Optional[float]:
    """日线缓存里 date 前一根的收盘(缓存缺票/缺日 → None, 不报错)。"""
    path = KLINE_CACHE_DIR / f"{ticker}.jsonl"
    prev = None
    try:
        for row in iter_jsonl(path):
            d = str(row.get("time", ""))[:10]
            if d >= date_str:
                break
            prev = coerce_float(row.get("close"))
    except FileNotFoundError:
        return None
    return prev


def probe(ticker: str, date_str: Optional[str] = None) -> str:
    """拉 60m + 判读 + 渲染(联网; catalyst_check --tape 复用)。"""
    from quant.data.market_data import get_kline  # 延迟 import: 纯函数测试不碰 OpenD
    bars = get_kline(ticker, ktype="60m", num=48)
    if not bars:
        return f"[tape] {ticker}: OpenD 无 60m 数据"
    dates = sorted({str(b.get("time", ""))[:10] for b in bars})
    day = date_str or dates[-1]
    sb = session_bars(bars, day)
    if not sb:
        return f"[tape] {ticker} {day}: 60m 窗口内无该日 bar (仅覆盖近 {len(dates)} 个 session: {dates[0]}~{dates[-1]})"
    m = tape_metrics(sb, prev_close=prev_daily_close(ticker, day))
    return render(ticker, day, m, sb, in_progress=(day == et_today()))


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description="60m 量价下沉判读 (情报层 [FYI])")
    ap.add_argument("ticker")
    ap.add_argument("--date", default=None, help="ET session 日期, 默认最近一个")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.json:
        from quant.data.market_data import get_kline
        bars = get_kline(a.ticker, ktype="60m", num=48)
        dates = sorted({str(b.get("time", ""))[:10] for b in bars})
        day = a.date or (dates[-1] if dates else None)
        sb = session_bars(bars, day or "")
        m = tape_metrics(sb, prev_close=prev_daily_close(a.ticker, day or ""))
        m["ticker"], m["date"] = a.ticker, day
        print(json.dumps(m, ensure_ascii=False, indent=2))
    else:
        print(probe(a.ticker, a.date))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
