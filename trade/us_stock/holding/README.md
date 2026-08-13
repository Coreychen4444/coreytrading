# Private holding workspace

This subtree is the runtime boundary for account-specific information. It may contain holdings, watchlists, lots, orders, fills, NAV history, decisions, analyses and journals after an AI initializes it locally.

The AI must keep broker facts separate from agent-authored interpretation:

- `state/`: current machine-readable truth and plans.
- `events/`: append-only trading, decision and NAV ledgers.
- `analysis/`: derived private reports; never a source of broker truth.
- `journals/`: narrative review and scoring.

Before creating a runtime file, read the child README and copy the corresponding example without `.example`. Never put real account data back into an example file.
