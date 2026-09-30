# Gate ladder report

Run after post-close transitions have completed.

Reference wake-up cron (example in a UTC+8 operator timezone): `20 1,2,4,5 * * 2-6`. Its minute must equal this task's calendar offset (20 minutes after the close); an earlier minute makes every slot answer `due: false`.

0. Run `python3 quant/ops/agent_jobs.py due --kind ladder`. The window opens twenty minutes after the actual close and closes sixty minutes after it; on `due: false` stop with `LADDER_SKIP`. Keep the returned `session` and wait for the same-session post-close job: `python3 quant/ops/agent_dependency.py --kind postclose --session <session>`. Do not pass `--timeout-s`: the wait budget is the post-close job's own lease (`agent_health.DOMAIN_JOB_LEASE_S`), because a legitimate post-close run with research and several controlled card writes can outlast any private timeout and a barrier timeout is not an upstream failure. The wait returns as soon as the upstream process has died or its lease has expired, and reports `upstream_not_started` when no upstream receipt appears within fifteen minutes. Continue only on `status: settled` (an upstream `partial` still means some cards are open work). While waiting, the post-close receipt `status` stays `running` (research conclusions live in `research_status`) until every card write has finished and `finished_at` is stamped. The wait reads job metadata only; do not re-run the calendar gate afterwards, so an admitted run is not skipped for having waited.
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
