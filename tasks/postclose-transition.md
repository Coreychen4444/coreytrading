# Post-close transition

Run only after the relevant market session has closed and completed bars are available.

1. Execute `python3 quant/ops/pipeline.py --mode postclose`. The close chain refreshes positions and completed bars, refreshes NAV, evaluates and emits exit alerts against the pre-transition card, applies close-confirmed regime transitions, then reconciles reminders and the authorization group.
2. Abort if the pipeline reports that the session is still open.
3. Report each regime transition with its close, gate, confirmation count and resulting cap.
4. Flag cards that require a zone redraw; do not invent new levels when evidence is insufficient.
5. Preserve `exit_overdue` warnings after a stop-triggered demotion. The ladder shows the post-transition map, but the old-stop exit action remains unresolved. A score may record a rule-external delay; it is not an override and does not authorize continued holding.
6. Preserve warnings and do not place orders or push local state to a remote repository.
