#!/usr/bin/env python3
"""company_profile_sync.py — OpenD 基本面 → agent.company_profile 机器区对账。

为什么存在 (用户): ai_news 利率传导链 (canonical =
ai_news/design/rate-to-equity-transmission-framework.md §7 公司暴露卡 / §14
数据接口) 要求"必须读取资产负债表与现金流量表", 而宏观 agent (ai_news 雷达, 起
= Claude 定时任务 ai-news-radar) 每轮只读
positions.json / watchlist.json 两个文件。本模块把 OpenD 结构化财报落到每张
活跃正股卡的 `agent.company_profile`, 让传导推演在事件窗内直接取用公司层事实
(净利息头寸/资本强度/融资依赖的原料), 不再现查、不从新闻正文抄数字。

所有权分区 (与 broker/agent 分区同一哲学, 两区永不互写):
  机器区 (本模块覆盖):   balance_sheet / fundamentals_ttm / source / fetched_at
  人工区 (本模块永不触碰): business_model / financing_notes / notes_as_of
    —— 浮息/固息拆分、到期墙、可转债条款 OpenD 不提供, 归人工维护 (prose 模式
       `--ticker T --business-model/--financing-notes` 是唯一 sanctioned 写法),
       notes_as_of 由 prose 写入自动盖当日。

🚨 零授权效力: 本块不被 sizing/entry_guard/exit_guard import, 不影响
cap/gate/zone/stop。消费者 = ai_news 宏观 agent (只读) 与人工复盘。

口径 (写死在此, 消费侧不要再猜):
  net_cash_ex_leases_m = cash_and_sti − (current_debt + lt_debt)
      租赁不进减数, lt_investments (Investments and Advances) 不进被减数——
      组件全部单列, 宏观侧要更宽/更窄口径自己组。
  capex_m = −按季 coalesce(Capital Expenditure 8044 → Net PPE Purchase and Sale 8046)
      两派披露 (MU 只有 8044, NBIS 只有 8046), 用了哪派写在 capex_basis;
      net_ppe_purchase 口径是净额 (含处置回收), 非 gross capex。
  TTM = 最近 4 个**离散季度** (financial_type 1-4) 求和, **字段级全齐才产出**
      (部分求和 = 静默少加, 比 null 更坏); 不足 4 季 → 全 null, window 写明
      仅 N 季可得 (新上市/数据缺口诚实暴露)。
  金额单位恒为百万 (字段后缀 _m), 币种由 balance_sheet.currency 声明 (请求 USD 归一)。

覆盖对象: positions active 正股卡 (含结构豁免层——豁免的是 task 输出与退出栈,
不是数据; 雷达个股检索覆盖它们) + watchlist watching 正股卡。期权卡跳过
(underlying 正股卡承载)。

CLI:
    .venv/moomoo/bin/python3 quant/sync/company_profile_sync.py [--tickers T ...] [--json]
    python3 quant/sync/company_profile_sync.py --ticker NBIS \
        --business-model "..." [--financing-notes "..."]      # prose 模式, 不碰 OpenD
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 直接 `python3 quant/<层>/<模块>.py` 时让 `quant.*` 可 import

import argparse
import json

from quant.core.io_json import state_lock  # noqa: E402
from quant.core.occ import is_option_ticker  # noqa: E402
from quant.core.timeutil import now_utc_iso, et_today  # noqa: E402
from quant.sync import state_io  # noqa: E402

# ── OpenD 财报字段 taxonomy (field_id 是 moomoo 归一化 key, 跨公司稳定) ──
# ⚠️ field_id 在不同 statement_type 里**复用** (8001 = 资产负债表的 Total Assets
#    = 利润表的 Total Revenue), 查表必须按报表分开。
STATEMENT_INCOME = 1
STATEMENT_BALANCE = 2
STATEMENT_CASHFLOW = 3
FINANCIAL_QUARTERLY_COMBO = 9  # 离散季度序列 (financial_type 1-4 逐季)

BS_FIELDS = {
    "cash_and_sti_m": 8003,        # Cash and Cash Equivalents & Short-Term Investments
    "restricted_cash_m": 8018,     # Restricted Cash
    "lt_investments_m": 8028,      # Investments and Advances (含 AFS 证券)
    "current_debt_m": 8058,        # -Current Debt
    "lt_debt_m": 8069,             # -Long Term Debt
    "current_lease_m": 8060,       # -Current Capital Lease Obligation
    "lt_lease_m": 8070,            # -Long Term Capital Lease Obligation
    "net_ppe_m": 8024,             # Net PPE (资本强度存量)
    "total_assets_m": 8001,
    "total_equity_m": 8081,
}
IS_FIELDS = {
    "revenue_m": 8001,             # Total Revenue as Reported
    "interest_income_m": 8019,     # Non-Operating Interest Income
    "interest_expense_m": 8020,    # Non-Operating Interest Expense
}
CF_FIELDS = {
    "operating_cash_flow_m": 8015,  # Operating Cash Flow
    "free_cash_flow_m": 8072,       # Free Cash Flow (OpenD 直给)
}
# capex 披露分两派 ( 实测: MU 只有 8044, NBIS 只有 8046) —— 按季
# coalesce, 口径写进 capex_basis, 消费侧不用猜。
CAPEX_DIRECT = 8044     # Capital Expenditure (真 capex, 负数)
CAPEX_NET_PPE = 8046    # Net PPE Purchase and Sale (净额含处置, 负数)
DISCRETE_QUARTER_TYPES = {1, 2, 3, 4}
TTM_QUARTERS = 4
_M = 1e6


def _items_by_id(report: dict) -> dict[int, float]:
    out: dict[int, float] = {}
    for it in report.get("item_list") or []:
        fid, val = it.get("field_id"), it.get("data")
        if isinstance(fid, int) and isinstance(val, (int, float)):
            out[fid] = float(val)
    return out


def _discrete_quarters(reports: list[dict]) -> list[dict]:
    """离散季报, 按报告期倒序; 同一 period_text 去重 (分页/重述保护)。"""
    seen: set[str] = set()
    rows: list[dict] = []
    for r in sorted(reports or [], key=lambda r: r.get("date_time") or 0, reverse=True):
        if r.get("financial_type") not in DISCRETE_QUARTER_TYPES:
            continue
        period = str(r.get("period_text") or r.get("date_time_str") or "")
        if not period or period in seen:
            continue
        seen.add(period)
        rows.append(r)
    return rows


def _pick_m(items: dict[int, float], fid: int) -> float | None:
    v = items.get(fid)
    return round(v / _M, 1) if v is not None else None


def build_balance_sheet(reports: list[dict]) -> dict | None:
    """最新一季资产负债表 → 机器区 balance_sheet 块; 无离散季报 → None。"""
    quarters = _discrete_quarters(reports)
    if not quarters:
        return None
    latest = quarters[0]
    items = _items_by_id(latest)
    block: dict = {
        "period": latest.get("period_text"),
        "as_of": latest.get("date_time_str"),
        "currency": latest.get("currency_code"),
        "accounting_standards": latest.get("accounting_standards") or None,
    }
    for key, fid in BS_FIELDS.items():
        block[key] = _pick_m(items, fid)
    cash, cur_d, lt_d = block["cash_and_sti_m"], block["current_debt_m"], block["lt_debt_m"]
    if cash is not None:
        block["total_debt_m"] = round((cur_d or 0.0) + (lt_d or 0.0), 1)
        block["net_cash_ex_leases_m"] = round(cash - block["total_debt_m"], 1)
    else:
        block["total_debt_m"] = None
        block["net_cash_ex_leases_m"] = None
    return block


def _sum_field(quarters: list[dict], fid: int) -> float | None:
    """严格全齐: 任何一季缺该字段 → None。部分求和 = 静默少加 = 比 null 更坏。"""
    vals = [q_items.get(fid) for q_items in (_items_by_id(q) for q in quarters)]
    if any(v is None for v in vals):
        return None
    return sum(vals)


def _sum_capex(quarters: list[dict]) -> tuple[float | None, str | None]:
    """按季 coalesce(真 capex 8044 → PPE 净额 8046), 全齐才产出, 返回 (值, 口径)。"""
    vals: list[float] = []
    bases: set[str] = set()
    for q in quarters:
        items = _items_by_id(q)
        if items.get(CAPEX_DIRECT) is not None:
            vals.append(items[CAPEX_DIRECT])
            bases.add("capital_expenditure")
        elif items.get(CAPEX_NET_PPE) is not None:
            vals.append(items[CAPEX_NET_PPE])
            bases.add("net_ppe_purchase")
        else:
            return None, None
    basis = bases.pop() if len(bases) == 1 else "mixed"
    return -sum(vals), basis


def build_fundamentals_ttm(income_reports: list[dict], cashflow_reports: list[dict]) -> dict:
    """最近 4 离散季求和; 不足 4 季 → 全 null + window 写明 (不年化、不外推)。"""
    inc = _discrete_quarters(income_reports)[:TTM_QUARTERS]
    cf = _discrete_quarters(cashflow_reports)[:TTM_QUARTERS]
    n = min(len(inc), len(cf))
    block: dict = {k: None for k in (
        "revenue_m", "operating_cash_flow_m", "capex_m", "capex_basis",
        "free_cash_flow_m", "interest_income_m", "interest_expense_m",
        "capex_to_revenue_pct", "capex_to_ocf_pct",
    )}
    if n < TTM_QUARTERS:
        block["window"] = f"insufficient_history: 仅 {n} 季可得, TTM 不产出"
        return block
    inc, cf = inc[:n], cf[:n]
    block["window"] = f"{inc[-1].get('period_text')}–{inc[0].get('period_text')}"

    rev = _sum_field(inc, IS_FIELDS["revenue_m"])
    ocf = _sum_field(cf, CF_FIELDS["operating_cash_flow_m"])
    fcf = _sum_field(cf, CF_FIELDS["free_cash_flow_m"])
    capex, capex_basis = _sum_capex(cf)
    block["capex_basis"] = capex_basis

    block["revenue_m"] = round(rev / _M, 1) if rev is not None else None
    block["operating_cash_flow_m"] = round(ocf / _M, 1) if ocf is not None else None
    block["capex_m"] = round(capex / _M, 1) if capex is not None else None
    block["free_cash_flow_m"] = round(fcf / _M, 1) if fcf is not None else None
    for key in ("interest_income_m", "interest_expense_m"):
        v = _sum_field(inc, IS_FIELDS[key])
        block[key] = round(v / _M, 1) if v is not None else None
    if capex is not None and rev:
        block["capex_to_revenue_pct"] = round(capex / rev * 100, 1)
    if capex is not None and ocf and ocf > 0:
        block["capex_to_ocf_pct"] = round(capex / ocf * 100, 1)
    return block


HUMAN_KEYS = ("business_model", "financing_notes", "notes_as_of")


def apply_machine_update(agent: dict, update: dict) -> None:
    """机器区覆盖写入; 人工区三键原样保留 (缺 profile 时建骨架)。"""
    profile = agent.get("company_profile")
    if not isinstance(profile, dict):
        profile = {}
    preserved = {k: profile.get(k) for k in HUMAN_KEYS if profile.get(k) is not None}
    machine = {k: v for k, v in update.items() if k not in HUMAN_KEYS}
    profile = {**profile, **machine, **preserved}
    agent["company_profile"] = profile


def target_cards(pos_doc: dict, wl_doc: dict) -> list[tuple[str, str]]:
    """(bucket, ticker) 清单: positions active 正股 + watchlist watching 正股。

    期权卡跳过 (underlying 承载); 结构豁免层**不跳过**——豁免的是 task 输出,
    不是数据, 雷达的个股检索覆盖它们。"""
    out: list[tuple[str, str]] = []
    for tk, card in (pos_doc.get("tickers") or {}).items():
        if is_option_ticker(tk) or card.get("status") != "active":
            continue
        out.append(("positions", tk))
    for tk, card in (wl_doc.get("tickers") or {}).items():
        if is_option_ticker(tk) or str(card.get("status")) != "watching":
            continue
        out.append(("watchlist", tk))
    return out


# ── OpenD 取数 (live 路径; 测试注入 fetch 函数替身) ──────────────────


def make_opend_fetcher():
    """返回 fetch(code, statement_type, num) -> report_list; 共享一个 quote ctx。

    节流: OpenD 限 get_financials_statements 30 次/30 秒 ( 实测,
    超限 = 后半清单整批失败)。滑动窗口留 2 次余量, 满了就睡到窗口腾出——
    限频处理必须在代码里, "手动跑两遍"是不存在的步骤。"""
    import time
    from collections import deque

    from quant.core.paths import add_openapi_path
    from quant.core import env  # noqa: F401  (.env → os.environ)
    add_openapi_path()
    from common import create_quote_context, safe_close, RET_OK  # noqa: E402

    ctx = create_quote_context()
    window: deque[float] = deque()
    WINDOW_S, MAX_CALLS = 30.0, 28  # 官方 30, 留余量

    def fetch(code: str, statement_type: int, num: int) -> list[dict]:
        now = time.monotonic()
        while window and now - window[0] > WINDOW_S:
            window.popleft()
        if len(window) >= MAX_CALLS:
            time.sleep(WINDOW_S - (now - window[0]) + 0.5)
        window.append(time.monotonic())
        ret, data = ctx.get_financials_statements(
            code, statement_type=statement_type,
            financial_type=FINANCIAL_QUARTERLY_COMBO, currency_code="USD", num=num)
        if ret != RET_OK or not isinstance(data, dict):
            raise RuntimeError(f"get_financials_statements({code}, type={statement_type}) failed: {data}")
        return data.get("report_list") or []

    def close() -> None:
        safe_close(ctx)

    fetch.close = close  # type: ignore[attr-defined]
    return fetch


def machine_update_for(ticker: str, fetch) -> dict:
    code = f"US.{ticker}"
    bs = build_balance_sheet(fetch(code, STATEMENT_BALANCE, 6))
    ttm = build_fundamentals_ttm(
        fetch(code, STATEMENT_INCOME, 8), fetch(code, STATEMENT_CASHFLOW, 8))
    return {
        "balance_sheet": bs,
        "fundamentals_ttm": ttm,
        "source": f"OpenD get_financials_statements ({code}, quarterly, USD)",
        "fetched_at": now_utc_iso(),
    }


def sync_profiles(pos_doc: dict, wl_doc: dict, *, fetch,
                  only: set[str] | None = None) -> dict:
    """对账主体: 逐卡拉数写机器区。单卡失败只报不炸整轮 (雷达同款降级哲学)。"""
    synced: list[str] = []
    errors: list[str] = []
    touched = {"positions": False, "watchlist": False}
    docs = {"positions": pos_doc, "watchlist": wl_doc}
    for bucket, tk in target_cards(pos_doc, wl_doc):
        if only and tk not in only:
            continue
        card = docs[bucket]["tickers"][tk]
        agent = card.get("agent")
        if not isinstance(agent, dict):
            agent = {}
            card["agent"] = agent
        try:
            update = machine_update_for(tk, fetch)
        except Exception as exc:  # noqa: BLE001 — 单票失败降级报告
            errors.append(f"{bucket}:{tk}: {exc}")
            continue
        if update["balance_sheet"] is None:
            errors.append(f"{bucket}:{tk}: OpenD 无离散季度资产负债表, 跳过 (不写空块)")
            continue
        apply_machine_update(agent, update)
        touched[bucket] = True
        synced.append(f"{bucket}:{tk}")
    return {"synced": synced, "errors": errors, "touched": touched}


def set_prose(ticker: str, *, business_model: str | None,
              financing_notes: str | None) -> dict:
    """prose 模式: 人工区唯一 sanctioned 写法。机器区一个字节不动。

    同一 ticker 在两个桶都有活跃卡时 (期权 synthetic tracker + watchlist 候选),
    prose 写进**所有**命中的卡 —— 只写一张 = 同概念两份数据各说各话。"""
    with state_lock():
        pos = state_io.read_positions()
        wl = state_io.read_watchlist()
        docs = {"positions": pos, "watchlist": wl}
        hits = [b for b, tk in target_cards(pos, wl) if tk == ticker]
        if not hits:
            raise SystemExit(f"{ticker} 不在 positions active / watchlist watching 里")
        stamp = et_today()
        for bucket in hits:
            agent = docs[bucket]["tickers"][ticker].setdefault("agent", {})
            profile = agent.get("company_profile")
            if not isinstance(profile, dict):
                profile = {}
                agent["company_profile"] = profile
            if business_model is not None:
                profile["business_model"] = business_model
            if financing_notes is not None:
                profile["financing_notes"] = financing_notes
            profile["notes_as_of"] = stamp
        if "positions" in hits:
            state_io.write_positions(pos)
        if "watchlist" in hits:
            state_io.write_watchlist(wl)
    return {"ok": True, "buckets": hits, "ticker": ticker, "notes_as_of": stamp}


def main() -> int:
    ap = argparse.ArgumentParser(description="OpenD 基本面 → agent.company_profile")
    ap.add_argument("--tickers", nargs="*", help="只同步这些 ticker (默认全部活跃正股卡)")
    ap.add_argument("--ticker", help="prose 模式的目标 ticker")
    ap.add_argument("--business-model", help="prose 模式: 写人工区 business_model")
    ap.add_argument("--financing-notes", help="prose 模式: 写人工区 financing_notes")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.business_model is not None or args.financing_notes is not None:
        if not args.ticker:
            ap.error("prose 模式必须带 --ticker")
        result = set_prose(args.ticker.upper(),
                           business_model=args.business_model,
                           financing_notes=args.financing_notes)
        print(json.dumps(result, ensure_ascii=False) if args.json
              else f"[company_profile] prose 已写 {'+'.join(result['buckets'])}:{result['ticker']}")
        return 0

    fetch = make_opend_fetcher()
    try:
        with state_lock():
            pos = state_io.read_positions()
            wl = state_io.read_watchlist()
            result = sync_profiles(pos, wl, fetch=fetch,
                                   only=set(t.upper() for t in args.tickers) if args.tickers else None)
            if result["touched"]["positions"]:
                state_io.write_positions(pos)
            if result["touched"]["watchlist"]:
                state_io.write_watchlist(wl)
    finally:
        fetch.close()

    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(f"[company_profile] synced {len(result['synced'])}: {', '.join(result['synced'])}")
        for e in result["errors"]:
            print(f"[company_profile] ERROR {e}", file=sys.stderr)
    return 1 if result["errors"] and not result["synced"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
