#!/usr/bin/env python3
"""exit_rules_backtest.py — 反事实回测: 实际入场不变, 退出严格按当前规则栈机械执行。

问题: 从私有成交事件的第一笔记录起, 如果入场决策
保持原样, 但退出由 trend-exit-system 全栈机械接管 (利润棘轮 / 趋势死亡线 /
2N 结构止损代理 / G-01 阶梯 / DTE 纪律 / no-add lock), 权益曲线长什么样?

设计原则 (不美化反事实):
- 入场 = 实际 fills 原样; 唯一例外是被 no-add lock 拦截的加仓 (记录明细)。
- 退出 = min(实际退出, 规则触发)。规则只强制退出, 不阻止自愿卖出。
- 规则数学 import exit_guard.compute_floor/compute_trail — 与 live 监控同源同参数。
- 结构位止损用 2N *上界* 代理 (§2b: 合规结构 stop 距离 ≤ 2N; 取最宽 = 触发最晚)。
- 规则触发的卖出按当日收盘 mark 成交, 期权再罚 3% premium 滑点, 正股 0.2%。
- 期权逐日 mark: Black-Scholes, IV 由该合约入场/出场真实成交价各解一次, 线性插值
  (两端锚定真实成交价, 无 vol 漂移自由度)。
- 短权/long put/组合外孤儿腿 pass-through (两个世界完全一致, 不进规则管辖)。
- 数据排除项由 ignored private config 提供，公开源码不保存账户特例。

三个世界:
  actual       — 完全复刻实际成交
  rules_all    — 规则管正股 + 期权 (全栈无豁免)
  rules_opt    — 规则只管期权; 正股按 mindset-structure-pairing 价值持有豁免

CLI: .venv/moomoo/bin/python3 quant/research/exit_rules_backtest.py
输出: tmp/backtest/{report.md, equity_curves.csv, equity.svg} + stdout 摘要
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 直接 `python3 quant/<层>/<模块>.py` 时让 `quant.*` 可 import

import json
import math
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

from quant.core.io_json import iter_jsonl  # noqa: E402
from quant.core.occ import parse_occ  # noqa: E402
from quant.core.paths import KLINE_CACHE_DIR, ROOT  # noqa: E402
from quant.core import technical  # noqa: E402
from quant.decision.exit_guard import PARAMS, compute_floor, compute_trail  # noqa: E402

EVENTS_GLOB = ROOT / "trade/us_stock/holding/events"
NAV_PATH = ROOT / "trade/us_stock/holding/events/nav_history.jsonl"
OUT_DIR = ROOT / "tmp/backtest"

R_RATE = 0.04            # 无风险利率 (BS)
STOP_2N_MULT = 2.0       # 结构位止损代理 = entry ref − 2×ATR20 (§2b 上界)
DTE_FORCE_CLOSE = 21     # DTE≤21 + OTM → force close (natural-humility-anchor)
DTE_GRACE_DAYS = 3       # 入场 3 天内不触发 DTE 规则 (entry vehicle 选择不在回测范围)
SLIP_OPT = 0.03          # 规则触发期权卖出的滑点惩罚 (premium 比例)
SLIP_STK = 0.002         # 规则触发正股卖出的滑点惩罚
DEFAULT_CONFIG = ROOT / "trade/us_stock/knowledge/research/exit_rules/config.json"


# ── 市场数据 ────────────────────────────────────────────────────

class Market:
    def __init__(self, bad_klines: set[str] | None = None) -> None:
        self._rows: Dict[str, List[dict]] = {}
        self._idx: Dict[str, Dict[str, int]] = {}
        self._atr: Dict[str, List[Optional[float]]] = {}
        self._mode_cache: Dict[Tuple[str, str], str] = {}
        self._bad_klines = bad_klines or set()

    def rows(self, und: str) -> List[dict]:
        if und not in self._rows:
            p = KLINE_CACHE_DIR / f"{und}.jsonl"
            rows = [] if und in self._bad_klines else ([r for r in iter_jsonl(p)] if p.exists() else [])
            for r in rows:
                r["_d"] = str(r.get("time", ""))[:10]
            self._rows[und] = rows
            self._idx[und] = {r["_d"]: i for i, r in enumerate(rows)}
            self._atr[und] = technical.atr(rows, PARAMS["atr_period"]) if rows else []
        return self._rows[und]

    def idx_on_or_before(self, und: str, d: str) -> Optional[int]:
        rows = self.rows(und)
        if not rows:
            return None
        idx = self._idx[und]
        if d in idx:
            return idx[d]
        lo = None
        for i, r in enumerate(rows):
            if r["_d"] <= d:
                lo = i
            else:
                break
        return lo

    def close(self, und: str, d: str) -> Optional[float]:
        i = self.idx_on_or_before(und, d)
        return float(self.rows(und)[i]["close"]) if i is not None else None

    def atr(self, und: str, d: str) -> Optional[float]:
        i = self.idx_on_or_before(und, d)
        if i is None:
            return None
        a = self._atr[und]
        return a[i] if i < len(a) else None

    def window(self, und: str, armed: str, d: str) -> List[dict]:
        """armed 日前 45 bars 起到 d 为止 (compute_trail 需要 ATR 预热 + armed 起点)。"""
        rows = self.rows(und)
        i = self.idx_on_or_before(und, d)
        if i is None:
            return []
        j = self.idx_on_or_before(und, armed)
        start = max(0, (j if j is not None else i) - 45)
        return rows[start:i + 1]

    def mode(self, und: str, d: str) -> str:
        """Momentum diagnosis (zone-maintenance 第4步) 的机械代理:
        Q1 6m≥+150% 或 YTD≥+200%; Q2 近20日有单日+10%; Q3 距窗口内高点<10%。"""
        key = (und, d)
        if key in self._mode_cache:
            return self._mode_cache[key]
        i = self.idx_on_or_before(und, d)
        rows = self.rows(und)
        out = "normal"
        if i is not None and i >= 21:
            closes = [float(r["close"]) for r in rows[:i + 1]]
            c = closes[-1]
            q1 = False
            if len(closes) > 126 and closes[-127] > 0:
                q1 = c / closes[-127] - 1 >= 1.50
            ytd_base = None
            current_year = d[:4]
            for k in range(i, -1, -1):
                if rows[k]["_d"][:4] < current_year:
                    ytd_base = closes[k]
                    break
            if not q1 and ytd_base:
                q1 = c / ytd_base - 1 >= 2.0
            q2 = any(closes[k] / closes[k - 1] - 1 >= 0.10
                     for k in range(max(1, len(closes) - 20), len(closes)))
            q3 = c >= 0.90 * max(closes)
            if q1 and q2 and q3:
                out = "parabolic"
        self._mode_cache[key] = out
        return out


# ── Black-Scholes + IV 锚定 ─────────────────────────────────────

def _phi(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_price(S: float, K: float, T: float, iv: float, cp: str) -> float:
    if T <= 0:
        return max(0.0, (S - K) if cp == "C" else (K - S))
    if iv <= 0:
        iv = 1e-4
    sq = iv * math.sqrt(T)
    d1 = (math.log(S / K) + (R_RATE + iv * iv / 2) * T) / sq
    d2 = d1 - sq
    disc = math.exp(-R_RATE * T)
    if cp == "C":
        return S * _phi(d1) - K * disc * _phi(d2)
    return K * disc * _phi(-d2) - S * _phi(-d1)


def solve_iv(S: float, K: float, T: float, px: float, cp: str) -> float:
    lo, hi = 1e-3, 5.0
    if bs_price(S, K, T, lo, cp) >= px:
        return lo   # 成交价 ≤ 无波动价 (盘中 S 与收盘 S 错位) → 贴 intrinsic
    if bs_price(S, K, T, hi, cp) <= px:
        return hi
    for _ in range(60):
        mid = (lo + hi) / 2
        if bs_price(S, K, T, mid, cp) < px:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


class OptionModel:
    """每个合约: 从全部真实成交解 IV 锚点, 持仓期内按日历日线性插值。"""

    def __init__(self, instr: str, occ: dict, mkt: Market) -> None:
        self.instr, self.occ, self.mkt = instr, occ, mkt
        self.anchors: List[Tuple[str, float]] = []   # (date, iv)

    def add_anchor(self, d: str, px: float) -> None:
        S = self.mkt.close(self.occ["underlying"], d)
        if S is None:
            return
        T = max(1, (date.fromisoformat(self.occ["expiry"]) - date.fromisoformat(d)).days) / 365.0
        iv = solve_iv(S, self.occ["strike"], T, px, self.occ["side"])
        self.anchors.append((d, iv))
        self.anchors.sort()

    def iv_at(self, d: str) -> float:
        a = self.anchors
        if not a:
            return 0.6
        if d <= a[0][0]:
            return a[0][1]
        if d >= a[-1][0]:
            return a[-1][1]
        for (d1, v1), (d2, v2) in zip(a, a[1:]):
            if d1 <= d <= d2:
                t1, t2 = date.fromisoformat(d1), date.fromisoformat(d2)
                t = (date.fromisoformat(d) - t1).days / max(1, (t2 - t1).days)
                return v1 + t * (v2 - v1)
        return a[-1][1]

    def mark(self, d: str) -> Optional[float]:
        S = self.mkt.close(self.occ["underlying"], d)
        if S is None:
            return None
        exp = date.fromisoformat(self.occ["expiry"])
        T = (exp - date.fromisoformat(d)).days / 365.0
        px = bs_price(S, self.occ["strike"], max(T, 0.0), self.iv_at(d), self.occ["side"])
        intrinsic = max(0.0, (S - self.occ["strike"]) if self.occ["side"] == "C"
                        else (self.occ["strike"] - S))
        return max(px, intrinsic)


# ── 持仓簿 ──────────────────────────────────────────────────────

class Leg:
    def __init__(self, instr: str, sign: int, model: Optional[OptionModel]) -> None:
        self.instr, self.sign, self.model = instr, sign, model
        self.occ = parse_occ(instr)
        self.mult = 100 if self.occ else 1
        self.lots: List[dict] = []   # {qty, cost, date, g01: set}

    @property
    def qty(self) -> float:
        return sum(l["qty"] for l in self.lots)

    @property
    def avg_cost(self) -> Optional[float]:
        q = self.qty
        return sum(l["qty"] * l["cost"] for l in self.lots) / q if q > 1e-9 else None

    def add(self, qty: float, px: float, d: str) -> None:
        self.lots.append({"qty": qty, "cost": px, "date": d, "g01": set()})

    def reduce(self, qty: float) -> float:
        """FIFO 减仓, 返回实际减掉的数量。"""
        take = min(qty, self.qty)
        rem = take
        for lot in self.lots:
            x = min(lot["qty"], rem)
            lot["qty"] -= x
            rem -= x
            if rem <= 1e-9:
                break
        self.lots = [l for l in self.lots if l["qty"] > 1e-9]
        return take


class Episode:
    _seq = 0

    def __init__(self, und: str, entry_date: str, pass_through: bool) -> None:
        Episode._seq += 1
        self.id = Episode._seq
        self.und, self.entry_date, self.pass_through = und, entry_date, pass_through
        self.legs: List[Leg] = []
        self.armed_date = entry_date
        self.seed_close: Optional[float] = None
        self.hwm: Optional[float] = None
        self.floor: Optional[float] = None
        self.fired1 = self.fired2 = False
        self.hwm_at_fire: Optional[float] = None
        self.prev_trail: Optional[float] = None
        self.stop_2n: Optional[float] = None
        self.break_pending = False   # trail_confirm 变体: 昨日已破线待确认
        self.rule_log: List[str] = []

    @property
    def is_option(self) -> bool:
        return any(l.occ for l in self.legs)

    @property
    def is_combo(self) -> bool:
        return len(self.legs) > 1

    @property
    def qty(self) -> float:
        return self.legs[0].qty if self.legs else 0.0

    @property
    def net_cost(self) -> Optional[float]:
        """净单位成本 (combo = 长腿成本 − 短腿 credit)。"""
        if not self.legs or self.legs[0].qty <= 1e-9:
            return None
        c = 0.0
        for l in self.legs:
            ac = l.avg_cost
            if ac is None:
                return None
            c += l.sign * ac
        return c

    def net_mark(self, marks: Dict[str, Optional[float]]) -> Optional[float]:
        m = 0.0
        for l in self.legs:
            v = marks.get(l.instr)
            if v is None:
                return None
            m += l.sign * v
        return m

    def min_dte(self, d: str) -> Optional[int]:
        dtes = [(date.fromisoformat(l.occ["expiry"]) - date.fromisoformat(d)).days
                for l in self.legs if l.occ]
        return min(dtes) if dtes else None


class Book:
    def __init__(self, name: str, rules: str, mkt: Market,
                 opt_models: Dict[str, OptionModel],
                 trail_confirm: bool = False, strict_lock: bool = False) -> None:
        self.name, self.rules, self.mkt = name, rules, mkt
        self.trail_confirm = trail_confirm   # §2b 确认标定: <1N 单日破线不确认, 2连收/≥1N 才执行
        self.strict_lock = strict_lock       # canonical §3 严格读法: lock 存活到结构修复 (含重进)
        self.opt_models = opt_models
        self.cash = 0.0
        self.by_instr: Dict[str, Episode] = {}
        self.episodes: List[Episode] = []
        # no-add lock: episode 存续期内拦同标的加仓 (镜像 live exit_guard 语义 —
        # 仓位清零后 guard 销毁, 全平后的重新入场归 entry 规则管, 不在本回测范围)
        self.locks: List[dict] = []                # {und, ref, ep}
        self.blocked: List[dict] = []
        self.rule_events: List[dict] = []
        self.pnl_by_und: Dict[str, float] = {}
        self.curve: List[Tuple[str, float]] = []
        self._pending_short: Dict[Tuple[str, str], Tuple[str, float, float]] = {}

    # ---- helpers ----
    def _ruled(self, ep: Episode) -> bool:
        if ep.pass_through or self.rules == "none":
            return False
        if self.rules == "options":
            return ep.is_option
        return True

    def _mark_of(self, leg: Leg, d: str) -> Optional[float]:
        if leg.occ:
            m = self.opt_models.get(leg.instr)
            return m.mark(d) if m else None
        return self.mkt.close(leg.instr, d)

    def _cashflow(self, und: str, amount: float) -> None:
        self.cash += amount
        self.pnl_by_und[und] = self.pnl_by_und.get(und, 0.0) + amount

    # ---- 事件应用 ----
    def apply_event(self, ev: dict) -> None:
        instr, side = ev["ticker"], ev["side"]
        qty, px, d = float(ev["qty"]), float(ev["price"]), ev["timestamp"][:10]
        occ = parse_occ(instr)
        und = occ["underlying"] if occ else instr
        mult = 100 if occ else 1

        if side == "BUY":
            ep = self.by_instr.get(instr)
            if ep is not None and ep.qty <= 1e-9:
                ep = None
            blockable = (self.rules == "all") or (self.rules == "options" and occ)
            active_refs = [lk["ref"] for lk in self.locks
                           if lk["und"] == und and (self.strict_lock or lk["ep"].qty > 1e-9)]
            if blockable and active_refs:
                self.blocked.append({"date": d, "instr": instr, "qty": qty, "px": px,
                                     "ref_high": max(active_refs)})
                return
            if ep is None:
                pt = bool(occ and occ["side"] == "P")   # long put 对冲仓 pass-through
                ep = Episode(und, d, pass_through=pt)
                ep.seed_close = self.mkt.close(und, d)
                ref = px if not occ else (self.mkt.close(und, d) or 0.0)
                a = self.mkt.atr(und, d)
                ep.stop_2n = (ref - STOP_2N_MULT * a) if (a and ref) else None
                ep.legs.append(Leg(instr, +1, self.opt_models.get(instr)))
                self.by_instr[instr] = ep
                self.episodes.append(ep)
            leg = next(l for l in ep.legs if l.instr == instr)
            leg.add(qty, px, d)
            ref = px if not occ else (self.mkt.close(und, d) or 0.0)
            a = self.mkt.atr(und, d)
            if a and ref:
                cand = ref - STOP_2N_MULT * a
                ep.stop_2n = max(ep.stop_2n, cand) if ep.stop_2n else cand
            self._cashflow(und, -qty * px * mult)

        elif side in ("SELL", "BUY_BACK"):
            ep = self.by_instr.get(instr)
            if ep is None or not ep.legs:
                # BUY_BACK-on-flat = 窗口前开仓的孤儿短腿：保留真实现金流方向。
                # SELL-on-flat = sim 从未持有 (入场被拦/提前平) → no-op, 不允许幽灵卖出收现金
                if side == "BUY_BACK":
                    self._cashflow(und, -qty * px * mult)
                return
            leg = next((l for l in ep.legs if l.instr == instr), None)
            if leg is None or leg.qty <= 1e-9:
                return   # sim 已按规则提前平掉 → 实际卖出 no-op
            take = leg.reduce(min(qty, leg.qty))
            self._cashflow(und, (1 if leg.sign > 0 else -1) * take * px * mult)

        elif side == "SELL_SHORT":
            # 同日同标的同 expiry 等量的 long call → 合并为 combo (bull call spread)
            host = None
            if occ:
                for l_instr, ep in self.by_instr.items():
                    locc = parse_occ(l_instr)
                    if (locc and ep.qty > 1e-9 and ep.und == und and not ep.is_combo
                            and locc["expiry"] == occ["expiry"] and locc["side"] == "C"
                            and abs(ep.qty - qty) < 1e-6 and ep.entry_date == d):
                        host = ep
                        break
            if host is not None:
                leg = Leg(instr, -1, self.opt_models.get(instr))
                leg.add(qty, px, d)
                host.legs.append(leg)
                self.by_instr[instr] = host
                self._cashflow(und, qty * px * mult)
            else:
                ep = Episode(und, d, pass_through=True)   # 独立短权 → pass-through
                leg = Leg(instr, -1, self.opt_models.get(instr))
                leg.add(qty, px, d)
                ep.legs.append(leg)
                self.by_instr[instr] = ep
                self.episodes.append(ep)
                self._cashflow(und, qty * px * mult)

    # ---- 规则强制卖出 ----
    def _rule_sell(self, ep: Episode, frac_qty: float, d: str,
                   marks: Dict[str, Optional[float]], rule: str, note: str) -> None:
        """按当日收盘 mark 卖出 frac_qty 个单位 (combo 全腿等比), 计滑点。"""
        sold_any = False
        for leg in ep.legs:
            m = marks.get(leg.instr)
            if m is None or leg.qty <= 1e-9:
                continue
            take = leg.reduce(min(frac_qty, leg.qty))
            if take <= 1e-9:
                continue
            slip = SLIP_OPT if leg.occ else SLIP_STK
            if leg.sign > 0:
                px = m * (1 - slip)
                self._cashflow(ep.und, take * px * leg.mult)
            else:
                px = m * (1 + slip)
                self._cashflow(ep.und, -take * px * leg.mult)
            sold_any = True
        if sold_any:
            self.rule_events.append({"date": d, "und": ep.und,
                                     "instr": "+".join(l.instr for l in ep.legs),
                                     "rule": rule, "qty": round(frac_qty, 4), "note": note})
            ep.rule_log.append(f"{d} {rule} {note}")

    def _set_lock(self, ep: Episode, highest_close: Optional[float]) -> None:
        if highest_close:
            for lk in self.locks:
                if lk["ep"] is ep:
                    lk["ref"] = max(lk["ref"], highest_close)
                    return
            self.locks.append({"und": ep.und, "ref": highest_close, "ep": ep})

    # ---- 收盘处理 ----
    def close_day(self, d: str) -> None:
        marks: Dict[str, Optional[float]] = {}
        for ep in self.episodes:
            if ep.qty <= 1e-9:
                continue
            for leg in ep.legs:
                if leg.instr not in marks:
                    marks[leg.instr] = self._mark_of(leg, d)

        for ep in list(self.episodes):
            if ep.qty <= 1e-9 or not self._ruled(ep):
                continue
            net_mark = ep.net_mark(marks)
            net_cost = ep.net_cost
            if net_mark is None or net_cost is None:
                continue
            und_close = self.mkt.close(ep.und, d)
            # HWM 棘轮 (只升)
            if ep.hwm is None:
                ep.hwm = max(net_mark, net_cost)
            elif net_mark > ep.hwm:
                ep.hwm = net_mark
            # 新 leg 重新武装
            if ep.fired1 and ep.hwm_at_fire and ep.hwm > ep.hwm_at_fire * PARAMS["new_leg_pct"]:
                ep.fired1 = ep.fired2 = False
                ep.hwm_at_fire = None
            dte = ep.min_dte(d)
            stage, floor_new = compute_floor(net_cost, ep.hwm, dte)
            ep.floor = max([v for v in (ep.floor, floor_new) if v is not None], default=None)

            # 趋势死亡线 (underlying 收盘口径)
            win = self.mkt.window(ep.und, ep.armed_date, d)
            tr = compute_trail(win, ep.armed_date, ep.seed_close,
                               self.mkt.mode(ep.und, d), ep.prev_trail)
            ep.prev_trail = tr.get("trail")

            # G-01 阶梯 (期权单腿, lot 级; 单张 lot 无法 partial → 跳过, 保 runner)
            if ep.is_option and not ep.is_combo and not ep.pass_through:
                leg = ep.legs[0]
                for lot in list(leg.lots):
                    for gain, trim_pct in PARAMS["g01_levels"]:
                        tag = f"+{int(gain*100)}%"
                        if tag in lot["g01"] or lot["qty"] <= 1 + 1e-9:
                            continue
                        if net_mark >= lot["cost"] * (1 + gain):
                            lot["g01"].add(tag)
                            t_qty = min(max(1, round(lot["qty"] * trim_pct / 100)),
                                        int(lot["qty"]) - 1)
                            if t_qty >= 1:
                                lot["qty"] -= t_qty
                                slip_px = net_mark * (1 - SLIP_OPT)
                                self._cashflow(ep.und, t_qty * slip_px * 100)
                                self.rule_events.append({
                                    "date": d, "und": ep.und, "instr": leg.instr,
                                    "rule": f"G-01 {tag}", "qty": t_qty,
                                    "note": f"trim {t_qty}张 @~{slip_px:.2f} (lot cost {lot['cost']})"})
                                ep.rule_log.append(f"{d} G-01 {tag} trim {t_qty}")
                leg.lots = [l for l in leg.lots if l["qty"] > 1e-9]
                net_mark = ep.net_mark(marks)  # 数量变化不影响单位 mark, 保持

            # 棘轮触发 (镜像 exit_guard.evaluate)
            if ep.floor is not None and ep.qty > 1e-9:
                if ep.fired1 and not ep.fired2 and net_mark >= ep.floor * 1.02:
                    ep.fired1, ep.hwm_at_fire = False, None   # +2% 收复重武装
                if not ep.fired1 and net_mark < ep.floor and stage == "locked":
                    ep.fired1 = True
                    ep.hwm_at_fire = ep.hwm
                    self._set_lock(ep, tr.get("highest_close"))
                    if ep.is_option:
                        sell_q = max(1, int(round(ep.qty * 0.5)))
                    else:
                        sell_q = round(ep.qty * 0.5, 2)
                    full = sell_q >= ep.qty - 1e-9
                    self._rule_sell(ep, sell_q, d, marks, "棘轮stage1",
                                    f"mark {net_mark:.2f} < floor {ep.floor:.2f} "
                                    f"(HWM {ep.hwm:.2f}, cost {net_cost:.2f}) 卖 {sell_q}")
                    if full:
                        ep.fired2 = True
                elif not ep.fired1 and net_mark < ep.floor and stage == "breakeven":
                    ep.fired1 = ep.fired2 = True
                    self._set_lock(ep, tr.get("highest_close"))
                    self._rule_sell(ep, ep.qty, d, marks, "棘轮保本",
                                    f"曾+50%+ (HWM {ep.hwm:.2f}) 破保本 {net_cost:.2f} → 全平")
                elif ep.fired1 and not ep.fired2 and net_mark < net_cost:
                    ep.fired2 = True
                    self._rule_sell(ep, ep.qty, d, marks, "棘轮stage2",
                                    f"mark {net_mark:.2f} < 成本 {net_cost:.2f} → 清仓")

            # 趋势死亡线 + 2N 结构止损代理
            # (trail_confirm 变体: 破线深度 <1N 的首日不执行, 次日仍破或 ≥1N 才确认 — §2b 标定)
            trail_broken = bool(tr.get("broken"))
            n2_broken = bool(ep.stop_2n and und_close and und_close < ep.stop_2n)
            if ep.qty > 1e-9 and (trail_broken or n2_broken):
                atr = tr.get("atr")
                line = tr.get("trail") if trail_broken else ep.stop_2n
                depth = (line - und_close) if (line and und_close) else None
                confirmed = (not self.trail_confirm) or ep.break_pending \
                    or (depth is not None and atr and depth >= atr)
                if confirmed:
                    self._set_lock(ep, tr.get("highest_close"))
                    rule = "趋势死亡线" if trail_broken else "2N止损"
                    self._rule_sell(ep, ep.qty, d, marks, rule,
                                    f"close {und_close} < {'trail' if trail_broken else 'stop'} "
                                    f"{line} ({tr.get('mode')})")
                    ep.break_pending = False
                else:
                    ep.break_pending = True
            else:
                ep.break_pending = False

            # DTE≤21 + OTM 强平 — 只管"衰减进 gamma 区"的仓;
            # 入场时 DTE 已 ≤21 的 lotto 仓属于 entry-side 规则管辖, 不在本回测范围
            if (ep.qty > 1e-9 and ep.is_option and not ep.is_combo and dte is not None
                    and dte <= DTE_FORCE_CLOSE and und_close):
                occ0 = ep.legs[0].occ
                otm = und_close < occ0["strike"] if occ0["side"] == "C" else und_close > occ0["strike"]
                entry_dte = (date.fromisoformat(occ0["expiry"])
                             - date.fromisoformat(ep.entry_date)).days
                if otm and entry_dte > DTE_FORCE_CLOSE:
                    self._rule_sell(ep, ep.qty, d, marks, "DTE≤21+OTM",
                                    f"DTE {dte}, und {und_close:.2f} vs K {occ0['strike']}")

        # 到期结算兜底
        for ep in self.episodes:
            if ep.qty <= 1e-9:
                continue
            for leg in ep.legs:
                if leg.occ and d >= leg.occ["expiry"] and leg.qty > 1e-9:
                    S = self.mkt.close(ep.und, d) or 0.0
                    intr = max(0.0, (S - leg.occ["strike"]) if leg.occ["side"] == "C"
                               else (leg.occ["strike"] - S))
                    q = leg.reduce(leg.qty)
                    self._cashflow(ep.und, (1 if leg.sign > 0 else -1) * q * intr * 100)
                    self.rule_events.append({"date": d, "und": ep.und, "instr": leg.instr,
                                             "rule": "到期结算", "qty": q,
                                             "note": f"intrinsic {intr:.2f}"})

        # no-add lock 清理: 结构修复 (close > ref) 或 episode 已清零 (guard 销毁)
        keep = []
        for lk in self.locks:
            if not self.strict_lock and lk["ep"].qty <= 1e-9:
                continue
            c = self.mkt.close(lk["und"], d)
            if c is not None and c > lk["ref"]:
                continue
            keep.append(lk)
        self.locks = keep

    def equity(self, d: str) -> float:
        total = self.cash
        for ep in self.episodes:
            for leg in ep.legs:
                if leg.qty <= 1e-9:
                    continue
                m = self._mark_of(leg, d)
                if m is None:
                    m = leg.avg_cost or 0.0
                total += leg.sign * leg.qty * m * leg.mult
        return total


# ── 主流程 ──────────────────────────────────────────────────────

def load_private_config(path: Path) -> dict:
    """读取 ignored private config；文件缺失时使用无特例的公开默认值。"""
    if not path.exists():
        return {"excluded_instruments": [], "bad_klines": [], "start_date": None}
    doc = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        raise ValueError("exit-rules config must be a JSON object")
    for key in ("excluded_instruments", "bad_klines"):
        if not isinstance(doc.get(key, []), list):
            raise ValueError(f"{key} must be a JSON list")
    return {
        "excluded_instruments": doc.get("excluded_instruments", []),
        "bad_klines": doc.get("bad_klines", []),
        "start_date": doc.get("start_date"),
    }


def load_events(excluded_instruments: set[str] | None = None) -> Tuple[List[dict], List[str]]:
    excluded_instruments = excluded_instruments or set()
    evs, notes = [], []
    for p in sorted(EVENTS_GLOB.glob("*/trades.jsonl")):
        for e in iter_jsonl(p):
            if e["ticker"] in excluded_instruments:
                continue
            evs.append(e)
    evs.sort(key=lambda e: e["timestamp"])
    if not evs:
        raise ValueError("no private trade events found after exclusions")
    notes.append(f"事件 {len(evs)} 条 ({evs[0]['timestamp'][:10]} → {evs[-1]['timestamp'][:10]}); "
                 f"排除项 {len(excluded_instruments)} 个")
    return evs, notes


def run(trail_confirm: bool = False, strict_lock: bool = False,
        config_path: Path = DEFAULT_CONFIG) -> None:
    variant = trail_confirm or strict_lock
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    config = load_private_config(config_path)
    mkt = Market(set(config["bad_klines"]))
    events, notes = load_events(set(config["excluded_instruments"]))
    start = config["start_date"] or events[0]["timestamp"][:10]

    # 期权模型: 全部成交价作为 IV 锚
    opt_models: Dict[str, OptionModel] = {}
    for e in events:
        occ = parse_occ(e["ticker"])
        if not occ:
            continue
        m = opt_models.setdefault(e["ticker"], OptionModel(e["ticker"], occ, mkt))
        m.add_anchor(e["timestamp"][:10], float(e["price"]))

    # 交易日历: 基准 kline 日 (>= 私有样本起点) + 事件残留日
    qqq = [r["_d"] for r in mkt.rows("QQQ") if r["_d"] >= start]
    if not qqq:
        raise ValueError("benchmark kline does not cover the configured sample")
    extra = sorted({e["timestamp"][:10] for e in events} - set(qqq))
    calendar = sorted(set(qqq) | {d for d in extra if d > qqq[-1]})

    books = [Book("actual", "none", mkt, opt_models),
             Book("rules_all", "all", mkt, opt_models, trail_confirm, strict_lock),
             Book("rules_opt", "options", mkt, opt_models, trail_confirm, strict_lock)]

    qqq_set = set(qqq)
    ei = {b.name: 0 for b in books}
    for d in calendar:
        for b in books:
            i = ei[b.name]
            while i < len(events) and events[i]["timestamp"][:10] <= d:
                b.apply_event(events[i])
                i += 1
            ei[b.name] = i
            if d in qqq_set:
                b.close_day(d)
            b.curve.append((d, b.equity(d)))

    # 期末未平仓的 mark 归因进每标的口径, 与 equity 一致
    last_d = calendar[-1]
    for b in books:
        for ep in b.episodes:
            for leg in ep.legs:
                if leg.qty > 1e-9:
                    m = b._mark_of(leg, last_d) or (leg.avg_cost or 0.0)
                    b.pnl_by_und[ep.und] = b.pnl_by_und.get(ep.und, 0.0) \
                        + leg.sign * leg.qty * m * leg.mult

    # ---- 汇总 ----
    final = {b.name: b.curve[-1][1] for b in books}
    dd = {}
    for b in books:
        peak, mdd = -1e18, 0.0
        for _, v in b.curve:
            peak = max(peak, v)
            mdd = min(mdd, v - peak)
        dd[b.name] = mdd
    qqq_rows = mkt.rows("QQQ")
    q0 = next(float(r["close"]) for r in qqq_rows if r["_d"] >= start)
    q1 = float(qqq_rows[-1]["close"])

    # ---- nav_history 验证 (nav 是盘前快照 ≈ 前收盘) ----
    nav = [r for r in iter_jsonl(NAV_PATH)]
    actual = books[0]
    eq = dict(actual.curve)
    caldays = [c for c, _ in actual.curve]
    val_rows, deltas = [], []
    for r1, r2 in zip(nav, nav[1:]):
        d1, d2 = r1["date"], r2["date"]
        prev1 = max((c for c in caldays if c < d1), default=None)
        prev2 = max((c for c in caldays if c < d2), default=None)
        if prev1 and prev2 and prev1 != prev2:
            dn = r2["total_assets"] - r1["total_assets"]
            dm = eq[prev2] - eq[prev1]
            val_rows.append((d2, dn, dm, dn - dm))
            deltas.append(abs(dn - dm))
    mae = sum(deltas) / len(deltas) if deltas else float("nan")

    # ---- 每标的 Δ 表 ----
    unds = sorted(set(books[0].pnl_by_und) | set(books[1].pnl_by_und) | set(books[2].pnl_by_und))
    # 末日在持仓的 mark 补进 pnl 口径: 用 equity−cash 分摊 — 简化: 单独列出
    per_und = []
    for u in unds:
        a = books[0].pnl_by_und.get(u, 0.0)
        f = books[1].pnl_by_und.get(u, 0.0)
        o = books[2].pnl_by_und.get(u, 0.0)
        per_und.append((u, a, f, o, f - a))
    per_und.sort(key=lambda x: -abs(x[4]))

    # ---- 输出 ----
    if variant:   # 变体实验只打印摘要, 不覆盖 base 报告
        tag = f"trail_confirm={trail_confirm} strict_lock={strict_lock}"
        print(f"[variant {tag}]")
        print(f"actual {final['actual']:+,.0f} | rules_all {final['rules_all']:+,.0f} "
              f"(DD {dd['rules_all']:,.0f}) | rules_opt {final['rules_opt']:+,.0f} "
              f"(DD {dd['rules_opt']:,.0f})")
        for b in books[1:]:
            blocked = ", ".join(f"{x['date']} {x['instr']}@{x['px']:g}" for x in b.blocked) or "无"
            print(f"  {b.name}: 触发 {len(b.rule_events)} 次 | 拦截: {blocked}")
        per = {u: (books[1].pnl_by_und.get(u, 0) - books[0].pnl_by_und.get(u, 0))
               for u in set(books[0].pnl_by_und) | set(books[1].pnl_by_und)}
        top = sorted(per.items(), key=lambda x: -abs(x[1]))[:8]
        print("  rules_all 每标的Δ:", ", ".join(f"{u} {v:+,.0f}" for u, v in top if abs(v) > 50))
        return
    csv_p = OUT_DIR / "equity_curves.csv"
    with csv_p.open("w") as f:
        f.write("date,actual,rules_all,rules_opt\n")
        for (d, a), (_, x), (_, y) in zip(*[b.curve for b in books]):
            f.write(f"{d},{a:.2f},{x:.2f},{y:.2f}\n")

    write_svg(OUT_DIR / "equity.svg", books)

    rep = []
    rep.append(f"# 退出规则栈反事实回测 ({calendar[0]} → {calendar[-1]})\n")
    rep.append("入场保持实际, 退出按 trend-exit-system 全栈机械执行。规则数学与 exit_guard.py 同源。\n")
    for n in notes:
        rep.append(f"- {n}")
    rep.append(f"- QQQ 同期: {q0:.2f} → {q1:.2f} ({(q1/q0-1)*100:+.1f}%)")
    rep.append(f"- 验证: 模型日损益 vs nav_history 日损益 MAE = ${mae:,.0f} ({len(deltas)} 日)\n")
    rep.append("## 终值 (累计交易损益, USD)\n")
    rep.append("| 世界 | 终值 | 最大回撤 |")
    rep.append("|---|---|---|")
    label = {"actual": "实际", "rules_all": "规则全栈 (正股+期权)", "rules_opt": "规则只管期权 (正股价值持有豁免)"}
    for b in books:
        rep.append(f"| {label[b.name]} | ${final[b.name]:,.0f} | ${dd[b.name]:,.0f} |")
    rep.append("\n## 每标的损益对比 (按 |Δ 全栈−实际| 排序)\n")
    rep.append("| 标的 | 实际 | 规则全栈 | 期权-only | Δ(全栈−实际) |")
    rep.append("|---|---|---|---|---|")
    for u, a, f_, o, dl in per_und:
        rep.append(f"| {u} | {a:+,.0f} | {f_:+,.0f} | {o:+,.0f} | {dl:+,.0f} |")
    for bname in ("rules_all", "rules_opt"):
        b = next(x for x in books if x.name == bname)
        rep.append(f"\n## {label[bname]} — 规则触发明细 ({len(b.rule_events)} 次)\n")
        for e in b.rule_events:
            rep.append(f"- {e['date']} **{e['und']}**{e['instr']} — {e['rule']}: {e['note']}")
        rep.append(f"\n### 被 no-add lock 拦截的入场 ({len(b.blocked)})\n")
        for x in b.blocked:
            rep.append(f"- {x['date']} {x['instr']} {x['qty']:g} @ {x['px']} "
                       f"(lock ref high {x['ref_high']:.2f})")
    rep.append("\n## nav_history 逐日验证 (Δnav vs Δmodel)\n")
    rep.append("| 日期 | Δnav | Δmodel | 差 |")
    rep.append("|---|---|---|---|")
    for d, dn, dm, df in val_rows:
        rep.append(f"| {d} | {dn:+,.0f} | {dm:+,.0f} | {df:+,.0f} |")
    rep.append("\n## 假设与局限\n")
    for s in [
        "规则与回放样本并非独立生成 — 本回测是制度回填, 不是 out-of-sample 证明。",
        "退出 = min(实际退出, 规则触发): 规则只强制退出, 不会让你比实际拿得更久。",
        "期权逐日 mark = BS + 入场/出场成交价解 IV 线性插值; 规则触发卖出罚 3% premium 滑点 (正股 0.2%)。",
        "结构位止损用 2N 上界代理 (§2b 合规 stop ≤ 2N), 取最宽 = 对反事实最不利。",
        "G-01 按 lot 执行; 单张 lot 无法 partial → 跳过 (保 runner), 依赖棘轮保护。",
        "短权 / long put 对冲 / 孤儿腿 pass-through; 手续费两边同略; 股息忽略。",
        "趋势线 parabolic/normal 模式用 zone-maintenance momentum 三问的机械代理逐日判定。",
        "执行假设 = 告警当日收盘成交 (系统设计口径), 实际执行率是另一个变量 — 半年复盘已定名。",
    ]:
        rep.append(f"- {s}")
    (OUT_DIR / "report.md").write_text("\n".join(rep), encoding="utf-8")

    # stdout 摘要
    print(f"actual final: {final['actual']:+,.0f}")
    print(f"rules_all final: {final['rules_all']:+,.0f}  (Δ {final['rules_all']-final['actual']:+,.0f})")
    print(f"rules_opt final: {final['rules_opt']:+,.0f}  (Δ {final['rules_opt']-final['actual']:+,.0f})")
    print(f"maxDD actual/all/opt: {dd['actual']:,.0f} / {dd['rules_all']:,.0f} / {dd['rules_opt']:,.0f}")
    print(f"nav validation MAE: {mae:,.0f}")
    print(f"report → {OUT_DIR/'report.md'}")


def write_svg(path: Path, books: List[Book]) -> None:
    W, H, PAD = 1080, 560, 60
    series = [(b.name, b.curve) for b in books]
    all_v = [v for _, c in series for _, v in c]
    lo, hi = min(all_v + [0]), max(all_v + [0])
    span = (hi - lo) or 1.0
    n = len(series[0][1])
    dates = [d for d, _ in series[0][1]]

    def X(i: int) -> float:
        return PAD + i * (W - 2 * PAD) / max(1, n - 1)

    def Y(v: float) -> float:
        return H - PAD - (v - lo) * (H - 2 * PAD) / span

    colors = {"actual": "#888888", "rules_all": "#d62728", "rules_opt": "#1f77b4"}
    names = {"actual": "actual 实际", "rules_all": "rules 全栈", "rules_opt": "rules 期权-only"}
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
             f'style="background:#ffffff;font-family:Menlo,monospace">']
    # y 轴网格
    step = 5000 if span > 12000 else 2000
    y = math.floor(lo / step) * step
    while y <= hi:
        parts.append(f'<line x1="{PAD}" y1="{Y(y):.1f}" x2="{W-PAD}" y2="{Y(y):.1f}" '
                     f'stroke="{"#333" if y == 0 else "#e5e5e5"}" '
                     f'stroke-width="{1.4 if y == 0 else 0.7}"/>')
        parts.append(f'<text x="{PAD-8}" y="{Y(y)+4:.1f}" font-size="11" fill="#666" '
                     f'text-anchor="end">{y//1000:+d}k</text>')
        y += step
    # 月份刻度
    seen = set()
    for i, d in enumerate(dates):
        m = d[:7]
        if m not in seen:
            seen.add(m)
            parts.append(f'<line x1="{X(i):.1f}" y1="{PAD}" x2="{X(i):.1f}" y2="{H-PAD}" '
                         f'stroke="#f0f0f0"/>')
            parts.append(f'<text x="{X(i):.1f}" y="{H-PAD+18}" font-size="11" fill="#666">{m}</text>')
    for name, curve in series:
        pts = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, (_, v) in enumerate(curve))
        parts.append(f'<polyline points="{pts}" fill="none" stroke="{colors[name]}" '
                     f'stroke-width="1.8"/>')
    for k, name in enumerate(series):
        nm = name[0]
        fv = name[1][-1][1]
        parts.append(f'<text x="{PAD+10}" y="{PAD+16+k*18}" font-size="12" fill="{colors[nm]}">'
                     f'— {names[nm]}: {fv:+,.0f}</text>')
    parts.append(f'<text x="{PAD}" y="{PAD-24}" font-size="14" fill="#222">'
                 f'累计交易损益 (USD) — 实际 vs 退出规则栈反事实 ({dates[0]} → {dates[-1]})</text>')
    parts.append("</svg>")
    path.write_text("\n".join(parts), encoding="utf-8")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="退出规则栈反事实回测")
    ap.add_argument("--trail-confirm", action="store_true",
                    help="§2b 确认标定变体: 破线 <1N 首日不执行, 2连收/≥1N 才确认")
    ap.add_argument("--strict-lock", action="store_true",
                    help="canonical §3 严格 lock: 存活到结构修复, 拦重进")
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG,
                    help="ignored private JSON config")
    a = ap.parse_args()
    run(trail_confirm=a.trail_confirm, strict_lock=a.strict_lock, config_path=a.config)
