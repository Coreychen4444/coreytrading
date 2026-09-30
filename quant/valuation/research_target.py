"""research_target.py — 研究级 12 个月目标价（未验证，零授权效力）。

先保证可用、起码有个数字出来：重建验收（独立复核人 + 成熟 EPS 样本 + 全资本路径闭合）在可预见时间内
不可达；估值层本身零授权，所以另开一条研究级出口，与 `releases.py` 的逐公司验收通道并存、互不冒充。

算式（每一项都有独立来源，不从 DCF 路径反解倍数 —— 反解倍数是已知的方法缺陷）：
  EPS   = 精简版 EPS 包（`eps_model.py`，首选；财报后机械滚动 + 复核）三情景前四季 EPS；
          无精简包时退回公司模型（company_models.json 冻结包）三情景的「下四个季度」EPS；
          那正是 12 个月后市场看到的 TTM。包已过期 → 按包自己的冻结时点重算并标「过期」。
  倍数  = 本股自己的历史 TTM PE：季度稀释 EPS（OpenD，拆股已调整）按期末 +45 天视为可知，
          × 同日收盘；只取 TTM > 0 的周样本，近 3 年。
  核心 EPS = 稀释 EPS 扣除证券处置/公允价值损益（8023，按 21% 税后）：大额投资收益会把 TTM 抬高、
          把「当前 PE」压低，前瞻 EPS 却不含它，两者错位会把目标算成大幅下跌。当前 PE 与历史 PE 同用核心口径。
  中枢  = 基准 EPS × 当前 PE（倍数不变，只兑现盈利增长）
  盈利情景 = 下行 / 上行 EPS × 当前 PE
  倍数区间 = 基准 EPS × 本股 3 年 PE 的 p25 / p75（倍数回归）
  一次性项 = 包里发行人指引的、已计入 EBIT 的一次性费用（如法律计提；`income_cash_timing.*.expense`）
          按包内口径（无税盾）逐情景回加；12 个月后市场不按一次性项定价。
  现金视角（FCF Yield + P/E）：TTM FCF 收益率、本股 3 年 FCF 收益率分位、FCF/核心净利。资本开支周期里
          盈利与现金背离（FCF 为负，或 FCF/净利远低于 1）时 PE 目标不被现金支持 —— 只播不改中枢。
          （本股中位 FCF 收益率当锚在周期股上发散，弃用。）
  隐含增长（反向 DCF）：现价 = 未来 10 年核心 EPS 按 g 增长的现值
          + 第 10 年末按永续 3% 的终值，r = 收据 10Y + 模型 ERP（β=1，声明假设）；解出 g =「现价要求盈利每年
          增长多少」，与模型/一致预期增速对照。EPS 当所有者盈利（假定维持性资本开支 ≈ 折旧）。
          不设「零成长底」（EPS÷r）：高成长平台股历史上从未跌到零成长倍数，跌破的多是周期顶盈利的假便宜，
          零成长价位对成长股不是可达的价位。
  口径对齐：公司包按调整后口径给窗时，用同窗季度行按 GAAP 重算，与 GAAP 历史 PE 同尺。
  包被新财报取代：券商已报季度期末 ≥ 包内第一个预测季期末 −10 天 → 仍用旧包但标 `package_superseded`
          （公司包没有人按财报更新时就会发生）。
  前向账本：每轮逐票一行写 `VALUATION_FORWARD_PATH`，12 个月后与实际价格对账。
TTM ≤ 0、正 PE 样本不足 → 不出数（PE 无经济意义），不用行业倍数兜底。
这不是公允价，不是概率区间，没有历史检验；前向冻结只是开始积累样本。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from datetime import date, datetime, timedelta
from statistics import quantiles

from quant.core.io_json import iter_jsonl, read_json
from quant.core.paths import KLINE_CACHE_DIR, KLINE_DEEP_DIR, VALUATION_RUNS_DIR
from quant.core.valuation_contracts import finite

VERSION = "research-target-v2"
EPS_LAG_DAYS = 45          # 期末 → 视为可知（provider available_at 为空，取保守滞后）
HISTORY_YEARS = 3
SAMPLE_EVERY = 5           # 交易日 → 周样本
MIN_POSITIVE_SAMPLES = 52  # 至少一年的正 PE 周样本
MIN_POSITIVE_SHARE = 0.7
CONSENSUS_GAP = 0.25       # 与券商一致预期（口径/期限未核）偏离超过此值即播
CASH_DIVERGENCE = 0.5      # FCF/核心净利 < 0.5 = 盈利与现金背离
IMPLIED_MAX_PE = 250       # 反向 DCF 的基数门: PE 数百到数千是微利基数效应; ~160 仍可能是真实的高增长定价, 保留
MAX_PE_WITHOUT_BAND = 100  # 没有自身倍数历史时，微利票的当前 PE 不能当锚（会把目标算成数倍上涨）
SCENARIOS = ("downside", "base", "upside")
GAIN_TAX_RATE = 0.21       # 证券损益税后化：法定税率，非公司实际税率


def _day(value):
    return date.fromisoformat(str(value)[:10])


def load_closes(ticker, *, until, deep_dir=KLINE_DEEP_DIR, cache_dir=KLINE_CACHE_DIR):
    """日收盘 {date: close}，近端缓存优先；深度日线按重叠日比例接到同一复权框架。"""
    def read(path):
        if not path.exists():
            return {}
        return {_day(r["time"]): float(r["close"]) for r in iter_jsonl(path)
                if finite(r.get("close")) and r["close"] > 0}
    recent, deep = read(Path(cache_dir) / f"{ticker}.jsonl"), read(Path(deep_dir) / f"{ticker}.jsonl")
    out = dict(recent)
    if deep and recent:
        overlap = sorted(set(deep) & set(recent))
        if overlap:
            ratio = recent[overlap[0]] / deep[overlap[0]]
            first = min(recent)
            out.update({d: c * ratio for d, c in deep.items() if d < first})
    elif deep:
        out = deep
    return {d: c for d, c in out.items() if d <= until}


def core_eps(rows):
    """逐季核心 EPS：稀释 EPS − 证券损益×(1−t)/稀释股数。股数 = 净利/EPS，缺则沿用上一季。"""
    out, shares = [], None
    for q in rows:
        eps, ni, gain = q["diluted_eps"], q.get("net_income"), q.get("gain_on_securities")
        if finite(ni) and abs(eps) > 0.01 and ni / eps > 0:
            shares = ni / eps
        if finite(gain) and gain and shares:
            eps = eps - gain * (1 - GAIN_TAX_RATE) / shares
        out.append({**q, "diluted_eps": eps})
    return out


def ttm_eps_series(quarters):
    """[(可知日, TTM 核心稀释 EPS)]，要求连续四个季度都有值。"""
    rows = core_eps(sorted((q for q in quarters or [] if q.get("period_end") and finite(q.get("diluted_eps"))),
                           key=lambda q: q["period_end"]))
    out = []
    for i in range(3, len(rows)):
        window = rows[i - 3:i + 1]
        span = (_day(window[-1]["period_end"]) - _day(window[0]["period_end"])).days
        if span > 300:          # 缺季 → 不拼 TTM
            continue
        out.append((_day(window[-1]["period_end"]) + timedelta(days=EPS_LAG_DAYS),
                    sum(q["diluted_eps"] for q in window)))
    return out


def _shares(rows):
    """逐季稀释股数 = 净利/稀释 EPS（拆股已调整口径），缺则沿用上一季。"""
    out, shares = [], None
    for q in rows:
        eps, ni = q.get("diluted_eps"), q.get("net_income")
        if finite(eps) and finite(ni) and abs(eps) > 0.01 and ni / eps > 0:
            shares = ni / eps
        out.append(shares)
    return out


def ttm_fcf_series(quarters):
    """[(可知日, TTM 每股 FCF, TTM FCF, TTM 核心净利)]，四季连续且都有 fcf。"""
    rows = sorted((q for q in quarters or [] if q.get("period_end") and finite(q.get("diluted_eps"))),
                  key=lambda q: q["period_end"])
    shares = _shares(rows)
    out = []
    for i in range(3, len(rows)):
        window = rows[i - 3:i + 1]
        if (_day(window[-1]["period_end"]) - _day(window[0]["period_end"])).days > 300 or not shares[i]:
            continue
        if any(not finite(q.get("fcf")) for q in window):
            continue
        fcf = sum(q["fcf"] for q in window)
        core = sum((q.get("net_income") or 0) - (q.get("gain_on_securities") or 0) * (1 - GAIN_TAX_RATE)
                   for q in window)
        out.append((_day(window[-1]["period_end"]) + timedelta(days=EPS_LAG_DAYS), fcf / shares[i], fcf, core))
    return out


def fcf_lens(quarters, closes, *, as_of, price):
    series = ttm_fcf_series(quarters)
    known = [r for r in series if r[0] <= as_of]
    if not known or not finite(price) or price <= 0:
        return {"status": "unavailable", "reason": "fcf_history_missing"}
    _, fcf_ps, fcf, core = known[-1]
    out = {"status": "ok", "fcf_per_share": fcf_ps, "fcf_ttm": fcf, "yield_now": fcf_ps / price,
           "conversion": fcf / core if core > 0 else None}
    start = as_of - timedelta(days=365 * HISTORY_YEARS)
    yields = []
    for d in sorted(d for d in closes if start <= d <= as_of)[::SAMPLE_EVERY]:
        prior = [r for r in series if r[0] <= d]
        if prior:
            yields.append(prior[-1][1] / closes[d])
    if len(yields) >= MIN_POSITIVE_SAMPLES:
        out.update(yield_p50_3y=quantiles(yields, n=4)[1],
                   yield_percentile_3y=_percentile_rank(yields, out["yield_now"]))
    return out


IMPLIED_YEARS = 10
TERMINAL_GROWTH = 0.03


def dcf_value(eps, g, rate, *, years=IMPLIED_YEARS, terminal=TERMINAL_GROWTH):
    """EPS 按 g 增长 years 年（全部当所有者盈利）+ 永续 terminal 终值，按 rate 贴现。"""
    value, e = 0.0, eps
    for t in range(1, years + 1):
        e *= 1 + g
        value += e / (1 + rate) ** t
    return value + e * (1 + terminal) / (rate - terminal) / (1 + rate) ** years


def implied_growth(*, core_eps, price, rate):
    """反向 DCF：解 dcf_value(core_eps, g) = price。超出 [−30%, +80%] 报边界。"""
    if not finite(rate) or rate <= TERMINAL_GROWTH or not finite(price) or price <= 0:
        return {"status": "unavailable", "reason": "required_return_missing"}
    if not finite(core_eps) or core_eps <= 0:
        return {"status": "unavailable", "reason": "ttm_eps_not_positive"}
    if price / core_eps > IMPLIED_MAX_PE:   # 微利基数: 解出来的是基数效应不是增长要求
        return {"status": "unavailable", "reason": "earnings_base_too_low"}
    lo, hi = -0.30, 0.80
    if dcf_value(core_eps, hi, rate) < price:
        return {"status": "above_range", "rate": rate, "implied_growth": hi}
    if dcf_value(core_eps, lo, rate) > price:
        return {"status": "below_range", "rate": rate, "implied_growth": lo}
    for _ in range(80):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if dcf_value(core_eps, mid, rate) < price else (lo, mid)
    return {"status": "ok", "rate": rate, "implied_growth": (lo + hi) / 2,
            "years": IMPLIED_YEARS, "terminal_growth": TERMINAL_GROWTH}


def special_addbacks(package, *, start, end):
    """{情景: 窗内一次性费用合计}；包内口径无税盾 → 税前额即税后回加额。"""
    out = {}
    for scen in (package or {}).get("scenarios") or []:
        total = 0.0
        for q in scen.get("quarters") or []:
            if not (start <= q.get("start", "") and q.get("end", "") <= end):
                continue
            for item in ((q.get("cash") or {}).get("income_cash_timing") or {}).values():
                if isinstance(item, dict) and item.get("expense_is_in_ebit") and finite(item.get("expense")):
                    total += item["expense"]
        out[scen.get("id")] = total
    return out


def ttm_at(series, when):
    known = [v for d, v in series if d <= when]
    return known[-1] if known else None


def own_pe_history(quarters, closes, *, as_of):
    series = ttm_eps_series(quarters)
    start = as_of - timedelta(days=365 * HISTORY_YEARS)
    days = sorted(d for d in closes if start <= d <= as_of)[::SAMPLE_EVERY]
    pes, total = [], 0
    for d in days:
        eps = ttm_at(series, d)
        if eps is None:
            continue
        total += 1
        if eps > 0:
            pes.append(closes[d] / eps)
    out = {"window_years": HISTORY_YEARS, "samples": total, "positive": len(pes),
           "eps_lag_days": EPS_LAG_DAYS}
    if len(pes) < MIN_POSITIVE_SAMPLES or not total or len(pes) / total < MIN_POSITIVE_SHARE:
        return {**out, "status": "unavailable", "reason": "positive_pe_history_insufficient"}
    q = quantiles(pes, n=4)
    return {**out, "status": "ok", "p25": q[0], "p50": q[1], "p75": q[2], "values": pes}


def pick_window(windows, *, as_of):
    """优先「下四个季度」；否则取期末最接近 as_of+12 月−滞后 的 GAAP/adjusted 窗。"""
    if not windows:
        return None, None
    if "next_four_quarters" in windows:
        return "next_four_quarters", windows["next_four_quarters"]
    target = as_of + timedelta(days=365 - EPS_LAG_DAYS)
    name = min(windows, key=lambda k: (abs((_day(windows[k]["end"]) - target).days),
                                       windows[k].get("earnings_basis") != "gaap"))
    return name, windows[name]


def _window_rows(scenario, start, end):
    return sorted((q for q in (scenario or {}).get("quarters") or []
                   if start <= q.get("start", "") and q.get("end", "") <= end), key=lambda q: q["start"])


def _gaap_window(scenario, start, end):
    """调整后窗 → 同窗季度行按 GAAP 重算（company_model.earnings_window 同一算式）；失败 → None。"""
    from quant.valuation import company_model
    rows = _window_rows(scenario, start, end)
    try:
        return company_model.earnings_window(rows, [q["id"] for q in rows], "gaap")
    except (ValueError, KeyError, TypeError, ZeroDivisionError):
        return None


def scenario_eps(company_research, *, as_of, package=None):
    """三情景同一窗口的 EPS（GAAP 口径、一次性项已回加）；任一缺失 → 该情景 None。"""
    scen = (company_research or {}).get("scenarios") or {}
    base_windows = (scen.get("base") or {}).get("earnings_windows") or {}
    name, window = pick_window(base_windows, as_of=as_of)
    if window is None:
        return None
    start, end = window.get("start", ""), window.get("end", "")
    specials = special_addbacks(package, start=start, end=end)
    basis = window.get("earnings_basis")
    eps, addback, recomputed = {}, {}, False
    for s in SCENARIOS:
        w = ((scen.get(s) or {}).get("earnings_windows") or {}).get(name) or {}
        if w.get("earnings_basis") == "adjusted":
            gaap = _gaap_window(scen.get(s), start, end)
            if gaap is not None:
                w, recomputed = gaap, True
        raw, shares = w.get("eps"), w.get("weighted_diluted_shares")
        add = specials.get(s, 0.0) / shares if specials.get(s) and finite(shares) and shares > 0 else 0.0
        eps[s] = raw + add if finite(raw) else None
        addback[s] = add
    rows = _window_rows(scen.get("base"), start, end)
    return {"window": name, "start": start, "end": end,
            "earnings_basis": "gaap" if recomputed else basis,
            "basis_note": "gaap_recomputed_from_adjusted_window" if recomputed else None,
            "adjusted_definition": window.get("adjusted_definition"), "eps": eps,
            "special_addback_per_share": addback,
            "first_quarter_end": rows[0]["end"] if rows else None}


def package_superseded(quarters, first_quarter_end):
    """券商已报季度追上包内第一个预测季 → 包被新财报取代（期末日期券商口径未核，留 10 天容差）。"""
    ends = [q["period_end"] for q in quarters or [] if q.get("period_end")]
    if not ends or not first_quarter_end:
        return False
    return _day(max(ends)) >= _day(first_quarter_end) - timedelta(days=10)


def _percentile_rank(values, x):
    return 100.0 * sum(v <= x for v in values) / len(values)


def research_target(ticker, *, company_research, input_state, quarters, price, closes, as_of,
                    package=None, consensus_eps=None, required_return=None, eps_model=None):
    """单票研究级 12 个月目标。所有失败都是带原因的 unavailable，不抛。"""
    as_of_day = _day(as_of)
    out = {"ticker": ticker, "version": VERSION, "as_of": str(as_of), "authority": "analysis_only",
           "validation": "unvalidated", "input_state": input_state, "price": price,
           "fcf": fcf_lens(quarters, closes, as_of=as_of_day, price=price),
           "consensus_eps": consensus_eps if finite(consensus_eps) else None}
    out["implied"] = implied_growth(core_eps=ttm_at(ttm_eps_series(quarters), as_of_day),
                                    price=price, rate=required_return)
    if eps_model:
        from quant.valuation import eps_model as slim
        eps = slim.ntm(eps_model)
        out["input_state"] = f"eps_model:{eps_model.get('origin')}"
    else:
        eps = scenario_eps(company_research, as_of=as_of_day, package=package)
    if not eps or eps["eps"].get("base") is None:
        return {**out, "status": "unavailable", "reason": "company_model_eps_missing"}
    out["eps"] = eps
    if eps["eps"]["base"] <= 0:
        return {**out, "status": "unavailable", "reason": "forward_eps_not_positive"}
    ttm_now = ttm_at(ttm_eps_series(quarters), as_of_day)
    out["ttm_eps"] = ttm_now
    if not finite(price) or price <= 0:
        return {**out, "status": "unavailable", "reason": "price_missing"}
    if ttm_now is None or ttm_now <= 0:
        return {**out, "status": "unavailable", "reason": "ttm_eps_not_positive"}
    hist = own_pe_history(quarters, closes, as_of=as_of_day)
    values = hist.pop("values", None)
    out["pe_history"] = hist
    pe_now = price / ttm_now
    base = eps["eps"]["base"]
    if hist["status"] != "ok" and pe_now > MAX_PE_WITHOUT_BAND:
        return {**out, "status": "unavailable", "reason": "near_breakeven_pe", "pe_now": pe_now}
    flags = [] if hist["status"] == "ok" else ["no_pe_band"]
    if package_superseded(quarters, eps.get("first_quarter_end")):
        flags.append("package_superseded")
    if eps.get("reviewed") is False:
        flags.append("eps_model_unreviewed")
    if eps["earnings_basis"] != "gaap":
        flags.append("eps_basis_adjusted_vs_gaap_pe_history")
    if hist["status"] == "ok" and hist["p75"] / hist["p25"] > 2:
        flags.append("pe_history_dispersed")
    if base / ttm_now > 3 or base / ttm_now < 1 / 3:
        flags.append("eps_step_change")
    elif base / ttm_now > 2:
        flags.append("eps_doubling")
    if any(eps["special_addback_per_share"].values()):
        flags.append("special_items_added_back")
    if out["consensus_eps"] and out["consensus_eps"] > 0 and abs(base / out["consensus_eps"] - 1) > CONSENSUS_GAP:
        flags.append("consensus_gap")
    fcf = out["fcf"]
    if fcf["status"] == "ok" and fcf["fcf_ttm"] <= 0:
        flags.append("fcf_negative")
    elif fcf["status"] == "ok" and fcf.get("conversion") is not None and fcf["conversion"] < CASH_DIVERGENCE:
        flags.append("cash_earnings_divergence")
    target = {
        "center": base * pe_now,
        "eps_scenarios": {s: (v * pe_now if v is not None else None) for s, v in eps["eps"].items()},
        "multiple_band": ({"p25": base * hist["p25"], "p50": base * hist["p50"], "p75": base * hist["p75"]}
                          if hist["status"] == "ok" else None),
    }
    return {**out, "status": "ok", "pe_now": pe_now,
            "pe_now_percentile_3y": _percentile_rank(values, pe_now) if values else None,
            "eps_growth": base / ttm_now - 1, "target_12m": target,
            "upside_center": target["center"] / price - 1, "flags": flags}


def build_all(receipt, frozen_valuation, *, package_builder=None, closes_fn=load_closes, eps_models=None):
    """对一份日更收据逐票出研究级目标。EPS 首选精简包；无精简包时过期公司包按冻结时点重算（不续期、不改包）。"""
    from quant.valuation import company_model
    as_of = receipt["as_of"]
    as_of_day = _day(as_of)
    packages = frozen_valuation.get("company_models") or {}
    fundamentals = frozen_valuation.get("fundamentals") or {}
    market = receipt.get("market") or {}
    rate = (market["rf"] + market["erp"]) if finite(market.get("rf")) and finite(market.get("erp")) else None
    out = {}
    for ticker, row in receipt["tickers"].items():
        research = row.get("company_research")
        state = "current"
        forward = (fundamentals.get(ticker) or {}).get("forward") or {}
        slim = (eps_models or {}).get(ticker)
        if not slim and not (research or {}).get("scenarios") and ticker in packages:
            package = packages[ticker]
            try:
                research = (package_builder or company_model.build)(package, as_of=package["known_at"])
                state = f"expired:frozen_at:{package['known_at'][:10]}"
            except Exception as exc:  # 包本身坏了 → 该票 unavailable，不阻断其他票
                research, state = None, f"package_error:{type(exc).__name__}"
        quote = (row.get("reference_quote") or {}).get("price")
        out[ticker] = research_target(
            ticker, company_research=research, input_state=state,
            quarters=(fundamentals.get(ticker) or {}).get("quarters"),
            price=quote, closes=closes_fn(ticker, until=as_of_day), as_of=as_of,
            package=packages.get(ticker), consensus_eps=forward.get("forward_eps"), required_return=rate,
            eps_model=slim)
    return {"version": VERSION, "as_of": as_of, "authority": "analysis_only", "required_return": rate,
            "required_return_basis": (f"10Y {market.get('rf'):.2%}（{market.get('as_of', '')[:10]}）+ ERP "
                                      f"{market.get('erp'):.2%}，β=1") if rate else None,
            "validation": "unvalidated", "receipt_hash": receipt.get("receipt_hash"),
            "tickers": out,
            "counts": {"ok": sum(r["status"] == "ok" for r in out.values()), "total": len(out)}}


FLAG_SHORT = {
    "fcf_negative": "FCF为负", "cash_earnings_divergence": "FCF<净利一半", "package_superseded": "新财报未入模型",
    "eps_model_unreviewed": "滚动未复核",
    "eps_doubling": "EPS翻倍偏乐观", "eps_step_change": "EPS跳变", "consensus_gap": "偏离一致预期",
    "pe_history_dispersed": "倍数离散", "eps_basis_adjusted_vs_gaap_pe_history": "口径偏高",
}


def fyi_line(r):
    """简报一行（原样挂依据行尾，零授权，不进任何判定）。"""
    implied = implied_text(r)
    tail = (" · " + implied) if implied else ""
    if r.get("status") != "ok":
        return f"📐[FYI] 研究级目标不出数（{REASONS.get(r.get('reason'), r.get('reason'))}）{tail} · 零授权"
    tg = r["target_12m"]
    warn = [FLAG_SHORT[f] for f in r.get("flags") or [] if f in FLAG_SHORT]
    return (f"📐[FYI] 研究级12月 中枢 ${tg['center']:,.0f}（{r['upside_center']:+.0%}，倍数不变）{tail}"
            + (f" · ⚠️{'/'.join(warn)}" if warn else "") + " · 未验证·零授权")


def load_latest(*, root=VALUATION_RUNS_DIR, now=None, max_age_days=3):
    """最近一次日更的研究级结果；失败 / 过旧 / 缺文件 → None（简报不回退旧估值）。"""
    from datetime import timezone
    try:
        job = read_json(Path(root) / "latest.json") or {}   # 缺文件 = None (公开 io_json 约定)
        if job.get("status") not in ("completed", "partial"):
            return None
        target = job.get("research_targets") or (job.get("artifact") and
                                                 str(Path(job["artifact"]).parent / "research_targets.json"))
        age = ((now or datetime.now(timezone.utc)).date() - date.fromisoformat(job["day_sgt"])).days
        if age > max_age_days:
            return None
        result = read_json(Path(target))
        return result if isinstance(result, dict) and result.get("tickers") else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def ledger_rows(result, *, run_id=None):
    """前向账本：逐票一行紧凑记录，12 个月后对账（中枢 / 情景 / 倍数带 / 隐含增长 / flags）。"""
    rows = []
    for t, r in sorted((result.get("tickers") or {}).items()):
        tg = r.get("target_12m") or {}
        e = r.get("eps") or {}
        rows.append({
            "as_of": result.get("as_of"), "run_id": run_id, "version": result.get("version"), "ticker": t,
            "status": r.get("status"), "reason": r.get("reason"), "price": r.get("price"),
            "center": tg.get("center"), "scenarios": tg.get("eps_scenarios"), "band": tg.get("multiple_band"),
            "eps_base": (e.get("eps") or {}).get("base"), "eps_window": [e.get("start"), e.get("end")],
            "eps_basis": e.get("earnings_basis"), "ttm_eps": r.get("ttm_eps"), "pe_now": r.get("pe_now"),
            "implied_growth": (r.get("implied") or {}).get("implied_growth"),
            "fcf_yield": (r.get("fcf") or {}).get("yield_now"), "input_state": r.get("input_state"),
            "flags": r.get("flags"), "required_return": result.get("required_return"),
            "horizon_end": (_day(result["as_of"]) + timedelta(days=365)).isoformat() if result.get("as_of") else None,
        })
    return rows


REASONS = {
    "company_model_eps_missing": "公司模型无可用 EPS 情景",
    "forward_eps_not_positive": "未来四季 EPS ≤ 0，PE 无意义",
    "ttm_eps_not_positive": "TTM EPS ≤ 0，PE 无意义",
    "price_missing": "无参考价",
    "positive_pe_history_insufficient": "近 3 年正 PE 样本不足",
    "near_breakeven_pe": "微利（当前 PE >100）且无自身倍数历史，PE 不能当锚",
}
FLAGS = {
    "eps_basis_adjusted_vs_gaap_pe_history": "EPS 为调整后口径、历史 PE 为 GAAP，中枢偏高",
    "package_superseded": "新财报已出、EPS 包未跟上（滚动未匹配到该季），EPS 仍按旧包",
    "eps_model_unreviewed": "财报后机械滚动、尚未按指引复核",
    "pe_history_dispersed": "历史倍数离散大（p75/p25 > 2）",
    "eps_step_change": "EPS 相对 TTM 跳变 >3 倍，倍数不变假设弱",
    "eps_doubling": "EPS 翻倍以上，周期/拐点票倍数通常收缩，中枢偏乐观",
    "special_items_added_back": "已回加发行人指引的一次性费用",
    "consensus_gap": "与券商一致预期差 >25%（一致预期口径/期限未核）",
    "fcf_negative": "TTM 自由现金流为负，PE 目标无现金支持",
    "cash_earnings_divergence": "FCF 不到核心净利一半，盈利与现金背离（资本开支周期）",
    "no_pe_band": "近 3 年正 PE 样本不足，只出中枢不出倍数带",
}


def cash_text(r):
    """现金视角一段；没有 FCF 序列 → 空串。"""
    f = r.get("fcf") or {}
    if f.get("status") != "ok":
        return ""
    parts = [f"FCF收益率 {f['yield_now']:.1%}"]
    if f.get("yield_percentile_3y") is not None:
        parts.append(f"3年分位 {f['yield_percentile_3y']:.0f}%")
    if f.get("conversion") is not None:
        parts.append(f"FCF/净利 {f['conversion']:.2f}")
    return "现金 " + "，".join(parts)


def implied_text(r):
    g = r.get("implied") or {}
    if g.get("status") == "ok":
        return f"现价隐含10年盈利年增 {g['implied_growth']:.0%}"
    if g.get("status") == "above_range":
        return "现价隐含10年盈利年增 >80%"
    if g.get("status") == "below_range":
        return "现价隐含10年盈利年增 <−30%"
    return ""


def render(result):
    """中文一票一行；只有 ok 的票出价，现金视角每票都播。"""
    def m(x):
        return f"${x:,.2f}"
    lines = [f"研究级 12 个月目标（{result['version']}，未验证，零授权）：{result['counts']['ok']}/"
             f"{result['counts']['total']} 票出数",
             "中枢 = 基准EPS × 当前PE（倍数不变）；情景 = 下行/上行EPS × 当前PE；"
             "倍数带 = 基准EPS × 本股3年PE p25–p75；隐含增长 = 反向DCF（核心TTM EPS，10年 + 永续3%"
             + (f"，r = {result['required_return_basis']}" if result.get("required_return_basis") else "")
             + "）。不是公允价、不是概率区间。", ""]
    for t, r in sorted(result["tickers"].items()):
        state = str(r.get("input_state", ""))
        stale = ("（输入过期，按冻结时点 " + state.split(":")[-1] + "）" if state.startswith("expired")
                 else "（EPS 包 " + state.split(":", 1)[1] + "）" if state.startswith("eps_model:roll") else "")
        cash = "；".join(x for x in (implied_text(r), cash_text(r)) if x)
        if r["status"] != "ok":
            lines.append(f"{t}  — 不出数：{REASONS.get(r['reason'], r['reason'])}{stale}"
                         + (f" · {cash}" if cash else ""))
            continue
        tg, e = r["target_12m"], r["eps"]
        sc = tg["eps_scenarios"]
        rng = " / ".join(m(sc[s]) if sc[s] is not None else "?" for s in SCENARIOS)
        flag = "；".join(FLAGS[f] for f in r["flags"])
        band, hist = tg["multiple_band"], r["pe_history"]
        pe = (f"PE {r['pe_now']:.1f}（3年分位 {r['pe_now_percentile_3y']:.0f}%，"
              f"p25–p75 {hist['p25']:.1f}–{hist['p75']:.1f}）" if band else f"PE {r['pe_now']:.1f}")
        cons = f"，一致预期 {r['consensus_eps']:.2f}" if r.get("consensus_eps") else ""
        lines.append(
            f"{t}  现价 {m(r['price'])} → 中枢 {m(tg['center'])}（{r['upside_center']:+.0%}）"
            f" · 情景 {rng}" + (f" · 倍数带 {m(band['p25'])}–{m(band['p75'])}" if band else "")
            + f" · EPS {e['eps']['base']:.2f}（{e['earnings_basis']}，{e['start']}→{e['end']}，核心TTM {r['ttm_eps']:.2f}，"
            f"{r['eps_growth']:+.0%}{cons}） · {pe}" + (f" · {cash}" if cash else "") + stale
            + (f" ⚠️ {flag}" if flag else ""))
    return "\n".join(lines)


# TG 推送版：同一份 result，按手机阅读重排 ——
# 先一张「中枢 vs 现价」表回答「空间多大」，再一张估值位置表，警示与不出数各成一段，
# 逐票 EPS 与口径说明进折叠块。数字与 render() 同源，只改版式与取舍位数；report.md 仍用 render()。
TG_FLAG_SHORT = {**FLAG_SHORT, "no_pe_band": "无倍数带", "special_items_added_back": "已回加一次性费用"}


def _px(x):
    """表格价位：≥1000 取整带千分位，否则两位小数。"""
    return f"{x:,.0f}" if abs(x) >= 1000 else f"{x:,.2f}"


def _span(lo, hi):
    """情景区间：≥100 取整，否则一位小数。"""
    if lo is None or hi is None:
        return "—"
    fmt = (lambda x: f"{x:,.0f}") if max(abs(lo), abs(hi)) >= 100 else (lambda x: f"{x:,.1f}")
    return f"{fmt(lo)}–{fmt(hi)}"


def _implied_short(r):
    g = r.get("implied") or {}
    return {"ok": lambda: f"{g['implied_growth']:.0%}", "above_range": lambda: ">80%",
            "below_range": lambda: "<−30%"}.get(g.get("status"), lambda: "—")()


def _stale_note(r):
    state = str(r.get("input_state", ""))
    if state.startswith("expired"):
        return "输入过期，按冻结时点 " + state.split(":")[-1]
    if state.startswith("eps_model:roll"):
        return "EPS 包 " + state.split(":", 1)[1]
    return ""


def render_tg(result, *, day=None, status=None, released=None):
    """TG 富消息版（GFM 子集，由 tg_rich 编译成原生表格 / 折叠块）。"""
    tickers = result.get("tickers") or {}
    ok = sorted(((t, r) for t, r in tickers.items() if r.get("status") == "ok"),
                key=lambda kv: kv[1]["upside_center"], reverse=True)
    rel = released or {}
    rel_text = ("获准发布 0" if not any(rel.values())
                else f"获准发布 公允价 {rel.get('fair_value', 0)} · 12月目标 {rel.get('market_target_12m', 0)}")
    head = f"# 📐 每日估值 {day}" if day else "# 📐 每日估值"
    lines = [head + (f"（{status}）" if status and status != "completed" else ""),
             f"研究级 12 个月目标 **{result['counts']['ok']}/{result['counts']['total']}** 出数 · "
             f"{rel_text} · 未验证 · 零授权", ""]
    if ok:
        lines += ["## 中枢 vs 现价", "按空间从大到小；⚠️ = 读数前先看下方警示", "",
                  "| 票 | 现价 | 中枢 | 空间 | 情景 下–上 |", "|---|--:|--:|--:|--:|"]
        for t, r in ok:
            sc = r["target_12m"]["eps_scenarios"]
            mark = " ⚠️" if r.get("flags") or _stale_note(r) else ""
            lines.append(f"| {t}{mark} | {_px(r['price'])} | {_px(r['target_12m']['center'])} | "
                         f"{r['upside_center']:+.0%} | {_span(sc.get('downside'), sc.get('upside'))} |")
        lines += ["", "## 估值位置", "PE 后为 3 年分位；倍数带 = 基准 EPS × 本股 3 年 PE p25–p75", "",
                  "| 票 | PE·分位 | 倍数带 | 隐含年增 | FCF收益率 |", "|---|--:|--:|--:|--:|"]
        for t, r in ok:
            pct = r.get("pe_now_percentile_3y")
            band = r["target_12m"].get("multiple_band") or {}
            f = r.get("fcf") or {}
            pe_text = f"{r['pe_now']:.1f}" + ("" if pct is None else f" · {pct:.0f}%")
            fcf_text = f"{f['yield_now']:.1%}" if f.get("status") == "ok" else "—"
            lines.append(f"| {t} | {pe_text} | {_span(band.get('p25'), band.get('p75'))} | "
                         f"{_implied_short(r)} | {fcf_text} |")
        warns = []
        for t, r in ok:
            items = [TG_FLAG_SHORT.get(x, FLAGS.get(x, x)) for x in r.get("flags") or []]
            if _stale_note(r):
                items.append(_stale_note(r))
            if items:
                warns.append(f"- **{t}** " + " · ".join(items))
        if warns:
            lines += ["", "## ⚠️ 警示"] + warns
    skipped = {}
    for t, r in sorted(tickers.items()):
        if r.get("status") != "ok":
            label = REASONS.get(r.get("reason"), r.get("reason"))
            note = _stale_note(r)
            skipped.setdefault(label + (f"（{note}）" if note else ""), []).append(t)
    if skipped:
        lines += ["", "## 不出数"] + [f"- **{'、'.join(ts)}** {label}" for label, ts in skipped.items()]
    rolls = result.get("eps_model_rolls") or {}
    if rolls:
        lines += ["", "## 🔁 EPS 包待复核"] + [
            f"- **{t}** {e[-1]['reported_end']} 营收实际/预测 {e[-1]['ratio']:.2f}" for t, e in sorted(rolls.items())]
    if ok:
        lines += ["", "<details><summary>逐票 EPS（基准 vs 核心 TTM）</summary>", "",
                  "| 票 | 基准EPS | TTM | 增速 | 一致预期 |", "|---|--:|--:|--:|--:|"]
        for t, r in ok:
            cons = r.get("consensus_eps")
            cons_text = f"{cons:.2f}" if cons else "—"
            lines.append(f"| {t} | {r['eps']['eps']['base']:.2f} | {r['ttm_eps']:.2f} | {r['eps_growth']:+.0%} | "
                         f"{cons_text} |")
        lines += ["", "</details>"]
    lines += ["", "<details><summary>口径</summary>", "",
              "- 中枢 = 基准 EPS × 当前 PE（倍数不变）",
              "- 情景 = 下行 / 上行 EPS × 当前 PE",
              "- 倍数带 = 基准 EPS × 本股 3 年 PE p25–p75（无 = 近 3 年正 PE 样本不足）",
              "- 隐含年增 = 反向 DCF：核心 TTM EPS，10 年 + 永续 3%"
              + (f"，r = {result['required_return_basis']}" if result.get("required_return_basis") else ""),
              "- 不是公允价、不是概率区间；估值层零授权，不进入场 / 仓位 / 退出",
              "", "</details>"]
    return "\n".join(lines)


def tg_message(job, result):
    """日更回执 + 研究级结果 → 推送正文（valuation-daily 第 5 步唯一出口）。"""
    return render_tg(result, day=job.get("day_sgt"), status=job.get("status"),
                     released=job.get("released_counts"))


def main(argv=None):
    import argparse
    import json
    from quant.core.io_json import read_json
    parser = argparse.ArgumentParser(description="研究级 12 个月目标：读某次日更归档，只读计算")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--run-dir", help="var/valuation-daily/runs/<day>/<run_id>")
    mode.add_argument("--tg-latest", action="store_true",
                      help="最近一次日更的 TG 推送正文（valuation-daily 第 5 步，只读已落盘结果不重算）")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.tg_latest:
        job = read_json(VALUATION_RUNS_DIR / "latest.json") or {}
        if not job.get("research_targets"):
            raise SystemExit("no daily valuation run with research targets yet")
        print(tg_message(job, read_json(Path(job["research_targets"]))))
        return 0
    from quant.valuation import eps_model
    run = Path(args.run_dir)
    receipt = read_json(run / "valuation.json")
    frozen = read_json(run / "capture.json")["snapshot"]["data"]["valuation"]
    result = build_all(receipt, frozen, eps_models=eps_model.load())
    print(json.dumps(result, ensure_ascii=False, indent=1) if args.json else render(result))


if __name__ == "__main__":
    raise SystemExit(main())
