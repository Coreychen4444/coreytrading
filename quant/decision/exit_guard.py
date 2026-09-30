#!/usr/bin/env python3
"""exit_guard.py — 赢家侧退出栈监控 (利润棘轮 + ATR 趋势死亡线 + G-01 档位 + margin guard)

为什么存在:  NOK LEAPS 复盘 (episode C) — G-01 +100% 在 6/2 客观触发
(mark $5.25, briefing 快照在案) 却零执行; 6/26 power $655 红灯躺在 nav_history
无人消费 → 7/2 LEVEL6 强平 -$2,295。规则不缺, 缺触发监控。本模块把
trend-exit-system.md 的硬线变成 heartbeat/overnight 每轮巡逻的机械检查。

四类检查:
  1. 利润棘轮: 峰值 ≥+50% → 保本地板; ≥+100% → 回吐上限 1/3 (DTE<90 收紧 1/4)。
     破地板两段式: stage1 卖 50% + 地板抬保本 + no-add lock; stage2 破保本清仓。
     地板/HWM 只升不降。触发一次性 (fired flag), 新高新 leg 后重新武装。
  2. 趋势死亡线: normal = 布防后最高收盘 - 3×ATR20; parabolic (zone methodology)
     = trailing 5d high × 0.92。只升不降, 正股收盘破线 → 平剩余/减至 base。
  3. G-01 档位: 期权 lot mark 过 +100%/+200%/+300% → trim 30/30/40 提示 (每档一次)。
  4. Margin guard: power/market_val < 5% warn / < 2% crit; risk LEVEL4+ crit;
     nav 数据 >3 天未更新 → INFRA stale。live accinfo 优先, nav_history 兜底。

宏观裁决 (canonical §macro): 棘轮与趋势死亡线**不接受宏观豁免**— 机械踩踏
均值回归是统计倾向不是担保; 想赌修复用重进协议, 不是不卖。

State: state/exit_guards.json (script-owned)。Canonical 规则:
Public schema: trade/strategy/trend-exit-system.example.md.
The active rule document is the ignored private copy without `.example`.

CLI:
    python3 quant/decision/exit_guard.py --arm [--json]   # 初始布防 (git 回填 HWM)
    python3 quant/decision/exit_guard.py [--json]         # 检查 + 更新 state (heartbeat)
    python3 quant/decision/exit_guard.py --read-only      # 只算不写 (session 内查看)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 直接 `python3 quant/<层>/<模块>.py` 时让 `quant.*` 可 import

from quant.core.paths import add_openapi_path  # noqa: E402

from quant.core import env, technical  # noqa: F401  (.env → os.environ)

import argparse
import json
from datetime import date
from typing import Dict, List, Optional, Tuple

from quant.core.holdings import UNMANAGED_ROLES, is_managed  # noqa: E402  结构豁免仓不进退出栈
from quant.core.io_json import atomic_write_json, iter_jsonl, read_json, state_lock  # noqa: E402
from quant.core.num import coerce_float  # noqa: E402
from quant.core.occ import parse_occ  # noqa: E402
from quant.core.paths import (# noqa: E402
    EVENTS_DIR, EXIT_GUARDS_PATH, IV_HISTORY_DIR, KLINE_CACHE_DIR, NAV_HISTORY_PATH,
    POSITIONS_PATH, ROOT,
)
from quant.core.timeutil import et_today, now_utc_iso  # noqa: E402
from quant.options import premium_proj  # noqa: E402  止损显示折基 (binding_stop 单一装配点)
from quant.core.sizing import granting_gate_level  # noqa: E402  期权 S5g: 授予 cap 的 gate = 退出线

PARAMS = {
    "ratchet_arm_gain": 0.50,       # 峰值 ≥ +50% → 保本地板 (赢单不许变亏单)
    "ratchet_lock_gain": 1.00,      # 峰值 ≥ +100% → 锁利地板
    "giveback_frac": 1 / 3,         # 回吐上限 = 峰值利润的 1/3
    "giveback_frac_low_dte": 0.25,  # DTE < 90 收紧到 1/4
    "low_dte": 90,
    "atr_period": 20,
    "atr_multiple": 3.0,
    "parabolic_trail_pct": 0.92,    # trailing 5d high × 0.92 (zone 体系一致)
    "new_leg_pct": 1.01,            # HWM 超 fire 时 HWM 1% → 重新武装 fired flags
    "margin_power_warn": 0.05,      # power / market_val
    "margin_power_crit": 0.02,
    "margin_risk_crit_level": 4,    # LEVEL4+ → critical
    "nav_stale_days": 3,
    "g01_levels": [(1.0, 30), (2.0, 30), (3.0, 40)],  # (gain, trim %)
    # ── 期权亏损侧 overlay (用户  终裁, 棘轮武装前的裸窗口) ──
    # 回测链: options_exit_overlay (17 笔回放, 切片组合全正) + synthetic_longcall
    # (1967 campaigns) + longcall_historical_replay (42 真实 Jan27, whipsaw 91-100%)。
    # S1=-25 是 用户 知情保守裁决 —— 回测窗  末起为 AI 牛市, whipsaw 读数
    # 被「什么都涨回来」抬高不可外推, 保守取紧; 紧档在牛市样本的 mean 代价
    # (-20.4pp) 同被牛市偏置污染。+12 月复测窗 (含更多 regime) 复核。
    # 用户 裁「收到 20%」(gate1 期权入场同日开放): 42 张 Jan27 重放上
    # −20 vs −25 = p10 −27.5 vs −32.3 (尾部好) / mean +24.8 vs +32.6 / whipsaw 95%
    # vs 91% —— 知情换尾部。sizing.OPTION_ATRISK_LOSS_FRAC 随 pin 跟到 0.20。
    "opt_s1_stop": -0.20,           # premium ≤ 成本×(1-20%) → 清仓 (硬 stop)
    # S5g gate 退出线 (用户, META 553 案): 期权的正股退出线 = 授予当前
    # cap 的那道 gate 本身 (sizing.granting_gate_level), 收盘 ×1 日跌破即清仓;
    # 正股仍用 bfloor_buf (gate − 0.5N), 期权含杠杆知情接受更高止损率。
    "opt_gate_stop_days": 1,
    "opt_s2_days": (20, 25),        # d20 未收上入场+1N → 减半; d25 → 清仓
    "opt_s4_dte_floor": 90,         # DTE ≤ 90 → close-or-roll (合成弱支持 90>120)
    "opt_s3_iv_drop": 8.0,          # advisory 只播不裁: IV 自入场 -8pts 且未 +1N
}

from quant.data.position_history import list_position_commits, position_at


# ── 纯计算层 (测试覆盖) ──────────────────────────────────────────

def compute_floor(avg_cost: Optional[float], hwm: Optional[float],
                  dte: Optional[int], p: Dict = PARAMS) -> Tuple[str, Optional[float]]:
    """棘轮地板: (stage, floor)。stage ∈ inactive | breakeven | locked。"""
    if not avg_cost or not hwm or avg_cost <= 0:
        return ("inactive", None)
    gain = (hwm - avg_cost) / avg_cost
    if gain >= p["ratchet_lock_gain"]:
        frac = p["giveback_frac_low_dte"] if (dte is not None and dte < p["low_dte"]) \
            else p["giveback_frac"]
        return ("locked", avg_cost + (hwm - avg_cost) * (1 - frac))
    if gain >= p["ratchet_arm_gain"]:
        return ("breakeven", avg_cost)
    return ("inactive", None)


def campaign_entry(ticker: str, underlying: str) -> Tuple[Optional[str], Optional[float]]:
    """本轮 campaign 的入场日 (ET) 与当日收盘 → (armed_date, seed_close)。

    布防口径与 analytics/exit_rules_backtest 对齐 ()。此前生产是
    "exit_guard 第一次看到这个仓位那天布防, seed 取布防前一日收盘", 而回测是
    `armed_date = entry_date` + seed 取入场当日收盘 —— **两个不同的模型**,
    于是实盘 trail 和回测出来的那条不是同一条线:

        MU 7/24 @925 建仓。生产 seed = 990.21 (7/23 收盘, 建仓前一天) → trail 750.84
                          回测 seed = 920.95 (入场当日收盘)         → trail 680.70

    生产那条锚在这笔仓位从没赚到过的价格上, 高 $70。方向上它更紧不更松, 所以
    不是"保护变少了", 是**实盘跑的根本不是被验证过的那条规则**—— 7/17 那批
    退出栈回测数字 (错配税 ≈$16.8k) 描述的是回测模型的行为。对齐 = 让生产回到
    已验证的那个模型, 不是引入一条没测过的新规则。

    取不到 events/kline 时返回 (None, None), 调用方退回旧口径 (老仓位 / 手工
    补录的持仓不能因为缺一条事件就布不了防)。
    """
    entry_date: Optional[str] = None
    for month_dir in sorted(p for p in EVENTS_DIR.glob("2*") if p.is_dir()):
        for row in iter_jsonl(month_dir / "trades.jsonl"):
            if str(row.get("ticker") or "").upper() != ticker.upper():
                continue
            et = row.get("event_type")
            if et in {"close", "expire", "assign"}:
                entry_date = None                      # 上一轮 campaign 结束
            elif et == "fill" and str(row.get("side", "")).upper() == "BUY" \
                    and entry_date is None:
                entry_date = str(row.get("timestamp") or "")[:10]
    if not entry_date:
        return (None, None)
    path = KLINE_CACHE_DIR / f"{underlying}.jsonl"
    if not path.exists():
        return (entry_date, None)
    for r in iter_jsonl(path):
        if str(r.get("time", ""))[:10] == entry_date:
            return (entry_date, coerce_float(r.get("close")))
    return (entry_date, None)


def compute_trail(klines: List[dict], armed_date: str, seed_close: Optional[float],
                  mode: str, prev_trail: Optional[float],
                  p: Dict = PARAMS) -> Dict[str, Optional[float]]:
    """趋势死亡线。返回 {highest_close, atr, trail, latest_close, broken}。
    trail 只升不降 (ratchet); 收盘破线 = broken。"""
    out: Dict[str, Optional[float]] = {
        "highest_close": None, "atr": None, "trail": prev_trail,
        "latest_close": None, "broken": False, "mode": mode,
    }
    if not klines:
        return out
    closes = [(str(r.get("time", ""))[:10], coerce_float(r.get("close"))) for r in klines]
    closes = [(t, c) for t, c in closes if t and c is not None]
    if not closes:
        return out
    latest_close = closes[-1][1]
    since = [c for t, c in closes if t >= (armed_date or "")]
    hc_candidates = since + ([seed_close] if seed_close else [])
    highest_close = max(hc_candidates) if hc_candidates else latest_close
    atr_series = technical.atr(klines, p["atr_period"])
    atr_latest = atr_series[-1] if atr_series else None
    out["atr"] = round(atr_latest, 4) if atr_latest else None
    trail_new: Optional[float] = None
    used_mode = mode
    if mode == "parabolic":
        highs = [coerce_float(r.get("high")) for r in klines[-5:]]
        highs = [h for h in highs if h is not None]
        if highs:
            trail_new = max(highs) * p["parabolic_trail_pct"]
    else:
        if atr_latest:
            trail_new = highest_close - p["atr_multiple"] * atr_latest
    # 首次布防落在已破线位置 (如崩盘中段布防, 5d high 嵌着崩盘前峰值):
    # 历史破位不追溯告警, 改用 ATR 从今天的高水位起一条活的线 (fresh start)。
    if prev_trail is None and trail_new is not None and latest_close < trail_new:
        if atr_latest:
            trail_new = highest_close - p["atr_multiple"] * atr_latest
            used_mode = f"{mode}→atr-fallback" if mode == "parabolic" else mode
        if latest_close < (trail_new or float("inf")):
            trail_new = None  # ATR 线也在价上 (极端), 等下轮新 bar 再挂
            used_mode = f"{mode}(pending)"
    out["mode"] = used_mode
    # Ratchet 约束: trail 只升不降, 且**不允许升到现价之上**— 止损线只能从
    # 价格下方拖尾。暴跌日公式值越过 close (5d high 嵌旧峰 / 单日 >3ATR) 时
    # 本轮不抬线, 用旧线判破位; 否则 fallback 修好的线下一轮又被抬回破位区。
    if trail_new is not None and trail_new > latest_close and prev_trail is not None:
        trail_new = None
    trail = max([v for v in (prev_trail, trail_new) if v is not None], default=None)
    out.update({
        "highest_close": round(highest_close, 4),
        "latest_close": latest_close,
        "trail": round(trail, 4) if trail is not None else None,
        "broken": bool(trail is not None and latest_close < trail),
    })
    return out


def g01_crossings(lots: Dict, mark: Optional[float], crossed: Dict[str, List[str]],
                  p: Dict = PARAMS) -> List[Dict]:
    """期权 lot 级 G-01 档位穿越 (每 lot 每档一次)。crossed 原地更新。"""
    hits: List[Dict] = []
    if mark is None:
        return hits
    for tier in ("base", "position"):
        for lot in (lots or {}).get(tier, []) or []:
            cost = coerce_float(lot.get("avg_cost"))
            qty = coerce_float(lot.get("qty")) or 0
            if not cost or cost <= 0 or qty <= 0:
                continue
            # entry_period = lot_reconcile 自动落账的时间字段 (期权 lot 同样自动落);
            # 旧手写 lot 的 entry_date / entry_period_human 优先, 已有 crossed 键不变。
            entry = (lot.get("entry_date") or lot.get("entry_period_human")
                     or lot.get("entry_period") or "?")
            key = f"{tier}|{entry}|{cost}"
            done = crossed.setdefault(key, [])
            for gain, trim_pct in p["g01_levels"]:
                tag = f"+{int(gain * 100)}%"
                if tag in done:
                    continue
                if mark >= cost * (1 + gain):
                    done.append(tag)
                    hits.append({
                        "tier": tier, "lot_key": key, "qty": qty, "cost": cost,
                        "level": tag, "trim_pct": trim_pct,
                        "trim_qty": max(1, round(qty * trim_pct / 100)),
                        "threshold": round(cost * (1 + gain), 4),
                    })
    return hits


def _atr20_at(klines: List[dict], date_str: str) -> Optional[float]:
    """ATR20 @ 指定交易日 (S2 的 +1N 锚用入场日的 N, 不随后续波动漂移)。"""
    atr = technical.atr(klines, period=20)
    for i, r in enumerate(klines):
        if str(r.get("time", ""))[:10] == date_str:
            return atr[i]
    return None


def _iv_at(underlying: str, date_str: str, latest: bool = False) -> Optional[float]:
    """iv_history 的 ATM IV (pct)。latest=True 取最新一行。"""
    path = IV_HISTORY_DIR / f"{underlying}.jsonl"
    if not path.exists():
        return None
    val = None
    for r in iter_jsonl(path):
        if latest:
            val = coerce_float(r.get("atm_iv_pct"))
        elif str(r.get("date")) == date_str:
            return coerce_float(r.get("atm_iv_pct"))
    return val


def option_loss_overlay(ticker: str, g: Dict, mark: Optional[float],
                        avg_cost: Optional[float], qty: float, dte: Optional[int],
                        underlying: str, klines: List[dict],
                        p: Dict = PARAMS, *,
                        gate_level: Optional[float] = None,
                        gate_label: Optional[str] = None,
                        s1_enabled: bool = True) -> Tuple[List[Dict], List[str]]:
    """单腿 long call 亏损侧 S1/S2/S4 + S3 advisory (用户终裁接线)。

    盈利侧 (棘轮/G-01/趋势死亡线) 已有 —— 本函数只补**棘轮武装前**的裸窗口
    (构造性盲区: OTM 横盘 + IV 回落 premium 大幅回落零告警;
    正股小幅下跌时 premium 已大幅亏损, underlying stop 慢一个量级)。
    fired flags 一次性; S1 收复 stop 线 +2% 重新武装 (同棘轮语义)。
    S2 单张不可拆: 减半档降级为提示行, d25 清仓档照常 (整股约束哲学)。

    S5g (用户, META 553 案): `gate_level` = 授予 underlying 当前 cap 的
    那道 gate (sizing.granting_gate_level); underlying 收盘 ×opt_gate_stop_days 跌破
    → 清仓一枪, 收回 gate 之上重新武装。这是「见底条件失效」的机械形状 —— 期权
    不等正股的 bfloor_buf。None = 常规参与卡/参与流已断, 退回卡 stop (S5)。

    reversal_probe (止损放在 gate): 调用方传 gate_level = 期权卡自己的
    stop_level (反转波段低点, underlying 基) + s1_enabled=False —— 本类止损只有 gate 一条,
    S1 保费百分比线关闭 (倒推 cap 已按 gate 处投影亏损定 size, S1 先响会让 cap 数学失真)。
    S2 / S4 / S3 advisory 照旧。
    """
    shots: List[Dict] = []
    lines: List[str] = []
    ov = g.setdefault("opt_overlay", {
        "s1_fired": False, "s2_half_fired": False, "s2_full_fired": False,
        "s4_fired": False})
    ov.setdefault("gate_fired", False)
    # 写回 guard 供显示层/画线读 (brief stop_status · price_reminder exit_floor_map):
    # gate 线是 underlying 基, 与 trail 同基, 挂正股 code。
    g["opt_gate_stop"] = ({"level": round(float(gate_level), 4),
                           "days": int(p.get("opt_gate_stop_days") or 1),
                           **({"label": gate_label} if gate_label else {})}
                          if gate_level else None)
    g["opt_s1_enabled"] = bool(s1_enabled)
    if mark is None or not avg_cost:
        return shots, lines
    s1_level = avg_cost * (1 + p["opt_s1_stop"])

    # S1 premium 硬 stop (reversal_probe 关: 止损只有 gate 一条)
    if not s1_enabled:
        ov["s1_fired"] = False
    if s1_enabled and ov["s1_fired"] and mark >= s1_level * 1.02:
        ov["s1_fired"] = False
        lines.append(f"{ticker}: mark {_fmt(mark)} 收复 S1 线 {_fmt(s1_level)} (+2%), S1 重新武装")
    if s1_enabled and not ov["s1_fired"] and mark <= s1_level:
        ov["s1_fired"] = True
        shots.append({
            "ticker": ticker, "type": "opt_s1_stop", "value": mark,
            "text": (f"🚨 {ticker} premium 硬 stop: mark {_fmt(mark)} ≤ 成本 {_fmt(avg_cost)} "
                     f"× {1 + p['opt_s1_stop']:.0%} = {_fmt(s1_level)}。"
                     f"行动【今日内】: 清仓 {qty:g} 张 — 这是资金管理线, 不看 underlying 在哪。"
                     f"无 override 路径；不执行 = 规则外评 C 起步。(期权退出栈 S1)")})

    # S5g gate 退出线 (underlying 收盘口径, 已完成日线)
    if gate_level and klines:
        gdays = int(p.get("opt_gate_stop_days") or 1)
        tail = [coerce_float(r.get("close")) for r in klines[-gdays:]]
        latest = tail[-1] if tail else None
        if ov["gate_fired"] and latest is not None and latest > gate_level:
            ov["gate_fired"] = False
            lines.append(f"{ticker}: {underlying} close {_fmt(latest)} 收回 gate "
                         f"{_fmt(gate_level)} 之上, S5g 重新武装")
        if not ov["gate_fired"] and len(tail) == gdays and all(
                c is not None and c < gate_level for c in tail):
            ov["gate_fired"] = True
            shots.append({
                "ticker": ticker, "type": "opt_gate_stop", "value": latest,
                "text": (f"🚨 {ticker} 跌破{gate_label or '授权 gate'}: {underlying} 收盘 {_fmt(latest)} < gate "
                         f"{_fmt(gate_level)} (×{gdays}日)。行动【今日内】: 清仓 {qty:g} 张 — "
                         f"见底条件失效即走, 不等正股 bfloor (期权含杠杆, 接受稍高止损率)。"
                         f"无 override 路径, "
                         f"不执行 = 规则外评 C 起步。(期权退出栈 S5g)")})

    # S2 时间 stop (锚 = 入场日 underlying close + 1×ATR20@入场)
    seed, armed = g.get("seed_close"), g.get("armed_date")
    atr0 = _atr20_at(klines, armed) if armed else None
    und_close = klines[-1].get("close") if klines else None
    t1, t2 = p["opt_s2_days"]
    if seed and atr0 and und_close is not None and armed:
        days = sum(1 for r in klines if str(r.get("time", ""))[:10] > armed)
        flat = und_close < seed + atr0
        if flat and days >= t2 and not ov["s2_full_fired"]:
            ov["s2_full_fired"] = True
            shots.append({
                "ticker": ticker, "type": "opt_s2_full", "value": mark,
                "text": (f"⏱ {ticker} 时间 stop d{t2}: 入场 {armed} 起 {days} 交易日, "
                         f"{underlying} {_fmt(und_close)} 未收上入场 {_fmt(seed)}+1N ({_fmt(seed + atr0)})。"
                         f"长 call 横盘 = theta 在烧, 趋势 thesis 兑现窗口已过。"
                         f"行动【今日内】: 清仓 {qty:g} 张。(期权退出栈 S2)")})
        elif flat and days >= t1 and not ov["s2_half_fired"] and not ov["s2_full_fired"]:
            ov["s2_half_fired"] = True
            if qty >= 2:
                half = max(1, int(round(qty * 0.5)))
                shots.append({
                    "ticker": ticker, "type": "opt_s2_half", "value": mark,
                    "text": (f"⏱ {ticker} 时间 stop d{t1}: {days} 交易日未收上入场+1N "
                             f"({_fmt(seed + atr0)})。行动【今日内】: 减半 卖 {half}/{qty:g} 张; "
                             f"d{t2} 未改善清仓。(期权退出栈 S2)")})
            else:
                lines.append(f"{ticker}: ⏱ S2 d{t1} 达标但单张不可拆 — d{t2} 清仓线生效中")
    else:
        miss = "seed/armed" if not (seed and armed) else "ATR20@入场未暖机"
        lines.append(f"{ticker}: ⚠️ S2 缺入场锚 ({miss}) — 时间 stop 未接管")

    # S4 DTE 地板
    if dte is not None and dte <= p["opt_s4_dte_floor"] and not ov["s4_fired"]:
        ov["s4_fired"] = True
        shots.append({
            "ticker": ticker, "type": "opt_s4_dte", "value": dte,
            "text": (f"⌛ {ticker} DTE 地板: 剩 {dte} 天 ≤ {p['opt_s4_dte_floor']}。"
                     f"行动【本周内】: close-or-roll — 末段月燃烧 >15%, 与趋势 PnL 对打是物理不是偏好。"
                     f"roll = 新 campaign 重过 IV 与 sizing。(期权退出栈 S4)")})

    # S3 advisory (只播不裁 — IV 独立路径回测不可得, 真实案例 2 笔方向强)
    if seed and atr0 and und_close is not None and armed:
        iv0, iv_now = _iv_at(underlying, armed), _iv_at(underlying, "", latest=True)
        if iv0 and iv_now and (iv0 - iv_now) >= p["opt_s3_iv_drop"] \
                and und_close < seed + atr0:
            lines.append(f"{ticker}: [FYI] S3 advisory — IV 自入场 {iv0:.1f}→{iv_now:.1f} "
                         f"(-{iv0 - iv_now:.1f}pts) 且 {underlying} 未收上入场+1N: "
                         f"vega+theta 慢烧形态 (AAOI200C 案 -51.9%→-5.3%)")
    return shots, lines


def parse_risk_level(risk_status: str) -> Optional[int]:
    digits = "".join(ch for ch in str(risk_status or "") if ch.isdigit())
    return int(digits) if digits else None


def process_reentry_watches(watches: Dict, active_unds: set, close_of, today: str,
                            max_age_days: int = 90) -> List[Dict]:
    """重进协议机械化 (trend-exit-system §3,  回测立项: MU 被 2/4 趋势线
    洗出后 4/13 结构已修复 (68 天), 但无信号提醒重上车 → 反事实少赚 ~$5k, 全部出场
    变体中最大单项损失)。全平离场且结构破坏的标的留 watch; 收盘收复退出时高水位 →
    🔓 一次性告警后移除; 重新建仓 / 超 max_age_days (90d, 必须 > MU 案例的 68d) → 静默移除。
    返回需告警的 [{und, ref, close, since, src}]; watches 原地修改。"""
    fired: List[Dict] = []
    for und in list(watches):
        w = watches[und] or {}
        ref = coerce_float(w.get("ref"))
        if und in active_unds or ref is None:
            watches.pop(und)
            continue
        try:
            age = (date.fromisoformat(today) - date.fromisoformat(str(w.get("since", today)))).days
        except ValueError:
            age = 0
        if age > max_age_days:
            watches.pop(und)
            continue
        c = close_of(und)
        if c is not None and c > ref:
            fired.append({"und": und, "ref": ref, "close": c,
                          "since": w.get("since"), "src": w.get("src")})
            watches.pop(und)
    return fired


# ── 数据装载 ────────────────────────────────────────────────────

def load_positions() -> Dict:
    try:
        return read_json(POSITIONS_PATH)
    except Exception:
        return {"tickers": {}}


def load_guards() -> Dict:
    if EXIT_GUARDS_PATH.exists():
        try:
            doc = read_json(EXIT_GUARDS_PATH)
            if isinstance(doc, dict):
                doc.setdefault("positions", {})
                return doc
        except Exception:
            pass
    return {"as_of": None, "positions": {}}


def _klines_for(underlying: str, n: int = 80) -> List[dict]:
    rows = list(iter_jsonl(KLINE_CACHE_DIR / f"{underlying}.jsonl"))
    return rows[-n:]


def _latest_cached_close(underlying: str) -> Optional[float]:
    rows = _klines_for(underlying, 1)
    return coerce_float(rows[-1].get("close")) if rows else None


def _zone_methodology(doc: Dict, underlying: str) -> str:
    zones = (((doc.get("tickers") or {}).get(underlying) or {})
             .get("agent") or {}).get("zones") or {}
    m = str(zones.get("methodology") or "")
    return "parabolic" if "parabolic" in m else "normal"


def git_hwm_backfill() -> Dict[str, Tuple[float, str]]:
    """--arm 时从 positions.json 的 git 历史回填每 ticker 的历史最高 mark。
    没有这步, 现有赢家的 HWM 从今天起算会低估地板。失败静默降级 (seed=当前)。"""
    best: Dict[str, Tuple[float, str]] = {}
    try:
        shas = [sha for sha, _ in list_position_commits(ROOT)]
    except Exception:
        return best
    for sha in shas:
        try:
            doc = position_at(sha, ROOT)
        except Exception:
            continue
        for tk, body in (doc.get("tickers") or {}).items():
            b = body.get("broker") or {}
            m = coerce_float(b.get("current_price"))
            if m and m > 0:
                cur = best.get(tk)
                if cur is None or m > cur[0]:
                    best[tk] = (m, str(b.get("as_of", ""))[:10])
    return best


# ── Margin guard ────────────────────────────────────────────────

def _fetch_live_account() -> Optional[Dict]:
    """OpenD accinfo (USD 口径, tools-environment.md 硬规则)。moomoo SDK 不在
    (plain python3) 或 OpenD 不可达 → None, 调用方回落 nav_history。"""
    try:
        add_openapi_path()  # openapi skill scripts (`common`) 上 sys.path
        from common import (create_trade_context, get_default_acc_id,
                            get_default_security_firm, parse_trd_env,
                            safe_close, safe_float, safe_get, RET_OK)
        from moomoo import Currency
    except Exception:
        return None
    ctx = None
    try:
        ctx = create_trade_context(security_firm=get_default_security_firm())
        ret, data = ctx.accinfo_query(trd_env=parse_trd_env("REAL"),
                                      acc_id=get_default_acc_id(),
                                      currency=Currency.USD)
        if ret != RET_OK or data is None or data.empty:
            return None
        r = data.iloc[0]
        return {
            "total_assets": safe_float(safe_get(r, "total_assets"), default=None),
            "cash": safe_float(safe_get(r, "cash"), default=None),
            "market_val": safe_float(safe_get(r, "market_val"), default=None),
            "power": safe_float(safe_get(r, "power"), default=None),
            "risk_status": str(safe_get(r, "risk_status", default="")),
            "source": "live",
        }
    except Exception:
        return None
    finally:
        if ctx is not None:
            try:
                safe_close(ctx)
            except Exception:
                pass


def margin_check(live_account: bool, p: Dict = PARAMS) -> Tuple[Optional[Dict], List[Dict], List[str]]:
    """→ (account, persistent_conditions, summary_lines)。6/26 教训: margin 状态
    每轮 brief 必须可见, 不是只在破阈值时。"""
    acct = _fetch_live_account() if live_account else None
    if acct is None:
        rows = list(iter_jsonl(NAV_HISTORY_PATH))
        if rows:
            acct = dict(rows[-1])
            acct["source"] = f"nav_history:{acct.get('date')}"
    if acct is None:
        return None, [], ["Margin: 无数据 (live 不可达且 nav_history 为空)"]

    conds: List[Dict] = []
    power = coerce_float(acct.get("power"))
    mv = coerce_float(acct.get("market_val"))
    cash = coerce_float(acct.get("cash"))
    risk = str(acct.get("risk_status") or "?")
    ratio = (power / mv) if (power is not None and mv) else None
    ratio_txt = f"{ratio:.1%}" if ratio is not None else "?"
    lines = [
        f"Margin: power ${power:,.0f} ({ratio_txt} of mkt val) | cash ${cash:,.0f} | "
        f"{risk} | src {acct.get('source')}"
        if power is not None and cash is not None else f"Margin: {risk} | src {acct.get('source')}"
    ]

    level = parse_risk_level(risk)
    if level is not None and level >= p["margin_risk_crit_level"]:
        conds.append({"ticker": "INFRA", "type": "margin_risk_level", "value": risk,
                      "text": (f"🚨 MARGIN CRITICAL: risk_status {risk}。行动【今日】: "
                               f"主动 delever — 按 conviction 排序减仓, 不要等 broker 替你选 "
                               f"(7/2 NOK 强平教训)。(trend-exit-system §margin)")})
    if ratio is not None:
        if ratio < p["margin_power_crit"]:
            conds.append({"ticker": "INFRA", "type": "margin_power_crit", "value": round(ratio, 4),
                          "text": (f"🚨 MARGIN CRITICAL: power ${power:,.0f} = {ratio_txt} < "
                                   f"{p['margin_power_crit']:.0%} of market_val。任何一天 -2% 即打穿。"
                                   f"行动【今日】: delever 至 ≥{p['margin_power_warn']:.0%} buffer。")})
        elif ratio < p["margin_power_warn"]:
            conds.append({"ticker": "INFRA", "type": "margin_power_warn", "value": round(ratio, 4),
                          "text": (f"⚠️ MARGIN: power ${power:,.0f} = {ratio_txt} < "
                                   f"{p['margin_power_warn']:.0%} of market_val (6/26 红灯位: 1.2%)。"
                                   f"本周内 delever 或停止一切加仓。")})
    # nav staleness (dead-man: 数据断更本身要报警)
    if str(acct.get("source", "")).startswith("nav_history"):
        try:
            last = date.fromisoformat(str(acct.get("date")))
            # ET 口径: SGT 从 ET 正午起已是明天, 用本地日会把 gap 多算 1 天
            gap = (date.fromisoformat(et_today()) - last).days
            if gap > p["nav_stale_days"]:
                conds.append({"ticker": "INFRA", "type": "nav_stale", "value": gap,
                              "text": f"⚠️ INFRA: nav_history 已 {gap} 天未更新 (最后 {last}), margin 监控在盲飞。"})
        except Exception:
            pass
    return acct, conds, lines


# ── 主评估 ──────────────────────────────────────────────────────

def _fmt(v: Optional[float], nd: int = 2) -> str:
    return "?" if v is None else f"{v:.{nd}f}"


def evaluate(doc: Dict, guards: Dict, today: str,
             p: Dict = PARAMS) -> Tuple[Dict, List[Dict], List[Dict], List[str]]:
    """核心评估。→ (guards, one_shot_alerts, persistent_conditions, summary_lines)。
    one_shot 用 fired flag 自去重; persistent 由 brief 的边沿触发机制守门。"""
    one_shot: List[Dict] = []
    persistent: List[Dict] = []
    lines: List[str] = []
    gpos = guards.setdefault("positions", {})
    active: set = set()
    structures_cache: Dict[str, Dict] = {}

    for ticker, body in (doc.get("tickers") or {}).items():
        broker = body.get("broker") or {}
        qty = coerce_float(broker.get("qty")) or 0
        if qty == 0 or broker.get("synthetic"):
            continue
        if not is_managed(body):
            continue  # 结构豁免仓: 不做仓位管理, 不进退出栈 ()
        mark = coerce_float(broker.get("current_price"))
        avg_cost = coerce_float(broker.get("avg_cost"))
        occ = parse_occ(ticker)
        underlying = occ["underlying"] if occ else ticker
        mult = occ["multiplier"] if occ else 1
        dte = None
        if occ:
            try:
                dte = (date.fromisoformat(occ["expiry"]) - date.fromisoformat(today)).days
            except Exception:
                dte = None
        active.add(ticker)

        g = gpos.get(ticker)
        if g is None:  # 自动布防 (新仓入场即武装; --arm 初始批已 git 回填)
            # 口径与 exit_rules_backtest 对齐: 从**入场日**布防, seed 取入场当日
            # 收盘。取不到就退回旧口径 (缺事件不能导致布不了防)。
            entry_date, entry_close = campaign_entry(ticker, underlying)
            g = gpos[ticker] = {
                "armed_date": entry_date or today,
                "hwm_mark": max([v for v in (mark, avg_cost) if v is not None], default=None),
                "hwm_date": today,
                "seed_close": entry_close if entry_close is not None
                else _latest_cached_close(underlying),
                "armed_basis": "entry" if entry_date else "first_seen",
                "ratchet": {"stage": "inactive", "floor": None,
                            "fired_stage1": False, "fired_stage2": False, "hwm_at_fire": None},
                "no_add_lock": False, "lock_ref_high": None,
                "trail": {}, "g01_crossed": {},
            }
            lines.append(f"{ticker}: 新布防 ({g['armed_basis']} {g['armed_date']}, "
                         f"seed {_fmt(g['seed_close'])}, HWM {_fmt(g['hwm_mark'])})")

        # HWM 棘轮
        if mark is not None and (g.get("hwm_mark") is None or mark > g["hwm_mark"]):
            g["hwm_mark"] = mark
            g["hwm_date"] = today
        r = g.setdefault("ratchet", {})
        # 新 leg: fire 后 HWM 再创高 1% → 重新武装
        if r.get("fired_stage1") and r.get("hwm_at_fire") and g.get("hwm_mark") \
                and g["hwm_mark"] > r["hwm_at_fire"] * p["new_leg_pct"]:
            r.update({"fired_stage1": False, "fired_stage2": False, "hwm_at_fire": None})
            lines.append(f"{ticker}: HWM 新高新 leg, 棘轮重新武装")

        stage, floor_new = compute_floor(avg_cost, g.get("hwm_mark"), dte, p)
        prev_floor = coerce_float(r.get("floor"))
        floor = max([v for v in (prev_floor, floor_new) if v is not None], default=None)
        r["stage"], r["floor"] = stage, (round(floor, 4) if floor is not None else None)

        # 趋势死亡线 (正股收盘口径)
        klines = _klines_for(underlying)
        mode = _zone_methodology(doc, underlying)
        prev_trail = coerce_float((g.get("trail") or {}).get("trail"))
        tr = compute_trail(klines, g.get("armed_date") or today, g.get("seed_close"),
                           mode, prev_trail, p)
        g["trail"] = tr
        # 道氏结构标签 (操作手册"昨收结构"数据源; 同 underlying 只算一次)
        if underlying not in structures_cache:
            structures_cache[underlying] = technical.swing_structure(klines) if klines else {}
        g["structure"] = structures_cache[underlying]

        # G-01 档位 (期权 lot 级)
        if occ:
            for hit in g01_crossings(body.get("lots") or {}, mark, g.setdefault("g01_crossed", {}), p):
                one_shot.append({
                    "ticker": ticker, "type": f"g01_{hit['level']}", "value": mark,
                    "text": (f"🎯 {ticker} {hit['tier']} lot ({hit['qty']:g}张@{hit['cost']}) 过 G-01 "
                             f"{hit['level']} (mark {_fmt(mark)} ≥ {hit['threshold']}): "
                             f"行动【今日内】trim {hit['trim_pct']}% ≈ {hit['trim_qty']} 张。"
                             f"let-winner-run 只减不清。无 override 路径, 不执行 = 规则外评 C 起步。(G-01)")})
            # 亏损侧 overlay S1/S2/S4 + S3 advisory (终裁; 棘轮武装前的裸窗口)
            # 入场类分流 : reversal_probe 卡的 gate = 卡自己的 stop_level
            # (反转波段低点, underlying 基), S1 关; 其余 = 授予正股 cap 的 gate (S5g)。
            _agent = body.get("agent") or {}
            _zones = _agent.get("zones") or {}
            if _agent.get("entry_class") == "reversal_probe" \
                    and (_zones.get("stop_level_basis") or "underlying") == "underlying":
                _gate_kw = {"gate_level": coerce_float(_zones.get("stop_level")),
                            "gate_label": "止跌低 (抄底 reversal_probe gate)", "s1_enabled": False}
            else:
                _gate_kw = {"gate_level": granting_gate_level((doc.get("tickers") or {}).get(underlying))}
            ov_shots, ov_lines = option_loss_overlay(
                ticker, g, mark, avg_cost, qty, dte, underlying, klines, p, **_gate_kw)
            one_shot.extend(ov_shots)
            lines.extend(ov_lines)

        # 棘轮触发 (两段式)
        if floor is not None and mark is not None:
            # 破线后 mark 收复地板 +2% (假期陈旧 mark 的边缘破线被新数据推翻, 或
            # stage1 已执行后剩余仓回升) → 重新武装: 地板原位继续保护, 再破再触发。
            # "用新数据重测老规则"是系统原生行为, 不是人工裁量 (MRVL 案)
            if r.get("fired_stage1") and not r.get("fired_stage2") and mark >= floor * 1.02:
                r["fired_stage1"] = False
                r["hwm_at_fire"] = None
                lines.append(f"{ticker}: mark {_fmt(mark)} 收复棘轮地板 {_fmt(floor)} (+2%), stage1 重新武装")
            if not r.get("fired_stage1") and mark < floor and stage == "locked":
                r["fired_stage1"] = True
                r["hwm_at_fire"] = g.get("hwm_mark")
                g["no_add_lock"] = True
                g["lock_ref_high"] = tr.get("highest_close")
                # 期权整张: qty 1 时 50% 无法拆 → 卖 1 (单张仓地板破 = 全保护)
                sell_qty = max(1, int(round(qty * 0.5))) if occ else round(qty * 0.5, 2)
                pct_txt = f"{sell_qty / qty:.0%}" + ("‚ 单张不可拆" if occ and qty == 1 else "")
                locked_gain = (mark - avg_cost) * qty * mult if avg_cost else None
                one_shot.append({
                    "ticker": ticker, "type": "ratchet_stage1", "value": mark,
                    "text": (f"🔒 {ticker} 破利润棘轮地板: mark {_fmt(mark)} < floor {_fmt(floor)} "
                             f"(HWM {_fmt(g['hwm_mark'])} @ {g.get('hwm_date')}, 成本 {_fmt(avg_cost)}, "
                             f"回吐已达上限)。行动【今日内】: 卖 {sell_qty:g}/{qty:g} ({pct_txt}), "
                             f"剩余地板抬至保本 {_fmt(avg_cost)}"
                             + (f", 现价卖出仍锁 ≈ ${locked_gain:,.0f}" if locked_gain else "")
                             + f"。no-add lock ON (解除: {underlying} close > {_fmt(g['lock_ref_high'])})。"
                             f"宏观大跌不豁免 — 想赌修复走重进协议。无 override 路径；不执行 = 规则外评 C 起步。"
                             f"(trend-exit-system 棘轮)")})
            elif not r.get("fired_stage1") and mark < floor and stage == "breakeven":
                # +50% 档破保本地板: 一段式直接清 (赢单不许变亏单)
                r["fired_stage1"] = True
                r["fired_stage2"] = True
                r["hwm_at_fire"] = g.get("hwm_mark")
                g["no_add_lock"] = True
                g["lock_ref_high"] = tr.get("highest_close")
                one_shot.append({
                    "ticker": ticker, "type": "ratchet_breakeven", "value": mark,
                    "text": (f"🔒 {ticker} 曾 +50%+ (HWM {_fmt(g['hwm_mark'])}) 现破保本 "
                             f"{_fmt(avg_cost)} (mark {_fmt(mark)}): 赢单不许变亏单。"
                             f"行动【今日内】: 全平 {qty:g}。重进走结构修复协议。(trend-exit-system 棘轮)")})
            elif r.get("fired_stage1") and not r.get("fired_stage2") and avg_cost and mark < avg_cost:
                r["fired_stage2"] = True
                one_shot.append({
                    "ticker": ticker, "type": "ratchet_stage2", "value": mark,
                    "text": (f"🔒🔒 {ticker} stage2 破保本: mark {_fmt(mark)} < 成本 {_fmt(avg_cost)}。"
                             f"行动【今日内】: 清仓剩余 {qty:g}。(trend-exit-system 棘轮)")})

        # 趋势死亡线触发 (persistent — 收盘持续破线由边沿机制守门)
        if tr.get("broken"):
            g["no_add_lock"] = True
            g["lock_ref_high"] = g.get("lock_ref_high") or tr.get("highest_close")
            persistent.append({
                "ticker": ticker, "type": "trail_break", "value": tr.get("latest_close"),
                "text": (f"📉 {ticker} 趋势死亡线已破: {underlying} close {_fmt(tr.get('latest_close'))} < "
                         f"trail {_fmt(tr.get('trail'))} ({tr.get('mode')}"
                         + (f", ATR {_fmt(tr.get('atr'))}" if tr.get("atr") else "")
                         + f")。行动: 平剩余或减至 base 层; 宏观大跌不豁免, 重进走结构修复协议。"
                           f"(trend-exit-system 趋势死亡线)")})

        # no-add lock 解除 (结构修复: close > lock_ref_high)
        if g.get("no_add_lock") and g.get("lock_ref_high") and tr.get("latest_close") \
                and tr["latest_close"] > g["lock_ref_high"] and not tr.get("broken"):
            g["no_add_lock"] = False
            lines.append(f"{ticker}: 结构修复 (close {_fmt(tr['latest_close'])} > "
                         f"{_fmt(g['lock_ref_high'])}), no-add lock 解除, 可按重进协议评估")
            g["lock_ref_high"] = None

        # 摘要行 (每轮可见)
        #
        # 显示合同 (用户, 前史 「只印 trail 藏了会先触发的
        # 线」): 各基 (card/lock 棘轮/trail) 照常运转, 显示折 max **只给一个止损
        # 值 + 来源**— 词表与 price_reminder 的 stop:card|lock|trail 同源。
        # 期权折进保费空间 (S1 原生, card/trail 是 underlying 基先投影) ——
        # 装配点 = options/premium_proj.binding_stop, 与 brief 卡片同一份。
        status = []
        card_stop = coerce_float(((body.get("agent") or {}).get("zones") or {}).get("stop_level"))
        days = int(coerce_float(((body.get("agent") or {})
                                 .get("zones") or {}).get("stop_level_days")) or 1)
        kw = {}
        if occ:
            kw = {"s1_floor": (round(avg_cost * (1 + p["opt_s1_stop"]), 2)
                               if (avg_cost and g.get("opt_s1_enabled", True)) else None),
                  "mark": mark,
                  "spot": coerce_float((klines[-1] or {}).get("close")) if klines else None,
                  "strike": occ.get("strike"), "dte_days": dte,
                  "iv_pct": coerce_float(broker.get("iv_pct")),
                  "delta": coerce_float(broker.get("delta")),
                  "gate_level": coerce_float((g.get("opt_gate_stop") or {}).get("level")),
                  "gate_days": int((g.get("opt_gate_stop") or {}).get("days") or 1)}
        bs = premium_proj.binding_stop(
            is_option=bool(occ), card_stop=card_stop, card_days=days,
            lock_floor=coerce_float(r.get("floor")), lock_stage=r.get("stage"),
            trail=coerce_float(tr.get("trail")), trail_mode=tr.get("mode"),
            trail_broken=bool(tr.get("broken")), **kw)
        if bs:
            status.append(f"止损 {_fmt(bs['value'])} ({bs['source']} {bs['note']})")
        if g.get("no_add_lock"):
            status.append("no-add🔒")
        st = g.get("structure") or {}
        if st.get("high_label") or st.get("low_label"):
            trend_cn = {"up": "上升", "down": "下降", "transition": "转换"}.get(st.get("trend"), "?")
            status.append(f"结构 {st.get('high_label') or '?'}+{st.get('low_label') or '?'}({trend_cn})")
        if dte is not None and dte < p["low_dte"]:
            status.append(f"DTE {dte}⚠")
        if status:
            lines.append(f"{ticker}: mark {_fmt(mark)} HWM {_fmt(g.get('hwm_mark'))} | "
                         + " | ".join(status))

    # 清掉已平仓 ticker 的 guard; 结构破坏中离场的 → 移入 reentry_watch (重进协议机械化)
    watches = guards.setdefault("reentry_watch", {})
    for tk in [t for t in gpos if t not in active]:
        g = gpos.pop(tk)
        role = (((doc.get("tickers") or {}).get(tk) or {}).get("instrument") or {}).get("role")
        if role in UNMANAGED_ROLES:
            continue  # 结构豁免仓退出追踪时不进重进协议
        broken = bool(g.get("no_add_lock") or (g.get("ratchet") or {}).get("fired_stage1")
                      or (g.get("trail") or {}).get("broken"))
        ref = coerce_float(g.get("lock_ref_high")) \
            or coerce_float((g.get("trail") or {}).get("highest_close"))
        if broken and ref:
            occ = parse_occ(tk)
            und = occ["underlying"] if occ else tk
            cur = watches.get(und)
            if cur is None or (coerce_float(cur.get("ref")) or 0) < ref:
                watches[und] = {"ref": round(ref, 4), "since": today, "src": tk}
    active_unds = set()
    for tk in active:
        occ = parse_occ(tk)
        active_unds.add(occ["underlying"] if occ else tk)
    for hit in process_reentry_watches(watches, active_unds, _latest_cached_close, today):
        one_shot.append({
            "ticker": hit["und"], "type": "reentry_unlock", "value": hit["close"],
            "text": (f"🔓 {hit['und']} 结构修复: close {_fmt(hit['close'])} > 退出时高水位 "
                     f"{_fmt(hit['ref'])} ({hit.get('src')} {hit.get('since')} 离场)。"
                     f"重进协议解锁 — 按新 tranche 走 entry 纪律 (entry_guard / ladder / "
                     f"Iron Rule #2B), 不是恢复旧仓。(trend-exit-system §3)")})
    if watches:
        lines.append("重进 watch: " + ", ".join(
            f"{u} 需 close>{_fmt(coerce_float(w.get('ref')))}" for u, w in watches.items()))
    guards["as_of"] = now_utc_iso()
    return guards, one_shot, persistent, lines


def run_checks(mutate_state: bool = True, live_account: bool = True) -> Dict:
    """portfolio_brief 集成入口。read-only 时不落 exit_guards.json (fired flags
    不持久化, 下轮 mutate 巡逻会再触发 → 宁重复不漏报)。"""
    doc = load_positions()
    guards = load_guards()
    today = et_today()
    guards, one_shot, persistent, lines = evaluate(doc, guards, today)
    acct, margin_conds, margin_lines = margin_check(live_account)
    persistent.extend(margin_conds)
    if mutate_state:
        with state_lock():
            atomic_write_json(EXIT_GUARDS_PATH, guards)
    return {
        "one_shot": one_shot,
        "persistent": persistent,
        "summary_lines": margin_lines + lines,
        "account": acct,
        # per-ticker guard state (trail / ratchet / structure). portfolio_brief
        # needs the fresh trail to print it next to the card's own stop and mark
        # which one binds; re-reading exit_guards.json would be one run stale in
        # --read-only (where nothing is persisted).
        "guards": (guards or {}).get("positions") or {},
        "read_only": not mutate_state,
    }


def arm(json_out: bool = False) -> int:
    """初始布防: 每个活跃持仓建 guard, HWM 从 git 历史回填 (现有赢家的地板
    从历史峰值算, 不从今天起算)。已有 guard 的 ticker 只抬不降。"""
    doc = load_positions()
    guards = load_guards()
    hist = git_hwm_backfill()
    today = et_today()
    gpos = guards.setdefault("positions", {})
    armed = []
    for ticker, body in (doc.get("tickers") or {}).items():
        broker = body.get("broker") or {}
        qty = coerce_float(broker.get("qty")) or 0
        if qty == 0 or broker.get("synthetic"):
            continue
        if not is_managed(body):
            continue  # 结构豁免仓: 不进退出栈 (同主循环,)
        mark = coerce_float(broker.get("current_price"))
        avg_cost = coerce_float(broker.get("avg_cost"))
        occ = parse_occ(ticker)
        underlying = occ["underlying"] if occ else ticker
        h = hist.get(ticker)
        hwm_candidates = [v for v in (mark, avg_cost, h[0] if h else None) if v is not None]
        hwm = max(hwm_candidates) if hwm_candidates else None
        g = gpos.get(ticker) or {}
        prev_hwm = coerce_float(g.get("hwm_mark"))
        gpos[ticker] = {
            "armed_date": g.get("armed_date") or today,
            "hwm_mark": max([v for v in (prev_hwm, hwm) if v is not None], default=None),
            "hwm_date": (h[1] if h and (prev_hwm is None or (hwm or 0) > prev_hwm) else g.get("hwm_date")) or today,
            "seed_close": g.get("seed_close") or _latest_cached_close(underlying),
            "ratchet": g.get("ratchet") or {"stage": "inactive", "floor": None,
                                            "fired_stage1": False, "fired_stage2": False,
                                            "hwm_at_fire": None},
            "no_add_lock": bool(g.get("no_add_lock")),
            "lock_ref_high": g.get("lock_ref_high"),
            "trail": g.get("trail") or {},
            "g01_crossed": g.get("g01_crossed") or {},
        }
        armed.append({"ticker": ticker, "hwm": gpos[ticker]["hwm_mark"],
                      "hwm_src": "git" if h and h[0] == hwm else "current"})
    guards["as_of"] = now_utc_iso()
    with state_lock():
        atomic_write_json(EXIT_GUARDS_PATH, guards)
    # 布防后立即跑一轮评估, 让 floor/trail 生效并输出现状
    out = run_checks(mutate_state=True, live_account=False)
    result = {"armed": armed, "summary": out["summary_lines"],
              "one_shot": [c["text"] for c in out["one_shot"]],
              "persistent": [c["text"] for c in out["persistent"]]}
    print(json.dumps(result, ensure_ascii=False, indent=2) if json_out
          else "\n".join([f"armed {a['ticker']} HWM {a['hwm']} ({a['hwm_src']})" for a in armed]
                         + out["summary_lines"]
                         + [c["text"] for c in out["one_shot"] + out["persistent"]]))
    return 0


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description="赢家侧退出栈监控 (trend-exit-system)")
    ap.add_argument("--arm", action="store_true", help="初始布防 (git 回填 HWM)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--read-only", action="store_true", help="只算不写 state")
    a = ap.parse_args(argv)
    if a.arm:
        return arm(a.json)
    out = run_checks(mutate_state=not a.read_only, live_account=not a.read_only)
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    else:
        for ln in out["summary_lines"]:
            print(ln)
        for c in out["one_shot"] + out["persistent"]:
            print(c["text"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
