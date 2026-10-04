# Gate ladder report

Run after post-close transitions have completed.

Reference wake-up cron (example in a UTC+8 operator timezone): `20 1,2,4,5 * * 2-6`. Its minute must equal this task's calendar offset (20 minutes after the close); an earlier minute makes every slot answer `due: false`.

0. Run `python3 quant/ops/agent_jobs.py due --kind ladder`. The window opens twenty minutes after the actual close and closes sixty minutes after it; on `due: false` stop with `LADDER_SKIP`. Keep the returned `session` and wait for the same-session post-close job: `python3 quant/ops/agent_dependency.py --kind postclose --session <session>`. Do not pass `--timeout-s`: the wait budget is the post-close job's own lease (`agent_health.DOMAIN_JOB_LEASE_S`), because a legitimate post-close run with research and several controlled card writes can outlast any private timeout and a barrier timeout is not an upstream failure. The wait returns as soon as the upstream process has died or its lease has expired, and reports `upstream_not_started` when no upstream receipt appears within fifteen minutes. Continue only on `status: settled` (an upstream `partial` still means some cards are open work). While waiting, the post-close receipt `status` stays `running` (research conclusions live in `research_status`) until every card write has finished and `finished_at` is stamped. The wait reads job metadata only; do not re-run the calendar gate afterwards, so an admitted run is not skipped for having waited.
1. Refresh completed bars with `python3 quant/data/kline_cache.py`.
2. Execute `python3 quant/report/gate_ladder_report.py`.
3. Preserve the generated ordering, gate status, authorization cap and stale-card warnings.
4. Do not reinterpret a future gate as present authorization.
5. Preserve any unresolved-demotion marker: the displayed levels are the new post-transition map, while the breached old-stop exit action remains open. There is no narrative override path.
   Each stock card's table is followed by two descriptive lines computed from the same 250-bar volume profile (`quant/core/vacuum.py`); option cards and tickers with too little history get neither:
   - `🧱` dense zones: the zone containing the price plus the nearest zone above and below (range, peak, distance, volume share, near-edge rejections / holds), with the count of farther zones; the line opens with the average daily turnover (float shares come from the `trade/market/fundamentals/<T>.json` snapshot; when missing the line says so). A zone above is trapped supply, a zone below is holder cost basis; treat it as evidence of the same weight as the card's pivot-cluster gates, never stronger. Do not write "strong resistance", "strong support" or "must retest", and do not argue resistance strength from the volume share.
   - `🌫` vacuum bands: the nearest thin-volume band above and below (≤25% of POC, at least 1 ATR wide) with range, distance, width, volume share, near-edge rejections / holds and inner clusters. **Vacuum is not absence of resistance** (volume shows inventory, not sellers), and "moves faster through a vacuum" is untested. Do not conclude "no resistance above", "hold longer" or "no need to trim"; stops, ratchets, trend lines and G-01 are never loosened by it.
6. Optionally forward the report by **piping** it: `python3 quant/report/gate_ladder_report.py | python3
   quant/ops/tg_notify.py --stdin`. Do not pass the text as a shell argument — backticks in the body would be
   executed. The notifier renders rich messages by default and splits oversized reports itself; `--legacy
   --split --plain` remains available for troubleshooting the plain-text channel.

If the dependency wait returns `failure` or `timeout`, report that result through the existing error channel and stop; do not render the old card set as the post-transition world.
