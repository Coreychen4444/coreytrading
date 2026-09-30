# Private valuation workspace

This directory holds the valuation layer's manual inputs and research packages. Runtime files (`inputs.json`, `eps_models.json`, `company_models.json`, the release files and the optional response files) are ignored by Git: they name the user's actual universe and record dated estimates about it.

Canonical runtime paths (constants in `quant/core/paths.py`):

- `inputs.json` — the manual model assumptions. One entry per ticker: a business-model `archetype` plus the `rationale` for that classification, with optional `overrides`, `news_overlay` and explicitly sourced `transmissions`. Archetype defaults live in `quant/valuation/archetypes.py`.
- `eps_models.json` — compact EPS packages: eight future quarters of three-scenario income statements per ticker, the EPS source of the research-grade twelve-month target. Rolled mechanically after each report and reviewed against primary issuer disclosures (`quant/valuation/eps_model.py`); shape in `eps_models.example.json`.
- `response_events.jsonl` — optional owner-produced, dated event surprises and mature split-adjusted price outcomes; schema example below.
- `response_queries.json` — optional current conditional feature scenarios, each with source, known_at and fixed valid_until.
- `company_models.json` — explicit company research packets keyed by ticker;
  source-backed actual statements and separately labelled analyst scenarios.
  The input contract and refusal to fall back to legacy prices are documented in
  `quant/valuation/README.md`. This catalog is private and grants no publication
  or trading permission. Sources and immutable trial receipts remain under
  `var/valuation-daily/rebuild/`; refreshing broker data never rewrites this file.

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

`releases.json` is the persistent release journal plus immutable, content-addressed
candidate/review/evidence documents. It is business audit history, not a disposable
cache. The owner is `quant/ops/valuation_release.py`; all applied updates use its
dedicated lock and atomic document replacement. Revocation appends history and
preserves prior artifacts. Never truncate this file to recover a failed release.
The controller is wired to daily reports. No real company release has been
approved or installed; raw computation remains diagnostic and withheld.

`release_policy.json` is a separate host-owned rollout/identity input; missing
means every output is disabled. `release_policy.example.json` is the empty shape.
`enabled_outputs` maps each ticker to a list drawn from `fair_value` and
`market_target_12m`. `verified_reviews` contains exact `review_hash`, `ticker`,
`kind`, `reviewer_id`, `identity_basis`, `verified_at` and `valid_until`. Only
independently produced reviews whose identity was actually verified belong here.
This file cannot create statistical evidence or approve a candidate on its own.
No trust is read from company packets or submitted request documents.

Use `python3 quant/ops/valuation_release.py status` to check current publication;
`--tickers <T>` limits scope. It measures company input and implementation/
requirements hashes and checks freshness, reconciliations, revocations, review
trust and version. A broken/expired company does not block another issuer. The
broad implementation fingerprint includes core modules; even a non-numerical
change requires re-review before an old candidate can be served. No previous
release is used when the newest one fails.

`submit --request <json>` previews an exact request containing only `event`,
`artifacts` and `expected_head`; `--apply` uses the existing locked CAS writer.
Use the same event ID and exact content to reconcile uncertain submissions.
Promotions require enabled host scope and an exact verified review. Revocation
does not depend on a still-valid company input or an enabled rollout.

Daily `publication.json` is the selection at report generation, separately from
`valuation.json` diagnostic calculations. `released_counts` in the job records
actual accepted outputs. Archives preserve what was shown at that time; use
`status` to check current validity rather than treating an old Markdown file as a
live release. There are no actual acceptance records in the example or tests.
Each candidate output must declare `unit=USD_per_common_share` and a nonempty
`share_basis`; aggregate operating/enterprise values cannot pass the share-price
renderer merely by carrying USD currency. The independent source audit still
has to verify the actual denominator, split basis and complete equity claims.
