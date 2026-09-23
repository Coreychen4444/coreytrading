"""future_gates.py — 卡片阶梯之上的**远端 gate 候选** (机械地标, 零授权)。

为什么存在: 卡上的转移轴只到 `next_regime.entry_gate` + `next_gate_preview` (gate+2),
再往上图上一条线都没有, 而操作者需要看到更多的关键点位。「gate 是被发现的」——
tape 上反复做过决定的地方本来就在那儿, 不需要等它成为 next 才看得见。

分工 (近端归卡, 远端归尺):
    cur / next / preview  = technical agent 按画 gate 检查表判读 (多维收敛, 带出处), 住卡
    preview 之上          = 本模块, **同一把尺的 pivot 簇维**机械重放, 不住卡
  远端候选只是「那儿有个地标」, 不是转移轴 —— 转移机只扫 `entry_gate`
  (`regime_transition.detect_transition`), 本模块的产出不进任何授权 / sizing / 转移判据。
  它升格为 preview / next 的那天, 仍由 agent 按当日分布重新发现 (「转移日按新分布复核」)。

口径 = `bottom_evidence` 的锚收敛同一份 (不新造旋钮): 5 根分形 pivot (SWING_WING) →
0.35×ATR14 聚类 (CLUSTER_TOL_ATR14) → 250 根回看 (PIVOT_LOOKBACK) → ≥2 触碰才算地标
(MIN_TOUCHES)。检查表 #2「簇的**上沿** = gate (整条簇翻面)」→ 取 `hi`。
窗口最高价 = 历史极值维, 单触碰也算地标 (它是「再往上没有 tape」的那条边界)。

校准 (与 agent 画的未 stale preview 对照): 多数落在本模块候选的一个桶宽内 (相当一部分就是
第一条)。没中的几类:
  VP 边界型 (带内拒绝墙 / LVN 上沿) —— 量的维度本模块不看, **已知盲区**
  单触碰 pivot (agent 自己也标「单维, 弱」) —— 被 MIN_TOUCHES 挡掉
  gate 画在簇内而非上沿 —— 同一堵墙, 差一个桶宽
  取 ATH **收盘** 而非 ATH 最高价
  降级时搬运的旧阶梯, 非当前分布
⇒ 它重放的是 agent 那把尺里的**簇维**, 不是全部; 远端够用, 近端不够 —— 所以近端归卡。

拒绝计数 `rejections` = `level_map.level_interactions` 同判据 (最高价够到线下一个桶宽
但收盘没站上), 只作播报收据, **不参与筛选** —— 它有 ATR 自反性 (刚出过事的票每条线
都显得更强), 当门槛用等于把波动率当证据。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from . import technical
from .bottom_evidence import CLUSTER_TOL_ATR14, MIN_TOUCHES, PIVOT_LOOKBACK
from .setups import SWING_WING

_MIN_BARS = 60   # 簇要有历史才谈得上「反复」; 新上市票宁可不画 (与 level_map 半窗口同量级)
# 卡片阶梯顶之上再出几条机械候选 (3 是显示深度约定, 不是定价过的参数;
# 只决定图上 / 报表里看得见多远, 不进任何授权判据)。**住这里不住消费者**: moomoo 画线
# (`sync/price_reminder_sync`) 与 TG 阶梯表 (`report/gate_ladder_report`) 读同一个数 ——
# 渲染口径 = 判定口径, 两处各存一份迟早分叉。
FUTURE_GATES_MAX = 3


def _day(bar: dict) -> str:
    return str(bar.get("time") or bar.get("date") or "")[:10]


def candidates(bars: List[dict], above: float, n: int) -> List[Dict]:
    """`above` 之上最近的 ≤n 个远端 gate 候选, 升序 (纯函数)。

    `above` = 卡片阶梯顶 (未 stale 的 preview, 缺则 next gate)。**不看现价** ——
    线不因价格穿越而消失: 现价越过候选之后它原地不动, 方向由
    `price_reminder_sync.orient_sides` 翻。与阶梯顶**同一个地标**不重复出: 上沿在阶梯顶
    一个桶宽内, 或簇带本身盖住阶梯顶 (agent 常把 gate 画在簇内而非上沿 —— 再出一条簇上沿
    是同一堵墙画两遍)。
    返回 [{level, touches, kinds, last_touch, rejections, source('cluster'|'window_high')}]。
    bars 不足 / ATR 算不出 → 空 (不编数)。
    """
    if n <= 0 or not above or len(bars) < _MIN_BARS:
        return []
    seg = bars[-PIVOT_LOOKBACK:]
    atr_s = technical.atr(seg, period=14)
    atr14 = atr_s[-1] if atr_s and atr_s[-1] else None
    if not atr14:
        return []
    bucket = technical.auto_bucket(float(seg[-1]["close"]), atr14)
    floor = float(above) + bucket

    highs, lows = technical.swing_points(seg, wing=SWING_WING)
    pivots = ([{"price": p, "date": _day(seg[i]), "kind": "high"} for i, p in highs]
              + [{"price": p, "date": _day(seg[i]), "kind": "low"} for i, p in lows])
    clusters = technical.cluster_pivots(pivots, CLUSTER_TOL_ATR14 * atr14) if pivots else []
    out = [{"level": float(c["hi"]), "touches": int(c["touches"]), "kinds": c["kinds"],
            "last_touch": c["last_touch"], "source": "cluster"}
           for c in clusters
           if c["touches"] >= MIN_TOUCHES and c["hi"] > floor and c["lo"] > float(above)]

    # 历史极值维: 窗口最高价。已被某个簇上沿覆盖 (一个桶宽内) 就不重复。
    top_bar = max(seg, key=lambda r: r["high"])
    top = round(float(top_bar["high"]), 2)
    if top > floor and not any(abs(top - c["level"]) <= bucket for c in out):
        out.append({"level": top, "touches": 1, "kinds": "high",
                    "last_touch": _day(top_bar), "source": "window_high"})

    out.sort(key=lambda c: c["level"])
    out = out[:n]
    for c in out:
        c["rejections"] = _rejections(seg, c["level"], bucket)
    return out


def _rejections(bars: List[dict], level: float, near: float) -> int:
    """最高价够到 `level − near` 但收盘没站上 `level` 的根数 (level_interactions 同判据)。"""
    return sum(1 for r in bars if r["close"] <= level and r["high"] >= level - near)


def evidence_tag(c: Dict) -> str:
    """一条候选的收据短语 (进 moomoo 备注, 不含 '/' —— 那是并入分隔符)。"""
    if c.get("source") == "window_high":
        return "窗高"
    # H = 全是 swing high (供给) / L = 全是 swing low (破掉的支撑) / HL = 翻过面的混合簇 (最硬)
    kinds = "".join(k[0].upper() for k in str(c.get("kinds") or "").split("+") if k)
    return (f"簇×{c['touches']}{kinds}"
            + (f" 拒{c['rejections']}" if c.get("rejections") else ""))


def for_card(agent: Optional[dict], bars: List[dict]) -> List[Dict]:
    """一张卡的机械远端候选 + 它在阶梯上的编号 `n` —— **两个消费者的唯一入口**。

    编号接着卡片阶梯往上数: 有未 stale 的 preview → 它占 +2, 机械候选从 +3 起;
    没有 → 从 +2 起。没有 next gate 的卡没有轴可接 → 空。"""
    top = ladder_top(agent)
    if top is None:
        return []
    out = candidates(bars, top, FUTURE_GATES_MAX)
    k0 = 3 if live_preview(agent) is not None else 2
    for i, c in enumerate(out):
        c["n"] = k0 + i
    return out


def _level(blk: Optional[dict]) -> Optional[float]:
    try:
        lvl = float((blk or {}).get("level"))
    except (TypeError, ValueError):
        return None
    return lvl if lvl > 0 else None


def live_preview(agent: Optional[dict]) -> Optional[float]:
    """卡上未 stale 的 `next_gate_preview.level`; `stale: true` = 降级时保留的旧上行阶梯
    (「价位已失效待重画」) → None, 既不画成线也不当锚。"""
    pv = ((agent or {}).get("next_regime") or {}).get("next_gate_preview") or {}
    return None if pv.get("stale") else _level(pv)


def ladder_top(agent: Optional[dict]) -> Optional[float]:
    """卡片阶梯顶 = 未 stale 的 preview, 缺则 next gate; 都没有 → None (没有轴可接)。

    stale preview 不当锚 (见 `live_preview`), 远端候选从 next gate 接上。"""
    pv = live_preview(agent)
    if pv is not None:
        return pv
    return _level(((agent or {}).get("next_regime") or {}).get("entry_gate"))
