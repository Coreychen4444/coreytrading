#!/usr/bin/env python3
"""calibrate.py — 估值预测 vs 实际的对账与校准 (财报后对账 + 历史「当时」重估回测)。

为什么存在 (用户 第二轮): 「财报后对账校准」。估值模型的每个参数都是声明值,
不对账 = 永远不知道它偏在哪。两条路径, 同一套对账函数:

  reconcile  账本 `valuations.jsonl` 里的每条预测, 等它之后的财报落地 (基本面缓存出现更新的季度),
             把实现值 (TTM 收入增速年化 / TTM 营业利润率 / 核心 EPS / 价格相对 SPY 的超额) 与预测
             (基准路径插值 + 熊/牛情景点) 对上, 落 `calibration.jsonl` (kind=reconcile)。
  backtest   把 OpenD 50 季历史当时间机器: 每个季度末 + 45 天 (财报大致公布时点) 只用**当时已知**的
             季度 + 当时收盘价 (深度日线) 重估一次, 再对 1–4 季后的实际对账 (kind=backtest)。
             今天就有 200+ 个样本, 不用等下季财报。回测里 PE 通道无历史 (OpenD 只给当前 5Y 通道),
             只跑引擎 A; rf/ERP 用常量 (声明), 无 overlay 无宏观 —— 校准的是**基本面路径**的偏差,
             价格侧 (判定 vs 前向超额) 只作参考。

对账口径 (与模型情景同一把尺):
  实现增速  = (TTM 收入_now / TTM 收入_then)^(4/季数) − 1   vs   预测 = 基准路径同期年化增速
  实现利润率 = TTM 营业利润率_now                              vs   预测 = 路径在该时点的利润率
  归类 = 最近的情景点 (熊/基/牛 = 基准 ∓ Δ), 超出熊−Δ/2 或 牛+Δ/2 为「出界」;
  Brier 型得分 = 分配给实现归类的概率 (出界 = 0); 偏差 = 实现 − 基准 (符号有意义: 正 = 模型太保守)。

判据 (设计 §10 预注册): 收入趋势归一化 (revenue_method=trend) 采纳当且仅当 semi_cyclical 组在 4 季
前向上 (增速覆盖率 ↑ 或 |偏差| ↓) 且 利润率覆盖率不降; 否则保留为可选覆盖不默认。

🚨 零授权效力: 本层不被 sizing/entry_guard/exit_guard import, 不写 positions/watchlist。

CLI:
    .venv/moomoo/bin/python3 quant/valuation/calibrate.py backtest --tickers MU LITE [--years 4] [--horizon 4]
        [--variant actual|trend|both] [--json] [--write]
    .venv/moomoo/bin/python3 quant/valuation/calibrate.py reconcile [--tickers ...] [--refresh] [--json] [--write]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 直接 `python3 quant/<层>/<模块>.py` 时让 `quant.*` 可 import

import argparse
import json
import math
import statistics
from datetime import date, timedelta

from quant.core.io_json import append_jsonl, iter_jsonl  # noqa: E402
from quant.core.paths import VALUATION_CALIBRATION_PATH, VALUATIONS_PATH  # noqa: E402
from quant.core.timeutil import et_today, now_et_iso  # noqa: E402
from quant.valuation import archetypes, fundamentals, model  # noqa: E402

BACKTEST_RF = 0.045          # 回测常量 (声明): 校准的是基本面路径, 不是利率
BACKTEST_ERP = 0.045
PUBLICATION_LAG_DAYS = 45    # 季度末 → 财报公布 的近似滞后; 回测「当时」只用 period_end ≤ as_of − lag 的季度
QUARTER_DAYS = 91
BANDS = ("below_bear", "bear", "base", "bull", "above_bull")


# ── 价格 ────────────────────────────────────────────────────────────

load_prices, price_on = fundamentals.load_prices, fundamentals.price_on   # 价格读取归原料层, 这里保留旧名


def beta_asof(prices_t: dict[str, float], prices_m: dict[str, float], day: str) -> dict | None:
    """v2: 5 年月度回归 (as-of 截断), 与 live 同口径。"""
    return model.beta_from_monthly(prices_t, prices_m, as_of=day)


# ── 「当时」的 bundle ────────────────────────────────────────────────

def bundle_as_of(bundle: dict, as_of: str, price: float, *, lag_days: int = PUBLICATION_LAG_DAYS) -> dict:
    """只保留当时已公布的季度 (period_end ≤ as_of − lag); 快照换成当时价; PE 通道/目标价置空 (无历史 = 不偷看)。"""
    cutoff = (date.fromisoformat(as_of) - timedelta(days=lag_days)).isoformat()
    rows = [r for r in bundle["quarters"] if (r.get("period_end") or "") <= cutoff]
    shares = None
    return {"ticker": bundle["ticker"], "fetched_at": bundle.get("fetched_at"), "source": f"as_of {as_of} (回测截断)",
            "quarters": rows,
            "snapshot": {"last_price": price, "update_time": as_of, "total_market_val": None, "outstanding_shares": shares},
            "valuation_pe": None, "forward": None, "consensus": None}   # 无历史一致预期 = 回测跑「无一致预期」模式


# ── 对账核心 ────────────────────────────────────────────────────────

def _ttm(rows: list[dict], k: int, field: str) -> float | None:
    return model.ttm_at(rows, k, field)


def realized_between(rows: list[dict], then_period_end: str, upto: str) -> dict | None:
    """then (预测时最新季 period_end) → upto 之间落地的季度: 实现增速年化 / TTM 利润率 / 核心 EPS 代理。"""
    idx_then = max((i for i, r in enumerate(rows) if (r.get("period_end") or "") <= then_period_end), default=None)
    idx_now = max((i for i, r in enumerate(rows) if (r.get("period_end") or "") <= upto), default=None)
    if idx_then is None or idx_now is None or idx_now <= idx_then or idx_then < 3:
        return None
    q = idx_now - idx_then
    rev_then, rev_now = _ttm(rows, idx_then, "revenue"), _ttm(rows, idx_now, "revenue")
    op_now = _ttm(rows, idx_now, "operating_profit")
    if not rev_then or not rev_now or rev_then <= 0 or rev_now <= 0 or op_now is None:
        return None
    return {"quarters": q, "years": q / 4, "period_end_then": then_period_end, "period_end_now": rows[idx_now]["period_end"],
            "growth_annualized": (rev_now / rev_then) ** (4 / q) - 1, "opm": op_now / rev_now,
            "revenue_ttm_now": rev_now, "revenue_ttm_then": rev_then}


def predicted_at(spec: dict, years: float) -> dict:
    """基准路径在 years 处的年化增速 (从 0 到 years 的收入几何年化) 与利润率 (线性插值)。"""
    path = model.project(spec)
    t = max(1e-9, years)
    lo = int(math.floor(t))
    rev0 = spec["revenue_base"]
    if lo == 0:
        rev_t = rev0 + (path[0]["revenue"] - rev0) * t
        m_t = spec["margin_start"] + (path[0]["margin"] - spec["margin_start"]) * t
    else:
        r_lo = path[min(lo, len(path)) - 1]
        r_hi = path[min(lo + 1, len(path)) - 1]
        f = t - lo
        rev_t = r_lo["revenue"] + (r_hi["revenue"] - r_lo["revenue"]) * f
        m_t = r_lo["margin"] + (r_hi["margin"] - r_lo["margin"]) * f
    return {"growth_annualized": (rev_t / rev0) ** (1 / t) - 1, "opm": m_t}


def classify(realized: float, base: float, delta: float) -> str:
    if delta <= 0:
        return "base"
    x = realized - base
    if x < -1.5 * delta:
        return "below_bear"
    if x < -0.5 * delta:
        return "bear"
    if x <= 0.5 * delta:
        return "base"
    if x <= 1.5 * delta:
        return "bull"
    return "above_bull"


def score_band(band: str, probs: dict) -> float:
    return float(probs.get(band, 0.0)) if band in ("bear", "base", "bull") else 0.0


def reconcile_prediction(spec: dict, scenario_deltas: dict, probs: dict, rows_full: list[dict],
                         then_period_end: str, upto: str) -> dict | None:
    real = realized_between(rows_full, then_period_end, upto)
    if real is None:
        return None
    pred = predicted_at(spec, real["years"])
    dg, dm = float(scenario_deltas.get("growth", 0.0)), float(scenario_deltas.get("margin", 0.0))
    bg = classify(real["growth_annualized"], pred["growth_annualized"], dg)
    bm = classify(real["opm"], pred["opm"], dm)
    return {**real, "pred_growth": pred["growth_annualized"], "pred_opm": pred["opm"],
            "bias_growth": real["growth_annualized"] - pred["growth_annualized"], "bias_opm": real["opm"] - pred["opm"],
            "band_growth": bg, "band_opm": bm, "score_growth": score_band(bg, probs), "score_opm": score_band(bm, probs),
            "delta_growth": dg, "delta_margin": dm}


# ── 回测 ────────────────────────────────────────────────────────────

def backtest_ticker(bundle: dict, resolved: dict, *, prices: dict[str, float], spy: dict[str, float],
                    years: int = 4, horizon_q: int = 4, rf: float = BACKTEST_RF, erp: float = BACKTEST_ERP,
                    as_of_end: str | None = None) -> list[dict]:
    """每个季度末+lag 当「当时」重估 → 对 horizon_q 季后实际对账。返回逐点行 (缺价/算不出的点跳过并记 reason)。"""
    rows_full = bundle["quarters"]
    ends = [r["period_end"] for r in rows_full if r.get("period_end")]
    if not ends:
        return []
    last_end = as_of_end or ends[-1]
    horizon_days = horizon_q * QUARTER_DAYS
    out: list[dict] = []
    for pe in ends:
        as_of = (date.fromisoformat(pe) + timedelta(days=PUBLICATION_LAG_DAYS)).isoformat()
        upto = (date.fromisoformat(as_of) + timedelta(days=horizon_days)).isoformat()
        if upto > last_end:
            continue
        if (date.fromisoformat(last_end) - date.fromisoformat(pe)).days > years * 365 + horizon_days:
            continue
        px = price_on(prices, as_of)
        if not px:
            out.append({"ticker": bundle["ticker"], "as_of": as_of, "skipped": "无价格"})
            continue
        b = bundle_as_of(bundle, as_of, px[1])
        try:
            res = model.valuate(b, resolved, rf=rf, erp=erp, beta=beta_asof(prices, spy, as_of), price=px[1],
                                overlays={"applied": [], "ignored": [], "flags": []}, macro_context=None)
        except ValueError as e:
            out.append({"ticker": bundle["ticker"], "as_of": as_of, "skipped": f"模型: {e}"})
            continue
        rec = reconcile_prediction(res["spec"], res["scenario_deltas"], res["scenarios"]["probabilities"], rows_full,
                                   res["drivers"]["latest_period_end"], upto)
        if rec is None:
            out.append({"ticker": bundle["ticker"], "as_of": as_of, "skipped": "对账季度不足"})
            continue
        px_now, spy_then, spy_now = price_on(prices, upto), price_on(spy, as_of), price_on(spy, upto)
        ret = (px_now[1] / px[1] - 1) if px_now else None
        spy_ret = (spy_now[1] / spy_then[1] - 1) if (spy_then and spy_now) else None
        out.append({
            "kind": "backtest", "ticker": bundle["ticker"], "archetype": res["archetype"], "as_of": as_of, "upto": upto,
            "model_version": res.get("model_version"),
            "horizon_q": horizon_q, "variant": resolved["overrides"].get("revenue_method", resolved["params"].get("revenue_method") or "actual"),
            "price": px[1], "fair_value": res["fair_value"], "verdict": res["verdict"]["label"], "ratio": res["verdict"]["ratio"],
            "confidence": res["confidence"]["tier"], "growth_start": res["growth_start"], "target_margin": res["target_margin"],
            "revenue_level_ratio": res["spec"]["revenue_level_ratio"], "quarters_used": res["drivers"]["quarters_used"],
            "price_return": ret, "spy_return": spy_ret, "excess_return": (ret - spy_ret) if (ret is not None and spy_ret is not None) else None,
            **{k: rec[k] for k in ("quarters", "growth_annualized", "opm", "pred_growth", "pred_opm", "bias_growth", "bias_opm",
                                   "band_growth", "band_opm", "score_growth", "score_opm", "delta_growth", "delta_margin")},
        })
    return out


# ── 财报后对账 (账本) ─────────────────────────────────────────────────

def latest_ledger_rows(path: Path | None = None) -> dict[str, dict]:
    """每票最新一条估值 (含 spec 的那种)。"""
    out: dict[str, dict] = {}
    for r in iter_jsonl(Path(path or VALUATIONS_PATH)):
        if r.get("skipped") or not r.get("spec"):
            continue
        out[r["ticker"]] = r
    return out


def reconcile_ledger(rows: dict[str, dict], bundles: dict[str, dict], prices: dict[str, dict[str, float]],
                     spy: dict[str, float], today: str) -> list[dict]:
    out = []
    for t, row in sorted(rows.items()):
        b = bundles.get(t)
        if not b:
            out.append({"ticker": t, "pending": "无基本面缓存"})
            continue
        then_end = (row.get("drivers") or {}).get("latest_period_end")
        deltas = row.get("scenario_deltas") or archetypes.ARCHETYPES.get(row.get("archetype"), {}).get("scenario") or {}
        probs = (row.get("scenarios") or {}).get("probabilities") or {"bear": 0.25, "base": 0.5, "bull": 0.25}
        rec = reconcile_prediction(row["spec"], deltas, probs, b["quarters"], then_end, today) if then_end else None
        if rec is None:
            out.append({"ticker": t, "pending": f"预测于 {row['timestamp'][:10]} (最新季 {then_end}), 之后还没有新季度落地"})
            continue
        px_now = price_on(prices.get(t, {}), today)
        spy_then, spy_now = price_on(spy, row["timestamp"][:10]), price_on(spy, today)
        ret = (px_now[1] / row["price"] - 1) if (px_now and row.get("price")) else None
        spy_ret = (spy_now[1] / spy_then[1] - 1) if (spy_then and spy_now) else None
        out.append({"kind": "reconcile", "ticker": t, "archetype": row.get("archetype"), "model_version": row.get("model_version", "v1"), "as_of": row["timestamp"][:10],
                    "upto": today, "price": row.get("price"), "fair_value": row.get("fair_value"), "verdict": row.get("verdict"),
                    "price_return": ret, "spy_return": spy_ret,
                    "excess_return": (ret - spy_ret) if (ret is not None and spy_ret is not None) else None, **rec})
    return out


# ── 汇总 ────────────────────────────────────────────────────────────

def aggregate(rows: list[dict], key: str = "archetype") -> dict:
    """按 key 分组: n / 增速与利润率的覆盖率 (未出界比例) / 偏差均值与中位 / Brier 型得分 / 判定 vs 超额。"""
    groups: dict[str, list[dict]] = {}
    for r in rows:
        if r.get("skipped") or r.get("pending"):
            continue
        groups.setdefault(str(r.get(key)), []).append(r)
        groups.setdefault("ALL", []).append(r)
    out = {}
    for g, rs in groups.items():
        def cov(field):
            return sum(1 for r in rs if r[field] in ("bear", "base", "bull")) / len(rs)
        def m(field):
            xs = [r[field] for r in rs if r.get(field) is not None]
            return (statistics.mean(xs), statistics.median(xs)) if xs else (None, None)
        bg, bm = m("bias_growth"), m("bias_opm")
        verdicts: dict[str, list[float]] = {}
        for r in rs:
            if r.get("excess_return") is not None:
                verdicts.setdefault(r.get("verdict") or "?", []).append(r["excess_return"])
        out[g] = {"n": len(rs), "coverage_growth": cov("band_growth"), "coverage_opm": cov("band_opm"),
                  "bias_growth_mean": bg[0], "bias_growth_median": bg[1], "bias_opm_mean": bm[0], "bias_opm_median": bm[1],
                  "score_growth": statistics.mean(r["score_growth"] for r in rs), "score_opm": statistics.mean(r["score_opm"] for r in rs),
                  "bands_growth": {b: sum(1 for r in rs if r["band_growth"] == b) for b in BANDS},
                  "bands_opm": {b: sum(1 for r in rs if r["band_opm"] == b) for b in BANDS},
                  "verdict_excess": {v: {"n": len(xs), "mean": statistics.mean(xs), "median": statistics.median(xs)} for v, xs in verdicts.items()}}
    return out


def _pct(x, d=1):
    return "—" if x is None else f"{x*100:+.{d}f}%"


def render_aggregate(agg: dict, title: str) -> str:
    lines = [f"== {title} =="]
    for g, a in sorted(agg.items(), key=lambda kv: (kv[0] != "ALL", kv[0])):
        lines.append(f"[{g}] n={a['n']} · 增速覆盖 {a['coverage_growth']:.0%} 偏差 均{_pct(a['bias_growth_mean'])} 中{_pct(a['bias_growth_median'])} 得分 {a['score_growth']:.2f}"
                     f" · 利润率覆盖 {a['coverage_opm']:.0%} 偏差 均{_pct(a['bias_opm_mean'])} 中{_pct(a['bias_opm_median'])} 得分 {a['score_opm']:.2f}")
        lines.append(f"      归类 增速 {a['bands_growth']} · 利润率 {a['bands_opm']}")
        if a["verdict_excess"]:
            lines.append("      判定→前向超额(vs SPY): " + " · ".join(f"{v} n={s['n']} 均{_pct(s['mean'])} 中{_pct(s['median'])}" for v, s in sorted(a["verdict_excess"].items())))
    return "\n".join(lines)


def write_rows(rows: list[dict], path: Path | None = None) -> int:
    """append-only, 按 (kind, ticker, as_of, horizon/upto, variant) 去重。"""
    p = Path(path or VALUATION_CALIBRATION_PATH)
    seen = {r.get("id") for r in iter_jsonl(p)} if p.exists() else set()
    n = 0
    for r in rows:
        if r.get("skipped") or r.get("pending"):
            continue
        rid = f"{r['kind']}-{r['ticker']}-{r['as_of']}-{r.get('horizon_q') or r.get('upto')}-{r.get('variant', '')}-{r.get('model_version', 'v1')}"
        if rid in seen:
            continue
        append_jsonl(p, {"id": rid, "written_at": now_et_iso(), **r}, lock=False)
        seen.add(rid)
        n += 1
    return n


# ── CLI ─────────────────────────────────────────────────────────────

def _tickers(args, inputs: dict) -> list[str]:
    t = [x.upper() for x in (args.tickers or [])]
    if args.all or not t:
        t = sorted(k for k, e in (inputs.get("tickers") or {}).items() if e.get("archetype") != "not_applicable")
    return t


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="估值对账校准: 财报后对账 / 历史当时重估回测 (零授权效力)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("backtest", "reconcile"):
        sp = sub.add_parser(name)
        sp.add_argument("--tickers", nargs="*")
        sp.add_argument("--all", action="store_true")
        sp.add_argument("--json", action="store_true")
        sp.add_argument("--write", action="store_true")
        sp.add_argument("--refresh", action="store_true", help="先从 OpenD 重拉基本面")
        if name == "backtest":
            sp.add_argument("--years", type=int, default=4)
            sp.add_argument("--horizon", type=int, default=4, help="前向季数")
            sp.add_argument("--variant", choices=("actual", "trend", "trend_r2_0.3", "both", "default"), default="default",
                            help="收入基数口径; default = 各原型缺省; both = 周期票跑 actual / trend / trend_r2_0.3 三种 (裁 §10)")
    a = ap.parse_args(argv)
    inputs = archetypes.load_inputs()
    tickers = _tickers(a, inputs)
    if a.refresh:
        fundamentals.refresh(tickers)
    spy = load_prices("SPY")
    today = et_today()
    all_rows: list[dict] = []
    if a.cmd == "backtest":
        for t in tickers:
            b = fundamentals.load_bundle(t)
            if not b:
                print(f"✗ {t}: 无基本面缓存 (先跑 valuate --refresh)", file=sys.stderr)
                continue
            base = archetypes.resolve(t, inputs)
            variants = [None]
            if a.variant in ("actual", "trend", "trend_r2_0.3"):
                variants = [a.variant]
            elif a.variant == "both":
                variants = ["actual", "trend", "trend_r2_0.3"] if base["archetype"] == "semi_cyclical" else [None]
            for v in variants:
                r = dict(base)
                r["overrides"] = dict(base["overrides"])
                if v:
                    r["overrides"]["revenue_method"] = "trend" if v.startswith("trend") else "actual"
                    if v == "trend_r2_0.3":
                        r["overrides"]["trend_min_r2"] = 0.3
                rows_v = backtest_ticker(b, r, prices=load_prices(t), spy=spy, years=a.years, horizon_q=a.horizon)
                for row in rows_v:
                    row["variant"] = v or row.get("variant")
                all_rows += rows_v
        good = [r for r in all_rows if not r.get("skipped")]
        if a.json:
            print(json.dumps({"rows": all_rows, "aggregate": {v: aggregate([r for r in good if r["variant"] == v])
                                                              for v in sorted({r["variant"] for r in good})}}, ensure_ascii=False, default=str))
        else:
            for v in sorted({r["variant"] for r in good}):
                print(render_aggregate(aggregate([r for r in good if r["variant"] == v]), f"回测 · 收入口径 {v} · 前向 {a.horizon} 季 · 近 {a.years} 年"))
                print()
            skipped = [r for r in all_rows if r.get("skipped")]
            if skipped:
                print(f"跳过 {len(skipped)} 点: " + "; ".join(f"{r['ticker']} {r['as_of']} {r['skipped']}" for r in skipped[:8]))
    else:
        rows = latest_ledger_rows()
        rows = {t: r for t, r in rows.items() if t in tickers}
        bundles = {t: fundamentals.load_bundle(t) for t in rows}
        prices = {t: load_prices(t) for t in rows}
        all_rows = reconcile_ledger(rows, bundles, prices, spy, today)
        done = [r for r in all_rows if r.get("kind") == "reconcile"]
        if a.json:
            print(json.dumps({"rows": all_rows, "aggregate": aggregate(done)}, ensure_ascii=False, default=str))
        else:
            for r in all_rows:
                if r.get("pending"):
                    print(f"⏳ {r['ticker']}: {r['pending']}")
                else:
                    print(f"✓ {r['ticker']} {r['as_of']}→{r['upto']} ({r['quarters']} 季): 增速 实 {_pct(r['growth_annualized'])} 预 {_pct(r['pred_growth'])} [{r['band_growth']}]"
                          f" · 利润率 实 {_pct(r['opm'])} 预 {_pct(r['pred_opm'])} [{r['band_opm']}] · 判定 {r['verdict']} 超额 {_pct(r['excess_return'])}")
            if done:
                print(render_aggregate(aggregate(done), "财报后对账汇总"))
    if a.write:
        n = write_rows(all_rows)
        print(f"\n校准账本 → {VALUATION_CALIBRATION_PATH.name} (+{n})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
