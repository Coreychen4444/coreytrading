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

## Company reconstruction pilot

`company_model.py` is a separate explicit company engine, currently
`company-v5.2-research`. It does not inherit archetype growth, margins, a share
count inferred from EPS, or the old cash-flow-derived PE. The private input
catalog is `trade/valuation/company_models.json`, keyed by ticker; capture adds
it to the frozen evidence with ownership/hash checks. Assessment routes a ticker
with a packet to this engine. Expired, mismatched or invalid packets do not fall
back to legacy prices. Other tickers remain legacy diagnostics under the global
publication hold. No current company is approved for publication.

The explicit input contract uses schema `1.0`, currency `USD`, scale `millions`
for both money and shares. Every packet has `known_at`, fixed `valid_until`, a
source catalog (URL, captured-content SHA256, availability and capture clocks),
`source_refs`, and `basis`. Reported statements and each forecast scenario have
their own evidence/basis. The engine checks declared availability; source hashes
do not independently prove the truth of a transcribed fact or assumption.

- `segment_names` is the complete nonoverlapping set including corporate and
  intersegment elimination rows. `observed.segments` reconciles to independently
  transcribed consolidated revenue and profit. It must not contain both a parent
  subtotal and its children. Additional signed-component `identities` reconcile
  balance-sheet/cash-flow totals without silently repairing the source.
- Each forecast quarter specifies start/end dates, segment revenue/profit,
  separate interest income/expense, named nonoperating items, tax expense, NCI
  income, and explicitly defined after-tax adjustments to parent income.
  `adjusted_definition` distinguishes company non-GAAP from analyst adjustments.
- Shares have explicit basic weighted, incremental dilutive, closing, issued and
  repurchased amounts. Loss-period anti-dilution and a profitable adjusted basis
  use separate denominators. Closing shares must roll forward. Annual EPS is
  aggregate income divided by day-weighted shares, not summed quarterly EPS.
- Cash rolls forward from consolidated income with D&A, named noncash changes,
  working capital, capitalized cash interest, both investing/financing capex,
  grants, partner flows, debt, common equity cash, leases and distributions.
  The liquidity pool excludes restricted cash. Transfers within that pool are
  not external inflows. Minimum-cash shortfalls are explicit; no balancing debt
  or financing is invented. Positive equity cash requires explicit share issuance.
- `earnings_windows` names four consecutive fiscal quarters and the exact
  earnings definition. A quarter already partly elapsed is not called an exact
  next-twelve-month forecast. Independent PE cases require four quarters starting
  near the twelve-month target date, not the forward year at today's cutoff.
  PE comes only from explicit same-basis comparable/history evidence or a clearly
  labelled analyst sensitivity. Missing multiples remain unknown.
- Optional owner DCF discounts explicit common-owner cash per share and terminal
  common equity after financing, reinvestment and partner claims. It never adds
  cash/debt a second time or manufactures a terminal multiple. An unfunded path
  withholds both price calculations. Missing owner/terminal evidence stays unknown.

The CLI writes a new detached output and refuses to overwrite existing forecasts:

```bash
.venv/moomoo/bin/python3 quant/valuation/company_model.py --input INPUT.json --as-of ISO_TIMESTAMP --output NEW_RECEIPT.json
```

`company_validation.py` evaluates preregistered prospective EPS records against a
declared frozen baseline. It separates missing/future outcomes, excludes hindsight
and overlapping forecasts, rejects unresolved actual revisions, and reports MAE,
bias, baseline error and sample counts. It does not test share-price accuracy or
approve publication. Synthetic tests and current-statement reconciliation are
not historical validation of the new forecasts.

### Observed multiple panels (company v5.1)

`company_multiples.py` now derives the PE range for `comparable_forward_pe` and
`historical_same_basis_pe`; these methods reject manually supplied `low`/`high`.
Only `analyst_sensitivity` accepts explicit hypothetical bounds. All three remain
research outputs, and today's observed multiples are not a prediction of the
multiple the market will use in twelve months.

The multiple case supplies a `panel` with `selection` and `observations`:

- Selection records `registered_at`, `basis`, the complete unique `tickers`, and
  inclusive `start`/`end` observation dates. A current peer panel contains every
  named peer exactly once, at least three peers excluding the subject. A historical
  panel contains only the subject and at least eight nonoverlapping forward years.
  These are minimal research panel rules, not statistical acceptance thresholds.
- Each observation records `id`, `ticker`, and `comparability_basis`, plus `price`
  and `eps` objects. Both need `value`, `currency`, `share_basis`, `captured_at` and
  `source_ref` into the company's hashed source catalog. Price has `as_of`; EPS
  has `available_at`, `period_start`, `period_end`, `earnings_definition`, `kind`
  (consensus_forecast/company_guidance/analyst_forecast), `vintage_basis` and
  `value_origin=direct_earnings_forecast`. Price-divided-by-provider-PE EPS is not
  independent earnings evidence. Source hashes do not prove the declarations true.
- EPS must be known at the price observation and cover its forward year, with the
  exact accounting definition used by the target calculation. Price and EPS must
  have consistent currency and split/share bases. Current peer prices expire after
  seven days. Capture times never renew observation times.
- The range is the unweighted, linearly interpolated 25th/75th percentiles of
  observed PE, with median, each observation and all exclusions retained. Missing
  selected peers, invalid rows or overlap exclusions withhold the range rather
  than quietly select a favourable subset. A historical sample needs an explicit
  reviewed nonoverlapping panel. Dispersion is not a probability interval.

The consistency requirement follows the
[relative PE framework](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/lectures/relpe.htm).
The fixed sample floors, maximum age and percentile convention are our research
design choices and remain subject to independent review before any acceptance.

Earnings evaluator version 2 additionally requires explicit `tickers` in the
preregistered specification and the exact `spec_hash` on every forecast, valid
64-character input/receipt/source hashes, and actual capture after disclosure.
When the specification declares a fixed `baseline_eps`, each prediction must
retain that exact value. Timezone-normalized creation times select the earliest
nonoverlapping prediction. Earlier specifications/forecasts remain immutable;
create a new registered specification and new predictions for this evaluator,
never edit older records to make them pass. Hash syntax/binding does not verify
the underlying artifact contents, and this evaluator never approves publication.

### Disclosed opening balances (company v5.2)

Every packet now supplies an evidence-bearing `opening_balance` with `date`,
`cash_and_equivalents`, `liquid_investments`, `restricted_cash_outside_pool`,
`current_debt`, `noncurrent_debt`, `closing_common_shares`, and `debt_basis`.
Its `reported` object separately anchors `opening_liquidity`, `opening_debt`,
`opening_shares`, and `cash_including_restricted`. Arithmetic mismatches stop
forecasts. Every scenario must use the exact disclosed date and computed opening
balances; changing starting cash to cure a funding gap is rejected. Starting at a
different date requires an explicitly implemented intervening bridge, not prose.

Restricted cash is reconciled to the cash-flow statement but excluded from the
operating pool. Reported debt carrying value is not automatically principal or
all senior claims. Every quarter now requires `cash.debt_non_cash_change` to
reconcile noncash carrying-value changes separately from financing cash. Zero is
an explicit analyst assumption where supplied, not a missing-field default.


### Conditional business value to common equity

`company_equity.py` supplies a pure bridge at a single settlement date. The
optional scenario `business_equity` case declares USD/millions, full consolidated
operating value before financial claims, a complete list of `required_business_ids`
and `businesses`, `nonoperating_assets`, and a complete `required_claim_ids`/`claims`
inventory. Each row has `economic_id`, `date`, `basis`, and `source_refs`. Repeated
economic IDs across any component are rejected. Corporate operating values may
be negative; nonoperating additions must be excess unrestricted cash or separately
valued nonoperating equity, never restricted cash or consolidated subsidiary EV.

Claims select exactly one treatment: `cash` with a cash amount; `shares` with
incremental `new_shares` and incremental `cash_proceeds`; `net_share_warrant` or
`net_cash_warrant` with underlying shares/strike; `not_triggered` with explicit
false trigger state and rationale; or `unresolved`, which withholds the value.
Incompatible settlement fields are rejected. Net warrants require
`exercise_basis=immediate_exercise_at_bridge_date`. The monotone settlement equation
solves the common price without taking today's market price as an input. It does
not price an unexpired option's time value. Nonpositive residual wealth stays
unknown pending a restructuring model, rather than publishing a negative target.

The company host binds the date to an explicit forecast quarter end and the
outstanding share anchor to that quarter's closing shares, validates every source
reference, and withholds the bridge for an unfunded operating path. Separately
settled claims must not already be in those shares, cash/assets or operating
values; matching economic IDs can detect declared duplicates but cannot prove
the completeness/truth of an analyst's inventory. Business values and claim
economics still require independent review. This bridge is not a twelve-month
market target, never changes publication status, and has no accepted
business-value inputs. Missing cases return unknown.

### Explicit consolidated operating cash flow

`company_cashflow.py` calculates conditional operating value before financial
claims, with no market price input. A scenario may supply `operating_dcf` with
dated evidence, USD/millions, `date`, consecutive `periods`, and `terminal`.
Cashflow basis is `consolidated_unlevered_after_operating_tax`; SBC remains an
expense with no addback. Operating leases stay expensed; finance leases belong
in capital investment and the corresponding debt/claims bridge. Explicit tax
and capital assumptions are mandatory.

Each period declares operating profit, cash operating tax excluding financing
tax shields, D&A, gross capital investment, capital grants, working-capital
increase and WACC. Debt cashflows, interest, partner distributions and share
financing are rejected from this path. The host binds operating profit to its
complete forecast quarters where periods overlap; cash taxes and reinvestment
need separately reviewed assumptions rather than borrowing net-income taxes.
Period-end discount factors use actual elapsed days.

The terminal declares next-year normalized NOPAT, incremental ROIC, growth and
WACC. Required reinvestment is growth/ROIC; terminal cashflow is NOPAT times one
minus that fraction. Growth must be below WACC and no greater than ROIC. The
receipt reports explicit cashflow PV, terminal PV and its share of total value.
These arithmetic constraints do not validate the stable-state transition.

A `business_equity` case can set `use_consolidated_dcf=true`, require only the
`consolidated` business ID, and leave `businesses` empty. The host inserts its
computed operating value at the exact same date, refusing additional segment
values. Remaining assets/claims and closing shares follow the equity bridge
contract. Missing long-term inputs remain unknown. No consolidated
DCF assumptions have been accepted; this connection alone does not approve
valuation publication or establish forecast accuracy.

### Market-target evaluation protocol

`market_validation.py` evaluates explicitly frozen predictive prices separately
from operating value and EPS. It uses the first XNYS close on/after twelve calendar
months from forecast creation (Feb 29 maps to Feb 28), retaining actual close
times and the canonical calendar's supported-range failures. Prices and outcomes
use the same forecast-date split/share basis and exclude dividends. A genuine
zero outcome is retained, not dropped as an invalid positive-price observation.

The specification pins tickers, model version, alpha, and a constant-price
baseline with a fixed fractional halfwidth. Forecasts bind `spec_hash`, input and
receipt hashes, source/capture clocks for anchor prices, target close, share basis,
point/low/high and the nominal interval level. Baseline bounds are checked against
the frozen formula, not merely a matching prose label. Only `interval_kind=predictive`
can be scored: deterministic operating scenario extrema are not probability bands.

Outputs include anchor-normalized absolute price error versus unchanged price,
coverage, width, proper interval score versus the fixed baseline, and explicit
pending, overlapping, out-of-scope and abstained counts. Earliest nonoverlapping
forecasts are selected per ticker; cross-company common shocks are still a stated
dependence limitation. Revised actuals require explicit vintage selection.

This is a descriptive evaluator, not a release switch. It does not establish
provenance, calibrated probabilities or statistically significant outperformance.
The current diagnostic specification has no automatic acceptance thresholds;
those and the dependence-aware decision method need independent preregistration
before using evaluation results to promote a model. No current live valuation has
been accepted and no real price forecast is invented to populate the evaluator.

### 暂存发布与撤回接口

`releases.py` 提供纯函数发布日志校验、CAS准备及精确报告选择；
`quant/report/valuation_report.py` 的 `release_fyi` 每次渲染重新检查撤回和到期。
按公司与输出种类分别绑定输入、代码、候选、独立复核及五类验收证据。
最新发布失效不会回退旧价；重新发布旧候选须在此前发布事件之后重新复核，
并继续满足当前输入/代码与到期约束。条件公允价值范围与概率预测区间分开显示。

此接口默认关闭，尚未替代日更的全局停用边界。持久化控制器还需提供专用锁、
原子日志存储、可信复核来源、实时输入/代码摘要以及上线开关；纯函数CAS不等于
跨进程锁。哈希与不同reviewer_id不证明复核者身份、证据真实性或统计验收正确。
本地合成演练不能作为真实模型验收证据。现有市场评价的not_determined不得转换为passed。

持久化接口现由 `quant/ops/valuation_release.py` 提供：submit默认预览，apply时专用锁内
重新读取registry与host current_context，CAS检查后单文档原子替换，artifact与event同次可见。
同event_id同请求重试只返回已有提交，内容不同拒绝；already_applied是历史写入事实，
不表示目前仍获准发布。读取端每次检查当前撤回、输入/代码与可信review hash集合。
current_context应由宿主从真实当前状态提供，固定参数仅适合已冻结上下文和合成演练。
控制器不自行认证reviewer；可信复核来源的装配和生产日更接线仍待完成。全局目标价停用不变。

DCF期间可提供lease_details，明确同合同范围经营租赁费用/现金、本金/利息、新融资租赁资产和
已包含的融资租赁折旧。只有营运资本排除租赁应计、现金经营税已含经营租赁扣除且排除融资
利息税盾时才计算费用减现金的调整。新增finance资产须勾稽到总资本投入，本金/利息不会再扣
一次FCFF。不同范围的披露差额不能直接填写同范围标签；缺省不启用该附加桥，原输入口径
仍需各自审核。租赁分类变化后的税、折旧、租金与稳定期假设仍需完整预测。

income可显式选择nonoperating_basis=combined_net并给出net_nonoperating及定义，适用于只映射
到公司披露/指引净额的材料。此时不允许同时提供interest_income/interest_expense/
other_nonoperating，避免双计或用伪造零填补未披露分项。默认split_components兼容原输入；
合并净额不提供利率重定价暴露，也不能把其中投资收益解释为经常性经营盈利。

季度cash可提供income_cash_timing：tax_cash_paid_net允许净退税；interest_cash_paid与
interest_cash_received须成对输入，且明确不含已有capitalized_interest_cash。桥从利润表税费和
分拆利息出发，把选定应计额替换为现金，绝不改变EPS。combined_net可仅桥接税，不能凭净额
推断利息。必须声明WC、noncash_adjustments与other_cash_flows均排除这些选定项目；该声明
不证明源数据范围正确，仍需独立勾稽。未提供时收据为not_separately_bridged，不声称已核实
付款时间；本桥不生成税负债或应计利息余额，也不替代无杠杆DCF的经营现金税。

DCF期间可提供capital_details，按有形资产、无形资产（两者均排除融资租赁）、融资租赁、
其他经营资本分拆新增投资与已在EBIT扣除的D&A。四类必须齐备、分别说明依据，合计须与
FCFF资本扣除及D&A加回一致；与lease_details同时使用时融资租赁分项也须一致。
营运资本须排除上述资产，新增投资口径排除偿还旧融资，避免重复计入。余额变化不能直接
充当新增投资或期间摊销。缺省not_separately_reconciled；算式通过仅为arithmetically_reconciled，
source_completeness仍为not_established，零值需要证据而不是填写标签即可放行。全局发布停用不变。
