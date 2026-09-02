# Pre-market briefing

Produce a human-reviewed operating brief before the US session.

1. Run `python3 quant/ops/pipeline.py --mode briefing`.
2. Summarize data freshness and any infrastructure warnings first.
3. Present macro/event context as dated `[FYI]` facts with sources. This task
   does not fetch news, social posts or macro data itself: read the operator's
   authorized intelligence archive if one is configured, and otherwise state
   that there is no intelligence input rather than searching for one.
4. Carry the local earnings calendar as `[FYI]`: which holdings and candidates
   report, the session (before or after the close), the reaction day, and the
   event-day size adjustment that follows from it. Name any card whose anchor
   predates a landed reaction day; that is a redraw obligation, not a veto.
5. Preserve the portfolio brief, exit alerts, entry verdicts, caps, ladders, stops and warnings from the deterministic output. Within each verdict, keep the short-horizon Dow priority from `entry_guard`; it is an ordering key only and cannot change authorization or size. For held-stock additions, copy the rendered `position_ladder` prices and whole-share tiers instead of inventing an unstructured add.
6. Separate current holdings from watchlist candidates and clearly mark stale or missing cards.
7. Do not infer authorization from news, conviction or a chart narrative, do not offer a narrative override after a mechanical exit breach, and do not place orders.
8. Optionally send the reviewed result with `quant/ops/tg_notify.py --stdin --split`.

The pipeline's immediate alert channel does not replace the full briefing. Preserve the alert receipt to avoid duplicating urgent cards, then send the reviewed briefing normally when configured.
