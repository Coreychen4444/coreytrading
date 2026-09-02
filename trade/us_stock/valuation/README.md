# Private valuation workspace

This directory holds the valuation layer's manual inputs and its prediction ledgers. Runtime files are ignored by Git: they name the user's actual universe and record dated estimates about it.

Canonical runtime paths (constants in `quant/core/paths.py`):

- `inputs.json` — the only hand-written file. One entry per ticker: a business-model `archetype` plus the `rationale` for that classification, with optional `overrides` and `news_overlay`. Archetype defaults live in `quant/valuation/archetypes.py`.
- `valuations.jsonl` — append-only prediction ledger. One line per valuation run, carrying the full input snapshot so the estimate stays auditable after the inputs move.
- `calibration.jsonl` — append-only reconciliation/backtest output: each predicted quantity against what later happened.

Create a private file by copying the matching `*.example.*` file and removing `.example`, then replace every synthetic value. Ask for the missing facts instead of guessing them.

## Boundaries

- The valuation layer is a judgement layer with **zero authorization effect**. It never sizes a position, never unlocks a zero cap and is never imported by sizing, entry or exit code. Undervalued is not an entry signal and overvalued is not an exit signal.
- Machine-owned fields (fundamentals, balance-sheet aggregates, fetch timestamps) belong to the sync/data modules. The hand-written region above is the only part a person edits.
- News reaches the model only as a structured overlay contract, never as prose, and only from a source the user has authorized.
- Cached fundamentals live under `market/fundamentals/` and stay out of Git; they can always be refetched.
