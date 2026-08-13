# AI private-workspace bootstrap contract

`trade/` is not a public strategy library and is not intended to contain a ready-made portfolio. It is a machine-readable contract that tells an AI how to create a private trading workspace for one user.

The public repository permits only two kinds of files below this directory:

- `README.md`: purpose, canonical paths, ownership and privacy rules.
- `*.example.*`: synthetic schemas that an AI may copy to a private runtime filename.

Everything generated from these files is private and ignored by Git.

## AI bootstrap procedure

When asked to initialize or repair the trading workspace, the AI must:

1. Read this file and every descendant `README.md` before writing data.
2. Select only the asset classes and modules requested by the user.
3. Copy each required example by removing the `.example` segment from its name. For example, `positions.example.json` becomes `positions.json`.
4. Replace synthetic values only with information supplied by the user or obtained from an explicitly authorized source. Never infer holdings, account identifiers, risk limits or personal strategy.
5. Preserve the example's field names and data types unless repository code or a README defines a newer schema.
6. Keep generated files local. Never stage, commit, paste or summarize their private contents into public files, tests, logs or issues.
7. Validate JSON/JSONL syntax and run the relevant repository checks before using the workspace.

Do not copy every example mechanically. Empty or unknown private facts remain `null`, an empty collection, or an explicit review item until the user provides them.

## Directory map

- `us_stock/holding/`: account state, events, analyses and journals.
- `us_stock/market/`: generated or licensed market-data caches.
- `us_stock/knowledge/`: private research cards and evidence.
- `us_stock/strategy/`: user-specific decision rules and option policies.
- `polymarket/`: optional prediction-market state and rules.

The child READMEs define the canonical runtime names expected by the code.
