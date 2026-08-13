# Private/generated market data

This directory holds generated caches and provider-derived datasets. They may be licensed, large, time-sensitive or reveal the user's active universe, so runtime files are ignored by Git.

Common canonical runtime paths are:

- `kline/{TICKER}.jsonl`
- `iv_history/{TICKER}.jsonl`
- `earnings_calendar.json`
- `catalysts.jsonl`
- `structure_drift.jsonl`

The examples show record shape only. The AI must create subdirectories on demand, verify provider authorization, record timestamps/source, and never treat synthetic values as market data.
