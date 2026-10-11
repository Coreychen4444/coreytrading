#!/usr/bin/env python3
"""fundamentals.py — OpenD 三表 + 快照 + 估值分位 + 卖方目标价 → 本地基本面缓存。

为什么存在 (用户, 估值模型立项): 估值模型要的原料——季度利润表/资产负债表/
现金流量表 (≥10 年, 周期归一化要看整个周期)、股本与市值、自身 PE 通道、卖方目标价——
OpenD 全部给得出 ( 实测: GOOGL/MU/NBIS/AAOI 季度 50 条一页, 2014 起; 估值分位
5Y; 目标价高/均/低)。本模块只做一件事: 把它们拉下来、按季对齐、落成一份自描述的 JSON,
模型层 (`quant/valuation/model.py`) 只读这份缓存, 不知道 OpenD 存在。

缓存 = `trade/market/fundamentals/<TICKER>.json` (可重拉, 不入 git, 与 klines
同款约定)。每次估值把用到的驱动数字快照进 `valuations.jsonl`, 所以缓存不入 git 不丢收据。

字段 taxonomy (field_id 是 moomoo 归一化 key, 跨公司稳定; ⚠️ 同一 id 在不同报表里复用,
查表必须按报表分开 —— 与 `quant/sync/company_profile_sync.py` 同一份认识):
  利润表  8001 收入 / 8017 营业利润 / 8018 净利息 / 8022 其他损益 / 8023 证券处置损益 /
          8034 税前 / 8035 所得税 / 8046 归母普通股净利 / 8048 稀释 EPS / 8049 DPS
  资产负债表 8003 现金+短投 / 8028 长期投资 / 8058 短债 / 8069 长债 / 8060+8070 租赁 /
          8024 净 PPE / 8081 权益
  现金流  8015 经营现金流 / 8019 折旧摊销 / 8044 真 capex → 8046 净 PPE 购置 (按季 coalesce,
          口径落 capex_basis) / 8072 FCF / 8061 股息 / 8059 净发股 (负 = 回购) / 8058 净发债

🚨 零授权效力: 本层不被 sizing/entry_guard/exit_guard import, 不写 positions/watchlist。

CLI:
    .venv/moomoo/bin/python3 quant/valuation/fundamentals.py --tickers GOOGL MU [--quarters 50] [--json]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 直接 `python3 quant/<层>/<模块>.py` 时让 `quant.*` 可 import

import argparse
import json
import time
from collections import deque
from datetime import datetime, timezone

from quant.core.io_json import atomic_write_json, iter_jsonl, read_json  # noqa: E402
from quant.core.paths import EARNINGS_CALENDAR_PATH, EARNINGS_DEEP_DIR, FUNDAMENTALS_DIR  # noqa: E402
from quant.core.timeutil import now_utc_iso  # noqa: E402
from quant.core.agent_contracts import canonical_hash  # noqa: E402
from quant.core.valuation_contracts import finite  # noqa: E402
from quant.core.financials import discrete_quarter  # noqa: E402

STATEMENT_INCOME = 1
STATEMENT_BALANCE = 2
STATEMENT_CASHFLOW = 3
FINANCIAL_QUARTERLY_COMBO = 9        # 离散季度序列 (financial_type 1-4 逐季)
DEFAULT_QUARTERS = 50                # 一页上限 = 12.5 年, 周期归一化够用

INCOME_FIELDS = {
    "revenue": 8001, "gross_profit": 8004, "rd": 8010, "operating_profit": 8017,
    "net_interest": 8018, "interest_income": 8019, "interest_expense": 8020,
    "other_income": 8022, "gain_on_securities": 8023, "pretax": 8034, "tax": 8035,
    "net_income": 8046, "diluted_eps": 8048, "dps": 8049,
}
BALANCE_FIELDS = {
    "cash_sti": 8003, "lt_investments": 8028, "net_ppe": 8024, "goodwill_intangibles": 8039,
    "total_assets": 8001, "total_liabilities": 8048, "current_assets": 8002,
    "current_liabilities": 8049, "current_debt": 8058, "lt_debt": 8069,
    "current_lease": 8060, "lt_lease": 8070, "total_equity": 8081,
}
CASHFLOW_FIELDS = {
    "ocf": 8015, "da": 8019, "capex_direct": 8044, "net_ppe_purchase": 8046, "fcf": 8072,
    "dividends_paid": 8061, "net_stock_issuance": 8059, "net_debt_issuance": 8058,
}
FLOW_FIELDS = tuple(k for k in INCOME_FIELDS if k not in ("diluted_eps", "dps")) + \
    ("capex",) + tuple(k for k in CASHFLOW_FIELDS if k not in ("capex_direct", "net_ppe_purchase"))
SNAPSHOT_FIELDS = {
    "last_price": "last_price", "update_time": "update_time", "pe_ttm": "pe_ttm_ratio",
    "pe_static": "pe_ratio", "pb": "pb_ratio", "total_market_val": "total_market_val",
    "outstanding_shares": "outstanding_shares", "issued_shares": "issued_shares",
    "eps_ttm_snapshot": "earning_per_share", "dividend_ratio_ttm": "dividend_ratio_ttm",
    "high_52w": "highest52weeks_price", "low_52w": "lowest52weeks_price",
}
PE_INTERVAL_5Y = 6
PS_INTERVAL_5Y = 6
VALUATION_TYPE_PE, VALUATION_TYPE_PS = 1, 3


# ── 解析 (纯函数, 测试用合成 report_list) ─────────────────────────────

def _items_by_id(report: dict) -> dict[int, float]:
    out: dict[int, float] = {}
    for it in report.get("item_list") or []:
        fid, val = it.get("field_id"), it.get("data")
        if type(fid) is int and finite(val):
            out[fid] = float(val)
    return out


def _discrete_quarters(reports: list[dict]) -> dict[str, dict]:
    """离散季报 by period_text; 同一期只留最新 date_time (重述保护)。"""
    out: dict[str, dict] = {}
    for r in reports or []:
        if discrete_quarter(r.get("financial_type")) is None:
            continue
        period = str(r.get("period_text") or "")
        if not period:
            continue
        prev = out.get(period)
        if prev is None or (r.get("date_time") or 0) >= (prev.get("date_time") or 0):
            out[period] = r
    return out


def parse_quarters(income: list[dict], balance: list[dict], cashflow: list[dict]) -> list[dict]:
    """三张表按 period_text 对齐 → 升序季度行。利润表是脊柱 (没有利润表的季度不成行);
    资产负债表/现金流缺的季度对应字段为 None (不猜、不前值填充)。"""
    inc = _discrete_quarters(income)
    bal = _discrete_quarters(balance)
    cf = _discrete_quarters(cashflow)
    rows: list[dict] = []
    for period, r in inc.items():
        items = _items_by_id(r)
        row: dict = {
            "period": period,
            "fiscal_year": r.get("fiscal_year"),
            "quarter": discrete_quarter(r.get("financial_type")),
            "period_end": r.get("date_time_str"),
            "period_end_basis": "provider_calendar_date_unverified_against_filing",
            "provider_period_timestamp": r.get("date_time"),
            "available_at": None,
            "_ts": r.get("date_time") or 0,
        }
        for k, fid in INCOME_FIELDS.items():
            row[k] = items.get(fid)
        b = _items_by_id(bal[period]) if period in bal else {}
        for k, fid in BALANCE_FIELDS.items():
            row[k] = b.get(fid)
        c = _items_by_id(cf[period]) if period in cf else {}
        for k, fid in CASHFLOW_FIELDS.items():
            row[k] = c.get(fid)
        # capex 两派披露按季 coalesce (company_profile_sync 同款): 真 capex 优先, 否则净 PPE 购置
        if row.get("capex_direct") is not None:
            row["capex"], row["capex_basis"] = row["capex_direct"], "capital_expenditure"
        elif row.get("net_ppe_purchase") is not None:
            row["capex"], row["capex_basis"] = row["net_ppe_purchase"], "net_ppe_purchase"
        else:
            row["capex"], row["capex_basis"] = None, None
        rows.append(row)
    rows.sort(key=lambda x: (x["_ts"], x["period"]))
    for x in rows:
        x.pop("_ts", None)
    return rows


REPORT_MAX_DAYS = 100               # 期末 → 财报披露最长间隔 (10-K 最迟 ~90 天)


def report_dates(ticker: str, *, deep_dir: Path | None = None, calendar: dict | None = None,
                 calendar_path: Path | None = None) -> dict:
    """{period_text: 反应日} 来自财报事件存档 + {"_last_print": 反应日} 来自财报日历 (存档只更新到建档时)。

    反应日 = 披露后第一个交易时段 (盘后发布 → 次日; 盘前 → 当日)，即市场能按这份财报定价的第一天。
    """
    out: dict = {}
    p = Path(deep_dir or EARNINGS_DEEP_DIR) / f"{ticker.upper()}.json"
    if p.exists():
        for ev in (read_json(p) or {}).get("data") or []:
            period, day = ev.get("period_text"), ev.get("trading_day_str") or ev.get("pub_trading_day_str")
            if period and day:
                out[period] = min(out.get(period, day), day)
    cal = calendar if calendar is not None else (read_json(calendar_path or EARNINGS_CALENDAR_PATH) or {})
    last = ((cal.get("tickers") or {}).get(ticker.upper()) or {}).get("last_print") or {}
    day = last.get("reaction_day") or last.get("pub_day")
    if day:
        out["_last_print"] = day
    return out


def annotate_availability(rows: list[dict], dates: dict, *, fetched_day: str | None = None) -> list[dict]:
    """给季度行补 available_at = 财报反应日。

    没有它时研究级目标一律按「期末 +45 天」视为可知：刚发布的季度要在反应日之后几周才进 TTM，
    盈利快速变化的票 TTM 会严重滞后、12 月空间被抬高。匹配: 存档按 period_text 精确对;
    日历 last_print 只给期末之后 ≤ REPORT_MAX_DAYS 的最近一个未匹配季度。对不上 = 保持 None (下游回退 +45 天)。
    """
    from datetime import date as _date
    used_last = False
    for q in sorted(rows, key=lambda r: str(r.get("period_end") or ""), reverse=True):
        end = q.get("period_end")
        if not end:
            continue
        day, basis = dates.get(q.get("period")), "earnings_deep:period_text"
        if not day and not used_last and dates.get("_last_print"):
            day, basis = dates["_last_print"], "earnings_calendar:last_print"
        if not day:
            continue
        gap = (_date.fromisoformat(day[:10]) - _date.fromisoformat(str(end)[:10])).days
        if not 0 < gap <= REPORT_MAX_DAYS or (fetched_day and day[:10] > fetched_day):
            continue
        if basis.startswith("earnings_calendar"):
            used_last = True
        q["available_at"], q["available_at_basis"] = day[:10], basis
    return rows


def parse_snapshot(raw: dict) -> dict:
    out = {}
    for k, src in SNAPSHOT_FIELDS.items():
        v = raw.get(src)
        if k == "update_time":
            out[k] = str(v) if v is not None else None
            if out[k]:
                try:
                    from quant.core.timeutil import ET
                    stamp = datetime.fromisoformat(out[k])
                    # This adapter requests US equities; OpenD quote times are ET.
                    out[k] = (stamp if stamp.tzinfo is not None else stamp.replace(tzinfo=ET)).isoformat()
                except ValueError:
                    out[k] = None
            continue
        try:
            out[k] = float(v) if finite(v) else None
        except (TypeError, ValueError):
            out[k] = None
    return out


def parse_valuation(raw: dict, interval_label: str = "5Y") -> dict | None:
    if not isinstance(raw, dict):
        return None
    t = raw.get("trend") or {}
    if not t:
        return None
    return {
        "type": raw.get("valuation_type"),
        "interval": interval_label,
        "current": t.get("current_value"),
        "average": t.get("average_value"),
        "minus_1sd": t.get("avg_minus_1_stddev"),
        "plus_1sd": t.get("avg_plus_1_stddev"),
        "percentile": t.get("valuation_percentile"),
        "forward": t.get("forward_value"),
        "last_update": raw.get("last_update_time_str"),
    }


def parse_sector(pe_raw: dict | None, ps_raw: dict | None) -> dict | None:
    """板块 (OpenD plate) 倍数: PE/PS 5 年序列里的 plate_value 求均值/中位 + 当前值 + 板块名。
    这是「市场一致口径」的参照 (半导体板块 PE 5Y 中位 37.5 / PS 9.2, 实测), 只作交叉不作引擎。"""
    import statistics as _st
    out: dict = {}
    for key, raw in (("pe", pe_raw), ("ps", ps_raw)):
        if not isinstance(raw, dict):
            continue
        items = ((raw.get("trend") or {}).get("historical_items")) or []
        vals = [it.get("plate_value") for it in items if isinstance(it.get("plate_value"), (int, float)) and it["plate_value"] > 0]
        pd_ = raw.get("plate_distribution") or {}
        if pd_.get("plate_name"):
            out["plate_name"] = pd_.get("plate_name")
        out[key] = {"current": vals[-1] if vals else None, "avg5y": _st.mean(vals) if vals else None,
                    "median5y": _st.median(vals) if vals else None, "n": len(vals),
                    "plate_average_now": pd_.get("plate_average_value"), "plate_count": pd_.get("plate_stock_item_count")}
    return out or None


def parse_forward(pe_raw: dict | None, ps_raw: dict | None, snap: dict) -> dict:
    """反推前瞻 EPS/收入的外部参考；未知期限与会计口径不能充当十二个月基准。"""
    px, mcap = snap.get("last_price"), snap.get("total_market_val")
    def _fwd(raw):
        t = (raw or {}).get("trend") or {}
        v = t.get("forward_value")
        return float(v) if finite(v) else None
    fpe, fps = _fwd(pe_raw), _fwd(ps_raw)
    out = {"forward_pe": fpe, "forward_ps": fps, "forward_eps": None, "forward_revenue": None,
           "horizon_months": None, "earnings_basis": None,
           "limitations": ["provider horizon and earnings basis unverified; not a forecast anchor"],
           "as_of": ((pe_raw or {}).get("last_update_time_str") or (ps_raw or {}).get("last_update_time_str")),
           "source": "OpenD get_valuation_detail forward_value (PE/PS) 反推"}
    if fpe and fpe > 0 and px:
        out["forward_eps"] = px / fpe
    elif fpe and fpe < 0 and px:
        out["forward_eps"] = px / fpe            # 负 = 一致预期亏损, 保留符号
    if fps and fps > 0 and mcap:
        out["forward_revenue"] = mcap / fps
    return out


def parse_consensus(raw: dict) -> dict | None:
    if not isinstance(raw, dict) or raw.get("total") in (None, 0):
        return None
    return {k: raw.get(k) for k in ("highest", "average", "lowest", "rating", "total",
                                     "update_time_str", "buy", "hold", "sell")}


# ── OpenD 取数 (live 路径; 测试注入 source 替身) ─────────────────────

def make_opend_source():
    """返回带节流的 OpenD 数据源: statements / snapshot / valuation / consensus / close。

    节流: 各接口官方 30 次/30 秒, 全部走同一个滑动窗口 (留 2 次余量) —— 限频处理必须
    在代码里, "手动跑两遍"是不存在的步骤 (company_profile_sync 同款)。"""
    from quant.core.paths import add_openapi_path
    from quant.core import env  # noqa: F401  (.env → os.environ)
    add_openapi_path()
    from common import create_quote_context, safe_close, RET_OK  # noqa: E402

    ctx = create_quote_context()
    window: deque[float] = deque()
    WINDOW_S, MAX_CALLS = 30.0, 28

    def _tick() -> None:
        now = time.monotonic()
        while window and now - window[0] > WINDOW_S:
            window.popleft()
        if len(window) >= MAX_CALLS:
            time.sleep(WINDOW_S - (now - window[0]) + 0.5)
        window.append(time.monotonic())

    class Source:
        def statements(self, code: str, statement_type: int, num: int) -> list[dict]:
            _tick()
            ret, data = ctx.get_financials_statements(
                code, statement_type=statement_type,
                financial_type=FINANCIAL_QUARTERLY_COMBO, currency_code="USD", num=num)
            if ret != RET_OK or not isinstance(data, dict):
                raise RuntimeError(f"get_financials_statements({code}, type={statement_type}) failed: {data}")
            return data.get("report_list") or []

        def snapshot(self, code: str) -> dict:
            _tick()
            ret, data = ctx.get_market_snapshot([code])
            if ret != RET_OK or data is None or getattr(data, "empty", False):
                raise RuntimeError(f"get_market_snapshot({code}) failed: {data}")
            return data.iloc[0].to_dict()

        def valuation(self, code: str, valuation_type: int = VALUATION_TYPE_PE,
                      interval_type: int = PE_INTERVAL_5Y) -> dict | None:
            _tick()
            ret, data = ctx.get_valuation_detail(code, valuation_type=valuation_type, interval_type=interval_type)
            return data if ret == RET_OK and isinstance(data, dict) else None

        def consensus(self, code: str) -> dict | None:
            _tick()
            ret, data = ctx.get_research_analyst_consensus(code)
            return data if ret == RET_OK and isinstance(data, dict) else None

        def close(self) -> None:
            safe_close(ctx)

    return Source()


def fetch_bundle(ticker: str, source, *, quarters: int = DEFAULT_QUARTERS) -> dict:
    """一票的全部原料。三表缺一 = 整票失败 (没有利润表就没有模型; 没有资产负债表就没有
    权益桥); 估值分位与目标价缺失只置 None (它们是参照不是原料)。"""
    code = f"US.{ticker.upper()}"
    inc = source.statements(code, STATEMENT_INCOME, quarters)
    bal = source.statements(code, STATEMENT_BALANCE, quarters)
    cf = source.statements(code, STATEMENT_CASHFLOW, quarters)
    rows = parse_quarters(inc, bal, cf)
    if not rows:
        raise RuntimeError(f"{ticker}: OpenD 未返回任何离散季度利润表")
    annotate_availability(rows, report_dates(ticker), fetched_day=now_utc_iso()[:10])
    snap = parse_snapshot(source.snapshot(code))
    pe_raw = ps_raw = None
    try:
        pe_raw = source.valuation(code)
        val = parse_valuation(pe_raw)
    except Exception as e:  # noqa: BLE001 — 参照数据, 缺了不炸
        print(f"[fundamentals] {ticker}: valuation detail failed: {e}", file=sys.stderr)
        val = None
    try:
        ps_raw = source.valuation(code, VALUATION_TYPE_PS, PS_INTERVAL_5Y)
    except Exception as e:  # noqa: BLE001
        print(f"[fundamentals] {ticker}: PS valuation failed: {e}", file=sys.stderr)
    forward = parse_forward(pe_raw, ps_raw, snap)
    sector = parse_sector(pe_raw, ps_raw)
    try:
        cons = parse_consensus(source.consensus(code))
    except Exception as e:  # noqa: BLE001
        print(f"[fundamentals] {ticker}: consensus failed: {e}", file=sys.stderr)
        cons = None
    return {
        "ticker": ticker.upper(),
        "fetched_at": now_utc_iso(),
        "source": f"OpenD get_financials_statements/get_market_snapshot/get_valuation_detail/"
                  f"get_research_analyst_consensus ({code}, quarterly, USD)",
        "quarters": rows,
        "snapshot": snap,
        "valuation_pe": val,
        "forward": forward,
        "sector": sector,
        "consensus": cons,
    }


# ── 价格 (深度日线 + 常规缓存, 供 β 回归与回测) ─────────────────────────

def load_prices(ticker: str, *, deep_dir: Path | None = None, cache_dir: Path | None = None) -> dict[str, float]:
    """深度日线 + 常规缓存 按日期合并 (后者覆盖前者), → {YYYY-MM-DD: close}。"""
    from quant.core.paths import KLINE_CACHE_DIR, KLINE_DEEP_DIR
    out: dict[str, float] = {}
    for d in (Path(deep_dir or KLINE_DEEP_DIR), Path(cache_dir or KLINE_CACHE_DIR)):
        p = d / f"{ticker.upper()}.jsonl"
        if p.exists():
            for r in iter_jsonl(p):
                t, c = str(r.get("time") or "")[:10], r.get("close")
                if t and c:
                    out[t] = float(c)
    return out


def price_on(prices: dict[str, float], day: str) -> tuple[str, float] | None:
    """≤ day 的最后一个收盘。"""
    keys = [k for k in prices if k <= day]
    if not keys:
        return None
    k = max(keys)
    return k, prices[k]


# ── 缓存 ────────────────────────────────────────────────────────────

def bundle_path(ticker: str, cache_dir: Path | None = None) -> Path:
    return Path(cache_dir or FUNDAMENTALS_DIR) / f"{ticker.upper()}.json"


def save_bundle(bundle: dict, cache_dir: Path | None = None) -> Path:
    p = bundle_path(bundle["ticker"], cache_dir)
    # Retain observations before replacing the mutable latest cache. These are
    # capture vintages, not reconstructed historical disclosure timestamps.
    history = p.parent / "snapshots" / p.stem
    if p.exists():
        previous = read_json(p)
        prior_path = history / (canonical_hash(previous) + ".json")
        if not prior_path.exists():
            atomic_write_json(prior_path, previous)
    archive = history / (canonical_hash(bundle) + ".json")
    if not archive.exists():
        atomic_write_json(archive, bundle)
    atomic_write_json(p, bundle)
    return p


def load_vintage(ticker: str, *, as_of: str, cache_dir: Path | None = None) -> dict | None:
    """Latest captured bundle available by cutoff, with no revised-latest fallback."""
    cutoff = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
    if cutoff.tzinfo is None:
        raise ValueError("vintage cutoff requires timezone")
    latest = bundle_path(ticker, cache_dir)
    paths = list((latest.parent / "snapshots" / latest.stem).glob("*.json"))
    if latest.exists():
        paths.append(latest)
    candidates = []
    for path in paths:
        bundle = read_json(path)
        try:
            stamp = datetime.fromisoformat(bundle["fetched_at"].replace("Z", "+00:00"))
            if bundle.get("evidence_prepared_at"):
                prepared = datetime.fromisoformat(bundle["evidence_prepared_at"].replace("Z", "+00:00"))
                if prepared.tzinfo is None:
                    continue
                stamp = max(stamp, prepared)
            if bundle.get("ticker") == ticker.upper() and stamp.tzinfo is not None and stamp <= cutoff:
                candidates.append((stamp, canonical_hash(bundle), bundle))
        except (KeyError, ValueError, TypeError):
            continue
    if not candidates:
        return None
    latest_stamp = max(r[0] for r in candidates)
    newest = {digest: b for stamp, digest, b in candidates if stamp == latest_stamp}
    if len(newest) != 1:
        raise ValueError("conflicting bundles at the same capture time")
    return next(iter(newest.values()))


def bundle_age_hours(bundle: dict, now: datetime | None = None) -> float | None:
    ts = bundle.get("fetched_at")
    if not ts:
        return None
    try:
        t = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    now = now or datetime.now(timezone.utc)
    return (now - t).total_seconds() / 3600.0


def load_bundle(ticker: str, *, cache_dir: Path | None = None,
                max_age_hours: float | None = None) -> dict | None:
    p = bundle_path(ticker, cache_dir)
    if not p.exists():
        return None
    b = read_json(p)
    if max_age_hours is not None:
        age = bundle_age_hours(b)
        if age is None or age > max_age_hours:
            return None
    return b


def refresh(tickers: list[str], *, quarters: int = DEFAULT_QUARTERS, source=None,
            cache_dir: Path | None = None) -> dict:
    own = source is None
    src = source or make_opend_source()
    out = {"refreshed": [], "errors": {}}
    try:
        for t in tickers:
            try:
                b = fetch_bundle(t, src, quarters=quarters)
                save_bundle(b, cache_dir)
                out["refreshed"].append(t.upper())
            except Exception as e:  # noqa: BLE001 — 单票失败不炸整轮
                out["errors"][t.upper()] = f"{type(e).__name__}: {e}"
    finally:
        if own:
            src.close()
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="OpenD 基本面 → market/fundamentals/<T>.json")
    ap.add_argument("--tickers", nargs="+", required=True)
    ap.add_argument("--quarters", type=int, default=DEFAULT_QUARTERS)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    res = refresh([t.upper() for t in a.tickers], quarters=a.quarters)
    if a.json:
        print(json.dumps(res, ensure_ascii=False))
    else:
        print(f"refreshed: {', '.join(res['refreshed']) or '-'}")
        for t, e in res["errors"].items():
            print(f"  ✗ {t}: {e}")
    return 1 if res["errors"] and not res["refreshed"] else 0


if __name__ == "__main__":
    sys.exit(main())
