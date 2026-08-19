# Weekly portfolio review

Review the locally configured portfolio without publishing account data.

1. Run the portfolio brief with `--read-only` and run the performance report. Weekly review must not consume the shared alert cooldown used by scheduled heartbeat, briefing and post-close modes.
2. Audit exposure, concentration, shock budget, at-risk limits, option DTE/IV, stale cards and unresolved infrastructure warnings.
3. Reconcile broker quantity, lots, fills and journals before judging performance.
4. Separate process quality from outcome quality; identify rule-external actions that need annotation.
5. Record reusable methodology only after removing identifiers and personal account values.
6. Do not place orders or push local state to a remote repository.
