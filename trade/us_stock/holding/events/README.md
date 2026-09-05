# Private event ledgers

Event files are append-only JSONL and are grouped into `YYYY-MM/` runtime directories. The AI creates a month directory only when it has an authorized event to record.

Canonical private names include:

- `YYYY-MM/trades.jsonl`: broker fills, closes, expiries and assignments.
- `YYYY-MM/decisions.jsonl`: user/AI decisions and their evidence snapshot.
- `nav_history.jsonl`: account-level NAV and buying-power snapshots.
- `cash_flows.jsonl`: external cash flows only (bank transfer deposits and withdrawals) synchronized from broker fund flows by `quant/data/cash_flow_sync.py`, with the USD equivalent and its basis; `cash_flows.sync.json` records how far the ledger has been synchronized. NAV change reports net these flows out, because a deposit or withdrawal is not performance.

Copy the matching example, remove `.example`, and append one JSON object per line. Never rewrite prior events merely to improve prose; add a correction event with provenance.
