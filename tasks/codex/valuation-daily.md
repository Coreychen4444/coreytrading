# Independent daily valuation task

Run only from the root of the private runtime checkout of this project. If the working directory does not match, return `PROJECT_ROOT_MISMATCH` and do not create substitute files in another repository.

Schedule: a runner-bound project task, daily at 08:00 in the operator's timezone, including weekends and market holidays. The task refreshes company data and completed daily bars itself; it does not wait for the pre-market briefing or the weekly review and does not call them. Macro inputs are read only from the existing archive and snapshots under `trade/intelligence/`; missing or stale input must be reported, never collected ad hoc.

1. Read `CLAUDE.md`, `agents/README.md`, `quant/valuation/README.md` and this file in full.
2. Run `quant/ops/valuation_daily.py --run --json` with the project interpreter. This single entry point owns the dynamic holdings ∪ candidates scope, refresh, freeze, the macro → calculation → valuation review chain, per-day deduplication and the independent report archive. Model routing and limits come from `agents/routing.json`. Do not start a separate valuation model and do not run pipeline, agent_jobs, valuate or valuation_sync.
3. `busy` means a run is already in progress: report briefly and stop. `skipped/already_attempted_today` means today was already attempted: do not add `--retry` automatically; read the returned existing record and report accurately whether it succeeded, partially completed or failed, then stop. `failed`/`partial` must name the failing stage, data-refresh errors and the artifacts that do exist. Never edit code or data to get around a failure, and never present an old report as today's result.
4. On normal completion read the returned `report`, `receipt` and `workflow` files and deliver the independent valuation report inside the runner session:
   - valuation/quote time, model version, coverage count, the number of tickers with a computable baseline, and the gaps;
   - each ticker's fair value against the current price; the twelve-month EPS, multiple and conditional target are reported separately;
   - sourced macro scenarios, transmission assumptions, price changes and channels that remain uncovered;
   - 1/5/20-session short-horizon responses, their intervals and validation status; without samples write unknown;
   - changes against the previous independent report, computed only when the bases are comparable; company, macro, price, assumption and model changes are attributed separately and unidentified attribution is not invented;
   - the local path of the full report and the next data gaps.
   Numbers must come from the host receipt; the model's text cannot create prices. A 25 bp policy-rate cut, a 25 bp risk-free-curve shift and a 25 bp surprise are distinct inputs. The current short-horizon module does not cover the announcement-minute jump.
5. Run `quant/valuation/calibrate.py reconcile --write` with the project interpreter to reconcile landed earnings against the older prediction ledger, and state the result or failure in the independent report. That command calibrates only the compatible ledger; the daily frozen predictions live in this task's run archive and cannot be described as historically validated.

Artifacts of record are the capture, workflow, valuation, report and job files under `var/valuation-daily/runs/<date>/<run-id>/`; `latest.json` points only to the most recent attempt, and a failure never falls back to an older successful price. The compatible ledger `trade/valuation/valuations.jsonl` is no longer updated by the weekly review; daily evidence and predictions are kept complete in the independent archive without rewriting old predictions.

Research judgements have zero authorization effect. Do not write trading state, zones, caps, broker orders or the intelligence workflow's files, and do not commit or push. Delivery happens inside the runner session; this task sends no Telegram or other external messages.
