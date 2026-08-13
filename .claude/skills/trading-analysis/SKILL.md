---
name: trading-analysis
description: Analyze a listed instrument using current price/technical context, dated company and macro sources, deterministic entry/exit guards, and explicit risk review. Use for stock analysis, long/short comparison, trade planning, or zone refreshes.
---

# Trading analysis

Use a six-part workflow:

1. **Data quality**— establish symbol, session, timestamp, source and whether bars are complete.
2. **Technical state**— inspect trend, swings, ATR, volume profile, zones, gate and regime.
3. **Information context**— use primary company sources and dated macro/news sources; label uncertain claims.
4. **Bull/bear cases**— state falsifiable evidence on both sides without converting conviction into authorization.
5. **Mechanical plan**— run `quant/decision/entry_guard.py` or `quant/decision/exit_guard.py` with the locally configured state.
6. **Risk review**— preserve the binding cap, whole-unit result, stop, event adjustment and infrastructure warnings.

## Data sources

Prefer, in order:

- Moomoo/OpenD scripts under `.claude/skills/openapi/scripts/` when a local instance is configured.
- Local broker/state snapshots under `trade/us_stock/holding/state/`.
- Local completed-bar caches under `trade/us_stock/market/`.
- Reputable external sources for dated facts.

Never manufacture a live quote, reuse a stale article as current data, or expose account fields in an analysis intended for publication.

## Zone writes

Use `.claude/skills/trading-analysis/scripts/update_position_zones.py` for controlled state updates. Preserve broker, lots and pending-order blocks. Every map requires an anchor date, methodology, update trigger and invalidation level. Writes to local live state require explicit user authorization; analysis alone is read-only.

## Output

Separate facts, hypotheses, mechanical verdicts and operator decisions. State clearly that the live pipeline does not place orders.
