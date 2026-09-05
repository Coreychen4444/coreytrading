---
name: trading-analysis
description: Analyze a listed instrument using current price/technical context, dated company and macro sources, deterministic entry/exit guards, and explicit risk review. Use for stock analysis, long/short comparison, trade planning, or zone refreshes.
---

# Trading analysis

Use a six-part workflow:

1. **Data quality**— establish symbol, session, timestamp, source and whether bars are complete.
2. **Technical state**— inspect trend, swings, ATR, volume profile, zones, gate and regime.
3. **Information context**— review the operator's authorized intelligence archive if one is configured, plus any
   material the user supplies directly; label uncertain claims. This workflow does not fetch news, social posts or
   macro data itself, and an empty archive is reported as "no intelligence input" rather than filled by searching.
4. **Bull/bear cases**— state falsifiable evidence on both sides without converting conviction into authorization.
5. **Mechanical plan**— run `quant/decision/entry_guard.py` or `quant/decision/exit_guard.py` with the locally configured state.
6. **Risk review**— preserve the binding cap, whole-unit result, stop, event adjustment and infrastructure warnings.

## Data sources

Prefer, in order:

- Moomoo/OpenD scripts under `.claude/skills/openapi/scripts/` when a local instance is configured.
- Local broker/state snapshots under `trade/us_stock/holding/state/`.
- Local completed-bar caches under `trade/us_stock/market/`.
- The operator's authorized intelligence archive for dated facts, read-only; material the user pastes in is fine.

Never manufacture a live quote, reuse a stale article as current data, or expose account fields in an analysis intended for publication.

## Zone writes

Use `.claude/skills/trading-analysis/scripts/update_position_zones.py` for controlled state updates. Preserve broker, lots and pending-order blocks. Every map requires an anchor date, methodology, update trigger and invalidation level. Writes to local live state require explicit user authorization; analysis alone is read-only.

Assigning a valuation archetype is part of painting a card. After the first zone write for a new instrument, run `python3 quant/valuation/archetypes.py set <TICKER> --archetype <name> --rationale "<one sentence>"`, then refresh the valuation mirror with `quant/valuation/valuate.py --tickers <TICKER> --refresh --write` and `quant/sync/valuation_sync.py --tickers <TICKER>`. A missing archetype is reported (a writer warning and the brief's `INFRA:card_missing_archetype`) and never blocks a zone, gate or cap; the valuation layer has zero authorization effect.

Scheduled repaints and missing-card detection go through the domain-agent controller described in `agents/README.md`: the host freezes evidence, the `technical` role proposes, another provider reviews, and the same writer applies the proposal under a revision check. Only `submission.status == "applied"` means a card was written; isolated workers never run the data refresh, write, valuation or notification steps of this skill themselves.

## Output

Separate facts, hypotheses, mechanical verdicts and operator decisions. State clearly that the live pipeline does not place orders.
