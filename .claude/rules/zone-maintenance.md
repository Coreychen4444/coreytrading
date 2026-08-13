# Zone and gate maintenance

- Zones and gates are dated technical maps, not permanent properties of an instrument.
- Store explicit anchors, methods and update triggers so a redraw is auditable.
- A gate is a state-transition threshold; a zone is an execution area within the current state. Do not reuse the terms interchangeably.
- Only completed bars may confirm a close-based transition.
- A redraw must preserve monotonic relationships such as `stop < accumulation zone <= no-chase <= trim` unless the schema explicitly models another structure.
- Stale or missing maps must be reported. Do not silently use an old level or invent a replacement.

Use `.claude/skills/trading-analysis/scripts/update_position_zones.py` for controlled writes and keep broker-owned fields unchanged.
