#!/usr/bin/env python3
"""skhy_premium.py — SKHY ADR 对首尔 000660 溢价每日追踪 → market/premium/SKHY.jsonl

为什么存在: SKHY watchlist 卡三 gate 之 A（溢价 ≤5% → scenario A, SKHY 接棒
vehicle; 持续 >10% → scenario B 结构溢价, DRAM ETF 保留为零溢价 vehicle）。
溢价是每日漂移、事后不可重拉的决策数据 → 必须入库（同 iv_history 逻辑）。
公式（watchlist SKHY 卡 canonical）:

    premium = SKHY_close / (000660_close / 10 / USDKRW) - 1

数据源: SKHY = OpenD（authoritative）; 000660 + USDKRW = Yahoo chart API
（OpenD 无 KR 市场行情——这是 data-discipline OpenD-first 的已记录例外）。
两腿存在时差（首尔收盘 KST 15:30 早于美股）, 记录各腿 as-of 日期, 解读时注意。

Gate 分类: <=5% A_trigger / 5-10% approaching / 10-20% structural_band(TSMC 式)
/ >20% flow_premium。briefing 侧约定: approaching 及以下 → 显式提醒迁移 gate。

CLI:
    .venv/moomoo/bin/python3 quant/data/skhy_premium.py            # log today (dedup by date)
    .venv/moomoo/bin/python3 quant/data/skhy_premium.py --json
    python3 quant/data/skhy_premium.py --offline --json            # OpenD down: SKHY 也走 Yahoo
    .venv/moomoo/bin/python3 quant/data/skhy_premium.py --force    # 覆盖当日重写
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 直接 `python3 quant/<层>/<模块>.py` 时让 `quant.*` 可 import

import argparse
import json
import urllib.parse
import urllib.request
from datetime import date, datetime
from zoneinfo import ZoneInfo

from quant.core.io_json import append_jsonl, iter_jsonl  # noqa: E402
from quant.core.timeutil import et_today, now_utc_iso  # noqa: E402
from quant.core.paths import SKHY_PREMIUM_PATH  # noqa: E402

YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range={rng}&interval=1d"
KRX_SYMBOL = "000660.KS"
FX_SYMBOL = "KRW=X"  # USDKRW


# ── Pure (testable) ────────────────────────────────────────────

def parse_yahoo_chart(doc: dict) -> tuple[str, float]:
    """Yahoo v8 chart JSON → (date_iso_in_exchange_tz, last_non_null_close)."""
    res = doc["chart"]["result"][0]
    ts = res.get("timestamp") or []
    quote = ((res.get("indicators") or {}).get("quote") or [{}])[0]
    closes = quote.get("close") or []
    best = None
    for t, c in zip(ts, closes):
        if c is not None:
            best = (t, float(c))
    if best is None:
        raise ValueError("yahoo chart: no non-null close")
    tz = ZoneInfo(res.get("meta", {}).get("exchangeTimezoneName") or "UTC")
    return datetime.fromtimestamp(best[0], tz).date().isoformat(), best[1]


def compute_premium(skhy_close: float, krx_close: float, usdkrw: float) -> tuple[float, float]:
    """→ (fair_value_usd, premium_frac). 1 ADR = 1/10 common share."""
    if min(skhy_close, krx_close, usdkrw) <= 0:
        raise ValueError("non-positive input")
    fv = krx_close / 10.0 / usdkrw
    return fv, skhy_close / fv - 1.0


def classify_gate(premium_frac: float) -> str:
    if premium_frac <= 0.05:
        return "A_trigger(<=5%)"
    if premium_frac <= 0.10:
        return "approaching(5-10%)"
    if premium_frac <= 0.20:
        return "structural_band(10-20%)"
    return "flow_premium(>20%)"


# ── Fetch ──────────────────────────────────────────────────────

def fetch_yahoo_close(symbol: str, rng: str = "7d") -> tuple[str, float]:
    url = YAHOO_CHART.format(sym=urllib.parse.quote(symbol), rng=rng)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return parse_yahoo_chart(json.load(r))


def fetch_skhy_opend() -> tuple[str, float]:
    """SKHY via OpenD snapshot (lazy import; needs venv + OpenD running)."""
    from quant.data.market_data import get_snapshot  # noqa: PLC0415
    snap = get_snapshot(["SKHY"]).get("SKHY") or {}
    px = snap.get("last_price")
    if not px:
        raise RuntimeError("OpenD snapshot returned no SKHY last_price")
    return et_today(), float(px)


# ── Main ───────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(description="SKHY ADR premium daily tracker")
    p.add_argument("--offline", action="store_true", help="SKHY 也走 Yahoo (OpenD down 时)")
    p.add_argument("--force", action="store_true", help="当日已有记录也重写一条")
    p.add_argument("--no-log", action="store_true", help="只算不落盘")
    p.add_argument("--json", action="store_true")
    a = p.parse_args()

    # : date.today() = 系统本地日期 (SGT), 不是 ET 交易日历日。
    # 定时任务在 SGT 18:30 跑, 那一刻 SGT 日 == ET 日, 所以三个月来每次都碰巧对;
    # 一旦在别的时点跑 (人工预览 / pipeline --read-only), SGT 已经是明天 →
    # 写出比 ET 交易日晚一天的 date key, 且幂等检查 `date == today` 同时失效 →
    # 同一个 ET 交易日被写两行。IV 序列不可重拉 (AGENTS.md), 污染是永久的。
    # 口径见 lib/timeutil.et_today() docstring。
    today = et_today()
    if not a.force and not a.no_log and SKHY_PREMIUM_PATH.exists():
        last = None
        for last in iter_jsonl(SKHY_PREMIUM_PATH):
            pass
        if last and last.get("date") == today:
            msg = f"[skhy_premium] already logged {today} (premium {last.get('premium_pct')}%), use --force"
            print(json.dumps(last, ensure_ascii=False) if a.json else msg)
            return

    if a.offline:
        skhy_asof, skhy = fetch_yahoo_close("SKHY")
        skhy_src = "yahoo"
    else:
        skhy_asof, skhy = fetch_skhy_opend()
        skhy_src = "opend"
    krx_date, krx = fetch_yahoo_close(KRX_SYMBOL)
    fx_date, fx = fetch_yahoo_close(FX_SYMBOL)

    fv, prem = compute_premium(skhy, krx, fx)
    rec = {
        "date": today,
        "logged_at": now_utc_iso(),
        "skhy_close": round(skhy, 2), "skhy_asof": skhy_asof, "skhy_src": skhy_src,
        "krx_close": round(krx, 0), "krx_date": krx_date,
        "usdkrw": round(fx, 2), "fx_date": fx_date,
        "fair_value": round(fv, 2),
        "premium_pct": round(prem * 100, 1),
        "gate": classify_gate(prem),
    }
    if not a.no_log:
        append_jsonl(SKHY_PREMIUM_PATH, rec)

    if a.json:
        print(json.dumps(rec, ensure_ascii=False))
    else:
        print(f"SKHY {rec['skhy_close']} ({skhy_src}) | 000660 {rec['krx_close']:.0f} KRW ({krx_date}) "
              f"| FX {rec['usdkrw']} → FV ${rec['fair_value']}")
        print(f"premium {rec['premium_pct']}% → {rec['gate']}")


if __name__ == "__main__":
    main()
