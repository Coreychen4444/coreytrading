#!/usr/bin/env python3
"""today_group_sync.py — 入场授权白名单 → moomoo 自选分组 "Today"(对账式幂等)。

语义(v3, 用户 定): 盘后+夜盘随时可成交, Today 回答的问题是
**"这票现在允许挂 BUY 单吗"**—— 判据 = **gate 达标 且 给出 cap**, 即卡片
`agent.current_regime.cap_pct_nav` 为显式正数(gate 达标 → regime_transition 把
授权 cap 落卡, 这就是"达标+给 cap"的持久化形态)。挂**限价单**只需要授权状态,
不需要执行时点 —— v2 曾用 entry_guard 的 GO verdict 当 membership, 错在把
"现价在参与带内"(时点)当成了"允许挂单"(状态): 有授权的卡在等回踩时是 WAIT,
无 GO 行, 白名单整晚空着(当晚 MRVL/MSFT 案, 用户 抓的)。

membership 判据：**只读取当前 regime 是否给出 cap**。
Schema 要求每张有 zones 的正股卡显式携带 current_regime，
因此这句话在数据层字面成立，
(schema 同生同死强制), 本模块 = 纯字段读, 零推断:
    cap_pct_nav > 0  → 授权(转移机落的 gate 达标 cap, 或 card 源常规参与天花板)
    cap 0 / 无 regime → 稳态静默(修复观察·持仓无加仓授权·观察层·stub)
    期权 key          → 不进(白名单是正股层语义)
    **够格但被拦**(补画锁/zones 过期/台账未落账)必须报出来, 不静默丢
    卖出/减仓永远允许, 不需要白名单。
历史注(读侧推断纪元,  当日走完): v2 错锚 GO verdict(执行时点≠授权状态,
MRVL/MSFT 案) → v3 显式 cap → v3.2 补默认 probe 推断(GOOGL 案) → 统一后推断删除。

授权生命周期(定版, 用户—— **成交不再终止授权**): 额度按**爬过几个
gate**给, 「没有 r1 r2 r3 概念了 —— gate1 max 2.5% gate2 max 4% gate3 max 5%」;
同一个 gate 里可以分多笔加, 可用 = 该档上限 − 当前敞口(市值, grant_actual_cap
走 `entry_cap` 减 `exposure`), 打满显 $0 等下一个 gate 解锁下一档。
    挂单未成交 不影响(白名单管"能不能去挂", 不管已挂的单); SELL 永不消费
    平仓      = campaign 结束 → 下一笔回到**第一档**→ 照常进组
               (`gate_count` 的参与流锚点含 exit_info.closed_at, 与 entry_guard
               同一个判别式; 拦平仓票归结构 VETO/过期/补画锁)
    台账守卫   broker 比 lots 多 = 敞口存在但账上看不见 → 见下方 `_admit` 注
历史注(退役实现, 别照着旧讨论找代码): v3.1「成交即行使出组」按 events 里的 BUY
成交日 vs current_regime.since 判,  轮语义定版时语义先退役, 而那套
fills 管道作为台账守卫的判据又多活了一天 —— 它把"平仓归档后 lots 空"误读成
"成交还没落账", AAOI  因此被挡在组外, 而同一轮 entry_guard 给的是
PLAN(R1)。判据换成 lot_gap 后 fills/buy_fill_dates 整条管道一并删除。

同步时点: pipeline 三模式(heartbeat/briefing/postclose)对账 —— 卡片态是当前
真值, 任何轮次同步都正确; regime 转移(postclose/briefing 的 regime 步)与
session 内补画解锁最迟 ≤6h 被接上, postclose 当轮即接(排 regime 之后)。
v2 曾绑死 postclose+GO 行, 是 verdict 锚定的连带错误, 一并废除。

所有权边界(与 Watchlist 组**方向相反**):
    "Watchlist" 组 = 用户的 membership 真源, 系统只读;
    "Today"    组 = agent 的渲染输出, **整组归系统**—— 对账会移除任何不在期望集
    里的票, 包括手动加进去的。想自己收藏 → 放 Watchlist 或别的组。
    期望集为空 → 组被清空 = **当前没有 regime 授权在生效**, 是常态不是故障。

get_user_security 失败(含组不存在) → **整轮放弃**(盲写会破坏对账), 不做半份同步。

CLI:
    python3 quant/sync/today_group_sync.py [--dry-run] [--json] [--group Today]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 直接 `python3 quant/<层>/<模块>.py` 时让 `quant.*` 可 import

import argparse
import json
from typing import Dict, List, Optional, Tuple

from quant.core import book, earnings, env, technical  # noqa: F401  (.env → os.environ; book = 组合占用真源)
from quant.core.holdings import live_tickers, lot_gap  # noqa: E402
from quant.core.io_json import read_json  # noqa: E402
from quant.core.num import coerce_float  # noqa: E402
from quant.core.occ import parse_occ  # noqa: E402
from quant.core.paths import (# noqa: E402
    OPTION_PLANS_PATH, POSITIONS_PATH, WATCHLIST_PATH, add_openapi_path,
)
from quant.core.sizing import (# noqa: E402
    binding_label, binding_stop, entry_cap, gate_count, line_price,
)
from quant.core.structure import MACHINE_REGIME_SOURCES, structure_repair_veto  # noqa: E402
from quant.core.timeutil import et_today  # noqa: E402

DEFAULT_GROUP = "Today"


def _is_stock(ticker: str) -> bool:
    """正股 ticker 全字母; OCC 期权 key(AAOI270617C140000)含数字 → 排除。"""
    return bool(ticker) and ticker.isalpha()


def regime_grant(card: dict):
    """当前所处 regime 的 cap 资格 — 纯字段读(regime 统一后零推断)。
    → (cap, label, since) 或 None(无 regime/残缺 = 不授权)。cap≤0 = 该 regime 不授权。
     分类轴退役: cap 二值 {0, NOTIONAL_CAP}, label 统一。"""
    cr = (card.get("agent") or {}).get("current_regime") or {}
    cap = cr.get("cap_pct_nav")
    if not cr or not isinstance(cap, (int, float)):
        return None
    cap = float(cap)
    name = cr.get("name") or "?"
    label = f"{name} cap {cap * 100:g}%NAV"
    return (cap, label, str(cr.get("since") or "")[:10])


def structure_veto_of(ticker: str, card: dict, bars_of=None):
    """本票今天的结构修复 VETO → (vetoed, reason)。**与 entry_guard 同一判别式**
    (`core.structure.structure_repair_veto`;  前身 entry_class_of)。

    机器源 cap (gate 授予) 豁免 — 判别式内部按 source 判, 无需 K 线, 先走。
    card 源无 K 线 → **VETO**, 不是放行。判别式本身对空结构不 VETO (那是"没有
    降级理由"的语义, entry_guard 在它之前就以 NEEDS_CARD 挡掉了); 但这里没有
    那道闸, 照搬就成了"缓存缺失 = 全额放行"的 fail-open (D1 附属)。
    """
    agent = card.get("agent") or {}
    src = str(((agent.get("current_regime")) or {}).get("source") or "")
    if src in MACHINE_REGIME_SOURCES:
        return False, None
    rows = (bars_of or book.bars)(ticker)
    if not rows:
        return True, f"{ticker} 无 K 线缓存 — 结构判不了, 保守不 grant (非降级结论)"
    st = technical.swing_structure(rows)
    close = coerce_float(rows[-1].get("close"))
    return structure_repair_veto(agent, st, close)


def default_quote_of(ticker: str):
    """(ATR20, 收盘) —— 冲击预算那一项的 per-ticker 输入。可注入 (测试 / 别的数据源)。"""
    return book.atr20_of(ticker), book.price_of(ticker)


def _line_price(card: dict, fallback):
    """Thin card adapter for the shared sizing line-price convention."""
    z1 = ((card.get("agent") or {}).get("zones") or {}).get("accumulation_zone_1")
    return line_price(z1, fallback)


def grant_actual_cap(ticker: str, card: dict, nav, usage=None, quote_of=None,
                     calendar=None, today=None, bars_of=None, veto_of=None,
                     exposure_of=None):
    """regime 授权 → **实际 cap**, 再过一遍组合层的冲击预算与 gross 余额。

    用户 : 「默认值只是名义天花板, 实际情况需要靠计算得出 —— 依赖
    NAV 15% 的回撤幅度与 gross 110% 算出余额, 才是实际 cap。」

    所以进组票的展示数从  起是**算出来的**, 不是抄卡片上那个百分比:
    书里堆到 gross 上限时, 一张写着 40% 的卡实际能下的可能只有 34%。
     (用户 Q2): 走 `core.sizing.entry_cap` —— **与 entry_guard 同一
    函数、同一组臂**。 (分类轴退役): 判档换 `structure_repair_veto`
    (同一判别式的 VETO 形态) —— 结构未修复 → 返回 cap 0 的结果 + veto 标记,
    调用方播报; membership 不动 (授权状态 ≠ 执行时点, v3.1)。
    → `core.sizing.entry_cap` 的返回 dict; None = 无授权 / NAV 缺失。
    """
    g = regime_grant(card)
    if not g or g[0] <= 0 or not nav:
        return None
    today = today or et_today()
    vetoed, veto_why = (veto_of or structure_veto_of)(ticker, card, bars_of)
    atr, spot = (quote_of or default_quote_of)(ticker)
    price, price_basis = _line_price(card, spot)
    # gate 预算 (用户  二次定版): 爬过几个 gate 决定上限
    # (gate1 2.5% / gate2 4% / gate3 5% NAV risk), 判据 = 卡上的 regime_history
    # 而不是 lots; 同一个 gate 内可分多笔加, 余额 = 上限 − 当前敞口 (市值口径,
    # `entry_cap` 内减 `exposure` —— 与 regime cap 臂同一个数)。打满 = $0 等下一个 gate。
    gno = gate_count(card)
    # 财报反应日那天备注上的数也要跟着缩 —— 手机响铃看到的必须是**那一天**能下的钱
    ev = earnings.event_state(ticker, today, calendar)
    # stop 与 entry_guard 同口径 (用户  #3): 真 stop 取 max, 2N 兜底。
    # v1 只喂卡片 stop —— 棘轮地板/trail 住 exit_guards.json, 未接; 它们只会**更高**
    # (风险距离更小 = cap 更大), 所以少喂 = 保守, 可安全等接线。
    stop, stop_basis = binding_stop(
        price, [coerce_float(((card.get("agent") or {}).get("zones") or {}).get("stop_level"))],
        atr, 2.0)
    ac = entry_cap(nav, card.get("agent"), atr=atr, price=price, stop=stop,
                   usage=usage, event_shrink=ev["shrink"] if ev["active"] else None,
                   gate_no=gno, power_room=(book.power_room(today) or {}).get("room"),
                   exposure=(exposure_of or book.exposure_of)(ticker))
    if ac.get("cap_usd") is None:
        return None
    if vetoed:
        # 结构 VETO: 实际 cap 归 0 (今天不给钱), 但 membership 保留 —— 结构临时
        # 转坏由重画/转移机收尾, 不在 grant 层改组
        ac = {**ac, "cap_usd": 0.0, "final": 0.0, "cap_frac": 0.0,
              "binding": "structure_veto"}
    return {**ac, "price_basis": price_basis, "price": price,
            "structure_veto": vetoed, "structure_veto_why": veto_why if vetoed else None,
            "risk_stop": stop, "risk_stop_basis": stop_basis}


def _admit(tk: str, card: dict, today: str,
           members: Dict[str, str], notes: List[str]) -> None:
    """一句话判据 + 三个生命周期修饰: regime 给了 cap 且未被 锁/过期/台账对不上 → 进组。
    cap≤0/无 regime = 稳态静默; 够格但被拦 = 必须报出来。"""
    grant = regime_grant(card)
    if not grant or grant[0] <= 0:
        return
    _cap, label, _since = grant
    agent = card.get("agent") or {}
    if agent.get("pending_repaint"):
        notes.append(f"{tk}: {label} 但 pending_repaint 补画锁 — 暂不进组, 解锁后自动接上")
        return
    vu = (agent.get("zones") or {}).get("valid_until")
    if vu and str(vu) < today:
        notes.append(f"{tk}: {label} 但 zones 过期 ({vu}) — 暂不进组, 重画后自动接上")
        return
    # 台账漂移 **只播报不拦**(二次定版起)。
    #
    # 它当初拦人的理由是「lots 记的比券商少 ⇒ 本轮已投低估 ⇒ 会给全额 = fail-open」。
    # 那条理由随扣减口径一起消失了: 额度现在减的是 `book.exposure_of` =
    # **broker.qty × 现价**, 根本不读 lots。而 `lot_gap > 0` 的字面意思恰恰是
    # 「broker 已经反映了这笔成交、只是 lots 还没落账」—— 也就是敞口**已经**被
    # 正确扣掉了, 没有任何 fail-open 可言。继续拦, 就是拿参与权去赌
    # `lot_reconcile` 什么时候跑完, 并且在告警里说一句已经不成立的话
    # (「已投低估」)。台账本身的问题归 brief 的 INFRA:lot_drift 管, 那是对的地方。
    # ⚠️ 真正会让敞口低估的是 **broker 快照本身陈旧**(stock_fetch 没跑成),
    # 而 lot_gap 对那种情况一个字都说不出来 —— 所以这里不是"放松了保护",
    # 是"删掉了一个从来没保护到那件事的东西"。
    gap = lot_gap(card)
    if abs(gap) > 1e-6:
        notes.append(f"{tk}: {label} 进组, 但 lots 与 broker 差 {gap:+g} 股 "
                     "(未落账 / 部分卖出未分配) — 额度按 broker 敞口算不受影响; "
                     "台账见 INFRA:lot_drift")
    # 成交**不再**终止授权 (用户): 行使的只是本档预算的一部分,
    # 可用 = 本档上限 − 当前敞口 (grant_actual_cap 算), 打满显 $0 等下一个 gate
    # 解锁下一档。v3.1「成交即行使出组」退役 (它是分类轴时代「cap = 单轮 ladder
    # 白名单化」的产物)。
    #
    # **平仓回来的候选照常进组**(用户  裁): 平仓 = campaign 结束,
    # 它现在就是一张普通候选卡, 下一笔回到**第一档**—— 这不是我们在这里发明的,
    # 是 `gate_count` 把 `exit_info.closed_at` 当参与流锚点、entry_guard 读同一个
    # 判别式在说的同一件事。此前拦住它的是旧台账守卫 (fill 日期 ≥ regime since
    # 且 lots 空), 它把"平仓归档后 lots 空"误读成"成交还没落账", 于是 entry_guard
    # 给 PLAN 而白名单说没授权 —— 同一张卡两个数 (AAOI  实证)。
    members[tk] = label


def desired_members(positions_doc: dict, watchlist_doc: dict, today: str,
                    option_plans: Optional[dict] = None) -> Tuple[Dict[str, str], List[str]]:
    """授权白名单(纯函数, 可测): 当前所处 regime 给 cap 且未被锁/过期/行使的卡。
    → ({ticker: 授权描述}, 拦下说明)。

    option_plans = entry_guard 落的 state/option_plans.json 的 plans 段
    (用户): 授权正股成员有选约且合约未过期 → **合约 code 一并进组**
    (响铃那刻直接下单那张, 不用手查链)。正股出组 (行使/撤授权/重画 VETO) →
    选约文件下轮不再含它 → 合约同拍出组 (整组对账天然覆盖)。
    分组 API 对期权 code 的 ADD/MOVE_OUT 已实测干净 (2026-08-10, GOOGL 合约往返)。"""
    members: Dict[str, str] = {}
    notes: List[str] = []
    live = {t for t in live_tickers(positions_doc) if _is_stock(t)}

    for tk in sorted(live):
        _admit(tk, (positions_doc.get("tickers") or {}).get(tk) or {},
               today, members, notes)
    for tk, card in sorted((watchlist_doc.get("tickers") or {}).items()):
        if not _is_stock(tk) or tk in live:
            continue
        if str(card.get("status")) != "watching" or card.get("moomoo_listed") is False:
            continue
        _admit(tk, card, today, members, notes)
    for und, plan in sorted((option_plans or {}).items()):
        if und not in members:
            continue                      # 正股无授权 → 合约不进 (选约文件可能滞后一晚)
        code, expiry = plan.get("code"), str(plan.get("expiry") or "")
        if not code:
            continue
        if expiry and expiry <= today:
            notes.append(f"{und} 选约 {code} 已到期 — 不进组, 等重选")
            continue
        n0 = (plan.get("tiers") or [{}])[0].get("contracts")
        members[code] = f"期权 vehicle: {und} 首档 {n0 or '?'} 张 @ ≤{(plan.get('tiers') or [{}])[0].get('premium', '?')}"
    return members, notes


def plan_ops(desired_codes: List[str], current_codes: List[str]) -> Tuple[List[str], List[str]]:
    """对账(纯函数): → (adds, dels)。整组所有权 —— 不在期望集里的**任何**条目
    (含手动加的、非美股的)都删; Today 是授权白名单, 不是收藏夹。"""
    want, have = set(desired_codes), set(current_codes)
    return sorted(want - have), sorted(have - want)


def annotate_actual_caps(members: Dict[str, str], positions_doc: dict,
                         watchlist_doc: dict, nav, usage=None,
                         quote_of=None, calendar=None, today=None,
                         veto_of=None) -> List[str]:
    """给每个成员的授权 label 补上**实际 cap**(原地改 members), 返回额外说明行。

    membership 判据不变 (regime 给没给 cap) —— 组合层只改"能下多少", 不改
    "允不允许挂单"。但一个 40% 的授权在 gross 打满时实际只剩个位数, 那必须
    看得见: 组里躺着一张读起来很宽的卡, 而钱早就没了, 正是本仓反复出的那种
    "文档/标签说一套, 真值另一套"。
    """
    if not nav:
        return ["NAV 缺失 — 授权 label 只有名义比例, 无实际 cap"]
    live = {t for t in live_tickers(positions_doc) if _is_stock(t)}
    notes: List[str] = []
    for tk in list(members):
        src = positions_doc if tk in live else watchlist_doc
        card = (src.get("tickers") or {}).get(tk) or {}
        ac = grant_actual_cap(tk, card, nav, usage,
                              quote_of, calendar, today, veto_of=veto_of,
                              exposure_of=lambda t: book.exposure_of(t, positions_doc))
        if not ac:
            continue
        members[tk] += (f" → 实际 ${ac['cap_usd']:,.0f} ({ac['cap_frac'] * 100:.1f}% NAV)"
                        f" [gate{ac['gate_no']} · 按{ac['price_basis']} {ac['price']:g}]")
        if ac["binding"] != "regime":
            members[tk] += f" [{binding_label(ac)} binding]"
        if ac.get("structure_veto"):
            notes.append(f"{tk}: 授权在, 但结构未修复 → 实际 cap $0 "
                         f"({ac.get('structure_veto_why')}) — 重画或等结构收复")
        elif ac["cap_usd"] < 1:
            notes.append(f"{tk}: 授权在, 但组合层 {ac['binding']} 余额已用尽 — "
                         "实际 cap $0, 进组≠下得了单")
    return notes


def _sync(dry_run: bool, group: str) -> Dict:
    positions = read_json(POSITIONS_PATH)
    watchlist = read_json(WATCHLIST_PATH) if WATCHLIST_PATH.exists() else {"tickers": {}}
    opt_plans = ((read_json(OPTION_PLANS_PATH) or {}).get("plans")
                 if OPTION_PLANS_PATH.exists() else None)
    members, skip_notes = desired_members(positions, watchlist, et_today(),
                                          option_plans=opt_plans)
    nav = book.latest_nav()
    skip_notes.extend(annotate_actual_caps(members, positions, watchlist, nav,
                                           book.load_usage(positions, nav),
                                           calendar=earnings.load_calendar()))
    desired_codes = [f"US.{t}" for t in sorted(members)]

    add_openapi_path()
    from common import create_quote_context, df_to_records, safe_close, RET_OK  # noqa: E402
    from moomoo import ModifyUserSecurityOp  # noqa: E402

    ctx = None
    try:
        ctx = create_quote_context()
        ret, data = ctx.get_user_security(group)
        if ret != RET_OK:
            return {"ok": False,
                    "error": f"get_user_security({group!r}) 失败: {data} — 整轮放弃"
                             f"(组不存在? 需在 moomoo App 手动建自选分组 {group!r})",
                    "desired": sorted(members)}
        rows = df_to_records(data) if data is not None and not data.empty else []
        current_codes = [str(r.get("code") or "") for r in rows if r.get("code")]

        adds, dels = plan_ops(desired_codes, current_codes)
        errors: List[str] = []
        if not dry_run:
            # 出组必须 MOVE_OUT 不是 DEL — DEL 的 group 参数是摆设, 从自选整体删除,
            # 其他分组连带消失 (实测  祭品双组; MRVL 行使出组连带清空
            # 用户的 Optical 组即此 bug)。ADD 语义正常 (入指定组 + 自动进 All)。
            for op, codes in ((ModifyUserSecurityOp.MOVE_OUT, dels),
                              (ModifyUserSecurityOp.ADD, adds)):
                if not codes:
                    continue
                rt, rdata = ctx.modify_user_security(group, op, codes)
                if rt != RET_OK:
                    errors.append(f"{'MOVE_OUT' if op == ModifyUserSecurityOp.MOVE_OUT else 'ADD'} "
                                  f"{codes}: {rdata}")
        return {"ok": not errors, "dry_run": dry_run, "group": group,
                "desired": sorted(members), "grants": members,
                "adds": adds, "dels": dels,
                "kept": len(set(desired_codes) & set(current_codes)),
                "revoked": skip_notes, "errors": errors}
    finally:
        if ctx is not None:
            try:
                safe_close(ctx)
            except Exception:
                pass


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description="入场授权白名单 → moomoo 'Today' 分组 对账同步")
    ap.add_argument("--dry-run", action="store_true", help="只算 diff 不写")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--group", default=DEFAULT_GROUP)
    a = ap.parse_args(argv)
    r = _sync(a.dry_run, a.group)
    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        if not r.get("ok") and r.get("error"):
            print(f"⚠️ {r['error']}")
            return 1
        tag = "(dry-run) " if r.get("dry_run") else ""
        grants = r.get("grants") or {}
        body = "; ".join(f"{t} ({grants[t]})" for t in r["desired"]) or "空 = 当前无 regime 授权"
        print(f"{tag}[{r['group']}] 授权 {len(r['desired'])} 票 | "
              f"+{len(r['adds'])} / -{len(r['dels'])} / 不变 {r['kept']}")
        print(f"  {body}")
        for c in r["adds"]:
            print(f"  + {c}")
        for c in r["dels"]:
            print(f"  - {c}")
        for n in r.get("revoked", []):
            print(f"  ~ {n}")
        for e in r.get("errors", []):
            print(f"  ✗ {e}")
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
