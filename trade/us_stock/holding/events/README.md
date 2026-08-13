# Private event ledgers

Event files are append-only JSONL and are grouped into `YYYY-MM/` runtime directories. The AI creates a month directory only when it has an authorized event to record.

Canonical private names include:

- `YYYY-MM/trades.jsonl`: broker fills, closes, expiries and assignments.
- `YYYY-MM/decisions.jsonl`: user/AI decisions and their evidence snapshot.
- `nav_history.jsonl`: account-level NAV and buying-power snapshots.

Copy the matching example, remove `.example`, and append one JSON object per line. Never rewrite prior events merely to improve prose; add a correction event with provenance.
