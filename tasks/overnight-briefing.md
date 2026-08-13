# Pre-market briefing

Produce a human-reviewed operating brief before the US session.

1. Run `python3 quant/ops/pipeline.py --mode briefing`.
2. Summarize data freshness and any infrastructure warnings first.
3. Present macro/event context as dated `[FYI]` facts with sources.
4. Preserve the portfolio brief, exit alerts, entry verdicts, caps, ladders, stops and warnings from the deterministic output.
5. Separate current holdings from watchlist candidates and clearly mark stale or missing cards.
6. Do not infer authorization from news, conviction or a chart narrative, and do not place orders.
7. Optionally send the reviewed result with `quant/ops/tg_notify.py --stdin --split`.
