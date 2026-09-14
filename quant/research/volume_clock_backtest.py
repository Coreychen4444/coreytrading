#!/usr/bin/env python3
"""volume_clock_backtest — 修复 gate 确认口径: 量的钟 vs 日历钟 + 单日量腿 (预注册 v1, 锁定)。

完整研究协议应保存在 private preregistration。问题: 平均量的收盘站上 gate 时, 基准当日放量会把
个股的相对量比罚掉; 低单日量但厚累计库存也可能说明市场接受了这个价。锁定口径: 财报反应日剔除 /
自身 ADV 不除基准 / K=2.0 单点 / 替代 days 腿 + 量腿 + fastlane (不叠加)。

## 世界 (共同锚 p = 价格腿 ×2 首满足日; 终点 p+20 / p+60; ITT; 池与统计原样复用 relative_volume)
W0  ×2 + 绝对量比 ≥1.2        round-0 回归锚
W1  ×2 + RelVR ≥1.2           round-0 裁决格 (次要对照)
W3  ×2 无量腿                 参考
W1F W1 或 快车道              **live 镜像 = 主对照** (常量 import 自 regime_transition, 不抄第二份)
W4  量的钟: streak(收盘>gate) 内 Σ vol/SMA20 ≥ K=2.0, 反应日贡献 0, 跌回归零   **裁决格**
W4_k15 / W4_k30 / W4_vap / W4_dnx / W4_noE = 稳健性臂, 只报不裁

## 裁决 (W4 − W1F, T60 全样本, 运行前锁定)
V: Δmean CI90 下界 ≥0 且 Δ止损率 CI90 上界 < +3pp 且 Δ覆盖率 CI90 下界 > 0
K: 其余 = 维持现行。no_call: Kish<30 / 回归不复现 (W1−W0 必须复现 round-0 报告) / 数据不足。

Usage: .venv/moomoo/bin/python3 quant/research/volume_clock_backtest.py [--json] [--report PATH]
       [--kdir DIR --earn-dir DIR]   # 数据目录只在本机 (gitignore), worktree 里显式指过去
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 直接 `python3 quant/<层>/<模块>.py` 时让 `quant.*` 可 import

import argparse  # noqa: E402
import bisect  # noqa: E402
import json  # noqa: E402
from typing import Dict, List, Optional, Set  # noqa: E402

from quant.core.sectors import SEMI_SMH  # noqa: E402  冻结映射 (W1F 的 RelVR 基准)
from quant.decision import regime_transition as rt  # noqa: E402  live 常量同源: fastlane_k / 量腿阈值 / 分母地板
from quant.research import relative_volume_backtest as rv  # noqa: E402  池 / 锚 / 终点 / 统计原样复用
from quant.research.gate_backtest import (  # noqa: E402
    load, sma_series, episodes_of, confirmed_swing_highs,
)
from quant.research.gate_backtest2 import (  # noqa: E402
    atr_series, confirmed_swing_lows, cluster_gate, vol_ok,
)

# ---------- 锁定项 (预注册; 测试逐条钉死) ----------

K_PRIMARY = 2.0                          # 裁决格: 两个平均日 = 与现行 ×2 同尺
K_ROBUST = (1.5, 3.0)                    # 稳健性臂, 只报不裁
FASTLANE_K = rt.PARAMS["fastlane_k"]     # 0.5 — live 同源
VOL_RATIO = rv.VOL_RATIO                 # 1.2 = **裁决当时的 live 值**, 研究层自己的历史常量 (rv.VOL_RATIO)。
                                         # ⚠️ **不再** import rt.RECLAIM_VOL_RATIO_MIN (live 已是 1.1):
                                         # W1/W1F 是 round-0 与 round-1/2 裁决时的世界, 跟着 live 漂 = 回归门与既往裁决
                                         # 不可复现 (快照就该冻住)。
REL_FLOOR = rt.REL_FLOOR                 # 0.4 — 与 rv.REL_FLOOR 同值 (V6: 两份常量的对齐由测试钉)
STOP_GUARD = rv.STOP_GUARD               # +3pp — 与 round-0 同一常量
KISH_MIN = 30
DECISION_T = 60
BASE = "W1F"                             # 主对照 = live 镜像
BASE2 = "W1"                             # 次要对照 = round-0 裁决格
WORLDS = ("W0", "W1", "W3", "W1F", "W4", "W4_k15", "W4_k30", "W4_vap", "W4_dnx", "W4_noE")
CMPS = ("W4", "W4_k15", "W4_k30", "W4_vap", "W4_dnx", "W4_noE")
BOOT_SEED = 20260908
EPS = 1e-12                              # acc + EPS ≥ K: 两个 1.0 之和恰等于 2.0 须触发

# 回归复现锚 = round-0 回测 Verdict 读数 (T60 全样本, 私有研究记录):
# 复现不了 = 环境有问题 = 中止, 不带着可疑环境往下裁 (hvn_gate 纪律)。
REGRESSION_ROUND0 = {"n_total": 2874, "n60": 2843, "kish": 168.0,
                   "dmean": 0.0048, "dmean_ci": (0.0016, 0.0079),
                   "dstop": 0.0095, "dcover": 0.037}    # tmp/backtest/relvol_report.md 原文 (T60 全样本行)
TOL_PP2 = 0.00005 + 1e-9                 # 印刷精度 0.01pp 的一半 (Δmean / CI / Δ止损率)
TOL_PP1 = 0.0005 + 1e-9                  # 印刷精度 0.1pp 的一半 (Δ覆盖率)


# ---------- 纯函数件 ----------

def reaction_days_of(ticker: str, dates: List[str], earn_dir=rv.EARN_DIR) -> Set[str]:
    """财报**反应交易日**集合 (预注册口径)。

    vendor `trading_day_str` 已解析 (AMC → 次一交易日 / BMO → 当日) 直接取。未解析行兜底:
    pub_type 2 或 pub_time ≥16:00 → `dates` 里 pub 日之后的首个交易日; BMO / 时刻已知且 <16:00
    → 当日; 时间未知 (pub_type 0 且无时刻) → 当日与次一交易日**都**剔 (保守)。
    `dates` 须升序 (bars 的 YYYY-MM-DD)。无文件 → 空集 (调用方计数报告)。
    """
    p = Path(earn_dir) / f"{ticker}.json"
    if not p.exists():
        return set()
    try:
        rows = json.loads(p.read_text()).get("data") or []
    except Exception:
        return set()
    out: Set[str] = set()
    for r in rows:
        td = str(r.get("trading_day_str") or "")[:10]
        if len(td) == 10:
            out.add(td)
            continue
        pub = str(r.get("pub_trading_day_str") or "")[:10]
        if len(pub) != 10:
            continue
        hour = str(r.get("pub_time_str") or "")[11:13]
        known = hour.isdigit() and hour != "00"
        amc = r.get("pub_type") == 2 or (known and int(hour) >= 16)
        k = bisect.bisect_right(dates, pub)
        nxt = dates[k] if k < len(dates) else None
        if amc:
            if nxt:
                out.add(nxt)
        elif known or r.get("pub_type") == 1:
            out.add(pub)
        else:
            out.add(pub)
            if nxt:
                out.add(nxt)
    return out


def ratio_series_dnx(vols, e_mask, lb=rv.VOL_LB):
    """稳健性臂 dnx: 分母 = 近 lb 日**剔除反应日**后的均量 (仍含当日)。主格分母不剔 (预注册口径)。"""
    out = [None] * len(vols)
    for j in range(lb - 1, len(vols)):
        win = [vols[k] for k in range(j - lb + 1, j + 1) if not e_mask[k]]
        base = sum(win) / len(win) if win else 0
        out[j] = vols[j] / base if base > 0 else None
    return out


def clock_contribs(ratios, e_mask, *, highs=None, lows=None, gate=None,
                   vap: bool = False, no_e: bool = False):
    """逐日贡献 r_i (预注册口径): 自身量比; 反应日 → 0 (no_e=True 关掉剔除 = W4_noE);
    vap=True (口径 B) → 乘以该日 K 线在 gate 之上的占比 min(1, max(0, (high−gate)/(high−low)))。
    窗不足 (ratio None) → None, 扫描时按 0 计。"""
    out = [None] * len(ratios)
    for i, r in enumerate(ratios):
        if r is None:
            continue
        c = 0.0 if (e_mask[i] and not no_e) else float(r)
        if vap and c > 0:
            hi, lo = highs[i], lows[i]
            frac = 1.0 if hi <= lo else min(1.0, max(0.0, (hi - gate) / (hi - lo)))
            c *= frac
        out[i] = c
    return out


def clock_scan(start, end, closes, contrib, level, k):
    """量的钟: 首个 j∈(start,end] 使得连续收盘>level 的 streak 内 Σcontrib ≥ k (含 EPS)。
    收盘 ≤ level → acc 归零 (跌回 = 接受作废)。None 贡献按 0 计, streak 不断。"""
    acc = 0.0
    for j in range(start + 1, end + 1):
        if closes[j] > level:
            acc += contrib[j] or 0.0
            if acc + EPS >= k:
                return j
        else:
            acc = 0.0
    return None


def fastlane_scan(start, end, closes, atr20, relvr, level, k=FASTLANE_K, ratio=VOL_RATIO):
    """live `regime_transition._fastlane` 镜像 (域 = ①修复 = 本池全部): streak **首日**
    (closes[j−1] ≤ level < closes[j]) 且 close > level + k×ATR20 且 RelVR ≥ ratio → j。
    非首日不许事后补票 (live 逐字)。"""
    for j in range(start + 1, end + 1):
        if not (closes[j - 1] <= level < closes[j]):
            continue
        a = atr20[j]
        if not a or closes[j] <= level + k * a:
            continue
        if relvr[j] is None or relvr[j] < ratio:
            continue
        return j
    return None


def world_entries(i, end_sim, closes, gate, vols, relvr, ratios, ratios_dnx, e_mask,
                  highs, lows, atr20) -> Dict[str, Optional[int]]:
    """十个世界的满足日 q (None = 窗内未满足)。W0/W1/W3 与 relative_volume 逐字同式。"""
    need = rv.NEED
    q: Dict[str, Optional[int]] = {
        "W0": rv.fire_scan_v(i, end_sim, closes, gate, need, lambda j: vol_ok(vols, j)),
        "W1": rv.fire_scan_v(i, end_sim, closes, gate, need,
                             lambda j: relvr[j] is not None and relvr[j] >= VOL_RATIO),
        "W3": rv.fire_scan_v(i, end_sim, closes, gate, need, lambda j: True),
    }
    fl = fastlane_scan(i, end_sim, closes, atr20, relvr, gate)
    q["W1F"] = min([x for x in (q["W1"], fl) if x is not None], default=None)
    base = clock_contribs(ratios, e_mask)
    q["W4"] = clock_scan(i, end_sim, closes, base, gate, K_PRIMARY)
    q["W4_k15"] = clock_scan(i, end_sim, closes, base, gate, K_ROBUST[0])
    q["W4_k30"] = clock_scan(i, end_sim, closes, base, gate, K_ROBUST[1])
    q["W4_vap"] = clock_scan(i, end_sim, closes,
                             clock_contribs(ratios, e_mask, highs=highs, lows=lows, gate=gate,
                                            vap=True), gate, K_PRIMARY)
    q["W4_dnx"] = clock_scan(i, end_sim, closes, clock_contribs(ratios_dnx, e_mask),
                             gate, K_PRIMARY)
    q["W4_noE"] = clock_scan(i, end_sim, closes, clock_contribs(ratios, e_mask, no_e=True),
                             gate, K_PRIMARY)
    return q


# ---------- 主循环 (池 / 锚 / 剔除规则 = relative_volume.collect 原样) ----------

def collect(kdir=rv.KDIR, earn_dir=rv.EARN_DIR, world_fn=None):
    """全池扫描 → (episodes, outside, drops)。outside = no_p episode 里 W4 在窗内触发的样本外披露。

    `world_fn` = 扩展点 (默认恒等 = `world_entries`, hvn_gate 纪律「原世界逐字复现」): 衍生
    预注册 (如 round-2 量腿对齐日) 传自己的 world_entries 追加世界, 本文件的定义/判据一字不动。
    """
    world_fn = world_fn or world_entries
    bench_maps = {}
    for b in ("QQQ", "SMH"):
        bars = load(Path(kdir) / f"{b}.jsonl")
        rs = rv.ratio_series([x.get("volume") or 0 for x in bars])
        bench_maps[b] = {bars[j]["time"][:10]: rs[j] for j in range(len(bars))
                         if rs[j] is not None}

    episodes, outside = [], []
    drops = {"no_gate": 0, "no_p": 0, "no_terminal": 0, "bench_gap": 0, "no_earnings_file": 0}
    for path in sorted(Path(kdir).glob("*.jsonl")):
        t = path.stem
        if t.startswith("_") or t in rv.ETF_EXCLUDE:
            continue
        bars = load(path)
        if len(bars) < 300:
            continue
        closes = [b["close"] for b in bars]
        highs = [b["high"] for b in bars]
        lows = [b["low"] for b in bars]
        vols = [b.get("volume") or 0 for b in bars]
        dates = [b["time"][:10] for b in bars]
        ma60 = sma_series(closes, 60)
        atr20 = atr_series(highs, lows, closes, 20)
        atr14 = atr_series(highs, lows, closes, 14)
        swings_h = confirmed_swing_highs(highs)
        swings_l = confirmed_swing_lows(lows)
        rs = rv.ratio_series(vols)
        semi = t in SEMI_SMH
        relvr = rv.relvr_series(rs, dates, bench_maps["SMH" if semi else "QQQ"])
        if not (Path(earn_dir) / f"{t}.json").exists():
            drops["no_earnings_file"] += 1
        rdays = reaction_days_of(t, dates, earn_dir)
        e_mask = [d in rdays for d in dates]
        rs_dnx = ratio_series_dnx(vols, e_mask)
        pub_days = rv.earnings_days_of(t, earn_dir)      # E±1 分层沿用 round-0 口径 (pub 日)

        for i in episodes_of(closes, ma60):
            gate = cluster_gate(i, closes[i], swings_h, swings_l, atr14[i])
            if gate is None:
                drops["no_gate"] += 1
                continue
            wend = min(i + rv.WINDOW, len(closes) - 1)
            p = rv.fire_scan_v(i, wend, closes, gate, rv.NEED, lambda j: True)
            if p is None:
                drops["no_p"] += 1
                # 样本外披露 (预注册): 价格腿 ×2 从未成立, 但钟在窗内触发 → q 锚定 T60
                q4 = clock_scan(i, wend, closes, clock_contribs(rs, e_mask), gate, K_PRIMARY)
                if q4 is not None:
                    end_q = min(q4 + rv.SIM_TAIL, len(closes) - 1)
                    k60 = q4 + 60
                    if k60 <= len(closes) - 1:
                        segs = rv.segs_for(q4, end_q, closes, atr20)
                        outside.append({"ticker": t, "month": dates[q4][:7],
                                        "pv": rv.policy_value(segs, k60, closes),
                                        "stopped": rv.stopped_by(segs, k60),
                                        "entered": bool(segs)})
                continue
            end_sim = min(p + rv.SIM_TAIL, len(closes) - 1)
            terms = {T: p + T for T in rv.TERMINALS if p + T <= len(closes) - 1}
            if not terms:
                drops["no_terminal"] += 1
                continue
            if any(relvr[j] is None for j in range(p, end_sim + 1)):
                drops["bench_gap"] += 1          # 基准覆盖规则: 对称剔除 (round-0 修订 #5)
                continue

            qs = world_fn(i, end_sim, closes, gate, vols, relvr, rs, rs_dnx, e_mask,
                          highs, lows, atr20)
            segs = {w: rv.segs_for(q, end_sim, closes, atr20) for w, q in qs.items()}
            rec = {"ticker": t, "month": dates[p][:7], "semi": semi,
                   "pv": {}, "stopped": {}, "entered": {}, "q": dict(qs),
                   "lat": {w: (qs[w] - p) if qs[w] is not None else None for w in qs},
                   "e_tag": rv.near_earnings(
                       [dates[p]] + [dates[qs[w]] for w in qs if qs[w] is not None],
                       pub_days)}
            for T, k in terms.items():
                for w in qs:
                    rec["pv"][(w, T)] = rv.policy_value(segs[w], k, closes)
                    rec["stopped"][(w, T)] = rv.stopped_by(segs[w], k)
                    rec["entered"][(w, T)] = bool(qs[w] is not None and qs[w] + 1 <= k)
            episodes.append(rec)
    return episodes, outside, drops


# ---------- 统计 ----------

def cell_stats(rows, w, base, T, seed_base):
    """一个对比格 (w − base, 终点 T) 的全部读数 (relative_volume.cell_stats 的 base 参数化版)。"""
    months = [r["month"] for r in rows]
    uniq, by_m, choice = rv._month_matrix(months, rv.BOOT_DRAWS, seed_base)
    dv = [r["pv"][(w, T)] - r["pv"][(base, T)] for r in rows]
    dstop = [float(r["stopped"][(w, T)]) - float(r["stopped"][(base, T)]) for r in rows]
    dcov = [float(r["entered"][(w, T)]) - float(r["entered"][(base, T)]) for r in rows]
    mean, mean_ci = rv._boot_mean_ci(dv, months, uniq, by_m, choice)
    stop, stop_ci = rv._boot_mean_ci(dstop, months, uniq, by_m, choice)
    cov, cov_ci = rv._boot_mean_ci(dcov, months, uniq, by_m, choice)
    p10, p10_ci = rv._boot_p10_diff([r["pv"][(w, T)] for r in rows],
                                    [r["pv"][(base, T)] for r in rows],
                                    months, seed_base + 1)
    lat_w = [r["lat"][w] for r in rows if r["lat"][w] is not None]
    lat_b = [r["lat"][base] for r in rows if r["lat"][base] is not None]
    n = len(rows)
    return {"n": n, "kish": round(rv._kish(months), 1), "base": base,
            "dmean": mean, "dmean_ci": mean_ci,
            "dstop": stop, "dstop_ci": stop_ci,
            "dcover": cov, "dcover_ci": cov_ci,
            "dp10": p10, "dp10_ci": p10_ci,
            "lat_w": (sum(lat_w) / len(lat_w)) if lat_w else None,
            "lat_b": (sum(lat_b) / len(lat_b)) if lat_b else None,
            "cover_w": sum(r["entered"][(w, T)] for r in rows) / n,
            "cover_b": sum(r["entered"][(base, T)] for r in rows) / n,
            "stop_w": sum(r["stopped"][(w, T)] for r in rows) / n,
            "stop_b": sum(r["stopped"][(base, T)] for r in rows) / n,
            "mean_w": sum(r["pv"][(w, T)] for r in rows) / n,
            "mean_b": sum(r["pv"][(base, T)] for r in rows) / n}


def timing_stats(rows, w, base):
    """w 对 base 逐 episode 的先后 (只报不裁): 双方都触发的样本里早/同/晚占比与均值 (交易日)。"""
    both = [r["q"][w] - r["q"][base] for r in rows
            if r["q"].get(w) is not None and r["q"].get(base) is not None]
    n = len(both)
    return {"n_both": n,
            "earlier": (sum(1 for d in both if d < 0) / n) if n else None,
            "same": (sum(1 for d in both if d == 0) / n) if n else None,
            "later": (sum(1 for d in both if d > 0) / n) if n else None,
            "mean_dq": (sum(both) / n) if n else None,
            "only_w": sum(1 for r in rows if r["q"].get(w) is not None and r["q"].get(base) is None),
            "only_base": sum(1 for r in rows if r["q"].get(w) is None and r["q"].get(base) is not None),
            "neither": sum(1 for r in rows if r["q"].get(w) is None and r["q"].get(base) is None)}


def _regression_ok(got: Dict) -> bool:
    exp = REGRESSION_ROUND0
    return (got.get("n_total") == exp["n_total"]
            and got.get("n60") == exp["n60"]
            and abs(got["kish"] - exp["kish"]) <= 0.05 + 1e-9
            and abs(got["dmean"] - exp["dmean"]) <= TOL_PP2
            and abs(got["dmean_ci"][0] - exp["dmean_ci"][0]) <= TOL_PP2
            and abs(got["dmean_ci"][1] - exp["dmean_ci"][1]) <= TOL_PP2
            and abs(got["dstop"] - exp["dstop"]) <= TOL_PP2
            and abs(got["dcover"] - exp["dcover"]) <= TOL_PP1)


def regression_check(rows60, n_total) -> Dict:
    """回归复现前置: W1−W0 (T60 全样本, round-0 seed) 必须复现 round-0 报告原文。"""
    if len(rows60) < 5:
        return {"ok": False, "reason": "insufficient", "got": None, "expected": REGRESSION_ROUND0}
    c = rv.cell_stats(rows60, "W1", 60, rv.BOOT_SEED + 60)
    got = {"n_total": n_total, "n60": c["n"], "kish": c["kish"], "dmean": c["dmean"],
           "dmean_ci": tuple(c["dmean_ci"]), "dstop": c["dstop"], "dcover": c["dcover"]}
    return {"ok": _regression_ok(got), "got": got, "expected": REGRESSION_ROUND0}


def summarize(episodes, outside=()):
    out: Dict = {"n_total": len(episodes)}
    strata = {"full": episodes,
              "e_tag": [r for r in episodes if r["e_tag"]],
              "non_e": [r for r in episodes if not r["e_tag"]]}
    for name, rows_all in strata.items():
        out[name] = {}
        for T in rv.TERMINALS:
            rows = [r for r in rows_all if ((BASE, T) in r["pv"])]
            if len(rows) < 5:
                out[name][T] = {"n": len(rows), "insufficient": True}
                continue
            out[name][T] = {
                "vs_base": {w: cell_stats(rows, w, BASE, T, BOOT_SEED + T) for w in CMPS},
                "vs_w1": {w: cell_stats(rows, w, BASE2, T, BOOT_SEED + T + 100)
                          for w in ("W4", "W1F")},
            }
    rows60 = [r for r in episodes if (BASE, 60) in r["pv"]]
    out["timing"] = timing_stats(rows60, "W4", BASE) if rows60 else None
    out["timing_vs_w1"] = timing_stats(rows60, "W4", BASE2) if rows60 else None
    out["e_exclusion_moved_fire"] = sum(1 for r in rows60 if r["q"]["W4"] != r["q"]["W4_noE"])
    out["regression"] = regression_check(rows60, len(episodes))
    n_out = len(outside)
    out["outside"] = {"n": n_out,
                      "stop_rate": (sum(1 for r in outside if r["stopped"]) / n_out) if n_out else None,
                      "mean_pv": (sum(r["pv"] for r in outside) / n_out) if n_out else None}
    return out


def _flags(c: Dict) -> Dict[str, bool]:
    return {"exp_ok": c["dmean_ci"][0] >= 0,
            "stop_ok": c["dstop_ci"][1] < STOP_GUARD,
            "cover_ok": c["dcover_ci"][0] > 0}


def verdict(summary: Dict) -> Dict:
    """机械裁决 (预注册规则): 回归门 → 数据门 → Kish 门 → 三判据 (W4 − W1F, T60 全样本)。
    稳健性臂只报 flags, 不参与分支。"""
    reg = summary.get("regression") or {}
    if not reg.get("ok"):
        return {"branch": "no_call", "reason": "回归不复现 round-0 W1−W0 → 环境可疑, 中止",
                "regression": reg}
    cells = (summary.get("full") or {}).get(DECISION_T) or {}
    if not cells or cells.get("insufficient"):
        return {"branch": "no_call", "reason": "数据不足"}
    c = cells["vs_base"]["W4"]
    if c["kish"] < KISH_MIN:
        return {"branch": "no_call", "reason": f"Kish {c['kish']} < {KISH_MIN}"}
    flags = _flags(c)
    branch = "V" if all(flags.values()) else "K"
    robust = {w: _flags(cells["vs_base"][w]) for w in CMPS if w != "W4"}
    return {"branch": branch, "flags": flags,
            "cell": {k: c[k] for k in ("n", "kish", "dmean", "dmean_ci", "dstop", "dstop_ci",
                                       "dcover", "dcover_ci", "lat_w", "lat_b")},
            "robust_flags": robust}


# ---------- 报告 ----------

def _ci(pair):
    return f"[{pair[0]:+.4f},{pair[1]:+.4f}]"


def _f(v, nd=1):
    return "—" if v is None else f"{round(v, nd)}"


def _cell_line(w, c):
    return (f"| {w}−{c['base']} | {c['n']} | {c['kish']} | {c['dmean']:+.4f} {_ci(c['dmean_ci'])} "
            f"| {c['dstop']:+.4f} {_ci(c['dstop_ci'])} | {c['dp10']:+.4f} {_ci(c['dp10_ci'])} "
            f"| {c['dcover']:+.3f} {_ci(c['dcover_ci'])} "
            f"| {c['cover_w']:.2f}/{c['cover_b']:.2f} | {c['stop_w']:.2f}/{c['stop_b']:.2f} "
            f"| {_f(c['lat_w'])}/{_f(c['lat_b'])} |")


def render(summary, drops, vd):
    L = [f"# volume_clock gate 确认口径裁决 — round-1 (seed {BOOT_SEED})", ""]
    L.append(f"episodes {summary['n_total']} | drops: " +
             " / ".join(f"{k} {v}" for k, v in drops.items()))
    reg = summary.get("regression") or {}
    L += ["", f"## 回归复现前置 (W1−W0 T60, round-0 seed): **{'PASS' if reg.get('ok') else 'FAIL'}**",
          "```json", json.dumps(reg, ensure_ascii=False, indent=1, default=str), "```"]
    hdr = ("| cmp | n | Kish | Δmean CI90 | Δ止损率 CI90 | Δp10 CI90(1k) | Δ覆盖率 CI90 "
           "| 覆盖 w/b | 止损率 w/b | 迟到 w/b |")
    sep = "|---|---|---|---|---|---|---|---|---|---|"
    for name, label in (("full", "全样本 (裁决)"), ("e_tag", "E±1 层 (报告)"),
                        ("non_e", "非 E±1 层 (报告)")):
        for T in rv.TERMINALS:
            cells = (summary.get(name) or {}).get(T)
            if not cells:
                continue
            L += ["", f"## {label} T+{T} — 对 W1F (live 镜像)", hdr, sep]
            if cells.get("insufficient"):
                L.append(f"| 数据不足 n={cells['n']} |")
                continue
            for w in CMPS:
                L.append(_cell_line(w, cells["vs_base"][w]))
            L += ["", f"### 同层 T+{T} — 对 W1 (round-0 裁决格)", hdr, sep]
            for w in ("W4", "W1F"):
                L.append(_cell_line(w, cells["vs_w1"][w]))
    for key, label in (("timing", "W4 vs W1F 逐 episode 先后 (T60 样本, 只报)"),
                       ("timing_vs_w1", "W4 vs W1 逐 episode 先后 (T60 样本, 只报)")):
        tm = summary.get(key)
        if tm:
            L += ["", f"## {label}",
                  f"- 双方触发 n={tm['n_both']}: 早 {_f((tm['earlier'] or 0) * 100)}% / 同 "
                  f"{_f((tm['same'] or 0) * 100)}% / 晚 {_f((tm['later'] or 0) * 100)}%; "
                  f"均值 {_f(tm['mean_dq'], 2)} 交易日",
                  f"- 只有 W4 触发 {tm['only_w']} / 只有对照触发 {tm['only_base']} / 都未触发 {tm['neither']}"]
    L += ["", f"## 财报剔除改变了触发日的 episode 数 (W4 ≠ W4_noE, T60 样本): {summary.get('e_exclusion_moved_fire')}"]
    o = summary.get("outside") or {}
    L += ["", "## 样本外披露: no_p episode 里 W4 在窗内触发 (q 锚定 T60, 不进裁决)",
          f"- n={o.get('n')} / 止损率 {_f((o.get('stop_rate') or 0) * 100)}% / 均值 "
          f"{'—' if o.get('mean_pv') is None else f'{o['mean_pv']:+.4f}'}"]
    L += ["", f"## 机械裁决: **{vd['branch']}**", "```json",
          json.dumps(vd, ensure_ascii=False, indent=1, default=str), "```", ""]
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--report", default="tmp/backtest/volume_clock_report.md")
    ap.add_argument("--kdir", default=str(rv.KDIR), help="klines_deep 目录 (缺省 paths.KLINE_DEEP_DIR)")
    ap.add_argument("--earn-dir", default=str(rv.EARN_DIR), help="earnings_deep 目录")
    a = ap.parse_args()
    episodes, outside, drops = collect(kdir=Path(a.kdir), earn_dir=Path(a.earn_dir))
    summary = summarize(episodes, outside)
    vd = verdict(summary)
    text = render(summary, drops, vd)
    out = Path(a.report)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    if a.json:
        print(json.dumps({"drops": drops, "verdict": vd, "n": summary["n_total"]},
                         ensure_ascii=False, default=str))
    else:
        print(text)


if __name__ == "__main__":
    main()
