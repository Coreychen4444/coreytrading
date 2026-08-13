#!/usr/bin/env python3
"""watchlist_sync.py — Moomoo 自选 "Watchlist" 分组 ↔ state/watchlist.json 对账

为什么存在 (): 用户的入场工作流是手动挂单, 入场候选的 **membership
真源在 moomoo App 的自选分组**(手机上加减最方便), 而 **分析真源**(thesis / zones /
action gates) 在 state/watchlist.json。两边会漂移: moomoo 新增的 ticker 没有卡 →
entry_guard 给不出执行计划; 系统有卡但 moomoo 已移除 → 候选可能已失去兴趣。
本脚本每日 (overnight-briefing 数据采集步) 对账:

- moomoo 有 + 系统无 → 写 stub 卡 (thesis.status=needs_card), 提示跑 /trading-analysis
- 系统有 + moomoo 无 → **不删卡**(thesis/zones 是资产), 标 moomoo_listed=false 并报 drift
- 指数/期权/非美股跳过
- **已持仓 (broker.qty != 0) → 自动移出 moomoo 分组**(用户 定):
  分组语义 = 入场候选, 成交即不再是候选 (卡片促升归 watchlist_migrate 管)。
  判据只有 qty≠0 (pipeline 里本步在 stock_fetch 之后 = broker 真值当轮新);
  每次移除显式报告。MOVE_OUT 失败不阻塞对账 (下轮幂等重试)。
- **平仓 (裸正股卡有 exit_info.closed_at 且已不在 held_bare) → 自动加回分组**
  (用户: "从持仓平仓的需要自动加回 watchlist")。**这条推翻了
  的后半句"平仓后想继续跟 = App 手动加回"**—— 默认从"平了就没了"改成"平了回
  候选池"。加回判据是移出判据的**逆**, 同一个 held_bare, 不另立一套"谁算平仓"。

  一次就够: 加回后在 positions 卡上盖 `exit_info.watchlist_readd`(记本次
  closed_at), 之后 用户 把它从组里删掉, 系统**不会**第二天又端回来 ——
  08-05 要防的僵尸候选仍然防着, 变的只是**默认**, 不是**谁说了算**。下一次
  平仓 = 新的 closed_at ≠ 盖章值 → 再加回一次。
  历史平仓卡 (close_reconcile  上线前的 NOK/LITE/DRAM/RKLB/AXTI +
  全部期权卡) 没有 closed_at, 由构造排除 —— 不需要一次性 backfill, 上线首日
  也不会把五张老卡端回自选。
- **回来的卡 = 平仓卡原样搬 (用户 : "卡的话原样保留即可 待过期重画")**:
  zones / gate / thesis / regime_history 逐字 deepcopy, 不刷 last_reviewed
  (刷了漂移时钟归零, 而"待过期"靠的正是那个时钟), 重画时点归既有 staleness
  机制。**这一半是纯对账不是一次性**: 空 stub + positions 有分析 = 随时补上,
  幂等; 已有 zones 的卡**永不覆盖**(促升守卫  的反方向同款)。
  ⚠️ 原样 = 连 `current_regime` 的 cap 一起带回来 —— 授权跟着图走, 图没过期
  授权就还在。要停授权就重画卡, 不在这里偷偷改。

系统对该分组的写操作只有这两个 (MOVE_OUT 持仓票 / ADD 平仓票), 互为镜像;
其余 membership 归 用户的手, 一个字段都不许碰。

依赖 OpenD (get_user_security)。OpenD 不可达 → 报错退出, 不改状态文件。

CLI:
    .venv/moomoo/bin/python3 quant/sync/watchlist_sync.py [--group Watchlist] [--json]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 直接 `python3 quant/<层>/<模块>.py` 时让 `quant.*` 可 import

from quant.core.paths import add_openapi_path, POSITIONS_PATH, ROOT, WATCHLIST_PATH  # noqa: E402

from quant.core import env, schema as _schema  # noqa: F401  (.env → os.environ)

import argparse
import copy
import json
from typing import Dict, List, Optional, Tuple

from quant.core.holdings import held_bare  # noqa: E402  「谁算持仓」单一真源 (裸 ticker 分支)
from quant.core.io_json import atomic_write_json, read_json, state_lock  # noqa: E402
from quant.core.occ import is_option_ticker, strip_us_prefix  # noqa: E402
from quant.core.timeutil import now_utc_iso  # noqa: E402

DEFAULT_GROUP = "Watchlist"
ALLOWED_TYPES = {"STOCK", "ETF"}
READD_MARKER = "watchlist_readd"          # exit_info 上的"本次平仓已回候选"盖章


def fetch_moomoo_group(group: str) -> List[dict]:
    """OpenD 自选分组内容。不可达/组不存在 → raise。"""
    add_openapi_path()  # openapi skill scripts (`common`) 上 sys.path
    from common import create_quote_context, df_to_records, safe_close, RET_OK
    ctx = None
    try:
        ctx = create_quote_context()
        ret, data = ctx.get_user_security(group)
        if ret != RET_OK:
            raise RuntimeError(f"get_user_security({group!r}) failed: {data}")
        return df_to_records(data) if data is not None and not data.empty else []
    finally:
        if ctx is not None:
            try:
                safe_close(ctx)
            except Exception:
                pass


def remove_from_group(group: str, codes: List[str]) -> None:
    """已持仓 ticker 移出**指定**自选分组。失败 raise, 由调用侧降级为报告 (不阻塞对账)。

    必须 MOVE_OUT 不是 DEL。实测 2026-08-06 (祭品 US.F 在 Optical+Energy 双组):
    DEL 的 group 参数是摆设 — 从自选整体删除, **所有分组连带消失**; MOVE_OUT 才是
    "只出这个组" (Optical 出、Energy/All 完好)。源案例: MRVL 8/5 成交后被本函数
    DEL, 用户 自建的 Optical 组连带被清空 — 系统对分组的授权只有自己管的那两个
    (Watchlist/Today), 其他组是 用户的地盘, 一个字段都不许碰。"""
    add_openapi_path()
    from common import create_quote_context, safe_close, RET_OK
    from moomoo import ModifyUserSecurityOp
    ctx = None
    try:
        ctx = create_quote_context()
        ret, data = ctx.modify_user_security(group, ModifyUserSecurityOp.MOVE_OUT, codes)
        if ret != RET_OK:
            raise RuntimeError(f"modify_user_security MOVE_OUT failed: {data}")
    finally:
        if ctx is not None:
            try:
                safe_close(ctx)
            except Exception:
                pass


def add_to_group(group: str, codes: List[str]) -> None:
    """平仓 ticker 加回**指定**自选分组 (remove_from_group 的镜像)。失败 raise,
    由调用侧降级为报告 (不阻塞对账, 下轮幂等重试)。

    ADD 语义正常 —— 入指定组 + 自动进 All。MOVE_OUT 那边的坑只在 DEL 上
    (group 参数是摆设, 从自选整体删除)。同一个 op 每天在 today_group_sync 往
    Today 组加票, 生产已验证。"""
    add_openapi_path()
    from common import create_quote_context, safe_close, RET_OK
    from moomoo import ModifyUserSecurityOp
    ctx = None
    try:
        ctx = create_quote_context()
        ret, data = ctx.modify_user_security(group, ModifyUserSecurityOp.ADD, codes)
        if ret != RET_OK:
            raise RuntimeError(f"modify_user_security ADD failed: {data}")
    finally:
        if ctx is not None:
            try:
                safe_close(ctx)
            except Exception:
                pass


def readd_plan(pos: dict, held: set, in_group: set) -> List[dict]:
    """平仓 → 回入场候选的待办 (纯函数, 可测)。→ [{ticker, closed_at, exit_price,
    realized_pnl_usd, in_group}]。

    判据**不看 status**—— 那是手工字段, 两个方向都出过事 (holdings.py 开头那两个
    源案例)。看的是 broker.qty (经 held_bare) + close_reconcile 落的 exit_info。
    """
    out: List[dict] = []
    for tk, body in sorted((pos.get("tickers") or {}).items()):
        if tk in held or is_option_ticker(tk):
            continue                       # 仍持有 / 期权卡 (OCC code 不是自选成员)
        if (body.get("instrument") or {}).get("type") != "stock":
            continue
        broker = body.get("broker") or {}
        if broker.get("source") != "moomoo_opend" or broker.get("synthetic"):
            continue                       # IBKR / synthetic underlying 不进 moomoo 分组
        exit_info = body.get("exit_info") or {}
        closed_at = exit_info.get("closed_at")
        if not closed_at:
            continue                       # 无平仓记录 (close_reconcile 上线前的老卡)
        if (exit_info.get(READD_MARKER) or {}).get("closed_at") == closed_at:
            continue                       # 本次平仓已加回过 —— 一次就够, 之后归 用户
        out.append({"ticker": tk, "closed_at": closed_at,
                    "exit_price": exit_info.get("exit_price"),
                    "realized_pnl_usd": exit_info.get("realized_pnl_usd"),
                    "in_group": tk in in_group})
    return out


def stamp_readd(items: List[dict], group: str, ts: str) -> List[str]:
    """把"本次平仓已回候选"盖进 positions 卡。→ 实际盖上的 ticker 列表。

    盖章是这条规则的**幂等锚**: 没有它, 用户 每删一次系统就加一次 = 删不掉的
    僵尸候选, 比 08-05 原来要防的还糟。持锁后重读 —— closed_at 变了 (期间又有
    新平仓) 就跳过, 下轮按新值重算。
    """
    if not items:
        return []
    stamped: List[str] = []
    with state_lock():
        doc = read_json(POSITIONS_PATH)
        for item in items:
            body = (doc.get("tickers") or {}).get(item["ticker"])
            if not body:
                continue
            exit_info = dict(body.get("exit_info") or {})
            if exit_info.get("closed_at") != item["closed_at"]:
                continue
            exit_info[READD_MARKER] = {"at": ts, "closed_at": item["closed_at"],
                                       "group": group,
                                       "was_listed": bool(item["in_group"])}
            body["exit_info"] = exit_info
            stamped.append(item["ticker"])
        errors, _ = _schema.validate_positions_doc(doc)
        if errors:                          # 校验不是可选项 —— 坏结构写不进去
            raise RuntimeError("positions schema validation failed: " + "; ".join(errors[:3]))
        atomic_write_json(POSITIONS_PATH, doc)
    return stamped


def classify_rows(rows: List[dict]) -> Tuple[List[str], Dict[str, List[str]]]:
    """→ (美股正股/ETF ticker 列表, skipped{reason: [codes]})。"""
    keep: List[str] = []
    skipped: Dict[str, List[str]] = {"index": [], "non_us": [], "option": [], "type": []}
    for r in rows:
        code = str(r.get("code") or "")
        stype = str(r.get("stock_type") or "")
        if not code.startswith("US."):
            skipped["non_us"].append(code)
            continue
        tk = strip_us_prefix(code)
        if tk.startswith("."):  # US..VIX 等指数
            skipped["index"].append(code)
            continue
        if is_option_ticker(tk):
            skipped["option"].append(code)
            continue
        if stype and stype.upper() not in ALLOWED_TYPES:
            skipped["type"].append(f"{code}({stype})")
            continue
        keep.append(tk)
    return keep, skipped


def _is_stub_card(card: dict) -> bool:
    """空卡 = 只有"待建卡"这一个信息, 覆盖它不损失任何分析。"""
    agent = card.get("agent") or {}
    return (agent.get("thesis") or {}).get("status") == "needs_card" and not agent.get("zones")


def closed_analysis(pos: dict, held: set) -> Dict[str, dict]:
    """{ticker: {"agent":…, "exit_info":…}} —— 已平仓但**分析还在**的正股卡。

    用户 : "卡的话原样保留即可, 待过期重画" —— 平仓不作废一张图。
    zones / gate / thesis / regime_history 原样搬回候选卡, 之后由既有 staleness
    机制 (valid_until / breakout-breakdown ×2 日 / 财报重画 SLA / structure_drift)
    决定何时重画, 与其他候选卡走同一套, 不为"刚平仓"另开一条规则。

    **判据与 readd_plan 分开是有意的**: 组里有没有它 = membership, 归 用户,
    所以加回只做一次(盖章); 卡里有没有分析 = 内容, 归系统, 所以纯对账 ——
    空 stub + positions 有分析 = 随时可以补上, 幂等且不丢东西。
    """
    out: Dict[str, dict] = {}
    for tk, body in (pos.get("tickers") or {}).items():
        if tk in held or is_option_ticker(tk):
            continue
        if (body.get("instrument") or {}).get("type") != "stock":
            continue
        exit_info = body.get("exit_info") or {}
        if not exit_info.get("closed_at"):
            continue
        agent = body.get("agent") or {}
        thesis = agent.get("thesis") or {}
        if not thesis.get("summary") or thesis.get("status") == "needs_card":
            continue                       # 那张卡自己就是空的, 没什么可搬
        out[tk] = {"agent": agent, "exit_info": exit_info}
    return out


def carried_card(tk: str, ts: str, src: dict) -> Optional[dict]:
    """平仓卡 → 候选卡: agent 块**逐字**搬 (deepcopy), 只换外层信封。

    不改 zones、不改 current_regime、不刷 last_reviewed —— 刷了漂移时钟就归零,
    而"待过期重画"靠的正是那个时钟。校验不过 → 返回 None, 调用侧回落 stub
    (一张搬不动的卡不该让整轮对账崩掉)。
    """
    exit_info = src.get("exit_info") or {}
    card = {
        "instrument": {"type": "stock"},
        "broker": None,                    # stock_fetch 下轮补报价快照
        "status": "watching",
        "priority": "unassigned",
        "moomoo_listed": True,
        "moomoo_synced_at": ts,
        # 信封层的来历 —— agent 块一个字段都没动, 这行只回答"这张卡怎么来的"
        "readd_from_position": {
            "at": ts,
            "closed_at": exit_info.get("closed_at"),
            "exit_price": exit_info.get("exit_price"),
            "realized_pnl_usd": exit_info.get("realized_pnl_usd"),
            "note": "平仓自动回候选, 卡原样保留待过期重画 (2026-08-13); "
                    "lots/exit_info 留在 positions.json 那张 closed 卡上",
        },
        "agent": copy.deepcopy(src["agent"]),
    }
    errors, _ = _schema.validate_watchlist_doc({"tickers": {tk: card}})
    return None if errors else card


def exit_note(item: dict) -> str:
    """平仓回归卡的一行出场摘要 (给建卡的人看, 不驱动任何判定)。"""
    bits = [f"平于 {str(item['closed_at'])[:10]}"]
    if isinstance(item.get("exit_price"), (int, float)):
        bits.append(f"@ {item['exit_price']:g}")
    if isinstance(item.get("realized_pnl_usd"), (int, float)):
        bits.append(f"实现 ${item['realized_pnl_usd']:+,.0f}")
    return " ".join(bits)


def make_stub(tk: str, ts: str, reentry: Optional[str] = None) -> dict:
    if reentry:
        summary = (f"[stub] {ts[:10]} 平仓后自动回入场候选 ({reentry}) — 原持仓卡分析"
                   f"留在 positions.json (status=closed, lots 归档在 exit_info.closed_lots)。"
                   f"平仓 = 结构已变, zones 一律重画不复用: 跑 /trading-analysis {tk}。"
                   f"建卡前 entry_guard 输出 NEEDS_CARD, 不给执行计划。")
        reason = "平仓后自动回候选池 (2026-08-13 规则), 无 zone 无 thesis"
    else:
        summary = (f"[stub] moomoo 自选同步新增 {ts[:10]} — thesis/zones 待 "
                   f"/trading-analysis {tk} 建卡。建卡前 entry_guard 输出 NEEDS_CARD, 不给执行计划。")
        reason = "moomoo watchlist 新增, 无 zone 无 thesis"
    return {
        "instrument": {"type": "stock"},
        "broker": None,
        "status": "watching",
        "priority": "unassigned",
        "moomoo_listed": True,
        "moomoo_synced_at": ts,
        "agent": {
            "thesis": {
                "summary": summary,
                "status": "needs_card",
                "conviction": None,
                "last_reviewed": ts,
            },
            "zones": None,
            "action": {"intent": "needs_card", "reason": reason, "confidence": None},
        },
    }


def sync(group: str = DEFAULT_GROUP) -> dict:
    ts = now_utc_iso()
    rows = fetch_moomoo_group(group)
    moomoo_tks, skipped = classify_rows(rows)

    wl = read_json(WATCHLIST_PATH) if WATCHLIST_PATH.exists() else \
        {"schema_version": 2, "generated_at": ts, "tickers": {}}
    try:
        pos = read_json(POSITIONS_PATH)
    except Exception:
        pos = {"tickers": {}}
    # 判据 = holdings.held_bare (**裸 ticker**, 期权 underlying 不算)。语义归分组:
    # 持有 GOOGL call ≠ 不再想买 GOOGL 正股。与 watchlist_migrate 的
    # held_incl_option_underlyings **有意不同**, 差异在 holdings.live_tickers
    # 的文档里命名并由 tests/core/test_holdings.py 钉住 ——  GOOGL 卡
    # 被清空 6 天, 就是因为这两个判别式各自手抄在两个文件里且没人写明它们不同。
    held = held_bare(pos)

    # 已持仓 → 移出分组 (入场候选语义)。本轮 stub/drift 逻辑仍按移除前的成员集走
    # (held 票照旧跳过 stub, 卡片促升归 watchlist_migrate); 下轮成员集自然干净。
    held_in_group = sorted(tk for tk in moomoo_tks if tk in held)
    removed_held: List[str] = []
    remove_errors: List[str] = []
    if held_in_group:
        try:
            remove_from_group(group, [f"US.{t}" for t in held_in_group])
            removed_held = held_in_group
        except Exception as e:                                    # noqa: BLE001
            remove_errors.append(f"{type(e).__name__}: {e}")

    # 平仓 → 加回分组 (用户)。移出的镜像: 同一个 held_bare, 反方向。
    # 已在组里的 (用户 手动加回过) 不调 API, **但照样盖章**—— 不盖的话他之后
    # 删掉它, 下一轮就会被系统端回来。
    readd_items = readd_plan(pos, held, set(moomoo_tks))
    readd_errors: List[str] = []
    to_add = [i for i in readd_items if not i["in_group"]]
    added_back: List[str] = []
    if to_add:
        try:
            add_to_group(group, [f"US.{i['ticker']}" for i in to_add])
            added_back = [i["ticker"] for i in to_add]
            moomoo_tks.extend(added_back)      # 当轮即建卡, 不等下一轮
        except Exception as e:                                    # noqa: BLE001
            readd_errors.append(f"{type(e).__name__}: {e}")
    # ADD 失败的不盖章 → 下轮重试 (盖了章就永远加不回来了)
    failed = {i["ticker"] for i in to_add} - set(added_back)
    stampable = [i for i in readd_items if i["ticker"] not in failed]
    reentry_note = {i["ticker"]: exit_note(i) for i in stampable}
    try:
        stamped = stamp_readd(stampable, group, ts)
    except Exception as e:                                        # noqa: BLE001
        readd_errors.append(f"盖章失败 (下轮重试): {type(e).__name__}: {e}")
        stamped = []

    # 平仓卡的分析 (zones/gate/thesis) 原样可搬 —— 与上面的 membership 盖章分开:
    # 组里有没有它归 用户 (一次性), 卡里有没有分析归系统 (纯对账, 幂等)。
    analysis = closed_analysis(pos, held)
    carried: List[str] = []
    carry_errors: List[str] = []

    added, holding_skips = [], []
    for tk in moomoo_tks:
        if tk in held:
            holding_skips.append(tk)
            continue
        card = wl["tickers"].get(tk)
        src = analysis.get(tk)
        # 无卡, 或只有一张空 stub → 用平仓卡的分析填上 (空卡覆盖不损失东西;
        # 有 zones 的卡永不覆盖 —— 促升守卫  的反方向同款)
        if src and (card is None or _is_stub_card(card)):
            new = carried_card(tk, ts, src)
            if new is not None:
                wl["tickers"][tk] = new
                carried.append(tk)
                continue
            carry_errors.append(f"{tk}: 平仓卡搬不过来 (watchlist 校验不过), 回落 stub")
        if card is None:
            wl["tickers"][tk] = make_stub(tk, ts, reentry=reentry_note.get(tk))
            added.append(tk)
        else:
            card["moomoo_listed"] = True
            card["moomoo_synced_at"] = ts
    moomoo_set = set(moomoo_tks)
    drift_system_only = []
    for tk, card in wl["tickers"].items():
        if tk not in moomoo_set and str(card.get("status")) == "watching":
            if card.get("moomoo_listed") is not False:
                card["moomoo_listed"] = False
                card["moomoo_synced_at"] = ts
            drift_system_only.append(tk)

    wl["generated_at"] = ts
    errors, warnings = _schema.validate_watchlist_doc(wl)
    if errors:
        raise RuntimeError("watchlist schema validation failed: " + "; ".join(errors))
    with state_lock():
        atomic_write_json(WATCHLIST_PATH, wl)

    return {
        "group": group,
        "moomoo": sorted(moomoo_tks),
        "added_stubs": added,
        "carried_from_closed": sorted(carried),
        "carry_errors": carry_errors,
        "drift_system_only": sorted(drift_system_only),
        "removed_held": removed_held,
        "remove_errors": remove_errors,
        "readded_closed": sorted(added_back),
        "readd_already_listed": sorted(i["ticker"] for i in readd_items if i["in_group"]),
        "readd_stamped": sorted(stamped),
        "readd_errors": readd_errors,
        "skipped_holdings": sorted(set(holding_skips) - set(removed_held)),
        "skipped_other": {k: v for k, v in skipped.items() if v},
        "schema_warnings": warnings,
    }


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description="moomoo 自选 ↔ watchlist.json 对账")
    ap.add_argument("--group", default=DEFAULT_GROUP)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        out = sync(a.group)
    except Exception as e:
        msg = {"error": f"{type(e).__name__}: {e}"}
        print(json.dumps(msg, ensure_ascii=False) if a.json else f"[watchlist_sync] {msg['error']}",
              file=sys.stderr)
        return 1
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(f"moomoo[{out['group']}]: {', '.join(out['moomoo']) or '(空)'}")
        if out["added_stubs"]:
            print(f"新增 stub 卡: {', '.join(out['added_stubs'])} → 跑 /trading-analysis 建卡")
        if out["carried_from_closed"]:
            print(f"平仓卡分析原样搬回候选: {', '.join(out['carried_from_closed'])}"
                  " (zones/gate/thesis 逐字保留, 待过期或 staleness 触发重画)")
        for e in out["carry_errors"]:
            print(f"⚠️ {e}")
        if out["drift_system_only"]:
            print(f"drift (系统有卡, moomoo 无): {', '.join(out['drift_system_only'])} — 卡保留, 确认是否还在跟")
        if out["removed_held"]:
            print(f"已持仓 → 移出自选分组: {', '.join(out['removed_held'])}"
                  " (入场候选语义; 平仓后自动加回一次)")
        for e in out["remove_errors"]:
            print(f"⚠️ 分组移除失败 (下轮重试): {e}")
        if out["readded_closed"]:
            print(f"平仓 → 自动加回自选分组: {', '.join(out['readded_closed'])}"
                  " (回候选池; 不想跟了从 App 删掉即可, 系统不会再加第二次)")
        if out["readd_already_listed"]:
            print(f"平仓票已在组里 (手动加过), 只盖章: {', '.join(out['readd_already_listed'])}")
        for e in out["readd_errors"]:
            print(f"⚠️ 平仓加回失败 (下轮重试): {e}")
        if out["skipped_holdings"]:
            print(f"跳过 (已持仓, 加仓归 brief 手册): {', '.join(out['skipped_holdings'])}")
        for k, v in out["skipped_other"].items():
            print(f"跳过 ({k}): {', '.join(v)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
