#!/usr/bin/env python3
"""annotate_event.py — 给已落账的 fill/close 事件补 tier / auth / score。

闭环 overnight-briefing step 9: 新成交检测 → 本工具写回 events JSONL
(+ 可选 scores.jsonl 评分行)。替代过去 prose-only 注解。

**起注解只给规则外成交**(用户 定, 判据见 CLAUDE.md §Trading Discipline);
计划内成交不注解 —— 事件流 + decisions worksheet + 当时 regime/cap 已完整描述它。

tier / auth 两个字段**不再问人, 默认机械推导**(用户):
  tier = base 只给结构豁免层 (holdings.UNMANAGED_ROLES 判据);
         其余一律 position。core/trading 已于
         并入 position (schema.LEGACY_TIER_MAP), 只在 tier_legacy 只读留存 ——
         系统只剩"受不受交易纪律管"这一个区分, 没有需要人判断的余地。
  auth = 成交时授权快照 {cap_pct_nav, gate_no} (起接替 classification,
         classification_removal_design.md D4; 历史事件的 classification 只读保留)。
         期权归正股 (授权住 underlying 卡)。cap 0 / 无 regime
         → 不推导, 留给人 —— 那正是"无授权成交"的形状。
推导结果**必然打印**(依据 + 来源), 不静默。
⚠️ 推导读的是**卡片当前状态**; 给几天前的成交补注解时, regime 可能已经转移过 ——
   打印的 since 日期是给你核对用的, 对不上就 --no-derive 并在 notes 里写明。

CLI:
    python3 quant/journal/annotate_event.py --order-id FS1C...          # tier/auth 自动
    python3 quant/journal/annotate_event.py --order-id FS1C... \\
        --score C --phase episode --score-notes "..."
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 直接 `python3 quant/<层>/<模块>.py` 时让 `quant.*` 可 import

import argparse
import json

from quant.core.holdings import derive_tier, stamping_cards  # noqa: E402  tier/卡片合并判别式单一真源
from quant.core.io_json import append_jsonl, iter_jsonl, read_json, state_lock  # noqa: E402
from quant.core.num import coerce_float  # noqa: E402
from quant.core.occ import parse_occ  # noqa: E402
from quant.core.paths import DECISIONS_PATH, EVENTS_DIR, OPTION_PLANS_PATH, POSITIONS_PATH, SCORES_PATH, WATCHLIST_PATH  # noqa: E402
from quant.core.schema import TIERS, ZONE_AT_FILL, validate_event  # noqa: E402
from quant.core.sizing import derive_auth  # noqa: E402  授权快照判别式单一真源
from quant.core.timeutil import et_today, parse_ts  # noqa: E402

SCORE_SET = {"A", "A-", "B", "C", "D"}


# ── tier / auth 机械推导 (用户 : 不再问人) ──────
#
# 判别式本身住 core (holdings.derive_tier / sizing.derive_auth) —— 因为
# **同步层落库时就要盖章**, 而 sync(2) 够不着 journal(5)。这里只留读文件的壳。
#  下沉: 此前两个判别式住在本文件, 于是 trade_history_sync 想盖章就得
# 反向 import, 分层测试会红; 结果是同步层干脆不盖, 每笔计划内成交裸着落库, 等人
# 想起来跑注解 —— 又一个"手工步骤 = 这一步实际上不存在"。


def _option_plans() -> dict:
    """reversal_probe 的授权住 state/option_plans.json, 与 trade_history_sync
    同一读法; 读不到 = 空 (无本类授权, 不是崩)。"""
    try:
        if not OPTION_PLANS_PATH.exists():
            return {}
        return dict((read_json(OPTION_PLANS_PATH) or {}).get("plans") or {})
    except Exception:                       # noqa: BLE001
        return {}

def _cards() -> dict:
    """Read both stores and use the canonical authorization-card merge."""
    try:
        return stamping_cards(
            read_json(POSITIONS_PATH) or {},
            (read_json(WATCHLIST_PATH) or {}) if WATCHLIST_PATH.exists() else {})
    except Exception:                       # noqa: BLE001 — 读不到就不推导, 不是崩
        return {}

# ── fill ↔ decision 对账 ───────────────────────────────────────
#
# 规则一直写着"超 cap 必须落书面例外"、"跑了 kelly/EV 就落 worksheet", 但强制点
# 只存在于 md 里: decision_log.py 是纯手动 CLI, annotate_event 落 events/scores
# 时完全不碰 decisions。 MU 就这么漏的 —— 7/22 落的是方案A
# (size=227), 7/24 实际成交方案B (1 整股 $925 = 4.1x cap), 例外本身没人记,
# 卡片却写着"decisions 已 log"。声称落库 ≠ 落库。
#
# 这里把它变成一个**会失败的动作**: 注解买入 fill 时反查 worksheet, 找不到 /
# size 对不上就拒写, 并把该跑的命令打出来。
DECISION_WINDOW_DAYS = 14
SIZE_TOL = 0.20          # 实际 size 偏离 worksheet 超 20% = 另一个决策, 得单独记


def _notional(event: dict) -> float:
    occ = parse_occ(str(event.get("ticker") or ""))
    mult = (occ or {}).get("multiplier") or 1
    return (coerce_float(event.get("qty")) or 0) * (coerce_float(event.get("price")) or 0) * mult


def _campaign_deployed(event: dict) -> float:
    """本轮 campaign 的累计投入 (含本笔), 上一次清仓事件后归零。

    对账口径必须是**累计**而不是单笔: 一条 ladder 是 N 笔成交对 1 份 worksheet,
    逐笔去比总额的话 2 档必然各显示 50% 而被误判 (dogfood 当场踩到:
    INTC 7/23 的 1@105 + 1@103 = 一条 2 档 ladder 共 $208, 在 $227 预算内, 却被
    逐笔判成"偏离 54%")。 分类过滤随轴退役 —— campaign 内全部 BUY 计入。
    """
    ticker = str(event.get("ticker") or "").upper()
    order_id = str(event.get("order_id") or "")
    total, seen = 0.0, False
    for path in sorted(EVENTS_DIR.glob("*/trades.jsonl")):
        for r in iter_jsonl(path):
            if str(r.get("ticker") or "").upper() != ticker:
                continue
            if r.get("event_type") in {"close", "expire", "assign"}:
                total, seen = 0.0, False
                continue
            if r.get("event_type") != "fill" or str(r.get("side", "")).upper() != "BUY":
                continue
            if str(r.get("order_id") or "") == order_id:
                seen = True
            total += _notional(r)
    return total if seen else total + _notional(event)


def _decision_candidates(ticker: str, fill_ts: str) -> list[dict]:
    """本 fill 之前 DECISION_WINDOW_DAYS 天内、同 ticker 的 worksheet, 新→旧。"""
    when = parse_ts(fill_ts)
    rows = []
    for r in iter_jsonl(DECISIONS_PATH):
        if str(r.get("ticker") or "").upper() != ticker.upper():
            continue
        ts = parse_ts(str(r.get("timestamp") or ""))
        if when and ts:
            # 窗口对称: 补录常常晚于成交好几天 (NBIS 7/23 的 worksheet 是 7/25 补的),
            # 而这道闸门管的是"记录在不在 + size 对不对", 不是 pre-commit 时序。
            # 时序纪律另有其人 (Bayesian #5), 别在这里顺手兼职。
            if abs((when - ts).total_seconds() / 86400) > DECISION_WINDOW_DAYS:
                continue
        rows.append(r)
    return list(reversed(rows))


def check_decision(event: dict, ref: str | None) -> tuple[bool, str]:
    """(ok, message)。ok=False → 拒绝注解。"""
    ticker = str(event.get("ticker") or "")
    notional = _notional(event)
    cands = _decision_candidates(ticker, str(event.get("timestamp") or ""))

    if ref:
        # 显式 ref 不受窗口限制 —— 窗口管的是**自动匹配**该考虑哪些 worksheet,
        # 人点名的那条不该被它藏起来。此前 ref 只在窗口内搜, 于是一条确实存在、
        # 只是隔得久的 worksheet 会得到 "在 decisions.jsonl 里找不到" ——
        # **报错在说谎**, 而说谎的报错比没有报错更贵 (人会照着它去补一条重复的)。
        # 补录 (backfill 冲动消费那笔) 天然离成交很远, 正是这条路径。
        allhits = [r for r in iter_jsonl(DECISIONS_PATH)
                   if str(r.get("ticker") or "").upper() == ticker.upper()
                   and str(r.get("timestamp", "")).startswith(ref)]
        if not allhits:
            return False, (f"--decision-ref {ref!r} 在 decisions.jsonl 里找不到 "
                           f"({ticker} 近 {DECISION_WINDOW_DAYS} 天内 {len(cands)} 条候选)")
        hit = allhits[-1]
        when, ts = parse_ts(str(event.get("timestamp") or "")), parse_ts(str(hit.get("timestamp") or ""))
        gap = abs((when - ts).total_seconds() / 86400) if when and ts else None
        far = (f" [距成交 {gap:.0f} 天, 超出 {DECISION_WINDOW_DAYS} 天自动匹配窗口 — "
               f"补录路径, 人已显式指认]" if gap and gap > DECISION_WINDOW_DAYS else "")
        return True, f"decision ref {hit.get('timestamp')} ✓{far}"

    if not cands:
        return False, _fix_hint(ticker, notional, "没有对应的 decisions.jsonl worksheet", [])

    sized = [c for c in cands if coerce_float(c.get("size")) not in (None, 0)]
    if not sized:
        return True, "worksheet 存在但未记 size — 放行 (无法对账, 不阻塞)"

    best = sized[0]
    size = coerce_float(best.get("size")) or 0
    deployed = _campaign_deployed(event)
    # 只罚**超**预算。ladder 只成交首档 = 部署不足, 从来不是违规;
    # 需要书面例外的是花超了, 不是没花完。
    if deployed > size * (1 + SIZE_TOL):
        return False, _fix_hint(
            ticker, deployed,
            f"最近 worksheet size=${size:,.0f} ({best.get('timestamp')}), "
            f"本轮同分类累计投入 ${deployed:,.0f} = {deployed / size:.1f}x — "
            f"超出 {deployed / size - 1:.0%} > 容差 {SIZE_TOL:.0%}",
            cands)
    return True, (f"decision {best.get('timestamp')} size ${size:,.0f} ✓ "
                  f"(本轮累计 ${deployed:,.0f} = 预算 {deployed / size:.0%})")


def _fix_hint(ticker: str, notional: float, why: str, cands: list[dict]) -> str:
    lines = [f"[annotate] {ticker} 买入 ${notional:,.0f} 对账失败: {why}",
             "  改了方案 / 超了 cap = 一个新决策, 先落库再注解:",
             f"    python3 quant/journal/decision_log.py --ticker {ticker} --action buy \\",
             f"      --chosen <方案名> --size {notional:.0f} --notes '<为什么偏离>'"]
    if cands:
        lines.append("  或引用已有 worksheet: --decision-ref <timestamp 前缀>")
        for c in cands[:3]:
            lines.append(f"    {c.get('timestamp')}  chosen={c.get('chosen')} "
                         f"size={c.get('size')}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="注解 events JSONL 中的成交事件")
    ap.add_argument("--order-id", required=True)
    ap.add_argument("--tier", choices=sorted(TIERS),
                    help="省略 = 机械推导 (base 只给结构豁免仓, 其余 position)")
    ap.add_argument("--no-derive", action="store_true",
                    help="关掉自动推导 (只写显式传入的字段)")
    ap.add_argument("--zone-at-fill", choices=sorted(ZONE_AT_FILL))
    ap.add_argument("--auth-cap", type=float, default=None,
                    help="人工裁定章: 成交时授权 cap 占 NAV (如 0.4), 须与 --auth-gate 成对。"
                         "机械推导读的是卡片**当前**状态, 给历史成交补章会发生时点错位；"
                         "该参数是人工核对成交时授权事实后的显式写回通道")
    ap.add_argument("--auth-gate", type=int, default=None,
                    help="人工裁定章: 成交时档位 gate_no (与 --auth-cap 成对)")
    ap.add_argument("--notes-add", default="")
    ap.add_argument("--score", choices=sorted(SCORE_SET))
    ap.add_argument("--phase", choices=["entry", "exit", "episode"], default="episode")
    ap.add_argument("--score-notes", default="")
    ap.add_argument("--decision-ref", default="",
                    help="引用 decisions.jsonl 某条 worksheet 的 timestamp 前缀 (对账用)")
    a = ap.parse_args()

    # 对账先于写入: 找到事件 → 校验 → 才允许改。买入 fill 打上 auth
    # 就是在声明"这笔属于某一轮 ladder", 那一轮的 worksheet 必须存在。
    target = next((e for p in sorted(EVENTS_DIR.glob("*/trades.jsonl"))
                   for e in iter_jsonl(p) if str(e.get("order_id")) == a.order_id), None)
    if target is None:
        raise SystemExit(f"[annotate] order_id {a.order_id} 不在 events 中 (先跑 trade_history_sync?)")

    # tier / auth 机械推导 (用户): 推导必打印。
    # 只补**事件上还没有**的字段 —— 已注解过的不覆写 (重跑注解不该改历史判断)。
    tier, auth = a.tier, None
    tkr = str(target.get("ticker") or "")
    # auth 只对 BUY fill 有语义 (入场授权快照) —— 与 trade_history_sync
    # 的落库盖章同一条规则。 实证: 少了这个守卫, 给 7/23 那笔平仓单补 tier
    # 时顺手把入场标签也盖到了一个 **SELL**上。两条路径对同一个
    # 问题给出不同答案 = 又一次"副本会分叉"; 规则必须在两处同形。
    is_entry = (target.get("event_type") == "fill"
                and str(target.get("side", "")).upper() == "BUY")

    # 人工裁定章 (--auth-cap/--auth-gate): 「推不出来 = 留空归人判」判完之后的
    # 写回通道：人工核对完成后若 auth 仍为空，fill_untagged 会持续点名。
    # 裁定值 = 人核对**成交时点**的授权事实后显式给出, 不走机械推导 (它读卡片
    # 当前状态, 对多天前的成交是错误时点 —— docstring ⚠️ 说的就是这个)。
    manual_auth = None
    if a.auth_cap is not None or a.auth_gate is not None:
        if a.auth_cap is None or a.auth_gate is None:
            raise SystemExit("[annotate] --auth-cap 与 --auth-gate 必须成对给出")
        if not is_entry:
            raise SystemExit("[annotate] auth 只对 BUY fill 有语义 (入场授权快照)")
        if target.get("auth"):
            raise SystemExit(f"[annotate] 事件已有 auth 章 {target.get('auth')} — "
                             "不覆写 (重跑注解不该改历史判断)")
        manual_auth = {"cap_pct_nav": a.auth_cap, "gate_no": a.auth_gate,
                       "stamped_at": et_today()}

    if not a.no_derive:
        cards = _cards()
        if not tier and not target.get("tier"):
            tier, why = derive_tier(tkr, cards)
            print(f"[annotate] tier 推导 → {tier} ({why})")
        if (is_entry and manual_auth is None
                and not target.get("auth") and not target.get("classification")):
            # 有效期按**成交日**判 (reversal_probe 过期成交 = 无授权); gate 类不读 today
            auth, why = derive_auth(tkr, cards, str(target.get("timestamp") or et_today())[:10],
                                    option_plans=_option_plans())
            print(f"[annotate] auth 推导 → {auth or '(留空)'} ({why})")

    if manual_auth:
        auth = manual_auth
        print(f"[annotate] auth 人工裁定章 → {auth} — 跳过 worksheet 对账 "
              "(机械章声明「规则内一轮 ladder」才要 worksheet; 裁定章的对账义务在 "
              "scores.jsonl 评分 + notes 判定记录, 缺了会被复盘看见)")
    elif auth and is_entry:
        ok, msg = check_decision(target, a.decision_ref or None)
        if not ok:
            raise SystemExit(msg)
        print(f"[annotate] {msg}")

    found = None
    with state_lock():
        for path in sorted(EVENTS_DIR.glob("*/trades.jsonl")):
            rows = list(iter_jsonl(path))
            hit = False
            for e in rows:
                if str(e.get("order_id")) != a.order_id:
                    continue
                hit = True
                found = e
                for field, v in (("tier", tier), ("auth", auth),
                                 ("zone_at_fill", a.zone_at_fill)):
                    if v:
                        e[field] = v
                if a.notes_add and a.notes_add not in (e.get("notes") or ""):
                    e["notes"] = (e.get("notes") + " | " if e.get("notes") else "") + a.notes_add
                errs = validate_event(e)
                if errs:
                    raise SystemExit(f"[annotate] 修改后事件不合法: {errs}")
            if hit:
                tmp = path.with_suffix(".jsonl.tmp")
                with tmp.open("w", encoding="utf-8") as f:
                    for e in rows:
                        f.write(json.dumps(e, ensure_ascii=False) + "\n")
                tmp.replace(path)
                print(f"annotated {a.order_id} in {path.parent.name}")
                break

    if a.score:
        append_jsonl(SCORES_PATH, {
            "date": str(found.get("timestamp"))[:10],
            "ticker": found.get("ticker"),
            "score": a.score,
            "phase": a.phase,
            "ref": a.order_id,
            "source": "annotate_event CLI",
            "notes": a.score_notes,
        })
        print(f"score {a.score} ({a.phase}) → scores.jsonl")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
