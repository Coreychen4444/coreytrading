# Macro lens publication (close + 30 minutes)

This task makes no model call: the deliverable is the code-rendered output of `quant/journal/macro_lens_log.py`. The macro lens is a descriptive reading of the cross-asset environment — six risk lights (they forecast volatility and drawdown depth, not direction), the strongest macro driver of the last 40 sessions and how the market reads it (a description, not a persistence claim), what the market is trading, and forward scenarios with estimated betas. It has zero authorization effect. Nothing outside the rendered output is added: no interpretation, prose or rewriting.

The reading depends only on cross-asset daily series, not on cards, transitions or the kline cache, so this task does not wait for the post-close job: a post-close failure or delay does not affect it, and its own failure affects no other task. Register it on several wake-up slots (for example thirty minutes past each candidate closing hour); the calendar gate opens thirty minutes after the actual close — the latest-settling input series finishes settling about thirty minutes after the close, and an earlier run would write intraday values into the ledger — and closes sixty minutes after it.

Reference wake-up cron (example in a UTC+8 operator timezone): `30 1,2,4,5 * * 2-6`. Its minute must equal this task's calendar offset (30 minutes after the close); an earlier minute makes every slot answer `due: false`.

0. Run `python3 quant/ops/agent_jobs.py due --kind macro`. On `due: false` stop with `MACRO_SKIP`, without refreshing or sending anything; a calendar error is reported, never replaced by a fixed clock time. On `due: true` keep the returned `session`.
1. Publish (refresh → read → write): `python3 quant/journal/macro_lens_log.py --update --session <session>`. It tail-refreshes the cross-asset daily series (`quant/data/macro_series.py`, cache `trade/market/macro_daily/`, not versioned), computes the reading (`quant/core/macro_lens.py`) and writes `trade/intelligence/macro/current.json` (overwritten), `ledger.jsonl` (idempotent append by as-of date) and the full text under `trade/intelligence/macro/reports/YYYY-MM/YYYY-MM-DD.md`. `--session` is a code gate: when the reading's as-of date is not this session (the benchmark bar for today is missing) it writes nothing and exits with code 3. A single series that fails to refresh or lags the session is read from the cache and flagged with an automatic `⚠️` data line under the heading.
2. Forward the reading as its own message by piping it into the notifier: `python3 quant/journal/macro_lens_log.py --digest | python3 quant/ops/tg_notify.py --stdin`.

## Failure handling

- Step 0 fails or the calendar is unavailable: report the error text through the existing error channel and stop.
- Step 1 exits with code 3: forward the single "not published" line it prints and do **not** run step 2 — `current.json` still holds the previous session's reading, and pushing it would present an old reading as today's. The missing ledger day is not back-filled.
- Any other step 1 failure: send the error text and do not run step 2.
- Step 2 fails: report the error text; the files are already published, so the pre-market `--brief` still reads today's reading.

## Boundaries

- Zero authorization: it never changes PLAN / VETO, caps or share counts, writes no cards and no decisions, consumes no alert cooldown and calls no model. Its only writes are the `current` / `ledger` / `reports` files under `trade/intelligence/macro/` and the `trade/market/macro_daily/` cache.
- Consumers: the pre-market briefing (`--brief`), the weekly review (`--week`), trading discussions that read `current.json` first, and the daily valuation job's risk-free rate (the cached 10-year series).

Review usage (not scheduled): `--report` (today's full text), `--week` (weekly review), `--explain <T> --date <D>` (single-day macro attribution, step 2 of `.claude/rules/macro-context-check.md`), `--update --no-refresh --no-publish --asof <D>` (re-read a past session without fetching or writing).
