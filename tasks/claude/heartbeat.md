# Heartbeat

Run the repository's periodic monitoring loop.

0. Run `python3 quant/ops/agent_jobs.py due --kind heartbeat`. When it returns `due: false`, stop with `HEARTBEAT_SKIP`: the exchange calendar decides session days, so never guess from the weekday. A calendar error is reported as an error, not worked around.
1. Execute `python3 quant/ops/pipeline.py --mode heartbeat --json` using the configured broker environment. Never pass `--read-only` from a scheduled task; that flag is the interactive cache preview and skips monitoring. When `agents/routing.json` sets `execution_transport` to `desktop`, read `tasks/desktop-research.md` first and run `python3 quant/ops/agent_desktop.py launch --kind heartbeat` instead; serve this runtime's research requests until the round's completion file appears, then read the controller output file as the pipeline JSON. The launch receipt itself is only a handle.
2. Preserve mechanical warnings and binding constraints exactly as emitted.
   The pipeline may already have sent `fired_cards` and `alerts` before external effectors. Honor the returned `tg_pushed` receipt: do not send duplicate cards when it is true, and use the normal notification path only as a fallback when delivery failed.
3. The heartbeat only records controller health and lets the missing-card detector dispatch background research. It does not start macro, technical, valuation or risk research itself, and unfinished research is never reported as a written card.
4. If the broker or required data source is unavailable, report the failure explicitly; do not manufacture stale values.
5. Do not place, modify or cancel orders.
6. Send the final text through `quant/ops/tg_notify.py --stdin` only when notification credentials are configured. The notifier renders rich messages by default and appends the operator's optional closing line itself; do not write it a second time.

Treat news and macro context as `[FYI]`. Position authorization comes from the deterministic card, structure and risk layers.

This task does not fetch news, social posts or macro data. Send the rendered
cards verbatim and append no commentary of your own: a field the report does not
contain is a field the narrator would invent. If the operator has configured an
intelligence archive, it may be read read-only and cited with its date; when it
says nothing, say nothing.
