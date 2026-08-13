"""structure.py — 结构快照的**时效性与自洽**: gate 与 zone 同生同死同锚。

为什么存在 (用户):
    zone 一直有 valid_until + staleness (过期 / breakout / breakdown /
    last_reviewed >14d); gate (reclaim_gates / next_regime.entry_gate) **一处都没有**
    —— 从不过期, 从不检查。而两者本来就是同一次分析的产物, 锚在同一份结构上。
    Gate 被默许了 zone 从来没有的永久性, 而这不是谁决定的, 只是没实现。

    实测代价 (同一方法, 同一只票 NBIS, 只换画卡日期):
         收 194.09 → confirmed 锚 ≈ 198
         收 216.92 → confirmed 锚 ≈ 234   ← 卡上这个
         收 187.88 → confirmed 锚 ≈ 198
    **234 不是 NBIS 的属性, 是"7/21"这个日期的属性。**
    而卡上挂着 234 (+24.5%), 当前结构指向 198 (+5.3%) —— 缺 staleness 正让
    confirmed 门槛比结构所要求的高出一倍距离。

不可下移的**不是数字, 是证据标准**(用户 定):
    · 数字      随结构重画, 可上可下
    · 证据标准  binding 供给簇 + 触碰次数 + 量比 + N 日站稳 —— 任何一项被
                悄悄放宽 = 作弊。`anchored_on.basis` 就是为了让这件事可审计
    · tier↔证据 满额 cap 的授予永远要求"结构性修复"级证据, 不因价格低而打折
    防 averaging down 的是 **cap**(current_regime.cap_pct_nav / 集中度),
    从来不是 gate。让 gate 兼职风控 = 为了防守把它冻死, 顺带把
    "300→100 筑底后 100→200" 那段一起冻掉。

"同步"的三层 (缺任何一层都是嘴上同步):
    1. 同生死   zone stale ⇒ gate stale (共用同一套触发条件)
    2. 同锚     gate.anchored_on.date 必须等于 zones 那次画卡的日期; 不等 = 有人
                只重画了一半 → 这本身就是告警
    3. 自洽     上行 gate 按定义在 no_chase_above **之上**(它是下一个状态的入口);
                repair gate 在 stop 之下。倒挂 = 结构不成立
"""
from __future__ import annotations

from datetime import date
from typing import Dict, List, Optional

from .num import coerce_float
from .sizing import NOTIONAL_CAP

# gate 与 zone 的画卡日期允许的最大偏差 (日历日)。>0 是为了容忍同一轮分析里
# 跨午夜写入; 真正的"只重画了一半"会差好几天。
ANCHOR_DRIFT_TOLERANCE_DAYS = 1

# ── card 源 current_regime 派生 (regime 统一 , 方案 a 用户 批) ──
#
# 判据一句话「当前 regime 有没有给 cap」要在数据层字面成立 → 每张有 zones 的
# 正股卡显式携带 current_regime。转移机/促升写不到的卡 (常规参与 / 修复观察 /
# 直接买入的持仓) 由建卡/重画路径按下表派生。
# 所有权: 只覆盖 source=="card" 或缺失的 regime; 机器源 (auto/demoted, 即
# regime_transition 写的; 枚举 schema.REGIME_SOURCES 强制 —— "promotion" 是
# 幽灵取值, 促升实测走本文件 derive 写 card) 一律不触碰 —— 结构变化导致的
# 授权升降归转移机, 不归画图的手。
# 设计真源: quant/research/regime_field_unification_design.md (语义规格表)。

CARD_REGIME_SOURCE = "card"


def derive_card_regime(agent: Optional[dict], *, bucket: str, today: str) -> str:
    """按语义规格派生/维护 card 源 current_regime。返回动作标签供播报:
    kept_machine / derived_normal / derived_repair / derived_no_add / cleared / noop。

    bucket: "watchlist" (候选: 干净→常规参与 NOTIONAL_CAP 满额,
            带 reclaim_gates→修复观察 0) / "positions" (无机器 regime 的持仓
            = 入场已行使, 加仓待 gate → 持仓·无加仓授权 0)。
    cap 二值 {0, NOTIONAL_CAP} (分类轴退役)。
    since = 结构锚定日 (消费锚, 不是执行日)。"""
    if agent is None:
        return "noop"
    cr = agent.get("current_regime")
    if isinstance(cr, dict) and cr.get("source") not in (None, CARD_REGIME_SOURCE):
        return "kept_machine"
    z = agent.get("zones")
    if not isinstance(z, dict) or not z:
        if cr is not None:
            agent.pop("current_regime", None)    # 同生同死: 无 zones 不许带 regime
            return "cleared"
        return "noop"
    since = (((z.get("anchored_on") or {}).get("date"))
             or str((agent.get("thesis") or {}).get("last_reviewed") or "")[:10]
             or today)
    if bucket == "positions":
        agent["current_regime"] = {
            "name": "持仓·无加仓授权", "cap_pct_nav": 0, "participation": "gate_only",
            "since": since, "source": CARD_REGIME_SOURCE,
            "reason": "入场授权已行使; 加仓授权待 gate 达标转移 (NBIS 规则 2026-08-05)"}
        return "derived_no_add"
    if z.get("reclaim_gates"):
        agent["current_regime"] = {
            "name": "修复观察", "cap_pct_nav": 0, "participation": "gate_only",
            "since": since, "source": CARD_REGIME_SOURCE,
            "reason": "修复路径 — gate 达标前不授权 (gate 授权域=修复路径 only,)"}
        return "derived_repair"
    agent["current_regime"] = {
        "name": "常规参与", "cap_pct_nav": NOTIONAL_CAP,
        "participation": "zone_eligible", "since": since, "source": CARD_REGIME_SOURCE,
        "reason": "结构干净 → 满额参与资格, 实际 size 由 gate 预算主导 "
                  "(cap 二值 {0, NOTIONAL_CAP},  分类轴退役)"}
    return "derived_normal"


# ── 结构修复 VETO 判别式 (用户  裁决, 前身 = 入场档位判别式) ──
#
# 分类轴退役 (classification_removal_design.md D1): 旧输出「降级 probe (cap 2%)」
# 改为 **VETO**—— 用户 逐字「结构降级反转立马 cap 0 即可」。二值世界没有打折
# 中间态 (probe 档历史净 −$2,570 / −26%/$, 中间态的钱是负的)。
#
# **适用域 = card 源 cap (用户  二裁纠偏)**: 「之前给了 cap 的 probe
# 仓位…应该是直接允许 R1 入场」——机器源 (auto/demoted) 的 cap 是**转移机在
# gate 收复 ×2 日 + 量腿确认后授予的**, 那一刻修复裁决已做完; 运行时再用 swing
# 标签 (HH+LL 常挂着**历史**LL) 复审 = 第二个判别式推翻第一个, 而 gate 才是
# 权威 (「卡片名不是判据, tape 才是」—— gate 收复正是 tape 的读数)。机器源的
# 降级权归**转移机**(破 stop → 降档 cap 0, 该机制一直在)。
# card 源 (derive「干净候选」) 的判断锚在画卡时点 → 运行时护栏适用, 堵的缝 =
# 两次重画之间结构转坏 (HH+LL 不破 stop 不触发转移机; LITE  源案例)。
#
# 判别式**内核逐字不动**(structure_downgrade_backtest 的证据继续有效):
# 降级只认 down/transition, 明确**不**纳入 close_below_last_swing_low
# (回测该桶 p10 优于 up 干净)。四消费者同一份 (entry_guard / today_group /
# price_reminder / brief),  收敛于此。

MACHINE_REGIME_SOURCES = {"auto", "demoted"}


def structure_repair_veto(agent: Optional[dict], st: Optional[dict],
                          close: Optional[float]) -> tuple:
    """结构未修复 → (vetoed: bool, reason | None)。

    `agent` 定适用域 (机器源 cap 豁免, 见上); `st` = `technical.swing_structure
    (rows)`; `close` = 最近完成日线收盘。结构数据不足 (st 空 / trend==
    "insufficient") → 不 VETO (数据不足的保守回落归调用方按场景处理)。
    """
    src = str((((agent or {}).get("current_regime")) or {}).get("source") or "")
    if src in MACHINE_REGIME_SOURCES:
        return False, None          # gate 授予的 cap — 修复裁决已由转移机做过
    st = st or {}
    lsh = coerce_float(st.get("last_swing_high"))
    repaired = bool(lsh and close and close > lsh)
    if st.get("trend") in ("down", "transition") and not repaired:
        reason = (f"结构 {st.get('high_label')}+{st.get('low_label')} {st.get('trend')}"
                  " 未修复 → VETO (cap 0,  二值裁决)"
                  + (f" (修复信号 close > {lsh:g})" if lsh else ""))
        return True, reason
    return False, None


# ── zone 数值带 + 落点 ────────────────────────────────────────────
#
#  由 decision/entry_guard 下沉。第二个消费者 (sync/lot_reconcile 要给
# 自动落账的 lot 写 "成交价落在哪个 zone") 在 sync 层, import 不到 decision ——
# 而这套判别式**只能有一份**: 它抄第二遍的那天, 入场计划说的 zone 和 lot 台账
# 记的 zone 就会开始各说各话 (UNMANAGED_ROLES 抄 8 处、CAP_FRACS 抄 2 处都是
# 这么来的)。entry_guard 保留同名 re-export, 调用方无感。

ZONE_LABELS = {
    "above_no_chase": "no_chase 之上",
    "trim": "trim 区",
    "waiting": "等待区 (z1 上沿之上)",
    "zone1": "z1 内",
    "gap_z1_z2": "z1/z2 之间",
    "zone2": "z2 内",
    "below_zones": "z2 之下",
}


def zone_bands(zones: Optional[dict]) -> Optional[dict]:
    """数值 zone 提取; 缺任一核心带 → None (prose-only 卡, 如 wait_for_structure 期)。"""
    if not isinstance(zones, dict):
        return None
    z1, z2 = zones.get("accumulation_zone_1"), zones.get("accumulation_zone_2")
    if not (isinstance(z1, list) and isinstance(z2, list) and len(z1) == 2 and len(z2) == 2):
        return None
    return {
        "z1": [float(z1[0]), float(z1[1])], "z2": [float(z2[0]), float(z2[1])],
        "trim": zones.get("trim_zone"),
        "no_chase": coerce_float(zones.get("no_chase_above")),
        "stop": coerce_float(zones.get("stop_level")),
        "valid_until": zones.get("valid_until"),
    }


def zone_position(close: float, zb: dict) -> str:
    """→ above_no_chase | trim | waiting | zone1 | gap_z1_z2 | zone2 | below_zones"""
    nc = zb.get("no_chase")
    trim = zb.get("trim")
    if nc and close >= nc:
        return "above_no_chase"
    if isinstance(trim, list) and len(trim) == 2 and trim[0] <= close <= trim[1]:
        return "trim"
    if zb["z1"][0] <= close <= zb["z1"][1]:
        return "zone1"
    if zb["z2"][0] <= close <= zb["z2"][1]:
        return "zone2"
    if close > zb["z1"][1]:
        return "waiting"
    if zb["z2"][1] < close < zb["z1"][0]:
        return "gap_z1_z2"
    return "below_zones"


def _d(s) -> Optional[date]:
    try:
        return date.fromisoformat(str(s)[:10])
    except (ValueError, TypeError):
        return None


def gates_of(agent: Optional[dict]) -> List[Dict]:
    """卡上所有 gate, 统一形状。label 用于播报, kind 决定自洽检查的方向。"""
    agent = agent or {}
    zones = agent.get("zones") or {}
    out: List[Dict] = []
    for g in (zones.get("reclaim_gates") or []):
        if isinstance(g, dict) and g.get("level") is not None:
            out.append({**g, "kind": "reclaim", "where": "zones.reclaim_gates"})
    for slot, kind in (("next_regime", "up"), ("last_regime", "down")):
        blk = agent.get(slot)
        if not isinstance(blk, dict):
            continue
        g = blk.get("entry_gate") or blk.get("repair_gate")
        if isinstance(g, dict) and g.get("level") is not None:
            out.append({**g, "kind": kind, "where": f"{slot}.{'entry' if kind == 'up' else 'repair'}_gate",
                        "regime": blk.get("name")})
    return out


def has_next_gate(agent: Optional[dict]) -> bool:
    """这张卡有没有**上行状态转移轴**。

    判据只认 `next_regime.entry_gate.level` —— 因为 `regime_transition.derive()`
    的上行分支只读它 (`if level is not None:` 之外整段不评估)。`reclaim_gates`
    是本 regime 内的早信号标记, 不切换 regime、不授予下一档 cap, 所以不算。
    """
    nr = (agent or {}).get("next_regime")
    if not isinstance(nr, dict):
        return False
    return coerce_float(((nr.get("entry_gate") or {}).get("level"))) is not None


def cards_missing_next_gate(*docs: Optional[dict]) -> List[str]:
    """活跃正股卡缺上行转移轴 —— **一张只能活一轮的卡**。

    用户  定版: **gate 在本系统的哲学 = 状态转移轴**, 就是标志性价格位;
    下降趋势有、上升趋势有、修复阶段也有 —— **不是破位卡的专属物**。
    当前 regime 管的是「现在能不能参与」(cap), next_regime 管的是「下一个状态在哪」,
    两件独立的事, **画卡时都要同步给出**。

    缺了会怎样 (两种都是真损失, 文本分开说):
      cap > 0   第一档能进, 但 gate2/gate3 (4%/5%) 永远拿不到授权
                —— 促升后 derive_card_regime 写 cap 0, 而没有 gate 就没有升回来的路
      cap == 0  卡本来就锁在 0, 没有上行出口 = 只能等破位 (那条路通往 reclaim_*
                cap 2%, 不是 confirmed 40%)

    写 `next_regime` 的两处都在 `regime_transition.apply_transition` 里 —— 机器
    **只在转移发生时**写。所以建卡时不写, 就真的没有人会补。
    源案例 : 12 张活跃卡里 COHR / META (常规参与 cap 40%) 与 GOOGL
    (持仓 cap 0) 三张缺, 其余 9 张都有 (AMZN `R_突破_279` 是范本: 价位带
    level_map 证据, 站不住的带标 provisional 等转移当日补画)。
    """
    from .holdings import is_managed                        # noqa: PLC0415  同层
    out: List[str] = []
    for doc in docs:
        for t, body in sorted(((doc or {}).get("tickers") or {}).items()):
            if body.get("status") == "closed" or not is_managed(body):
                continue
            if ((body.get("instrument") or {}).get("type")) == "option":
                continue        # 期权卡的转移轴在 underlying 上 (同 current_regime 豁免)
            agent = body.get("agent") or {}
            if not (agent.get("zones") or {}) or has_next_gate(agent):
                continue
            cr = agent.get("current_regime") or {}
            cap = coerce_float(cr.get("cap_pct_nav")) or 0
            tail = ("只有一轮 (R2/R3 无授权路径)" if cap > 0
                    else "cap 0 且无上行出口 = 锁死, 只能等破位")
            out.append(f"{t} [{cr.get('name') or '无 regime'} cap "
                       f"{cap * 100:g}%] — {tail}")
    return out


def cards_missing_zone_anchor(*docs: Optional[dict]) -> List[str]:
    """机器装上来的 zones 没有锚 —— `anchor_date()` 会悄悄替它补一个新鲜日期。

    `apply_transition` 上行分支是 `agent["zones"] = dict(nr.get("zones") or {})`,
    **整体替换**。而 `next_regime.zones` 是画卡时预先声明的价位块, 里面**从来没有
    `anchored_on`**(它不是一次画卡, 是一份对下一个状态的预案)。于是转移一发生:

        zones.anchored_on  →  消失
        anchor_date()      →  退到 thesis.last_reviewed = 最近碰过卡的时间
        ⇒ 所有以锚为判据的检查全部当它是**刚画的**

    受害者至少两个: `earnings.cards_stale_after_print` (财报重画 SLA 自动消音) 与
    `gate_staleness` 的同锚层。**这比"盖错锚"更隐蔽**—— 盖锚至少有一步写了那个
    日期, 这里没有任何一步写过, 是 fallback 凭空补的。而它专挑"刚发生结构大事所以
    刚转移"的时刻发作, 正是最需要重画的那一刻。

    🚨 **不要用"转移时自动盖锚"去修**: 那等于让一份**转移前**推出来的价位块自称
    锚在转移日 = 「复核 ≠ 重锚」(zone-maintenance, MU/MRVL  源案例)。
    锚**空着是诚实的**, 错的是 fallback 悄悄补 —— 所以出口是告警, 不是补锚。

    判据 = `current_regime.source` 是机器源 (auto/demoted, 即转移机写的) 且
    `zones.anchored_on.date` 缺失。card 源与无 source 的老卡不进 (前画的
    卡全都没有锚, zone-maintenance 明定那是**提示级**; 它们也没有"凭空变新鲜"这个
    问题 —— 它们的 last_reviewed 本来就是自己那次画卡)。

    源案例  (用户 抓): NBIS  BMO 跳空 +28%, 当晚快车道转移在即。
    模拟转移后 `anchored_on: None` / `anchor_date() → `, 而那套 zones 是
    8/01 建、8/06 按**跳空前**分布重推的 —— 不含当天那根 45 点、40M+ 股的 bar。
    """
    from .holdings import is_managed                        # noqa: PLC0415  同层
    out: List[str] = []
    for doc in docs:
        for t, body in sorted(((doc or {}).get("tickers") or {}).items()):
            if body.get("status") == "closed" or not is_managed(body):
                continue
            if ((body.get("instrument") or {}).get("type")) == "option":
                continue        # 期权卡的结构锚在 underlying 上 (同 next_gate 豁免)
            agent = body.get("agent") or {}
            z = agent.get("zones") or {}
            if not z or (z.get("anchored_on") or {}).get("date"):
                continue
            src = (agent.get("current_regime") or {}).get("source")
            if not src or src == CARD_REGIME_SOURCE:
                continue        # 老卡缺锚 = 提示级, 且不会凭空变新鲜
            cr = agent.get("current_regime") or {}
            out.append(f"{t} [{cr.get('name') or '?'} source={src} "
                       f"since={cr.get('since') or '?'}] — anchor_date() 正回落到 "
                       f"thesis.last_reviewed={anchor_date(agent) or '?'} "
                       f"(没有任何一步写过这个日期)")
    return out


def anchor_date(agent: Optional[dict]) -> Optional[str]:
    """这套结构是哪天画的。优先卡片显式 anchored_on, 退到 thesis.last_reviewed。

    ⚠️ 这个 fallback 对**机器装上来的 zones**会凭空造出新鲜度 —— 判据与出口见
    `cards_missing_zone_anchor()`。这里不改行为 (老卡靠它才有锚可读)。
    """
    agent = agent or {}
    z = agent.get("zones") or {}
    a = z.get("anchored_on")
    if isinstance(a, dict) and a.get("date"):
        return str(a["date"])[:10]
    lr = ((agent.get("thesis") or {}).get("last_reviewed") or "")
    return str(lr)[:10] or None


def gate_staleness(agent: Optional[dict], today: str,
                   zone_stale_reasons: Optional[List[str]] = None) -> List[str]:
    """gate 的**硬**staleness (会导致 STALE verdict)。空 = fresh。

    zone_stale_reasons 非空 ⇒ gate 一律 stale (第 1 层同步: 同生死)。

    ⚠️ 缺 anchored_on **不在这里**—— 见 gate_audit_gaps()。那是审计字段缺失
    (之前画的卡全都没有), 是文档欠账不是"结构已失效"的证据。
    混进来的后果实测过: 全部 5 张卡当场 STALE, 当晚入场计划整段空掉 ——
    一个判别式因为迁移而拦下所有票, 正是它该防的那类错误的镜像。
    """
    out: List[str] = []
    gates = gates_of(agent)
    if not gates:
        return out

    if zone_stale_reasons:
        out.append(f"zone 已 stale ({zone_stale_reasons[0]}) ⇒ gate 同步失效 —— "
                   "两者锚在同一份结构上, 不存在 zone 过期而 gate 仍有效")

    # 第 2 层: 同锚 (单一判别式, 与 brief 的 INFRA:gate_anchor_desync 同一份)
    out.extend(gate_anchor_desync(agent))
    td = _d(today)
    for g in gates:
        ga = g.get("anchored_on")
        if not isinstance(ga, dict) or not ga.get("date"):
            continue                                    # → gate_audit_gaps
        vu = _d(ga.get("valid_until"))
        if vu and td and vu < td:
            out.append(f"gate {g['level']:g} ({g['where']}) valid_until "
                       f"{ga.get('valid_until')} 已过期")
    return out


def gate_anchor_desync(agent: Optional[dict]) -> List[str]:
    """同锚层单独拎出来 (): gate 自带日期 ≠ zone 画卡日期 = 只重画了一半。

    从 `gate_staleness` 里抽出来是因为它有了**第二个消费者**—— brief 的
    `INFRA:gate_anchor_desync`。`gate_staleness` 只被 entry_guard 调, 而
    entry_guard 只评估 watchlist ⇒ **持仓卡的同锚破裂此前完全没有出口**
    (实测: MU 在 watchlist 判 STALE 看得见, 同笔造成的 MRVL 镜像
    ——zones 8/11 而 entry_gate 8/06—— 一声不响)。

    🚨 抽出来而不是在 brief 里再写一遍: 同一个概念两份判别式是本仓的惯犯
    (GOOGL 卡被清空 6 天 = `held` 判据在 sync 与 migrate 各存一份)。

    缺 anchored_on 的 gate 跳过 (→ `gate_audit_gaps`), zone 无锚时整张跳过
    ——  之前的卡全都没有, 混进判定实测会让全部卡当场作废。
    """
    zdate = anchor_date(agent)
    zd = _d(zdate)
    if not zd:
        return []
    out: List[str] = []
    for g in gates_of(agent):
        ga = g.get("anchored_on")
        if not isinstance(ga, dict) or not ga.get("date"):
            continue
        gdate = str(ga["date"])[:10]
        gd = _d(gdate)
        if gd and abs((gd - zd).days) > ANCHOR_DRIFT_TOLERANCE_DAYS:
            out.append(f"gate {g['level']:g} 锚于 {gdate}, zone 锚于 {zdate} —— "
                       "只重画了一半, gate 与 zone 必须同一次画")
    return out


def cards_with_gate_anchor_desync(*docs: Optional[dict]) -> List[str]:
    """全书扫同锚破裂 —— brief 的 `INFRA:gate_anchor_desync` 出口。

    与 `cards_missing_next_gate` 同一套卡片选取 (活跃 / 受管 / 非期权 / 有 zones),
    判别式复用 `gate_anchor_desync` (不是第二份实现)。

    为什么需要它: `gate_staleness` 的唯一消费者 entry_guard **只评估 watchlist**,
    而同锚破裂在持仓卡上同样会发生 —— 而且持仓卡的 gate 正是加仓授权的来源
    (促升卡 cap 0, 额度由 next_regime gate 达标转移授予)。源案例 :
    884411d 一笔造出两个方向相反的破裂, watchlist 那张 (MU) 判 STALE 天天喊,
    持仓那张 (MRVL) 零告警。
    """
    from .holdings import is_managed                        # noqa: PLC0415  同层
    out: List[str] = []
    for doc in docs:
        for t, body in sorted(((doc or {}).get("tickers") or {}).items()):
            if body.get("status") == "closed" or not is_managed(body):
                continue
            if ((body.get("instrument") or {}).get("type")) == "option":
                continue        # 期权卡无自有 zones/gate, 锚在 underlying 上
            agent = body.get("agent") or {}
            if not (agent.get("zones") or {}):
                continue
            for r in gate_anchor_desync(agent):
                out.append(f"{t} {r}")
    return out


def gate_audit_gaps(agent: Optional[dict]) -> List[str]:
    """审计字段欠账 —— **提示, 不判 STALE**。

    没有 anchored_on 就无法回答"凭什么是这个数"、也无法在下次重画时判断证据
    标准有没有被悄悄放宽。但它不是"这个 gate 已经失效"的证据 —— 下次跑
    /trading-analysis 时用 --anchored-on/--anchor-basis 补上即可。
    """
    missing = [g for g in gates_of(agent)
               if not (isinstance(g.get("anchored_on"), dict)
                       and g["anchored_on"].get("date"))]
    if not missing:
        return []
    lv = ", ".join(f"{g['level']:g}" for g in missing)
    return [f"📋 gate {lv} 无 anchored_on — 凭什么是这个数无法审计。"
            "下次 /trading-analysis 重画时补 --anchored-on/--anchor-basis"]


def gate_coherence(agent: Optional[dict]) -> List[str]:
    """第 3 层: 自洽。gate 按定义是**下一个状态的入口**, 不能和本 regime 的边界倒挂。

    entry_guard 早就在注释里写过这条 (`gate 不能和本 regime 的 no_chase 比大小:
    它按定义在 no_chase 之上`), 但没有任何地方检查。重画 gate 而不重画 zone 时,
    倒挂是最先出现的症状 —— 例如 NBIS 若把 confirmed 锚重画到 198 而 no_chase
    仍是 7/21 的 229, gate 就掉到了"本 regime 都不许追"的价位之下。
    """
    agent = agent or {}
    z = agent.get("zones") or {}
    out: List[str] = []
    nc, stop = z.get("no_chase_above"), z.get("stop_level")
    for g in gates_of(agent):
        lvl = g.get("level")
        if not isinstance(lvl, (int, float)):
            continue
        # 严格小于才算倒挂。gate == no_chase 是**正常设计**—— "在追价上限处确认
        # 突破"(NVDA 卡: gate 214 / no_chase 214, label "no-chase 突破确认→重锚再评")。
        # 首版写 <= 把它判成不自洽, 差点让一张健康的卡整天 STALE。
        if g["kind"] == "up" and isinstance(nc, (int, float)) and lvl < nc:
            out.append(f"⚠️ 上行 gate {lvl:g} < no_chase_above {nc:g} —— 倒挂。"
                       "gate 是下一个状态的入口, 不能低于本 regime 的追价上限 "
                       "(否则 gate 触发时你还在'可以追'的区间里); 多半是只重画了 gate 没重画 zone")
        if g["kind"] == "down" and isinstance(stop, (int, float)) and lvl < stop:
            out.append(f"⚠️ 修复 gate {lvl:g} < 本 regime stop {stop:g} —— 倒挂: "
                       "还没跌到那儿就先谈修复")
    return out


def structure_lines(agent: Optional[dict], today: str,
                    zone_stale_reasons: Optional[List[str]] = None) -> List[str]:
    """播报行: 结构锚定日 + gate staleness + 自洽告警。"""
    out: List[str] = []
    ad = anchor_date(agent)
    gs = gate_staleness(agent, today, zone_stale_reasons)
    co = gate_coherence(agent)
    gaps = gate_audit_gaps(agent)
    if ad and (gs or co):
        out.append(f"🧭 结构锚定于 {ad} — gate 与 zone 必须同一次画、同步失效")
    out.extend(f"⚠️ gate stale: {r}" for r in gs)
    out.extend(f"   {r}" for r in co)
    out.extend(f"   {r}" for r in gaps)       # 提示级, 不参与 STALE 判定
    return out
