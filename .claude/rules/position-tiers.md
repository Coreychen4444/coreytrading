# Position and risk tiers

- Structural exemptions and managed trading positions are separate concepts; derive the role from state rather than conversational labels.
- `current_regime.cap_pct_nav` is the authorization ceiling. It is not a target and cannot create buying power.
- Actual size is the minimum of regime cap, campaign/gate budget, account buying power, gross exposure, shock budget, stop-risk limit, event adjustment and whole-unit feasibility.
- A cap of zero is a veto. Do not create an undocumented exception.
- Options use delta/notional and premium-risk constraints where applicable; contract price alone is not the exposure.
- Lots and broker quantity must reconcile independently of whether a trade needs a narrative annotation.
- When a held stock has add capacity, render its add ladder through the same `entry_guard.build_ladder` and `sizing.share_ladder` functions used for candidates; do not reduce the plan to one unstructured share count.

The executable formulas live in `quant/core/sizing.py` and `quant/core/book.py`; update tests when they change.
