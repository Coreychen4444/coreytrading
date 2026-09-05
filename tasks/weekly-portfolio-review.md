# Weekly portfolio review

Review the locally configured portfolio without publishing account data.

0. Run `python3 quant/ops/agent_jobs.py due --kind weekly` (calendar-independent; it only confirms the gate contract). Refresh data first, then run the four-role research, then assemble the report.
1. Run the portfolio brief with `--read-only` and run the performance report. Weekly review must not consume the shared alert cooldown used by scheduled heartbeat, briefing and post-close modes.
2. Take the weekly NAV change from `python3 quant/report/nav_report.py --days 7`. It nets out external cash flows from the ledger maintained by `quant/data/cash_flow_sync.py`; never subtract the first and last NAV rows yourself, because a deposit or withdrawal would be reported as performance. Quote its warning lines when the ledger does not cover the window.
3. Audit exposure, concentration, shock budget, at-risk limits, option DTE/IV, stale cards and unresolved infrastructure warnings. Take the stale-candidate list from the brief's `watchlist_stale` output rather than scanning the watchlist file, so cards removed from the broker-side candidate group are not reported as live candidates.
4. Reconcile broker quantity, lots, fills and journals before judging performance.
5. Separate process quality from outcome quality; identify rule-external actions that need annotation.
6. Render the valuation digest and the post-earnings reconciliation of the valuation ledger. It is a judgement
   layer: quote it as context, never as an entry, exit or cap decision. A candidate without an archetype is
   reported as such (the brief names it as `INFRA:card_missing_archetype`), not silently skipped.
7. Run `python3 quant/ops/agent_jobs.py run --kind weekly --scheduled --json` and place the macro, technical, risk and valuation conclusions and unknowns in their sections. Risk conclusions must cite the mechanical receipts of the same frozen snapshot; macro and valuation stay `[FYI]`. A missing or failed role is reported as missing, never filled in with a confident guess, and generating the report is not a verification pass.
8. Record reusable methodology only after removing identifiers and personal account values.
9. Do not place orders or push local state to a remote repository.
