# Pre-market briefing

Produce a human-reviewed operating brief before the US session.

0. Run `python3 quant/ops/agent_jobs.py due --kind briefing`. When it returns `due: false` (holiday, or the session already opened), stop with `BRIEFING_SKIP`; a calendar error is reported, never guessed around.
1. Run `python3 quant/ops/pipeline.py --mode briefing --json`. Never pass `--read-only` from a scheduled task. When `agents/routing.json` sets `execution_transport` to `desktop`, read `tasks/desktop-research.md` first and run `python3 quant/ops/agent_desktop.py launch --kind briefing` instead; serve this runtime's research requests until the round's completion file appears, then read the controller output file as the pipeline JSON. The launch receipt itself is only a handle.
2. Summarize data freshness and any infrastructure warnings first.
3. Read `stages.agents.data`: `reports.technical` (the only model research; `reused: true` means the technical run for the same close and card revisions was reused, not new research), `reports.risk` (`provider: host`, the code-rendered mechanical risk receipt whose `summary` is transcribed as is), `cards` (submission receipts) and `pending_cards` (targets beyond this round's `repaint_bound`: at least five, or ⌈N/2⌉ of a longer queue so any queue drains in two rounds). Only a card with `status == "applied"` was written; failed, abstained, review-rejected or pending targets are listed as unfinished, never as done. A top-level `partial.agents` entry means the domain job finished with missing roles or unwritten cards, not that the stage crashed; `failed` is reserved for real failures. Do not start research of your own or bypass the controller to repaint a card.
4. Present macro/event context as dated `[FYI]` facts with sources. This task
   does not fetch news, social posts or macro data itself: read the operator's
   authorized intelligence archive under `trade/intelligence/reports/` if one is
   configured, and otherwise state that there is no intelligence input rather
   than searching for one. The 🌐 macro `[FYI]` lines come from `python3 quant/journal/macro_lens_log.py --brief`,
   pasted verbatim (the macro lens as of the previous close: environment band and what the
   market is trading / strongest driver of the last 40 sessions and its reading / hand-over
   and calendar / overnight futures move); when the command fails or `current.json` is not
   `macro_lens/1`, write that there is no valid macro prior. It is zero-authority context: it
   never changes an action, PLAN / VETO, cap or share count. This task calls no macro model
   and adds no macro reasoning of its own. Valuation belongs to the independent daily
   valuation task and is not run here; when the brief carries a `valuation_fyi[ticker]`
   line (the latest research-grade 12-month target, unvalidated), append it verbatim to that
   ticker's basis line as zero-authority `[FYI]` (an upside midpoint is not 🟢, a negative
   FCF flag is not 🔴); a missing or stale daily run means no line and no fallback. Amounts and authorization come only from the
   mechanical reports.
5. Carry the local earnings calendar as `[FYI]`: which holdings and candidates
   report, the session (before or after the close), the reaction day, and the
   event-day size adjustment that follows from it. Name any card whose anchor
   predates a landed reaction day; that is a redraw obligation, not a veto.
6. Preserve the portfolio brief, exit alerts, entry verdicts, caps, ladders, stops and warnings from the deterministic output. Within each verdict, keep the short-horizon Dow priority from `entry_guard`; it is an ordering key only and cannot change authorization or size. For held-stock additions, copy the rendered `position_ladder` prices and whole-share tiers instead of inventing an unstructured add. Adding to a holding is allowed only at or above its average cost (lot-weighted; option contracts by premium): when a held card's budget line says the cost floor blocks adding, or its ladder line says there is no compliant add price today, list that holding as hold with no add below the average cost. Tiers below the average cost are already removed by the code and are never added back. When the overview shows `VETO+🔁` for a flat ticker, the bottom class produced a plan: list the stock limit × shares and/or contract × quantity × limit copied from the 🔁 block, with the stop-low (close ×1) and its valid-until date, after the gate-class PLAN tickers. A 🔁 block marked `VETO` is not listed, and held tickers never carry a 🔁 block.
7. Separate current holdings from watchlist candidates and clearly mark stale or missing cards.
8. Name fills that fall outside the rules, including a buy flagged `below_avg_cost` (it lowered the average cost of an existing holding), and ask for an annotation; fills inside the plan get one line.
9. Do not infer authorization from news, conviction or a chart narrative, do not offer a narrative override after a mechanical exit breach, and do not place orders.
10. Optionally send the reviewed result with `quant/ops/tg_notify.py --stdin` (rich messages by default; pipe the text, do not pass it as a shell argument).

The pipeline's immediate alert channel does not replace the full briefing. Preserve the alert receipt to avoid duplicating urgent cards, then send the reviewed briefing normally when configured.
