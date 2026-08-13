#!/usr/bin/env python3
"""option_plan.py — 期权 vehicle 的**执行参数**: strike / DTE / 每档限价 / 张数。

为什么存在 (用户 : "Nvda 这种决定用期权入场的, 需要把具体 strike、
dte 和 premium 入场价格一同给出"):
    entry_guard 是纯正股工具。它对期权只输出**一行闸门**——
        `若期权: ATM IV 47.8% () → <60 → 单腿可考虑`
    然后就没了。而它给的阶梯 195.53 / 193.57 / 191 是**标的价位**, 拿到期权
    账户没法挂。零件全都在 (implied_move / strike_compare crossover /
    level_map --oi / IV 闸门表 / leaps-call-template), 缺的是把标的阶梯映射成
    期权限价的那一段。本模块补这一段。

**只读, 不下单。**输出直接进简报, 用户 手动挂。

三件事:
  1. IV 闸门   percentile (iv_history) → options-strategy-framework §六 canonical 表
                → 允许什么 vehicle。这是**否决位**, 不是建议
  2. 选约      到期按目标 DTE; strike 按 delta 带 (LEAPS 默认 DITM 0.70-0.85,
                IV≥70% 再往低 strike 压 = 多拿 intrinsic 少付 extrinsic)
                + 流动性硬门槛 (OI / bid>0 / spread%)
  3. 每档限价  标的到 rung 时该合约的 BS 估值 —— **估算不是报价**。
                外加 IV+bump 情景: 回调通常伴随 IV 上抬, 恒 IV 的限价系统性偏低,
                照它挂大概率不成交

张数走 lib.sizing.share_ladder(multiplier=100) —— 与正股共用同一套整股/降档
/书面例外逻辑, 1 张就是最小单位。止损锚**标的价位**, 不是期权价。

CLI:
    .venv/moomoo/bin/python3 quant/options/option_plan.py NVDA \
        --ladder 195.53 193.57 191 --cap 1127 --stop 188 [--dte 270 --min-dte 270] [--json]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 直接 `python3 quant/<层>/<模块>.py` 时让 `quant.*` 可 import

from quant.core.paths import add_openapi_path  # noqa: E402

from quant.core import env  # noqa: F401  (.env → os.environ)

import argparse
import json
import math
from datetime import date
from typing import Dict, List, Optional, Tuple

add_openapi_path()  # openapi skill scripts (`common`) 上 sys.path
from quant.core.num import coerce_float  # noqa: E402
from quant.core.sizing import notional_to_premium_cap, share_ladder  # noqa: E402
from quant.core.timeutil import et_today  # noqa: E402
from quant.data import iv_logger  # noqa: E402
from quant.options.strike_compare import bs_call, _norm_cdf  # noqa: E402

PARAMS = {
    "ladder_weights": (0.50, 0.30, 0.20),
    # 流动性硬门槛 —— 薄链是真否决 (LITE 卡: "7/31 全链最大档仅 1.2k → 期权 vehicle
    # 数据不支持, 默认正股")。挂得进去挂不出来的合约不是 vehicle。
    "min_open_interest": 200,
    "max_spread_pct": 15.0,
    # delta 带 = §13.1 定稿 (用户 批,  复确认;  由
    # 旧 DITM (0.70,0.85) 对齐 canonical): 主带 Δ0.60-0.80 (intrinsic ≥40% premium,
    # 退出执行弱 + 确认日入场 → extrinsic 压低当缓冲); IV pct <30 放宽下沿至 0.55
    # (轻度 OTM 档, 定稿「仅当 IV percentile < 30-40」取保守端); IV 高往 DITM 压
    # (canonical §六)。Δ<0.40 绝对禁区由带下限保证。
    "delta_band": (0.60, 0.80),
    "delta_band_low_iv": (0.55, 0.80),
    "delta_band_high_iv": (0.78, 0.92),
    "fill_days": 5,          # 假设几个日历日后回踩到位 (theta 折)
    "dip_iv_bump_pts": 5.0,  # 回调时 IV 上抬的情景 (vol points)
    "risk_free": 0.04,
}

# options-strategy-framework.md §六 = 全系统 canonical。这里只是它的可执行副本;
# 阈值改动必须先改那份文档。
IV_GATES = [
    (80.0, "禁 naked long", "极端: **禁 naked long**(uncertainty Q-D); 卖方也要压测 tail"),
    (70.0, "强 sell-premium tilt", "long premium 仅 4 条 override + **偏 DITM 低 strike**"
                                   "(多拿 intrinsic 少付 extrinsic)"),
    (60.0, "偏 sell premium", "long premium 默认不入, 除非 4 条 override 全满足: "
                              "①parabolic AI 名 ②uncapped catalyst chain ③LEAPS 级 DTE ④size 减档"),
    (30.0, "中性", "按 conviction 选 vehicle"),
    (0.0, "买 premium 友好", "long call/put, debit spread 友好; sell premium 收太薄"),
]


def iv_gate(pct: Optional[float], reliable: bool = True) -> Dict:
    """样本不足时**不让 percentile 驱动硬闸门**—— canonical 要求 ≥60 天序列,
    n=5 的 "percentile 100%" 是噪声不是极值。此时降级为 provisional 并要求
    手查外部源 (framework §六: "攒满 60 天前先用 Barchart 等手查 + 标注来源")。"""
    if pct is None:
        return {"tier": None, "verdict": "IV percentile 不可得",
                "note": "先跑 iv_logger --tickers <T> 攒序列; spot IV 不是 percentile, 别混用"}
    for thresh, verdict, note in IV_GATES:
        if pct >= thresh:
            if not reliable:
                return {"tier": thresh, "verdict": f"{verdict} (provisional)",
                        "provisional": True,
                        "note": f"{note}\n      ⚠️ 样本 <60, percentile 是噪声不是极值 —— "
                                "**这个闸门未定案**。用绝对 ATM IV 作代理并手查外部源 "
                                "(Barchart) 标注来源后再定 vehicle"}
            return {"tier": thresh, "verdict": verdict, "note": note}
    return {"tier": 0.0, "verdict": "?", "note": ""}


def bs_delta(S: float, K: float, T: float, sigma: float, r: float = 0.04) -> float:
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 1.0 if S > K else 0.0
    d1 = (math.log(S / K) + (r + sigma * sigma / 2) * T) / (sigma * math.sqrt(T))
    return _norm_cdf(d1)


def implied_vol(price: float, S: float, K: float, T: float, r: float = 0.04) -> Optional[float]:
    """从中价反解 IV (二分)。moomoo 对冷门/深 ITM 档常返回 option_implied_volatility=0,
    直接信那个 0 会把 BS 估值算成纯 intrinsic。"""
    if price <= 0 or T <= 0:
        return None
    intrinsic = max(0.0, S - K * math.exp(-r * T))
    if price <= intrinsic + 1e-6:
        return None                      # 无时间价值可反解 (或报价穿透)
    lo, hi = 0.01, 5.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if bs_call(S, K, T, mid, r) < price:
            lo = mid
        else:
            hi = mid
    v = (lo + hi) / 2
    return v if 0.02 < v < 4.9 else None


def pick_expiry(candidates: List[Tuple[str, int]], target_dte: int,
                min_dte: int = 7) -> Optional[str]:
    """(expiry, dte) 候选里取「dte ≥ min_dte 中距 target_dte 最近」者。

    LEAPS 传 target=min=270 ⇒ 下限之上的最近可用周期 (§13.2 执行周期,
     用户 移除「最近 Jan 周期」限制后的语义)。min_dte 缺省 7
    = 旧行为 (纯最近, 只滤周内到期)。下限单独成参不并进 target: 纯最近会
    选中 250 这类「更近但破下限」的到期日。"""
    best, diff = None, 10 **9
    for ds, dte in candidates:
        if dte < min_dte:
            continue
        if abs(dte - target_dte) < diff:
            best, diff = ds, abs(dte - target_dte)
    return best


# ── 行情层 ────────────────────────────────────────────────────────

def fetch_chain(ticker: str, target_dte: int, option_type: str = "call",
                min_dte: int = 7) -> Optional[Dict]:
    from common import create_quote_context, safe_close, safe_float, safe_get, RET_OK
    from moomoo import OptionType

    code = f"US.{ticker}"
    ctx = None
    try:
        ctx = create_quote_context()
        ret, snap = ctx.get_market_snapshot([code])
        if ret != RET_OK or snap is None or snap.empty:
            return None
        spot = safe_float(safe_get(snap.iloc[0], "last_price"), default=None)
        if not spot:
            return None

        ret, exps = ctx.get_option_expiration_date(code)
        if ret != RET_OK or exps is None or exps.empty:
            return None
        today = date.fromisoformat(et_today())
        cands: List[Tuple[str, int]] = []
        for _, r in exps.iterrows():
            ds = str(safe_get(r, "strike_time", default=""))[:10]
            try:
                cands.append((ds, (date.fromisoformat(ds) - today).days))
            except ValueError:
                continue
        best_exp = pick_expiry(cands, target_dte, min_dte)
        if not best_exp:
            return None
        dte = (date.fromisoformat(best_exp) - today).days

        ot = OptionType.CALL if option_type == "call" else OptionType.PUT
        ret, ch = ctx.get_option_chain(code, start=best_exp, end=best_exp, option_type=ot)
        if ret != RET_OK or ch is None or ch.empty:
            return None
        # 只取 spot 附近的档 (深 OTM/ITM 无意义且拖慢快照)
        rows = sorted(
            ({"code": str(safe_get(r, "code", default="")),
              "strike": safe_float(safe_get(r, "strike_price"), default=0.0)}
             for _, r in ch.iterrows()),
            key=lambda x: abs(x["strike"] - spot))[:28]
        codes = [r["code"] for r in rows if r["code"]]
        quotes: Dict[str, Dict] = {}
        for i in range(0, len(codes), 20):            # 快照批量上限
            ret, q = ctx.get_market_snapshot(codes[i:i + 20])
            if ret != RET_OK or q is None or q.empty:
                continue
            for _, r in q.iterrows():
                quotes[str(safe_get(r, "code", default=""))] = {
                    "bid": safe_float(safe_get(r, "bid_price"), default=0.0),
                    "ask": safe_float(safe_get(r, "ask_price"), default=0.0),
                    "last": safe_float(safe_get(r, "last_price"), default=0.0),
                    "oi": safe_float(safe_get(r, "option_open_interest"), default=0.0),
                    "iv_broker": safe_float(safe_get(r, "option_implied_volatility"), default=0.0),
                    "delta_broker": safe_float(safe_get(r, "option_delta"), default=0.0),
                }
        return {"ticker": ticker, "spot": spot, "expiry": best_exp, "dte": dte,
                "type": option_type,
                "strikes": [{**r, **quotes.get(r["code"], {})} for r in rows if r["code"] in quotes]}
    except Exception as e:                                       # noqa: BLE001
        print(f"[option_plan] {ticker}: {type(e).__name__}: {e}", file=sys.stderr)
        return None
    finally:
        safe_close(ctx)


# ── 选约 ──────────────────────────────────────────────────────────

def enrich(strikes: List[Dict], spot: float, dte: int) -> List[Dict]:
    """补 mid / spread% / 自算 IV 与 delta (broker 的 0.0 不可信)。"""
    T = max(dte, 1) / 365.0
    out = []
    for s in strikes:
        bid, ask = s.get("bid") or 0.0, s.get("ask") or 0.0
        if bid <= 0 or ask <= 0:
            continue
        mid = (bid + ask) / 2
        iv = implied_vol(mid, spot, s["strike"], T, PARAMS["risk_free"])
        if iv is None:
            ivb = s.get("iv_broker") or 0.0
            iv = ivb / 100.0 if ivb > 0 else None
        out.append({**s, "mid": round(mid, 2),
                    "spread_pct": round((ask - bid) / mid * 100, 1) if mid > 0 else None,
                    "iv": round(iv * 100, 1) if iv else None,
                    "delta": round(bs_delta(spot, s["strike"], T, iv), 3) if iv else None})
    return out


def pick_strike(strikes: List[Dict], iv_pct: Optional[float]) -> Dict:
    """按 delta 带选 + 流动性硬门槛。返回 {chosen, rejected: [(strike, 原因)]}。"""
    if iv_pct is not None and iv_pct >= 70.0:
        lo, hi = PARAMS["delta_band_high_iv"]
    elif iv_pct is not None and iv_pct < 30.0:
        lo, hi = PARAMS["delta_band_low_iv"]      # §13.1 放宽档: 低 IV 允许轻度 OTM
    else:
        lo, hi = PARAMS["delta_band"]
    rejected, eligible = [], []
    for s in strikes:
        if s.get("delta") is None:
            rejected.append((s["strike"], "无法定价 (bid/ask 缺失或穿透)"))
        elif (s.get("oi") or 0) < PARAMS["min_open_interest"]:
            rejected.append((s["strike"], f"OI {s.get('oi'):.0f} < {PARAMS['min_open_interest']}"))
        elif (s.get("spread_pct") or 999) > PARAMS["max_spread_pct"]:
            rejected.append((s["strike"], f"spread {s.get('spread_pct')}% > {PARAMS['max_spread_pct']}%"))
        elif not (lo <= s["delta"] <= hi):
            rejected.append((s["strike"], f"delta {s['delta']} 不在 {lo}-{hi} 带"))
        else:
            eligible.append(s)
    # 带内取 delta 最接近中点者
    mid_d = (lo + hi) / 2
    chosen = min(eligible, key=lambda x: abs(x["delta"] - mid_d)) if eligible else None
    return {"chosen": chosen, "eligible": eligible, "rejected": rejected,
            "delta_band": [lo, hi]}


def rung_premiums(contract: Dict, ladder: List[float], dte: int) -> List[Dict]:
    """标的到每个 rung 时该合约的 BS 估值。**估算不是报价。**"""
    iv = (contract.get("iv") or 0) / 100.0
    if iv <= 0:
        return []
    T = max(dte - PARAMS["fill_days"], 1) / 365.0
    bump = PARAMS["dip_iv_bump_pts"] / 100.0
    out = []
    for px in ladder:
        flat = bs_call(px, contract["strike"], T, iv, PARAMS["risk_free"])
        pumped = bs_call(px, contract["strike"], T, iv + bump, PARAMS["risk_free"])
        out.append({"underlying": px, "premium_flat_iv": round(flat, 2),
                    "premium_iv_bump": round(pumped, 2)})
    return out


def build(ticker: str, ladder: List[float], cap: Optional[float], *,
          target_dte: int, stop: Optional[float], option_type: str = "call",
          min_dte: int = 7, notional_cap: Optional[float] = None,
          at_risk_premium_cap: Optional[float] = None) -> Dict:
    """`cap` = **保费**预算 (分类 cap, 你花出去的钱);
    `notional_cap` = **标的名义**上限 (n_unit / 冲击预算 / gross), 由本函数用
    所选合约的 delta 折成保费后再取小;
    `at_risk_premium_cap` = 单笔 at-risk 天花板折成的保费上限
    (AT_RISK_CAP_FRAC × NAV / OPTION_ATRISK_LOSS_FRAC ≈ $4,250 @ NAV $21.2k —
     接线: confirmed 分类 cap $8.5k > 它 ⇒ **at-risk 是期权入场的真
    binding**, 缺了这条会超买)。三者取小 = 实际保费预算。

    两个参数不能合并成一个 (单位错误): 一张合约花 premium×100 却带来
    delta×100×spot 的名义敞口, 把名义上限当保费花 = 名义敞口数倍于上限, 方向是
    **放松**约束 —— 正是 shock_budget_design §1.2 说的"名义 cap 在期权上完全失效"。
    """
    ivp = iv_logger.percentile(ticker)
    iv_pct = ivp.get("iv_percentile") if not ivp.get("error") else None
    reliable = bool(ivp.get("reliable"))
    gate = iv_gate(iv_pct, reliable)

    chain = fetch_chain(ticker, target_dte, option_type, min_dte=min_dte)
    if not chain:
        return {"ticker": ticker, "error": "链数据不可得 (OpenD? 无期权?)",
                "iv": ivp, "gate": gate}

    strikes = enrich(chain["strikes"], chain["spot"], chain["dte"])
    sel = pick_strike(strikes, iv_pct)
    out = {"ticker": ticker, "spot": chain["spot"], "expiry": chain["expiry"],
           "dte": chain["dte"], "type": option_type,
           "iv": {**ivp, "reliable": reliable}, "gate": gate,
           "delta_band": sel["delta_band"], "chosen": sel["chosen"],
           "eligible_count": len(sel["eligible"]),
           "rejected_sample": sel["rejected"][:6], "ladder": ladder, "stop_underlying": stop}
    if not sel["chosen"]:
        out["error"] = ("无合格 strike —— 流动性门槛或 delta 带内没有可交易档。"
                        "薄链不是 vehicle: 挂得进去挂不出来")
        return out

    rungs = rung_premiums(sel["chosen"], ladder, chain["dte"])
    out["rungs"] = rungs
    eff_cap = cap
    if notional_cap is not None and rungs:
        prem_cap = notional_to_premium_cap(notional_cap, sel["chosen"].get("delta"),
                                           chain["spot"], rungs[0]["premium_flat_iv"])
        if prem_cap is None:
            out["cap_note"] = ("⚠️ 组合层名义上限折不成保费 (delta/spot/premium 缺) — "
                               "本计划**只受分类 cap 约束**, 名义敞口未受限")
        elif eff_cap is None or prem_cap < eff_cap:
            eff_cap = prem_cap
            out["cap_note"] = (
                f"组合层名义上限 ${notional_cap:,.0f} binding → 折成保费预算 "
                f"${prem_cap:,.0f} (Δ{sel['chosen'].get('delta')} × spot {chain['spot']:g}; "
                f"分类 cap ${cap:,.0f})" if cap else
                f"组合层名义上限 ${notional_cap:,.0f} → 保费预算 ${prem_cap:,.0f}")
    if at_risk_premium_cap is not None and (eff_cap is None or at_risk_premium_cap < eff_cap):
        eff_cap = at_risk_premium_cap
        out["cap_note"] = (f"单笔 at-risk 天花板 binding → 保费预算 ${at_risk_premium_cap:,.0f} "
                          f"(5% NAV ÷ S1 loss 0.25; 分类 cap ${cap:,.0f})" if cap else
                          f"单笔 at-risk 天花板 → 保费预算 ${at_risk_premium_cap:,.0f}")
    out["cap_deployment"] = cap
    out["cap_notional"] = notional_cap
    out["cap_at_risk"] = at_risk_premium_cap
    out["cap_used"] = eff_cap
    if eff_cap and rungs:
        # 与正股共用整股/降档/书面例外逻辑, 期权 1 张 = 100 股
        out["size"] = share_ladder(eff_cap, [r["premium_flat_iv"] for r in rungs],
                                   PARAMS["ladder_weights"], multiplier=100, unit="张")
        out["size_iv_bump"] = share_ladder(eff_cap, [r["premium_iv_bump"] for r in rungs],
                                           PARAMS["ladder_weights"], multiplier=100, unit="张")
    if stop:
        T = max(chain["dte"] - PARAMS["fill_days"], 1) / 365.0
        iv = (sel["chosen"].get("iv") or 0) / 100.0
        out["premium_at_stop"] = round(bs_call(stop, sel["chosen"]["strike"], T, iv,
                                               PARAMS["risk_free"]), 2) if iv > 0 else None
    return out


def render(p: Dict) -> str:
    L: List[str] = [f"🧰 期权执行计划 {p['ticker']}"
                    + (f" (spot ${p['spot']:g})" if p.get("spot") else "")]
    g, iv = p["gate"], p["iv"]
    ivline = (f"IV percentile {iv.get('iv_percentile')}% "
              f"(n={iv.get('samples')}{'' if iv.get('reliable') else ' ⚠️<60 不可靠'})"
              f"; 绝对 ATM IV {iv.get('latest_iv_pct')}%"
              if not iv.get("error") else f"⚠️ {iv['error']}")
    L.append(f"   IV 闸门: {ivline} → **{g['verdict']}**")
    L.append(f"      {g['note']}")
    if p.get("error"):
        L.append(f"   ⛔ {p['error']}")
        for s, why in p.get("rejected_sample", []):
            L.append(f"      剔除 ${s:g}: {why}")
        return "\n".join(L)

    c = p["chosen"]
    L.append(f"   选约: {p['ticker']} {p['expiry']} {'C' if p['type'] == 'call' else 'P'}"
             f"{c['strike']:g}  DTE {p['dte']}  delta {c['delta']}"
             f" (带 {p['delta_band'][0]}-{p['delta_band'][1]})")
    L.append(f"      bid {c['bid']:g} / ask {c['ask']:g} / mid {c['mid']:g}"
             f" | spread {c['spread_pct']}% | OI {c['oi']:.0f} | IV {c['iv']}%")
    L.append(f"      {c['code']}   ({p['eligible_count']} 个档过流动性+delta 门槛)")

    size = p.get("size")
    if p.get("cap_note"):
        L.append(f"   预算: {p['cap_note']}")
    L.append("   档位 (期权限价 = 标的到位时的 **BS 估值, 不是报价**):")
    for i, r in enumerate(p.get("rungs", [])):
        n = ""
        if size and i < len(size["tiers"]):
            t = size["tiers"][i]
            n = f" × {t['shares']} 张 (${t['usd']:,.0f})"
        L.append(f"      标的 ${r['underlying']:g} → 限价 ${r['premium_flat_iv']:g}{n}"
                 f"   [IV+{PARAMS['dip_iv_bump_pts']:g}pt: ${r['premium_iv_bump']:g}]")
    L.append(f"      ⚠️ 回调通常伴随 IV 上抬 —— 恒 IV 的限价系统性偏低, 照它挂大概率不成交。"
             f"方括号里是 IV+{PARAMS['dip_iv_bump_pts']:g}pt 情景, 挂那个更容易成但多付 vega")
    if size:
        if size["note"]:
            L.append(f"      {size['note']}")
        if size["exception"]:
            L.append("      ⛔ 未落 decisions.jsonl 书面例外前, 这不是可执行计划")
    if p.get("stop_underlying"):
        pa = p.get("premium_at_stop")
        L.append(f"   止损: **标的**${p['stop_underlying']:g} 触发 (不是期权价)"
                 + (f" — 到那时该合约约值 ${pa:g}" if pa else ""))
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description="期权 vehicle 执行参数 (strike/DTE/限价/张数)")
    ap.add_argument("ticker")
    ap.add_argument("--ladder", nargs="+", type=float, required=True,
                    help="标的阶梯价位 (entry_guard 的输出)")
    ap.add_argument("--cap", type=float,
                    help="**保费**预算 USD (entry_guard 的 caps.deployment_cap = 分类 cap)")
    ap.add_argument("--notional-cap", type=float,
                    help="**标的名义**上限 USD (entry_guard 的 caps.notional_cap = "
                         "n_unit/冲击预算/gross 取小); 本脚本用 delta 折成保费后再取小")
    ap.add_argument("--stop", type=float, help="标的止损位 (结构位或 2N)")
    ap.add_argument("--dte", type=int, default=45,
                    help="目标 DTE (LEAPS 走 §13.2: --dte 270 --min-dte 270 "
                         "= 下限之上最近周期)")
    ap.add_argument("--min-dte", type=int, default=7,
                    help="DTE 硬下限 (纯最近会选中更近但破下限的到期日)")
    ap.add_argument("--type", default="call", choices=["call", "put"])
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    p = build(a.ticker.upper(), a.ladder, a.cap, target_dte=a.dte,
              stop=a.stop, option_type=a.type, min_dte=a.min_dte,
              notional_cap=a.notional_cap)
    print(json.dumps(p, ensure_ascii=False, indent=1) if a.json else render(p))
    return 0 if not p.get("error") else 1


if __name__ == "__main__":
    raise SystemExit(main())
