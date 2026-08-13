#!/usr/bin/env python3
"""Upsert or migrate execution zones in v2 state/ (positions.json or watchlist.json).

Writes the agent.zones / agent.thesis / agent.key_risk / agent.invalid_if /
instrument.role fields on the matching ticker. Broker block, lots,
pending_orders, exit_info — never touched.

Usage examples (CLI unchanged from v1; only the storage path moved):
  python3 update_position_zones.py \\
    --ticker AAOI --bucket current --instrument-type stock --role core_theme \\
    --zone1 80 84 --zone2 74 78 --trim 105 112 --no-chase-above 95 \\
    --invalid-if "Breaks post-event structure / thesis weakens" \\
    --timeframe swing_2w --valid-until  \\
    --update-trigger "earnings, macro shock, structure break" \\
    --reasoning-summary "post-panic washout zone; prior support; event-driven repricing"

  python3 update_position_zones.py \\
    --ticker TSLA --bucket watchlist --instrument-type stock \\
    --zone1 390 405 --zone2 360 380

  python3 update_position_zones.py \\
    --ticker AXTI --bucket current --remove-from-other
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

# scripts -> trading-analysis -> skills -> .claude -> workspace root
ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))          # repo root → `quant.*` (分层见 quant/README.md)

from quant.core import book, schema as _schema  # noqa: E402
from quant.core.io_json import atomic_write_json, state_lock  # noqa: E402
from quant.core.num import coerce_float  # noqa: E402
from quant.core.occ import is_option_ticker  # noqa: E402
from quant.core.paths import KLINE_CACHE_DIR, POSITIONS_PATH, WATCHLIST_PATH  # noqa: E402
from quant.core import structure  # noqa: E402
from quant.core.structure import derive_card_regime  # noqa: E402
from quant.core.timeutil import et_today, now_utc_iso as utc_now_iso  # noqa: E402

KNOWN_ROLES = {
    "core_theme",
    "out_of_competence_cleanup",
    "bull_call_spread_long_leg",
    "bull_call_spread_short_leg",
    "core_thesis_via_leaps_call",
    "core_thesis_via_long_dated_call",
    "conditional_entry_short_put",
    # 量能/板块参考票 (SPY/QQQ/SMH/SOXX, 用户): 只供 kline/量比/板块读数,
    # 不交易不评估 — entry_guard 与 gate_ladder_report 按此 role 跳过评估。
    # ⚠️  教训: normalize_role 白名单静默过滤 = 第一次标注没写进去还不报错。
    "volume_reference",
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Upsert execution zones in v2 state/")
    p.add_argument("--ticker", required=True)
    p.add_argument("--bucket", required=True, choices=["current", "watchlist"])
    p.add_argument("--instrument-type", default="stock", choices=["stock", "option"])
    p.add_argument("--role", default="")
    p.add_argument("--linked-underlying", default="")
    p.add_argument("--status", default="")
    p.add_argument("--zone1", nargs=2, type=float)
    p.add_argument("--zone2", nargs=2, type=float)
    p.add_argument("--trim", nargs=2, type=float)
    p.add_argument("--no-chase-above", type=float)
    p.add_argument("--stop-level", type=float, help="Optional explicit stop level (else inferred from invalid_if)")
    p.add_argument(
        "--stop-level-basis", default="underlying", choices=["underlying", "option_price"],
        help="What stop_level refers to (required semantics for option entries; default underlying)",
)
    p.add_argument(
        "--stop-level-days", type=int, default=None,
        help="Consecutive closes below stop_level required to call it broken (default 1). "
             "Use 2 when invalid_if says 连续 2 日 — portfolio_brief's stop alert reads this, "
             "and without it the alert fires a day early.",
)
    p.add_argument("--thesis", default="")
    p.add_argument("--key-risk", default="")
    p.add_argument("--invalid-if", default="")
    p.add_argument("--notes", default="", help="Free-form notes (currently appended to thesis if non-empty)")
    p.add_argument("--timeframe", default="", choices=["", "swing_2w", "swing_4w", "event_window", "post_earnings"])
    p.add_argument("--methodology", default="", choices=["", "normal", "parabolic_mode_spirit"])
    p.add_argument(
        "--reclaim-gates", default=None,
        help='JSON 站稳 gate 列表 [{"level":880,"days":2,"label":"..."}] — entry_guard 逐日计数并'
             '打印 ET 日历。省略=保留现有; "[]"=清空。单独传 (无其他 zone 参数) 时并入现有 zones 不整块替换',
)
    p.add_argument("--valid-until", default="", help="YYYY-MM-DD")
    # gate 与 zone 同生死同锚 (用户)。234 是""这个日期的属性,
    # 不是 NBIS 的属性 —— 没有 anchored_on 就无法审计"重画时标准有没有被放宽"。
    p.add_argument("--anchored-on", default="",
                   help="本次结构画卡日 YYYY-MM-DD (缺省 = 今天 ET)。写进 zones.anchored_on, "
                        "gate 的 anchored_on 必须与之一致, 否则 entry_guard 判 stale")
    p.add_argument("--anchor-basis", default="",
                   help="凭什么画成这样 (如 '226.8-234.4 pivot×4 + HVN 8.2%%')。"
                        "**证据标准不得放宽**这条规矩靠它可审计")
    # last_regime.repair_gate 是转移机生成的, 此前重画只推进 zones 锚 → 每次转移后
    # 的下一次重画 repair gate 必然 STALE (LITE/MSFT  实证, 用户 裁决加通道)。
    p.add_argument("--repair-gate-anchor-basis", default="",
                   help="复核声明: 本次重画已复核 last_regime.repair_gate (收复线) 的证据, "
                        "给它盖与 zones 同日的锚。日期恒 = 本次调用后的 zones.anchored_on.date "
                        "(同锚是构造不是巧合, 不接受第二个日期); level 不可经此改动 (它是转移史)。"
                        "卡上无 repair_gate → 报错。单独传 (无 zone 参数) = 对现有 zone 锚日的补锚")
    p.add_argument("--update-trigger", default="")
    p.add_argument("--reasoning-summary", default="")
    p.add_argument(
        "--conviction", default="medium", choices=["low", "medium", "high"]
)
    p.add_argument(
        "--thesis-status", default="",
        choices=["", "intact", "needs_reverification", "weakening", "broken"],
        help="v2 agent.thesis.status. 省略=保留现有 (stub 拿到真 thesis 自动升 intact)。"
             "v3 (2026-08-04) 起 thesis 状态仅 [FYI] 播报, 不参与授权。",
)
    p.add_argument(
        "--action-intent",
        default="",
        help=(
            "v2 agent.action.intent. For positions: hold/scale_in_acc1/"
            "scale_in_acc2/trim_partial/trim_aggressive/exit/no_chase/cleanup. "
            "For watchlist: watching/wait_for_zone/scale_in_acc1/scale_in_acc2/"
            "drop. Defaults preserved-or-hold/watching."
),
)
    p.add_argument(
        "--action-reason", default="", help="v2 agent.action.reason"
)
    # agent.action.constraints — 结构化约束 (touch_protocol / vehicle 播报)。
    # [] 分类轴退役: --max-classification 只剩 'none' (清除老卡遗留字段);
    # 收紧授权改动 regime cap (classification_removal_design.md D6)。
    p.add_argument(
        "--max-classification", default="",
        choices=["", "none"],
        help="[已退役 ] 仅 'none' 可用 = 清除老卡遗留的分类天花板字段",
)
    p.add_argument("--constraint-reason", default="", help="天花板理由 (进播报)")
    p.add_argument("--constraint-until", default="", help="YYYY-MM-DD, 约束失效日 (含当日); 缺省=永久")
    p.add_argument("--constraint-from", default="", help="YYYY-MM-DD, 约束生效日 (含当日); 缺省=立即。v3 2026-08-04: 约束仅限结构/量化类 (如 SKHY premium gate), 财报/thesis 类 clamp 已废止不再写入")
    p.add_argument(
        "--touch-protocol", default="",
        choices=["", "A_take_first", "B_passive_only", "C_60m_first_bar", "none"],
        help="zone 触碰判读协议 (本版播报, 不改判)。'none' = 清除",
)
    p.add_argument(
        "--vehicle", default="", choices=["", "stock", "leaps", "spread", "stock_first", "none"],
        help="卡片指定 vehicle (期权分支未接线, 当前仅播报)。'none' = 清除",
)
    p.add_argument(
        "--next-regime", default=None,
        help='JSON: agent.next_regime — gate 触发后要切换到的**整套**zone。'
             '{"name":..,"entry_gate":{"level":..,"days":2},'
             '"cap_pct_nav":0.40,"zones":{..},"provisional":["accumulation_zone_1"],'
             '"derivation":".."}。gate 按定义在本 regime 的 no_chase 之上 (它是下一个'
             '状态的入口, 不是本 regime 的入场位)。省略=保留现有; "null"=清空。',
)
    p.add_argument("--remove-from-other", action="store_true")
    p.add_argument("--print-json", action="store_true")
    return p.parse_args()


def load_doc(path: Path, *, required: bool = False) -> Dict[str, Any]:
    if not path.exists():
        if required:
            raise FileNotFoundError(
                f"{path} missing — refusing to recreate a skeleton (restore from git)"
)
        return {
            "schema_version": 2,
            "generated_at": utc_now_iso(),
            "tickers": {},
        }
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"Invalid JSON root in {path}")
    data.setdefault("schema_version", 2)
    data.setdefault("tickers", {})
    return data


def apply_notes(thesis: Dict[str, Any], notes: str) -> None:
    """Idempotent notes block: replaces any prior '[notes]' block instead of
    appending again (repeated CLI runs used to duplicate the block)."""
    base = (thesis.get("summary") or "").split("\n\n[notes] ")[0].rstrip()
    thesis["summary"] = base + "\n\n[notes] " + notes


def warn_bfloor_gap(ticker: str, agent: Dict[str, Any],
                    atr: Optional[float] = None) -> None:
    """bfloor_buf 写入校验 (ledger A3, ; **warn 不拦**)。

    现行约定 (round-2b  用户 批): 带 last_regime.repair_gate 的卡,
    regime 地板 = 收复线 − 0.5×ATR20。此前唯一告警线是 entry_guard 的 0.25N
    (比约定松一半), 写入路径零校验, live 合规全靠画卡的人记得。
    容差 [0.4, 0.6]N; 有意偏离请写进 reasoning, 本函数只喊不拦。
    """
    rg = ((agent.get("last_regime") or {}).get("repair_gate") or {})
    gate = coerce_float(rg.get("level"))
    stop = coerce_float((agent.get("zones") or {}).get("stop_level"))
    if gate is None or stop is None or is_option_ticker(ticker):
        return
    if atr is None:
        try:
            atr = book.atr20_of(ticker, KLINE_CACHE_DIR)
        except Exception:
            atr = None
    if not atr:
        print(f"[zones] ℹ️ bfloor 校验跳过: {ticker} 无 ATR20 (kline 缓存不足) — "
              "stop 距收复线的 0.5N 约定这次没人核")
        return
    gap = (gate - stop) / atr
    if not (0.4 <= gap <= 0.6):
        print(f"[zones] ⚠️ bfloor_buf 偏离: {ticker} stop {stop:g} 距收复线 {gate:g} "
              f"= {gap:.2f}N, 现行约定 0.5N (容差 [0.4,0.6]; round-2b 2026-07-30) — "
              "有意偏离请写进 reasoning-summary, 本警告不拦截")


def apply_constraints(action: Dict[str, Any], args: argparse.Namespace) -> None:
    """写 agent.action.constraints (结构化, 供 lib/sizing 强制)。

    只动本次显式传入的键 —— 不传 = 保持原值 (不是清空)。传 'none' = 显式删除。
    空 constraints dict 直接移除, 免得留一个空壳让人以为"已结构化"。
    """
    c: Dict[str, Any] = dict(action.get("constraints") or {})
    simple = {
        "max_classification": args.max_classification,
        "touch_protocol": args.touch_protocol,
        "vehicle": args.vehicle,
        "reason": args.constraint_reason,
        "until": args.constraint_until,
        "from": args.constraint_from,
    }
    for key, val in simple.items():
        if not val:
            continue
        if val == "none":
            c.pop(key, None)
        else:
            c[key] = val
    if c:
        action["constraints"] = c
    else:
        action.pop("constraints", None)


def zone_pair(value: Optional[list]) -> Optional[list]:
    if not value:
        return None
    low, high = value
    return [min(low, high), max(low, high)]


def normalize_role(raw: str) -> Optional[str]:
    return raw if raw in KNOWN_ROLES else None


def derive_stop_level(args: argparse.Namespace) -> Optional[float]:
    if args.stop_level is not None:
        return float(args.stop_level)
    if args.invalid_if:
        import re
        m = re.search(r"close\s*<\s*\$?(\d+(?:\.\d+)?)", args.invalid_if)
        if m:
            return float(m.group(1))
    return None


def build_zones_block(args: argparse.Namespace, *, ticker: str = "") -> Dict[str, Any]:
    zones: Dict[str, Any] = {}
    z1 = zone_pair(args.zone1)
    if z1:
        zones["accumulation_zone_1"] = z1
    z2 = zone_pair(args.zone2)
    if z2:
        zones["accumulation_zone_2"] = z2
    trim = zone_pair(args.trim)
    if trim:
        zones["trim_zone"] = trim
    if args.no_chase_above is not None:
        zones["no_chase_above"] = float(args.no_chase_above)
    stop = derive_stop_level(args)
    if stop is not None:
        zones["stop_level"] = stop
        if is_option_ticker(ticker) or args.instrument_type == "option":
            zones["stop_level_basis"] = args.stop_level_basis
        # Only persist a non-default confirmation window; 1 is the implicit
        # default everywhere that reads it.
        if args.stop_level_days and int(args.stop_level_days) > 1:
            zones["stop_level_days"] = int(args.stop_level_days)
    if args.valid_until:
        zones["valid_until"] = args.valid_until
    if args.anchored_on or args.anchor_basis:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        zones["anchored_on"] = {
            "date": args.anchored_on or datetime.now(ZoneInfo("America/New_York")).date().isoformat(),
            **({"basis": args.anchor_basis} if args.anchor_basis else {}),
        }
    if args.timeframe:
        zones["timeframe"] = args.timeframe
    if args.methodology:
        zones["methodology"] = args.methodology
    if args.update_trigger:
        zones["update_trigger"] = args.update_trigger
    if args.reasoning_summary:
        zones["reasoning_summary"] = args.reasoning_summary
    if args.reclaim_gates is not None:
        try:
            gates = json.loads(args.reclaim_gates)
        except json.JSONDecodeError as e:
            raise SystemExit(f"--reclaim-gates 不是合法 JSON: {e}")
        if not isinstance(gates, list) or not all(
            isinstance(g, dict)
            and isinstance(g.get("level"), (int, float))
            and isinstance(g.get("days"), int) and g["days"] >= 1
            for g in gates
):
            raise SystemExit('--reclaim-gates 需为 [{"level":数值,"days":整数>=1,"label":可选}] 列表')
        zones["reclaim_gates"] = gates
    return zones


LEVEL_KEYS = {"accumulation_zone_1", "accumulation_zone_2", "trim_zone",
              "no_chase_above", "stop_level"}


def apply_zones_block(agent: Dict[str, Any], zones_block: Dict[str, Any]) -> None:
    """zones 写入语义: **不含任何价位的调用并入现有 zones**; 传了价位 = 重画 = 整块
    替换 (保留现有 reclaim_gates, 除非本次显式传了 --reclaim-gates)。

     加价位判别: 此前判据是 `set(zones_block) <= {"reclaim_gates"}`, 于是
    只传 --valid-until / --reasoning-summary / --update-trigger / --anchored-on 这类
    **纯元数据**的调用走进整块替换分支, 把 zones 换成只剩那一个字段的 dict ——
    z1/z2/trim/no_chase/stop 全部静默消失, schema 仍合法 (都是可选字段) 所以照常落盘。
    重画必然带价位, 所以替换语义对真重画不变。
    """
    prev = agent.get("zones") if isinstance(agent.get("zones"), dict) else None
    if zones_block and not (set(zones_block) & LEVEL_KEYS) and prev:
        agent["zones"] = {**prev, **zones_block}
    elif zones_block:
        if "reclaim_gates" not in zones_block and prev and prev.get("reclaim_gates"):
            zones_block["reclaim_gates"] = prev["reclaim_gates"]
        agent["zones"] = zones_block
    elif "zones" not in agent:
        agent["zones"] = None


def _warn_form_vs_tape(ticker: str, agent: Dict[str, Any], derive_act: str) -> None:
    """画卡时的「形态 ↔ tape」交叉验证（提醒但不拦截）。

    `derive_card_regime` 的判据是 **reclaim_gates 字段有无**—— 一个手工声明,
    不看 tape。8/11 META 卡: basis 里明写「LH+LL(下降)」, 却没写 reclaim_gates
    → derive 按「干净候选」派生常规参与 cap 40%。旧世界运行时降级 (probe 2%)
    把这个分叉盖住了 (两个 bug 互相抵消); 分类轴退役后天花板变实额, 卡当场被
    运行时 VETO 天天拦。判断 (down) 与字段 (无 reclaim_gates) 脱钩 = 本仓惯病
    「会驱动动作的判断必须有结构化字段」的又一例 —— 这里在**画卡当场**把
    分叉说出来, 不等运行时护栏兜底。

    不硬拦: 强拦会让人随手塞个假 reclaim gate, 比警告更坏 (has_next_gate 同风格)。
    """
    if derive_act != "derived_normal":
        return
    try:
        from quant.core import technical
        rows = book.bars(ticker, KLINE_CACHE_DIR)
        if not rows:
            return
        st = technical.swing_structure(rows)
        close = coerce_float(rows[-1].get("close"))
        vetoed, why = structure.structure_repair_veto(None, st, close)
    except Exception:                       # noqa: BLE001 — 守卫失败不挡画卡
        return
    if vetoed:
        print(f"⚠️ {ticker}: 卡片按干净候选派生「常规参与 cap 40%」, 但 tape 读数 = {why}")
        print("   下降/转换趋势候选的正确形态 = --reclaim-gates 修复观察 (cap 0, 收复线"
              "转移后给满额) — 按干净候选画会被 entry_guard 运行时 VETO 天天拦"
              "。确认结构已修复才保留常规参与形态。")


def apply_repair_gate_anchor(agent: Dict[str, Any], args: argparse.Namespace) -> None:
    """--repair-gate-anchor-basis 通道 (用户 裁决)。

    转移机把收复线放在 last_regime.repair_gate (起转移时平移源锚),
    但重画只推进 zones.anchored_on —— repair gate 的锚留在原地, 下一次重画后
    entry_guard 必然 STALE。本通道 = 显式声明"这条收复线在本次重画中复核过":
    日期恒等于卡片 (本次调用后的) zones 锚日 —— 同锚由构造保证, 不给第二个
    日期就没有再漂移的路。level 不可经此改动: 它是转移史, 不是画线产物。

    未传 flag 而本次重锚了 zones → 打印 ⚠️ 提醒但不拦截: 分析者可能确实没复核
    这条线, STALE 是诚实信号 —— 但甩尾必须说出口, 不许无声发生。
    """
    lr = agent.get("last_regime")
    rg = lr.get("repair_gate") if isinstance(lr, dict) else None
    zdate = ((agent.get("zones") or {}).get("anchored_on") or {}).get("date")
    basis = getattr(args, "repair_gate_anchor_basis", "") or ""
    if basis:
        if not isinstance(rg, dict) or rg.get("level") is None:
            raise SystemExit("--repair-gate-anchor-basis: 卡上没有 last_regime.repair_gate — "
                             "无处可锚 (该 gate 由转移机生成, 没经历过转移的卡不需要本 flag)")
        if not zdate:
            raise SystemExit("--repair-gate-anchor-basis: zones 没有 anchored_on 日期 — "
                             "先有 zone 锚才有 gate 同锚 (--anchored-on/--anchor-basis)")
        lr = dict(lr)                       # copy-on-write: 不改脏 existing 的嵌套对象
        rg = dict(rg)
        rg["anchored_on"] = {"date": zdate, "basis": basis}
        lr["repair_gate"] = rg
        agent["last_regime"] = lr
        print(f"[zones] repair_gate {rg['level']:g} 锚 → {zdate} (同 zones)")
        return
    # 重锚了 zones 却没带 repair gate: 提醒, 不拦截
    if (args.anchored_on or args.anchor_basis) and isinstance(rg, dict) and rg.get("level") is not None:
        gdate = ((rg.get("anchored_on") or {}).get("date")) or "无"
        if zdate and gdate != zdate:
            print(f"⚠️ last_regime.repair_gate {rg['level']:g} 锚 {gdate} 未随本次重画 ({zdate}) — "
                  f"entry_guard 将判 STALE; 复核该收复线后加 --repair-gate-anchor-basis 盖同日锚")


def apply_next_gate_anchor(agent: Dict[str, Any], args: argparse.Namespace) -> None:
    """`next_regime` 里 gate 的锚日 **由构造给定**, 不接受手写 ()。

    `apply_repair_gate_anchor` 的同构物, 补的是它漏掉的那半: repair_gate 走专用
    通道、日期恒等于 zones 锚日; 而 `next_regime.entry_gate` / `next_gate_preview`
    走 `--next-regime` **一整块 raw JSON 原样落盘**, 里面的 `anchored_on.date`
    写什么就是什么 —— 而那正是驱动转移的那道 gate, 被保护的 repair_gate 反倒只是
    转移史。**唯一没闸的地方恰好是唯一会动钱的地方。**

    源案例  (用户  从盘前计划里抓出): 收据复核只刷新了拒绝
    计数 (MU 924 拒 ×11→×10, 一根滚出 90 日窗), basis 原文至今写着「60m 真分布
    复核 ****」「故 level 不动」—— 证据没换、level 没动, 却把锚盖成
    8/11。后果不只是 entry_guard 判 STALE (那天 MU 因此**整天没有入场计划**),
    更是让一条证据停在 8/06 的 gate **自称更新鲜**: 规则本来要让"只画一半"显形,
    这一盖反而给 stale 镀了层金, 方向正好反了。**复核 ≠ 重锚。**
    同笔的 MRVL 是镜像 (zones 8/11 而 gate 留在 8/06) —— 有闸的 repair_gate
    自动跟到了 8/11, 没闸的 entry_gate 没有。

    ⛔ **不拦截, 永远不拦截**(与  裁决同拍): 硬拦会误伤两条合法流程 ——
    (a) 只重画 zones、没复核 gate (那正是 8/06 裁决说的"STALE 是诚实信号");
    (b) 转移机写的 next_regime 骨架**根本没有 anchored_on**(它靠 pending_repaint
    管)。

    🚨 **改写只发生在传了 `--next-regime` 的调用里**—— 那才是"本次把 gate 重新
    declare 了一遍"。只重画 zones 时把旧 gate 的锚一起拖到今天, 就是本函数要防的
    那件事**反着做一遍**: 一条没人复核过的线凭空变新鲜。那种情况只提醒 (甩尾),
    让 entry_guard 的 STALE 照常说话。
    """
    nr = agent.get("next_regime")
    if not isinstance(nr, dict) or not nr:
        return
    zdate = ((agent.get("zones") or {}).get("anchored_on") or {}).get("date")
    declared = args.next_regime is not None          # 本次调用重新 declare 了 gate
    rezoned = bool(args.anchored_on or args.anchor_basis)
    for key in ("entry_gate", "next_gate_preview"):
        g = nr.get(key)
        if not isinstance(g, dict) or g.get("level") is None:
            continue
        a = g.get("anchored_on")
        # isinstance 先行: 畸形 anchored_on (字符串等) 不许把重画整条打断 ——
        # 本通道永不拦截, 那也包括"不因为脏数据而抛"。
        gdate = a.get("date") if isinstance(a, dict) else None
        if not gdate:
            continue                    # 无锚 = 转移机骨架, 归 pending_repaint 管
        if not zdate:
            if declared:
                print(f"⚠️ next_regime.{key} {g['level']:g} 带锚 {gdate} 但 zones 无锚 — "
                      f"无从同步; 补 --anchored-on/--anchor-basis 才能审计"
                      f"「重画时标准有没有被放宽」")
            continue
        if gdate == zdate:
            continue
        if declared:
            g["anchored_on"] = {**a, "date": zdate}
            print(f"[zones] next_regime.{key} {g['level']:g} 锚 {gdate} → {zdate} "
                  f"(同 zones, 由构造保证 — 复核 ≠ 重锚, 刷新收据不动日期)")
        elif rezoned:
            print(f"⚠️ next_regime.{key} {g['level']:g} 锚 {gdate} 未随本次重画 "
                  f"({zdate}) — entry_guard 将判 STALE; 复核该线后用 --next-regime "
                  f"重新 declare 才盖同日锚 (不复核就别盖, STALE 是诚实信号)")


def warn_missing_next_gate(ticker: str, agent: Dict[str, Any]) -> None:
    """有 zones 却没有上行状态转移轴 → 提醒, 不拦截。

    用户  定版: **gate = 状态转移轴**, 上升/下降/修复三种趋势都有,
    不是破位卡的专属物。当前 regime 给的是「现在能不能参与」, next_regime 给的是
    「下一个状态在哪」—— 两件独立的事, **画卡时都要同步给出**。

    不拦截的理由同 repair_gate 锚: 分析者可能确实还没定出那条线, 强拦会让人
    随手填一个数 —— 那比空着更坏。空着由 brief 的 `INFRA:card_missing_next_gate`
    每轮点名 (缺字段静默是本仓反复栽的形状, 见 memory data_discipline)。
    """
    if not (agent.get("zones") or {}):
        return
    if structure.has_next_gate(agent):
        return
    cap = coerce_float(((agent.get("current_regime") or {}).get("cap_pct_nav"))) or 0
    tail = ("R2/R3 拿不到授权 = 这张卡只有一轮" if cap > 0
            else "cap 0 且无上行出口 = 锁死, 只能等破位")
    print(f"⚠️ {ticker} 有 zones 但无 next_regime.entry_gate — {tail}。"
          f"重画时用 --next-regime 补 (价位带 level_map 证据, 站不住的带标 "
          f"provisional; 范本 AMZN R_突破_279)")


def merge_position(
    existing: Dict[str, Any], args: argparse.Namespace, *, ticker: str
) -> Dict[str, Any]:
    """Apply CLI args onto an existing positions.json[ticker] block.

    Preserve broker / lots / pending_orders / exit_info exactly. Update only
    instrument.role + agent.zones + agent.thesis (summary, conviction) +
    agent.key_risk / invalid_if + agent.linked_underlying + agent.action.
    """
    out: Dict[str, Any] = dict(existing) if existing else {}
    instrument = dict(out.get("instrument") or {})
    instrument.setdefault("type", args.instrument_type)
    role = normalize_role(args.role)
    if role:
        instrument["role"] = role
    out["instrument"] = instrument

    if "broker" not in out:
        out["broker"] = None
    out.setdefault("status", args.status or "active")
    out.setdefault("lots", {"base": [], "position": []})
    out.setdefault("position_summary", {})
    out.setdefault("pending_orders", [])

    agent = dict(out.get("agent") or {})
    thesis = dict(agent.get("thesis") or {})
    if args.thesis:
        thesis["summary"] = args.thesis
        # stub 卡 (watchlist_sync 建的 needs_card) 拿到真 thesis 即升级为 intact
        if thesis.get("status") == "needs_card":
            thesis["status"] = "intact"
    elif "summary" not in thesis:
        thesis["summary"] = "[pending]"
    thesis.setdefault("status", "intact")
    # 显式覆盖优先于上面的 stub→intact 升级: 一张卡可以同时"有真 thesis"和
    # "thesis 待再验证" (如验证窗口已到但结果偏弱)。entry_guard 读这个字段决定
    # gate_only, 此前没有任何写入路径能设它。
    if args.thesis_status:
        thesis["status"] = args.thesis_status
    thesis["conviction"] = args.conviction
    thesis["last_reviewed"] = utc_now_iso()
    agent["thesis"] = thesis

    zones_block = build_zones_block(args, ticker=ticker)
    apply_zones_block(agent, zones_block)
    if args.next_regime is not None:
        nr = json.loads(args.next_regime)
        if nr is None:
            agent.pop("next_regime", None)
        else:
            agent["next_regime"] = nr
    apply_repair_gate_anchor(agent, args)
    apply_next_gate_anchor(agent, args)
    # card 源 current_regime 跟着结构走 (regime 统一); 期权卡不参与
    # (Today 白名单/加仓授权都是正股层语义), 机器源 regime 由 derive 内部保护
    if not is_option_ticker(ticker):
        derive_card_regime(agent, bucket="positions", today=et_today())

    action = dict(agent.get("action") or {})
    if args.action_intent:
        action["intent"] = args.action_intent
    else:
        action.setdefault("intent", "hold")
    if args.action_reason:
        action["reason"] = args.action_reason
    else:
        action.setdefault("reason", "auto-stub from update_position_zones; review")
    action.setdefault("confidence", args.conviction)
    apply_constraints(action, args)
    agent["action"] = action

    if args.key_risk:
        agent["key_risk"] = args.key_risk
    if args.invalid_if:
        agent["invalid_if"] = args.invalid_if
    if args.linked_underlying:
        agent["linked_underlying"] = args.linked_underlying.upper().strip()
    if args.notes:
        apply_notes(thesis, args.notes)

    out["agent"] = agent
    return out


def merge_candidate(
    existing: Dict[str, Any], args: argparse.Namespace
) -> Dict[str, Any]:
    """Apply CLI args onto state/watchlist.json[ticker]. Same shape as
    position but uses WatchlistAction intent vocabulary.
    """
    out: Dict[str, Any] = dict(existing) if existing else {}
    instrument = dict(out.get("instrument") or {})
    instrument.setdefault("type", args.instrument_type)
    role = normalize_role(args.role)
    if role:
        instrument["role"] = role
    out["instrument"] = instrument
    out.setdefault("broker", None)
    out.setdefault("status", args.status or "watching")

    agent = dict(out.get("agent") or {})
    thesis = dict(agent.get("thesis") or {})
    if args.thesis:
        thesis["summary"] = args.thesis
        # stub 卡 (watchlist_sync 建的 needs_card) 拿到真 thesis 即升级为 intact
        if thesis.get("status") == "needs_card":
            thesis["status"] = "intact"
    elif "summary" not in thesis:
        thesis["summary"] = "[pending]"
    thesis.setdefault("status", "intact")
    # 显式覆盖优先于上面的 stub→intact 升级: 一张卡可以同时"有真 thesis"和
    # "thesis 待再验证" (如验证窗口已到但结果偏弱)。entry_guard 读这个字段决定
    # gate_only, 此前没有任何写入路径能设它。
    if args.thesis_status:
        thesis["status"] = args.thesis_status
    thesis["conviction"] = args.conviction
    thesis["last_reviewed"] = utc_now_iso()
    agent["thesis"] = thesis

    zones_block = build_zones_block(args, ticker=str(out.get("_ticker") or ""))
    apply_zones_block(agent, zones_block)
    if args.next_regime is not None:
        nr = json.loads(args.next_regime)
        if nr is None:
            agent.pop("next_regime", None)
        else:
            agent["next_regime"] = nr
    apply_repair_gate_anchor(agent, args)
    apply_next_gate_anchor(agent, args)
    # card 源 current_regime 跟着结构走 (regime 统一 ; 机器源不触碰)
    if not is_option_ticker(str(out.get("_ticker") or "")):
        act = derive_card_regime(agent, bucket="watchlist", today=et_today())
        _warn_form_vs_tape(str(out.get("_ticker") or ""), agent, act)

    action = dict(agent.get("action") or {})
    if args.action_intent:
        action["intent"] = args.action_intent
    else:
        action.setdefault("intent", "watching")
    if args.action_reason:
        action["reason"] = args.action_reason
    else:
        action.setdefault("reason", "auto-stub from update_position_zones; review")
    action.setdefault("confidence", args.conviction)
    apply_constraints(action, args)
    agent["action"] = action

    if args.key_risk:
        agent["key_risk"] = args.key_risk
    if args.invalid_if:
        agent["invalid_if"] = args.invalid_if
    if args.notes:
        apply_notes(thesis, args.notes)

    out["agent"] = agent
    out.setdefault("priority", "medium")
    out.pop("_ticker", None)
    return out


def main() -> int:
    args = parse_args()
    ticker = args.ticker.upper().strip()

    with state_lock():
        pos = load_doc(POSITIONS_PATH, required=True)
        wl = load_doc(WATCHLIST_PATH)
        pos_dirty = wl_dirty = False

        if args.bucket == "current":
            existing = pos["tickers"].get(ticker, {})
            pos["tickers"][ticker] = merge_position(existing, args, ticker=ticker)
            pos["generated_at"] = utc_now_iso()
            pos_dirty = True
            if args.remove_from_other and ticker in wl["tickers"]:
                wl["tickers"].pop(ticker)
                wl["generated_at"] = utc_now_iso()
                wl_dirty = True
            written = pos["tickers"][ticker]
        else:
            existing = dict(wl["tickers"].get(ticker, {}))
            existing["_ticker"] = ticker
            wl["tickers"][ticker] = merge_candidate(existing, args)
            wl["generated_at"] = utc_now_iso()
            wl_dirty = True
            if args.remove_from_other and ticker in pos["tickers"]:
                pos["tickers"].pop(ticker)
                pos["generated_at"] = utc_now_iso()
                pos_dirty = True
            written = wl["tickers"][ticker]

        # 补画解锁 (裁决 A 配套): 上行转移置的 pending_repaint 由"带新锚
        # 的重画写回"清除 —— 此前只有下行路径清锁 (regime_transition:317), 上行补画
        # 完卡还挂着 NEEDS_CARD 永不解锁。
        if getattr(args, "anchored_on", None) or getattr(args, "next_regime", None):
            _ag = written.get("agent") or {}
            _pr = _ag.pop("pending_repaint", None)
            if _pr:
                print(f"[zones] pending_repaint 解锁: {ticker} slots={_pr.get('slots')} "
                      f"(since {_pr.get('since')}) — 补画写回即解除")

        warn_bfloor_gap(ticker, written.get("agent") or {})
        warn_missing_next_gate(ticker, written.get("agent") or {})

        # 保留路径防呆 (): 无 zone 参数的调用是"保留路径" —— 输出的
        # zones / next_regime 必须与输入逐字节相同, 变异即 abort 不落盘。
        # 起因:  03:14 SGT 一次 --key-risk/--notes-only 调用后 GOOGL 卡
        # zones/current/next_regime 全为 null (schema 合法 = stub 形状, 静默写入);
        # 同参数复现三次全部保留, 现场已被 git checkout 销毁, 根因未确认。
        # 无论真凶是本脚本的某个瞬态还是别的写入方, 本脚本从此在保留路径上
        # **可证明无损**—— 下一次同类破坏在这里变成响亮的 abort 而不是安静的落盘。
        prev_agent = (existing.get("agent") or {}) if isinstance(existing, dict) else {}
        new_agent = (written.get("agent") or {})
        if not build_zones_block(args, ticker=ticker):
            pz = prev_agent.get("zones")
            if isinstance(pz, dict) and pz and new_agent.get("zones") != pz:
                raise RuntimeError(
                    f"保留路径变异: {ticker} zones 在无 zone 参数的调用中被改写 — abort, 未写盘")
        if args.next_regime is None:
            pnr = prev_agent.get("next_regime")
            if isinstance(pnr, dict) and pnr and new_agent.get("next_regime") != pnr:
                raise RuntimeError(
                    f"保留路径变异: {ticker} next_regime 在未传 --next-regime 的调用中被改写 — abort, 未写盘")

        # Validate before persisting; only rewrite files actually modified.
        if pos_dirty:
            errors, warnings = _schema.validate_positions_doc(pos)
            report = _schema.format_report(errors, warnings, label="positions.json")
            if report:
                print(report, file=sys.stderr)
            if errors:
                raise RuntimeError("zone update aborted — positions.json schema errors")
            atomic_write_json(POSITIONS_PATH, pos)
        if wl_dirty:
            errors, warnings = _schema.validate_watchlist_doc(wl)
            report = _schema.format_report(errors, warnings, label="watchlist.json")
            if report:
                print(report, file=sys.stderr)
            if errors:
                raise RuntimeError("zone update aborted — watchlist.json schema errors")
            atomic_write_json(WATCHLIST_PATH, wl)

    if args.print_json:
        print(
            json.dumps(
                {
                    "ticker": ticker,
                    "bucket": args.bucket,
                    "payload": written,
                    "removed_from_other": bool(args.remove_from_other),
                },
                ensure_ascii=False,
                indent=2,
)
)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
