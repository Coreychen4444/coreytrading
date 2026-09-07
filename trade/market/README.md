# Private/generated market data

This directory holds generated caches and provider-derived datasets. They may be licensed, large, time-sensitive or reveal the user's active universe, so runtime files are ignored by Git.

Common canonical runtime paths (constants in `quant/core/paths.py`):

- `klines/{TICKER}.jsonl`: daily bars, refreshable from the provider.
- `klines_deep/`, `option_klines/`, `earnings_deep/`: multi-year bars, option bars and historical earnings events used by research.
- `iv_history/{TICKER}.jsonl`: implied-volatility observations; these cannot be re-pulled once missed.
- `earnings_calendar.json`: forward earnings calendar with the per-ticker reaction-day size adjustment.
- `fundamentals/{TICKER}.json` plus `fundamentals/snapshots/`: provider financial statements and their capture vintages.
- `premium/`: cross-market premium tracking.

Refreshable caches and non-rebuildable snapshots have different retention needs; never clear the directory as a whole. Derived judgements (catalysts, structure drift) live under `trade/portfolio/observations/`, not here.

The examples show record shape only. The AI must create subdirectories on demand, verify provider authorization, record timestamps/source, and never treat synthetic values as market data.
