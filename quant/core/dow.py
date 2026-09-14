"""短期道氏结构状态机 —— 「趋势终止观察期」的单一真源（判读层）。

本机追踪一条短周期、全部按完成收盘确认的四步链：结构被逆向 K 线破坏、
折返点成形、反向尝试失败、最后跌破或站上折返点确认趋势终止。合成例
``100 → 200 → 180 → 190 → 170`` 表示上涨趋势完成 LH+LL 序列；下跌逐字镜像。

判别式（K 线级转折；全部收盘口径；零数值参数）：

  转折     阴线 = close < 前一根的 low；阳线 = close > 前一根的 high。
           不是「收阴」（红 K）—— 红 K 太弱，任何一天都可能；「收破前一根的低点」才是
           「破坏了（K 线级的 HL）结构」。用前一根的极值而不是等待分形确认，
           使状态机能描述数根 K 线内的结构。
  推进(0)  冲动极值 imp（上涨 = 运行中的最高 high = lsh）随新高刷新；前一次回调的低点 = ret（HL）。
  观察①   一根阴线收破前一根低点 → 进入观察①。lsh 就此定格；obs = 破坏以来的最低 low（候选 lsl）。
           收盘收复 lsh ⇒ 恢复（V 形，候选低点直接升格为 HL）。
           「几根大阴线直接砸穿」= 一直停在①，候选跟着新低走 —— **那段归 stop，机器不裁**。
  观察②   一根阳线收上前一根高点 ⇒ lsl 成形（= obs）。看反弹：cnt = 反弹以来最高 high（候选 LH）。
           收盘收复 lsh ⇒ 恢复（洗盘后延续，lsl 升格为 HL）；收盘跌破 lsl ⇒ 终止（反弹极值记为 LH）。
  观察③   反弹里再出一根阴线收破前一根低点 ⇒ 反弹失败，LH = cnt 定格。看 lsl：
           收盘跌破 lsl ⇒ 终止；收盘收复 lsh ⇒ 恢复；再出阳线收上前一根高点 ⇒ 回到②（反弹续）。
           观察期内 intrabar 更低的低点让 lsl 跟着下移（道氏比较的永远是最近那个低点），
           收盘没破就不算破。
  终止     LH+LL 序列完成：趋势翻面。新下跌的冲动极值 = 破位那根的 low（继续刷新），
           要守的 LH = 那个反弹高点。下跌逐字镜像（阳线破坏 → 等 lsh → 看回踩 vs lsl → HL → 收上 lsh = 反转）。

  标签     结构摆动点 = 每次观察①定格的 lsh、每次观察②成形的 lsl、每次终止定格的 LH；
           与上一个同类比 → HH/LH、HL/LL。`labels` 只是快照，阶段才是序列读数。

  与 5 根分形 `technical.swing_structure` 的关系：**两个尺度，各标各的**。那是日线结构
  （授权层口径：结构修复 VETO / gate_path / exit_guard 标签的输入，一字不改）；本机是
  K 线级的短期结构。卡片两行并列，不互相解释。

本模块被 `report/chart_read`（判读）与 `decision/entry_guard` / `report/portfolio_brief`（**盘前清单
排序键**，见 `priority()`：只排序不授权 —— 不改 PLAN/VETO、不改 cap、不改股数）消费，不 import 任何
core 以外的东西。「终止确认」在这一层只是裁量复核点，stop / 棘轮 /
趋势死亡线照旧是硬线。若要接入 exit_guard，必须另行完成退出规则验证。

无前视：第 t 根只用 bars[≤t]；`structure_state(bars[:t+1])` 与全量回放到 t 的状态逐字相同
（`tests/core/test_dow.py` 钉）。只吃**已完成日线**（盘中 bar 当收盘是本仓栽过的坑）。
"""
from __future__ import annotations

from typing import Dict, List, Optional

RECENT_EVENTS = 6   # 输出里保留最近几条事件 / 摆动点（再早的对「当前所处」没有信息）

# ── Rhea 的次级折返带（《The Dow Theory》1932）：次级折返通常回撤前一段的 1/3–2/3。──
# 本机拿它给观察期定「成色」：lsl 成形那一刻回撤了最后一段（lsh−HL）的几成。
# 阈值是发表的，不是拟合的；带名是**分桶标签**，不是判断。
RETRACE_BANDS = ((1 / 3, "<1/3"), (2 / 3, "1/3-2/3"), (1.0, "2/3-1"))
RETRACE_BAND_CN = {"<1/3": "轻微回调·不到次级折返", "1/3-2/3": "次级折返·Rhea 带内",
                   "2/3-1": "深折返·过 2/3 上界", ">1": "吐回整段·跌破前 HL/LH"}
def retrace_band(frac: Optional[float]) -> Optional[str]:
    """回撤比例 → Rhea 带名。None 进 None 出。"""
    if frac is None:
        return None
    for lim, name in RETRACE_BANDS:
        if (frac < lim) if name == "<1/3" else (frac <= lim):
            return name
    return ">1"

# 阶段显示名 —— 单一真源，渲染层不许自己再写一份（两处映射迟早分叉）
STAGE_CN: Dict[tuple, str] = {
    ("up", 0): "上涨·推进中（结构完好，尚无阴线破坏）",
    ("up", 1): "上涨·终止观察①（阴线破坏结构，等 lsl 成形）",
    ("up", 2): "上涨·终止观察②（lsl 已成形，看反弹 vs lsh）",
    ("up", 3): "上涨·终止观察③（反弹失败 LH，看 lsl 守不守）",
    ("down", 0): "下跌·推进中（结构完好，尚无阳线破坏）",
    ("down", 1): "下跌·止跌观察①（阳线破坏结构，等 lsh 成形）",
    ("down", 2): "下跌·止跌观察②（lsh 已成形，看回踩 vs lsl）",
    ("down", 3): "下跌·止跌观察③（回踩守住 HL，看 lsh 破不破）",
    (None, 0): "方向未定（K 线不足）",
}
EVENT_CN = {
    "seed": "结构建立",
    "obs1": "转折 K 线破坏结构（进入观察①）",
    "obs2": "折返点成形（观察②）",
    "obs3": "反向尝试失败（观察③）",
    "obs2_again": "反向尝试重启（回到观察②）",
    "resume": "冲动极值被收复（折返只是洗盘，原趋势延续）",
    "terminate": "趋势终止确认（序列完成）",
}
RESOLUTION_CN = {
    ("terminate", "up"): "趋势终止确认（LH+LL）→ 转下跌",
    ("terminate", "down"): "下跌终止确认（HL+HH）→ 转上涨",
    ("resume", "up"): "结构恢复（收复 lsh，洗盘后延续上涨）",
    ("resume", "down"): "下跌恢复（跌破 lsl，反弹失败，延续下跌）",
}


def _f(x) -> float:
    return float(x or 0)


def _date(bar: dict) -> str:
    return str(bar.get("time") or bar.get("date") or "")[:10]


# ── 转折 K 线的**单一定义**（定义：阴线 = close < 前一根 low；阳线 = close > 前一根 high）──
# 从 `_Machine` 提成模块级：`core/setups`（两种模式的判读）要用**同一条**判别式，
# 抄第二遍的那天「阳线止跌」在两个模块里就会开始各说各话（同概念双判别式的风险）。

def bull_bar(bars: List[dict], t: int) -> bool:
    """第 t 根是不是**阳线**（收盘收上前一根的高点）。t=0 恒 False（没有前一根）。"""
    return t >= 1 and _f(bars[t].get("close")) > _f(bars[t - 1].get("high"))


def bear_bar(bars: List[dict], t: int) -> bool:
    """第 t 根是不是**阴线**（收盘收破前一根的低点）。t=0 恒 False。"""
    return t >= 1 and _f(bars[t].get("close")) < _f(bars[t - 1].get("low"))


class _Machine:
    """一次回放 = 一台机。状态 = 「最近一个 XX」，道氏的全部记忆就这么多。

    字段按**趋势方向**取名（上涨 ↔ 下跌逐字镜像，靠 `s` 翻符号）：
      imp  冲动极值（上涨 = lsh）        ret  本段起点 = 上一次折返点（上涨 = HL）
      obs  观察期里的折返极值（上涨 = lsl 候选 / 已成形）
      cnt  反向尝试的极值（上涨 = 反弹高点 = LH 候选 / 已定格）
    """

    def __init__(self, bars: List[dict]):
        self.bars = bars
        self.highs = [_f(b.get("high")) for b in bars]
        self.lows = [_f(b.get("low")) for b in bars]
        self.closes = [_f(b.get("close")) for b in bars]
        self.trend: Optional[str] = None
        self.phase = 0
        self.imp: Optional[Dict] = None
        self.ret: Optional[Dict] = None
        self.obs: Optional[Dict] = None
        self.cnt: Optional[Dict] = None
        self.obs_start: Optional[Dict] = None
        self.obs_frac: Optional[float] = None   # lsl 成形那一刻的回撤比例（Rhea 带用）
        self.obs_committed = False         # obs 是否已作为结构摆动点登记（观察②时登记）
        self.swing_h: List[Dict] = []       # 结构摆动高点（带 HH/LH）
        self.swing_l: List[Dict] = []       # 结构摆动低点（带 HL/LL）
        self.events: List[Dict] = []
        self.timeline: List[tuple] = []
        self.phase_since = 0
        self.resolved_at = -1
        self.t = 0

    # ── 方向代数 ────────────────────────────────────────────────
    @property
    def s(self) -> int:
        return 1 if self.trend == "up" else -1

    def _ahead(self, a: float, b: float) -> bool:
        return self.s * a > self.s * b

    def _behind(self, a: float, b: float) -> bool:
        return self.s * a < self.s * b

    def _ext_with(self, t: int) -> float:
        """这根 K 线在趋势方向上的极值（上涨 = high）。"""
        return self.highs[t] if self.trend == "up" else self.lows[t]

    def _ext_against(self, t: int) -> float:
        return self.lows[t] if self.trend == "up" else self.highs[t]

    def _bear(self, t: int) -> bool:
        return bear_bar(self.bars, t)

    def _bull(self, t: int) -> bool:
        return bull_bar(self.bars, t)

    def _against_bar(self, t: int) -> bool:
        """逆趋势的转折 K 线（上涨 = 阴线收破前一根低点）。"""
        return self._bear(t) if self.trend == "up" else self._bull(t)

    def _with_bar(self, t: int) -> bool:
        return self._bull(t) if self.trend == "up" else self._bear(t)

    def _pt(self, idx: int, price: float, **extra) -> Dict:
        d = {"idx": idx, "price": round(price, 4), "date": _date(self.bars[idx])}
        d.update(extra)
        return d

    def _event(self, typ: str, **extra) -> None:
        e = {"idx": self.t, "date": _date(self.bars[self.t]), "type": typ,
             "cn": EVENT_CN.get(typ, typ), "close": round(self.closes[self.t], 4),
             "trend": self.trend, "phase": self.phase}
        e.update(extra)
        self.events.append(e)

    def _label(self, lst: List[Dict], price: float, kind: str) -> Optional[str]:
        if not lst:
            return None
        prev = lst[-1]["price"]
        if kind == "H":
            return "HH" if price > prev else "LH"
        return "HL" if price > prev else "LL"

    def _commit(self, p: Dict, kind: str) -> Dict:
        lst = self.swing_h if kind == "H" else self.swing_l
        q = {**p, "kind": kind, "label": self._label(lst, p["price"], kind),
             "confirmed_at": self.t}
        lst.append(q)
        return q

    def _labels(self) -> str:
        h = self.swing_h[-1]["label"] if self.swing_h else None
        lo = self.swing_l[-1]["label"] if self.swing_l else None
        return f"{h or '?'}+{lo or '?'}"

    def _enter(self, phase: int) -> None:
        self.phase, self.phase_since = phase, self.t

    # ── 转移 ──────────────────────────────────────────────────
    def _start_obs(self) -> None:
        t = self.t
        self.imp = self._commit(self.imp, "H" if self.trend == "up" else "L")
        lo_i = self.imp["idx"] + 1
        rng = range(lo_i, t + 1) if lo_i <= t else range(t, t + 1)
        j = (min(rng, key=lambda i: self.lows[i]) if self.trend == "up"
             else max(rng, key=lambda i: self.highs[i]))
        self.obs = self._pt(j, self._ext_against(j))
        self.obs_committed = False
        self.obs_frac = None
        self.cnt = None
        self.obs_start = {"idx": t, "date": _date(self.bars[t]), "close": round(self.closes[t], 4),
                          "broke": round(self._ext_against(t - 1), 4) if t >= 1 else None}
        self._enter(1)
        self._event("obs1", imp=self.imp["price"], broke=self.obs_start["broke"])

    def _resume(self) -> None:
        t = self.t
        self._event("resume", from_phase=self.phase, level=self.imp["price"])
        if not self.obs_committed:
            self.obs = self._commit(self.obs, "L" if self.trend == "up" else "H")
            self.obs_committed = True
        self.ret = self.obs
        self.imp = self._pt(t, self._ext_with(t))
        self.obs = self.cnt = self.obs_start = None
        self.obs_frac = None
        self.resolved_at = t
        self._enter(0)

    def _terminate(self) -> None:
        t = self.t
        old = self.trend
        cnt = self._commit(self.cnt, "H" if old == "up" else "L")
        self._event("terminate", from_phase=self.phase, level=self.obs["price"],
                    cnt=cnt, imp=self.imp, retrace=self.obs_frac,
                    band=retrace_band(self.obs_frac))
        self.trend = "down" if old == "up" else "up"
        self.ret = cnt
        self.imp = self._pt(t, self._ext_with(t))
        self.obs = self.cnt = self.obs_start = None
        self.obs_frac = None
        self.resolved_at = t
        self._enter(0)

    # ── 每根 ──────────────────────────────────────────────────
    def _seed(self) -> None:
        """方向从**第一次逆着开局走势的转折 K 线**建立：开局以来在涨 + 一根阴线 → 上涨进观察①；
        开局以来在跌 + 一根阳线 → 下跌进观察①。顺着走势的转折不算（一路阳线的上涨不是
        「下跌被破坏」）。起点影响在几次终止/恢复后消失（全书深度缓存 ≥2,700 根）。"""
        t = self.t
        rising = self.closes[t] >= self.closes[0]
        if rising and self._bear(t):
            self.trend = "up"
        elif not rising and self._bull(t):
            self.trend = "down"
        else:
            return
        rng = range(0, t + 1)
        j = (max(rng, key=lambda i: self.highs[i]) if self.trend == "up"
             else min(rng, key=lambda i: self.lows[i]))
        self.imp = self._pt(j, self._ext_with(j))
        self.resolved_at, self.phase_since = t, t
        self._event("seed")
        self._start_obs()

    def _step(self) -> None:
        t = self.t
        if self.trend is None:
            self._seed()
            return
        c = self.closes[t]
        if self.phase == 0:
            if self._ahead(self._ext_with(t), self.imp["price"]):
                self.imp = self._pt(t, self._ext_with(t))
            if self._against_bar(t):
                self._start_obs()
            return
        if self.phase == 1:
            if self._behind(self._ext_against(t), self.obs["price"]):
                self.obs = self._pt(t, self._ext_against(t))
            if self._ahead(c, self.imp["price"]):
                self._resume()
            elif self._with_bar(t):
                self.obs = self._commit(self.obs, "L" if self.trend == "up" else "H")
                self.obs_committed = True
                self.cnt = self._pt(t, self._ext_with(t))
                if self.ret and self.imp["price"] != self.ret["price"]:
                    leg = self.imp["price"] - self.ret["price"]
                    self.obs_frac = round((self.imp["price"] - self.obs["price"]) / leg, 4)
                self._enter(2)
                self._event("obs2", level=self.obs["price"], retrace=self.obs_frac,
                            band=retrace_band(self.obs_frac))
            return
        # phase 2 / 3：先裁终止与恢复（都按**这根之前**的线），再更新极值与转折
        if self._behind(c, self.obs["price"]):
            self._terminate()
            return
        if self._ahead(c, self.imp["price"]):
            self._resume()
            return
        if self._ahead(self._ext_with(t), self.cnt["price"]):
            self.cnt = self._pt(t, self._ext_with(t))
        if self._behind(self._ext_against(t), self.obs["price"]):
            self.obs = self._pt(t, self._ext_against(t))       # intrabar 更低：lsl 下移
        if self.phase == 2 and self._against_bar(t):
            self._enter(3)
            self._event("obs3", level=self.cnt["price"])
        elif self.phase == 3 and self._with_bar(t):
            self._enter(2)
            self._event("obs2_again", level=self.cnt["price"])

    def run(self) -> "_Machine":
        for t in range(len(self.bars)):
            self.t = t
            self._step()
            self.timeline.append((self.trend, self.phase))
        return self


def _candidate(m: _Machine) -> Optional[Dict]:
    """观察期里下一个要定格的点 + 让它定格的那根 K 线长什么样。"""
    t = len(m.bars) - 1
    up = m.trend == "up"
    if m.trend is None or m.phase == 0:
        return None
    if m.phase == 1:
        return {"role": "lsl" if up else "lsh", "price": m.obs["price"], "date": m.obs["date"],
                "confirm_if": (f"一根阳线收上 {m.highs[t]:g}" if up
                               else f"一根阴线收破 {m.lows[t]:g}")}
    if m.phase == 2:
        return {"role": "LH" if up else "HL", "price": m.cnt["price"], "date": m.cnt["date"],
                "confirm_if": (f"一根阴线收破 {m.lows[t]:g} = 反弹失败" if up
                               else f"一根阳线收上 {m.highs[t]:g} = 回踩守住")}
    return None


def _leg_volume(m: _Machine) -> Optional[Dict]:
    """反向尝试腿 vs 折返腿的均量比（Wyckoff 的老比较：反弹缩量 = 需求弱）。
    只在观察②/③有意义；纯 ticker 内部比较，不需要基准。"""
    if m.trend is None or m.phase not in (2, 3) or not m.obs or not m.cnt:
        return None
    vols = [_f(b.get("volume")) for b in m.bars]
    a, b = m.imp["idx"] + 1, m.obs["idx"]
    c = (m.phase_since - 1) if m.phase == 3 else len(m.bars) - 1   # ③：反弹腿止于失败那根之前
    retrace = vols[a:b + 1]
    attempt = vols[b + 1:c + 1]
    if not retrace or not attempt or sum(retrace) <= 0:
        return None
    return {"retrace_bars": len(retrace), "attempt_bars": len(attempt),
            "ratio": round((sum(attempt) / len(attempt)) / (sum(retrace) / len(retrace)), 2)}


_EMPTY = {"trend": None, "phase": 0, "stage_cn": STAGE_CN[(None, 0)], "labels": "?+?",
          "imp": None, "ret": None, "obs": None, "cnt": None, "obs_start": None,
          "bars_in_phase": 0, "watch": {}, "candidate": None, "leg_volume": None,
          "retrace": None, "retrace_at_form": None, "events": [], "swings": [],
          "last_resolution": None, "last_close": None, "up": None}


def structure_state(bars: List[dict]) -> Dict:
    """回放全序列 → 当前所处的道氏阶段 + 要看的线 + 最近事件。只吃**已完成日线**。"""
    if len(bars) < 3:
        return dict(_EMPTY)
    m = _Machine(bars).run()
    n = len(bars)
    t = n - 1
    up = m.trend == "up"
    watch: Dict[str, Optional[float]] = {}
    retrace = None
    if m.trend:
        if m.phase == 0:
            watch = {"imp_level": m.imp["price"],
                     "trigger_ref": m._ext_against(t),           # 明天收破它 = 观察①
                     "prev_ret": m.ret["price"] if m.ret else None}
        else:
            watch = {"resume_level": m.imp["price"],
                     "obs_level": m.obs["price"],
                     "terminate_level": m.obs["price"] if m.phase >= 2 else None,
                     "cnt_level": m.cnt["price"] if m.cnt else None,
                     "prev_ret": m.ret["price"] if m.ret else None}
            if m.ret and m.imp["price"] != m.ret["price"]:
                leg = m.imp["price"] - m.ret["price"]
                retrace = {"pct": round((m.imp["price"] - m.obs["price"]) / leg, 2),
                           "broke_prev_ret": m._behind(m.obs["price"], m.ret["price"])}
    last_res = next((e for e in reversed(m.events) if e["type"] in ("resume", "terminate")), None)
    swings = sorted(m.swing_h + m.swing_l, key=lambda p: p["idx"])
    return {
        "trend": m.trend, "phase": m.phase,
        "stage_cn": STAGE_CN[(m.trend, m.phase)],
        "labels": m._labels(),
        "imp": m.imp, "ret": m.ret, "obs": m.obs, "cnt": m.cnt, "obs_start": m.obs_start,
        "bars_in_phase": (t - m.phase_since) if m.trend else 0,
        "watch": watch,
        "candidate": _candidate(m),
        "leg_volume": _leg_volume(m),
        "retrace": retrace,
        "retrace_at_form": ({"pct": m.obs_frac, "band": retrace_band(m.obs_frac),
                             "band_cn": RETRACE_BAND_CN[retrace_band(m.obs_frac)]}
                            if (m.trend and m.phase >= 2 and m.obs_frac is not None) else None),
        "events": m.events[-RECENT_EVENTS:],
        "swings": swings[-RECENT_EVENTS:],
        "last_resolution": ({**last_res, "bars_ago": t - last_res["idx"],
                             "cn": RESOLUTION_CN.get((last_res["type"], last_res["trend"]),
                                                     last_res["cn"])}
                            if last_res else None),
        "last_close": round(m.closes[-1], 4),
        "up": up if m.trend else None,
    }


def replay(bars: List[dict]) -> Dict:
    """全量回放 —— 普查 / 回测用：所有事件 + 每根的 (trend, phase)。"""
    if len(bars) < 3:
        return {"events": [], "timeline": [(None, 0)] * len(bars)}
    m = _Machine(bars).run()
    return {"events": m.events, "timeline": m.timeline}


def describe(state: Dict) -> List[str]:
    """机械白话（不带距离 —— 距离要 ATR，归渲染层）。每条都是「线 + 语义」。"""
    tr, ph = state.get("trend"), state.get("phase", 0)
    L = [state["stage_cn"]]
    if tr is None:
        return L
    w = state["watch"]
    up = tr == "up"
    if ph == 0:
        L.append(f"{'lsh' if up else 'lsl'} {w['imp_level']:g} 刷新中；"
                 f"一根{'阴' if up else '阳'}线收{'破' if up else '上'} {w['trigger_ref']:g}"
                 f"（前一根{'低' if up else '高'}点）= 进入{'终止' if up else '止跌'}观察①"
                 + (f"；前 {'HL' if up else 'LH'} {w['prev_ret']:g}" if w.get("prev_ret") else ""))
        return L
    L.append(f"{'收复' if up else '跌破'} {w['resume_level']:g} = "
             f"{'结构恢复（洗盘后延续上涨）' if up else '下跌恢复（反弹失败，延续下跌）'}")
    if w.get("terminate_level") is not None:
        L.append(f"{'跌破' if up else '站上'} {w['terminate_level']:g} = "
                 f"{'趋势终止确认（LH+LL）' if up else '下跌终止确认（HL+HH）'}")
    cand = state.get("candidate")
    if cand:
        L.append(f"候选 {cand['role']} {cand['price']:g}（{cand['date']}）：{cand['confirm_if']}")
    return L


# ══════════════════════════════════════════════════════════════
# 盘前清单排序键
# ══════════════════════════════════════════════════════════════
#
# 道氏终止不接退出栈；这里只用于同一批 PLAN 票的排序，不决定能不能挂、挂多少。
#   1 优先：上涨·推进中（含 洗盘后延续 / 反转向上已确认 = 刚从下跌终止转上涨）
#   2 次位：观察期 —— 上涨·终止观察①②③；下跌·止跌观察②③（反转未确认）
#   3 末位：下跌·推进中 / 止跌观察①（连 lsh 都没成形）；方向未定同列末位
# 同档内用周线级打破平局（周线级 上涨·推进中 > 上涨·观察期 > 下跌 > 未定）。
PRIORITY_CN = {1: "优先", 2: "次位", 3: "末位"}


def priority(state: Dict, weekly: Optional[Dict] = None, recent: int = 5) -> Dict:
    """→ {tier, short, cn, sort_key, weekly_cn}。`sort_key` 越小越先挂。"""
    tr, ph = state.get("trend"), state.get("phase", 0)
    res = state.get("last_resolution") or {}
    recent_term = res.get("type") == "terminate" and res.get("bars_ago", 99) <= recent
    recent_res = res.get("type") == "resume" and res.get("bars_ago", 99) <= recent
    if tr is None:
        tier, why = 3, "方向未定"
    elif tr == "up" and ph == 0:
        tier = 1
        why = (f"反转向上已确认（HL+HH {res['date'][5:]}）" if recent_term
               else f"洗盘后延续（{res['date'][5:]} 收复 lsh）" if recent_res
               else "上涨推进中")
    elif tr == "up":
        tier, why = 2, f"终止观察{'①②③'[ph - 1]}"
    elif ph >= 2:
        tier, why = 2, f"止跌观察{'①②③'[ph - 1]}（反转未确认）"
    else:
        tier, why = 3, ("下跌推进中" if ph == 0 else "止跌观察①")
    wrank, wcn = 3, None
    if weekly and weekly.get("trend"):
        wt, wp = weekly["trend"], weekly.get("phase", 0)
        wrank = 0 if (wt == "up" and wp == 0) else (1 if wt == "up" else 2)
        wcn = str(weekly.get("stage_cn", "")).split("（")[0]
    return {"tier": tier, "short": PRIORITY_CN[tier], "cn": f"{PRIORITY_CN[tier]}·{why}",
            "sort_key": (tier, wrank), "weekly_cn": wcn}


def priority_of_bars(bars: List[dict], weekly_bars: Optional[List[dict]] = None) -> Dict:
    """一步到位：日线 + 周线（调用方传 `technical.resample_weekly(bars)`）→ priority + stage_cn。"""
    st = structure_state(bars)
    wk = structure_state(weekly_bars) if weekly_bars and len(weekly_bars) >= 10 else None
    out = priority(st, wk if (wk and wk.get("trend")) else None)
    out["stage_cn"] = st["stage_cn"]
    return out
