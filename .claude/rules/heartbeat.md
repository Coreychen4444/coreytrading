# Heartbeat rules

- Use `quant/ops/pipeline.py --mode heartbeat` as the canonical periodic sequence.
- Every scheduled trading prompt starts with `quant/ops/agent_jobs.py due --kind <kind>`; a non-session day or an out-of-window wake-up stops before any collection, and a calendar error is reported rather than guessed around.
- The `agents` stage (domain research and reviewed card updates) runs after the brief and the regime transition and before reminders and the authorization group. Only a receipt with `status == "applied"` is a written card; a `partial` receipt lists missing roles, cards not applied and pending targets and is recorded as `partial`, not as a failed stage.
- `pipeline --read-only` is an allowlist: only the cached brief runs, and the agents stage is skipped.
- Missing eligible cards may start detached analysis through `quant/ops/card_autopaint.py`; the stage must return immediately, and read-only runs must use its dry-run mode.
- Promote a held watchlist card before lot reconciliation so re-entry lots inherit the current analyzed card.
- Score structural exits between close reconciliation and candidate migration: the exit record is the predicate, and promotion would otherwise overwrite the card being scored.
- Treat broker/state refresh, reconciliation and data freshness failures as first-class alerts.
- Preserve warnings from `quant/report/portfolio_brief.py` and `quant/decision/exit_guard.py`; do not weaken or paraphrase away binding constraints.
- The pipeline sends `fired_cards` and `alerts` immediately after the brief, before broker reminder and authorization-group effectors. Respect its `tg_pushed` receipt and do not duplicate an alert already delivered. Send the rendered cards verbatim and append no narrative or market-commentary lines of your own: a field absent from the report is a field the narrator would invent.
- Treat a completed close below zone 2 as stale immediately; upward breakouts retain their configured confirmation window. A one-session close move above `2 × ATR14` is volatility-stale when ATR is available.
- If live price is below the lower accumulation boundary on an authorized card, freeze BUY actions for that session while leaving sell/reduce actions available. Re-evaluate from the completed close rather than mutating authorization from an intraday snapshot.
- Never place, modify or cancel orders from a heartbeat.
- Market or company news is `[FYI]` and cannot silently change mechanical authorization.
- Never run close-dependent transitions on an incomplete market bar.
- Post-close mode evaluates exits against the old card before applying a regime transition, so a demotion cannot erase a breached stop before it is reported.
