#!/usr/bin/env python3
"""Breadth expansion for the gate backtest — Yahoo v8 chart API daily bars.

🚨 **命名消歧（登记）**：本文件里的 "breadth" = **票池的宽度**
（把回测池从 56 票扩到 ~160 票，去相关），**不是市场宽度**
（A/D line · new high-low · % above 50DMA · McClellan · TRIN 那一族）。
两者在同一个 research 目录里会撞名 —— 真市场宽度若要做，出口在
`quant/report/chart_read.py` 的 ① Context 段（`technical_engine_landing_plan.md` §5 阶段 3 的 B3a），
**与本文件无关**。
（立此注的理由 = `RULINGS_LEDGER` §2 命名冲突表的同款预防：一个词承载两个意思，
grep 到符号推不出它指哪件事。）

⚠️ Research-only 数据源例外 (用户 批准 , gate round-2 数据扩容):
Yahoo 日线只进 market/klines_deep/ 回测池, 永不进实时决策/live 缓存/卡片。
数据纪律主条款不变: 价格/K线的实时真源 = OpenD (memory data_discipline)。

Why not OpenD for these: OpenD 历史 K 线 quota 按 30 日滚动的**去重股票数**计费,
已有 56 票在窗口内 (深化免费), 新增 ~105 票会打穿 quota。
Why not Stooq:  起挂 JS proof-of-work 反爬墙, 绕过 bot 检测是红线, 不做。
Yahoo v8 chart 是公开无验证的 JSON 端点, 能用就用, 被拒即停。

差异标注: Yahoo OHLC 是 split-adjusted (不含股息复权; OpenD 是前复权)。episode
检测只用票内相对结构, T+60/120 短窗下股息偏差可忽略且两个世界对称承担。
无 turnover 字段 → 写 null, 消费端用 close×volume 近似 (HVN 加权, 不敏感)。
manifest `_sources.json` 记录每票来源, 混源可审计。

Universe: 行业分层的去相关扩容 (金融/能源/医疗/工业/消费/软件互联网/材料/
公用地产/旅游航空/中概/ETF) — 与 pull_gate_history.OTHER 同一设计哲学:
这些票的 drawdown 由不同因素驱动, gate 规则若在它们身上也成立, 才是结构规则
而不是 2025-26 AI 周期的 artifact。

Usage: python3 quant/research/pull_breadth_history.py   (纯 stdlib, 无需 OpenD/venv)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 直接 `python3 quant/<层>/<模块>.py` 时让 `quant.*` 可 import

import json
import time
import urllib.request
from datetime import datetime, timezone

from quant.core.paths import KLINE_DEEP_DIR as OUT  # noqa: E402

# 1995 起 (二次扩容): 补互联网泡沫熊市 = 又一类独立 regime (阴跌型,
# 与 COVID V 型 / 2022 加息熊不同类)。OpenD 的 56 票史深上限 2006, 不跨源拼接
# (接缝处复权口径不一致会造假 episode), 所以只有 Yahoo 票吃这段。
START, END = "1995-01-01", "2026-07-25"   # END 多一天含 7/24 全日, 与 pull_gate_history 同窗口

FINANCIALS = ["GS", "MS", "C", "WFC", "AXP", "SCHW", "BLK", "V", "MA", "COF"]
ENERGY = ["COP", "SLB", "OXY", "EOG", "HAL", "DVN", "MPC", "PSX"]
HEALTH = ["JNJ", "LLY", "ABBV", "BMY", "GILD", "AMGN", "MRNA", "BIIB", "CVS",
          "REGN", "VRTX", "ISRG"]
INDUSTRIAL = ["DE", "HON", "UPS", "FDX", "LMT", "RTX", "MMM", "EMR", "ETN", "URI"]
CONSUMER = ["MCD", "SBUX", "TGT", "COST", "HD", "LOW", "PG", "PEP", "LULU",
            "GM", "DG", "DLTR", "ROST", "TJX", "EL"]
SOFTWARE_NET = ["ORCL", "CRM", "ADBE", "NFLX", "PYPL", "SHOP", "UBER", "ABNB",
                "SNOW", "PLTR", "CSCO", "IBM", "INTU", "NOW", "XYZ", "TWLO"]  # XYZ = 旧 SQ (Block)
MATERIALS = ["FCX", "NUE", "DOW", "LIN", "CLF", "AA"]
UTIL_REIT = ["NEE", "DUK", "SO", "AMT", "PLD", "O"]
TRAVEL = ["DAL", "UAL", "AAL", "LUV", "CCL", "RCL", "MAR", "BKNG"]
CHINA_ADR = ["BABA", "JD", "PDD", "BIDU"]
HOMEBUILD = ["DHI", "LEN", "PHM", "TOL"]
ETF_EXTRA = ["XLV", "XLI", "XLU", "XLY", "XLP", "XBI", "XHB", "XRT", "KRE", "GDX"]

UNIVERSE = (FINANCIALS + ENERGY + HEALTH + INDUSTRIAL + CONSUMER + SOFTWARE_NET
            + MATERIALS + UTIL_REIT + TRAVEL + CHINA_ADR + HOMEBUILD + ETF_EXTRA)

URL = ("https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
       "?period1={p1}&period2={p2}&interval=1d")
UA = {"User-Agent": "Mozilla/5.0 (research; personal quant backtest)"}


def _epoch(d: str) -> int:
    return int(datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())


def fetch(tk: str) -> list[dict]:
    req = urllib.request.Request(
        URL.format(sym=tk, p1=_epoch(START), p2=_epoch(END)), headers=UA)
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read().decode("utf-8", errors="replace"))
    result = (payload.get("chart") or {}).get("result") or []
    if not result:
        return []
    ts = result[0].get("timestamp") or []
    q = ((result[0].get("indicators") or {}).get("quote") or [{}])[0]
    rows = []
    for i, t in enumerate(ts):
        o, h, lo, c, v = (q.get(k, [None] * len(ts))[i]
                          for k in ("open", "high", "low", "close", "volume"))
        if c is None or o is None:
            continue
        day = time.strftime("%Y-%m-%d", time.gmtime(t))   # 美股开盘 13:30/14:30 UTC, gmtime 日期即交易日
        rows.append({
            "time": f"{day} 00:00:00",
            "open": round(o, 4), "high": round(h, 4),
            "low": round(lo, 4), "close": round(c, 4),
            "volume": int(v or 0),
            "turnover": None,   # Yahoo 无成交额; 消费端 close×volume 近似
        })
    return rows


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    manifest_path = OUT / "_sources.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    for p in OUT.glob("*.jsonl"):
        manifest.setdefault(p.stem, "opend")   # 既有 OpenD 票登记来源, 不覆盖其数据

    ok = fail = skip = 0
    for i, tk in enumerate(UNIVERSE, 1):
        path = OUT / f"{tk}.jsonl"
        if path.exists():
            skip += 1     # OpenD 票或已拉过的票, 都不动
            continue
        try:
            rows = fetch(tk)
        except Exception as e:                                   # noqa: BLE001
            print(f"[{i}/{len(UNIVERSE)}] {tk}: ERROR {type(e).__name__}: {e}", flush=True)
            fail += 1
            time.sleep(1.0)
            continue
        if len(rows) < 300:
            print(f"[{i}/{len(UNIVERSE)}] {tk}: only {len(rows)} bars — skip", flush=True)
            fail += 1
            time.sleep(1.0)
            continue
        path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                        encoding="utf-8")
        manifest[tk] = "yahoo"
        print(f"[{i}/{len(UNIVERSE)}] {tk}: {len(rows)} bars "
              f"{rows[0]['time'][:10]} → {rows[-1]['time'][:10]}", flush=True)
        ok += 1
        time.sleep(0.8)   # 礼貌限速

    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                             encoding="utf-8")
    print(f"\ndone: {ok} pulled, {skip} existing, {fail} failed → {OUT}")
    return 0 if ok or skip else 1


if __name__ == "__main__":
    raise SystemExit(main())
