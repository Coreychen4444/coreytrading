# 领域 Agent 运行手册（现行契约）

本文件只写**现在怎么跑**。模型选型与升级授权见 [MODEL_POLICY.md](MODEL_POLICY.md)；实际开关以 `routing.json` 为准，本文不代替它。

## 一句话

宿主（Python 控制器）冻结证据、派发研究、校验结果、经既有 writer 受控写卡；模型只读冻结数据包、只返回契约 JSON。**每个交易日只有 technical 一个模型角色在跑**；风控收据由代码渲染；估值退到独立估值日更。**宏观不是模型角色**：由代码判读的宏观透镜 `quant/core/macro_lens.py` 产出，零模型调用；其精简先验随每次冻结输入进入 technical / valuation 视图。订单永远人工。

市场情报采集是这些角色的上游，属于用户自有的可选工作流，本仓不包含其采集与投递流程：证据与发布数据在 `trade/intelligence/`（公开仓只有 README 与合成示例），共用方法（若配置）在 `agents/methods/`，缺失时角色政策把它们记为 `optional_methods_missing`，不臆造。

## 角色与运行时点

| 时点 | 模型角色 | 代码渲染 | 说明 |
|---|---|---|---|
| postclose（收盘 +10m） | technical（组合视图 1 次 + 单卡 ≤ `repaint_bound`） | risk 收据 | 机械转移之后、提醒线之前 |
| chart（收盘 +35m） | 无（零模型调用） | 两种模式清单（`chart_read.py` 代码渲染） | 0 |
| briefing（盘前） | technical（卡片或成交有变才新跑，否则复用同收盘同卡片版本的 run） | risk 收据 + 宏观透镜 `--brief` | 宏观 = 代码判读 |
| weekly（周复盘） | technical + risk 工作流 | 阶梯 / NAV / 基线表 + 宏观透镜 `--week` | 唯一全量研究时点 |
| valuation-daily（独立任务） | 宿主计算 → valuation → 研究级目标 | 估值报告 | rf = 宏观日线缓存 10Y |
| heartbeat | 无 | 健康扫描 + 缺卡派发 | 不调用模型 |

角色契约真源 `quant/core/agent_contracts.py`；职责文本 `agents/{technical,risk,valuation}.md`，角色注册 `roles.json`。只有 technical 可提 `zone_update` 提案，其余 `analysis_only`。`repaint_bound`（`agent_jobs.repaint_bound`）= 每轮至少 5 张，队列更长时取 ⌈N/2⌉，任何队列两轮排空（失败卡不计入保证）。

## 路由（`routing.json` v2）

- `model_profiles`：`anthropic` = Claude runtime，`openai` = Codex runtime；每个 profile 显式模型 + effort，fallback 列表可为空。
- `roles.<role>.profile / reviewer_profile`：技术、风控、估值的主用 profile 与复核 profile 以 `routing.json` 为准（默认主用 anthropic、复核 openai）。`reviewer_profile` 在 `autopaint.review=false` 时**不生效**（配置保留，行为关闭）。
- `execution_transport = desktop`：不起模型 CLI；控制器把数据包写进 `var/agent-runs/desktop/`，各自厂商的 Desktop 会话按 `tasks/desktop-research.md` 领取并回写。`cli` 仍可选：Claude 使用无工具 safe mode，Codex 使用隔离配置、macOS 沙箱及限定模型域名的 TLS 代理；CLI 解析顺序 = `CLAUDE_BIN` / `CODEX_BIN` 显式路径（无效即失败）→ PATH → 已知安装位置。
- `limits` 约束单次 workflow（并行、调用、追问轮数）；`global_limits` 约束所有任务共享额度（窗口调用数、并行、租约宽限）。这是系统自己的调用保护，不代表账户剩余额度或实际金额。
- `autopaint`：`enabled` 缺卡派发；`apply` 允许受控写入；`review` 要求另一厂商复核。当前取值见 `routing.json` 与 [MODEL_POLICY.md](MODEL_POLICY.md)。

回执区分请求值与 CLI 元数据报告的模型：`requested_model`、`requested_effort`、`reported_model`、`model_verification`（`exact_match` / `model_mismatch` / `unreported` / `multiple_reported`）；Desktop 交接下恒为 `unreported`，自报身份不算验证。

## 冻结输入

`agent_inputs.capture_inputs()` 只读本地已有来源（持仓、候选、日线缓存、NAV、事件、宏观归档、估值账本），读取期间来源变化即拒绝发布。快照 = `snapshot_id / input_hash / as_of / expires_at(默认 1h) / evidence_refs / data`；结果里的每个 `evidence_refs` 必须来自快照。过期重新捕获，不能修改旧快照续期；缺失和未知不以 0、cash 或默认系数补齐。

技术组合视图：本轮 `repaint_targets` 保留完整卡与 60 根日线；其余卡是数字化精简卡 + 20 根 + `structure_receipt`（ATR20 / RelVR / gate streak / zone 位置，由 `quant.decision.regime_transition` 计算）；视图明确列出 `not_provided_by_design`（60m bars、IV 分位、组合 at-risk）。单卡（card scope）保留完整卡与 260 根。

Worker 政策 = 角色 md + 该角色的 rules + methods + 一段 `project_contract_excerpt`；不附宿主会话合同与策略总索引。技术角色另收与提交边界一致的参数白名单。候选 sizing 是共享预算下各候选的独立上限，**不能相加成批量买入额度**。

## Desktop 交接

`agent_desktop.py launch --kind <kind>` 把原控制器放后台；会话 `claim --provider <claude|codex> --wait-s 50` 领取，产出契约 JSON 后 `complete`。`complete` 时宿主：绑定身份字段 → `validate_result` → 带 proposal 的结果再跑 `agent_submit.preview_candidate`（静态检查 + writer merge / schema / gate 一致性 / stop 与 gate 不放宽 / protected fields 干跑），被拒返回 `blocked_reasons`，会话修正后重提。`answered ≠ applied`。

时限：每个请求带 `deadline_ts`（按角色区分），过期 = 该角色失败，workflow 继续并标缺失；已领取但长时间无 complete 的请求可被重新领取。回执 `usage.packet_bytes` 与 `estimated_tokens` 是成本信号。

已知取舍：Desktop 会话有全工具，隔离是**指令级**（`tasks/desktop-research.md` 禁令）+ 结果侧机械校验，不是 CLI 形态的 OS 级隔离。

## 提交边界

唯一写入路径 `.claude/skills/trading-analysis/scripts/update_position_zones.py --expected-revision`。提交必须满足：技术提案、参数白名单、快照与政策 hash 一致、结果未过期、最新完整交易日证据、锁内目标卡 revision 一致（`card_revision` 不含 broker 报价块与估值镜像）。带价位带的提案必须显式带 `--stop-level`，否则预览即拒（`proposal would remove existing stop`）。writer 锁内生成 `prepared` 收据；崩溃后 `reconcile RUN_ID` 只对账不重试。`review=false` 时回执 `review_policy=not_required_by_host`。

## 运维入口

```bash
.venv/moomoo/bin/python3 quant/ops/agent_jobs.py due --kind postclose          # 交易时钟
.venv/moomoo/bin/python3 quant/ops/agent_runtime.py roles                      # 展开后的路由
.venv/moomoo/bin/python3 quant/ops/agent_health.py                             # 健康扫描（只读）
.venv/moomoo/bin/python3 quant/ops/agent_doctor.py --json                      # 注册元数据盘点
.venv/moomoo/bin/python3 quant/report/agent_metrics.py --days 7                # 周基线（只读回执）
.venv/moomoo/bin/python3 quant/report/risk_receipt.py <run 或 snapshot JSON>   # 机械风控收据
.venv/moomoo/bin/python3 quant/ops/agent_runtime.py submit RUN_ID [--apply]    # 人工单笔提交
```

生产入口先经 `quant/core/interpreter.py` 检查解释器：缺少项目依赖时明确拒绝并指出原因（`COREYTRADING_ALLOW_ANY_PYTHON=1` 显式绕过并记录）。运行产物在 git 忽略的 `var/agent-runs/`。health 默认只读运行元数据，`--record-events` 持久化状态与告警 outbox；doctor 只读注册元数据白名单，不证明应用当前运行状态或某轮成功。

## 判据

- 只有 `cards[].status == "applied"` / `submission.status == "applied"` 算落卡；`partial` = 任务已结束但有缺失角色、未落卡或待办，不是挂了。
- 定时任务是否注册以运行器的运行时任务清单为准，文件存在不算。
- 「变好 / 变差」先看 `agent_metrics` 周基线，再谈配置。
