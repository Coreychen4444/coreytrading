#!/usr/bin/env python3
"""kelly_size.py — 分数凯利仓位上限计算器.

把"概率加权的赌注"从口号变成一个 size 上限。回答的是"进的话最多多大",
不回答"该不该进"(那是 Bayesian + Pre-Trade Check)。

公式:
    f* = (b*p - q) / b = (p*(b+1) - 1) / b
    p = 后验胜率(你的 Bayesian 估计)
    q = 1 - p
    b = 净赔率(赢拿 b, 输亏 1 单位 at-risk)

关键区分(见 strategy/kelly-position-sizing.md §4):
    at-risk $   = fraction * f* * bankroll      ← 会真正亏掉的部分
    名义 $      = at-risk $ / loss_per_unit      ← 仓位名义大小
      long option/LEAPS : loss_per_unit = 1.0   (名义 = at-risk)
      spread            : loss_per_unit = (width-credit)/width
      stock + 20% stop  : loss_per_unit = 0.20  (名义 = 5x at-risk)
      naked stock no stop: Kelly 不适用

永远用分数凯利(默认 half=0.5; 陌生/相关高用 quarter=0.25)。
真实下单 size = MIN(本脚本名义, concentration cap, ladder 预算) —— 取最紧的。

CLI:
    python3 quant/risk/kelly_size.py --p 0.65 --b 2.5 --bankroll 50000 \
        --fraction 0.5 --loss-per-unit 1.0 --cap-pct 25 [--json]

    # 期权: 用 entry/target 推 b (b = (target-entry)/entry):
    python3 quant/risk/kelly_size.py --p 0.6 --entry 2.47 --target 8.0 \
        --bankroll 50000 --loss-per-unit 1.0
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 直接 `python3 quant/<层>/<模块>.py` 时让 `quant.*` 可 import

import argparse
import json


def kelly_fraction(p: float, b: float) -> float:
    """full Kelly f* = (b*p - (1-p)) / b. 负值 = 无 edge。"""
    if b <= 0:
        raise ValueError("b(净赔率) 必须 > 0")
    return (b * p - (1.0 - p)) / b


def compute(p, b, fraction, bankroll, loss_per_unit, cap_pct):
    out = {
        "inputs": {
            "p": p, "b": round(b, 4), "fraction": fraction,
            "bankroll": bankroll, "loss_per_unit": loss_per_unit,
            "cap_pct": cap_pct,
        },
        "warnings": [],
    }
    f_full = kelly_fraction(p, b)
    out["f_full"] = round(f_full, 4)

    if f_full <= 0:
        out["edge"] = False
        out["warnings"].append(
            f"f* = {f_full:.3f} ≤ 0 → 这个 p/b 组合没有 edge,不下注。"
            f"(b={b:.2f} 需要 p > {1/(b+1):.1%} 才有正期望)"
)
        out["fraction_f"] = 0.0
        out["at_risk_dollar"] = 0.0
        out["notional_dollar"] = 0.0
        out["binding"] = "no_edge"
        return out

    out["edge"] = True
    f_frac = f_full * fraction
    out["fraction_f"] = round(f_frac, 4)

    at_risk = f_frac * bankroll
    notional_uncapped = at_risk / loss_per_unit if loss_per_unit > 0 else float("inf")

    binding = "kelly"
    notional = notional_uncapped
    if cap_pct is not None:
        cap_notional = (cap_pct / 100.0) * bankroll
        if cap_notional < notional_uncapped:
            notional = cap_notional
            at_risk = notional * loss_per_unit  # 名义被压, at-risk 同步缩
            binding = "concentration_cap"
            out["warnings"].append(
                f"concentration cap ({cap_pct:.0f}% 名义 = ${cap_notional:,.0f}) "
                f"比 Kelly 名义 (${notional_uncapped:,.0f}) 更紧 → cap 生效。"
                f"说明这一注 Kelly 想要的比组合能承受的大,按 cap 走。"
)

    out["at_risk_dollar"] = round(at_risk, 2)
    out["at_risk_pct_bankroll"] = round(100 * at_risk / bankroll, 2)
    out["notional_dollar"] = round(notional, 2)
    out["notional_pct_bankroll"] = round(100 * notional / bankroll, 2)
    out["binding"] = binding
    return out


def fmt(out) -> str:
    i = out["inputs"]
    L = []
    L.append("=" * 60)
    L.append("Kelly 仓位上限(上限,不是目标;真实 size 还要 MIN concentration/ladder)")
    L.append("=" * 60)
    L.append(f"  输入: p={i['p']:.0%}  b={i['b']:.2f}:1  fraction={i['fraction']}"
             f"({'half' if i['fraction']==0.5 else 'quarter' if i['fraction']==0.25 else 'custom'} Kelly)")
    L.append(f"        bankroll=${i['bankroll']:,.0f}  loss/unit={i['loss_per_unit']}")
    L.append("-" * 60)
    L.append(f"  full Kelly f*        : {out['f_full']:.1%}")
    if not out["edge"]:
        for w in out["warnings"]:
            L.append(f"  ⚠ {w}")
        L.append("=" * 60)
        return "\n".join(L)
    L.append(f"  分数 Kelly f         : {out['fraction_f']:.1%}  (at-risk 比例)")
    L.append(f"  → at-risk $          : ${out['at_risk_dollar']:,.0f}"
             f"  ({out['at_risk_pct_bankroll']:.1f}% bankroll)")
    L.append(f"  → 仓位名义 $         : ${out['notional_dollar']:,.0f}"
             f"  ({out['notional_pct_bankroll']:.1f}% bankroll)")
    L.append(f"  binding 约束         : {out['binding']}")
    for w in out["warnings"]:
        L.append(f"  ⚠ {w}")
    L.append("-" * 60)
    L.append("  提醒: p 是你的主观后验(垃圾进垃圾出);同层相关仓位需套联合 Kelly")
    L.append("        (见 chain_layers.py + kelly-position-sizing.md §7)")
    L.append("=" * 60)
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description="分数凯利仓位上限计算器")
    ap.add_argument("--p", type=float, required=True, help="后验胜率 0-1")
    ap.add_argument("--b", type=float, help="净赔率(赢:亏),与 --entry/--target 二选一")
    ap.add_argument("--entry", type=float, help="期权入场价(配 --target 推 b)")
    ap.add_argument("--target", type=float, help="期权目标价(配 --entry 推 b)")
    ap.add_argument("--fraction", type=float, default=0.5,
                    help="Kelly 分数, 默认 0.5(half); 陌生/相关高用 0.25(quarter)")
    ap.add_argument("--bankroll", type=float, required=True, help="组合总值 / 风险资本")
    ap.add_argument("--loss-per-unit", type=float, default=1.0,
                    help="单位最大亏损比例: long option=1.0, stock+20%%stop=0.20")
    ap.add_argument("--cap-pct", type=float, default=None,
                    help="concentration cap(名义 %% of bankroll), 可选; 与 Kelly 取 MIN")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    if not (0 < a.p < 1):
        sys.exit("[kelly] --p 必须在 (0,1)")
    if a.b is None:
        if a.entry and a.target and a.entry > 0:
            a.b = (a.target - a.entry) / a.entry
        else:
            sys.exit("[kelly] 需要 --b 或 (--entry 且 --target)")
    if not (0 < a.loss_per_unit <= 1):
        sys.exit("[kelly] --loss-per-unit 必须在 (0,1]")

    out = compute(a.p, a.b, a.fraction, a.bankroll, a.loss_per_unit, a.cap_pct)
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(fmt(out))


if __name__ == "__main__":
    main()
