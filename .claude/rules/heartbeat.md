# Heartbeat rules

- Use `quant/ops/pipeline.py --mode heartbeat` as the canonical periodic sequence.
- Missing eligible cards may start detached analysis through `quant/ops/card_autopaint.py`; the stage must return immediately, and read-only runs must use its dry-run mode.
- Promote a held watchlist card before lot reconciliation so re-entry lots inherit the current analyzed card.
- Treat broker/state refresh, reconciliation and data freshness failures as first-class alerts.
- Preserve warnings from `quant/report/portfolio_brief.py` and `quant/decision/exit_guard.py`; do not weaken or paraphrase away binding constraints.
- The pipeline sends `fired_cards` and `alerts` immediately after the brief, before broker reminder and authorization-group effectors. Respect its `tg_pushed` receipt and do not duplicate an alert already delivered.
- Treat a completed close below zone 2 as stale immediately; upward breakouts retain their configured confirmation window. A one-session close move above `2 × ATR14` is volatility-stale when ATR is available.
- If live price is below the lower accumulation boundary on an authorized card, freeze BUY actions for that session while leaving sell/reduce actions available. Re-evaluate from the completed close rather than mutating authorization from an intraday snapshot.
- Never place, modify or cancel orders from a heartbeat.
- Market or company news is `[FYI]` and cannot silently change mechanical authorization.
- Never run close-dependent transitions on an incomplete market bar.
- Post-close mode evaluates exits against the old card before applying a regime transition, so a demotion cannot erase a breached stop before it is reported.
