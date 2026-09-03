# coreytrading — Project Agent Contract

This file is the complete project-level operating contract. Claude Code reads it directly; other coding agents enter through `AGENTS.md` and must then read this file in full before changing code, state, task prompts, schemas or trading behavior. It is deliberately detailed; do not replace it with a short repository summary.

The public repository contains the complete reusable operating model while excluding personal identity, live account data, private strategy prose, machine-local configuration and historical attribution annotations.

## Authority and scope

- Explicit user instructions define the current task.
- This file defines project-wide behavior and architecture.
- `AGENTS.md` is a compatibility entry point, not a duplicate contract. It must continue to direct non-Claude agents here.
- `AI_BOOTSTRAP.md` is the source of truth for fresh-clone setup, infrastructure, private-workspace initialization and the bootstrap handoff report; `AI_BOOTSTRAP_CN.md` is its maintained Chinese translation.
- `.claude/rules/` contains focused rules for trading discipline, zones, heartbeat behavior, macro context, position tiers and environment setup.
- `.claude/skills/` contains task-specific workflows. Read the relevant `SKILL.md` before using one.
- `tasks/` is the source of truth for optional scheduled-task prompts.
- `quant/README.md` is the source of truth for package layering and dependency direction.
- Executable formulas and schemas win over prose when they disagree. Repair the stale prose and add a regression test rather than creating a second implementation.

## Identity and communication style

- Act as a technical trading-operations collaborator and repository maintainer, not as an autonomous trader.
- Be direct, evidence-led and concise. Call out mistakes or unsafe assumptions before they compound.
- Match the user's language. Chinese is the default for trading operations; English is acceptable for code and API terminology.
- Separate observed facts, calculations, hypotheses, mechanical verdicts and operator decisions.
- Verify prices, timestamps, catalysts and broker state instead of trusting conversational recollection.
- Be resourceful before asking questions, but never invent private facts, account values, holdings, authorization or risk limits.
- External actions—orders, messages, posts, task registration, commits, pushes and releases—require the authorization appropriate to that action.
- The canonical pipeline prepares state, alerts and plans. It does not place, modify or cancel trades.

## Privacy and public-repository boundary

- Never commit `.env`, private keys, tokens, login hashes, account identifiers, broker exports or populated OpenD configuration.
- Never commit real holdings, watchlists, lots, orders, fills, NAV history, journals, active-universe market caches, private research cards or user-specific strategies.
- Public `quant/research/` is limited to reusable mechanics, synthetic examples and bootstrap documentation. Populated protocols, datasets, results, rulings, backlog and operator notes remain in the ignored private research workspace.
- Public files below `trade/` are limited to `README.md` and synthetic `*.example.*` schemas.
- `trade/README.md` is the AI bootstrap contract. Read it and the relevant descendant READMEs before creating private runtime files.
- Copy only the examples needed by the requested workflow. Remove `.example` from the private filename, preserve the schema, and request missing facts instead of guessing.
- Generated private files remain ignored by Git. Never copy their contents into public fixtures, logs, reports, issues or documentation.
- Tests use synthetic fixtures and temporary paths. Offline tests must not read or mutate live files below `trade/us_stock/holding/` or `trade/us_stock/market/`.
- Public prose, comments, docstrings and filenames must not contain personal-name attribution or dated internal-history annotations. Dates required as executable input, schema examples or test fixtures remain data rather than provenance.

## Workspace and data contracts

### Canonical state paths

- `trade/us_stock/holding/state/positions.json` is the local single source of truth for held and closed position cards.
- `trade/us_stock/holding/state/watchlist.json` is the local single source of truth for candidate cards and entry analysis.
- `trade/us_stock/holding/state/exit_guards.json` is script-owned exit-stack state.
- `trade/us_stock/holding/state/option_plans.json` is generated option-planning state.
- `trade/us_stock/valuation/inputs.json` is the valuation layer's hand-written region; `valuations.jsonl` and `calibration.jsonl` are its append-only prediction and reconciliation ledgers.
- `ai_news/` is an optional, user-owned intelligence workspace outside this repository. Trading code may read the macro snapshot and the valuation overlay ledger from it, must degrade gracefully when they are missing, and must never write there or treat their contents as authorization.
- `quant/ops/ainews_universe.py` is the only export in the other direction: a read-only command that prints the current coverage universe derived from local state, so an external research workspace never re-derives it from its own copy of the predicate. It writes nothing and emits names only — no price, position, cap or authorization.
- Canonical path constants live in `quant/core/paths.py`. Business modules must import paths from there rather than derive repository roots themselves.
- State writers use the atomic JSON helpers and shared state lock from `quant/core/io_json.py`.

A position card may contain:

- `instrument`: stock/option identity and optional management role;
- `broker`: synchronized quantity, price, cost, currency, source and timestamp;
- `agent`: thesis, zones, current regime, intended action, risks and invalidation;
- `lots`: `base` and `position` accounting lots;
- `position_summary`: derived lot/accounting summary;
- `pending_orders`: synchronized open-order state;
- `exit_info` and `regime_history`: lifecycle/audit state.

Ownership is strict:

- `quant/sync/` owns broker blocks and broker-derived order/fill truth.
- Controlled agent workflows own thesis, zones, action and user-reviewed rules.
- Reconciliation owns script-derived lifecycle fields.
- A broker refresh must preserve agent blocks, lots and pending-order semantics it does not own.
- Unknown is not zero. Use `null`, an empty collection, a warning or an explicit review item according to the schema.

### Watchlist membership and cards

- A configured broker-side `Watchlist` group represents entry candidates, not all instruments of interest.
- `quant/sync/watchlist_sync.py` reconciles group membership with `watchlist.json`.
- Newly observed members receive a schema-valid stub for later analysis.
- Removing a member marks its membership false; it does not erase the historical card.
- A newly held bare ticker is moved out of the entry-candidate group.
- A fully closed campaign may be re-added once for the new campaign. The close timestamp acts as the idempotency receipt.
- Re-added cards retain their agent block, zones, regime history and original review timestamp so staleness is not silently reset.
- Existing zoned cards must never be overwritten by empty stubs.
- Membership reconciliation and card-content reconciliation are separate decisions; do not collapse their predicates.

### Events, decisions and journals

- Trade events live at `trade/us_stock/holding/events/<YYYY-MM>/trades.jsonl`.
- Event ledgers are append-only. One line is one JSON object.
- Fill/close/expiry/assignment timestamps use ISO format with the correct market offset.
- Broker order identity is the primary deduplication key; combo-parent coverage must remain stable.
- Decision events and NAV history use the canonical paths defined in `quant/core/paths.py` and the holding/event README.
- Structured process scores belong in the journal score ledger. Narrative review alone is not measurable state.
- Corrections are new provenance-bearing events; do not rewrite prior events to improve the story.

### Market data

- `trade/us_stock/market/` contains private/generated K-line, IV, earnings, catalyst, structure-drift and account-benchmark datasets.
- Provider terms, timestamp, session completeness and source must be recorded.
- K-line consumers may use completed bars only for close-confirmed transitions.
- Missing or stale market data is an infrastructure condition, not permission to reuse an old number as current.
- Public market examples are synthetic schemas and must never be treated as observations.

### Research and strategy workspaces

- Private research cards are generated from `trade/us_stock/knowledge/research/research-card.example.md`.
- Research must separate sourced facts, inference, counterevidence, invalidation and open questions.
- Research and news are informational inputs. They do not directly grant entry authorization.
- `trade/us_stock/strategy/` contains public schemas only. The active private rule is the ignored copy of an example with `.example` removed.
- Before creating a private rule, read `trade/us_stock/strategy/README.md`, obtain the user's unresolved policy choices, and record scope, inputs, freshness, trigger, action, missing-data behavior, validation and review ownership.
- Never treat a public example as an active recommendation.

## Architecture and dependency direction

Dependencies move downward only:

```text
ops          orchestration and notifications
├── report   read-time presentation and alerts
├── journal  event/decision/accounting annotations
├── valuation fundamental fair value and implied expectations (judgement only)
├── decision deterministic gates and state transitions
│   ├── options contract selection and premium mapping
│   └── risk portfolio/position risk analysis
├── sync     broker truth and local-state reconciliation
├── data     external data ingestion
└── core     pure shared schemas, paths, math and invariants

research     offline validation bypass; live code must not import it
```

Rules enforced by architecture tests:

- Keep modules at `quant/<layer>/<module>.py` unless the bootstrap and architecture tests are deliberately redesigned.
- Use absolute `quant.<layer>...` imports outside `core`; use relative imports within `core`.
- Only `quant/core/paths.py` derives the repository root and canonical runtime paths.
- OpenD scripts load their shared helper through `quant.core.paths.add_openapi_path()`.
- `quant/research` may import production code for validation; production code may never import `quant/research`.
- Tests mirror production layers under `tests/<layer>/`.
- Every production module needs matching coverage or an explicit, justified entry in the coverage ledger.

## Core layer: executable single sources of truth

### Paths, schema and I/O

- `quant/core/paths.py`: all canonical repository, state, market and event paths.
- `quant/core/io_json.py`: atomic JSON writes, JSONL append/iteration and the cross-process state lock.
- `quant/core/schema.py`: position, watchlist and event validation. Every material state write must pass it.
- `quant/core/timeutil.py`: market-time normalization, completed-session checks and daylight-saving-safe date logic.
- `quant/core/occ.py`: option-symbol parsing. Do not duplicate OCC parsing in consumers.
- `quant/core/ledger.py`: shared ledger helpers.

### Sizing and portfolio math

`quant/core/sizing.py` and `quant/core/book.py` are the only places allowed to assemble sizing math. Consumers may render their result but may not reimplement it.

Current invariants:

- `current_regime.cap_pct_nav` is the authorization ceiling. It is not a target and does not create cash or buying power.
- Authorization is binary at the regime layer: zero means VETO; a positive configured regime cap enters the gate-budget calculation.
- The nominal single-position ceiling is `NOTIONAL_CAP = 0.40` of NAV.
- The portfolio shock budget is `SHOCK_BUDGET = 0.15` of NAV using `SHOCK_X = 1.88`.
- The portfolio gross ceiling is `GROSS_CAP = 1.10` of NAV.
- The per-position stop-risk ceiling is `AT_RISK_CAP_FRAC = 0.05` of NAV.
- Long-option at-risk loss uses `OPTION_ATRISK_LOSS_FRAC = 0.20`, pinned to the premium stop assumption.
- The shock breaker threshold is `SHOCK_BREAKER_MULT = 1.5` times the portfolio shock budget.
- Gate risk ceilings are cumulative: gate 1 = `2.5%`, gate 2 = `4.0%`, gate 3 and above = `5.0%` of NAV.
- There is one campaign risk budget unlocked by gates; there are no independent `R1/R2/R3` cash buckets.
- A position can be added to multiple times inside the same gate. Remaining room equals the gate ceiling minus current exposure.
- `sizing.gate_count(card)` counts only upward transitions that granted a cap.
- A down transition or completed campaign resets the participation stream to the first gate.
- Close receipts may live on either the position card or the re-added watchlist card; the gate-count predicate reads both.
- Options may enter at the first cap-granting gate (`OPTION_MIN_GATE = 1`) under the same qualification a stock entry needs, and only after a hard implied-volatility gate: a low IV percentile, or a low absolute ATM IV while the series is too short to rank. Missing IV data is a refusal, not a pass.
- A long option's underlying exit line is the gate that granted the cap, confirmed on the close. The stock's buffered gate floor does not apply to it.
- Option premium caps use the newly unlocked gate-risk increment, not the cumulative stock budget.
- Stock exposure, option premium, option delta notional, stop risk, shock occupancy and gross exposure are different units. Convert explicitly; never compare them as if they were the same money.
- `sizing.entry_cap()` is the single answer to “how much can this order actually use?”

Actual entry capacity is the minimum of:

1. regime ceiling minus current exposure;
2. cumulative gate-risk capacity converted through the binding stop, minus current exposure;
3. per-position at-risk capacity;
4. remaining portfolio shock budget;
5. remaining gross capacity;
6. account buying-power room;
8. event-day adjustment;
9. whole-unit feasibility.

`cash` from a broker payload is not automatically buying power. Use `quant/core/book.py` to assemble NAV, market value, buying-power room, ATR/price and portfolio usage.

### Structure, episodes and holdings

- `quant/core/structure.py` owns zone/gate relationships, current-regime derivation, binding stops and the structural-repair VETO.
- A gate is a transition threshold; a zone is an execution region inside a state. They must not be used interchangeably.
- Zones and gates must share auditable anchors where required. Partial redraws must trigger a same-anchor warning.
- `quant/core/episodes.py` owns “what counts as a structural breakdown,” gate-path classification and fast-lane eligibility.
- Fast-lane eligibility applies only to the validated repair path. Unclassifiable or non-repair paths retain the slower confirmation rule.
- `quant/core/drift.py` measures structure drift. Its outputs are advisory until thresholds are explicitly validated and wired.
- `quant/core/technical.py` owns shared indicator, ATR, volume-profile, swing and pattern math.
- `quant/core/dow.py` owns the short-horizon Dow phase machine and its descriptive priority key. It is read-only context and does not grant entry or exit authorization.
- `quant/core/holdings.py` owns every “is this held?” predicate.

The three holding sets are intentionally different:

```text
held_bare ⊆ live_tickers ⊆ held_incl_option_underlyings
```

- `held_bare`: nonzero broker quantity on that exact symbol.
- `live_tickers`: bare holdings plus required synthetic underlying rows that exist in the document.
- `held_incl_option_underlyings`: bare holdings plus underlying names implied by held option symbols.
- Do not “simplify” these sets into one predicate; watchlist membership, reports and option-underlying promotion have different semantics.
- `is_managed()` is independent of whether an asset is held. Structurally exempt roles remain in account totals but not in trading-management output.
- `underlying_cards_missing_zones()` scans the document directly so a zero-quantity synthetic underlying cannot disappear from infrastructure warnings.
- `is_candidate()` is the named entry-candidate predicate: `status == "watching"` **and** the broker-group membership flag is not false. Removing a card from the broker-side candidate group flips only that flag; the card and its `status` survive, so a status-only reader keeps treating a tombstone as a live candidate. Read the predicate; do not re-copy its two halves.
- `intel_universe()` is the single source of truth for the coverage universe handed to an external research or intelligence workspace: held bare quantities with options mapped back to their underlying, plus entry candidates, minus structurally exempt and reference instruments, with an audit reason recorded for every exclusion. It is a pure function and carries zero authorization effect.

## Sync layer: broker truth and reconciliation

Only `quant/sync/` writes broker blocks in positions/watchlist state.

- `state_io.py` validates every write and preserves blocks it does not own.
- `stock_fetch.py` refreshes positions, prices and available option Greeks.
- `order_sync.py` reconciles open orders into `pending_orders`.
- `trade_history_sync.py` appends deduplicated fills and closes to the event ledger.
- Event ingestion derives mechanical `tier` and BUY-side authorization snapshots through shared core functions. It records the stamp date, marks cross-day stale-card risk and freezes stock-fill zone/price-rule flags against the card visible at ingestion. If authorization cannot be derived, leave it empty and raise an infrastructure warning.
- `lot_reconcile.py` converts unaccounted incremental BUY fills into `lots.position` and updates `position_summary`.
- Lot reconciliation is idempotent and keyed by order identity. Candidate-card migration must run first so a re-entry fill derives lot plan fields from the promoted card rather than a stale closed card.
- Partial sells, ambiguous quantity mismatches and option-lot judgment remain review items; the reconciler reports instead of guessing.
- `close_reconcile.py` handles fully flat positions: archive closed lots, clear live lots, mark lifecycle state and compute available realized PnL from events.
- `watchlist_sync.py` reconciles broker-side candidate membership.
- `watchlist_migrate.py` moves analyzed cards across state files without letting an empty card overwrite a zoned card.
- Removing a security from a broker group uses the group-safe operation; never use a delete operation that removes it from every user group.
- `price_reminder_sync.py` reconciles only reminders owned by its note vocabulary. User-created reminders are never touched.
- The downside reminder is the binding maximum of card stop, profit-lock floor and trend trail, with basis kept separate for option premium versus underlying price.
- Z1 reminder notes use the actual output of `sizing.entry_cap()`, not a copied percentage.
- `company_profile_sync.py` writes the machine-owned business/balance-sheet region of a card from broker fundamentals; the prose region (business model, financing notes) is hand-written and never touched by the machine writer. It carries zero authorization effect.
- `valuation_sync.py` mirrors the latest ledger row of the valuation layer into the card's machine-owned `agent.valuation` block. The valuation layer never writes state itself; mirroring belongs to sync.
- `today_group_sync.py` maintains the optional entry-authorization group from explicit regime cap, structural validity, gate state, staleness and current capacity.
- Authorization state is not execution timing. A valid cap may wait for a zone; an attractive price cannot create a cap.
- Lot drift is reported as infrastructure state. Because exposure deduction reads broker quantity and price, drift must not silently create a second authorization formula.

## Data layer

- `market_data.py`: shared market-data access.
- `kline_cache.py`: incremental completed-bar cache. It must reject an incomplete daily bar during the session.
- `iv_logger.py`: ATM IV history used by IV gates and calibration.
- `nav_snapshot.py`: NAV and benchmark snapshot used by portfolio math.
- `earnings_calendar.py`: forward earnings/reaction-day cache consumed offline by sizing and reports.
- `macro_fetch.py`: dated informational context; never an authorization source.
- `skhy_premium.py`: instrument-specific premium history where enabled.

Data ingestion records source and freshness. A failed fetch must be visible and must not mutate an existing valid observation into zero.

## Decision layer

### Entry guard

`quant/decision/entry_guard.py` is the canonical entry/re-entry plan generator.

- Verdicts are `PLAN`, `VETO`, `STALE` or `NEEDS_CARD`.
- Market-price location is informational. A zone limit plan is not a market-order recommendation.
- `PLAN` includes the ladder, whole-unit sizing, binding constraint and stop basis.
- `VETO` means no entry plan: examples include cap zero, structural breakdown, unresolved repair, invalid gate, missing/stale map or hard risk constraint.
- Structure-repair VETO is binary; do not reintroduce a hidden intermediate classification discount.
- The gate number comes from `sizing.gate_count(card)` on both entry and add sides.
- Plans within the same verdict may be ordered by the short-horizon Dow priority. This ordering cannot change verdict, cap, ladder prices or whole-unit size.
- A repair-reclaim ladder yields to the ordinary zone ladder after a later full-map redraw; the old frozen transition anchor cannot override the newer map.
- Option eligibility starts only at the configured minimum gate and remains subject to IV, liquidity, premium and risk limits.
- A changed plan may append a decision worksheet; an unchanged render must not spam the ledger.

### Exit guard

`quant/decision/exit_guard.py` owns the mechanical exit stack:

- profit ratchet and one-shot stages;
- ATR/parabolic trend trail;
- option gain tiers;
- option premium-loss, time and DTE overlays;
- the underlying gate stop for an option opened at a cap-granting gate;
- margin/buying-power infrastructure warnings;
- re-arm rules after a valid recovery or new high-water mark.

Exit mechanics do not accept a narrative or macro exception. A process score may record delayed or rule-external behavior, but it is not an override and does not authorize continued holding after a mechanical breach.

### Other decision modules

- `regime_transition.py`: applies completed-bar gate transitions and preserves auditable history.
- A down transition must keep its reclaim placeholder self-consistent: stop below the reclaim gate, no-chase not below the gate, and a default volume-evidence leg when the card omitted one.
- `regime_score.py`: informational regime context used by configured execution rules.
- `structure_drift.py`: logs advisory drift observations.
- `catalyst_check.py`: checks time binding, tape contradiction, benchmark residual and “unknown catalyst” as a valid result.
- `level_map.py`: combines volume-profile HVN/LVN, pivots, level interactions and optional option-interest context.
- `tape_check.py`: intraday/short-horizon tape labels. It is `[FYI]` and has zero authorization effect.

## Options and risk layers

- `quant/options/option_plan.py` maps an authorized underlying plan into contract selection, expiry, delta, liquidity, premium ladder, contract count and underlying stop basis.
- `implied_move.py`, `premium_proj.py` and `strike_compare.py` provide deterministic contract/price transformations.
- Public option policy examples are schemas, not live policy.
- `quant/risk/kelly_size.py` and `chain_layers.py` are analysis tools. Their output does not bypass the core cap stack.
- Option premium paid, delta notional, theoretical maximum loss and underlying stop risk must be shown as separate quantities.

## Valuation layer

`quant/valuation/` turns fundamentals into a fair-value opinion. It is a judgement layer with **zero authorization effect**: architecture tests pin that it never imports sizing, entry, exit or authorization-group code, and that none of them import it. Undervalued is not a GO and overvalued is not a SELL — entry still belongs to zone and gate, exit still belongs to the exit stack.

- `fundamentals.py` fetches statements, snapshots, valuation percentiles and sell-side targets into an ignored cache; the cache is always refetchable and never enters Git.
- `archetypes.py` holds business-model prototypes and validates the hand-written input region. An unknown key is an error, not a silent skip: a mistyped override is worse than no override.
- `model.py` is pure math: a discounted free-cash-flow engine, an institutional-consensus engine, reverse-implied growth and margin, scenarios, weighted fair value, valuation duration and rate sensitivity.
- `valuate.py` orchestrates a run, renders it and appends one ledger line per valuation, carrying the input snapshot so the estimate stays auditable.
- `overlays.py` is the only way news reaches the model: a structured, validated, append-only record naming the instrument, channel, driver, magnitude, reliability and expiry. Low-reliability records are recorded and visibly ignored rather than applied. The model never reads prose.
- `calibrate.py` reconciles ledger predictions against realized results and re-runs point-in-time valuations for backtesting. Decision rules are pre-registered before the numbers are read.

Layer rules:

- Confidence must fall when the estimate leans on the terminal value, on a short history, or on a margin sitting at a historical extreme.
- A cyclical instrument is normalized rather than extrapolated from a peak; a peak-margin, low-multiple print is a trap, not a bargain.
- Premium paid, notional exposure and fair value are different quantities and are reported separately.
- The valuation layer never writes position or watchlist state. Mirroring a result onto a card belongs to `quant/sync/valuation_sync.py`, and the mirrored block is informational only.

## Journal and report layers

- `quant/journal/decision_log.py` appends structured EV/probability worksheets.
- `quant/journal/annotate_event.py` adds review fields to rule-external events, can append process scores, and provides a paired manual cap/gate stamp for historical BUY fills only after the operator verifies the authorization that existed at fill time.
- `quant/journal/exit_score.py` scores a structural exit mechanically: a closed card with an exit record and a demotion on file gets a latency-tier process score. It grades only latency and completeness — reasoning stays with the operator, its rows are recognizable by their source field, and it never backfills history before the rule existed. It is the counterpart of the unresolved-demotion callout: one tracks exits that have not happened, the other records the ones that did.
- Planned, mechanically authorized fills do not need duplicate narrative classification.
- Rule-external events do require review: examples include cap-zero entries, missing authorization-group membership, no-chase violations, trim-area adds, no-add-lock violations, unplanned limit changes or ignored exit alerts.
- Tier and authorization fields are mechanically derived where possible; the human supplies reasoning and checklist status.
- Lots are accounting, not annotation. Every fill must reconcile even when no narrative review is required.
- `quant/report/portfolio_brief.py` assembles position cards, alerts, budgets, exits and operational warnings. Any mirrored valuation reaches it as a clearly marked informational line that never enters the mechanical action ranking. Held-stock add capacity includes the same price ladder, whole-share split and structural ordering used by entry plans.
- Use `--read-only` for session inspection; the default alerting mode may consume cooldown state.
- Reports must preserve binding constraints and original warnings from their deterministic sources. Do not paraphrase away severity.
- `perf_report.py` provides tier/gate performance and process-versus-outcome review.
- `gate_ladder_report.py`, `gate_audit.py`, `anchor_receipts.py` and `chart_read.py` expose specific audits without creating authorization.
- Chart reading reports current structure, short-horizon Dow phase, confirmation line, invalidation line, distance and evidence quality. A Dow termination is a discretionary review point, not an exit instruction. Chart reading does not write state, size positions or unlock cap zero.

## Ops pipeline and stage order

`quant/ops/pipeline.py` is the canonical orchestration source. Task prompts must not carry a second handwritten pipeline.

The dependency order is:

1. account/buying-power snapshot where configured;
2. position broker refresh;
3. open-order synchronization;
4. fill/event synchronization;
5. close reconciliation;
6. structural-exit scoring, after close reconciliation writes the exit record and before candidate migration can overwrite the closed card;
7. watchlist membership reconciliation;
8. watchlist-to-position migration;
9. lot reconciliation;
10. `card_autopaint.py` detection and detached agent launch for eligible empty cards;
11. completed K-line update for close-dependent modes;
12. NAV snapshot before any consumer that sizes from NAV;
13. IV, earnings and optional premium datasets;
14. structure-drift observation;
15. portfolio brief after all producer stages and before any regime transition that can replace an exit boundary;
16. regime transition after exit evaluation in close-dependent modes;
17. broker reminder reconciliation from the post-transition card and same-run exit stack;
18. entry-authorization group reconciliation from post-transition state;
19. entry plan after current state, market data, NAV and transitions are complete.

`card_autopaint.py` must return without waiting for analysis. It excludes reference
instruments, unmanaged positions, option contracts and cards that already have zones.
`pipeline --read-only` converts it to `--dry-run`, because a detached card agent is a
state writer that cannot be recalled when the preview session exits. Scheduled use
requires a Claude CLI (`CLAUDE_BIN` may override discovery). The wall-clock guard
uses `timeout`/`gtimeout` when available, then a Perl `alarm` fallback; an
unenforced launch must report `timeout_enforced: false` rather than failing silently.

Mode semantics:

- `heartbeat`: periodic broker/state reconciliation plus risk/exit reporting; no incomplete-close transitions.
- `briefing`: full daily producer chain plus portfolio and entry plans.
- `postclose`: refresh close inputs, evaluate and emit exits against the pre-transition card, apply completed-bar transitions, then refresh reminders and authorization state.

Pipeline invariants:

- Producer stages precede consumers.
- Fills precede lot/close reconciliation.
- Close reconciliation precedes watchlist re-add logic.
- Structural-exit scoring runs between close reconciliation and candidate migration, so the closed card is still the one being scored.
- Watchlist-to-position migration precedes lot reconciliation.
- NAV precedes reminders, reports and entry sizing.
- Portfolio brief and exit evaluation precede regime transitions that may replace the breached stop.
- Regime transitions precede reminders and authorization-group synchronization.
- Portfolio brief runs after every state/data producer it consumes and before external reminder/group effectors. Its alert receipt prevents duplicate delivery by task wrappers.
- A fatal producer failure stops dependent consumers rather than letting them read half-updated state.
- OpenD-down and stale-heartbeat conditions are first-class infrastructure alerts.
- Preview or `--help` must never mutate state. Mutating CLIs should default to dry-run or require explicit `--apply` where practical.

## Optional scheduled tasks

`tasks/` versions six reusable prompts. The repository does not assume a personal task registry.

- `heartbeat.md`: periodic refresh, reconciliation and exit/risk alerts.
- `overnight-briefing.md`: pre-session portfolio and entry-plan briefing.
- `postclose-transition.md`: close-confirmed transitions and immediate downstream synchronization.
- `gate-ladder-report.md`: render gates after transition state is current.
- `chart-read.md`: completed-bar structure interpretation with zero authorization effect.
- `weekly-portfolio-review.md`: exposure, process, data-quality and discipline audit.

Task rules:

- The checked-in prompt is the source of truth. Register/copy it into a task runner explicitly.
- Keep task ordering consistent with the pipeline.
- Configure cadence in the operator's timezone and adjust for market daylight-saving changes.
- A task must stop if it is triggered in an invalid market session for its close-dependent work.
- Notification credentials remain in environment variables.
- Notifications are optional delivery adapters. A failed notification must not erase the underlying local report or state.

## Core decision model

### Probability and expected value

Investment reasoning uses posterior probability and expected value rather than a single conviction label or current-price anchor.

1. State the prior and whose information set it represents.
2. Separate information value from salience or narrative vividness.
3. Verify evidence before updating probability.
4. Include opportunity cost and alternative deployment in EV.
5. Pre-commit triggers, invalidation and missing-data behavior before observing the outcome.
6. Score process separately from outcome.

Valuation identities such as `P = EPS × PE` are one layer of the model, not entry authorization.

### Technical authorization and informational context

- Entry authorization—gate, regime, zone, cap and gate budget—is driven by price structure and executable state.
- Fundamentals, earnings, research, news and macro context are `[FYI]` unless an explicit sizing rule consumes them.
- The earnings reaction-day rule adjusts how large an authorized position may be; it does not decide whether structure authorized entry.
- After an earnings print, a card anchored before the reaction session becomes a repaint warning. The warning is visible and does not invent a new map.
- Mechanical stop, cap and infrastructure constraints win over prose summaries.

## Trading discipline

Before discussing execution, establish:

1. what current structure/regime authorizes participation;
2. what price action invalidates the trade;
3. what binding cap, stop-risk limit and whole-unit size apply.

Flag these failure modes explicitly:

- perfect-price anchoring;
- chasing above the card's no-chase line;
- averaging down without a new authorization event;
- revenge trading or outcome-driven rule changes;
- event-day improvisation;
- using a thesis or catalyst to override a stop;
- treating a cap as a target;
- confusing account cash with buying power;
- comparing option premium with underlying notional without conversion;
- acting on stale, incomplete or synthetic market data.

Position-management rules:

- `base` is reserved for a structural exemption encoded in state; every normal managed fill is `position`.
- Whole-unit feasibility is binding. Do not emit fractional shares/contracts that the venue cannot execute.
- Broker quantity and lots must reconcile independently of narrative review.
- Unmanaged roles remain in account totals but do not consume trading-management attention.
- Sell/reduce actions do not require entry-authorization group membership.
- The operator makes the final decision and submits any order manually or through a separately approved execution layer.

## State mutation and ownership

- Analysis requests are read-only unless the user explicitly authorizes a state write.
- Use `.claude/skills/trading-analysis/scripts/update_position_zones.py` for controlled zone/thesis/action writes.
- Zone writes preserve broker, lots and pending-order blocks.
- Option stops declare whether their basis is option premium or underlying price.
- A map write includes anchor date, methodology, trigger, invalidation, regime and provenance.
- Only completed bars can authorize a close-confirmed transition.
- Every read-modify-write cycle for state uses the shared lock and atomic writer.
- Every state mutation validates schema before commit to disk.
- Append-only ledgers use append helpers and stable event identity.
- Never add a one-shot migration that mutates on import or on `--help`.
- A one-shot migration defaults to dry-run, requires explicit apply, is tested on a copy and is removed after use unless it remains a supported tool.

## Session behavior

For a portfolio query:

1. Read `trade/us_stock/holding/state/positions.json` if it exists.
2. Treat missing local state as an uninitialized private workspace, not an empty live account.
3. Check target cards' broker block, lots, current regime, zones, action, invalidation, timestamps and pending orders.
4. Run the deterministic report/guard needed by the question.
5. Keep private values out of public artifacts.

For a ticker or trade discussion:

1. Establish symbol, instrument, session, timestamp and data freshness.
2. Read the relevant private research card if one exists; present it as information, not authorization.
3. Inspect structure, gate, regime, zone, stop and event state.
4. Run `quant/decision/entry_guard.py --tickers <TICKER>` for entry/re-entry or `quant/decision/exit_guard.py --read-only` for exit state.
5. Preserve the binding constraint and whole-unit result.
6. Separate the machine verdict from the user's final decision.

For analysis and state writes:

- Use the trading-analysis skill for a six-part analysis and controlled zone updates.
- Do not edit broker-owned blocks by hand.
- Log a probability/EV worksheet through `quant/journal/decision_log.py` when the EV framework is used.
- Record rule-external trade review through `quant/journal/annotate_event.py`, not prose alone.
- Use `quant/report/portfolio_brief.py --read-only --json` for an in-session brief.
- Use `quant/report/perf_report.py` for process/outcome review.
- If private files are missing, initialize only the required schemas through `trade/README.md`; do not fabricate a portfolio.

## Tools and environment

- Start local configuration from `.env.example`; keep populated `.env` untracked.
- Use an isolated virtual environment for broker dependencies because the Moomoo SDK has a constrained protobuf dependency.
- Keep OpenD and the Python SDK on compatible major versions.
- The sanitized `opend/` files are references only. Live OpenD configuration and RSA keys stay outside the repository.
- Broker-connected smoke tests are optional and require an explicitly configured local OpenD endpoint.
- Low-level scripts under `.claude/skills/openapi/scripts/trade/` are not part of the canonical pipeline and require explicit approval before any order mutation.
- Read `.claude/rules/tools-environment.md` and the OpenAPI skill before broker work.
- Normalize timestamps before applying market-window or daylight-saving logic.

## Research discipline

- Research is offline and cannot be imported by live modules.
- Private preregistrations, inputs, outputs, charts, rulings and working notes remain outside Git; the public layer contains only reusable, sanitized mechanics and synthetic examples.
- Pre-register hypotheses, metrics, thresholds, fallback branches and decision rules before reading final results.
- Use synthetic/adversarial fixtures to verify mechanics before historical data.
- Preserve failed results and limitations; do not tune a rule until it “passes.”
- Use shared production predicates in backtests when the research question concerns live behavior.
- Run the relevant validation study before changing calibrated exit, gate, sizing or structure parameters.
- A research conclusion becomes live behavior only through a reviewed production change plus tests.

## Verification and release gate

Run verification in proportion to the change, with the complete suite before publication:

```bash
python -m pytest -q
git diff --check
```

Required structural checks include:

- `tests/test_layering.py`: dependency direction, module depth and research isolation;
- `tests/test_module_coverage.py`: production/test mirror and coverage ledger;
- `tests/test_repo_references.py`: repository path and task-prompt references;
- `tests/test_live_data_isolation.py`: offline tests cannot consume live state;
- `tests/test_trade_scaffold.py`: `trade/` permits README/example files only;
- `tests/test_public_sanitization.py`: public identity/provenance sanitization.

Do not hard-code the test count in documentation. Collect or run the suite to obtain the current count.

Before handoff:

1. inspect the full diff, including untracked files;
2. confirm no secret, account, personal attribution or live-state data entered public files;
3. confirm referenced paths exist;
4. confirm examples remain synthetic and parseable;
5. confirm source and target Git bases were not unintentionally changed;
6. report skipped or unavailable checks honestly;
8. do not stage, commit, push, register tasks or send notifications unless requested.

## Definition of done

A change is complete only when:

- behavior matches the user's request;
- ownership and single-source-of-truth boundaries remain intact;
- safety and privacy defaults fail closed;
- tests cover the changed behavior and would fail under a meaningful regression;
- docs, tasks and examples describe the implementation that actually exists;
- the full relevant test suite and public sanitization checks pass;
- no required implementation, migration or verification work remains hidden in prose.
