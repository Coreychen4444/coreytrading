# Trading discipline

When a user expresses trade intent, establish three things before discussing execution:

1. What current structure/regime authorizes participation?
2. What price action invalidates the trade?
3. What binding risk limit and whole-unit size apply?

Flag perfect-price anchoring, chasing above the card, averaging down without a new authorization event, revenge trading, event-day improvisation and attempts to override a stop with narrative conviction.

Use `quant/decision/entry_guard.py` for entry plans and `quant/decision/exit_guard.py` for exit/risk state. Read `trade/us_stock/strategy/README.md` before constructing user-specific private rules from its examples; the examples themselves are not active strategy. Mechanical results win over prose summaries. The operator makes the final decision and submits any order manually.
