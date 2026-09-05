#!/usr/bin/env python3
"""model.py — 股价第一性原理的两个引擎 + 反向隐含 + 情景分布 (纯函数, 不碰网络不写 state)。

为什么存在 (用户): 「按股价第一性原理, 用 Σ FCF_t/(1+r)^t 或 EPS×PE, 根据商业模式、
估值久期、资产负债表、经营周期、增量新闻大致算出股价, 判断高估还是低估」。五个输入在本模块的落点:

  商业模式   → 原型参数 (`archetypes.py`): 预测期 N / 增速取法与衰减 / 利润率路径 / 引擎权重
  估值久期   → **输出**不是输入: DCF 现金流的加权年限 + WACC +100bp 的价格弹性 (`dcf_value`)
  资产负债表 → 权益桥 (EV + 现金短投 + 长期投资 − 有息债; 租赁不减, 经营租赁费用已在 EBIT 里)
               + 净利息 (直接 EPS 通道) + 投入资本/ROIC/销售资本比 (再投资强度)
  经营周期   → 营业利润率对历史中位的分位与回归路径 (周期票顶部 PE 最低是陷阱, 用归一化 EPS)
  增量新闻   → 结构化 overlay (`overlays.py` 契约, 雷达每轮写 / 用户 手写) 分四段加在驱动上:
               市场段 rf/erp/wacc (PE 通道: 贴现率·流动性·ERP) → 资本成本重算;
               驱动段 revenue_base/growth_start/target_margin/margin_start/share_change_rate/
                 net_interest_after_tax/tax_rate/terminal_growth (EPS 通道: 订单·指引·利润率·回购·融资);
               倍数段 pe_multiplier (PE 通道: 监管·诉讼·资金流折价/溢价) → 引擎 B 的 PE 锚;
               概率段 scenario_tilt (有量化依据的显式概率调整: 概率从熊搬到牛或反之)。
               宏观风险环境 (ai_news/macro/current.json) 只保留为研究上下文，不自动改变 ERP、
               折价或概率；数值调整来自显式参数及通过契约筛选的 overlay。本模块仍不读新闻正文——
               新闻→数字是雷达/人的判断, 这里只保证每一个数字进来都带出处、有到期、可撤销。

引擎 A  FCFF-DCF:  EV = Σ_{t=1..N} FCF_t/(1+WACC)^t + TV/(1+WACC)^N,
                    FCF_t = NOPAT_t − Δ收入_t / 销售资本比, TV = NOPAT_N(1+gT)(1 − gT/RONIC_T)/(WACC − gT)
引擎 B  EPS×PE:    P_0 = (EPS_T × PE_T + Σ DPS) / (1+r_e)^T, T = 3;
                    EPS 用**核心口径** (营业利润 + 净利息)×(1−τ)/稀释股数, 剔 8022/8023 一次性损益
                    (GOOGL 2026 实测: 报表 TTM EPS 被股权投资重估撑到静态 PE 的一半, 核心口径才能比);
                    PE_T 锚 = 自身 5Y PE 均值 (均值回归) 与稳态合理 PE (1−gT/RONIC)(1+g)/(r_e−gT) 的高者
反向隐含:            解 g0 使引擎 A = 现价 → 「市场在为多少增长定价」, 只需回答信不信
情景:                熊/基/牛 (g0 ∓Δ, 终点利润率 ∓Δ, WACC ±Δ) × 概率 → 加权公允价 + 区间
判定:                现价/加权公允 − 1 → 低估 / 偏低 / 合理 / 偏高 / 高估 (带置信与旗标)

每个数都带出处 (`drivers[...]['basis']` / `provenance`), 缺什么写什么, 不静默填缺省。

v2 ( 夜, 用户: 「NBIS 才 19 MU 才 232, 严重滞后」; 参照 Damodaran 与开源 DCF 的通行做法):
  近端 = 一致预期  第 1 年收入/利润率锚定 OpenD forward PS/PE 反推的卖方一致前瞻 (`fundamentals.parse_forward`;
                   MU 前瞻收入 $233B vs TTM $90B, EPS $144 vs $45 —— v1 全用 TTM 往后推, 这就是「滞后」),
                   无一致预期 → 陡峭爬坡时用最近季年化, 否则 TTM×(1+g); 第 2 年起增速 = 历史趋势斜率封顶后衰减
  资本成本         β = Damodaran 行业 levered β 与个股 5 年**月度**回归 β (Blume) 各半 (v1 一年日线回归在
                   主升浪票上给 3–4, WACC 14% 把终值砍没), 夹逼 [0.8, 1.8]; ERP 4.23% (Damodaran 隐含)
  终值/折现        稳态增速 3.5% (Damodaran: ≤ rf; 开源通行 3%); 中年折现 (t−0.5); 周期票利润率向近 5 年中位分 5 年回归
  仍然不做         没有卖方一致预期的期限声明 (OpenD forward 未标 NTM/FY+1, 当 12 个月后用, 差异归校准)
v3 (同夜, 用户: 「半导体还是离谱, MRVL/INTC 差太远」):
  引擎 B = 卖方一致目标价  OpenD analyst consensus 的 低/均/高 目标价按 r_e 折现 12 个月 = **机构口径**的价值 (熊/基/牛 各取
                   低/均/高); 与本仓 DCF 各半加权, 两者差距明示 —— 「DCF 说贵、街上说便宜」本身就是信息。旧 EPS×PE
                   降为交叉显示。β 只用 Damodaran 行业 β (自下而上, 回归 β 噪声大), 个股月度回归作交叉。周期原型跑道
                   10 年指数衰减 (半衰期 3 年)、年 2 起封顶 30%、RONIC 上限 20%。板块 PE/PS 5 年中位作交叉显示。

🚨 零授权效力: 本层不被 sizing/entry_guard/exit_guard import, 不写 positions/watchlist,
不产出股数/金额/cap。估值再便宜不解除 cap 0, 再贵不触发退出 —— 那是 zone 与退出栈的活。
"""
from __future__ import annotations

import math
import statistics
from typing import Callable

MODEL_VERSION = "v3.1"           # : 保留 v3 引擎，取消宏观风险标签自动映射 ERP
TTM_QUARTERS = 4
MIN_CYCLE_QUARTERS = 12          # through_cycle 至少 3 年利润率历史, 否则退回 current 并打旗
MIN_TREND_QUARTERS = 20          # 收入对数趋势至少 5 年 (20 个 TTM 点), 否则不用趋势
MIN_TREND_R2 = 0.5               # 趋势拟合 R² 门槛, 低于此收入根本不是趋势型, 归一化没有依据
REVENUE_RATIO_CLAMP = (0.5, 2.0) # 趋势/实际 收入比夹逼
REVENUE_CYCLE_FLAG = (0.8, 1.25) # 收入偏离趋势线超此区间 → 周期峰/谷旗标
DEFAULT_TAX = 0.21
TAX_CLAMP = (0.05, 0.35)
S2C_CLAMP = (0.3, 8.0)
COST_OF_EQUITY_CLAMP = (0.03, 0.13)   # (rf+3pp 下限, 绝对上限; v2 由 16% 收到 13%: 4.75% + 1.8×4.23% = 12.4%)
COST_OF_DEBT_CLAMP = (0.02, 0.12)
BETA_BLUME = (0.67, 0.33)             # 调整 β = 0.67 raw + 0.33
BETA_CLAMP = (0.8, 1.8)               # v2: 行业 β (1.3–1.7) 与 5 年月度回归各半后再夹逼 (v1 一年日线给 3–4)
BETA_INDUSTRY_WEIGHT = 0.5            # Damodaran 行业 β 与个股回归 β 的混合权重
BETA_MONTHLY_YEARS, BETA_MIN_MONTHS = 5, 36
MARGIN_1_CLAMP = (-1.0, 0.85)         # 一致前瞻 EPS 反推的第 1 年营业利润率夹逼
RUNRATE_RAMP = 1.25                   # 最近季×4 > 1.25×TTM = 陡峭爬坡: 无一致预期时用最近季年化当第 1 年
MIDYEAR = 0.5                         # 中年折现: 现金流按 (t−0.5) 折
PE_BAND_DEGENERATE_MULT = (0.5, 2.0)  # 历史 PE 通道被负 EPS 期污染 (σ > 均值) 时, 熊/牛 PE 退回 [0.5×, 2×] 均值
IMPLIED_GROWTH_RANGE = (-0.5, 3.0)
BETA_MIN_OBS, BETA_MAX_OBS = 120, 250
SHARE_CHANGE_CLAMP = (-0.05, 0.04)    # 年股本变化: 负=增发稀释, 正=回购
EPS_PE_HORIZON = 3
VERDICT_BANDS = ((-0.25, "低估"), (-0.10, "偏低"), (0.10, "合理"), (0.25, "偏高"))  # 末档 高估
CYCLE_PEAK_MULT = 1.3                 # 周期票: 当前利润率 > 1.3× 历史中位 = 峰值利润, 报表 PE 失真
ONEOFF_WARN_SHARE = 0.20
TV_SHARE_WARN = 0.80
PEAK_MARGIN_PCTL = 0.95              # TTM 利润率处于自身历史 ≥95% 分位 → 置信 −1 (均值回归风险, 平台原型也不豁免)
PROB_FLOOR = 0.05                     # scenario_tilt 后每个情景概率下限
MULTIPLICATIVE_DRIVERS = frozenset({"revenue_base", "pe_multiplier"})


# ── 基础工具 ────────────────────────────────────────────────────────

def _clamp(x: float, lo: float | None, hi: float | None) -> float:
    if lo is not None and x < lo:
        return lo
    if hi is not None and x > hi:
        return hi
    return x


def ttm_at(rows: list[dict], k: int, field: str) -> float | None:
    """rows[k-3..k] 的 field 求和; 任一缺失 = None (部分求和 = 静默少加, 比 None 更坏)。"""
    if k < TTM_QUARTERS - 1:
        return None
    vals = [rows[i].get(field) for i in range(k - TTM_QUARTERS + 1, k + 1)]
    if any(v is None for v in vals):
        return None
    return float(sum(vals))


def _cagr(now: float | None, then: float | None, years: float) -> float | None:
    if now is None or then is None or now <= 0 or then <= 0 or years <= 0:
        return None
    return (now / then) ** (1.0 / years) - 1.0


def _median(xs: list[float]) -> float | None:
    xs = [x for x in xs if x is not None and math.isfinite(x)]
    return statistics.median(xs) if xs else None


def fit_log_trend(values: list[float]) -> dict | None:
    """ln(v) 对序号的 OLS: → {slope_q, annual_growth, fitted_last, r2, n}。v ≤ 0 的点剔除。
    用途: 周期票的「趋势收入」与「趋势增速」—— 首尾点 CAGR 在谷→峰区间有端点偏差, 斜率没有。"""
    pts = [(i, math.log(v)) for i, v in enumerate(values) if v is not None and v > 0]
    n = len(pts)
    if n < 3:
        return None
    mx = sum(i for i, _ in pts) / n
    my = sum(y for _, y in pts) / n
    sxx = sum((i - mx) ** 2 for i, _ in pts)
    if sxx == 0:
        return None
    b = sum((i - mx) * (y - my) for i, y in pts) / sxx
    a = my - b * mx
    ss_tot = sum((y - my) ** 2 for _, y in pts)
    ss_res = sum((y - (a + b * i)) ** 2 for i, y in pts)
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
    last_i = len(values) - 1
    return {"slope_q": b, "annual_growth": math.exp(4 * b) - 1, "fitted_last": math.exp(a + b * last_i),
            "r2": r2, "n": n}


def _percentile_rank(xs: list[float], x: float) -> float | None:
    xs = [v for v in xs if v is not None]
    if not xs:
        return None
    return sum(1 for v in xs if v <= x) / len(xs)


# ── 驱动推导 ────────────────────────────────────────────────────────

def filter_history(rows: list[dict], history_start: str | None) -> list[dict]:
    """人工区 history_start (YYYY-MM-DD): 排除不属于当前商业模式的历史 (NBIS 的 Yandex 年代)。"""
    if not history_start:
        return list(rows)
    return [r for r in rows if (r.get("period_end") or "") >= history_start]


def derive_drivers(bundle: dict, params: dict, overrides: dict | None = None) -> dict:
    """三表 + 快照 → 估值驱动 (全部带 basis)。抛 ValueError 的只有「连 TTM 都凑不齐」。"""
    overrides = overrides or {}
    rows = filter_history(bundle.get("quarters") or [], overrides.get("history_start"))
    if len(rows) < TTM_QUARTERS:
        raise ValueError(f"季度不足 {TTM_QUARTERS} 个 (有 {len(rows)}), 无法构成 TTM")
    k = len(rows) - 1
    snap = bundle.get("snapshot") or {}
    d: dict = {"quarters_used": len(rows), "history_span": f"{rows[0]['period']}–{rows[-1]['period']}",
               "latest_period": rows[-1]["period"], "latest_period_end": rows[-1].get("period_end")}
    flags: list[str] = []

    def T(field: str, back: int = 0) -> float | None:
        return ttm_at(rows, k - back, field)

    rev = T("revenue")
    if rev is None or rev <= 0:
        raise ValueError("TTM 收入缺失或非正, 模型无从起步")
    op = T("operating_profit")
    if op is None:
        raise ValueError("TTM 营业利润缺失")
    d["revenue_ttm"] = rev
    d["operating_profit_ttm"] = op
    d["opm_ttm"] = op / rev
    lq = rows[-1]
    d["revenue_last_q"] = lq.get("revenue")
    d["revenue_runrate"] = (lq["revenue"] * 4) if lq.get("revenue") else None
    d["runrate_vs_ttm"] = (d["revenue_runrate"] / rev) if d["revenue_runrate"] else None
    d["opm_last_q"] = (lq["operating_profit"] / lq["revenue"]) if (lq.get("revenue") and lq.get("operating_profit") is not None) else None

    # 增速候选 (TTM yoy / 3y / 5y / 10y CAGR), 缺哪个写 None
    d["growth_candidates"] = {
        "yoy": _cagr(rev, T("revenue", 4), 1),
        "cagr3": _cagr(rev, T("revenue", 12), 3),
        "cagr5": _cagr(rev, T("revenue", 20), 5),
        "cagr10": _cagr(rev, T("revenue", 40), 10),
    }
    d["revenue_decel"] = None
    if d["growth_candidates"]["yoy"] is not None:
        prev_yoy = _cagr(T("revenue", 4), T("revenue", 8), 1)
        if prev_yoy is not None:
            d["revenue_decel"] = d["growth_candidates"]["yoy"] - prev_yoy   # 增速二阶导 (双杀形状)

    # 收入对数趋势 (滚动 TTM 收入序列): 周期票的归一化收入与趋势增速
    rev_hist = [ttm_at(rows, i, "revenue") for i in range(TTM_QUARTERS - 1, len(rows))]
    tr = fit_log_trend(rev_hist)
    d["revenue_trend"] = tr
    d["revenue_vs_trend"] = (rev / tr["fitted_last"]) if tr and tr["fitted_last"] > 0 else None

    # 利润率历史 (滚动 TTM 营业利润率) → 中位 / 分位
    opm_hist: list[float] = []
    for i in range(TTM_QUARTERS - 1, len(rows)):
        r_i, o_i = ttm_at(rows, i, "revenue"), ttm_at(rows, i, "operating_profit")
        if r_i and r_i > 0 and o_i is not None:
            opm_hist.append(o_i / r_i)
    d["opm_history_n"] = len(opm_hist)
    win = int(params.get("margin_window_q") or 0) if params else 0
    if "margin_window_q" in overrides:
        win = int(overrides["margin_window_q"])
    d["opm_median_full"] = _median(opm_hist)
    d["opm_median"] = _median(opm_hist[-win:]) if (win and opm_hist) else d["opm_median_full"]
    d["opm_window_q"] = win or len(opm_hist)
    d["opm_p25"] = _median(sorted(opm_hist)[: max(1, len(opm_hist) // 2)]) if opm_hist else None
    d["opm_p75"] = _median(sorted(opm_hist)[len(opm_hist) // 2:]) if opm_hist else None
    d["opm_percentile"] = _percentile_rank(opm_hist, d["opm_ttm"])

    # 税率: 税前为正才算, 夹逼; 否则缺省并打旗
    pretax, tax = T("pretax"), T("tax")
    if pretax is not None and pretax > 0 and tax is not None and tax >= 0:
        d["tax_rate"] = _clamp(tax / pretax, *TAX_CLAMP)
        d["tax_basis"] = "TTM 所得税/税前 (夹逼 5–35%)"
    else:
        d["tax_rate"] = DEFAULT_TAX
        d["tax_basis"] = f"缺省 {DEFAULT_TAX:.0%} (税前非正或字段缺失)"
        flags.append("税率用缺省")
    if "tax_rate" in overrides:
        d["tax_rate"], d["tax_basis"] = float(overrides["tax_rate"]), "override"
    tau = d["tax_rate"]

    # 一次性损益 (其他损益 + 证券处置) 占税前 —— 报表 EPS vs 核心 EPS 的差
    net_int_ttm = T("net_interest")
    q_ints = [rows[i].get("net_interest") for i in range(k - TTM_QUARTERS + 1, k + 1)]
    net_int = (statistics.median(q_ints) * 4) if all(v is not None for v in q_ints) else net_int_ttm
    if net_int_ttm is not None and net_int is not None and abs(net_int_ttm - net_int) > 0.02 * rev:
        flags.append(f"净利息 TTM {net_int_ttm/1e6:+,.0f}M 与季中位年化 {net_int/1e6:+,.0f}M 差 >2% 收入 (某季混入一次性), 用中位年化")
    nonop = (pretax - op - (net_int_ttm or 0.0)) if pretax is not None else None
    d["oneoff_ttm"] = nonop
    d["oneoff_basis"] = "税前 − 营业利润 − 净利息 (残差口径)"
    d["oneoff_share_of_pretax"] = (nonop / pretax) if (nonop is not None and pretax and pretax > 0 and op > 0) else None
    if d["oneoff_share_of_pretax"] is not None and abs(d["oneoff_share_of_pretax"]) > ONEOFF_WARN_SHARE:
        flags.append(f"非经营损益占税前 {d['oneoff_share_of_pretax']:+.0%}: 报表 EPS 失真, 用核心 EPS")
    elif nonop is not None and op <= 0 and abs(nonop) > 0.2 * abs(op):
        flags.append(f"非经营损益 {nonop/1e6:+,.0f}M vs 营业利润 {op/1e6:,.0f}M: 报表 EPS 失真, 用核心 EPS")

    d["net_interest_ttm"] = net_int_ttm
    d["net_interest_annualized_median"] = net_int
    d["net_interest_after_tax"] = (net_int or 0.0) * (1 - tau)
    d["nopat_ttm"] = op * (1 - tau) if op > 0 else op
    d["core_net_income_ttm"] = (op + (net_int or 0.0)) * (1 - tau) if (op + (net_int or 0.0)) > 0 \
        else (op + (net_int or 0.0))
    d["reported_net_income_ttm"] = T("net_income")

    # 股本: 近 4 季 净利/稀释EPS 的中位 (稀释口径), 与快照流通股交叉校验
    derived = []
    for i in range(k - TTM_QUARTERS + 1, k + 1):
        ni, eps = rows[i].get("net_income"), rows[i].get("diluted_eps")
        if ni and eps and eps != 0 and (ni > 0) == (eps > 0):
            derived.append(ni / eps)
    px_snap, mcap_snap = snap.get("last_price"), snap.get("total_market_val")
    anchor = (mcap_snap / px_snap) if (mcap_snap and px_snap) else snap.get("outstanding_shares")
    anchor_basis = "市值/现价" if (mcap_snap and px_snap) else "快照 outstanding_shares"
    if derived:
        sh = statistics.median(derived)
        if anchor and abs(sh / anchor - 1) > 0.15:
            flags.append(f"稀释股数推导 {sh/1e6:,.0f}M 与{anchor_basis} {anchor/1e6:,.0f}M 偏差 >15%, 用后者")
            d["shares"], d["shares_basis"] = float(anchor), f"{anchor_basis} (推导偏差过大)"
        else:
            d["shares"], d["shares_basis"] = float(sh), "近 4 季 净利/稀释EPS 中位"
    elif anchor:
        d["shares"], d["shares_basis"] = float(anchor), anchor_basis
    else:
        raise ValueError("股本推不出来 (无有效 EPS 且快照无市值/流通股)")
    d["eps_reported_ttm"] = (d["reported_net_income_ttm"] / d["shares"]) if d["reported_net_income_ttm"] is not None else None
    d["eps_core_ttm"] = d["core_net_income_ttm"] / d["shares"]

    # 资产负债表 (最新季)
    last = rows[-1]
    cash = last.get("cash_sti") or 0.0
    lti = last.get("lt_investments") or 0.0
    debt = (last.get("current_debt") or 0.0) + (last.get("lt_debt") or 0.0)
    leases = (last.get("current_lease") or 0.0) + (last.get("lt_lease") or 0.0)
    equity_book = last.get("total_equity")
    d.update({"cash_sti": cash, "lt_investments": lti, "debt": debt, "leases": leases,
              "net_cash_ex_leases": cash - debt, "book_equity": equity_book,
              "goodwill_intangibles": last.get("goodwill_intangibles") or 0.0,
              "net_ppe": last.get("net_ppe")})
    if last.get("cash_sti") is None or last.get("total_equity") is None:
        flags.append("最新季资产负债表字段缺失 (现金/权益)")

    # 投入资本 / ROIC / 销售资本比
    def ic_at(i: int, ex_goodwill: bool = False) -> float | None:
        r = rows[i]
        if r.get("total_equity") is None:
            return None
        ic = (r["total_equity"] + (r.get("current_debt") or 0) + (r.get("lt_debt") or 0)
              + (r.get("current_lease") or 0) + (r.get("lt_lease") or 0)
              - (r.get("cash_sti") or 0) - (r.get("lt_investments") or 0))
        return ic - (r.get("goodwill_intangibles") or 0) if ex_goodwill else ic

    def ic_avg_of(ex_goodwill: bool) -> float | None:
        now, prev = ic_at(k, ex_goodwill), (ic_at(k - 4, ex_goodwill) if k >= 4 else None)
        if now is not None and prev is not None:
            return (now + prev) / 2
        return now
    ic_avg, ic_op_avg = ic_avg_of(False), ic_avg_of(True)
    d["invested_capital"] = ic_at(k)
    d["invested_capital_ex_goodwill"] = ic_at(k, True)
    d["roic_ttm"] = (d["nopat_ttm"] / ic_avg) if ic_avg and ic_avg > 0 else None       # 全口径 (含并购商誉, 记账用)
    # 再投资强度与新增资本回报用**经营口径** (剔商誉无形): 过去并购付的溢价不是未来每一元增长要再投的钱
    # (AMD Xilinx / COHR / MRVL Inphi 商誉占投入资本一半以上, 全口径会把 s2c 压到 0.5 以下)
    if ic_op_avg is not None and ic_op_avg > 0:
        d["roic_ex_goodwill"] = d["nopat_ttm"] / ic_op_avg
        d["sales_to_capital"] = _clamp(rev / ic_op_avg, *S2C_CLAMP)
        d["s2c_basis"] = "TTM 收入 / 平均投入资本 (剔商誉无形, 夹逼 0.3–8)"
    else:
        d["roic_ex_goodwill"] = None
        d["sales_to_capital"] = params.get("s2c_default")
        d["s2c_basis"] = f"经营投入资本非正 → 原型缺省 {params.get('s2c_default')}"
        flags.append("经营投入资本非正 (现金多于资本), 销售资本比用原型缺省")
    roic_hist: list[float] = []
    for i in range(TTM_QUARTERS - 1, len(rows)):
        o_i, ic_i = ttm_at(rows, i, "operating_profit"), ic_at(i, True)
        if o_i is not None and ic_i and ic_i > 0:
            roic_hist.append(o_i * (1 - tau) / ic_i)
    d["roic_median"] = _median(roic_hist)           # 经营口径中位 → RONIC_T 的候选
    floor = params.get("s2c_floor")
    if floor is not None and d["sales_to_capital"] is not None and d["sales_to_capital"] < floor:
        d["s2c_basis"] = f"{d['s2c_basis']} → 原型地板 {floor} (扩产期资本占用非稳态, 原值 {d['sales_to_capital']:.2f})"
        d["sales_to_capital"] = float(floor)
    if "sales_to_capital" in overrides:
        d["sales_to_capital"], d["s2c_basis"] = float(overrides["sales_to_capital"]), "override"

    # 现金流特征
    capex = T("capex")
    d["capex_ttm"] = capex
    d["capex_intensity"] = (abs(capex) / rev) if capex is not None else None
    d["fcf_ttm"] = T("fcf")
    d["ocf_ttm"] = T("ocf")
    d["da_ttm"] = T("da")

    # 市值 / 股东收益率 / 股本变化
    price = snap.get("last_price")
    mcap = snap.get("total_market_val") or (price * d["shares"] if price else None)
    d["price"], d["mcap"] = price, mcap
    nsi, div = T("net_stock_issuance"), T("dividends_paid")
    d["buyback_yield"] = (-nsi / mcap) if (nsi is not None and mcap) else None
    d["dividend_yield"] = (abs(div) / mcap) if (div is not None and mcap) else None
    d["dps_ttm"] = ttm_at(rows, k, "dps") if all(rows[i].get("dps") is not None for i in range(k - 3, k + 1)) else None
    d["share_change_rate"] = _clamp(d["buyback_yield"], *SHARE_CHANGE_CLAMP) if d["buyback_yield"] is not None else 0.0
    d["flags"] = flags
    return d


# ── β 与资本成本 ────────────────────────────────────────────────────

def beta_from_klines(rows_t: list[dict], rows_m: list[dict], *, min_obs: int = BETA_MIN_OBS,
                     max_obs: int = BETA_MAX_OBS) -> dict | None:
    """日对数收益回归 β (cov/var), Blume 调整。K 线缓存只含已完成日线 (heartbeat 盘中闸)。"""
    def closes(rows: list[dict]) -> dict[str, float]:
        out = {}
        for r in rows:
            t, c = str(r.get("time") or "")[:10], r.get("close")
            if t and c:
                out[t] = float(c)
        return out
    ct, cm = closes(rows_t), closes(rows_m)
    dates = sorted(set(ct) & set(cm))[-(max_obs + 1):]
    if len(dates) < min_obs + 1:
        return None
    rt = [math.log(ct[dates[i]] / ct[dates[i - 1]]) for i in range(1, len(dates))]
    rm = [math.log(cm[dates[i]] / cm[dates[i - 1]]) for i in range(1, len(dates))]
    n = len(rt)
    mt, mm = sum(rt) / n, sum(rm) / n
    var_m = sum((x - mm) ** 2 for x in rm) / (n - 1)
    if var_m <= 0:
        return None
    cov = sum((a - mt) * (b - mm) for a, b in zip(rt, rm)) / (n - 1)
    raw = cov / var_m
    return {"raw": raw, "adjusted": BETA_BLUME[0] * raw + BETA_BLUME[1], "n_obs": n,
            "window": f"{dates[1]}–{dates[-1]}"}


def beta_from_monthly(prices_t: dict[str, float], prices_m: dict[str, float], *, as_of: str | None = None,
                      years: int = BETA_MONTHLY_YEARS, min_months: int = BETA_MIN_MONTHS) -> dict | None:
    """5 年月末收盘的对数收益回归 β (Damodaran/Bloomberg 口径之一), Blume 调整。prices = {日期: 收盘}。"""
    def month_ends(prices: dict[str, float]) -> dict[str, float]:
        out: dict[str, float] = {}
        for d in sorted(prices):
            if as_of and d > as_of:
                break
            out[d[:7]] = prices[d]          # 同月后写的赢 = 月末
        return out
    mt, mm = month_ends(prices_t), month_ends(prices_m)
    months = sorted(set(mt) & set(mm))[-(years * 12 + 1):]
    if len(months) < min_months + 1:
        return None
    rt = [math.log(mt[months[i]] / mt[months[i - 1]]) for i in range(1, len(months))]
    rm = [math.log(mm[months[i]] / mm[months[i - 1]]) for i in range(1, len(months))]
    n = len(rt)
    at, am = sum(rt) / n, sum(rm) / n
    var_m = sum((x - am) ** 2 for x in rm) / (n - 1)
    if var_m <= 0:
        return None
    cov = sum((a - at) * (b - am) for a, b in zip(rt, rm)) / (n - 1)
    raw = cov / var_m
    return {"raw": raw, "adjusted": BETA_BLUME[0] * raw + BETA_BLUME[1], "n_obs": n,
            "window": f"{months[1]}–{months[-1]} 月度"}


def cost_of_capital(drivers: dict, *, rf: float, erp: float, beta: dict | None,
                    overrides: dict | None = None, terminal_growth: float = 0.03,
                    industry_beta: float | None = None) -> dict:
    overrides = overrides or {}
    tau = drivers["tax_rate"]
    if "beta" in overrides:
        b, b_basis = float(overrides["beta"]), "override"
    elif industry_beta:
        b = _clamp(float(industry_beta), *BETA_CLAMP)
        cross = (f"; 交叉: 个股 5 年月度回归 {beta['adjusted']:.2f} (raw {beta['raw']:.2f}, {beta['n_obs']} 月)" if beta
                 else f"; 个股月度回归不足 {BETA_MIN_MONTHS} 月")
        b_basis = f"行业 β {industry_beta:.2f} (Damodaran 2026-01 自下而上, 回归 β 只作交叉)" + cross
    elif beta:
        b = _clamp(beta["adjusted"], *BETA_CLAMP)
        b_basis = f"个股回归 raw {beta['raw']:.2f} Blume 调整 {beta['adjusted']:.2f}" + \
            (f" 夹逼到 {b:.2f}" if b != beta["adjusted"] else "") + f" ({beta['n_obs']} {beta['window']})"
    else:
        b, b_basis = 1.0, "缺省 1.0 (无行业 β 且回归不足)"
    re = _clamp(rf + b * erp, rf + COST_OF_EQUITY_CLAMP[0], COST_OF_EQUITY_CLAMP[1])
    debt = drivers.get("debt") or 0.0
    ie = drivers.get("net_interest_ttm")
    rd_basis = f"rf + 1.5pp"
    rd = rf + 0.015
    # 净利息为负且有债 → 利息支出/债务 作为债务成本 (净口径, 现金多的公司会偏低 → 夹逼)
    if debt > 0 and ie is not None and ie < 0:
        cand = -ie / debt
        if COST_OF_DEBT_CLAMP[0] <= cand <= COST_OF_DEBT_CLAMP[1]:
            rd, rd_basis = cand, "TTM 净利息支出 / 有息债"
    mcap = drivers.get("mcap") or 0.0
    w_d = debt / (debt + mcap) if (debt + mcap) > 0 else 0.0
    wacc = (1 - w_d) * re + w_d * rd * (1 - tau)
    wacc_floor = max(rf + 0.02, terminal_growth + 0.02)
    wacc = max(wacc, wacc_floor)
    basis = f"WACC = {1-w_d:.0%}×r_e {re:.2%} + {w_d:.0%}×r_d {rd:.2%}×(1−τ)"
    if "wacc" in overrides:
        wacc, basis = float(overrides["wacc"]), "override"
    return {"rf": rf, "erp": erp, "beta": b, "beta_basis": b_basis, "cost_of_equity": re,
            "cost_of_debt": rd, "cost_of_debt_basis": rd_basis, "debt_weight": w_d,
            "wacc": wacc, "wacc_basis": basis}


# ── 路径与引擎 ──────────────────────────────────────────────────────

def growth_path(g0: float, gT: float, n: int, fade: str = "linear", half_life: float | None = None) -> list[float]:
    if n <= 0:
        return []
    if n == 1:
        return [g0]
    if fade == "flat":
        return [g0] * n
    out = []
    for t in range(1, n + 1):
        if fade == "exp" and half_life:
            g = gT + (g0 - gT) * 0.5 ** ((t - 1) / half_life)
        else:
            g = g0 + (gT - g0) * (t - 1) / (n - 1)
        out.append(g)
    return out


def margin_path(m0: float, mT: float, n: int, fade_years: int) -> list[float]:
    f = max(1, int(fade_years or 1))
    return [m0 + (mT - m0) * min(1.0, t / f) for t in range(1, n + 1)]


def resolve_growth_start(drivers: dict, params: dict, overrides: dict) -> tuple[float, str]:
    if "growth_start" in overrides:
        return float(overrides["growth_start"]), "override"
    c = drivers["growth_candidates"]
    src = params.get("growth_source")
    if src == "blend":
        pool = [c["yoy"], c["cagr3"]]
        basis = "均(TTM yoy, 3y CAGR)"
    elif src == "long_run":
        tr = drivers.get("revenue_trend")
        min_r2 = float(overrides.get("trend_min_r2", MIN_TREND_R2))
        if tr and tr["n"] >= MIN_TREND_QUARTERS and tr["r2"] >= min_r2:
            g = tr["annual_growth"]
            g_c = _clamp(g, params.get("growth_floor"), params.get("growth_cap"))
            basis = f"收入对数趋势斜率 ({tr['n']} 季, R² {tr['r2']:.2f})"
            if g_c != g:
                basis += f", 原值 {g:.1%} 夹逼到 [{params.get('growth_floor')}, {params.get('growth_cap')}]"
            return g_c, basis
        pool = [c["cagr10"], c["cagr5"], c["cagr3"]]
        basis = "中位(10y/5y/3y CAGR) (趋势不可用)"
    else:
        pool = [c["yoy"], c["cagr3"]]
        basis = "TTM yoy"
    vals = [v for v in pool if v is not None]
    if not vals:
        vals = [v for v in c.values() if v is not None] or [params.get("terminal_growth") or 0.03]
        basis += " (候选缺失, 退回可得项)"
    g = statistics.median(vals) if src == "long_run" else (vals[0] if src == "ttm" else sum(vals) / len(vals))
    g_c = _clamp(g, params.get("growth_floor"), params.get("growth_cap"))
    if g_c != g:
        basis += f", 原值 {g:.1%} 夹逼到 [{params.get('growth_floor')}, {params.get('growth_cap')}]"
    return g_c, basis


def resolve_margin_target(drivers: dict, params: dict, overrides: dict, margin_1: float | None = None) -> tuple[float, str, list[str]]:
    flags: list[str] = []
    if "target_margin" in overrides:
        return float(overrides["target_margin"]), "override", flags
    m = params.get("margin_method")
    hold = drivers["opm_ttm"] if margin_1 is None else margin_1
    if m == "through_cycle":
        if drivers.get("opm_median") is not None and drivers.get("opm_history_n", 0) >= MIN_CYCLE_QUARTERS:
            return drivers["opm_median"], f"近 {drivers.get('opm_window_q')} 季 TTM 中位", flags
        flags.append("利润率历史不足 3 年, through_cycle 退回维持第 1 年")
        return hold, "第 1 年利润率 (历史不足退回)", flags
    if m == "target":
        return float(params.get("target_margin")), "原型缺省成熟期利润率 (假设)", flags
    return hold, "维持第 1 年利润率", flags


def resolve_base(drivers: dict, params: dict, overrides: dict, forward: dict | None) -> dict:
    """第 1 年锚点 (v2 核心): 收入与利润率优先取卖方一致前瞻, 其次最近季年化 (陡峭爬坡), 最后 TTM×(1+g)。
    第 2 年起增速 = 历史趋势/CAGR 判据 (resolve_growth_start) 封顶 growth_cap_after。"""
    fw = forward or {}
    use_cons = bool(overrides.get("use_consensus", True))
    rev = drivers["revenue_ttm"]
    tau = drivers["tax_rate"]
    flags: list[str] = []
    g0, g0_basis = resolve_growth_start(drivers, params, overrides)
    fwd_rev, fwd_eps = fw.get("forward_revenue"), fw.get("forward_eps")
    consensus_rev = bool(use_cons and fwd_rev and fwd_rev > 0)
    if consensus_rev:
        rev_1 = float(fwd_rev)
        rev_basis = f"一致前瞻收入 ${rev_1/1e9:,.1f}B (TTM ×{rev_1/rev:.2f}, OpenD forward PS 反推)"
    elif drivers.get("runrate_vs_ttm") and drivers["runrate_vs_ttm"] > RUNRATE_RAMP:
        rev_1 = float(drivers["revenue_runrate"])
        rev_basis = f"最近季年化 ${rev_1/1e9:,.1f}B (TTM ×{rev_1/rev:.2f}; 陡峭爬坡且无一致预期)"
        flags.append("无一致前瞻收入, 第 1 年用最近季年化")
    else:
        rev_1 = rev * (1 + g0)
        rev_basis = f"TTM × (1 + {g0:.1%} {g0_basis})"
    g_1 = rev_1 / rev - 1
    if "growth_start" in overrides:
        g_after, g_after_basis = float(overrides["growth_start"]), "override"
    else:
        cap = overrides.get("growth_cap_after", params.get("growth_cap_after"))
        anchored = consensus_rev or (drivers.get("runrate_vs_ttm") and drivers["runrate_vs_ttm"] > RUNRATE_RAMP)
        if anchored and g_1 > g0:
            g_raw = (g_1 + g0) / 2      # 年 1 跳变与历史趋势各半: 卖方对 2–3 年的外推介于两者之间, 再封顶
            g_after_basis = f"均(年1 跳变 {g_1:.0%}, {g0_basis} {g0:.1%})"
        else:
            g_raw, g_after_basis = g0, g0_basis
        g_after = _clamp(g_raw, params.get("growth_floor"), cap)
        if cap is not None and g_after != g_raw:
            g_after_basis += f", 封顶 {cap:.0%}"
    consensus_eps = bool(use_cons and fwd_eps is not None and drivers.get("shares"))
    if consensus_eps:
        ni_1 = float(fwd_eps) * drivers["shares"]
        nopat_1 = ni_1 - (drivers.get("net_interest_after_tax") or 0.0)
        m_raw = (nopat_1 / (1 - tau)) / rev_1 if nopat_1 > 0 else nopat_1 / rev_1
        m_1 = _clamp(m_raw, *MARGIN_1_CLAMP)
        m_basis = f"一致前瞻 EPS ${fwd_eps:,.2f} 反推 (营业利润率 {m_1:.1%}" + (f", 原值 {m_raw:.1%} 夹逼" if m_1 != m_raw else "") + ")"
    elif drivers.get("runrate_vs_ttm") and drivers["runrate_vs_ttm"] > RUNRATE_RAMP and drivers.get("opm_last_q") is not None:
        m_1, m_basis = float(drivers["opm_last_q"]), f"最近季利润率 {drivers['opm_last_q']:.1%} (陡峭爬坡)"
    else:
        m_1, m_basis = float(drivers["opm_ttm"]), f"TTM 利润率 {drivers['opm_ttm']:.1%}"
    return {"revenue_1": rev_1, "growth_1": g_1, "growth_after": g_after, "margin_1": m_1,
            "revenue_basis": rev_basis, "growth_basis": g_after_basis, "margin_basis": m_basis,
            "consensus_revenue": consensus_rev, "consensus_eps": consensus_eps, "flags": flags,
            "forward_pe": (drivers.get("price") / fwd_eps) if (fwd_eps and fwd_eps > 0 and drivers.get("price")) else None,
            "ev_to_forward_sales": (((drivers.get("mcap") or 0) - (drivers.get("net_cash_ex_leases") or 0) - (drivers.get("lt_investments") or 0)) / fwd_rev)
            if (fwd_rev and fwd_rev > 0 and drivers.get("mcap")) else None}


def resolve_revenue_base(drivers: dict, params: dict, overrides: dict) -> tuple[float, int, str, list[str]]:
    """→ (趋势/实际 收入比, 回归年数, basis, flags)。actual → ratio 1.0。"""
    flags: list[str] = []
    method = overrides.get("revenue_method", params.get("revenue_method") or "actual")
    fade = int(overrides.get("revenue_fade_years", params.get("revenue_fade_years") or 1))
    if method != "trend":
        return 1.0, 1, "TTM 实际", flags
    tr = drivers.get("revenue_trend")
    min_r2 = float(overrides.get("trend_min_r2", MIN_TREND_R2))
    if not tr or tr["n"] < MIN_TREND_QUARTERS or tr["r2"] < min_r2:
        why = "无趋势" if not tr else f"{tr['n']} 季 / R² {tr['r2']:.2f} 不达门槛 {min_r2}"
        flags.append(f"收入趋势归一化退回 TTM 实际 ({why})")
        return 1.0, 1, f"TTM 实际 (趋势不可用: {why})", flags
    raw = drivers["revenue_vs_trend"]
    ratio = _clamp(1.0 / raw, *REVENUE_RATIO_CLAMP)
    if raw > REVENUE_CYCLE_FLAG[1]:
        flags.append(f"周期峰值收入: TTM 收入是 10 年趋势线的 {raw:.2f}×, {fade} 年回归")
    elif raw < REVENUE_CYCLE_FLAG[0]:
        flags.append(f"周期谷底收入: TTM 收入只有趋势线的 {raw:.2f}×, {fade} 年回归")
    return ratio, fade, f"趋势线 ({tr['n']} 季, R² {tr['r2']:.2f}) = TTM × {ratio:.2f}, {fade} 年回归", flags


def apply_overlays_market(rf: float, erp: float, overlays: list[dict], macro_context: dict | None) -> dict:
    """市场段只应用显式 rf/erp 增量；宏观标签留作上下文，不代表可量化的风险溢价。"""
    rf_d = sum(float(o["delta"]) for o in overlays if o["driver"] == "rf")
    erp_d = sum(float(o["delta"]) for o in overlays if o["driver"] == "erp")
    level = (macro_context or {}).get("risk_level")
    tilt_basis = f"宏观风险 {level}: 仅研究上下文；ERP 只用显式参数和 overlay" if level else "无宏观状态"
    # 保留字段兼容已有渲染与账本读取；0 仅表示没有由标签产生的额外调整。
    return {"rf": rf + rf_d, "erp": erp + erp_d, "rf_delta": rf_d, "erp_delta": erp_d,
            "erp_tilt": 0.0, "erp_tilt_basis": tilt_basis}


def apply_overlays_spec(spec: dict, overlays: list[dict]) -> dict:
    """驱动段: EPS 通道 + terminal_growth / wacc。乘法驱动 ×(1+δ), 其余加法。返回新 dict。"""
    p = dict(spec)
    for o in overlays:
        drv, delta = o["driver"], float(o["delta"])
        if drv in MULTIPLICATIVE_DRIVERS and drv in p:
            p[drv] = p[drv] * (1 + delta)
        elif drv == "wacc":
            p["wacc"] = p["wacc"] + delta
            p["cost_of_equity"] = p["cost_of_equity"] + delta
        elif drv in p and drv not in ("pe_multiplier", "erp", "rf", "scenario_tilt"):
            p[drv] = p[drv] + delta
    p["tax_rate"] = _clamp(p["tax_rate"], 0.0, 0.5)
    p["terminal_growth"] = min(p["terminal_growth"], p["wacc"] - 0.02)
    return p


def pe_multiplier_of(overlays: list[dict]) -> float:
    m = 1.0
    for o in overlays:
        if o["driver"] == "pe_multiplier":
            m *= 1 + float(o["delta"])
    return m


def tilt_probabilities(probs: dict, overlays: list[dict]) -> dict:
    """概率段: scenario_tilt δ>0 把 δ 从熊搬到牛, δ<0 反之; 每档 ≥ PROB_FLOOR 后归一。"""
    t = sum(float(o["delta"]) for o in overlays if o["driver"] == "scenario_tilt")
    if t == 0:
        return dict(probs)
    p = dict(probs)
    if t > 0:
        mv = min(t, p["bear"] - PROB_FLOOR)
        p["bear"] -= mv
        p["bull"] += mv
    else:
        mv = min(-t, p["bull"] - PROB_FLOOR)
        p["bull"] -= mv
        p["bear"] += mv
    tot = sum(p.values())
    return {k: v / tot for k, v in p.items()}


def apply_overlay(base: dict, overlay: list[dict]) -> dict:
    """兼容旧名: 驱动段应用。"""
    return apply_overlays_spec(base, overlay)


def project(spec: dict) -> list[dict]:
    """spec: revenue_base (TTM), growth_year1 (第 1 年跳变, 缺省 = growth_start), growth_start (第 2 年起的起始增速),
    terminal_growth, horizon_years, fade, half_life, margin_start (= 第 1 年利润率), target_margin, margin_fade_years
    (从第 1 年起算), tax_rate, sales_to_capital, shares, share_change_rate, net_interest_after_tax, dps."""
    n = int(spec["horizon_years"])
    g1 = spec.get("growth_year1")
    if g1 is None:
        gs = growth_path(spec["growth_start"], spec["terminal_growth"], n, spec.get("fade") or "linear", spec.get("half_life"))
    else:
        gs = [float(g1)] + growth_path(spec["growth_start"], spec["terminal_growth"], n - 1, spec.get("fade") or "linear", spec.get("half_life"))
    f = max(1, int(spec.get("margin_fade_years") or 1))
    m1, mT = spec["margin_start"], spec["target_margin"]
    ms = [m1 + (mT - m1) * min(1.0, (t - 1) / f) if f > 1 else (m1 if t == 1 else mT) for t in range(1, n + 1)]
    tau, s2c = spec["tax_rate"], spec["sales_to_capital"]
    rev_prev, shares = spec["revenue_base"], spec["shares"]
    ratio, rf_y = float(spec.get("revenue_level_ratio", 1.0) or 1.0), max(1, int(spec.get("revenue_fade_years", 1) or 1))
    lvl_prev = 1.0
    out = []
    for t in range(1, n + 1):
        lvl = ratio ** min(1.0, t / rf_y)            # 收入水平向趋势线回归 (ratio<1 = 从峰值回落)
        rev = rev_prev * (1 + gs[t - 1]) * (lvl / lvl_prev)
        lvl_prev = lvl
        ebit = rev * ms[t - 1]
        nopat = ebit * (1 - tau) if ebit > 0 else ebit          # 亏损年不记税盾 (保守, 声明)
        reinvest = max(0.0, rev - rev_prev) / s2c
        fcf = nopat - reinvest
        shares = shares * (1 - spec.get("share_change_rate", 0.0))
        eps = (nopat + spec.get("net_interest_after_tax", 0.0)) / shares
        out.append({"year": t, "growth": gs[t - 1], "revenue": rev, "level": lvl, "margin": ms[t - 1], "nopat": nopat,
                    "reinvest": reinvest, "fcf": fcf, "shares": shares, "eps": eps,
                    "dps": spec.get("dps") or 0.0})
        rev_prev = rev
    return out


def _pv_sum(path: list[dict], rate: float) -> tuple[float, float]:
    pv, wt = 0.0, 0.0
    for r in path:
        t = r["year"] - MIDYEAR                       # 中年折现
        f = r["fcf"] / (1 + rate) ** t
        pv += f
        wt += t * f
    return pv, wt


def dcf_value(path: list[dict], *, wacc: float, terminal_growth: float, ronic_terminal: float,
              cash_sti: float, lt_investments: float, debt: float, shares: float) -> dict:
    n = path[-1]["year"]
    nopat_n = path[-1]["nopat"]
    reinvest_rate = terminal_growth / ronic_terminal if ronic_terminal > 0 else 0.0
    fcf_next = nopat_n * (1 + terminal_growth) * (1 - reinvest_rate)

    def ev_at(rate: float) -> tuple[float, float, float, float]:
        pv_exp, wt = _pv_sum(path, rate)
        spread = rate - terminal_growth
        tv = fcf_next / spread if spread > 0.005 else float("nan")
        pv_tv = tv / (1 + rate) ** (n - MIDYEAR)      # 终值与中年现金流同拍
        return pv_exp, pv_tv, wt, tv

    pv_exp, pv_tv, wt, tv = ev_at(wacc)
    ev = pv_exp + pv_tv
    equity = ev + cash_sti + lt_investments - debt
    per_share = equity / shares if shares else float("nan")

    def ps_at(rate: float) -> float:
        e, t, _, _ = ev_at(rate)
        return (e + t + cash_sti + lt_investments - debt) / shares

    duration_years = ((wt + (n - MIDYEAR) * pv_tv) / ev) if ev > 0 else None
    sens = None
    eff_dur = None
    if per_share and per_share > 0 and math.isfinite(per_share):
        up, dn = ps_at(wacc + 0.005), ps_at(wacc - 0.005)
        eff_dur = (dn - up) / (2 * 0.005 * per_share)
        sens = ps_at(wacc + 0.01) / per_share - 1
    return {"ev": ev, "pv_explicit": pv_exp, "pv_terminal": pv_tv, "terminal_value": tv,
            "tv_share": (pv_tv / ev) if ev > 0 else None, "equity": equity, "per_share": per_share,
            "fcf_next": fcf_next, "ronic_terminal": ronic_terminal, "reinvest_rate_terminal": reinvest_rate,
            "duration_years": duration_years, "effective_duration": eff_dur,
            "rate_sensitivity_100bp": sens}


def justified_pe(g: float, ronic: float, r: float) -> float | None:
    """稳态合理 PE (McKinsey 价值驱动式): (1 − g/RONIC)(1+g)/(r − g)。"""
    if r - g <= 0.005 or ronic <= 0:
        return None
    return (1 - g / ronic) * (1 + g) / (r - g)


def path_justified_pe(path: list[dict], T: int, *, cost_of_equity: float, terminal_growth: float,
                      ronic_terminal: float) -> float | None:
    """T 年末的路径一致合理 PE: 之后各年 EPS × 派息率 (1 − 再投资/NOPAT, 同一条路径) 折现
    + 稳态终值, 除以 EPS_T。增速回到 gT 时退化为稳态式 (1 − gT/RONIC)(1+gT)/(r − gT)。"""
    eps_T = path[T - 1]["eps"]
    if eps_T is None or eps_T <= 0:
        return None
    r, n = cost_of_equity, len(path)
    v = 0.0
    for row in path[T:]:
        payout = 1.0 - (row["reinvest"] / row["nopat"] if row["nopat"] > 0 else 1.0)
        v += row["eps"] * max(0.0, min(1.0, payout)) / (1 + r) ** (row["year"] - T)
    spread = r - terminal_growth
    if spread <= 0.005 or path[-1]["eps"] <= 0:
        return None
    tv = path[-1]["eps"] * (1 + terminal_growth) * (1 - terminal_growth / ronic_terminal) / spread
    v += tv / (1 + r) ** (n - T)
    return v / eps_T


def eps_pe_value(path: list[dict], *, cost_of_equity: float, terminal_growth: float,
                 ronic_terminal: float, pe_hist: dict | None, horizon: int = EPS_PE_HORIZON,
                 pe_mult: float = 1.0) -> dict | None:
    T = min(horizon, len(path))
    eps_T = path[T - 1]["eps"]
    if eps_T is None or eps_T <= 0:
        return None
    pe_steady = justified_pe(terminal_growth, ronic_terminal, cost_of_equity)
    pe_path = path_justified_pe(path, T, cost_of_equity=cost_of_equity, terminal_growth=terminal_growth,
                                ronic_terminal=ronic_terminal)
    h = pe_hist or {}
    anchors = {"justified_steady": pe_steady, "justified_path": pe_path,
               "hist_avg": h.get("average"), "hist_minus_1sd": h.get("minus_1sd"), "hist_plus_1sd": h.get("plus_1sd")}
    hist_avg = anchors["hist_avg"]
    if hist_avg and hist_avg > 0:
        pe_base, basis = hist_avg, "5Y PE 均值 (均值回归)"
        lo, hi = anchors["hist_minus_1sd"], anchors["hist_plus_1sd"]
        degenerate = lo is None or hi is None or lo <= 0 or (hi - hist_avg) > hist_avg
        if degenerate:
            lo, hi = hist_avg * PE_BAND_DEGENERATE_MULT[0], hist_avg * PE_BAND_DEGENERATE_MULT[1]
            basis += " (通道退化, 熊/牛用 0.5×/2× 均值)"
        pe_bear = max(lo, pe_steady or 0.0)
        pe_bull = hi
    else:
        pe_base = pe_path or pe_steady or 15.0
        basis = "路径合理 PE (无正 EPS 历史)"
        pe_bear, pe_bull = max(pe_steady or 0.0, pe_base * 0.8), pe_base * 1.25
    if pe_mult != 1.0:
        pe_base, pe_bear, pe_bull = pe_base * pe_mult, pe_bear * pe_mult, pe_bull * pe_mult
        basis += f" ×{pe_mult:.3f} (新闻 PE 倍数 overlay)"
    divs = sum(path[t - 1]["dps"] * (1 + cost_of_equity) ** (T - t) for t in range(1, T + 1))
    disc = (1 + cost_of_equity) ** T

    def value(pe: float) -> float:
        return (eps_T * pe + divs) / disc

    return {"horizon": T, "eps_T": eps_T, "pe_T": pe_base, "pe_basis": basis, "pe_bear": pe_bear,
            "pe_bull": pe_bull, "pe_mult": pe_mult, "anchors": anchors, "per_share": value(pe_base),
            "per_share_bear_pe": value(pe_bear), "per_share_bull_pe": value(pe_bull)}


def street_value(consensus: dict | None, *, cost_of_equity: float, min_analysts: int = 3) -> dict | None:
    """引擎 B (v3): 卖方一致目标价 (12 个月) 按 r_e 折现一年 → 今天的机构口径价值; 低/均/高 = 熊/基/牛。
    已知偏差: 卖方目标价系统性高于现价 (文献 ~10–15%), 不修正, 明示。"""
    c = consensus or {}
    n = c.get("total") or 0
    avg = c.get("average")
    if not avg or avg <= 0 or n < min_analysts:
        return None
    disc = 1 + cost_of_equity
    lo, hi = c.get("lowest") or avg, c.get("highest") or avg
    return {"target_avg": avg, "target_low": lo, "target_high": hi, "n": n, "as_of": c.get("update_time_str"),
            "per_share": avg / disc, "per_share_bear": lo / disc, "per_share_bull": hi / disc,
            "basis": f"卖方一致目标价 均 ${avg:,.0f} (低 ${lo:,.0f} / 高 ${hi:,.0f}, {n} 家, {c.get('update_time_str')}) 按 r_e {cost_of_equity:.1%} 折现 12 个月"}


def implied_param(target: float, spec: dict, value_fn: Callable[[dict], float], key: str,
                  lo: float, hi: float, iters: int = 60) -> float | None:
    """二分解 spec[key] 使 value_fn(spec) = target (value 对该参数单调时唯一; 不单调 → None)。"""
    def f(x: float) -> float:
        return value_fn({**spec, key: x}) - target
    flo, fhi = f(lo), f(hi)
    if not (math.isfinite(flo) and math.isfinite(fhi)) or flo * fhi > 0:
        return None
    for _ in range(iters):
        mid = (lo + hi) / 2
        fm = f(mid)
        if not math.isfinite(fm):
            return None
        if fm * flo <= 0:
            hi, fhi = mid, fm
        else:
            lo, flo = mid, fm
    return (lo + hi) / 2


def implied_growth(target: float, spec: dict, value_fn: Callable[[dict], float]) -> float | None:
    """解 N 年**平坦** CAGR (不衰减, N 年后按 gT 永续) 使 DCF = 现价 —— 「现价要求收入以多少 CAGR 长 N 年」。"""
    return implied_param(target, {**spec, "fade": "flat"}, value_fn, "growth_start", *IMPLIED_GROWTH_RANGE)


def implied_margin(target: float, spec: dict, value_fn: Callable[[dict], float]) -> float | None:
    """解终点营业利润率 (其余同基准): 周期票「市场在为多高的中周期利润率定价」。"""
    return implied_param(target, spec, value_fn, "target_margin", -0.5, 0.95)


def verdict(price: float, fair: float) -> dict:
    if fair is None or not math.isfinite(fair) or fair <= 0 or not price:
        return {"ratio": None, "label": "无法判定", "basis": "公允价非正或缺价"}
    ratio = price / fair - 1
    label = "高估"
    for cut, lab in VERDICT_BANDS:
        if ratio < cut:
            label = lab
            break
    return {"ratio": ratio, "label": label,
            "basis": "现价/概率加权公允 − 1; 档位 ≤−25% 低估 / −10% 偏低 / ±10% 合理 / +10% 偏高 / ≥+25% 高估"}


def confidence(base: int | None, *, tv_share: float | None, fcf_ttm: float | None,
               quarters: int, dcf_positive: bool, opm_percentile: float | None = None) -> dict:
    score = base if base is not None else 0
    reasons = []
    if opm_percentile is not None and opm_percentile >= PEAK_MARGIN_PCTL:
        score -= 1
        reasons.append(f"TTM 利润率处于自身历史 {opm_percentile:.0%} 分位 (峰值, 均值回归风险)")
    if tv_share is not None and tv_share > TV_SHARE_WARN:
        score -= 1
        reasons.append(f"终值占 EV {tv_share:.0%} > {TV_SHARE_WARN:.0%}")
    if fcf_ttm is not None and fcf_ttm < 0:
        score -= 1
        reasons.append("TTM FCF 为负")
    if quarters < MIN_CYCLE_QUARTERS:
        score -= 1
        reasons.append(f"历史仅 {quarters} 季")
    if not dcf_positive:
        score -= 1
        reasons.append("DCF 每股非正")
    tier = "高" if score >= 2 else ("中" if score == 1 else "低")
    return {"score": score, "tier": tier, "reasons": reasons}


# ── 单票全流程 ──────────────────────────────────────────────────────

def valuate(bundle: dict, resolved: dict, *, rf: float, erp: float, beta: dict | None,
            price: float | None = None, overlays: dict | None = None, macro_context: dict | None = None,
            forward: dict | None = None) -> dict:
    """bundle (fundamentals 缓存) + resolved (archetypes.resolve) + 市场参数 + 有效 overlay → 完整估值结果。
    `overlays` = overlays.active_overlays() 的输出 ({applied, ignored, flags}); `macro_context` =
    {risk_level, risk_score, run_id, ...} 来自 ai_news/macro/current.json。`not_applicable` 原型直接返回说明。"""
    params, overrides = resolved["params"], resolved["overrides"]
    ticker = bundle["ticker"]
    if overlays is None:
        # 直接调用 (不经 valuate.run) 时至少把 inputs.json 手写的 news_overlay 接上, 不让它静默失效
        from quant.valuation import overlays as _ovmod   # 同层, 无环 (overlays 只依赖 archetypes)
        from quant.core.timeutil import et_today as _today
        overlays = _ovmod.active_overlays(_ovmod.manual_records(ticker, resolved.get("news_overlay") or []),
                                          ticker=ticker, archetype=resolved["archetype"], as_of=_today())
    ov = overlays
    applied = list(ov.get("applied") or [])
    if resolved["archetype"] == "not_applicable":
        return {"ticker": ticker, "archetype": "not_applicable", "skipped": True,
                "reason": resolved["provenance"][0]}
    d = derive_drivers(bundle, params, overrides)
    px = price if price is not None else d.get("price")
    if not px:
        raise ValueError(f"{ticker}: 无现价")
    flags = list(d["flags"]) + list(ov.get("flags") or [])
    gT = float(overrides.get("terminal_growth", params["terminal_growth"]))
    mkt = apply_overlays_market(rf, erp, applied, macro_context)
    ib = overrides.get("industry_beta", params.get("industry_beta"))
    coc = cost_of_capital(d, rf=mkt["rf"], erp=mkt["erp"], beta=beta, overrides=overrides, terminal_growth=gT,
                          industry_beta=float(ib) if ib else None)
    coc.update({"rf_base": rf, "erp_base": erp, "rf_delta": mkt["rf_delta"], "erp_delta": mkt["erp_delta"],
                "erp_tilt": mkt["erp_tilt"], "erp_tilt_basis": mkt["erp_tilt_basis"]})
    fw = forward if forward is not None else (bundle.get("forward") or {})
    base_anchor = resolve_base(d, params, overrides, fw)
    flags += base_anchor["flags"]
    g0, g0_basis = base_anchor["growth_after"], base_anchor["growth_basis"]
    mT, mT_basis, mflags = resolve_margin_target(d, params, overrides, margin_1=base_anchor["margin_1"])
    flags += mflags
    lvl_ratio, lvl_fade, lvl_basis, lflags = resolve_revenue_base(d, params, overrides)
    flags += lflags
    if resolved["archetype"] == "semi_cyclical" and d.get("opm_median") and d["opm_median"] > 0 \
            and d["opm_ttm"] > CYCLE_PEAK_MULT * d["opm_median"]:
        flags.append(f"周期峰值: TTM 营业利润率 {d['opm_ttm']:.0%} 是历史中位 {d['opm_median']:.0%} 的 "
                     f"{d['opm_ttm']/d['opm_median']:.1f}×, 报表 PE 失真")
    if d.get("revenue_decel") is not None and d["revenue_decel"] < -0.10 and (d.get("price") and d.get("eps_core_ttm") and d["eps_core_ttm"] > 0 and px / d["eps_core_ttm"] > 30):
        flags.append(f"双杀形状: 核心 PE {px/d['eps_core_ttm']:.0f}× 且增速二阶导 {d['revenue_decel']:+.0%}")
    wacc = coc["wacc"]
    ronic_cap = params.get("ronic_cap")
    ronic_T = float(overrides["ronic_terminal"]) if "ronic_terminal" in overrides else \
        _clamp(d.get("roic_median") if d.get("roic_median") is not None else (ronic_cap or wacc + 0.02),
               wacc, ronic_cap if ronic_cap is not None else wacc + 0.02)
    spec = {
        "revenue_base": d["revenue_ttm"], "growth_year1": base_anchor["growth_1"], "growth_start": g0, "terminal_growth": gT,
        "horizon_years": int(overrides.get("horizon_years", params["horizon_years"])),
        "fade": params.get("fade"), "half_life": params.get("half_life"),
        "margin_start": base_anchor["margin_1"], "target_margin": mT,
        "margin_fade_years": int(overrides.get("margin_fade_years", params["margin_fade_years"])),
        "tax_rate": d["tax_rate"], "sales_to_capital": d["sales_to_capital"], "shares": d["shares"],
        "share_change_rate": d["share_change_rate"], "net_interest_after_tax": d["net_interest_after_tax"],
        "dps": d.get("dps_ttm") or 0.0, "wacc": wacc, "cost_of_equity": coc["cost_of_equity"],
        "ronic_terminal": ronic_T,
        "revenue_level_ratio": lvl_ratio, "revenue_fade_years": lvl_fade,
    }
    spec_pre = dict(spec)
    spec = apply_overlays_spec(spec, applied)
    pe_mult = pe_multiplier_of(applied)
    bridge = {"cash_sti": d["cash_sti"], "lt_investments": d["lt_investments"], "debt": d["debt"], "shares": d["shares"]}

    def dcf_ps(s: dict) -> float:
        p = project(s)
        return dcf_value(p, wacc=s["wacc"], terminal_growth=s["terminal_growth"],
                         ronic_terminal=s["ronic_terminal"], **bridge)["per_share"]

    weights = dict(overrides.get("engine_weights", params["engine_weights"]))
    pe_hist = bundle.get("valuation_pe")
    street = street_value(bundle.get("consensus"), cost_of_equity=coc["cost_of_equity"])
    if street is None:
        flags.append("无卖方一致目标价 (<3 家): 引擎 B 零权, 只有 DCF")

    tbv_floor = max(0.0, ((d.get("book_equity") or 0.0) - (d.get("goodwill_intangibles") or 0.0)) / d["shares"]) if d.get("book_equity") else 0.0

    def run(s: dict) -> dict:
        p = project(s)
        a = dcf_value(p, wacc=s["wacc"], terminal_growth=s["terminal_growth"], ronic_terminal=s["ronic_terminal"], **bridge)
        b = eps_pe_value(p, cost_of_equity=s["cost_of_equity"], terminal_growth=s["terminal_growth"],
                         ronic_terminal=s["ronic_terminal"], pe_hist=pe_hist, pe_mult=pe_mult)
        wa, wb = weights["fcf_dcf"], (weights.get("street", 0.0) if street else 0.0)
        pa_raw = a["per_share"] if (a["per_share"] is not None and math.isfinite(a["per_share"])) else 0.0
        pa = max(pa_raw, tbv_floor)          # 引擎 A 地板 = 有形账面价值/股 (剔商誉): 亏损路径的公司也不值 0
        pb = street[s.get("_street_key", "per_share")] if street else 0.0
        blended = (wa * pa + wb * pb) / (wa + wb) if (wa + wb) > 0 else None
        return {"path": p, "dcf": a, "eps_pe": b, "street": (dict(street, used=pb) if street else None),
                "weights": {"fcf_dcf": wa, "street": wb}, "blended": blended,
                "tbv_floor": tbv_floor, "floor_binding": pa_raw < tbv_floor}

    base = run(spec)
    sc = params["scenario"]
    probs = tilt_probabilities(dict(overrides.get("probabilities") or {"bear": 0.25, "base": 0.50, "bull": 0.25}), applied)
    fy = spec["margin_fade_years"]
    fy_d = int(sc.get("margin_fade_years", 0) or 0)
    rfy = spec["revenue_fade_years"]
    g1 = spec["growth_year1"]
    bear = run({**spec, "_street_key": "per_share_bear", "growth_year1": (1 + g1) * (1 - sc["growth"] / 2) - 1, "margin_start": spec["margin_start"] - sc["margin"] / 2,
                "growth_start": spec["growth_start"] - sc["growth"], "target_margin": spec["target_margin"] - sc["margin"],
                "wacc": spec["wacc"] + sc["wacc"], "cost_of_equity": spec["cost_of_equity"] + sc["wacc"],
                "margin_fade_years": max(1, fy - fy_d), "revenue_fade_years": max(1, rfy - fy_d)})
    bull = run({**spec, "_street_key": "per_share_bull", "growth_year1": (1 + g1) * (1 + sc["growth"] / 2) - 1, "margin_start": spec["margin_start"] + sc["margin"] / 2,
                "growth_start": spec["growth_start"] + sc["growth"], "target_margin": spec["target_margin"] + sc["margin"],
                "wacc": max(spec["wacc"] - sc["wacc"], gT + 0.02), "cost_of_equity": spec["cost_of_equity"] - sc["wacc"],
                "margin_fade_years": fy + fy_d, "revenue_fade_years": rfy + fy_d})
    scen = {"bear": bear["blended"], "base": base["blended"], "bull": bull["blended"], "probabilities": probs}
    fair = None
    if all(scen[k] is not None for k in ("bear", "base", "bull")):
        fair = sum(probs[k] * scen[k] for k in ("bear", "base", "bull"))
    g_impl = implied_growth(px, spec, dcf_ps)
    m_impl = implied_margin(px, spec, dcf_ps)
    n_after = max(1, spec["horizon_years"] - 1)
    base_cagr = (base["path"][-1]["revenue"] / base["path"][0]["revenue"]) ** (1 / n_after) - 1
    # 增长在基准 ROIC 下是否创造价值 (NOPAT 率 × 销售资本比 vs WACC 的机械版): 不创造 → 隐含 CAGR 无意义
    growth_creates_value = dcf_ps({**spec, "fade": "flat", "growth_start": 0.10}) > dcf_ps({**spec, "fade": "flat", "growth_start": 0.0})
    # 周期票的真问题: 若峰值**不回归** (利润率维持 TTM, 收入不回趋势线), 现价要求多少 CAGR
    hold = {**spec, "target_margin": spec["margin_start"], "margin_fade_years": 1, "revenue_level_ratio": 1.0}
    g_hold = implied_growth(px, hold, dcf_ps)
    m_ceiling = dcf_ps({**spec, "target_margin": 0.95})
    implied = {"cagr": g_impl, "base_cagr": base_cagr, "vs_base_cagr": (g_impl - base_cagr) if g_impl is not None else None,
               "horizon_years": spec["horizon_years"], "cagr_years": n_after, "growth_creates_value": growth_creates_value,
               "target_margin": m_impl, "vs_base_margin": (m_impl - spec["target_margin"]) if m_impl is not None else None,
               "margin_unreachable": (m_impl is None and math.isfinite(m_ceiling) and m_ceiling < px),
               "cagr_at_current_margin": g_hold,
               "basis": "分别解 N 年平坦收入 CAGR / 终点营业利润率 / (峰值不回归: 利润率维持 TTM 且收入不回趋势线时的) CAGR 使 FCF-DCF 每股 = 现价, 其余假设同基准",
               "revenue_level_ratio": spec["revenue_level_ratio"]}
    if g_impl is not None:
        implied["revenue_year_N"] = base["path"][0]["revenue"] * (1 + g_impl) ** n_after
    if g_hold is not None:
        implied["revenue_year_N_at_current_margin"] = base["path"][0]["revenue"] * (1 + g_hold) ** n_after
    dcf = base["dcf"]
    if dcf["tv_share"] is not None and dcf["tv_share"] > TV_SHARE_WARN:
        flags.append(f"终值占 EV {dcf['tv_share']:.0%}: 结论几乎全押在 N 年后")
    if not (dcf["per_share"] and math.isfinite(dcf["per_share"]) and dcf["per_share"] > 0):
        flags.append("DCF 每股非正 (利润率路径未转正或终值溢价不足)")
    if base.get("floor_binding") or bear.get("floor_binding"):
        flags.append(f"引擎 A 触及有形账面地板 ${tbv_floor:,.2f}/股 (剔商誉)")
    conf = confidence(params.get("base_confidence"), tv_share=dcf["tv_share"], fcf_ttm=d.get("fcf_ttm"),
                      quarters=d["quarters_used"], dcf_positive=bool(dcf["per_share"] and dcf["per_share"] > 0),
                      opm_percentile=d.get("opm_percentile") if d.get("opm_history_n", 0) >= MIN_CYCLE_QUARTERS else None)
    anchor_out = {k: base_anchor[k] for k in ("revenue_1", "growth_1", "growth_after", "margin_1", "revenue_basis",
                                                 "growth_basis", "margin_basis", "consensus_revenue", "consensus_eps",
                                                 "forward_pe", "ev_to_forward_sales")}
    anchor_out["forward"] = fw or None
    sec = bundle.get("sector") or {}
    anchor_out["sector"] = {"plate_name": sec.get("plate_name"),
                            "pe_median5y": (sec.get("pe") or {}).get("median5y"), "pe_now": (sec.get("pe") or {}).get("current"),
                            "ps_median5y": (sec.get("ps") or {}).get("median5y"), "ps_now": (sec.get("ps") or {}).get("current")} if sec else None
    anchor_out["dcf_vs_street"] = ((base["dcf"]["per_share"] / base["street"]["per_share"]) - 1) if (base.get("street") and base["dcf"]["per_share"] and base["dcf"]["per_share"] > 0) else None
    news = {
        "applied": [{k: o.get(k) for k in ("id", "date", "ticker", "channel", "driver", "delta", "reliability",
                                             "resolved_until", "source", "note")} for o in applied],
        "ignored": list(ov.get("ignored") or []),
        "rf_delta": mkt["rf_delta"], "erp_delta": mkt["erp_delta"], "erp_tilt": mkt["erp_tilt"],
        "erp_tilt_basis": mkt["erp_tilt_basis"], "pe_mult": pe_mult, "probabilities": probs,
        "spec_changes": {k: (spec_pre[k], spec[k]) for k in spec if k in spec_pre and spec[k] != spec_pre[k]},
        "macro_context": macro_context,
    }
    return {
        "ticker": ticker, "archetype": resolved["archetype"], "archetype_label": resolved["label"],
        "model_version": MODEL_VERSION, "news": news,
        "price": px, "price_time": (bundle.get("snapshot") or {}).get("update_time"),
        "drivers": {k: v for k, v in d.items() if k != "flags"},
        "growth_start": g0, "growth_basis": g0_basis, "target_margin": spec["target_margin"], "margin_basis": mT_basis,
        "revenue_basis": lvl_basis, "scenario_deltas": dict(sc), "anchor": anchor_out,
        "cost_of_capital": coc, "spec": {k: v for k, v in spec.items()},
        "base": base, "scenarios": scen, "fair_value": fair, "implied": implied,
        "verdict": verdict(px, fair), "confidence": conf, "flags": flags,
        "provenance": resolved["provenance"], "pe_hist": pe_hist, "consensus": bundle.get("consensus"),
        "fundamentals_fetched_at": bundle.get("fetched_at"),
    }
