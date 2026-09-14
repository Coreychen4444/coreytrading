---
name: trading-analysis
description: Structured multi-step technical + risk analysis for a stock or listed ticker using the workspace workflow. Use when the user asks to analyze a stock, compare long/short cases, produce a trade plan, or requests `/analyze [ticker]`. Best for US listed equities and current holdings. Combines (1) the project contract and trading rules, (2) current price context from moomoo OpenAPI (via OpenD), the `trade/portfolio/state/positions.json` broker block, or a Stooq fallback, (3) [FYI] intelligence ONLY from the operator's optional local intelligence archive under `trade/intelligence/reports/` (this skill never fetches news, social posts or macro data), and (4) a final risk-reviewed action recommendation. Also use it to produce dynamic execution zones (accumulation/trim/no-chase/invalidation) and refresh `positions.json` (active) or `watchlist.json` (candidates) `agent.zones` after analysis.
---

# Trading Analysis

中立领域运行入口见 `agents/README.md`：宿主冻结证据，`technical` 角色产生提案，另一运行器复核后由受控入口调用同一个 writer。
隔离 worker 只有研究权限，不执行下文的数据刷新、写回、估值初始化或消息命令；这些步骤由宿主与既有所有者承担。
下文保留交互分析流程。缺卡与定时重画由 `quant/ops/agent_jobs.py` 接管，跨运行器复核后才受控写卡；仅 `submission.status=applied` 表示已写卡。

Use a **five-step workflow** inspired by TradingAgents, but grounded in this workspace's actual tools and data sources. 情报层已收拢：旧的「新闻分析师 + 情绪分析师」合并为只读情报归档的「情报回顾」——本 skill 专注技术分析 + 风控，零自行拉取新闻 / 社媒。

## Real data-source mapping

### Price / technical context
Use in this order:
1. **moomoo OpenAPI** via the `openapi` skill（美股 + 港股实时行情，日常主源）
   - 行情脚本：`.claude/skills/openapi/scripts/quote/get_snapshot.py`、`get_stock_quote.py`、`get_kline.py`（一律用隔离环境的解释器跑）
   - 持仓查询：`.claude/skills/openapi/scripts/trade/get_portfolio.py`
   - 连接配置：OpenD 本机 `127.0.0.1:11111`，可选 RSA 加密（详见 `.claude/rules/tools-environment.md`）
2. `trade/portfolio/state/positions.json` 的 `tickers["<T>"].broker`（current_price / change_pct / as_of / unrealized_pnl_usd 等）
3. 本地缓存：`trade/market/klines/<T>.jsonl`（`quant/data/kline_cache.py` 增量维护）+ `market/iv_history/<T>.jsonl`（IV percentile：`quant/data/iv_logger.py --percentile <T>`，样本 <60 会明示不可靠）
4. `python3 .claude/skills/openapi/scripts/trade/get_portfolio.py --currency USD --json`（刷新持仓 + 行情；非港币计价账户必须传 `--currency USD`，否则 server 默认 HKD）；持仓写回用 `quant/sync/stock_fetch.py`
5. Stooq fallback (only if OpenD down)

### News / sentiment（情报层收拢：本 skill 零自行拉取）
情报（新闻 / 宏观叙事 / 社媒情绪）由 operator 自有的可选情报工作区负责获取。本 skill 只做技术分析 + 风控，情报侧唯一动作 = **只读**该归档（若配置，`trade/intelligence/reports/` 最近 1-2 日目录，找该 ticker / 其产业链相关条目）：
- 归档有 → [FYI] 引用（带日期+来源），零解读，不驱动授权
- 归档没有或未配置 → 写「情报层：无输入」，**不做 web_search / 不搜社媒**
- 用户在对话里贴的研报 / 新闻可以用（用户提供 ≠ 本 skill 拉取）

### Rules / behavior context
CLAUDE.md 与 `.claude/rules/` 已自动加载；额外读 operator 的私有策略规则（由 `trade/strategy/*.example.md` 派生，先看该目录 README）：
- trading-rules（master index = 全决策栈入口）
- pre-trade-checklist
- 两阶段入场规则（若 operator 维护）

Do **not** invent prices, valuation numbers, technical indicators, social statistics, or fundamental metrics. If unavailable, label them **待核实**.

## Pre-flight checklist

Before analysis:
1. Read the private trading-rules file if the discussion may lead to a trade decision.
2. Read the private pre-trade checklist if the user is preparing to trade.
3. Read the private entry rules if the analysis may lead to an entry/add decision.
4. Refresh quotes: 港股优先 moomoo OpenAPI；美股 `python3 quant/sync/stock_fetch.py`（写 broker block 到 state/positions.json，底层按账户传 `--currency USD`）。
5. Read `trade/portfolio/state/positions.json` 的 `tickers["<TICKER>"]`：取 `broker`（行情）+ `agent.zones / thesis / lots / pending_orders`。如果 ticker 不在 positions 里，看 `state/watchlist.json` 是否有候选 entry。
6. Determine whether the ticker belongs to:
   - `current` = real active holding（在 positions.json 且 broker.qty != 0）
   - `watchlist` = intended entry but not yet held（在 watchlist.json，或 positions.json 里 broker == null / qty == 0）
7. Optional — 读 `trade/portfolio/analysis/<TICKER>/`（示例 `analysis.example.md`）看是否有先前分析积累（frontmatter `last_updated` 判断新鲜度）。
8. Search durable memory only when it adds useful prior context (past thesis, prior mistakes, existing position context, repeated lessons). Avoid fishing for noise.

## Dynamic execution-zone policy

Execution zones are **analysis outputs**, not static notes.

Every meaningful trading analysis should try to produce these fields when enough evidence exists
（字段名 = v2 schema 实名，与 `agent.zones` 一致）:
- `accumulation_zone_1`
- `accumulation_zone_2`
- `trim_zone`
- `no_chase_above`
- `stop_level`（期权 entry 必须配 `stop_level_basis`: underlying | option_price）；**重画必须显式传 `--stop-level`**（保持 = 传原值 + 原 `--stop-level-days`；不传 = writer 拒绝，不是保持；只传 `--stop-level` = 并入不重画）
- `invalid_if`
- `timeframe`
- `valid_until`
- `update_trigger`
- `reasoning_summary`

**归原型 = 画卡的一部分**：新票首次画卡（或估值层人工区还没有它）时，
写完 zones 立刻 `python3 quant/valuation/archetypes.py set <T> --archetype <platform|semi_cyclical|ai_infra_capex|software_growth|not_applicable> --rationale "<商业模式一句话>"`
（原型参数与语义见 `quant/valuation/archetypes.py` ARCHETYPES；明确知道比缺省更准才加 `--override key=value`），
再 `python3 quant/valuation/valuate.py --tickers <T> --refresh --write` + `quant/sync/valuation_sync.py --tickers <T>` 把估值镜像进卡。
漏了不拦截：`update_position_zones` 写盘后 ⚠️、brief `INFRA:card_missing_archetype` 每轮点名。判断层零授权。

**重算 zone 前必跑 momentum diagnosis**（`.claude/rules/zone-maintenance.md` 第 4 步）：
Q1 6m ≥ +150%？ Q2 20d 内有单日 ≥10%？ Q3 距 ATH < 10%？ 全 YES → **parabolic-mode anchor**
（trailing 5d/10d high 锚，不是 ATH retrace），写回时必须传 `--methodology parabolic_mode_spirit`。
Staleness 检测已代码化（heartbeat 的 portfolio_brief 自动报 `⚠ ZONE STALE`）——本 skill 负责"刷新"那一半。

### Zone philosophy
- `accumulation_zone_1`: first participation zone. This is the first tranche zone that secures participation.
- `accumulation_zone_2`: deeper weakness zone. This is the second tranche zone, not a replacement for zone 1.
- `trim_zone`: area where partial profit-taking / de-risking becomes reasonable.
- `no_chase_above`: price above which buying is no longer attractive without a new confirmation or thesis update.
- `invalid_if`: the concrete condition that breaks the setup or makes the prior zone obsolete.

### Hard rules for zone generation
- Do **not** create fake precision. If evidence only supports rough bands, use rough bands.
- Zones must be tied to actual context: support/resistance, post-event range, prior gap, valuation comfort, event window, or sentiment washout/repricing.
- Zone 2 must not replace zone 1. It exists only as the deeper-weakness tranche.
- Dynamic does **not** mean emotional. Do not rewrite zones intraday just because price moved. Rewrite only when the analysis basis changed.

### Zone refresh triggers
Refresh zones when any of these is true:
1. earnings / guidance / conference / product event materially changed the setup
2. macro regime changed (rates, tariffs, geopolitics, policy shock)
3. price structure broke or confirmed a key range
4. previous zone is stale (`valid_until` passed)
5. the user explicitly asks to refresh / re-analyze / update trade plan

### Default zone validity
Use one of these and say which one applies（= `update_position_zones.py --timeframe` 实际枚举）:
- `swing_2w`
- `swing_4w`
- `event_window`
- `post_earnings`

Default to:
- `swing_2w` for normal trade planning
- `event_window` when the main driver is a known event

### Write-back policy
After analysis, if the zones are specific enough to be actionable, update the matching v2 state file via the bundled script (which writes only `agent.zones` / `thesis` / `action` / `key_risk` / `invalid_if` and preserves `broker` / `lots` / `pending_orders` / `exit_info`):
- active holdings → `--bucket current` writes `trade/portfolio/state/positions.json`
- not-yet-held ideas → `--bucket watchlist` writes `trade/portfolio/state/watchlist.json`

Writes to local live state require explicit user authorization; analysis alone is read-only. Use the bundled script:
- `.claude/skills/trading-analysis/scripts/update_position_zones.py`

Preferred pattern:
```bash
python3 .claude/skills/trading-analysis/scripts/update_position_zones.py \
  --ticker {TICKER} \
  --bucket {current|watchlist} \
  --instrument-type {stock|option} \
  --role {role} \
  --zone1 {LOW1} {HIGH1} \
  --zone2 {LOW2} {HIGH2} \
  --trim {LOWT} {HIGHT} \
  --no-chase-above {PRICE} \
  --stop-level {PRICE} \
  --stop-level-basis {underlying|option_price} \
  --invalid-if "{condition}" \
  --timeframe {swing_2w|swing_4w|event_window|post_earnings} \
  --methodology {normal|parabolic_mode_spirit} \
  --valid-until {YYYY-MM-DD} \
  --update-trigger "{trigger text}" \
  --reasoning-summary "{short basis}" \
  --thesis "{optional short thesis}" \
  --key-risk "{optional key risk}" \
  --notes "{optional notes — 幂等，重复跑会替换不会堆叠}"
# --remove-from-other 仅在 watchlist→current 迁移时加
# 写入带 schema 校验 + 跨进程锁；broker/lots/pending_orders 完整保留
```

If writing back:
- upsert only the analyzed ticker
- preserve other tickers already in the file
- set `last_updated` using current UTC time
- set `valid_until` explicitly when possible
- set `zone_reasoning_summary` to a short 1-3 bullet style string or concise sentence

If the ticker migrated from watchlist to current because a real position was opened (note: `quant/sync/watchlist_migrate.py` automates this when broker.qty != 0):
- update it via `--bucket current` (writes positions.json)
- remove it from watchlist.json using `--remove-from-other`
- keep watchlist.json free of stale active positions

If the analysis is too uncertain to produce useful zones:
- say so explicitly
- do not force write-back
- use `待核实`

## Required five-step workflow

Follow all five steps in order. Do not skip. Do not give the final trade recommendation before steps 1-4 are complete.

---

## Step 1 — 基本面分析师

Analyze:
- revenue / profit trend
- valuation context (PE/PB/PS if available; otherwise mark 待核实)
- balance sheet / cash flow quality if available
- business model / moat
- recent major events: earnings, guidance, customer/order updates, management changes, policy
- compare against prior thesis or memory when relevant

Allowed evidence:
- user-provided numbers（用户贴的研报 / 财报数字——用户提供 ≠ 本 skill 拉取）
- **卡片 `agent.company_profile`**（商业模式 + 资产负债表 + TTM 资本强度）——先读它；机器区数字带 as_of/source 直接引用
- **OpenD 结构化财报**（自有量化数据，非情报层）：`quant/sync/company_profile_sync.py --tickers <T>`
  重拉机器区；底层 `get_financials_statements.py` 可查明细
- **卡片 `agent.valuation`**（估值层摘要：判定 / 公允 vs 现价 / 反向隐含 CAGR / 置信 / 生效新闻数，
  `valuation_sync` 从 `valuations.jsonl` 镜像）——thesis 里引用它的数字而不是散文；要最新的跑
  `python3 quant/valuation/valuate.py --tickers <T> [--refresh]`（`--path` 看逐年路径）。
  **判断层零授权**：估值不改 zone/gate/cap，低估不是入场理由，高估不是退出理由；低置信票引用「要求 CAGR」不引用公允价
- 情报归档条目（若配置）
- known workspace thesis files
- **不自行 web_search**（情报层归 operator 的情报工作区）；if hard numbers are missing, say so explicitly and mark 待核实

Company profile 维护（本 step 顺手做，尤其财报重画 SLA 触发的重画）:
- 机器区过期（`balance_sheet.as_of` 早于最新 print 的报告期）→ 跑
  `company_profile_sync.py --tickers <T>` 刷新
- 商业模式 / 融资结构有实质变化（新可转债 / ATM / 大额发债 / 资本结构重组）→ 用
  `--ticker <T> --business-model "..."` / `--financing-notes "..."` 更新人工区
  （唯一 sanctioned 写法，直接编辑 JSON 禁止；机器区永不手改）

Output format:

【基本面报告】
评级：强劲 / 一般 / 偏弱
关键结论：
- ...
- ...
- ...
数据说明：
- 用户提供：...
- 已核实：...
- 待核实：...

---

## Step 2 — 技术分析师

### Data collection (mandatory before analysis)

**Fetch K-line data** using moomoo OpenAPI. Run these commands and use actual output:

```bash
# Daily K-line: recent 20 trading days
python3 .claude/skills/openapi/scripts/quote/get_kline.py US.{TICKER} --ktype 1d --num 20 --json

# Weekly K-line: recent 12 weeks
python3 .claude/skills/openapi/scripts/quote/get_kline.py US.{TICKER} --ktype 1w --num 12 --json

# Current snapshot for intraday context
python3 .claude/skills/openapi/scripts/quote/get_snapshot.py US.{TICKER} --json

# Intraday 60m K-line（异动日 / entry timing / washout 结构讨论时必拉）: ~8 sessions
python3 .claude/skills/openapi/scripts/quote/get_kline.py US.{TICKER} --ktype 60m --num 64 --json
```

For HK stocks, use `HK.{CODE}` prefix instead.

**60m 量价读法（异动 / entry 讨论下沉到此层，不停留在日线 gap 叙事）**：
- 逐时段拆量：climax 信号 = 开盘首小时放量下杀 + 长下影 + 当日收回（如 vol ≥ 3x 常态小时量）；fade 信号 = 早盘冲高后午后缩量阴跌
- 首小时量 vs 全日量占比（>30% = 情绪时段主导）；反弹日缩量 vs 下跌日放量 = 修复质量
- 60m bar 时间戳 = **窗口结束时刻**（10:30 bar = 09:30-10:30 开盘时段；16:00 bar = 收盘前半小时）
- 4h 视角用 60m 自行聚合（morning 09:30-13:30 / afternoon 13:30-16:00），SDK 无原生 4h
- ⚠️ 盘前时段 snapshot 的 `prev_close_price` = **前前交易日**收盘（last_price 才是最近收盘）——对照日线核对，别把上一交易日收盘当再前一日收盘

If K-line fetch fails, fall back to local cache `trade/market/klines/{TICKER}.jsonl`, then to positions.json broker block, and mark depth as **待核实**.

**真实指标不用手算**：`python3 quant/core/technical.py {TICKER}` 输出 RSI/MACD/Bollinger/均线/量比 + K 线形态 + 见底信号评分。"Do not fake MACD/RSI" 的正解是跑它，不是跳过。

### Analysis using real K-line data

From the fetched K-line data, identify:

**Daily structure (from 1d K-line):**
- recent swing high / swing low (highest high and lowest low in past 5-10 days)
- panic low if any (sharp intraday wick with recovery)
- gap regions (where open significantly differs from prior close)
- volume spikes (days with 2x+ average volume = institutional participation)
- whether price is trending, consolidating, or breaking down
- key support: prior lows that held, gap-fill zones, high-volume pivot areas
- key resistance: prior highs that rejected, gap-down open areas, high-volume rejection candles

**Weekly structure (from 1w K-line):**
- multi-week range (high/low of past 4-12 weeks)
- weekly trend direction
- whether current price is near weekly support/resistance

**Intraday context (from snapshot + today's fields):**
- `current_price` / `last_price`
- `change_pct`
- `open` / `high` / `low` / `prev_close`
- `volume` / `turnover`
- where today's price sits within the daily and weekly structure

### Deriving support/resistance for zones

When producing execution zones later in Step 5, the K-line derived levels feed directly into zone boundaries:
- `accumulation_zone_1` should align with identifiable daily support / retrace area
- `accumulation_zone_2` should align with deeper support / panic low area
- `trim_zone` should align with resistance / prior high / extension area
- `no_chase_above` should align with resistance or overextension level

### Important boundary
- Use the actual K-line numbers. Do **not** invent round numbers that feel right.
- If 20 daily bars are not enough for a pattern, say so and note confidence is lower.
- Do **not** fake MACD / RSI / moving averages unless you actually compute them from K-line data.
- Mark anything unverifiable as **待核实**.

Output format:

【技术报告】
评级：买入信号 / 中性 / 卖出信号
信号强度：强 / 中 / 弱
关键价位：
支撑：___
压力：___
技术结论：
- ...

---

## Step 3 — 情报回顾（[FYI]，只读情报归档）

**零自行拉取**（旧「新闻分析师 web_search + 情绪分析师社媒检索」两步随情报层收拢退役）。只读 operator 配置的情报归档最近 1-2 日条目，找该 ticker / 其产业链相关条目：

- 有条目 → 逐条 [FYI] 引用（日期+来源），区分 公司 catalyst / 板块叙事 / 宏观背景
- 无条目或未配置 → 输出一行「情报层：无输入」——这不降低技术腿的置信度
- 用户对话里提供的研报 / 新闻照用（用户提供 ≠ 本 skill 拉取）
- 🚨 情报只作 [FYI]，不驱动授权（v3 技术面优先）；情绪 / 拥挤类判断没有本地数据源时直接标 待核实，不编

Output format:

【情报回顾 [FYI]】
归档命中：<N 条 / 无输入>
- <日期> [标签] <事实> — 来源
待核实项：
- ...

---

## Step 4 — 多空辩论 + 交易决策

First run a structured bull/bear debate using only evidence already established in steps 1-3.
Then translate the outcome into an action plan.

Explicitly apply these standing rules when relevant:
- verification before judgment
- confirmation over perfect price
- anti-FOMO
- event-day early strength is not confirmation
- Trading Rules master index before trading
- avoid turning the right macro concern into one uniform blunt action across all positions

Quantitative tools to run when applicable（算过的必须用 `quant/journal/decision_log.py` 落库，否则永远无法校准）:
- Sizing 上限：机械上限 = `quant/core/sizing.py` `entry_cap()` 六臂取小（entry_guard 输出已含）；判断层参考 `python3 quant/risk/kelly_size.py --p {后验} --b {赔率} --bankroll {X}`（真实 size = MIN(分数Kelly, entry_cap, ladder)）
- 同层冗余：`python3 quant/risk/chain_layers.py`
- 加仓 / 异动日 regime：`python3 quant/decision/regime_score.py {TICKER} --peers ...`（VIX 自动拉取）
- Binary catalyst 48h 内的"现在买 vs 等"：跑 entry-timing EV 框架完整流程（`quant/options/implied_move.py` + ATR→P_fill 表），禁止直觉 target
- 期权 vehicle 选择：先查 `quant/data/iv_logger.py --percentile {T}` 对照 IV 闸门表（`sizing.long_call_iv_gate` 是硬门槛）

Output format:

【多空辩论】
多头核心论据：
1. ...
2. ...
3. ...
空头核心论据：
1. ...
2. ...
3. ...
辩论裁定：___
关键分歧：___

【交易决策】
评级：Buy / Overweight / Hold / Underweight / Sell
操作建议：买入 / 持有观察 / 减仓 / 回避
建议仓位：___%（占总仓位）
入场区间：___ 至 ___
止损位：___（理由：___）
目标价：___（3个月视角）
置信度：高 / 中 / 低
决策逻辑摘要：...

### Mandatory execution-zone block
After `【交易决策】`, produce:

【执行区间】
标的归属：current / watchlist
时间框架：swing_2w / swing_4w / event_window / post_earnings
首档吸纳区：___ 至 ___ / 待核实
二档吸纳区：___ 至 ___ / 待核实
减仓观察区：___ 至 ___ / 待核实
不追价上限：___ / 待核实
失效条件：...
有效期至：...
触发重算条件：...
区间依据摘要：
- ...
- ...

Notes:
- If the user already holds the name, the block should still exist; in that case it defines add / trim / no-chase levels for the existing position.
- If there is no responsible way to set a field, mark it `待核实`.

---

## Step 5 — 风险审查

Do an independent review of Step 4.

Must check:
- concentration / correlation with current holdings
- overlap with existing themes
- maximum likely loss and whether it fits the proposed plan
- ignored tail risks
- whether the recommendation is thesis-driven or emotion/FOMO-driven
- 结构修复了吗（`structure_repair_veto`）/ regime cap 给了吗 / 第几个给 cap 的 gate（entry_guard 的 Q1 三问）
- whether the plan respects current trading rules instead of chasing narrative elegance
- whether the generated execution zones are still valid under the stated catalyst / macro scenario

Output format:

【风险审查】
风险项：
- ...
最终裁定：批准 / 批准但建议降低仓位至___% / 否决
否决或调整理由：...

## Memory policy

Only store durable memory (whatever memory facility the active agent provides) if the analysis produced a durable conclusion, repeatable lesson, or decision. Do **not** store routine noise, duplicate ticker chatter, or transient quote updates. Keep entries short and atomic; prefer one case record plus at most one pattern/lesson.

## Constraints

- Mark unknown figures as **待核实**.
- Prefer current workspace facts over fuzzy memory.
- Do not present fabricated precision.
- Do not let the final recommendation front-run the earlier steps.
- If the user is clearly asking for pre-trade advice, remind them to pass the private trading-rules and pre-trade checklist first.
- If execution zones are generated and specific enough, write them back via `update_position_zones.py` (positions.json or watchlist.json) instead of leaving them only in chat.
- Separate facts, hypotheses, mechanical verdicts and operator decisions. The live pipeline does not place orders; never expose account fields in an analysis intended for publication.
