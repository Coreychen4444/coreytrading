"""形态判读的单一真源 —— Lo, Mamaysky & Wang (2000) 的工序，逐条照抄。

════════════════════════════════════════════════════════════════════════
🎯 核心目的（用户  逐字定版，钉在代码里防跑偏）
════════════════════════════════════════════════════════════════════════

    「每天收盘后 你根据 k 线 交易量 指标等，分析出当前所处结构及形态，
      例如这可能是底部 这可能见顶 这会继续延续 这后续会震荡
      这会形成双顶 楔形 头肩形态，然后进而分析出潜在的交易机会」

    链条 = Context → Structure → Price Action → Volume → Indicators → Trade

这段话是本模块与 `quant/report/chart_read.py` 存在的**唯一理由**。
任何一次改动，先问「它是否让上面这件事做得更好」——不是「它是否更严谨/更全面」。

🚨 **判读层 ≠ 授权层。这是本模块的第一条边界，`tests/core/test_patterns.py` 钉它。**

    判读层（本模块）              授权层（entry_guard / regime_transition / sizing）
    ─────────────────────────    ──────────────────────────────────────────────
    产物 = 假设 + 证伪条件        产物 = PLAN/VETO/cap/stop/档位
    决策人 = 用户                决策人 = 代码，无例外
    证据标准 = 标注先验强度       证据标准 = 预注册回测 / Kish N / 守卫 / ledger
    说错的代价 = 他看了不认       说错的代价 = 自动下单、自动放大、自动静默

    ⇒ 本模块**永不**被 decision/ 层 import；输出里**永不**出现 cap / 档位 / 下单价。
    这与 v3「授权纯结构机器管，判断权归 用户，agent 供弹药」是同一条分工。

════════════════════════════════════════════════════════════════════════
方法论：为什么是 Lo 的工序而不是我自己写几个 if
════════════════════════════════════════════════════════════════════════

出处：Andrew W. Lo, Harry Mamaysky, Jiang Wang, *Foundations of Technical Analysis:
Computational Algorithms, Statistical Inference, and Empirical Implementation*,
*Journal of Finance* 55(4), 2000, 1705-1765.（本仓上一轮读了全文，见
`quant/research/price_action_framework_design.md` §4.1）

抄它的四个理由，每个都对应本仓踩过的一个坑：

1. **族由外部封死，不由我今天想到几个封顶。**它的 10 个形态是一篇 2000 年
   顶刊论文定下的，不是我挑的 —— 这是对数据窥探（Sullivan-Timmermann-White 的
   7,846 条规则 / Bajgrowicz-Scaillet FDR 后无一存活）唯一诚实的回应。
2. **容差参数是他给的，不是我调的**（HS/DTOP 1.5%、矩形 0.75%、双顶间隔 22 日
   沿用 Edwards & Magee）。原文脚注 5 说明带宽选择是在统计分析**之前**定下的
   —— 那是 2000 年的一次预注册，与本仓「判据先于数字 commit」同构。
3. **平滑后再取极值**。原文点名反对在原始价上直接找极值：那样「识别出太多极值，
   而且产出的形态与技术分析师认为可信的形态不一致」。
   ⚠️ 本仓 `technical.swing_points(wing=2)` 正是他反对的那种做法 —— 那是已登记的
   方法论债（它驱动 `trend` → 授权，**要动就整体重跑 structure_downgrade_backtest**）。
   本模块**不碰**`swing_points`，自己走核回归这条路：判读层可以用更贵的方法，
   因为它不需要向后兼容任何已接线的判别式。
4. **每个形态有已发表的信息含量 p 值**（`PATTERN_SPECS[...]["ks_p"]`）——
   于是「这像双顶」可以带着「这类形状历史上有多强」一起说出口。

**Lo 的两条结论必须一起引**（只引一半就是滥用文献）：
  正面：「certain technical patterns … **do provide incremental information**,
        especially for Nasdaq stocks」
  限制：「Although this does not necessarily imply that technical analysis can be
        used to generate '**excess**' trading profits …」
⇒ 形态给的是**信息含量**，不是盈利保证。本模块的输出因此永远是「假设 + 证伪」，
   不是「买/卖」。

════════════════════════════════════════════════════════════════════════
为什么判读层可以有比授权层更大的形态族
════════════════════════════════════════════════════════════════════════

数据窥探的危害发生在「**跑一堆再挑好的那个去用**」。判读层不挑：它**全报**，
每条带自己的证据等级与证伪条件，由人裁。所以：

  - 授权层加一个形态 = +1 自由度，必须走预注册（landing plan §2 的 DoF 预算）；
  - 判读层加一个形态 = +1 条**带标签的假设**，代价是读者的注意力，不是统计有效性。

因此本模块允许收录 Lo 族之外的形状（如**楔形**—— 用户 点名要），
但**必须**标 `evidence="民间"` 且 `ks_p=None`，渲染层照实播「无正式检验」。
**混进 Lo 族冒充有出处 = 本模块最严重的违规**，测试钉它。
"""
from __future__ import annotations

import math
from typing import Callable, Dict, List, Optional, Sequence, Tuple

# ── Lo 的三个参数（原文给定，不是我调的；改动 = 离开这篇论文的授权范围） ──
LO_WINDOW = 35          # 滚动窗口 l：形态必须在窗口内完成
LO_LAG = 3              # 检测滞后 d：形态完成后第 d 日之后才算数（零前视）
TOL_PCT = 0.015         # HS / DTOP 族容差 1.5%
TOL_RECT_PCT = 0.0075   # 矩形族容差 0.75%
DTOP_MIN_SEP = 22       # 双顶两峰最小间隔（交易日，沿用 Edwards & Magee）
BANDWIDTH_SCALE = 0.3   # h = 0.3 × h*_CV（原文自陈 "admittedly ad hoc"，但事前定）


# ══════════════════════════════════════════════════════════════════
# 1. 核回归平滑（Lo 工序第一步）
# ══════════════════════════════════════════════════════════════════

def kernel_smooth(y: Sequence[float], h: float) -> List[float]:
    """Nadaraya-Watson 高斯核平滑，自变量 = 时间序号。

    m̂(t) = Σ K_h(t−j)·y_j / Σ K_h(t−j)，K = 标准正态核。
    h 越大越平滑；h→0 退化为原序列（= Lo 反对的那种做法）。
    """
    n = len(y)
    if n == 0:
        return []
    if h <= 0:
        return list(map(float, y))
    out: List[float] = []
    for i in range(n):
        num = den = 0.0
        for j in range(n):
            w = math.exp(-0.5 * ((i - j) / h) **2)
            num += w * y[j]
            den += w
        out.append(num / den if den else float(y[i]))
    return out


H_GRID = (1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 6.5, 8.0, 10.0, 13.0, 16.0, 20.0, 26.0)
DENSITY_MIN, DENSITY_MAX = 5, 12   # 每个窗口的目标极值数（见 bandwidth_for_density）


def cv_bandwidth(y: Sequence[float], grid: Optional[Sequence[float]] = None) -> float:
    """留一交叉验证选 h*，返回 **0.3 × h***（Lo 的原始做法）。

    ⚠️ **本模块不再默认用它 —— 因为在我们的数据上它退化了，实测如下。**

    Lo 的原文说交叉验证选出的 h* **太平滑**（分析师一致认为「过度平滑」），
    所以他们缩到 0.3×h*。我们复现时得到相反的病：LOO-CV 在日线上**总是选到
    网格最小值**（COHR  的 35 根窗口实测 h*=1.0 → ×0.3 = **0.3**），
    因为日价近似随机游走时，「用邻居预测自己」的最优解就是几乎不平滑。
    结果是 35 根里检出 **16 个极值**（还出现同一天既是极大又是极小）——
    **正是 Lo 点名反对的那种做法**，只是这次是从相反方向到达的。

    诚实处理：不假装复现了他们的带宽选择。改用 `bandwidth_for_density()`，
    并把这个偏离写在这里 —— 引用「本模块照抄 Lo 的工序」时，**必须连这条一起引**。
    保留本函数是为了让下一个人能自己复核这个退化，不是为了给它背书。
    """
    n = len(y)
    if n < 10:
        return 1.0
    grid = grid or H_GRID
    best_h, best_err = grid[0], float("inf")
    for h in grid:
        err = 0.0
        for i in range(n):
            num = den = 0.0
            for j in range(n):
                if j == i:                      # 留一
                    continue
                w = math.exp(-0.5 * ((i - j) / h) **2)
                num += w * y[j]
                den += w
            if den:
                err += (y[i] - num / den) **2
        if err < best_err:
            best_h, best_err = h, err
    return best_h * BANDWIDTH_SCALE


def bandwidth_for_density(bars: List[dict], lo: int = DENSITY_MIN,
                          hi: int = DENSITY_MAX) -> float:
    """选让**极值数落进 [lo, hi]**的最小带宽 —— 本模块实际使用的带宽规则。

    为什么是密度而不是 CV：形态语法要的是「一个窗口里有几个转折」这件事，
    而它必须**跨尺度不变**—— 同一套 5 极值不等式要能在 35 根与 120 根上都讲得通。
    密度目标做到了这一点（h 会随窗口自动放大），CV 做不到（它只关心拟合误差）。

    两个数的来历（说清楚，别装成推导出来的）：
      `lo = 5`  —— 5 极值不等式的**下限**，少于 5 个连这个语法都跑不起来；
      `hi = 12` —— 允许窗口里存在两组不重叠的 5 极值 + 余量。再多就是噪音，
                   Lo 的原话是「识别出太多极值，产出的形态与分析师认为可信的不一致」。

    在区间内取**最大**的 h（= 最平滑的那一档）：Lo 的方向是「宁可少而可信，
    不要多而琐碎」，带宽越大极值越少；取最小会退回「几乎不平滑」那一端。

    ⚠️ 这是**我们的选择**，不是 Lo 的参数。它没有回测背书 ——
    但它只影响判读层看到几个形态，不影响任何授权。
    """
    closes = [float(b.get("close", 0)) for b in bars]
    if len(closes) < 12:
        return 2.0
    ok: List[float] = []
    best_h, best_gap = None, float("inf")
    for h in H_GRID:
        n = len(_raw_extrema(bars, h))
        if lo <= n <= hi:
            ok.append(h)
            continue
        gap = (lo - n) if n < lo else (n - hi)
        if gap < best_gap:
            best_h, best_gap = h, gap
    return max(ok) if ok else (best_h or 2.0)


# ══════════════════════════════════════════════════════════════════
# 2. 极值检测（Lo 工序第二步）
# ══════════════════════════════════════════════════════════════════

def extrema(bars: List[dict], h: Optional[float] = None) -> List[Dict]:
    """平滑序列上一阶导变号 → 回**原始价**在**半周期 ∩ ±h**内取真实高低点。

    Lo 的原话：极值 = `sgn(m̂'(t)) = −sgn(m̂'(t+1))` 处，然后回到原始价格取该邻域
    的真实极值 —— 平滑只用来**定位**，报出去的价必须是 tape 上真实存在的价
    （否则画出来的线在 K 线图上找不到对应物）。

    「该邻域」多大， 由 ±1 改成**半周期 ∩ ±h**（两条约束都导出，
    乘数都是 1，理由与实测见 `_raw_extrema`）。±1 比平滑器自身的位移还小，
    全书 38% 的极值因此报错价 —— MSFT 双底颈线错 2.6N 就是这么来的。

    `h` 缺省走 `bandwidth_for_density()`（不是 `cv_bandwidth`，理由见后者 docstring）。
    返回 [{idx, kind: 'max'|'min', price, date}]，按时间升序、max/min 严格交替。
    """
    if len(bars) < 5:
        return []
    return _raw_extrema(bars, h if h is not None else bandwidth_for_density(bars))


def _turning_points(closes: List[float], h: float) -> List[Tuple[int, str]]:
    """平滑序列一阶导变号点 —— **只定位，不取价**。返回 [(t, 'max'|'min')]。"""
    n = len(closes)
    sm = kernel_smooth(closes, h)
    d = [sm[i + 1] - sm[i] for i in range(n - 1)]
    # 浮点噪音守卫：平坦段上 |d| 会在 1e-15 量级来回变号，若不挡掉，一段横盘
    # 会被读成「每根 bar 一个极值」并进而合成出矩形形态。
    # 实测：90 根等值收盘的尾巴造出 5 个假极值 + 一个假 RTOP。
    eps = 1e-7 * (sum(closes) / n)
    return [(i + 1, "max" if d[i] > 0 else "min")          # 变号点落在 t = i+1
            for i in range(len(d) - 1)
            if abs(d[i]) >= eps and abs(d[i + 1]) >= eps and d[i] * d[i + 1] < 0]


def _raw_extrema(bars: List[dict], h: float) -> List[Dict]:
    n = len(bars)
    if n < 5:
        return []
    closes = [float(b.get("close", 0)) for b in bars]
    highs = [float(b.get("high", 0)) for b in bars]
    lows = [float(b.get("low", 0)) for b in bars]
    tps = _turning_points(closes, h)

    out: List[Dict] = []
    for j, (t, kind) in enumerate(tps):
        # ── 回捞真实高低点的搜索窗 = **半周期 ∩ ±h**（由 ±1 改）──
        # 两条约束都是导出的，没有可调分数（乘数都是 1）：
        #   · 半周期（不越过相邻转折点）—— 越过就会偷隔壁摆动的极值。纯半周期实测
        #     位移最大 14 根（CRWV 转折 3/10 报出 3/30），且能报出**早于**转折点
        #     13 根的价（SNDK）；
        #   · ±h（一个带宽）—— 平滑器自身能造成的最大位移就是 h。
        # 旧的 ±1 是结构性错的：带宽中位 5.0，**搜索半径比它要纠正的误差还小**，
        # 于是 38% 的极值报出的价与它自己半周期里的真实极值差 >2%（全书 223 个）。
        # 源案例 MSFT：平滑顶落在 5/27，真顶是 6/01（466.32），±1 够不着 → 捞回
        # 5/28 的 429.49，双底颈线因此错 2.6N，「双底成立」的门槛凭空低了一截。
        # ⚠️ 极值**个数**不受影响（四个变体实测全是 223）—— 这只改「报什么价」。
        prev_t = tps[j - 1][0] if j > 0 else 0
        nxt_t = (tps[j + 1][0] if j + 1 < len(tps)
                 else min(n - 1, t + (t - prev_t)))     # 末端：镜像后向跨度
        k_h = math.ceil(h)
        lo_i, hi_i = max(0, prev_t, t - k_h), min(n - 1, nxt_t, t + k_h)
        span = range(lo_i, hi_i + 1)
        if kind == "max":
            k = max(span, key=lambda j2: highs[j2])
            price = highs[k]
        else:
            k = min(span, key=lambda j2: lows[j2])
            price = lows[k]
        if out and out[-1]["kind"] == kind:          # 强制交替：同向取更极端者
            if (kind == "max" and price > out[-1]["price"]) or \
               (kind == "min" and price < out[-1]["price"]):
                out[-1] = {"idx": k, "kind": kind, "price": price,
                           "date": str(bars[k].get("time", ""))[:10]}
            continue
        out.append({"idx": k, "kind": kind, "price": price,
                    "date": str(bars[k].get("time", ""))[:10]})
    return out


# ══════════════════════════════════════════════════════════════════
# 3. 形态族（Lo 的 10 个 + 明确标注的族外候选）
# ══════════════════════════════════════════════════════════════════
#
# ks_p = Lo 表 II 的 KS 检验 p 值（NYSE/AMEX 全样本）。**Nasdaq 全样本 10 个
# 形态全部在 5% 显著**—— 我们这本书全是 Nasdaq 大票，所以 NYSE 那一列是**保守读数**。
# 两列都给，渲染层报 NYSE 那个（取严）。

PATTERN_SPECS: Dict[str, Dict] = {
    "HS":    {"cn": "头肩顶",   "dir": "bearish", "family": "lo", "ks_p": 0.002,
              "nasdaq_sig": True,  "note": "Lo 族里 NYSE 最显著的一档"},
    "IHS":   {"cn": "头肩底",   "dir": "bullish", "family": "lo", "ks_p": 0.104,
              "nasdaq_sig": True,  "note": "NYSE 不显著 (0.104)，Nasdaq 显著"},
    "BTOP":  {"cn": "扩散顶",   "dir": "bearish", "family": "lo", "ks_p": 0.139,
              "nasdaq_sig": True,  "note": "NYSE 不显著"},
    "BBOT":  {"cn": "扩散底",   "dir": "bullish", "family": "lo", "ks_p": 0.004,
              "nasdaq_sig": True,  "note": ""},
    "TTOP":  {"cn": "收敛三角顶", "dir": "bearish", "family": "lo", "ks_p": 0.393,
              "nasdaq_sig": True,  "note": "NYSE 全族最弱 (0.393)"},
    "TBOT":  {"cn": "收敛三角底", "dir": "bullish", "family": "lo", "ks_p": 0.185,
              "nasdaq_sig": True,  "note": "NYSE 不显著；也是量趋势条件化唯一有差异的一个"},
    "RTOP":  {"cn": "矩形顶",   "dir": "bearish", "family": "lo", "ks_p": 0.002,
              "nasdaq_sig": True,  "note": "= 震荡区间上沿"},
    "RBOT":  {"cn": "矩形底",   "dir": "bullish", "family": "lo", "ks_p": 0.000,
              "nasdaq_sig": True,  "note": "NYSE 全族最显著"},
    "DTOP":  {"cn": "双顶",     "dir": "bearish", "family": "lo", "ks_p": 0.021,
              "nasdaq_sig": True,  "note": "两峰间隔须 >22 交易日"},
    "DBOT":  {"cn": "双底",     "dir": "bullish", "family": "lo", "ks_p": 0.215,
              "nasdaq_sig": True,  "note": "NYSE 不显著"},
    # ── 族外：用户 点名要，但 Lo 没测过。必须标出来。 ──
    "RWEDGE": {"cn": "上升楔形", "dir": "bearish", "family": "folk", "ks_p": None,
               "nasdaq_sig": None, "note": "Lo 族外：我没有找到可引用的正式检验"},
    "FWEDGE": {"cn": "下降楔形", "dir": "bullish", "family": "folk", "ks_p": None,
               "nasdaq_sig": None, "note": "Lo 族外：我没有找到可引用的正式检验"},
}

LO_FAMILY = tuple(k for k, v in PATTERN_SPECS.items() if v["family"] == "lo")
FOLK_FAMILY = tuple(k for k, v in PATTERN_SPECS.items() if v["family"] == "folk")

# **方向已定**的只有颈线族：形状本身就指向一边，破颈线 = 论点成立。
# 其余（矩形 / 三角 / 扩散 / 楔形）是**收敛或发散的容器**，方向由突破选边 ——
# `dir` 字段上的 bearish/bullish 只是它们名字里 TOP/BOT 的传统叫法，**不是判断**。
#  GOOGL 实测过这个矛盾：「扩散顶若成立 = 下行方向：收盘突破 384.48 = 向上解决」。
DIRECTIONAL = frozenset({"HS", "IHS", "DTOP", "DBOT"})


def evidence_grade(name: str) -> str:
    """一句话的先验强度，直接进播报 —— 不许省，省了就是拿论文给民间说法背书。"""
    s = PATTERN_SPECS[name]
    if s["family"] == "folk":
        return "民间·无正式检验"
    p = s["ks_p"]
    if p is None:
        return "未知"
    if p <= 0.05:
        return f"Lo2000 KS p={p:.3f} 显著"
    return f"Lo2000 KS p={p:.3f} 不显著(Nasdaq 显著)"


# ── 5 极值不等式（Lo 逐字） ────────────────────────────────────

def _within(vals: Sequence[float], tol: float) -> bool:
    """各点都在其均值的 tol 比例内。"""
    m = sum(vals) / len(vals)
    return m > 0 and all(abs(v - m) / m < tol for v in vals)


def _match5(e: List[Dict]) -> Optional[str]:
    """对 5 个连续交替极值判形态。e[0].kind 决定是顶族还是底族。"""
    p = [x["price"] for x in e]
    top = e[0]["kind"] == "max"
    e1, e2, e3, e4, e5 = p

    if top:
        # HS: E1 极大; E3 > E1, E3 > E5; E1,E5 在均值 1.5% 内; E2,E4 在均值 1.5% 内
        if e3 > e1 and e3 > e5 and _within([e1, e5], TOL_PCT) and _within([e2, e4], TOL_PCT):
            return "HS"
        if _within([e1, e3, e5], TOL_RECT_PCT) and _within([e2, e4], TOL_RECT_PCT) \
                and min(e1, e3, e5) > max(e2, e4):
            return "RTOP"
        if e1 < e3 < e5 and e2 > e4:
            return "BTOP"
        if e1 > e3 > e5 and e2 < e4:
            return "TTOP"
        # 族外楔形 = 两条边**同向**+ **收敛**（不收敛就是通道，不给标签）
        converging = (e5 - e4) < (e1 - e2)
        if converging and e1 > e3 > e5 and e2 > e4:
            return "FWEDGE"                     # 高低点齐跌且收敛
        if converging and e1 < e3 < e5 and e2 < e4:
            return "RWEDGE"                     # 高低点齐涨且收敛
    else:
        if e3 < e1 and e3 < e5 and _within([e1, e5], TOL_PCT) and _within([e2, e4], TOL_PCT):
            return "IHS"
        if _within([e1, e3, e5], TOL_RECT_PCT) and _within([e2, e4], TOL_RECT_PCT) \
                and min(e2, e4) > max(e1, e3, e5):
            return "RBOT"
        if e1 > e3 > e5 and e2 < e4:
            return "BBOT"
        if e1 < e3 < e5 and e2 > e4:
            return "TBOT"
        converging = (e4 - e5) < (e2 - e1)
        if converging and e1 > e3 > e5 and e2 > e4:
            return "FWEDGE"
        if converging and e1 < e3 < e5 and e2 < e4:
            return "RWEDGE"
    return None


def _double(ext: List[Dict], kind: str) -> Optional[Dict]:
    """双顶/双底：Lo 的定义不走 5 极值，走「窗口内最高的两个极大」。

    条件 = 两峰在其均值的 1.5% 内，且**间隔 > 22 个交易日**。
    """
    pts = [x for x in ext if x["kind"] == kind]
    if len(pts) < 2:
        return None
    rev = (kind == "max")
    top2 = sorted(pts, key=lambda x: -x["price"] if rev else x["price"])[:2]
    a, b = sorted(top2, key=lambda x: x["idx"])
    if b["idx"] - a["idx"] <= DTOP_MIN_SEP:
        return None
    if not _within([a["price"], b["price"]], TOL_PCT):
        return None
    return {"a": a, "b": b}


# ══════════════════════════════════════════════════════════════════
# 4. 对外接口：detect()
# ══════════════════════════════════════════════════════════════════

def detect(bars: List[dict], window: int = LO_WINDOW, lag: int = LO_LAG,
           h: Optional[float] = None) -> List[Dict]:
    """判读当前窗口里**正在成形/已成形**的形态假设。

    返回 [{name, cn, direction, evidence, ks_p, state, key_level, confirm_if,
           invalidate_if, points, completed_idx, bars_since}]

    `state`：
      `forming`   —— 最后一个极值还在窗口末端 lag 根之内，形态**尚未确认**
                     （Lo 的检测滞后 d=3：形态完成后第 d 日之后那一天才算数）
      `confirmed` —— 已过 lag，且价格**还没有**碰到证伪条件
      `invalidated` —— 已触发证伪条件（**照报不删**—— 失败的形态本身是信息，
                     它是 E2⇄E3 镜像里的另一半）

    ⚠️ 只吃**已完成日线**。盘中未完成 bar 当收盘是本仓栽过的坑
    （`heartbeat.md` §盘中运行，NBIS 7/28 案）——调用方负责别把当日 bar 喂进来。
    """
    if len(bars) < 12:
        return []
    win = bars[-max(window, 12):]
    ext = extrema(win, h)
    closes = [float(b.get("close", 0)) for b in win]
    last_close = closes[-1]
    n = len(win)
    out: List[Dict] = []

    def _emit(name: str, pts: List[Dict], key: float,
              confirm: str, invalidate: str, invalid_now: bool,
              trigger_test: Optional[Callable[[float], bool]] = None,
              confirm_level: Optional[float] = None,
              invalidate_level: Optional[float] = None) -> None:
        spec = PATTERN_SPECS[name]
        done = pts[-1]["idx"]
        since = n - 1 - done
        stale_gap = max(lag + 2, n // 3)
        # 「当前所处的形态」—— 最后一个极值必须落在窗口末段 1/3 内。
        # 不加这条，120 根窗口会天天报三个月前那个已经走完的形状，
        # 而核心目的问的是**现在**处在什么结构里。
        if since > stale_gap:
            return
        triggered = bool(trigger_test and trigger_test(last_close))
        # 破线**多久了**—— 从最后一根往回走，走到确认条件不再成立为止
        # （中途回到线内 = 那一段结束了，现在这段才是「当前所处」）。
        # 下界卡在 done：形态长出来之前的越线不算这个形态触发的。
        since_trigger: Optional[int] = None
        if triggered:
            i = n - 1
            while i > done and trigger_test(closes[i - 1]):
                i -= 1
            since_trigger = n - 1 - i
        # 四态语义（**这三个词不能混**，混了就会把「形状在那儿」读成「信号来了」）:
        #   forming     最后一个极值还在 lag 内 —— 形状本身都还没定
        #   shaped      形状已成立，但**确认条件没满足**（颈线还没破）
        #   triggered   确认条件已满足（价格真的越过了那条线）
        #   invalidated 已触发证伪条件（照报不删 —— 失败的形态是 E2⇄E3 镜像的另一半）
        if invalid_now:
            state = "invalidated"
        elif since < lag:
            state = "forming"
        elif triggered:
            state = "triggered"
        else:
            state = "shaped"
        # 🚫 不要在这里加「破线太久 → 丢弃」的过滤（试过，是空转）：
        # 破线**必然**发生在形态完成之后，`since_trigger ≤ since` 由构造成立，
        # 而 `since` 已经被上面那把尺卡过了 —— 同一把尺量破线永远不会再拒掉任何东西。
        # 想拒就得**新拍一个更紧的阈值**，而全书实测破线年龄最大 14 根（AMZN），
        # 没有一个够得着 40 —— 没有样本可校准的阈值 = 无出处自由度（DoF 棘轮：只减不增）。
        # 真正让 MSFT 看起来「陈年兑现」的是**颈线画错 2.6N**，不是时效。
        out.append({
            "name": name, "cn": spec["cn"], "direction": spec["dir"],
            "family": spec["family"], "ks_p": spec["ks_p"],
            "evidence": evidence_grade(name), "note": spec["note"],
            "state": state, "bars_since": since, "window": n,
            "bars_since_trigger": since_trigger,
            # 容器型形态（三角/楔形/矩形）的名字不带方向，方向由**破哪边**决定 ——
            # 不落这个字段，渲染层就只能把已解决的形态继续按「等突破选边」印。
            "resolved_side": (None if not triggered or confirm_level is None
                              else ("up" if last_close > confirm_level else "down")),
            "key_level": round(key, 2),
            "confirm_if": confirm, "invalidate_if": invalidate,
            # 数值版的两条线：让渲染层能算「距离还有多远」——
            # 「未触发」这个词本身不自明（用户  当场问了），
            # 但「确认线在 432.50，距现价 +31.6% / 3.1N」是自明的。
            "confirm_level": round(confirm_level, 2) if confirm_level else None,
            "invalidate_level": round(invalidate_level, 2) if invalidate_level else None,
            "last_close": round(last_close, 2),
            "points": [{"date": p["date"], "price": round(p["price"], 2),
                        "kind": p["kind"]} for p in pts],
        })

    # ── 5 极值族：取最近 5 个交替极值 ──
    if len(ext) >= 5:
        e5 = ext[-5:]
        name = _match5(e5)
        if name:
            prices = [x["price"] for x in e5]
            top = e5[0]["kind"] == "max"
            if name in ("HS", "IHS"):
                neck = min(prices[1], prices[3]) if top else max(prices[1], prices[3])
                head = prices[2]
                _emit(name, e5, neck,
                      f"收盘{'跌破' if top else '站上'}颈线 {neck:.2f}（量能配合）",
                      f"收盘{'站上头部' if top else '跌破头部'} {head:.2f}",
                      (last_close > head) if top else (last_close < head),
                      (lambda c: c < neck) if top else (lambda c: c > neck),
                      neck, head)
            elif name in ("RTOP", "RBOT"):
                hi, lo = max(prices), min(prices)
                _emit(name, e5, hi if top else lo,
                      f"收盘越出区间 [{lo:.2f}, {hi:.2f}] 任一侧 ×N 日 = 方向选定",
                      "区间本身失效（越出后不回）", False,
                      lambda c: not (lo <= c <= hi), hi, lo)
            else:
                # 收敛/扩散族（三角 · 楔形）：突破位取**最近**的那对边，不取窗口极值。
                # 取全局 max/min 会得到荒谬的确认线 —— INTC  实测：
                # 下降楔形的"向上解决"被算成 142.35（六周前的高点），而现价 97.71，
                # 那条线在这个 regime 里根本够不着 = 等于没有确认条件。
                hi = prices[4] if top else prices[3]
                lo = prices[3] if top else prices[4]
                _emit(name, e5, prices[-1],
                      f"收盘突破最近高点 {hi:.2f} = 向上解决",
                      f"收盘跌破最近低点 {lo:.2f} = 向下解决", False,
                      lambda c: not (lo <= c <= hi), hi, lo)

    # ── 双顶 / 双底（Lo 的独立定义） ──
    for kind, nm in (("max", "DTOP"), ("min", "DBOT")):
        d = _double(ext, kind)
        if not d:
            continue
        a, b = d["a"], d["b"]
        mid = [x for x in ext if a["idx"] < x["idx"] < b["idx"]
               and x["kind"] != kind]
        if not mid:
            continue
        neck = (min(x["price"] for x in mid) if kind == "max"
                else max(x["price"] for x in mid))
        peak = (a["price"] + b["price"]) / 2
        if kind == "max":
            _emit(nm, [a, b], neck,
                  f"收盘跌破颈线 {neck:.2f} = 双顶成立",
                  f"收盘站上双峰均值 {peak:.2f} = 不是双顶",
                  last_close > peak, lambda c: c < neck, neck, peak)
        else:
            _emit(nm, [a, b], neck,
                  f"收盘站上颈线 {neck:.2f} = 双底成立",
                  f"收盘跌破双谷均值 {peak:.2f} = 不是双底",
                  last_close < peak, lambda c: c > neck, neck, peak)

    return out


SWING_WINDOW = 120       # 摆动尺度：多数「双顶 / 头肩」在日线上跨几个月，35 根装不下

# 四态的显示名与排序优先级 —— **单一真源**，渲染层不许自己再写一份映射
# （两处映射迟早分叉，本仓「同概念双判别式」的老病）。
STATE_CN = {"triggered": "● 已破确认线", "shaped": "○ 形状已成立·未破确认线",
            "forming": "◐ 成形中(极值未定)", "invalidated": "✗ 已证伪"}
STATE_LEGEND = ("形态四态：◐ 成形中(最后一个极值还可能移动) ｜ "
                "○ 形状已成立但**价格还没破确认线**(轮廓在，市场还没投票) ｜ "
                "● 已破确认线 ｜ ✗ 已证伪(照报不删——失败的形态也是信息)")
STATE_ORDER = {"triggered": 0, "shaped": 1, "forming": 2, "invalidated": 3}


def detect_multi(bars: List[dict], windows: Sequence[int] = (LO_WINDOW, SWING_WINDOW),
                 lag: int = LO_LAG) -> List[Dict]:
    """在多个**时间尺度**上跑同一套形态语法 → 合并去重（同名同方向只留大窗口那个）。

    为什么要有第二个窗口：Lo 的 l=35 是他检验用的口径，而 用户 关心的「这在形成
    双顶 / 头肩」在日线上通常跨**几个月**—— DTOP 自己就要求两峰间隔 >22 日，
    35 根窗口里几乎装不下一个像样的双顶。

    🚨 **诚实标注**：只有 `window == LO_WINDOW` 那一档是 Lo 检验过的口径。
    其余窗口是**尺度外推**—— 形状的定义没变，但那个 p 值**不自动跟着走**。
    渲染层必须把窗口打出来（`tests` 钉它）。跨尺度组合本身有顶刊证据
    （Han-Zhou-Zhu 2016，夏普翻倍），但那说的是**趋势**跨尺度，不是形态 p 值可外推。
    """
    seen: Dict[tuple, Dict] = {}
    for w in sorted(set(windows)):
        for hit in detect(bars, window=w, lag=lag):
            hit["lo_calibrated"] = (w == LO_WINDOW)
            seen[(hit["name"], hit["state"])] = hit      # 大窗口后写 = 覆盖小窗口
    return sorted(seen.values(),
                  key=lambda h: (STATE_ORDER.get(h["state"], 9), -h["window"]))


def summarize(hits: List[Dict]) -> str:
    """一行白话总结，给渲染层用。空 = 明说「窗口内无可识别形态」——
    **不许因为没形态就沉默**：沉默会被读成「没看」，而「没有形态」是最常见的真相。"""
    if not hits:
        return "各尺度窗口内均无形态成形（这是常态，不是遗漏）"
    live = [h for h in hits if h["state"] != "invalidated"]
    if not live:
        return "窗口内形态**全部已被证伪**—— 失败本身是信息（假突破 = 陷阱的另一半）"
    parts = [f"{h['cn']}({STATE_CN[h['state']].split(' ')[-1]}/{h['window']}日窗)"
             for h in live]
    return " + ".join(parts)
