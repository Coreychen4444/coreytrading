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
5. Read `trade/portfolio/state/positions.json` 的 `tickers["<TICKER>"]`：取 `broker`（行情）+ `agent.zones` / `thesis`（有 `frame` 先读 frame）/ `lots` / `pending_orders`。如果 ticker 不在 positions 里，看 `state/watchlist.json` 是否有候选 entry。
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
- Zones must be tied to actual price context: support/resistance, post-event range, prior gap, event window, or sentiment washout/repricing. Valuation / thesis never moves a zone boundary（技术面优先 v3；基本面只进 `thesis.frame`）.
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
  --invalid-if "{价格结构失效条件}" \
  --timeframe {swing_2w|swing_4w|event_window|post_earnings} \
  --methodology {normal|parabolic_mode_spirit} \
  --valid-until {YYYY-MM-DD} \
  --update-trigger "{trigger text}" \
  --reasoning-summary "{short basis}" \
  --thesis-frame '{Step 1 框架 JSON — 形状见 quant/core/thesis_frame.py}' \
  --key-risk "{结构 / 执行风险；基本面风险在 frame.bear_case}" \
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
- thesis 只走 `--thesis-frame`（`--thesis` 只给还没迁的旧散文卡，卡上有 frame 后 writer 拒绝）。存量散文卡不专门迁，重画价位带时一并迁入（writer 会 ⚠️ 提醒，不拦截）
- `--invalid-if` 只写价格结构失效条件；基本面证伪条件写 frame 的 `bear_case.falsifier`

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

## Step 1 — 基本面框架（[FYI] 判断层，零授权）

写法规格 = 方法文件 `agents/methods/`（若配置）；形状判别式 `quant/core/thesis_frame.py`（定时重画的 technical 角色读同一份）。本 step 按它产出，落卡走 `--thesis-frame`。

骨架：**假设 → 驱动 → 空头与证伪 → 估值交叉验证 → 下一催化剂预案**。三件不取：评级与「好的入场点」（入场只看价格结构，v3）、目标价、「强劲 / 一般 / 偏弱」式评级词。

### 做法（按顺序）
1. **先读上一版**：卡上已有 `agent.thesis.frame` 且 `catalyst.date` ≤ 今天（ET）→ 第一件事是逐条对账它的 `watch`（实际 vs 预注册），产出 `review`；结果拿不到写「待核实（原因）」，不删看点。卡上是旧散文 thesis → 仍成立的基本面论点迁入对应槽，结构注记不迁，旧 `invalid_if` / `key_risk` 里的基本面证伪条件与风险移进 `bear_case`。
2. **假设**：一句话，增长或利润由哪一两个驱动决定。说不出来 = 基本面还没读完。
3. **驱动（1–3 条）**：机制（怎么变成收入或利润）→ 证据（事实 + 出处）→ KPI（下次披露读得到的指标）。
4. **空头**：写最强版本 → 反驳（带出处，没有就写「待核实」）→ **证伪条件**（可观测，出现即 `thesis.status=broken`）。
5. **估值**：价格要求什么（`agent.valuation` 的要求 CAGR / 目标利润率，带 as_of，标「诊断口径」），与驱动是否相容；可加透明的自上而下校验（份额 × 市场规模）。低估不是入场理由，高估不是退出理由。
6. **价格与基本面**（只写报告，不落卡）：近期价格与基本面交付背离时，判断市场在定价空头主张（回到第 4 条）还是板块 / 宏观 / 流动性（按 `.claude/rules/macro-context-check.md` 归因）。这是归因，不是入场信号。
7. **下一催化剂**：事件 + 事件日（财报取 `trade/market/earnings_calendar.json` 的 `pub_day`）+ 基线（上次指引 / 长期模型 / 一致预期）+ 看点 1–5 条（来自 KPI 与证伪条件）。看点在事件前写下，这就是预注册。
8. **thesis.status**：证伪出现 → `broken`；KPI 恶化未证伪 → `weakening`；关键看点「待核实」→ `needs_reverification`；其余 `intact`（[FYI] 播报）。

### 证据（只读，零自行拉取）
- 卡片 `agent.company_profile`（商业模式 + 资产负债表 + TTM 资本强度）——先读，机器区数字带 as_of / source 直接引用。
  - 机器区过期（`balance_sheet.as_of` 早于最新 print 的报告期）→ `python3 quant/sync/company_profile_sync.py --tickers <T>`
  - 商业模式 / 融资结构有实质变化（新可转债 / ATM / 大额发债 / 资本结构重组）→ `--ticker <T> --business-model "..."` / `--financing-notes "..."`（唯一 sanctioned 写法；直接编辑 JSON 禁止，机器区永不手改）
- 卡片 `agent.valuation`（估值镜像；as_of 早于最新财报期 = 过期，写「待核实」）；诊断重算 `python3 quant/valuation/valuate.py --tickers <T> [--refresh]`。估值层零授权：不改 zone / gate / cap。
- 情报归档条目（若配置，`trade/intelligence/reports/`；文件名作出处）
- `trade/market/earnings_calendar.json`（`pub_day` / `next_reaction_day`）
- 用户在对话里贴的研报 / 财报（用户提供 ≠ 本 skill 拉取；出处写提供者 + 日期）
- **不自行 web_search / 社媒**（情报层归 operator 的情报工作区）；拿不到的数字写「待核实」

Output format:

【基本面框架 [FYI · 零授权]】as_of YYYY-MM-DD
假设：…
驱动：
- ① <名>：<机制> → 证据 <…>（出处）→ KPI <…>
- ② …
空头：<最强版本>
- 反驳：…（出处 / 待核实）
- 证伪：<可观测条件>
估值：<价格要求什么（as_of，诊断口径）> → 与驱动 <相容 / 冲突 / 待核实>
价格与基本面：<是否背离；市场在定价什么>（不落卡，不是入场理由）
下一催化剂：<事件> <事件日>
- 基线：…
- 看点：① … ② … ③ …
上次看点对账：<仅上一版 catalyst 已到事件日> ① <看点> → <结果> …
thesis.status：<intact / weakening / needs_reverification / broken>（依据：…）
数据说明：
- 用户提供：…
- 已核实：…
- 待核实：…

落卡映射：假设 / 驱动 / 空头 / 估值 / 下一催化剂 / 上次看点对账 / 数据出处 → `--thesis-frame` JSON 的 `hypothesis` / `drivers` / `bear_case` / `valuation` / `catalyst` / `review` / `sources`（`as_of` 同表头）；「价格与基本面」只留在报告里。

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
形态（两种模式，`chart_read.py --tickers <T>` 口径；不出买入 / 卖出信号评级）：🔁 抄底 <✓止跌 / ⏳今日新低 / 无> · 🚀 趋势确认 <✓已转移 / ✅站稳·量腿未过 / ⏳计数中 m/n / ▫️逼近 / 无>
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
基本面侧论据直接引用 Step 1 框架（驱动 / 空头 / 证伪），不另起炉灶；它们是 [FYI]，裁定与操作建议仍以价格结构为准。
Then translate the outcome into an action plan.

**决策不是评级**：入场决策 = 系统 + agent（简报的当日挂单清单），执行 = operator 照清单人工挂单（不加不改不追），退出 = 系统的钟。所以本块不出 Buy / Overweight 评级、不出目标价、不出「占总仓位 %」——价位、股数上限、止损一律取机器输出原文：
- 候选（watchlist）：`python3 quant/decision/entry_guard.py --tickers <T>`（不带 `--log`，session 内不落 decisions）
- 持仓（current）：不走 entry_guard，也不走抄底。退出栈 `python3 quant/decision/exit_guard.py --read-only`；加仓授权 = 卡片 `current_regime.cap_pct_nav` + Iron Rule #2 v2，加仓阶梯与预算看 `python3 quant/report/portfolio_brief.py --read-only`
- 形态判读（两种模式已形成 / 即将形成）：`python3 quant/report/chart_read.py --tickers <T>`（只读）

Explicitly apply these standing rules when relevant:
- verification before judgment
- confirmation over perfect price
- anti-FOMO
- event-day early strength is not confirmation
- Trading Rules master index before trading
- avoid turning the right macro concern into one uniform blunt action across all positions

Quantitative tools to run when applicable（算过的必须用 `quant/journal/decision_log.py` 落库，否则永远无法校准）:
- Sizing 上限：机械上限 = `quant/core/sizing.py` `entry_cap()` 六臂取小（entry_guard 输出已含）；判断层参考 `python3 quant/risk/kelly_size.py --p {后验} --b {赔率} --bankroll {X}`（真实 size = MIN(分数Kelly, entry_cap, ladder)——只能压低股数，不能抬高）
- 同层冗余：`python3 quant/risk/chain_layers.py`
- 加仓 / 异动日 regime：`python3 quant/decision/regime_score.py {TICKER} --peers ...`（VIX 自动拉取）
- Binary catalyst 48h 内的"现在买 vs 等"：跑 entry-timing EV 框架完整流程（`quant/options/implied_move.py` + ATR→P_fill 表），禁止直觉 target
- 期权：IV 门以 entry_guard「若期权」行为准（`sizing.long_call_iv_gate`：IVP <40，序列不足用 HV20 分位代理，ATM IV <50% 末位兜底；抄底 call 不认绝对 IV 兜底），原始序列 `quant/data/iv_logger.py --percentile {T}`。限价锚 = 正股 z1 回踩投影（追高 = `above_z1` 规则外）；抄底合约取 entry_guard 🔁 块的合约计划

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
⚓ 宁可错过，不可失序；失去节奏，终会成为市场的猎物。
第零问 · 节奏：今日挂单清单内 / 清单外（本分析即「先过 agent 一轮」，等不及 = NO）/ 持仓管理（非 BUY）
入场模式：🔁 抄底（空仓 + ✓止跌）/ 🚀 趋势确认（gate 转移授予的 cap 内）/ 无（未形成 → 不做）
Q1 授权（entry_guard 原文）：<PLAN(gateN) / VETO / STALE / NEEDS_CARD>；🔁 <PLAN / TOO_FAR / OUT_OF_DOMAIN / VETO / NEEDS_CHAIN / 无>；理由：…
挂单：<阶梯原文 $价×股 / …>（价位不手改）；股数 <照阶梯 / 压低至 N 股（理由）>；size 上限 $<…>（binding=<臂>）
工具：<正股 / call（IV 门 过 / 不过，basis …）>
Q2 错了怎么认：<价> ×<N> 日（来源：卡 stop / 止跌低 / 棘轮地板 / 趋势死亡线）——成立即当日执行，没有「看一天」
Q3 位置：<z1 / z2 / waiting / 超 no_chase / trim 区 / 破 z2 下沿 = 当日 BUY 冻结>；zone stale → 先重画再判断
持仓（仅 current）：退出栈 <exit_guard 原文>；加仓 <cap 0 = 无授权 / cap 40% + Iron Rule #2 v2：averaging down / pyramiding up>
操作：照阶梯挂单 / 等（写明等什么：gate 线 ×N + 量腿 / ✓止跌 / 回踩 z1）/ 持有（退出照钟）/ 减仓（trim 许可区 / 退出线成立）/ 不做
决策逻辑摘要：<一两句，只引结构证据；基本面引 Step 1 并标 [FYI]>

### Mandatory execution-zone block
After `【交易决策】`, produce:

【执行区间】
标的归属：current / watchlist
时间框架：swing_2w / swing_4w / event_window / post_earnings
首档吸纳区：___ 至 ___ / 待核实
二档吸纳区：___ 至 ___ / 待核实
减仓观察区：___ 至 ___ / 待核实
不追价上限：___ / 待核实
止损：___ ×N 日（来源）/ 待核实
下一 gate：___ 收盘 ×N + RelVR ≥1.1（`next_regime.entry_gate`）/ 待核实
失效条件（只写价格结构）：...
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

Do an independent review of Step 4. 只能维持或压低，不能放大：阶梯价位、股数上限、止损是机器与卡片的输出，风险审查不改价、不加股、不放宽 stop；压低股数必须写理由。

Must check:
- 第零问与 Q1–Q3 都答了没有。VETO / STALE / NEEDS_CARD 的笔 → 裁定只能是「不做」或「先重画」；清单外的笔 → 本审查就是那一轮，GO 须 entry_guard PLAN 且 Q2 / Q3 全过，否则 NO
- 行为模式：对照 `.claude/rules/trading-discipline.md` 行为模式表（FOMO / revenge / 改限价 / greedy-bid / 期权追高 `above_z1` / 抄底过期追单 `TOO_FAR` / 破 stop 心存侥幸 / 30 分钟规则）
- concentration / correlation with current holdings：同层冗余（`quant/risk/chain_layers.py`）、主题重叠；size 上限的 binding 臂是不是组合层（gross / at-risk / 冲击预算）
- maximum likely loss：股数 × (成交价 − stop)；stop 落在真空带 / LVN 里按跳空后更差的成交估；是否在本档 gate 风险预算内
- 事件：48h 内财报 / FOMC / CPI（财报反应日 T+0 缩仓由 sizing 自动；binary catalyst 只算 EV，不拦截）
- ignored tail risks
- 基本面 [FYI]：Step 1 的证伪条件是否临近——只提示，不改裁定
- whether the generated execution zones are still valid under the stated catalyst / macro scenario（stale 判据见 `.claude/rules/zone-maintenance.md`）

Output format:

【风险审查】
规则核对：第零问 <…> · Q1 <…> · Q2 <…> · Q3 <…>
风险项：
- ...
最大亏损：<N 股 × (价 − stop) = $… ≈ …% NAV>；跳空情景 <…>
最终裁定：检查通过 · 照阶梯执行 / 压低至 <N> 股（理由：…）/ 等（条件：…）/ 不做（理由：…）/ 先重画

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
