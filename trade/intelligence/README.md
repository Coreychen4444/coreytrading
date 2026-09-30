# Optional market-intelligence data

This directory is the data home for two things: an optional, user-owned news workflow (AI supply-chain and company evidence) and the code-rendered **macro lens**. The public repository ships only this README; the news collection, publication and delivery procedures are not part of this repository. The macro lens publisher (`quant/journal/macro_lens_log.py`) is the only in-repo code that writes here, and it writes only its own `macro/` outputs.

## Layout (paths relative to this directory)

| Path | Contents |
|---|---|
| `macro/current.json` | The latest macro-lens reading (`schema: macro_lens/1`, `authority: none`), overwritten on each publication; validated by `quant/core/macro_lens.py` `validate_published` |
| `macro/ledger.jsonl` | One snapshot per as-of date, idempotent append; the reconcilable record of past readings |
| `macro/reports/YYYY-MM/YYYY-MM-DD.md` | Full text of each published reading |
| `macro/calendar.json` | Optional, independently verified release queue from the news workflow; the macro lens lists upcoming releases from it (a separate optional `trade/market/macro_event_calendar.json` carries official FOMC / CPI / payrolls dates). Missing means an empty calendar section, not an error |
| `sources/` | Optional news-workflow source texts, tables, locations, fetch times and versions; originals are kept |
| `reports/` | Optional fixed-cycle news reports and delivery copies; sent content is kept as is |
| `valuation/overlays.jsonl` | Optional structured news-to-valuation overlays (contract in `quant/valuation/overlays.py`) |

The cross-asset daily series behind the macro lens are cached under `trade/market/macro_daily/` (re-fetchable, ignored by Git). Runtime candidates, claims, last-run markers, locks and delivery receipts live in `var/intelligence/` (ignored by Git), not here.

## How trading code uses it

- `quant/core/paths.py` defines `INTELLIGENCE_DIR`, `MACRO_CURRENT_PATH`, `MACRO_LEDGER_PATH`, `MACRO_REPORTS_DIR` and `VALUATION_OVERLAYS_PATH`. `resolve_intelligence_reference` resolves references embedded in older records, including earlier data roots, without rewriting them; `quant/ops/intelligence_archive.py` exposes that resolution on the command line.
- The macro-lens task (`tasks/claude/macro-lens.md`) refreshes the daily series and publishes `macro/`; the briefing reads `--brief`, the weekly review reads `--week`, and frozen agent captures carry the compact prior (`macro_lens.prior_view`). Missing or invalid input is reported as "no valid macro prior", never fabricated.
- Domain roles read the news report archive as `[FYI]` context when it is configured.
- `quant/ops/intelligence_universe.py` is the only reverse exit: it prints the coverage names for the news workflow and nothing else.

Nothing here grants authorization. Macro readings and news never translate into gates, caps, share counts, exits or order permissions. Point-in-time work (features, labels, backtests) must keep publication time, first-available time, observation window and revision separate from the raw evidence; missing timestamps in older material cannot be back-filled to look precise.
