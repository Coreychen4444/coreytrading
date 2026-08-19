# Gate ladder report

Run after post-close transitions have completed.

1. Execute `python3 quant/report/gate_ladder_report.py`.
2. Preserve the generated ordering, gate status, authorization cap and stale-card warnings.
3. Do not reinterpret a future gate as present authorization.
4. Preserve any unresolved-demotion marker: the displayed levels are the new post-transition map, while the breached old-stop exit decision remains open.
5. Optionally forward the report through `quant/ops/tg_notify.py --stdin --split`.
