# Derived portfolio observations

Mechanical modules write their judgements here, separate from the fact ledgers in `events/` and the raw market inputs in `trade/market/`:

- `catalysts.jsonl`: dated catalyst records logged by `quant/decision/catalyst_check.py`.
- `structure_drift.jsonl`: structure-drift measurements from `quant/decision/structure_drift.py` (advisory only).

Copy the matching example without `.example` only when the corresponding module is in use. These files are derived and private; they never grant authorization.
