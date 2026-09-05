---
name: trading-journal
description: Record trading lessons, execution mistakes, and review-worthy trade journeys into durable files across the structured ledger (events/scores), narrative journals, the operator's private strategy rules and agent memory. Use when the user asks to record a trade lesson, sync a trading rule, update journaling files, write a post-trade note, annotate fills, or keep trading心得/纪律/复盘 in sync. Also use when asked to commit those updates.
---

# Trading Journal

Use this skill when the task is **recording durable trading knowledge**, not just discussing a trade.

## What this skill is for
This skill handles the **recording workflow** after a trade lesson, execution mistake, or repeatable rule is identified.

It exists to make sure trade wisdom is not trapped in chat history, and that every复盘 lands in BOTH forms:
1. **结构化**（可统计）：events 标签 + `journals/scores.jsonl` 评分 → `quant/report/perf_report.py` 的 process×outcome 矩阵
2. **叙事**（可阅读）：journals 的 prose 复盘

> 铁律：**评分不落 scores.jsonl = 没复盘**。prose-only 注解是审计确认过的失败模式（评分散落多种格式无法统计）。

## When to use
Use this skill when the user says things like:
- "record this" / "记下来" / "写成复盘" / "更新交易日志"
- "加到交易纪律里" / "把这个心得沉淀下来"
- "commit"

Do **not** use this skill for normal market commentary or live trading opinions unless the user explicitly wants the lesson recorded.

## Required workflow
When triggered, follow this order.

### 1) Classify the note
Decide which of these it is (a single lesson may need more than one destination):
- **Per-trade annotation**: zone_at_fill / 逻辑 / 评分 for a specific fill → structured layer (step 2)
- **Daily journal note**: one-off timeline, context, raw event log → narrative layer (step 3)
- **Repeatable rule**: should become discipline / checklist / anti-FOMO / risk rule → private strategy rules (step 4)
- **Formal review**: worthy of a standalone case-study → narrative layer with the review template
- **Behavioral / partner-calibration lesson**: how the agent should behave → memory (step 5)

### 2) Structured layer FIRST（可统计的真源）
- **成交注解**（zone_at_fill / 逻辑 / 评分；`tier` 与 `auth` 落库时已机械盖章，一般不手填）：
  ```bash
  python3 quant/journal/annotate_event.py --order-id <id> \
    [--notes-add "逻辑一句 + 是否过 checklist"] [--zone-at-fill {zone1|zone2|waiting|trim|above_no_chase|unknown}] \
    [--tier {base|position}] [--auth-cap 0.4 --auth-gate N] \
    [--score {A|A-|B|C|D} --phase {entry|exit|episode} --score-notes "..."]
  ```
  写入 `events/<YYYY-MM>/trades.jsonl`（schema 校验强制）+ `journals/scores.jsonl`。`--auth-cap/--auth-gate` 只给「推不出→留空归人判」判完后的写回；分类轴（confirmed/probe/early_risky）已退役，不再有 `--classification`。**只注解规则外成交**（判据见 CLAUDE.md 常设裁决 9）。
- **评分标准**：process 通过 + 结果好 / 坏 = A，process 错 + 结果好 = D 🚨，process 错 + 结果坏 = C（canonical 在 operator 的私有 post-trade scoring 规则）。结构性退出由 `quant/journal/exit_score.py` 机械评分（只评时延与完整度）。
- **决策 worksheet**（如果复盘涉及 EV / Kelly 计算）：`quant/journal/decision_log.py` 落 `events/decisions.jsonl`。
- 统计验证：`python3 quant/report/perf_report.py` 应能看到这条记录。

### 3) Narrative layer
- 正股 → `trade/us_stock/holding/journals/trade_journal.md`（示例 `trade_journal.example.md`）
- 期权 → `trade/us_stock/holding/journals/options_journal.md`（含 Greeks 入场快照）
- 结构参考 `references/review-template.md`。Prose 里写了评分的，必须已在 step 2 落过 scores.jsonl。这些路径是私有运行数据，被 Git 忽略。

### 4) Private strategy rules（可复用规则）
Layering convention（`trade/us_stock/strategy/README.md`）：**methodology → 私有策略规则；行为触发 → `.claude/rules/`；指针 → memory**。
- 私有规则文件由 `trade/us_stock/strategy/*.example.md` 复制去掉 `.example` 派生，只在 operator 复核后写入；不要把策略散文加进公开的 example 层。
- 新规则归层看私有 `trading-rules.md` 顶部 master index 选对文件；都不合适且确实 reusable → 建独立小文件并把它加进 master index。
- 加规则必须带源注解：`_Source: TICKER event description, YYYY-MM-DD_`（让规则在诱惑时刻有案例可引）。
- 行为触发类（硬拦话术 / 检测信号）→ `.claude/rules/trading-discipline.md`，正文仍放私有规则 + 指针。

### 5) Memory（agent 侧持久化）
- 用当前 Agent 提供的持久记忆机制，按主题归档（交易教训 / 入场执行 / 期权选择 / 持仓管理 / 行为校准），而不是日记式流水。
- methodology 类只留**指针**指向私有规则（不复制正文）；behavioral / 校准类才放全文。
- repo 已记录的东西（代码结构 / git 历史 / CLAUDE.md 已有内容）不进 memory。

### 6) Public mirrors（若 operator 维护公开知识库）
- 永不把 journals、holdings、fills、NAV 历史、个人校准同步到公开仓。
- 只能同步经过占位符替换的通用方法论：维护真实 ticker / 账户 / 人名 → 占位符的映射表，文件名同样不得带真实 ticker，push 前对 `git diff | grep '^+'` 做零命中的泄露扫描。公开仓的 git 历史不可逆，删除 ≠ 未发生；泄露的凭据要轮换而不是删提交。

### 7) Commit after recording
Commit message patterns:
- `journal: record <lesson>`
- `strategy: sync <rule>`
- `review: add <case-name>`
- `state: ...`（events / scores 类）

只有用户要求时才 commit / push。

## Writing rules
- Prefer blunt, compact rules over long emotional recaps.
- Preserve concrete examples when they clarify the lesson.
- Extract the rule, not just the story.
- Do not water down mistakes into "bad luck" if execution was flawed —— process 错 + 结果好 = **D**，照实记。
- Keep titles stable and searchable.

## References
- `references/file-map.md` — 全部目标文件地图
- `references/review-template.md` — 复盘结构模板（含结构化字段）
