#!/usr/bin/env python3
"""止损守卫 v2 —— ladder_timing 三格按代价守卫重判。

预注册全文: quant/research/stop_cost_guard_design.md
**U/C 已锁 (用户): U = 止损代价 / C = 每笔已入场。T 待锁 —— 锁定前本脚本拒跑。**

## 原则 (设计稿 §1)
守卫的量纲必须跟着止损模型走: 入场锚定 (escape 的对称 2N) 下两臂每股风险按构造相同
⟹ 次数 ≡ 代价, +5pp 次数守卫合法; gate 锚定 (live bfloor_buf) 下单位随入场时点变
⟹ 次数 ≠ 代价, 必须直接量代价。家系: +5pp → +8pp (手工找补) → v2 (直接量)。

## 定义 (§3, U/C 已锁)
每 episode 实现止损代价 (N 计) = Σ_止损档 w × (px入 − px出·(1−SLIP)) / ATR20[D1]
(gpt.measure 的 `stop_cost` 键; 未止损 = 0; 负值不夹取)。
配对 = 两臂都入场的 episode (判据① 同一谓词); 按月 block bootstrap 10k draws CI90,
seed 20260809 (新种子, 不复用已跑轮次)。

## T (§4, 待锁)
  A_strict    代价差 CI90 上界 < 0 才过 (显著更省才过)
  B_harm_veto 代价差 CI90 下界 > 0 才否 (不显著更贵 = 过)

## 范围 (§5)
只重判 ladder_timing 三格 (①②③④⑥ 原样, ⑤ 换代价守卫); ANCHOR 主裁决。
PASS → 建议 fastlane 留任 + A7 结案; FAIL → 建议撤下回 ×2。只出建议, 实施等 用户。
②③④ 不重开 —— 它们的下一次机会 = +12 个月复测, 届时本守卫适用 (那才是盲的)。

Usage: .venv/moomoo/bin/python3 quant/research/stop_cost_guard_backtest.py [--json] [--report PATH]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import argparse  # noqa: E402
import json  # noqa: E402
import statistics as st  # noqa: E402

from quant.core.paths import KLINE_DEEP_DIR as KDIR  # noqa: E402
from quant.research import gate_path_taxonomy_backtest as gpt  # noqa: E402  统计件单一真源
from quant.research import ladder_timing_backtest as lt  # noqa: E402  样本构造单一真源

# ✅ T 已锁定 (用户): **B_harm_veto**—— 代价差 CI90 下界 > 0 才否。
# 守卫的本职是否决伤害; 收益的举证在判据①②肩上 (设计稿 §4, 含已见信息披露)。跑完不改。
T_MODE = "B_harm_veto"
BOOT_SEED = 20260809          # 新种子 (§3): 不复用 20260808 两轮已抽过的月索引


def cost_guard(rows, mode, draws=gpt.BOOT_DRAWS, seed=BOOT_SEED):
    """代价守卫读数 + 判定。配对谓词与判据① 同一条 (两臂都入场)。"""
    if not rows:
        return None
    months = sorted({r["month"] for r in rows})
    idx = gpt._month_index(months, draws, seed)
    both = lambda r: r["FAST"]["riskN"] is not None and r["SLOW"]["riskN"] is not None
    bm = gpt._pair(rows, lambda r: r["FAST"]["stop_cost"] - r["SLOW"]["stop_cost"], both)
    n_pair = sum(v[1] for v in bm.values())
    point = round(sum(v[0] for v in bm.values()) / max(n_pair, 1), 4)
    ci = gpt._boot(bm, months, idx)

    kept = [r for r in rows if both(r)]
    arm_cost = {a: round(st.mean(r[a]["stop_cost"] for r in kept), 4) if kept else None
                for a in gpt.ARMS}
    if mode == "A_strict":
        ok = bool(ci and ci[1] < 0)
    elif mode == "B_harm_veto":
        ok = bool(ci and not (ci[0] > 0))
    else:
        raise ValueError(f"T_MODE 未锁定或非法: {mode!r}")
    return {"mode": mode, "n_pair": n_pair, "cost_point": point, "cost_ci90": ci,
            "arm_cost_mean": arm_cost, "pass": ok}


def rejudge(base, guard):
    """①②③④⑥ 原样 + ⑤ 换代价守卫 → 本格重判。"""
    if not base or not base.get("n") or guard is None:
        return {"decision": "no_data", "crit": {}, "text": "无样本"}
    c = dict(base["crit"])
    c.pop("all_pass", None)
    old5 = c.pop("c5_stop_guard", None)
    c["c5_cost_guard"] = guard["pass"]
    c["all_pass"] = all(c.values())
    if not c["c6_kish"]:
        return {"decision": "insufficient", "crit": c, "old_c5_count_guard": old5,
                "text": f"**数据不足不裁决**(Kish {base['kish']} < {gpt.KISH_MIN})"}
    if c["all_pass"]:
        return {"decision": "support", "crit": c, "old_c5_count_guard": old5,
                "text": "**六判据全过 (⑤=代价守卫) → 建议 `fastlane_k=0.5` 留任, A7 结案**"
                        " — 实施等 用户"}
    miss = [k for k, v in c.items() if not v and k != "all_pass"]
    return {"decision": "reject", "crit": c, "old_c5_count_guard": old5,
            "text": "**未获支持 → 建议撤下 `_fastlane` 回 ×2**(未过: "
                    + ", ".join(miss) + ") — 实施等 用户"}


def render(cells, verds):
    L = ["=== 止损守卫 v2: ladder_timing 三格按代价守卫重判 ===",
         f"U=止损代价(N, 实现值含滑点) / C=每笔已入场(判据①同谓词) / T={T_MODE} / "
         f"seed {BOOT_SEED} / 其余判据①②③④⑥ 原样", ""]
    for cell in lt.CELLS:
        g = cells[cell]["guard"]
        base = cells[cell]["base"]
        if g is None:
            L.append(f"--- {lt.CELL_CN[cell]} --- 无样本")
            continue
        L.append(f"--- {lt.CELL_CN[cell]} --- n={base['n']} 配对 n={g['n_pair']}")
        L.append(f"  代价均值(已入场): FAST {g['arm_cost_mean']['FAST']:+.4f}N / "
                 f"SLOW {g['arm_cost_mean']['SLOW']:+.4f}N")
        L.append(f"  ⑤v2 代价差(F−S) {g['cost_point']:+.4f}N CI90 {gpt._ci(g['cost_ci90'])}"
                 f" [{g['mode']}] → {'✓' if g['pass'] else '✗'}"
                 f"   (旧⑤次数守卫: {'✓' if verds[cell].get('old_c5_count_guard') else '✗'})")
        L.append(f"  → {verds[cell]['text']}")
        L.append("")
    L.append(f"VERDICT (主裁决 = {lt.ADJUDICATING}):")
    L.append(f"  {verds[lt.ADJUDICATING]['text']}")
    a, r = verds[lt.ADJUDICATING]["decision"], verds[lt.REPLICATION]["decision"]
    L.append(f"复制臂一致性: ANCHOR={a} / ROLL={r} → "
             + ("一致" if a == r else "⚠️ 分叉 (构造敏感, 写回设计稿)"))
    L.append("范围提醒 (§5): ②③④ 不重开; ④ 的下一次机会 = +12 个月复测, 届时本守卫适用。")
    return "\n".join(L)


def main_cli(argv=None):
    ap = argparse.ArgumentParser(description="止损守卫 v2 重判")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--report", type=Path, default=None)
    a = ap.parse_args(argv)
    if T_MODE is None:
        print("⛔ T 未锁定 (stop_cost_guard_design.md §4 待 用户 选 A_strict / B_harm_veto)。"
              "锁定前拒跑 —— 这是预注册纪律的代码强制点, 不是 bug。", file=sys.stderr)
        return 2

    cells, verds = {}, {}
    for cell in lt.CELLS:
        rows, _, _ = lt.CELL_FN[cell](KDIR, lt.DECISION_T)
        base = gpt.analyse(rows, cell)
        guard = cost_guard(rows, T_MODE)
        cells[cell] = {"base": base, "guard": guard}
        verds[cell] = rejudge(base, guard)
    text = render(cells, verds)
    if a.json:
        print(json.dumps({"cells": {c: cells[c]["guard"] for c in cells},
                          "verdict": verds, "t_mode": T_MODE},
                         ensure_ascii=False, indent=1, default=float))
    else:
        print(text)
    if a.report:
        a.report.parent.mkdir(parents=True, exist_ok=True)
        a.report.write_text("# 止损守卫 v2 重判 (机器输出存档)\n\n```\n" + text + "\n```\n")
    return 0


if __name__ == "__main__":
    sys.exit(main_cli())
