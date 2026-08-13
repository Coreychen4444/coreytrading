#!/usr/bin/env python3
"""relative_volume_backtest — 修复 gate 量腿计量方式裁决（预注册 v1，锁定）。

设计稿全文: quant/research/relative_volume_gate_design.md（四决策点 + 运行前修订 10 钉全锁）。
立项源头 = INTC 8/4-8/5: gate 98 价格腿 2/2, 量腿 1.01×/0.73× 不过 1.2 未转移,
而  QQQ/SMH 同为缩量日 — 用户: 全场缩量日用绝对量比惩罚个股 = 尺子瞄歪。

## 四个世界（共同锚 p = 价格腿首满足日; 唯一自由度 = 量腿; ITT, 终点 p+20/p+60）
W0 现行: 收盘>gate ×2 + 绝对量比≥1.2 (完成日)     W1 相对: RelVR≥1.2
W2 取或: 绝对或相对                                W3 无量腿: 只看价格
RelVR = 量比_个股 / max(量比_基准, 0.4); 基准 = 半导体→SMH, 其余→QQQ (映射表冻结)。
入场 = 满足日次日收盘 (镜像 live postclose→次日执行); 止损 primary 2N ×1日 SLIP 0.2%;
single-shot; 未入场 = 现金。

## 裁决 (D1, T60 全样本)
R(换尺): Δmean CI90 下界≥0 且 Δ止损率 CI90 上界<+3pp 且 Δ覆盖率 CI90 下界>0
  (W1/W2 同过取 W1)
N(量腿退休): W3 对 W0/W1/W2 全部 Δmean CI90 下界>0 且 Δ止损率上界<+3pp (触发须 用户 单独批)
K/no_call: 其余 = 维持现行。
分层 (E±1 / 非) 各出全表不改写主裁决; 敏感性 = 半导体子集 QQQ 尺翻转 (不裁决)。

Usage: .venv/moomoo/bin/python3 quant/research/relative_volume_backtest.py [--json] [--report PATH]
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 直接 `python3 quant/<层>/<模块>.py` 时让 `quant.*` 可 import

import argparse
import json
from collections import defaultdict
from datetime import date

import numpy as np

from quant.core.paths import KLINE_DEEP_DIR as KDIR  # noqa: E402
from quant.core.paths import MARKET_DIR  # noqa: E402
from quant.research.gate_backtest import (# noqa: E402  round-1/round-2 口径按构造复用
    load, sma_series, episodes_of,  confirmed_swing_highs,
)
from quant.research.gate_backtest2 import (# noqa: E402
    atr_series, confirmed_swing_lows, cluster_gate, vol_ok, stop_scan,
    policy_value, stopped_by,
)

from quant.core.sectors import SEMI_SMH  # noqa: E402  冻结映射单一真源 (live 同表)

EARN_DIR = MARKET_DIR / "earnings_deep"

NEED = 2                   # days 腿固定 ×2 (escape_velocity C + gate2 已裁, 不在本测范围)
WINDOW = 120               # 价格腿等待窗 (episodes_of 的 repair 窗, gate2 同)
SIM_TAIL = 60              # 模拟窗 = p+60
TERMINALS = (20, 60)
VOL_RATIO = 1.2
VOL_LB = 20
REL_FLOOR = 0.4
STOP_MULT = 2.0
SLIP = 0.002
BOOT_DRAWS = 10_000
P10_DRAWS = 1_000
BOOT_SEED = 20260806
CI_Q = (5.0, 95.0)         # 90% CI
STOP_GUARD = 0.03          # +3pp (用户 锁定, 比 escape 的 +5 严)
WORLDS = ("W0", "W1", "W2", "W3")
CMPS = ("W1", "W2", "W3")

# 半导体映射表: quant/core/sectors.SEMI_SMH (冻结 , live 与回测同表同源)
# ETF 只作基准, 不出 episode (运行前修订 #3)
ETF_EXCLUDE = frozenset({
    "QQQ", "SPY", "SMH", "IWM", "GDX", "KRE", "XBI", "XHB",
    "XLE", "XLF", "XLI", "XLP", "XLU", "XLV", "XLY", "XRT",
})


# ---------- 纯函数件 ----------

def ratio_series(vols, lb=VOL_LB):
    """逐日量比, 分母 SMA(lb) **含当日**— 与 vol_ok / technical.volume_ratio 同拍。"""
    out = [None] * len(vols)
    for j in range(lb - 1, len(vols)):
        base = sum(vols[j - lb + 1:j + 1]) / lb
        out[j] = vols[j] / base if base > 0 else None
    return out


def relvr_series(stock_ratios, dates, bench_by_date, floor=REL_FLOOR):
    """RelVR[j] = 个股量比 / max(基准量比, floor)。任一侧不可算 → None。"""
    out = [None] * len(stock_ratios)
    for j, r in enumerate(stock_ratios):
        if r is None:
            continue
        b = bench_by_date.get(dates[j])
        if b is None:
            continue
        out[j] = r / max(b, floor)
    return out


def fire_scan_v(start, end, closes, level, need, ok):
    """gate2.fire_scan 的量腿参数化版: 首个 j∈(start,end] 连续 need 日收盘>level
    且 ok(j)。level 固定值 (cluster gate 破位日画定); 跌回清零 streak。"""
    streak = 0
    for j in range(start + 1, end + 1):
        streak = streak + 1 if closes[j] > level else 0
        if streak >= need and ok(j):
            return j
    return None


def world_entries(i, p, end_sim, closes, gate, vols, relvr):
    """四世界的量腿满足日 q (共同锚 p 由构造保证 q≥p)。→ {world: q|None}"""
    oks = {
        "W0": lambda j: vol_ok(vols, j),
        "W1": lambda j: relvr[j] is not None and relvr[j] >= VOL_RATIO,
        "W2": lambda j: vol_ok(vols, j) or (relvr[j] is not None and relvr[j] >= VOL_RATIO),
        "W3": lambda j: True,
    }
    out = {}
    for w, ok in oks.items():
        out[w] = fire_scan_v(i, end_sim, closes, gate, NEED, ok)
    assert out["W3"] == p or out["W3"] is None
    return out


def segs_for(q, end_sim, closes, atr20):
    """满足日 q → 次日收盘入场的单段 (primary 2N 止损)。q+1 超窗 = 未入场 []。"""
    if q is None or q + 1 > end_sim:
        return []
    e = q + 1
    px = closes[e]
    stop = px - STOP_MULT * atr20[e] if atr20[e] else None
    hit = stop_scan(e, end_sim, closes, stop)
    return [(e, px, hit[1], hit[2], stop)] if hit else [(e, px, None, None, stop)]


def earnings_days_of(ticker, earn_dir=EARN_DIR):
    p = earn_dir / f"{ticker}.json"
    if not p.exists():
        return set()
    try:
        rows = json.loads(p.read_text()).get("data") or []
    except Exception:
        return set()
    return {str(r.get("pub_trading_day_str"))[:10] for r in rows if r.get("pub_trading_day_str")}


def near_earnings(day_strs, edays, tol_days=1):
    """任一 day (YYYY-MM-DD) 距任一 vendor E 日 ≤ tol 自然日。"""
    if not edays:
        return False
    try:
        ds = [date.fromisoformat(s) for s in day_strs]
        es = [date.fromisoformat(e) for e in edays]
    except ValueError:
        return False
    return any(abs((d - e).days) <= tol_days for d in ds for e in es)


# ---------- 主循环 ----------

def collect(kdir=KDIR, earn_dir=EARN_DIR):
    """全池扫描 → episode 记录 (pv/stopped/entered 已算好, 不携带 closes)。"""
    bench_maps = {}
    for b in ("QQQ", "SMH"):
        bars = load(kdir / f"{b}.jsonl")
        rs = ratio_series([x.get("volume") or 0 for x in bars])
        bench_maps[b] = {bars[j]["time"][:10]: rs[j] for j in range(len(bars))
                         if rs[j] is not None}

    episodes = []
    drops = {"no_gate": 0, "no_p": 0, "no_terminal": 0, "bench_gap": 0}
    for path in sorted(kdir.glob("*.jsonl")):
        t = path.stem
        if t.startswith("_") or t in ETF_EXCLUDE:
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
        rs = ratio_series(vols)
        semi = t in SEMI_SMH
        relvr = relvr_series(rs, dates, bench_maps["SMH" if semi else "QQQ"])
        relvr_flip = relvr_series(rs, dates, bench_maps["QQQ"]) if semi else None
        edays = earnings_days_of(t, earn_dir)

        for i in episodes_of(closes, ma60):
            gate = cluster_gate(i, closes[i], swings_h, swings_l, atr14[i])
            if gate is None:
                drops["no_gate"] += 1
                continue
            wend = min(i + WINDOW, len(closes) - 1)
            p = fire_scan_v(i, wend, closes, gate, NEED, lambda j: True)
            if p is None:
                drops["no_p"] += 1
                continue
            end_sim = min(p + SIM_TAIL, len(closes) - 1)
            terms = {T: p + T for T in TERMINALS if p + T <= len(closes) - 1}
            if not terms:
                drops["no_terminal"] += 1
                continue
            if any(relvr[j] is None for j in range(p, end_sim + 1)):
                drops["bench_gap"] += 1          # 基准覆盖规则: 对称剔除 (修订 #5)
                continue

            qs = world_entries(i, p, end_sim, closes, gate, vols, relvr)
            segs = {w: segs_for(q, end_sim, closes, atr20) for w, q in qs.items()}
            rec = {"ticker": t, "month": dates[p][:7], "semi": semi,
                   "pv": {}, "stopped": {}, "entered": {},
                   "lat": {w: (qs[w] - p) if qs[w] is not None else None for w in WORLDS},
                   "e_tag": near_earnings(
                       [dates[p]] + [dates[qs[w]] for w in WORLDS if qs[w] is not None],
                       edays)}
            for T, k in terms.items():
                for w in WORLDS:
                    rec["pv"][(w, T)] = policy_value(segs[w], k, closes)
                    rec["stopped"][(w, T)] = stopped_by(segs[w], k)
                    rec["entered"][(w, T)] = bool(qs[w] is not None and qs[w] + 1 <= k)
            if semi and relvr_flip is not None \
                    and not any(relvr_flip[j] is None for j in range(p, end_sim + 1)):
                fq = {w: fire_scan_v(i, end_sim, closes, gate, NEED, ok) for w, ok in {
                    "W1": (lambda j: relvr_flip[j] is not None
                           and relvr_flip[j] >= VOL_RATIO),
                    "W2": (lambda j: vol_ok(vols, j) or (relvr_flip[j] is not None
                                                         and relvr_flip[j] >= VOL_RATIO)),
                }.items()}
                fsegs = {w: segs_for(q, end_sim, closes, atr20) for w, q in fq.items()}
                rec["flip_pv"] = {(w, T): policy_value(fsegs[w], k, closes)
                                  for T, k in terms.items() for w in ("W1", "W2")}
            episodes.append(rec)
    return episodes, drops


# ---------- 统计 ----------

def _kish(months):
    counts = defaultdict(int)
    for m in months:
        counts[m] += 1
    n = np.array(list(counts.values()), dtype=float)
    return float(n.sum() **2 / (n **2).sum()) if len(n) else 0.0


def _month_matrix(months, draws, seed):
    """共享月索引矩阵: 每 draw 抽 len(unique) 个月 (有放回) → 行索引 list。"""
    uniq = sorted(set(months))
    by_m = {m: np.array([i for i, mm in enumerate(months) if mm == m]) for m in uniq}
    rng = np.random.RandomState(seed)
    choice = rng.randint(0, len(uniq), size=(draws, len(uniq)))
    return uniq, by_m, choice


def _boot_mean_ci(values, months, uniq, by_m, choice):
    """月 block bootstrap 的均值 CI90 (per-month 前缀和, 10k draws 向量化)。"""
    v = np.asarray(values, dtype=float)
    sums = np.array([v[by_m[m]].sum() for m in uniq])
    cnts = np.array([len(by_m[m]) for m in uniq], dtype=float)
    draw_sum = sums[choice].sum(axis=1)
    draw_cnt = cnts[choice].sum(axis=1)
    means = draw_sum / np.maximum(draw_cnt, 1)
    lo, hi = np.percentile(means, CI_Q)
    return float(v.mean()), (float(lo), float(hi))


def _boot_p10_diff(vx, v0, months, seed, draws=P10_DRAWS):
    uniq, by_m, choice = _month_matrix(months, draws, seed)
    x = np.asarray(vx, dtype=float)
    z = np.asarray(v0, dtype=float)
    outs = np.empty(draws)
    for d in range(draws):
        idx = np.concatenate([by_m[uniq[c]] for c in choice[d]])
        outs[d] = np.percentile(x[idx], 10) - np.percentile(z[idx], 10)
    lo, hi = np.percentile(outs, CI_Q)
    return float(np.percentile(x, 10) - np.percentile(z, 10)), (float(lo), float(hi))


def cell_stats(rows, w, T, seed_base):
    """一个对比格 (w vs W0, 终点 T) 的全部读数。rows 已按 T 过滤。"""
    months = [r["month"] for r in rows]
    uniq, by_m, choice = _month_matrix(months, BOOT_DRAWS, seed_base)
    dv = [r["pv"][(w, T)] - r["pv"][("W0", T)] for r in rows]
    dstop = [float(r["stopped"][(w, T)]) - float(r["stopped"][("W0", T)]) for r in rows]
    dcov = [float(r["entered"][(w, T)]) - float(r["entered"][("W0", T)]) for r in rows]
    mean, mean_ci = _boot_mean_ci(dv, months, uniq, by_m, choice)
    stop, stop_ci = _boot_mean_ci(dstop, months, uniq, by_m, choice)
    cov, cov_ci = _boot_mean_ci(dcov, months, uniq, by_m, choice)
    p10, p10_ci = _boot_p10_diff([r["pv"][(w, T)] for r in rows],
                                 [r["pv"][("W0", T)] for r in rows],
                                 months, seed_base + 1)
    lat_w = [r["lat"][w] for r in rows if r["lat"][w] is not None]
    lat_0 = [r["lat"]["W0"] for r in rows if r["lat"]["W0"] is not None]
    return {"n": len(rows), "kish": round(_kish(months), 1),
            "dmean": mean, "dmean_ci": mean_ci,
            "dstop": stop, "dstop_ci": stop_ci,
            "dcover": cov, "dcover_ci": cov_ci,
            "dp10": p10, "dp10_ci": p10_ci,
            "lat_w": (sum(lat_w) / len(lat_w)) if lat_w else None,
            "lat_w0": (sum(lat_0) / len(lat_0)) if lat_0 else None,
            "cover_w": sum(r["entered"][(w, T)] for r in rows) / len(rows),
            "cover_w0": sum(r["entered"][("W0", T)] for r in rows) / len(rows)}


def pair_mean_ci(rows, wa, wb, T, seed_base):
    """W_a − W_b 的均值 CI (N 分支的 W3 对 W1/W2 判据)。"""
    months = [r["month"] for r in rows]
    uniq, by_m, choice = _month_matrix(months, BOOT_DRAWS, seed_base)
    dv = [r["pv"][(wa, T)] - r["pv"][(wb, T)] for r in rows]
    return _boot_mean_ci(dv, months, uniq, by_m, choice)


def summarize(episodes):
    out = {"n_total": len(episodes)}
    strata = {"full": episodes,
              "e_tag": [r for r in episodes if r["e_tag"]],
              "non_e": [r for r in episodes if not r["e_tag"]]}
    for name, rows_all in strata.items():
        out[name] = {}
        for T in TERMINALS:
            rows = [r for r in rows_all if (("W0", T) in r["pv"])]
            if len(rows) < 5:
                out[name][T] = {"n": len(rows), "insufficient": True}
                continue
            out[name][T] = {w: cell_stats(rows, w, T, BOOT_SEED + T) for w in CMPS}
    # N 分支: W3 对 W1/W2 (T60 全样本)
    rows60 = [r for r in episodes if ("W0", 60) in r["pv"]]
    if len(rows60) >= 5:
        out["w3_vs"] = {wb: pair_mean_ci(rows60, "W3", wb, 60, BOOT_SEED + 60)
                        for wb in ("W1", "W2")}
    # 敏感性: 半导体子集 SMH 尺 vs QQQ 尺 (W1−W0, T60, 只报告)
    semi60 = [r for r in episodes if r["semi"] and ("W0", 60) in r["pv"]]
    flip60 = [r for r in semi60 if "flip_pv" in r and ("W1", 60) in r["flip_pv"]]
    if len(flip60) >= 5:
        months = [r["month"] for r in flip60]
        uniq, by_m, choice = _month_matrix(months, BOOT_DRAWS, BOOT_SEED + 7)
        smh_d = [r["pv"][("W1", 60)] - r["pv"][("W0", 60)] for r in flip60]
        qqq_d = [r["flip_pv"][("W1", 60)] - r["pv"][("W0", 60)] for r in flip60]
        m1, c1 = _boot_mean_ci(smh_d, months, uniq, by_m, choice)
        m2, c2 = _boot_mean_ci(qqq_d, months, uniq, by_m, choice)
        out["semi_sens"] = {"n": len(flip60), "smh": (m1, c1), "qqq": (m2, c2)}
    return out


def d1_verdict(summary):
    """机械裁决 (T60 全样本): R (W1 优先) / N (须 用户 批) / K。"""
    cells = summary.get("full", {}).get(60) or {}
    if not cells or cells.get("insufficient"):
        return {"branch": "no_call", "reason": "数据不足"}
    flags = {}
    for w in CMPS:
        c = cells[w]
        flags[w] = {"exp_ok": c["dmean_ci"][0] >= 0,
                    "stop_ok": c["dstop_ci"][1] < STOP_GUARD,
                    "cover_ok": c["dcover_ci"][0] > 0}
    for w in ("W1", "W2"):                              # 修订 #8: W1 优先
        if all(flags[w].values()):
            return {"branch": "R", "world": w, "flags": flags}
    w3 = cells["W3"]
    n_ok = (w3["dmean_ci"][0] > 0 and w3["dstop_ci"][1] < STOP_GUARD
            and all(ci[1][0] > 0 for ci in summary.get("w3_vs", {}).values())
            and len(summary.get("w3_vs", {})) == 2)
    if n_ok:
        return {"branch": "N_pending_review", "flags": flags}
    return {"branch": "K", "flags": flags}


# ---------- 报告 ----------

def _ci(pair):
    return f"[{pair[0]:+.4f},{pair[1]:+.4f}]"


def _cell_line(w, c):
    return (f"| {w}−W0 | {c['n']} | {c['kish']} | {c['dmean']:+.4f} {_ci(c['dmean_ci'])} "
            f"| {c['dstop']:+.4f} {_ci(c['dstop_ci'])} | {c['dp10']:+.4f} {_ci(c['dp10_ci'])} "
            f"| {c['dcover']:+.3f} {_ci(c['dcover_ci'])} "
            f"| {c['cover_w']:.2f}/{c['cover_w0']:.2f} "
            f"| {c['lat_w'] if c['lat_w'] is None else round(c['lat_w'], 1)}"
            f"/{c['lat_w0'] if c['lat_w0'] is None else round(c['lat_w0'], 1)} |")


def render(summary, drops, verdict):
    L = ["# relative_volume gate 量腿裁决 — round-1 (, seed 20260806)", ""]
    L.append(f"episodes {summary['n_total']} | drops: " +
             " / ".join(f"{k} {v}" for k, v in drops.items()))
    hdr = ("| cmp | n | Kish | Δmean CI90 | Δ止损率 CI90 | Δp10 CI90(1k) "
           "| Δ覆盖率 CI90 | 覆盖 w/w0 | 迟到 w/w0 |")
    sep = "|---|---|---|---|---|---|---|---|---|"
    for name, label in (("full", "全样本 (裁决)"), ("e_tag", "E±1 层 (报告)"),
                        ("non_e", "非 E±1 层 (报告)")):
        for T in TERMINALS:
            cells = summary.get(name, {}).get(T)
            if not cells:
                continue
            L += ["", f"## {label} T+{T}", hdr, sep]
            if cells.get("insufficient"):
                L.append(f"| 数据不足 n={cells['n']} |")
                continue
            for w in CMPS:
                L.append(_cell_line(w, cells[w]))
    if "w3_vs" in summary:
        L += ["", "## W3 对 W1/W2 (N 分支判据, T60 全样本)"]
        for wb, (m, ci) in summary["w3_vs"].items():
            L.append(f"- W3−{wb}: {m:+.4f} {_ci(ci)}")
    if "semi_sens" in summary:
        s = summary["semi_sens"]
        L += ["", f"## 敏感性: 半导体子集 W1−W0 (n={s['n']}, T60, 不裁决)",
              f"- SMH 尺: {s['smh'][0]:+.4f} {_ci(s['smh'][1])}",
              f"- QQQ 尺: {s['qqq'][0]:+.4f} {_ci(s['qqq'][1])}"]
    L += ["", f"## D1 机械裁决: **{verdict['branch']}**", "```json",
          json.dumps(verdict, ensure_ascii=False, indent=1, default=str), "```", ""]
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--report", default="tmp/backtest/relvol_report.md")
    a = ap.parse_args()
    episodes, drops = collect()
    summary = summarize(episodes)
    verdict = d1_verdict(summary)
    text = render(summary, drops, verdict)
    out = Path(a.report)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    if a.json:
        print(json.dumps({"drops": drops, "verdict": verdict,
                          "n": summary["n_total"]}, ensure_ascii=False, default=str))
    else:
        print(text)


if __name__ == "__main__":
    main()
