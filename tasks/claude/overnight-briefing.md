# Pre-market briefing

Produce a human-reviewed operating brief before the US session.

0. Run `python3 quant/ops/agent_jobs.py due --kind briefing`. When it returns `due: false` (holiday, or the session already opened), stop with `BRIEFING_SKIP`; a calendar error is reported, never guessed around.
1. Run `python3 quant/ops/pipeline.py --mode briefing --json`. Never pass `--read-only` from a scheduled task.
2. Summarize data freshness and any infrastructure warnings first.
3. Read `stages.agents.data`: the controller's `reports` (macro, technical and risk conclusions and unknowns), `cards` (independent-review and submission receipts) and `pending_cards` (targets beyond the per-run bound). Only a card with `status == "applied"` was written; failed, abstained, review-rejected or pending targets are listed as unfinished, never as done. A top-level `partial.agents` entry means the domain job finished with missing roles or unwritten cards, not that the stage crashed; `failed` is reserved for real failures. Do not start research of your own or bypass the controller to repaint a card.
4. Present macro/event context as dated `[FYI]` facts with sources. This task
   does not fetch news, social posts or macro data itself: read the operator's
   authorized intelligence archive under `trade/intelligence/reports/` if one is
   configured, and otherwise state that there is no intelligence input rather
   than searching for one. Macro research from the controller stays `[FYI]`;
   valuation belongs to the independent daily valuation task and is neither run
   nor spliced into this briefing. Amounts and authorization come only from the
   mechanical reports.
5. Carry the local earnings calendar as `[FYI]`: which holdings and candidates
   report, the session (before or after the close), the reaction day, and the
   event-day size adjustment that follows from it. Name any card whose anchor
   predates a landed reaction day; that is a redraw obligation, not a veto.
6. Preserve the portfolio brief, exit alerts, entry verdicts, caps, ladders, stops and warnings from the deterministic output. Within each verdict, keep the short-horizon Dow priority from `entry_guard`; it is an ordering key only and cannot change authorization or size. For held-stock additions, copy the rendered `position_ladder` prices and whole-share tiers instead of inventing an unstructured add.
7. Separate current holdings from watchlist candidates and clearly mark stale or missing cards.
8. Do not infer authorization from news, conviction or a chart narrative, do not offer a narrative override after a mechanical exit breach, and do not place orders.
9. Optionally send the reviewed result with `quant/ops/tg_notify.py --stdin` (rich messages by default; pipe the text, do not pass it as a shell argument).

The pipeline's immediate alert channel does not replace the full briefing. Preserve the alert receipt to avoid duplicating urgent cards, then send the reviewed briefing normally when configured.
