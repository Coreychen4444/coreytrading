# Heartbeat

Run the repository's periodic monitoring loop.

1. Execute `python3 quant/ops/pipeline.py --mode heartbeat` using the configured broker environment.
2. Preserve mechanical warnings and binding constraints exactly as emitted.
3. If the broker or required data source is unavailable, report the failure explicitly; do not manufacture stale values.
4. Do not place, modify or cancel orders.
5. Send the final text through `quant/ops/tg_notify.py --stdin --split` only when notification credentials are configured.

Treat news and macro context as `[FYI]`. Position authorization comes from the deterministic card, structure and risk layers.

When material industry events occur, add at most two sourced `[FYI]` lines for
model releases or pricing changes, accelerator roadmaps, hyperscaler capital
spending, major compute or data-center contracts, and supply-chain capacity or
pricing. Trigger this track by event type rather than ticker; map it only to
relevant holdings or candidates and state no directional recommendation.
