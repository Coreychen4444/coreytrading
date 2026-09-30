#!/usr/bin/env python3
"""Regime & Confluence Scorecard — 不确定性下的加仓决策辅助.

把"凭感觉加仓"换成"按证据评分"。输出三样东西:
  - regime 标签(宏观/个股 × 阶段 × vol)
  - size 修正系数(这一注占剩余预算几分之一)
  - vehicle 判决(naked / spread / 等)

**不输出买卖信号**。它只是把证据量化,倾斜 size + 信心。接在 Pre-Trade
Quick Check 的 Q3(zone)之后作为 sizing 层。详见
公开 schema 为 `trade/strategy/uncertainty-execution-system.example.md`；
实际规则写入移除 `.example` 后的私有文件。

四个问题(各自独立回答,不是一个开关):
  Q-A 宏观 vs 个股?   → GO/NO-GO + 总预算    (ticker vs peers + VIX + 催化)
  Q-B 什么阶段?       → 这一档占预算几分之一  (RSI / MA偏离 / K线 / 连阴)
  Q-C 衰竭了吗?       → R:R 是否转好          (复用 detect_bottom_signals)
  Q-D vol 贵贱?       → vehicle 选择          (期权 IV + VIX)

复用 technical.py 的指标库(RSI/MACD/Boll/量比/MA偏离/形态/背离/见底评分),
本脚本只补 ATR + 编排 + 综合处方。

CLI:
  .venv/moomoo/bin/python3 quant/decision/regime_score.py DRAM \\
      --peers SMH,SOXX,MU --option DRAM270115C60000 \\
      --vix 21.5 --vix-chg 40 --budget-remaining 2 [--json]

数据缺口(诚实):
  - RSI/ATR/MA/量/K线/见底 = OpenD 今天就能算
  - VIX = OpenD 取不稳; 自动取数走宏观日线缓存 (data/macro_series ^VIX), 取不到用 --vix / --vix-chg 手动传入
  - 期权 IV = 取的是 spot IV;真正的 IV Rank(历史百分位)OpenD 无历史,
    需另攒数据,当前只能用 spot + 经验阈值粗判(会大声标注)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 直接 `python3 quant/<层>/<模块>.py` 时让 `quant.*` 可 import

import argparse
import json
import os
from typing import Dict, List, Optional

from quant.core.paths import add_openapi_path  # noqa: E402
from quant.data.market_data import get_kline, get_snapshot  # noqa: E402
from quant.core import technical as ta  # noqa: E402


# ── small helpers ────────────────────────────────────────────────

def _fmt(v, d=2, suffix=""):
    if v is None:
        return "N/A"
    try:
        return f"{float(v):.{d}f}{suffix}"
    except (TypeError, ValueError):
        return str(v)


def atr(klines: List[dict], period: int = 14) -> Optional[float]:
    """Average True Range (simple mean of last `period` true ranges)."""
    if len(klines) < period + 1:
        return None
    trs = []
    for i in range(1, len(klines)):
        h, l, pc = klines[i]["high"], klines[i]["low"], klines[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs[-period:]) / period


def consecutive_down(closes: List[float]) -> int:
    """Trailing consecutive close-to-close down days."""
    n = 0
    for i in range(len(closes) - 1, 0, -1):
        if closes[i] < closes[i - 1]:
            n += 1
        else:
            break
    return n


def candle_position(k: dict) -> Optional[float]:
    """Where close sits in the day's range. 0 = at low, 1 = at high."""
    rng = k["high"] - k["low"]
    if rng <= 0:
        return None
    return (k["close"] - k["low"]) / rng


def pct_from_high(highs: List[float], close: float, lookback: int) -> Optional[float]:
    window = highs[-lookback:] if len(highs) >= lookback else highs
    if not window:
        return None
    hi = max(window)
    return (close / hi - 1) * 100 if hi else None


def option_iv(option_code: str) -> Optional[float]:
    """Spot implied vol (%) for an option code via direct snapshot."""
    try:
        add_openapi_path()  # openapi skill scripts (`common`) 上 sys.path
        from common import create_quote_context, safe_close, RET_OK
        ctx = create_quote_context()
        try:
            ret, data = ctx.get_market_snapshot([f"US.{option_code}"])
            if ret != RET_OK or data is None or len(data) == 0:
                return None
            row = data.iloc[0]
            if "option_implied_volatility" in data.columns:
                return float(row["option_implied_volatility"])
        finally:
            safe_close(ctx)
    except Exception:
        return None
    return None


# ── the four questions ───────────────────────────────────────────

def q_a_regime(ticker_chg, peers: Dict[str, dict], defensives: Dict[str, dict],
               vix, vix_chg) -> dict:
    """宏观 vs 个股 + 场内轮动 vs 全面出逃 → 参与资格."""
    peer_chgs = {t: d.get("change_pct") for t, d in peers.items()
                 if d.get("change_pct") is not None}
    peer_avg = (sum(peer_chgs.values()) / len(peer_chgs)) if peer_chgs else None
    def_chgs = {t: d.get("change_pct") for t, d in defensives.items()
                if d.get("change_pct") is not None}
    def_avg = (sum(def_chgs.values()) / len(def_chgs)) if def_chgs else None

    vix_hot = (vix is not None and vix >= 20) or (vix_chg is not None and vix_chg >= 15)
    peers_down = peer_avg is not None and peer_avg <= -1.5
    ticker_down = ticker_chg is not None and ticker_chg <= -3

    # 场内轮动 vs 全面出逃 (defensive rotation tell — 源: X post):
    # 防御涨/抗跌 + cyclical 跌 = 钱在场内换赛道(健康, 可逢低); 防御也跌 = 系统性出逃(现金为王)
    cyclical_down = (peer_avg is None) or (peer_avg <= -1.0) or ticker_down
    if def_avg is None:
        rotation = "UNKNOWN — 未提供防御板块 (--defensives)"
        broad_exit = False
    elif def_avg >= -0.3 and cyclical_down:
        rotation = (f"INTRA-MARKET ROTATION — 防御 {def_avg:+.1f}% 抗跌/逆势, cyclical 跌 "
                    "→ 钱没离场(健康, 回调可逢低)")
        broad_exit = False
    elif def_avg <= -1.0:
        rotation = f"BROAD EXIT — 防御 {def_avg:+.1f}% 也跌 → 系统性出逃, 现金为王"
        broad_exit = True
    else:
        rotation = f"MIXED — 防御 {def_avg:+.1f}%, 轮动信号不明"
        broad_exit = False

    # idiosyncratic = ticker fell hard but peers/VIX calm
    idiosyncratic = (ticker_down and peer_avg is not None and peer_avg > -1.0
                     and not vix_hot)

    if not ticker_down:
        verdict = "NOT-A-DIP — 标的当日未明显下跌,本框架针对回调入场"
        eligibility = "n/a"
    elif idiosyncratic:
        verdict = "IDIOSYNCRATIC — 标的独跌,板块/VIX 平静 → 可能个股 fundamental 问题"
        eligibility = "NO-GO(除非催化核实为噪音)"
    elif peers_down or vix_hot:
        verdict = "MACRO/SECTOR PANIC — 板块同跌 / VIX 升 → 系统性,非个股坏"
        eligibility = "GO — ladder 开 (v3: 授权纯结构; thesis 状态 [FYI] 归 用户 判断)"
    else:
        verdict = "MIXED/UNCLEAR — 信号不一致,手动核实催化"
        eligibility = "VERIFY"

    # 全面出逃 override: 即使 macro panic, 防御也跌 = 降级保现金
    if broad_exit and eligibility.startswith("GO"):
        eligibility = "CASH/VERIFY(macro panic 但防御也跌 = 系统性出逃, 先保现金)"

    return {
        "verdict": verdict,
        "eligibility": eligibility,
        "rotation": rotation,
        "broad_exit": broad_exit,
        "ticker_chg_pct": ticker_chg,
        "peer_avg_chg_pct": peer_avg,
        "peers": peer_chgs,
        "defensive_avg_chg_pct": def_avg,
        "defensives": def_chgs,
        "vix": vix, "vix_chg_pct": vix_chg, "vix_hot": vix_hot,
        "note": "⚠ 催化识别(宏观数据 vs 公司新闻)+ flow 驱动(gamma/CTA/强平/IPO抽血)脚本无法替代 → 手动核实",
    }


def q_b_phase(analysis: dict, klines: List[dict]) -> dict:
    """什么阶段 → 这一档占预算几分之一."""
    latest = analysis["latest"]
    closes = [k["close"] for k in klines]
    rsi_v = latest.get("rsi")
    ma20 = latest.get("ma20")
    ma60 = latest.get("ma60")
    close = latest.get("close")
    cpos = candle_position(klines[-1])
    cdown = consecutive_down(closes)
    div = analysis.get("rsi_divergence") is not None
    patterns = analysis.get("patterns", [])
    stabilizing = div or any(p in patterns for p in ("hammer", "bullish_engulfing", "morning_star"))

    dist_ma20 = ((close / ma20 - 1) * 100) if (ma20 and close) else None
    dist_ma60 = ((close / ma60 - 1) * 100) if (ma60 and close) else None
    dist_high = pct_from_high([k["high"] for k in klines], close, 20)

    # phase classification → fraction of remaining budget for THIS clip
    if rsi_v is not None and rsi_v < 30 and stabilizing:
        phase = "OVERSOLD + STABILIZING — 接近衰竭,R:R 转好"
        frac = (0.40, 0.60)
    elif rsi_v is not None and rsi_v < 30:
        phase = "OVERSOLD 但未企稳 — 刀还在飞"
        frac = (0.20, 0.35)
    elif cpos is not None and cpos < 0.33 and cdown >= 2:
        phase = "MID-SLIDE 续跌(收当日低位 + 连阴)— 早,不是底"
        frac = (0.15, 0.25)
    elif rsi_v is not None and 40 <= rsi_v <= 62:
        phase = "EARLY/MID 回调(未超卖)— 早,留弹药"
        frac = (0.15, 0.30)
    else:
        phase = "INDETERMINATE — 信号不集中,偏保守"
        frac = (0.15, 0.25)

    return {
        "phase": phase, "clip_fraction": frac,
        "rsi": rsi_v, "candle_position": cpos, "consecutive_down": cdown,
        "rsi_divergence": div, "patterns": patterns, "stabilizing": stabilizing,
        "dist_ma20_pct": dist_ma20, "dist_ma60_pct": dist_ma60,
        "dist_20d_high_pct": dist_high,
    }


def q_c_exhaustion(klines: List[dict]) -> dict:
    """衰竭了吗 → 复用 technical.detect_bottom_signals(A/B/C)."""
    b = ta.detect_bottom_signals(klines)
    score = b.get("score", "C")
    mapping = {
        "A": "EXHAUSTION 确认(3+ 见底信号共振)— R:R 明显转好",
        "B": "PARTIAL(2 信号)— 初步,未确认",
        "C": "无确认 / 有否决 — 别指望是底",
    }
    return {
        "bottom_score": score,
        "verdict": mapping.get(score, score),
        "signals": b.get("signals", []),
        "vetoes": b.get("vetoes", []),
        "recommendation": b.get("recommendation", ""),
    }


def q_d_vol(iv: Optional[float], vix: Optional[float]) -> dict:
    """vol 贵贱 → vehicle 选择. ⚠ spot IV,非 IV Rank(无历史)."""
    # heuristic bands (single-name/ETF). LOUD caveat: not a true percentile.
    if iv is None:
        verdict = "无期权 IV 输入 — 传 --option 才能判;VIX 仅作大盘 vol 参考"
        vehicle = "n/a"
    elif iv >= 70:
        verdict = f"IV {iv:.0f}% = 高(经验阈值)— vol 贵"
        vehicle = "SPREAD / 等 IV 降;别买 naked long premium"
    elif iv >= 45:
        verdict = f"IV {iv:.0f}% = 偏高 — 倾向 spread,naked 减 size"
        vehicle = "SPREAD 优先 / naked 减 size"
    else:
        verdict = f"IV {iv:.0f}% = 中性偏低 — naked long 可"
        vehicle = "NAKED long 可"
    return {
        "spot_iv_pct": iv, "vix": vix, "verdict": verdict, "vehicle": vehicle,
        "caveat": "⚠ 这是 spot IV 不是 IV Rank(历史百分位)。OpenD 无 IV 历史,"
                  "阈值是经验值,需逐日攒 IV 才能算真百分位。",
    }


def synthesize(qa, qb, qc, qd, budget_remaining) -> dict:
    """三个旋钮合成处方:资格 → 节奏 → 工具."""
    elig = qa["eligibility"]
    if elig.startswith("NO-GO") or elig.startswith("CASH") or elig == "n/a" or qa.get("broad_exit"):
        reason = ("防御板块也跌 = 系统性出逃,现金为王。" if qa.get("broad_exit")
                  else "个股问题先核实,别因为超卖就接。" if elig.startswith("NO-GO")
                  else "标的未明显下跌,本框架不适用。")
        return {
            "regime": f"{qa['verdict'].split(' — ')[0]} / {qb['phase'].split(' — ')[0]}",
            "size_modifier": "0 — 资格闸未过(Q-A),不碰",
            "vehicle": qd["vehicle"],
            "clips": 0,
            "headline": f"STOP:Q-A 资格未过。{reason}",
        }

    lo, hi = qb["clip_fraction"]
    # Q-C exhaustion can lift the clip toward the top of Q-B's band
    if qc["bottom_score"] == "A":
        lo = min(hi, lo + 0.15)
    elif qc["bottom_score"] == "C" and qc["vetoes"]:
        hi = lo  # veto present → cap at the small end

    clips = None
    if budget_remaining:
        clips = (round(budget_remaining * lo, 1), round(budget_remaining * hi, 1))

    regime = (f"{qa['verdict'].split(' — ')[0]} / "
              f"{qb['phase'].split(' — ')[0]} / "
              f"{(qd['verdict'].split(' — ')[0]) if qd['spot_iv_pct'] else 'VOL?'}")

    size_mod = f"这一档 = 剩余预算的 {int(lo*100)}-{int(hi*100)}%"
    if clips:
        size_mod += f" ≈ {clips[0]}-{clips[1]} 张(剩 {budget_remaining})"

    headline = (
        f"资格={elig.split('(')[0].strip()} | 节奏={qb['phase'].split(' — ')[0]} | "
        f"工具={qd['vehicle']}。"
        " 早 = 小 + 留弹药;size 跟证据(Q-C)走,不跟价格走。"
)

    return {
        "regime": regime, "size_modifier": size_mod,
        "vehicle": qd["vehicle"], "clips": clips, "headline": headline,
    }


# ── orchestration ────────────────────────────────────────────────

def run(ticker, peers_list, defensives_list, option_code, vix, vix_chg, budget_remaining, num):
    klines = get_kline(ticker, ktype="1d", num=num)
    if not klines or len(klines) < 30:
        return {"error": f"K线不足(需 ≥30 bars,得到 {len(klines) if klines else 0})。"
                         " OpenD 在跑吗?"}

    closes = [k["close"] for k in klines]
    ticker_chg = ((closes[-1] / closes[-2] - 1) * 100) if len(closes) >= 2 else None

    analysis = ta.analyze(klines)
    # one snapshot call for peers + defensives, then split by membership
    aux = list(dict.fromkeys((peers_list or []) + (defensives_list or [])))
    snaps = get_snapshot(aux) if aux else {}
    peers = {t: snaps[t] for t in (peers_list or []) if t in snaps}
    defensives = {t: snaps[t] for t in (defensives_list or []) if t in snaps}
    iv = option_iv(option_code) if option_code else None

    qa = q_a_regime(ticker_chg, peers, defensives, vix, vix_chg)
    qb = q_b_phase(analysis, klines)
    qc = q_c_exhaustion(klines)
    qd = q_d_vol(iv, vix)
    synth = synthesize(qa, qb, qc, qd, budget_remaining)

    return {
        "ticker": ticker.upper(),
        "bars": len(klines),
        # 读盘量, 显式 14 (算钱路径一律 ATR20 — 见 trend-exit-system §2b 口径表)
        "atr14": atr(klines, 14),
        "latest_close": closes[-1],
        "Q_A_regime": qa, "Q_B_phase": qb,
        "Q_C_exhaustion": qc, "Q_D_vol": qd,
        "PRESCRIPTION": synth,
        "disclaimer": "不是买卖信号。是把证据量化以倾斜 size+信心。"
                      "买 sized 档 ≠ 赌底。最终决策仍走 Pre-Trade Quick Check。",
    }


def _print_human(r: dict):
    if "error" in r:
        print(f"错误: {r['error']}")
        return
    p = "=" * 66
    print(p)
    print(f"  {r['ticker']} Regime & Confluence Scorecard"
          f"   (close {_fmt(r['latest_close'])}, ATR14 {_fmt(r['atr14'])}, {r['bars']} bars)")
    print(p)

    qa = r["Q_A_regime"]
    print(f"\n[Q-A 资格] 宏观 vs 个股 → GO/NO-GO")
    print(f"   标的当日 {_fmt(qa['ticker_chg_pct'],2,'%')} | 同业均 {_fmt(qa['peer_avg_chg_pct'],2,'%')}"
          f" | VIX {_fmt(qa['vix'],1)} ({_fmt(qa['vix_chg_pct'],0,'%')})")
    if qa["peers"]:
        print("   同业: " + ", ".join(f"{t} {_fmt(c,1,'%')}" for t, c in qa["peers"].items()))
    if qa.get("defensives"):
        print("   防御: " + ", ".join(f"{t} {_fmt(c,1,'%')}" for t, c in qa["defensives"].items())
              + f"  (均 {_fmt(qa.get('defensive_avg_chg_pct'),1,'%')})")
    print(f"   → 归因: {qa['verdict']}")
    print(f"   → 轮动: {qa['rotation']}")
    print(f"   → 资格: {qa['eligibility']}")
    print(f"   {qa['note']}")

    qb = r["Q_B_phase"]
    print(f"\n[Q-B 阶段] → 这一档下多少")
    print(f"   RSI {_fmt(qb['rsi'],1)} | 收盘在日内 {_fmt((qb['candle_position'] or 0)*100,0,'%')} 分位"
          f" | 连阴 {qb['consecutive_down']} | 距20d高 {_fmt(qb['dist_20d_high_pct'],1,'%')}"
          f" | 距MA20 {_fmt(qb['dist_ma20_pct'],1,'%')}")
    print(f"   背离={qb['rsi_divergence']} 形态={qb['patterns'] or '无'} 企稳={qb['stabilizing']}")
    print(f"   → {qb['phase']}")

    qc = r["Q_C_exhaustion"]
    print(f"\n[Q-C 衰竭] 见底信号评分 = {qc['bottom_score']}")
    print(f"   → {qc['verdict']}")
    for s in qc["signals"]:
        print(f"     + {s}")
    for v in qc["vetoes"]:
        print(f"     ! {v}")

    qd = r["Q_D_vol"]
    print(f"\n[Q-D vol] → vehicle")
    print(f"   → {qd['verdict']}")
    print(f"   → vehicle: {qd['vehicle']}")
    print(f"   {qd['caveat']}")

    s = r["PRESCRIPTION"]
    print(f"\n{'─'*66}")
    print(f"  处方  regime: {s['regime']}")
    print(f"        size  : {s['size_modifier']}")
    print(f"        工具  : {s['vehicle']}")
    print(f"  >>> {s['headline']}")
    print(f"{'─'*66}")
    print(f"  {r['disclaimer']}")
    print(p)


def main():
    ap = argparse.ArgumentParser(description="Regime & Confluence Scorecard (加仓辅助,非买卖信号)")
    ap.add_argument("ticker", help="标的, 如 DRAM")
    ap.add_argument("--peers", default="", help="同业/板块对比, 逗号分隔, 如 SMH,SOXX,MU")
    ap.add_argument("--defensives", default="XLV,XLP,XLU,XLE",
                    help="防御板块(场内轮动 vs 全面出逃信号), 逗号分隔; 默认 XLV,XLP,XLU,XLE")
    ap.add_argument("--option", default="", help="期权代码取 spot IV, 如 DRAM270115C60000")
    ap.add_argument("--vix", type=float, default=None,
                    help="VIX 收盘(省略时自动从 FRED 免费源拉取, 失败再手动传)")
    ap.add_argument("--vix-chg", type=float, default=None, help="VIX 当日涨跌%%")
    ap.add_argument("--budget-remaining", type=float, default=None,
                    help="该标的 ladder 剩余预算(张/股), 用于把 size%% 换成具体数量")
    ap.add_argument("--num", type=int, default=90, help="K线根数(默认 90)")
    ap.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args()

    peers_list = [p.strip().upper() for p in args.peers.split(",") if p.strip()]
    defensives_list = [p.strip().upper() for p in args.defensives.split(",") if p.strip()]

    if args.vix is None:
        # 宏观日线缓存（data/macro_series，VIX 的唯一取数口）；缺 → --vix 手动传入
        from quant.data.macro_series import load
        rows = load("VIX")
        if len(rows) >= 2:
            args.vix = rows[-1][1]
            if args.vix_chg is None:
                args.vix_chg = round((rows[-1][1] / rows[-2][1] - 1) * 100, 2)
            print(f"[regime] VIX auto: {args.vix} ({rows[-1][0]}, macro_daily ^VIX)", file=sys.stderr)

    # OpenD SDK logs to stdout (fd 1) both via a handler bound to the original
    # stream AND from background threads (async disconnect) — so neither a
    # sys.stdout swap nor redirect-then-restore wins the race. Redirect fd 1 ->
    # stderr for the whole run and emit final output through a separate handle to
    # the real stdout, so --json stays cleanly pipeable.
    sys.stdout.flush()
    _real_out = os.fdopen(os.dup(1), "w", encoding="utf-8")
    os.dup2(2, 1)  # fd 1 -> stderr permanently (swallows sync + async SDK logs)

    r = run(args.ticker, peers_list, defensives_list, args.option or None,
            args.vix, args.vix_chg, args.budget_remaining, args.num)

    if args.as_json:
        _real_out.write(json.dumps(r, ensure_ascii=False, indent=2, default=str) + "\n")
    else:
        _saved = sys.stdout
        sys.stdout = _real_out
        try:
            _print_human(r)
        finally:
            sys.stdout = _saved
    _real_out.flush()


if __name__ == "__main__":
    main()
