"""Free-source macro data fetchers (零 key 依赖).

VIX via FRED keyless CSV endpoint (fredgraph.csv) — 日级收盘, 延迟 1 个交易日内,
对 regime 判断足够。失败时返回 None, 调用方回退手动 --vix。

FX/利率/商品 live 快照 via Yahoo chart meta (同 kline_cache 指数源, 结构化端点):
`--live` 渲染一行隔夜宏观读数, **briefing 的宏观数字必须来自这里, 不许 WebSearch**。
 源案例: briefing 的「USDJPY >163 (40 年新高)」抄自 WebSearch 命中的
8/4 旧文, 与实时值对不上 —— WebSearch 给的是文章不是行情, 文章永远滞后
(memory partner_calibration: WebSearch 对无订阅标的返回缓存旧值, 同一形状)。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 直接 `python3 quant/<层>/<模块>.py` 时让 `quant.*` 可 import

import urllib.request
from datetime import date, timedelta

FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=VIXCLS&cosd={start}"


def fetch_vix(timeout: int = 10) -> dict | None:
    """Returns {"vix": latest_close, "vix_chg_pct": d/d %, "date": iso} or None."""
    start = (date.today() - timedelta(days=14)).isoformat()
    url = FRED_CSV.format(start=start)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"[macro_fetch] VIX fetch failed: {type(e).__name__}: {e}", file=sys.stderr)
        return None
    rows = []
    for line in text.strip().splitlines()[1:]:
        parts = line.split(",")
        if len(parts) != 2:
            continue
        d, v = parts[0].strip(), parts[1].strip()
        try:
            rows.append((d, float(v)))
        except ValueError:
            continue  # '.' = missing value rows
    if not rows:
        return None
    latest_d, latest_v = rows[-1]
    chg = None
    if len(rows) >= 2 and rows[-2][1]:
        chg = round((latest_v - rows[-2][1]) / rows[-2][1] * 100, 2)
    return {"vix": latest_v, "vix_chg_pct": chg, "date": latest_d, "source": "FRED VIXCLS"}


# ── Live macro snapshot (Yahoo chart meta) ───────────────────────────
#
# meta.regularMarketPrice = 该品种自己 session 的最新成交 (FX/期货近乎 24h),
# chartPreviousClose = 上一 session 收盘。^TNX 是 CBOE 10Y 指数 = 收益率×10。
LIVE_SYMBOLS = {
    "USDJPY": "USDJPY=X",
    "DXY": "DX-Y.NYB",
    "US10Y": "^TNX",
    "VIX": "^VIX",
    "WTI": "CL=F",
    "GOLD": "GC=F",
}
_YAHOO_CHART = ("https://query1.finance.yahoo.com/v8/finance/chart/"
                "{sym}?range=5d&interval=1d")


def _yahoo_chart(sym: str, timeout: int = 15) -> tuple[dict, list[tuple[str, float]]]:
    """(meta, [(date_iso, close)...]) — closes 只含非空 bar。测试注入点。"""
    import json
    import urllib.parse
    from datetime import datetime, timezone
    req = urllib.request.Request(
        _YAHOO_CHART.format(sym=urllib.parse.quote(sym)),
        headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read())
    res = payload["chart"]["result"][0]
    ts = res.get("timestamp") or []
    q = (res.get("indicators") or {}).get("quote", [{}])[0]
    closes = []
    for i, t in enumerate(ts):
        c = (q.get("close") or [None] * len(ts))[i]
        if c is None:
            continue
        d = datetime.fromtimestamp(t, tz=timezone.utc).date().isoformat()
        closes.append((d, float(c)))
    return res["meta"], closes


def fetch_live_macro(symbols: dict[str, str] | None = None) -> dict[str, dict]:
    """{name: {price, prev_close, chg_pct, as_of_et}} — 单品种失败跳过不拖全局。

    prev_close 从日线序列取**最近一个非当日 bar 的收盘**—— meta 里的
    chartPreviousClose 是 range 起点之前的收盘不是昨收 (实测 :
    CL=F chartPreviousClose 84.67 = 7/31, 拿它算日变化得出 -9.9%, 真实 d/d
    +0.7%; previousClose/regularMarketPreviousClose 在 chart meta 里全为空)。
    """
    from datetime import datetime, timezone
    out: dict[str, dict] = {}
    for name, sym in (symbols or LIVE_SYMBOLS).items():
        try:
            meta, closes = _yahoo_chart(sym)
            price = meta.get("regularMarketPrice")
            if price is None:
                continue
            price = float(price)
            ts = meta.get("regularMarketTime")
            live_date = (datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
                         if ts else None)
            prev = next((c for d, c in reversed(closes) if d != live_date), None)
            # ^TNX 两种口径并存 (老约定 yield×10 = 46.3, 实测当前源直接给 4.627):
            # >20 判定为 ×10 口径再除, 否则原值即收益率。写死除以 10 在当前源上
            # 会输出 0.46% —— 数字荒谬但不报错, 正是最危险的那种错。
            if name == "US10Y":
                price = price / 10.0 if price > 20 else price
                if prev is not None and prev > 20:
                    prev = prev / 10.0
            as_of = None
            if ts:
                from datetime import timedelta
                et = datetime.fromtimestamp(ts, tz=timezone.utc) - timedelta(hours=4)
                as_of = et.strftime("%m-%d %H:%M ET")   # 夏令 ET; 冬令差 1h, 标注用
            chg = round((price - prev) / prev * 100, 2) if prev else None
            out[name] = {"price": round(price, 4), "prev_close": prev,
                         "chg_pct": chg, "as_of_et": as_of}
        except Exception as e:
            print(f"[macro_fetch] {name} ({sym}) failed: {type(e).__name__}: {e}",
                  file=sys.stderr)
    return out


def render_live_line(data: dict[str, dict]) -> str:
    """代码渲染的隔夜宏观行 — briefing 原文照贴, 只许追加叙事不许改数字。"""
    if not data:
        return "隔夜宏观: live 快照全部失败 — 数字缺失, 不要用 WebSearch 的旧文补"
    fmt = {
        "US10Y": lambda d: f"10Y {d['price']:.2f}%",
        "USDJPY": lambda d: f"USDJPY {d['price']:.2f}" + (f" ({d['chg_pct']:+.2f}%)" if d.get("chg_pct") is not None else ""),
        "DXY": lambda d: f"DXY {d['price']:.2f}",
        "VIX": lambda d: f"VIX {d['price']:.2f}",
        "WTI": lambda d: f"WTI ${d['price']:.2f}" + (f" ({d['chg_pct']:+.1f}%)" if d.get("chg_pct") is not None else ""),
        "GOLD": lambda d: f"Gold ${d['price']:.0f}",
    }
    parts = [fmt[k](data[k]) for k in ("US10Y", "DXY", "USDJPY", "WTI", "GOLD", "VIX")
             if k in data and k in fmt]
    stamps = [d.get("as_of_et") for d in data.values() if d.get("as_of_et")]
    stamp = max(stamps) if stamps else "?"
    return "隔夜宏观 (live " + stamp + "): " + " · ".join(parts)


def main(argv: list[str]) -> int:
    import argparse
    import json
    ap = argparse.ArgumentParser(description="宏观数据快照")
    ap.add_argument("--live", action="store_true", help="Yahoo live FX/利率/商品快照")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.live:
        data = fetch_live_macro()
        if a.json:
            print(json.dumps(data, ensure_ascii=False, indent=1))
        print(render_live_line(data))
        return 0 if data else 1
    v = fetch_vix()
    print(json.dumps(v, ensure_ascii=False) if v else "VIX fetch failed")
    return 0 if v else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
