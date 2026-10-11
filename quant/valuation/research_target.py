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
  可知日：季度行有 available_at（财报反应日）用它，否则期末 +45 天。
  一次性非经营项：「其他损益 − 证券损益」> 25% 营收 → 该季按税前剔除后 ×(1−21%) 重算。
EPS 锚不住（前瞻 / TTM EPS ≤ 0、微利、无 EPS 包）→ EV/营收路径（`ev_sales_route`）；
营收也没有 → 不出数。不用行业倍数兜底。
这不是公允价，不是概率区间，没有历史检验；前向冻结只是开始积累样本。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import math
from datetime import date, datetime, timedelta
from statistics import median, quantiles

from quant.core.io_json import iter_jsonl, read_json
from quant.core.paths import KLINE_CACHE_DIR, KLINE_DEEP_DIR, VALUATION_RUNS_DIR
from quant.core.valuation_contracts import finite

VERSION = "research-target-v2"
EPS_LAG_DAYS = 45          # 期末 → 视为可知的**回退**滞后；季度行有 available_at（财报反应日，fundamentals 补）优先
ONEOFF_REVENUE_FRAC = 0.25 # 单季「其他损益 − 已扣证券损益」绝对值 > 25% 营收 = 一次性非经营项（如大额衍生 / 可转债公允价值损失）
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


def known_day(q):
    """季度可知日：财报反应日（available_at）优先，否则期末 + EPS_LAG_DAYS。"""
    if q.get("available_at"):
        return _day(q["available_at"])
    return _day(q["period_end"]) + timedelta(days=EPS_LAG_DAYS)


def oneoff_residual(q):
    """一次性非经营项（税前）：其他损益扣掉已单独处理的证券损益后，绝对值 > 25% 营收才算；否则 0。"""
    other, gain, rev = q.get("other_income"), q.get("gain_on_securities"), q.get("revenue")
    if not finite(other) or not finite(rev) or rev <= 0:
        return 0.0
    resid = other - (gain if finite(gain) else 0.0)
    return resid if abs(resid) > ONEOFF_REVENUE_FRAC * rev else 0.0


def core_eps(rows):
    """逐季核心 EPS：稀释 EPS − 证券损益×(1−t)/稀释股数。股数 = 净利/EPS，缺则沿用上一季。

    一次性非经营项季度（`oneoff_residual` ≠ 0）：按「(税前 − 证券损益 − 一次性项) × (1 − 21%)」
    重算 —— 这类损失（可转债 / 衍生公允价值）多半没有税盾，报表税项远小于 21%，
    沿用「EPS − 项×(1−t)」会把核心 EPS 算成大幅为负。
    """
    out, shares = [], None
    for q in rows:
        eps, ni, gain = q["diluted_eps"], q.get("net_income"), q.get("gain_on_securities")
        if finite(ni) and abs(eps) > 0.01 and ni / eps > 0:
            shares = ni / eps
        resid = oneoff_residual(q)
        if resid and shares and finite(q.get("pretax")):
            g = gain if finite(gain) else 0.0
            eps = (q["pretax"] - g - resid) * (1 - GAIN_TAX_RATE) / shares
            out.append({**q, "diluted_eps": eps, "oneoff_stripped": resid})
            continue
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
        out.append((known_day(window[-1]), sum(q["diluted_eps"] for q in window)))
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
        core = sum(_core_income(q) for q in window)
        out.append((known_day(window[-1]), fcf / shares[i], fcf, core))
    return out


def _core_income(q):
    """单季核心净利（与 core_eps 同口径）。"""
    gain = q.get("gain_on_securities") if finite(q.get("gain_on_securities")) else 0.0
    resid = oneoff_residual(q)
    if resid and finite(q.get("pretax")):
        return (q["pretax"] - gain - resid) * (1 - GAIN_TAX_RATE)
    return (q.get("net_income") or 0) - gain * (1 - GAIN_TAX_RATE)


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


# ── 周期票 ────────────────────────────────────────────────────────────────────
# R0 = 倍数不变（中枢）；R2 = 周期平均法（5 年平均 EPS × 本股 5 年 P/N 中位），默认只作旁注。
# 盈利猛增状态（EPS 增 >50%）下「倍数不变」即便盈利猜对也倾向高估 → 挂 eps_surge_bias。
# 这些规则是声明值，未在公开仓验证；采纳前用自己的样本做预注册研究。
CYCLE_QUARTERS = 20
CYCLE_YEARS = 5
EPS_SURGE = 0.5
SURGE_BIAS_TEXT = "EPS 增 >50% 的状态下，倍数不变法即便盈利猜对也倾向高估"


def norm_eps_series(quarters):
    """[(可知日, N)]：N = 最近 20 个连续季度核心 EPS 之和 ÷ 5（5 年平均年 EPS）。"""
    rows = core_eps(sorted((q for q in quarters or [] if q.get("period_end") and finite(q.get("diluted_eps"))),
                           key=lambda q: q["period_end"]))
    out = []
    for i in range(CYCLE_QUARTERS - 1, len(rows)):
        w = rows[i - CYCLE_QUARTERS + 1:i + 1]
        if (_day(w[-1]["period_end"]) - _day(w[0]["period_end"])).days > 91 * CYCLE_QUARTERS + 30:
            continue
        out.append((known_day(w[-1]), sum(q["diluted_eps"] for q in w) / CYCLE_YEARS))
    return out


def cycle_normalized(quarters, closes, *, as_of_day, eps_base, price, eps_scenarios=None):
    """R2：(最近 16 个已知季度核心 EPS + 前瞻四季 EPS) ÷ 5 × 本股 5 年 P/N 中位。

    非猛增状态只作旁注；猛增状态（周期票）参与几何中值中枢（`geo_center`，声明规则，未验证）。
    eps_scenarios 给了 → 逐情景同式算 `scenario_centers`。"""
    rows = core_eps(sorted((q for q in quarters or [] if q.get("period_end") and finite(q.get("diluted_eps"))
                            and known_day(q) <= as_of_day), key=lambda q: q["period_end"]))
    last16 = rows[-(CYCLE_QUARTERS - 4):]
    if len(last16) < CYCLE_QUARTERS - 4 or not finite(eps_base) or not finite(price) or price <= 0:
        return {"status": "unavailable", "reason": "history_short"}
    n12 = (sum(q["diluted_eps"] for q in last16) + eps_base) / CYCLE_YEARS
    series = norm_eps_series(quarters)
    start = as_of_day - timedelta(days=365 * CYCLE_YEARS)
    vals = []
    for d in sorted(d for d in closes if start <= d <= as_of_day)[::SAMPLE_EVERY]:
        n = ttm_at(series, d)
        if n is not None and n > 0:
            vals.append(closes[d] / n)
    if n12 <= 0 or len(vals) < MIN_POSITIVE_SAMPLES:
        return {"status": "unavailable", "reason": "normalized_eps_not_positive" if n12 <= 0 else "history_short"}
    multiple = median(vals)
    center = n12 * multiple
    sum16 = sum(q["diluted_eps"] for q in last16)
    scen = {s: ((sum16 + e) / CYCLE_YEARS * multiple if finite(e) else None)
            for s, e in (eps_scenarios or {}).items()}
    return {"status": "ok", "normalized_eps_12m": n12, "multiple_5y_median": multiple, "samples": len(vals),
            "center": center, "upside": center / price - 1, "scenario_centers": scen}


def geo_center(target, cyc):
    """周期票猛增状态中枢 = √(R0 × R2)；逐情景同式，R2 ≤ 0 的情景退回 R0。"""
    def g(a, b):
        return math.sqrt(a * b) if finite(a) and finite(b) and a > 0 and b > 0 else a
    sc = cyc.get("scenario_centers") or {}
    return {"center": g(target["center"], cyc["center"]),
            "eps_scenarios": {s: g(v, sc.get(s)) for s, v in target["eps_scenarios"].items()},
            "multiple_band": target.get("multiple_band")}


def _eps_route(ticker, *, company_research, input_state, quarters, price, closes, as_of,
               package=None, consensus_eps=None, required_return=None, eps_model=None, archetype=None):
    """EPS × 本股 PE 路径（v2 原式）。所有失败都是带原因的 unavailable，不抛。"""
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
    if oneoff_in_ttm(quarters, as_of_day):
        flags.append("oneoff_items_stripped")
    if base / ttm_now - 1 > EPS_SURGE:
        flags.append("eps_surge_bias")
    if archetype == "semi_cyclical":
        cyc = cycle_normalized(quarters, closes, as_of_day=as_of_day, eps_base=base, price=price,
                               eps_scenarios=eps["eps"])
        out["cycle_normalized"] = cyc
        if base / ttm_now - 1 > EPS_SURGE and cyc.get("status") == "ok":
            out["r0_target_12m"] = target
            target = geo_center(target, cyc)
            flags.append("cyclical_geo_center")
        else:
            flags.append("cyclical_constant_pe")
    return {**out, "status": "ok", "method": "eps_pe", "pe_now": pe_now,
            "pe_now_percentile_3y": _percentile_rank(values, pe_now) if values else None,
            "eps_growth": base / ttm_now - 1, "target_12m": target,
            "upside_center": target["center"] / price - 1, "flags": flags}


def oneoff_in_ttm(quarters, as_of_day):
    """当前 TTM 窗口（可知日 ≤ as_of 的最近四季）里有被剔除的一次性非经营项？"""
    rows = sorted((q for q in quarters or [] if q.get("period_end") and finite(q.get("diluted_eps"))
                   and known_day(q) <= as_of_day), key=lambda q: q["period_end"])
    return any(oneoff_residual(q) for q in rows[-4:])


# ── 无盈利锚路径：EV / 营收 ──────────────────────────────────────────────
# EPS × PE 对前瞻 / TTM 盈利 ≤ 0 或微利的票没有经济意义，v2 直接不出数。这条路径换成营收：
#   中枢 = 12 个月后的「前瞻 EV/营收」不变：EV_12m = 精简包第 5–8 季营收 × (今天 EV ÷ 第 1–4 季营收)
#          股权 = EV_12m − 12 个月后净债务（今天净债务 − TTM FCF：烧钱就加债）÷ 股数
#          用前瞻倍数而不是 TTM 倍数：高增长票 TTM EV/营收不变 = 假设增速永远不降（会把目标算成数倍上涨）
#   反推 = 现价隐含第 5 年营收：EV = 第 5 年末起按稳态 FCF 利润率永续的现值（原型 target_margin × (1−21%)，
#          永续 3%，r 同 EPS 路径）；忽略 1–5 年现金流 —— 这些票 1–5 年 FCF 为负，所以隐含营收是**下限**。
#          隐含 5 年营收年化 = (隐含第 5 年营收 ÷ TTM 营收)^(1/5) − 1，与模型路径对照 =「价格在为多少想象付钱」。
# 同样未验证、零授权；倍数不变是声明假设。
EV_ROUTE_REASONS = frozenset({"forward_eps_not_positive", "ttm_eps_not_positive", "near_breakeven_pe",
                              "company_model_eps_missing"})
REVERSE_YEARS = 5
FCF_TAX_RATE = 0.21
MODEL_UNITS = 1e6          # eps_models.json 营收 / 股数单位 = 百万


def latest_known(quarters, as_of_day):
    rows = sorted((q for q in quarters or [] if q.get("period_end") and known_day(q) <= as_of_day),
                  key=lambda q: q["period_end"])
    return rows


def net_debt(q):
    """债务 + 租赁 − 现金短投（缺项按 0，缺了哪项报出来）。"""
    parts = {k: q.get(k) for k in ("current_debt", "lt_debt", "current_lease", "lt_lease", "cash_sti")}
    missing = [k for k, v in parts.items() if not finite(v)]
    v = {k: (x if finite(x) else 0.0) for k, x in parts.items()}
    return (v["current_debt"] + v["lt_debt"] + v["current_lease"] + v["lt_lease"] - v["cash_sti"]), missing


def model_revenue(eps_model, start, stop):
    """精简包第 start..stop-1 季三情景营收合计（美元）；季度不足 → None。"""
    qs = (eps_model or {}).get("quarters") or []
    if len(qs) < stop:
        return None
    return {s: sum(q["revenue"][s] for q in qs[start:stop]) * MODEL_UNITS for s in SCENARIOS}


def implied_revenue(*, ev, ttm_revenue, rate, margin, years=REVERSE_YEARS, terminal=TERMINAL_GROWTH):
    if not all(finite(x) for x in (ev, ttm_revenue, rate, margin)) or ev <= 0 or ttm_revenue <= 0 \
            or margin <= 0 or rate <= terminal:
        return {"status": "unavailable", "reason": "inputs_missing" if margin else "no_target_margin"}
    fcf_margin = margin * (1 - FCF_TAX_RATE)
    rev = ev * (1 + rate) ** years * (rate - terminal) / (fcf_margin * (1 + terminal))
    return {"status": "ok", "years": years, "rate": rate, "target_margin": margin, "fcf_margin": fcf_margin,
            "revenue_implied": rev, "revenue_ttm": ttm_revenue,
            "implied_cagr": (rev / ttm_revenue) ** (1 / years) - 1, "basis": "lower_bound_ignores_years_1_to_5"}


def ev_sales_route(ticker, *, quarters, price, as_of_day, eps_model, fundamentals_row, archetype_margin, rate):
    """无盈利锚票的 EV/营收路径。失败 → unavailable + 原因。"""
    snap = (fundamentals_row or {}).get("snapshot") or {}
    forward = (fundamentals_row or {}).get("forward") or {}
    rows = latest_known(quarters, as_of_day)
    if len(rows) < 4 or any(not finite(q.get("revenue")) for q in rows[-4:]):
        return {"status": "unavailable", "reason": "revenue_history_missing"}
    if (_day(rows[-1]["period_end"]) - _day(rows[-4]["period_end"])).days > 300:
        return {"status": "unavailable", "reason": "revenue_history_missing"}
    ttm_rev = sum(q["revenue"] for q in rows[-4:])
    shares, shares_basis = None, None
    if finite(snap.get("total_market_val")) and finite(snap.get("last_price")) and snap["last_price"] > 0:
        shares, shares_basis = snap["total_market_val"] / snap["last_price"], "snapshot_market_cap"
    elif eps_model and (eps_model.get("quarters") or []):
        shares = eps_model["quarters"][0]["diluted_shares"]["base"] * MODEL_UNITS
        shares_basis = "eps_model_diluted"
    if not finite(shares) or shares <= 0 or not finite(price) or price <= 0:
        return {"status": "unavailable", "reason": "shares_or_price_missing"}
    nd, nd_missing = net_debt(rows[-1])
    ev = price * shares + nd
    flags = []
    near = model_revenue(eps_model, 0, 4)
    far = model_revenue(eps_model, 4, 8)
    if near and far:
        basis = "eps_model:forward_multiple"
    elif finite(forward.get("forward_revenue")) and forward["forward_revenue"] > 0:
        near = {"base": forward["forward_revenue"], "downside": None, "upside": None}
        far = None
        basis = "provider_forward_revenue:trailing_multiple"
        flags.append("forward_revenue_provider_unverified")
    else:
        return {"status": "unavailable", "reason": "forward_revenue_missing"}
    fcf = [q.get("fcf") for q in rows[-4:]]
    fcf_ttm = sum(fcf) if all(finite(x) for x in fcf) else None
    nd_12m = nd - fcf_ttm if fcf_ttm is not None else nd
    if fcf_ttm is None:
        flags.append("net_debt_held_flat")
    elif fcf_ttm < 0:
        flags.append("cash_burn_adds_debt")
    if nd_missing:
        flags.append("balance_items_missing")
    if far:                       # 12 个月后前瞻倍数 = 今天前瞻倍数
        multiple = ev / near["base"]
        ev_12m = {s: far[s] * multiple for s in SCENARIOS}
    else:                         # 只有一个前瞻营收：12 个月后 TTM 营收 = 它，TTM 倍数不变
        multiple = ev / ttm_rev
        ev_12m = {s: (near[s] * multiple if near.get(s) is not None else None) for s in SCENARIOS}
    per_share = {s: ((v - nd_12m) / shares if v is not None else None) for s, v in ev_12m.items()}
    if per_share["base"] is None:
        return {"status": "unavailable", "reason": "forward_revenue_missing"}
    if per_share["base"] <= 0:
        flags.append("equity_residual_negative")
    if any(finite(q.get("net_stock_issuance")) and q["net_stock_issuance"] > 0 for q in rows[-4:]):
        flags.append("dilution_ignored")
    if far and far["base"] / near["base"] > 2:
        flags.append("revenue_step_change")
    return {"status": "ok", "method": "ev_sales", "basis": basis,
            "ev": {"ev": ev, "market_cap": price * shares, "shares": shares, "shares_basis": shares_basis,
                   "net_debt": nd, "net_debt_12m": nd_12m, "fcf_ttm": fcf_ttm,
                   "net_debt_quarter": rows[-1]["period_end"], "missing": nd_missing},
            "revenue": {"ttm": ttm_rev, "ntm": near, "next_ntm": far},
            "ev_sales_now": ev / near["base"] if far else ev / ttm_rev,
            "ev_sales_ttm": ev / ttm_rev,
            "revenue_growth": near["base"] / ttm_rev - 1,
            "target_12m": {"center": per_share["base"], "eps_scenarios": per_share, "multiple_band": None},
            "upside_center": per_share["base"] / price - 1,
            "implied_revenue": implied_revenue(ev=ev, ttm_revenue=ttm_rev, rate=rate, margin=archetype_margin),
            "flags": flags + ["no_multiple_band"]}


def research_target(ticker, *, company_research, input_state, quarters, price, closes, as_of,
                    package=None, consensus_eps=None, required_return=None, eps_model=None,
                    fundamentals_row=None, archetype_margin=None, archetype=None):
    """单票研究级 12 个月目标：EPS 路径；无盈利锚 → EV/营收路径。不抛。"""
    out = _eps_route(ticker, company_research=company_research, input_state=input_state, quarters=quarters,
                     price=price, closes=closes, as_of=as_of, package=package, consensus_eps=consensus_eps,
                     required_return=required_return, eps_model=eps_model, archetype=archetype)
    if out["status"] == "ok" or out.get("reason") not in EV_ROUTE_REASONS:
        return out
    ev = ev_sales_route(ticker, quarters=quarters, price=price, as_of_day=_day(as_of), eps_model=eps_model,
                        fundamentals_row=fundamentals_row, archetype_margin=archetype_margin,
                        rate=required_return)
    if ev["status"] != "ok":
        return {**out, "ev_route": ev}
    return {**out, **ev, "eps_route_reason": out["reason"], "reason": None,
            "ttm_eps": out.get("ttm_eps", ttm_at(ttm_eps_series(quarters), _day(as_of)))}


def build_all(receipt, frozen_valuation, *, package_builder=None, closes_fn=load_closes, eps_models=None):
    """对一份日更收据逐票出研究级目标。EPS 首选精简包；无精简包时过期公司包按冻结时点重算（不续期、不改包）。"""
    from quant.valuation import company_model
    from quant.valuation.archetypes import ARCHETYPES
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
        arche = (((frozen_valuation.get("inputs") or {}).get("tickers") or {}).get(ticker) or {})
        margin = (((arche.get("overrides") or {}).get("target_margin"))
                  or (ARCHETYPES.get(arche.get("archetype")) or {}).get("target_margin"))
        out[ticker] = research_target(
            ticker, company_research=research, input_state=state,
            quarters=(fundamentals.get(ticker) or {}).get("quarters"),
            price=quote, closes=closes_fn(ticker, until=as_of_day), as_of=as_of,
            package=packages.get(ticker), consensus_eps=forward.get("forward_eps"), required_return=rate,
            eps_model=slim, fundamentals_row=fundamentals.get(ticker), archetype_margin=margin,
            archetype=arche.get("archetype"))
    return {"version": VERSION, "as_of": as_of, "authority": "analysis_only", "required_return": rate,
            "required_return_basis": (f"10Y {market.get('rf'):.2%}（{market.get('as_of', '')[:10]}）+ ERP "
                                      f"{market.get('erp'):.2%}，β=1") if rate else None,
            "validation": "unvalidated", "receipt_hash": receipt.get("receipt_hash"),
            "tickers": out,
            "counts": {"ok": sum(r["status"] == "ok" for r in out.values()), "total": len(out)}}


FLAG_SHORT = {
    "cash_burn_adds_debt": "烧钱加债", "dilution_ignored": "未计增发", "revenue_step_change": "营收跳变",
    "forward_revenue_provider_unverified": "前瞻营收口径未核", "equity_residual_negative": "股权为负",
    "oneoff_items_stripped": "剔一次性项", "balance_items_missing": "资产负债缺项",
    "eps_surge_bias": "EPS猛增·倍数不变倾向高估", "cyclical_constant_pe": "周期票",
    "cyclical_geo_center": "周期猛增·几何中值中枢",
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
    if r.get("method") == "ev_sales":
        imp = r.get("implied_revenue") or {}
        tail = (f" · 现价隐含5年营收年增 ≥{imp['implied_cagr']:.0%}" if imp.get("status") == "ok" else "")
        how = "前瞻EV/营收不变，无盈利锚"
    elif r.get("r0_target_12m"):
        how = "周期猛增·√(倍数不变×周期平均)"
        tail += f" · 倍数不变 ${r['r0_target_12m']['center']:,.0f}（{r['r0_target_12m']['center'] / r['price'] - 1:+.0%}）"
    else:
        how = "倍数不变"
        cyc = r.get("cycle_normalized") or {}
        if cyc.get("status") == "ok":
            tail += f" · 周期平均法 ${cyc['center']:,.0f}（{cyc['upside']:+.0%}）"
    return (f"📐[FYI] 研究级12月 中枢 ${tg['center']:,.0f}（{r['upside_center']:+.0%}，{how}）{tail}"
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
            "method": r.get("method"), "eps_route_reason": r.get("eps_route_reason"),
            "ev_sales_now": r.get("ev_sales_now"),
            "cycle_center": (r.get("cycle_normalized") or {}).get("center"),
            "r0_center": (r.get("r0_target_12m") or {}).get("center"),
            "implied_revenue_cagr": (r.get("implied_revenue") or {}).get("implied_cagr"),
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
    "oneoff_items_stripped": "TTM 内有一次性非经营项（>25% 营收）已剔除，按税前重算",
    "eps_surge_bias": SURGE_BIAS_TEXT + "（声明规则，未验证）",
    "cyclical_constant_pe": "周期票：倍数不变假设弱，旁注周期平均法（5 年平均 EPS × 5 年 P/N 中位）",
    "cyclical_geo_center": "周期票 EPS 猛增：中枢 = √(倍数不变 × 周期平均法)（声明规则，未验证）",
    "cash_burn_adds_debt": "TTM FCF 为负，12 个月后净债务按 TTM 烧钱额增加",
    "dilution_ignored": "近四季有净增发，股数按现值不变（偏乐观）",
    "revenue_step_change": "精简包次年营收是今年 2 倍以上，倍数不变假设弱",
    "forward_revenue_provider_unverified": "无精简包，前瞻营收取券商反推（期限/口径未核），TTM 倍数不变",
    "equity_residual_negative": "12 个月后 EV 不够覆盖净债务",
    "balance_items_missing": "资产负债表缺项按 0 计",
    "no_multiple_band": "无倍数带",
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


def _bn(x):
    return f"${x / 1e9:,.1f}B" if finite(x) else "?"


def ev_line(t, r):
    """EV/营收路径一行（render 用）。"""
    tg, ev, rv = r["target_12m"], r["ev"], r["revenue"]
    sc = tg["eps_scenarios"]
    rng = " / ".join(f"${sc[s]:,.2f}" if sc.get(s) is not None else "?" for s in SCENARIOS)
    nxt = f" → 次年 {_bn(rv['next_ntm']['base'])}" if rv.get("next_ntm") else ""
    imp = r.get("implied_revenue") or {}
    imp_text = (f" · 现价隐含5年营收年增 ≥{imp['implied_cagr']:.0%}（稳态 FCF 利润率 {imp['fcf_margin']:.0%}）"
                if imp.get("status") == "ok" else "")
    flag = "；".join(FLAGS.get(f, f) for f in r.get("flags") or [] if f != "no_multiple_band")
    return (f"{t}  现价 ${r['price']:,.2f} → 中枢 ${tg['center']:,.2f}（{r['upside_center']:+.0%}，EV/营收）"
            f" · 情景 {rng} · EV/营收 前瞻 {r['ev_sales_now']:.1f}x（TTM {r['ev_sales_ttm']:.1f}x）"
            f" · 营收 TTM {_bn(rv['ttm'])} → NTM {_bn(rv['ntm']['base'])}（{r['revenue_growth']:+.0%}）{nxt}"
            f" · 净债务 {_bn(ev['net_debt'])} → {_bn(ev['net_debt_12m'])}"
            f" · EPS 路径不可用：{REASONS.get(r.get('eps_route_reason'), r.get('eps_route_reason'))}{imp_text}"
            + (f" ⚠️ {flag}" if flag else ""))


def render(result):
    """中文一票一行；只有 ok 的票出价，现金视角每票都播。"""
    def m(x):
        return f"${x:,.2f}"
    lines = [f"研究级 12 个月目标（{result['version']}，未验证，零授权）：{result['counts']['ok']}/"
             f"{result['counts']['total']} 票出数",
             "中枢 = 基准EPS × 当前PE（倍数不变）；无盈利锚 = 精简包次年营收 × 今天前瞻EV/营收 − 12个月后净债务；"
             "情景 = 下行/上行EPS × 当前PE；"
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
        if r.get("method") == "ev_sales":
            lines.append(ev_line(t, r) + (f" · {cash}" if cash else "") + stale)
            continue
        tg, e = r["target_12m"], r["eps"]
        sc = tg["eps_scenarios"]
        rng = " / ".join(m(sc[s]) if sc[s] is not None else "?" for s in SCENARIOS)
        flag = "；".join(FLAGS[f] for f in r["flags"])
        band, hist = tg["multiple_band"], r["pe_history"]
        pe = (f"PE {r['pe_now']:.1f}（3年分位 {r['pe_now_percentile_3y']:.0f}%，"
              f"p25–p75 {hist['p25']:.1f}–{hist['p75']:.1f}）" if band else f"PE {r['pe_now']:.1f}")
        cons = f"，一致预期 {r['consensus_eps']:.2f}" if r.get("consensus_eps") else ""
        cyc = r.get("cycle_normalized") or {}
        if r.get("r0_target_12m"):
            r0 = r["r0_target_12m"]["center"]
            cash = "；".join(x for x in (cash, f"中枢为几何中值 · 倍数不变 {m(r0)}（{r0 / r['price'] - 1:+.0%}）") if x)
        if cyc.get("status") == "ok":
            cash = "；".join(x for x in (cash, f"周期平均法 {m(cyc['center'])}（{cyc['upside']:+.0%}，"
                                                f"5年平均EPS {cyc['normalized_eps_12m']:.2f} × P/N {cyc['multiple_5y_median']:.1f}）") if x)
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
                  "无盈利锚的票显示前瞻 EV/营收与现价隐含 5 年营收年增（下限）", "",
                  "| 票 | PE·分位 | 倍数带 | 隐含年增 | FCF收益率 |", "|---|--:|--:|--:|--:|"]
        for t, r in ok:
            f = r.get("fcf") or {}
            if r.get("method") == "ev_sales":
                imp = r.get("implied_revenue") or {}
                imp_text = f"营收≥{imp['implied_cagr']:.0%}" if imp.get("status") == "ok" else "—"
                fcf_text = f"{f['yield_now']:.1%}" if f.get("status") == "ok" else "—"
                lines.append(f"| {t} | EV/S {r['ev_sales_now']:.1f}x | — | {imp_text} | {fcf_text} |")
                continue
            pct = r.get("pe_now_percentile_3y")
            band = r["target_12m"].get("multiple_band") or {}
            pe_text = f"{r['pe_now']:.1f}" + ("" if pct is None else f" · {pct:.0f}%")
            fcf_text = f"{f['yield_now']:.1%}" if f.get("status") == "ok" else "—"
            lines.append(f"| {t} | {pe_text} | {_span(band.get('p25'), band.get('p75'))} | "
                         f"{_implied_short(r)} | {fcf_text} |")
        warns = []
        for t, r in ok:
            items = [TG_FLAG_SHORT.get(x, FLAGS.get(x, x)) for x in r.get("flags") or [] if x != "no_multiple_band"]
            if _stale_note(r):
                items.append(_stale_note(r))
            cyc = r.get("cycle_normalized") or {}
            if r.get("r0_target_12m"):
                r0 = r["r0_target_12m"]["center"]
                items.append(f"倍数不变 {_px(r0)}（{r0 / r['price'] - 1:+.0%}）")
            if cyc.get("status") == "ok":
                items.append(f"周期平均法 {_px(cyc['center'])}（{cyc['upside']:+.0%}）")
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
    eps_ok = [(t, r) for t, r in ok if r.get("method") != "ev_sales"]
    ev_ok = [(t, r) for t, r in ok if r.get("method") == "ev_sales"]
    if ev_ok:
        lines += ["", "<details><summary>无盈利锚：营收与净债务</summary>", "",
                  "| 票 | 营收TTM | NTM | 次年 | 净债务→12月 |", "|---|--:|--:|--:|--:|"]
        for t, r in ev_ok:
            rv, ev = r["revenue"], r["ev"]
            nxt = _bn(rv["next_ntm"]["base"]) if rv.get("next_ntm") else "—"
            lines.append(f"| {t} | {_bn(rv['ttm'])} | {_bn(rv['ntm']['base'])} | {nxt} | "
                         f"{_bn(ev['net_debt'])}→{_bn(ev['net_debt_12m'])} |")
        lines += ["", "</details>"]
    if eps_ok:
        lines += ["", "<details><summary>逐票 EPS（基准 vs 核心 TTM）</summary>", "",
                  "| 票 | 基准EPS | TTM | 增速 | 一致预期 |", "|---|--:|--:|--:|--:|"]
        for t, r in eps_ok:
            cons = r.get("consensus_eps")
            cons_text = f"{cons:.2f}" if cons else "—"
            lines.append(f"| {t} | {r['eps']['eps']['base']:.2f} | {r['ttm_eps']:.2f} | {r['eps_growth']:+.0%} | "
                         f"{cons_text} |")
        lines += ["", "</details>"]
    lines += ["", "<details><summary>口径</summary>", "",
              "- 中枢 = 基准 EPS × 当前 PE（倍数不变）；周期票 EPS 增 >50% 时 = √(倍数不变 × 周期平均法)",
              "- 无盈利锚（前瞻 / TTM EPS ≤ 0 或微利）：中枢 = 精简包次年营收 × 今天前瞻 EV/营收 − 12 个月后净债务（按 TTM FCF 滚）÷ 股数；"
              "隐含营收 = 现价要求第 5 年营收（原型稳态利润率，忽略 1–5 年现金流 = 下限）",
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
