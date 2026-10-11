"""open_orders.py — Moomoo 在途挂单的提醒文本 (纯函数, 无副作用)。

为什么存在: 前一天挂下、随后被忘掉的限价单会在开盘时自动成交, 并不是本意。heartbeat 每轮
都跑 order_sync, 能看到这类单 —— 但合约没有持仓卡时 order_sync 把它归 orphan 只印在 JSON 里,
没人读; 有卡的单写进 pending_orders 也同样没人推。**看见了不说 = 没看见**。所以: 有在途挂单
就逐张推送, 每轮都推 (提醒忘单就是目的, 不设冷却), 无挂单不推。

标记:
    隔夜  created 的 ET 日 < 今天 ET 日 —— 不是今天挂的, 最容易被忘
    无卡  标的 (期权按正股) 在 positions / watchlist 都没有卡 —— 系统没有计划覆盖它
"""
from __future__ import annotations

from .occ import parse_occ


def card_status(ticker: str, positions: set[str], watchlist: set[str]) -> str:
    """'position' / 'watchlist' / 'none'。合约本身有卡或正股有卡都算有卡。"""
    names = [ticker]
    parts = parse_occ(ticker)
    if parts:
        names.append(parts["underlying"])
    if any(n in positions for n in names):
        return "position"
    if any(n in watchlist for n in names):
        return "watchlist"
    return "none"


def render(orders: list[dict], today_et: str) -> str | None:
    """在途挂单 → TG 文本; 没有挂单返回 None (不推)。

    orders 每项: ticker / side / qty / dealt_qty / limit_price / status / created (ET ISO)
    / tif / card (card_status 的结果, 可缺)。
    """
    if not orders:
        return None
    lines = [f"**📋 Moomoo 在途挂单 {len(orders)} 张** — 到价即自动成交, 不是本意就撤"]
    for o in sorted(orders, key=lambda x: (str(x.get("created") or ""), str(x.get("ticker")))):
        created = str(o.get("created") or "")
        day = created[:10]
        qty = _num(o.get("qty"))
        dealt = float(o.get("dealt_qty") or 0)
        fill = f" (已成 {_num(dealt)})" if dealt else ""
        flags = []
        if day and day < today_et:
            flags.append("⏳隔夜")
        if o.get("card") == "none":
            flags.append("⚠️无卡")
        when = f"{day[5:]} {created[11:16]} ET" if len(created) >= 16 else "时间未知"
        lines.append(f"- {o.get('ticker')} {o.get('side')} {qty}{fill} @ {_px(o.get('limit_price'))}"
                     f" · {o.get('tif') or '?'} · 挂于 {when}"
                     + (" · " + " ".join(flags) if flags else ""))
    if any(o.get("card") == "none" for o in orders):
        lines.append("无卡 = 不在任何计划里; 清单外 BUY 成交 = 规则外")
    return "\n".join(lines)


def _num(x) -> str:
    v = float(x or 0)
    return str(int(v)) if v == int(v) else f"{v:g}"


def _px(x) -> str:
    return f"{float(x or 0):.2f}"
