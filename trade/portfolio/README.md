# Private portfolio workspace

This subtree is the runtime boundary for account-specific information: holdings, candidates, lots, orders, fills, NAV, external cash flows, decisions, scores, analyses, journals and derived observations after an AI initializes it locally. It is not a scratch directory and not a copy of any public knowledge base.

| Location | Contents and ownership |
|---|---|
| `state/positions.json` | Single source of truth for holdings; broker sync owns `broker`, controlled writers own the agent cards |
| `state/watchlist.json` | Candidate cards; membership follows the broker-side candidate group |
| `state/exit_guards.json`, `state/option_plans.json` | Mechanical exit state and option plans, owned by the corresponding deterministic modules |
| `events/YYYY-MM/trades.jsonl` | Append-only fill ledger with its authorization and scoring stamps |
| `events/nav_history.jsonl`, `events/cash_flows.jsonl` | NAV and external cash flows; both are needed for correct performance accounting |
| `events/cash_flows.sync.json` | Synchronization progress; not the cash-flow facts themselves and never deleted casually |
| `events/decisions.jsonl` | Decision worksheets written by the journal layer |
| `journals/` | Scoring ledger and narrative review; structured records and prose do not replace each other |
| `analysis/` | Derived private analyses; never a source of broker truth and never an authorization |
| `observations/` | Derived judgements produced by mechanical modules (catalysts, structure drift), distinct from the fact ledgers in `events/` and the raw inputs in `trade/market/` |

Before creating a runtime file, read the child README and copy the corresponding example without `.example`. Never put real account data back into an example file.

All production writers share the lock at `var/portfolio/.state.lock` through `quant/core/io_json.py`; the lock is outside Git. Committed position history is read across the current path and the earlier paths listed in `quant/core/paths.py`, so a pure directory move creates no new zone changes.
