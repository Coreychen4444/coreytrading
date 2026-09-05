# Trading Review Template（接结构化评分体系）

## 结构化字段（先落库，再写叙事）
- order_id(s)：___
- Tier：base / position（落库时机械盖章，一般不手填）
- Auth 章：成交时 `cap_pct_nav` / `gate_no`（落库时机械盖章；推不出 = 无授权成交的形状 = 需要注解的那一类）
- Process 评分：A / A- / B / C / D（标准 = post-trade scoring 矩阵：process 通过 = A，错+好 = D，错+坏 = C）
- 落库命令：`python3 quant/journal/annotate_event.py --order-id <id> --notes-add "..." --score <X> --phase <entry|exit|episode> --score-notes "..."`

## Summary
What happened in one paragraph.

## Original plan
- Entry / exit / size / risk plan（pre-commit 的版本，不是事后美化版）

## What changed
- New information
- Intraday change
- Emotional change

## Actual outcome
- Fill / no fill / partial fill
- Price path
- Aftermath
- Realized PnL（USD 绝对值；与 `perf_report.py` episode 数字对账）

## Root mistake or success（process 维度，不是结果维度）
- Thesis issue?
- Timing issue?
- Sizing issue?（过 Kelly / concentration / ladder 了吗）
- Execution issue?（perfect-price / greedy-bid / FOMO / reactive？）
- ⚠️ 评分检查：process 错 + 结果好 = **D**，不是 A（双向对称 trap）

## Rule extracted
Write the compact rule that should survive beyond this single trade, without account values or identifiable trade details when it leaves the private workspace.
- 归层：看私有 trading-rules master index 选对应规则文件
- trading-rules 条目带 `_Source: ..., YYYY-MM-DD_`

## Where to sync
- [ ] 结构化：annotate_event（events 标签 + scores.jsonl）✅ 必做
- [ ] 叙事：trade_journal.md / options_journal.md
- [ ] 规则：私有策略规则（+ master index 如新文件）
- [ ] memory：仅 behavioral / 校准教训（methodology 只留指针）
