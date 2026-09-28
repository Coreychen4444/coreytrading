# `quant/` — 交易系统代码

 由扁平的 `scripts/`（45 个文件平铺 + 每个文件自己 `sys.path` 找同伴）重组为分层包。
**这份文件是分层的真源**：新脚本放哪、能 import 谁，看这里。

## 分层与依赖方向

依赖只能**向下**。上层可以 import 下层，反之不行；同层之间尽量不互相 import。

```
  ops          编排 / 通知 / 运维
   │           pipeline · card_autopaint · tg_notify · tg_rich（报告文本 → Rich Message blocks 编译器）
   │           agent_runtime / agent_runners / agent_workflow（角色与模型解耦，研究与提交分离）
   │           agent_jobs / agent_inputs / agent_dependency（生产入口、冻结证据与角色视图、同 session 上游屏障）
   │           valuation_daily（独立日更估值：刷新/冻结/宏观→估值/去重/归档）
   │           agent_snapshots / agent_submit / agent_doctor / agent_health（快照、受控写卡、任务元数据盘点、健康与租约）
   │           agent_evidence（显式冻结组合输入 → 既有机械 sizing 收据，零取数/写入）
   │           agent_desktop（Desktop 交接：后台启动原控制器，会话领取 / 提交研究请求）· macro_state（宏观文档校验与原子发布；pre_event 必须从事件剧本编译）
   ├── report  报告层（读时计算，不产生新状态）
   │           portfolio_brief · perf_report · gate_ladder_report · gate_audit
   │           chart_read（每日收盘判读 = 两种模式已形成/即将形成清单，**判读层零授权效力**）
   │           transition_report（转移达标条件收据）· risk_receipt（机械风控收据）· agent_metrics（Agent 层周基线）
   │           macro_report（宏观研究只读摘要）· macro_playbook_report（事件剧本校验 / 渲染 / 编译）
   │           nav_report（NAV 区间变动剔除外部现金流，周复盘 NAV 变动的唯一口径）
   │           valuation_report（冻结估值收据 → 当轮 FYI，缺口不回退到旧价）
   ├── journal 账本写入层（prose-only 不算落库）
   │           annotate_event · decision_log · market_regime_log（大盘状态卡每日快照账本 + chart-read 首段，判读层）
   ├── valuation 估值层（**判断层零授权效力**：读三表/快照/K 线/宏观快照，写自己的账本）
   │           fundamentals（OpenD 三表+快照+PE 分位+目标价 → market/fundamentals 缓存）
   │           archetypes（商业模式原型缺省 + 人工区 inputs.json 校验）
   │           model（FCFF-DCF · EPS×PE · 反向隐含 · 情景 · 久期，纯函数）
   │           valuate（CLI 编排/渲染/valuations.jsonl 账本）
   │           overlays（新闻→驱动契约: 校验/生效筛选/CLI append）· calibrate（财报后对账 + 非 PIT 的历史近似诊断）
   │           assessment（冻结输入的每日计算收据）· scenarios（宏观/公司情景传导）· response（短期事件样本与滚动检验）
   │           evidence（财报补充证据、前瞻时效和利息传导）· event_data（按真实交易日构建消息后日线样本）
   │           universe（估值层默认范围 = 持仓 ∪ 候选，只有名字，零授权效力）
   │           company_model / company_eps（公司级模型与显式证券级 EPS 算术，无现金/债务换算）
   ├── decision 决策层（机械闸门）
   │           entry_guard · exit_guard · regime_transition · regime_score
   │           structure_drift · level_map · catalyst_check · tape_check
   │    ├── options  期权执行参数   option_plan · implied_move · strike_compare
   │    └── risk     仓位/集中度     kelly_size · chain_layers
   ├── sync    broker 真值 ↔ state（唯一写 broker block 的层）
   │           state_io · stock_fetch · order_sync · trade_history_sync
   │           watchlist_sync · watchlist_migrate · price_reminder_sync
   │           lot_reconcile（买入落账） · close_reconcile（平仓收尾） · today_group_sync
   │           company_profile_sync（券商财报 → agent.company_profile 机器区：商业模式/资产负债表/TTM 资本强度）
   │           valuation_sync（valuations.jsonl 最新行 → agent.valuation 机器区；估值层不写 state，镜像归 sync）
   ├── data    把外部世界读进本地
   │           market_data · macro_fetch · earnings_calendar
   │           sec_facts（显式 CIK 的 SEC 财务事实读取和披露日期筛选，不推断字段映射）
   │           sec_inline（缓存财报离线数字抽取，精确期间/实体/单位/分部，冲突或不支持格式保留未知）
   │           kline_cache · iv_logger · nav_snapshot · skhy_premium
   │           cash_flow_sync（券商资金流水 → 外部现金流账本，只收出入金）
   └── core    纯共享库 · 单一真源 · 无副作用
               paths · env · io_json · timeutil · num · occ · schema · ledger
               sizing · structure · drift · technical · holdings
               episodes · book · earnings · sectors · patterns（形态判别式，Lo 2000 工序）
               dow（短期道氏四步状态机，提供趋势终止观察与结构排序）
               market_regime（大盘状态卡：ER20 自身分位档 × 短期道氏 → 单边上行/单边下行/震荡/转换中 + 转移线 + 历史频率；判读层零授权）
               reversal（抄底入场类判别式：信号 / 止跌低 / 域 / 有效期；授权归 entry_guard）· setups（两种模式判读：抄底 = 空头衰竭的价格行为 / 趋势确认 = gate 接近度；零授权）
               future_gates（卡片阶梯顶之上的远端 gate 候选：pivot 簇上沿 + 窗口最高价，与 bottom_evidence 同一把尺；零授权；两个消费者读同一个 `for_card`：price_reminder_sync 的 `gate:+N` 线 / gate_ladder_report 的远端行）
               key_levels（⭐箱顶 = 近 20 根最高价所在的簇 × 近 60 根被从下方测 ≥2 次、⭐突破 ×2 收盘（只站上一根 = ⭐突破中）、箱体位置判读；同一把簇尺，窗口全是既有常量别名；零授权；消费者 price_reminder_sync 的 ⭐ 线）
               market_sessions（XNYS 交易日历：休市 / 半日 / DST 的唯一时段真源）· quote_times · nav（NAV × 外部现金流，纯函数）
               agent_contracts（四领域角色的 JSON 契约、权限与证据绑定）· valuation_contracts（情景单位、期限与传导约束）· motto（可选尾行，未配置即空）
               macro_analysis / macro_evidence（宏观研究模块 schema 与逐项证据收据）· macro_playbook（事件剧本形状，零授权）· interpreter（生产入口解释器守卫）

  research     验证层（可复用机械件/合成脚手架）—— 旁路，不参与实时决策
```

## 每层一句话

| 层 | 职责 | 判断标准（新脚本该不该放这） |
|---|---|---|
| `core/` | 被所有层共享的纯函数与路径/IO 真源 | 无副作用、不碰网络、不知道自己在为谁服务 |
| `data/` | 外部世界 → 本地文件 | 会拉外部数据或写 `market/` 下的账本 |
| `sync/` | broker 真值 ↔ `state/*.json` | 会写 `positions.json` / `watchlist.json` |
| `decision/` | 产出机械判定（PLAN/VETO、告警、regime 转移） | 输出是**动作**，不是数据 |
| `options/` | 期权合约级执行参数 | 需要期权链 / Greeks / BS 定价 |
| `risk/` | 单档上限与组合集中度 | 回答"能下多少"而非"要不要下" |
| `journal/` | 决策与成交写进账本 | 往 `events/` / `scores.jsonl` append |
| `valuation/` | 基本面 → 公允价/隐含预期/高估低估（判断层） | 不 import 授权层、不写 positions/watchlist；输出是**判断**不是动作（测试钉死） |
| `report/` | 读时计算的人类可读输出 | 只读不写（除告警冷却状态） |
| `ops/` | 编排、通知、仓库运维 | 调别的脚本，或与外部系统（Telegram/git）打交道 |
| `research/` | 公开研究机械件与合成脚手架 | 只产出统计，不保存私人数据、结果或裁决 |

## 约定

**市场情报**——本仓不采集新闻 / 宏观；情报数据目录 `trade/intelligence/`（公开仓只有 README 与合成示例）与运行状态 `var/intelligence/` 由用户自有的可选采集流程维护，交易代码只读。不把报告、任务或数据整包放进 quant；现有 `valuation/event_data.py` 继续服务估值，出现第二个实际消费者再提取通用实现。新代码层仍须通过依赖与测试镜像守卫，不预建空层。
`core/macro_analysis.py` 定义宏观因果与市场结构研究模块的 schema 及证据约束，由 agent_contracts 验证，不是新决策层；宏观 worker 的结构化研究不改变宏观发布 schema、数据采集或交易授权。`core/macro_evidence.py` 将已有捕获整理为逐项时间与来源收据，只计算同源样本变化和同日官方利率差；不取数、不宣称实时或因果。`report/macro_report.py` 从验证后的结果派生摘要和只读报告；ops 负责把初始 / 追问分别保留在工作流与任务回执中，报告层不修改交易或宏观发布状态。

**领域 Agent**——职责定义在 `agents/`，运行方式见 `agents/README.md`。角色不拥有文件/券商写权限；
`ops` 控制器选择 Claude/Codex 执行通道（`routing.json` 的 `execution_transport`：Desktop 交接或 CLI），验证冻结输入与研究结果；技术提案仅经现有 writer 和 revision 检查落卡。每日循环只跑 technical 模型（同收盘同卡片版本复用 run），风控收据由 `report/risk_receipt` 代码渲染，三角色工作流只在 weekly；基线指标 `report/agent_metrics`；入口解释器守卫 `core/interpreter`。
现有 `decision/risk/sync` 的机械规则不迁入模型 prompt。`card_autopaint` 与 `agent_jobs` 保留跨模型复核后的受控自动写卡能力，是否要求复核由 `routing.json` 的 `autopaint.review` 决定；金额、退出、CAS 与最终开关均由宿主检查。

**运行**——每个模块既能 import 也能直接跑：

```bash
.venv/moomoo/bin/python3 quant/ops/pipeline.py --mode briefing --json
python3 quant/risk/kelly_size.py --help
```

直接跑靠的是模块顶部这一行 bootstrap（`core/` 里的纯库没有，它们只被 import）：

```python
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
```

因此**目录深度固定为两级**（`quant/<层>/<模块>.py`）。要加第三级，先改这行。

**import**——一律绝对路径 `from quant.<层>.<模块> import ...`；只有 `core/` 内部互相引用用相对
`from .paths import ...`（这一层是自洽的包，不该知道自己叫 `quant.core`）。

**路径**——推导 repo root 只有一个地方：`core/paths.py`。业务代码里写
`Path(__file__).resolve().parent.parent` 等于把"自己在第几层"编码进逻辑—— 重组时
正是这种写法让 5 个文件（`portfolio_brief` / `stock_fetch` / `trade_history_sync` /
`pipeline` / `_env`）一起指错目录。需要什么路径就从 `paths` 拿，缺就往 `paths` 加。

**OpenD**——`common` 是 openapi skill 的模块、不是包，只能靠 `sys.path`。统一走
`core.paths.add_openapi_path()`，不要再各写各的相对路径。

## 已删除：一次性迁移脚本

原 `scripts/maintenance/` 的 4 个脚本（`enrich_events_20260611` / `migrate_events_tz_20260611` /
`reconcile_positions_20260611` / `migrate_to_state_v2`）**删除**。都已跑过、无任何文档引用，
其中两个早已跑不动（依赖不存在的 `../cockpit` 仓库 / 读已废弃的 `lots["core"]` 层级）。

删除的真正理由是**它们是上了膛的枪**：三个既没有 argparse 也没有 dry-run，任何一次调用
（哪怕 `--help`）都直接重写 append-only 的成交账本或 `positions.json`。 重构时对全部入口
跑 `--help` 冒烟，`enrich_events_20260611` 就这样把 `scores.jsonl` 回滚到了 6-11 的快照，
**删掉 30 条里的 12 条**（6/22 之后全部，含 NOK LEAPS 强平那笔和 7/16 整批退出评分）。已从 git 还原。

需要考古时从重构前那个 commit 取：

```bash
git show b98a42e:scripts/maintenance/enrich_events_20260611.py
git show b98a42e:scripts/migrate_to_state_v2.py
```

**新的一次性迁移脚本**：跑完就删，或者从第一天就带 `--apply`（缺省 dry-run）。不要在
`quant/` 里留一个"只读历史、不要再跑"的目录——那句话拦不住任何东西。

## 测试

`tests/` 镜像同一套分层——找某模块的测试就去同名子目录：

```bash
.venv/moomoo/bin/python3 -m pytest -q             # 默认跳过需显式授权的数据/宿主审计
.venv/moomoo/bin/python3 -m pytest tests/core -q  # 只跑共享库
.venv/moomoo/bin/python3 -m pytest --collect-only -q | tail -1  # 现查总数
```

会读真实持仓等私有数据的审计标记为 `live_data`，只有显式传 `--live-data` 才运行；
读取本机已安装任务的 `host_state` 审计需 `--host-state`。默认跳过不代表这些审计已经通过。
收集阶段不加载真实 `.env`；测试 fixture 将 writer、共享锁、临时 Git 仓等依赖指向测试数据。
涉及本地进程/代理的 runner 测试没有真实模型调用，仍需要对应平台的本地集成环境。

**tests/ 为什么不放进 quant/**：测试要横着 import 每一层，放进包里就成了一个「可以任意方向
import」的成员，上面那条依赖规则当场要开例外；而且它是三级深度，破坏 `parents[2]` bootstrap。
包外是 Python 惯例，也让「依赖只能向下」保持零例外。

### 这份文档的规则是被强制的，不是自觉遵守的

上面每一条约定都有对应的失败测试——这个仓库反复栽在「规则只写在文档里」上，所以分层本身也照此办理：

| 守卫 | 钉住什么 |
|---|---|
| `tests/test_layering.py` | 依赖单向向下（模块级零例外，lazy 向上须报备理由且名单不许腐烂）· 深度锁死两级 · bootstrap 唯一写法 · `core/` 内部相对 import · research 是旁路无人依赖 · 新层必须排进依赖序 |
| `tests/test_module_coverage.py` | `quant/<层>/` ↔ `tests/<层>/` 镜像 · 新模块要么有测试要么进 `UNTESTED` 并写明原因 · 补了测试必须从名单删（棘轮，名单只能变短） |
| `tests/test_repo_references.py` | 注释/文档/任务 prompt 里写的 `quant/` `tests/` `scripts/` 路径必须真的存在 · **live 定时任务副本与 `tasks/claude/` 逐字节一致**（脱钩不会报错，只会静静跑旧版） |

两组守卫都做过变异测试：逐条制造违规，确认**每一条都真的会红**（一个只会绿的守卫比没有守卫更坏——
 就有一次冒烟检查因为 macOS 没有 `timeout` 而对 51 个入口全部"通过"）。

`UNTESTED` 的条数不要抄进文档；名单和每条理由都在 `tests/test_module_coverage.py` 中，
需要时现读现算。它是棘轮：补上测试的模块必须出列，名单只能缩短。

仍标记为 **真缺口** 的项目应优先处理；准确名单以
`tests/test_module_coverage.py` 为准。私人研究脚本不进入公开 coverage ledger，
可复用机械件只有在完成脱敏与 synthetic test 后才能加入 `quant/research/`。
