---
name: trading-journal
description: Record trade decisions, rule-external actions, process scores and reusable lessons into the repository's local journal/state boundary without publishing private account data.
---

# Trading journal

Use this skill only when the user asks to record a decision, trade lesson, annotation or review.

1. Identify the local event or decision record by stable order/event identity.
2. Write structured fields before prose so reports can measure process separately from outcome.
3. Put private narrative and scores under `trade/us_stock/holding/journals/`; these paths are ignored by Git.
4. Turn a reusable lesson into a private rule only after user review: read `trade/us_stock/strategy/README.md`, copy the relevant example to an ignored private filename, and record provenance. Do not add strategy prose to the public example layer.
5. Run the relevant report or tests after a schema/mechanics change.

Do not sync journals, holdings, fills, NAV history or personal calibration to a public repository. Do not treat deleting a committed secret as recovery; rotate exposed credentials and remove sensitive history before publication.

See `references/file-map.md` and `references/review-template.md`.
