# Gate ladder report

Run after post-close transitions have completed.

1. Execute `python3 quant/report/gate_ladder_report.py`.
2. Preserve the generated ordering, gate status, authorization cap and stale-card warnings.
3. Do not reinterpret a future gate as present authorization.
4. Preserve any unresolved-demotion marker: the displayed levels are the new post-transition map, while the breached old-stop exit action remains open. There is no narrative override path.
5. Optionally forward the report by **piping** it: `python3 quant/report/gate_ladder_report.py | python3
   quant/ops/tg_notify.py --stdin --split --plain`. Do not pass the text as a shell argument — backticks in the
   body would be executed — and do not treat `--split` as optional: this report routinely exceeds the messenger's
   length limit, where the failure mode is whole-message rejection rather than truncation. `--plain` keeps `_`
   and `*` in identifiers from being eaten by markup. The report is a deterministic read-only render, so
   re-running it inside the pipe yields the same text and removes a transcription step.
