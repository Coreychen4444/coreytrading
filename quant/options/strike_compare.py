#!/usr/bin/env python3
"""strike_compare.py — Call strike 收益量化器 (crossover + IV 调整).

回答"同一方向、同一到期, 哪个 strike 在我的目标价上收益更高", 把
"越 OTM 杠杆越高" 这条直觉换成可计算的 crossover + 情景表。
不回答"该不该买 / 买多大"(那是 Bayesian + kelly_size + Pre-Trade Check)。

核心 (到期口径, 纯 payoff, 无模型假设):
    return_multiple(K, p, S) = max(S - K, 0) / p
    两个 strike K1<K2 (K2 更 OTM, p2<p1) 的 crossover:
        S* = (p2*K1 - p1*K2) / (p2 - p1)
    含义: 标的到期 > S* 时, 更 OTM 的 K2 收益才反超 K1。
          目标价 < S* → 低 strike (K1) 收益更高。

为什么反直觉 (高 IV 杀 OTM 杠杆):
    OTM 加杠杆的前提是 OTM strike "足够便宜" 补偿放弃的 intrinsic。
    IV 越高, OTM 的 extrinsic 越贵 → crossover 被推高 → 离你目标越远。
    washout 入场 = IV 峰值 = 买 OTM 最差时机 → 反而该往 DITM/低 strike 靠。

可选 (BS 提前平仓情景, 用 math.erf, 无 scipy 依赖):
    给 --exit-dte 和 --exit-iv → 模型化"标的到目标价时, 剩余 DTE + 该 IV 下"
    每个 strike 的 BS 估值与收益。捕捉"涨到目标的路径上 IV 回落, 高 vega 的
    OTM 双重受损"这一维度。

CLI:
    python3 quant/options/strike_compare.py --strikes 60:13.90 70:11.10 \
        --targets 70 80 90 100 110 120 [--underlying 55.79] [--entry-iv 88]

    # 加 BS 提前平仓情景 (目标价时剩 90 天, IV crush 到 55%):
    python3 quant/options/strike_compare.py --strikes 60:13.90 70:11.10 \
        --targets 100 --exit-dte 90 --exit-iv 55 --entry-iv 88 [--json]

The public schema is trade/strategy/options/leaps-call-template.example.md;
active methodology belongs in the ignored private copy without `.example`.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 直接 `python3 quant/<层>/<模块>.py` 时让 `quant.*` 可 import

import argparse
import json
import math


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_call(S: float, K: float, T: float, sigma: float, r: float = 0.04) -> float:
    """Black-Scholes call value. T in years, sigma decimal (0.88 = 88%)."""
    if T <= 0:
        return max(S - K, 0.0)
    if sigma <= 0:
        return max(S - K * math.exp(-r * T), 0.0)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)


def expiry_return(strike: float, premium: float, target: float):
    """At-expiry intrinsic + return multiple for a long call."""
    intrinsic = max(target - strike, 0.0)
    mult = intrinsic / premium if premium > 0 else float("inf")
    return intrinsic, mult


def crossover(k1: float, p1: float, k2: float, p2: float):
    """Underlying price S* where (S-k1)/p1 == (S-k2)/p2. k1<k2 expected.

    None if premiums equal (parallel, no crossover).
    """
    denom = p2 - p1
    if abs(denom) < 1e-12:
        return None
    return (p2 * k1 - p1 * k2) / denom


def compute(strikes, targets, underlying=None, exit_dte=None, exit_iv=None,
            entry_iv=None, r=0.04):
    strikes = sorted(strikes, key=lambda x: x[0])  # ascending by strike
    out = {
        "inputs": {
            "strikes": [{"strike": k, "premium": p} for k, p in strikes],
            "targets": targets,
            "underlying": underlying,
            "exit_dte": exit_dte,
            "exit_iv": exit_iv,
            "entry_iv": entry_iv,
        },
        "warnings": [],
    }

    # --- at-expiry return table ---
    table = []
    for S in targets:
        row = {"target": S, "returns": []}
        best_idx, best_mult = 0, -1.0
        for i, (k, p) in enumerate(strikes):
            intrinsic, mult = expiry_return(k, p, S)
            row["returns"].append({
                "strike": k, "intrinsic": round(intrinsic, 2),
                "multiple": round(mult, 3), "pct": round((mult - 1) * 100, 1),
            })
            if mult > best_mult:
                best_mult, best_idx = mult, i
        row["best_strike"] = strikes[best_idx][0]
        table.append(row)
    out["expiry_table"] = table

    # --- pairwise crossovers (adjacent strikes) ---
    crossings = []
    for i in range(len(strikes) - 1):
        k1, p1 = strikes[i]
        k2, p2 = strikes[i + 1]
        S = crossover(k1, p1, k2, p2)
        c = {"lower_strike": k1, "higher_strike": k2, "crossover": None, "note": ""}
        if S is None:
            c["note"] = "premiums 相等, 无 crossover"
        else:
            c["crossover"] = round(S, 2)
            if S <= k2:
                c["note"] = (f"crossover ${S:.2f} ≤ 高 strike ${k2:.0f} = 非经济区 "
                             f"(高 strike 尚未 ITM); 现实目标价下 ${k1:.0f}C 一律更优")
            else:
                c["note"] = (f"标的到期 > ${S:.2f} 时 ${k2:.0f}C 收益才反超 ${k1:.0f}C; "
                             f"目标 < ${S:.2f} → ${k1:.0f}C 更优 (越 OTM ≠ 越赚)")
        crossings.append(c)
    out["crossovers"] = crossings

    # --- optional BS pre-expiry scenario ---
    if exit_dte is not None and exit_iv is not None:
        T = exit_dte / 365.0
        sigma = exit_iv / 100.0
        scen = []
        for S in targets:
            row = {"target": S, "exit_dte": exit_dte, "exit_iv": exit_iv, "values": []}
            best_idx, best_mult = 0, -1.0
            for i, (k, p) in enumerate(strikes):
                val = bs_call(S, k, T, sigma, r)
                mult = val / p if p > 0 else float("inf")
                row["values"].append({
                    "strike": k, "bs_value": round(val, 2),
                    "multiple": round(mult, 3), "pct": round((mult - 1) * 100, 1),
                })
                if mult > best_mult:
                    best_mult, best_idx = mult, i
            row["best_strike"] = strikes[best_idx][0]
            scen.append(row)
        out["scenario_table"] = scen
        # 排名相反检测: at-expiry 赢家 vs pre-expiry 赢家 (时间价值导致)
        exp_winner = {r["target"]: r["best_strike"] for r in table}
        for srow in scen:
            t = srow["target"]
            if exp_winner.get(t) is not None and exp_winner[t] != srow["best_strike"]:
                out["warnings"].append(
                    f"@${t:.0f} 排名相反: 持到到期 ${exp_winner[t]:.0f}C 赢 / "
                    f"提前平仓 ${srow['best_strike']:.0f}C 赢 → 先定'持到到期 vs 提前平'; "
                    f"差异全在时间价值 (gamma + vega/IV 方向)。crossover 只管持到到期。")

    # --- warnings ---
    if entry_iv is not None and entry_iv >= 70:
        out["warnings"].append(
            f"entry IV {entry_iv:.0f}% 偏高 → OTM extrinsic 被吹贵, crossover 被推高、"
            f"远离目标; 高 IV 入场倾向低 strike/DITM (memory options_vehicle_selection.md §IV-pairing "
            f"+ leaps-call-template §11.5)")
    if exit_iv is not None and entry_iv is not None and exit_iv < entry_iv:
        out["warnings"].append(
            f"exit IV {exit_iv:.0f}% < entry IV {entry_iv:.0f}% → 涨到目标的路径上 IV 回落, "
            f"vega 越高 (越 OTM) 亏越多 → 进一步利空 OTM strike")
    return out


def fmt(out) -> str:
    i = out["inputs"]
    L = ["=" * 66,
         "Strike 收益量化 (到期口径; 该不该买/多大 仍走 Bayesian + kelly_size)",
         "=" * 66]
    sk = "   ".join(f"${s['strike']:.0f}C@${s['premium']:.2f}" for s in i["strikes"])
    L.append(f"  strikes: {sk}"
             + (f"   underlying ${i['underlying']}" if i["underlying"] is not None else ""))
    L.append("-" * 66)
    strikes = [s["strike"] for s in i["strikes"]]
    L.append("  目标价 | " + " ".join(f"${k:>5.0f}C" for k in strikes) + " | 赢家")
    for row in out["expiry_table"]:
        cells = []
        for rr in row["returns"]:
            mark = "*" if rr["strike"] == row["best_strike"] else " "
            cells.append(f"{rr['multiple']:>5.2f}x{mark}")
        L.append(f"  ${row['target']:>5.0f} | " + " ".join(cells)
                 + f" | ${row['best_strike']:.0f}C")
    L.append("-" * 66)
    for c in out["crossovers"]:
        cv = f"${c['crossover']}" if c["crossover"] is not None else "—"
        L.append(f"  crossover ${c['lower_strike']:.0f}C↔${c['higher_strike']:.0f}C = {cv}")
        L.append(f"    {c['note']}")
    if "scenario_table" in out:
        L.append("-" * 66)
        L.append(f"  BS 提前平仓情景 (到目标时剩 {i['exit_dte']:.0f}d, IV {i['exit_iv']:.0f}%):")
        for row in out["scenario_table"]:
            cells = "   ".join(f"${v['strike']:.0f}C {v['multiple']:.2f}x" for v in row["values"])
            L.append(f"    @${row['target']:.0f}: {cells}  → 赢家 ${row['best_strike']:.0f}C")
    for w in out["warnings"]:
        L.append(f"  ⚠ {w}")
    L.append("=" * 66)
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(
        description="Call strike 收益量化器 (crossover + IV 调整)",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--strikes", nargs="+", required=True,
                    help="strike:premium 列表, 如 60:13.90 70:11.10")
    ap.add_argument("--targets", nargs="+", type=float, required=True,
                    help="标的目标价列表, 如 80 90 100 110")
    ap.add_argument("--underlying", type=float, default=None, help="标的现价 (仅展示)")
    ap.add_argument("--exit-dte", type=float, default=None,
                    help="可选: 到目标价时剩余 DTE(天), 配 --exit-iv 做 BS 提前平仓情景")
    ap.add_argument("--exit-iv", type=float, default=None,
                    help="可选: 提前平仓情景的 IV(百分数, 如 55), 配 --exit-dte")
    ap.add_argument("--entry-iv", type=float, default=None,
                    help="可选: 入场 IV(百分数), 仅用于 warning")
    ap.add_argument("--rate", type=float, default=0.04, help="无风险利率, 默认 0.04")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    strikes = []
    for s in a.strikes:
        if ":" not in s:
            sys.exit(f"[strike_compare] --strikes 格式错误: '{s}' 应为 strike:premium 如 60:13.90")
        ks, ps = s.split(":", 1)
        try:
            strikes.append((float(ks), float(ps)))
        except ValueError:
            sys.exit(f"[strike_compare] --strikes 数值无法解析: '{s}'")
    if len(strikes) < 2:
        sys.exit("[strike_compare] 至少需要 2 个 strike 才能比较")
    for k, p in strikes:
        if p <= 0:
            sys.exit(f"[strike_compare] premium 必须 > 0 (strike ${k:.0f})")

    out = compute(strikes, a.targets, underlying=a.underlying, exit_dte=a.exit_dte,
                  exit_iv=a.exit_iv, entry_iv=a.entry_iv, r=a.rate)
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(fmt(out))


if __name__ == "__main__":
    main()
