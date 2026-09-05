"""Earnings reaction-session sizing adjustment.

Production rule:

    财报反应日 T+0 的仓位上限 = 名义上限 × 缩仓比例(per-ticker) × 0.7
    恢复 = T+1 起全额 (缩仓只压 T+0 那一天; T+1 开盘前 event_state 已回 1.0)

    缩仓比例 = min(1.0, X_normal / X_earn)
    X_earn   = C 收缩臂: λ·单票 p98 + (1−λ)·池级 p90, λ = n/(n+10);
               先验事件 <8 → 直接用池级 p90 (3.00N)

Why it exists: under a 40% nominal ceiling, a single-name 4N earnings event
是 **13–21% NAV 一天**, 已经超过 15% 的组合总预算本身。**40% 若不配财报窗口缩仓,
等于把整个组合预算押在一次财报上。**

**这不违反  的技术面优先 v3**: v3 把财报从**授权层**降为情报层
(不因财报 veto / 降级 / 延迟参与); 本条属于 **sizing 层的风险度量**—— 它不改
"能不能进", 只改"那一天能有多大"。两个不同的层, design §2.6 已写明。

本模块只读 `market/earnings_calendar.json` (由 `quant/data/earnings_calendar.py`
每日重建)。**日历缺失/过期 = 不缩仓并报出来**—— 静默不缩比缩错更危险, 但静默
"因为读不到日历所以没缩"又是本仓最常见的那种失效, 所以两头都要说话。
"""
from __future__ import annotations

from datetime import date
from typing import Dict, List, Optional

from .io_json import read_json
from .num import coerce_float
from .paths import EARNINGS_CALENDAR_PATH
from .sizing import SHOCK_X

# 与 research/earnings_window.py 同一组预注册常量。X_normal 取 live 的 SHOCK_X
# (1.88) 而不是设计稿里写的 1.94 —— 判据是"财报日占用不高于**它自己的**普通日
# 占用", 普通日那一项用的就是 SHOCK_X。两处不同步会让缩仓比例悄悄偏松 3%。
POOL_X_EARN = 3.00          # A 臂池级 p90, 也是先验不足时的回退
SHRINK_N0 = 10              # C 臂 λ = n/(n+n0)
MIN_PRIOR = 8               # 先验事件 <8 → 回退池级
EVENT_MULT = 0.7            # §10c: 再乘 m=0.7 才进 [1%,4%] 超限带
CALENDAR_STALE_DAYS = 7     # 日历超此天数未重建 → 报出来 (仍用, 但要知道它旧了)
PUB_TYPE_LABEL = {1: "BMO", 2: "AMC", 3: "盘中"}


def shrink_ratio(x_earn: Optional[float]) -> float:
    """X_earn → 该票财报反应日的缩仓比例 (含 §10c 的 m=0.7)。

    X_earn ≤ X_normal (如 NVDA 2.29N) → 比例 1.0, 只剩 m; 高波动票 (AAOI 7.36N)
    → 0.26 × 0.7 ≈ 0.18。**只会收紧, 永不放大。**
    """
    if not x_earn or x_earn <= 0:
        x_earn = POOL_X_EARN
    return min(1.0, SHOCK_X / float(x_earn)) * EVENT_MULT


def load_calendar(path=None) -> Dict:
    return read_json(path or EARNINGS_CALENDAR_PATH) or {}


def event_state(ticker: str, today: str, calendar: Optional[Dict] = None) -> Dict:
    """今天是不是 `ticker` 的财报反应日, 以及该压多少。

    → {"active", "shrink", "reaction_day", "x_earn", "n_prior", "implied_move_pct",
       "note"}。`active=False` 时 `shrink` 恒 1.0 (调用方可以无脑乘)。
    """
    cal = calendar if calendar is not None else load_calendar()
    row = ((cal.get("tickers") or {}).get(ticker.upper())) or {}
    day = str(row.get("next_reaction_day") or "")[:10]
    out = {"active": False, "shrink": 1.0, "reaction_day": day or None,
           "x_earn": coerce_float(row.get("x_earn")),
           "n_prior": row.get("n_prior"),
           "implied_move_pct": coerce_float(row.get("implied_move_pct")),
           "note": None}
    if not cal:
        out["note"] = ("⚠️ 财报日历缺失 — **本轮未做财报缩仓**"
                       "(跑 quant/data/earnings_calendar.py; 40% 名义上限在财报日"
                       "等于把整个组合预算押在一次财报上)")
        return out
    as_of = str(cal.get("as_of") or "")[:10]
    try:
        age = (date.fromisoformat(str(today)[:10]) - date.fromisoformat(as_of)).days
    except ValueError:
        age = None
    if age is None or age > CALENDAR_STALE_DAYS:
        out["note"] = (f"⚠️ 财报日历 as_of {as_of or '缺失'} 已 "
                       f"{age if age is not None else '?'} 天 — 反应日可能已改期")
    if day and day == str(today)[:10]:
        out["active"] = True
        out["shrink"] = shrink_ratio(out["x_earn"])
    return out


def event_line(ticker: str, state: Dict) -> Optional[str]:
    """播报行。**未生效也要说**—— 财报在 3 天后 vs 日历读不到, 是两回事。"""
    if state.get("active"):
        src = (f"X_earn {state['x_earn']:.2f}N/{state['n_prior']} 事件"
               if state.get("x_earn") and state.get("n_prior") is not None
               else f"X_earn 回退池级 {POOL_X_EARN:.2f}N (先验 <{MIN_PRIOR})")
        im = (f", 期权隐含 ±{state['implied_move_pct']:.1f}%"
              if state.get("implied_move_pct") else "")
        return (f"📉 财报反应日 {state['reaction_day']} — 本日仓位上限 ×"
                f"{state['shrink']:.2f} ({src}, ×{EVENT_MULT} 事件系数{im});"
                " T+1 起恢复全额 [sizing 层风险度量, 不是授权层 gate — v3 不变]")
    if state.get("note"):
        return state["note"]
    if state.get("reaction_day"):
        return f"[FYI] 下一个财报反应日 {state['reaction_day']} — 当日仓位上限会自动缩"
    return None


def landed_print(row: Optional[Dict], today: str) -> Optional[Dict]:
    """该票**已落地**(含今日落地)的那一期 print → `{pub_day, pub_type, reaction_day}`。

    两个来源取较新的一个:
      `next_reaction_day`  前视条目, 反应日到了就算落地 (AMC 是 T+1, BMO 当日)
      `last_print`         粘住的那期 (`data/earnings_calendar.merge_last_print` 写)

    **为什么需要粘住的那半**: 前视条目在反应日**次日**就滚走 —— `pick_upcoming`
    的 `rday < today` 会把它剔成历史期, 条目换成下一季。只读前视条目 = 这条判别式
    只在反应日当天成立一天, 而它要抓的恰恰是"没人发现"的那种漏 (LITE
    AMC, 卡锚停在 8/06 无人察觉了四个交易日)。一条只活一天的告警修不了这个。
    """
    row = row or {}
    day = str(today)[:10]
    cands: List[Dict] = []
    lp = row.get("last_print")
    if isinstance(lp, dict) and str(lp.get("reaction_day") or "")[:10] <= day:
        if lp.get("reaction_day"):
            cands.append(dict(lp))
    rday = str(row.get("next_reaction_day") or "")[:10]
    if rday and rday <= day:
        cands.append({"pub_day": row.get("pub_day"), "pub_type": row.get("pub_type"),
                      "reaction_day": rday,
                      "implied_move_pct": row.get("implied_move_pct")})
    return max(cands, key=lambda c: str(c["reaction_day"])) if cands else None


def cards_stale_after_print(*docs: Optional[dict], today: str,
                            calendar: Optional[Dict] = None) -> List[str]:
    """print 已落地、卡锚还停在 print 之前 —— **这张图不含反应日的信息**。

    判据 = `anchor_date(agent) < landed_print(...).reaction_day`。锚在反应日当天或
    之后 = 已重画, 自动消音; 卡一直不重画就一直吵 (同 `cards_missing_next_gate`)。

    🚨 **只报不拦, 永不写 `pending_repaint`**: 技术面优先 v3 () 把财报降为
    情报层 —— 财报不冻结、不降级、不延迟参与, **只要求地图及时跟上**。写
    `pending_repaint` 会让 entry_guard 转 NEEDS_CARD, 那就是每个财报反应日自动
    VETO 一次 =  AMZN print 日"僵尸拦截"的机器化版本, 方向正好反了。
    唯一合法的财报侧收紧是 sizing 层的 T+0 缩仓 (`event_state`, 改的是"那天能多大"
    不是"能不能进")。

    ⏱ **反应日当天就报, 但重画要等那天收盘**: 报的是"到点了", 文案自带这句 ——
    盘前照 print 画 = 未完成的 bar 当收盘 (heartbeat.md §盘中运行) + repaint_trigger
    裁决 A (提前画白画率 40%)。

    源案例  (用户 抓): LITE 卡 `update_trigger` 逐字写着「 AMC FQ4
    print 落地当日全重画 (v3 SLA)」, 日历也知道 `pub_day  / AMC /
    反应日 08-12`, 但两者之间**没有任何一条代码路径**—— 日历只喂 `event_state`
    缩仓, 重画 SLA 只活在 prose 的 `update_trigger` 里, 没有读者。同轮 NBIS
    (8/12 BMO) 卡锚同样停在 8/06。**强制点只在文档不在代码 = 这条规则不存在。**
    """
    from .holdings import is_managed, is_scannable_card     # noqa: PLC0415  同层
    from .structure import anchor_date                      # noqa: PLC0415  同层
    cal = calendar if calendar is not None else load_calendar()
    rows = (cal.get("tickers") or {}) if cal else {}
    out: List[str] = []
    for doc in docs:
        for t, body in sorted(((doc or {}).get("tickers") or {}).items()):
            if not is_scannable_card(body) or not is_managed(body):
                continue
            if ((body.get("instrument") or {}).get("type")) == "option":
                continue        # 期权卡无自有 zones, 它的图锚在 underlying 卡上
            agent = body.get("agent") or {}
            if not (agent.get("zones") or {}):
                continue        # 缺 zones 归 underlying_card_missing, 别双报
            pr = landed_print(rows.get(t), today)
            if not pr:
                continue
            anc = anchor_date(agent)
            if anc and str(anc)[:10] >= str(pr["reaction_day"])[:10]:
                continue        # 锚在反应日或之后 = 已重画
            lbl = PUB_TYPE_LABEL.get(pr.get("pub_type"), "?")
            im = (f", 隐含 ±{pr['implied_move_pct']:g}%"
                  if pr.get("implied_move_pct") else "")
            out.append(f"{t} print {pr.get('pub_day') or '?'} {lbl} → 反应日 "
                       f"{pr['reaction_day']}{im}, 卡锚停在 {anc or '无'}")
    return out
