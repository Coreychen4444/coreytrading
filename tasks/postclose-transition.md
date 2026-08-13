# Post-close transition

Run only after the relevant market session has closed and completed bars are available.

1. Execute `python3 quant/ops/pipeline.py --mode postclose`.
2. Abort if the pipeline reports that the session is still open.
3. Report each regime transition with its close, gate, confirmation count and resulting cap.
4. Flag cards that require a zone redraw; do not invent new levels when evidence is insufficient.
5. Preserve warnings and do not place orders.
