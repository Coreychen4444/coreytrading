#!/usr/bin/env python3
"""Gate backtest — 结构破位后, 什么信号说明"可以回来了"?

gate = 状态转移锚点。系统卡片可以记录 gate 门槛，但公开代码不附带私人结论。
个别历史卡片只能提供证据级线索，不能替代验证。本脚本量化的是唯一那个
权衡——**迟到 vs 假突破**。

只测 production 已经计算的两个维度，避免在结果产生后增加自由度：
  swing  道氏 swing high 收复 — technical.swing_structure(), entry_guard 已在
         用它输出"修复第一信号"。趋势的定义本身。
  ma60   收盘站上 MA60 — 作为对照基线

每个 × 站稳 {1,2,3} 日 = 6 格。有效独立事件 ≈41 (403 episodes 聚在 69 个月,
Kish 有效样本), 6 格是样本能承受的上限——再多维度就是拟合噪音。

⚠️ 无前视: 第 j 日决策只用 ≤j 的收盘; swing pivot 需右侧 wing 根确认, 故第 j
日只认 index ≤ j-wing 的 pivot。MA60 同理只用 ≤j 的数据。

Usage: .venv/moomoo/bin/python3 quant/research/gate_backtest.py [--json]
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 直接 `python3 quant/<层>/<模块>.py` 时让 `quant.*` 可 import

import json
import statistics as st
from collections import Counter, defaultdict

from quant.core.paths import KLINE_DEEP_DIR as KDIR  # noqa: E402
# episode 构造  提到 core/ —— 回测与 live 必须 import 同一份实现
# (gate_path_taxonomy 预注册决策点 E)。这里保留同名 re-export, 四份回测的
# `from quant.research.gate_backtest import episodes_of` 原样可用, 但只有一份实现。
from quant.core.episodes import COOLDOWN, LOOKBACK, episodes_of  # noqa: E402,F401

WING = 2          # technical.swing_points 的 fractal 半宽
WINDOW = 120      # episode 向前观察窗
TRAP_DAYS = 10    # 触发后多少日内跌回 gate 位之下 = 假突破
FWD = (20, 40)    # 前向收益 horizon (各自入场价起算)
TERMINAL = (60, 120)  # 共同终点 horizon (破位日起算, 唯一可比的口径)


def load(path):
    rows = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    return [r for r in rows if r.get("close")]


def sma_series(closes, n):
    out, s = [None] * len(closes), 0.0
    for i, c in enumerate(closes):
        s += c
        if i >= n:
            s -= closes[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def confirmed_swing_highs(highs, wing=WING):
    """→ [(idx, high)]，与 technical.swing_points 同口径 (严格单峰)。"""
    out = []
    for i in range(wing, len(highs) - wing):
        win = highs[i - wing:i + wing + 1]
        if highs[i] == max(win) and win.count(highs[i]) == 1:
            out.append((i, highs[i]))
    return out


def gate_level(kind, j, closes, highs, ma60, swings):
    """第 j 日收盘后, 该 gate 的门槛价。None = 当日无法定义。"""
    if kind == "ma60":
        return ma60[j]
    # swing: 只认已被右侧 wing 根确认的 pivot (idx + wing <= j)
    usable = [h for idx, h in swings if idx + WING <= j]
    return usable[-1] if usable else None


def run():
    tickers = sorted(p for p in KDIR.glob("*.jsonl"))
    results = defaultdict(list)          # (kind, days) -> [rec]
    base_break, base_oracle, n_ep = {}, {}, 0
    ep_months = Counter()

    for path in tickers:
        bars = load(path)
        if len(bars) < 300:
            continue
        closes = [b["close"] for b in bars]
        highs = [b["high"] for b in bars]
        ma60 = sma_series(closes, 60)
        swings = confirmed_swing_highs(highs)

        for i in episodes_of(closes, ma60):
            end = min(i + WINDOW, len(closes) - 1)
            if end - i < max(FWD) + TRAP_DAYS:
                continue                  # 窗口不足以评估, 丢弃 (避免右侧截断偏差)
            n_ep += 1
            ep_months[bars[i]["time"][:7]] += 1
            ep_low = min(closes[i:end + 1])

            # 反事实基线: 破位日直接买 / 买在最低点 (上帝视角上界)
            for T in TERMINAL:
                k = i + T
                if k <= end:
                    base_break.setdefault(T, []).append(closes[k] / closes[i] - 1)
                    base_oracle.setdefault(T, []).append(closes[k] / ep_low - 1)

            for kind in ("swing", "ma60"):
                for need in (1, 2, 3):
                    streak, fired = 0, None
                    for j in range(i + 1, end + 1):
                        lvl = gate_level(kind, j, closes, highs, ma60, swings)
                        if lvl is None:
                            streak = 0
                            continue
                        streak = streak + 1 if closes[j] > lvl else 0
                        if streak >= need:
                            fired = (j, lvl)
                            break
                    if not fired:
                        results[(kind, need)].append({"fired": False})
                        continue
                    j, lvl = fired
                    px = closes[j]
                    low_so_far = min(closes[i:j + 1])
                    trap_end = min(j + TRAP_DAYS, end)
                    trap = any(closes[k] < lvl for k in range(j + 1, trap_end + 1))
                    rec = {
                        "fired": True,
                        "lateness": px / low_so_far - 1,
                        "delay_days": j - i,
                        "trap": trap,
                    }
                    for h in FWD:
                        k = min(j + h, end)
                        rec[f"fwd{h}"] = closes[k] / px - 1 if k > j else None
                    # 苹果对苹果: 从破位日到**同一终点**T 的总收益。
                    # gate 策略在 i→j 持现金 (收益 0), j 才建仓 → 终值 = P_T / P_j。
                    # 不这样比就是拿"晚 14-33 天、价高 8-14% 的入场"去比"破位日入场"
                    # 的同长度前向窗, 两者终点不同, 结论无意义。
                    for T in TERMINAL:
                        k = i + T
                        rec[f"term{T}"] = (closes[k] / px - 1) if k <= end and k > j else None
                    results[(kind, need)].append(rec)

    return results, base_break, base_oracle, n_ep, ep_months


def pct(x):
    return f"{x * 100:+.1f}%"


def summarize(results, base_break, base_oracle, n_ep, ep_months):
    eff = n_ep **2 / sum(v * v for v in ep_months.values()) if ep_months else 0
    out = {"episodes": n_ep, "months": len(ep_months), "effective_n": round(eff, 1),
           "baseline": {}, "gates": {}}
    for T in TERMINAL:
        if base_break.get(T):
            out["baseline"][f"buy_at_break_term{T}_median"] = round(st.median(base_break[T]), 4)
            out["baseline"][f"buy_at_low_term{T}_median"] = round(st.median(base_oracle[T]), 4)
            out["baseline"][f"n_term{T}"] = len(base_break[T])
    for (kind, need), recs in sorted(results.items()):
        fired = [r for r in recs if r["fired"]]
        if not fired:
            continue
        row = {
            "n": len(recs),
            "fire_rate": round(len(fired) / len(recs), 3),
            "trap_rate": round(sum(r["trap"] for r in fired) / len(fired), 3),
            "lateness_median": round(st.median(r["lateness"] for r in fired), 4),
            "delay_days_median": round(st.median(r["delay_days"] for r in fired), 1),
        }
        for T in TERMINAL:
            v = [r[f"term{T}"] for r in fired if r.get(f"term{T}") is not None]
            if v:
                v.sort()
                row[f"term{T}_median"] = round(st.median(v), 4)
                row[f"term{T}_p10"] = round(v[max(0, int(len(v) * 0.10) - 1)], 4)
                row[f"term{T}_n"] = len(v)
        for h in FWD:
            v = [r[f"fwd{h}"] for r in fired if r.get(f"fwd{h}") is not None]
            if v:
                v.sort()
                row[f"fwd{h}_median"] = round(st.median(v), 4)
                row[f"fwd{h}_p10"] = round(v[max(0, int(len(v) * 0.10) - 1)], 4)
                row[f"fwd{h}_mean"] = round(sum(v) / len(v), 4)
        out["gates"][f"{kind}_x{need}"] = row
    return out


def render(s):
    L = [f"=== gate backtest === {s['episodes']} episodes / {s['months']} 月 / "
         f"Kish 有效独立事件 ≈{s['effective_n']}"]
    b = s["baseline"]
    L.append("【苹果对苹果】从破位日到同一终点的总收益 (gate 策略等待期持现金):")
    for T in TERMINAL:
        if f"buy_at_break_term{T}_median" in b:
            L.append(f"  基线 T=+{T}d (n={b[f'n_term{T}']}): 破位日直接买 "
                     f"{pct(b[f'buy_at_break_term{T}_median'])} | "
                     f"买在最低点(上帝视角) {pct(b[f'buy_at_low_term{T}_median'])}")
    L.append("")
    L.append(f"{'gate':<12}{'触发率':>7}{'假突破':>7}{'迟到':>8}{'延迟d':>6}"
             f"{'T+60总收益':>11}{'T+60 p10':>10}{'T+120总收益':>12}")
    for k, r in s["gates"].items():
        L.append(f"{k:<12}{r['fire_rate']:>7.0%}{r['trap_rate']:>7.0%}"
                 f"{pct(r['lateness_median']):>8}{r['delay_days_median']:>6.0f}"
                 f"{pct(r.get('term60_median', 0)):>11}{pct(r.get('term60_p10', 0)):>10}"
                 f"{pct(r.get('term120_median', 0)):>12}")
    return "\n".join(L)


if __name__ == "__main__":
    s = summarize(*run())
    if "--json" in sys.argv:
        print(json.dumps(s, ensure_ascii=False, indent=2))
    else:
        print(render(s))
