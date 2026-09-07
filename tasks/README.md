# Scheduled task definitions

This directory versions reusable prompts for the optional desktop tasks, grouped by runner: `tasks/claude/` holds the six trading-task prompts written for a Claude Desktop local task, and `tasks/codex/` holds runner specifications for Codex (currently the independent daily valuation job). Copy or register them in your own task runner; the repository does not assume a specific user profile or local task registry.

| Task | Calendar gate (`agent_jobs.py due --kind`) | Purpose |
|---|---|---|
| `heartbeat` | `heartbeat`: any exchange session day; non-session days skip | Refresh broker state, reconcile fills and emit risk/exit alerts. |
| `overnight-briefing` | `briefing`: before the session opens | Build a macro, portfolio and entry-plan briefing with the macro, technical and risk research receipts. |
| `postclose-transition` | `postclose`: 10 to 60 minutes after the actual close | Evaluate exits, apply close-confirmed transitions, run technical/risk research and reviewed repaints. |
| `gate-ladder-report` | `ladder`: 20 to 60 minutes after the close, then wait for the same-session post-close receipt | Render the current gate ladder. |
| `chart-read` | `chart`: 35 to 60 minutes after the close, then wait for the post-close receipt | Summarize structure and chart hypotheses without authorization. |
| `weekly-portfolio-review` | `weekly`: calendar-independent | Audit exposure, process, data quality and rule adherence with macro, technical and risk research; valuation is not part of it. |

The independent daily valuation job is specified in `tasks/codex/valuation-daily.md`: it refreshes company and completed-bar caches, freezes inputs, runs the macro-scenario and valuation chain through `quant/ops/valuation_daily.py --run --json`, and archives its own receipt and report under `var/valuation-daily/`. Briefing and weekly review do not run the valuation CLI, the valuation role or the card mirror. It is a judgement layer with zero authorization effect and registers no schedule by itself.

Every prompt starts with `python3 quant/ops/agent_jobs.py due --kind <kind>`. The gate reads the pinned XNYS exchange calendar (`quant/core/market_sessions.py`): holidays, half days and daylight-saving changes are decided there, so the post-close prompts can be registered on several wake-up slots (for example ten minutes past each candidate closing hour) and only the slot inside the actual window runs. The calendar refuses to answer outside its supported range or without its dependency; a prompt must then report the error instead of falling back to a fixed clock time. The ladder and chart prompts additionally wait for the same-session post-close job to settle (`quant/ops/agent_dependency.py`), because the calendar decides when to start, not whether the upstream work finished.

Cadence is configured in the operator's timezone. Prompts assume the repository is the task working directory, local state is configured, and any external notification credentials are present only in environment variables. Domain research and reviewed card updates belong to the controller described in `agents/README.md`; the prompts only quote its receipts, and only a `status == "applied"` receipt is a written card.
