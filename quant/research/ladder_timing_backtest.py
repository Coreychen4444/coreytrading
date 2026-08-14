#!/usr/bin/env python3
"""ladder 的起步日 —— 「早一天」单自由度 × 双构造。

预注册全文: private preregistration
**判据必须在运行前由私人预注册锁定；查看结果后不得修改本次协议。**

## 问题
live `fastlane_k=0.5` 的采纳对照是 **ladder@D1 vs 单点@D2**—— 执行结构与时点绑在
一起, 「早一天」从未被单独验过。本轮两臂**都是**ladder, 唯一差别是从哪天起步。

## 双构造 (同脚本内, 其余逐字相同)
  ANCHOR  破位锚: for i in episodes_of(...) → gate = cluster_gate(i, closes[i], ...)
          → D1 = 窗内首个 close > gate。**= ladder_entry 逐字同款, 主裁决**
  ROLL    滚动上穿: G(t) 逐日重画 → D1 = 首个 closes[j] > G(j−1), 取 gate_path=="repair"
          **复制臂 —— 只用来量构造效应**, 不单独裁决
内建自证: ANCHOR 每条记录跑 gate_path() 断言全部 = repair (§3e 恒等式的经验复核)。

## 两臂 (两构造共用) / 止损 / 终点 —— 全部沿用 gate_path_taxonomy 的机械件, 不另抄
FAST = D1 收盘起 ladder 50/30/20 ｜ SLOW = close[D2] > gate 才入场 → D2 收盘起同样 ladder
stop = gate − 0.5×ATR20@本臂确认日, 收盘 ×1 日, 滑点 0.2%; 风险N 分母统一 ATR20[D1];
终点 D1+60 裁决 / D1+20 并列; k 锁死 0.5 (首个 close>gate 那天不达幅度 → 丢弃 episode)。

## 量腿 (§3a): 主裁决格**关掉**
ANCHOR×off = 主裁决 (逐字复现 ladder_entry 的池 ⟹ 与它的差异只剩「SLOW 改 ladder」
+「加判据⑤」两件) ｜ ROLL×off = 复制臂 (两格只差构造) ｜ ANCHOR×on = 预注册敏感性。
⚠️ live 的 _fastlane 是要量腿的, 主裁决格的池比 live 宽 —— 量腿是两臂共享的过滤器,
不混淆时点比较, 只改变总体。

## 判据 (= gate_path_taxonomy 同一套六条, ①在两轮之间苹果对苹果)
六条全过 → fastlane_k=0.5 独立成立; 任一不过 → **建议撤下 _fastlane 回 ×2**
(这是对 incumbent 的检验；「未获支持」= 建议撤下。脚本不自动修改 production。)

Usage: .venv/moomoo/bin/python3 quant/research/ladder_timing_backtest.py [--json] [--report PATH]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import argparse  # noqa: E402
import json  # noqa: E402
from collections import Counter  # noqa: E402

from quant.core.episodes import breakdown_in_window, episodes_of, gate_path  # noqa: E402
from quant.core.paths import KLINE_DEEP_DIR as KDIR  # noqa: E402
from quant.research import gate_path_taxonomy_backtest as gpt  # noqa: E402  机械件单一真源
from quant.research.gate_backtest import (# noqa: E402
    confirmed_swing_highs, load, sma_series)
from quant.research.gate_backtest2 import (# noqa: E402
    WINDOW, atr_series, cluster_gate, confirmed_swing_lows)
from quant.research.relative_volume_backtest import (# noqa: E402
    ETF_EXCLUDE, VOL_RATIO, ratio_series, relvr_series)

# ── 全部沿用已锁定值; 本文件不新增任何可调参数 ──────────────────────
K_MAG = gpt.K_MAG                 # 0.5, ladder_entry 定版
BFLOOR_BUF = gpt.BFLOOR_BUF       # 0.5
DECISION_T = gpt.DECISION_T       # 60
REPORT_T = gpt.REPORT_T           # 20
MIN_TAIL = 50                     # ladder_entry `if end - i < 50: continue` 逐字同款
CELLS = ("ANCHOR", "ROLL", "ANCHOR_VOL")
CELL_CN = {"ANCHOR": "ANCHOR 破位锚 (主裁决)",
           "ROLL": "ROLL 滚动上穿 (复制臂)",
           "ANCHOR_VOL": "ANCHOR + 量腿 (敏感性)"}
ADJUDICATING = "ANCHOR"
REPLICATION = "ROLL"


# ── ANCHOR 构造 (ladder_entry.collect 的骨架 + 本轮两臂) ─────────────

def collect_anchor(kdir=KDIR, term_len=DECISION_T, require_volume=False):
    """破位锚 episode → 记录。`ladder_entry.collect` 的逐字骨架, 换成 FAST/SLOW 两臂。"""
    bench_maps = {}
    if require_volume:
        for b in ("QQQ", "SMH"):
            bars = load(kdir / f"{b}.jsonl")
            rs = ratio_series([x.get("volume") or 0 for x in bars])
            bench_maps[b] = {bars[j]["time"][:10]: rs[j]
                             for j in range(len(bars)) if rs[j] is not None}

    rows, drops, path_mix = [], Counter(), Counter()
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
        opens = [b.get("open") or b["close"] for b in bars]
        dates = [b["time"][:10] for b in bars]
        n = len(closes)

        ma60 = sma_series(closes, 60)
        a20 = atr_series(highs, lows, closes, 20)
        a14 = atr_series(highs, lows, closes, 14)
        sh, sl = confirmed_swing_highs(highs), confirmed_swing_lows(lows)
        eps_idx = episodes_of(closes, ma60)
        relvr = None
        if require_volume:
            from quant.core.sectors import benchmark_for
            relvr = relvr_series(ratio_series([b.get("volume") or 0 for b in bars]),
                                 dates, bench_maps[benchmark_for(t)])

        for i in eps_idx:
            end = min(i + WINDOW, n - 1)
            if end - i < MIN_TAIL:
                continue
            gate = cluster_gate(i, closes[i], sh, sl, a14[i])
            if gate is None:
                drops["no_gate"] += 1
                continue
            d1 = next((j for j in range(i + 1, end + 1) if closes[j] > gate), None)
            if d1 is None or a20[d1] is None or d1 + 1 > end:
                drops["no_d1"] += 1
                continue
            N = a20[d1]
            if closes[d1] <= gate + K_MAG * N:      # 幅度腿 (首个上穿日不达 → 丢弃)
                drops["magnitude"] += 1
                continue
            term = d1 + term_len
            if term > n - 1 or not a20[d1 + 1]:
                drops["no_terminal"] += 1
                continue
            # ladder_entry 同款守卫。⚠️ 它**结构上不可达**: D1 的定义就是 closes[d1] > gate,
            # 而 gate − 0.5N < gate。留着只为与 ladder_entry 逐行对应 (变异测试确认它
            # 拿掉不改变任何结果 = 等价变异, 不是测试缺口)。ladder_entry 那边同样是死的。
            if closes[d1] <= gate - BFLOOR_BUF * N:
                continue
            if require_volume:
                r = relvr[d1]
                if r is None:
                    drops["bench_gap"] += 1
                    continue
                if r < VOL_RATIO:
                    continue

            # 自证: 破位锚构造下每条记录都该是①修复 (§3e 恒等式)
            broke, n_ep, n_below = breakdown_in_window(eps_idx, closes, d1, gate)
            trend = gpt.trend_before(sh, sl, d1)
            ath = max(closes[:d1])
            cls = gate_path(broke, trend, gate, ath, gpt.DELTA_ATR * N)
            path_mix[cls] += 1

            worlds = gpt.build_worlds(d1, gate, closes, lows, opens, a20, term)
            rec = {"ticker": t, "date": dates[d1], "month": dates[d1][:7],
                   "year": int(dates[d1][:4]), "cls": cls, "trend": trend,
                   "gate_over_ath": gate / ath, "over_n": (closes[d1] - gate) / N,
                   "win_eps": n_ep, "win_eps_below": n_below,
                   "flat_days": gpt._flat_days(closes, d1, gate),
                   "slow_entered": bool(worlds["SLOW"][0])}
            for a in gpt.ARMS:
                legs, stop = worlds[a]
                rec[a] = gpt.measure(legs, stop, N, term, closes)
            rec["fast_tranche2"] = len(worlds["FAST"][0]) >= 2
            rec["fast_tranche3"] = len(worlds["FAST"][0]) >= 3
            rows.append(rec)
    return rows, dict(drops), dict(path_mix)


def collect_roll(kdir=KDIR, term_len=DECISION_T, require_volume=False):
    """复制臂: 滚动上穿构造, 只取 gate_path=="repair" 的那一类。"""
    rows, drops = gpt.collect(kdir, term_len, require_volume=require_volume)
    return [r for r in rows if r["cls"] == "repair"], drops


CELL_FN = {
    "ANCHOR": lambda k, t: collect_anchor(k, t, require_volume=False),
    "ANCHOR_VOL": lambda k, t: collect_anchor(k, t, require_volume=True),
    "ROLL": lambda k, t: (collect_roll(k, t, require_volume=False) + ({},))[:3],
}


# ── 裁决 ────────────────────────────────────────────────────────────

def verdict_of(res):
    """判据机械读数 → 本格结论。incumbent 检验: 未获支持 = 建议撤下。"""
    if not res or not res.get("n"):
        return {"decision": "no_data", "text": "无样本"}
    if not res["crit"]["c6_kish"]:
        return {"decision": "insufficient",
                "text": f"**数据不足不裁决**(Kish {res['kish']} < {gpt.KISH_MIN})"}
    if res["crit"]["all_pass"]:
        return {"decision": "support",
                "text": "**六判据全过 → 「早一天」独立成立, `fastlane_k=0.5` 留在 live 有据**"}
    miss = [k for k, v in res["crit"].items() if not v and k != "all_pass"]
    return {"decision": "reject",
            "text": "**未获支持 → 建议撤下 `_fastlane` 回 ×2**(未过: "
                    + ", ".join(miss) + ") — 不自动修改 production"}


def trigger_234(main_v, repl_v):
    """§5 跑前锁定的 ②③④ 重跑触发。看完①再定就是"跑完再挑"。"""
    both = {main_v["decision"], repl_v["decision"]}
    if main_v["decision"] in ("no_data", "insufficient") or \
            repl_v["decision"] in ("no_data", "insufficient"):
        return ("undetermined",
                "两格中有一格数据不足 → 构造效应未测出, ②③④ **维持但标注未复验**")
    if len(both) == 1:
        return ("hold",
                "ANCHOR 与 ROLL 裁决**一致**→ 未观察到构造方向分叉；"
                "`gate_path_taxonomy` 的 ②③④ **维持**")
    return ("downgrade",
            "ANCHOR 与 ROLL 裁决**相反**→ ②③④ 降级为「**构造敏感, 不作结论**」; "
            "且**无法在 ANCHOR 上复验**(破位锚构造产不出②③④, 结构性限制)")


def reproduction_check(res):
    """Return current ANCHOR FAST metrics without embedding a private baseline."""
    if not res or not res.get("n"):
        return None
    f = res["FAST"]
    return {"anchor_FAST": {"deploy": f["deploy"], "riskN": f["riskN_median"],
                            "stop": f["stop_rate"], "R": f["R_winsor"]}}


# ── 渲染 ────────────────────────────────────────────────────────────

def render(by_cell, by_cell20, verds, trig, path_mix, drops, repro):
    L = ["=== ladder 的起步日: 「早一天」单自由度 × 双构造 ===",
         f"两臂都是 ladder 50/30/20 / k={K_MAG} / stop=gate−{BFLOOR_BUF}N@确认日 / "
         f"限价 {gpt.LIMIT_DAYS}d / 裁决终点 D1+{DECISION_T}", ""]

    mix = path_mix.get("ANCHOR") or {}
    ok = set(mix) <= {"repair"}
    L.append(f"【自证】ANCHOR 构造的结构身份分布: {mix} → "
             + ("✅ 全部 = ①修复, §3e 恒等式经验成立" if ok
                else "🚨 **出现非 repair —— 恒等式推导有误, 停下来查**"))
    if repro:
        a = repro["anchor_FAST"]
        L.append(f"【审计】ANCHOR FAST: 部署 {a['deploy']:.0%} · "
                 f"风险N {a['riskN']} · 止损 {a['stop']:.0%} · R {a['R']:+.3f}")
    L.append("")

    for cell in CELLS:
        r = by_cell.get(cell)
        if not r or not r.get("n"):
            L.append(f"--- {CELL_CN[cell]} --- 无样本")
            L.append("")
            continue
        L.append(f"--- {CELL_CN[cell]} --- n={r['n']}  Kish≈{r['kish']}  月={r['months']}  "
                 f"SLOW 入场率 {r['slow_entry_rate']:.0%}  "
                 f"超出 {r['over_n_median']:.2f}N  era {r['era']}")
        L.append(f"{'臂':<8}{'部署率':>8}{'风险N':>8}{'止损率':>8}"
                 f"{'收益均值':>10}{'收益中位':>10}{'R winsor':>10}{'R 中位':>9}")
        for a in gpt.ARMS:
            d = r[a]
            rk = f"{d['riskN_median']:.2f}N" if d["riskN_median"] is not None else "—"
            L.append(f"{a:<8}{d['deploy']:>8.0%}{rk:>8}{d['stop_rate']:>8.0%}"
                     f"{d['ret_mean'] * 100:>9.1f}%{d['ret_median'] * 100:>9.1f}%"
                     f"{d['R_winsor']:>10.3f}{d['R_median']:>9.3f}")
        cr = r["crit"]
        m = lambda k: "✓" if cr.get(k) else "✗"
        L.append(f"  ①风险N(F−S) {r['riskN_diff_point']:+.3f}N CI90 "
                 f"{gpt._ci(r['riskN_diff_ci90'])} (配对 n={r['riskN_diff_n']}) "
                 f"→ {m('c1_riskN_down')}")
        L.append(f"  ②R(F−S) {r['R_diff_point']:+.3f} CI90 {gpt._ci(r['R_diff_ci90'])}"
                 f" → {m('c2_R_up')}")
        L.append(f"  ③部署率 {r['FAST']['deploy']:.0%} ≥ {gpt.DEPLOY_MIN:.0%} {m('c3_deploy')}"
                 f"   ④FAST 绝对收益 CI90 {gpt._ci(r['fast_ret_ci90'])} {m('c4_abs_exp')}")
        L.append(f"  ⑤止损率差 {r['stop_diff_point'] * 100:+.2f}pp CI90 "
                 f"{gpt._ci(r['stop_diff_ci90'])} < +{gpt.STOP_GUARD * 100:.0f}pp "
                 f"{m('c5_stop_guard')}   ⑥Kish {m('c6_kish')}   → "
                 f"{'PASS' if cr.get('all_pass') else 'FAIL'}")
        L.append(f"  → {verds[cell]['text']}")
        L.append("")

    if by_cell20:
        L.append(f"D1+{REPORT_T} 并列 (不参与裁决):")
        for cell in CELLS:
            r = by_cell20.get(cell)
            if r and r.get("n"):
                L.append(f"  {cell:<11} n={r['n']:<5} 风险N F {r['FAST']['riskN_median']} / "
                         f"S {r['SLOW']['riskN_median']}  "
                         f"R F {r['FAST']['R_winsor']:+.3f} / S {r['SLOW']['R_winsor']:+.3f}"
                         f"  ②CI90 {gpt._ci(r['R_diff_ci90'])}"
                         f"  ⑤{r['stop_diff_point'] * 100:+.2f}pp")
        L.append("")
    L.append(f"drops={drops}")
    L.append("")
    L.append("VERDICT (主裁决 = ANCHOR):")
    L.append(f"  {verds[ADJUDICATING]['text']}")
    L.append(f"§5 ②③④ 触发 [{trig[0]}]: {trig[1]}")
    L.append("边缘结果必须标为 inconclusive，并由 private preregistration 定义重跑条件。")
    return "\n".join(L)


def main_cli(argv=None):
    ap = argparse.ArgumentParser(description="ladder 的起步日 × 双构造")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--report", type=Path, default=None)
    ap.add_argument("--limit", type=int, default=None)
    a = ap.parse_args(argv)

    kdir = KDIR
    if a.limit:
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        keep = sorted(kdir.glob("*.jsonl"))[:a.limit]
        keep += [kdir / f"{b}.jsonl" for b in ("QQQ", "SMH")]
        for p in dict.fromkeys(keep):
            (tmp / p.name).symlink_to(p)
        kdir = tmp

    by_cell, by_cell20, path_mix, drops = {}, {}, {}, {}
    for cell in CELLS:
        rows, dr, mix = CELL_FN[cell](kdir, DECISION_T)
        by_cell[cell] = gpt.analyse(rows, cell)
        path_mix[cell], drops[cell] = mix, dr
        rows20, _, _ = CELL_FN[cell](kdir, REPORT_T)
        by_cell20[cell] = gpt.analyse(rows20, cell, REPORT_T)

    verds = {c: verdict_of(by_cell[c]) for c in CELLS}
    trig = trigger_234(verds[ADJUDICATING], verds[REPLICATION])
    repro = reproduction_check(by_cell[ADJUDICATING])
    text = render(by_cell, by_cell20, verds, trig, path_mix, drops, repro)
    if a.json:
        print(json.dumps({"decision": by_cell, "report_T20": by_cell20,
                          "verdict": verds, "trigger_234": trig,
                          "path_mix": path_mix, "drops": drops,
                          "reproduction": repro}, ensure_ascii=False,
                         indent=1, default=float))
    else:
        print(text)
    if a.report:
        a.report.parent.mkdir(parents=True, exist_ok=True)
        a.report.write_text("# ladder 的起步日 × 双构造 (机器输出存档)\n\n```\n"
                            + text + "\n```\n")
    return 0


if __name__ == "__main__":
    sys.exit(main_cli())
