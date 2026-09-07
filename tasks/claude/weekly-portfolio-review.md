# Weekly portfolio review

Review the locally configured portfolio without publishing account data.

Valuation refresh, calculation and reporting belong to the independent `valuation-daily` task (`tasks/codex/valuation-daily.md`); this review does not run the valuation CLI, the valuation role or the card mirror, and does not splice in a valuation section.

0. Run `python3 quant/ops/agent_jobs.py due --kind weekly` (calendar-independent; it only confirms the gate contract). Refresh data first, then run the macro, technical and risk research, then assemble the report.
1. Run the portfolio brief with `--read-only` and run the performance report. Weekly review must not consume the shared alert cooldown used by scheduled heartbeat, briefing and post-close modes.
2. Take the weekly NAV change from `python3 quant/report/nav_report.py --days 7`. It nets out external cash flows from the ledger maintained by `quant/data/cash_flow_sync.py`; never subtract the first and last NAV rows yourself, because a deposit or withdrawal would be reported as performance. Quote its warning lines when the ledger does not cover the window.
3. Audit exposure, concentration, shock budget, at-risk limits, option DTE/IV, stale cards and unresolved infrastructure warnings. Take the stale-candidate list from the brief's `watchlist_stale` output rather than scanning the watchlist file, so cards removed from the broker-side candidate group are not reported as live candidates. A candidate without a valuation archetype is reported as such (the brief names it as `INFRA:card_missing_archetype`), not silently skipped.
4. Reconcile broker quantity, lots, fills and journals before judging performance.
5. Separate process quality from outcome quality; identify rule-external actions that need annotation.
6. Run `python3 quant/ops/agent_jobs.py run --kind weekly --scheduled --json` and place the macro, technical and risk conclusions and unknowns in their sections. Risk conclusions must cite the mechanical receipts of the same frozen snapshot; macro stays `[FYI]`. A missing or failed role is reported as missing, never filled in with a confident guess, and generating the report is not a verification pass.
7. Record reusable methodology only after removing identifiers and personal account values.
8. Do not place orders or push local state to a remote repository.
