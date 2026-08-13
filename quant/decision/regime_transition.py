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

PARAMS = {
    # 算钱口径统一 ATR20 (stop 距离 / sizing 必须同一个 N, 见 trend-exit-system §2b)
    "atr_period": 20,
    "swing_wing": 2,
    "default_days": 2,
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


def _atr(klines: List[dict]) -> Optional[float]:
    s = technical.atr(klines, PARAMS["atr_period"])
    return s[-1] if s and s[-1] else None


REL_FLOOR = 0.4     # RelVR 分母地板 — relative_volume_gate_design v1 () 锁定值

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


def rel_volume(klines: List[dict], ticker: Optional[str] = None):
    """RelVR 的**计算**单一真源 → (value, note, degraded)。value=None = 算不出。

     从 `volume_leg` 里提出来: 判读层 (`report/chart_read`) 要的是
    **读数本身**, 授权层要的是**它过没过 min_ratio**。两个需求共读这一份 ——
    否则判读层会手搓第二个 RelVR, 而那正是本仓「同概念双判别式」的形状
    (GOOGL 卡裸奔 6 天的病根)。`volume_leg` 的行为一字未改。
    """
    vols = [int(r.get("volume") or 0) for r in klines]
    ratios = technical.volume_ratio(vols, 20)
    r = ratios[-1] if ratios and ratios[-1] is not None else None
    if r is None:
        return None, "量比不可算(K线不足)", False
    bench = benchmark_for(ticker)
    day = str(klines[-1].get("time") or "")[:10]
    b = _bench_ratio(bench, day)
    if b is None:
        return r, f"量比(abs兜底,{bench}缺{day or '?'}) {r:.2f}", True
    return r / max(b, REL_FLOOR), f"量比(rel {bench} {b:.2f}) {r / max(b, REL_FLOOR):.2f}", False


def volume_leg(klines: List[dict], min_ratio: Optional[float],
               ticker: Optional[str] = None):
    """量腿 canonical (relative_volume 回测裁决 R): **相对量比**
    RelVR = 个股20日量比 / max(基准量比, 0.4) ≥ min_ratio; 基准 = 半导体→SMH
    其余→QQQ (core.sectors 冻结表)。回测: 2,874 修复 episodes, W1−W0 ΔT60
    +0.48pp CI90 [+0.16,+0.79], 止损率 +0.95pp (守卫 +3pp 内), 覆盖率 +3.7pp。
    基准缺当日 → **绝对量比兜底, note 必须说出口**(降级不静默)。
    → (ok, note); min_ratio 缺省 = 卡片没设量腿 → (True, None)。
    消费方: detect_transition (授权) + gate_ladder_report (播报) — 同一函数,
    报表与转移机永不分叉。"""
    if not min_ratio:
        return True, None
    val, note, _ = rel_volume(klines, ticker)
    if val is None:
        return False, note
    return val >= min_ratio, note


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
    days = max(1, int(coerce_float(zones.get("stop_level_days")) or PARAMS["default_days"]))
    if stop is not None:
        closes = _closes(klines, days)
        if len(closes) == days and all(c < stop for c in closes):
            return {"ticker": ticker, "direction": "down", "level": stop, "days": days,
                    "closes": closes, "date": today,
                    "reason": f"收盘 {closes[-1]:g} < 卡片 stop {stop:g} 连续 {days} 日"}

    nr = agent.get("next_regime") or {}
    gate = nr.get("entry_gate") or {}
    level = coerce_float(gate.get("level"))
    if level is not None:
        gdays = max(1, int(coerce_float(gate.get("days")) or PARAMS["default_days"]))
        closes = _closes(klines, gdays)
        if len(closes) == gdays and all(c > level for c in closes):
            vok, vnote = volume_leg(klines, coerce_float(gate.get("volume_ratio_min")),
                                    ticker)
            if vok:
                return {"ticker": ticker, "direction": "up", "level": level,
                        "days": gdays, "closes": closes, "date": today,
                        "to": nr.get("name"), "fastlane": False,
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
    vok, vnote = volume_leg(klines, coerce_float(gate.get("volume_ratio_min")), ticker)
    if not vok:                                                 # ③
        return None
    over = (two[1] - level) / atr
    return {"ticker": ticker, "direction": "up", "level": level,
            "days": 1, "closes": two[1:], "date": today, "to": nr.get("name"),
            "fastlane": True, "fastlane_over_n": round(over, 2),
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
    history.append({"date": tr["date"], "direction": tr["direction"],
                    "level": tr["level"], "reason": tr["reason"],
                    "fastlane": bool(tr.get("fastlane"))})

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
        new_zones.setdefault("stop_level_days", cur_zones.get("stop_level_days",
                                                              PARAMS["default_days"]))
        agent["zones"] = new_zones
        # 升档同样要落 current_regime —— 否则"上一次是向下转移"留下的 cap 0
        # 会残留下来, 把一个已经修复的仓位永久锁死。
        agent["current_regime"] = {
            "name": nr.get("name") or f"promoted_at_{tr['date']}",
            "cap_pct_nav": nr.get("cap_pct_nav"),
            "participation": nr.get("participation") or "zone_eligible",
            "source": nr.get("source") or "auto",
            "since": tr["date"],
            "reason": tr["reason"],
        }
        action["intent"] = "hold"
        # next ← preview 的骨架 (只有 gate 和 cap, zone 待推导)
        preview = dict(nr.get("next_gate_preview") or {})
        agent["next_regime"] = {
            "name": f"pending_after_{nr.get('name') or tr['date']}",
            "entry_gate": {k: preview[k] for k in ("level", "days",
                                                   "volume_ratio_min") if k in preview} or None,
            "cap_pct_nav": preview.get("cap_pct_nav"),
            "zones": None,
            "derivation": "上一档 preview 的骨架 — zone 待 trading-analysis 推导",
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
        agent["next_regime"] = {
            "name": f"reclaim_{tr['level']:g}",
            "entry_gate": {k: v for k, v in repair.items() if k != "unlocks"},
            # 满额落地 (用户 「第一次给 cap 就允许以 R1 入场 ……
            # 不再有 2% 的限制」, classification_removal_design.md D3): gate 收复
            # 转移后的 regime 与常规候选同权, 实际 size 由 gate 预算主导。
            # [历史]  前这里写 CAP_FRACS["probe"] = 0.02。
            "cap_pct_nav": NOTIONAL_CAP,
            "zones": cur_zones,
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
    for bucket, read, write in buckets:
        doc = read()
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
            if tr:
                tr["bucket"] = bucket
                found.append(tr)
                hits.append(tr)
                if apply:
                    apply_transition(card, tr)
        if apply and hits:
            with state_lock():
                write(doc)
        for t, c in (doc.get("tickers") or {}).items():
            pr = (c.get("agent") or {}).get("pending_repaint")
            if pr:
                pending[t] = pr
    return {"date": today, "transitions": found, "applied": bool(apply and found),
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
    for tk, pr in (out.get("pending_repaint") or {}).items():
        print(f"[regime] ⏳ {tk} 待补画 {pr.get('slots')} (since {pr.get('since')}) — "
              "补画前不授权入场")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
