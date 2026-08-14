# Heartbeat rules

- Use `quant/ops/pipeline.py --mode heartbeat` as the canonical periodic sequence.
- Missing eligible cards may start detached analysis through `quant/ops/card_autopaint.py`; the stage must return immediately, and read-only runs must use its dry-run mode.
- Treat broker/state refresh, reconciliation and data freshness failures as first-class alerts.
- Preserve warnings from `quant/report/portfolio_brief.py` and `quant/decision/exit_guard.py`; do not weaken or paraphrase away binding constraints.
- Never place, modify or cancel orders from a heartbeat.
- Market or company news is `[FYI]` and cannot silently change mechanical authorization.
- Never run close-dependent transitions on an incomplete market bar.
