# Codex runner specifications

This directory holds task specifications written for a Codex-style scheduled runner. Register each specification in the runner yourself and point the registration prompt at the file's absolute path: changing a specification changes what the next run reads, while schedule, model, enable/disable, notification and project-binding changes are made in the runner and read back for verification. Do not copy these specifications into Claude Desktop tasks (`tasks/claude/`), and never treat the presence of a file as proof of registration.

| Specification | Reference cadence | Purpose |
|---|---|---|
| [valuation-daily.md](valuation-daily.md) | Daily 08:00 in the operator's timezone, including non-session days | Independent valuation: refresh, freeze, macro scenarios, calculation and report under `var/valuation-daily/` |

The trading tasks live in `tasks/claude/`; `tasks/README.md` describes the calendar gates shared by every runner.
