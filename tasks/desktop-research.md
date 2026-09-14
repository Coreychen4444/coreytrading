# Desktop research handoff

Use this protocol when `agents/routing.json` sets `execution_transport` to `desktop`. The current desktop session is the model executor. `agent_jobs`, the pipeline and `valuation_daily` keep ownership of the calendar gate, leases, frozen evidence, role dependencies, mechanical calculations, submission and receipts. Never call the zone writer directly to bypass `agent_submit`, and never start another model CLI or sub-model. A Claude session claims only `claude` requests and a Codex session claims only `codex` requests; the default split is Claude for technical and risk, Codex for macro and valuation. An independent review request is answered for the target the host names.

## Launching a business task

Run the task's calendar gate exactly as its own prompt specifies. Where the prompt would run the pipeline, `agent_jobs` or `valuation_daily`, use:

```bash
.venv/moomoo/bin/python3 quant/ops/agent_desktop.py launch --kind <kind>
```

`kind` is one of `heartbeat`, `briefing`, `postclose`, `chart`, `weekly` or `valuation`. The command only moves the existing mechanical controller into the background; it does not create a model. Schedules, calendar gates, stage order, the per-round card bound and completion barriers are unchanged. Keep the returned `controller_id` and the output and completion file paths. Launch once per round.

## Serving research requests in the current session

1. Run `.venv/moomoo/bin/python3 quant/ops/agent_desktop.py claim --provider <claude|codex> --wait-s 50`. On `idle`, read the output if this round's completion file exists and continue with the original task prompt; if the controller is still running, keep waiting. One idle poll is not a reason to restart the controller.
2. After `claimed`, keep `request_id` and `claim_token`, then read the packet's instructions, task, schema, review target and snapshot. Read each policy file by its hash path in full the first time; reuse it when the same hash was already read in this session. The snapshot is the evidence: consult its fields as needed rather than copying the whole input into the prompt or report. Memory from other sessions and newly fetched facts are not frozen evidence.
3. Produce JSON that satisfies the schema and save it as `var/agent-runs/desktop/results/<request_id>.json` (create the directory first). `role`, `schema_version`, `contract_version`, `snapshot_id`, `input_hash`, `generated_at` and `expires_at` may be omitted and are bound by the host; if provided they must match exactly. Every other required field must be present and permissions must follow the contract (`analysis_only` / `proposal_only`). Every `evidence_refs` entry must come from the snapshot. Do not invent evidence, fetch new quotes, edit cards or issue research instructions through messages. A proposal contains only whitelisted writer arguments, never a ticker, bucket, path or revision.
4. Run `.venv/moomoo/bin/python3 quant/ops/agent_desktop.py complete --provider <claude|codex> --request-id <id> --claim-token <token> --result <json file>`. `answered` means the result was handed over, **not** that a card was applied. A contract or preview rejection may be corrected in the same result file and resubmitted; never edit the packet, policy, old snapshot or run records to get past validation. Insufficient evidence yields `unknown` or a valid abstention. If the request cannot be completed, pass `--error '<short reason>'` instead of `--result` so the controller records the failure instead of waiting.
5. Keep claiming later requests for the same provider (for example risk after technical, or single-card repaints) until this round's completion file appears. Then read the controller output and finish the original task prompt: quote reports, cards and pending cards, deliver the verbatim sections and close out. Only `cards[].status` or `submission.status` equal to `applied` counts as a written card. Result files are kept even when the controller fails.

A standalone Codex macro-intake task does not launch a business job: it waits for and serves `codex` requests, exits after completing one macro request, and reports that nothing arrived if no request appears within its intake window. It never fabricates a report or starts a model CLI. Business failures are reported by the original controller's receipt.

## Time limits and verification

Desktop execution is not bound by the single-call CLI deadline, but every request still carries its own `deadline_ts` and remains bound by the frozen evidence expiry and the business lease. An expired snapshot is never renewed and a draft is never submitted as a result; new evidence must be captured by the host. Provider fields record the host route and the claiming application. Model version and effort stay `unreported` unless native runtime metadata proves them; a self-reported identity is not verification. A warm desktop context does not guarantee cache hits, and any saving must be measured separately.
