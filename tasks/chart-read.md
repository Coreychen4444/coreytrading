# Completed-bar chart read

Read completed daily bars only.

0. Run `python3 quant/ops/agent_jobs.py due --kind chart`. The window opens thirty-five minutes after the actual close and closes sixty minutes after it; on `due: false` stop with `CHART_SKIP`.
1. Refresh completed bars with `python3 quant/data/kline_cache.py`, then run `python3 quant/ops/agent_jobs.py run --kind chart --scheduled --json`. The controller re-checks the window, waits (read-only, up to thirty minutes) for the same-session post-close job to settle, captures the post-transition state and produces the technical role's reading. Do not call the dependency wait or the calendar gate a second time. Its `reports.technical` is the only domain reading; do not start a second analysis or touch a card.
2. Render the mechanical digest with `python3 quant/report/chart_read.py --book --digest`: one line per instrument with the short-horizon Dow phase, the level that would advance or invalidate that phase, and the completed-week context. Treat a Dow termination confirmation as a discretionary review point, never as an exit instruction.
3. Forward the digest by piping it into the notifier (`| python3 quant/ops/tg_notify.py --stdin`), never as a shell argument.
4. When `reports.technical.status == "succeeded"`, a narrative supplement may follow the digest, using only facts the digest already printed: no new numbers, no probabilities, no sizes or limit prices. Every pattern statement is a hypothesis with its invalidation condition; a conflict with the mechanical digest is resolved in favor of the digest and stated as such.

This task is descriptive only: it must not write state, calculate position size, grant a cap or override entry/exit guards. If the controller reports a dependency `failure` or `timeout`, report it and stop instead of rendering stale cards; if the domain model failed or abstained after the dependency settled, send the mechanical digest and say that the technical reading is missing.
