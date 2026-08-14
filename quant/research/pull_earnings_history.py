"""为 earnings_gate_backtest 拉财报事件数据的一次性取数脚本。

对 klines_deep universe 逐票调 openapi `get_financials_earnings_price_history`
(20 期/票, 含 pub_trading_day_str / pub_time_str / predict_vola_ratio_newest =
vendor 期权隐含 implied move ~2021 起), 原始 JSON 落
`trade/us_stock/market/earnings_deep/<TICKER>.json` (gitignore, 回测专用)。

端点配额、授权与频率限制必须写入 private preregistration；脚本默认限速请求。
幂等: 已存在且 --force 未给 → 跳过。ETF/无财报票端点返回空 → 记 skip 不报错。

用法: .venv/moomoo/bin/python3 quant/research/pull_earnings_history.py [--force]
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from quant.core.paths import KLINE_DEEP_DIR, MARKET_DIR, ROOT  # noqa: E402

EARNINGS_DEEP_DIR = MARKET_DIR / "earnings_deep"
SCRIPT = (ROOT / ".claude/skills/openapi/scripts/quote/"
          "get_financials_earnings_price_history.py")
PYBIN = ROOT / ".venv/moomoo/bin/python3"

# 指数/行业 ETF 无财报, 直接跳过不浪费请求
ETF_SKIP = {"SPY", "QQQ", "SMH", "IWM", "XLE", "XLF", "XLV", "XLI", "XLP",
            "XLY", "XLU", "XLB", "XLRE", "XLK", "XLC", "DIA", "KWEB", "FXI"}


def main() -> int:
    force = "--force" in sys.argv
    EARNINGS_DEEP_DIR.mkdir(parents=True, exist_ok=True)
    tickers = sorted(p.stem for p in KLINE_DEEP_DIR.glob("*.jsonl"))
    sources: dict = {}
    ok = skipped = empty = failed = 0
    for t in tickers:
        out_path = EARNINGS_DEEP_DIR / f"{t}.json"
        if t in ETF_SKIP:
            sources[t] = "etf_skip"
            skipped += 1
            continue
        if out_path.exists() and not force:
            sources[t] = "cached"
            ok += 1
            continue
        try:
            r = subprocess.run(
                [str(PYBIN), str(SCRIPT), f"US.{t}", "--json"],
                capture_output=True, text=True, timeout=60,
)
            payload = json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else None
        except Exception:
            payload = None
        if payload:
            out_path.write_text(json.dumps(payload, ensure_ascii=False))
            sources[t] = "opend"
            ok += 1
        elif payload is not None:
            sources[t] = "empty"
            empty += 1
        else:
            sources[t] = "failed"
            failed += 1
        time.sleep(0.8)
    (EARNINGS_DEEP_DIR / "_sources.json").write_text(
        json.dumps(sources, ensure_ascii=False, indent=1))
    print(f"earnings_deep: ok={ok} skipped_etf={skipped} empty={empty} failed={failed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
