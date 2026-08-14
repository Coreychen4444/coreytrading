"""谁算持仓 —— 单一真源。

 之前这个判据被抄在 portfolio_brief 的两个函数里, 而且**抄错了方向**:
`status == "closed" and qty == 0` 才算不持仓。问题是 `status` 是纯手工字段 ——
全仓没有一行代码把它写成 "closed", 只有 setdefault。它记录的是"平仓后有没有人
来改过这个文件", 不是"这个仓还在不在"。两个方向都会漏:

  qty > 0 + status="closed"   INTC  实际持有 2 股, 却被这条静音掉整天
                              的 -7.89% / zone 信号 / stop 检查
  qty == 0 + status="active"  NBIS  16:48 ET 清仓, broker.qty 当晚就是
                              0.0, 但没人手动改 status → 一只 0 股的票带着完整
                              zone_config 当上 7/29 简报的头号异动 (-9.82%),
                              还进了 budgets

判据只有一条: **qty**。它来自 broker, 不来自人的记性。
"""
from __future__ import annotations

from .occ import parse_occ


def _qty(body: dict) -> float:
    return float(((body.get("broker") or {}).get("qty")) or 0)


def held_bare(doc: dict) -> set[str]:
    """**裸 ticker**判据: 只看这一行自己的 `broker.qty`, 期权 underlying 不算。

    持有 GOOGL LEAPS **不会**让 GOOGL 进这个集合 —— 这是有意的, 归 watchlist_sync
    的自选分组语义所有 (用户 定: 分组 = **入场候选**, 而"持有一张
    GOOGL call"并不意味着"不再想买 GOOGL 正股")。
    """
    return {t for t, body in (doc.get("tickers") or {}).items() if _qty(body)}


def held_incl_option_underlyings(doc: dict) -> set[str]:
    """`held_bare` ∪ 在持期权的 underlying **名字**—— 即使 doc 里没有那一行。

    归 watchlist_migrate 的促升语义所有: 手上有 GOOGL 期权时, watchlist 里那张
    GOOGL 卡应该搬进 positions, 好让期权的 underlying 有图可对。
    """
    bare = held_bare(doc)
    return bare | {occ["underlying"] for t in bare if (occ := parse_occ(t))}


def live_tickers(doc: dict) -> set[str]:
    """positions.json 里现在真的持有的 ticker 集合。

    两条进入路径:
      1. `broker.qty != 0` —— 真持有。
      2. synthetic 行, 且**该标的还有在持期权**。stock_fetch 会给每个在持期权的
         标的补一条 qty=0 的行情行, 好让期权的 zone/stop 有价可对。那行会活得比
         期权久 (NOK/AAOI/MRVL/DRAM/RKLB/AXTI 六条至今还挂着 synthetic=true,
         而对应期权早就平了), 所以 `synthetic` 本身不能当"还持有"。

    ── 三个"持仓"判别式, 差异是有意的, 不许顺手对齐 ────────────────────
    恒等式 (测试钉住): `held_bare ⊆ live_tickers ⊆ held_incl_option_underlyings`

      held_bare                      裸 qty。GOOGL LEAPS 不让 GOOGL 进来
      live_tickers                   + doc 里**存在**的 synthetic 行 (要有那一行)
      held_incl_option_underlyings   + underlying **名字**(没有那一行也算)

     GOOGL 卡被清空 6 天, 病根就是这三者中的两个各自被手抄在
    `watchlist_sync`(裸) 与 `watchlist_migrate`(含 underlying) 里, 没有一处
    写明它们不同:
      sync   判 GOOGL 非持仓 → 不移出自选分组 + 建空 stub 卡
      migrate 判 GOOGL 是持仓 → 促升, 拿那张 stub **无条件覆盖**真卡
    两侧单独读都自洽、都有注释解释自己为什么这么写 —— **分歧本身就是那条路径**。
    修法不是让两边相等 (语义差异各有理由), 是把差异搬到这里命名 + 钉住。
    """
    tickers = doc.get("tickers") or {}
    live = held_bare(doc)
    held_unds = {occ["underlying"] for t in live if (occ := parse_occ(t))}
    return live | {
        t for t, body in tickers.items()
        if (body.get("broker") or {}).get("synthetic") and t in held_unds
    }


#: 结构豁免角色 —— **这个字符串在全仓只允许出现在这里**(测试棘轮强制)。
#:  之前它被抄在 5 个模块 8 处, 于是第 9 处 (portfolio_brief 的
#: load_rows_from_state) 漏了, 一个"完全不用关心"的仓位照样天天进简报卡片与行列表。
UNMANAGED_ROLES = frozenset({"commemorative_hold"})


REFERENCE_ROLES = frozenset({"volume_reference"})


def is_reference_role(role) -> bool:
    """Return whether a flattened instrument role is reference-only."""
    return role in REFERENCE_ROLES


def is_traded(body: dict) -> bool:
    """Return whether a card represents a tradable instrument.

    Reference instruments may provide quotes, volume or sector context, but they
    intentionally have no thesis, zones or entry plan. This predicate is separate
    from ``is_managed``: an unmanaged position is real exposure outside the
    discipline engine, while a reference instrument is not traded at all.
    """
    role = ((body or {}).get("instrument") or {}).get("role")
    return not is_reference_role(role)


def is_managed(body: dict) -> bool:
    """这个仓位参不参与交易管理与播报。

    与 `live_tickers` 正交, 别混:
      live_tickers  = **是不是持仓**(判据 broker.qty) —— 算 NAV、算敞口用
      is_managed    = **要不要管/要不要播报**(判据 instrument.role)

    role 落在 UNMANAGED_ROLES 里 = 结构上就不参与交易纪律: 不设 zone/stop、
    不进退出栈与 regime 转移、不进任何 task 输出 (用户 : 完全不用
    关心的仓位不该占用注意力)。钱还在账上, 所以 NAV 与账户级数字照算不误。
    """
    return ((body or {}).get("instrument") or {}).get("role") not in UNMANAGED_ROLES


def managed_tickers(doc: dict) -> set[str]:
    """既在持、又受管理的 ticker —— 播报层该看的那一集。"""
    tickers = doc.get("tickers") or {}
    return {t for t in live_tickers(doc) if is_managed(tickers.get(t) or {})}


def derive_tier(ticker: str, cards: dict) -> tuple[str, str]:
    """成交事件的 tier —— 机械推导, → (tier, 依据)。

    用户  定: tier 不问人。base 的语义是"exit_guard 不管它", 只能由
    **结构**授予 (position-tiers.md), 所以它等价于一个卡片字段, 没有人工判断空间。

    住在这里是因为判据就是 UNMANAGED_ROLES —— 那个字符串全仓只允许出现在本文件。
    纯函数, cards 由调用方读 (core 不读文件): 同步层 (落库时盖章) 与注解层
    (补盖/覆写) 共用同一份判别式, 否则同一笔成交在两条路径上会被分到不同层。
    """
    card = (cards or {}).get(ticker.upper()) or {}
    role = (card.get("instrument") or {}).get("role")
    if role in UNMANAGED_ROLES:
        return "base", f"instrument.role={role} → 结构豁免层"
    return "position", ("默认层 — base 只给结构豁免 (holdings.UNMANAGED_ROLES); "
                        "旧 core/trading 层已并入 position")


def lot_qty(body: dict) -> float:
    """卡片 lots 里记着的总股数 (base + position)。"""
    lots = body.get("lots") or {}
    return sum(float(lot.get("qty") or 0)
               for tier in ("base", "position")
               for lot in (lots.get(tier) or []))


def lot_gap(body: dict) -> float:
    """broker 说的股数 − lots 记的股数。0 = 台账与券商对得上。

    方向有语义, 两边不对称:
      >0  买入未落账 (lot_reconcile 待跑) —— 敞口真实存在但账本上看不见, 于是
          "本轮已投多少" 一律低估 → 授权侧是 **fail-open**, 必须拦
      <0  平仓/部分卖出未分配 —— 账本多记, 已投高估 → 授权侧偏保守, 不是风险

    synthetic 行 (期权 underlying 的合成报价行) 恒 0 —— 它没有自己的 lots。

     抽出来: 这个减法此前手写在 portfolio_brief.lot_drift 里, 而
    today_group 的授权守卫用的是**另一套**判据 (fill 日期 vs regime since),
    对平仓卡会得出"已成交待落账"这种事实错误的结论。同一个概念两份判别式是
    本仓惯犯 (GOOGL 卡被清空 6 天同病)。
    """
    broker = body.get("broker") or {}
    if broker.get("synthetic"):
        return 0.0
    return float(broker.get("qty") or 0) - lot_qty(body)


def flat_with_lots(doc: dict) -> list[str]:
    """已平仓 (不在 live) 但 lots 还挂着的 ticker —— 平仓清理没做完。

    `close_reconcile` 自动处理这些; 这里同时是 portfolio_brief 的告警来源, 所以
    自动化失灵时仍会响, 而不是安静地回到  那天的状态。
    """
    live = live_tickers(doc)
    return [t for t, body in (doc.get("tickers") or {}).items()
            if t not in live and lot_qty(body)]


def cardless_tickers(positions_doc: dict, watchlist_doc: dict) -> list[str]:
    """Return stock cards that should have zones but do not.

    Eligible candidates are listed watchlist entries and live positions. Option
    contracts are excluded because their structure is anchored to the underlying
    stock card. Reference instruments and unmanaged positions are also excluded.
    Existing zones terminate the condition naturally, so no separate completion
    flag is needed.
    """
    out: set[str] = set()
    for doc, live_only in ((watchlist_doc, False), (positions_doc, True)):
        tickers = (doc or {}).get("tickers") or {}
        live = live_tickers(doc or {}) if live_only else None
        for ticker, body in tickers.items():
            if not ticker.isalpha():
                continue
            if live is not None:
                if ticker not in live:
                    continue
            elif (str(body.get("status") or "") != "watching"
                  or body.get("moomoo_listed") is False):
                continue
            if not is_managed(body) or not is_traded(body):
                continue
            if not ((body.get("agent") or {}).get("zones")):
                out.add(ticker)
    return sorted(out)


def underlying_cards_missing_zones(doc: dict) -> list[str]:
    """在持期权的 underlying 正股卡缺 zones —— 期权的退出线锚在一张空卡上。

    期权卡的 `stop_level_basis: underlying` 意味着那条线读的是**正股**结构; 正股卡
    没有 zones, 这条线就没有任何东西承载, 而且 staleness / 重画 SLA 全部失效。

    这是  GOOGL 案 6 天零告警的直接出口。当时没人发现是因为 GOOGL 正股
    `broker.qty == 0` (它只是期权的 synthetic 行), 而 `load_rows_from_state` 只产出
    qty≠0 的行 → 那张卡进不了简报卡片、异动榜、zone 行、staleness 检测。
    **不是"检查不会触发", 是"被检查的对象进不了数据链"**—— 所以这条必须从
    positions doc 直接扫, 不能挂在 rows 上。

    → [ticker] (underlying 名, 排序)。结构豁免层的期权不进 (整个不在管辖内)。
    """
    tickers = doc.get("tickers") or {}
    out: set[str] = set()
    for t in held_bare(doc):
        occ = parse_occ(t)
        if not occ or not is_managed(tickers.get(t) or {}):
            continue
        agent = (tickers[t].get("agent") or {})
        und = agent.get("linked_underlying") or occ["underlying"]
        card = tickers.get(und)
        if not ((card or {}).get("agent") or {}).get("zones"):
            out.add(und)
    return sorted(out)
