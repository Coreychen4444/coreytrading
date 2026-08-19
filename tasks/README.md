# Scheduled task definitions

This directory versions reusable prompts for six optional desktop tasks. Copy or register them in your own task runner; the repository does not assume a specific user profile or local task registry.

| Task | Example cadence (Singapore time) | Purpose |
|---|---|---|
| `heartbeat` | Every 6 hours on trading days | Refresh broker state, reconcile fills and emit risk/exit alerts. |
| `overnight-briefing` | Before the US session | Build a macro, portfolio and entry-plan briefing. |
| `postclose-transition` | After the US close | Evaluate exits before applying close-confirmed regime transitions. |
| `gate-ladder-report` | After transitions | Render the current gate ladder. |
| `chart-read` | After completed bars are available | Summarize structure and chart hypotheses without authorization. |
| `weekly-portfolio-review` | Weekly | Audit exposure, process, data quality and rule adherence. |

Schedules must be adjusted for daylight-saving time and the operator's timezone. Prompts assume the repository is the task working directory, local state is configured, and any external notification credentials are present only in environment variables.
