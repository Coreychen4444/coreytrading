#!/usr/bin/env python3
"""entry_guard.py — 入场闸门 + 每日入场执行计划 (exit_guard 的入口侧对称物)

为什么存在 (, 源 = exit_rules_backtest 三个数据发现):
  1. 入场分类有预测力但从未绑 size — early_risky 4/4 全亏 -$3,843, probe 净 -$2,570,
     confirmed +$5,165 (→07 全样本);
  2. IV 闸门有数据无钩子 — INTC 6/25 买 200C 当天 iv_history 已记录 ATM IV 95.3%,
     canonical 闸门 (>70%ile 应 sell premium) 无人在下单时刻查;
  3. decisions.jsonl 从未落盘一行 — 决策工具链建好了但不在下单路径上。
  出口已机械化 (exit_guard 每 6h 巡逻), 入口只有对话触发的 checklist — 本模块补对称。

角色: **只读, 不下单**(用户 手动挂单)。每日盘前 (overnight-briefing) 对 watchlist
候选输出【具体执行计划】: verdict + 3 档 ladder 价位 + size 上限 (regime cap ×
gate 预算取小) + 预设止损 (结构位单线, 2N 为 sizing 内部量), 直接进 TG 简报照着挂单。
PLAN 计划用 --log 落 decisions.jsonl (变动才落一行 — 校准闭环 + 重锚审计轨迹)。

管辖边界: watchlist 入场 (新仓/重进)。**档位不再由域决定**——  二次定版
起入场侧与加仓侧读同一个 `sizing.gate_count(card)` (数卡上爬过几个 gate), 所以一张
连过两道 gate 却没买过的候选卡拿的就是 gate2 的额度。已持仓的加仓归 overnight 手册
优先级 7 + today_group grant, 不重复。--event-day 自 v3 起仅播报不降级。

技术面优先 v3 (用户 directive) + 分类轴退役 (,
classification_removal_design.md): 授权判定只用价格结构, 授权量只有 regime cap
一个来源 (二值 {0, NOTIONAL_CAP}), sizing 主尺 = gate 预算 (gate1 2.5% / gate2 4%
/ gate3 5% NAV risk, 累计上限减当前敞口)。
财报·thesis·订单·宏观消息一律降为 [FYI] 情报层提醒 — 不降级、不拦截、不参与制定。

Verdict 语义 (用户: 移除 GO/WAIT —— 挂的是 zone 限价单不是市价单,
"现价相对 zone 在哪"不驱动动作, verdict 只回答: 这张卡今天能不能挂单):
  PLAN(gateN)    有资格 → 出 zone 限价单计划 (ladder+股数+止损), 照挂。现价在
                 waiting/trim/no_chase 之上只是位置信息行, 不再拦截
  VETO           今天不挂单: regime cap 0 / 破 stop / z2 下沿以下 (限价在市价上方
                 = 即时成交不是回踩单) / 结构修复卡 gate 未成立 / 结构未修复
                 (structure_repair_veto — 旧「降级 probe」的接替者,)
  STALE          zone/gate 过期或不自洽 → 先重画 (拒绝旧结构)
  NEEDS_CARD     无卡 / 无数值 zone / 转移后待补画 → 先 /trading-analysis

CLI:
    .venv/moomoo/bin/python3 quant/decision/entry_guard.py [--json] [--log] [--event-day]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 直接 `python3 quant/<层>/<模块>.py` 时让 `quant.*` 可 import

from quant.core import env, technical  # noqa: F401  (.env → os.environ)

import argparse
import json
import re
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from quant.core import book, dow, earnings  # noqa: E402  (NAV/ATR20/组合占用 + 财报反应日缩仓 + 短期道氏排序键)
from quant.core.holdings import is_traded  # noqa: E402  (参考票判据单一真源)
from quant.data import iv_logger  # noqa: E402  (期权 IV 硬门槛的 percentile 源)
from quant.core.io_json import append_jsonl, iter_jsonl, read_json  # noqa: E402
from quant.core.num import coerce_float  # noqa: E402
from quant.core.paths import (# noqa: E402
    DECISIONS_PATH, EXIT_GUARDS_PATH, IV_HISTORY_DIR,
    SKHY_PREMIUM_PATH, WATCHLIST_PATH,
)
from quant.core.timeutil import et_today, now_et_iso  # noqa: E402
from quant.core.structure import (# noqa: E402
    structure_repair_veto, gate_coherence, gate_staleness, gates_of, structure_lines,
    zone_bands, zone_mech_stale, zone_position,  # 真源在 core (sync/lot_reconcile 同读); 此处 re-export
)
from quant.core import structure as structure_stale  # noqa: E402  (staleness 常量 re-export 源)
from quant.core.sizing import (  # noqa: E402
    AT_RISK_CAP_FRAC, OPTION_ATRISK_LOSS_FRAC, OPTION_MIN_GATE,
    binding_stop, constraint_lines, entry_cap, option_premium_cap_frac,
    granting_gate_level, long_call_iv_gate,
    read_constraints, regime_cap_frac,
    gate_count, gate_risk_cap_frac, regime_gate, regime_lines, share_ladder, usage_lines,
)

# 卡片 constraints.vehicle 落在这些值时, PLAN 必须带合约级参数 (option_plan)。
# "stock_first" 不在内 —— 它的意思是先用正股, 期权是后备。
OPTION_VEHICLES = {"leaps", "spread"}

PARAMS = {
    # 授权量 = current_regime.cap_pct_nav 单一来源 (分类轴退役,
    # classification_removal_design.md) —— 本表不再存任何 cap 数; sizing 主尺 =
    # gate 预算 (sizing.GATE_RISK_CAPS), 档位 = sizing.gate_count(card)。
    # IV 闸门: 起单腿 long call 的**硬门槛**住 sizing.long_call_iv_gate
    # (percentile <40 可信时 / 绝对 ATM IV <50% 兜底 / 无数据不放行), 本表不再存 IV 数
    # (旧 iv_hard_no_single_leg 80 / iv_soft_half_size 60 两条播报带同批退役)。
    # Ladder (greedy-bid trap 3 档强制; parabolic 锚 = entry_execution 校准)
    #  回测 (analytics/entry_ladder_backtest.py, 2237 样本): 前置 50/30/20
    # 全 regime 均值 +0.5~0.9pp vs 40/35/25, 中位/尾部不劣; 未成交档每 2 交易日重锚只升不降
    "ladder_weights": (0.50, 0.30, 0.20),
    "parabolic_anchors": (0.98, 0.93, 0.88),   # close× / 5d high× / 10d high×
    "normal_t1_disc": 0.995,
    # 修复 gate 单日强势突破 (ladder_entry 回测裁决 k=0.5; 适用域+锚
    #  用户 收窄「仅针对单日强势突破 gate 后的参与权, 其他照常 zone」):
    # 锚**冻结在 close@since**— 首档 = 突破日收盘 (买那一天的参与权), 二三档 =
    # close@since − {0.5,1.0}×ATR20@since, **地板 = 刚收复的 gate**。适用签名 =
    # repair_gate.fastlane 标记 (转移机落卡, 判别式同一份; ×2 慢磨 = False)。
    # 地板不是装饰: 无地板版有 35.3% 的档位成交落在 gate 之下, 那里离 stop 极近,
    # 会靠缩小分母虚增 R (实测加地板后 R 反而 +0.470→+0.480, 且概念上也对 ——
    # 不买在刚收复的 gate 之下)。
    "repair_reclaim_offsets": (0.0, 0.5, 1.0),   # 低于 close@since 几个 ATR20@since
    "repair_reclaim_days": 10,                   # 限价挂单有效期 (交易日)
    # N-unit risk 分数  起随分类走 schedule (risk_allocation): 真源 =
    # sizing.ROUND_RISK_FRACS — confirmed R1 2.5% / probe flat 0.75%。本处不再存数。
    "stop_n_mult": 2.0,
    "zone_stale_days_reviewed": 14,
    "atr_period": 20,
}

# zone-maintenance breakout-stale (上行) 确认口径「连续 ≥2 交易日」。
# 判别式内核已下沉 core.structure.zone_mech_stale (单一真源 —— 入场侧、白名单与
# 画线读同一份), 本模块 zone_staleness 委托之; 常量 re-export 保证三方
# (core/eg/pb) 恒等, pin 测试照钉。portfolio_brief.zone_staleness 手搓孪生保留
# (历史有意重复) —— 改口径改 core。
STALE_CONFIRM_DAYS = structure_stale.STALE_CONFIRM_DAYS
# ① 破位 stale 不等确认 —— 收盘 < z2 下沿**当日**即 stale。破位后多等一天没有
#   意义, 挂单语义已反转 (带内限价 BUY = 挂上即市价成交在破位区); 上行 breakout
#   保持 ×2 (挂单只是不成交, 无执行危害, 确认避免 churn) —— 不对称是有意的。
# ② 波动 stale 用 per-ticker ATR 尺 —— |单日收收| > 2×ATR14。固定百分比一把尺
#   量不了两个池: 高波动票的 ±10% 是日常噪音, 低波动票的同一个数是地震。
#   倍数校准 (180 日 × 15 票 ≈ 2,490 票日): 2.0× 触发率 1.5% ≈ 每票两月一次,
#   命中全为真结构日; 1.5× 吵三倍 (4.7%), 2.5× 开始漏真事件。
STALE_BREAKDOWN_DAYS = structure_stale.STALE_BREAKDOWN_DAYS
STALE_VOL_ATR_MULT = structure_stale.STALE_VOL_ATR_MULT


# ── 纯计算层 (tests/decision/test_entry_guard.py 覆盖) ───────────

def momentum_mode(rows: List[dict]) -> str:
    """zone-maintenance momentum 三问的机械代理 (与 exit_rules_backtest 同口径):
    Q1 6m≥+150% 或 YTD≥+200%; Q2 近20日单日+10%; Q3 距窗口高点<10% → 全 YES = parabolic。"""
    closes = [coerce_float(r.get("close")) for r in rows]
    closes = [c for c in closes if c]
    if len(closes) < 21:
        return "normal"
    c = closes[-1]
    q1 = len(closes) > 126 and closes[-127] > 0 and c / closes[-127] - 1 >= 1.50
    if not q1:
        ytd_base = None
        for r in reversed(rows):
            # ET 年份: SGT 的 1/1 在 ET 还是去年 12/31, 用本地年会取错 YTD 基准
            if str(r.get("time", ""))[:10] < f"{et_today()[:4]}-01-01":
                ytd_base = coerce_float(r.get("close"))
                break
        q1 = bool(ytd_base) and c / ytd_base - 1 >= 2.0
    q2 = any(closes[i] / closes[i - 1] - 1 >= 0.10
             for i in range(max(1, len(closes) - 20), len(closes)))
    q3 = c >= 0.90 * max(closes)
    return "parabolic" if (q1 and q2 and q3) else "normal"


def _add_weekdays(d: date, n: int) -> date:
    while n > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


def reclaim_gate_lines(zones: Optional[dict], rows: List[dict], today: str) -> List[str]:
    """结构化站稳 gate (zones.reclaim_gates: [{level, days, label}]) → 明确 ET 日历行。
    计数用已完成日线 (kline 日期 = ET 交易日); 收盘跌破 → 计数清零, 第 1 日重新定义。
    最早成立日按周一至周五推算, 遇美股休市顺延 (不查假日表)。量比等附加条件在 label 里
    人工核, 本函数只数收盘价。源需求: 用户— gate 必须绑 ET 日历否则不知道哪天挂单。"""
    gates = (zones or {}).get("reclaim_gates")
    if not isinstance(gates, list) or not gates or not rows:
        return []
    closes = [(str(r.get("time", ""))[:10], coerce_float(r.get("close"))) for r in rows]
    closes = [(t, c) for t, c in closes if t and c]
    if not closes:
        return []
    as_of = closes[-1][0]
    try:
        td = date.fromisoformat(today)
        pending = today > as_of and td.weekday() < 5  # 今日 ET bar 未落库 (盘中/收盘后未同步)
    except ValueError:
        td, pending = None, False
    out: List[str] = []
    for g in gates:
        if not isinstance(g, dict):
            continue
        lvl, need = coerce_float(g.get("level")), int(g.get("days") or 2)
        if not lvl or need < 1:
            continue
        streak: List[str] = []
        for t, c in reversed(closes):
            if c > lvl:
                streak.append(t)
            else:
                break
        streak.reverse()
        k = len(streak)
        tag = f"⏱ 止跌线 收盘>${lvl:g}" + (f" [{g.get('label')}]" if g.get("label") else "")
        got = "".join(f"第{i+1}日 {d[5:]}✓ " for i, d in enumerate(streak))
        if k >= need:
            days = ", ".join(d[5:] for d in streak[-need:])
            out.append(f"{tag}: ✅ {need}/{need} 已成立 ({days} ET)")
        elif pending and td:
            rem = need - k - 1
            eta = today if rem <= 0 else _add_weekdays(td, rem).isoformat()
            out.append(f"{tag}: {k}/{need} (截至 {as_of[5:]} ET 收盘) — {got}"
                       f"今日 {today[5:]}(ET) 收盘达标 = 第{k+1}日, 最早成立 {eta[5:]}(ET) 收盘; 中途收盘跌破清零")
        else:
            eta = _add_weekdays(date.fromisoformat(as_of), need - k).isoformat()
            out.append(f"{tag}: {k}/{need} (截至 {as_of[5:]} ET 收盘) — {got}"
                       f"还需 {need - k} 个收盘, 最早成立 {eta[5:]}(ET) 收盘; 中途收盘跌破清零")
    return out


def _gate_streak(rows: List[dict], level: float) -> List[str]:
    """连续收在 level 之上的日期 (最近一段, 跌破即断)。"""
    closes = [(str(r.get("time", ""))[:10], coerce_float(r.get("close"))) for r in rows]
    closes = [(t, c) for t, c in closes if t and c]
    streak: List[str] = []
    for t, c in reversed(closes):
        if c > level:
            streak.append(t)
        else:
            break
    streak.reverse()
    return streak


def _position_line(pos: str, close: float, zb: dict) -> str:
    """现价相对 zone 的位置 —— 信息行, 不驱动 verdict (起挂单授权与
    市价位置解耦: 限价单挂在 zone, 现价在哪只影响回踩距离)。

    **当前 regime 的 no_chase 无条件印出来**(用户)。此前它只在
    `above_no_chase` 那一支出现 = 被突破了才说 —— 而 `next_regime` 的整套仓位层
    (含它自己的 no_chase) 是**无条件**渲染的。于是块上唯一看得见的 no_chase
    是**还没生效的那条**。

    源案例 COHR : 当前 regime no_chase **339**, next_regime no_chase
    **374**, 差 10%; 8/11 收盘 328.57 判「等待区」→ 339 静默, 块上只剩 374。
    而当天现价 351.62 (+7.0%) 正好夹在两条中间 —— 快读会把还没生效的 374 当成
    现在那条线。`gate_ladder_report` 一直是印的 (`📍 等待区: ... | no_chase 339`),
    两个报表对同一张卡说的话不一样; 这里对齐它的写法。
    """
    nc = zb.get("no_chase")
    tail = f" | no_chase {nc:g}" if nc else ""
    if pos == "above_no_chase":     # 这一支自己就点名了它 → 提前 return, 不拼 tail
        return (f"📍 位置: 现价高于 no_chase {zb['no_chase']:g} — 不追市价 (Rule #11), "
                "zone 限价单照挂")
    if pos == "trim":
        return "📍 位置: 现价在 trim 区 — 减仓评估归退出侧, zone 限价单照挂" + tail
    if pos == "waiting":
        dist = (close - zb["z1"][1]) / close * 100
        return f"📍 位置: 等待区, 距 z1 上沿 {dist:+.1f}%" + tail
    if pos == "gap_z1_z2":
        return "📍 位置: z1/z2 间隙 — z1 档在上方, z2 档在下方待命" + tail
    return f"📍 位置: {pos.replace('zone', 'z')} 内 — 首档即时可成交区" + tail


FLOOR_GAP_ATR = 0.25   # round-2b (): stop 距 gate < 0.25N = whipsaw 语义告警线


def floor_gate_gap_lines(agent: dict, atr: Optional[float]) -> List[str]:
    """round-2b 裁决 (用户 批准): 修复 regime 的地板 = gate − 0.5×ATR20。

    stop 贴 gate (距离 < 0.25N) = whipsaw 机器 — 回测中 bfloor×1 是全场最差止损变体,
    bfloor_buf 唯一双主格合格 (gate_backtest2b)。**advisory only, 不改判**
    ("新能力先只说话不动手"): 只播报, 不参与 STALE/VETO, 重画时按新约定落地板。
    """
    if not atr or atr <= 0:
        return []
    stop = coerce_float((agent.get("zones") or {}).get("stop_level"))
    if stop is None:
        return []
    out = []
    for g in gates_of(agent):
        lvl = coerce_float(g.get("level"))
        if lvl is not None and abs(stop - lvl) < FLOOR_GAP_ATR * atr:
            out.append(f"⚠️ 地板贴 gate: stop {stop:g} 距 {g.get('kind')} gate {lvl:g}"
                       f" < 0.25×ATR20 — whipsaw 语义 (round-2b), 重画时地板 = gate − 0.5N")
    return out


def next_regime_lines(agent: dict, rows: List[dict]) -> Tuple[List[str], bool]:
    """agent.next_regime — gate 是**状态转移锚点**, 不是本 regime 的入场位。

    模型 (用户): 一张卡不是"一套 zone + 一串 gate", 是若干 regime,
    每个 regime 自带完整 zone 集, gate 触发整套上移。上一档 gate 位 = 下一档
    regime 的 stop —— 入场侧棘轮, 与 exit_guard 的利润棘轮同构。

    这也是为什么 gate 不能和本 regime 的 no_chase 比大小: gate 按定义在
    no_chase 之上 (它是下一个状态的入口)。混在一个坐标系里就会出现"gate 成立
    的那一刻必然 above_no_chase → VETO"的死锁 (INTC 卡原始写法)。

    返回 (显示行, gate 是否已成立)。×N 收盘成立后由 regime_transition **自动切换**
    (用户 裁决 A —— 此前 watchlist 不在扫换域, "约定由文件位置决定",
    8/4 首夜三笔达标未切现形)。provisional 带切换即标 pending_repaint, 补画前
    不授权入场; 达标收据与新授权见盘后转移简报。
    """
    nr = (agent or {}).get("next_regime")
    if not isinstance(nr, dict) or not rows:
        return [], False
    gate = nr.get("entry_gate") or {}
    lvl, need = coerce_float(gate.get("level")), int(gate.get("days") or 2)
    if not lvl or need < 1:
        return [], False

    name = nr.get("name") or "next"
    streak = _gate_streak(rows, lvl)
    k = len(streak)
    z = nr.get("zones") or {}
    prov = set(nr.get("provisional") or [])
    fired = k >= need

    # 语义分层 (用户 定): gate 行只说结构事实; probe/confirmed 是仓位层
    # 词汇, 档位由结构判别式逐笔判 (v3  纯结构单腿; 旧两腿公式的 thesis 半已废),
    # 不由单一 gate "解锁"。
    head = f"🎚 regime 转移线 收盘>${lvl:g} ×{need} → [{name}] 生效"
    if fired:
        out = [f"{head}: ✅ {k}/{need} 已成立 ({', '.join(d[5:] for d in streak[-need:])} ET)",
               "   🔁 自动切换域 (regime_transition,  裁决 A) — 本行仍显示"
               " = 切换在途 (等 postclose/briefing 的 regime 步), 勿手动改卡;"
               " provisional 带补画前不授权入场"]
    else:
        out = [f"{head}: {k}/{need} — 还需 {need - k} 个收盘, 中途跌破清零"]

    def _fmt(key, label):
        v = z.get(key)
        if v is None:
            return None
        txt = f"{v[0]:g}-{v[1]:g}" if isinstance(v, list) else f"{v:g}"
        return f"{label} {txt}" + ("(provisional)" if key in prov else "")

    parts = [x for x in (_fmt("accumulation_zone_1", "z1"), _fmt("stop_level", "stop"),
                         _fmt("no_chase_above", "no_chase"), _fmt("trim_zone", "trim")) if x]
    if parts:
        out.append("   切换后仓位层: " + " | ".join(parts))
    if nr.get("cap_pct_nav"):
        out.append(f"   预算档 cap {float(nr['cap_pct_nav']) * 100:g}% NAV — 股数按 floor(cap/price) 降级表切档")
    return out, fired


def zone_staleness(zb: dict, thesis: dict, rows: List[dict], today: str) -> List[str]:
    """staleness 触发清单 (zone-maintenance 时间/价格触发的机械子集)。空 = fresh。

    起委托 core.structure.zone_mech_stale (单一真源) —— zb 键名
    (z2/trim/valid_until) 与原始 zones 键名 core 版都认, 输出文本逐字同源。
    """
    return zone_mech_stale(zb, thesis, rows, today)


def reclaimed_gate_of(agent: dict, today: str,
                      rows: Optional[List[dict]] = None) -> Optional[dict]:
    """repair_reclaim 参与权阶梯的适用判定 + 冻结锚。None = 不适用 (照常 zone ladder)。

    返回 {"gate", "anchor_close", "anchor_atr", "since"} — build_ladder 用冻结锚。

    判据四条，全部必须成立：
      ① `current_regime.since` 在 repair_reclaim_days 个**交易日**内 (挂单有效期,
         ladder_entry 采纳参数; ledger V3 : 交易日口径, 数 kline bars)。
      ② `last_regime.repair_gate.level` 存在 —— 刚被清掉的那道线。
      ③ **repair_gate.fastlane 标记**—— 转移机自己判的「单日强势突破」(首日+幅度
         +量腿+①域四条, `regime_transition._fastlane`), apply_transition 落卡。
         ×2 慢磨转移落 False; 标记缺失 (fastlane 前的历史转移) = 不给。
         消费侧不得从 closes 重推弱化版签名；判别式必须与转移机同一份。
      ④ **图没有在转移之后重画过** (`zones.anchored_on.date` ≤ since) —— 冻结锚
         与地板都是转移那天的结构产物; 全图重画 = 结构被重新评估, 入场带归新
         z1/z2 (照常 zone ladder)。否则旧冻结锚可能高于重画后的 no-chase，
         让限价单在错误的结构图上立即成交。

    锚**冻结在 close@since / ATR20@since**：参与权是突破当日的价格，
    不随后续上涨漂移。
    """
    cur = (agent or {}).get("current_regime") or {}
    since = str(cur.get("since") or "")[:10]
    if not since or not rows or str(today)[:10] < since:
        return None
    z_anchor = str(((agent.get("zones") or {}).get("anchored_on") or {}).get("date") or "")[:10]
    if z_anchor and z_anchor > since:
        return None       # ④ 转移后全图重画过 → 新图接管, 照常 zone ladder
    rg = ((agent.get("last_regime") or {}).get("repair_gate") or {})
    gate = coerce_float(rg.get("level"))
    if gate is None or not rg.get("fastlane"):
        return None
    dated = [(str(r.get("time", ""))[:10], r) for r in rows]
    idx = next((i for i, (t, _r) in enumerate(dated) if t == since), None)
    if idx is None:
        return None
    elapsed = sum(1 for t, _r in dated if since < t <= str(today)[:10])
    if elapsed > PARAMS["repair_reclaim_days"]:
        return None
    c_since = coerce_float(dated[idx][1].get("close"))
    if c_since is None:
        return None
    atr_series = technical.atr([r for _t, r in dated[:idx + 1]], PARAMS["atr_period"])
    a_atr = atr_series[-1] if atr_series else None
    if not a_atr:
        return None
    return {"gate": gate, "anchor_close": c_since, "anchor_atr": a_atr, "since": since}


def build_ladder(mode: str, close: float, rows: List[dict], zb: Optional[dict],
                 atr: Optional[float] = None,
                 reclaim: Optional[dict] = None) -> List[float]:
    """3 档 ladder 价位 (降序)。

    parabolic      = 现价×0.98 / 5d高×0.93 / 10d高×0.88
    repair_reclaim = **冻结锚**close@since / −0.5N / −1.0N (N=ATR20@since),
                     每档地板 = 刚收复的 gate — 参与权不随后续上涨漂移
    normal         = min(close×0.995, z1上沿) / z1下沿 / z2中值 (close 已在 z2 → 沿 z2 排)
    """
    if mode == "repair_reclaim" and reclaim:
        a_close, a_atr = reclaim["anchor_close"], reclaim["anchor_atr"]
        reclaimed_gate = reclaim["gate"]
        offs = PARAMS["repair_reclaim_offsets"]
        tiers = [max(a_close - k * a_atr, reclaimed_gate) for k in offs]
    elif mode == "parabolic":
        highs = [coerce_float(r.get("high")) for r in rows]
        highs = [h for h in highs if h]
        h5 = max(highs[-5:]) if len(highs) >= 5 else close
        h10 = max(highs[-10:]) if len(highs) >= 10 else close
        a, b, c = PARAMS["parabolic_anchors"]
        tiers = [close * a, h5 * b, h10 * c]
    else:
        if not zb:
            return []
        z1, z2 = zb["z1"], zb["z2"]
        z2_mid = (z2[0] + z2[1]) / 2
        if close < z1[0]:   # 已深入 z1 下方/z2: 贴现价往 z2 排
            tiers = [close * PARAMS["normal_t1_disc"], max(z2[1], z2_mid), z2[0]]
        else:
            tiers = [min(close * PARAMS["normal_t1_disc"], z1[1]), z1[0], z2_mid]
    # 单调降序 + 去重 (相邻档差 <0.5% 时下压)
    out: List[float] = []
    for t in tiers:
        t = round(t, 2)
        if out and t >= out[-1] * 0.995:
            t = round(out[-1] * 0.99, 2)
        out.append(t)
    if mode == "repair_reclaim" and reclaim:
        # ⚠️ 上面那句"下压 1%"会把**贴在地板上的档推到 gate 之下**—— 正是地板要防的事。
        # (tiers=[110,100,100] 时第三档会变成 99.0)。所以这里重新夹回地板, 并丢掉
        # 因此变得重复的档: **宁可少一档, 不可买在刚收复的 gate 之下**。
        floor = round(reclaim["gate"], 2)
        seen, clamped = set(), []
        for t in out:
            t = max(t, floor)
            if t not in seen:
                seen.add(t)
                clamped.append(t)
        return clamped
    return out


def size_caps(nav: Optional[float], atr: Optional[float],
              t1: Optional[float], agent: Optional[dict] = None,
              usage: Optional[dict] = None,
              event_shrink: Optional[float] = None,
              risk_stop: Optional[float] = None,
              power_room: Optional[float] = None,
              exposure: Optional[float] = None,
              gate_no: int = 1) -> dict:
    """六条约束取小者为 binding。金额均为 ladder 全档合计上限。

    全部走 `core.sizing.entry_cap`（**实际 cap 的单一真源**,  起
    today_group 的白名单 grant 与 price_reminder 的 z1 备注读同一份）；本函数
    只在其上做**期权的单位分层**（保费 vs 标的名义, 见下）与播报字段整理。
    ① regime cap = 唯一授权来源 (分类轴退役, cap 二值 {0, NOTIONAL_CAP})。
    ② **gate 预算**= sizing 主尺 (gate1 2.5% / gate2 4% / gate3 5% NAV risk
    ÷ stop 距离 — 真源 sizing.GATE_RISK_CAPS) 与 ③ at-risk 天花板 (5% NAV /
    真实 stop 距离, N1 ; 持仓侧由 at_risk_breach_signal 按同一常量管)。
    ③ 定位是**后备**: ②的上界就是 T = AT_RISK_CAP_FRAC ⇒ ② ≤ ③ 恒成立。

    `usage` = `core.book.load_usage()` 的产物。**不传 = 当作空书**（首次入场口径），
    但那会高估可用额度 —— 调用方该传。
    """
    # ② gate 预算 —— 档位由调用方按 `sizing.gate_count(card)` 传入。
    # ⚠️  二次定版前这里**写死 1**, 理由是"watchlist 入场 = 新 campaign"。
    # 那个理由把"没开过仓"错读成"没爬过 gate": 一张连过两道 gate 却一股没买的
    # 候选卡 (LITE 716→860), 解锁的 4% 会被整档丢掉 —— 而"前面没用完的额度跟着
    # 过来"正是 用户 要的东西。判据换成数卡上的 gate 后, 入场侧与加仓侧读同一个
    # `gate_count`, 平仓/破位由它的参与流锚点归零, 不需要域特判。
    ac = entry_cap(nav, agent, atr=atr, price=t1, stop=risk_stop,
                   usage=usage, event_shrink=event_shrink, gate_no=gate_no,
                   power_room=power_room, exposure=exposure)
    nunit_cap, at_risk_cap = ac["n_unit_cap"], ac["at_risk_cap"]
    final, binding = ac["final"], ac["binding"]
    live = [(k, v) for k, v in (("regime", ac["regime_cap"]),
                                ("n_unit", nunit_cap), ("at_risk", at_risk_cap),
                                ("shock_budget", ac["shock_budget_cap"]),
                                ("gross", ac["gross_cap"])) if v is not None]
    # 单位分层 (): 正股上各条是同一种钱, 期权上**不是**——
    #   regime cap = 你花出去多少 (保费)
    #   n_unit / 冲击预算 / gross = 标的名义敞口多少
    # 所以给 option_plan 的必须是两个数, 由它用 delta 折算后取小; 直接把 `final`
    # 当保费花会让名义敞口数倍于上限 (见 sizing.notional_to_premium_cap)。
    # ③ at_risk (标的 stop 口径) **不进 notional**(复查修): §b1 明文
    # 「标的 stop 不给期权折扣」—— 期权的 at-risk 走保费口径 at_risk_premium_cap
    # (option_plan 三者取小的第三条), 标的口径折保费 = 拿标的 stop 距离当期权风险尺,
    # 正是 §b1 否定的语义。正股不受影响 (③ 仍在 final 的 min 里)。
    # ⑥ power 是**现金**约束: 期权上花的是保费, 与 regime cap 同单位 → 不进 notional
    notional = [v for k, v in live if k not in ("regime", "at_risk", "power")]
    u = usage or {}
    return {"regime_cap": ac["regime_cap"], "n_unit_cap": nunit_cap,
            "at_risk_cap": at_risk_cap,
            "shock_budget_cap": ac["shock_budget_cap"], "gross_cap": ac["gross_cap"],
            "power_cap": ac["power_cap"], "gate_no": ac["gate_no"],
            "budget_room": ac["budget_room"], "gross_room": ac["gross_room"],
            "deployment_cap": ac["regime_cap"],
            "notional_cap": min(notional) if notional else None,
            "event_shrink": ac.get("event_shrink"),
            "final": final, "binding": binding,
            "usage_missing_atr": u.get("missing_atr") or [],
            "usage_assumed_full_delta": u.get("assumed_full_delta") or []}


def latest_iv(ticker: str) -> Optional[dict]:
    p = IV_HISTORY_DIR / f"{ticker}.jsonl"
    if not p.exists():
        return None
    last = None
    for r in iter_jsonl(p):
        iv = coerce_float(r.get("atm_iv_pct"))
        if iv is None or iv <= 0:
            continue  # 0.0 = 盘前空报价误写, 取最后一条有效读数
        last = r
    return last


def option_iv_gate(ticker: str) -> dict:
    """单腿 long call 的 IV 硬门槛 (用户「硬门槛」): percentile <40
    (序列 ≥60 可信) / 绝对 ATM IV <50% (兜底) / 无数据不放行。判别式单源 =
    sizing.long_call_iv_gate; 本函数只把 iv_logger 的读数喂进去并带上播报字段。
    → {allowed, basis, reason, pct, samples, abs_iv}。

    为什么是硬门槛: 见底点 (gate1) 恰是 IV 峰值 (AAOI g1 149%→g2 112% / LITE 114→91 /
    MU 93→67), Δ0.7 270DTE call 在正股 +4~9% 时因 vega crush 仍 −6~−21%; 08-11 曾把
    60-80 带的"size 减半"从文案删掉 (从未进 cap), 现在的门是真门: 不过 = 不选约。
    """
    try:
        ivp = iv_logger.percentile(ticker)
    except Exception as e:                                        # noqa: BLE001
        ivp = {"error": f"{type(e).__name__}: {e}"}
    if ivp.get("error"):
        row = latest_iv(ticker)
        abs_iv = coerce_float((row or {}).get("atm_iv_pct"))
        g = long_call_iv_gate(None, False, abs_iv)
        return {**g, "pct": None, "samples": 0, "abs_iv": abs_iv}
    g = long_call_iv_gate(ivp.get("iv_percentile"), bool(ivp.get("reliable")),
                          ivp.get("latest_iv_pct"))
    return {**g, "pct": ivp.get("iv_percentile"), "samples": ivp.get("samples"),
            "abs_iv": ivp.get("latest_iv_pct")}


def iv_gate_line(ticker: str) -> str:
    g = option_iv_gate(ticker)
    if g["basis"] == "none":
        return f"IV 未入库 — 期权硬门槛不放行; 先跑 iv_logger (--tickers {ticker})"
    head = "✓ 单腿 long call 可用" if g["allowed"] else "⛔ VETO 单腿 long call → 正股"
    return f"IV 硬门槛 {head} — {g['reason']} (n={g.get('samples') or 0})"


# ── 数据装载 + 主评估 ───────────────────────────────────────────

# 数据装载全部走 core.book —— NAV / K 线 / 组合占用此前在 entry_guard、
# portfolio_brief、price_reminder_sync 里各存一份实现, 读同一批文件却是三份代码
# (期权 delta 更是各自 BS 反解 = 各自一个答案)。 合并到一处。
_rows = book.bars
_nav = book.latest_nav
_book_usage = book.load_usage
_power = book.power_room


def _skhy_premium_line() -> Optional[str]:
    if not SKHY_PREMIUM_PATH.exists():
        return None
    last = None
    for r in iter_jsonl(SKHY_PREMIUM_PATH):
        last = r
    if not last:
        return None
    pm = coerce_float(last.get("premium_pct"))
    return f"ADR 溢价 gate: {pm:+.1f}% ({last.get('date')}) → {last.get('gate', '?')}" \
        if pm is not None else None


def dow_priority(rows: List[dict]) -> dict:
    """短期道氏排序键 (core/dow.priority_of_bars): 日线级 + 周线级 (走完的周)。"""
    if not rows:
        return {"tier": 3, "short": "末位", "cn": "末位·方向未定", "sort_key": (3, 3),
                "weekly_cn": None, "stage_cn": dow.STAGE_CN[(None, 0)]}
    return dow.priority_of_bars(rows, technical.resample_weekly(rows))


def dow_line(pr: dict) -> str:
    """「结构位」行 —— 清单排序的依据要印在票的块里, 排序才可解释。"""
    stage = str(pr.get("stage_cn") or "")
    detail = stage.split("（", 1)[1].rstrip("）") if "（" in stage else stage
    return (f"结构位 {pr['cn']}" + (f" ｜ {detail}" if detail else "")
            + (f" ｜ 周线级 {pr['weekly_cn']}" if pr.get("weekly_cn") else ""))


def evaluate_ticker(tk: str, card: dict, today: str, nav: Optional[float],
                    event_day: bool = False, reentry_ref: Optional[float] = None,
                    usage: Optional[dict] = None,
                    calendar: Optional[dict] = None) -> dict:
    agent = card.get("agent") or {}
    thesis = agent.get("thesis") or {}
    action = agent.get("action") or {}
    rows = _rows(tk)
    close = coerce_float(rows[-1].get("close")) if rows else None
    atr_series = technical.atr(rows, PARAMS["atr_period"]) if rows else []
    atr = atr_series[-1] if atr_series else None
    st = technical.swing_structure(rows) if rows else {}
    mode = momentum_mode(rows) if rows else "normal"
    # 单日强势突破 gate → 参与权阶梯 (ladder_entry , k=0.5; 适用域与
    # 冻结锚  收窄)。优先于 parabolic: 这是**结构事件**, 不是动量分类。
    reclaimed = reclaimed_gate_of(agent, today, rows)
    if reclaimed and close:
        mode = "repair_reclaim"

    out: dict = {
        "ticker": tk, "close": close, "mode": mode,
        "trend": st.get("trend"), "structure": st,
        "moomoo_listed": card.get("moomoo_listed"),
        "verdict": None, "reasons": [], "lines": [],
        "ladder": None, "caps": None, "stops": None,
        "iv_line": iv_gate_line(tk),
        "card_gates": (action.get("reason") or "")[:300],
        "constraints": read_constraints(agent),
        "reentry_ref": reentry_ref,
    }
    # 卡片约束无条件播报；短期道氏结构位是盘前清单的第一排序键，
    # 只排序，不改 verdict /
    # cap / 股数 (退出侧回测裁定不接退出栈, design §5.1; 它在本仓的位置是「谁先挂」)。
    out["dow"] = dow_priority(rows)
    out["lines"].append(dow_line(out["dow"]))
    out["lines"].extend(constraint_lines(out["constraints"], today))
    out["regime_gate"] = regime_gate(agent)
    out["lines"].extend(regime_lines(agent))
    # 财报反应日缩仓 (earnings_window 路 A) —— **sizing 层, 不是授权层**:
    # 它不改 verdict, 只改那一天的 size 上限。所以放在这里播报 (无论 verdict),
    # 与 v3 "财报不参与授权" 不冲突。
    ev = earnings.event_state(tk, today, calendar)
    out["earnings_event"] = ev
    ev_line = earnings.event_line(tk, ev)
    if ev_line:
        out["lines"].append(ev_line)

    def done(verdict: str) -> dict:
        out["verdict"] = verdict
        if verdict != "PLAN":
            # vehicle 约束行的"合约级参数见 🧰 期权执行计划"是给 PLAN 写的文案 ——
            # 无计划时指向不存在的段落 (NVDA VETO 案), 砍掉尾巴留事实半句
            out["lines"] = [ln.split(" — 下方阶梯为")[0] if "下方阶梯为" in ln else ln
                            for ln in out["lines"]]
        return out

    if not rows:
        out["reasons"].append("无 K 线缓存 (kline_cache 下轮拉取)")
        return done("NEEDS_CARD")
    if str(thesis.get("status")) == "needs_card" or not thesis.get("summary"):
        out["reasons"].append("stub 卡 — 先 /trading-analysis 建 thesis+zones")
        return done("NEEDS_CARD")
    # regime 刚转移过, current 的 provisional 项还是机械落的观察级数值。
    # 转移本身是机械的、不可阻挡的; 但转移**之后**授权入场需要补画 —— 机械项
    # (stop / no_chase / cap) 已生效, z1/trim 在补画前不是"算过的位"。
    pr = agent.get("pending_repaint") or {}
    if "current" in (pr.get("slots") or []):
        out["reasons"].append(
            f"regime 于 {pr.get('since')} 转移, current 的 provisional zone 未补画 "
            "(机械落的观察级数值不授权入场) — 先跑 /trading-analysis 补画")
        return done("NEEDS_CARD")

    zb = zone_bands(agent.get("zones"))
    out["lines"].extend(reclaim_gate_lines(agent.get("zones"), rows, today))
    nr_lines, nr_fired = next_regime_lines(agent, rows)
    out["lines"].extend(nr_lines)
    out["lines"].extend(floor_gate_gap_lines(agent, atr))
    if nr_fired:
        out["next_regime_ready"] = (agent.get("next_regime") or {}).get("name") or True

    # regime cap 0 = 观察/修复层, 不授权任何 size。先于 zone 判定 —— 在这个 regime
    # 里"价格在 z1/z2 内"只说明跌到哪了, 不是买点。阶梯/gate 行在上面**先渲染**
    # (regime 统一方案 a, 用户 批): VETO = 无授权, 阶梯 = 修复地图,
    # 两者不矛盾 —— cap 0 卡砍掉地图 = 手册只剩一行 VETO, 看不到解除路径。
    if regime_cap_frac(agent) == 0:
        g = out["regime_gate"]
        out["reasons"].append(
            f"当前 regime [{g.get('name')}] cap 0 (自 {g.get('since')}: {g.get('reason')})"
            " — 观察层不授权入场; 解除 = 收复 next_regime 的 gate")
        return done("VETO")
    # 参与资格双轨判别式 (LITE 809 案): 资格来源必须显式输出, 不埋卡片 prose。
    # v3 (): 资格只看结构 — wait_for_structure 卡走 gate 轨。thesis 状态不再
    # 参与判定 (旧: needs_reverification 也进 gate_only — 已废), 降为 [FYI] 播报。
    gate_only = str(action.get("intent")) == "wait_for_structure"
    _ts = str(thesis.get("status") or "")
    if _ts and _ts != "intact":
        out["lines"].append(f"[FYI] thesis.status={_ts} — 情报层提醒, 不参与授权 (技术面优先 v3)")
    out["participation"] = "gate_only" if gate_only else "zone_eligible"
    # 结构修复信号 (对 wait_for_structure 卡是第一 gate; 对其余是加权信息)
    lsh = st.get("last_swing_high")
    repaired = bool(lsh and close and close > lsh)
    if str(action.get("intent")) == "wait_for_structure":
        if not repaired:
            out["lines"].append("⛔ 回踩资格: 无 (结构修复卡) — zone 触碰不给参与资格, "
                                "仅 gate 成立后 (v3: print/thesis 不参与资格判定)")
            out["reasons"].append(
                f"结构未修复: {st.get('high_label')}+{st.get('low_label')}({st.get('trend')})"
                f" — 修复第一信号 = 收盘 > ${lsh:g}" if lsh else "结构数据不足")
            return done("VETO")
        out["lines"].append(f"🔧 结构修复第一信号已现: close {close:g} > 最近 swing high {lsh:g}"
                            " (≠🔓 完全修复线)")
        if not zb:
            out["reasons"].append("结构已修复但无数值 zone — 跑 /trading-analysis 建 zone 后给 ladder")
            return done("NEEDS_CARD")
    if not zb:
        out["reasons"].append("无数值 zone (卡片为观察位 prose) — 先建 zone")
        return done("NEEDS_CARD")

    stale = zone_staleness(zb, thesis, rows, today)
    # gate 与 zone 同生死同锚同自洽 (用户)。gate 此前无 valid_until、
    # 无 staleness、从不检查 —— 而两者是同一次分析的产物。NBIS 实测: 卡上 234
    # 画于 7/21, 按 7/27 结构该是 198, 差 +24.5% vs +5.3%。
    gstale = gate_staleness(agent, today, stale)
    out["lines"].extend(structure_lines(agent, today, stale))
    if stale or gstale:
        out["reasons"].extend(stale)
        out["reasons"].extend(gstale)
        out["reasons"].append("→ 先重画 zone **与 gate**(同一次, zone-maintenance 流程), "
                              "拒绝用旧结构给计划")
        return done("STALE")
    incoherent = gate_coherence(agent)
    if incoherent:
        out["reasons"].extend(incoherent)
        out["reasons"].append("→ 结构不自洽, 先跑 /trading-analysis 整套重画")
        return done("STALE")

    if not gate_only:
        # : 旧文案硬编码"= probe 参与资格" —— 字符串说 probe、算法给
        # confirmed, 是同一分叉的自证。资格行只说**资格**, 档位归下方判别式。
        # 判定资格带包含 z1 与 z2，展示口径必须与判定口径一致。
        out["lines"].append(
            f"🎫 回踩资格: z1 {zb['z1'][0]:g}-{zb['z1'][1]:g}"
            f" / z2 {zb['z2'][0]:g}-{zb['z2'][1]:g} 触碰 = 参与资格成立,"
            " 资格带下限 = z2 下沿"
            f" (thesis {thesis.get('status', '?')}; 预挂授权, 触碰判读走 zone-touch 协议;"
            " 档位由结构判别式定, gate 只管升级/重锚, 非资格前提)")

    pos = zone_position(close, zb)
    out["zone_position"] = pos
    out["z1"] = zb["z1"]
    out["z2"] = zb["z2"]
    # 真 VETO: 破位 / z2 下沿以下 (z1/z2 限价此刻在市价上方 = 即时成交不是回踩单)。
    # 其余位置 (waiting/trim/above_no_chase/zone 内) 一律出计划 ——  用户:
    # 挂的是 zone 限价单, GO/WAIT 描述的是市价, 没有意义。位置降为信息行。
    if pos == "below_zones":
        stop = zb.get("stop")
        if stop and close < stop:
            out["reasons"].append(f"close {close:g} < stop_level {stop:g} — 破位, 对照 invalid_if")
            return done("VETO")
        out["reasons"].append("z2 下沿以下未破 stop — 不挂单观察 (zone 限价此刻在市价"
                              "上方 = 即时成交), 破 stop 即 invalid")
        return done("VETO")
    out["lines"].append(_position_line(pos, close, zb))

    # ── 出计划前的最后一道闸: 结构修复 VETO (分类轴退役, D1) ──
    #
    # 判别式真源 = `core.structure.structure_repair_veto` (内核 = 旧 entry_
    # classification 的降级条件, 逐字不动; 回测依据见该函数注释)。旧输出「降级
    # probe (cap 2%)」→ 新输出 **VETO**(用户:「结构降级反转立马 cap 0 即可」)。
    # 适用域 = card 源 (机器源 cap = gate 授予, 豁免 — 用户  二裁);
    # card 源护栏堵的缝 = 两次重画之间结构转坏 (HH+LL 不破 stop 不触发转移机)。
    vetoed, veto_why = structure_repair_veto(agent, st, close)
    if vetoed:
        out["reasons"].append(veto_why)
        out["reasons"].append("→ cap>0 卡结构未修复 = 地图错误, 重画 (regime 应切修复观察)")
        return done("VETO")
    if event_day:
        out["lines"].append("[FYI] 事件日 (宏观/财报) — 情报层提醒, v3 起不降级不拦截")

    ladder = build_ladder(mode, close, rows, zb, atr=atr, reclaim=reclaimed)
    # ②③ 的 stop 口径 (用户  #3): 用**真 stop**, 多条取 max ——
    # 会先触发的那条才是这笔的风险距离。2N 只在一条真 stop 都没有时兜底,
    # **不进 max**(它不是你会挂的单子; 混进去会在"卡 stop 比 2N 远"时低估风险)。
    # 旧写法取 min(结构位, 2N) = 更远 = 更保守, 但量的是一条不存在的线。
    _t1 = ladder[0] if ladder else None
    _rs, _rs_basis = binding_stop(_t1, [coerce_float(zb.get("stop")) if zb else None],
                                  atr, PARAMS["stop_n_mult"])
    caps = size_caps(nav, atr, _t1,
                     agent=agent, usage=usage, power_room=(_power() or {}).get("room"),
                     event_shrink=ev["shrink"] if ev["active"] else None,
                     risk_stop=_rs, exposure=book.exposure_of(tk),
                     gate_no=gate_count(card))
    caps["risk_stop"], caps["risk_stop_basis"] = _rs, _rs_basis
    # 期权资格闸 (当前配置; 公开 schema 见 trade/us_stock/strategy/options/):
    # ① gate1 起可期权 (OPTION_MIN_GATE=1) —— 见底条件 = 第一个给 cap 的 gate, 与正股
    #    同一套资格 (cap>0 + 结构修复 + 未过期 + z1/z2 限价), 不是"随意开仓";
    # ② IV 硬门槛 (sizing.long_call_iv_gate) —— 见底点是 IV 峰值, 高 IV 票 gate1 一律正股;
    # ③ 过门 → 当场翻成合约级计划 (option_plan.build), 失败降级为
    #    一行"手动跑", 不静默。保费上限 = 本档新增 risk ÷ |S1| (gate1 12.5% NAV);
    #    期权的正股退出线 = max(卡 stop, 授予 cap 的 gate) (S5g, granting_gate_level)。
    veh = (out["constraints"] or {}).get("vehicle")
    if veh in OPTION_VEHICLES:
        _g = caps.get("gate_no") or 1
        prem_frac = option_premium_cap_frac(_g)
        ivg = option_iv_gate(tk)
        out["option_iv_gate"] = ivg
        if _g < OPTION_MIN_GATE or prem_frac <= 0:
            out["reasons"].append(
                f"⛔ 期权 gate{_g} 无保费上限 (OPTION_MIN_GATE={OPTION_MIN_GATE}) — 本档正股执行")
        elif not ivg["allowed"]:
            out["reasons"].append(
                f"⛔ 期权 IV 硬门槛 VETO: {ivg['reason']} — 本档正股执行 "
                f"(gate{_g} 保费上限 {prem_frac:.1%} NAV 留给 IV 回落后)")
        else:
            out["reasons"].append(
                f"🧰 期权 gate{_g} 可用: IV 硬门槛 ✓ ({ivg['reason']}) · 保费上限 "
                f"{prem_frac:.1%} NAV" + (f" = ${prem_frac * nav:,.0f}" if nav else ""))
            try:
                from quant.options import option_plan
                # LEAPS DTE = §13.2 下限 270, 执行 = 下限之上最近可用周期不限 Jan
                dte, dte_floor = (270, 270) if veh == "leaps" else (45, 7)
                ar_prem = (AT_RISK_CAP_FRAC * nav / OPTION_ATRISK_LOSS_FRAC) if nav else None
                _gate = granting_gate_level(card)
                _und_stop = max([v for v in (coerce_float(zb.get("stop")) if zb else None, _gate)
                                 if v is not None], default=None)
                plan = option_plan.build(tk, ladder, (prem_frac * nav) if nav else None,
                                         target_dte=dte, min_dte=dte_floor,
                                         stop=_und_stop,
                                         notional_cap=caps.get("notional_cap"),
                                         at_risk_premium_cap=ar_prem)
                out["option_plan"] = option_plan.render(plan)
                out["option_plan_data"] = plan
            except Exception as e:                                    # noqa: BLE001
                out["reasons"].append(f"⚠️ 期权计划生成失败 ({type(e).__name__}: {e}) — "
                                      f"手动跑 quant/options/option_plan.py {tk}")
    stops = {
        # 显示合同 (用户: 一个位置建仓/一个位置止损/一个位置止盈):
        # 播报只给 display 一条线。structural/two_n 保留为 sizing 内部量 ——
        # at-risk 的 risk_stop 仍取双线更远者 (保守), 但那是 cap 数学不是止损线。
        "structural": zb.get("stop"),
        "two_n": round(ladder[0] - PARAMS["stop_n_mult"] * atr, 2) if (ladder and atr) else None,
    }
    stops["display"] = stops["structural"] if stops["structural"] is not None else stops["two_n"]
    stops["display_basis"] = "结构位" if stops["structural"] is not None else "2N 兜底"
    out["ladder"] = ladder
    out["caps"] = caps
    out["stops"] = stops
    out["trim"] = zb.get("trim")
    out["atr"] = round(atr, 3) if atr else None
    return done("PLAN")


# ── 渲染 + 落库 ─────────────────────────────────────────────────

_ICON = {"PLAN": "🟢", "STALE": "⚠️", "NEEDS_CARD": "📇", "VETO": "⛔"}


def _esc_cell(s: str) -> str:
    """表格单元格里的竖线要转义 (tg_rich 认 `\\|`), 否则一条 reason 把列切歪。"""
    return str(s).replace("|", "\\|")


def _summary_cells(r: dict) -> tuple:
    """一览表一行的三格 (票·价, 判定, 位置/计划) —— display-only: 简报要像
    gate-ladder-report 一目了然; 渲染成 Telegram 原生表格 (rich 通道)。
    数据全部取自本文件自己渲染的 lines/结构化字段 —— 机械抽取, 不是散文改写。
    PLAN 行的信息 = 挂哪 (z1 带 + 回踩距离) + 挂多少 (首档股数) ——
    verdict 不含市价语义, 一览要回答的就是这两问。"""
    who = f"{_ICON.get(r['verdict'], '?')} {r['ticker']}"
    if r.get("close"):
        who += f" {r['close']:g}"
    verdict = r["verdict"]
    if r["verdict"] == "PLAN":
        # 档位不再由域决定 (二次定版): 一张连过两道 gate 却没买过的
        # 候选卡就是 gate2 —— 旧的写死 "(R1)" 会在一览上把 4% 的计划标成 2.5% 的。
        verdict += f"(gate{(r.get('caps') or {}).get('gate_no') or 1})"
        if r.get("dow"):
            verdict += f"·{r['dow']['short']}"          # 结构位 = 清单第一排序键 ()
    tail = None
    if r["verdict"] == "PLAN" and r.get("ladder"):
        parts = []
        z1 = r.get("z1")
        if z1 and r.get("close"):
            dist = (z1[1] - r["close"]) / r["close"] * 100
            parts.append(f"z1 {z1[0]:g}-{z1[1]:g} ({dist:+.1f}%)")
        z2 = r.get("z2")
        if z2:
            parts.append(f"z2 {z2[0]:g}-{z2[1]:g}")
        cap = (r.get("caps") or {}).get("final")
        sl = share_ladder(cap, r["ladder"], PARAMS["ladder_weights"]) if cap else None
        if sl and sl.get("tiers"):
            t0 = sl["tiers"][0]
            parts.append(f"首档 ${t0['price']:g}×{t0['shares']}股"
                         + (" ⛔0股例外" if sl["exception"] else ""))
        tail = " · ".join(parts) or None
    if tail is None:
        for ln in r.get("lines", []):
            if "⏱" in ln and "✅" not in ln:
                # 计数锚定在 "]: n/2" — label 文本里的日期 (如 "7/24-27") 会咬松散的 .*?(\d)/2
                lv, ct = re.search(r"收盘>\$([\d.]+)", ln), re.search(r"\]:\s*(\d)/2", ln)
                if lv and ct:
                    tail = f"下一线 {lv.group(1)}: {ct.group(1)}/2"
                    break
    if tail is None:
        for ln in r.get("lines", []):
            if ln.startswith("🎚"):
                lv, ct = re.search(r"收盘>\$([\d.]+)", ln), re.search(r"生效:\s*(\d)/2", ln)
                if lv and ct:
                    tail = f"转移线 {lv.group(1)}: {ct.group(1)}/2"
                break
    if tail is None and r.get("reasons"):
        tail = str(r["reasons"][0])[:60]
    return who, verdict, tail or ""


SUMMARY_HEADER = ("| 票 | 判定 | 位置 / 计划 |", "|---|---|---|")


def _summary_line(r: dict) -> str:
    """一览表一行 (GFM 管道表行; 老的单行文本 = 三格用 ` | ` 并排, 语义不变)。"""
    return "| " + " | ".join(_esc_cell(c) for c in _summary_cells(r)) + " |"


def render(results: List[dict], nav: Optional[float], today: str,
           usage_txt: Optional[List[str]] = None,
           delisted: Optional[List[str]] = None,
           reentry_only: Optional[Dict[str, float]] = None) -> str:
    order = {"PLAN": 0, "STALE": 1, "NEEDS_CARD": 2, "VETO": 3}
    icon = _ICON
    # verdict 之内按短期道氏结构位排：优先 = 上涨推进中/反转向上，
    # 次位 = 观察期, 末位 = 下跌推进中; 同档周线级打平局)。只排序, 不改任何块内数字。
    ordered = sorted(results, key=lambda x: (order.get(x["verdict"], 9),
                                             tuple((x.get("dow") or {}).get("sort_key") or (9, 9))))
    lines = [f"📥 入场执行计划 {today} (entry_guard)"
             + (f" | NAV ${nav:,.0f}" if nav else "")]
    lines.extend(usage_txt or [])
    if len(ordered) > 1:
        lines.append("── 一览 ──────────────")
        lines.extend(SUMMARY_HEADER)              # 表头 + 对齐行: tg_notify --rich 渲染成原生表格
        lines.extend(_summary_line(r) for r in ordered)
        lines.append("── 详情 ──────────────")
    for r in ordered:
        head = f"{icon.get(r['verdict'], '?')} {r['ticker']}"
        if r.get("close"):
            head += f" ${r['close']:g}"
        head += f" — {r['verdict']}"
        if r["verdict"] == "PLAN":
            head += f"(gate{(r.get('caps') or {}).get('gate_no') or 1})"
            if r.get("dow"):
                head += f"·{r['dow']['short']}"
        if r.get("moomoo_listed") is False:
            head += " [moomoo 已移出]"
        lines.append(head)
        for x in r.get("lines", []):
            lines.append(f"   {x}")
        for reason in r["reasons"]:
            lines.append(f"   · {reason}")
        if r.get("reentry_ref"):
            lines.append(f"   🔓 完全修复线 ${r['reentry_ref']:g} (exit_guard 重进 watch)"
                         " — 提示线非入场条件, 入场按上方 gate/zone")
        if r["verdict"] == "PLAN" and r.get("ladder"):
            w = PARAMS["ladder_weights"]
            caps = r["caps"] or {}
            cap = caps.get("final")
            # 整股约束 (fix 2, position-tiers.md §整股约束): 先 floor(cap/price) 定股数,
            # 再按降级表切档。此前直接按权重分钱除价格 → 碎股档 (LITE 0.6/0.3/0.2 股)。
            sl = share_ladder(cap, r["ladder"], w) if cap else None
            if sl:
                seg = [f"${t['price']:g}×{t['shares']}股(${t['usd']:,.0f})" for t in sl["tiers"]]
                lines.append(f"   阶梯挂单 [{r['mode']}] {sl['n_tiers']}档: " + " / ".join(seg))
                if sl["note"]:
                    lines.append(f"   {sl['note']}")
                if sl["exception"]:
                    lines.append("   ⛔ 未落 decisions.jsonl 书面例外前, 这不是可执行计划")
            else:
                seg = [f"${px:g}×{w[i]*100:.0f}%" for i, px in enumerate(r["ladder"])]
                lines.append(f"   阶梯挂单 [{r['mode']}]: " + " / ".join(seg) + " (无 cap, 股数待定)")
            if cap:
                lines.append(f"   size 上限: ${cap:,.0f} (binding={caps.get('binding')}; "
                             f"regime cap ${caps.get('regime_cap') or 0:,.0f}, "
                             f"gate{caps.get('gate_no')} 预算 ${caps.get('n_unit_cap') or 0:,.0f}) — "
                             f"真实 size 仍受 Kelly/concentration 约束")
            # 止损单行 (用户: 一个建仓位/一个止损位/一个止盈位)。
            # 2N 降为 sizing 内部量, 不再当第二条止损线播报。
            st = r.get("stops") or {}
            seg2 = f"   预设止损: {st.get('display')} ({st.get('display_basis')})"
            tz = r.get("trim")
            if isinstance(tz, list) and len(tz) == 2:
                seg2 += f" | 止盈 trim {tz[0]:g}-{tz[1]:g}"
            lines.append(seg2 + f" | ATR20 {r.get('atr')}")
        if r.get("iv_line"):
            lines.append(f"   若期权: {r['iv_line']}")
        # 卡片指定了期权 vehicle → 阶梯必须给到合约级, 否则 "195.53/193.57/191"
        # 是标的价位, 拿到期权账户没法挂 (用户)。
        if r.get("option_plan"):
            lines.append("   " + "\n   ".join(r["option_plan"].splitlines()))
        elif r["verdict"] == "PLAN" and (r.get("constraints") or {}).get("vehicle") in OPTION_VEHICLES:
            # 没有合约级计划的 vehicle 卡: 说清是哪道门拦的 (IV 硬门槛 / 生成失败 /
            # 无保费上限), reasons 里已有原文, 这里提到阶梯块内 —— 静默是本仓的病。
            why = [x for x in (r.get("reasons") or [])
                   if x.startswith(("⛔ 期权", "⚠️ 期权", "🧰 期权"))]
            lines.append("   " + (why[-1] if why else
                                  f"⛔ vehicle={r['constraints']['vehicle']} 无合约级计划 — 本档正股执行"))
        if r["ticker"] == "SKHY":
            pm = _skhy_premium_line()
            if pm:
                lines.append(f"   {pm}")
        if r["verdict"] == "VETO" and r.get("card_gates"):
            lines.append(f"   卡片 gate: {r['card_gates'][:160]}")
        # 「卡片约束未结构化」告警  摘除 (用户 定)。
        #
        # 它写于  (LITE 3.6× 授权案): 那时 prose 里真的躺着财报/thesis
        # 类天花板, 而 entry_guard 读不到 → 输出 GO(confirmed) cap $818 vs 卡片
        # 授权的 probe $225。判据是 `verdict==PLAN 且 card_gates 非空 且无
        # max_classification`。
        #
        # v3 () 把 max_classification 的财报/thesis 语义整个废掉,
        # 六张卡的都拿掉了, 全书只剩 SKHY 的 ADR premium gate 一个合法用途 ——
        # 而 SKHY 不在书上。于是这条告警的两个前置同时变成恒真:
        #   card_gates = action.reason[:300]  → 每张卡都有 reason ⇒ 恒非空
        #   max_classification                → 全书 0 张
        #  实测: **6/6 张 PLAN 全部触发**。
        #
        # 100% 触发率 = 零信息量。这是  那把「全池固定阈值」的另一端:
        # 不会触发的检查比没有更危险, 而**永远触发的检查等于没有检查**——
        # 读的人会连着真信号一起学会略过。
        #
        # [ 追记] clamp 机制已随分类轴整体退役 —— 卡片级收紧如需回归,
        # 走数值字段另立 (classification_removal_design.md D6), 不复活分类词。
    if reentry_only:
        seg = " | ".join(f"{k} close>{v:g}" for k, v in reentry_only.items())
        lines.append(f"🔓 完全修复 watch (非 watchlist 候选): {seg}"
                     " — 触发 = 提醒可评估新 campaign, 非入场前提")
    if delisted:
        lines.append(f"⚠️ 已移出 moomoo 分组 (卡保留, 不评估): {', '.join(delisted)}"
                     " — 重新跟进 = App 加回分组, 或 --tickers 显式评估")
    return "\n".join(lines)


def _plan_signature(r: dict) -> Optional[str]:
    """计划的可比指纹: 价位 + 股数 + 档位。cap 金额随 NAV 每日微漂, 不进指纹。"""
    if not r.get("ladder"):
        return None
    cap = (r.get("caps") or {}).get("final")
    sl = share_ladder(cap, r["ladder"], PARAMS["ladder_weights"]) if cap else None
    return json.dumps({"ladder": r["ladder"],
                       "shares": [t["shares"] for t in sl["tiers"]] if sl else [],
                       "round": 1}, sort_keys=True)


def _last_plan_signatures() -> Dict[str, str]:
    out: Dict[str, str] = {}
    if DECISIONS_PATH.exists():
        for row in iter_jsonl(DECISIONS_PATH):
            if row.get("source") == "entry_guard" and row.get("action") == "entry_plan" \
                    and row.get("plan_sig"):
                out[str(row.get("ticker"))] = row["plan_sig"]
    return out


def log_decisions(results: List[dict]) -> int:
    """PLAN 计划落 decisions.jsonl —— **价位/股数/档位相对上次落库有变才写一行**
    (verdict 改为挂单授权语义后 PLAN 是常态, 逐日全落 = 噪音淹掉重锚轨迹;
    变动才落让「未成交档只升不降」第一次有了审计轨迹)。"""
    last = _last_plan_signatures()
    n = 0
    for r in results:
        if r["verdict"] != "PLAN":
            continue
        sig = _plan_signature(r)
        if sig is not None and last.get(r["ticker"]) == sig:
            continue
        cap = (r.get("caps") or {}).get("final")
        sl = share_ladder(cap, r.get("ladder") or [], PARAMS["ladder_weights"]) if cap else None
        row = {
            "timestamp": now_et_iso(), "ticker": r["ticker"], "action": "entry_plan",
            "chosen": "ladder_r1",
            "size": cap,
            "plan_sig": sig,
            "notes": (f"entry_guard {r['mode']} ladder "
                      + "/".join(f"{p:g}" for p in (r.get("ladder") or []))
                      + (f" shares={[t['shares'] for t in sl['tiers']]}"
                         f"{' EXCEPTION(0股)' if sl['exception'] else ''}" if sl else "")
                      + f" stops={r.get('stops')}"),
            "source": "entry_guard",
        }
        append_jsonl(DECISIONS_PATH, row)
        n += 1
    return n


def write_option_plans(results: List[dict], today: str) -> int:
    """GO 且期权计划有张数的选约 → state/option_plans.json (script-owned, 全量覆盖写)。

    消费者 (用户 「重画 regime 符合条件也要把合适的合约加到 Today 并画线」):
    today_group_sync (合约 code 进 Today 组, 响铃那刻直接下单那张) +
    price_reminder_sync (合约首档限价 oz1 线)。两者**纯文件读**— sync 层不
    import decision (分层)。全量覆盖 = 授权撤销/VETO/重画后旧选约自然消失;
    刷新节奏 = pipeline briefing 模式 (每日 18:30), postclose 转移的新授权
    合约信息次日接上 (正股成员 postclose 已即时进组)。
    """
    from quant.core.io_json import atomic_write_json
    from quant.core.paths import OPTION_PLANS_PATH
    plans: Dict[str, dict] = {}
    for r in results:
        pd_ = r.get("option_plan_data") or {}
        chosen = pd_.get("chosen") or {}
        size = pd_.get("size") or {}
        rungs = pd_.get("rungs") or []
        tiers = [t for t in (size.get("tiers") or []) if t.get("shares")]
        if r.get("verdict") != "PLAN" or not chosen.get("code") or not tiers or not rungs:
            continue
        code = str(chosen["code"])
        code = code[3:] if code.startswith("US.") else code
        plans[r["ticker"]] = {
            "code": code, "strike": chosen.get("strike"), "expiry": pd_.get("expiry"),
            "dte": pd_.get("dte"), "delta": chosen.get("delta"),
            "tiers": [{"premium": rungs[i]["premium_flat_iv"], "contracts": t["shares"]}
                      for i, t in enumerate(tiers) if i < len(rungs)],
            "selected_at": today,
        }
    atomic_write_json(OPTION_PLANS_PATH, {"as_of": today, "plans": plans})
    return len(plans)


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description="入场闸门 + 每日入场执行计划")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--log", action="store_true", help="PLAN 计划落 decisions.jsonl (变动才落)")
    ap.add_argument("--event-day", action="store_true",
                    help="宏观/财报事件日标记 (v3: 仅 [FYI] 播报, 不降级不拦截)")
    ap.add_argument("--tickers", nargs="*", help="只评估指定 ticker (默认全 watchlist)")
    a = ap.parse_args(argv)

    today = now_et_iso()[:10]  # ET 交易日为基准 (用户 : gate 日历以美东为主)
    wl = read_json(WATCHLIST_PATH) if WATCHLIST_PATH.exists() else {"tickers": {}}
    eg = read_json(EXIT_GUARDS_PATH) if EXIT_GUARDS_PATH.exists() else {}
    reentry: Dict[str, float] = {}
    for k, v in (eg.get("reentry_watch") or {}).items():
        ref = coerce_float((v or {}).get("ref"))
        if ref:
            reentry[k] = ref
    nav = _nav()
    # 整轮共用同一份"书的占用"读数 —— 逐票重算会让同一天的可用额度前后不一致
    usage = _book_usage(nav=nav)
    usage_txt = usage_lines(usage, nav)     # 与 portfolio_brief 同一段渲染
    calendar = earnings.load_calendar()     # 整轮一份, 逐票读会把同一天读成不同天
    results = []
    delisted = []
    reference = []
    explicit = {t.upper() for t in a.tickers} if a.tickers else None
    for tk, card in (wl.get("tickers") or {}).items():
        if str(card.get("status")) != "watching":
            continue
        if explicit is not None and tk not in explicit:
            continue
        # 已移出 moomoo 分组的卡不进每日计划 (卡保留; --tickers 显式点名仍评估)
        if explicit is None and card.get("moomoo_listed") is False:
            delisted.append(tk)
            continue
        # 量能参考票 (SPY/QQQ/SMH/SOXX, 用户): 只供 kline/量比/板块读数,
        # 不交易不评估 — 不进计划、不渲染 NEEDS_CARD 噪音 (--tickers 显式点名仍评估)
        if explicit is None and not is_traded(card):
            reference.append(tk)
            continue
        results.append(evaluate_ticker(tk, card, today, nav, event_day=a.event_day,
                                       reentry_ref=reentry.get(tk), usage=usage,
                                       calendar=calendar))

    evaluated = {r["ticker"] for r in results}
    reentry_only = ({k: v for k, v in reentry.items() if k not in evaluated}
                    if explicit is None else {})
    if a.log:
        log_decisions(results)
        write_option_plans(results, today)   # Today 组/画线的选约真源 (--log = pipeline 路径)
    if a.json:
        print(json.dumps({"date": today, "nav": nav, "results": results,
                          "delisted_skipped": delisted, "reentry_only": reentry_only,
                          "reference_skipped": reference},
                         ensure_ascii=False, indent=2, default=str))
    else:
        out = render(results, nav, today, usage_txt, delisted, reentry_only)
        if reference:
            out += (f"\n📊 量能参考 (不评估, 读数走板块表头/情报层): "
                    + " ".join(sorted(reference)))
        print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
