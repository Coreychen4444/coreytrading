# Post-close transition

Run only after the relevant market session has closed and completed bars are available.

0. Run `python3 quant/ops/agent_jobs.py due --kind postclose`. The wake-up cadence may fire several times a day; the exchange calendar admits only the slot that falls between ten and sixty minutes after the actual close (regular or half day, daylight-saving aware). On `due: false` stop with `POSTCLOSE_SKIP`; never fall back to a fixed clock time or the weekday.
1. Execute `python3 quant/ops/pipeline.py --mode postclose --json`. The close chain refreshes positions and completed bars, refreshes NAV, evaluates and emits exit alerts against the pre-transition card, applies close-confirmed regime transitions, runs the domain-agent stage (technical and risk research, then at most two reviewed repaints), and only then reconciles reminders and the authorization group.
2. Abort if the pipeline reports that the session is still open.
3. Report each regime transition with its close, gate, confirmation count and resulting cap.
4. Read `stages.agents.data`. Quote `reports.technical` and `reports.risk` (conclusions, disagreements, unknowns) without re-analyzing. List every card receipt with its run id, review and submission status; only `status == "applied"` is a written card. `pending_cards` beyond the two-card bound stay listed as open work. A top-level `partial.agents` entry means missing roles or unwritten cards, not a crashed stage. Never call the writer, edit state by hand, change routing or re-ask a model for a different answer.
5. Flag cards that require a zone redraw; do not invent new levels when evidence is insufficient. Take the
   earnings-stale set from the report's mechanical warning rather than re-deriving it from prose: the predicate
   is that the card's anchor predates the landed reaction session, which a rule phrased around the publication
   day silently misses.
6. A redraw after an earnings print is a full redraw. Updating zones alone pushes the anchor past the reaction
   day — which silences the staleness warning — while the thesis still describes the pre-print world, and any
   write refreshes the review timestamp that would otherwise expose it.
7. Preserve `exit_overdue` warnings after a stop-triggered demotion. The ladder shows the post-transition map, but the old-stop exit action remains unresolved. A score may record a rule-external delay; it is not an override and does not authorize continued holding.
8. Reminder and authorization-group receipts come from the same pipeline run (`stages.reminders`, `stages.today_group`); they already ran after the agents stage, so do not synchronize them again.
9. Preserve warnings and do not place orders or push local state to a remote repository.
