# Research Layer

[中文版](README_CN.md)

`quant/research/` is the public, offline validation boundary. It contains reusable mechanics, synthetic examples, and instructions for constructing a private study. It must not contain an operator's datasets, symbols, holdings, fills, account values, working notes, conversations, conclusions, rulings, backlog, or historical attribution.

Live modules may never import this package. A study can inspect production functions to validate them; the dependency direction never reverses.

## Public contents

- `calibration.py` is a generic, read-only calibration utility. It reads the ignored private decision ledger only when explicitly executed.
- `study_example.py` demonstrates a deterministic paired comparison using synthetic records.
- `study.example.md` is the preregistration template an AI should copy into a private workspace.
- `*_backtest.py` and analysis modules preserve reusable research mechanics. Their comments describe invariants and protocols, not an operator's study history.
- `pull_*_history.py` modules accept an explicit private universe or discover an ignored cache; they do not ship a private symbol list.
- `tests/research/` contains synthetic tests for the public mechanics.

No populated study belongs here. Public examples are schemas and mechanics, not trading evidence or recommendations.

## Directory map

| Group | Purpose | Private boundary |
|---|---|---|
| `gate_*`, `fastlane_*`, `ladder_*`, `repaint_*` | Structure, entry, confirmation, and repaint comparisons | Universe, window, outputs, and rulings stay ignored |
| `exit_*`, `longcall_*`, `options_*`, `synthetic_longcall_*` | Exit and option-policy replay | Contracts, fills, marks, exclusions, and account state stay ignored |
| `earnings_*`, `risk_*`, `shock_*`, `poc_*` | Event, risk, and calibration studies | Provider payloads and populated results stay ignored |
| `indicator_*`, `volume_*`, `interaction_*`, `zone_*`, `dof_audit.py` | Offline diagnostics and audits | Runtime observations and reports stay ignored |
| `pull_*_history.py` | Optional research-only data acquisition | Requires explicit authorization and a private manifest |

Run a module's `--help` before using it. Importing a research module must not start a study, call a provider, or write files.

## Private input recipes

Create these only below the ignored study workspace. Replace placeholders with user-provided facts; never copy the examples into Git as populated data.

Universe manifest used by the deep-bar pullers:

```json
["SYNTH_A", "SYNTH_B"]
```

Long-call replay manifest:

```json
[
  {
    "ticker": "SYNTH_A",
    "entry_date": "<YYYY-MM-DD>",
    "option_code": "<PRIVATE_OCC_SYMBOL>"
  }
]
```

Exit-rules replay config:

```json
{
  "start_date": "<YYYY-MM-DD>",
  "excluded_instruments": [],
  "bad_klines": []
}
```

HVN incumbent regression receipt (`--baseline`):

```json
{
  "n_episodes": 0,
  "months": 0,
  "kish_n": 0.0,
  "A_primary_T60": {},
  "cells": {
    "<INCUMBENT_CELL>": [0.0, 0.0, 0.0, 0.0]
  }
}
```

Each cell array is `[fire_rate, diff_mean, ci_low, ci_high]`. Populate the receipt only in the ignored workspace from the locked incumbent run.

Option-overlay config:

```json
{
  "exclude_symbols": []
}
```

Entry-cap config:

```json
{
  "merge_spreads": []
}
```

Date windows are CLI inputs for pullers and windowed backtests; record the same values in the private preregistration.

## AI bootstrap procedure

1. Read `AGENTS.md`, this file, and `trade/knowledge/research/README.md` before creating anything.
2. Restate the research question as one falsifiable hypothesis about one production behavior.
3. Copy `study.example.md` to the ignored private workspace and remove `.example` from the private filename.
4. Ask for every unresolved choice: population, observation unit, metric, horizon, threshold, missing-data behavior, fallback branch, and promotion rule. Do not infer them from public examples.
5. Build mechanics against synthetic and adversarial records first. Tests must prove construction, boundary, missing-data, and decision branches before private data is read.
6. Record an immutable protocol receipt before inspecting final results. Changing a locked choice creates a new study version rather than silently rewriting the old one.
7. Run the authorized private dataset, preserve failures and limitations, and keep every generated input, report, chart, ruling, and note outside Git.
8. Promote a result only through a separately reviewed production change with production-layer tests. A research result never grants live authorization by itself.

Before running existing mechanics, also verify that no source literal encodes a private ticker, option contract, account value, sample result, or historical annotation. If a script needs one, add an explicit private input instead of another constant.

Recommended ignored layout:

```text
trade/knowledge/research/<study_slug>/
├── preregistration.md
├── data_manifest.json
├── inputs/
├── outputs/
└── ruling.md
```

Create only the files needed by the requested study. Missing private facts remain missing.

## Required study contract

Every private study must state:

- hypothesis and null;
- unit of observation and inclusion/exclusion rules;
- source, license, freshness, session completeness, and survivorship limits;
- production predicate or formula being tested;
- primary metric, horizon, threshold, uncertainty method, and minimum sample rule;
- multiple-comparison policy and sensitivity analyses;
- missing-data, error, and insufficient-evidence branches;
- exact decision table for support, no support, and inconclusive;
- known limitations, counterevidence, and conditions for rerun;
- whether the result is informational or eligible for a production proposal.

## Public/private decision rule

A file may be public only when all of the following are true:

- it is reusable across operators;
- it contains no real account or operator-specific observation;
- examples are wholly synthetic;
- comments explain invariant behavior rather than internal history;
- importing it cannot read private state or trigger a study;
- it has synthetic tests or an explicit coverage declaration.

Otherwise, keep it in the ignored private workspace.

## Common questions

### Can a backtest script be committed?

Only if it is reusable, contains no private universe or result, defaults to synthetic/explicit input, performs no work on import, and has synthetic tests. One-off studies stay private.

### May an existing script read ignored canonical data paths?

Yes, but only when explicitly executed after the private protocol is locked. Importing the module must remain side-effect free, and generated reports must stay under ignored output paths.

### What should an AI do when a script still contains a hardcoded private sample?

Replace that sample with a required CLI argument or ignored JSON manifest, add a synthetic boundary test, and document the schema here. Do not preserve the value in a comment or fixture.

### Where do preregistration and rulings live?

In the ignored study directory. The public repository ships the template and method, not an operator's research history.

### Can public examples contain familiar market symbols?

Use neutral synthetic identifiers. A real symbol may reveal a private universe or be mistaken for a recommendation.

### What happens when data is missing or the sample is too small?

Return an explicit insufficient-evidence result. Never weaken a threshold or expand the sample after seeing outcomes.

### How does a finding reach live code?

Through a separate reviewed implementation, production tests, documentation updates, and the normal release gate. Research code remains offline.
