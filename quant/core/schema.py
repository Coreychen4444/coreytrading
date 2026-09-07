"""Zero-dependency structural validation for state files and trade events.

Why hand-rolled instead of jsonschema: state writers run under BOTH the
moomoo venv and system python3 — a stdlib-only validator works everywhere
without betting on two environments staying in sync.

Two severity tiers:
  errors   — structural breakage; writers must refuse to persist
  warnings — drift signals (lots↔broker mismatch etc.); print, don't block
"""
from __future__ import annotations

from typing import Any

from .occ import is_option_ticker
from .timeutil import parse_ts

INSTRUMENT_TYPES = {"stock", "option", "warrant", "fund", "etf"}
POSITION_STATUS = {"active", "closed"}
EVENT_TYPES = {"fill", "close", "expire", "assign"}
EVENT_SIDES = {"BUY", "SELL", "SELL_SHORT", "BUY_BACK"}
# : core/trading 合并为单一 position 层。理由 (用户): 系统转以趋势
# 交易为主, NAV 规模 (~$22k) 不适合价值投资, 而 core/trading 的区分是价值 vs
# 战术的区分。base 保留, 但只装**结构上不参与交易纪律**的仓位 —— 它不是
# "长期看好"的意思, 是"exit_guard 不管它"的意思 (判据 holdings.UNMANAGED_ROLES)。
TIERS = {"base", "position"}
# 旧标签 → 新标签。历史 events 的 tier 已按此迁移, 原值留在 tier_legacy,
# perf_report 想切旧口径仍切得动。
LEGACY_TIER_MAP = {"core": "position", "trading": "position"}
# [legacy] 退役分类枚举只为验证历史事件。
# 只为让**历史**fill 事件过校验 (tier_legacy 先例); 新事件不写 classification,
# 改写 auth {cap_pct_nav, gate_no} (sizing.derive_auth)。round_no =
# 当天那一版的键 (lots 近似轮号), 只读留存 —— tier_legacy / classification 先例。
CLASSIFICATIONS = {"confirmed", "probe", "early_risky"}
ZONE_AT_FILL = {
    "zone1", "zone2", "waiting", "trim", "above_no_chase", "unknown",
    "gap_z1_z2", "below_zones",
}
PRICE_FLAGS = {"above_no_chase", "in_trim_zone"}
STOP_BASES = {"underlying", "option_price"}

QTY_TOL = 1e-6
USD_TOL = 0.51  # cent-rounding accumulation across lots


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _zone_pair_ok(v) -> bool:
    return (
        isinstance(v, list) and len(v) == 2
        and all(_is_num(x) for x in v) and v[0] <= v[1]
)


# ── 三态 regime (last / current / next) ────────────────────────
#
# gate = 状态转移锚点, 每个 gate 有自己一整套 zone。卡片持有一个滚动窗口:
#   last_regime  — 破了 current 的 stop 之后落到哪 (纯观察, 永不授权)
#   agent.zones  — current
#   next_regime  — 收复 gate 之后升到哪 (可授权, 带 cap)
#
# last 的 cap 必须是 0。带 cap 的 last = "跌破 stop 自动降级到下一个抄底区",
# 那是一台**结构化的 averaging down 机器**(同时踩 Iron Rule #2A 与 falling
# knife), 而且是系统自动帮你踩。恢复资格的唯一路径是走回 up-gate。
PARTICIPATION = {"gate_only", "zone_eligible"}
# source = 所有权判别式: card 由建卡/重画派生覆盖, auto/demoted 只有转移机可写。
# 枚举收死 (ledger N3/N4): "transition"/"promotion" 是设计稿幽灵取值,
# live 从未写过 (促升实测走 derive 写 card); 任何非 card 值都让重画永不刷新该卡,
# 所以 typo 必须在校验层被拦, 不能只查非空。
REGIME_SOURCES = {"card", "auto", "demoted"}
PROVISIONAL_KEYS = {"accumulation_zone_1", "accumulation_zone_2", "trim_zone"}
# : 不再写死 0.05 —— 它当时抄的是 CAP_FRACS["confirmed"] 的值, 于是
# confirmed 从 5% 改成 40% (冲击预算框架定版) 那一刻, schema 直接把所有重画拦死。
# **上界要跟着真源走, 不能抄一份数字。**
from .sizing import NOTIONAL_CAP as _NOTIONAL_CAP  # noqa: E402

CAP_CONFIRMED_MAX = _NOTIONAL_CAP


def _validate_regime(block: Any, p: str, *, is_last: bool) -> list[str]:
    errors: list[str] = []
    if block is None:
        return errors
    if not isinstance(block, dict):
        return [f"{p} must be a dict"]

    cap = block.get("cap_pct_nav")
    if is_last:
        # 显式 0, 不接受"省略即 0" —— 这条红线必须在文件里看得见
        if cap != 0:
            errors.append(f"{p}.cap_pct_nav must be exactly 0 "
                          f"(观察层永不授权入场; got {cap!r})")
        if block.get("participation") != "gate_only":
            errors.append(f"{p}.participation must be 'gate_only' (got {block.get('participation')!r})")
    elif cap is not None:
        if not _is_num(cap) or not (0 <= cap <= CAP_CONFIRMED_MAX):
            errors.append(f"{p}.cap_pct_nav must be numeric in [0, {CAP_CONFIRMED_MAX}] (got {cap!r})")

    zones = block.get("zones")
    if zones is not None and not isinstance(zones, dict):
        errors.append(f"{p}.zones must be a dict")
    elif isinstance(zones, dict):
        for zk in PROVISIONAL_KEYS:
            if zk in zones and zones[zk] is not None and not _zone_pair_ok(zones[zk]):
                errors.append(f"{p}.zones.{zk} must be [lo, hi] numeric pair")
        if is_last:
            prov = set(block.get("provisional") or [])
            missing = sorted((PROVISIONAL_KEYS & set(zones)) - prov)
            if missing:
                errors.append(f"{p}.provisional must cover {missing} "
                              "(观察层的吸纳/减仓区都是待补画, 不可当成算过的位)")

    for gate_key in ("entry_gate", "repair_gate"):
        gate = block.get(gate_key)
        if gate is None:
            continue
        if not isinstance(gate, dict) or not _is_num(gate.get("level")):
            errors.append(f"{p}.{gate_key}.level missing/not numeric")
    return errors


# ── company_profile (用户: 配合情报层利率传导链) ─────
#
# agent.company_profile = 公司层事实底座 (商业模式 + 资产负债表 + TTM 资本强度),
# 消费者是 市场情报采集任务 的公司暴露卡 (§7)。零授权效力。
# 所有权分区: 机器区 (balance_sheet/fundamentals_ttm/source/fetched_at,
# company_profile_sync 覆盖) / 人工区 (business_model/financing_notes/notes_as_of,
# prose 模式写)。这里只拦结构性破坏 —— 数字口径归写侧模块 docstring。
_PROFILE_NUMERIC_BS = (
    "cash_and_sti_m", "restricted_cash_m", "lt_investments_m", "current_debt_m",
    "lt_debt_m", "current_lease_m", "lt_lease_m", "net_ppe_m", "total_assets_m",
    "total_equity_m", "total_debt_m", "net_cash_ex_leases_m",
)
_PROFILE_NUMERIC_TTM = (
    "revenue_m", "operating_cash_flow_m", "capex_m", "free_cash_flow_m",
    "interest_income_m", "interest_expense_m", "capex_to_revenue_pct",
    "capex_to_ocf_pct",
)


def _iso_date_ok(v) -> bool:
    s = str(v or "")
    return len(s) >= 10 and s[4] == "-" and s[7] == "-"


def _validate_company_profile(agent: dict, p: str) -> list[str]:
    cp = agent.get("company_profile")
    if cp is None:
        return []
    if not isinstance(cp, dict):
        return [f"{p}.agent.company_profile must be a dict"]
    errs: list[str] = []
    for key in ("business_model", "financing_notes"):
        v = cp.get(key)
        if v is not None and not (isinstance(v, str) and v.strip()):
            errs.append(f"{p}.agent.company_profile.{key} must be non-empty string when present")
    bs = cp.get("balance_sheet")
    if bs is not None:
        if not isinstance(bs, dict):
            errs.append(f"{p}.agent.company_profile.balance_sheet must be a dict")
        else:
            if not _iso_date_ok(bs.get("as_of")):
                errs.append(f"{p}.agent.company_profile.balance_sheet.as_of must be YYYY-MM-DD "
                            f"(got {bs.get('as_of')!r} — 无 as-of 的报表数字不许落卡)")
            for key in _PROFILE_NUMERIC_BS:
                v = bs.get(key)
                if v is not None and not _is_num(v):
                    errs.append(f"{p}.agent.company_profile.balance_sheet.{key} must be numeric/null (got {v!r})")
    ttm = cp.get("fundamentals_ttm")
    if ttm is not None:
        if not isinstance(ttm, dict):
            errs.append(f"{p}.agent.company_profile.fundamentals_ttm must be a dict")
        else:
            for key in _PROFILE_NUMERIC_TTM:
                v = ttm.get(key)
                if v is not None and not _is_num(v):
                    errs.append(f"{p}.agent.company_profile.fundamentals_ttm.{key} must be numeric/null (got {v!r})")
    if (bs is not None or ttm is not None) and not str(cp.get("source") or "").strip():
        errs.append(f"{p}.agent.company_profile.source missing (机器区数字必须带出处)")
    return errs


# ── valuation (, 用户: 估值结果落卡片机器区) ─────────────────
#
# agent.valuation = 估值层 (quant/valuation) 最新一行账本的摘要, 由 sync/valuation_sync 从
# valuations.jsonl 镜像到卡上 (卡片是简报/trading-analysis 读的地方)。**判断层零授权效力**:
# 不被 sizing/entry_guard/exit_guard/today_group 读; 低估 ≠ GO, 高估 ≠ SELL。
# 这里只拦结构性破坏: 判定/置信词表、数值类型、as_of/source 出处。
_VALUATION_VERDICTS = ("低估", "偏低", "合理", "偏高", "高估", "无法判定")
_VALUATION_CONFIDENCE = ("高", "中", "低")
_VALUATION_NUMERIC = ("price", "fair_value", "ratio", "implied_cagr", "implied_horizon_years",
                      "implied_target_margin", "implied_cagr_at_current_margin", "news_applied")


def _validate_valuation(agent: dict, p: str) -> list[str]:
    v = agent.get("valuation")
    if v is None:
        return []
    if not isinstance(v, dict):
        return [f"{p}.agent.valuation must be a dict"]
    errs: list[str] = []
    if not _iso_date_ok(v.get("as_of")):
        errs.append(f"{p}.agent.valuation.as_of must be YYYY-MM-DD (无 as-of 的估值不许落卡)")
    if v.get("verdict") not in _VALUATION_VERDICTS:
        errs.append(f"{p}.agent.valuation.verdict must be one of {_VALUATION_VERDICTS} (got {v.get('verdict')!r})")
    if v.get("confidence") not in _VALUATION_CONFIDENCE:
        errs.append(f"{p}.agent.valuation.confidence must be one of {_VALUATION_CONFIDENCE}")
    for key in _VALUATION_NUMERIC:
        val = v.get(key)
        if val is not None and not _is_num(val):
            errs.append(f"{p}.agent.valuation.{key} must be numeric/null (got {val!r})")
    for key in ("source", "fyi"):
        if not str(v.get(key) or "").strip():
            errs.append(f"{p}.agent.valuation.{key} missing (机器区必须带出处与 FYI 行)")
    return errs


def _validate_card_regime(agent: dict, p: str, tk: str) -> list[str]:
    """zones ↔ current_regime 同生同死 + 形状 (regime 统一 , 方案 a)。

    正股活跃卡强制: 有 zones 必有 current_regime (授权语义不再允许活在读侧推断里),
    有 current_regime 必有 zones。期权卡不参与 (白名单/加仓授权是正股层语义)。"""
    if is_option_ticker(tk):
        return []
    errs: list[str] = []
    zones = agent.get("zones")
    has_z = isinstance(zones, dict) and bool(zones)
    cr = agent.get("current_regime")
    if has_z and cr is None:
        return [f"{p}.agent.current_regime missing while zones present (同生同死; "
                "建卡/重画走 update_position_zones 自动派生)"]
    if cr is None:
        return errs
    if not has_z:
        errs.append(f"{p}.agent.current_regime present without zones (同生同死)")
    if not isinstance(cr, dict):
        return errs + [f"{p}.agent.current_regime must be a dict"]
    if not str(cr.get("name") or "").strip():
        errs.append(f"{p}.agent.current_regime.name missing")
    cap = cr.get("cap_pct_nav")
    if not _is_num(cap) or not (0 <= cap <= CAP_CONFIRMED_MAX):
        errs.append(f"{p}.agent.current_regime.cap_pct_nav must be numeric in "
                    f"[0, {CAP_CONFIRMED_MAX}] (got {cap!r})")
    if cr.get("participation") not in PARTICIPATION:
        errs.append(f"{p}.agent.current_regime.participation must be gate_only|zone_eligible "
                    f"(got {cr.get('participation')!r})")
    since = str(cr.get("since") or "")
    if not (len(since) >= 10 and since[4] == "-" and since[7] == "-"):
        errs.append(f"{p}.agent.current_regime.since must be YYYY-MM-DD (消费锚, got {cr.get('since')!r})")
    if str(cr.get("source") or "").strip() not in REGIME_SOURCES:
        errs.append(f"{p}.agent.current_regime.source must be card|auto|demoted "
                    f"(got {cr.get('source')!r} — 所有权判别式: 非 card 值重画永不刷新该卡, "
                    "typo 会把卡永久锁进机器源保护)")
    return errs


def validate_positions_doc(doc: dict) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(doc, dict):
        return (["positions doc is not a dict"], [])
    if "tickers" not in doc or not isinstance(doc["tickers"], dict):
        return (["positions doc missing tickers dict"], [])

    for tk, t in doc["tickers"].items():
        p = f"tickers[{tk}]"
        if not isinstance(t, dict):
            errors.append(f"{p}: not a dict")
            continue

        inst = t.get("instrument")
        if not isinstance(inst, dict) or inst.get("type") not in INSTRUMENT_TYPES:
            errors.append(f"{p}.instrument.type missing/invalid (got {inst and inst.get('type')!r})")

        broker = t.get("broker")
        if not isinstance(broker, dict):
            errors.append(f"{p}.broker missing")
            broker = {}
        elif not _is_num(broker.get("qty")):
            errors.append(f"{p}.broker.qty not numeric")

        if t.get("status") not in POSITION_STATUS:
            errors.append(f"{p}.status must be active|closed (got {t.get('status')!r})")

        agent = t.get("agent") or {}
        if not (agent.get("thesis") or {}).get("summary"):
            errors.append(f"{p}.agent.thesis.summary missing")
        if not (agent.get("action") or {}).get("intent"):
            errors.append(f"{p}.agent.action.intent missing")

        zones = agent.get("zones")
        if isinstance(zones, dict):
            for zk in ("accumulation_zone_1", "accumulation_zone_2", "trim_zone"):
                if zk in zones and zones[zk] is not None and not _zone_pair_ok(zones[zk]):
                    errors.append(f"{p}.agent.zones.{zk} must be [lo, hi] numeric pair")
            if "stop_level_underlying" in zones:
                errors.append(f"{p}.agent.zones uses non-canonical key stop_level_underlying (use stop_level + stop_level_basis)")
            if _is_num(zones.get("stop_level")) and is_option_ticker(tk):
                if zones.get("stop_level_basis") not in STOP_BASES:
                    errors.append(f"{p}.agent.zones.stop_level on option entry requires stop_level_basis (underlying|option_price)")

        errors.extend(_validate_regime(agent.get("last_regime"), f"{p}.agent.last_regime", is_last=True))
        errors.extend(_validate_regime(agent.get("next_regime"), f"{p}.agent.next_regime", is_last=False))
        errors.extend(_validate_company_profile(agent, p))
        errors.extend(_validate_valuation(agent, p))
        if t.get("status") == "active":
            errors.extend(_validate_card_regime(agent, p, tk))

        lots = t.get("lots")
        if not isinstance(lots, dict) or not all(isinstance(lots.get(k), list) for k in TIERS):
            errors.append(f"{p}.lots must contain base/position lists (empty tier = [])")
            lots = {k: [] for k in TIERS}
        if not isinstance(t.get("position_summary"), dict):
            errors.append(f"{p}.position_summary must be a dict")
        if not isinstance(t.get("pending_orders"), list):
            errors.append(f"{p}.pending_orders must be a list")

        # ---- drift warnings (don't block) ----
        lot_qty = 0.0
        lot_cost = {k: 0.0 for k in TIERS}
        lot_qty_tier = {k: 0.0 for k in TIERS}
        for tier in TIERS:
            for l in lots.get(tier, []):
                q = l.get("qty") if _is_num(l.get("qty")) else 0.0
                c = l.get("avg_cost") if _is_num(l.get("avg_cost")) else 0.0
                lot_qty += q
                lot_qty_tier[tier] += q
                lot_cost[tier] += q * c

        bqty = broker.get("qty") if _is_num(broker.get("qty")) else 0.0
        synthetic = bool(broker.get("synthetic"))
        if t.get("status") == "active" and not synthetic and abs(lot_qty - bqty) > QTY_TOL:
            warnings.append(f"{p}: lots qty {lot_qty} != broker qty {bqty} (manual lot maintenance missed?)")

        ps = t.get("position_summary") or {}
        mult = 100 if is_option_ticker(tk) else 1
        for tier in TIERS:
            sq = ps.get(f"{tier}_qty")
            sc = ps.get(f"{tier}_cost_total")
            if _is_num(sq) and abs(sq - lot_qty_tier[tier]) > QTY_TOL:
                warnings.append(f"{p}.position_summary.{tier}_qty {sq} != lots {lot_qty_tier[tier]}")
            if _is_num(sc) and abs(sc - lot_cost[tier] * mult) > USD_TOL:
                warnings.append(f"{p}.position_summary.{tier}_cost_total {sc} != lots arithmetic {lot_cost[tier] * mult:.2f}")

    return errors, warnings


def validate_watchlist_doc(doc: dict) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(doc, dict) or not isinstance(doc.get("tickers"), dict):
        return (["watchlist doc missing tickers dict"], [])
    for tk, t in doc["tickers"].items():
        p = f"watchlist[{tk}]"
        if not isinstance(t, dict):
            errors.append(f"{p}: not a dict")
            continue
        agent = t.get("agent") or {}
        if not (agent.get("thesis") or {}).get("summary"):
            errors.append(f"{p}.agent.thesis.summary missing")
        zones = agent.get("zones")
        if isinstance(zones, dict):
            for zk in ("accumulation_zone_1", "accumulation_zone_2", "trim_zone"):
                if zk in zones and zones[zk] is not None and not _zone_pair_ok(zones[zk]):
                    errors.append(f"{p}.agent.zones.{zk} must be [lo, hi] numeric pair")
        errors.extend(_validate_regime(agent.get("last_regime"), f"{p}.agent.last_regime", is_last=True))
        errors.extend(_validate_regime(agent.get("next_regime"), f"{p}.agent.next_regime", is_last=False))
        errors.extend(_validate_company_profile(agent, p))
        errors.extend(_validate_valuation(agent, p))
        if str(t.get("status")) == "watching":
            errors.extend(_validate_card_regime(agent, p, tk))
    return errors, warnings


def validate_event(event: dict) -> list[str]:
    errors: list[str] = []
    if not isinstance(event, dict):
        return ["event is not a dict"]
    et = event.get("event_type")
    if et not in EVENT_TYPES:
        errors.append(f"event_type must be one of {sorted(EVENT_TYPES)} (got {et!r})")
    ts = event.get("timestamp")
    dt = parse_ts(ts) if ts else None
    if dt is None:
        errors.append(f"timestamp unparseable: {ts!r}")
    elif "Z" not in str(ts) and "+" not in str(ts) and not str(ts).count("-") > 2:
        errors.append(f"timestamp must carry explicit tz offset (got {ts!r})")
    if not event.get("ticker"):
        errors.append("ticker missing")
    qty = event.get("qty")
    if not _is_num(qty) or qty <= 0:
        errors.append(f"qty must be > 0 (got {qty!r})")
    if et in {"fill", "close"}:
        if event.get("side") not in EVENT_SIDES:
            errors.append(f"side must be one of {sorted(EVENT_SIDES)} for fill/close")
        if not _is_num(event.get("price")) or event.get("price") < 0:
            errors.append("price must be numeric >= 0")
        if not event.get("order_id"):
            errors.append("order_id required for fill/close (use MANUAL:<desc> for backfills)")
    # optional enrichment enums
    for field, allowed in (("tier", TIERS), ("classification", CLASSIFICATIONS), ("zone_at_fill", ZONE_AT_FILL)):
        v = event.get(field)
        if v is not None and v not in allowed:
            errors.append(f"{field} must be one of {sorted(allowed)} (got {v!r})")
    # auth = 成交时授权快照 (起接替 classification, sizing.derive_auth)
    auth = event.get("auth")
    if auth is not None:
        if not isinstance(auth, dict):
            errors.append("auth must be an object {cap_pct_nav, gate_no}")
        else:
            if not _is_num(auth.get("cap_pct_nav")) or auth.get("cap_pct_nav") <= 0:
                errors.append("auth.cap_pct_nav must be numeric > 0 (cap 0 = 无授权, 不该有 auth)")
            # gate_no = 爬过几个 gate (二次定版起); round_no = 同日早些
            # 时候那一版的键 (lots 近似轮号), **只读留存**不再写入 —— 两条历史事件
            # 带着它, 校验必须继续认, 否则老事件会突然变成非法 (tier_legacy 先例)。
            gn = auth.get("gate_no", auth.get("round_no"))
            if not isinstance(gn, int) or gn < 1:
                errors.append("auth.gate_no must be int >= 1")
    price_flags = event.get("price_flags")
    if price_flags is not None:
        if not isinstance(price_flags, list) or not set(price_flags) <= PRICE_FLAGS:
            errors.append(
                f"price_flags must be a list ⊆ {sorted(PRICE_FLAGS)} (got {price_flags!r})"
            )
    if event.get("fees") is not None and not _is_num(event.get("fees")):
        errors.append("fees must be numeric when present")
    return errors


def format_report(errors: list[str], warnings: list[str], *, label: str) -> str:
    lines = []
    for e in errors:
        lines.append(f"[schema:ERROR] {label}: {e}")
    for w in warnings:
        lines.append(f"[schema:warn]  {label}: {w}")
    return "\n".join(lines)
