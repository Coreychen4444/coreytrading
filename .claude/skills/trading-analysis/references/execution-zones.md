# Execution Zones Method

## Purpose
Turn analysis into executable price zones instead of vague opinions.

Execution zones are not static notes. They are the quantified output of:
- event context
- K-line structure
- thesis / fundamental comfort
- sentiment positioning

## Four-layer model

### 1) Event layer
Ask:
- Did earnings / order / conference / macro shock change the setup?
- Is this an event-window trade or a normal swing trade?
- Did the event create a new range / new gap / new support?

Typical outputs:
- panic low
- event-day high / low
- post-gap retrace band
- breakout failure line

### 2) K-line / structure layer

**Data source**: moomoo OpenAPI K-line scripts (run with the isolated environment's interpreter).

```bash
# Fetch daily and weekly K-lines before zone computation
python3 .claude/skills/openapi/scripts/quote/get_kline.py US.{TICKER} --ktype 1d --num 20 --json
python3 .claude/skills/openapi/scripts/quote/get_kline.py US.{TICKER} --ktype 1w --num 12 --json
```

From daily K-line (20 bars), extract:
- **swing high / swing low**: highest high and lowest low over past 5-10 days
- **panic low**: sharp intraday wick with recovery (low much below close)
- **gap zones**: where open significantly differs from prior day's close
- **volume spikes**: days with 2x+ average volume → institutional participation signals
- **box/range boundaries**: high/low of consolidation range if price is ranging
- **retest levels**: prior breakout levels being retested as support

From weekly K-line (12 bars), extract:
- **weekly support / resistance**: multi-week highs/lows
- **weekly trend direction**: higher lows = uptrend, lower highs = downtrend
- **multi-week range**: 4-12 week high/low defines the broader structure

Zone mapping from K-line:
- `accumulation_zone_1` → daily swing low / retrace support / gap-support area
- `accumulation_zone_2` → deeper panic low / weekly support / prior major low
- `trim_zone` → daily/weekly resistance / prior high / extension area
- `no_chase_above` → near or above resistance without fresh confirmation

Do not fake indicators. If K-line data is insufficient, mark 待核实 and use event/thesis layers only.

### 3) Thesis / fundamental comfort layer
Ask:
- At what price would you still be happy to own more if the thesis is right?
- At what price is the market already pricing too much good news?
- What price still leaves room for thesis error?

### 4) Sentiment / positioning layer
Ask:
- Is the market in panic / washout / skepticism / squeeze / euphoria?
- Is the crowd under-positioned or already crowded?
- Is this a reaction low worth buying, or a broken stock pretending to bounce?

## Mapping rules

### accumulation_zone_1
Purpose: first tranche / participation right.
Usually comes from one or more of:
- support retest after event move
- upper part of a panic retrace band
- gap-support area
- breakout retest zone

Hard rule:
- zone 1 is the realistic first buy area
- it must not be replaced by zone 2 because price "might go lower"

### accumulation_zone_2
Purpose: deeper weakness / second tranche only.
Usually comes from:
- panic low zone
- prior major swing low
- deeper support / lower edge of event flush

Hard rule:
- zone 2 exists to add lower, not to justify cancelling zone 1

### trim_zone
Purpose: partial de-risk / profit-taking observation area.
Usually comes from:
- prior resistance
- event extension zone
- overhead supply area
- post-gap overextension

### no_chase_above
Purpose: prevent bad risk-reward entries.
Usually comes from:
- near or above short-term extension
- near resistance without fresh confirmation
- price where the original setup has already rerated too far

### invalid_if
Must be concrete, not poetic.
Good examples:
- loses 42-44 support on closing basis
- event catalyst fails / customer order credibility weakens
- earnings changes the margin/backlog story materially

Bad examples:
- if vibes change
- if market looks weird

## Timeframe rules
Use one of（= `update_position_zones.py --timeframe` 实际枚举）:
- swing_2w
- swing_4w
- event_window
- post_earnings

Default guidance:
- normal setup → swing_2w
- major catalyst just happened → event_window
- post-earnings reset → post_earnings

## Output quality standard
A good zone set should answer:
- Where should first tranche be waiting?
- Where can second tranche wait?
- Where should you stop chasing?
- Where does de-risking become reasonable?
- What clearly breaks the setup?

If it does not answer those five, it is not ready.

## Anti-self-deception rules
- Dynamic does not mean changing numbers whenever price moves.
- Recompute only when the basis changes: event, macro, structure, or expiry.
- Use ranges, not fake single-price precision, unless there is a truly obvious line.
- If uncertain, say 待核实 instead of inventing confidence.
