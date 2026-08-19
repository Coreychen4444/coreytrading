# Zone and gate maintenance

- Zones and gates are dated technical maps, not permanent properties of an instrument.
- Store explicit anchors, methods and update triggers so a redraw is auditable.
- A gate is a state-transition threshold; a zone is an execution area within the current state. Do not reuse the terms interchangeably.
- Only completed bars may confirm a close-based transition.
- A redraw must preserve monotonic relationships such as `stop < accumulation zone <= no-chase <= trim` unless the schema explicitly models another structure.
- Stale or missing maps must be reported. Do not silently use an old level or invent a replacement.
- A completed close below the lower zone-2 boundary makes the map breakdown-stale immediately; a two-close confirmation is not required because an old-zone buy can execute in broken structure.
- An upward close above the trim boundary retains its configured multi-session confirmation to avoid unnecessary redraw churn.
- A one-session absolute close-to-close move above `2 × ATR14` is volatility-stale when enough completed bars exist. Missing ATR data suppresses only that trigger; it does not authorize use of a fixed fallback threshold.

Use `.claude/skills/trading-analysis/scripts/update_position_zones.py` for controlled writes and keep broker-owned fields unchanged.
