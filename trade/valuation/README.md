# Private valuation workspace

This directory holds the valuation layer's manual inputs and prediction ledgers. Runtime files (`inputs.json`, `valuations.jsonl`, `calibration.jsonl` and the optional response files) are ignored by Git: they name the user's actual universe and record dated estimates about it.

Canonical runtime paths (constants in `quant/core/paths.py`):

- `inputs.json` — the manual model assumptions. One entry per ticker: a business-model `archetype` plus the `rationale` for that classification, with optional `overrides`, `news_overlay` and explicitly sourced `transmissions`. Archetype defaults live in `quant/valuation/archetypes.py`.
- `valuations.jsonl` — append-only prediction ledger. One line per valuation run, carrying the full input snapshot so the estimate stays auditable after the inputs move.
- `response_events.jsonl` — optional owner-produced, dated event surprises and mature split-adjusted price outcomes; schema example below.
- `response_queries.json` — optional current conditional feature scenarios, each with source, known_at and fixed valid_until.
- `calibration.jsonl` — append-only reconciliation/backtest output: each predicted quantity against what later happened.

Create a private file by copying the matching `*.example.*` file and removing `.example`, then replace every synthetic value; ask for the missing facts instead of guessing them. For a missing optional input, the matching `*.example.*` file documents the schema; replace every synthetic value with reviewed evidence before creating a runtime file. Missing facts remain explicit gaps.

## Boundaries

- The valuation layer is a judgement layer with **zero authorization effect**. It never sizes a position, never unlocks a zero cap and is never imported by sizing, entry or exit code. Undervalued is not an entry signal and overvalued is not an exit signal.
- Machine-owned fields (fundamentals, balance-sheet aggregates, fetch timestamps) belong to the sync/data modules. The hand-written region above is the only part a person edits.
- News reaches the model only as a structured overlay contract, never as prose, and only from a source the user has authorized.
- Cached fundamentals live under `market/fundamentals/` and stay out of Git. Current provider data can be fetched again, but prior capture vintages cannot be reconstructed if discarded; retain the snapshots archive.

## Conditional valuation inputs

The executable contract and limitations are documented in `quant/valuation/README.md`.
`response_events.example.jsonl` and `response_queries.example.json` are synthetic
schemas, not enough observations to fit a model. Remove synthetic data before use.
Event producers must verify the trading-session count, split adjustment and
pre-anchor availability from original sources. The consumer validates declared
provenance and time order; it does not reconstruct the underlying market tape.

Missing bridge fields require the source balance sheet; an omitted broker field
is not evidence of a zero balance. Current and historical consensus require an
explicit horizon, earnings basis and availability/revision history. Do not add
these labels to an unverified broker multiple merely to enable a calculation.
The existing archive alone cannot establish historical forecasting accuracy.

Optional rate_exposure in inputs.json declares dated, finite-year repricing
assets/debt and pass-through assumptions; its synthetic shape is included in
inputs.example.json. It is a conditional earnings channel, not a policy-to-price
rule. response_queries.json must identify ticker, feature_schema and
anchor_convention in cohort, and may supply a sourced spot with price/as_of/source.
Keep its price anchor consistent with the training cohort.

The event_data builder reconstructs close-to-close horizons from an explicit
event JSON array and split-adjusted daily prices, using the canonical exchange
calendar. Its daily follow-through excludes the initial announcement jump.
Original event expectations and vendor availability records are still required.

sec_mapping.example.json illustrates reviewed issuer, period and financial-field
mapping. It uses fictional concepts and cannot fetch real data. Prepared SEC
supplements remain detached research inputs until explicitly placed in a frozen
valuation package; they do not overwrite source caches. Broker refreshes retain
capture vintages under the fundamentals cache's snapshots directory.
