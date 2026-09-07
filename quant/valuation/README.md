# Valuation and conditional price research

This is a judgement layer with 零授权效力. It cannot change trading permissions.
The numerical implementation is authoritative; a successful research response
does not establish forecast accuracy.

## Outputs and meaning

The current intrinsic value is an FCFF valuation of the operating business plus
the equity bridge. Analyst targets are external comparisons, never a component
of intrinsic value. Bear/base/bull are conditional stress cases, not calibrated
quantiles. An expected value requires an explicit probability vector and basis.

The earnings-based twelve-month price is a separate conditional output. Its EPS
and multiple are shown separately; a multiple adjustment changes this output,
not the intrinsic cash flows. Negative earnings make this output unavailable.
Neither output predicts the date on which the market will recognize value.

Short-horizon prices are empirical conditional forecasts for 1, 5 and 20 trading
sessions. They require dated, mature event outcomes and an untouched calibration
sample. Missing evidence, insufficient samples or extrapolation produce unknown.
Daily price variation alone cannot identify a monetary-policy surprise.

## Macro transmission

Macro research precedes valuation research. The host computes conditional
scenarios from frozen inputs; workers cannot execute code or publish a baseline.
Every quantitative shock identifies its unit, cash-flow years, evidence and
assumption basis. A policy-rate shock is not a risk-free-curve shock: company
transmission coefficients must be explicitly supplied, never inferred from a
risk label. Unsupported channels remain visible and invalidate a complete
scenario price. Scenario calculations report partial contributions and their
interaction, not an arbitrary additive price rule.

All scenarios use the same company baseline, market-input version and price
time. Temporary revenue changes do not permanently raise the revenue base.
An explicit risk-free path changes discount factors; a terminal-rate shock must
be stated separately. Fixed-rate debt coupons do not instantly reprice.

## Input quality

Forward multiples with unknown earnings basis or horizon remain comparisons;
they are not relabelled as twelve-month consensus forecasts. Missing cash/debt
or discount-rate facts must not become zero or a silently manufactured rate.
Historical validation requires archived availability times and revisions; the
existing broker quarter history does not supply them. Fetching
an old observation does not refresh it. Daily receipts withhold calculations
when the latest financial period is over two hundred days old.

Overlays require visibility at the calculation cutoff, a fixed expiry and a
matching baseline when specified. Cancellation takes effect only when it was
known. A rolling next-event calendar cannot extend an old overlay. Quantified
source reliability does not establish the correctness of its coefficient.

## Validation

Synthetic tests check equations, units, curve direction, finite effects,
provenance, missing inputs, event maturity and the macro-to-valuation dependency.
They do not establish performance on real securities. Historical evaluation
must separate training, calibration and test periods, purge overlapping outcomes,
compare a simple baseline, report abstentions and keep all assumptions frozen.
Without point-in-time consensus or event surprises, the corresponding historical
claim remains unavailable. Production updates use the independent daily job in quant/ops/valuation_daily.py;
briefing and weekly trading jobs do not execute valuation.

## Calculation and migration

`model.py` is v4.2. Result schemas use version 1.1.0; every macro result must
contain valuation_scenarios, which can be empty. Legacy research outputs must be
regenerated against the new schema. Old engine_weights overrides are ignored
with a flag. Legacy probabilities require an explicit probability_basis; neither
the baseline nor a probability score is manufactured from absent probabilities.
Do not compare a v3 weighted target with a v4 DCF change as market attribution.

`assessment.py --input FILE --as-of TIMESTAMP` computes a frozen package without
network access. Optional --scenarios FILE reads scenario objects; --output FILE
writes only an explicitly selected research artifact. The independent daily workflow calls the same function after refreshing its own
company and completed-bar caches, then keeps the receipt with its separate report. The earlier valuate CLI keeps its ledger
interface and emits the same intrinsic/earnings definitions plus curve
sensitivities. Job receipts and the legacy valuation ledger remain separate. This module
registers no schedule.

The baseline uses a declared flat US10Y curve proxy and model ERP assumption
unless a timed explicit market assumption is supplied. Growth, margins,
sales-to-capital, beta and terminal RONIC retain visible archetype assumptions.
An industry-specific segment model, complete financing schedule, operating
cash versus excess cash split, option claims and future dilution are not yet
implemented. RONIC convergence is an assumption, not demonstrated persistent
competitive advantage. Historical buybacks do not automatically shrink future
shares. Missing net interest makes earnings targets unavailable.

## Financial evidence and capture vintages

`quant/data/sec_facts.py` reads an explicit SEC companyfacts file or makes one
request for a verified CIK with the caller's identifying User-Agent. It selects
USD instant facts by exact period and filing date. Components must be explicitly
disjoint and come from the same accession; missing, conflicting, duration or
currency-mismatched facts withhold the result. It does not infer a zero from an
absent tag, choose similar concept names, discover issuer identities or parse
custom/segment facts. The endpoint's coverage is described by the
[SEC API documentation](https://www.sec.gov/search-filings/edgar-application-programming-interfaces).

`evidence.py --bundle FILE --sec-input FILE --mapping FILE --as-of TIMESTAMP`
prepares a detached bundle and selection receipts. The mapping explicitly names
ticker, CIK, identity_basis, reviewed_at, provider_period_end, period_end,
period_basis and fields. Each field contains concepts and a basis explaining
completeness and absence of double counting. Provider calendar dates can differ
from filing period ends; corrections require this mapping and are never inferred
by nearest date. See `trade/valuation/sec_mapping.example.json`.

The prepared bundle carries balance_evidence and evidence_prepared_at. The daily
assessment applies the evidence in memory and reports applied/rejected entries;
the original broker cache is untouched. Each evidence row includes ticker,
field, nonnegative USD value, period_end, available_at, reviewed_at, source,
source_hash and basis. Contradictory values invalidate the field. Supplements
are an explicit analysis input, not an automatic override of broker truth.
Preparation output contains a bundle member; a caller must explicitly place
that member in its frozen fundamentals input. No live cache is installed by the
preparation CLI. Historical publication times for the other statements remain
unknown even when one balance field has a filing timestamp.

Future `fundamentals.save_bundle` refreshes preserve both old and new contents
under the cache's snapshots/TICKER/HASH.json directory. `load_vintage` selects
only captures available by a cutoff and rejects conflicting same-time versions.
This starts a capture history; it does not reconstruct vintages never saved.
The source's period timestamp is retained separately, without relabelling it as
a publication timestamp. TTM computation rejects missing/nonconsecutive recent
quarters, and incomplete historical bridge fields cannot silently become zero
inside invested-capital calculations.

A forecast used to anchor the full valuation must include available_at,
valid_until, period_start, period_end, source and basis, as well as its twelve-month
and earnings-basis declarations. It expires at valid_until or after ninety days;
the projection period must span roughly one year and start within forty-five
days of the valuation date. This is a stated alignment tolerance, not exact daily
consensus interpolation. Unverified forecasts remain comparisons.

## Rate exposure from company disclosures

An optional inputs.tickers.TICKER.rate_exposure profile contains repricing assets
and debt in USD after hedges, their explicit pass-through fractions, tax rate,
financial period, known_at, valid_until, source, basis and start_year/end_year.
The annual after-tax interest sensitivity to one policy basis point equals
(assets × asset pass-through − debt × debt pass-through) × (1 − tax rate) / 10000.
An expected reduction in asset income can offset lower floating-debt cost.
All balances, tax treatment, hedge effects and pass-through assumptions require
evidence; fixed debt is not automatically treated as repricing debt.

The derived channel is limited to the declared forecast years and never enters
terminal earnings. It does not estimate the change in long-term yields, ERP,
demand or multiples. An exposure-only policy scenario remains partial until a
curve transmission is supplied. An explicit coefficient for the same interest
channel cannot coexist with the derived profile. General transmissions may also
declare start_year/end_year/terminal together; absent bounds retain the explicitly
supplied scenario's duration. The current shock contract changes annual rates;
within-year reset dates, rate floors and nonlinear hedge payoffs are not modeled.

## Shock units and attribution

| Factor | Unit and meaning |
|---|---|
| rf_bp, erp_bp, debt_spread_bp | Basis-point changes in annual discount inputs |
| policy_rate_bp | Policy shock requiring an explicit transmission coefficient |
| revenue_pct, pe_pct | Fractional changes; 0.10 means ten percent |
| margin_pp, inflation_pp | Fractional percentage points; 0.01 means one point |
| demand_pct, usd_pct, energy_pct | Fractional macro changes requiring transmissions |
| reinvestment_usd | Additional cash investment in named forecast years |
| net_interest_usd | Additional after-tax net interest income in named years |

Every shock has start_year/end_year and terminal. pe_pct refers only to the
year-one multiple. A temporary rate shock leaves the terminal rate unchanged.
A terminal rate change must include the last explicit forecast year. Two
simultaneous macro channels may share a driver only when their supplied bases
justify adding the effects; direct and transmitted duplicates withhold a
complete price. Missing channels remain partial. The supported vocabulary does
not cover every possible EPS/PE driver.

Single-factor contributions hold everything else fixed. The joint result minus
these contributions is reported as interaction; it is not a causal allocation.
Cash-flow discounting uses annual forward discount factors with midyear timing.
Temporary revenue increments expire without raising terminal revenue. The
simple capital-intensity model does not release investment on revenue declines.
A finite legacy overlay is pinned to a complete bundle hash; any bundle refresh
invalidates it until its owner checks baseline absorption and republishes it.

## Short-horizon data and validation

The response model consumes the optional private event/query files described in
`trade/valuation/README.md`. It fits standardized ridge regression to
nonoverlapping mature events, reserving a later set for absolute-residual
interval calibration. At least forty training events (or ten per factor) and
twenty calibration events are required. Constant/collinear factors or feature
values outside historical marginal ranges produce unknown. Remaining joint
support and regime stability still require research review.

`response.walk_forward` fits only data available before each test anchor and
reports untouched-test MAE, interval coverage, abstentions and a zero-return
baseline for each horizon. Hyperparameters, feature definitions and test start
must be frozen before viewing test results. Calibration diagnostics alone do
not qualify a model for production. Predicted prices are exp(predicted log
return) times the supplied spot, not an expected arithmetic price.

`event_data.py --events FILE --prices FILE --as-of TIMESTAMP` reconstructs samples
from explicit event evidence and an explicit split-adjusted daily price package.
The first builder supports policy-change surprises: actual basis-point change
minus a sourced expectation known before the event. A fully expected cut has
zero surprise. It does not scrape news or manufacture historical expectations.
The price package declares ticker, source, price_basis and bars with session,
close and available_at. A source fetching an old bar today must not pretend it
had a verified availability timestamp at the original close.

The anchor is the first regular close after the information became known;
horizons are the following 1/5/20 XNYS sessions. Holidays, early closes and missing
or duplicate bars are checked by the calendar and builder. Incomplete horizons
remain pending. These are daily follow-through estimates; the announcement-minute
jump and any move before the anchor are not measured. That requires a separate
intraday event dataset. The build receipt contains an events array that can be
passed directly to the evaluation CLI; production response_events.jsonl receives
the array's rows via an explicit owner import, not an automatic state write.

The evaluation CLI is `response.py --input FILE --features FEATURE --test-start
TIMESTAMP --as-of TIMESTAMP`, with optional --cohort FILE and --output FILE.
It excludes outcomes unavailable at the evaluation cutoff and reports pending,
abstained and overlapping test samples separately. Cohorts distinguish ticker,
feature_schema and anchor_convention; samples with different meanings cannot
be pooled merely because their feature columns have the same name.

Daily short-term queries require an explicit cohort and may supply a separately
sourced, timed spot. They compute independently of DCF inputs and have a separate
status. Computed intervals are labelled research_estimate with unestablished
test performance; passing synthetic tests or calibration diagnostics never
silently promotes them into validated forecasts. A production query's anchor
must match its training cohort. Daily close follow-through cannot be applied to
an arbitrary intraday or several-days-later spot.
For first_close_after_information queries, information_known_at determines the
required closing timestamp; spot.as_of must equal that close and the query's
features must have been known by it. The daily host enforces this check.

## Remaining data work

| Missing input | What can supply it | Consequence while missing |
|---|---|---|
| Complete cash/investment/debt bridge | Source balance sheet and reviewed concept mapping | Intrinsic price unavailable |
| Operating cash, investment tax and other claims | Company disclosures and explicit equity bridge model | Existing simplified bridge remains an assumption |
| Comparable forward EPS/revenue | Broker estimates with period and accounting reconciliation | Historical/run-rate assumptions remain in use |
| Company rate/FX/demand exposure | Debt/hedge/segment notes and supported sensitivity estimates | Unsupported macro channels remain partial |
| Before-event expectations and consistent prices | Existing broker history or owner-provided public event archive | No fitted real-event short-term forecast |
| Untouched historical tests | Frozen vintages, event samples and a prespecified evaluation period | Accuracy improvement remains unestablished |

Current public sources and broker caches may not supply every item. New provider
subscriptions and importing reviewed live evidence require separate work. The
independent schedule is registered in the operator's task runner; numerical
modules do not register tasks.

The older calibrate backtest remains an approximate fundamental-path diagnostic:
it uses revised quarters, an assumed publication lag and constant rates. Its
receipts explicitly mark point_in_time_validated=false. Scenario bands are
stress cases, and their displayed score is not a Brier score.

Cash-flow and discount-rate consistency follows the standard
[DCF input framework](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/lectures/dcfinput.html).
Monetary-policy event evidence concerns the unexpected component and varies by
sector; historical aggregate estimates are not a reusable stock-price formula
([Federal Reserve research](https://www.newyorkfed.org/research/staff_reports/sr174.html)).
Residual intervals also depend on distributional assumptions; time dependence
and regime change can break nominal coverage
([conformal prediction under nonexchangeability](https://www.stat.cmu.edu/~ryantibs/papers/nexcp.pdf)).

## Host integration (v4.2)

Daily macro and valuation workers receive the existing company profiles and
macro observations, including provider quality flags and original units. Role
views show the latest three macro captures with an explicit coverage/hash receipt;
the host retains the full archive for calculations. Profile
prose is research context, not a numeric coefficient or an independent second
source. Financial supplements still require reviewed explicit evidence.

Daily valuation takes its price from the latest locally captured completed XNYS
bar when available, separately from the financial cache. The price observation
and capture times remain visible; a quote older than four days, unavailable at
the cutoff or without a source is withheld. This is a stated daily-cache limit,
not a live-price guarantee. Repricing preserves the cached share anchor, not a
newly fetched share count. Neither prices nor profiles rewrite broker caches.

Daily assessment and the weekly CLI share financial identity, disclosure/capture
cutoff, latest-period age, SEC supplementation and market-assumption checks.
The macro snapshot's generated_at_utc and US10Y-specific unusable/stale
flags are enforced. Official daily yields remain background, not verified
historical release vintages. Other unusable assets do not invalidate a usable
US10Y. Manual overlays also need fixed expiry; visibility, baseline and finite
years survive their adapter, and all consumers use the calculation timestamp.

The independent daily task publishes its own receipt and report under
var/valuation-daily/. Baseline value, twelve-month earnings price, conditional
sensitivities and missing short-term evidence remain separate. The latest pointer
records the latest attempt even on failure; historical reports remain dated.
Briefing and weekly jobs do not calculate, refresh, synchronize or append valuation
sections. The old valuate CLI and prediction ledger remain compatibility tools;
new daily frozen predictions live in this job's immutable run archives. The task
also reconciles mature predictions in the older ledger, without claiming that
this establishes accuracy for the new daily forecasts. See
tasks/codex/valuation-daily.md for ownership, schedule and output requirements.

These changes repair data use and delivery. They do not validate forecast
accuracy, recover historical consensus vintages, turn qualitative financing
notes into rate exposures, or measure announcement-minute price jumps.
