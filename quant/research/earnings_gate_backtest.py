"""earnings_gate_backtest — 财报前入场 clamp 按 implied move 分级是否有依据。

运行前必须锁定私人预注册；公开脚本不保存批准记录或研究结果。
私人数据按锁定协议运行；本 docstring 仅保留通用机械摘要。

── 预注册要点 (运行前锁定) ──────────────────────────────────────────
对赌: H-proposal (趋势成立 + 低 implied move → pre-print 建仓无显著惩罚 → 解除 clamp)
vs H-System (高 implied move → 左尾显著肥, gap 穿越 stop 机器 → clamp 保留)。
天数轴已由 fiat 裁决 (D4, : clamp 窗口 = print − 7 自然日), 本回测只裁 IM 轴。

Episode:
- 事件日 E: pub_time_str ≥16:00 = AMC → E = pub 次交易日; <09:30 = BMO → E = pub 当日;
  盘中罕见 → 当日, 标记 intraday
- 资格: regime_of(bars, E-1) ∈ {parabolic, strong_up} (entry_ladder 代码复刻, 无新自由度);
  predict_vola_ratio_newest 非空 (≈ 起)
- World PRE:  E-1 收盘买入, 承担 print → 共同终点 E+20 / E+60 收盘
- World POST: E-1→E+1 持现金, E+1 收盘买入 → 共同终点 (policy, 共同起点 E-1, 1 元本金)
- 副轴 (只报告): PRE5 = E-5 收盘买入
- 止损: 对称 entry − 2×ATR20@入场, 收盘 ×1 触发, 次日收盘出 SLIP 0.2%, single-shot;
  gap-through: print 跳空穿 stop → 次日收盘出场, 不按 stop 价成交 (被测机制, gap 不救);
  终点日触发 = 悬置 (终点收盘 mark, 计入止损率)
- 同票 cooldown 40 交易日; 右截断: 终点超数据末端 → 该终点剔除

分桶 (锁定, 不移边界): IM 三桶 <5% / 5-10% / >10% × regime 两桶 = 6 报告格;
D1 裁决按 IM 桶独立 (桶内 regime 合并), T60 主口径:
- 分支 A (解除): expectancy(PRE−POST) CI90 下界 ≥ 0 且 p10 差 CI90 下界 > −1pp
- 分支 B (保留-保险): expectancy CI 含 0 且 p10 差 CI90 上界 < 0
- 分支 C (保留-均值也输): expectancy CI90 上界 < 0
- 其余 → 维持现行 (证据不足不改规则)
D2: 本回测测单位暴露差, size 政策在结论层折算。D3: 只作用于卡片财报 constraint, 宏观事件域外。

统计: 按月 (E 月) block bootstrap CI90, 使用固定中性 seed 与 10,000 draws (p10 差 seed+1,
1,000 draws); Kish 按月 (财报集中季报月, 聚类重); 任一 D1 桶 Kish < 30 → 数据不足不裁决。
Cohort 仅按数据中存在的年份分组报告点估计。

Survivorship 声明: 存续票池 → 财报暴雷退市者缺席 → 偏向低估 pre-print 风险
(对 H-proposal 有利); 若仍测出左尾肥, 结论更稳。

不做什么: 不加桶 / 不移边界 / 不挑 E 窗口 / 不看中间数字 / PRE5 与 cohort 不裁决 /
无 IM 期不进主样本。数据陷阱: 端点有缺期 → 按 pub 日期去重, 不假设期数连续。

── 验收修正 (对抗性审查 , 全部在真数据运行之前; 非结果驱动,
   任何修订必须生成新的私人协议版本）──────────────────
#1 (blocker) E 日判定改为 vendor day-0 (schedule_delta==0 的 trading_day_str) 优先,
   退 provider contract 中的 pub_type 映射, 再退干净时间戳; 全不可判 → 剔除计数不猜。
   原因: pub_time_str 可能出现系统性 '00:00:00' 哨兵，
   纯时间 heuristic 把 AMC 错判 BMO → E 错一天。语义不变 (E 仍 = print 反应日)。
#2 3N 止损敏感性只报告不裁决
#3 D1 桶的 n/Kish/bootstrap 全部收窄到 t60 承载子样本 (右截断条款的正确执行)
#4 每主格报告 ΔT60/ΔT20 CI90（同 seed 零自由度）
#5 (pin) 分支 A 与 B 同真时 A 优先 — −1pp materiality 覆盖显著性; Kish 闸门定义在
   D1 桶级 (kish_t60), 薄格以 thin_cells 标记可见不整桶 abort
#6 BMO 期 newest-IM 时点 caveat 上报 + session/E 日来源构成进报告
──────────────────────────────────────────────────────────────────

用法: python3 quant/research/earnings_gate_backtest.py [--report tmp/backtest/earnings_gate_report.md]
"""
from __future__ import annotations

import json
import sys
from bisect import bisect_left, bisect_right
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from quant.core.paths import KLINE_DEEP_DIR, MARKET_DIR  # noqa: E402
from quant.research.entry_ladder_backtest import regime_of  # noqa: E402
from quant.research.gate_backtest2 import atr_series  # noqa: E402
from quant.research.escape_velocity_backtest import (# noqa: E402
    _boot_metric, _mean_diff_at, _p10, kish, load_deep, world_value,
)

_mean_diff_t20 = _mean_diff_at("t20")


def _count_by(rows, key):
    out: dict = {}
    for e in rows:
        out[e.get(key)] = out.get(e.get(key), 0) + 1
    return out

EARNINGS_DEEP_DIR = MARKET_DIR / "earnings_deep"
SEED = 314_159
DRAWS = 10_000
P10_DRAWS = 1_000
WARMUP = 126
COOLDOWN = 40
MIN_BARS = 300
KISH_MIN = 30
IM_BUCKETS = ("im<5", "im5-10", "im>10")
REGIMES = ("parabolic", "strong_up")


def im_bucket(im: float) -> str:
    if im < 5:
        return "im<5"
    if im <= 10:
        return "im5-10"
    return "im>10"


def periods_of(path: Path):
    """按 pub 日期去重的期列表。验收修正 #1 (blocker): 同时抽取 vendor day-0
    (schedule_delta==0 行的 trading_day_str) 作为反应日首选真源 — pub_time_str 存在
    系统性 '00:00:00' 哨兵值，纯时间 heuristic 会把 AMC 错判 BMO。"""
    doc = json.loads(path.read_text())
    per: dict = {}
    for r in doc.get("data") or []:
        pub = r.get("pub_trading_day_str")
        if not pub:
            continue
        d = per.setdefault(pub, {
            "pub": pub,
            "pub_time": r.get("pub_time_str") or "",
            "pub_type": r.get("pub_type"),
            "im": r.get("predict_vola_ratio_newest"),
            "day0": None,
        })
        if r.get("schedule_delta") == 0 and r.get("trading_day_str"):
            d["day0"] = str(r["trading_day_str"])[:10]
    return [per[k] for k in sorted(per)]


def session_of(pub_time_str: str) -> str:
    """'AMC' / 'BMO' / 'intraday' — 由发布时刻机械判定。"""
    try:
        hh, mm = pub_time_str.split(" ")[1].split(":")[:2]
        minutes = int(hh) * 60 + int(mm)
    except Exception:
        return "intraday"
    if minutes >= 16 * 60:
        return "AMC"
    if minutes < 9 * 60 + 30:
        return "BMO"
    return "intraday"


DROPPED: dict = {}


def collect():
    DROPPED.clear()
    episodes = []
    for f in sorted(EARNINGS_DEEP_DIR.glob("*.json")):
        t = f.stem
        kf = KLINE_DEEP_DIR / f"{t}.jsonl"
        if not kf.exists():
            continue
        bars = load_deep(kf)
        n = len(bars)
        if n < MIN_BARS:
            continue
        dates = [b[0] for b in bars]
        closes = [b[4] for b in bars]
        highs = [b[2] for b in bars]
        lows = [b[3] for b in bars]
        atr20 = atr_series(highs, lows, closes, 20)
        last_e = -(10 **9)
        for p in periods_of(f):
            pub, pub_time, im = p["pub"], p["pub_time"], p["im"]
            if im is None:
                continue
            sess = session_of(pub_time)
            # 验收修正 #1 (blocker): E 日判定优先级 vendor day-0 > pub_type > 干净时间戳,
            # 全部不可判 → 剔除计数 (不猜)。pub_type 语义来自 provider contract。
            if p["day0"]:
                e_i = bisect_left(dates, p["day0"])
                e_src = "vendor_day0"
            elif p["pub_type"] == 1:
                e_i = bisect_left(dates, pub)
                e_src = "pub_type_bmo"
            elif p["pub_type"] == 2:
                e_i = bisect_right(dates, pub)
                e_src = "pub_type_amc"
            elif sess == "AMC":
                e_i = bisect_right(dates, pub)
                e_src = "time_amc"
            elif sess == "BMO" and pub_time and not pub_time.endswith("00:00:00"):
                e_i = bisect_left(dates, pub)
                e_src = "time_bmo"
            else:
                DROPPED["unknown_session"] = DROPPED.get("unknown_session", 0) + 1
                continue
            if e_i >= n or e_i - 6 < WARMUP:
                continue
            if e_i - last_e < COOLDOWN:
                continue
            reg = regime_of(bars, e_i - 1)
            if reg not in REGIMES:
                continue
            if atr20[e_i - 1] is None:
                continue
            last_e = e_i
            ep = {"ticker": t, "date": dates[e_i], "month": dates[e_i][:7],
                  "regime": reg, "im": im, "bucket": im_bucket(im),
                  "session": sess, "e_src": e_src}
            for label, tdays in (("t20", 20), ("t60", 60)):
                term = e_i + tdays
                if term > n - 1 or e_i + 1 > n - 1:
                    ep[label] = None
                    continue
                pre_v, pre_s = world_value(closes, atr20, e_i - 1, term)
                post_v, post_s = world_value(closes, atr20, e_i + 1, term)
                # POST 世界 policy: E-1 起 1 元现金, E+1 收盘全额转入 → 值即 close[term]/close[e+1]
                pre5 = None
                if e_i - 5 >= 0 and atr20[e_i - 5] is not None:
                    p5v, p5s = world_value(closes, atr20, e_i - 5, term)
                    pre5 = {"v": p5v, "stop": p5s}
                # 3N 止损敏感性只报告不裁决。
                pre3_v, _ = world_value(closes, atr20, e_i - 1, term, stop_mult=3)
                post3_v, _ = world_value(closes, atr20, e_i + 1, term, stop_mult=3)
                ep[label] = {"fast": pre_v, "fast_stop": pre_s,
                             "slow": post_v, "slow_stop": post_s, "pre5": pre5,
                             "diff_3n": pre3_v - post3_v}
            if ep.get("t20") or ep.get("t60"):
                episodes.append(ep)
    return episodes


def _pool(rows, label):
    rows = [e for e in rows if e.get(label)]
    if not rows:
        return None
    diffs = [e[label]["fast"] - e[label]["slow"] for e in rows]
    return {"n": len(rows), "mean_diff": sum(diffs) / len(diffs),
            "pre_stop": sum(1 for e in rows if e[label]["fast_stop"]) / len(rows),
            "post_stop": sum(1 for e in rows if e[label]["slow_stop"]) / len(rows),
            "p10_pre": _p10([e[label]["fast"] for e in rows]),
            "p10_post": _p10([e[label]["slow"] for e in rows])}


def _mean_diff(rows):
    return (sum(e["t60"]["fast"] - e["t60"]["slow"] for e in rows) / len(rows)) if rows else None


def _stop_diff(rows):
    if not rows:
        return None
    return (sum(1 for e in rows if e["t60"]["fast_stop"]) -
            sum(1 for e in rows if e["t60"]["slow_stop"])) / len(rows)


def _p10_diff(rows):
    if not rows:
        return None
    a = _p10([e["t60"]["fast"] for e in rows])
    b = _p10([e["t60"]["slow"] for e in rows])
    return None if a is None or b is None else a - b


def summarize(episodes):
    out = {"cells": {}, "d1_pools": {}, "cohorts": {}, "pre5": {}, "sens3n": {},
           "n_total": len(episodes),
           "months": len({e["month"] for e in episodes}),
           "kish_total": round(kish([e["month"] for e in episodes]), 1),
           "dropped": dict(DROPPED),
           # 验收修正 #6: session/E-日来源构成上报 (BMO 的 newest-IM 时点 caveat 可见)
           "session_mix": _count_by(episodes, "session"),
           "e_src_mix": _count_by(episodes, "e_src")}
    for b in IM_BUCKETS:
        for reg in REGIMES:
            rows = [e for e in episodes if e["bucket"] == b and e["regime"] == reg]
            rows60 = [e for e in rows if e.get("t60")]
            out["cells"][f"{b}|{reg}"] = {
                "n": len(rows), "kish": round(kish([e["month"] for e in rows]), 1),
                "n_t60": len(rows60),
                "kish_t60": round(kish([e["month"] for e in rows60]), 1),
                "t20": _pool(rows, "t20"), "t60": _pool(rows, "t60"),
                # 每格报告 CI90；T20/T60 使用同 seed。
                "diff_ci90": _boot_metric(rows, "t60", _mean_diff, DRAWS, SEED),
                "diff_t20_ci90": _boot_metric(rows, "t20", _mean_diff_t20, DRAWS, SEED)}
        pool_all = [e for e in episodes if e["bucket"] == b]
        # 验收修正 #3: D1 的 n/Kish/bootstrap 全部用 t60 承载子样本 (右截断条款)
        pool = [e for e in pool_all if e.get("t60")]
        d = {"n_all": len(pool_all), "n_t60": len(pool),
             "kish_t60": round(kish([e["month"] for e in pool]), 1),
             "t60": _pool(pool, "t60"),
             "diff_ci90": _boot_metric(pool, "t60", _mean_diff, DRAWS, SEED),
             "stop_diff_ci90": _boot_metric(pool, "t60", _stop_diff, DRAWS, SEED),
             "p10_diff_ci90": _boot_metric(pool, "t60", _p10_diff, P10_DRAWS, SEED + 1)}
        out["sens3n"][b] = {"n": len(pool),
                            "mean_diff_3n": (sum(e["t60"]["diff_3n"] for e in pool) / len(pool))
                            if pool else None}
        out["d1_pools"][b] = d
    years = sorted({int(e["month"][:4]) for e in episodes})
    first = (years[0] // 2) * 2 if years else 0
    last = (years[-1] // 2) * 2 if years else -1
    for index, lo in enumerate(range(first, last + 1, 2)):
        hi = lo + 1
        name = f"cohort_{index + 1}"
        rows = [e for e in episodes if lo <= int(e["month"][:4]) <= hi]
        out["cohorts"][name] = {"n": len(rows), "t60": _pool(rows, "t60")}
    p5 = [e for e in episodes if e.get("t60") and e["t60"]["pre5"]]
    if p5:
        out["pre5"] = {"n": len(p5),
                       "mean_diff_vs_post": sum(e["t60"]["pre5"]["v"] - e["t60"]["slow"]
                                                for e in p5) / len(p5)}
    return out


def d1_flags(summary):
    """预注册 D1 机械读数 (按 IM 桶, T60, kish_t60 闸门)。

    机械修正 #5（pin，非结果驱动）：A 与 B 同真时（lo≥0 且 p10 差
    CI ⊂ (−1pp, 0)) **A 优先**— −1pp 是 materiality 阈值, 统计显著但幅度 <1pp
    的左尾恶化不构成保留 clamp 的理由 (分支书写顺序即优先级)。
    闸门定义在 D1 桶级 (kish_t60); 成员格 kish_t60 < 30 → verdict 附 thin_cells
    标记 (可见, 人工复核), 不整桶 abort。"""
    flags = {}
    for b, d in summary["d1_pools"].items():
        lo, hi = d["diff_ci90"] or (None, None)
        plo, phi = d["p10_diff_ci90"] or (None, None)
        if d["kish_t60"] < KISH_MIN or lo is None or plo is None:
            flags[b] = {"verdict": "insufficient", "kish_t60": d["kish_t60"]}
            continue
        branch_a = lo >= 0 and plo > -0.01
        branch_c = hi < 0
        branch_b = (not branch_a) and (not branch_c) and lo <= 0 <= hi and phi < 0
        verdict = ("A_unclamp" if branch_a else
                   ("C_keep_mean_loss" if branch_c else
                    ("B_keep_insurance" if branch_b else "no_call_keep")))
        thin = [f"{b}|{r}" for r in REGIMES
                if summary["cells"][f"{b}|{r}"]["kish_t60"] < KISH_MIN]
        flags[b] = {"verdict": verdict, "exp_ci90": [lo, hi],
                    "p10_diff_ci90": [plo, phi],
                    "stop_diff_ci90": list(d["stop_diff_ci90"] or (None, None)),
                    "kish_t60": d["kish_t60"], "thin_cells": thin}
    return flags


def _f(x, w=9):
    return f"{x:>{w}.4f}" if isinstance(x, float) else f"{str(x):>{w}}"


def render(summary, flags):
    L = []
    L.append(f"earnings_gate round-1 | episodes {summary['n_total']} / {summary['months']} 月 "
             f"/ Kish≈{summary['kish_total']}")
    L.append("口径: policy 共同起点 E-1, PRE=E-1 收盘入承担 print vs POST=E+1 收盘入,")
    L.append("     止损对称 2N×1 收盘 (gap 不救), T+60 主 / T+20 副, CI90 按月 block bootstrap")
    L.append("")
    L.append(f"{'cell':>20} {'n60':>5} {'Kish60':>7} {'ΔT60':>9} {'CI90':>21} {'preStop':>8} "
             f"{'postStop':>8} {'p10pre':>8} {'p10post':>8}")
    for key, c in summary["cells"].items():
        t = c["t60"] or {}
        lo, hi = c["diff_ci90"] or (None, None)
        ci = f"[{lo:+.4f},{hi:+.4f}]" if lo is not None else "n/a"
        L.append(f"{key:>20} {c['n_t60']:>5} {c['kish_t60']:>7} {_f(t.get('mean_diff'))} {ci:>21} "
                 f"{_f(t.get('pre_stop'), 8)} {_f(t.get('post_stop'), 8)} "
                 f"{_f(t.get('p10_pre'), 8)} {_f(t.get('p10_post'), 8)}")
    L.append("")
    L.append(f"session 构成: {summary['session_mix']} | E 日来源: {summary['e_src_mix']} "
             f"| 剔除: {summary['dropped']}")
    L.append("⚠️ BMO 期的 predict_vola_newest 可能含 E-1 收盘后的盘前更新 (vendor 只存 newest 快照,")
    L.append("   无法取 E-1 时点值) — BMO 占比见上行, 解读 IM 分桶时计入此 caveat (验收修正 #6)")
    L.append("")
    L.append("D1 (按 IM 桶, regime 合并, T60, kish_t60 闸门):")
    for b in IM_BUCKETS:
        d = summary["d1_pools"][b]
        fl = flags[b]
        lo, hi = d["diff_ci90"] or (None, None)
        ci = f"[{lo:+.4f},{hi:+.4f}]" if lo is not None else "n/a"
        plo, phi = d["p10_diff_ci90"] or (None, None)
        pci = f"[{plo:+.4f},{phi:+.4f}]" if plo is not None else "n/a"
        thin = f" ⚠️thin:{','.join(fl['thin_cells'])}" if fl.get("thin_cells") else ""
        L.append(f"  {b:>8} n60={d['n_t60']:<4} Kish60={d['kish_t60']:<6} ΔT60 CI90 {ci:>21} "
                 f"p10Δ CI90 {pci:>21} → {fl['verdict']}{thin}")
    L.append("")
    L.append("敏感性 3N (只报告不裁决): " + ", ".join(
        f"{b}: Δ={_f(summary['sens3n'][b]['mean_diff_3n'], 8)} (n={summary['sens3n'][b]['n']})"
        for b in IM_BUCKETS))
    L.append("")
    for name, c in summary["cohorts"].items():
        t = c["t60"] or {}
        L.append(f"cohort {name}: n={c['n']} ΔT60={_f(t.get('mean_diff'))}")
    if summary.get("pre5"):
        L.append(f"副轴 PRE5 (只报告): n={summary['pre5']['n']} "
                 f"Δ(PRE5−POST)T60={_f(summary['pre5']['mean_diff_vs_post'])}")
    return "\n".join(L)


def main():
    report = None
    if "--report" in sys.argv:
        report = Path(sys.argv[sys.argv.index("--report") + 1])
    episodes = collect()
    summary = summarize(episodes)
    flags = d1_flags(summary)
    text = render(summary, flags)
    print(text)
    if report:
        report.parent.mkdir(parents=True, exist_ok=True)
        payload = {"summary": summary, "d1_flags": flags, "seed": SEED, "draws": DRAWS}
        report.write_text(
            "# earnings_gate round-1 结果 (机器输出存档)\n\n```\n" + text + "\n```\n\n"
            "<details><summary>full json</summary>\n\n```json\n"
            + json.dumps(payload, ensure_ascii=False, indent=1)
            + "\n```\n</details>\n")
        print(f"\nreport → {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
