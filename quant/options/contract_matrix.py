#!/usr/bin/env python3
"""contract_matrix.py — 期权合约矩阵 + 情景表。**判读层, 零授权效力, 不是计划。**

用途: 一眼看清波动率 / strike / DTE, 估算不同情景下的损失与收益和期望值, 帮助在同一笔正股判断下
比较合约。

option_plan 只给**一张**合约 (最近 ≥270 DTE 到期 × Δ 带中点), 被放弃的到期 / strike 看不见。
本模块把同一笔正股判断摊成一张表: 到期 × strike, 每张在**卡上的线**和**系统的钟**下值多少。

    情景 (全部是退出栈本身, 不是自报目标价):
      止损   先触发的那条: 正股收破期权退出线 (授予 cap 的 gate / 卡 stop, 抄底 = 止跌低) 或 S1 保费 −20%
             (抄底无 S1); 按 +5 交易日估值
      S2     正股原地不动 → 第 25 交易日 S2 时间 stop 清仓 (exit_guard opt_s2_days), 按 +25 日估值
      gate   到下一 gate (next_regime.entry_gate), 按 +20 日估值
      trim   到 trim 下沿, 按 +60 日估值
    R = 情景收益 ÷ 止损亏损 (sizing 按风险分钱, 不按张, 所以比较的是每单位风险换来多少)。

    误差带 = 常数 IV 的 BS 重估相对真实 LEAPS 报价的离线校准误差 p10–p90, 按持有天数 × 正股涨跌桶
      (`BANDS`; 用自己的合约样本在私有研究里重新校准)。给定情景下的合约排序在校准中足够稳定 → 本表
      可标 ★; IV 轴取固定网格 {−10, 0, +5} pt。

    期望值 [FYI·未验证]: 零漂移对数随机游走 (σ = HV20, core.bottom_evidence.confirm_prob_rw 同一个件),
      25 交易日内三个结局 —— 先到 gate (或 trim) / 先触发止损 / 都没到 (S2 清仓)。概率对同一只票的所有
      合约几乎相同 (只有 S1 线因合约而异), 所以 EV 比较的是**合约之间的支付形状与时间价值**, 不说明这笔
      该不该做 (零漂移下正股 EV ≈ 0), 也不能跨票比较。概率那一半没有验证过 (校准只覆盖支付那一半)。

不下单, 不写 state, 不进 PLAN / cap / 张数 / Today。张数列只是「按 live 口径能买几张」的读数,
组合层名义臂 (单票 40% / 冲击预算 / gross / 购买力) 没算, 以 entry_guard 为准。

CLI:
    .venv/moomoo/bin/python3 quant/options/contract_matrix.py TICKER [--anchor 100] [--stop 95]
        [--gate 110] [--trim 120] [--mode trend|bottom] [--json]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 直接 `python3 quant/<层>/<模块>.py` 时让 `quant.*` 可 import

from quant.core.paths import add_openapi_path  # noqa: E402

from quant.core import env  # noqa: F401,E402  (.env → os.environ)

import argparse  # noqa: E402
import json  # noqa: E402
from datetime import date  # noqa: E402
from typing import Dict, List, Optional, Tuple  # noqa: E402

add_openapi_path()
from quant.core import book, earnings, technical  # noqa: E402
from quant.core.bottom_evidence import confirm_prob_rw, daily_sigma  # noqa: E402
from quant.core.holdings import live_tickers  # noqa: E402
from quant.core.io_json import read_json  # noqa: E402
from quant.core.num import coerce_float  # noqa: E402
from quant.core.paths import OPTION_PLANS_PATH, POSITIONS_PATH, WATCHLIST_PATH  # noqa: E402
from quant.core.sizing import (  # noqa: E402
    AT_RISK_CAP_FRAC, OPTION_ATRISK_LOSS_FRAC, gate_count, granting_gate_level,
    option_premium_cap_frac, reversal_probe_premium_cap)
from quant.core.timeutil import et_today  # noqa: E402
from quant.data import iv_logger  # noqa: E402
from quant.options import option_plan  # noqa: E402
from quant.options.strike_compare import bs_call  # noqa: E402

PARAMS = {
    "min_dte": 270,               # §13.2 入场下限, 与 entry_guard → option_plan.build(min_dte=270) 同数
    "term_targets": (365, 540),   # §13.2 对照档: 270-365 平台 / Jan+2
    "max_expiries": 3,
    "delta_show": (0.40, 0.92),   # 显示下限 = Δ0.40 绝对禁区线 (§13.1)
    "strikes_per_expiry": 40,
    "h_stop": 5,                  # 交易日
    "h_s2": 25,                   # = exit_guard PARAMS opt_s2_days[1]
    "h_gate": 20,
    "h_trim": 60,
    "ev_horizon": 25,             # = S2 清仓日: 25 日内没到 gate 也没止损 → 被时间 stop 清掉
    "iv_grid_pts": (-10.0, 0.0, 5.0),   # 固定 IV 网格
    "n_up": 2.0,                  # 通用上行列 = 锚 + 2×ATR20 (卡线不在锚之上时也有可比的上行情景; S2 判据是 +1N)
    "delta_grid": (0.90, 0.85, 0.80, 0.75, 0.70, 0.65, 0.60, 0.55, 0.50, 0.45, 0.40),
    "grid_tol": 0.03,             # Δ 网格取档: 流动性合格且 |Δ − 目标| ≤ 本值的最近档优先
    "s1_frac": OPTION_ATRISK_LOSS_FRAC,  # S1 −20% (exit_guard opt_s1_stop)
}
TD_TO_CD = 365.0 / 252.0
R = option_plan.PARAMS["risk_free"]

# 离线校准: e = (投影 − 真实) ÷ 入场权利金 的 p10 / p90, 主域 Δ0.40–0.92、DTE₀ ≥150。
# 真实收益 ≈ 投影收益 − e → 区间 [投影 − p90, 投影 − p10]。
BANDS = {
    5: {"≤−10%": (-0.041, 0.053), "−10~−3%": (-0.035, 0.045), "−3~+3%": (-0.038, 0.044),
        "+3~+10%": (-0.051, 0.041), "≥+10%": (-0.087, 0.044)},
    20: {"≤−10%": (-0.045, 0.092), "−10~−3%": (-0.048, 0.066), "−3~+3%": (-0.054, 0.058),
         "+3~+10%": (-0.075, 0.072), "≥+10%": (-0.154, 0.058)},
    60: {"≤−10%": (-0.063, 0.130), "−10~−3%": (-0.077, 0.101), "−3~+3%": (-0.086, 0.088),
         "+3~+10%": (-0.070, 0.076), "≥+10%": (-0.251, 0.039)},
}


# ── 纯计算 ──────────────────────────────────────────────────────────

def move_bucket(m: float) -> str:
    """正股涨跌桶: ≤−10% / (−10%, −3%] / (−3%, +3%) / [+3%, +10%) / ≥+10%。"""
    if m <= -0.10:
        return "≤−10%"
    if m <= -0.03:
        return "−10~−3%"
    if m < 0.03:
        return "−3~+3%"
    if m < 0.10:
        return "+3~+10%"
    return "≥+10%"


def band(h: int, m: float) -> Tuple[float, float]:
    """持有 h 交易日、正股涨跌 m 时, 真实收益相对投影收益的偏移区间 (lo, hi) (小数)。
    实测只有 5 / 20 / 60 三档, 取最近的一档 (25 日 → 20)。"""
    hb = min(BANDS, key=lambda x: abs(x - h))
    p10, p90 = BANDS[hb][move_bucket(m)]
    return -p90, -p10


def t_after(dte: float, h_td: float) -> float:
    """入场 DTE 下, 过 h 个交易日后的剩余年限。"""
    return max(dte - h_td * TD_TO_CD, 0.0) / 365.0


def premium_at(S: float, K: float, dte: float, h_td: float, iv: float) -> float:
    return bs_call(S, K, t_after(dte, h_td), iv, R)


def s1_level(p0: float, K: float, dte: float, iv: float, frac: float, h_td: float,
             hi: float) -> Optional[float]:
    """正股跌到哪里, 该合约 (h 交易日后) 保费 = 入场 × (1 − frac) → S1 在正股上的投影线。
    hi = 入场正股价; 在 (0, hi] 内找不到 = None (时间价值自己就烧穿了 S1, 不需要跌)。"""
    target = p0 * (1.0 - frac)
    T = t_after(dte, h_td)
    if bs_call(hi, K, T, iv, R) <= target:
        return None
    lo_s, hi_s = 1e-6, hi
    if bs_call(lo_s, K, T, iv, R) > target:
        return None
    for _ in range(80):
        mid = (lo_s + hi_s) / 2
        if bs_call(mid, K, T, iv, R) < target:
            lo_s = mid
        else:
            hi_s = mid
    return (lo_s + hi_s) / 2


def first_trigger(p0: float, K: float, dte: float, iv: float, anchor: float,
                  stop_line: Optional[float], s1_frac: Optional[float]) -> Optional[Dict]:
    """先触发的止损 → {"loss", "by", "level"}。正股往下走, 先碰到的那条线先执行:
    S1 投影线更高 (离锚更近) = S1 先到, 亏损就是 s1_frac; 否则按退出线处的投影亏损。"""
    h = PARAMS["h_stop"]
    s1 = s1_level(p0, K, dte, iv, s1_frac, h, anchor) if s1_frac else None
    if stop_line is not None and stop_line < anchor:
        loss_line = 1.0 - premium_at(stop_line, K, dte, h, iv) / p0
        if s1 is not None and s1 >= stop_line and loss_line > s1_frac:
            return {"loss": s1_frac, "by": "S1", "level": s1}
        return {"loss": max(loss_line, 0.0), "by": "线", "level": stop_line}
    if s1 is not None:
        return {"loss": s1_frac, "by": "S1", "level": s1}
    return None


def rw_three(anchor: float, lo: float, hi: float, sigma_d: float, horizon: int) -> Optional[Dict]:
    """零漂移对数随机游走, horizon 根内: 先收盘越 hi / 先收破 lo / 都没有。
    下行 = 对数价格镜像后的同一个 confirm_prob_rw (两条线各向外移的离散监控修正对称)。"""
    if not (lo and hi and anchor and lo < anchor < hi and sigma_d and sigma_d > 0):
        return None
    up = confirm_prob_rw(anchor, lo, hi, sigma_d, horizon)
    dn = confirm_prob_rw(1.0 / anchor, 1.0 / hi, 1.0 / lo, sigma_d, horizon)
    if up is None or dn is None:
        return None
    return {"up": up, "down": dn, "neither": max(0.0, 1.0 - up - dn)}


def contracts_affordable(cap: Optional[float], premium: float) -> Optional[int]:
    if cap is None or premium <= 0:
        return None
    return int(cap // (premium * 100))


def ev_target(anchor: float, lines: Dict) -> Optional[float]:
    """EV 的上行目标: 下一 gate → trim 下沿 → 锚 + 2N, 取第一条在锚之上的。"""
    for k in ("gate", "trim", "n2"):
        v = lines.get(k)
        if v and v > anchor:
            return v
    return None


def contract_row(s: Dict, *, dte: int, spot: float, anchor: float, lines: Dict,
                 mode: str, nav: Optional[float], gate_no: int, sigma_d: Optional[float]) -> Optional[Dict]:
    """一张合约 (option_plan.enrich 之后的行) → 属性 + 情景 + EV + 张数。算不了 = None。"""
    iv = (s.get("iv") or 0) / 100.0
    mid = s.get("mid") or 0.0
    K = s["strike"]
    if iv <= 0 or mid <= 0:
        return None
    T0 = dte / 365.0
    p0 = mid if abs(anchor - spot) < 1e-9 else bs_call(anchor, K, T0, iv, R)
    if p0 <= 0:
        return None
    intrinsic = max(anchor - K, 0.0)
    theta = (bs_call(anchor, K, max(dte - 1, 0) / 365.0, iv, R) - bs_call(anchor, K, T0, iv, R)) / p0
    vega = {pt: bs_call(anchor, K, T0, max(iv + pt / 100.0, 0.01), R) / bs_call(anchor, K, T0, iv, R) - 1.0
            for pt in PARAMS["iv_grid_pts"] if pt}
    s1_frac = PARAMS["s1_frac"] if mode == "trend" else None
    ft = first_trigger(p0, K, dte, iv, anchor, lines.get("stop"), s1_frac)

    def ret(level, h):
        return premium_at(level, K, dte, h, iv) / p0 - 1.0

    sc = {"stop": -ft["loss"] if ft else None,
          "s2": ret(anchor, PARAMS["h_s2"])}
    if lines.get("n2") and lines["n2"] > anchor:
        sc["n2"] = ret(lines["n2"], PARAMS["h_gate"])
    if lines.get("gate") and lines["gate"] > anchor:
        sc["gate"] = ret(lines["gate"], PARAMS["h_gate"])
    if lines.get("trim") and lines["trim"] > anchor:
        sc["trim"] = ret(lines["trim"], PARAMS["h_trim"])
    L = ft["loss"] if ft and ft["loss"] > 0 else None
    r_mult = {k: (sc[k] / L if (L and sc.get(k) is not None) else None) for k in ("n2", "gate", "trim")}
    spread_rt = (s.get("spread_pct") or 0.0) / 100.0

    ev = None
    tgt = ev_target(anchor, lines)
    if ft and tgt and sigma_d:
        lo_line = ft["level"]                 # 先触发的那条 (S1 投影线或退出线)
        pr = rw_three(anchor, lo_line, tgt, sigma_d, PARAMS["ev_horizon"])
        if pr:
            up_ret = ret(tgt, PARAMS["h_gate"])
            e = pr["up"] * up_ret - pr["down"] * ft["loss"] + pr["neither"] * sc["s2"] - spread_rt
            ev = {"pct": e, "R": (e / L if L else None), "probs": pr, "target": tgt, "stop_level": lo_line}

    if mode == "bottom":
        cap = reversal_probe_premium_cap(nav, L) if L else None
        cap_note = "gate1 2.5% NAV ÷ 止损投影亏损"
    else:
        arms = [x for x in ((option_premium_cap_frac(gate_no) * nav) if nav else None,
                            (AT_RISK_CAP_FRAC * nav / OPTION_ATRISK_LOSS_FRAC) if nav else None)
                if x is not None]
        cap = min(arms) if arms else None
        cap_note = f"gate{gate_no} 保费上限与 at-risk 天花板取小"
    n = contracts_affordable(cap, p0)
    return {
        "code": s.get("code"), "strike": K, "delta": s.get("delta"), "iv": s.get("iv"),
        "bid": s.get("bid"), "ask": s.get("ask"), "mid": mid, "p0": round(p0, 2),
        "oi": s.get("oi"), "spread_pct": s.get("spread_pct"),
        "one_contract": p0 * 100, "one_nav_frac": (p0 * 100 / nav) if nav else None,
        "extrinsic_frac": max(p0 - intrinsic, 0.0) / p0,
        "theta_day": theta, "vega": vega,
        "leverage": ((s.get("delta") or 0) * anchor / p0),
        "breakeven": K + p0,
        "first_trigger": ft, "scen": sc, "R": r_mult, "ev": ev,
        "cap": cap, "cap_note": cap_note, "n": n,
        "risk_usd": (n * p0 * 100 * L) if (n and L) else 0.0,
    }


def stock_row(anchor: float, lines: Dict, sigma_d: Optional[float]) -> Optional[Dict]:
    """正股同一组线的对照 (零杠杆, 无时间价值)。"""
    stop = lines.get("stop")
    if not stop or stop >= anchor:
        return None
    L = 1.0 - stop / anchor
    sc = {"stop": -L, "s2": 0.0}
    for k in ("n2", "gate", "trim"):
        if lines.get(k) and lines[k] > anchor:
            sc[k] = lines[k] / anchor - 1.0
    tgt = ev_target(anchor, lines)
    ev = None
    if tgt and sigma_d:
        pr = rw_three(anchor, stop, tgt, sigma_d, PARAMS["ev_horizon"])
        if pr:
            e = pr["up"] * (tgt / anchor - 1.0) - pr["down"] * L
            ev = {"pct": e, "R": e / L, "probs": pr}
    return {"scen": sc, "R": {k: (sc[k] / L if sc.get(k) is not None else None) for k in ("n2", "gate", "trim")},
            "loss": L, "ev": ev}


def pick_expiries(cands: List[Tuple[str, int]], min_dte: int = PARAMS["min_dte"],
                  targets: Tuple[int, ...] = PARAMS["term_targets"],
                  k: int = PARAMS["max_expiries"]) -> List[Tuple[str, int]]:
    """≥ min_dte 的到期里: 最近的一个 (= live 选约到期) + 最接近各对照档的, 去重, 至多 k 个。"""
    ok = sorted((c for c in cands if c[1] >= min_dte), key=lambda c: c[1])
    if not ok:
        return []
    out = [ok[0]]
    for t in targets:
        c = min(ok, key=lambda c: abs(c[1] - t))
        if c not in out:
            out.append(c)
    return sorted(out, key=lambda c: c[1])[:k]


def atm_iv(rows: List[Dict], spot: float) -> Optional[float]:
    r = [x for x in rows if x.get("iv")]
    return min(r, key=lambda x: abs(x["strike"] - spot))["iv"] if r else None


def skew(rows: List[Dict]) -> Optional[float]:
    """Δ0.70 档 IV − Δ0.50 档 IV (vol pt)。"""
    r = [x for x in rows if x.get("iv") and x.get("delta")]
    if len(r) < 2:
        return None
    a = min(r, key=lambda x: abs(x["delta"] - 0.70))
    b = min(r, key=lambda x: abs(x["delta"] - 0.50))
    return (a["iv"] - b["iv"]) if a is not b else None


def grid_rows(rows: List[Dict], liquid: set, keep: set) -> List[Dict]:
    """Δ 网格取档: 每个目标 Δ 在容差内取最近档, 流动性合格的优先; 容差内一张都没有 = 该档跳过
    (链上那个 Δ 附近没有合约)。keep 里的 (live 选约) 一定保留。返回按行权价升序、去重。"""
    have = [r for r in rows if r.get("delta") is not None]
    out = {id(r): r for r in have if id(r) in keep}
    for d in PARAMS["delta_grid"]:
        near = [r for r in have if abs(r["delta"] - d) <= PARAMS["grid_tol"]]
        pool = [r for r in near if id(r) in liquid] or near
        if pool:
            r = min(pool, key=lambda x: abs(x["delta"] - d))
            out[id(r)] = r
    return sorted(out.values(), key=lambda x: x["strike"])


def build_matrix(ticker: str, spot: float, chains: List[Dict], *, anchor: float, lines: Dict,
                 mode: str, nav: Optional[float], gate_no: int, iv_ctx: Dict,
                 sigma_d: Optional[float], live_expiry: Optional[str], show_all: bool = False) -> Dict:
    """纯函数: chains = [{"expiry", "dte", "strikes": option_plan.enrich 之后的行}]。"""
    lo, hi = PARAMS["delta_show"]
    expiries = []
    for ch in chains:
        rows = ch["strikes"]
        sel = option_plan.pick_strike(rows, iv_ctx.get("eff_pct"))
        eligible = {id(x) for x in sel["eligible"]}
        why = {k: w for k, w in sel["rejected"]}
        live_pick = sel["chosen"]["strike"] if (sel["chosen"] and ch["expiry"] == live_expiry) else None
        shown = [x for x in rows if x.get("delta") is not None and lo <= x["delta"] <= hi]
        if not show_all:
            # 流动性合格 = pick_strike 没以 OI / spread / 无法定价剔除 (Δ 带外不影响「流动」)
            illiquid = {k for k, w in sel["rejected"] if not str(w).startswith("delta")}
            liquid = {id(x) for x in shown if x["strike"] not in illiquid}
            keep = {id(x) for x in shown if x["strike"] == live_pick}
            shown = grid_rows(shown, liquid, keep)
        out_rows = []
        for s in sorted(shown, key=lambda x: x["strike"]):
            row = contract_row(s, dte=ch["dte"], spot=spot, anchor=anchor, lines=lines, mode=mode,
                               nav=nav, gate_no=gate_no, sigma_d=sigma_d)
            if row is None:
                continue
            row["eligible"] = id(s) in eligible
            row["why_not"] = None if row["eligible"] else why.get(s["strike"])
            row["live_pick"] = (live_pick == s["strike"])
            out_rows.append(row)
        expiries.append({"expiry": ch["expiry"], "dte": ch["dte"], "atm_iv": atm_iv(rows, spot),
                         "skew": skew(rows), "live": ch["expiry"] == live_expiry,
                         "delta_band": sel["delta_band"], "rows": out_rows})
    # ★ = 带内 (Δ 带 + 流动性) 合约里各情景最好的那张 (给定情景下的排序可信)
    pool = [r for e in expiries for r in e["rows"] if r["eligible"]]
    stars = {}
    for key, getter in (("stop", lambda r: r["scen"].get("stop")), ("s2", lambda r: r["scen"].get("s2")),
                        ("R_n2", lambda r: r["R"].get("n2")),
                        ("R_gate", lambda r: r["R"].get("gate")), ("R_trim", lambda r: r["R"].get("trim")),
                        ("ev", lambda r: (r["ev"] or {}).get("R"))):
        cand = [r for r in pool if getter(r) is not None]
        if cand:
            stars[key] = max(cand, key=getter)["code"]
    bands = {}
    for key, level, h in (("stop", lines.get("stop"), PARAMS["h_stop"]), ("s2", anchor, PARAMS["h_s2"]),
                          ("n2", lines.get("n2"), PARAMS["h_gate"]),
                          ("gate", lines.get("gate"), PARAMS["h_gate"]), ("trim", lines.get("trim"), PARAMS["h_trim"])):
        if level:
            bands[key] = band(h, level / anchor - 1.0)
    return {"ticker": ticker, "spot": spot, "anchor": anchor, "lines": lines, "mode": mode,
            "nav": nav, "gate_no": gate_no, "iv": iv_ctx, "sigma_d": sigma_d,
            "expiries": expiries, "stars": stars, "bands": bands,
            "stock": stock_row(anchor, lines, sigma_d)}


# ── 卡片 / 取数 ─────────────────────────────────────────────────────

def load_card(ticker: str) -> Tuple[Optional[dict], str]:
    """在持 (holdings.live_tickers, 含只剩期权腿的 synthetic 行 —— 期权的退出线住正股卡) → positions 卡;
    否则 watchlist 卡。positions 里平仓后留下的归档行不算。"""
    pos = read_json(POSITIONS_PATH) if POSITIONS_PATH.exists() else {}
    if ticker in live_tickers(pos or {}):
        return (pos.get("tickers") or {}).get(ticker), "持仓"
    wl = read_json(WATCHLIST_PATH) if WATCHLIST_PATH.exists() else {}
    card = (wl.get("tickers") or {}).get(ticker)
    return (card, "候选") if card else (None, "无卡")


def card_lines(ticker: str, card: Optional[dict]) -> Tuple[Dict, str, Dict]:
    """卡 → (lines, mode, 来源注释)。lines = {"stop", "gate", "trim"}。
    trend: 退出线 = max(卡 stop, 授予 cap 的 gate) —— 与 entry_guard 给 option_plan.build 的 stop 同式 (S5g);
    bottom: 止跌低 (option_plans.json 抄底计划的 support, 否则卡上已盖 reversal_probe 章的 stop), 无 S1。"""
    agent = (card or {}).get("agent") or {}
    zones = agent.get("zones") or {}
    src: Dict[str, str] = {}
    plans = (read_json(OPTION_PLANS_PATH) or {}).get("plans") or {} if OPTION_PLANS_PATH.exists() else {}
    plan = plans.get(ticker) or {}
    if plan and str(plan.get("valid_until") or "9999") < et_today():
        plan = {}                                   # 过期 = 错过不追, 不再按抄底线算
    card_stop = coerce_float(zones.get("stop_level"))
    if plan.get("entry_class") == "reversal_probe" and coerce_float(plan.get("support")):
        mode, stop = "bottom", coerce_float(plan.get("support"))
        src["stop"] = f"抄底计划止跌低 ({plan.get('signal_date')}, 有效至 {plan.get('valid_until')})"
    elif agent.get("entry_class") == "reversal_probe" and card_stop:
        mode, stop = "bottom", card_stop
        src["stop"] = "卡 stop (reversal_probe 章 = 止跌低)"
    else:
        mode = "trend"
        g = granting_gate_level(card)
        cands = [v for v in (card_stop, g) if v is not None]
        stop = max(cands) if cands else None
        src["stop"] = ("授予 cap 的 gate" if (g is not None and stop == g) else "卡 stop") if stop else "无"
    gate = coerce_float(((agent.get("next_regime") or {}).get("entry_gate") or {}).get("level"))
    trim = zones.get("trim_zone")
    trim = coerce_float(trim[0]) if isinstance(trim, list) and trim else None
    src["gate"] = "next_regime.entry_gate" if gate else "无"
    src["trim"] = "trim 下沿" if trim else "无"
    return {"stop": stop, "gate": gate, "trim": trim}, mode, src


def fetch_chains(ticker: str) -> Optional[Dict]:
    from common import create_quote_context, safe_close
    code = f"US.{ticker}"
    ctx = None
    try:
        ctx = create_quote_context()
        spot = option_plan.spot_of(ctx, code)
        if not spot:
            return None
        cands = option_plan.list_expiries(ctx, code)
        live = option_plan.pick_expiry(cands, PARAMS["min_dte"], PARAMS["min_dte"])
        chains = []
        for exp, dte in pick_expiries(cands):
            raw = option_plan.chain_quotes(ctx, code, exp, spot, "call", PARAMS["strikes_per_expiry"])
            chains.append({"expiry": exp, "dte": dte, "strikes": option_plan.enrich(raw, spot, dte)})
        return {"spot": spot, "chains": chains, "live_expiry": live}
    finally:
        safe_close(ctx)


def vol_panel(ticker: str, iv_ctx: Dict) -> Dict:
    rows = book.bars(ticker)
    hv20 = (technical.hv(rows, 20) or [None])[-1] if rows else None
    hv60 = (technical.hv(rows, 60) or [None])[-1] if rows else None
    hvp = book.hv20_percentile_of(ticker) or {}
    ev = earnings.event_state(ticker, et_today())
    ivp = iv_ctx.get("ivp") or {}
    lp = iv_logger.percentile(ticker, series="leaps")
    return {"atm30": ivp.get("latest_iv_pct"), "atm30_date": ivp.get("latest_date"),
            "leaps_ivp": None if lp.get("error") else lp.get("iv_percentile"),
            "leaps_n": 0 if lp.get("error") else lp.get("samples"),
            "ivp": iv_ctx.get("iv_pct"), "ivp_n": ivp.get("samples"),
            "ivp_reliable": iv_ctx.get("reliable"), "hv20": hv20, "hv60": hv60, "hv20_pct": hvp.get("pct"),
            "earnings_day": ev.get("reaction_day"), "earnings_note": ev.get("note"),
            "sigma_d": daily_sigma(rows) if rows else None}


# ── 渲染 ─────────────────────────────────────────────────────────────

def _p(x, d=1, sign=True):
    if x is None:
        return "—"
    return f"{x * 100:+.{d}f}%" if sign else f"{x * 100:.{d}f}%"


def _r(x):
    return "—" if x is None else f"{x:.2f}"


def render(m: Dict, vol: Dict, src: Dict) -> str:
    L, t, a = m["lines"], m["ticker"], m["anchor"]
    out = [f"📐 合约矩阵 {t} · 现价 {m['spot']:g} · 锚 {a:g}"
           + (" (现价)" if abs(a - m["spot"]) < 1e-9 else " (--anchor; 入场权利金 = BS 投影)")
           + f" · {'🔁 抄底 (止损 = 止跌低, 无 S1)' if m['mode'] == 'bottom' else '🚀 趋势 (退出线 ×1 + S1 −20%)'}",
           "   判读层 · 零授权 · 不是计划 —— 授权与张数以 entry_guard 为准"]

    def lv(k):
        if not L.get(k):
            return f"无 ({src.get(k)})"
        warn = " ⚠️已在锚之下" if k in ("gate", "trim") and L[k] <= a else ""
        return f"{L[k]:g} ({src.get(k)}){warn}"
    out.append(f"   线: 止损 {lv('stop')} · 下一 gate {lv('gate')} · trim {lv('trim')}"
               + (f" · +2N {L['n2']:.2f} (ATR20 {L['atr']:.2f})" if L.get("n2") else ""))
    if any(L.get(k) and L[k] <= a for k in ("gate", "trim")):
        out.append("   ⚠️ 卡上的上行线已在锚之下 (卡可能待重画) —— 上行情景只剩 +2N 通用列")
    hv20, hv60, atm30 = vol.get("hv20"), vol.get("hv60"), vol.get("atm30")
    ratio = (atm30 / 100.0 / hv20) if (atm30 and hv20) else None
    ivp, hvpct = vol.get("ivp"), vol.get("hv20_pct")
    d30 = vol.get("atm30_date")
    stale = ""
    if d30:
        try:
            age = (date.fromisoformat(et_today()) - date.fromisoformat(d30)).days
            stale = f" ⚠️{age} 天前" if age > 5 else ""
        except ValueError:
            pass
    out.append(f"   波动率: 30D ATM IV {f'{atm30:.1f}%' if atm30 is not None else '—'}"
               + (f" ({d30}{stale})" if d30 else "")
               + f" · IVP {ivp if ivp is not None else '—'} (n={vol.get('ivp_n') or 0}"
               f"{'' if vol.get('ivp_reliable') else ', <60 不可靠'})"
               f" · HV20 {_p(hv20, sign=False)} (252 日分位 {hvpct if hvpct is not None else '—'})"
               f" · HV60 {_p(hv60, sign=False)} · IV÷HV20 {f'{ratio:.2f}' if ratio else '—'}")
    out.append(f"   LEAPS ATM IV 分位: " + (f"{vol['leaps_ivp']} (n={vol['leaps_n']}{'' if vol['leaps_n'] >= 60 else ', <60 攒样本中'})"
                                        if vol.get("leaps_ivp") is not None else "攒样本中 (iv_logger 记 ≥270 DTE)")
               + " —— 只描述不进门; 上面的 IV 门用的是 30D 口径")
    hard = (m["iv"] or {}).get("hard") or {}
    out.append(f"   IV 硬门槛: {'✓ 放行' if hard.get('allowed') else '⛔ VETO → 本档正股执行, 下表只看代价'}"
               f" — {hard.get('reason')}")
    term = " · ".join(f"{e['expiry']} {e['dte']}d {e['atm_iv']:.1f}%" + (" ◀live" if e["live"] else "")
                      for e in m["expiries"] if e.get("atm_iv"))
    out.append(f"   期限结构 (ATM IV): {term or '—'}")
    if vol.get("earnings_day"):
        out.append(f"   财报: 下次反应日 {vol['earnings_day']} —— 表内合约都跨这次财报 (财报后 IV 通常回落)")
    first = next((r for e in m["expiries"] for r in e["rows"]), None)
    nav = m.get("nav")
    if first and m["mode"] == "bottom":
        out.append(f"   预算: {first['cap_note']} —— 逐张不同, 见「可买」列" + (f" · NAV ${nav:,.0f}" if nav else ""))
    elif first:
        out.append((f"   预算: {first['cap_note']} = ${first['cap']:,.0f}" if first.get("cap") else
                    f"   预算: {first['cap_note']} 算不出 (NAV 缺?)") + (f" · NAV ${nav:,.0f}" if nav else ""))
    b = m["bands"]
    names = {"stop": "止损", "s2": "S2", "n2": "+2N", "gate": "gate", "trim": "trim"}
    out.append("   估值 = 常数 IV 的 BS; 实测误差 p10–p90 (真实 − 投影, pp 入场权利金): "
               + " · ".join(f"{names[k]} {b[k][0] * 100:+.0f}~{b[k][1] * 100:+.0f}" for k in names if k in b))
    st = m["stars"]

    def star(r, k):
        return "★" if st.get(k) == r["code"] else " "
    for e in m["expiries"]:
        out.append("")
        out.append(f"── {e['expiry']} · DTE {e['dte']}"
                   + (f" · ATM IV {e['atm_iv']:.1f}%" if e.get("atm_iv") is not None else "")
                   + (f" · skew Δ0.7−Δ0.5 {e['skew']:+.1f}pt" if e.get("skew") is not None else "")
                   + f" · Δ 带 {e['delta_band'][0]}–{e['delta_band'][1]}" + (" · ◀ live 到期" if e["live"] else "") + " ──")
        out.append("   行权     Δ   IV%     mid   一张$ (NAV)  可买  点差 时间值   θ/日  IV−10  杠杆  盈亏平衡")
        for r in e["rows"]:
            flag = "◀ live 选约" if r["live_pick"] else ("" if r["eligible"] else f"带外: {r['why_not']}")
            n = r["n"] if r["n"] is not None else "—"
            out.append(f"  {r['strike']:>7g}C {r['delta']:.2f} {r['iv']:>5.1f} {r['p0']:>7.2f} "
                       f"{r['one_contract']:>8,.0f} ({_p(r['one_nav_frac'], 0, False):>4}) {n:>4} "
                       f"{(r['spread_pct'] or 0):>4.1f}% {_p(r['extrinsic_frac'], 0, False):>5} {_p(r['theta_day'], 2):>6} "
                       f"{_p(r['vega'].get(-10.0), 0):>6} {r['leverage']:>4.1f}x {r['breakeven']:>8.2f}  {flag}")
        out.append("   行权   止损(先触发@线)    S2横盘   +2N(20d)  gate(20d)  trim(60d) │  R+2N R_gate R_trim │ EV[FYI]")
        for r in e["rows"]:
            sc, ft, ev = r["scen"], r["first_trigger"] or {}, r["ev"] or {}
            trig = f"{ft.get('by', '—')}@{ft['level']:.1f}" if ft.get("level") else "—"
            out.append(f"  {r['strike']:>7g}C {_p(sc.get('stop')):>7}{star(r, 'stop')} {trig:>9}"
                       f" {_p(sc.get('s2')):>8}{star(r, 's2')} {_p(sc.get('n2')):>9}  {_p(sc.get('gate')):>9}  {_p(sc.get('trim')):>9} │"
                       f" {_r(r['R'].get('n2')):>5}{star(r, 'R_n2')}{_r(r['R'].get('gate')):>5}{star(r, 'R_gate')}"
                       f"{_r(r['R'].get('trim')):>5}{star(r, 'R_trim')}│ "
                       + (f"{_p(ev.get('pct'))} ({ev['R']:+.2f}R){star(r, 'ev')}" if ev.get("R") is not None else "—"))
    sr = m.get("stock")
    if sr:
        sc, ev = sr["scen"], sr.get("ev") or {}
        out.append("")
        out.append(f"  正股对照 (同一组线, 零杠杆零时间价值): 止损 {_p(sc['stop'])} · S2 横盘 +0.0% · +2N {_p(sc.get('n2'))}"
                   f" · gate {_p(sc.get('gate'))} · trim {_p(sc.get('trim'))} │ R+2N {_r(sr['R'].get('n2'))}"
                   f" · R_gate {_r(sr['R'].get('gate'))} · R_trim {_r(sr['R'].get('trim'))}"
                   + (f" │ EV {_p(ev.get('pct'))} ({ev['R']:+.2f}R)" if ev.get("R") is not None else ""))
    pr = next(((r["ev"] or {}) for e in m["expiries"] for r in e["rows"] if r.get("ev")), None)
    if pr:
        p = pr["probs"]
        tname = next((names[k] for k in ("gate", "trim", "n2") if L.get(k) == pr["target"]), "目标")
        out.append(f"  EV [FYI·未验证]: 零漂移对数随机游走 σ=HV20, 25 交易日内 先到{tname} {pr['target']:.2f} {p['up']:.0%}"
                   f" / 先止损 {p['down']:.0%} / 都没到 → S2 清仓 {p['neither']:.0%} (S1 先触发的合约止损线不同, 概率逐张略异);"
                   " 扣点差往返。")
        out.append("     比的是同一只票各合约的支付形状与时间价值 —— 不说明该不该做 (零漂移下正股 EV≈0), 不能跨票比。")
    out.append("  ★ = 表内带内 (Δ 带 + 流动性) 各列最好的一张 (给定情景下的合约间排序, 不是跨情景的推荐)。"
               "组合层名义臂 (40% / 冲击 / gross / 购买力) 未计。")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description="期权合约矩阵 + 情景表 (判读层, 零授权)")
    ap.add_argument("ticker")
    ap.add_argument("--anchor", type=float, help="入场正股价 (默认现价; 按 z1 回踩挂单就填 z1 档)")
    ap.add_argument("--stop", type=float, help="期权退出线 (默认取卡)")
    ap.add_argument("--gate", type=float, help="下一 gate (默认取卡)")
    ap.add_argument("--trim", type=float, help="trim 下沿 (默认取卡)")
    ap.add_argument("--mode", choices=("trend", "bottom"), help="默认按卡 / 抄底计划自动判")
    ap.add_argument("--all", action="store_true", help="列出 Δ0.40–0.92 全部行权价 (默认按 Δ 网格取档)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    tk = a.ticker.upper()

    card, bucket = load_card(tk)
    lines, mode, src = card_lines(tk, card)
    for k in ("stop", "gate", "trim"):
        if getattr(a, k) is not None:
            lines[k], src[k] = getattr(a, k), "CLI"
    mode = a.mode or mode
    hvp = (book.hv20_percentile_of(tk) or {}).get("pct")
    iv_ctx = option_plan.iv_context(tk, hvp)
    vol = vol_panel(tk, iv_ctx)
    data = fetch_chains(tk)
    if not data or not data["chains"]:
        print(f"⛔ {tk}: 期权链拉不到 (OpenD? 无 ≥{PARAMS['min_dte']} DTE 到期?)", file=sys.stderr)
        return 1
    anchor = a.anchor or data["spot"]
    atr = book.atr20_of(tk)
    if atr:
        lines["atr"], lines["n2"] = atr, anchor + PARAMS["n_up"] * atr
    nav = book.latest_nav()
    # gate 档: 0 也按 gate1 算保费上限 —— 与 entry_guard `_g = caps.get("gate_no") or 1` 同口径
    m = build_matrix(tk, data["spot"], data["chains"], anchor=anchor, lines=lines,
                     mode=mode, nav=nav, gate_no=max(gate_count(card), 1), iv_ctx=iv_ctx,
                     sigma_d=vol.get("sigma_d"), live_expiry=data["live_expiry"], show_all=a.all)
    m["card_bucket"] = bucket
    if a.json:
        m2 = {**m, "iv": {k: v for k, v in iv_ctx.items() if k != "ivp"}, "vol": vol, "sources": src}
        print(json.dumps(m2, ensure_ascii=False, indent=1, default=str))
    else:
        print(render(m, vol, src))
    return 0


if __name__ == "__main__":
    sys.exit(main())
