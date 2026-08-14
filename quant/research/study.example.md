# Study: <neutral title>

Status: preregistered
Owner: <review role>
Protocol receipt: <immutable content hash>

## Question

<One falsifiable question about one production behavior.>

## Hypothesis and null

- Hypothesis: <candidate changes the locked primary metric in the specified direction>.
- Null: <candidate does not meet the locked threshold>.

## Scope

- Observation unit: <episode, decision, position, or other explicit unit>
- Population: <source-independent inclusion rule>
- Exclusions: <mechanical exclusions fixed before results>
- Horizon: <explicit completed-observation horizon>
- Minimum sample: <locked count or effective-sample rule>

## Inputs

| Input | Source and license | Freshness | Missing-data behavior |
|---|---|---|---|
| `<dataset>` | `<private source>` | `<rule>` | `<fail, omit by locked rule, or inconclusive>` |

No account export, licensed dataset, private symbol list, or populated manifest belongs in the public repository.

## Mechanics

- Production predicate under test: `<module.function>`
- Baseline: `<locked definition>`
- Candidate: `<locked definition>`
- Primary metric: `<one metric>`
- Uncertainty method: `<method and parameters>`
- Multiple-comparison policy: `<policy>`
- Sensitivity analyses: `<non-decision analyses>`

## Synthetic verification

- [ ] Normal construction
- [ ] Boundary values
- [ ] Missing input
- [ ] Duplicate/overlap prevention
- [ ] Insufficient sample
- [ ] Support branch
- [ ] No-support branch
- [ ] Inconclusive branch

## Decision table

| Branch | Locked condition | Output |
|---|---|---|
| Support | `<condition>` | Eligible for a separate production proposal |
| No support | `<condition>` | Preserve incumbent behavior |
| Inconclusive | `<condition>` | No behavior change; state what evidence is missing |

## Results

Do not populate this section until the protocol receipt is recorded. Store populated results only in the ignored private study directory.

## Promotion checklist

- [ ] Limitations and counterevidence recorded
- [ ] Production owner reviewed the result
- [ ] Production implementation is separate from research code
- [ ] Production tests fail under the meaningful regression
- [ ] Documentation states scope and missing-data behavior
- [ ] Complete release verification passed
