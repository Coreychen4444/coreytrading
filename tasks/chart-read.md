# Completed-bar chart read

Use `python3 quant/report/chart_read.py` after completed daily bars are available.

Summarize context, structure, price action, volume, indicators and falsifiable pattern hypotheses. The digest should lead with each ticker's short-horizon Dow phase, the level that would advance or invalidate that phase, and the completed-week context. Treat a Dow termination confirmation as a discretionary review point, never as an exit instruction. Every pattern statement must include confirmation and invalidation conditions. This task is descriptive only: it must not write state, calculate position size, grant a cap or override entry/exit guards.

When forwarding it, pipe the render into the notifier with `--stdin --split --plain` instead of passing it as a
shell argument, for the same reasons the gate ladder does.
