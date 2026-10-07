"""宏观透镜 macro_lens —— 环境 × 最强驱动 × 市场在交易什么 × 未来方向（判读层，零授权效力）。

用途：用指标与数据判断当前市场环境、整理哪个宏观因素在驱动市场、推导市场在交易什么与未来可能的方向，
并只保留研究证明对交易有信息量的部分。

本模块的每个读数都标了它在研究里的身份：`风险有效`（H1 过）/ `描述`（未验证或只过一半）。

════════════════════════════════════════════════════════════
四块输出（全部收盘口径，第 t 天只用 ≤t 的数据）
════════════════════════════════════════════════════════════

  ① 风险环境   六盏灯（H1 过：预告前瞻 20 日更深回撤 + 更高波动，且在当前波动之外有增量）：
               波动 VIX≥3年p80 / 期限 VIX÷VIX3M≥1 / 趋势 SPY<200日线 / 信用 HYG−0.5·IEF 20日≤3年p20 /
               利率波动 MOVE≥3年p80 / 油价冲击 |WTI 20日|≥3年p80。
               亮灯数 → 档位；**不预告方向**（H2 不成立：亮灯多的时候前瞻 20 日收益并不更差）。
  ② 最强驱动   8 个宏观因子（原油 USO / 10Y / 2Y SHY / 美元 / 黄金 GLD / 信用 / 日元 / 铜）对 SPY 日收益的 40 日相关²。
               「近 40 日最强驱动」描述近 40 日（事实），不承诺延续：H3 持续性弱且不稳，逐因子收据见 DOMINANT_HISTORY。
               （对外叫「最强驱动」，不叫「主导」；代码字段名 dominant 沿用。）
               最强驱动 = 相关² 在自身 3 年分位 ≥0.80 且 ≥0.10 的因子里取最大；没有 = 「无突出宏观驱动」。
               宏观主导度 M = SPY 对 8 因子回归的 R²。相关的**符号**就是市场的解读（油涨股跌 = 供给冲击）。
  ③ 在交易什么 最强驱动 × 符号 → 解读（事先写死的对照表，不拟合）+ 同向链条 + 10Y 拆分（实际利率 / 通胀预期）
               + 行业相对强弱（资金在买什么 / 卖什么）。
  ④ 未来方向   接棒候选（20 日解释力明显高于 40 日的因子）/ 情景推算（H4：行业 β 未来 40 日横截面稳定，
               可以给数字）/ 读法翻转条件 / 历史倾向（带收据）。日历与持仓暴露由 journal / report 层补。

🚨 零授权：只被判读层（macro_lens_log 账本、macro-lens 定时任务推送 / 简报 / 周回顾）消费；不改 PLAN / VETO / cap / 股数。纯函数，只 import core。
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

SCHEMA = "macro_lens/1"
DESIGN_DOC = "quant/core/macro_lens.py"   # 定义与研究收据住本模块；预注册研究稿不随公开仓发布

W = 40                  # 主窗口（相关标准误 ≈0.16）
W_FAST = 20             # 显示辅助 / 接棒判别
PCT_WIN = 756           # 自身分位窗（≈3 年）
MIN_FRAC = 0.80         # 滚动窗 / 分位窗最少有效比例（债市休市、供方坏点）
PCT_DOM = 0.80
R2_DOM = 0.10
SIG_RHO = 0.30          # 「有读法」的最小 |ρ|（40 日窗 ≈ 2 个标准误）
RISE_R2 = 0.10          # 升温 / 降温：40 日相关² 与 10 个交易日前相比 ±0.10
RISE_LAG = 10
CO_DOM = 0.90           # 并列最强：同样异常（分位 ≥0.80）且相关² ≥ 最强驱动的 90%
COLLINEAR = 0.60        # 归因回归里两个因子 |相关| ≥ 此值 = 共线，只留一个
FFILL_LEVELS = ("VIX", "VIX3M", "MOVE", "US10Y", "DXY", "WTI")   # 水平量前向填充 ≤3 日（债市休市日）
FFILL_LIMIT = 3
BAD_MOVE = 0.4          # |ln| 超过 = 换月 / 负油价坏点，记缺不修补

FACTORS = ("OIL", "R10", "R2", "USD", "GOLD", "CREDIT", "JPY", "COPPER")
FACTOR_CN = {"OIL": "原油", "R10": "长端利率", "R2": "短端利率", "USD": "美元", "GOLD": "黄金",
             "CREDIT": "信用", "JPY": "日元", "COPPER": "铜"}
FACTOR_UNIT = {"OIL": "%", "R10": "bp", "R2": "%", "USD": "%", "GOLD": "%", "CREDIT": "%", "JPY": "%", "COPPER": "%"}
SHY_DURATION = 1.9      # R2 = −ln SHY；显示成 2Y 收益率 bp ≈ R2 ÷ 久期（只影响显示，不进计算）


def display_move(factor: str, x: Optional[float]) -> str:
    """因子变动的白话显示：10Y = bp；2Y = SHY 价格变动折成近似收益率 bp；其余 = %。"""
    if x is None:
        return "—"
    if factor == "R10":
        return f"{x:+.0f}bp"
    if factor == "R2":
        return f"{x / SHY_DURATION * 1e4:+.0f}bp"
    return f"{x * 100:+.1f}%"
# 因子「上涨」的白话（给符号读法用）；R2 用 2 年期美债期货取反，正 = 2Y 收益率上行
FACTOR_UP = {"OIL": "油价涨", "R10": "10Y 收益率升", "R2": "2Y 收益率升", "USD": "美元涨", "GOLD": "金价涨",
             "CREDIT": "信用改善", "JPY": "日元贬值", "COPPER": "铜价涨"}

SECTORS = {"XLK": "科技", "SMH": "半导体", "XLE": "能源", "XLF": "金融", "KRE": "地区银行", "XLU": "公用事业",
           "XLP": "必需消费", "XLY": "可选消费", "XLV": "医疗", "XLI": "工业", "XLB": "材料", "IWM": "小盘"}
SCENARIO_TARGETS = ("SPY", "QQQ", "SMH", "XLE", "XLK", "IWM")

# ── 研究收据（预注册研究的首跑结果；改口径 = 重跑研究，不许手改数字）─────────────
# H1：六盏灯都过「回撤 + 增量波动」两判据（样本外窗口）。S6 / S7 / S9 / S10 不过 → 只作描述。
RISK_LIGHTS = ("S1", "S2", "S3", "S4", "S5", "S8")
# 亮灯数 → 前瞻 20 日（全样本，描述性聚合）：平均最大回撤 / ≥5% 回撤频率 / 年化实现波动 / 平均收益
TIER_BASE = {0: {"dd": -1.88, "p_dd5": 9.7, "rv": 11.6, "ret": 1.01, "days": 2395},
             1: {"dd": -2.58, "p_dd5": 14.0, "rv": 14.7, "ret": 1.10, "days": 845},
             2: {"dd": -3.26, "p_dd5": 24.1, "rv": 18.5, "ret": 0.95, "days": 469},
             3: {"dd": -3.68, "p_dd5": 29.3, "rv": 20.6, "ret": 1.22, "days": 253},
             4: {"dd": -4.10, "p_dd5": 27.5, "rv": 27.4, "ret": 2.85, "days": 291}}
TIER_CN = {0: "平稳", 1: "留意", 2: "警戒", 3: "高风险", 4: "高风险"}
# H3：因子成为最强驱动后（每 20 日取样），未来 40 日它的相关² 均值 vs 该因子无条件均值；符号保持率。
# 因子口径 v1.1 的全样本数字。H3 整体「弱且不稳」：v1.0 刚过、v1.1 刚不过 —— 「最强驱动」描述当下，
# 不承诺延续；逐因子持续性看这张表（原油两个口径都明显持续，短端利率当选后通常回落）。
DOMINANT_HISTORY = {"OIL": (15, 0.260, 0.173, 0.80), "R10": (10, 0.145, 0.163, 0.90),
                    "R2": (14, 0.076, 0.138, 0.71), "USD": (14, 0.113, 0.108, 0.86),
                    "GOLD": (11, 0.109, 0.102, 0.82), "CREDIT": (49, 0.630, 0.574, 1.00),
                    "JPY": (4, 0.041, 0.067, 0.25), "COPPER": (18, 0.146, 0.119, 0.89)}
# H4：行业 β 未来 40 日横截面稳定（OOS 均值，v1.1）；JPY 不过 → 情景表不给日元数字。
BETA_STABLE = {"OIL": 0.72, "R10": 0.56, "R2": 0.52, "USD": 0.36, "GOLD": 0.37, "CREDIT": 0.78,
               "JPY": 0.00, "COPPER": 0.46}
# H2 / H5：宏观主导度高（M≥3年p80）是 10 个状态里唯一过方向检验的：之后 20 日 SPY 平均多涨
# +1.78pp（OOS）/ +1.16pp（IS）；突破信号 mean NAV bp 多 +66（样本外）/ +141（训练）。零接线，只播报。
# 排序研究：这份偏强几乎全是大盘漂移（两种模式一视同仁、同周候选之间无差异）→ 大盘时点，不是选股 / 排序信号。
M_HIGH_FINDING = "宏观主导度高的时期，之后 20 日 SPY 平均多涨 +1.8pp（样本外）；两种模式的信号跟着变好，但来自大盘漂移，不是选股 —— 零授权，只作倾向"

# 背离（预注册研究；背离 = 价格条件 P 成立时 G 不确认，只在 P 日上比）。
# D2 / D4 只过 H1 的增量波动一项 → 全文附表「描述」；D1 信用背离 / D3 广度背离两项都不过 → 删，不计算。H2 方向四个全不过。
DIVERGENCE_DESIGN = "quant/core/macro_lens.py divergence_states"
DIVERGENCE_FINDING = {
    "D2": "之后 20 日波动偏高（ln RV 增量 IS +0.055 / OOS +0.120，两窗显著）；回撤、方向、两种模式都无增量",
    "D4": "之后 20 日波动偏低（ln RV 增量 IS −0.179 / OOS −0.141，两窗显著）；回撤只 IS 显著，方向无增量，抄底臂两窗反号",
}

# 广度（描述行，零授权；预注册研究复测过）。
# 窄度 = 等权 / 小盘相对 SPY + 行业站上 50 日线家数；B60 = 「高位 + 窄」。
# D3 20 日尺度已删、B60 60 日尺度样本内段数不足未裁 —— 所以只描述形状，不预告方向，不进风险灯。
BREADTH_DESIGN = "quant/core/macro_lens.py breadth_state"
BREADTH_SECTORS = {"XLK": "科技", "XLC": "通信", "XLY": "可选消费", "XLF": "金融", "XLV": "医疗", "XLI": "工业",
                   "XLE": "能源", "XLP": "必需消费", "XLU": "公用事业", "XLB": "材料", "XLRE": "地产"}
BREADTH_FINDING = ("窄幅上涨已检验：20 日尺度无增量（D3 删）；60 日尺度样本不足未裁 —— 样本外点估计之后 60 日"
                   "回撤多 2.4pp、少涨 2.2pp，不显著。只描述形状，不预告方向")


def persistence_label(factor: str) -> Optional[str]:
    """该因子成为最强驱动后的历史持续性（DOMINANT_HISTORY 的未来解释力 ÷ 常态）。"""
    h = DOMINANT_HISTORY.get(factor)
    if not h or not h[2]:
        return None
    ratio = h[1] / h[2]
    if ratio >= 1.3:
        return "较持续"
    if ratio >= 1.0:
        return "一般"
    return "之后通常回落"

# 最强驱动 × 符号 → 市场在交易什么（事先写死；ρ = corr(SPY, 因子)）
READING = {
    ("OIL", -1): ("供给冲击 / 滞胀", "油涨 = 利空：市场把油价当通胀与需求税"),
    ("OIL", 1): ("需求 / 增长", "油涨 = 利好：油价跟着增长预期走"),
    ("R10", -1): ("长端利率 / 估值压缩", "利率升 = 利空：期限溢价、通胀或财政在压估值"),
    ("R10", 1): ("增长预期", "利率升 = 利好：收益率跟着增长预期走（跌 = 增长担忧）"),
    ("R2", -1): ("联储路径 / 鹰派", "前端升 = 利空：市场在交易加息 / 不降息"),
    ("R2", 1): ("增长 / 衰退担忧", "前端跌 = 利空：降息预期来自衰退担忧"),
    ("USD", -1): ("美元流动性收紧", "美元涨 = 利空：全球美元流动性收紧"),
    ("USD", 1): ("美国例外 / 资金回流", "美元涨 = 利好：资金流入美国资产"),
    ("GOLD", -1): ("避险", "金涨 = 利空：避险资金进黄金"),
    ("GOLD", 1): ("实际利率 / 流动性", "金股同向：实际利率与流动性同时驱动两者"),
    ("CREDIT", 1): ("信用 / 系统性去风险", "信用走弱 = 股跌：股债信用一起去杠杆"),
    ("CREDIT", -1): ("利率久期（信用读数被久期污染）", "信用超额与股反向：久期冲击在驱动"),
    ("JPY", 1): ("套息交易", "日元升值 = 利空：套息平仓"),
    ("JPY", -1): ("日元贬值压力", "日元贬值 = 利空"),
    ("COPPER", 1): ("全球增长 / 周期", "铜涨 = 利好：增长交易"),
    ("COPPER", -1): ("商品供给冲击", "铜涨 = 利空"),
}


# ════════════════════════════════════════════════════════════
# 对齐与因子
# ════════════════════════════════════════════════════════════

Series = Sequence[Tuple[str, float]]


def align(series: Dict[str, Series], calendar: str = "SPY") -> Dict:
    """按 calendar 的日期精确对齐；缺 = None。FFILL_LEVELS 里的水平量另存一份前向填充（≤3 日）。"""
    dates = [d for d, _ in series.get(calendar, [])]
    raw: Dict[str, List[Optional[float]]] = {}
    for key, rows in series.items():
        m = dict(rows)
        raw[key] = [m.get(d) for d in dates]
    lv: Dict[str, List[Optional[float]]] = {}
    for key in FFILL_LEVELS:
        vals = raw.get(key) or [None] * len(dates)
        out, last, gap = [], None, 0
        for v in vals:
            if v is not None:
                last, gap = v, 0
                out.append(v)
            else:
                gap += 1
                out.append(last if last is not None and gap <= FFILL_LIMIT else None)
        lv[key] = out
    return {"dates": dates, "raw": raw, "level": lv}


def _ln_diff(vals: Sequence[Optional[float]], neg: bool = False, guard: bool = False) -> List[Optional[float]]:
    out: List[Optional[float]] = [None]
    for i in range(1, len(vals)):
        a, b = vals[i - 1], vals[i]
        if a is None or b is None or a <= 0 or b <= 0:
            out.append(None)
            continue
        x = math.log(b / a)
        if guard and abs(x) > BAD_MOVE:
            out.append(None)
            continue
        out.append(-x if neg else x)
    return out


def changes(fr: Dict) -> Dict[str, List[Optional[float]]]:
    """日变动：价格 = ln 收益；10Y = bp；2Y = −ln ZT；信用 = ln HYG − 0.5·ln IEF。"""
    raw = fr["raw"]
    n = len(fr["dates"])
    get = lambda k: raw.get(k) or [None] * n                                    # noqa: E731
    ch: Dict[str, List[Optional[float]]] = {"SPY": _ln_diff(get("SPY"))}
    # 因子口径 v1.1：原油 / 2Y / 黄金用与 SPY 同收盘、无换月跳空的 ETF（USO / SHY / GLD）；
    # 连续期货 CL=F / ZT=F / GC=F 只作水平读数。
    ch["OIL"] = _ln_diff(get("USO"), guard=True)
    t10 = get("US10Y")
    ch["R10"] = [None] + [(t10[i] - t10[i - 1]) * 100 if t10[i] is not None and t10[i - 1] is not None else None
                          for i in range(1, n)]
    ch["R2"] = _ln_diff(get("SHY"), neg=True, guard=True)
    ch["USD"] = _ln_diff(get("DXY"))
    ch["GOLD"] = _ln_diff(get("GLD"), guard=True)
    hyg, ief = _ln_diff(get("HYG")), _ln_diff(get("IEF"))
    ch["CREDIT"] = [a - 0.5 * b if a is not None and b is not None else None for a, b in zip(hyg, ief)]
    ch["JPY"] = _ln_diff(get("USDJPY"))
    ch["COPPER"] = _ln_diff(get("COPPER"), guard=True)
    ch["TLT"] = _ln_diff(get("TLT"))
    for k in list(SECTORS) + ["QQQ"]:
        ch[k] = _ln_diff(get(k))
    return ch


# ════════════════════════════════════════════════════════════
# 统计原语
# ════════════════════════════════════════════════════════════

def corr_at(y: Sequence[Optional[float]], x: Sequence[Optional[float]], t: int, w: int,
            min_n: Optional[int] = None) -> Optional[float]:
    """窗口 (t−w, t] 内成对有效样本的 Pearson 相关；有效对 < min_n = None。"""
    min_n = int(MIN_FRAC * w) if min_n is None else min_n
    xs, ys = [], []
    for i in range(max(0, t - w + 1), t + 1):
        a, b = y[i], x[i]
        if a is not None and b is not None:
            ys.append(a)
            xs.append(b)
    k = len(xs)
    if k < min_n:
        return None
    mx, my = sum(xs) / k, sum(ys) / k
    sxx = sum((v - mx) ** 2 for v in xs)
    syy = sum((v - my) ** 2 for v in ys)
    if sxx <= 0 or syy <= 0:
        return None
    return sum((a - mx) * (b - my) for a, b in zip(xs, ys)) / math.sqrt(sxx * syy)


def beta_at(y: Sequence[Optional[float]], x: Sequence[Optional[float]], t: int, w: int) -> Optional[float]:
    xs, ys = [], []
    for i in range(max(0, t - w + 1), t + 1):
        if y[i] is not None and x[i] is not None:
            ys.append(y[i])
            xs.append(x[i])
    if len(xs) < MIN_FRAC * w:
        return None
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    sxx = sum((v - mx) ** 2 for v in xs)
    if sxx <= 0:
        return None
    return sum((a - mx) * (b - my) for a, b in zip(xs, ys)) / sxx


def _solve(A: List[List[float]], b: List[float]) -> Optional[List[float]]:
    n = len(b)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(M[r][c]))
        if abs(M[p][c]) < 1e-18:
            return None
        M[c], M[p] = M[p], M[c]
        for r in range(c + 1, n):
            f = M[r][c] / M[c][c]
            if f:
                for k in range(c, n + 1):
                    M[r][k] -= f * M[c][k]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        x[r] = (M[r][n] - sum(M[r][k] * x[k] for k in range(r + 1, n))) / M[r][r]
    return x


def r2_at(y: Sequence[Optional[float]], X: Sequence[Sequence[Optional[float]]], t: int, w: int) -> Optional[float]:
    """y ~ 1 + X 在窗口 (t−w, t] 内的 OLS R²（整行有效才进样本；有效行 < 0.8w = None）。"""
    rows = []
    for i in range(max(0, t - w + 1), t + 1):
        if y[i] is None:
            continue
        xi = [col[i] for col in X]
        if any(v is None for v in xi):
            continue
        rows.append((y[i], [1.0] + xi))
    if len(rows) < MIN_FRAC * w:
        return None
    k = len(rows[0][1])
    XtX = [[0.0] * k for _ in range(k)]
    Xty = [0.0] * k
    for yy, xx in rows:
        for a in range(k):
            xa = xx[a]
            Xty[a] += xa * yy
            row = XtX[a]
            for b_ in range(a, k):
                row[b_] += xa * xx[b_]
    for a in range(k):
        for b_ in range(a):
            XtX[a][b_] = XtX[b_][a]
    beta = _solve(XtX, Xty)
    if beta is None:
        return None
    ys = [r[0] for r in rows]
    my = sum(ys) / len(ys)
    sst = sum((v - my) ** 2 for v in ys)
    if sst <= 0:
        return None
    ssr = sum((yy - sum(bi * xi for bi, xi in zip(beta, xx))) ** 2 for yy, xx in rows)
    return 1 - ssr / sst


def ols_at(y: Sequence[Optional[float]], X: Sequence[Sequence[Optional[float]]], t: int,
           w: int) -> Optional[Tuple[List[float], float]]:
    """y ~ 1 + X 在窗口 (t−w, t] 内的 OLS 系数（[截距, b1, …]）与 R²；样本不足 / 奇异 = None。"""
    rows = []
    for i in range(max(0, t - w + 1), t + 1):
        if y[i] is None or any(col[i] is None for col in X):
            continue
        rows.append((y[i], [1.0] + [col[i] for col in X]))
    if len(rows) < MIN_FRAC * w:
        return None
    k = len(rows[0][1])
    XtX = [[sum(r[1][a] * r[1][b] for r in rows) for b in range(k)] for a in range(k)]
    Xty = [sum(r[1][a] * r[0] for r in rows) for a in range(k)]
    beta = _solve(XtX, Xty)
    if beta is None:
        return None
    ys = [r[0] for r in rows]
    my = sum(ys) / len(ys)
    sst = sum((v - my) ** 2 for v in ys)
    ssr = sum((yy - sum(bi * xi for bi, xi in zip(beta, xx))) ** 2 for yy, xx in rows)
    return beta, (1 - ssr / sst if sst > 0 else 0.0)


def explain_day(ret: Sequence[Optional[float]], ch: Dict, drv_prev: Dict, t: int) -> Optional[Dict]:
    """第 t 天的收益 = 截距 + Σ b_f·Δf（f = 前一日「读法链」里互不共线的因子，≤3 个，40 日多元回归，窗口止于 t−1）+ 残差。
    归因只用前一日已知的系数（无前视）。|宏观解释| ≥ 2/3|实际| 且同号 = 宏观驱动；|残差| ≥ 2/3|实际| = 非宏观；其余 = 混合。"""
    if ret[t] is None:
        return None
    fs: List[str] = []
    for c in chain(drv_prev):                 # 共线的因子只留解释力更高的那个（2Y 与 10Y 常 >0.7 相关，一起回归系数会发散）
        f = c["factor"]
        if all(abs(corr_at(ch[f], ch[g], t - 1, W) or 0) < COLLINEAR for g in fs):
            fs.append(f)
        if len(fs) == 3:
            break
    if not fs:
        return {"actual": ret[t], "explained": 0.0, "residual": ret[t], "parts": [], "r2": None, "label": "非宏观"}
    fit = ols_at(ret, [ch[f] for f in fs], t - 1, W)
    if fit is None or any(ch[f][t] is None for f in fs):
        return None
    coef, r2 = fit
    parts = [{"factor": f, "move": ch[f][t], "beta": b, "contrib": b * ch[f][t]} for f, b in zip(fs, coef[1:])]
    explained = sum(x["contrib"] for x in parts)
    resid = ret[t] - coef[0] - explained
    a = ret[t]
    if abs(a) < 1e-12:
        label = "无波动"
    elif abs(explained) >= abs(a) * 2 / 3 and explained * a > 0:
        label = "宏观驱动"
    elif abs(resid) >= abs(a) * 2 / 3:
        label = "非宏观"
    else:
        label = "混合"
    return {"actual": a, "explained": explained, "residual": resid, "intercept": coef[0], "parts": parts,
            "r2": r2, "label": label}


def pct_at(values: Sequence[Optional[float]], t: int, win: int = PCT_WIN) -> Optional[float]:
    """values[t] 在 values[t−win : t] 有效值里的分位（严格小于的比例）；位置不足 win 或有效 < 0.8·win = None。"""
    v = values[t] if 0 <= t < len(values) else None
    if v is None or t < win:
        return None
    hist = [x for x in values[t - win:t] if x is not None]
    if len(hist) < MIN_FRAC * win:
        return None
    return sum(1 for x in hist if x < v) / len(hist)


def rolling(fn, t0: int, t1: int) -> List[Optional[float]]:
    """fn(t) 在 [t0, t1] 上的序列，前面补 None 到长度 t1+1（与 pct_at 的位置口径对齐）。"""
    return [None] * t0 + [fn(t) for t in range(t0, t1 + 1)]


def _sum_window(vals: Sequence[Optional[float]], t: int, w: int, min_n: int) -> Optional[float]:
    xs = [v for v in vals[max(0, t - w + 1):t + 1] if v is not None]
    return sum(xs) if len(xs) >= min_n else None


def _shift_log(vals: Sequence[Optional[float]], t: int, lag: int, absolute: bool = False) -> Optional[float]:
    if t - lag < 0:
        return None
    a, b = vals[t - lag], vals[t]
    if a is None or b is None or a <= 0 or b <= 0:
        return None
    x = math.log(b / a)
    return abs(x) if absolute else x


def _shift_diff(vals: Sequence[Optional[float]], t: int, lag: int, absolute: bool = False) -> Optional[float]:
    if t - lag < 0 or vals[t] is None or vals[t - lag] is None:
        return None
    x = vals[t] - vals[t - lag]
    return abs(x) if absolute else x


# ════════════════════════════════════════════════════════════
# ① 风险环境
# ════════════════════════════════════════════════════════════

def risk_states(fr: Dict, ch: Dict, t: int, pct_M: Optional[float] = None) -> Dict[str, Dict]:
    """S1–S10 在 t 的读数：{code: {on, value, pct?, text}}；on = None 表示数据不足。"""
    lv, raw = fr["level"], fr["raw"]
    lo = max(0, t - PCT_WIN)
    out: Dict[str, Dict] = {}

    def pct_series(fn):
        vals = [None] * lo + [fn(i) for i in range(lo, t + 1)]
        return vals

    vix = lv["VIX"]
    p = pct_at(vix, t)
    out["S1"] = {"name": "波动", "on": None if p is None else p >= 0.8, "value": vix[t], "pct": p,
                 "text": f"VIX {vix[t]:.1f}（3年{_pp(p)}分位）" if vix[t] is not None else "VIX 缺"}
    v3 = lv["VIX3M"][t]
    ratio = vix[t] / v3 if vix[t] is not None and v3 else None
    out["S2"] = {"name": "期限", "on": None if ratio is None else ratio >= 1.0, "value": ratio,
                 "text": f"VIX/VIX3M {ratio:.2f}（≥1 倒挂）" if ratio is not None else "VIX3M 缺"}
    spy = raw.get("SPY") or []
    sma = None
    if t >= 199 and all(v is not None for v in spy[t - 199:t + 1]):
        sma = sum(spy[t - 199:t + 1]) / 200
    gap = spy[t] / sma - 1 if sma else None
    out["S3"] = {"name": "趋势", "on": None if sma is None else spy[t] < sma, "value": gap,
                 "text": f"SPY 距 200 日线 {gap * 100:+.1f}%" if gap is not None else "SPY 缺"}
    c20 = pct_series(lambda i: _sum_window(ch["CREDIT"], i, 20, 16))
    p = pct_at(c20, t)
    out["S4"] = {"name": "信用", "on": None if p is None else p <= 0.2, "value": c20[t], "pct": p,
                 "text": f"高收益超额 20日 {c20[t] * 100:+.2f}%（3年{_pp(p)}分位）" if c20[t] is not None else "HYG 缺"}
    mv = lv["MOVE"]
    p = pct_at(mv, t)
    out["S5"] = {"name": "利率波动", "on": None if p is None else p >= 0.8, "value": mv[t], "pct": p,
                 "text": f"MOVE {mv[t]:.0f}（3年{_pp(p)}分位）" if mv[t] is not None else "MOVE 缺"}
    d10 = pct_series(lambda i: _shift_diff(lv["US10Y"], i, 20, absolute=True))
    p = pct_at(d10, t)
    raw10 = _shift_diff(lv["US10Y"], t, 20)
    out["S6"] = {"name": "利率急变", "on": None if p is None else p >= 0.8, "value": raw10, "pct": p,
                 "text": f"10Y 20日 {raw10 * 100:+.0f}bp（幅度 3年{_pp(p)}分位）" if raw10 is not None else "10Y 缺"}
    dx = pct_series(lambda i: _shift_log(lv["DXY"], i, 20))
    p = pct_at(dx, t)
    out["S7"] = {"name": "美元急升", "on": None if p is None else p >= 0.8, "value": dx[t], "pct": p,
                 "text": f"美元 20日 {dx[t] * 100:+.1f}%（3年{_pp(p)}分位）" if dx[t] is not None else "DXY 缺"}
    ol = pct_series(lambda i: _shift_log(lv["WTI"], i, 20, absolute=True))
    p = pct_at(ol, t)
    oraw = _shift_log(lv["WTI"], t, 20)
    out["S8"] = {"name": "油价冲击", "on": None if p is None else p >= 0.8, "value": oraw, "pct": p,
                 "text": f"WTI 20日 {oraw * 100:+.1f}%（幅度 3年{_pp(p)}分位）" if oraw is not None else "WTI 缺"}
    sb = corr_at(ch["SPY"], ch["TLT"], t, 60, 48)
    out["S9"] = {"name": "股债同向", "on": None if sb is None else sb > 0, "value": sb,
                 "text": f"60日 股债相关 {sb:+.2f}" if sb is not None else "TLT 缺"}
    out["S10"] = {"name": "宏观主导高", "on": None if pct_M is None else pct_M >= 0.8, "value": pct_M,
                  "text": f"宏观主导度 3年{_pp(pct_M)}分位" if pct_M is not None else "缺"}
    for code, row in out.items():
        row["code"] = code
        row["validated"] = code in RISK_LIGHTS
    return out


def risk_tier(states: Dict[str, Dict]) -> Dict:
    lights = [states[c] for c in RISK_LIGHTS]
    known = [s for s in lights if s["on"] is not None]
    n = sum(1 for s in known if s["on"])
    k = min(n, 4)
    return {"count": n, "known": len(known), "of": len(RISK_LIGHTS), "tier": TIER_CN[k],
            "base": TIER_BASE[k], "on": [s["name"] for s in lights if s["on"]]}


def _pp(p: Optional[float]) -> str:
    return "—" if p is None else f"{p * 100:.0f}"


# ════════════════════════════════════════════════════════════
# ② 最强驱动
# ════════════════════════════════════════════════════════════

def drivers(ch: Dict, t: int) -> Dict:
    """8 因子 40 日相关 / 相关² / 3 年分位 / 20 日相关；宏观主导度 M 与分位；主导 / 接棒。"""
    y = ch["SPY"]
    lo = max(0, t - PCT_WIN)
    rows = []
    for f in FACTORS:
        x = ch[f]
        r2s = rolling(lambda i: (lambda r: None if r is None else r * r)(corr_at(y, x, i, W)), lo, t)
        rho = corr_at(y, x, t, W)
        rho20 = corr_at(y, x, t, W_FAST)
        rows.append({"factor": f, "cn": FACTOR_CN[f], "rho": rho, "r2": None if rho is None else rho * rho,
                     "pct": pct_at(r2s, t), "rho20": rho20, "r2_20": None if rho20 is None else rho20 * rho20,
                     "rho_lag": r2s[t - RISE_LAG] ** 0.5 * (1 if (corr_at(y, x, t - RISE_LAG, W) or 0) >= 0 else -1)
                     if t - RISE_LAG >= lo and r2s[t - RISE_LAG] is not None else None,
                     "r2_lag": r2s[t - RISE_LAG] if t - RISE_LAG >= lo else None})
    Ms = rolling(lambda i: r2_at(y, [ch[f] for f in FACTORS], i, W), lo, t)
    M, pM = Ms[t], pct_at(Ms, t)
    elig = [r for r in rows if r["pct"] is not None and r["r2"] is not None
            and r["pct"] >= PCT_DOM and r["r2"] >= R2_DOM]
    complete = all(r["pct"] is not None for r in rows)
    top = max(elig, key=lambda r: r["r2"]) if elig and complete else None
    dominant = top["factor"] if top else None
    co = [r["factor"] for r in elig if top and r is not top and r["r2"] >= CO_DOM * top["r2"]]
    for r in rows:
        r["trend"] = _trend(r)
    rising = [r for r in rows if r["factor"] != dominant and r["factor"] not in co and r["trend"] == "升温"
              and r["r2"] is not None and r["r2"] >= R2_DOM]
    rising += [r for r in rows if r["factor"] in co and r["trend"] == "升温"]
    challenger = max(rising, key=lambda r: r["r2"] - (r["r2_lag"] or 0))["factor"] if rising else None
    rows.sort(key=lambda r: -(r["r2"] or 0))
    return {"window": W, "rows": rows, "M": M, "pct_M": pM, "dominant": dominant, "co_dominant": co,
            "complete": complete, "challenger": challenger}


def _trend(r: Dict) -> str:
    """40 日相关² 相对 RISE_LAG 个交易日前：升温 / 降温 / 持平（描述性，未验证）。"""
    if r["r2"] is None or r["r2_lag"] is None:
        return "—"
    d = r["r2"] - r["r2_lag"]
    if d >= RISE_R2:
        return "升温"
    if d <= -RISE_R2:
        return "降温"
    return "持平"


def sign_of(rho: Optional[float]) -> int:
    if rho is None or abs(rho) < SIG_RHO:
        return 0
    return 1 if rho > 0 else -1


def reading(factor: str, rho: Optional[float]) -> Optional[Tuple[str, str]]:
    s = sign_of(rho)
    return READING.get((factor, s)) if s else None


def sign_age(ch: Dict, factor: str, t: int, cap: int = 250) -> Optional[int]:
    """当前读法（40 日 ρ 的符号且 |ρ|≥SIG_RHO）已连续多少个交易日。"""
    s = sign_of(corr_at(ch["SPY"], ch[factor], t, W))
    if not s:
        return None
    n = 0
    for i in range(t, max(-1, t - cap), -1):
        if sign_of(corr_at(ch["SPY"], ch[factor], i, W)) != s:
            break
        n += 1
    return n


# ════════════════════════════════════════════════════════════
# ③ 市场在交易什么
# ════════════════════════════════════════════════════════════

CHAIN_PCT = 0.75        # 读法链只列解释力高于自身常态的因子（结构性相关的信用 / 铜常态就高，不算「在交易」）


def chain(drv: Dict) -> List[Dict]:
    """有读法（|ρ|≥0.30）且解释力在自身 3 年 p75 以上的因子，按相关² 降序：股跌时它们各自怎么动。"""
    out = []
    for r in drv["rows"]:
        s = sign_of(r["rho"])
        if not s or r["pct"] is None or r["pct"] < CHAIN_PCT:
            continue
        up = FACTOR_UP[r["factor"]]
        out.append({"factor": r["factor"], "cn": r["cn"], "rho": r["rho"], "sign": s,
                    "text": f"{up} → 股{'跌' if s < 0 else '涨'}（ρ {r['rho']:+.2f}）"})
    return out


def leadership(ch: Dict, t: int, w: int = W_FAST) -> List[Dict]:
    """行业相对 SPY 的 w 日 ln 超额，降序。"""
    base = _sum_window(ch["SPY"], t, w, int(MIN_FRAC * w))
    out = []
    for k, cn in SECTORS.items():
        v = _sum_window(ch[k], t, w, int(MIN_FRAC * w))
        if v is None or base is None:
            continue
        out.append({"ticker": k, "cn": cn, "rel": v - base})
    out.sort(key=lambda r: -r["rel"])
    return out


def story(drv: Dict, ch: Dict, t: int) -> Dict:
    dom = drv["dominant"]
    top = next((r for r in drv["rows"] if r["factor"] == dom), None) if dom else None
    lead = next((r for r in drv["rows"] if sign_of(r["rho"])), None)
    if top is not None and reading(dom, top["rho"]):
        theme, why = reading(dom, top["rho"])
        basis = "dominant"
        for f in drv.get("co_dominant") or []:
            r = next(x for x in drv["rows"] if x["factor"] == f)
            if reading(f, r["rho"]) and reading(f, r["rho"])[0] != theme:
                theme += "，叠加" + reading(f, r["rho"])[0]
    elif lead is not None and reading(lead["factor"], lead["rho"]):
        theme, why = reading(lead["factor"], lead["rho"])
        theme, basis = theme + "（无异常突出的驱动，按相关最强者）", "strongest"
    else:
        theme, why, basis = "宏观无突出驱动", "宏观因子对指数解释力都在常态以内：个股 / 盈利 / 主题在驱动", "none"
    return {"theme": theme, "why": why, "basis": basis, "chain": chain(drv), "leaders": leadership(ch, t)}


def rates_split(fred: Dict[str, Series], lag_days: int = 28) -> Optional[Dict]:
    """10Y 近 20 个交易日变动拆成 实际利率 + 通胀预期（FRED DGS10 / DFII10 / T10YIE，T+1 发布，带 as-of）。"""
    def last_and_prior(rows: Series):
        rows = [r for r in rows if r[1] is not None]
        if len(rows) < 21:
            return None
        return rows[-1], rows[-21]
    parts = {}
    for key in ("DGS10", "DFII10", "T10YIE"):
        lp = last_and_prior(fred.get(key) or [])
        if lp is None:
            return None
        parts[key] = lp
    asof = parts["DGS10"][0][0]
    d = {k: (v[0][1] - v[1][1]) * 100 for k, v in parts.items()}
    return {"asof": asof, "since": parts["DGS10"][1][0], "nominal_bp": d["DGS10"], "real_bp": d["DFII10"],
            "breakeven_bp": d["T10YIE"], "level": parts["DGS10"][0][1],
            "real_level": parts["DFII10"][0][1], "breakeven_level": parts["T10YIE"][0][1]}


def since_extreme(dates: Sequence[str], vals: Sequence[Optional[float]], t: int, min_days: int = 252) -> Optional[Dict]:
    """当前值是「自 X 以来最高 / 最低」吗（X 距今 ≥ min_days 个交易日才报）。"""
    v = vals[t]
    if v is None:
        return None
    hi = lo = None
    for i in range(t - 1, -1, -1):
        x = vals[i]
        if x is None:
            continue
        if hi is None and x >= v:
            hi = i
        if lo is None and x <= v:
            lo = i
        if hi is not None and lo is not None:
            break
    if hi is None and t >= min_days:
        return {"kind": "高", "since": dates[0], "record": True}
    if hi is not None and t - hi >= min_days:
        return {"kind": "高", "since": dates[hi], "record": False}
    if lo is None and t >= min_days:
        return {"kind": "低", "since": dates[0], "record": True}
    if lo is not None and t - lo >= min_days:
        return {"kind": "低", "since": dates[lo], "record": False}
    return None


# ════════════════════════════════════════════════════════════
# ④ 未来方向
# ════════════════════════════════════════════════════════════

def shock_size(ch: Dict, factor: str, t: int, horizon: int = 20, look: int = 252) -> Optional[float]:
    """因子 horizon 日累计变动的 1σ（近 look 日，重叠窗口）。"""
    sums = [s for s in (_sum_window(ch[factor], i, horizon, int(MIN_FRAC * horizon))
                        for i in range(max(horizon, t - look + 1), t + 1)) if s is not None]
    if len(sums) < 60:
        return None
    m = sum(sums) / len(sums)
    return math.sqrt(sum((s - m) ** 2 for s in sums) / (len(sums) - 1))


def scenarios(ch: Dict, factor: str, t: int, targets: Sequence[str] = SCENARIO_TARGETS) -> Optional[Dict]:
    """因子 ±1σ(20日) → 各目标按 40 日 β 推算的变动（H4 不过的因子不给数字）。"""
    if BETA_STABLE.get(factor, 0) <= 0.3:
        return None
    sd = shock_size(ch, factor, t)
    if sd is None:
        return None
    rows = []
    for k in targets:
        b = beta_at(ch[k], ch[factor], t, W)
        if b is not None:
            rows.append({"ticker": k, "beta": b, "move": b * sd})
    return {"factor": factor, "shock": sd, "unit": FACTOR_UNIT[factor], "rows": rows}


def flip_watch(drv: Dict) -> List[str]:
    """读法翻转 = 市场换剧本的信号：最强驱动 / 接棒因子的 ρ 过零。"""
    out = []
    for f in (drv["dominant"], drv["challenger"]):
        if not f:
            continue
        r = next(x for x in drv["rows"] if x["factor"] == f)
        s = sign_of(r["rho"])
        if not s:
            continue
        other = READING.get((f, -s))
        if other:
            out.append(f"{FACTOR_UP[f]}时股{'不再跌' if s < 0 else '不再涨'}（{FACTOR_CN[f]} ρ 过零）→ 剧本从「"
                       f"{READING[(f, s)][0]}」换成「{other[0]}」")
    return out


# ════════════════════════════════════════════════════════════
# 汇总
# ════════════════════════════════════════════════════════════

def context(series: Dict[str, Series], asof: Optional[str] = None) -> Tuple[Dict, Dict, int]:
    """(对齐帧, 日变动, 读数位置 t)。t = 最后一个 SPY 交易日，或 asof 当日及之前最近一日。"""
    fr = align(series)
    dates = fr["dates"]
    if not dates:
        raise ValueError("SPY 序列为空")
    t = len(dates) - 1
    if asof:
        while t >= 0 and dates[t] > asof:
            t -= 1
        if t < 0:
            raise ValueError(f"asof {asof} 早于数据起点")
    return fr, changes(fr), t


def assess(series: Dict[str, Series], asof: Optional[str] = None) -> Dict:
    """全量读数。series = {KEY: [(date, value)]}（data/macro_series.load 的形状）。"""
    fr, ch, t = context(series, asof)
    dates = fr["dates"]
    drv = drivers(ch, t)
    st = risk_states(fr, ch, t, drv["pct_M"])
    lv = fr["level"]
    readings = {}
    for key, cn in (("US10Y", "10Y"), ("WTI", "WTI"), ("DXY", "美元指数"), ("VIX", "VIX"), ("MOVE", "MOVE")):
        vals = lv.get(key) or fr["raw"].get(key)
        if vals and vals[t] is not None:
            readings[key] = {"cn": cn, "value": vals[t], "d20": _shift_diff(vals, t, 20),
                             "extreme": since_extreme(dates, vals, t)}
    for key, cn in (("GOLD", "黄金"), ("COPPER", "铜"), ("US3M", "3M 国库券")):
        vals = fr["raw"].get(key)
        if vals and vals[t] is not None:
            readings[key] = {"cn": cn, "value": vals[t], "d20": _shift_diff(vals, t, 20),
                             "extreme": since_extreme(dates, vals, t)}
    fred = {k: series.get(k) or [] for k in ("DGS10", "DFII10", "T10YIE", "DGS2", "HY_OAS")}
    fred = {k: [r for r in v if r[0] <= dates[t]] for k, v in fred.items()}
    split = rates_split(fred)
    dom = drv["dominant"]
    fwd_factors = [f for f in (dom, drv["challenger"]) if f]
    if not fwd_factors and drv["rows"] and sign_of(drv["rows"][0]["rho"]):
        fwd_factors = [drv["rows"][0]["factor"]]
    return {
        "schema": SCHEMA, "design": DESIGN_DOC, "asof": dates[t],
        "params": {"window": W, "fast": W_FAST, "pct_window": PCT_WIN, "pct_dom": PCT_DOM, "r2_dom": R2_DOM},
        "risk": {"states": st, "tier": risk_tier(st)},
        "readings": readings, "rates_split": split,
        "fred_last": {k: (v[-1] if v else None) for k, v in fred.items()},
        "drivers": drv,
        "dominant_history": {f: dict(zip(("n", "fut_r2", "uncond", "sign_keep"), DOMINANT_HISTORY[f]))
                             for f in fwd_factors if f in DOMINANT_HISTORY},
        "sign_age": {f: sign_age(ch, f, t) for f in fwd_factors},
        "story": story(drv, ch, t),
        "forward": {"scenarios": [s for s in (scenarios(ch, f, t) for f in fwd_factors) if s],
                    "flip_watch": flip_watch(drv),
                    "m_high": bool(drv["pct_M"] is not None and drv["pct_M"] >= 0.8),
                    "m_high_finding": M_HIGH_FINDING},
        "divergence": divergence_states(fr, t),
        "breadth": breadth_state(fr, t),
    }


def _window_min_max(vals: Sequence[Optional[float]], t: int, n: int = 20, need: int = 16):
    w = [v for v in vals[max(0, t - n + 1):t + 1] if v is not None]
    return (min(w), max(w)) if len(w) >= need else (None, None)


def divergence_states(fr: Dict, t: int) -> Dict[str, Dict]:
    """D2 价涨波升 / D4 底部不确认。
    on = P ∧ G；P 不成立 = False；缺数据 = None。描述性，零授权，不预告方向。"""
    raw, lv, n = fr["raw"], fr["level"], len(fr["dates"])
    spy = raw.get("SPY") or [None] * n
    vix = lv.get("VIX") or [None] * n

    def ln_ratio(a, b):
        return math.log(a / b) if a is not None and b is not None and a > 0 and b > 0 else None

    def on(p, g):
        return False if p is False else (None if p is None or g is None else bool(p and g))

    r5 = ln_ratio(spy[t], spy[t - 5]) if t >= 5 else None
    v5 = ln_ratio(vix[t], vix[t - 5]) if t >= 5 else None
    p2 = None if r5 is None else r5 > 0
    g2 = None if v5 is None else v5 > 0
    out = {"D2": {"name": "价涨波升", "P": p2, "G": g2, "on": on(p2, g2), "finding": DIVERGENCE_FINDING["D2"],
                  "text": ("—" if r5 is None or v5 is None else f"SPY 5 日 {r5 * 100:+.1f}%，VIX 5 日 {v5 * 100:+.0f}%")}}

    closes = spy[max(0, t - 19):t + 1]
    p4 = None if t < 19 or any(c is None for c in closes) else spy[t] <= min(closes)
    hyg, ief, rsp = (raw.get(k) or [None] * n for k in ("HYG", "IEF", "RSP"))
    s0 = max(0, t - 19)
    credit, run = [], 0.0                        # 窗内相对累计水平（与全历史累计同序，平移不变）
    for i in range(s0, t + 1):
        a, b = (ln_ratio(hyg[i], hyg[i - 1]), ln_ratio(ief[i], ief[i - 1])) if i >= 1 else (None, None)
        c = None if a is None or b is None else a - 0.5 * b
        run += c or 0.0
        credit.append(None if c is None else run)
    breadth = [ln_ratio(rsp[i], spy[i]) for i in range(s0, t + 1)]
    votes = []
    for series_, beats in ((credit, lambda x, lo, hi: x > lo), (vix[s0:t + 1], lambda x, lo, hi: x < hi),
                           (breadth, lambda x, lo, hi: x > lo)):
        lo, hi = _window_min_max(series_, len(series_) - 1)
        x = series_[-1]
        votes.append(None if lo is None or x is None else beats(x, lo, hi))
    g4 = None if any(v is None for v in votes) else sum(votes) >= 2
    names = ("信用未创新低", "VIX 未创新高", "等权未创新低")
    out["D4"] = {"name": "底部不确认", "P": p4, "G": g4, "on": on(p4, g4), "finding": DIVERGENCE_FINDING["D4"],
                 "text": ("—" if p4 is None else ("SPY 非 20 日新低" if not p4 else
                          "SPY 20 日新低；" + ("、".join(nm for nm, v in zip(names, votes) if v) or "三项都跟着创新") +
                          (f"（{sum(v for v in votes if v)}/3）" if g4 is not None else "（缺数据）")))}
    return out


def breadth_state(fr: Dict, t: int) -> Dict:
    """广度描述：等权 / 小盘相对 SPY（20 / 60 日 ln 变动 + 自身 3 年分位）、11 行业站上 50 日线家数、
    B60「高位 + 窄」状态。缺数据的项 = None。零授权，不预告方向（BREADTH_FINDING）。"""
    raw = fr["raw"]
    n = len(fr["dates"])
    spy = raw.get("SPY") or [None] * n

    def ln_ratio(a, b):
        return math.log(a / b) if a is not None and b is not None and a > 0 and b > 0 else None

    def rel(key: str) -> Dict:
        other = raw.get(key) or [None] * n
        ratio = [ln_ratio(other[i], spy[i]) for i in range(n)]
        out = {}
        for lag in (20, 60):
            ser = rolling(lambda i, lag=lag: _shift_diff(ratio, i, lag), max(0, t - PCT_WIN), t)
            out[f"d{lag}"] = ser[t]
            out[f"pct{lag}"] = pct_at(ser, t)
        return out

    above, known = [], 0
    for k, cn in BREADTH_SECTORS.items():
        c = (raw.get(k) or [None] * n)[max(0, t - 49):t + 1]
        if len(c) < 50 or any(x is None for x in c):
            continue
        known += 1
        if c[-1] > sum(c) / 50:
            above.append(cn)

    w60 = spy[max(0, t - 59):t + 1]
    r60 = _shift_log(spy, t, 60)
    p = None if len(w60) < 60 or any(x is None for x in w60) or r60 is None \
        else bool(spy[t] >= 0.97 * max(w60) and r60 > 0)
    eq = rel("RSP")
    g = None if eq["pct60"] is None else eq["pct60"] <= 0.2
    on = False if p is False else (None if p is None or g is None else bool(p and g))
    return {"equal_weight": eq, "small_cap": rel("IWM"),
            "sectors_above_50dma": {"n": len(above), "of": known, "names": above},
            "B60": {"P": p, "G": g, "on": on}, "finding": BREADTH_FINDING, "design": BREADTH_DESIGN}


def book_exposure(holding_returns: Dict[str, Sequence[Optional[float]]], weights: Dict[str, float],
                  factor_ret: Sequence[Optional[float]], t: int) -> Optional[Dict]:
    """组合对因子的 β = Σ 权重 × 各持仓 40 日 β（权重 = 名义 / NAV，期权按 delta 名义）。持仓收益须与 factor_ret 同一日期轴。"""
    rows, tot, used = [], 0.0, 0.0
    for tk, w in weights.items():
        r = holding_returns.get(tk)
        if r is None:
            continue
        b = beta_at(r, factor_ret, t, W)
        if b is None:
            continue
        rows.append({"ticker": tk, "weight": w, "beta": b, "contrib": w * b})
        tot += w * b
        used += w
    if not rows:
        return None
    rows.sort(key=lambda r: -abs(r["contrib"]))
    return {"beta": tot, "weight_used": used, "rows": rows}


# ════════════════════════════════════════════════════════════
# 日历通道与情报关键词（journal 层读文件，这里只给映射）
# ════════════════════════════════════════════════════════════

# 事件 → 它直接击中的因子（名称关键词，先到先得）
EVENT_CHANNELS = (
    (("FOMC", "联储", "议息", "利率决议"), ("R2", "R10"), "联储路径"),
    (("CPI", "PCE", "PPI", "通胀", "物价"), ("R2", "R10", "OIL"), "通胀 → 利率"),
    (("就业报告", "非农", "NFP", "Employment", "JOLTS", "初请", "失业"), ("R2", "R10"), "就业 → 利率"),
    (("GDP", "ISM", "PMI", "零售", "耐用品", "新屋", "成屋", "工业产出"), ("COPPER", "R10"), "增长"),
    (("OPEC", "EIA", "原油", "石油"), ("OIL",), "原油"),
    (("国债拍卖", "再融资", "Refunding", "auction"), ("R10",), "期限溢价"),
)


# 事件量级（推送只列 A / B；A = 历史上单日能改写利率路径的那几个）
EVENT_TIER_A = ("FOMC", "联储", "议息", "利率决议", "CPI", "PCE", "就业报告", "非农", "NFP")
EVENT_TIER_B = ("JOLTS", "ISM", "GDP", "零售", "PPI", "OPEC")


def event_tier(name: str) -> str:
    low = name.lower()
    if any(k.lower() in low for k in EVENT_TIER_A):
        return "A"
    if any(k.lower() in low for k in EVENT_TIER_B):
        return "B"
    return "C"


def event_channel(name: str) -> Optional[Tuple[Tuple[str, ...], str]]:
    for keys, factors, label in EVENT_CHANNELS:
        if any(k.lower() in name.lower() for k in keys):
            return factors, label
    return None


# 因子 → 情报归档里的关键词（触发源只取归档命中，不补查、不编造）
ARCHIVE_KEYWORDS = {
    "OIL": ("原油", "油价", "布伦特", "Brent", "WTI", "伊朗", "霍尔木兹", "Hormuz", "OPEC", "石油"),
    "R10": ("10年期", "长端", "期限溢价", "30Y", "10Y", "国债拍卖", "财政"),
    "R2": ("联储", "FOMC", "加息", "降息", "前端", "2Y", "鲍威尔"),
    "USD": ("美元", "DXY"),
    "GOLD": ("黄金", "金价"),
    "CREDIT": ("信用", "利差", "OAS", "违约", "私募信贷", "高收益"),
    "JPY": ("日元", "日本央行", "BOJ", "套息"),
    "COPPER": ("铜", "中国刺激"),
}


# ════════════════════════════════════════════════════════════
# 发布文件（trade/intelligence/macro/current.json）的校验与下游视图
# ════════════════════════════════════════════════════════════

PUBLISHED_KEYS = ("schema", "asof", "generated_at", "authority", "risk", "drivers", "story", "forward")


def validate_published(doc) -> List[str]:
    """发布文件的最小合同：schema / 必需块 / 零授权 / 带时区的生成时间。返回错误列表（空 = 合格）。"""
    if not isinstance(doc, dict):
        return ["not_an_object"]
    errors = [f"missing:{k}" for k in PUBLISHED_KEYS if k not in doc]
    if doc.get("schema") != SCHEMA:
        errors.append("schema")
    if doc.get("authority") != "none":
        errors.append("authority")
    stamp = doc.get("generated_at")
    try:
        from datetime import datetime as _dt
        if not isinstance(stamp, str) or _dt.fromisoformat(stamp).tzinfo is None:
            errors.append("generated_at")
    except ValueError:
        errors.append("generated_at")
    return errors


def prior_view(doc: Dict) -> Dict:
    """给 worker / 卡片快照的精简先验（standing #4：方向先验与风险环境，零授权）。"""
    drv = doc.get("drivers") or {}
    rows = {r.get("factor"): r for r in drv.get("rows") or []}

    def factor(f):
        r = rows.get(f)
        if not r:
            return None
        rd = reading(f, r.get("rho"))
        return {"factor": f, "cn": FACTOR_CN.get(f, f), "rho": r.get("rho"), "r2": r.get("r2"),
                "pct": r.get("pct"), "trend": r.get("trend"), "reading": rd[0] if rd else None}
    tier = (doc.get("risk") or {}).get("tier") or {}
    story_ = doc.get("story") or {}
    leaders = story_.get("leaders") or []
    return {
        "schema": doc.get("schema"), "asof": doc.get("asof"), "generated_at": doc.get("generated_at"),
        "authority": "none",
        "risk_tier": {k: tier.get(k) for k in ("tier", "count", "of", "on")},
        "strongest_driver_40d": factor(drv.get("dominant")) if drv.get("dominant") else None,
        "co_strongest_40d": [factor(f) for f in drv.get("co_dominant") or []],
        "challenger": factor(drv.get("challenger")) if drv.get("challenger") else None,
        "macro_dominance": {"M": drv.get("M"), "pct": drv.get("pct_M")},
        "theme": story_.get("theme"), "why": story_.get("why"),
        "rates_split": doc.get("rates_split"),
        "sector_leaders": [{"ticker": r.get("ticker"), "rel": r.get("rel")} for r in leaders[:3]],
        "sector_laggards": [{"ticker": r.get("ticker"), "rel": r.get("rel")} for r in leaders[-3:]],
        "note": "FYI 判读层（core/macro_lens）：风险灯预告波动与回撤深度，"
                "不预告方向；最强驱动描述近 40 日，不承诺延续；不翻译成 GO / VETO / cap / 股数。",
    }
