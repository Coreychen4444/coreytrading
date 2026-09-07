---
paths:
  - "quant/ops/pipeline.py"
  - "quant/report/portfolio_brief.py"
  - "quant/decision/exit_guard.py"
  - "tasks/claude/heartbeat.md"
  - "tasks/claude/overnight-briefing.md"
  - "tasks/claude/postclose-transition.md"
  - "tmp/heartbeat-state.json"
---
# Heartbeat Patrol Rules

Applied when: 改动巡逻 / 简报代码或任务 prompt。定时任务 prompt 真源 `tasks/claude/heartbeat.md`；采集顺序真源 `quant/ops/pipeline.py`（`STAGES`，由 `tests/ops/test_pipeline.py` 钉住），不在 prose 里抄序列。

## 执行
每份定时 prompt 第一条命令 = `quant/ops/agent_jobs.py due --kind <kind>`：XNYS 日历裁定交易日与窗口，休市 / 窗口外直接跳过，日历不可用即报错不猜。然后一条命令：`python3 quant/ops/pipeline.py --mode heartbeat --json`。OpenD 探活、dead-man 时间戳（`lastChecks.heartbeat`，briefing 模式绝不写）、OpenD 不可达的 Telegram 告警（12h 冷却 + 恢复通知）全在 pipeline 内。人工 / session 内跑必须 `--read-only`：只读预览是白名单，只跑缓存 brief，采集、写状态、后台 agent、效果层全部跳过。

## 告警与冷却（`portfolio_brief`；状态 `tmp/heartbeat-state.json`）
- 边沿触发：首次成立提醒；持续成立 24h 内不重复（`REALERT_SECONDS`）；解除后再成立重新提醒。key `TICKER:trigger`（涨跌分方向 `adverse_move_up` / `_down`），`INFRA:*` 留给基础设施。
- 告警发射权归 heartbeat / overnight-briefing / postclose-transition 三任务共有（同一份冷却状态，谁先看到谁发）；三者跑 mutate 模式是有意的。
- pipeline 在 brief 一算完即自动推 fired_cards + alerts 原文到 TG（`push_brief_alerts`，返回顶层 `tg_pushed`），效果层（画线 / 白名单）排在其后。
- `agents` 阶段（领域研究 + 复核后写卡）在 brief 与 regime 转移之后、reminders 与授权组之前；只有 `status == "applied"` 的回执是已写卡，`partial` 回执（缺失角色 / 未落卡 / 待办）记为顶层 `partial`，不是该步挂了。heartbeat 本身只做健康检查与非阻塞缺卡派发，不起领域研究。
- 缺卡派发走 `quant/ops/card_autopaint.py`（`autopaint` 阶段）：只起分离进程立即返回，`--read-only` 下必须用它的 `--dry-run`；写卡仍只经跨运行器复核后的受控入口。
- 阶段顺序里两条不可对调：持仓中的候选卡先促升（`watchlist_migrate`）再落 lots（`lot_reconcile`），重进的 lot 才继承当前分析卡；结构性退出评分（`exit_score`）排在平仓收尾之后、候选迁移之前——退出记录是评分判据，促升会覆盖正被评分的卡。

## 检查项
| 项 | 判据 |
|---|---|
| 持仓异动 | \|change_pct\| > 标的自身 1×ATR14（`MOVE_ATR_MULT` / `MOVE_ATR_PERIOD`；kline 不足回落 5% 并标「兜底」；期权固定 ±20%）。文本带 `[阈值 X.X% = 口径]` |
| 大盘 | QQQ / SPY 最近完成日线 change < −2%（`index_risk`，`INDEX_DROP_PCT`） |
| watchlist 触碰 | `wl_zone_touch` 📥：z2 下沿 ≤ 快照价 ≤ z1 上沿 且 cap > 0 |
| 破 z2 下沿 | `z2_breach_buy_freeze` 🚫：两桶都查，只报 cap > 0 的卡；只冻结当日 BUY，不写卡不动 cap |
| 退出栈 | 🔒 棘轮 / 📉 趋势死亡线 / 🎯 G-01 / 🚨 MARGIN / 🔓 重进解锁（`exit_guard`）——原文全文推，不得改写削弱 |
| 基础设施 | `INFRA:*`：opend_down / lot_drift / fill_untagged / card_stale_earnings / card_missing_next_gate / card_missing_archetype / gate_anchor_desync / promote_blocked / underlying_card_missing 等 |

## 输出规则
- 持仓告警推 `fired_cards` 原文（代码渲染：预算 / binding stop / zone 位置 / gate / 结构标签），不改写、不压缩、不用散文替代，卡片后不追加叙事行。JSON 里没有的字段不许叙述者补。
- 无新增异常 → `HEARTBEAT_OK` 不发 TG。operator 配置的可选尾行（`TG_FOOTER_LINE`）由 `tg_notify.py` CLI 自动附加，不用写也不许删。
- 情报层已收拢：heartbeat 不采集、不搜索、不转述任何宏观 / 新闻。
- 永不下单、改单、撤单。

## 盘中运行：未完成的 bar 不是收盘
收盘依赖层（regime_transition / exit_guard / entry_guard / staleness）都把 kline `rows[-1]` 当昨收。两道闸：`kline_cache` 盘中丢弃当日未完成 bar；`iv_logger` / `nav_snapshot` / `skhy_premium` 标 `eod_only`。`--read-only` 不 gate 数据层——预览不能改交易状态。postclose 模式先按旧卡评估退出再转移 regime，降级不能在被报告前抹掉已破的 stop。
