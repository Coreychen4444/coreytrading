#!/usr/bin/env python3
"""Round-2b — 修复路径止损语义扫描 (预注册: gate_backtest2_design.md §Round-2b)。

背景: round-2 裁决落 D1 分支 3 (gate 授权只留修复路径), 顺带发现 live 止损语义
(stop = 刚收复的 gate, ×1 日) 是全场最差变体。本轮在**保留 gate 的前提下**扫描
止损语义, 选择规则运行前锁定 (见 design doc):

  替换 incumbent (bfloor×1) 当且仅当: expectancy 配对差 CI90 下界 > 0
  且 p10 配对差 CI90 下界 > −1pp。多合格取 expectancy 点估计最高; 无合格 → live 维持。
  single-shot 与 replay 矛盾 → 以 replay 为准 (修复路径真实行为)。

设计要点:
- 人群 = 该入场格 fired 的 episodes; 同格所有止损变体共享同一入场日 → 纯配对
- 主入场格 = {cluster, swing} × 2日 + 量比1.2 (live 标准口径); ma60×2vol 与 ×1日 = 附表
- 变体: bfloor×1 / bfloor×2 / bfloor−0.5N×1 / 2N×1 / structure×1 (口径同 round-2:
  收盘触发、次日收盘出场、滑点 0.2%、终点日触发悬置)
- replay: 止损后等下一次同规则 fire 再进, ≤3 段; 每段 stop 按该段入场时点重算
- CI: 按月 bootstrap (round-2 同机器); p10 配对差 1,000 draws
- 复用 round-2 已审查引擎 (同层 import), 新机械件仅 stop_scan_ndays + 变体 stop 定义

Usage: .venv/moomoo/bin/python3 quant/research/gate_backtest2b.py [--json] [--report PATH]
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 直接 `python3 quant/<层>/<模块>.py` 时让 `quant.*` 可 import

import json
import statistics as st
from collections import defaultdict

from quant.core.paths import KLINE_DEEP_DIR as KDIR  # noqa: E402
from quant.research.gate_backtest import (# noqa: E402
    load, sma_series, episodes_of, confirmed_swing_highs,
)
from quant.research.gate_backtest2 import (# noqa: E402  同层复用已审查引擎
    WINDOW, SLIP, STOP_MULT, BOOT_SEED, CI_Q,
    atr_series, confirmed_swing_lows, last_swing_high_array, cluster_gate,
    fire_scan, nearest_swing_low_below, policy_value, whipsaw_cost, stopped_by,
    _pct_row,
)

TERMINAL = (60, 120)
BUFFER_N = 0.5                 # bfloor−0.5N 变体的缓冲 (×ATR20@入场)
MAX_SEGMENTS = 3               # replay: 1 初始 + ≤2 重进
BOOT_DRAWS = 10_000
P10_DRAWS = 1_000

ENTRY_CELLS = (# (kind, need, vol_on, main?)
    ("cluster", 2, True, True),
    ("swing", 2, True, True),
    ("ma60", 2, True, False),
    ("cluster", 1, True, False),
    ("swing", 1, True, False),
)
VARIANTS = ("bfloor_x1", "bfloor_x2", "bfloor_buf", "two_n", "structure")
INCUMBENT = "bfloor_x1"
# 平手裁决的"语义更简单"序 (预注册于此, 运行前): 2N 自包含无 gate 依赖 < bfloor_x2
# (live 级别只加一天) < bfloor_buf (加一个缓冲参数) < structure (加 swing-low 数据依赖)
SIMPLICITY = {"two_n": 0, "bfloor_x2": 1, "bfloor_buf": 2, "structure": 3}


def stop_scan_ndays(entry_j, end, closes, stop_lvl, need):
    """收盘 < stop 连续 need 日 → 次日收盘出场。悬置口径同 round-2 stop_scan。

    streak 在 close ≥ stop 时清零 (对称于 fire_scan 的收复计数)。
    """
    if stop_lvl is None:
        return None
    streak = 0
    for m in range(entry_j + 1, end + 1):
        streak = streak + 1 if closes[m] < stop_lvl else 0
        if streak >= need:
            if m == end:
                return None            # 终点日确认完成, 无次日可成交 → 悬置
            return m, m + 1, closes[m + 1]
    return None


def variant_stop(variant, entry_j, entry_px, gate_lvl, atr20, swings_l):
    """→ (stop_lvl, confirm_days)。gate_lvl = 该段 fire 时的 level。"""
    two_n = entry_px - STOP_MULT * atr20[entry_j] if atr20[entry_j] else None
    if variant == "bfloor_x1":
        return (gate_lvl if gate_lvl is not None else two_n), 1
    if variant == "bfloor_x2":
        return (gate_lvl if gate_lvl is not None else two_n), 2
    if variant == "bfloor_buf":
        if gate_lvl is not None and atr20[entry_j]:
            return gate_lvl - BUFFER_N * atr20[entry_j], 1
        return two_n, 1
    if variant == "two_n":
        return two_n, 1
    if variant == "structure":
        sl = nearest_swing_low_below(entry_j, entry_px, swings_l)
        return (sl if sl is not None else two_n), 1
    raise ValueError(variant)


def simulate_variant(i, end, closes, level_fn, need, vols, von, variant,
                     atr20, swings_l, max_segments=1):
    """B 世界段列表, 止损语义按 variant。形状同 round-2 segments。"""
    segs, cursor = [], i
    while len(segs) < max_segments:
        fired = fire_scan(cursor, end, closes, level_fn, need, vols, von)
        if not fired:
            break
        j, lvl = fired
        px = closes[j]
        stop, sdays = variant_stop(variant, j, px, lvl, atr20, swings_l)
        hit = stop_scan_ndays(j, end, closes, stop, sdays)
        if hit:
            _, x, xp = hit
            segs.append((j, px, x, xp, stop))
            cursor = x
            if x >= end:
                break
        else:
            segs.append((j, px, None, None, stop))
            break
    return segs


def collect(kdir=KDIR):
    out = []
    for path in sorted(kdir.glob("*.jsonl")):
        bars = load(path)
        if len(bars) < 300:
            continue
        closes = [b["close"] for b in bars]
        highs = [b["high"] for b in bars]
        lows = [b["low"] for b in bars]
        vols = [b.get("volume") or 0 for b in bars]
        ma60 = sma_series(closes, 60)
        atr20 = atr_series(highs, lows, closes, 20)
        atr14 = atr_series(highs, lows, closes, 14)
        swings_h = confirmed_swing_highs(highs)
        swings_l = confirmed_swing_lows(lows)
        swing_arr = last_swing_high_array(len(closes), swings_h)

        for i in episodes_of(closes, ma60):
            end = min(i + WINDOW, len(closes) - 1)
            if end - i < 50:
                continue
            terminals = {T: i + T for T in TERMINAL if i + T <= end}
            if not terminals:
                continue
            c_gate = cluster_gate(i, closes[i], swings_h, swings_l, atr14[i])
            level_fns = {"swing": (lambda j, _a=swing_arr: _a[j]),
                         "ma60": (lambda j, _a=ma60: _a[j]),
                         "cluster": (lambda j, _g=c_gate: _g)}
            rec = {"month": bars[i]["time"][:7], "terminals": terminals,
                   "closes_ref": closes, "cells": {}}
            for kind, need, von, main in ENTRY_CELLS:
                level_fn = level_fns[kind]
                fired = fire_scan(i, end, closes, level_fn, need, vols, von)
                if not fired:
                    rec["cells"][(kind, need, von)] = None    # 配对人群外
                    continue
                cell = {}
                for v in VARIANTS:
                    cell[v] = {
                        "single": simulate_variant(i, end, closes, level_fn, need,
                                                   vols, von, v, atr20, swings_l, 1),
                        "replay": simulate_variant(i, end, closes, level_fn, need,
                                                   vols, von, v, atr20, swings_l,
                                                   MAX_SEGMENTS),
                    }
                rec["cells"][(kind, need, von)] = cell
            out.append(rec)
    return out


def summarize(episodes):
    out = {"n_episodes": len(episodes), "cells": {}}
    for kind, need, von, main in ENTRY_CELLS:
        ck = (kind, need, von)
        key = f"{kind}_x{need}{'_vol' if von else ''}"
        fired_eps = [e for e in episodes if e["cells"].get(ck)]
        cell_out = {"main": main, "n_fired": len(fired_eps)}
        for mode in ("single", "replay"):
            for T in TERMINAL:
                elig = [e for e in fired_eps if T in e["terminals"]]
                if not elig:
                    continue
                rows = {}
                inc_vals = {}
                for e in elig:
                    k = e["terminals"][T]
                    inc_vals[id(e)] = policy_value(e["cells"][ck][INCUMBENT][mode],
                                                   k, e["closes_ref"])
                for v in VARIANTS:
                    vals, by_month = [], defaultdict(lambda: [0.0, 0, 0])
                    vals_bm = defaultdict(lambda: ([], []))
                    stop_n, whip = 0, 0.0
                    for e in elig:
                        k = e["terminals"][T]
                        segs = e["cells"][ck][v][mode]
                        val = policy_value(segs, k, e["closes_ref"])
                        vals.append(val)
                        stop_n += int(stopped_by(segs, k))
                        whip += whipsaw_cost(segs, k)
                        inc = inc_vals[id(e)]
                        agg = by_month[e["month"]]
                        agg[0] += val - inc
                        agg[1] += 1
                        agg[2] += int(stopped_by(segs, k)) - int(
                            stopped_by(e["cells"][ck][INCUMBENT][mode], k))
                        vb, va = vals_bm[e["month"]]
                        vb.append(val)
                        va.append(inc)
                    row = _pct_row(vals)
                    row["n"] = len(vals)
                    # 稀释可见性 (审查修正): T 前已入场的占比 — fire 晚于 T 的 episode
                    # 对全部变体贡献相同的 0-policy, 把配对差往 null 方向稀释 (偏 incumbent 保守)
                    row["n_entered_by_T"] = sum(
                        1 for e in elig
                        if e["cells"][ck][v][mode]
                        and e["cells"][ck][v][mode][0][0] <= e["terminals"][T])
                    row["stop_rate"] = stop_n / len(vals)
                    row["whipsaw"] = whip / len(vals)
                    if v != INCUMBENT:
                        row["vs_inc_mean"] = (sum(vals) / len(vals)
                                              - sum(inc_vals[id(e)] for e in elig) / len(elig))
                        row["_by_month"] = dict(by_month)
                        row["_vals_bm"] = dict(vals_bm)
                    rows[v] = row
                cell_out[f"{mode}_T{T}"] = rows
        out["cells"][key] = cell_out
    return out


def _p10_idx(sorted_vals):
    n = len(sorted_vals)
    return sorted_vals[max(0, int(n * 0.10) - 1)]


def bootstrap(out, draws=BOOT_DRAWS, p10_draws=P10_DRAWS, seed=BOOT_SEED + 2):
    """候选 vs incumbent: 配对 expectancy 差 CI + 止损率差 CI + p10 差 CI (按月)。"""
    try:
        import numpy as np
        use_np = True
    except ImportError:
        import random
        use_np = False
        draws, p10_draws = min(draws, 2000), min(p10_draws, 300)

    for key, cell in out["cells"].items():
        for slot, rows in list(cell.items()):
            if not isinstance(rows, dict) or INCUMBENT not in rows:
                continue
            for v, row in rows.items():
                if "_by_month" not in row:
                    continue
                months = sorted(row["_by_month"])
                M = len(months)
                bm = row["_by_month"]
                sums = [bm[m][0] for m in months]
                cnts = [bm[m][1] for m in months]
                stps = [bm[m][2] for m in months]
                if use_np:
                    import numpy as np
                    rng = np.random.default_rng(seed)
                    idx = rng.integers(0, M, size=(draws, M))
                    s = np.asarray(sums)[idx].sum(axis=1)
                    c = np.asarray(cnts, dtype=float)[idx].sum(axis=1)
                    t = np.asarray(stps, dtype=float)[idx].sum(axis=1)
                    mask = c > 0
                    dm = s[mask] / c[mask]
                    sm = t[mask] / c[mask]
                    row["vs_inc_ci90"] = [round(float(np.quantile(dm, CI_Q[0])), 4),
                                          round(float(np.quantile(dm, CI_Q[1])), 4)]
                    row["stop_diff_ci90"] = [round(float(np.quantile(sm, CI_Q[0])), 4),
                                             round(float(np.quantile(sm, CI_Q[1])), 4)]
                else:
                    import random
                    rng = random.Random(seed)
                    dms, sms = [], []
                    for _ in range(draws):
                        pick = [rng.randrange(M) for _ in range(M)]
                        c = sum(cnts[x] for x in pick)
                        if c:
                            dms.append(sum(sums[x] for x in pick) / c)
                            sms.append(sum(stps[x] for x in pick) / c)
                    dms.sort()
                    sms.sort()
                    row["vs_inc_ci90"] = [round(dms[int(len(dms) * CI_Q[0])], 4),
                                          round(dms[min(len(dms) - 1, int(len(dms) * CI_Q[1]))], 4)]
                    row["stop_diff_ci90"] = [round(sms[int(len(sms) * CI_Q[0])], 4),
                                             round(sms[min(len(sms) - 1, int(len(sms) * CI_Q[1]))], 4)]
                # p10 配对差 (candidate p10 − incumbent p10)
                vbm = row["_vals_bm"]
                b_all = sorted(x for mo in months for x in vbm[mo][0])
                a_all = sorted(x for mo in months for x in vbm[mo][1])
                row["p10_diff_point"] = round(float(_p10_idx(b_all) - _p10_idx(a_all)), 4)
                pv = []
                if use_np:
                    import numpy as np
                    rng2 = np.random.default_rng(seed + 1)
                    b_arr = {mo: np.asarray(vbm[mo][0]) for mo in months}
                    a_arr = {mo: np.asarray(vbm[mo][1]) for mo in months}
                    for _ in range(p10_draws):
                        pick = rng2.integers(0, M, size=M)
                        b = np.sort(np.concatenate([b_arr[months[x]] for x in pick]))
                        a = np.sort(np.concatenate([a_arr[months[x]] for x in pick]))
                        pv.append(float(_p10_idx(list(b)) - _p10_idx(list(a))))
                else:
                    import random
                    rng2 = random.Random(seed + 1)
                    for _ in range(p10_draws):
                        pick = [rng2.randrange(M) for _ in range(M)]
                        b = sorted(x for t2 in pick for x in vbm[months[t2]][0])
                        a = sorted(x for t2 in pick for x in vbm[months[t2]][1])
                        pv.append(_p10_idx(b) - _p10_idx(a))
                pv.sort()
                row["p10_diff_ci90"] = [
                    round(float(pv[int(len(pv) * CI_Q[0])]), 4),
                    round(float(pv[min(len(pv) - 1, int(len(pv) * CI_Q[1]))]), 4)]
    out["bootstrap"] = {"draws": draws, "p10_draws": p10_draws, "seed": seed,
                        "backend": "numpy" if use_np else "python"}


def strip_private(out):
    for cell in out["cells"].values():
        for rows in cell.values():
            if isinstance(rows, dict):
                for row in rows.values():
                    if isinstance(row, dict):
                        row.pop("_by_month", None)
                        row.pop("_vals_bm", None)
    return out


def selection(out):
    """预注册选择规则的机械执行 (design doc §Round-2b, 锁定于运行前)。

    合格: vs_inc_ci90 下界 > 0 且 p10_diff_ci90 下界 > −0.01。
    以 replay_T60 为主读数 (修复路径真实行为); single_T60 矛盾则记录。
    只在 main 入场格上裁决。
    """
    verdicts = {}
    for key, cell in out["cells"].items():
        if not cell.get("main"):
            continue
        res = {"qualify_replay": [], "qualify_single": [], "winner": None}
        for slot, qlist in (("replay_T60", "qualify_replay"), ("single_T60", "qualify_single")):
            rows = cell.get(slot) or {}
            for v, row in rows.items():
                ci = row.get("vs_inc_ci90")
                pci = row.get("p10_diff_ci90")
                if ci and pci and ci[0] > 0 and pci[0] > -0.01:
                    res[qlist].append({"variant": v, "vs_inc_ci90": ci,
                                       "p10_diff_ci90": pci,
                                       "vs_inc_mean": round(row["vs_inc_mean"], 4)})
        if res["qualify_replay"]:
            res["winner"] = max(res["qualify_replay"],
                                key=lambda r: (r["vs_inc_mean"],
                                               -SIMPLICITY.get(r["variant"], 9)))
        # 矛盾 = 两口径合格集合不同 (审查修正: 原实现只比空/非空, 漏掉成员不同的矛盾)
        res["modes_contradict"] = ({q["variant"] for q in res["qualify_replay"]}
                                   != {q["variant"] for q in res["qualify_single"]})
        verdicts[key] = res
    out["selection"] = verdicts
    return verdicts


def _f(x, w=8):
    return f"{x * 100:+.1f}%".rjust(w) if x is not None else "—".rjust(w)


def render(out):
    L = [f"=== round-2b 修复路径止损语义 === fired 配对人群, incumbent = {INCUMBENT}",
         f"bootstrap: {out['bootstrap']}", ""]
    for key, cell in out["cells"].items():
        tag = "主格" if cell.get("main") else "附表"
        L.append(f"—— {key} [{tag}] (fired n={cell['n_fired']}) ——")
        for slot in ("replay_T60", "single_T60", "replay_T120"):
            rows = cell.get(slot)
            if not rows:
                continue
            L.append(f"  [{slot}]  {'variant':<12}{'mean':>8}{'p10':>8}{'止损率':>7}"
                     f"{'whip':>8}{'vsInc':>8}{'CI90':>18}{'p10Δ CI':>18}")
            for v in VARIANTS:
                r = rows.get(v)
                if not r:
                    continue
                ci = r.get("vs_inc_ci90")
                pci = r.get("p10_diff_ci90")
                ci_s = f"[{ci[0]*100:+.1f},{ci[1]*100:+.1f}]" if ci else "—"
                pci_s = f"[{pci[0]*100:+.1f},{pci[1]*100:+.1f}]" if pci else "—"
                L.append(f"    {v:<12}{_f(r['mean'])}{_f(r['p10'])}"
                         f"{r['stop_rate']:>7.0%}{_f(r['whipsaw'])}"
                         f"{_f(r.get('vs_inc_mean'))}{ci_s:>18}{pci_s:>18}")
        sel = (out.get("selection") or {}).get(key)
        if sel:
            w = sel["winner"]
            L.append(f"  → 选择规则: winner = {w['variant'] if w else 'None (live 语义维持)'}"
                     f"  qualify_replay={[q['variant'] for q in sel['qualify_replay']]}"
                     f"  矛盾={sel['modes_contradict']}")
        L.append("")
    return "\n".join(L)


def main():
    episodes = collect()
    out = summarize(episodes)
    bootstrap(out)
    selection(out)
    strip_private(out)
    if "--json" in sys.argv:
        print(json.dumps(out, ensure_ascii=False, indent=1))
    else:
        print(render(out))
    if "--report" in sys.argv:
        rp = Path(sys.argv[sys.argv.index("--report") + 1])
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text("# gate round-2b 修复路径止损语义 (机器输出存档)\n\n```\n"
                      + render(out) + "\n```\n\n<details><summary>full json</summary>\n\n```json\n"
                      + json.dumps(out, ensure_ascii=False, indent=1)
                      + "\n```\n</details>\n", encoding="utf-8")
        print(f"report → {rp}")
    return out


if __name__ == "__main__":
    main()
