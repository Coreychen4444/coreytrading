#!/usr/bin/env python3
"""valuate.py — 估值 CLI: 基本面缓存 + 原型 + 市场参数 → 每票公允价/隐含增速/判定 → 账本。

为什么存在 (用户): 「大致算出股价, 判断高估还是低估」。本模块是编排与渲染:
  1. 原料   `fundamentals.load_bundle` (缺/过期 → `--refresh` 从 OpenD 重拉)
  2. 原型   `archetypes.resolve` (人工区 inputs.json: 商业模式 + 覆盖 + 增量新闻 overlay)
  3. 市场   rf = ai_news 宏观快照的 US10Y (FRED DGS10, 只读; 情报层收拢后本仓不自拉宏观),
            ERP 参数 (声明) + 宏观风险环境机械加点 (current.json), β 从 K 线缓存对 SPY 回归
  3b. 新闻  `ai_news/valuation/overlays.jsonl` (雷达经 overlays.py add 写) + inputs.json 手写条目
            → `overlays.active_overlays` 按票/到期/可靠度筛出当时有效的, 交给模型分四段应用;
            应用了哪些、忽略了哪些 (可靠度不够/到期) 逐条进输出与账本
  4. 模型   `model.valuate` (纯函数)
  5. 出口   文本报告 / `--json` / `--write` 追加 `trade/us_stock/valuation/valuations.jsonl`
            (每行 = 一次预测的完整输入快照, 财报后可对账 → 校准)

🚨 零授权效力: 本层不被 sizing/entry_guard/exit_guard import, 不写 positions/watchlist,
不产出股数/金额/cap。低估不是 GO, 高估不是 SELL —— 入场归 zone/gate, 退出归退出栈。
估值只回答「市场在为什么定价、我信不信」, 给选股侧 (用户 的 edge) 与卡片 thesis 当数字底座。

CLI:
    .venv/moomoo/bin/python3 quant/valuation/valuate.py --tickers GOOGL MU [--refresh] [--json] [--write]
    .venv/moomoo/bin/python3 quant/valuation/valuate.py --all            # inputs.json 里的全部
    参数: --rf 0.0475 (缺省读宏观快照) --erp 0.045 --max-age-hours 24
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 直接 `python3 quant/<层>/<模块>.py` 时让 `quant.*` 可 import

import argparse
import json

from quant.core.earnings import load_calendar  # noqa: E402
from quant.core.io_json import append_jsonl, iter_jsonl, read_json  # noqa: E402
from quant.core.paths import (KLINE_CACHE_DIR, MACRO_CURRENT_PATH, MACRO_SNAPSHOTS_DIR,  # noqa: E402
                              VALUATION_OVERLAYS_PATH, VALUATIONS_PATH)
from quant.core.timeutil import et_today, now_et_iso  # noqa: E402
from quant.valuation import archetypes, fundamentals, model, overlays  # noqa: E402

DEFAULT_ERP = 0.0423         # 股权风险溢价 = Damodaran 美股隐含 ERP 4.23% (ERP 2026 Edition, SSRN 6361419)
FALLBACK_RF = 0.045          # 宏观快照缺失时的兜底 (必须播报)
BETA_BENCHMARK = "SPY"
DEFAULT_MAX_AGE_HOURS = 24.0


# ── 市场参数 ────────────────────────────────────────────────────────

def risk_free_from_macro(snapshots_dir: Path | None = None) -> dict:
    """最新宏观快照的 US10Y (FRED DGS10)。找不到 → 兜底值并写明。"""
    d = Path(snapshots_dir or MACRO_SNAPSHOTS_DIR)
    files = sorted(d.rglob("*.json")) if d.exists() else []
    for p in reversed(files):
        try:
            doc = read_json(p)
            m = (doc.get("metrics") or {}).get("US10Y") or {}
            v = m.get("value")
            if isinstance(v, (int, float)) and 0 < v < 20:
                return {"rf": float(v) / 100.0, "basis": f"{p.name} US10Y {v:.2f}% as_of {m.get('as_of')} ({(m.get('source') or {}).get('identifier')})"}
        except Exception:  # noqa: BLE001 — 坏文件跳过找上一份
            continue
    return {"rf": FALLBACK_RF, "basis": f"⚠️ 宏观快照无 US10Y, 兜底 {FALLBACK_RF:.2%}"}


def macro_context_from_current(path: Path | None = None) -> dict | None:
    """ai_news/macro/current.json → {risk_level, risk_score, run_id, generated_at, next_tier1_day}。缺/坏 → None。"""
    p = Path(path or MACRO_CURRENT_PATH)
    if not p.exists():
        return None
    try:
        d = read_json(p)
    except Exception:  # noqa: BLE001
        return None
    re_ = d.get("risk_environment") or {}
    nxt = d.get("next_event") or {}
    return {"risk_level": re_.get("level"), "risk_score": re_.get("score"), "run_id": d.get("run_id"),
            "generated_at": (d.get("generated_at") or {}).get("et") or d.get("generated_at"),
            "next_tier1_day": (str(nxt.get("scheduled_at_et") or "")[:10] or None)
            if nxt.get("status") in (None, "upcoming") else None}


def next_print_day(ticker: str, calendar: dict | None) -> str | None:
    row = ((calendar or {}).get("tickers") or {}).get(ticker.upper()) or {}
    day = str(row.get("next_reaction_day") or "")[:10]
    return day or None


def load_klines(ticker: str, kdir: Path | None = None) -> list[dict]:
    p = Path(kdir or KLINE_CACHE_DIR) / f"{ticker.upper()}.jsonl"
    return list(iter_jsonl(p)) if p.exists() else []


def beta_for(ticker: str, kdir: Path | None = None, deep_dir: Path | None = None) -> dict | None:
    """v2: 5 年月度回归 (深度日线 + 缓存合并); 不足 36 个月 → None (资本成本退回行业 β)。"""
    pt = fundamentals.load_prices(ticker, deep_dir=deep_dir, cache_dir=kdir)
    pm = fundamentals.load_prices(BETA_BENCHMARK, deep_dir=deep_dir, cache_dir=kdir)
    return model.beta_from_monthly(pt, pm)


# ── 渲染 ────────────────────────────────────────────────────────────

def _b(x: float | None, unit: float = 1e9, suffix: str = "B") -> str:
    return "—" if x is None else f"${x/unit:,.1f}{suffix}"


def _pct(x: float | None, digits: int = 1) -> str:
    return "—" if x is None else f"{x*100:+.{digits}f}%" if x < 0 else f"{x*100:.{digits}f}%"


def _px(x: float | None) -> str:
    return "—" if x is None or x != x else f"${x:,.2f}"


def render(res: dict) -> str:
    t = res["ticker"]
    if res.get("skipped"):
        return f"━━ {t} ━━ 不适用: {res['reason']}"
    d, c, b, sc = res["drivers"], res["cost_of_capital"], res["base"], res["scenarios"]
    dcf, epe, imp, v, cf = b["dcf"], b["eps_pe"], res["implied"], res["verdict"], res["confidence"]
    gc = d["growth_candidates"]
    lines = [
        f"━━ {t} ━━ 现价 {_px(res['price'])} · 原型 {res['archetype']} ({res['archetype_label']}) · "
        f"数据 {d['quarters_used']} 季 {d['history_span']}",
        f"TTM: 收入 {_b(d['revenue_ttm'])} (yoy {_pct(gc['yoy'])} · 3y {_pct(gc['cagr3'])} · 5y {_pct(gc['cagr5'])} · 10y {_pct(gc['cagr10'])})"
        f" · 营业利润率 {_pct(d['opm_ttm'])} (历史中位 {_pct(d['opm_median'])}, 分位 {_pct(d['opm_percentile'],0)})"
        f" · 税率 {_pct(d['tax_rate'],0)}",
        f"EPS: 核心 {_px(d['eps_core_ttm'])} vs 报表 {_px(d['eps_reported_ttm'])} (一次性占税前 {_pct(d['oneoff_share_of_pretax'],0)})"
        f" · 核心 PE {res['price']/d['eps_core_ttm']:.1f}×" if d.get("eps_core_ttm") and d["eps_core_ttm"] > 0 else
        f"EPS: 核心 {_px(d['eps_core_ttm'])} vs 报表 {_px(d['eps_reported_ttm'])} (核心为负, PE 无意义)",
        f"资产负债表: 现金短投 {_b(d['cash_sti'])} · 长投 {_b(d['lt_investments'])} · 有息债 {_b(d['debt'])} → 净现金 {_b(d['net_cash_ex_leases'])}"
        f" · 投入资本 {_b(d['invested_capital'])} (剔商誉 {_b(d['invested_capital_ex_goodwill'])}) · ROIC {_pct(d['roic_ttm'],0)} / 剔商誉 {_pct(d['roic_ex_goodwill'],0)} (中位 {_pct(d['roic_median'],0)}) · 销售/资本 {d['sales_to_capital']:.2f}"
        f" · capex/收入 {_pct(d['capex_intensity'],0)} · FCF {_b(d['fcf_ttm'])} · 回购收益率 {_pct(d['buyback_yield'])}",
        f"资本成本: rf {_pct(c['rf'],2)} + β {c['beta']:.2f} × ERP {_pct(c['erp'],1)} = r_e {_pct(c['cost_of_equity'],2)} · WACC {_pct(c['wacc'],2)}"
        f" ({c['beta_basis']})",
        f"锚点(年1): 收入 {res['anchor']['revenue_basis']} · 利润率 {res['anchor']['margin_basis']}"
        + (f" · 前瞻 PE {res['anchor']['forward_pe']:.1f}×" if res['anchor'].get('forward_pe') else "")
        + (f" · EV/前瞻收入 {res['anchor']['ev_to_forward_sales']:.1f}×" if res['anchor'].get('ev_to_forward_sales') else ""),
        f"路径: 年2起 g {_pct(res['growth_start'])} ← {res['growth_basis']} → gT {_pct(res['spec']['terminal_growth'])} 过 {res['spec']['horizon_years']} 年 ({res['spec']['fade']})"
        f" · 利润率 {_pct(res['spec']['margin_start'])} → {_pct(res['target_margin'])} ({res['margin_basis']}, {res['spec']['margin_fade_years']} 年)"
        + (f" · 收入水平 → {res['spec']['revenue_level_ratio']:.2f}× ({res['revenue_basis']})" if res['spec'].get('revenue_level_ratio', 1.0) != 1.0 else ""),
        f"引擎A FCF-DCF: EV {_b(dcf['ev'])} · 终值占 {_pct(dcf['tv_share'],0)} · RONIC_T {_pct(dcf['ronic_terminal'],0)} → 每股 {_px(dcf['per_share'])}"
        f" · 久期 {dcf['duration_years']:.1f} 年 · WACC +100bp → {_pct(dcf['rate_sensitivity_100bp']) if dcf['per_share'] > 1.0 else 'n/a (每股≈0)'}" if dcf.get("duration_years") else
        f"引擎A FCF-DCF: EV {_b(dcf['ev'])} → 每股 {_px(dcf['per_share'])} (久期无定义)",
    ]
    st = b.get("street")
    if st:
        gap = res["anchor"].get("dcf_vs_street")
        lines.append(f"引擎B 卖方一致目标价: {st['basis']} → 每股 {_px(st['per_share'])} (熊/牛 = 低/高 {_px(st['per_share_bear'])}/{_px(st['per_share_bull'])})"
                     + (f" · DCF 相对街上 {_pct(gap,0)}" if gap is not None else ""))
    else:
        lines.append("引擎B 卖方一致目标价: 无 (<3 家), 权重 0")
    sec = res["anchor"].get("sector") or {}
    cross = []
    if epe:
        cross.append(f"路径 EPS_T {_px(epe['eps_T'])} × 自身 5Y PE {epe['pe_T']:.1f} → {_px(epe['per_share'])}")
    if res["anchor"].get("forward_pe") and sec.get("pe_median5y"):
        cross.append(f"前瞻 PE {res['anchor']['forward_pe']:.1f}× vs 板块 {sec.get('plate_name')} 5Y 中位 {sec['pe_median5y']:.1f}×")
    if res["anchor"].get("ev_to_forward_sales") and sec.get("ps_median5y"):
        cross.append(f"EV/前瞻收入 {res['anchor']['ev_to_forward_sales']:.1f}× vs 板块 PS 5Y 中位 {sec['ps_median5y']:.1f}×")
    if cross:
        lines.append("交叉: " + " · ".join(cross))
    w = b["weights"]
    lines.append(f"加权 (DCF {w['fcf_dcf']:.0%} / 街 {w['street']:.0%}): 基准 {_px(sc['base'])} · 熊 {_px(sc['bear'])} ({sc['probabilities']['bear']:.0%})"
                 f" / 牛 {_px(sc['bull'])} ({sc['probabilities']['bull']:.0%}) → 概率加权公允 {_px(res['fair_value'])}")
    inv = []
    N = imp["horizon_years"]
    if not imp.get("growth_creates_value"):
        inv.append("增长在基准 ROIC 下不创造价值 (NOPAT率×销售资本比 < WACC) → 隐含 CAGR 无意义, 看利润率")
    elif imp.get("cagr") is not None:
        inv.append(f"现价要求年2起 {imp.get('cagr_years', N-1)} 年 CAGR {_pct(imp['cagr'])} (基准路径 {_pct(imp['base_cagr'])}, 差 {_pct(imp['vs_base_cagr'])})"
                   f" → 第 {N} 年收入 {_b(imp.get('revenue_year_N'))}")
    else:
        inv.append("隐含 CAGR 无解 (−50%…+300% 都到不了现价)")
    if imp.get("target_margin") is not None:
        inv.append(f"隐含终点营业利润率 {_pct(imp['target_margin'])} (基准 {_pct(res['target_margin'])})")
    elif imp.get("margin_unreachable"):
        inv.append("隐含利润率无解 (终点利润率 95% 也到不了现价)")
    else:
        inv.append("隐含利润率无解")
    if imp.get("cagr_at_current_margin") is not None and (abs(res["target_margin"] - res["spec"]["margin_start"]) > 0.02
                                                          or res["spec"].get("revenue_level_ratio", 1.0) != 1.0):
        inv.append(f"若峰值不回归 (利润率维持年1 {_pct(res['spec']['margin_start'])}): 要求年2起 CAGR {_pct(imp['cagr_at_current_margin'])}"
                   f" → 第 {N} 年收入 {_b(imp.get('revenue_year_N_at_current_margin'))}")
    lines.append("反向: " + " · ".join(inv))
    low = " (低置信: 公允价当量级看, 以「反向」行为主)" if cf["tier"] == "低" else ""
    nw = res.get("news") or {}
    if nw:
        parts = []
        for o in nw.get("applied") or []:
            d_ = o["delta"]
            dtxt = f"×{1+d_:.3f}" if o["driver"] in ("revenue_base", "pe_multiplier") else (
                f"{d_:+,.0f}" if o["driver"] == "net_interest_after_tax" else f"{d_:+.2%}")
            parts.append(f"{o['date']} {o['channel']}:{o['driver']} {dtxt} [{o['reliability']}, 至 {o.get('resolved_until') or '—'}] {o['note']}")
        head = f"增量新闻: {len(nw.get('applied') or [])} 条生效"
        if nw.get("ignored"):
            head += f" · {len(nw['ignored'])} 条未用 ({'; '.join(i['id'] + ': ' + i['reason'] for i in nw['ignored'][:3])})"
        lines.append(head + (" — " + " | ".join(parts) if parts else ""))
        lines.append(f"  宏观→PE: {nw.get('erp_tilt_basis')} · overlay Δrf {_pct(nw.get('rf_delta') or 0,2)} Δerp {_pct(nw.get('erp_delta') or 0,2)}"
                     f" · PE 倍数 ×{nw.get('pe_mult', 1.0):.3f} · 情景概率 熊/基/牛 {nw['probabilities']['bear']:.0%}/{nw['probabilities']['base']:.0%}/{nw['probabilities']['bull']:.0%}")
    lines.append(f"判定: 现价 vs 公允 {_pct(v['ratio'])} → **{v['label']}**{low} · 置信 {cf['tier']} (原型 {res['archetype']}"
                 + (f"; {'; '.join(cf['reasons'])}" if cf["reasons"] else "") + ")")
    if res.get("flags"):
        lines.append("旗标: " + " | ".join(res["flags"]))
    cons = res.get("consensus")
    if cons:
        lines.append(f"[FYI] 卖方目标价 低/均/高 {_px(cons['lowest'])}/{_px(cons['average'])}/{_px(cons['highest'])} ({cons['total']} 家, {cons.get('update_time_str')})")
    ph = res.get("pe_hist")
    if ph and ph.get("current") is not None:
        pctl = ph.get("percentile")
        lines.append(f"[FYI] OpenD 报表 PE {ph['current']} · 5Y 分位 {pctl:.0f}% · forward {ph.get('forward')}" if isinstance(pctl, (int, float))
                     else f"[FYI] OpenD 报表 PE {ph['current']} · forward {ph.get('forward')}")
    lines.append("出处: " + " ; ".join(res["provenance"]))
    return "\n".join(lines)


def digest(results: list[dict], *, rf: dict | None = None, erp: float | None = None, macro: dict | None = None) -> str:
    """周复盘 / Telegram 一览: 一票一行, 按 现价/公允 由低到高 (最便宜的在前 = 选股侧视角)。
    只有判断层字段: 判定·置信·公允 vs 现价·反向隐含·生效新闻数; 不出股数/金额/cap, 尾行钉零授权。"""
    live = [r for r in results if not r.get("skipped") and r.get("fair_value")]
    live.sort(key=lambda r: (r["verdict"]["ratio"] if r["verdict"]["ratio"] is not None else 1e9))
    mc = macro or {}
    head = "📐 估值一览 (DCF 与卖方目标价各半, 判断层)"
    ctx = []
    if rf:
        ctx.append(f"rf {rf['rf']:.2%}")
    if erp is not None:
        ctx.append(f"ERP {erp:.2%}")
    if mc.get("risk_level"):
        ctx.append(f"宏观 {mc['risk_level']}")
    lines = [head + (" · " + " · ".join(ctx) if ctx else "")]
    for r in live:
        v, cf, imp = r["verdict"], r["confidence"], r["implied"]
        n_news = len((r.get("news") or {}).get("applied") or [])
        st = (r.get("base") or {}).get("street")
        parts = [f"{r['ticker']} {v['label']} {_pct(v['ratio'],0)} · 置信{cf['tier']}",
                 f"公允 {_px(r['fair_value'])} vs {_px(r['price'])}"
                 + (f" (DCF {_px((r['base'] or {}).get('dcf', {}).get('per_share'))} / 街 {_px(st['per_share'])})" if st else "")]
        if imp.get("growth_creates_value") is False:
            parts.append("增长不创造价值, 看利润率")
        elif imp.get("cagr") is not None:
            parts.append(f"要求年2起 CAGR {_pct(imp['cagr'],0)} (基准 {_pct(imp['base_cagr'],0)})")
        if imp.get("cagr_at_current_margin") is not None and abs(r["target_margin"] - r["spec"]["margin_start"]) > 0.02:
            parts.append(f"峰值不回归需 {_pct(imp['cagr_at_current_margin'],0)}")
        if n_news:
            parts.append(f"新闻 {n_news} 条")
        lines.append(" · ".join(parts))
    skipped = [r["ticker"] for r in results if r.get("skipped")]
    if skipped:
        lines.append("不适用: " + ", ".join(skipped))
    lines.append("零授权: 低估≠GO 高估≠SELL, 入场归 zone/gate, 退出归退出栈; 低置信票看「要求 CAGR」不看公允价")
    return "\n".join(lines)


def path_table(res: dict) -> str:
    rows = res["base"]["path"]
    out = ["  年  增速   利润率   收入      NOPAT    再投资   FCF     EPS"]
    for r in rows:
        out.append(f"  {r['year']:>2}  {r['growth']*100:5.1f}%  {r['margin']*100:5.1f}%  {_b(r['revenue']):>8}  {_b(r['nopat']):>7}  "
                   f"{_b(r['reinvest']):>7}  {_b(r['fcf']):>7}  {_px(r['eps'])}")
    return "\n".join(out)


# ── 账本 ────────────────────────────────────────────────────────────

def ledger_row(res: dict, rf_basis: str) -> dict:
    """账本行 = 判定 + 复算所需的全部输入 (spec/drivers 快照), 不含逐年路径 (可从 spec 复算)。"""
    if res.get("skipped"):
        return {"timestamp": now_et_iso(), "ticker": res["ticker"], "skipped": True, "reason": res["reason"]}
    b = res["base"]
    return {
        "timestamp": now_et_iso(), "ticker": res["ticker"], "model_version": res.get("model_version"), "price": res["price"], "price_time": res.get("price_time"),
        "archetype": res["archetype"], "fair_value": res["fair_value"], "verdict": res["verdict"]["label"],
        "ratio": res["verdict"]["ratio"], "confidence": res["confidence"]["tier"],
        "scenarios": res["scenarios"], "dcf_per_share": b["dcf"]["per_share"],
        "eps_pe_per_share": b["eps_pe"]["per_share"] if b["eps_pe"] else None,
        "street": ({k: b["street"].get(k) for k in ("target_avg", "target_low", "target_high", "n", "as_of", "per_share")} if b.get("street") else None),
        "weights": b["weights"], "dcf_vs_street": res["anchor"].get("dcf_vs_street"), "sector": res["anchor"].get("sector"),
        "implied_cagr": res["implied"].get("cagr"), "implied_target_margin": res["implied"].get("target_margin"),
        "implied_cagr_at_current_margin": res["implied"].get("cagr_at_current_margin"),
        "news": {k: (res.get("news") or {}).get(k) for k in ("applied", "ignored", "rf_delta", "erp_delta", "erp_tilt", "pe_mult", "probabilities", "macro_context")},
        "growth_creates_value": res["implied"].get("growth_creates_value"),
        "scenario_deltas": res.get("scenario_deltas"), "revenue_basis": res.get("revenue_basis"), "anchor": res.get("anchor"),
        "growth_basis": res.get("growth_basis"), "margin_basis": res.get("margin_basis"),
        "duration_years": b["dcf"].get("duration_years"), "rate_sensitivity_100bp": b["dcf"].get("rate_sensitivity_100bp"),
        "tv_share": b["dcf"].get("tv_share"), "spec": res["spec"], "cost_of_capital": res["cost_of_capital"],
        "rf_basis": rf_basis, "drivers": res["drivers"], "flags": res["flags"], "provenance": res["provenance"],
        "fundamentals_fetched_at": res.get("fundamentals_fetched_at"),
        "eps_T": b["eps_pe"]["eps_T"] if b["eps_pe"] else None,
        "revenue_year1": b["path"][0]["revenue"] if b["path"] else None,
    }


# ── 编排 ────────────────────────────────────────────────────────────

def run(tickers: list[str], *, inputs: dict, refresh: bool = False, max_age_hours: float | None = DEFAULT_MAX_AGE_HOURS,
        rf: float | None = None, erp: float = DEFAULT_ERP, write: bool = False, source=None,
        cache_dir: Path | None = None, kdir: Path | None = None, snapshots_dir: Path | None = None,
        ledger_path: Path | None = None, overlays_path: Path | None = None, macro_current_path: Path | None = None,
        calendar: dict | None = None, as_of: str | None = None, deep_dir: Path | None = None) -> dict:
    rfi = {"rf": rf, "basis": "参数 --rf"} if rf is not None else risk_free_from_macro(snapshots_dir)
    day = as_of or et_today()
    records = overlays.load_records(overlays_path or VALUATION_OVERLAYS_PATH)
    macro_ctx = macro_context_from_current(macro_current_path)
    cal = calendar if calendar is not None else load_calendar()
    results, errors = [], {}
    written = 0
    live = []
    for t in tickers:
        try:
            r = archetypes.resolve(t, inputs)
        except KeyError as e:
            errors[t] = f"KeyError: {e}"
            continue
        if r["archetype"] == "not_applicable":
            results.append({"ticker": t, "archetype": "not_applicable", "skipped": True, "reason": r["provenance"][0]})
        else:
            live.append(t)
    tickers = live
    stale = [t for t in tickers if refresh or fundamentals.load_bundle(t, cache_dir=cache_dir, max_age_hours=max_age_hours) is None]
    if stale:
        r = fundamentals.refresh(stale, source=source, cache_dir=cache_dir)
        errors.update({t: f"fundamentals: {e}" for t, e in r["errors"].items()})
    for t in tickers:
        if t in errors:
            continue
        try:
            bundle = fundamentals.load_bundle(t, cache_dir=cache_dir)
            if bundle is None:
                raise RuntimeError("无基本面缓存且拉取失败")
            resolved = archetypes.resolve(t, inputs)
            recs = records + overlays.manual_records(t, resolved["news_overlay"])
            act = overlays.active_overlays(recs, ticker=t, archetype=resolved["archetype"], as_of=day,
                                           next_print_day=next_print_day(t, cal),
                                           next_tier1_day=(macro_ctx or {}).get("next_tier1_day"))
            res = model.valuate(bundle, resolved, rf=rfi["rf"], erp=erp, beta=beta_for(t, kdir, deep_dir),
                                overlays=act, macro_context=macro_ctx, forward=bundle.get("forward"))
            res["rf_basis"] = rfi["basis"]
            results.append(res)
            if write:
                append_jsonl(Path(ledger_path or VALUATIONS_PATH), ledger_row(res, rfi["basis"]), lock=False)
                written += 1
        except Exception as e:  # noqa: BLE001 — 单票失败不炸整轮
            errors[t] = f"{type(e).__name__}: {e}"
    return {"rf": rfi, "erp": erp, "results": results, "errors": errors, "written": write, "written_count": written,
            "macro_context": macro_ctx, "overlay_records": len(records)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="估值: FCF-DCF + EPS×PE → 公允价 / 隐含增速 / 高估低估 (零授权效力)")
    ap.add_argument("--tickers", nargs="*", default=[])
    ap.add_argument("--all", action="store_true", help="inputs.json 里的全部 ticker")
    ap.add_argument("--refresh", action="store_true", help="强制从 OpenD 重拉基本面")
    ap.add_argument("--max-age-hours", type=float, default=DEFAULT_MAX_AGE_HOURS)
    ap.add_argument("--rf", type=float, help="无风险利率 (小数); 缺省读 ai_news 宏观快照 US10Y")
    ap.add_argument("--erp", type=float, default=DEFAULT_ERP)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--path", action="store_true", help="附逐年路径表")
    ap.add_argument("--digest", action="store_true", help="只输出一览 (周复盘/Telegram 用, 一票一行)")
    ap.add_argument("--write", action="store_true", help="追加账本 valuations.jsonl")
    a = ap.parse_args(argv)
    inputs = archetypes.load_inputs()
    tickers = [t.upper() for t in a.tickers]
    if a.all:
        tickers = sorted(set(tickers) | set(inputs.get("tickers") or {}))
    if not tickers:
        ap.error("给 --tickers 或 --all")
    out = run(tickers, inputs=inputs, refresh=a.refresh, max_age_hours=a.max_age_hours, rf=a.rf, erp=a.erp, write=a.write)
    if a.digest:
        print(digest(out["results"], rf=out["rf"], erp=out["erp"], macro=out.get("macro_context")))
        for t, e in out["errors"].items():
            print(f"✗ {t}: {e}")
        if out["written"]:
            print(f"账本 +{out['written_count']}")
        return 1 if out["errors"] and not out["results"] else 0
    if a.json:
        slim = {"rf": out["rf"], "erp": out["erp"], "errors": out["errors"], "written": out["written"],
                "results": [{k: v for k, v in r.items() if k != "base"} | ({"dcf": r["base"]["dcf"], "eps_pe": r["base"]["eps_pe"], "street": r["base"].get("street"), "weights": r["base"]["weights"]} if not r.get("skipped") else {})
                            for r in out["results"]]}
        print(json.dumps(slim, ensure_ascii=False, default=str))
        return 0
    mc = out.get("macro_context") or {}
    print(f"rf {out['rf']['rf']:.2%} ← {out['rf']['basis']} · ERP {out['erp']:.2%} · 宏观风险 {mc.get('risk_level') or '无'} "
          f"({mc.get('run_id') or '—'}) · overlay 记录 {out.get('overlay_records', 0)} 条")
    for r in out["results"]:
        print()
        print(render(r))
        if a.path and not r.get("skipped"):
            print(path_table(r))
    for t, e in out["errors"].items():
        print(f"\n✗ {t}: {e}")
    if out["written"]:
        print(f"\n账本 → {VALUATIONS_PATH.relative_to(VALUATIONS_PATH.parents[3])} (+{out['written_count']}; 不适用的票不落账)")
    return 1 if out["errors"] and not out["results"] else 0


if __name__ == "__main__":
    sys.exit(main())
