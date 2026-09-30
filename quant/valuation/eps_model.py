"""eps_model.py — 精简版 EPS 模型包（研究级目标的 EPS 源，替代「全资本闭合」的公司包）。

为什么存在：全资本闭合的公司包（`company_models.json`，每家上百项索偿/资本条目）在财报后很难及时维护，
财报季里全书会陆续变成「按旧包」。研究级 12 个月目标只需要未来 8 季三情景的利润表，所以包只留这些
（只更新指引和未来 8 季利润表）。

每票一份（`trade/valuation/eps_models.json`，私有运行文件；合成形状见 `trade/valuation/eps_models.example.json`）：
  quarters[i] = {id, start, end, revenue / operating_profit / other_pretax / tax_rate / other_after_tax /
                 special_expense / diluted_shares: {downside, base, upside}}
  归母净利 = (营业利润 + 其他税前) × (1 − 税率) + 其他税后项；一次性费用（已在营业利润里）单列，估值时回加（无税盾，
  与公司包口径一致）。NTM EPS = 前四季净利合计 ÷ 前四季稀释股数均值。

三种来源（`origin`）：
  seed:company_package@<known_at>  由现有公司包逐季抄出（数字与公司包一致，不重新判断）
  roll:<期末>                       财报后机械滚动：已报季度出列，后续各季营收/营业利润按营收超预期比例整体缩放
                                    （利润率不变，比例夹在 0.5–2.0），末尾按最后一季同比增速补一季；reviewed=false
  review:<日期>                     日更会话读发行人财报 / 指引（只读 SEC / IR 一手披露，不采新闻）后 `set` + `review`
机械滚动只是让数字跟上已报实际，不是预测判断；未复核的票在研究级输出里挂 ⚠️。零授权效力。

CLI:
  .venv/moomoo/bin/python3 quant/valuation/eps_model.py show [--ticker T]
  .venv/moomoo/bin/python3 quant/valuation/eps_model.py roll            # 按最新基本面缓存机械滚动（写盘）
  .venv/moomoo/bin/python3 quant/valuation/eps_model.py set --ticker T --quarter ID \\
      [--revenue D B U] [--op-margin D B U] [--tax-rate R] [--shares N] [--special D B U]
  .venv/moomoo/bin/python3 quant/valuation/eps_model.py review --ticker T --source URL [--source URL] --note "..."
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import argparse
import json
from datetime import date, datetime, timedelta, timezone

from quant.core.io_json import atomic_write_json, read_json, state_lock
from quant.core.paths import FUNDAMENTALS_DIR, VALUATION_EPS_MODELS_PATH
from quant.core.valuation_contracts import finite

SCENARIOS = ("downside", "base", "upside")
FIELDS = ("revenue", "operating_profit", "other_pretax", "tax_rate", "other_after_tax",
          "special_expense", "diluted_shares")
MATCH_DAYS = 10          # 券商期末日期未核（常差 1–3 天），容差
RATIO_CLAMP = (0.5, 2.0)
HISTORY_KEEP = 12


def _day(value):
    return date.fromisoformat(str(value)[:10])


def _now():
    return datetime.now(timezone.utc).isoformat()


# ── 计算 ────────────────────────────────────────────────────────────

def quarter_income(q, scenario):
    pretax = q["operating_profit"][scenario] + q["other_pretax"][scenario]
    return pretax * (1 - q["tax_rate"][scenario]) + q["other_after_tax"][scenario]


def ntm(model):
    """前四季三情景 EPS（GAAP，一次性费用回加后）+ 窗口元数据；季度不足四个 → None。"""
    quarters = (model or {}).get("quarters") or []
    if len(quarters) < 4:
        return None
    window = quarters[:4]
    eps, addback = {}, {}
    for s in SCENARIOS:
        shares = sum(q["diluted_shares"][s] for q in window) / 4
        if not shares or shares <= 0:
            eps[s], addback[s] = None, 0.0
            continue
        income = sum(quarter_income(q, s) for q in window)
        special = sum(q["special_expense"][s] for q in window)
        eps[s] = (income + special) / shares
        addback[s] = special / shares
    return {"window": "next_four_quarters", "start": window[0]["start"], "end": window[-1]["end"],
            "earnings_basis": "gaap", "basis_note": None, "adjusted_definition": None, "eps": eps,
            "special_addback_per_share": addback, "first_quarter_end": window[0]["end"],
            "origin": model.get("origin"), "reviewed": bool(model.get("reviewed")),
            "known_at": model.get("known_at")}


# ── 播种：公司包 → 精简包 ─────────────────────────────────────────────

def seed_from_research(ticker, research, package, *, known_at):
    """company_model.build 的三情景季度行（GAAP）+ 包内一次性费用 → 精简包。"""
    scen = (research or {}).get("scenarios") or {}
    if any(not (scen.get(s) or {}).get("quarters") for s in SCENARIOS):
        return None
    specials = {}
    for sc in (package or {}).get("scenarios") or []:
        for q in sc.get("quarters") or []:
            total = sum(item["expense"] for item in ((q.get("cash") or {}).get("income_cash_timing") or {}).values()
                        if isinstance(item, dict) and item.get("expense_is_in_ebit") and finite(item.get("expense")))
            specials[(sc.get("id"), q.get("id"))] = total
    ids = [q["id"] for q in scen["base"]["quarters"]]
    by = {s: {q["id"]: q for q in scen[s]["quarters"]} for s in SCENARIOS}
    quarters = []
    for qid in ids:
        if any(qid not in by[s] for s in SCENARIOS):
            return None
        row = {"id": qid, "start": by["base"][qid]["start"], "end": by["base"][qid]["end"]}
        for f in FIELDS:
            row[f] = {}
        for s in SCENARIOS:
            q = by[s][qid]
            pretax, tax, parent = q["pretax"], q["tax_expense"], q["parent_income"]
            row["revenue"][s] = q["revenue"]
            row["operating_profit"][s] = q["operating_profit"]
            row["other_pretax"][s] = pretax - q["operating_profit"]
            row["tax_rate"][s] = tax / pretax if pretax else 0.0
            row["other_after_tax"][s] = parent - (pretax - tax)
            row["special_expense"][s] = specials.get((s, qid), 0.0)
            row["diluted_shares"][s] = q["gaap_diluted_shares"]
        quarters.append(row)
    return {"ticker": ticker, "schema": 1, "basis": "gaap", "known_at": known_at,
            "origin": f"seed:company_package@{(package or {}).get('known_at', '?')}", "reviewed": True,
            "last_reported_end": (_day(quarters[0]["start"]) - timedelta(days=1)).isoformat(),
            "sources": [], "notes": ["播种自公司包；数字与公司包一致，未重新判断"], "history": [],
            "quarters": quarters}


# ── 机械滚动：财报出来后已报季度出列 ──────────────────────────────────────

def _next_quarter(last, prev_year, growth_from, growth_to):
    length = _day(last["end"]) - _day(last["start"])
    start = _day(last["end"]) + timedelta(days=1)
    row = {"id": f"roll-{(start + length).isoformat()}", "start": start.isoformat(),
           "end": (start + length).isoformat(), "approx_dates": True}
    for f in FIELDS:
        row[f] = {}
    for s in SCENARIOS:
        base_growth = (growth_to["revenue"][s] / growth_from["revenue"][s]
                       if growth_from["revenue"][s] > 0 and growth_to["revenue"][s] > 0 else 1.0)
        margin = (last["operating_profit"][s] / last["revenue"][s]) if last["revenue"][s] else 0.0
        revenue = prev_year["revenue"][s] * base_growth
        row["revenue"][s] = revenue
        row["operating_profit"][s] = revenue * margin
        for f in ("other_pretax", "tax_rate", "other_after_tax", "diluted_shares"):
            row[f][s] = last[f][s]
        row["special_expense"][s] = 0.0
    return row


def roll(model, fundamentals_quarters, *, now=None):
    """已报季度追上模型首季 → 出列、按营收超预期比例缩放后续、补一季。返回 (新模型, 事件列表)。"""
    events = []
    model = json.loads(json.dumps(model))
    reported = sorted((q for q in fundamentals_quarters or []
                       if q.get("period_end") and finite(q.get("revenue"))), key=lambda q: q["period_end"])
    while model["quarters"]:
        first = model["quarters"][0]
        hit = next((q for q in reported if abs((_day(q["period_end"]) - _day(first["end"])).days) <= MATCH_DAYS
                    and _day(q["period_end"]) > _day(model["last_reported_end"]) + timedelta(days=MATCH_DAYS)), None)
        if hit is None:
            break
        forecast = first["revenue"]["base"]
        ratio = hit["revenue"] / forecast if forecast and forecast > 0 else 1.0
        ratio = min(max(ratio, RATIO_CLAMP[0]), RATIO_CLAMP[1])
        rest = model["quarters"][1:]
        for q in rest:
            for s in SCENARIOS:
                q["revenue"][s] *= ratio
                q["operating_profit"][s] *= ratio
        if len(rest) >= 4:
            rest.append(_next_quarter(rest[-1], rest[-4], rest[-5] if len(rest) >= 5 else rest[-4], rest[-1]))
        model["history"] = (model.get("history") or [])[-(HISTORY_KEEP - 1):] + [
            {"known_at": model.get("known_at"), "origin": model.get("origin"),
             "last_reported_end": model.get("last_reported_end")}]
        model.update(quarters=rest, last_reported_end=hit["period_end"], known_at=now or _now(),
                     origin=f"roll:{hit['period_end']}", reviewed=False)
        events.append({"reported_end": hit["period_end"], "quarter": first["id"],
                       "actual_revenue": hit["revenue"], "forecast_revenue": forecast, "ratio": ratio})
    return model, events


def roll_all(models, fundamentals, *, now=None):
    """{ticker: 模型} × {ticker: 基本面缓存} → (新 models, {ticker: 事件})。无事件的票原样返回。"""
    out, events = dict(models), {}
    for ticker, model in models.items():
        quarters = (fundamentals.get(ticker) or {}).get("quarters")
        new, ev = roll(model, quarters, now=now)
        if ev:
            out[ticker], events[ticker] = new, ev
    return out, events


# ── 存取 / 人工复核 ────────────────────────────────────────────────────

def load(path=VALUATION_EPS_MODELS_PATH):
    try:
        data = read_json(Path(path))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save(models, path=VALUATION_EPS_MODELS_PATH):
    atomic_write_json(Path(path), dict(sorted(models.items())))


def set_quarter(model, quarter_id, *, revenue=None, op_margin=None, tax_rate=None, shares=None, special=None):
    q = next((q for q in model["quarters"] if q["id"] == quarter_id), None)
    if q is None:
        raise ValueError(f"unknown quarter {quarter_id}")
    for i, s in enumerate(SCENARIOS):
        if revenue is not None:
            q["revenue"][s] = float(revenue[i])
        if op_margin is not None:
            q["operating_profit"][s] = q["revenue"][s] * float(op_margin[i])
        elif revenue is not None and q["revenue"][s]:
            pass  # 只改营收时营业利润保持绝对值；要保利润率请同时传 --op-margin
        if tax_rate is not None:
            q["tax_rate"][s] = float(tax_rate)
        if shares is not None:
            q["diluted_shares"][s] = float(shares)
        if special is not None:
            q["special_expense"][s] = float(special[i])
    return model


def review(model, *, sources, note, now=None):
    if not sources:
        raise ValueError("review requires at least one primary source (SEC / IR)")
    model["history"] = (model.get("history") or [])[-(HISTORY_KEEP - 1):] + [
        {"known_at": model.get("known_at"), "origin": model.get("origin"),
         "last_reported_end": model.get("last_reported_end")}]
    stamp = now or _now()
    model.update(reviewed=True, origin=f"review:{stamp[:10]}", known_at=stamp,
                 sources=list(sources), notes=(model.get("notes") or [])[-4:] + [note])
    return model


def _fundamentals(tickers):
    out = {}
    for t in tickers:
        try:
            out[t] = read_json(Path(FUNDAMENTALS_DIR) / f"{t}.json")
        except (OSError, ValueError):
            continue
    return out


def render(models, tickers=None):
    lines = []
    for t in sorted(tickers or models):
        m = models.get(t)
        if not m:
            lines.append(f"{t}  — 无精简包")
            continue
        n = ntm(m)
        eps = " / ".join(f"{n['eps'][s]:.2f}" if n and n["eps"][s] is not None else "?" for s in SCENARIOS)
        lines.append(f"{t}  {m['origin']}{'' if m.get('reviewed') else ' ⚠️未复核'} · 已报至 {m['last_reported_end']}"
                     f" · NTM EPS 下/基/上 {eps}（{n['start']}→{n['end']}）" if n else f"{t}  季度不足")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    show = sub.add_parser("show")
    show.add_argument("--ticker")
    sub.add_parser("roll")
    st = sub.add_parser("set")
    st.add_argument("--ticker", required=True)
    st.add_argument("--quarter", required=True)
    st.add_argument("--revenue", nargs=3, type=float)
    st.add_argument("--op-margin", nargs=3, type=float)
    st.add_argument("--tax-rate", type=float)
    st.add_argument("--shares", type=float)
    st.add_argument("--special", nargs=3, type=float)
    rv = sub.add_parser("review")
    rv.add_argument("--ticker", required=True)
    rv.add_argument("--source", action="append", required=True)
    rv.add_argument("--note", required=True)
    args = parser.parse_args(argv)
    if args.cmd == "show":
        models = load()
        print(render(models, [args.ticker.upper()] if args.ticker else None))
        return 0
    with state_lock():
        models = load()
        if args.cmd == "roll":
            models, events = roll_all(models, _fundamentals(models))
            save(models)
            print(json.dumps(events, ensure_ascii=False, indent=1) if events else "无新财报可滚动")
            return 0
        t = args.ticker.upper()
        if t not in models:
            parser.error(f"{t} 无精简包")
        if args.cmd == "set":
            set_quarter(models[t], args.quarter, revenue=args.revenue, op_margin=args.op_margin,
                        tax_rate=args.tax_rate, shares=args.shares, special=args.special)
            models[t]["reviewed"] = False
        else:
            review(models[t], sources=args.source, note=args.note)
        save(models)
    print(render(models, [t]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
