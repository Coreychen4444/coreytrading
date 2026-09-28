"""Single source for regime caps, gate budgets, and whole-unit sizing.

This module contains pure functions only. Authorization comes exclusively from
``current_regime.cap_pct_nav``: zero means VETO and a positive value enters the
shared gate-budget calculation. Consumers may render the result but must not
copy the formulas.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from .num import coerce_float
from .occ import parse_occ
from .timeutil import et_today

# ── 1b. 冲击预算框架 (定版) ──────────────────────────────
#
# w = min(NOTIONAL_CAP, 剩余预算/(SHOCK_X × ATR/价), GROSS_CAP − 已用 gross, 分类/regime cap)
#
# These pinned production constants require a locked private study and reviewed
# production change before modification:
#   NOTIONAL_CAP 0.40  用户  定 (风险偏好较高; 业界对照: 1940 法案 5% /
#                      UCITS 10% / 集中型 8-15% / 极端集中 15-25%)
#   SHOCK_BUDGET 0.15  用户 定; 组合单日冲击损失上限
#   SHOCK_X      1.88  **池级常数**。§13 实测: 换成 per-ticker 后逐笔仓位差 **0.0%**
#                      (连 p90 都是 0) —— 因为 NOTIONAL_CAP 在 99.9% 的 ticker-day 上
#                      先咬, X 根本进不了最终结果。所以 per-ticker 表已删。
#                      ⚠️ **这条成立于 NOTIONAL_CAP = 40% 之下**。若日后把它压到
#                      15% 量级, 预算会重新 binding, per-ticker 也会重新有意义 ——
#                      改 NOTIONAL_CAP 的人必须知道自己同时改变了这件事。
#   GROSS_CAP    1.10  用户  定 (不设时实测堆到 160%; 110% 花 0.17pp)
#
# ⚠️ 两条仍然成立的否定结论 (§15a, 不要因为定版了就忘掉):
#   H1 控制力不过 —— 校准假设满仓 5 票而实际入场稀疏, 打穿率只有目标的 0.02-0.04x。
#      **这不是"更安全", 是校准与现实脱节**; 真要用 0.15 得先给"典型占用率"一个经验值。
#   H2 选择性不过 —— NOTIONAL_CAP 一旦 binding, 预算在单笔层面空转。所以这套东西的
#      实质是「单票 40% + 组合波动率目标 + gross 110%」, **不是精细的 per-position 风险模型**。
NOTIONAL_CAP = 0.40
SHOCK_BUDGET = 0.15
SHOCK_X = 1.88
GROSS_CAP = 1.10
# 单笔 at-risk 天花板 (risk_unit (b), N1 立项  用户 翻案定初值):
# **单笔止损打掉后的损失 ≤ AT_RISK_CAP_FRAC × NAV**。名义 cap 管"投入多少钱",
# 这条管"冒多少险" —— 同一名义下 at-risk 随 stop 距离差 5 倍 (risk_unit_design §0)。
# v1 口径 (design §b1 锁定): 名义 at-risk (现价/首档 − stop), **不乘**1.48 实现损失
# 系数 (跳空+滑点偏置记录在案, 校准后再议)。
# 期权 long: 曾为保费全额 (loss/unit=1.0 教义) → **用户 裁决放宽**:
# S1 premium 硬 stop (-25%, exit_guard.PARAMS["opt_s1_stop"]) 接线后 loss/unit =
# OPTION_ATRISK_LOSS_FRAC = |S1| = 0.25 (两数相等由 pins 测试钉)。放宽前提 = S1 会
# 被执行 —— 退出执行史是已知最弱环, 首笔 S1 触发未当日执行 = 前提证伪, 复核本条
# (risk_unit_design §b1)。
# 咬点分布 (设计时算过, 别装作它到处都管): 入场侧几乎恒被 ② 轮预算先咬 = 后备
# (R1 升 2.5% 后余量收窄: R1 在 2N stop 处 ⑤/② 恰 2×, stop >4N
# 才轮到 ⑤); 真咬点在**持仓侧**(棘轮抬升后 mark−stop 变宽 /
# 期权保费) — 检查在 portfolio_brief.at_risk_breach_signal。
AT_RISK_CAP_FRAC = 0.05
# S1 −25 → −20 (用户 裁「收到 20%」, gate1 期权入场同日开放) ⇒ 本系数
# 随 pin 跟到 0.20。⚠️ 已知低估: 跳空日止损线 ≠ 损失 (GOOGL 390C 8/05 收盘 −24.7% /
# NVDA 220C 8/28 单日 −20%), 0.20 是「S1 被执行且无跳空」的名义 loss/unit, 实证会更大。
OPTION_ATRISK_LOSS_FRAC = 0.20

# 熔断：受管书**单日损失**
# ≥ SHOCK_BREAKER_MULT × SHOCK_BUDGET × NAV (= 22.5% NAV,「比 30 年样本里最坏的
# 那天还糟」) → 裁决动作 = **直接清仓, 盘中立即执行**。检测在 portfolio_brief
# (shock_breaker_signal, 火警绕过 24h 冷却)。与回测 BREAKER_MULT 的相等由
# tests/test_constant_pins.py 钉。⚠️ H4 反事实 (round-1 杠杆污染, round-2 未重跑)
# 曾读出「触发后不清仓 T+20 中位 +22%/+2%」—— 清仓裁决带着这条已知反证成立,
# +12 月复测重验 H4。
SHOCK_BREAKER_MULT = 1.5

# ── Gate 解锁的风险预算 (用户  二次定版, 逐字) ──────────────
#
#   「就是没有所谓的 r1 r2 r3 概念了 —— 就是 gate1 max 2.5% gate2 max 4%
#    gate3 max 5% 就这样。gate1 里还可以分开加: 比如 gate1 的 cap 给了 8k,
#    第一笔加了 4000, 那我还能在当前 gate 的 zone 再继续加, 但第二次加的时候
#    余额会显示只剩 4000。」
#
# 所以这张表是**累计上限**, 不是每档增量: 一张卡过了几个 gate, 就对应一个
# max; 在同一个 gate 里可以分多笔加, 余额 = max − 当前敞口 (见 `entry_cap`)。
# 「轮 (round)」这个词随之退役 —— 它曾经暗示"每轮一份新预算、不用完就作废",
# 而这里从头到尾只有**一个**T 被分批解锁。
#
# 出处仍是 T × 50/30/20 (risk_allocation  三点锁定), 只是换成累加式
# 表达: T = 5% NAV 顶格 = AT_RISK_CAP_FRAC **同一个数**(语义闭合不留双账,
# 由 `test_gate_risk_caps_locked_values` 钉住这条来历)。
# ⚠️ 名义口径折算随行播报: 名义 −1.0R 实证 ≈ −1.40R (71,093 止损样本, 2N 入场锚定族,
# 见 design §4) —— T 名义 5% 打满 ≈ 实证 7.0% NAV, 用户 知情选择顶格。
GATE_RISK_CAPS = (0.025, 0.040, 0.050)

# ── 期权 gate 资格闸 ──
# 当前配置 = 第一个**给 cap 的** gate 起即可用 long call, 但资格与正股**同一套**:
#   ① 见底条件 = 系统里第一个给 cap 的 gate (收复线/止跌线 ×N 日确认 + 相对量腿,
#      `structure_repair_veto` 放行) ⇒ OPTION_MIN_GATE = 1。「可以用期权」不等于
#      「可以随意开仓」—— 没有那道 gate 就没有资格, 与正股一致。
#   ② IV 硬门槛 (long_call_iv_gate): 见底点往往同时是 IV 峰值, 长 DTE call 会在
#      正股上涨时被 vega crush 吃掉, 所以第一个 gate 只把 call 给 IV 友好的标的,
#      其余走正股。
#   ③ 保费上限的分母 = |S1| (OPTION_ATRISK_LOSS_FRAC, 与 exit_guard 的保费硬 stop
#      同一个数, 由 pins 测试钉相等)。
# 历史配置对照 (先后两版都实跑过): 「首档用期权、之后一律正股」与「第一个 gate 严禁
# 期权、gate2 起可用」。改这个常量前先想清楚翻案依据是**工具属性**还是样本量 ——
# 样本量在本仓从来不够裁决。保费上限恒按**本档新增** risk ÷ |S1| (不跟累计结转):
# gate1 = 2.5%/0.20 = 12.5% NAV, gate2 = 7.5%, gate3 = 5%。
# ⚠️ 小账户上这条常常算出 0 张 (单张 Δ0.7 长 DTE 保费 > 上限) —— 0 张的菜单是
# 「正股或 veto」, 不是降 delta 去凑 cap。
OPTION_MIN_GATE = 1


def gate_risk_cap_frac(gate_no: int) -> float:
    """过了 `gate_no` 个 gate 时的**风险预算上限**占 NAV（累计, 不是增量）。

        gate1 2.5%  →  gate2 4.0%  →  gate3 5.0% (= T = AT_RISK_CAP_FRAC)

    超过 3 个 gate **停在 T 不归零**—— 再过一个 gate 不会让已经解锁的额度蒸发;
    真正的耗尽由 `entry_cap` 减当前敞口给出 (打满 = 0)。gate_no < 1 = 0.0。
    """
    if gate_no < 1:
        return 0.0
    return GATE_RISK_CAPS[min(gate_no, len(GATE_RISK_CAPS)) - 1]


def gate_step_frac(gate_no: int) -> float:
    """本档**新增**的风险预算（= 累计差分）。**唯一用途 = 期权保费上限**——
    正股侧一律用 `gate_risk_cap_frac` 的累计值, 别拿这个去算额度。"""
    if not 1 <= gate_no <= len(GATE_RISK_CAPS):
        return 0.0
    prev = GATE_RISK_CAPS[gate_no - 2] if gate_no >= 2 else 0.0
    return GATE_RISK_CAPS[gate_no - 1] - prev


def option_premium_cap_frac(gate_no: int) -> float:
    """期权保费上限占 NAV = 本档**新增** risk ÷ |S1| (OPTION_ATRISK_LOSS_FRAC)。
    gate1 12.5% / gate2 7.5% / gate3 5% NAV;
    gate_no < OPTION_MIN_GATE (=1) 或超 3 个 gate = 0.0。"""
    if gate_no < OPTION_MIN_GATE:
        return 0.0
    return gate_step_frac(gate_no) / OPTION_ATRISK_LOSS_FRAC


def binding_stop(price: Optional[float], candidates: Sequence[Optional[float]],
                 atr: Optional[float] = None, n_mult: float = 2.0) -> tuple:
    """多个止损候选 → **实际会先触发的那条**, → (stop, basis)。

    用户 : 「stop 是多者取 max」。多头上, 卡片 stop / 棘轮地板 /
    趋势死亡线里**最高**的那条先被打到 —— 那才是这笔真正的风险距离。与
    `price_reminder_sync` 的下方线 (max) 和 `at_risk_breach_signal` 的 binding
    stop 同一口径。

    ⚠️ **2N 只是兜底不是候选**: `n_mult × ATR20` 是 sizing 约定, 不是你会挂的
    单子。把它放进 max, 会在"卡片 stop 比 2N 远"时用一条不存在的线去量风险 ——
    风险被低估、size 被放大 (AMZN 实测: 卡 stop 距 9.2%, 2N 距 6.5%, 差 42%)。
    所以只在**一条真 stop 都没有**时才回落, 且 basis 标出来。
    """
    live = [float(s) for s in candidates
            if isinstance(s, (int, float)) and price and 0 < float(s) < float(price)]
    if live:
        return max(live), "card/lock/trail max"
    if atr and atr > 0 and price:
        return round(float(price) - n_mult * float(atr), 2), f"{n_mult:g}N 兜底"
    return None, "无 stop"


def line_price(z1, fallback) -> tuple:
    """Return the z1 upper edge used by the first ladder order, or spot fallback."""
    if isinstance(z1, (list, tuple)) and len(z1) == 2:
        upper = coerce_float(z1[1])
        if upper:
            return upper, "z1"
    return fallback, "现价"


def binding_label(cap: dict) -> str:
    """Render a shared human-readable label for the binding capacity arm."""
    binding = cap.get("binding")
    if binding == "n_unit":
        return f"gate{cap.get('gate_no')} 预算上限"
    return {
        "at_risk": "单笔 at-risk 5%NAV",
        "shock_budget": "冲击预算余额",
        "gross": "gross 余额",
        "power": "现金购买力",
        "regime": "regime cap",
        "below_cost": "均价之下不摊",
    }.get(binding, str(binding))


def below_cost(price: Optional[float], spot: Optional[float],
               avg_cost: Optional[float]) -> bool:
    """成本闸（成本不向下摊：加仓只许 pyramiding up，averaging down 退役）: 持仓之后这一笔加仓
    会不会把成本往下摊。

    成交价 = min(线价, 现价) —— 现价已在线下时, 线上的限价单按现价成交。`avg_cost` None
    (空仓 / 成本未知, 见 `holdings.cost_basis`) → False: 空仓没有成本可摊, 建仓阶梯不受限。"""
    if not avg_cost:
        return False
    px = [float(p) for p in (price, spot) if p]
    return bool(px) and min(px) < float(avg_cost)


def risk_notional_cap(nav: Optional[float], risk_frac: Optional[float],
                      price: Optional[float], stop: Optional[float]) -> Optional[float]:
    """风险预算 → **名义**上限。②轮预算与⑤at-risk 的**同一个公式**, 只差 risk 分数:

        cap = risk_frac × NAV / ((价 − stop) / 价)

    用户 : 「算 round risk fracs 和 at risk cap frac 时不应该用固定的
    2N, 应该用 stop」。此前 ② 的分母写死 2×ATR20 —— 一个**约定**, 不是那笔交易
    真实的风险距离; ⑤ 用真 stop。同一笔仓位被两把不同的尺量, 而两者本来就是同
    一个量纲 (风险预算 ÷ 每股风险)。

    stop 无效 (缺失 / ≥ 价 / ≤ 0) → None = 本条不参与取小, **不编数**。
    ⚠️ `risk_frac == 0` 与 `risk_frac is None` **不是一回事**: 前者是"预算用尽"
    (第 4 轮 = T 花光) → 必须返回 **0.0**让这条臂咬死; 后者才是"不知道"。
    把 0 当成 None 会让 T 用尽的 campaign 拿回整份分类 cap = fail-open。
    """
    if risk_frac is not None and float(risk_frac) == 0.0:
        return 0.0
    if not nav or risk_frac is None or not price or price <= 0:
        return None
    if stop is None or stop <= 0 or stop >= price:
        return None
    return float(risk_frac) * nav * price / (price - stop)


def gate_risk_notional_cap(nav: Optional[float], gate_no: int,
                           price: Optional[float], stop: Optional[float]) -> Optional[float]:
    """gate 解锁的风险预算 → 名义上限 (= ② n_unit 的**毛额**, 未减敞口)。
    entry_guard 与 today_group 共读这份 —— **唯一换算点**。

     两次更名, 每次都是语义真的变了 (同名换语义 = 本仓最恨的分叉):
    `round_risk_notional_cap`(本轮) → `unlocked_risk_notional_cap`(累计解锁)
    → 本名 (accumulate 的主语从"轮"换成"gate")。
    """
    return risk_notional_cap(nav, gate_risk_cap_frac(gate_no), price, stop)


# ── 过了几个 gate (用户  二次定版) ───────────────────────
#
# 判据 = **卡上的 gate 转移史**, 不是 lots。「gate1 max 2.5% / gate2 max 4% /
# gate3 max 5%」问的是"这张卡爬过几道线", 那件事写在 `agent.regime_history`
# 里 (`apply_transition` 每次转移 append 一条, 带 date + direction), 不写在
# 成交记录里。
#
# 退役的两个 lots 近似 (当日先后废):
#   · `round_no_from_lots_since` —— 拿"有没有比 current_regime.since 更早的
#     lot"当轮号, 只有两个桶。两个洞: ①走到第 3 个 gate 还持有旧仓时永远报 2
#     (数不出第三段); ②上一档**一股没买**就过了下一个 gate 时 lots 为空 → 报 1,
#     解锁额度整档丢失 —— 而"没用完的余额跟着过来"正是结转要给的东西。
#     两个洞都由"数 gate 而不是数 lot"一次性消掉。
#     ⚠️ 它还会**错报**: NBIS 的 8/03 那笔是在 gate 误读下买的 (零授权), 旧判据
#     看见"有比 since 早的 lot"就判 gate2 = 4%, 而这张卡其实只爬过 231 一道线。
#   · `campaign_round_no` (1 + 不同入场日数) —— 盖章/展示用的更粗近似, 同废。
#
# 参与流的**起点**= 最近一次中断: 破位 (down 转移) 或平仓 (exit_info.closed_at)。
# 「平仓 = campaign 结束, 下一笔是新 campaign 的第一档」是 用户  早前的裁决;
# down 转移同理 —— 结构降级立刻 cap 0, 那条参与流就断了, 之后重新收复算重新起步。

def _participation_anchor(card: Optional[dict]) -> str:
    """参与流起点 (YYYY-MM-DD, "" = 无锚): max(最后一次 down 转移, 平仓日)。

    ⚠️ **平仓日在两个桶里记在不同字段**, 两个都要读:
      · positions 的 closed 卡 → `exit_info.closed_at` (close_reconcile 落)
      · watchlist 回来的候选卡 → `readd_from_position.closed_at` (watchlist_sync 落,
        注里明写「lots/exit_info 留在 positions.json 那张 closed 卡上」)
    而 **grant 走的恰恰是 watchlist 那张**(卡源判别式: 非 live → watchlist)。
    只读 `exit_info` 的话, AAOI 这种"平仓当晚又转移一次"的卡会被数成 gate2 ——
    同一个事件两处记录、判别式只认一处, 正是本仓反复出的病 (GOOGL 卡裸奔案)。
    """
    card = card or {}
    agent = card.get("agent") or {}
    stamps = [str(h.get("date") or "")[:10] for h in (agent.get("regime_history") or [])
              if str(h.get("direction")) == "down"]
    for blk in (card.get("exit_info"), card.get("readd_from_position")):
        stamps.append(str((blk or {}).get("closed_at") or "")[:10])
    return max([s for s in stamps if len(s) == 10] or [""])


def _granted_cap_positive(entry: dict) -> bool:
    """这条转移是不是**给了 cap**。

     起 `apply_transition` 直接盖 `cap_pct_nav` (取自它刚写完的
    `current_regime`, 不可能与实际落卡的值漂移)。⚠️ **字段缺失 ≠ 值为 None**:
    缺失 = 老条目 (盖章上线前, 已查实全为授权转移 → 计入); 显式 None/0 =
    这道 gate 落在一个不授权的 regime 上 (卡的 `next_regime` 没写 cap) → 不计。
    ⚠️ 不能靠 `last_regime` 回填老条目: 上行转移时它写的是 `demoted_at_*` =
    "掉下去会落在哪", cap 恒 0, 不是"上一次转移产生的 regime"。
    """
    if "cap_pct_nav" not in entry:
        return True
    return (coerce_float(entry.get("cap_pct_nav")) or 0.0) > CAP_TOL


def gate_count(card: Optional[dict]) -> int:
    """这张卡在**当前参与流**里爬过几个**给了 cap 的**gate → 1..len(GATE_RISK_CAPS)。

    用户 :「不是任何一个 gate 都算次数, 只有给 cap 的 gate 才开始算。」
    这不是细节 —— 上行转移落在哪个 regime 由卡上的 `next_regime` 决定, 而
    `apply_transition` 是照抄它的 `cap_pct_nav`, **可以是 0/None**(修复路上的
    中间站)。爬过一道不授权的线不该吃掉一档预算。

    = 参与流起点之后、`direction == "up"` 且**授予 cap > 0**的转移条数, **下限 1**:
    一张 cap > 0 却没有任何转移史的卡 (`derive_card_regime` 的「常规参与」,
    结构本来就干净) 拿的是第一档 —— 它有参与资格, 只是没爬过线。

    ⚠️ 三个判定细节:
      · **老条目 (无 `cap_pct_nav`) 计入**。这不是猜: live 全部 10 条老条目已逐条
        查实为授权转移 (git /06 快照: LITE 8/04 → `B_repair_probe_716`
        cap 0.02, AAOI 8/05 → `B_repair_probe_120.5` cap 0.02, 皆 > 0)。老条目是
        **封闭集合**—— 新条目一律带戳, 所以这条兜底不会再扩大。
      · 缺 `direction` 的条目不计入 —— 数不出来就不给, 不猜。
      · 起点比较用**日期严格大于**。平仓与当天晚些时候的转移会落在同一个日期上
        (AAOI 8/12: 13:29 ET 平仓 → 当晚 postclose 转移 R_突破_133), 这时那条
        转移不计入, 由下限 1 兜住 = 第一档 —— 正好也是"平仓后重来"该给的答案。
    """
    anchor = _participation_anchor(card)
    hist = ((card or {}).get("agent") or {}).get("regime_history") or []
    n = sum(1 for h in hist
            if str(h.get("direction")) == "up"
            and str(h.get("date") or "")[:10] > anchor
            and _granted_cap_positive(h))
    return max(1, min(n, len(GATE_RISK_CAPS)))


def granting_gate_level(card: Optional[dict]) -> Optional[float]:
    """把这张卡送进**当前授权**的那道 gate 的价位 → float | None。

    = 参与流起点之后最近一条 `direction == "up"` 且给了 cap 的转移的 `level`
    (与 `gate_count` 数的是同一批条目, 取最后一条)。None = 常规参与卡 (没爬过线,
    授权由建卡派生) / 参与流已被 down 转移或平仓打断 / 条目无 level。

    消费者 = 期权退出栈 S5g (用户, META 553 案:「站上 553 的时候进场就
    非常对了, 然后如果跌破 553 就果断离场 —— 虽然是以 gate 作为退出条件, 但这是含
    杠杆的期权, 所以可以接受稍高的止损率」): 期权的正股退出线 = 这条 gate 本身,
    不是正股用的 bfloor_buf (gate − 0.5N, round-2b 唯一合格变体 —— 正股照旧,
    期权知情取紧)。
    """
    anchor = _participation_anchor(card)
    hist = ((card or {}).get("agent") or {}).get("regime_history") or []
    for h in reversed(hist):
        if str(h.get("direction")) == "up" and str(h.get("date") or "")[:10] > anchor \
                and _granted_cap_positive(h):
            return coerce_float(h.get("level"))
    return None


# ── 期权 IV 硬门槛 (用户 裁「硬门槛」) ──────────────────
#
# long call 同时买方向 (delta) 和波动 (vega)。见底点 (gate1) 的 delta 腿最便宜,
# vega 腿却最贵 —— 三对可对上 IV 记录的转移 gate1→gate2 IV 全部回落 (AAOI 149→112 /
# LITE 114→91 / MU 93→67), Δ0.7 270DTE call 在正股 +4.4~8.8% 时仍 −6~−21%。
# 所以 gate1 用 call 只限 IV 友好的票: percentile < 40 (序列 ≥60 天才可信,
# iv_logger.percentile 的 reliable), 序列不足时用绝对 ATM IV < 50% 兜底
# (mega-cap 常态 25-45% / 半导体 & 光模块 70-150%, 07-21 LEAPS 质量倾斜同一条线)。
# ⚠️ 已知形状: percentile 口径下一只 IV 88% 的票只要处于自身低分位就会放行 ——
# 绝对上限没有单独裁, 要加是另一条裁决。无 IV 数据 = 不放行 (与 today_group
# 「无 K 线→不 grant」同一 fail-closed 哲学)。
LONG_CALL_IV_PCT_MAX = 40.0    # IV percentile 上限 (含 = 不放行)
LONG_CALL_IV_ABS_MAX = 50.0    # 绝对 ATM IV % 上限, 仅 percentile 不可信时兜底


def long_call_iv_gate(pct: Optional[float], reliable: bool,
                      abs_iv: Optional[float],
                      hv_pct: Optional[float] = None) -> dict:
    """单腿 long call 的 IV 硬门槛 → {"allowed", "basis", "reason"}。

    basis ∈ {"pct", "hv_pct", "abs", "none"}:
      pct     percentile 可信 (≥60 样本) 用 IV percentile;
      hv_pct  **IV 序列不可信时用 HV20 的 252 日分位代理** (不同标的 IV
              绝对值本身水平不同, 应看分位而不是绝对值; 阈值同一个 LONG_CALL_IV_PCT_MAX,
              代理的是分位不是水平, 读法 core.book.hv20_percentile_of);
      abs     两个分位都没有才回落绝对 ATM IV (老兜底, 只剩 K 线也不够 252 日的新票);
      none    无数据 → 不放行。reason 是播报原文, basis 必须播出去 (代理 ≠ 真 IVP)。
    """
    p = coerce_float(pct)
    a = coerce_float(abs_iv)
    h = coerce_float(hv_pct)
    if reliable and p is not None:
        ok = p < LONG_CALL_IV_PCT_MAX
        return {"allowed": ok, "basis": "pct",
                "reason": (f"IV percentile {p:g} {'<' if ok else '≥'} {LONG_CALL_IV_PCT_MAX:g}"
                           + (f" (ATM IV {a:g}%)" if a is not None else ""))}
    if h is not None:
        ok = h < LONG_CALL_IV_PCT_MAX
        return {"allowed": ok, "basis": "hv_pct",
                "reason": (f"HV20 分位 {h:g} {'<' if ok else '≥'} {LONG_CALL_IV_PCT_MAX:g}"
                           f" (IV 序列不可信, HV 分位代理 IVP"
                           + (f"; ATM IV {a:g}%" if a is not None else "") + ")")}
    if a is not None:
        ok = a < LONG_CALL_IV_ABS_MAX
        return {"allowed": ok, "basis": "abs",
                "reason": (f"ATM IV {a:g}% {'<' if ok else '≥'} {LONG_CALL_IV_ABS_MAX:g}%"
                           " (序列 <60 天, percentile 不可信 → 绝对值兜底)")}
    return {"allowed": False, "basis": "none",
            "reason": "无 IV 数据 → 不放行 (先跑 iv_logger --tickers <T>)"}


# ── reversal_probe 入场类的保费 cap ──────────
#
# 保费预算按 gate1 取, 止损放在 gate, 由此倒推 cap:
#   风险预算 = gate1 的本档新增 risk (gate_step_frac(1) = 2.5% NAV), 与 gate 类同一个数;
#   止损     = 本类的 gate = 反转波段低点 (core.reversal.signal_at["support"]), underlying 基;
#   cap      = 风险预算 ÷ 正股跌到 gate 时保费的投影亏损比例 (option_plan.loss_frac_at_stop,
#              BS 同 IV / DTE−fill_days 重估), 不再拿固定 |S1| 0.20 当分母 —— gate 类的
#              12.5% 上限本来就只是 2.5% ÷ 0.20 的产物, 本类没有 S1 (止损只有 gate 一条)。
#   其余臂照旧取小 (单票 delta 名义 NOTIONAL_CAP / 冲击预算 / gross / 购买力), 由
#   option_plan.build 的 notional_cap 折算兜住; at-risk 5% ÷ loss_frac 恒 ≥ 本数不另列。
# ⚠️ 已知低估同 OPTION_ATRISK_LOSS_FRAC: 跳空日止损线 ≠ 损失, 投影是 BS 估计不是报价。
REVERSAL_PROBE_RISK_GATE = 1


def reversal_probe_premium_cap(nav: Optional[float],
                               loss_frac_at_stop: Optional[float]) -> Optional[float]:
    """reversal_probe 的保费上限 (美元) = gate1 风险预算 ÷ gate 处投影亏损比例。
    None = 算不出 (NAV 缺 / 投影缺 / 投影 ≤0 = stop 在价上或链数据坏), **不编数**。"""
    lf = coerce_float(loss_frac_at_stop)
    if not nav or nav <= 0 or lf is None or lf <= 0 or lf > 1:
        return None
    return float(nav) * gate_step_frac(REVERSAL_PROBE_RISK_GATE) / lf


def at_risk_nominal_cap(nav: Optional[float], price: Optional[float],
                        stop: Optional[float]) -> Optional[float]:
    """单笔 at-risk 天花板 → 该笔**名义**上限 (risk_unit (b) v1, N1)。

        名义 cap = AT_RISK_CAP_FRAC × NAV / ((price − stop) / price)

    stop 无效 (缺失 / ≥ price / ≤ 0) → None = 本条不参与取小, **不编数**——
    stop 在价上方是"已在破位区"的形状, 归 stop_breach 告警管, 不是 sizing 题。
    期权不走这条 (保费全额即 at-risk, 由持仓侧 breach 检查按保费口径管)。
    """
    return risk_notional_cap(nav, AT_RISK_CAP_FRAC, price, stop)


def shock_occupancy(weight_frac: float, atr: Optional[float],
                    price: Optional[float]) -> Optional[float]:
    """一笔仓位吃掉多少冲击预算 (占 NAV): w × SHOCK_X × ATR20/价。"""
    if not atr or not price or price <= 0 or atr <= 0:
        return None
    return weight_frac * SHOCK_X * (atr / price)


def book_usage(cards: Optional[Dict], nav: Optional[float],
               price_of=None, atr_of=None, delta_of=None) -> Dict:
    """当前书已用掉多少冲击预算与 gross (都占 NAV)。

    只数**受管**持仓 (holdings.is_managed): 结构豁免层不进任何风控口径,
    与 position-tiers.md §base 一致。

    **调用方一律走 `core.book.load_usage()`**, 不要各自接线 —— 组合层占用是
    标量, 同一天里 entry_guard / portfolio_brief / moomoo 备注必须是同一个数。

    **期权双口径 (用户  裁决: 「sizing 层也改 premium 口径」—— 的
    delta 名义是顺便改的不是终案)**:
      · **gross = premium 市值**(qty × 100 × mark): 「押进去多少钱」。永不行权 +
        单腿 long call ⇒ 最大损失恒 = 保费, 尾部有界。⚠️ 代价明示: 方向杠杆从此
        对 gross 不可见 (GOOGL 案 delta 名义 71% NAV vs premium 市值 9.6%) ——
        用户 知情选择, 方向敞口的信息保留在冲击预算口径里。
      · **冲击预算 = delta 近似的冲击日权利金损失**(qty × 100 × delta × 标的价
        × X × ATR/价): 它回答「冲击那天这张期权亏多少钱」, 本来就是 premium
        语义, delta 只是中间量 —— 保留。
    ⚠️ 数据缺口按"宁可高估不可低估"处理并显式报出:
      · `broker` 没有 `atr20` → 预算占用要靠 `atr_of(ticker)` 注入
        (`core.book.atr20_of` 读 kline 缓存 —— 单一实现, 别再写第二份)。
        拿不到 → 该笔**不计入预算占用**, 但计入 `missing_atr`, 由调用方播报。
      · 期权 delta 解析顺序: 注入的 `delta_of` (显式覆盖) → `broker.delta`
        (起由 stock_fetch 落库 OpenD 快照的真 greeks) → **1.0**。
        落到 1.0 = 整张合约的标的名义计冲击占用, 计入 `assumed_full_delta`。
    """
    from .holdings import is_managed          # 局部 import: core 内部不制造环状依赖
    used_budget = used_gross = 0.0
    missing_atr: List[str] = []
    assumed_full_delta: List[str] = []
    if not nav:
        return {"used_budget": 0.0, "used_gross": 0.0, "missing_atr": [],
                "assumed_full_delta": [], "nav_missing": True}
    for tk, body in ((cards or {}).get("tickers") or {}).items():
        if not isinstance(body, dict) or not is_managed(body):
            continue
        br = body.get("broker") or {}
        qty = coerce_float(br.get("qty")) or 0.0
        if not qty:
            continue
        inst = body.get("instrument") or {}
        if inst.get("type") == "option":
            opt = inst.get("option") or {}
            und = opt.get("underlying")
            u_px = price_of(und) if (price_of and und) else None
            mark = coerce_float(br.get("current_price"))
            if not u_px or not mark:
                missing_atr.append(f"{tk}({'标的价' if not u_px else '保费 mark'}缺失)")
                continue
            delta = delta_of(tk) if delta_of else None
            if delta is None:
                delta = coerce_float(br.get("delta"))       # sync 落库的 OpenD 真 greeks
            if delta is None:
                delta = 1.0
                assumed_full_delta.append(tk)
            # gross = premium 市值 (押进去的钱); 冲击预算 = delta 名义 (冲击损失中间量)
            gross_notional = abs(qty) * 100.0 * mark
            shock_notional = abs(qty) * 100.0 * float(delta) * u_px
            atr = atr_of(und) if atr_of else None
            ref_px = u_px
        else:
            px = coerce_float(br.get("current_price"))
            if not px:
                continue
            gross_notional = shock_notional = abs(qty) * px
            atr = atr_of(tk) if atr_of else None
            ref_px = px
        used_gross += gross_notional / nav
        occ = shock_occupancy(shock_notional / nav, atr, ref_px)
        if occ is None:
            missing_atr.append(tk)
        else:
            used_budget += occ
    return {"used_budget": used_budget, "used_gross": used_gross,
            "missing_atr": missing_atr, "assumed_full_delta": assumed_full_delta,
            "nav_missing": False}


def shock_budget_cap(nav: Optional[float], atr: Optional[float],
                     price: Optional[float], used_budget: float = 0.0,
                     used_gross: float = 0.0) -> Dict[str, Optional[float]]:
    """组合层两条约束 → 本笔的美元上限。None = 数据不足, **不静默当无限制**。"""
    out: Dict[str, Optional[float]] = {"budget_cap": None, "gross_cap": None,
                                       "budget_room": None, "gross_room": None}
    if not nav:
        return out
    gross_room = max(GROSS_CAP - used_gross, 0.0)
    out["gross_room"] = gross_room
    out["gross_cap"] = nav * gross_room
    if not atr or not price or price <= 0 or atr <= 0:
        return out
    budget_room = max(SHOCK_BUDGET - used_budget, 0.0)
    out["budget_room"] = budget_room
    out["budget_cap"] = nav * budget_room / (SHOCK_X * (atr / price))
    return out


def available_cap(nav: Optional[float], cap_frac: Optional[float],
                  atr: Optional[float], price: Optional[float],
                  usage: Optional[Dict] = None,
                  event_shrink: Optional[float] = None,
                  power_room: Optional[float] = None,
                  exposure: Optional[float] = None) -> Dict:
    """**实际 cap**= regime cap × 冲击预算余额 × gross 余额 × 购买力余额, 取小。

    用户 : 「默认值只是名义天花板, 实际情况需要靠计算得出 —— 依赖
    NAV 15% 的回撤幅度与 gross 110% 算出**余额**, 才是实际 cap。」

    所以 regime cap 是**名义天花板不是额度**: 40% 只说"单票最多这么大", 而这一笔
    真能下多少, 要看书里还剩多少冲击预算和多少 gross。**这个函数是那个数的唯一真源**
    —— 入场计划 (entry_guard)、持仓预算行 (portfolio_brief)、moomoo z1 备注
    (price_reminder_sync) 三处读同一份, 否则手机上那个金额和简报上那个金额会分叉。

    返回 `cap_usd=None` = NAV 缺失, **算不出 ≠ 无限制**, 调用方必须当"不知道"处理。
    `binding` ∈ {regime, shock_budget, gross, power}, 播报要带上 —— 不写出来就
    无法解释"为什么 40% 的卡今天只给了 34%"。

    `event_shrink` = 财报反应日缩仓系数 (`core.earnings.event_state`), 只收紧;
    None/1.0 = 非反应日。
    """
    parts: Dict[str, Optional[float]] = {"regime_cap": None,
                                         "shock_budget_cap": None, "gross_cap": None,
                                         "power_cap": None}
    out: Dict = {"cap_usd": None, "cap_frac": None, "binding": None,
                 "budget_room": None, "gross_room": None, "exposure": None,
                 "event_shrink": event_shrink, **parts}
    if not nav:
        return out
    if cap_frac is not None:
        # 财报反应日 T+0 缩仓 (earnings_window 路 A) —— 作用在 **regime cap**上:
        # 它压的是"这一笔那天能有多大", 不是组合层还剩多少。见 core/earnings.py。
        if event_shrink is not None:
            cap_frac = float(cap_frac) * min(1.0, max(0.0, float(event_shrink)))
        # **累计语义**(用户  A1): regime cap 从"每轮独立重置、不减已部署"
        # 改为"这个 ticker 总共能有多少" —— 减掉该票**当前敞口**(正股市值 + 期权
        # 保费市值, 与 gross 同口径)。每轮多大归 ② 轮预算管, 两层分开。
        # ⚠️ 已知后果 (用户 知情接受): 低波动票 R1 就能吃掉整个 40%, campaign
        # 事实上只剩一轮 —— 「低波动票就是一轮打满」。
        out["regime_cap"] = max(nav * float(cap_frac) - float(exposure or 0.0), 0.0)
        out["exposure"] = float(exposure or 0.0)
    fw = shock_budget_cap(nav, atr, price,
                          used_budget=(usage or {}).get("used_budget", 0.0),
                          used_gross=(usage or {}).get("used_gross", 0.0))
    out["shock_budget_cap"] = fw["budget_cap"]
    out["gross_cap"] = fw["gross_cap"]
    out["budget_room"] = fw["budget_room"]
    out["gross_room"] = fw["gross_room"]
    # ⑥ 购买力 (用户  Q2): gross 110% NAV 在零杠杆现金户里够不着,
    # 真 binding 的是现金。None = 快照缺失, **不参与取小也不当无限制**—— 调用方
    # 该播报"购买力未接"。真源 `core.book.power_room()` (含当日已成交扣减)。
    out["power_cap"] = power_room
    live = [(k, v) for k, v in (("regime", out["regime_cap"]),
                                ("shock_budget", out["shock_budget_cap"]),
                                ("gross", out["gross_cap"]),
                                ("power", out["power_cap"])) if v is not None]
    if not live:
        return out
    final = min(v for _k, v in live)
    out["cap_usd"] = final
    out["cap_frac"] = final / nav
    out["binding"] = next(k for k, v in live if v == final)
    return out


def entry_cap(nav: Optional[float], agent: Optional[dict] = None, *,
              atr: Optional[float] = None, price: Optional[float] = None,
              stop: Optional[float] = None, usage: Optional[Dict] = None,
              event_shrink: Optional[float] = None, gate_no: int = 1,
              power_room: Optional[float] = None,
              exposure: Optional[float] = None,
              avg_cost: Optional[float] = None,
              spot: Optional[float] = None) -> Dict:
    """**「这一笔实际能下多少」的单一真源**—— 成本闸 + 六条约束取小。

    用户 : 「available cap 无论是画线还是 entry guard 都要以
    available cap 显示」—— 四个消费者 (entry_guard / today_group /
    price_reminder / portfolio_brief) 读**这一个函数**。

        ① regime cap        current_regime.cap_pct_nav × NAV (× 财报反应日缩仓, 减敞口)
        ② gate 预算 N-unit  NAV × gate_risk_cap_frac(gate_no) / stop 距离, 减敞口
        ③ at-risk 天花板    5% NAV × 价 / (价 − stop)
        ④ 冲击预算余额       NAV × (0.15 − used) / (X × ATR20/价)
        ⑤ gross 余额        NAV × (1.10 − used_gross)
        ⑥ 购买力余额        power 快照 − 当日已成交 BUY
        ⓪ 成本闸            持仓之后 min(价, 现价) < 均价 → 0 (`below_cost`, 成本不向下摊;
                            `avg_cost` 由持仓侧调用方给 `holdings.cost_basis`,
                            空仓 None = 不参与。binding 名 `below_cost`, 同结构 VETO: 授权与画线保留, 钱归 0)

    **① 的唯一来源 = regime cap**(分类轴退役): 无 regime / cap 缺失
    → **0.0 fail-closed**(schema 同生同死保证活跃正股卡必有 regime, 无 regime =
    无授权, 不是"不约束")。二值世界: cap 0 = VETO, cap 给了 = 名义天花板,
    sizing 主尺是 ② 轮预算 (risk ÷ stop 距离)。

    ②③ 共用同一个 stop (用户) —— 比恒为 gate_risk_cap_frac /
    AT_RISK_CAP_FRAC, 而 `gate_risk_cap_frac` 的上界就是 T = AT_RISK_CAP_FRAC
    (测试钉相等) ⇒ ② ≤ ③ 恒成立, ③ 在入场侧**可证明永不 binding**, gate3 打满时
    两者相等。这不是巧合: T 既是「一个 campaign 总共冒多少险」也是「单笔
    at-risk 天花板」, 同一个数两种读法闭合。③ 的真咬点在持仓侧
    (at_risk_breach_signal, 棘轮抬升会推高 stop)。

    `price` 的口径由调用方选, 但**必须是这笔真要成交的价**: 入场计划用首档价,
    z1 提醒线用 z1 上沿 (响铃那一刻的价) —— 两者对 zone 锚定的 ladder 天然相等。

    `gate_no` = 这张卡爬过几个 gate (`gate_count`, 判据 = `regime_history`)。
    INTC: 收上 98 = gate1 (max 2.5%), 将来收上 104.5 = gate2 (max 4%)。
    **同一个 gate 里可以分多笔加**(用户: 「gate1 的 cap 给了 8k, 第一笔加了
    4000, 还能在当前 gate 的 zone 继续加, 第二次余额显示只剩 4000」) —— 那个
    减法就是 `exposure` (当前敞口, 市值口径), 与 ① 减的是**同一个数**, 因为两条
    臂问的都是"这个名字身上已经有多少钱"。打满 = 0, 等下一个 gate。
    返回 `final=None` = 算不出 (NAV 缺失), **算不出 ≠ 无限制**。
    """
    rc = regime_cap_frac(agent)
    ac = available_cap(nav, rc if rc is not None else 0.0, atr, price,
                       usage, event_shrink=event_shrink, power_room=power_room,
                       exposure=exposure)
    nunit = gate_risk_notional_cap(nav, gate_no, price, stop)
    if nunit is not None:
        nunit = max(nunit - float(exposure or 0.0), 0.0)
    at_risk = at_risk_nominal_cap(nav, price, stop)
    cost_gate = 0.0 if below_cost(price, spot, avg_cost) else None
    named = [("regime", ac["regime_cap"]), ("below_cost", cost_gate), ("n_unit", nunit),
             ("at_risk", at_risk), ("shock_budget", ac["shock_budget_cap"]),
             ("gross", ac["gross_cap"]), ("power", ac["power_cap"])]
    live = [(k, v) for k, v in named if v is not None]
    final = min(v for _k, v in live) if live else None
    binding = next((k for k, v in live if v == final), None) if final is not None else None
    out = dict(ac)
    out.update({"n_unit_cap": nunit, "at_risk_cap": at_risk, "gate_no": gate_no,
                "regime_cap_frac": rc,
                "cap_usd": final, "final": final, "binding": binding,
                "cap_frac": (final / nav) if (final is not None and nav) else None})
    return out


def notional_to_premium_cap(notional_cap: Optional[float], delta: Optional[float],
                            spot: Optional[float],
                            premium: Optional[float]) -> Optional[float]:
    """underlying-delta **名义**上限 → 期权**保费**预算。None = 参数不足, 不换算。

    ⚠️ 这两件事不是同一种钱, 混用是  接线时才暴露的旧单位错误:
      · 分类 cap = 你**花出去**多少钱 (保费)
      · n_unit / 冲击预算 / gross = 标的**名义敞口**多少钱
    一张合约: 名义 = delta×100×spot, 花费 = premium×100
      ⇒ 同一个名义上限折成保费预算 = notional × premium / (delta × spot)

    不换算会怎样: GOOGL LEAPS delta 0.41 / spot 354 / premium 20.5 —— 把
    $7,400 的**名义**上限当保费花, 买到 3 张 = 名义 $43,700, 6 倍于上限。
    方向是**放松约束**, 正是这套框架要堵的那个洞 (§2 "名义 cap 在期权上完全失效")。
    """
    if not notional_cap or not delta or not spot or not premium:
        return None
    if delta <= 0 or spot <= 0 or premium <= 0:
        return None
    return notional_cap * premium / (delta * spot)


def usage_lines(usage: Optional[Dict], nav: Optional[float] = None) -> List[str]:
    """组合占用播报 —— entry_guard 与 portfolio_brief 共用同一段文本。

    两个数据缺口都要说出口 (静默降级是本仓反复出的病): delta 反解不到按 1.0 计
    = 敞口被**高估**; 缺 ATR 的仓位不计入预算 = 预算被**低估**。方向相反, 所以
    必须分开讲, 不能合并成一句"数据不全"。
    """
    if not usage or usage.get("nav_missing") or not nav:
        return []
    out = [f"🧮 组合占用: 冲击预算 {usage.get('used_budget', 0.0) * 100:.2f}%/"
           f"{SHOCK_BUDGET * 100:.0f}% NAV | gross {usage.get('used_gross', 0.0) * 100:.1f}%/"
           f"{GROSS_CAP * 100:.0f}% NAV | 单票名义上限 {NOTIONAL_CAP * 100:.0f}%"]
    if usage.get("assumed_full_delta"):
        out.append("   ⚠️ 期权 delta 缺失 → 按 delta=1.0 保守计 (敞口被高估): "
                   + ", ".join(usage["assumed_full_delta"]))
    if usage.get("missing_atr"):
        out.append("   ⚠️ 缺 ATR, **未计入冲击预算占用**(预算被低估): "
                   + ", ".join(usage["missing_atr"]))
    return out

# ── 2. 卡片约束 (agent.action.constraints) ─────────────────────────
#
# schema (agent.action.constraints, 全部可选):
#   reason             : str   — 为什么 (进播报)
#   from / until       : "YYYY-MM-DD" — 时间窗 (两端含当日); 现仅作播报标记
#   touch_protocol     : "A_take_first" | "B_passive_only" | "C_60m_first_bar"
#                        触碰判读协议 (zone-touch-confirmation)。**只播报不改判**。
#   vehicle            : "stock" | "leaps" | "spread" | "stock_first" — 期权分支读
#
# [历史] max_classification (分类天花板硬 clamp)  随分类轴退役 ——
# 退役时 live 活例 0 张; 老卡若还带此字段, constraint_lines 播报"已退役"而非静默忽略。

_VALID_TOUCH = {"A_take_first", "B_passive_only", "C_60m_first_bar"}


CAP_TOL = 1e-9


def read_constraints(agent: Optional[dict]) -> dict:
    """从卡片读出结构化约束。返回 {} 表示卡片没写 (不是"没有约束"的断言,
    只是这张卡还没结构化 —— 调用方不该据此推断"无限制")。"""
    action = (agent or {}).get("action") or {}
    c = action.get("constraints")
    return dict(c) if isinstance(c, dict) else {}


def constraint_window_state(constraints: dict, today: str) -> str:
    """约束时间窗三态: 'active' / 'pending' (from 未到) / 'expired' (until 已过) / 'none'。
    from 缺省 = 立即生效; until 缺省 = 永久有效; 两端都含当日。

    窗口机制保留给结构/量化类约束 (v3 2026-08-04: 财报/thesis 类 clamp 已废止,
    现存唯一使用者 = SKHY premium gate)。窗口未开 = 不 clamp, 但必须播报
    —— 未开窗静默不显示与过期静默失效是同一个病。
    [历史] from 字段  为财报 clamp 而设 (print−7), 该用途随 v3 退役。"""
    if not constraints:
        return "none"
    start = constraints.get("from")
    if start and str(today) < str(start):
        return "pending"
    until = constraints.get("until")
    if until and str(today) > str(until):
        return "expired"
    return "active"


def constraint_active(constraints: dict, today: str) -> bool:
    """在窗口内 (from ≤ today ≤ until) 才生效。"""
    return constraint_window_state(constraints, today) == "active"


def derive_auth(ticker: str, cards: dict,
                today: Optional[str] = None,
                option_plans: Optional[dict] = None) -> tuple[Optional[dict], str]:
    """成交事件的授权快照 —— 跟随系统授权, → (auth | None, 依据)。

     分类轴退役后 `derive_classification` 的接替者 (D4): 不再反查分类词,
    直接盖**成交时的授权事实**`{"cap_pct_nav": float, "gate_no": int}` ——
    信息量严格多于旧标签 (cap 说明授权档, gate_no 说明爬到第几个 gate)。
    ⚠️  当日先后两个键: `round_no`(lots 近似的轮号) 只读留存于当天那
    两条历史事件, 新事件一律 `gate_no` —— `tier_legacy` / `classification` 先例。
    期权归正股: 授权住 underlying 卡 (与 Today 白名单同拍)。

    推不出来 (cap 0 / 无 regime) → None: 那正是"**无授权**"的形状, 这类成交按
    规则外处理, 归人判。**None 不是失败, 是判断**——调用方必须让它可见
    (同步层留空 + brief 告警), 别拿它当"算不出来就算了"。

    纯函数, cards 由调用方读。与 holdings.derive_tier 配对使用。
    gate_no = 落库那一刻卡上的 `gate_count` —— 与 grant 侧**逐字同一个判别式**
    (盖的必须是当时真的授权了多少, 不是另一套近似)。
    """
    cards = cards or {}
    key = ticker.upper()
    card = cards.get(key) or {}
    src = key
    occ = parse_occ(key)
    # reversal_probe : 授权不住正股卡 (它 cap 0 也能出计划), 住
    # entry_guard 落的 option_plans.json —— 合约 code 对上 + 落库日在有效期内 = 本类
    # 授权成交; gate_no 记风险预算所在档 (REVERSAL_PROBE_RISK_GATE), cap 记保费上限占 NAV。
    if option_plans:
        und_key = str(occ.get("underlying") or "").upper() if occ else key
        plan = (option_plans or {}).get(und_key) or {}
        leg = None
        if plan.get("entry_class") == "reversal_probe":
            if occ and str(plan.get("code") or "").upper() == key:
                leg = ("call", coerce_float(plan.get("cap_frac")))
            elif not occ and isinstance(plan.get("stock"), dict):
                leg = ("正股", coerce_float((plan.get("stock") or {}).get("cap_frac")))   # 正股腿
        if leg:
            vu = str(plan.get("valid_until") or "")
            day = str(today or "")[:10]
            if vu and day and day > vu:
                return None, (f"{key} 抄底 (reversal_probe) 计划已过期 (valid_until {vu} < {day}) — "
                              "过期成交 = 无授权, 归人判")
            vehicle, cap_frac = leg
            if cap_frac and cap_frac > 0:
                return ({"cap_pct_nav": float(cap_frac), "gate_no": REVERSAL_PROBE_RISK_GATE,
                         "entry_class": "reversal_probe", "support": coerce_float(plan.get("support")),
                         "signal_date": plan.get("signal_date"), "vehicle": vehicle},
                        f"抄底 (reversal_probe) 计划 {plan.get('signal_date')} {vehicle} (止跌低 {plan.get('support')}, "
                        f"cap {cap_frac:.1%} NAV, 有效至 {vu or '?'})")
    if occ and not ((card.get("agent") or {}).get("current_regime")):
        card = cards.get(occ["underlying"]) or card      # 期权授权住正股卡
        src = occ["underlying"]
    agent = card.get("agent") or {}
    regime = agent.get("current_regime") or {}
    cap = coerce_float(regime.get("cap_pct_nav"))
    if cap is None:
        return None, f"{src} 无 current_regime — 无法推导, 归人判 (无授权成交?)"
    if cap <= CAP_TOL:
        return None, (f"{src} regime {regime.get('name')!r} cap 0 = 未授权 — "
                      "不推导 (无授权成交按规则外处理, 归人判)")
    gno = gate_count(card)
    auth = {"cap_pct_nav": float(cap), "gate_no": gno}
    basis = (f"{src} regime {regime.get('name')!r} cap {cap:.0%} · gate{gno}"
             f" (since {regime.get('since')})")
    return auth, basis


def constraint_lines(constraints: dict, today: str) -> List[str]:
    """卡片约束的播报行 —— **无论 verdict 是什么都要出现**。
    此前只在 WAIT 时打印, GO 时消失, 恰好是最需要看到它的时候。"""
    if not constraints:
        return []
    out: List[str] = []
    state = constraint_window_state(constraints, today)
    mark = {"active": "", "pending": " [窗口未开]", "expired": " [已过期]"}.get(state, "")
    cap_cls = constraints.get("max_classification")
    if cap_cls:
        # 分类轴  退役 —— 老卡带此字段不再产生任何 clamp, 播报而非静默
        out.append(f"⚠️ 卡片带 max_classification={cap_cls} — **分类轴已退役 (),"
                   " 此字段不生效**, 用 update_position_zones 清掉; 收紧授权改动 regime cap")
    tp = constraints.get("touch_protocol")
    if tp:
        desc = {
            "A_take_first": "A 缩量回踩 → 首档直接吃",
            "B_passive_only": "B 放量急杀 → **仅预挂被动**, 等 climax 三件套, 不主动追档",
            "C_60m_first_bar": "C gap → 60 分钟首根定性后再动",
        }.get(tp, tp)
        flag = "" if tp in _VALID_TOUCH else " ⚠️未知协议值"
        out.append(f"🎯 触碰协议{mark}: {desc}{flag}")
    veh = constraints.get("vehicle")
    if veh and veh != "stock":
        tail = ("— 下方阶梯为**标的价位**, 合约级参数见 🧰 期权执行计划"
                if veh in {"leaps", "spread"} else "— 正股优先, 期权为后备")
        out.append(f"🧰 卡片 vehicle: {veh} {tail}")
    # 死字段守卫 (ledger A1, ;  分类轴退役后时间窗对任何字段
    # 都无 clamp 效力): 带时间窗的 constraints 不产生任何动作, 只会造"有约束"的错觉。
    if not cap_cls and (constraints.get("from") or constraints.get("until")):
        out.append("⚠️ constraints 带时间窗 = **死字段不生效**"
                   "(clamp 机制随分类轴退役 ; 财报缩仓走 earnings_calendar) — 清掉 "
                   "(update_position_zones --constraint-until none ...)")
    return out


# ── 2b. 当前 regime 的授权属性 (agent.current_regime) ──────────────
#
#  源案例: regime_transition 造 observation_below_* 时写了
# `cap_pct_nav: 0` + `participation: "gate_only"`, derivation 里逐字写着
# 「cap 0 只作'价格下去大概到哪'的底, **不授权任何入场**」。但它变成 current
# 的那一刻, apply_transition 只把 `zones` 复制进 agent.zones ——
#     agent["zones"] = dict(last.get("zones") or {})     # cap / participation 丢在这里
# 而 cap_pct_nav 全仓只有两处被读, **都只读 next_regime**。于是一个明确
# 标着"不授权入场"的 regime, 变成当前状态后只剩两条 accumulation 带,
# 正好架在刚破的 stop 底下。
#
# 那句 `# current ← 旧 last (观察层: cap 0, 只看不做)` 是注释, 不是字段 ——
# 同 LITE 卡片天花板、同 fired_cards: **会驱动动作的属性必须有地方住。**
#
# 部分保护此前来自 action.intent = "wait_for_structure" (entry_guard 的
# gate_only 轨), 但它挡不住三种情况:
#   1) 持仓票 —— entry_guard 根本不评估, 加仓走 briefing 手册优先级 7
#   2) 结构一旦修复, gate_only 轨放行, 之后按正常 cap 给 ladder
#   3) portfolio_brief 的预算行读 fill 的标签, 与 regime 无关

def regime_gate(agent: Optional[dict]) -> dict:
    """当前 regime 的授权属性。{} = 卡片没有 regime 机制或未记录 (不约束)。"""
    cr = (agent or {}).get("current_regime")
    return dict(cr) if isinstance(cr, dict) else {}


def regime_cap_frac(agent: Optional[dict]) -> Optional[float]:
    """当前 regime 对 size 的硬上限 (占 NAV)。None = 未记录 (2026-08-13 起
    **消费侧一律 fail-closed 当 0**, 见 entry_cap ①)。0.0 = 一分不授权。"""
    v = regime_gate(agent).get("cap_pct_nav")
    return float(v) if isinstance(v, (int, float)) else None


def regime_lines(agent: Optional[dict]) -> List[str]:
    """当前 regime 的播报行 —— cap 0 必须显式说出来, 不能只体现为"金额小"。"""
    g = regime_gate(agent)
    if not g:
        return []
    cap, name = g.get("cap_pct_nav"), g.get("name") or "?"
    out: List[str] = []
    if cap == 0:
        out.append(f"⛔ 当前 regime [{name}] cap 0 — **不授权任何入场/加仓**"
                   f"{'; ' + str(g['reason']) if g.get('reason') else ''}"
                   " (zone 只作'价格下去大概到哪'的底, 不是买点)")
    elif isinstance(cap, (int, float)):
        out.append(f"🎚 当前 regime [{name}] cap {cap * 100:g}% NAV")
    if g.get("participation") == "gate_only":
        out.append("   参与资格 = gate_only — zone 触碰不给资格, 只认 gate 成立")
    return out


# ── 3. 整股约束下的档位分配 ────────────────────────────────────────
#
# position-tiers.md §整股约束 (用户): 不做碎股, 最小 1 股。
#     shares = floor(cap / price)
#       ≥3 → 3 档 (50/30/20)
#        2 → 2 档 (1 股 + 1 股)
#        1 → 单档, **豁免 greedy-bid 单档禁令**(物理上切不开)
#        0 → 买 1 股 + 强制 decisions.jsonl 书面例外
# 这条规则 7/25 就定了, 但只活在文档里 —— entry_guard 的 render 照旧按权重
# 分钱再除价格,  LITE 输出 "≈0.6股 / ≈0.3股 / ≈0.2股" 三档全碎股,
# 读起来像个正常三档计划。


def _largest_remainder(n: int, weights: Sequence[float]) -> List[int]:
    """按权重把 n 个整数单位分到 len(weights) 档, 最大余数法。
    n >= len(weights) 时保证每档 ≥1 (档位为 0 的"档"不是档)。"""
    k = len(weights)
    exact = [n * w for w in weights]
    alloc = [int(e) for e in exact]
    left = n - sum(alloc)
    if left > 0:
        order = sorted(range(k), key=lambda i: (exact[i] - alloc[i], weights[i]), reverse=True)
        for i in order[:left]:
            alloc[i] += 1
    # 零档回填: 从最大档挪 1 股过来 (n>=k 时一定挪得动)
    if n >= k:
        for i in range(k):
            if alloc[i] == 0:
                j = max(range(k), key=lambda x: alloc[x])
                if alloc[j] > 1:
                    alloc[j] -= 1
                    alloc[i] += 1
    return alloc


def share_ladder(cap_usd: Optional[float], prices: Sequence[float],
                 weights: Sequence[float], multiplier: float = 1.0,
                 unit: str = "股") -> Optional[dict]:
    """把金额 cap + 阶梯价位翻译成**整股**档位。

    股数以首档价 (最接近现价、最贵的那档) 为基准算 —— 保守口径, 与
    position-tiers.md 的 floor(cap/price) 一致。

    返回:
      tiers        [{price, shares, usd, weight_actual}] — 只含 shares>=1 的档
      n_shares     总股数 (0 = 切不开)
      n_tiers      实际档数
      degraded     是否因整股约束降档 (档数 < len(prices))
      exception    True = 0 股路径, 需 decisions.jsonl 书面例外
      note         给渲染层直接用的一句话
    """
    if not cap_usd or cap_usd <= 0 or not prices:
        return None
    unit0 = float(prices[0]) * multiplier
    if unit0 <= 0:
        return None
    n = int(cap_usd // unit0)
    k = len(prices)

    if n <= 0:
        p0 = float(prices[0])
        return {
            "tiers": [{"price": p0, "shares": 1, "usd": round(unit0, 2), "weight_actual": 1.0}],
            "n_shares": 0, "n_tiers": 1, "degraded": True, "exception": True,
            "cap_multiple": round(unit0 / cap_usd, 2),
            "note": (f"⚠️ floor(cap/price)=0 {unit} — 1 {unit} = ${unit0:,.0f} = "
                     f"{unit0 / cap_usd:.1f}× cap。要做 = 买 1 {unit} + **强制**decisions.jsonl "
                     f"书面例外 (position-tiers.md §整股约束); 不做 = 跳过, 不要挂碎档"),
        }

    use_k = min(n, k)
    alloc = _largest_remainder(n, list(weights)[:use_k]) if use_k > 1 else [n]
    tiers = []
    for i in range(use_k):
        if alloc[i] < 1:
            continue
        px = float(prices[i])
        tiers.append({"price": px, "shares": alloc[i],
                      "usd": round(px * alloc[i] * multiplier, 2),
                      "weight_actual": round(alloc[i] / n, 3)})
    n_tiers = len(tiers)
    degraded = n_tiers < k
    if n_tiers == 1:
        note = (f"整{unit}约束 → 单档 (floor(cap/price)=1)。**豁免 greedy-bid 单档禁令**—— "
                "物理上切不开 ≠ 赌深度回调; 但首档价位锚点不变, 必须在参与位")
    elif degraded:
        note = f"整{unit}约束 → {n_tiers} 档 (原 {k} 档; floor(cap/price)={n} {unit})"
    else:
        note = ""
    return {"tiers": tiers, "n_shares": n, "n_tiers": n_tiers,
            "degraded": degraded, "exception": False, "cap_multiple": None, "note": note}
