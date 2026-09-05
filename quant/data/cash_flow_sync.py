#!/usr/bin/env python3
"""cash_flow_sync.py — 券商资金流水 → events/cash_flows.jsonl (只收外部流 = 银证转账)。

为什么存在: 周复盘拿 nav_history 首尾相减报收益率, 区间内的一笔提款会被整个记成亏损 (入金则被
记成盈利); 券商流水里那笔银证转账与同日换汇腿写得明明白白, 但 nav_snapshot 只存 total_assets,
全仓没有任何账本记「哪一天有钱进出」—— 没有账本, 这个错误是永久的。

判据来自券商流水, 不来自人的记性 (holdings.py 同一哲学): `get_acc_cash_flow` 按 clearing_date
逐日查 (券商 API 实测**只支持按清算日查**, start/end 区间查询被服务端拒绝), 每轮回看 --days N
(缺省 7) 幂等 (key = 清算日:cashflow_id 去重), 周末/假日返回空即跳过。外部流判据 + USD 折算 =
`core/nav` (纯函数, 测试钉), 本模块只做 IO。

写什么: 每条外部流一行 {date, key, cashflow_id, settlement_date, currency, type, direction, amount,
usd_amount, usd_basis, remark, source, synced_at}; 非外部流 (换汇腿 / 货币基金申赎 / 交易结算 Others /
股息 / 利息) 只在 --json 的 seen_types 里计数, 不落账 (它们要么 NAV 中性, 要么本来就是盈亏)。
sidecar `cash_flows.sync.json` 记 synced_through, nav_report 据此判「账本可能漏计」。API 够不到的
(更早的日期 / 券商查不出) 用 --manual 补录, source=manual, 报告原样带出处。

CLI:
    .venv/moomoo/bin/python3 quant/data/cash_flow_sync.py [--days 7 | --start D --end D] [--json]
    python3 quant/data/cash_flow_sync.py --manual --date YYYY-MM-DD --usd-amount -1500 --note "withdrawal"
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 直接 `python3 quant/<层>/<模块>.py` 时让 `quant.*` 可 import

from quant.core import env  # noqa: F401  (.env → os.environ)

import argparse  # noqa: E402
import contextlib  # noqa: E402
import json  # noqa: E402
import time  # noqa: E402
from datetime import date, timedelta  # noqa: E402
from typing import Callable, Dict, Iterator, List, Optional  # noqa: E402

from quant.core.io_json import append_jsonl, atomic_write_json, iter_jsonl, read_json  # noqa: E402
from quant.core.nav import is_external_flow, usd_equivalent  # noqa: E402
from quant.core.num import coerce_float  # noqa: E402
from quant.core.paths import CASH_FLOWS_PATH, CASH_FLOWS_SYNC_PATH, add_openapi_path  # noqa: E402
from quant.core.timeutil import et_today, now_utc_iso  # noqa: E402

SOURCE = "opend:get_acc_cash_flow"
DEFAULT_LOOKBACK_DAYS = 7
# 券商限频 ( 回填实测: "Maximum 20 times per 30 seconds"): 逐日查一次 = 一次请求,
# 回填 60 日就会撞; 撞了不能假装同步过 (synced_through 停在最后成功日), 但更好的是别撞。
RATE_MAX_CALLS, RATE_WINDOW_S = 20, 30.0
RAW_COLS = ("cashflow_id", "clearing_date", "settlement_date", "currency", "cashflow_type",
            "cashflow_direction", "cashflow_amount", "cashflow_remark")


def day_range(start: str, end: str) -> List[str]:
    """[start, end] 逐日 (含周末 —— 清算日可以是任何日历日; 券商对无流水日返回空)。"""
    d0, d1 = date.fromisoformat(start), date.fromisoformat(end)
    if d1 < d0:
        d0, d1 = d1, d0
    return [(d0 + timedelta(days=i)).isoformat() for i in range((d1 - d0).days + 1)]


def _normalize_raw(row: dict) -> dict:
    """SDK DataFrame 行 → 纯 python 类型 (numpy int64 进不了 json)。"""
    out = {}
    for k in RAW_COLS:
        v = row.get(k)
        if k == "cashflow_amount":
            out[k] = coerce_float(v)
        else:
            out[k] = None if v is None else str(v)
    return out


@contextlib.contextmanager
def opend_fetcher() -> Iterator[Callable[[str], List[dict]]]:
    """一个 OpenD 交易连接 → fetch(day) 逐清算日拉流水 (原始行, 列名照 SDK)。OpenD 不可达 → 抛。"""
    add_openapi_path()
    from common import (create_trade_context, get_default_acc_id, get_default_security_firm,  # noqa: E402
                        parse_trd_env, safe_close, RET_OK)
    ctx = create_trade_context(security_firm=get_default_security_firm())
    acc_id, env_real = get_default_acc_id(), parse_trd_env("REAL")

    def fetch(day: str) -> List[dict]:
        ret, data = ctx.get_acc_cash_flow(clearing_date=day, trd_env=env_real, acc_id=acc_id)
        if ret != RET_OK:
            raise RuntimeError(f"get_acc_cash_flow({day}): {data}")
        if data is None or getattr(data, "empty", True):
            return []
        return [_normalize_raw(dict(r)) for _, r in data.iterrows()]

    try:
        yield fetch
    finally:
        safe_close(ctx)


def ledger_row(raw: dict, same_day: List[dict], *, synced_at: str) -> dict:
    day = str(raw.get("clearing_date") or "")[:10]
    usd, basis = usd_equivalent(raw, same_day)
    return {
        "date": day,
        "key": f"{day}:{raw.get('cashflow_id')}",
        "cashflow_id": raw.get("cashflow_id"),
        "settlement_date": raw.get("settlement_date"),
        "currency": raw.get("currency"),
        "type": raw.get("cashflow_type"),
        "direction": raw.get("cashflow_direction"),
        "amount": coerce_float(raw.get("cashflow_amount")),
        "usd_amount": usd,
        "usd_basis": basis,
        "remark": raw.get("cashflow_remark") or "",
        "source": SOURCE,
        "synced_at": synced_at,
    }


def _existing_keys(path: Path) -> set:
    return {str(r.get("key")) for r in iter_jsonl(path) if r.get("key")}


def _throttle(stamps: List[float], *, now: Callable[[], float], sleep: Callable[[float], None]) -> None:
    """20 次 / 30 秒的滑动窗: 第 21 次前睡到窗口空出来。stamps 由调用方持有 (跨日累计)。"""
    t = now()
    stamps[:] = [x for x in stamps if t - x < RATE_WINDOW_S]
    if len(stamps) >= RATE_MAX_CALLS:
        wait = RATE_WINDOW_S - (t - stamps[0]) + 0.5
        if wait > 0:
            sleep(wait)
        t = now()
        stamps[:] = [x for x in stamps if t - x < RATE_WINDOW_S]
    stamps.append(t)


def sync(days: List[str], fetch: Callable[[str], List[dict]], *, ledger_path: Optional[Path] = None,
         sync_path: Optional[Path] = None, synced_at: Optional[str] = None,
         now: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep) -> Dict:
    """逐清算日拉流水 → 外部流落账 (幂等)。券商限频用滑动窗自限 + 撞限频再等一窗重试一次;
    其它 fetch 错误即停 (synced_through 不越过失败日)。"""
    ledger = Path(ledger_path or CASH_FLOWS_PATH)
    side = Path(sync_path or CASH_FLOWS_SYNC_PATH)
    stamp = synced_at or now_utc_iso()
    existing = _existing_keys(ledger)
    out: Dict = {"queried": [], "new": [], "existing": 0, "seen_types": {}, "unpriced": [], "errors": {}}
    stamps: List[float] = []
    for d in sorted(days):
        try:
            _throttle(stamps, now=now, sleep=sleep)
            try:
                raw = fetch(d)
            except Exception as e:                                # noqa: BLE001
                if "high frequency" not in str(e).lower():
                    raise
                sleep(RATE_WINDOW_S + 0.5)                        # 撞限频: 等一窗, 只重试一次
                stamps.clear()
                stamps.append(now())
                raw = fetch(d)
        except Exception as e:                                    # noqa: BLE001
            out["errors"][d] = f"{type(e).__name__}: {e}"
            break
        out["queried"].append(d)
        for r in raw:
            t = str(r.get("cashflow_type") or "")
            out["seen_types"][t] = out["seen_types"].get(t, 0) + 1
            if not is_external_flow(r):
                continue
            row = ledger_row(r, raw, synced_at=stamp)
            if row["key"] in existing:
                out["existing"] += 1
                continue
            append_jsonl(ledger, row, lock=False)
            existing.add(row["key"])
            out["new"].append(row)
            if row["usd_amount"] is None:
                out["unpriced"].append(row)
    if out["queried"]:
        meta = read_json(side) if side.exists() else {}
        through = max(out["queried"])
        prev = str((meta or {}).get("synced_through") or "")
        meta = {"synced_through": max(through, prev), "last_queried": out["queried"], "at": stamp}
        atomic_write_json(side, meta)
        out["synced_through"] = meta["synced_through"]
    else:
        out["synced_through"] = str(((read_json(side) if side.exists() else {}) or {}).get("synced_through") or "") or None
    return out


def manual_row(day: str, usd_amount: float, note: str, *, synced_at: Optional[str] = None) -> dict:
    return {
        "date": day, "key": f"manual:{day}:{usd_amount:+.2f}", "cashflow_id": None, "settlement_date": day,
        "currency": "USD", "type": "Manual", "direction": "IN" if usd_amount >= 0 else "OUT",
        "amount": round(float(usd_amount), 2), "usd_amount": round(float(usd_amount), 2),
        "usd_basis": "人工补录", "remark": note or "", "source": "manual", "synced_at": synced_at or now_utc_iso(),
    }


def append_manual(day: str, usd_amount: float, note: str, *, ledger_path: Optional[Path] = None,
                  synced_at: Optional[str] = None) -> Dict:
    ledger = Path(ledger_path or CASH_FLOWS_PATH)
    row = manual_row(day, usd_amount, note, synced_at=synced_at)
    if row["key"] in _existing_keys(ledger):
        return {"written": False, "row": row, "reason": "同日同金额的人工补录已存在"}
    append_jsonl(ledger, row, lock=False)
    return {"written": True, "row": row}


def _fmt_new(rows: List[dict]) -> str:
    return ", ".join(f"{r['date'][5:]} {r.get('type')} {r.get('currency')} {r.get('amount'):+,.2f}"
                     + (f" = USD {r['usd_amount']:+,.2f}" if r.get("usd_amount") is not None else " (未折算)")
                     for r in rows) or "无"


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="券商资金流水 → 外部现金流账本 (只收出入金)")
    ap.add_argument("--days", type=int, default=DEFAULT_LOOKBACK_DAYS, help="回看 N 日 (含今天, ET 日历)")
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--manual", action="store_true", help="人工补录一笔外部流 (API 够不到的日期)")
    ap.add_argument("--date", help="--manual: 清算日 YYYY-MM-DD")
    ap.add_argument("--usd-amount", type=float, help="--manual: USD 金额, 入金正 / 提款负")
    ap.add_argument("--note", default="", help="--manual: 出处 (谁说的、哪天确认的)")
    a = ap.parse_args(argv)

    if a.manual:
        if not a.date or a.usd_amount is None:
            ap.error("--manual 需要 --date 与 --usd-amount")
        res = append_manual(a.date, a.usd_amount, a.note)
        print(json.dumps(res, ensure_ascii=False) if a.json
              else f"[cashflows] manual {a.date} USD {a.usd_amount:+,.2f}: "
                   f"{'written' if res['written'] else res['reason']}")
        return 0

    if a.start or a.end:
        end = a.end or et_today()
        days = day_range(a.start or end, end)
    else:
        today = date.fromisoformat(et_today())
        days = [(today - timedelta(days=i)).isoformat() for i in range(max(int(a.days), 1) - 1, -1, -1)]
    try:
        with opend_fetcher() as fetch:
            res = sync(days, fetch)
    except Exception as e:                                        # noqa: BLE001
        res = {"queried": [], "new": [], "existing": 0, "seen_types": {}, "unpriced": [],
               "errors": {"connect": f"{type(e).__name__}: {e}"}, "synced_through": None}
    if a.json:
        print(json.dumps(res, ensure_ascii=False))
    else:
        q = res["queried"]
        span = f"{q[0]}..{q[-1]}" if q else "无"
        print(f"[cashflows] 查 {len(q)} 日 ({span}): 新 {len(res['new'])} [{_fmt_new(res['new'])}] / "
              f"已有 {res['existing']} / 类型 {res['seen_types']}"
              + (f" / ⚠️ 未折算 {len(res['unpriced'])}" if res["unpriced"] else "")
              + (f" / ✗ {res['errors']}" if res["errors"] else ""))
    return 0 if res["queried"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
