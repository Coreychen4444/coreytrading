#!/usr/bin/env python3
"""交互事件 Step 0 —— **信号量普查**（不是回测，不产出裁决）。

所有定义必须先写入私人预注册；公开脚本不保存研究历史。

只回答一个问题: **按 §7 的定义，一天会触发多少事件?**
若单票单日常态 >1 个事件 = `tol` 或阈值错了 → 先修定义再谈回测
(本仓 `portfolio_brief` 固定 5% 阈值的教训: 全池固定阈值 = 没有阈值)。

**本脚本刻意不算前向收益**—— 见了收益就会忍不住调阈值，那正是 §4.5
(Sullivan-Timmermann-White / Bajgrowicz-Scaillet) 要防的数据窥探。
阈值必须在看到任何收益数字**之前**锁定并 commit。

锚 (§7.1) 全部用**事件日之前**的 bar 计算 (poc_window round-1 的自变量污染教训):
  profile : 滚动 60 根 → volume_profile 三角铺开 → HVN 带边界 + LVN 带边界
  pivot   : 滚动 250 根 → swing_points(wing=2) → cluster_pivots(touches≥2) → 簇中心
  ath     : 事件日之前的历史最高收盘
  card    : 历史不可得，本普查缺这一类（私人 state 才有 zones/gate）—— 计数会偏低

Usage:
    .venv/moomoo/bin/python3 quant/research/interaction_census.py --limit 40 [--json]
    .venv/moomoo/bin/python3 quant/research/interaction_census.py --tickers <SYMBOL_A> <SYMBOL_B>
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import argparse  # noqa: E402
import json  # noqa: E402
from collections import Counter, defaultdict  # noqa: E402

from quant.core import technical as ta  # noqa: E402
from quant.core.episodes import merge_bars_by_date  # noqa: E402
from quant.core.paths import KLINE_CACHE_DIR, KLINE_DEEP_DIR  # noqa: E402
from quant.core.sectors import SEMI_SMH  # noqa: E402
from quant.decision.level_map import cluster_pivots, find_lvn, top_hvn  # noqa: E402
from quant.sync.state_io import read_positions, read_watchlist  # noqa: E402

# ── 预注册占位参数 (§7.2 — 未锁定，改动必须在跑收益之前) ────────────
PARAMS = {
    "tol_atr_mult": 0.25,      # 带宽 = max(0.25×ATR20, profile bucket)  ← 本仓既有 0.25N 口径
    "effort_hi": 1.2,          # RelVR ≥ → 高力；与 production 相对量阈值同值
    "effort_lo": 0.8,          # RelVR ≤ → 低力  ← 占位，无出处
    "result_hi": 1.0,          # range/ATR20 ≥ → 大果  ← 占位
    "result_lo": 0.6,          # range/ATR20 ≤ → 小果  ← 占位
    "close_weak": 0.35,        # 收盘位 ≤ → 弱收       ← 占位
    "close_strong": 0.65,      # 收盘位 ≥ → 强收       ← 占位
    "k_confirm": 1,            # T+k 确认 (§6.4)
    "profile_bars": 60,
    "pivot_bars": 250,
    "pivot_touches_min": 3,    # v0 是 2 → 普查 300/票/年 (见 design §8.0 首跑) → 收紧
    "rebuild_every": 20,       # 锚重建间隔 (交易日)
    "hvn_top_n": 3,            # v0 是 5
    "lvn_poc_frac": 0.25,
    "flip_lookback": 20,       # E4: 过去 m 日内发生过 E2
    "debounce_days": 10,       # 同一条线上两次事件的最小间隔 (边沿触发 + 冷却, 同 heartbeat 语义)
    "max_lines": 6,            # 只保留距当前价最近的 N 条锚 (远处的线本来也交互不到)
    "near_atr": 3.0,           # 锚必须在现价 ±3N 内, 否则不进集合
    "quota_pivot": 0,          # >0 = 按锚类型分名额 (0 = 关闭, 纯按距离取前 max_lines)
    "quota_profile": 0,
    "dedupe_atr_mult": 0.5,    # 锚合并半径 (>tol: 0.25N 内的两条线在读者眼里是同一条)
}

SEMI = set(SEMI_SMH)


def load_bars(ticker: str) -> list[dict]:
    p = KLINE_DEEP_DIR / f"{ticker}.jsonl"
    if not p.exists():
        return []
    out = []
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    out.sort(key=lambda r: str(r.get("time", "")))
    return out


def bench_ratio_map(name: str) -> dict[str, float]:
    """基准的 20 日量比, 按日期索引 (RelVR 的分母)。"""
    bars = load_bars(name)
    if not bars:
        return {}
    vr = ta.volume_ratio([int(b.get("volume") or 0) for b in bars], 20)
    return {str(b["time"])[:10]: v for b, v in zip(bars, vr) if v is not None}


CARD_LINES: dict[str, list[dict]] = {}      # live 模式: ticker → 卡片锚 (§7.1 的 card 类)


def card_lines(body: dict, with_zones: bool = False) -> list[dict]:
    """卡片锚: trim 两沿 + no_chase + stop + next_gate + repair_gate。

    **z1/z2 吸纳带边界默认不进**（`with_zones=True` 才加）: 那条通道已经存在 ——
    watchlist 有 `wl_zone_touch` 告警、持仓卡有 zone 位置行。再报一遍就是本仓
    形状 #8「两条线并存」。判据是「有没有别的通道已经在报」，不是「事件多不多」。"""
    a = body.get("agent") or {}
    z = a.get("zones") or {}
    out: list[dict] = []
    bands = (("trim_zone", "trim"),)
    if with_zones:
        bands = (("accumulation_zone_1", "z1"), ("accumulation_zone_2", "z2")) + bands
    for key, label in bands:
        v = z.get(key)
        if isinstance(v, list) and len(v) == 2 and all(v):
            out.append({"level": float(v[0]), "kind": "card", "evidence": f"{label}下沿"})
            out.append({"level": float(v[1]), "kind": "card", "evidence": f"{label}上沿"})
    for key, label in (("no_chase_above", "no_chase"), ("stop_level", "stop")):
        v = z.get(key)
        if v:
            out.append({"level": float(v), "kind": "card", "evidence": label})
    for blk, label in ((a.get("next_regime") or {}, "next_gate"),
                       (a.get("last_regime") or {}, "repair_gate")):
        g = blk.get("entry_gate") or blk.get("repair_gate") or {}
        lv = g.get("level") if isinstance(g, dict) else None
        if lv:
            out.append({"level": float(lv), "kind": "card", "evidence": label})
    return out


def build_lines(hist: list[dict], atr20: float, ticker: str | None = None) -> list[dict]:
    """锚集合 (§7.1)。hist = 事件日**之前**的全部 bar。"""
    lines: list[dict] = []
    prof_bars = hist[-PARAMS["profile_bars"]:]
    spot = float(prof_bars[-1]["close"])
    atr14_series = ta.atr(prof_bars, 14)
    atr14 = atr14_series[-1] if atr14_series and atr14_series[-1] else atr20
    bucket = ta.auto_bucket(spot, atr14)
    prof = ta.volume_profile(prof_bars, bucket)
    # 每条带只取**朝向现价的那个边**: 远端的边价格要先穿过整条带才够得到, 不是独立的锚
    for band in top_hvn(prof, PARAMS["hvn_top_n"]):
        near_edge = band["lo"] if spot <= band["lo"] else band["hi"] if spot >= band["hi"] else None
        if near_edge is not None:
            lines.append({"level": near_edge, "kind": "profile", "evidence": "HVN边"})
    for band in find_lvn(prof, bucket, PARAMS["lvn_poc_frac"]):
        near_edge = band["lo"] if spot <= band["lo"] else band["hi"] if spot >= band["hi"] else None
        if near_edge is not None:
            lines.append({"level": near_edge, "kind": "profile", "evidence": "LVN边"})

    piv_bars = hist[-PARAMS["pivot_bars"]:]
    sh, sl = ta.swing_points(piv_bars, wing=2)
    pivots = ([{"price": p, "date": str(piv_bars[i]["time"])[:10], "kind": "high"} for i, p in sh]
              + [{"price": p, "date": str(piv_bars[i]["time"])[:10], "kind": "low"} for i, p in sl])
    for c in cluster_pivots(pivots, tol=0.5 * atr20):
        # touches≥3 或 高低互换簇 (support-turned-resistance) —— 后者 level_map 已标为强位
        if c["touches"] >= PARAMS["pivot_touches_min"] or c["kinds"] == "high+low":
            lines.append({"level": c["center"], "kind": "pivot",
                          "evidence": f"pivot×{c['touches']}({c['kinds']})"})

    ath = max(float(b["close"]) for b in hist)
    lines.append({"level": ath, "kind": "pivot", "evidence": "ATH_close"})

    near = PARAMS["near_atr"] * atr20
    cards = [dict(c) for c in CARD_LINES.get(ticker or "", []) if abs(c["level"] - spot) <= near]
    lines = [ln for ln in lines if abs(ln["level"] - spot) <= near]
    lines.sort(key=lambda ln: abs(ln["level"] - spot))
    qp, qf = PARAMS["quota_pivot"], PARAMS["quota_profile"]
    if qp or qf:
        # 按锚类型分名额: 防止数量多的 pivot 簇把 profile 边挤光 (§11.3-2)
        kept, cnt = [], {"pivot": 0, "profile": 0}
        cap = {"pivot": qp or PARAMS["max_lines"], "profile": qf or PARAMS["max_lines"]}
        for ln in lines:
            k = ln["kind"]
            if k in cnt and cnt[k] >= cap[k]:
                continue
            if k in cnt:
                cnt[k] += 1
            kept.append(ln)
            if len(kept) >= PARAMS["max_lines"]:
                break
        lines = kept
    # 卡片锚**不受 max_lines 名额限制**—— 它们是本系统自己画的线, 最不该被 pivot 挤掉
    return cards + lines[:PARAMS["max_lines"]], bucket


def dedupe(lines: list[dict], tol: float) -> list[dict]:
    """同一 tol 内的锚合并成一条 (否则一根 bar 在三条线上产生三个事件 = 人为放大 N)。"""
    out: list[dict] = []
    for ln in sorted(lines, key=lambda x: x["level"]):
        if out and abs(ln["level"] - out[-1]["level"]) <= tol:
            out[-1]["evidence"] += "|" + ln["evidence"]
            out[-1]["kind"] = out[-1]["kind"] if out[-1]["kind"] == ln["kind"] else "mixed"
            continue
        out.append(dict(ln))
    return out


def classify_effort_result(relvr: float | None, range_n: float | None,
                           close_pos: float | None) -> str:
    e = "?" if relvr is None else ("hi" if relvr >= PARAMS["effort_hi"]
                                   else "lo" if relvr <= PARAMS["effort_lo"] else "mid")
    r = "?" if range_n is None else ("hi" if range_n >= PARAMS["result_hi"]
                                     else "lo" if range_n <= PARAMS["result_lo"] else "mid")
    c = "?" if close_pos is None else ("weak" if close_pos <= PARAMS["close_weak"]
                                       else "strong" if close_pos >= PARAMS["close_strong"] else "mid")
    return f"E{e}/R{r}/{c}"


def scan_ticker(ticker: str, bars: list[dict], bench: dict[str, dict[str, float]],
                window: int) -> list[dict]:
    """返回该票在最后 `window` 根 bar 上检测到的事件。"""
    n = len(bars)
    warmup = max(PARAMS["pivot_bars"], PARAMS["profile_bars"]) + 25
    start = max(warmup, n - window)
    if start >= n - PARAMS["k_confirm"] - 1:
        return []

    atr_series = ta.atr(bars, 20)
    vr_series = ta.volume_ratio([int(b.get("volume") or 0) for b in bars], 20)
    bmap = bench["SMH"] if ticker in SEMI else bench["QQQ"]
    bname = "SMH" if ticker in SEMI else "QQQ"

    events: list[dict] = []
    lines: list[dict] = []
    bucket = 0.0
    recent_cross: dict[float, int] = {}   # level → 最近一次 E2 的 idx (给 E4 用)
    last_event: dict[float, int] = {}     # level → 最近一次事件的 idx (debounce)

    for i in range(start, n - PARAMS["k_confirm"]):
        atr20 = atr_series[i - 1] if atr_series[i - 1] else None
        if not atr20:
            continue
        if not lines or (i - start) % PARAMS["rebuild_every"] == 0:
            lines, bucket = build_lines(bars[:i], atr20, ticker)   # 严格用 i 之前的 bar
            lines = dedupe(lines, max(PARAMS["dedupe_atr_mult"] * atr20, bucket))

        bar, prev, nxt = bars[i], bars[i - 1], bars[i + PARAMS["k_confirm"]]
        hi, lo, close = float(bar["high"]), float(bar["low"]), float(bar["close"])
        pclose, nclose = float(prev["close"]), float(nxt["close"])
        day = str(bar["time"])[:10]
        rng = hi - lo
        range_n = rng / atr20 if atr20 else None
        close_pos = (close - lo) / rng if rng > 0 else None
        vr = vr_series[i]
        b = bmap.get(day)
        relvr = (vr / max(b, 0.4)) if (vr is not None and b) else vr
        cell = classify_effort_result(relvr, range_n, close_pos)
        tol = max(PARAMS["tol_atr_mult"] * atr20, bucket)

        for ln in lines:
            L = ln["level"]
            key = round(L, 4)
            if i - last_event.get(key, -10**6) < PARAMS["debounce_days"]:
                continue          # 边沿触发 + 冷却: 同一条线上的震荡不重复计事件
            side = "below" if pclose < L else "above"
            ev = None
            if side == "below":
                if close > L + tol:                      # 收盘站上带外
                    ev = "E2_accept" if nclose > L - tol else "E3_trap"
                elif hi > L + tol and close < L:         # 盘中穿出带外又被收回
                    ev = "E3_trap" if nclose < L - tol else None
                elif hi >= L and close < L - tol:        # **真的够到了线**才算触碰
                    ev = "E1_reject" if nclose < L - tol else None
            else:
                if close < L - tol:
                    ev = "E2_accept" if nclose < L + tol else "E3_trap"
                elif lo < L - tol and close > L:
                    ev = "E3_trap" if nclose > L + tol else None
                elif lo <= L and close > L + tol:
                    ev = "E1_reject" if nclose > L + tol else None
            # E4 易位: 过去 m 日内该线发生过 E2, 今日回踩带内并守住
            if ev is None:
                last_x = recent_cross.get(key)
                if (last_x is not None and 0 < i - last_x <= PARAMS["flip_lookback"]
                        and lo <= L + tol and hi >= L - tol):
                    holds = (close >= L - tol) if side == "above" else (close <= L + tol)
                    ev = "E4_flip" if holds else None
            if ev == "E2_accept":
                recent_cross[key] = i
            if ev:
                last_event[key] = i
                events.append({"ticker": ticker, "date": day, "level": round(L, 2),
                               "kind": ln["kind"], "evidence": ln["evidence"][:40],
                               "side": side, "event": ev, "cell": cell,
                               "relvr": round(relvr, 2) if relvr else None,
                               "range_n": round(range_n, 2) if range_n else None,
                               "bench": bname})
    return events


def merge_same_day(events: list[dict]) -> list[dict]:
    """同票、同日、同事件类型、同方向合并为一次交互。

    单根趋势 bar 可能连续穿越多条相邻线；穿越数量属于强度信息，写入
    ``lines`` 字段，不能重复计为多次事件。
    """
    groups: dict[tuple, list[dict]] = {}
    for e in events:
        groups.setdefault((e["ticker"], e["date"], e["event"], e["side"]), []).append(e)
    out = []
    for (tk, d, ev, side), g in groups.items():
        g.sort(key=lambda x: x["level"])
        head = dict(g[0] if side == "above" else g[-1])   # 最"远"的那条 = 走到了哪
        head["lines"] = len(g)
        head["levels"] = [x["level"] for x in g]
        head["evidence"] = " ; ".join(dict.fromkeys(x["evidence"] for x in g))[:80]
        out.append(head)
    return sorted(out, key=lambda x: (x["date"], x["ticker"]))


def live_bars(ticker: str) -> list[dict]:
    """Production-card 序列由深度缓存和常规缓存按日期去重合并。"""
    deep = load_bars(ticker)
    cache: list[dict] = []
    p = KLINE_CACHE_DIR / f"{ticker}.jsonl"
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if line:
                try:
                    cache.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    bars, _warn = merge_bars_by_date(deep, cache)
    return bars


def live_report(days: int) -> None:
    """live 预览: 用真卡锚跑最近 N 个交易日, 渲染 [FYI] 表 (§9.1 的样子)。

    这**不是**live 任务, 只是让方案长什么样看得见 —— 参数未锁、未预注册。"""
    pos, wl = read_positions(allow_missing=True), read_watchlist(allow_missing=True)
    cards: dict[str, dict] = {}
    for doc, bucket in ((pos, "positions"), (wl, "watchlist")):
        for t, body in (doc.get("tickers") or {}).items():
            if (body.get("instrument") or {}).get("type") == "option":
                continue
            lines = card_lines(body)
            if lines:
                cards.setdefault(t, {"lines": lines, "bucket": bucket})
    if not cards:
        print("没有带 zones 的活跃正股卡"); return
    for t, c in cards.items():
        CARD_LINES[t] = c["lines"]

    bench = {"QQQ": bench_ratio_map("QQQ"), "SMH": bench_ratio_map("SMH")}
    rows: list[dict] = []
    for t in sorted(cards):
        bars = live_bars(t)
        if len(bars) < 300:
            print(f"⚠️ {t}: 只有 {len(bars)} 根 bar, 跳过 (锚需要 ≥300)"); continue
        rows.extend(scan_ticker(t, bars, bench, days))
    rows = merge_same_day(rows)
    if not rows:
        print(f"最近 {days} 个交易日: 无交互事件"); return
    print(f"[FYI] 交互事件 — 观察层, 零授权效力   (最近 {days} 交易日, {len(cards)} 张卡)")
    NAME = {"E1_reject": "触碰-被拒", "E2_accept": "穿越-被接受",
            "E3_trap": "穿越-被拒(陷阱)", "E4_flip": "触碰-被接受(易位)"}
    for d in sorted({r["date"] for r in rows}):
        print(f"\n── {d}")
        for r in sorted([x for x in rows if x["date"] == d], key=lambda x: x["ticker"]):
            span = f"×{r['lines']}线" if r.get("lines", 1) > 1 else "     "
            print(f"  {r['ticker']:<6} {NAME[r['event']]:<16} @{r['level']:<8}{span} "
                  f"力 RelVR {r['relvr']} / 果 {r['range_n']}N / {r['cell']} ({r['side']})")
            print(f"         └ {r['evidence']}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", nargs="*", help="默认 = 深度缓存全池")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 只 (冒烟用)")
    ap.add_argument("--window", type=int, default=1260, help="每票回看多少根 bar (默认 ~5 年)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--dump", help="事件明细写 jsonl")
    ap.add_argument("--live", action="store_true",
                    help="live 预览: 用真卡锚跑最近 --days 天, 渲染 [FYI] 表")
    ap.add_argument("--days", type=int, default=10, help="--live 回看交易日数")
    ap.add_argument("--set", nargs="*", default=[], metavar="K=V",
                    help="覆盖 PARAMS (敏感性用), 例: --set max_lines=12 debounce_days=5")
    a = ap.parse_args()

    for kv in a.set:
        k, v = kv.split("=", 1)
        if k not in PARAMS:
            raise SystemExit(f"未知参数 {k}")
        PARAMS[k] = type(PARAMS[k])(float(v)) if isinstance(PARAMS[k], int) else float(v)

    if a.live:
        live_report(a.days)
        return

    universe = a.tickers or sorted(p.stem for p in KLINE_DEEP_DIR.glob("*.jsonl"))
    universe = [t for t in universe if t not in {"QQQ", "SPY", "SMH"}]
    if a.limit:
        universe = universe[:a.limit]

    bench = {"QQQ": bench_ratio_map("QQQ"), "SMH": bench_ratio_map("SMH")}
    if not bench["QQQ"]:
        print("⚠️ 缺 QQQ 深度缓存 → RelVR 退化为绝对量比 (降级已说出口)", file=sys.stderr)

    all_events: list[dict] = []
    per_ticker: dict[str, dict] = {}
    for t in universe:
        bars = load_bars(t)
        if len(bars) < 400:
            continue
        evs = merge_same_day(scan_ticker(t, bars, bench, a.window))
        span = min(a.window, len(bars)) / 252.0
        per_ticker[t] = {"events": len(evs), "years": round(span, 1),
                         "per_year": round(len(evs) / span, 1) if span else 0,
                         "ticker_days": len({e["date"] for e in evs})}
        all_events.extend(evs)

    by_type = Counter(e["event"] for e in all_events)
    by_kind = Counter(e["kind"] for e in all_events)
    by_cell = Counter(e["cell"] for e in all_events)
    per_year = [v["per_year"] for v in per_ticker.values() if v["years"]]
    per_year.sort()

    def pct(c: Counter) -> dict:
        tot = sum(c.values()) or 1
        return {k: f"{v} ({100*v/tot:.1f}%)" for k, v in c.most_common()}

    summary = {
        "universe": len(per_ticker), "events": len(all_events),
        "events_per_ticker_year": {
            "median": per_year[len(per_year)//2] if per_year else None,
            "p10": per_year[int(0.1*len(per_year))] if per_year else None,
            "p90": per_year[int(0.9*len(per_year))] if per_year else None,
            "max": per_year[-1] if per_year else None,
        },
        "by_event": pct(by_type), "by_anchor_kind": pct(by_kind),
        "top_cells": dict(list(pct(by_cell).items())[:8]),
        "params": PARAMS,
    }
    if a.dump:
        Path(a.dump).write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in all_events))
    if a.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return
    print(f"池 {summary['universe']} 票 / 事件 {summary['events']}")
    print(f"每票每年事件数: p10 {summary['events_per_ticker_year']['p10']} / "
          f"中位 {summary['events_per_ticker_year']['median']} / "
          f"p90 {summary['events_per_ticker_year']['p90']} / "
          f"max {summary['events_per_ticker_year']['max']}")
    print(f"按事件类型: {summary['by_event']}")
    print(f"按锚类型:   {summary['by_anchor_kind']}")
    print(f"力/果格 top: {summary['top_cells']}")


if __name__ == "__main__":
    main()
