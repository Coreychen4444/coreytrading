#!/usr/bin/env python3
"""volume_leg_align_backtest — 量腿对齐日: 「完成日 RelVR ≥1.2」→「streak 内任一日 RelVR ≥1.2」
(volume_clock 预注册 round-2)。

问题形状: 突破日放量、跟进日平量, 现行规则只认**完成日**的量, 于是最健康的「放量突破 +
缩量站稳」形状被罚。量的钟 (round-1) 救得回来但止损率 +3.1pp 被否; 本轮测**最小的放松**:
价格腿 ×2 不动, 量腿在 streak 内任一日过即算 (day-1 的量在 price 站住期间不过期)。

## 世界 (在 volume_clock 十个世界之上追加, 池/锚/终点/统计原样复用)
W1any = ×2 + streak 内任一日 RelVR ≥1.2        (纯对齐效应, 对 W1)
W5    = W1any 或 快车道                       (**候选 = live 规则只改对齐**, 对 W1F 裁决)

## 裁决 (W5 − W1F, T60 全样本, 运行前锁定, 与 round-1 同三判据)
V: Δmean CI90 下界 ≥0 且 Δ止损率 CI90 上界 < +3pp 且 Δ覆盖率 CI90 下界 > 0 → 立项接线
K: 其余 = 维持。no_call: Kish<30 / 回归不复现 (W1−W0 须复现 round-1 Verdict 记录的当前池锚)。

Usage: .venv/moomoo/bin/python3 quant/research/volume_leg_align_backtest.py [--json] [--report PATH]
       [--kdir DIR --earn-dir DIR]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 直接 `python3 quant/<层>/<模块>.py` 时让 `quant.*` 可 import

import argparse  # noqa: E402
import json  # noqa: E402
from typing import Dict, Optional  # noqa: E402

from quant.research import relative_volume_backtest as rv  # noqa: E402
from quant.research import volume_clock_backtest as vc  # noqa: E402

WORLDS_EXT = vc.WORLDS + ("W1any", "W5")
CAND, BASE = "W5", "W1F"                 # 裁决格
PURE, PURE_BASE = "W1any", "W1"          # 纯对齐效应 (只报)
DECISION_T = vc.DECISION_T
BOOT_SEED = 20260908 + 1000              # 与 round-1 不同 seed, 避免共用抽样

# 回归复现锚 = round-1 Verdict 追记的**当前池**读数 (私有研究记录);
# round-0 锚已因池变失效, 本轮不再引它。
REGRESSION_CUR = {"n_total": 2897, "n60": 2866, "kish": 168.2,
                  "dmean": 0.004914, "dmean_ci": (0.001805, 0.008184),
                  "dstop": 0.009770, "dcover": 0.037334}
TOL = 0.0000005 + 1e-12                  # 六位小数 = 记录精度的一半


def fire_scan_any(start, end, closes, relvr, level, need=rv.NEED, ratio=vc.VOL_RATIO):
    """×2 价格腿 + **streak 内任一日** RelVR ≥ ratio → 首个满足日 j。跌回 (收盘 ≤ level) streak
    归零, day-1 的量随之作废 —— 「接受」= 价格站住期间的参与度, 不是历史上某天的。"""
    streak = 0
    for j in range(start + 1, end + 1):
        streak = streak + 1 if closes[j] > level else 0
        if streak >= need and any(relvr[k] is not None and relvr[k] >= ratio
                                  for k in range(j - streak + 1, j + 1)):
            return j
    return None


def world_entries_ext(i, end_sim, closes, gate, vols, relvr, ratios, ratios_dnx, e_mask,
                      highs, lows, atr20) -> Dict[str, Optional[int]]:
    q = vc.world_entries(i, end_sim, closes, gate, vols, relvr, ratios, ratios_dnx, e_mask,
                         highs, lows, atr20)
    q["W1any"] = fire_scan_any(i, end_sim, closes, relvr, gate)
    fl = vc.fastlane_scan(i, end_sim, closes, atr20, relvr, gate)
    q["W5"] = min([x for x in (q["W1any"], fl) if x is not None], default=None)
    return q


def collect(kdir=rv.KDIR, earn_dir=rv.EARN_DIR):
    return vc.collect(kdir=kdir, earn_dir=earn_dir, world_fn=world_entries_ext)


def _regression_ok(got: Dict) -> bool:
    e = REGRESSION_CUR
    return (got.get("n_total") == e["n_total"] and got.get("n60") == e["n60"]
            and abs(got["kish"] - e["kish"]) <= 0.05 + 1e-9
            and abs(got["dmean"] - e["dmean"]) <= TOL
            and abs(got["dmean_ci"][0] - e["dmean_ci"][0]) <= TOL
            and abs(got["dmean_ci"][1] - e["dmean_ci"][1]) <= TOL
            and abs(got["dstop"] - e["dstop"]) <= TOL
            and abs(got["dcover"] - e["dcover"]) <= TOL)


def regression_check(rows60, n_total) -> Dict:
    if len(rows60) < 5:
        return {"ok": False, "reason": "insufficient", "expected": REGRESSION_CUR}
    c = rv.cell_stats(rows60, "W1", 60, rv.BOOT_SEED + 60)
    got = {"n_total": n_total, "n60": c["n"], "kish": c["kish"], "dmean": c["dmean"],
           "dmean_ci": tuple(c["dmean_ci"]), "dstop": c["dstop"], "dcover": c["dcover"]}
    return {"ok": _regression_ok(got), "got": got, "expected": REGRESSION_CUR}


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
            out[name][T] = {"cand": vc.cell_stats(rows, CAND, BASE, T, BOOT_SEED + T),
                            "pure": vc.cell_stats(rows, PURE, PURE_BASE, T, BOOT_SEED + T + 100),
                            "clock_ref": vc.cell_stats(rows, "W4", BASE, T, BOOT_SEED + T + 200)}
    rows60 = [r for r in episodes if (BASE, 60) in r["pv"]]
    out["timing"] = vc.timing_stats(rows60, CAND, BASE) if rows60 else None
    out["timing_pure"] = vc.timing_stats(rows60, PURE, PURE_BASE) if rows60 else None
    out["regression"] = regression_check(rows60, len(episodes))
    return out


def verdict(summary: Dict) -> Dict:
    reg = summary.get("regression") or {}
    if not reg.get("ok"):
        return {"branch": "no_call", "reason": "回归不复现 round-1 当前池锚 → 环境可疑, 中止",
                "regression": reg}
    cells = (summary.get("full") or {}).get(DECISION_T) or {}
    if not cells or cells.get("insufficient"):
        return {"branch": "no_call", "reason": "数据不足"}
    c = cells["cand"]
    if c["kish"] < vc.KISH_MIN:
        return {"branch": "no_call", "reason": f"Kish {c['kish']} < {vc.KISH_MIN}"}
    flags = vc._flags(c)
    return {"branch": "V" if all(flags.values()) else "K", "flags": flags,
            "cell": {k: c[k] for k in ("n", "kish", "dmean", "dmean_ci", "dstop", "dstop_ci",
                                       "dcover", "dcover_ci", "lat_w", "lat_b")},
            "pure_flags": vc._flags(cells["pure"])}


def render(summary, drops, vd):
    L = [f"# volume_leg_align 量腿对齐日裁决 — round-2 (seed {BOOT_SEED})", ""]
    L.append(f"episodes {summary['n_total']} | drops: " + " / ".join(f"{k} {v}" for k, v in drops.items()))
    reg = summary.get("regression") or {}
    L += ["", f"## 回归复现前置 (W1−W0 T60 对 round-1 当前池锚): **{'PASS' if reg.get('ok') else 'FAIL'}**",
          "```json", json.dumps(reg, ensure_ascii=False, indent=1, default=str), "```"]
    hdr = ("| cmp | n | Kish | Δmean CI90 | Δ止损率 CI90 | Δp10 CI90(1k) | Δ覆盖率 CI90 "
           "| 覆盖 w/b | 止损率 w/b | 迟到 w/b |")
    sep = "|---|---|---|---|---|---|---|---|---|---|"
    for name, label in (("full", "全样本 (裁决)"), ("e_tag", "E±1 层 (报告)"), ("non_e", "非 E±1 层 (报告)")):
        for T in rv.TERMINALS:
            cells = (summary.get(name) or {}).get(T)
            if not cells:
                continue
            L += ["", f"## {label} T+{T}", hdr, sep]
            if cells.get("insufficient"):
                L.append(f"| 数据不足 n={cells['n']} |")
                continue
            L.append(vc._cell_line(CAND, cells["cand"]))
            L.append(vc._cell_line(PURE, cells["pure"]))
            L.append(vc._cell_line("W4(参照)", cells["clock_ref"]))
    for key, label in (("timing", f"{CAND} vs {BASE} 逐 episode 先后 (T60, 只报)"),
                       ("timing_pure", f"{PURE} vs {PURE_BASE} 逐 episode 先后 (T60, 只报)")):
        tm = summary.get(key)
        if tm:
            L += ["", f"## {label}",
                  f"- 双方触发 n={tm['n_both']}: 早 {vc._f((tm['earlier'] or 0) * 100)}% / 同 "
                  f"{vc._f((tm['same'] or 0) * 100)}% / 晚 {vc._f((tm['later'] or 0) * 100)}%; 均值 {vc._f(tm['mean_dq'], 2)} 交易日",
                  f"- 只有候选触发 {tm['only_w']} / 只有对照触发 {tm['only_base']} / 都未触发 {tm['neither']}"]
    L += ["", f"## 机械裁决: **{vd['branch']}**", "```json",
          json.dumps(vd, ensure_ascii=False, indent=1, default=str), "```", ""]
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--report", default="tmp/backtest/volume_leg_align_report.md")
    ap.add_argument("--kdir", default=str(rv.KDIR))
    ap.add_argument("--earn-dir", default=str(rv.EARN_DIR))
    a = ap.parse_args()
    episodes, outside, drops = collect(kdir=Path(a.kdir), earn_dir=Path(a.earn_dir))
    summary = summarize(episodes, outside)
    vd = verdict(summary)
    text = render(summary, drops, vd)
    out = Path(a.report)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    print(json.dumps({"drops": drops, "verdict": vd, "n": summary["n_total"]}, ensure_ascii=False, default=str)
          if a.json else text)


if __name__ == "__main__":
    main()
