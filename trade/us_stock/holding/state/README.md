# Runtime state contract

These JSON documents are mutable private state. To bootstrap one, copy its example to the canonical filename and then replace synthetic fields from an authorized source.

| Example | Private runtime file | Owner |
| --- | --- | --- |
| `positions.example.json` | `positions.json` | broker sync owns `broker`; AI/user own thesis, lots and actions |
| `watchlist.example.json` | `watchlist.json` | AI/user, with market-derived fields refreshed by code |
| `exit_guards.example.json` | `exit_guards.json` | `quant/decision/exit_guard.py` |
| `option_plans.example.json` | `option_plans.json` | entry/option planning workflow |

AI rules:

1. Never overwrite agent-authored blocks during broker refresh.
2. Never convert an unknown value into zero; use `null` or request review.
3. Use ISO-8601 timestamps with an offset or `Z`.
4. Validate position/watchlist documents through `quant.core.schema` after material edits.
5. Treat account IDs, balances, quantities, costs and active tickers as private.
