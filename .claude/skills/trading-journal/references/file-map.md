# Trading Journal File Map

所有 holding / events / journals 路径都是私有运行数据（由 `trade/` 下的示例派生），被 Git 忽略。

## 结构化层（可统计真源，先写这里）
- `trade/us_stock/holding/events/<YYYY-MM>/trades.jsonl` — append-only fill/close/expire/assign 事件流（ET-offset 时间戳；tier / auth / zone_at_fill 标签；写入走 `quant/journal/annotate_event.py`，schema 校验强制）
- `trade/us_stock/holding/journals/scores.jsonl` — process 评分 sidecar（`annotate_event.py --score`；结构性退出由 `quant/journal/exit_score.py` 机械落行）
- `trade/us_stock/holding/events/decisions.jsonl` — EV / Kelly 决策 worksheet（`quant/journal/decision_log.py`）
- 统计出口：`quant/report/perf_report.py`（realized / 胜率 / 分层 / process×outcome 矩阵）

## 叙事层
- `trade/us_stock/holding/journals/trade_journal.md` — 正股叙事复盘（示例 `trade_journal.example.md`）
- `trade/us_stock/holding/journals/options_journal.md` — 期权专属（Greeks 入场快照）

## 规则层（operator 的私有策略规则；公开仓只有 `*.example.md` schema）
- master 入口：私有 `trading-rules.md`（示例 `trade/us_stock/strategy/trading-rules.example.md`；新规则带 `_Source: ..._` 注解）
- 决策 / sizing：Bayesian 决策模型、Kelly sizing（示例 `kelly-position-sizing.example.md`）、风险资本框架
- 执行：不确定性执行系统（示例 `uncertainty-execution-system.example.md`）、两阶段入场、entry-timing EV（示例 `entry-timing-ev-framework.example.md`）、strike 三角定位、pre-trade checklist（示例 `pre-trade-checklist.example.md`）
- 框架：capital deployment while waiting、sell飞 vs rebalance、natural humility anchor、post-trade scoring、trend exit system（示例 `trend-exit-system.example.md`）
- 期权：options strategy framework（IV 闸门表）、sell-put 规则、Greeks 纪律、LEAPS call 模板（示例 `options/leaps-call-template.example.md`）、dealer gamma positioning
- 行为触发（硬拦话术）：`.claude/rules/trading-discipline.md`（+ zone-maintenance / macro-context-check / position-tiers）

## 持仓数据（只读引用，不在复盘里改）
- `trade/us_stock/holding/state/positions.json` — 单一真源（broker + agent + lots）
- `trade/us_stock/holding/state/watchlist.json` — 候选 entry

## Memory 层（agent 侧，非 repo）
- 当前 Agent 的持久记忆机制，按主题归档；methodology 只留指针 → 私有规则，behavioral / 校准类放全文

## Typical mapping
- 单笔成交注解（评分 / 逻辑 / zone_at_fill）→ annotate_event（events + scores）
- 一次性教训 + 时间线 → journal 叙事
- 可复用执行规则 → 私有策略规则对应层 + master index
- 行为 / 校准教训（关于 agent 怎么协作）→ memory
