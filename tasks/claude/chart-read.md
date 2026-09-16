# Completed-bar chart read (two setups)

Read completed daily bars only. This task makes no model call: the deliverable is the code-rendered output of `quant/report/chart_read.py`. It does not launch the domain controller or the desktop handoff, and nothing outside the rendered output is added.

The daily read lists only the two setups the system trades, and only instruments where a setup has formed or is about to form: 🔁 **bottom** (seller exhaustion confirmed by basic price action) and 🚀 **trend confirmation** (a close-confirmed gate transition). Everything else is not listed.

0. Run `python3 quant/ops/agent_jobs.py due --kind chart`. The window opens thirty-five minutes after the actual close and closes sixty minutes after it; on `due: false` stop with `CHART_SKIP`. Keep the returned `session` and wait for the same-session post-close job with `python3 quant/ops/agent_dependency.py --kind postclose --session <session>`. Do not pass `--timeout-s`: the wait budget is the post-close job's own lease (`agent_health.DOMAIN_JOB_LEASE_S`); the wait returns as soon as the upstream process has died or its lease has expired, and reports `upstream_not_started` when no upstream receipt appears within fifteen minutes. Continue only on `status: settled`; do not re-run the calendar gate afterwards.
1. Refresh completed bars with `python3 quant/data/kline_cache.py`.
2. Render the list with `python3 quant/report/chart_read.py --book --digest`: two tables and one remainder line.
   - `🔁 Bottom`: ticker | exhaustion | low = stop | bounce | prior high | confirmation | bounce/reversal. ✓ formed = the session after the low made no new low, or the low bar itself has a long lower shadow (listed only within ten bars of the signal); ⏳ forming = a new 20-session low today without a long lower shadow (tomorrow without a new low completes it); ✗ = a close below the low (listed on that day). The low is the lower-shadow low and is the stop. The exhaustion column carries facts only (lower-shadow share, whether the close reclaimed the low bar's high); the bounce column shows its size, high and pullback low (↑ = higher low); bounce vs reversal is two facts (above the prior high / a higher pullback low) plus the weekly prior and a probe marker, all `[FYI]`. Population gate: the 20-session low is at least 10% below the 60-session high. Confirmation = the probability that, within 20 bars, a close above the prior high comes before a close below the low (a zero-parameter random-walk baseline from position and HV20, `quant/core/bottom_evidence.py`; landmark, volume, oversold or divergence facts do not modify it); once the prior high is already taken it reads "above prior high". It is `[FYI]`, not a ranking key (the closer to the stop, the lower the probability and the better the payoff).
   - `🚀 Trend confirmation`: ticker | state | gate | count | volume leg | distance. States: ✓ transitioned (within five bars; the gate column shows the **next** gate and "needs redraw" when the card still carries the line just passed) / ✅ holding, volume leg not passed / ⏳ counting m/n / ▫️ approaching (within 1N below the gate). Gate names follow the `gate_path` taxonomy and are descriptive only.
   - The tail line names every other instrument that is in neither setup; it proves the whole book was scanned. An instrument with insufficient bars gets its own ⚠️ line.
   Close, daily change, authorization cap and zone position never appear; the last two belong to the authorization layer.
3. Forward the list by piping it into the notifier (`python3 quant/report/chart_read.py --book --digest | python3 quant/ops/tg_notify.py --stdin`), never as a shell argument. The notifier renders headings, the legend quote and both native tables, and splits oversized output by table rows.

## Boundaries

- Zero authorization: whether and how much to buy always comes from `entry_guard`, the card zones and `sizing.entry_cap`. A ✓ bottom does not lift a cap of 0, and "bounce" is not a sell instruction; exits belong to `exit_guard` and the stop stays a hard line.
- Setups outside these two are neither read nor narrated. The task writes no state, raises no alerts, consumes no cooldown, records no decisions, writes no cards and calls no model.
- The six-step facts (context, structure, price action, volume, indicators) and the short-horizon Dow view remain on the `--tickers <T>` detail page only.

## Failure handling

- Dependency `failure` or `timeout`, or a failing dependency command: report it through the existing error channel and stop; do not render the old card set as the post-transition world. An upstream `partial` only means the card-writing stage ended with open work.
- Bar refresh failure: report it, render from the cache and head the message with "⚠️ not refreshed, data as of the previous session".
- Render failure: send the error text, never a partial list.

Review usage (not scheduled): `python3 quant/report/chart_read.py --tickers <T> --asof <date>` reads a past session (cards are not historical, so this is illustrative, not evidence); `--json` returns the structured `setups.bottom` / `setups.trend` blocks.
