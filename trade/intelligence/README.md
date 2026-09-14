# Optional market-intelligence data

This directory is the data home for an optional, user-owned intelligence workflow (AI supply-chain, company, macro and cross-asset evidence). The public repository ships only this README and the synthetic example under `examples/`; the collection, publication and delivery procedures are not part of this repository, and trading code never writes here.

## Layout (paths relative to this directory)

| Path | Contents |
|---|---|
| `sources/` | Source texts, tables, locations, fetch times and versions; originals are kept |
| `macro/snapshots/` | Market-evidence packages with observation time, source and quality flags |
| `macro/current.json` | The latest successfully published macro judgement; not a historical fact table |
| `macro/history/` | Versioned judgement history, append-only |
| `macro/events/` | Pre-event plans and post-event results; immutable once published. A same-named `<event-id>-playbook.json` is the authoring format of a plan (`quant/core/macro_playbook.py`) and is frozen with it |
| `macro/reports/` | Full event reports |
| `reports/` | Fixed-cycle reports and delivery copies; sent content is kept as is |
| `valuation/overlays.jsonl` | Structured news-to-valuation overlays (contract in `quant/valuation/overlays.py`) |
| `schema/`, `examples/` | The macro-state schema the publisher validates against, and synthetic examples (macro state, event playbook) |

Runtime candidates, claims, last-run markers, the macro lock and delivery receipts live in `var/intelligence/` (ignored by Git), not here.

## How trading code uses it

- `quant/core/paths.py` defines `INTELLIGENCE_DIR`, `MACRO_CURRENT_PATH`, `MACRO_SCHEMA_PATH` and `VALUATION_OVERLAYS_PATH`. `resolve_intelligence_reference` resolves references embedded in older records, including earlier data roots, without rewriting them; `quant/ops/intelligence_archive.py` exposes that resolution on the command line.
- `quant/ops/macro_state.py` validates a macro document against the schema; its publish paths are inert when the workspace is absent. A pre-event plan must be compiled from its event playbook; `quant/report/macro_playbook_report.py` validates, renders and compiles playbooks (shape: `examples/event-playbook.example.json`).
- Domain roles read `macro/current.json` and the report archive as `[FYI]` context. Missing or invalid input is reported as "no macro prior", never fabricated.
- `quant/ops/intelligence_universe.py` is the only reverse exit: it prints the coverage names for the collection workflow and nothing else.

Nothing here grants authorization. Macro and event judgements never translate into gates, caps, share counts, exits or order permissions. Point-in-time work (features, labels, backtests) must keep publication time, first-available time, observation window and revision separate from the raw evidence; missing timestamps in older material cannot be back-filled to look precise.
