"""escape_velocity_backtest — breakout-stale 单日放量快车道 vs 现行 2 日确认。

预注册: quant/research/escape_velocity_redraw_design.md (锁定 , 用户 批准)。
真数据只跑一次; 本 docstring 复制设计稿要点作为正式预注册。

── 预注册要点 (运行前锁定) ──────────────────────────────────────────
问题: breakout-stale 需 close > trim 上沿连续 2 日 → 重画, 垂直行情识别延迟
2-3 session (NBIS  +27% 案例)。检验单日逃逸信号当日给重画资格。

对赌: H-fast (单日价量双确认可靠, 提前收益 > whipsaw 税) vs H-slow (假突破率高,
2 日确认的保护 > 延迟成本 = 现行设计)。

Episode:
- level = 前 60 日最高收盘 (不含当日) — live trim 的机械代理 (测确认口径的一般性质)
- 触发日 i: close[i] > level 首日, 同票 cooldown 60 交易日
- FAST 资格: 触发日 量比 ≥ 2 (SMA20 含当日, 与 technical.volume_ratio 同拍)
- World FAST: i+1 收盘买入 (当日重画、次日执行)
- World SLOW (现行): close[i+1] > level → i+2 收盘买入; 跌回 → 持现金 (intent-to-treat)
- 共同起点 i、1 元本金、共同终点 i+20 / i+60 收盘 (policy 口径)
- 止损: 对称 entry − 2×ATR20@入场, 收盘 ×1 日触发, 次日收盘出场 SLIP 0.2%,
  single-shot; 终点日触发 = 悬置 (按终点收盘 mark, 不计滑点, 计入止损率)
- whipsaw 度量: FAST 买入而 close[i+1] 跌回 level 之下 (SLOW 空仓) 的占比与分布

主格 = 3 regime (regime_of 复用 entry_ladder_backtest, 无新自由度) × FAST 资格成立。
对照池 (量比 < 2 的触发日) 与敏感性变体 (量比≥1.5 / close > level + 1×ATR20)
只报告不裁决。

统计: 按月 block bootstrap CI90, seed=20260801 固定 10,000 draws (p10 差 seed+1,
1,000 draws); Kish 有效 N 按月; 任一主格 Kish < 30 → 报「数据不足」不裁决。

D1 裁决菜单 (按 regime 格独立, 机械执行落 JSON):
- 分支 A (快车道转正): expectancy(FAST−SLOW) T60 CI90 下界 > 0 且 止损率差 CI90 上界 < +5pp
- 分支 B (可选提前): expectancy CI 含 0 且 止损率差 CI90 上界 < +5pp
- 分支 C (维持现行): expectancy CI90 上界 < 0 或 止损率差 CI90 下界 > +5pp (显著恶化)

Survivorship 声明: klines_deep 存续票池 → 突破后崩掉退市的票缺席 → 偏向高估
突破跟随价值 (对 H-fast 有利); 若 FAST 仍不显著, 结论更稳。

不做什么: 不调 level 窗口 / 不调量比阈值 / 不加格 / 不看中间数字 /
对照池与变体不参与裁决 / whipsaw 子样本不单独裁决。

── 验收修正 (对抗性审查 , 全部在真数据运行之前; 非结果驱动,
   用户 保留否决权; 同批追记设计稿「运行前修订」段) ──────────────────
#1 Kish<30 闸门措辞补「该格」— 按格独立 insufficient, 不全局 abort (对齐姊妹设计)
#2 终点日止损悬置**不计止损率**(对齐 gate_backtest2 口径, round-2 数字可横比)
#3 _boot_metric 月宇宙只由承载行构建, <2 月守卫过滤后判
#4 数据无效 (vr/ATR None) 的触发日不消耗 cooldown
#5 world_value 用 `atr is not None` (ATR==0 → stop=entry, 不再判成无止损)
#6 D1 闸门 Kish 用 t60 承载子样本 (kish_t60), 全口径 Kish 并列报告
#7 每格补 ΔT20 CI90 (同 seed/draws) + whipsaw 子样本 p10/p50/p90
#8 no_call 兜底语义 = 维持现行 (证据不足), 对齐 earnings 设计的兜底条款
──────────────────────────────────────────────────────────────────

用法: python3 quant/research/escape_velocity_backtest.py [--report tmp/backtest/escape_velocity_report.md]
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from quant.core.paths import KLINE_DEEP_DIR  # noqa: E402
from quant.research.entry_ladder_backtest import regime_of  # noqa: E402
from quant.research.gate_backtest2 import atr_series  # noqa: E402

SEED = 20260801
DRAWS = 10_000
P10_DRAWS = 1_000
LEVEL_LOOKBACK = 60
COOLDOWN = 60
WARMUP = 126          # regime_of 需要
SLIP = 0.002
VOL_FAST = 2.0
VOL_ALT = 1.5
MIN_BARS = 300
KISH_MIN = 30
REGIMES = ("parabolic", "strong_up", "other")


def load_deep(path: Path):
    bars = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("close") is None:
            continue
        bars.append((r["time"][:10], r["open"], r["high"], r["low"], r["close"],
                     r.get("volume") or 0.0))
    return bars


def vol_ratio(vols, i, lb=20):
    """量比 = vol[i] / SMA20 含当日 (与 gate_backtest2.vol_ok / technical 同拍)。"""
    if i < lb - 1:
        return None
    base = sum(vols[i - lb + 1:i + 1]) / lb
    return (vols[i] / base) if base > 0 else None


def world_value(closes, atr20, entry_i, term_i, stop_mult=2):
    """policy 收益: entry_i 收盘买入 → term_i 收盘, stop_mult×ATR20 ×1 日止损 single-shot。
    返回 (value, stopped)。终点日收盘破 stop = 悬置: 按终点收盘 mark、**不计止损率**
    (验收修正 #2: 对齐 gate_backtest2 悬置口径, 止损率可与 round-2 横比;
    验收修正 #5: `if atr is not None` — ATR==0.0 时 stop=entry, 不再被 falsy 判成无止损)。"""
    entry_px = closes[entry_i]
    atr = atr20[entry_i]
    if atr is not None:
        stop = entry_px - stop_mult * atr
        for d in range(entry_i + 1, term_i):
            if closes[d] < stop:
                return closes[d + 1] * (1 - SLIP) / entry_px, True
    return closes[term_i] / entry_px, False


def collect(kdir=KLINE_DEEP_DIR):
    episodes = []
    for f in sorted(kdir.glob("*.jsonl")):
        bars = load_deep(f)
        n = len(bars)
        if n < MIN_BARS:
            continue
        closes = [b[4] for b in bars]
        highs = [b[2] for b in bars]
        lows = [b[3] for b in bars]
        vols = [b[5] for b in bars]
        atr20 = atr_series(highs, lows, closes, 20)
        last_trig = -(10 **9)
        for i in range(max(WARMUP, LEVEL_LOOKBACK), n - 2):
            level = max(closes[i - LEVEL_LOOKBACK:i])
            if closes[i] <= level:
                continue
            if i - last_trig < COOLDOWN:
                continue
            vr = vol_ratio(vols, i)
            if vr is None or atr20[i] is None:
                continue          # 验收修正 #4: 数据无效的触发日不消耗 cooldown
            last_trig = i
            slow_enters = closes[i + 1] > level
            ep = {
                "ticker": f.stem, "date": bars[i][0], "month": bars[i][0][:7],
                "regime": regime_of(bars, i) or "other",
                "vr": vr, "fast_ok": vr >= VOL_FAST, "vol15": vr >= VOL_ALT,
                "price_esc": closes[i] > level + atr20[i],
                "whipsaw": not slow_enters,
            }
            for label, t in (("t20", 20), ("t60", 60)):
                term = i + t
                if term > n - 1:
                    ep[label] = None
                    continue
                fv, fs = world_value(closes, atr20, i + 1, term)
                if slow_enters:
                    sv, ss = world_value(closes, atr20, i + 2, term)
                else:
                    sv, ss = 1.0, False
                ep[label] = {"fast": fv, "fast_stop": fs, "slow": sv, "slow_stop": ss}
            if ep["t20"] or ep["t60"]:
                episodes.append(ep)
    return episodes


def kish(months_list):
    if not months_list:
        return 0.0
    cnt = {}
    for m in months_list:
        cnt[m] = cnt.get(m, 0) + 1
    tot = sum(cnt.values())
    return tot * tot / sum(v * v for v in cnt.values())


def _pool_stats(eps, label):
    rows = [e for e in eps if e.get(label)]
    if not rows:
        return None
    diffs = [e[label]["fast"] - e[label]["slow"] for e in rows]
    return {
        "n": len(rows),
        "mean_diff": sum(diffs) / len(diffs),
        "fast_stop_rate": sum(1 for e in rows if e[label]["fast_stop"]) / len(rows),
        "slow_stop_rate": sum(1 for e in rows if e[label]["slow_stop"]) / len(rows),
    }


def _p10(vals):
    """type-1 分位 (取 s[ceil(0.1n)-1])。注: 与 gate_backtest2._pct_row 的秩约定在
    n%10!=0 时差一位 (那边 s[int(0.1n)-1]) — 本实现是标准 type-1, 不改数学,
    跨报告横比 p10 时注意 (验收 stats-minor 记录)。"""
    if not vals:
        return None
    s = sorted(vals)
    return s[max(0, int(0.10 * len(s)) - (1 if len(s) % 10 == 0 else 0))]


def _boot_metric(rows, label, metric_fn, draws, seed):
    """按月 block bootstrap。metric_fn(list_of_rows) -> float。返回 (lo90, hi90)。
    验收修正 #3: 月宇宙只由 label 承载行构建 (对齐 gate2 _by_month 惯例),
    <2 月守卫在过滤后判 — t60=None 的末端月不再稀释重抽。"""
    rows = [e for e in rows if e.get(label)]
    by_month: dict = {}
    for e in rows:
        by_month.setdefault(e["month"], []).append(e)
    months = sorted(by_month)
    if len(months) < 2:
        return None, None
    rng = random.Random(seed)
    vals = []
    for _ in range(draws):
        pick = [months[rng.randrange(len(months))] for _ in months]
        pool = [e for m in pick for e in by_month[m]]
        v = metric_fn(pool)
        if v is not None:
            vals.append(v)
    if not vals:
        return None, None
    vals.sort()
    return vals[int(0.05 * len(vals))], vals[min(len(vals) - 1, int(0.95 * len(vals)))]


def _mean_diff_at(label):
    def f(rows):
        if not rows:
            return None
        return sum(e[label]["fast"] - e[label]["slow"] for e in rows) / len(rows)
    return f


def _stop_diff_at(label):
    def f(rows):
        if not rows:
            return None
        return (sum(1 for e in rows if e[label]["fast_stop"]) -
                sum(1 for e in rows if e[label]["slow_stop"])) / len(rows)
    return f


def _p10_diff_at(label):
    def f(rows):
        if not rows:
            return None
        pf = _p10([e[label]["fast"] for e in rows])
        ps = _p10([e[label]["slow"] for e in rows])
        return None if pf is None or ps is None else pf - ps
    return f


_mean_diff = _mean_diff_at("t60")
_stop_diff = _stop_diff_at("t60")
_p10_diff = _p10_diff_at("t60")


def summarize(episodes):
    out = {"cells": {}, "control": {}, "variants": {}, "whipsaw": {}}
    main = [e for e in episodes if e["fast_ok"]]
    for reg in REGIMES:
        rows = [e for e in main if e["regime"] == reg]
        rows60 = [e for e in rows if e.get("t60")]
        cell = {
            "n": len(rows),
            "kish": round(kish([e["month"] for e in rows]), 1),
            # 验收修正 #6: D1 闸门用 t60 承载子样本的 Kish (末端 t20-only 行不虚增有效 N)
            "kish_t60": round(kish([e["month"] for e in rows60]), 1),
            "t20": _pool_stats(rows, "t20"),
            "t60": _pool_stats(rows, "t60"),
            "whipsaw_rate": (sum(1 for e in rows if e["whipsaw"]) / len(rows)) if rows else None,
            "p10_fast_t60": _p10([e["t60"]["fast"] for e in rows60]),
            "p10_slow_t60": _p10([e["t60"]["slow"] for e in rows60]),
        }
        cell["diff_ci90"] = _boot_metric(rows, "t60", _mean_diff, DRAWS, SEED)
        cell["stop_diff_ci90"] = _boot_metric(rows, "t60", _stop_diff, DRAWS, SEED)
        cell["p10_diff_ci90"] = _boot_metric(rows, "t60", _p10_diff, P10_DRAWS, SEED + 1)
        # 验收修正 #7: 设计稿承诺每格 ΔT20 CI90 — 同 seed 同 draws 零自由度补齐
        cell["diff_t20_ci90"] = _boot_metric(rows, "t20", _mean_diff_at("t20"), DRAWS, SEED)
        out["cells"][reg] = cell
    ctrl = [e for e in episodes if not e["fast_ok"]]
    out["control"] = {"n": len(ctrl), "t60": _pool_stats(ctrl, "t60"),
                      "whipsaw_rate": (sum(1 for e in ctrl if e["whipsaw"]) / len(ctrl)) if ctrl else None}
    for name, pred in (("vol15", lambda e: e["vol15"]),
                       ("price_esc", lambda e: e["price_esc"])):
        pool = [e for e in episodes if pred(e)]
        out["variants"][name] = {"n": len(pool), "t60": _pool_stats(pool, "t60")}
    wh = [e for e in main if e["whipsaw"] and e.get("t60")]
    whv = sorted(e["t60"]["fast"] for e in wh)
    out["whipsaw"] = {
        "n": len(wh),
        "mean_fast_t60": (sum(whv) / len(whv)) if whv else None,
        # 验收修正 #7: whipsaw 子样本补分位 (设计稿"forward 分布")
        "p10": _p10(whv), "p50": whv[len(whv) // 2] if whv else None,
        "p90": whv[int(0.90 * len(whv))] if whv else None,
    }
    out["n_total"] = len(episodes)
    out["n_fast"] = len(main)
    out["months"] = len({e["month"] for e in episodes})
    out["kish_total"] = round(kish([e["month"] for e in episodes]), 1)
    return out


def d1_flags(summary):
    """预注册 D1 机械读数 (T60 主口径)。"""
    flags = {}
    for reg, cell in summary["cells"].items():
        lo, hi = cell["diff_ci90"] or (None, None)
        slo, shi = cell["stop_diff_ci90"] or (None, None)
        if cell["kish_t60"] < KISH_MIN or lo is None or slo is None:
            flags[reg] = {"verdict": "insufficient", "kish_t60": cell["kish_t60"]}
            continue
        branch_a = lo > 0 and shi < 0.05
        branch_c = hi < 0 or slo > 0.05
        branch_b = (not branch_a) and (not branch_c) and lo <= 0 <= hi and shi < 0.05
        verdict = "A_fastlane" if branch_a else ("C_keep_2day" if branch_c else
                                                 ("B_optional" if branch_b else "no_call"))
        flags[reg] = {"verdict": verdict, "exp_ci90": [lo, hi],
                      "stop_diff_ci90": [slo, shi], "kish_t60": cell["kish_t60"]}
    return flags


def _f(x, w=9):
    return f"{x:>{w}.4f}" if isinstance(x, float) else f"{str(x):>{w}}"


def render(summary, flags):
    L = []
    L.append(f"escape_velocity round-1 | episodes {summary['n_total']} (FAST 资格 {summary['n_fast']}) "
             f"/ {summary['months']} 月 / Kish≈{summary['kish_total']}")
    L.append("口径: policy/intent-to-treat, FAST=i+1 收盘入 vs SLOW=2 日确认 i+2 收盘入 (跌回=现金),")
    L.append("     止损对称 2N×1 收盘, 次日收盘出 SLIP 0.2%, 终点 T+60 主 / T+20 副, CI90 按月 block bootstrap")
    L.append("")
    L.append(f"{'regime':>10} {'n':>5} {'Kish':>6} {'ΔT60':>9} {'CI90':>21} {'止损Δ':>8} "
             f"{'CI90':>19} {'p10Δ':>8} {'whip%':>6} verdict")
    for reg in REGIMES:
        c = summary["cells"][reg]
        t60 = c["t60"] or {}
        lo, hi = c["diff_ci90"] or (None, None)
        slo, shi = c["stop_diff_ci90"] or (None, None)
        p10d = None
        if c["p10_fast_t60"] is not None and c["p10_slow_t60"] is not None:
            p10d = c["p10_fast_t60"] - c["p10_slow_t60"]
        ci = f"[{lo:+.4f},{hi:+.4f}]" if lo is not None else "n/a"
        sci = f"[{slo:+.3f},{shi:+.3f}]" if slo is not None else "n/a"
        L.append(f"{reg:>10} {c['n']:>5} {c['kish']:>6} {_f(t60.get('mean_diff'))} {ci:>21} "
                 f"{_f((t60.get('fast_stop_rate') or 0) - (t60.get('slow_stop_rate') or 0), 8)} {sci:>19} "
                 f"{_f(p10d, 8)} {_f((c['whipsaw_rate'] or 0) * 100, 5)} {flags[reg]['verdict']}")
    L.append("")
    ctrl = summary["control"]
    L.append(f"对照池 (量比<2, 不裁决): n={ctrl['n']} ΔT60={_f((ctrl['t60'] or {}).get('mean_diff'))} "
             f"whip%={_f((ctrl['whipsaw_rate'] or 0) * 100, 5)}")
    for name, v in summary["variants"].items():
        L.append(f"变体 {name} (不裁决): n={v['n']} ΔT60={_f((v['t60'] or {}).get('mean_diff'))}")
    wh = summary["whipsaw"]
    L.append(f"whipsaw 子样本 (FAST 买了 SLOW 空仓): n={wh['n']} FAST T60 均值={_f(wh['mean_fast_t60'])}")
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
            "# escape_velocity round-1 结果 (机器输出存档)\n\n```\n" + text + "\n```\n\n"
            "<details><summary>full json</summary>\n\n```json\n"
            + json.dumps(payload, ensure_ascii=False, indent=1)
            + "\n```\n</details>\n")
        print(f"\nreport → {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
