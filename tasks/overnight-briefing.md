# Pre-market briefing

Produce a human-reviewed operating brief before the US session.

1. Run `python3 quant/ops/pipeline.py --mode briefing`.
2. Summarize data freshness and any infrastructure warnings first.
3. Present macro/event context as dated `[FYI]` facts with sources.
4. Add an industry/theme track only for material model releases or pricing
   changes, accelerator roadmaps, hyperscaler capital-spending changes, major
   compute or data-center contracts, and supply-chain capacity or pricing.
   Trigger it by event type rather than ticker, include at most three sourced
   lines, map only to relevant holdings or candidates, and give no directional
   recommendation. Check each event class with an independent source query;
   absence from one curated feed is not evidence that no event occurred. On the
   first briefing after a weekend, use a 72-hour lookback so weekend releases are
   not omitted.
5. Preserve the portfolio brief, exit alerts, entry verdicts, caps, ladders, stops and warnings from the deterministic output. Within each verdict, keep the short-horizon Dow priority from `entry_guard`; it is an ordering key only and cannot change authorization or size. For held-stock additions, copy the rendered `position_ladder` prices and whole-share tiers instead of inventing an unstructured add.
6. Separate current holdings from watchlist candidates and clearly mark stale or missing cards.
7. Do not infer authorization from news, conviction or a chart narrative, do not offer a narrative override after a mechanical exit breach, and do not place orders.
8. Optionally send the reviewed result with `quant/ops/tg_notify.py --stdin --split`.

The pipeline's immediate alert channel does not replace the full briefing. Preserve the alert receipt to avoid duplicating urgent cards, then send the reviewed briefing normally when configured.
