#!/usr/bin/env python3
"""regime_transition.py — gate 触发即机械转移卡片状态。

系统的核心是 gate: **gate 是状态转移锚点, 每个 gate 有自己一整套 zone**。
卡片持有一个滚动窗口, 三个状态:

    agent.last_regime   破了 current 的 stop 之后落到哪 — 纯观察, cap 恒 0
    agent.zones         current — 现在生效的那套
    agent.next_regime   收复 gate 之后升到哪 — 可授权, 带 cap

转移是**机械的**: 确认逻辑全部前置在 gate 定义里 (收盘×N 日 + 量比 + 收盘口径
而非盘中), 触发即转移。没有人工确认, 没有缓冲, override 不阻断 —— 三条替代
路径在本系统的记录里都失败过:

  · 人工确认  = NOK 7/2 的形状 (G-01 客观触发、快照在案、零执行)
  · 缓冲确认  = trend-exit-system §8 已回测否决 (-$2.0~2.4k)
  · 条件转移  = 7/4 NOK 裁决边界禁止 (宏观不豁免)

退出动作 (平仓/减仓) 与状态转移是两件事, 不互为前提: 即使对退出走了书面
override, 状态转移照常发生。价格事实成立就是成立 —— 否则 override 会污染
状态机, 留下"仓位还在、卡片假装 regime 没变"的卡。

转移后:
  机械项 (stop / no_chase / cap / participation) 立即生效
  provisional 项 (z1 / z2 / trim) 落一版**观察级**(source=auto) 并挂
  pending_repaint —— 补画前 entry_guard 输出 NEEDS_CARD, 不授权入场。
  下行转移不需要授权级补画 (新 current 的 cap 是 0, 本来就不该入场)。

CLI:
    python3 quant/decision/regime_transition.py --check [--json]     # 只报不写
    python3 quant/decision/regime_transition.py --apply [--json]     # 执行转移
"""
from __future__ import annotations

from contextlib import nullcontext
from copy import deepcopy

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 直接 `python3 quant/<层>/<模块>.py` 时让 `quant.*` 可 import

from quant.core import env, technical  # noqa: F401  (.env → os.environ)

import argparse
import json
from datetime import date
from typing import Dict, List, Optional

from quant.sync import state_io  # noqa: E402
from quant.core.holdings import is_managed  # noqa: E402  结构豁免仓不进状态机
from quant.core.episodes import (# noqa: E402  ①修复域判别式: 与回测同一份 (决策点 E)
    MIN_BARS, breakdown_in_window, episodes_of, merge_bars_by_date)
from quant.core.io_json import iter_jsonl, state_lock  # noqa: E402
from quant.core.num import coerce_float  # noqa: E402
from quant.core.occ import is_option_ticker  # noqa: E402  期权卡不进状态机 (regime 是正股层语义)
from quant.core.paths import KLINE_CACHE_DIR, KLINE_DEEP_DIR  # noqa: E402
from quant.core.sectors import benchmark_for  # noqa: E402  量比基准映射 (冻结表)
from quant.core.sizing import NOTIONAL_CAP  # noqa: E402
from quant.core.timeutil import et_today  # noqa: E402
from quant.core import schema as _schema  # noqa: E402  每票隔离校验 (与 state_io 写闸同一份)


class TransitionCardError(ValueError):
    """这一张卡的转移落不了库 —— 其余各票不受影响。

    若把整本文件当一个事务 (逐票 apply 完再 `write(doc)`), 任何一票让 schema 不过就
    **全书零转移** —— 一张卡的空 cap 会连带扣住同日已达标的其他卡, 报告还渲染成
    「盘后：无 regime 转移」。一张卡的字段缺失不该有全局爆炸半径。
    """


PARAMS = {
    # 算钱口径统一 ATR20 (stop 距离 / sizing 必须同一个 N, 见 trend-exit-system §2b)
    "atr_period": 20,
    "swing_wing": 2,
    # 上行 gate 确认缺省 ×2 日 (zone-maintenance 检查表 #3 定版口径)。
    "default_days": 2,
    # A close-confirmed stop breach defaults to one completed bar. Upward gates
    # retain their separate two-close default.
    "stop_days_default": 1,
    # 观察级 zone 只锚近端结构 (~6 个月)。一年前的 pivot 在"价格下去大概到哪"
    # 这个用途上没有信息, 只会拉出一条几十个百分点宽的假区间。
    "pivot_lookback": 120,
    # 破位线下方多少个 ATR 内没有 pivot = 空气区。这才是该被看见的东西:
    # 支撑不在附近, 不是"下一档在哪"。
    "air_pocket_atr": 2.0,
    # 幅度快车道 (ladder_entry 采纳 k=0.5; **证据基座  换血**)
    # 首日收盘 > gate + 0.5×ATR20 → 当日即转移, 不等第二天。
    # ⚠️ ladder_entry 的原对照混淆了「执行结构」与「时点」(C@D1 vs 单点@D2, ledger A7),
    # 「早一天」由 ladder_timing (双构造, 两臂都 ladder) + stop_cost_guard (⑤换代价守卫,
    # U/C/T 先锁后跑) 重判 support: 风险N −0.16N / R +0.226 显著 / 止损**代价**−0.072N
    # 显著更省 (次数 +10.6pp 但每次损失更小 —— 守卫的量纲跟止损模型走, 见该设计稿 §1)。
    # 残留边界: 带量腿的窄池上 R 含 0 (机制级支持); +12 个月以代价守卫盲测复验。
    # ⚠️ 5.5% 的失败成本: 08-07 原话「用 at-risk cap 管, 不作否决理由」→ 那笔账由
    # stop_cost_guard v2 的代价守卫付讫 (止损**代价**显著更省, 见上); at-risk cap (b)
    # 本体  同日二裁后**立项接线**(AT_RISK_CAP_FRAC=0.05, ledger N1,
    # 规格 risk_unit_design §b1) —— 现在是真实存在的控制点, 但 fastlane 的证据
    # 基座仍是代价守卫, 不是它。
    "fastlane_k": 0.5,
}


def _klines(ticker: str) -> List[dict]:
    p = KLINE_CACHE_DIR / f"{ticker}.jsonl"
    return list(iter_jsonl(p)) if p.exists() else []


def _closes(klines: List[dict], n: int) -> List[float]:
    out = [coerce_float(r.get("close")) for r in klines]
    return [c for c in out if c is not None][-n:] if n else []


def _close_rows(klines: List[dict], n: int) -> List[Dict]:
    """达标收盘的**日期 + 收盘价** (与 `_closes` 同一过滤口径)。
    : 转移收据要能逐条指出「哪两天的收盘达标」, 不再让人回去翻 kline。"""
    rows = [{"date": str(r.get("time") or "")[:10], "close": coerce_float(r.get("close"))}
            for r in klines]
    return [r for r in rows if r["close"] is not None][-n:] if n else []


def _atr(klines: List[dict]) -> Optional[float]:
    s = technical.atr(klines, PARAMS["atr_period"])
    return s[-1] if s and s[-1] else None


REL_FLOOR = 0.4     # RelVR 分母地板 — 相对量比口径锁定值
RECLAIM_VOL_RATIO_MIN = 1.1   # 量腿阈值 canonical = 证据标准 (zone-maintenance 检查表 #3)。
                              # 阈值 1.1 由预注册回测 (adaptive_volume_leg round-3) 确定:
                              # 非劣效判据锁在看数据前; 1.0 被否; 收紧有代价 (更高阈值期望更差)。
                              # 缺省即整条跳过 (volume_leg None→True)。
                              # 🚫 研究层**不再** import 本常量: 历史世界 (W1/W1F/W5) 冻结在裁决当时的 1.2,
                              #    否则回归门与既往裁决不可复现 (research/*_backtest.py HISTORICAL_VOL_RATIO)。
                              # 复测 +12 月: Δmean 下界破 −0.10pp 或 T60 止损率上界破 +3pp → 撤回。

_BENCH_RATIOS: Dict[str, Dict[str, float]] = {}


def _bench_ratio(bench: str, day: str) -> Optional[float]:
    """基准 (QQQ/SMH) 在 day 的 20 日量比 (含当日, technical.volume_ratio 口径)。
    缓存无该日 → None (volume_leg 落绝对兜底)。"""
    if bench not in _BENCH_RATIOS:
        rows = _klines(bench)
        vols = [int(r.get("volume") or 0) for r in rows]
        ratios = technical.volume_ratio(vols, 20)
        _BENCH_RATIOS[bench] = {str(rows[j].get("time") or "")[:10]: ratios[j]
                                for j in range(len(rows)) if ratios[j] is not None}
    return _BENCH_RATIOS[bench].get(day)


def rel_volume_detail(klines: List[dict], ticker: Optional[str] = None) -> Dict:
    """RelVR 一次计算的**结构化收据**。`rel_volume` 是它的三元视图, 两者同一份数字。

    转移达标条件必须可读: 此前量腿证据只活在
    note 散文里 —— 「量比(rel SMH 0.51) 1.68」要靠人去解析才知道基准是谁、
    读数多少、对齐在哪一天。转移收据与 regime_history 现在存结构化字段,
    note 仍逐字保留 (gate_ladder_report.vol_short 靠末位 token 取数)。
    """
    vols = [int(r.get("volume") or 0) for r in klines]
    ratios = technical.volume_ratio(vols, 20)
    r = ratios[-1] if ratios and ratios[-1] is not None else None
    day = str(klines[-1].get("time") or "")[:10] if klines else None
    bench = benchmark_for(ticker)   # None/未知 → QQQ (与既有行为一致, 不要加 falsy 守卫)
    if r is None:
        return {"value": None, "note": "量比不可算(K线不足)", "degraded": False,
                "mode": "unavailable", "own_ratio": None, "benchmark": bench,
                "benchmark_ratio": None, "date": day}
    b = _bench_ratio(bench, day)
    if b is None:
        return {"value": r, "note": f"量比(abs兜底,{bench}缺{day or '?'}) {r:.2f}",
                "degraded": True, "mode": "absolute_fallback", "own_ratio": r,
                "benchmark": bench, "benchmark_ratio": None, "date": day}
    value = r / max(b, REL_FLOOR)
    return {"value": value, "note": f"量比(rel {bench} {b:.2f}) {value:.2f}",
            "degraded": False, "mode": "relative", "own_ratio": r,
            "benchmark": bench, "benchmark_ratio": b, "date": day}


def rel_volume(klines: List[dict], ticker: Optional[str] = None):
    """RelVR 的**计算**单一真源 → (value, note, degraded)。value=None = 算不出。

    从 `volume_leg` 里提出来: 判读层 (`report/chart_read`) 要的是
    **读数本身**, 授权层要的是**它过没过 min_ratio**。两个需求共读这一份 ——
    否则判读层会手搓第二个 RelVR, 而那正是本仓「同概念双判别式」的形状
    (单卡长期无有效退出线的已知失效形状)。`volume_leg` 的行为一字未改。
    实现搬进 `rel_volume_detail`, 本函数是它的三元视图, 契约不变。
    """
    d = rel_volume_detail(klines, ticker)
    return d["value"], d["note"], d["degraded"]


def streak_above(klines: List[dict], level: float) -> int:
    """收盘 > level 的**连续**日数, 从最新 bar 往回数 (跌回 = 归零)。
    = entry_guard._gate_streak 的长度 (同口径, 只数已完成收盘); 量腿对齐日的输入。"""
    n = 0
    for r in reversed(klines or []):
        c = coerce_float(r.get("close"))
        if c is None or c <= level:
            break
        n += 1
    return n


def volume_leg(klines: List[dict], min_ratio: Optional[float],
               ticker: Optional[str] = None, streak: int = 1):
    """量腿 canonical (relative_volume 回测裁决 R): **相对量比**
    RelVR = 个股20日量比 / max(基准量比, 0.4) ≥ min_ratio; 基准 = 半导体→SMH
    其余→QQQ (core.sectors 冻结表)。回测: 2,874 修复 episodes, W1−W0 ΔT60
    +0.48pp CI90 [+0.16,+0.79], 止损率 +0.95pp (守卫 +3pp 内), 覆盖率 +3.7pp。
    基准缺当日 → **绝对量比兜底, note 必须说出口**(降级不静默)。
    → (ok, note); min_ratio 缺省 = 卡片没设量腿 → (True, None)。
    消费方: detect_transition (授权) + gate_ladder_report (播报) — 同一函数,
    报表与转移机永不分叉。

    **对齐日 (预注册回测 volume_clock round-2 确定, +12 月复测)**:
    `streak` = 连续收盘 > gate 的日数 (调用方按 `streak_above` 数出来), 量腿在 streak 内
    **任一日** RelVR ≥ min_ratio 即过 —— 「放量突破 + 平量站稳」不再被罚 (突破日 RelVR 1.27 ✓ /
    跟进日 ✗ 的签名); 跌回归零, day-1 的量随之作废。streak=1 =
    旧口径 (快车道首日 / ×1 gate 不受影响)。过在早前某日时 note 写明是哪一日, 数字仍在
    末位 (gate_ladder_report.vol_short 靠末位 token 取数)。"""
    d = volume_leg_detail(klines, min_ratio, ticker, streak=streak)
    return d["ok"], d["note"]


def volume_leg_detail(klines: List[dict], min_ratio: Optional[float],
                      ticker: Optional[str] = None, streak: int = 1) -> Dict:
    """量腿一次判定的**结构化收据** —— `volume_leg` 是它的二元视图。

    转移达标条件必须可读: 达标条件此前只能从
    note 散文里读出来, 且格式随对齐日变化 (最后一日过 = 只有数字; 早前某日过 =
    多一段「MM-DD streak第N日 X」)。转移收据改存这里的字段, note 逐字不动。
    判定逻辑一字未改: 同一个循环, 同一个 `rel_volume_detail`。
    """
    k = max(1, int(streak or 1))
    out = {"ok": True, "note": None, "required": min_ratio, "actual": None,
           "mode": "skipped", "benchmark": None, "benchmark_ratio": None,
           "own_ratio": None, "aligned_date": None, "aligned_streak_day": None,
           "streak": k, "degraded": False}
    if not min_ratio:
        return out
    latest = None
    for back in range(min(k, len(klines))):
        sub = klines[:len(klines) - back] if back else klines
        d = rel_volume_detail(sub, ticker)
        val, note = d["value"], d["note"]
        if back == 0:
            latest = d
        if val is not None and val >= min_ratio:
            if back != 0:
                day = str(sub[-1].get("time") or "")[:10]
                head, _, num = note.rpartition(" ")
                note = f"{head}; {day[5:] or '?'} streak第{k - back}日 {num}"
            return {**out, "ok": True, "note": note, "actual": val,
                    "mode": d["mode"], "benchmark": d["benchmark"],
                    "benchmark_ratio": d["benchmark_ratio"], "own_ratio": d["own_ratio"],
                    "aligned_date": d["date"], "aligned_streak_day": k - back,
                    "degraded": d["degraded"]}
    d = latest or {"value": None, "note": "量比不可算(K线不足)", "mode": "unavailable",
                   "benchmark": None, "benchmark_ratio": None, "own_ratio": None,
                   "date": None, "degraded": False}
    return {**out, "ok": False, "note": d["note"], "actual": d["value"],
            "mode": d["mode"], "benchmark": d["benchmark"],
            "benchmark_ratio": d["benchmark_ratio"], "own_ratio": d["own_ratio"],
            "aligned_date": None, "aligned_streak_day": None, "degraded": d["degraded"]}


# ── 观察级 regime 的机械推导 ────────────────────────────────────

def derive_observation_regime(klines: List[dict], break_level: float, *,
                              days: int = 2) -> Dict:
    """破位之后落到哪 —— 机械一版, 只够看, 不够下单。

    锚: 破掉的那条线是天花板, 往下按 pivot 低点分档, 找不到就按 ATR20 退。
    repair_gate = 收复被破的那条线 —— 回到上一个 regime 的唯一路径。

    zone-maintenance 明令禁止"仅凭 K 线更新 zone, 不查宏观 context", 所以这版
    一律标 provisional + source=auto: 进显示与风险计算, 不进授权。
    """
    _, swing_lows = technical.swing_points(klines[-PARAMS["pivot_lookback"]:],
                                           PARAMS["swing_wing"])
    atr = _atr(klines) or break_level * 0.05
    below = sorted({round(v, 2) for _, v in swing_lows if v < break_level}, reverse=True)

    z1_lo = below[0] if below else round(break_level - atr, 2)
    z2_lo = below[1] if len(below) >= 2 else round(z1_lo - atr, 2)
    stop = below[2] if len(below) >= 3 else round(z2_lo - atr, 2)
    # 退化保护: pivot 挤在一起时不要产出 lo >= hi 的区间
    if z1_lo >= break_level:
        z1_lo = round(break_level - atr, 2)
    if z2_lo >= z1_lo:
        z2_lo = round(z1_lo - atr, 2)
    if stop >= z2_lo:
        stop = round(z2_lo - atr, 2)

    gap_atr = (break_level - z1_lo) / atr if atr else None
    air = bool(gap_atr and gap_atr > PARAMS["air_pocket_atr"])
    out = {
        "name": f"observation_below_{break_level:g}",
        "cap_pct_nav": 0,
        "participation": "gate_only",
        "source": "auto",
        "provisional": ["accumulation_zone_1", "accumulation_zone_2", "trim_zone"],
        "zones": {
            "accumulation_zone_1": [z1_lo, round(break_level, 2)],
            "accumulation_zone_2": [z2_lo, z1_lo],
            "stop_level": stop,
            "stop_level_days": days,
        },
        "repair_gate": {"level": round(break_level, 2), "days": days,
                        "label": f"收复 {break_level:g} = 回到上一 regime"},
        "air_pocket": air,
        "derivation": (
            f"机械推导 (source=auto, 观察级): 破位线 {break_level:g} 作天花板; "
            f"z1/z2/stop 依次锚近 {PARAMS['pivot_lookback']} 根 K 的 pivot 低点 "
            f"{below[:3] or '(无, 按 ATR20 退)'}; ATR20={atr:.2f}。"
            "**未查宏观 context, 未做四信号收敛**—— cap 0 只作'价格下去大概到哪'的底, "
            "不授权任何入场。"
),
    }
    if air:
        out["derivation"] += (
            f" ⚠️ 空气区: 破位线下方 {gap_atr:.1f}N 内无 pivot 支撑 "
            f"(最近一档 {z1_lo:g} = -{(break_level - z1_lo) / break_level * 100:.0f}%), "
            "z1 宽度不是'吸纳区'而是'没有结构'。")
    return out


# ── 转移检测 ────────────────────────────────────────────────────

def detect_transition(ticker: str, card: Dict, klines: Optional[List[dict]] = None,
                      today: Optional[str] = None) -> Optional[Dict]:
    """None = 没触发。下行优先于上行 (风险先行, 且两者实际互斥)。

    🚫 **期权合约永不进状态机**(修, 见下)。regime 是正股层语义 ——
    `core/schema.regime_shape` 第一句就是「期权卡不参与 (白名单/加仓授权是正股层
    语义)」, 而 `derive_observation_regime` 造出来的 zones 结构上带不出
    `stop_level_basis`, 一个合法的期权卡根本无法由本模块产出。

    源案例  GOOGL270115C390000: 8/10 起活仓合约进 `kline_cache`
    刷新集 (期权卡按 premium 口径画线要读合约自己的日线), 而本模块无期权分支 ——
    于是拿**保费**收盘 20.70 / 21.26 去比卡片那条 **underlying 基准**的
    stop 358.5, 造出一条假破位; apply 后 zones 丢掉 `stop_level_basis`,
    整个 positions 桶的写入被 schema 拦下 → **当日零转移被应用, 全票状态机变黑**。
    (写入是整桶原子的, 所以一张坏卡的爆炸半径 = 所有票。拦下是对的, 但触发源是假的。)
    期权的退出管理归 `exit_guard` 的期权 overlay —— GOOGL 的保费地板告警当天
    正常响了, 说明该管的那条链没坏, 坏的是这条不该管它的。
    """
    if is_option_ticker(ticker):
        return None
    klines = _klines(ticker) if klines is None else klines
    if not klines:
        return None
    agent = card.get("agent") or {}
    zones = agent.get("zones") or {}
    today = today or et_today()

    stop = coerce_float(zones.get("stop_level"))
    days = max(1, int(coerce_float(zones.get("stop_level_days")) or PARAMS["stop_days_default"]))
    if stop is not None:
        closes = _closes(klines, days)
        if len(closes) == days and all(c < stop for c in closes):
            return {"ticker": ticker, "direction": "down", "level": stop, "days": days,
                    "closes": closes, "date": today,
                    "confirm": {"mode": "stop_break", "days": days, "level": stop,
                                "closes": _close_rows(klines, days), "volume_required": False},
                    "reason": f"收盘 {closes[-1]:g} < 卡片 stop {stop:g} 连续 {days} 日"}

    nr = agent.get("next_regime") or {}
    gate = nr.get("entry_gate") or {}
    level = coerce_float(gate.get("level"))
    if level is not None:
        gdays = max(1, int(coerce_float(gate.get("days")) or PARAMS["default_days"]))
        closes = _closes(klines, gdays)
        if len(closes) == gdays and all(c > level for c in closes):
            streak = streak_above(klines, level)
            vol = volume_leg_detail(klines, coerce_float(gate.get("volume_ratio_min")),
                                    ticker, streak=streak)
            vok, vnote = vol["ok"], vol["note"]
            if vok:
                return {"ticker": ticker, "direction": "up", "level": level,
                        "days": gdays, "closes": closes, "date": today,
                        "to": nr.get("name"), "fastlane": False,
                        "confirm": {"mode": f"close_x{gdays}", "days": gdays, "level": level,
                                    "closes": _close_rows(klines, gdays), "streak": streak,
                                    "volume_required": bool(vol["required"])},
                        "volume": vol,
                        "reason": f"收盘 {closes[-1]:g} > gate {level:g} 连续 {gdays} 日"
                                  + (f"; {vnote}" if vnote else "")}
        fl = _fastlane(klines, level, gate, ticker, nr, today)
        if fl:
            return fl
    return None


def _repair_domain(ticker: str, klines: List[dict], level: float):
    """⓪ 适用域: 这条 gate 是不是①修复路径? → (ok, note)。

     接线 (用户 批「接判别式」, 前提「跑完回测再动」已满足)。证据链:
      · gate_path_taxonomy (08-08): 按结构身份分四类, ②③④全部不采纳维持 ×2,
        ③箱体顶还有**显著负 R**的正面反证 (−0.672 CI 排除 0)
      · ladder_timing + stop_cost_guard (08-08/09): ①修复上「早一天」六判据全过
        (风险N −0.16 / R +0.226 / 止损**代价**−0.072N 全显著, 双构造一致)
    判据只要 `broke` 一道 (非①一律 ×2, 不必分②③④):
    **120 日窗内存在 `episodes_of` 破位日且其收盘 < 本 gate**—— 与回测 import
    同一份 `core/episodes` 实现 (决策点 E 当初就是为这一天立的)。
    序列 = 深度缓存 + 常规缓存按日期去重合并 (E 锁定口径; 深度缺席时常规 221+ 根
    也够 MIN_BARS=180)。**判不了 = 不给快车道**(断层/太短都算) —— 保守方向:
    错拒的代价只是等一天 ×2, 错放的代价是给③发当日转移 + Today 当日重授权。
    """
    dp = KLINE_DEEP_DIR / f"{ticker}.jsonl"
    deep = list(iter_jsonl(dp)) if dp.exists() else []
    bars, gap = merge_bars_by_date(deep, klines) if deep else (klines, None)
    closes = [c for c in (coerce_float(r.get("close")) for r in bars) if c is not None]
    if gap:
        return False, f"域不可判({gap}) — 保守 ×2"
    if len(closes) < MIN_BARS:
        return False, f"域不可判(序列 {len(closes)} < {MIN_BARS} 根) — 保守 ×2"
    eps = episodes_of(closes, technical.sma(closes, 60))
    broke, _, n_below = breakdown_in_window(eps, closes, len(closes), level)
    if broke:
        return True, f"域=①修复({n_below}次破位在120日窗内)"
    return False, "域外(120日窗内无破位, 非修复路径) — 维持 ×2"


def _fastlane(klines: List[dict], level: float, gate: Dict, ticker: str,
              nr: Dict, today: str) -> Optional[Dict]:
    """幅度快车道: **首日**收盘 > gate + k×ATR20 → 当日转移, 不等第二天。

    四个都必须成立, 缺一不可:
      ⓪ **适用域 = ①修复路径**——  接线 (`_repair_domain`)。采纳域外的
         ②③④ 一律 ×2: 证据只覆盖破位后收复的 gate, ③还有显著负 R 反证
      ① **首日**—— 前一根收盘还在 gate 之下。已经站上去好几天却没凑满 ×2 的,
         不许事后补一张快车票 (那是另一种情形, 回测没测过)
      ② 幅度 —— close > level + fastlane_k × ATR20
      ③ 量腿照旧 —— relative_volume  裁决的那条独立维度, 快车道不豁免它
    """
    two = _closes(klines, 2)
    if len(two) < 2 or two[0] > level or two[1] <= level:      # ①: 昨在下, 今在上
        return None
    atr = _atr(klines)
    k = PARAMS["fastlane_k"]
    if not atr or two[1] <= level + k * atr:                    # ②
        return None
    dok, dnote = _repair_domain(ticker, klines, level)          # ⓪ (放①②后省 IO)
    if not dok:
        return None
    vol = volume_leg_detail(klines, coerce_float(gate.get("volume_ratio_min")), ticker)
    vok, vnote = vol["ok"], vol["note"]
    if not vok:                                                 # ③
        return None
    over = (two[1] - level) / atr
    return {"ticker": ticker, "direction": "up", "level": level,
            "days": 1, "closes": two[1:], "date": today, "to": nr.get("name"),
            "fastlane": True, "fastlane_over_n": round(over, 2),
            "confirm": {"mode": "fastlane", "days": 1, "level": level,
                        "closes": _close_rows(klines, 1), "over_n": round(over, 2),
                        "threshold_n": k, "domain": dnote,
                        "volume_required": bool(vol["required"])},
            "volume": vol,
            "reason": f"⚡快车道: 首日收盘 {two[1]:g} > gate {level:g} + {k}N "
                      f"(超出 {over:.2f}N) — 幅度替代第二日确认; {dnote}"
                      + (f"; {vnote}" if vnote else "")}


# ── 转移执行 ────────────────────────────────────────────────────

def _fired_gate_anchor(next_regime: Dict, zones: Dict, level: float) -> Optional[Dict]:
    """触发转移的那道 gate 自带的 anchored_on —— 平移进 repair_gate。

     实证 (8/5 MU/INTC 转移): repair_gate 落地即缺锚 (📋 审计缺口),
    而源 gate 明明带着 anchored_on —— 字段在转换中被丢。锚跟着 gate 走,
    不跟容器走。找不到 (老卡 gate 本就没锚) → None, 走 gate_audit_gaps
    提示补画, 不造假锚。
    """
    cands = [g for g in (zones.get("reclaim_gates") or []) if isinstance(g, dict)]
    eg = (next_regime or {}).get("entry_gate")
    if isinstance(eg, dict):
        cands.append(eg)
    for g in cands:
        try:
            if abs(float(g.get("level")) - float(level)) > 1e-6:
                continue
        except (TypeError, ValueError):
            continue
        a = g.get("anchored_on")
        if isinstance(a, dict) and a.get("date"):
            return dict(a)
    return None


def _as_observation(regime_zones: Dict, *, repair_gate: Dict, name: str) -> Dict:
    """把一套曾经可授权的 zone 降级成观察层 —— cap 归零是硬要求。"""
    return {
        "name": name,
        "cap_pct_nav": 0,
        "participation": "gate_only",
        "source": "demoted",
        "provisional": ["accumulation_zone_1", "accumulation_zone_2", "trim_zone"],
        "zones": dict(regime_zones or {}),
        "repair_gate": repair_gate,
        "derivation": "由上一轮 current 降级而来 (转移时机械平移, 数值不变, 资格归零)",
    }


def apply_transition(card: Dict, tr: Dict, klines: Optional[List[dict]] = None) -> Dict:
    """滚动窗口平移。返回改过的 card (原地修改 agent)。"""
    klines = _klines(tr["ticker"]) if klines is None else klines
    agent = card.setdefault("agent", {})
    cur_zones = dict(agent.get("zones") or {})
    action = agent.setdefault("action", {})
    history = agent.setdefault("regime_history", [])
    entry = {"date": tr["date"], "direction": tr["direction"],
             "level": tr["level"], "reason": tr["reason"],
             "fastlane": bool(tr.get("fastlane"))}
    # 达标条件同时以结构化形式落卡 —— 此前只有 reason 散文,
    # 事后要复核「那天靠什么达标的」必须解析中文句子 (gate_count 只数条数)。
    for key in ("confirm", "volume"):
        if tr.get(key):
            entry[key] = tr[key]
    history.append(entry)

    if tr["direction"] == "up":
        nr = dict(agent.get("next_regime") or {})
        # last ← 旧 current (降级, 回来的路 = 刚刚过的那道 gate)。
        # fastlane 标记随 repair_gate 持久化 (用户 抓漏:  fastlane
        # 落地时只活在 tr dict 里, 落卡即丢, 唯一痕迹是 reason 散文的"⚡" ——
        # entry_guard 的 repair_reclaim 参与权阶梯只好拿 closes 重推弱化版签名。
        # 判别式必须与转移机同一份: 消费者读这个标记, 不自带第二实现)。
        repair_gate = {"level": tr["level"], "days": tr["days"],
                       "fastlane": bool(tr.get("fastlane")),
                       "label": f"收复 {tr['level']:g} = 回到 {nr.get('name') or 'current'}"}
        anchor = _fired_gate_anchor(nr, cur_zones, tr["level"])
        if anchor:
            repair_gate["anchored_on"] = anchor
        agent["last_regime"] = _as_observation(
            cur_zones, repair_gate=repair_gate, name=f"demoted_at_{tr['date']}")
        # current ← 旧 next
        new_zones = dict(nr.get("zones") or {})
        # bfloor_buf 写入强制点 (ledger A3,  接线): 修复/上行转移后的
        # regime 地板 = 刚收复的 gate − 0.5×ATR20(转移日), 确认口径收盘 ×1 日 ——
        # round-2b (, gate_backtest2b) 用户 批的现行约定。此前没有任何
        # 写入路径强制它: 转移机原样搬画卡时的旧 stop, live 六张卡合规全靠人记得。
        # ATR 算不出 (序列不足) → 保持旧行为, stop 留给重画, 不编数。
        atr20 = _atr(klines)
        if atr20:
            prior_stop = new_zones.get("stop_level")
            bfloor = round(tr["level"] - 0.5 * atr20, 2)
            new_zones["stop_level"] = bfloor
            new_zones["stop_level_days"] = 1
            note = (f"bfloor_buf: 收复线 {tr['level']:g} − 0.5×ATR20@{tr['date']} "
                    f"({atr20:.2f}) = {bfloor:g}, 收盘×1日 (round-2b 2026-07-30)")
            if isinstance(prior_stop, (int, float)) and round(float(prior_stop), 2) != bfloor:
                note += f"; 覆盖画卡值 {prior_stop:g}"
            new_zones["stop_derivation"] = note
        # 旧卡显式写过口径才随卡带; 不再用 default_days=2 兜底盖章 —— 那会把
        # 一张没表态的卡悄悄钉成 ×2, 与下行缺省 ×1 (stop_days_default) 相矛盾。
        if cur_zones.get("stop_level_days"):
            new_zones.setdefault("stop_level_days", cur_zones["stop_level_days"])
        agent["zones"] = new_zones
        # 升档同样要落 current_regime —— 否则"上一次是向下转移"留下的 cap 0
        # 会残留下来, 把一个已经修复的仓位永久锁死。
        # cap 必须是数字 —— current_regime 那边 schema 要求 numeric, 这里原样搬
        # 会把「卡上没写」兑换成**整本文件写入被拒** (达标的转移一笔未落, 报告还印成
        # 「无转移」)。schema 在
        # 写卡期就拦住带 gate 的空 cap; 这里是第二道闸, 转移不靠运气。
        granted = nr.get("cap_pct_nav")
        if not isinstance(granted, (int, float)) or isinstance(granted, bool):
            raise TransitionCardError(
                f"next_regime.cap_pct_nav 不是数字 ({granted!r}) — 这道 gate 授予多少"
                f"卡上没说, 转移无法落库。中间站写显式 0, 授权站写 {NOTIONAL_CAP}")
        agent["current_regime"] = {
            "name": nr.get("name") or f"promoted_at_{tr['date']}",
            "cap_pct_nav": granted,
            "participation": nr.get("participation") or "zone_eligible",
            "source": nr.get("source") or "auto",
            "since": tr["date"],
            "reason": tr["reason"],
        }
        action["intent"] = "hold"
        # next ← preview 的骨架 (只有 gate 和 cap, zone 待推导)
        preview = dict(nr.get("next_gate_preview") or {})
        # preview 块历来不带 cap (它只是「再上一档在哪」), 于是骨架的 cap 是 None ——
        # 而骨架带着 entry_gate, 下一次转移会在它身上重演空 cap 的全局失败。
        # 继承本次刚授予的 cap: 骨架本来就锁在 pending_repaint.slots=["next"] 里
        # (补画前 entry_guard = NEEDS_CARD), 继承值是占位不是授权, 补画时重新回答。
        preview_cap = preview.get("cap_pct_nav")
        if not isinstance(preview_cap, (int, float)) or isinstance(preview_cap, bool):
            preview_cap = granted
        agent["next_regime"] = {
            "name": f"pending_after_{nr.get('name') or tr['date']}",
            "entry_gate": {k: preview[k] for k in ("level", "days",
                                                   "volume_ratio_min") if k in preview} or None,
            "cap_pct_nav": preview_cap,
            "zones": None,
            "derivation": ("上一档 preview 的骨架 — zone 待 trading-analysis 推导"
                           + ("" if preview.get("cap_pct_nav") is not None else
                              f"; cap {preview_cap} 继承自本次转移 (preview 未声明), 补画时重新回答")),
        } if preview else None
        # 新 current 的 provisional 项 + 新 next 都要补画才能授权
        slots = ["next"]
        if set(nr.get("provisional") or []):
            slots.insert(0, "current")
        agent["pending_repaint"] = {
            "slots": slots, "since": tr["date"], "source": "auto",
            "reason": (f"{tr['reason']} → 升入 {nr.get('name')}。机械项已生效; "
                       f"provisional {nr.get('provisional') or []} 需 trading-analysis "
                       "补画后才授权入场 (在此之前 entry_guard = NEEDS_CARD)"),
        }
    else:
        last = dict(agent.get("last_regime") or {})
        if not last:
            last = derive_observation_regime(klines, tr["level"], days=tr["days"])
        # next ← 旧 current (它成为回来的目标)
        repair = last.get("repair_gate") or {"level": tr["level"], "days": tr["days"]}
        # 缺口 1 (): 此前这里**整体覆盖**next_regime, 于是破位那一刻
        # 通往上一档的路 (上行 gate + 它的 next_gate_preview) 被直接删掉,
        # 只剩 reclaim_<broken>。爬回来之后无档可升。
        # 现在把上行阶梯**结构**原样带走 (数字全部标 stale, 等重画) ——
        # 阶梯的形状该活下来, 具体价位不该。
        prior_up = agent.get("next_regime") or {}
        preserved = None
        if prior_up.get("entry_gate"):
            preserved = {
                "name": prior_up.get("name"),
                "entry_gate": prior_up.get("entry_gate"),
                "cap_pct_nav": prior_up.get("cap_pct_nav"),
                "next_gate_preview": prior_up.get("next_gate_preview"),
                "stale": True,
                "note": (f"破位 {tr['date']} 前的上行阶梯, **价位已失效待重画**—— "
                         "保留是为了不丢 tier 结构 (还有 confirmed/rebase 这两档), "
                         "不是为了继续用这些数字"),
            }
        # 旧图原样搬进 next_regime.zones 有三个构造性退化。这里只修占位显示；
        # 真正生效的值由上行分支在收复触发日重算或复核：
        # ① stop 贴 gate — 破 stop 型降级的 reclaim gate 就是刚破的那条 stop
        #   会把 whipsaw 语义挂在卡面/报表上。gate 参照 = repair gate；
        #   已有足够缓冲的 stop 保持不变。
        # ② no_chase 低于收复 gate — 旧图 no_chase ≤ 自己的 next gate 是正常设计,
        #   但作为「收复后世界」会形成刚确认突破却低于入口禁追的矛盾
        #   → 摘除待重画, 不编一个更高的数。
        # ③ (entry_gate 的量腿缺省见下方 setdefault。)
        # ATR 算不出 → ①保持旧行为不编数; ②仍摘 (判据不依赖 ATR)。
        nr_zones = dict(cur_zones)
        _gate_ref = coerce_float(repair.get("level")) or float(tr["level"])
        _prior_stop = nr_zones.get("stop_level")
        _atr20_dn = _atr(klines)
        if (_atr20_dn and isinstance(_prior_stop, (int, float))
                and round(float(_prior_stop), 2) >= round(_gate_ref - 0.25 * _atr20_dn, 2)):
            _bfloor = round(_gate_ref - 0.5 * _atr20_dn, 2)
            nr_zones["stop_level"] = _bfloor
            nr_zones["stop_level_days"] = 1
            nr_zones["stop_derivation"] = (
                f"bfloor_buf 占位: 收复线 {_gate_ref:g} − 0.5×ATR20@{tr['date']} "
                f"({_atr20_dn:.2f}) = {_bfloor:g} (原图 stop {_prior_stop:g} 贴 gate "
                f"≤0.25N, 不可原样保留); 收复触发日由转移机按当日 ATR 重算")
        _prior_nc = coerce_float(nr_zones.get("no_chase_above"))
        if _prior_nc is not None and _prior_nc < _gate_ref:
            nr_zones.pop("no_chase_above", None)
            nr_zones["no_chase_derivation"] = (
                f"原图 no_chase {_prior_nc:g} < 收复 gate {_gate_ref:g} — 收复后世界的"
                f"追价上限不可低于其入口 (刚确认突破却不许追 = 自相矛盾), 降级时摘除"
                f"待重画; 不编一个更高的数")
        _eg = {k: v for k, v in repair.items() if k != "unlocks"}
        # 量腿缺省补齐: repair dict 无 volume_ratio_min 时, volume_leg 对 None 的
        # 行为是**整条跳过** (min_ratio 缺省 = 卡片没设量腿 → True) ⇒ 机器写的
        # reclaim gate 只看价格就发 cap。缺省补标准值，显式写了的不动。
        _eg.setdefault("volume_ratio_min", RECLAIM_VOL_RATIO_MIN)
        agent["next_regime"] = {
            "name": f"reclaim_{tr['level']:g}",
            "entry_gate": _eg,
            # Gate 收复后的 regime 与常规候选同权；实际 size 由 gate 预算主导。
            "cap_pct_nav": NOTIONAL_CAP,
            "zones": nr_zones,
            "provisional": [],
            # 缺口 2: reclaim_* 此前不带 next_gate_preview, 于是收复之后
            # `agent["next_regime"] = None` —— 爬回原位却头顶一个 gate 都没有。
            "next_gate_preview": ({k: preserved["entry_gate"].get(k)
                                   for k in ("level", "days", "volume_ratio_min")}
                                  | {"cap_pct_nav": preserved.get("cap_pct_nav"), "stale": True}
                                  if preserved else None),
            "preserved_upper_ladder": preserved,
            "derivation": f"由破位前的 current 直接充当修复目标 (破于 {tr['date']})",
        }
        # current ← 旧 last (观察层: cap 0, 只看不做)
        agent["zones"] = dict(last.get("zones") or {})
        # ⚠️ 上面这行只搬 zones。cap_pct_nav / participation 曾就丢在这里 ——
        # 一个 derivation 里写着"不授权任何入场"的 regime, 成为 current 之后
        # 只剩两条 accumulation 带架在刚破的 stop 底下 (修)。
        agent["current_regime"] = {
            "name": last.get("name") or f"observation_below_{tr['level']:g}",
            "cap_pct_nav": last.get("cap_pct_nav", 0),
            "participation": last.get("participation") or "gate_only",
            "source": last.get("source") or "auto",
            "since": tr["date"],
            "reason": tr["reason"],
        }
        action["intent"] = "wait_for_structure"
        # last ← 再往下一层, 机械推导
        new_stop = coerce_float((last.get("zones") or {}).get("stop_level")) or tr["level"]
        agent["last_regime"] = derive_observation_regime(klines, new_stop, days=tr["days"])
        # 下行不需要授权级补画 —— 新 current 的 cap 就是 0
        agent.pop("pending_repaint", None)

    # 这条转移**授予了多少 cap**—— 从刚写完的 current_regime 上取, 不重推第二份
    # (用户 :「不是任何一个 gate 都算次数, 只有给 cap 的 gate 才开始算」)。
    # 上行落哪个 regime 由卡上的 next_regime 决定, 它的 cap 可以是 0/None (修复路上
    # 的中间站), 那种 gate 不该吃掉一档预算 —— 判读在 `sizing.gate_count`, 但事实
    # 只有转移发生的这一刻知道, 不落库就永远推不回来 (老条目正是如此)。
    history[-1]["cap_pct_nav"] = (agent.get("current_regime") or {}).get("cap_pct_nav")
    return card


# ── CLI ─────────────────────────────────────────────────────────

def run(apply: bool = False) -> Dict:
    #  用户 裁决 A (open_ruling_watchlist_regime_switch): watchlist 卡
    # 与 positions 卡同样**自动切**。此前只扫 positions.json —— "约定由文件位置
    # 决定, 不由规则决定", 8/4 首夜 LITE/MRVL/MSFT 三笔达标未切当场现形。
    today = et_today()
    found: List[Dict] = []
    pending: Dict[str, Dict] = {}
    buckets = (
        ("positions", state_io.read_positions, state_io.write_positions),
        ("watchlist", state_io.read_watchlist, state_io.write_watchlist),
    )
    rejected: List[Dict] = []
    stale_cards: List[str] = []
    for bucket, read, write in buckets:
        # Hold the lock from the read through the merge and write. Locking only
        # write(doc) can overwrite a broker sync or a newly repainted card with
        # the old whole-document snapshot. A preview neither writes nor locks.
        with state_lock() if apply else nullcontext():
            doc = read()
            validate = (_schema.validate_positions_doc if bucket == "positions"
                        else _schema.validate_watchlist_doc)
            # 基线 = 转移**之前**这本文件已有的错误。判据是「这笔转移新造出什么」,
            # 不是「这本文件干不干净」—— 否则转移机会替上游的旧账坐牢, 而那正好是
            # 反的: 一张早就不合规的卡会把当天所有合规转移一起扣住。
            baseline = set(validate(doc)[0])
            hits: List[Dict] = []
            for ticker, card in (doc.get("tickers") or {}).items():
                if bucket == "positions":
                    broker = card.get("broker") or {}
                    if card.get("status") == "closed" and not (broker.get("qty") or 0):
                        continue
                    if not is_managed(card):
                        continue
                else:
                    if card.get("moomoo_listed") is False:
                        continue
                tr = detect_transition(ticker, card, today=today)
                if not tr:
                    continue
                tr["bucket"] = bucket
                if not apply:
                    found.append(tr)
                    continue
                # 爆炸半径 = 1 张卡。在这张卡的**副本**上转移,
                # 只有它自己过了写闸才合入 doc —— 坏票留原样并逐笔报出, 其余照常落库。
                # 校验用的是 state_io 写盘时的同一份判别式, 不另写一套 (同概念两份
                # 判别式会漂移)。
                probe = deepcopy(doc)
                try:
                    apply_transition(probe["tickers"][ticker], tr)
                    introduced = sorted(set(validate(probe)[0]) - baseline)
                    if introduced:
                        raise TransitionCardError("; ".join(introduced[:4]))
                except TransitionCardError as exc:
                    rejected.append({"ticker": ticker, "bucket": bucket,
                                     "direction": tr.get("direction"), "level": tr.get("level"),
                                     "reason": tr.get("reason"), "error": str(exc)})
                    print(f"[regime] ⛔ {ticker} 转移被拒 (本票跳过, 其余照常): {exc}",
                          file=sys.stderr)
                    continue
                doc["tickers"][ticker] = probe["tickers"][ticker]
                baseline = set(validate(doc)[0])
                found.append(tr)
                hits.append(tr)
            if apply and hits:
                try:
                    write(doc)
                except Exception as exc:                    # noqa: BLE001  写闸拒绝 = 要诊断不要 traceback
                    # 写闸校验的是**整份文件**, 不是单卡 —— 一张早就不合规的卡能把当天
                    # 所有合规转移一起焊死, 而且原来是在 write() 里以裸 traceback 现形
                    # (表现为「无 regime 转移」+ stderr 里一段栈)。
                    # baseline 是进场前就已存在的错误, 逐条点名 —— 那才是要修的东西。
                    stale_cards.extend(sorted(baseline))
                    print(f"[regime] ⛔ {bucket}: 本轮 {len(hits)} 笔转移未落库 — {exc}",
                          file=sys.stderr)
                    if baseline:
                        print(f"[regime]    文件已有 {len(baseline)} 条既存 schema 错误 "
                              f"(写闸校验整份文件, 与本轮转移无关), 先修这些卡:", file=sys.stderr)
                        for err in sorted(baseline):
                            print(f"           · {err}", file=sys.stderr)
                    for tr in hits:
                        rejected.append({"ticker": tr["ticker"], "bucket": bucket,
                                         "direction": tr.get("direction"), "level": tr.get("level"),
                                         "reason": tr.get("reason"),
                                         "error": f"write_aborted: {exc}"})
                        found.remove(tr)
        for t, c in (doc.get("tickers") or {}).items():
            pr = (c.get("agent") or {}).get("pending_repaint")
            if pr:
                pending[t] = pr
    return {"date": today, "transitions": found, "applied": bool(apply and found),
            "rejected": rejected, "preexisting_card_errors": stale_cards,
            "pending_repaint": pending}


def main() -> int:
    ap = argparse.ArgumentParser(description="regime 状态转移 (gate 机械触发)")
    ap.add_argument("--apply", action="store_true", help="执行转移并写回 positions.json")
    ap.add_argument("--check", action="store_true", help="只检测不写 (默认)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    out = run(apply=a.apply)
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    if not out["transitions"]:
        print(f"[regime] {out['date']}: 无转移")
    for t in out["transitions"]:
        arrow = "↑" if t["direction"] == "up" else "↓"
        print(f"[regime] {arrow} {t['ticker']}: {t['reason']}"
              + ("  → 已执行" if out["applied"] else "  (--apply 才写)"))
    for rj in (out.get("rejected") or []):
        print(f"[regime] ⛔ {rj['ticker']} 达标但落不了库 — {rj['error']}")
    for err in (out.get("preexisting_card_errors") or []):
        print(f"[regime] 🩹 先修这张卡 (挡住本轮落库): {err}")
    for tk, pr in (out.get("pending_repaint") or {}).items():
        print(f"[regime] ⏳ {tk} 待补画 {pr.get('slots')} (since {pr.get('since')}) — "
              "补画前不授权入场")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
