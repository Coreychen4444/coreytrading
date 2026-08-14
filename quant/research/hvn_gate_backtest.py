#!/usr/bin/env python3
"""HVN 锚定 gate vs pivot 簇 gate — 主回测 (预注册 private preregistration, 已锁定)。

## 为什么是一个衍生脚本而不是改 gate_backtest2
预注册要求「原三种 kind 必须复现 round-2 的数字, 复现不了就中止」。这条纪律只有在
**跑对照的那份代码与当初跑出 round-2 的那份逐字相同**时才有效力。所以:
  - `gate_backtest2` 只加了一个**默认恒等**的扩展点 (kinds / extra_gates 参数);
    不传参数时它走的还是原来那条路径 —— 对照因此是真对照, 不是自证。
  - 本文件只提供第 4 个 kind (`hvn`) 的画线函数, 引擎/反事实/统计口径全部 import。

## HVN gate 定义 (预注册 §2, 逐条钉在 tests/research/test_hvn_gate_screen.py)
`hvn_gate_screen.hvn_gate`: 破位日 i 往前 60 根日线三角铺开 profile → auto_bucket →
top_hvn(n=5) → **价格上方最近那条带的上沿**; 无 → 回退 cluster 的取法 (回退率报出)。
与 cluster 同拍: 破位日画一次, 固定, 无前视。

## 裁决 (预注册 §3 + §3a, 运行前锁定)
裁决格 = `_x2` (×2 日 / vol off / primary / T+60)。四判据:
  1 拒绝权不弱: hvn 的 notfired-vs-fired 反事实 gap ≤ cluster 的, 且 CI90 排除 0
  2 保费不更贵: hvn 的 ΔT60 不比 cluster 差超过 0.5pp
  3 触发率不退化: hvn 触发率 ∈ cluster ±15pp
  4 回退比例 ≤ 20%
任一不满足 → 不采纳, cluster 保持 incumbent。平手 → 保留 cluster。

Usage: .venv/moomoo/bin/python3 quant/research/hvn_gate_backtest.py [--json] [--report PATH]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import argparse  # noqa: E402
import json  # noqa: E402

from quant.research import gate_backtest2 as gb2  # noqa: E402
from quant.research.hvn_gate_screen import hvn_gate  # noqa: E402

KINDS_EXT = gb2.KINDS + ("hvn",)
DECISION_CELL = "_x2"          # 预注册 §3a: ×2 日 / vol off
DECISION_MODE = "primary"
DECISION_T = 60

# 预注册判据阈值 (运行前锁定, 不许事后调)
PREMIUM_TOL = 0.005            # 判据 2: expectancy 最多差 0.5pp
FIRE_TOL = 0.15                # 判据 3: 触发率 ±15pp
FALLBACK_MAX = 0.20            # 判据 4: 回退比例 ≤ 20%

# Private baseline values are loaded from an ignored JSON receipt. Public source
# retains only comparison mechanics and print-precision tolerances.
TOL_PCT = 0.0005 + 1e-9        # ±0.05pp = 印刷精度的一半
TOL_FIRE = 0.005 + 1e-9        # ±0.5pp


def load_baseline(path: Path) -> dict:
    """Load a private regression receipt without embedding study results."""
    if not path.exists():
        raise FileNotFoundError(f"private baseline not found: {path}")
    baseline = json.loads(path.read_text(encoding="utf-8"))
    required = {"n_episodes", "months", "kish_n", "A_primary_T60", "cells"}
    if not isinstance(baseline, dict) or not required <= set(baseline):
        raise ValueError(f"baseline requires keys: {sorted(required)}")
    if not isinstance(baseline["A_primary_T60"], dict) or not isinstance(baseline["cells"], dict):
        raise ValueError("baseline A_primary_T60 and cells must be JSON objects")
    return baseline


def hvn_builder(ctx):
    """gate_backtest2.collect 的扩展点: 返回破位日固定的 hvn level_fn。

    回退语义 (预注册 §2): 上方无 HVN → 用 cluster 的取法。cluster 也无 →
    None = gate 不存在 = 永不触发 (与 round-2 cluster 同构)。
    """
    lvl = hvn_gate(ctx["bars"], ctx["i"], ctx["close_i"], ctx["atr14_i"])
    fell_back = lvl is None
    if fell_back:
        lvl = ctx["cluster_gate"]
    _FALLBACK.append(fell_back)
    return lambda j, _g=lvl: _g


_FALLBACK: list[bool] = []


# ---------- 回归对照 ----------

def add_gap_points(summary):
    """gap 点估计 = Σ(A@notfired)/n_nf − Σ(A@fired)/n_f, 从 _by_month 槽位算。

    必须在 strip_private **之前**调用。summarize 只存了 notfired 一侧的均值,
    而判据 1 比较的是**差**; bootstrap 只给 CI 不给点估计, 所以在这里补齐 ——
    用 CI 中点冒充点估计是另一个量, 不能混。
    """
    for cell in summary["cells"].values():
        if cell.get("appendix"):
            continue
        for key, row in cell.items():
            if not isinstance(row, dict) or "_by_month" not in row:
                continue
            nf_s = nf_c = f_s = f_c = 0.0
            for agg in row["_by_month"].values():
                nf_s += agg[3]; nf_c += agg[4]; f_s += agg[5]; f_c += agg[6]
            row["notfired_gap_point"] = ((nf_s / nf_c) - (f_s / f_c)) if (nf_c and f_c) else None


def head_to_head(summary, suffixes=("_x1", "_x1_vol", "_x2", "_x2_vol"),
                 draws=gb2.BOOT_DRAWS, seed=gb2.BOOT_SEED):
    """hvn − cluster 的**配对**按月 block bootstrap CI (必须在 strip_private 之前)。

    预注册 §3 的收尾条款是「平手 (判据全过但差异不显著) → 保留 cluster」——
    要执行它就得有"差异显著吗"的读数, 而四判据本身只给各自的 CI, 不给**差的 CI**。
    两条几乎重合的区间看着像平手, 但那是眼测; 这里把它算出来。
    配对 = 同 episodes 同月同止损口径, 只换画线方式。
    """
    import numpy as np
    out = {}
    for suf in suffixes:
        hc = summary["cells"].get(f"hvn{suf}") or {}
        cc = summary["cells"].get(f"cluster{suf}") or {}
        h = hc.get(f"{DECISION_MODE}_T{DECISION_T}") or {}
        c = cc.get(f"{DECISION_MODE}_T{DECISION_T}") or {}
        if "_by_month" not in h or "_by_month" not in c:
            continue
        hm, cm = h["_by_month"], c["_by_month"]
        months = sorted(set(hm) | set(cm))
        M = len(months)
        Z = [0.0, 0, 0, 0.0, 0, 0.0, 0]
        A = np.asarray([[float(x) for x in hm.get(m, Z)] for m in months])
        B = np.asarray([[float(x) for x in cm.get(m, Z)] for m in months])
        rng = np.random.default_rng(seed)
        idx = rng.integers(0, M, size=(draws, M))

        def q(v):
            return [round(float(np.quantile(v, gb2.CI_Q[0])), 4),
                    round(float(np.quantile(v, gb2.CI_Q[1])), 4)]

        Ha, Bb = A[idx].sum(axis=1), B[idx].sum(axis=1)
        n = Ha[:, 1]
        ok = n > 0
        exp_d = (Ha[ok, 0] / n[ok]) - (Bb[ok, 0] / n[ok])
        stop_d = (Ha[ok, 2] / n[ok]) - (Bb[ok, 2] / n[ok])
        m2 = (Ha[:, 4] > 0) & (Ha[:, 6] > 0) & (Bb[:, 4] > 0) & (Bb[:, 6] > 0)
        gap_d = ((Ha[m2, 3] / Ha[m2, 4] - Ha[m2, 5] / Ha[m2, 6])
                 - (Bb[m2, 3] / Bb[m2, 4] - Bb[m2, 5] / Bb[m2, 6]))
        out[suf] = {
            "exp_diff_point": round(float(h["diff_mean"] - c["diff_mean"]), 4),
            "exp_diff_ci90": q(exp_d),
            "stop_diff_point": round(float(h["stop_rate_policy"] - c["stop_rate_policy"]), 4),
            "stop_diff_ci90": q(stop_d),
            "gap_diff_point": (round(float(h["notfired_gap_point"] - c["notfired_gap_point"]), 4)
                               if h.get("notfired_gap_point") is not None
                               and c.get("notfired_gap_point") is not None else None),
            "gap_diff_ci90": q(gap_d) if len(gap_d) else None,
            "fire_diff_point": round(float(hc["fire_rate"] - cc["fire_rate"]), 4),
        }
    return out


def regression_check(summary, baseline):
    """Compare incumbent cells with a private baseline receipt."""
    bad = []

    def cmp(label, got, want, tol):
        if got is None or abs(got - want) > tol:
            bad.append(f"{label}: got {got!r} want {want!r} (tol {tol})")

    cmp("n_episodes", summary["n_episodes"], baseline["n_episodes"], 0)
    cmp("months", summary["months"], baseline["months"], 0)
    cmp("kish_n", summary["kish_n"], baseline["kish_n"], 0.05 + 1e-9)
    a = summary["A"].get("primary_T60") or {}
    for k, want in baseline["A_primary_T60"].items():
        cmp(f"A.primary_T60.{k}", a.get(k), want, TOL_PCT if k != "stop_rate" else TOL_FIRE)
    for key, values in baseline["cells"].items():
        if not isinstance(values, list) or len(values) != 4:
            bad.append(f"{key}: baseline cell must be [fire_rate, diff_mean, ci_lo, ci_hi]")
            continue
        fire, dm, lo, hi = values
        cell = summary["cells"].get(key)
        if not cell:
            bad.append(f"{key}: 缺失")
            continue
        cmp(f"{key}.fire_rate", cell.get("fire_rate"), fire, TOL_FIRE)
        r = cell.get("primary_T60") or {}
        cmp(f"{key}.diff_mean", r.get("diff_mean"), dm, TOL_PCT)
        ci = r.get("diff_ci90")
        if not ci:
            bad.append(f"{key}.diff_ci90: 缺失")
        else:
            cmp(f"{key}.ci_lo", ci[0], lo, TOL_PCT)
            cmp(f"{key}.ci_hi", ci[1], hi, TOL_PCT)
    return (not bad), bad


# ---------- 判据 ----------

def _row(summary, kind, suffix):
    cell = summary["cells"].get(f"{kind}{suffix}") or {}
    return cell, (cell.get(f"{DECISION_MODE}_T{DECISION_T}") or {})


def pair_verdict(summary, suffix, fallback_rate):
    """一对 (hvn vs cluster) 同格比较 → 四判据机械读数。"""
    h_cell, h = _row(summary, "hvn", suffix)
    c_cell, c = _row(summary, "cluster", suffix)
    if not h or not c:
        return {"suffix": suffix, "error": "格缺失"}

    h_gap, c_gap = h.get("notfired_gap_ci90"), c.get("notfired_gap_ci90")
    # gap = notfired 组 A 收益 − fired 组 A 收益; **负 = 被拒的那批确实更差 = 有拒绝权**
    h_pt, c_pt = h.get("notfired_gap_point"), c.get("notfired_gap_point")

    # 判据 1: hvn 的 gap ≤ cluster 的 (至少一样能挡住坏的) 且 CI90 排除 0
    crit1 = bool(h_pt is not None and c_pt is not None and h_pt <= c_pt
                 and h_gap and (h_gap[1] < 0 or h_gap[0] > 0))
    crit2 = bool(h.get("diff_mean") is not None and c.get("diff_mean") is not None
                 and h["diff_mean"] >= c["diff_mean"] - PREMIUM_TOL)
    crit3 = bool(h_cell.get("fire_rate") is not None and c_cell.get("fire_rate") is not None
                 and abs(h_cell["fire_rate"] - c_cell["fire_rate"]) <= FIRE_TOL)
    crit4 = bool(fallback_rate is not None and fallback_rate <= FALLBACK_MAX)
    return {
        "suffix": suffix,
        "hvn": {"fire_rate": h_cell.get("fire_rate"), "diff_mean": h.get("diff_mean"),
                "diff_ci90": h.get("diff_ci90"), "p10": h.get("p10"),
                "stop_rate_policy": h.get("stop_rate_policy"),
                "notfired_A_mean": h.get("notfired_A_mean"),
                "notfired_gap_ci90": h_gap, "notfired_gap_point": h_pt},
        "cluster": {"fire_rate": c_cell.get("fire_rate"), "diff_mean": c.get("diff_mean"),
                    "diff_ci90": c.get("diff_ci90"), "p10": c.get("p10"),
                    "stop_rate_policy": c.get("stop_rate_policy"),
                    "notfired_A_mean": c.get("notfired_A_mean"),
                    "notfired_gap_ci90": c_gap, "notfired_gap_point": c_pt},
        "crit1_rejection_not_weaker": crit1,
        "crit2_premium_not_worse": crit2,
        "crit3_fire_rate_in_band": crit3,
        "crit4_fallback_ok": crit4,
        "all_pass": bool(crit1 and crit2 and crit3 and crit4),
    }


def render(summary, verdicts, fallback_rate, reg_ok, reg_bad, smoke=False, h2h=None):
    L = [f"=== HVN gate 主回测 === {summary['n_episodes']} episodes / "
         f"{summary['months']} 月 / Kish ≈{summary['kish_n']}"]
    L.append("回归对照: ⚠️ 跳过 (--limit 冒烟运行, **不可裁决**)" if smoke else
             f"回归对照 (原三 kind vs round-2): "
             f"{'✅ 逐格复现' if reg_ok else '❌ 未复现 — 中止'}")
    for b in reg_bad[:20]:
        L.append(f"    ✗ {b}")
    if not reg_ok:
        return "\n".join(L)
    L += ["", f"HVN 回退 cluster 比例: {fallback_rate:.1%}  (判据 4 上限 {FALLBACK_MAX:.0%})", ""]
    hdr = (f"{'cell':<16}{'fire':>6}{'ΔT60':>9}{'CI90':>18}{'p10':>8}"
           f"{'止损率':>8}{'nf 反事实':>10}{'gap CI90':>18}")
    L.append(hdr)
    for v in verdicts:
        if v.get("error"):
            continue
        for who in ("cluster", "hvn"):
            r = v[who]
            ci = r["diff_ci90"]
            ci_s = f"[{ci[0]*100:+.1f},{ci[1]*100:+.1f}]" if ci else "—"
            g = r["notfired_gap_ci90"]
            g_s = f"[{g[0]*100:+.1f},{g[1]*100:+.1f}]" if g else "—"
            L.append(f"{who + v['suffix']:<16}{r['fire_rate']:>6.0%}"
                     f"{gb2._f(r['diff_mean'], 9)}{ci_s:>18}{gb2._f(r['p10'])}"
                     f"{r['stop_rate_policy']:>8.0%}"
                     f"{gb2._f(r['notfired_A_mean'], 10)}{g_s:>18}")
        L.append("")
    L.append("判据机械读数 (预注册 §3; 裁决格 = _x2, 其余为稳健性):")
    for v in verdicts:
        if v.get("error"):
            L.append(f"  {v['suffix']}: {v['error']}")
            continue
        mark = "★裁决格" if v["suffix"] == DECISION_CELL else "      "
        L.append(f"  {mark} {v['suffix']:<9} "
                 f"①拒绝权={'✓' if v['crit1_rejection_not_weaker'] else '✗'} "
                 f"②保费={'✓' if v['crit2_premium_not_worse'] else '✗'} "
                 f"③触发率={'✓' if v['crit3_fire_rate_in_band'] else '✗'} "
                 f"④回退={'✓' if v['crit4_fallback_ok'] else '✗'} "
                 f"→ {'PASS' if v['all_pass'] else 'FAIL'}")
    if h2h:
        L.append("")
        L.append("配对 h2h (hvn − cluster, 同 episodes; 用于预注册的「平手」条款):")
        L.append(f"{'cell':<10}{'Δ拒绝权gap':>13}{'CI90':>18}{'Δexp':>9}{'CI90':>18}"
                 f"{'Δ止损率':>9}{'Δ触发率':>9}")
        for suf, r in h2h.items():
            gd, gc = r["gap_diff_point"], r["gap_diff_ci90"]
            ec, ed = r["exp_diff_ci90"], r["exp_diff_point"]
            L.append(f"{suf:<10}{gb2._f(gd, 13)}"
                     f"{f'[{gc[0]*100:+.1f},{gc[1]*100:+.1f}]' if gc else '—':>18}"
                     f"{gb2._f(ed, 9)}"
                     f"{f'[{ec[0]*100:+.1f},{ec[1]*100:+.1f}]' if ec else '—':>18}"
                     f"{r['stop_diff_point']*100:>+8.1f}pp{r['fire_diff_point']*100:>+8.1f}pp")
    L.append("")
    L.append(final_verdict(verdicts, h2h)["text"])
    return "\n".join(L)


def final_verdict(verdicts, h2h):
    """预注册 §3 全文的机械执行 —— 包括最后那句「平手 → 保留 cluster」。

    判据全过 **不等于**采纳: H1 问的是「拒绝权是否不弱」, 是**非劣性**假设。
    非劣通过之后还要问"有没有更好", 而"更好"的读数只有配对 gap 差的 CI。
    CI 含 0 = 拒绝权无差异 = 平手 = 保留更简单的 incumbent。
    """
    dec = next((v for v in verdicts if v["suffix"] == DECISION_CELL), None)
    if not dec or not dec.get("all_pass"):
        return {"adopt": False, "reason": "criteria_failed",
                "text": "VERDICT: **不采纳 hvn, cluster 保持 incumbent**— 裁决格判据未全过 (预注册 §3)"}
    r = (h2h or {}).get(DECISION_CELL) or {}
    gc = r.get("gap_diff_ci90")
    tie = bool(gc and gc[0] <= 0 <= gc[1])
    if tie:
        ec = r.get("exp_diff_ci90")
        worse = bool(ec and ec[1] < 0)
        return {"adopt": False, "reason": "tie", "exp_significantly_worse": worse,
                "text": ("VERDICT: **不采纳 hvn, cluster 保持 incumbent**— 四判据全过, 但配对 "
                         f"gap 差 CI90 {gc} **含 0 = 拒绝权无差异 = 平手**, 预注册 §3 末句判归 cluster"
                         + ("; 且 hvn 的 expectancy 配对差显著为负 "
                            f"{ec} = 保费更贵而判别力没换来" if worse else ""))}
    better = bool(gc and gc[1] < 0)
    return {"adopt": better, "reason": "gap_significant",
            "text": ("VERDICT: **采纳 hvn**— 配对 gap 差 CI90 "
                     f"{gc} 排除 0 且为负 = 拒绝权显著更强" if better else
                     "VERDICT: **不采纳 hvn**— 配对 gap 差显著为正 = 拒绝权显著更弱")}


def main(argv=None):
    ap = argparse.ArgumentParser(description="HVN gate 主回测")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--report", type=Path, default=None)
    ap.add_argument("--baseline", type=Path, help="ignored JSON incumbent receipt")
    ap.add_argument("--limit", type=int, default=None, help="只扫前 N 个文件 (冒烟用, 不可裁决)")
    a = ap.parse_args(argv)
    if not a.limit and not a.baseline:
        ap.error("--baseline is required for a full adjudicating run")

    kdir = gb2.KDIR
    if a.limit:
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        for p in sorted(kdir.glob("*.jsonl"))[:a.limit]:
            (tmp / p.name).symlink_to(p)
        kdir = tmp

    _FALLBACK.clear()
    episodes = gb2.collect(kdir=kdir, kinds=KINDS_EXT,
                           extra_gates={"hvn": hvn_builder})
    fallback_rate = (sum(_FALLBACK) / len(_FALLBACK)) if _FALLBACK else None
    summary = gb2.summarize(episodes, kinds=KINDS_EXT)
    gb2.bootstrap_cis(summary)
    gb2.p10_diff_cis(summary)
    summary["d1_flags"] = gb2.d1_flags(summary)
    add_gap_points(summary)          # 必须在 strip 之前
    h2h = head_to_head(summary)      # 同上
    gb2.strip_private(summary)

    reg_ok, reg_bad = ((True, []) if a.limit else
                       regression_check(summary, load_baseline(a.baseline)))
    verdicts = ([] if not reg_ok else
                [pair_verdict(summary, s, fallback_rate)
                 for s in ("_x1", "_x1_vol", "_x2", "_x2_vol")])
    text = render(summary, verdicts, fallback_rate, reg_ok, reg_bad,
                  smoke=bool(a.limit), h2h=h2h)
    payload = {"smoke": bool(a.limit), "regression_ok": reg_ok, "head_to_head": h2h, "regression_mismatches": reg_bad,
               "fallback_rate": fallback_rate, "verdicts": verdicts,
               "summary": summary}
    if a.json:
        print(json.dumps(payload, ensure_ascii=False, indent=1, default=float))
    else:
        print(text)
    if a.report:
        a.report.parent.mkdir(parents=True, exist_ok=True)
        a.report.write_text("# HVN gate 主回测 (机器输出存档)\n\n```\n" + text + "\n```\n")
    return 0 if reg_ok else 1


if __name__ == "__main__":
    sys.exit(main())
