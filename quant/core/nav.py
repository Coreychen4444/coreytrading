"""NAV 序列 × 外部现金流 → 区间真实变动 (纯函数, 单一真源)。

为什么存在: 周复盘 §2 若直接拿 nav_history 首尾两行相减, 区间内的一笔提款会被整个记成亏损
(入金则被记成盈利), 而券商流水里那笔银证转账与同日换汇腿写得明明白白。出入金不是盈亏:
没有外部现金流账本, 这个错误是永久的。NAV 又是所有 cap 的分母 —— 分母变小是对的
(book.latest_nav 照读最后一行, 本模块不碰它), 错的只是「区间收益率」这一个读数。

口径 (钉在测试里):
  外部流   = 银证转账 `Bank Transfer Deposits / Withdrawals` (EXTERNAL_FLOW_TYPES 子串匹配)。
             换汇两腿 / 货币基金申赎 / 交易结算 (Others) / 股息 / 利息 / 手续费都**不是**
             外部流: 它们要么 NAV 中性 (钱在账户内换形态), 要么本来就是盈亏。
  USD 折算 = 非 USD 外部流按**同日换汇腿的备注汇率**折 USD (提款前先 USD→SGD 换汇, SGD 腿
             金额与提款金额相同, 备注里就是那次换汇的汇率); 同日没有同币种同金额的换汇腿
             → 退而取同日任一含该币种↔USD 汇率的换汇腿; 都没有 → usd None, 报告必须明示,
             不猜汇率。
  时点     = 流 F_t 记在 clearing_date = t, 视为发生在 t 收盘前 (NAV_t 已含它); 非交易日
             (周末清算) 的流归到**下一个** NAV 快照日。
             逐日 r_t = (NAV_t − F_t) / NAV_{t−1} − 1 连乘 = TWR;
             另给 Dietz 型简算 (ΔNAV − ΣF) / NAV_start —— 两个都播报, 差异来自流的时点权重。

零授权效力: 只算收益率, 不产出 cap / 股数 / 授权; 不读文件 (core 不读 state), 账本行由
data/cash_flow_sync 写、report/nav_report 读。
"""
from __future__ import annotations

import re
from typing import Iterable, Optional

from .num import coerce_float

#: cashflow_type 子串 → 外部流。券商实测词表: "Bank Transfer Withdrawals" /
#: "Bank Transfer Deposits" (推断同前缀) 是出入金; "Currency Exchange" / "Fund Redemption" /
#: "Others" (交易结算) 都不是。新词出现时 cash_flow_sync 的 --json 会在 seen_types 里列出。
EXTERNAL_FLOW_TYPES = ("Bank Transfer",)
FX_LEG_TYPE = "Currency Exchange"
_RATE_RE = re.compile(r"([A-Z]{3})\s*->\s*([A-Z]{3})\s*([0-9]+(?:\.[0-9]+)?)")
_AMOUNT_TOL = 0.011


def _type(row: dict) -> str:
    return str(row.get("type") or row.get("cashflow_type") or "")


def _amount(row: dict) -> Optional[float]:
    v = row.get("amount")
    if v is None:
        v = row.get("cashflow_amount")
    return coerce_float(v)


def _remark(row: dict) -> str:
    return str(row.get("remark") or row.get("cashflow_remark") or "")


def _currency(row: dict) -> str:
    return str(row.get("currency") or "").upper()


def is_external_flow(row: dict) -> bool:
    """这行流水是不是出入金 (账户与外部世界之间的钱)。"""
    t = _type(row).lower()
    return any(k.lower() in t for k in EXTERNAL_FLOW_TYPES)


def _rate_to_usd(remark: str, currency: str) -> Optional[float]:
    """备注 "(USD -> SGD 1.34)" → 把 1 currency 折成 USD 的乘数; 备注不含该币种对 → None。"""
    for a, b, r in _RATE_RE.findall(remark):
        rate = coerce_float(r)
        if not rate:
            continue
        if a == "USD" and b == currency:
            return 1.0 / rate
        if a == currency and b == "USD":
            return rate
    return None


def usd_equivalent(row: dict, same_day_rows: Iterable[dict]) -> tuple[Optional[float], str]:
    """外部流的 USD 金额 → (usd, 依据)。USD 原币直接用; 其它币种按同日换汇腿的备注汇率折算。"""
    amt = _amount(row)
    if amt is None:
        return None, "金额缺失"
    cur = _currency(row)
    if cur == "USD":
        return round(amt, 2), "USD 原币"
    fx = [r for r in same_day_rows if FX_LEG_TYPE.lower() in _type(r).lower()]
    # 首选: 同币种、同金额的换汇腿 (提款前那一次换汇就是为这笔提款做的)
    paired = [r for r in fx if _currency(r) == cur
              and abs(abs(_amount(r) or 0.0) - abs(amt)) <= _AMOUNT_TOL]
    for cand in paired + [r for r in fx if r not in paired]:
        mult = _rate_to_usd(_remark(cand), cur)
        if mult:
            tag = "同日换汇配对" if cand in paired else "同日换汇汇率 (金额未配对)"
            return round(amt * mult, 2), f"{tag} {_remark(cand).strip()}"
    return None, f"无同日 {cur}↔USD 换汇汇率, 未折算"


def flows_by_date(rows: Iterable[dict]) -> dict[str, float]:
    """账本行 → {date: ΣUSD}; usd_amount 为 None 的行不计 (由 unpriced_flows 单独播报)。"""
    out: dict[str, float] = {}
    for r in rows:
        usd = coerce_float(r.get("usd_amount"))
        d = str(r.get("date") or "")[:10]
        if usd is None or not d:
            continue
        out[d] = round(out.get(d, 0.0) + usd, 2)
    return out


def _nav_rows(rows: Iterable[dict]) -> list[dict]:
    good = [r for r in rows if coerce_float(r.get("total_assets")) is not None and r.get("date")]
    return sorted(good, key=lambda r: str(r["date"]))


def select_window(nav_rows: Iterable[dict], *, days: Optional[int] = None,
                  start: Optional[str] = None, end: Optional[str] = None) -> tuple[Optional[dict], Optional[dict]]:
    """区间端点 = NAV 快照行。end = 不晚于 `end` 的最后一行 (缺省最后一行);
    start = 不晚于 (end − days) 或 `start` 的最后一行 (取不到就用第一行)。→ (start_row, end_row)。"""
    rows = _nav_rows(nav_rows)
    if not rows:
        return None, None
    end_row = rows[-1]
    if end:
        cands = [r for r in rows if str(r["date"]) <= end]
        if not cands:
            return None, None
        end_row = cands[-1]
    if start:
        cutoff = start
    elif days:
        from datetime import date, timedelta
        cutoff = (date.fromisoformat(str(end_row["date"])[:10]) - timedelta(days=int(days))).isoformat()
    else:
        cutoff = None
    if cutoff is None:
        start_row = rows[0]
    else:
        earlier = [r for r in rows if str(r["date"]) <= cutoff and str(r["date"]) < str(end_row["date"])]
        start_row = earlier[-1] if earlier else rows[0]
    if start_row is end_row:
        return None, end_row
    return start_row, end_row


def change_report(nav_rows: Iterable[dict], flow_rows: Iterable[dict], *, days: Optional[int] = 7,
                  start: Optional[str] = None, end: Optional[str] = None) -> Optional[dict]:
    """区间 NAV 变动, 剔除外部现金流。→ dict 或 None (快照不足两行)。

    键: start/end {date,nav} · raw_change_usd/pct · flows [账本行 (仅区间内)] · flow_usd_total ·
    unpriced_flows [usd None 的行] · adjusted_change_usd/pct (Dietz 型) · twr_pct (逐日连乘) ·
    benchmarks {qqq_pct, spy_pct} · snapshots (区间内快照行数)。
    """
    start_row, end_row = select_window(nav_rows, days=days, start=start, end=end)
    if not start_row or not end_row:
        return None
    rows = _nav_rows(nav_rows)
    s_date, e_date = str(start_row["date"])[:10], str(end_row["date"])[:10]
    window = [r for r in rows if s_date < str(r["date"])[:10] <= e_date]
    flows = sorted((r for r in flow_rows if s_date < str(r.get("date") or "")[:10] <= e_date),
                   key=lambda r: str(r.get("date")))
    priced = [r for r in flows if coerce_float(r.get("usd_amount")) is not None]
    unpriced = [r for r in flows if coerce_float(r.get("usd_amount")) is None]
    by_date = flows_by_date(priced)
    flow_total = round(sum(by_date.values()), 2)

    nav0, nav1 = coerce_float(start_row["total_assets"]), coerce_float(end_row["total_assets"])
    raw = round(nav1 - nav0, 2)
    adjusted = round(raw - flow_total, 2)

    # TWR: 非快照日的流归到下一个快照日 (周末清算 → 周一)
    prev_date, prev_nav, growth = s_date, nav0, 1.0
    for r in window:
        d = str(r["date"])[:10]
        f = sum(v for k, v in by_date.items() if prev_date < k <= d)
        nav_t = coerce_float(r["total_assets"])
        if prev_nav and prev_nav > 0:
            growth *= (nav_t - f) / prev_nav
        prev_date, prev_nav = d, nav_t

    def _pct(a, b):
        return round((a / b - 1.0) * 100.0, 2) if a is not None and b else None

    return {
        "start": {"date": s_date, "nav": nav0},
        "end": {"date": e_date, "nav": nav1},
        "snapshots": len(window),
        "raw_change_usd": raw,
        "raw_change_pct": round(raw / nav0 * 100.0, 2) if nav0 else None,
        "flows": flows,
        "flow_usd_total": flow_total,
        "unpriced_flows": unpriced,
        "adjusted_change_usd": adjusted,
        "adjusted_change_pct": round(adjusted / nav0 * 100.0, 2) if nav0 else None,
        "twr_pct": round((growth - 1.0) * 100.0, 2),
        "benchmarks": {
            "qqq_pct": _pct(coerce_float(end_row.get("qqq_close")), coerce_float(start_row.get("qqq_close"))),
            "spy_pct": _pct(coerce_float(end_row.get("spy_close")), coerce_float(start_row.get("spy_close"))),
        },
    }
