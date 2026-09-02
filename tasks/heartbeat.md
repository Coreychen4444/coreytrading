# Heartbeat

Run the repository's periodic monitoring loop.

1. Execute `python3 quant/ops/pipeline.py --mode heartbeat` using the configured broker environment.
2. Preserve mechanical warnings and binding constraints exactly as emitted.
   The pipeline may already have sent `fired_cards` and `alerts` before external effectors. Honor the returned `tg_pushed` receipt: do not send duplicate cards when it is true, and use the normal notification path only as a fallback when delivery failed.
3. If the broker or required data source is unavailable, report the failure explicitly; do not manufacture stale values.
4. Do not place, modify or cancel orders.
5. Send the final text through `quant/ops/tg_notify.py --stdin --split` only when notification credentials are configured.

Treat news and macro context as `[FYI]`. Position authorization comes from the deterministic card, structure and risk layers.

This task does not fetch news, social posts or macro data. Send the rendered
cards verbatim and append no commentary of your own: a field the report does not
contain is a field the narrator would invent. If the operator has configured an
intelligence archive, it may be read read-only and cited with its date; when it
says nothing, say nothing.
