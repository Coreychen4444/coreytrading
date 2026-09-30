# AI private-workspace bootstrap contract

`trade/` is not a public strategy library and is not intended to contain a ready-made portfolio. It is a machine-readable contract that tells an AI how to create a private trading workspace for one user. The workspace serves US equities and the existing US equity-options workflow; cross-asset macro material is observation and research input, never an additional trading market.

The public repository permits only two kinds of files below this directory:

- `README.md`: purpose, canonical paths, ownership and privacy rules.
- `*.example.*`: synthetic schemas that an AI may copy to a private runtime filename.

Everything generated from these files is private and ignored by Git.

## AI bootstrap procedure

When asked to initialize or repair the trading workspace, the AI must:

1. Read this file and every descendant `README.md` before writing data.
2. Select only the data domains and modules requested by the user.
3. Copy each required example by removing the `.example` segment from its name. For example, `positions.example.json` becomes `positions.json`.
4. Replace synthetic values only with information supplied by the user or obtained from an explicitly authorized source. Never infer holdings, account identifiers, risk limits or personal strategy.
5. Preserve the example's field names and data types unless repository code or a README defines a newer schema.
6. Keep generated files local. Never stage, commit, paste or summarize their private contents into public files, tests, logs or issues.
7. Validate JSON/JSONL syntax and run the relevant repository checks before using the workspace.

Do not copy every example mechanically. Empty or unknown private facts remain `null`, an empty collection, or an explicit review item until the user provides them.

## Directory map

Business data is organized directly by responsibility: there is no per-market wrapper directory and no placeholder for other markets. `quant/core/paths.py` is the single source of the canonical paths.

| Directory | Contents and retention |
|---|---|
| `portfolio/` | Account state, candidates, fills, NAV, cash flows, scores, journals and derived observations; not a rebuildable cache |
| `market/` | Prices, earnings and raw market observations; refreshable caches and non-rebuildable historical snapshots have different retention needs |
| `intelligence/` | Optional news sources and reports written by a user-owned collection workflow (trading code only reads them), plus the code-rendered macro-lens readings under `macro/` |
| `valuation/` | Manual valuation inputs, compact EPS packages and release records |
| `strategy/` | User-specific decision rules and option policies; the public repository holds schemas only |
| `knowledge/` | Private research cards and evidence |

The shared runtime lock lives at `var/portfolio/.state.lock` (override with `COREYTRADING_STATE_LOCK_PATH`), outside this data tree and outside Git. Ignore rules are not a backup strategy: confirm the writer, timing, versioning and retention of each runtime file separately. The child READMEs define the canonical runtime names expected by the code.
