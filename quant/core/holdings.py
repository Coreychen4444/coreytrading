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


#: 参考票角色 —— 只为量能/板块基准躺在自选里, **不交易**。
#: 与 UNMANAGED_ROLES 同理, 这个字符串在全仓也只允许出现在这里(测试棘轮强制)。
#: ⚠️ 推荐配置是**入场候选分组里不放参考票**(指数/ETF 观察指标归券商的关注列表),
#: 基准 K 线改走 kline_cache.BENCHMARKS 硬名单 —— 数据层与 watchlist membership
#: 解耦。判别式保留为防御: 万一参考票又被加进候选分组, 各消费端的跳过语义依然正确。
REFERENCE_ROLES = frozenset({"volume_reference"})


#: 基金票 (ETF / ETN) —— **交易, 但没有公司财报语义**。价格结构与正股无异, zones / gate /
#: entry_guard / 退出栈全套照走; 只是没有财报日 / 三张表 / EPS。与 `is_traded` 正交:
#: 参考票 = 不做; 基金票 = 做, 只在「按公司取数」的三个消费端跳过 (财报日历 / 公司画像 /
#: 估值范围)。不跳 = 每轮各留一行没人能修的取数错 (基金票上财报接口直接拒
#: "This API only supports equities" / 报表接口空表 / 估值日更「不出数」)。
#: 判据 = `instrument.security_type`, 由 `watchlist_sync` 按 moomoo 分组行的 stock_type 盖
#: (机器事实, 不靠人记)。`instrument.type` 仍是 "stock": 受控重画 / 冻结证据 / 写卡脚本都按
#: type=="stock" 认正股类卡, 改 type = 焊死写路径。
FUND_SECURITY_TYPES = frozenset({"ETF"})


def has_fundamentals(body: dict) -> bool:
    """这张卡背后是不是一家**有财报的公司** (判据 instrument.security_type)。"""
    st = ((body or {}).get("instrument") or {}).get("security_type")
    return str(st or "").upper() not in FUND_SECURITY_TYPES


def stamp_security_type(body: dict, stock_type) -> bool:
    """按券商报的证券类型给卡盖基金章; 非基金类型不盖 (存量正股卡零 diff)。→ 是否改了卡。"""
    st = str(stock_type or "").upper()
    if st not in FUND_SECURITY_TYPES:
        return False
    inst = body.setdefault("instrument", {})
    if inst.get("security_type") == st:
        return False
    inst["security_type"] = st
    return True


def fund_tickers(positions_doc: dict, watchlist_doc: dict) -> set[str]:
    """两桶里盖了基金章的裸 ticker。期权卡自己不带章 (它只有 underlying 的名字) ——
    按标的名取数的消费端查这张表, 不查期权卡 body。"""
    return {t for doc in (positions_doc, watchlist_doc)
            for t, body in ((doc or {}).get("tickers") or {}).items()
            if isinstance(body, dict) and not has_fundamentals(body)}


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


def is_candidate(body: dict) -> bool:
    """watchlist 卡是不是**当前入场候选** —— 判据 status=watching **且** moomoo_listed≠false。

    membership 的真源在券商侧的候选分组。`watchlist_sync` 见到分组里没有了 **不删卡**
    (thesis/zones 是资产), 只把 `moomoo_listed` 翻成 false —— 所以移出分组时卡片的
    `status` **不变**, 仍然是 "watching"。只读 status 的消费者会把已移出的墓碑卡当活
    候选: 一张早就退出候选分组的卡, 可以连着几周继续被覆盖范围、报表与提醒列出来,
    而 `moomoo_listed` 那一半从来没有人查过。

    这条判据在消费端被反复手抄 (`grep moomoo_listed quant/`), 抄漏一处就是上面那个
    形状 —— 同一个概念存在多份判别式是本仓的老病。这里是它的名字: 新消费者读它,
    不要再抄一份。
    """
    body = body or {}
    return (str(body.get("status") or "") == "watching"
            and body.get("moomoo_listed") is not False)


def is_scannable_card(body: dict) -> bool:
    """全书扫描 (INFRA 卡片体检: 缺转移轴 / 锚不同步 / zones 缺锚 / 财报重画 SLA) 的**卡片级**选取。

    候选桶 (卡带 watchlist 语义: status=watching 或有 moomoo_listed 键) → `is_candidate`:
    移出自选分组的墓碑卡不进。源案例 : 周复盘 §3 的 INFRA:card_missing_next_gate
    点名的全是已移出分组的墓碑卡, status 仍 watching, 四个扫描函数都只看
    `status != "closed"`, 墓碑卡天天被体检、天天被点名。
    持仓桶 → status≠closed (与既有一致: 这些扫描不读 broker, qty 判据归 `live_tickers`)。
    """
    body = body or {}
    st = str(body.get("status") or "")
    if st == "watching" or "moomoo_listed" in body:
        return is_candidate(body)
    return st != "closed"


def watchlist_candidates(doc: dict) -> set[str]:
    """watchlist.json 里现在真的是候选的 ticker 集合 (`is_candidate` 的 doc 级)。"""
    return {t for t, body in ((doc or {}).get("tickers") or {}).items() if is_candidate(body)}


def stamping_cards(positions_doc: dict, watchlist_doc: dict) -> dict:
    """Merge cards used to stamp fill authorization and later annotations.

    A live positions card is authoritative after promotion. A flat archived card
    yields to a zoned watchlist candidate so a new campaign is stamped from its
    active authorization rather than the previous campaign. A bare candidate stub
    cannot displace an archived card. This pure function is shared by both writers.
    """
    def _dead_for_auth(body: dict) -> bool:
        return bool(body.get("exit_info"))
        # ``exit_info`` is lifecycle-owned and survives a broker quantity refresh.
        # Quantity cannot distinguish an archived card refreshed after re-entry
        # from a promoted live card. A synthetic underlying row has no exit_info
        # and therefore remains owned by positions.

    out = dict((watchlist_doc or {}).get("tickers") or {})
    for t, body in ((positions_doc or {}).get("tickers") or {}).items():
        wl = out.get(t) or {}
        if _dead_for_auth(body) and ((wl.get("agent") or {}).get("zones")):
            continue                        # 死卡让位给活候选卡
        out[t] = body
    return out


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


def cost_basis(body: dict) -> float | None:
    """「成本不向下摊」的成本 = 这张卡**受管层**持仓的加权均价 → float | None。

    lots.position (lot_reconcile 按实际成交落的) 优先 —— broker.avg_cost 是 moomoo 把 base 与
    position 合并的均价, 不作 PnL 参考 (position-tiers); lots 缺 (未落账)
    → 回落 broker.avg_cost (期权卡按合约, 即权利金成本)。broker.qty 为 0 / synthetic 行 → None:
    空仓没有成本可摊, 建仓不受此约束。"""
    broker = body.get("broker") or {}
    if broker.get("synthetic") or not float(broker.get("qty") or 0):
        return None
    lots = [lot for lot in ((body.get("lots") or {}).get("position") or [])
            if float(lot.get("qty") or 0) > 0 and float(lot.get("avg_cost") or 0) > 0]
    q = sum(float(lot["qty"]) for lot in lots)
    if q > 0:
        return sum(float(lot["qty"]) * float(lot["avg_cost"]) for lot in lots) / q
    ac = float(broker.get("avg_cost") or 0)
    return ac if ac > 0 else None


def averaging_down(prior_events, ticker: str, order_created: str,
                   fill_price: float) -> dict | None:
    """成交侧判据: 这笔 BUY 是不是把成本往下摊了 → {avg_cost, qty, campaign_start} | None (不算)。

    成本不向下摊: 加仓只许确认后往上加 (Iron Rule #2 v2 的 averaging down 分支退役)。`prior_events` = 本票在这笔成交之前的全部事件 (trades.jsonl 行); 时间一律比 ET 墙钟
    前 19 位 (事件 ISO 与 moomoo 下单时刻同为 ET, 夏冬令 offset 不参与比较)。

      campaign  最近一次持仓归零之后的成交 (平均成本法: 卖出按均价减量, 归零重开; expire / assign 归零)
      豁免      建仓阶梯 —— 下单时本票没有持仓, 或下单日 (ET) = 本 campaign 首笔成交日 (同一轮计划
                补挂的档)。三档阶梯的第 2、3 档本来就挂在第 1 档下面 (ladder_entry 阶梯)
      摊低      之后新下的买单, 成交价 < 当时均价

    期权按合约 (ticker = OCC code, 价 = 权利金), 与正股同一套。"""
    t, created = ticker.upper(), str(order_created or "")[:19]
    qty = cost = 0.0
    start = None
    at_create = 0.0                              # 下单那一刻的持仓量 (之前的事件处理完的 qty)
    for e in sorted((e for e in prior_events if str(e.get("ticker") or "").upper() == t),
                    key=lambda e: str(e.get("timestamp") or "")[:19]):
        ts = str(e.get("timestamp") or "")[:19]
        kind = e.get("event_type")
        side = str(e.get("side") or "").upper()
        q, p = float(e.get("qty") or 0), float(e.get("price") or 0)
        if kind in ("expire", "assign"):
            qty = cost = 0.0
        elif side == "BUY" and q > 0:
            if qty <= 1e-9:
                qty, cost, start = 0.0, 0.0, ts
            qty, cost = qty + q, cost + q * p
        elif side in ("SELL", "SELL_SHORT") and qty > 0:
            avg = cost / qty
            qty = max(0.0, qty - q)
            cost = avg * qty
        if created and ts < created:
            at_create = qty
    if qty <= 1e-9 or not start:
        return None
    if created and not at_create:
        return None                              # 下单时还是空仓 = 建仓阶梯
    if created and created[:10] == start[:10]:
        return None                              # 与首笔成交同一天下的单 = 同一轮建仓计划
    avg = cost / qty
    if float(fill_price) >= avg:
        return None
    return {"avg_cost": round(avg, 4), "qty": qty, "campaign_start": start[:10]}


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
            elif not is_candidate(body):
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


def coverage_universe(positions_doc: dict, watchlist_doc: dict) -> list[str]:
    """任何「全量扫一遍」型任务输出的**默认范围** = 持仓 (期权映射回正股) ∪ 候选 —— `intel_universe`
    的扁平版, 排序去重, 零授权效力 (只有名字)。

    源案例: 周复盘 §7 估值一览曾按 inputs.json **全表**跑, 把已移出自选分组的墓碑卡 (moomoo_listed=false)
    与纪念仓「不进任何 task 输出」一起列了; 同一份 prompt 的 §3 又按 watchlist.json 手扫 valid_until
    列了墓碑卡。与 `intel_universe` docstring 记的是同一个洞, 只是换了个消费端 —— 范围不该由各消费端
    各自解读 state 文件, 应该抄这一条的输出。"""
    u = intel_universe(positions_doc, watchlist_doc)
    return sorted(set(u["active"]) | set(u["watchlist"]))


def intel_universe(positions_doc: dict, watchlist_doc: dict) -> dict:
    """外部情报 / 研究工作区的**动态投资范围** —— 单一真源。

    → {"active": [正股…], "watchlist": [ticker…],
       "excluded": {"positions": {ticker: 原因}, "watchlist": {ticker: 原因}}}  (全部排序)

      active     `held_bare` (broker.qty≠0), 期权映射回 underlying 正股 (卡片
                 `agent.linked_underlying` 优先, 否则 OCC 解析); 结构豁免层
                 `is_managed` ("不进任何 task 输出") 与参考票 `is_traded` 不进
      watchlist  `is_candidate` 且 `is_traded`; 已在 active 的不重复列 (在持期权的
                 underlying 仍留在候选分组里 —— 那是 `held_bare` 语义, 这里只是去重)
      excluded   只列**会误导的**行: positions 里 status=active 但 qty=0 (期权已平 /
                 清仓残卡) + 角色排除; watchlist 里 moomoo_listed=false 的墓碑 +
                 status≠watching + 去重。平仓归档卡 (status=closed) 不列, 那是常态

    为什么要有它: 外部研究 agent 此前按自己的提示词解读这两个 state 文件 ——
    "positions 只取 active 持仓" "watchlist 取 watching 标的" —— 两条读的都是
    `status`, 而 `status` 在本仓是手工字段 (本模块顶部: 两个方向都出过事)。于是
    结构豁免仓、期权已平的残卡与已移出候选分组的墓碑一起被当成覆盖范围, 而
    `grep moomoo_listed quant/` 永远看不见提示词里的那一份判别式。修法 = 判别式
    进代码, 消费者只抄输出。出口 = `quant/ops/intelligence_universe.py --json`。

    纯函数, 不读文件 (core 不读 state)。零授权效力: 输出只有名字, 没有价格、仓位或 cap。
    """
    pos = (positions_doc or {}).get("tickers") or {}
    wl = (watchlist_doc or {}).get("tickers") or {}
    active: set[str] = set()
    ex_pos: dict[str, str] = {}
    ex_wl: dict[str, str] = {}

    for t in held_bare(positions_doc or {}):
        body = pos.get(t) or {}
        role = (body.get("instrument") or {}).get("role")
        if not is_managed(body):
            ex_pos[t] = f"结构豁免层 instrument.role={role} — 不进任何 task 输出"
            continue
        if not is_traded(body):
            ex_pos[t] = f"参考票 instrument.role={role} — 不交易"
            continue
        occ = parse_occ(t)
        und = ((body.get("agent") or {}).get("linked_underlying")
               or (occ["underlying"] if occ else t))
        active.add(und)

    for t, body in pos.items():
        if t in active or t in ex_pos or not t.isalpha():
            continue
        if str(body.get("status") or "") == "active" and not _qty(body):
            ex_pos[t] = "status=active 但 broker.qty=0 (期权已平/清仓残卡) — status 不是持仓判据"

    candidates: set[str] = set()
    for t, body in wl.items():
        if not t.isalpha():
            continue
        if t in active:
            ex_wl[t] = "已在 active (在持期权的 underlying) — 不重复列"
            continue
        if not is_candidate(body):
            ex_wl[t] = ("moomoo_listed=false — 已移出券商侧候选分组, 卡只是墓碑"
                        if body.get("moomoo_listed") is False
                        else f"status={body.get('status')!r} ≠ watching")
            continue
        if not is_traded(body):
            ex_wl[t] = f"参考票 instrument.role={(body.get('instrument') or {}).get('role')} — 不交易"
            continue
        candidates.add(t)

    return {
        "active": sorted(active),
        "watchlist": sorted(candidates),
        "excluded": {"positions": dict(sorted(ex_pos.items())),
                     "watchlist": dict(sorted(ex_wl.items()))},
    }
