# Gate ladder report

Run after post-close transitions have completed.

0. Run `python3 quant/ops/agent_jobs.py due --kind ladder`. The window opens twenty minutes after the actual close and closes sixty minutes after it; on `due: false` stop with `LADDER_SKIP`. Keep the returned `session` and wait for the same-session post-close job: `python3 quant/ops/agent_dependency.py --kind postclose --session <session> --timeout-s 1800`. Continue only on `status: settled` (an upstream `partial` still means some cards are open work). The wait reads job metadata only; do not re-run the calendar gate afterwards, so an admitted run is not skipped for having waited.
1. Refresh completed bars with `python3 quant/data/kline_cache.py`.
2. Execute `python3 quant/report/gate_ladder_report.py`.
3. Preserve the generated ordering, gate status, authorization cap and stale-card warnings.
4. Do not reinterpret a future gate as present authorization.
5. Preserve any unresolved-demotion marker: the displayed levels are the new post-transition map, while the breached old-stop exit action remains open. There is no narrative override path.
6. Optionally forward the report by **piping** it: `python3 quant/report/gate_ladder_report.py | python3
   quant/ops/tg_notify.py --stdin`. Do not pass the text as a shell argument — backticks in the body would be
   executed. The notifier renders rich messages by default and splits oversized reports itself; `--legacy
   --split --plain` remains available for troubleshooting the plain-text channel.

If the dependency wait returns `failure` or `timeout`, report that result through the existing error channel and stop; do not render the old card set as the post-transition world.
