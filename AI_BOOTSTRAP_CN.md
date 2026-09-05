# coreytrading — AI 搭建与基础设施指南

[English](AI_BOOTSTRAP.md)

本文档供编码 Agent 将一个刚 clone 的仓库搭建为经过验证的本地工作区，覆盖项目基础设施、私有交易工作区、可选的券商连接和可选的运营层（通知、定时任务、领域 Agent 运行器）。它不取代 `CLAUDE.md` 中的行为、架构和交易规则。

安全默认模式是离线环境加合成数据。Broker 连接、真实账户状态、通知、定时任务、模型运行器和 live execution 都是相互独立的可选能力。

## 目标结果

完成搭建必须同时满足以下条件：

- Agent 已加载完整项目契约；
- 仓库使用受支持且隔离的 Python 环境，依赖来自 `pyproject.toml`，包括固定版本的交易所日历；
- 离线测试不依赖 broker、模型 CLI 或私有数据即可通过；
- 只创建当前工作流确实需要的私有运行文件，且都从 `trade/` 下的示例复制而来；
- secrets、私有运行数据、`var/` 下的运行产物和可选的 `ai_news/` 工作区只保存在本机并被 Git 忽略；
- 每个可选集成都已验证，或明确报告为未配置；
- 不会隐式执行订单、通知、任务注册、模型调用、commit、push 或其他外部副作用。

依赖安装成功不等于搭建完成。Agent 还必须执行本文的验证门槛并输出规定的交接报告。

## Agent 指令文件：不要重命名 `CLAUDE.md`

不同编码 Agent 没有统一的项目指令文件名。把 `CLAUDE.md` 重命名为 `AGENTS.md` 会降低兼容性，而不是提高兼容性。

| Agent/runtime | 默认项目指令 | 本仓库策略 |
|---|---|---|
| Claude Code | `CLAUDE.md` | 直接完整读取根目录文件，并保留原文件。 |
| OpenAI Codex | `AGENTS.md` | 先使用已提交的 `AGENTS.md` 入口，再完整读取 `CLAUDE.md`。 |
| Gemini CLI | `GEMINI.md` | 配置 context filename，或创建导入 `CLAUDE.md` 的小型未提交适配文件；不能重命名原文件。 |
| GitHub Copilot | 取决于 Copilot 运行界面，部分界面识别 `AGENTS.md`、`CLAUDE.md` 或两者 | 检查当前生效的指令清单，避免重复或互相冲突。 |
| 未知 Agent | 取决于厂商 | 先查该 Agent 的指令发现规则；需要时创建小型适配文件并指向 `CLAUDE.md`。 |

### 单一真源策略

- `CLAUDE.md` 是完整且权威的项目契约。它是指针式的紧凑文件；它指向的方法文件（`quant/README.md`、`agents/README.md`、`tasks/README.md`、`.claude/rules/`、`trade/README.md`）各自是所属主题的真源。
- `AGENTS.md` 是轻量兼容入口。它必须要求 Agent 完整读取 `CLAUDE.md`，不能复制一份契约内容。
- 工具专属适配文件必须保持精简，不能重新定义架构、公式、隐私规则或交易行为。适配文件与 `CLAUDE.md` 冲突时，停止并修复适配文件。
- 同一份契约对所有模型和运行器生效，包括 `agents/README.md` 描述的领域 Agent worker；worker 的研究结果永远不会授予契约未给出的权限。

## 必读顺序

写文件或运行会改变状态的命令之前，Agent 必须依次读取：

1. 当前 Agent 自动选择的指令文件；
2. 根目录 `CLAUDE.md` 全文；
3. 本文档；
4. `quant/README.md`，了解 package 分层和依赖方向；
5. 创建私有状态前，读取 `trade/README.md` 及相关后代目录的 README；
6. 配置或调用任何领域 Agent 运行器前，读取 `agents/README.md`；
7. 当前能力涉及的 `.claude/rules/` 和 `.claude/skills/*/SKILL.md`；
8. 讨论或注册定时任务前，读取 `tasks/README.md`。

不要预先加载所有 skill 或所有 example。项目级契约必须先加载，任务专属材料只在相关时读取。

## 阶段 0 — Preflight 与范围确认

先只核实事实，不改变工作区：

1. 确认当前目录是目标 Git 仓库根目录。
2. 检查 `git status --short`，保留所有已有用户改动。
3. 检测操作系统和可用 Python 版本（最低版本由 `pyproject.toml` 声明）。
4. 根据下表确定搭建模式。
5. 列出所需外部访问，并标明哪些步骤需要用户授权。
6. 报告 blocker；可选服务不可用时，仍继续安全的离线搭建。

| 模式 | 包含 | 默认排除 |
|---|---|---|
| 文档/schema | 读取契约并检查 example | 安装依赖、创建私有文件、网络访问 |
| 离线开发 | Editable package、测试工具、合成测试 | `.env`、OpenD、broker 调用、模型 CLI、通知、定时任务 |
| 私有工作区 | 离线开发，加上从 example 选择性创建的私有文件 | 猜测持仓、批量复制 example、外部数据拉取 |
| Broker 连接 | 私有工作区，加上经过批准的 OpenD 和 broker SDK 配置 | Live trading 和 SDK 解锁交易 |
| Operations | 已验证的 broker 连接，加上选定报告、任务 prompt 和领域 Agent 运行器 | 自动注册任务、发送消息、未经复核的写卡或执行订单 |

用户没有指定模式时，默认使用“离线开发”。只有缺失事实或授权会实质影响后续可选阶段时才询问用户。

## 阶段 1 — Python 基础设施

### 受支持的 runtime

最低 Python 版本和依赖以 `pyproject.toml` 为单一真源：应用依赖 `numpy`、`pandas` 与 `exchange-calendars`（每份定时任务门背后的 XNYS 交易日历），`broker` extra 提供 Moomoo SDK 及其 protobuf pin，`dev` extra 提供 `pytest`。不要把版本 pin 复制到新的 requirements 文件，也不要静默升级 broker package。

除非用户提供了其他隔离环境，否则使用仓库本地虚拟环境：

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
```

Windows PowerShell 通常先执行 `.venv\Scripts\Activate.ps1`，再运行同一条安装命令。

规则：

- 使用选定解释器的 `python -m pip`，不能使用含义不明的全局 `pip`；
- 可以使用隔离环境时，不要向系统解释器安装依赖；broker SDK 的 protobuf 约束会与很多系统环境冲突；
- 不能为了方便安装而修改依赖版本；
- 已有环境存在时，替换或升级前先检查其状态；
- 下载 package 需要网络权限时，显式申请权限；
- 在交接报告中写明解释器路径和 Python 版本。

仅运行应用可使用 `python -m pip install -e .`。`dev` extra 是标准测试环境。

## 阶段 2 — 离线验证基线

先运行契约、脚手架和隐私检查，因为它们失败快且不需要外部服务：

```bash
python -m pytest -q \
  tests/test_project_contract.py \
  tests/test_ai_bootstrap.py \
  tests/test_trade_scaffold.py \
  tests/test_public_sanitization.py \
  tests/core/test_market_sessions.py
```

然后运行完整测试：

```bash
python -m pytest -q
git diff --check
```

离线测试使用合成 fixture 和临时路径。任何读取真实 `trade/` 文件的测试，即使本机通过，也属于搭建失败；`tests/test_live_data_isolation.py` 在源码层钉住这一点。需要本机模型 CLI 或 OS 沙箱的 runner 集成测试按设计被跳过或隔离，绝不调用真实模型。依赖 broker 的 smoke check 必须与离线门槛分开。

测试失败时需要区分：搭建前已存在的失败、环境或依赖失败、Agent 新引入的失败、有意不配置的可选集成。不能通过删除测试、削弱 guard、读取私有数据或安装无关 package 来掩盖失败。

## 阶段 3 — 私有交易工作区

只有用户要求的工作流需要私有状态时，才进入本阶段。

`trade/` 是 schema 与契约目录，不是内置策略或示例投资组合。其公开文件仅允许 `README.md` 和合成 `*.example.*`；生成的运行文件都是私有文件并被 Git 忽略。

流程：

1. 读取 `trade/README.md` 和所有相关后代 README。
2. 找出当前工作流所需的最少运行文件。
3. 只复制这些 example，并从目标文件名移除 `.example`。
4. 保留字段名和数据类型。
5. 仅使用用户提供的事实，或来自显式授权来源的数据替换合成值。
6. 未知值保持 `null`、空值或列入待确认；绝不能把未知变成零。
7. 验证 JSON/JSONL 语法和适用 schema。
8. 确认生成路径已被 Git 忽略。

以下是只在确实需要对应文件时才使用的最小命令：

```bash
cp trade/us_stock/holding/state/positions.example.json \
  trade/us_stock/holding/state/positions.json
cp trade/us_stock/holding/state/watchlist.example.json \
  trade/us_stock/holding/state/watchlist.json
git check-ignore -v \
  trade/us_stock/holding/state/positions.json \
  trade/us_stock/holding/state/watchlist.json
```

后续工作流各自从对应示例新增的运行文件包括：`holding/events/` 下的事件账本与 NAV 历史、NAV 报告用来剔除出入金的外部现金流账本、`valuation/` 下的估值人工区与账本、`market/` 下的市场缓存，以及 `strategy/` 下的私有策略规则。不要复制全部 example；策略 example 是 schema，不是有效规则或推荐。

所有权必须严格保持：broker synchronization 拥有 broker 派生事实；受控的 Agent 与用户工作流拥有 thesis、zone、已复核的 action 和私有规则；decision module 拥有后代 README 指定的生成 guard/plan 状态；append-only 事件历史通过新增修正事件修正，不能重写。

## 阶段 4 — Secrets 与本地配置

离线开发不需要任何 secret。

只有经过批准的 broker 连接或 operations 搭建才执行：

```bash
cp .env.example .env
```

仅在本地填写 `.env`。不能把 secret 输出到 terminal、测试日志、chat、patch、报告或文档。`quant/core/env.py` 会确保已有 process environment 优先于 `.env`。

模板将配置分为：OpenD host、port、encryption mode 和 private-key path；可选 Telegram 通知凭证（通知器读取的凭据文件位置是 `~/.config/coreytrading/telegram.env`）；可选外部数据凭证。通知的可选尾行通过 `TG_FOOTER_LINE` 配置，未设置即没有尾行。

规则：

- 私钥必须放在仓库外，并使用本地绝对路径；
- 不能向 `opend/OpenD.xml.reference` 写入真实凭证；
- 不能提交已填写的 `.env`、`.mcp.json`、OpenD 配置、broker export、key 或模型 CLI 的登录状态；
- 不要生成看起来可用的假 secret；
- 必填值未知时保持 unresolved，并在报告中说明。

`.mcp.json.example` 有意保持为空。应用不强制依赖 MCP。只有用户批准集成时才创建本地 `.mcp.json`；凭证放在环境变量中，并在启用可修改外部系统的 tool 前写清 trust boundary。

## 阶段 5 — 可选 OpenD 与 broker 基础设施

OpenD 是可选组件。只有用户明确授权 broker 连接时才进入本阶段。

1. 读取 `.claude/rules/tools-environment.md`。
2. 当前 Agent 支持项目 skill 时，安装或升级 OpenD 前读取 `.claude/skills/install-moomoo-opend/SKILL.md`。
3. 阅读 `opend/README.md` 和对应的 OpenAPI legal notice。
4. 使用 provider 当前官方发行版安装或启动 OpenD。
5. 除非用户明确设计并保护远程访问，否则 listener 只绑定本机。
6. 将脱敏 reference config 复制到 OpenD 的仓库外配置位置，只在那里填写真实值。
7. 根据仓库 pin 和 skill check 对齐 broker SDK 与 OpenD 版本。
8. 执行任何 state synchronization 前，先用只读账户或 quote 操作验证连接。
9. Broker smoke check 必须与离线测试门槛分开。

硬性安全边界：

- 绝不能通过 SDK 解锁交易；
- Setup 或验证过程不能下单、改单或撤单；
- 不能因为默认值就假设是 paper trading，必须核实实际环境；
- 没有明确 network/security 设计时，不能把 OpenD 暴露到 localhost 之外；
- 不能把填有真实值的 OpenD 配置复制回仓库。

OpenD 不存在时，“离线开发”仍是有效的完成模式。应报告 broker 连接未配置，而不是把它视为 core setup 失败。

## 阶段 6 — 可选 Operations 基础设施

### 通知

通知模块属于可选外部副作用。只有凭证、destination、message scope 和测试消息授权都明确后才配置。本地 render 成功（`quant/ops/tg_notify.py --dry-run`）不等于获得发送授权。

### 领域 Agent 运行器

四个领域角色通过 `quant/ops/agent_runners.py` 在 Claude Code 与 Codex CLI 上运行；`agents/routing.json` 为每个角色指定运行器、首选模型、次选模型与 effort。启用前：

1. 读取 `agents/README.md` 与 `agents/MODEL_POLICY.md`；
2. 在本机安装并登录路由需要的 CLI；代码永不读取或保存它们的凭据，`python3 quant/ops/agent_doctor.py --json` 只按路径报告二进制解析结果，不执行任何东西；
3. 确认隔离前提（Claude 以无工具模式运行；Codex runner 需要 macOS 的 `sandbox-exec` 和限定模型域名的代理），并复核 `python3 quant/ops/agent_runtime.py roles`；
4. 在独立运行目录内先跑合成契约探针，遵守全局调用额度，再启动任何生产任务；
5. 在 operator 明确授权复核后的自动写卡之前，保持 `autopaint.apply` 为 false；永远不要把 `succeeded` 的研究回执读成已写卡，只有 `applied` 才是。

运行产物、租约与备份放在被忽略的 `var/` 目录下。Worker 没有工作区、券商、shell、消息或下单权限；不要为了让流程省事而扩大它的权限。

### 定时任务

`tasks/` 保存可复用 prompt，不代表已有 active schedule。注册前必须：

1. 读取 `tasks/README.md` 和选定 prompt；
2. 确认 timezone、working directory、environment 和 notification destination；交易所日历与夏令时行为由每份 prompt 先跑的门（`python3 quant/ops/agent_jobs.py due --kind <kind>`）裁定，先确认这道门在本机能正常回答；
3. 先用非交易模式手动验证底层命令；
4. 向用户展示准确 schedule 和 prompt；
5. 获得创建或修改任务的授权。

不能自动注册全部任务，也不能推断 prompt file 已安装到用户的 task runner；注册、文件存在与完成回执是三种不同的证据。

### CI

CI 只安装声明的开发依赖并运行离线验证门槛。它不能获得 broker 凭证或模型 CLI 登录、访问私有 `trade/` 运行文件、启动 OpenD、注册任务、发送通知或执行订单脚本。

## 数据与时间就绪检查

运行 operations report 前，需要区分“已安装”和“数据已就绪”。确认：

- 必需私有文件存在并通过 schema validation；
- timestamp 带 offset 或 `Z`，并在应用 session rule 前完成 normalize；
- market-data source、permission、freshness 和 timezone 已知，收盘依赖的转移只用完整 bar；
- broker state 已针对请求模式完成 reconcile；
- 缺失数据会 fail closed，而不是伪造连续性；
- 交易所日历门能回答所请求的 session；不支持的日期或缺失的日历依赖是错误，不是按星期几猜。

系统即使完成安装，只要 market data 陈旧或仍为合成数据，就不能视为 operations ready。

## Mutation 与恢复纪律

- 改动前检查现有文件和 Git 状态。
- 保留无关用户改动和私有运行数据。
- Mutable state 优先使用项目定义的 atomic writer 和 shared lock；卡片写入走 `.claude/skills/trading-analysis/scripts/update_position_zones.py`。
- Setup 期间不能使用 destructive Git command 或宽泛递归删除。
- 获得授权的 migration 执行前，先备份重要私有 state file。
- 一次性 migration 默认 dry-run，并要求显式 apply。
- 除非用户要求，否则不能 stage、commit、push、release、notify、register task 或调用模型运行器。

## 完成门槛与交接报告

完成后，向用户提供包含以下准确信息的简洁报告：

```text
搭建模式：
Agent/runtime 与已加载指令文件：
仓库根目录与已有改动状态：
Python executable 与版本：
已安装依赖集合：
创建或修改的私有运行文件（仅路径）：
Secrets/configuration 状态（绝不输出值）：
OpenD/broker 状态：
Notifications/tasks/MCP/模型运行器状态：
专项检查：
完整离线测试结果：
未解决输入或待授权事项：
建议的下一条安全命令：
```

私有文件只能报告路径，不能粘贴内容。如果搭建停在较早模式，应明确说明，并列出未尝试的可选阶段。

## AI Agent 快速路径

对于不需要 broker 的标准 fresh-clone 请求：

1. 读取 `AGENTS.md` 或当前 Agent 的原生入口；
2. 完整读取 `CLAUDE.md`，然后读取本文档和 `quant/README.md`；
3. 检查 Git 状态和 Python 可用性；
4. 创建或复用隔离环境；
5. 从 `pyproject.toml` 安装 `.[dev]`；
6. 先运行专项检查，再运行完整离线 suite；
7. 不创建 `.env`、私有运行文件、MCP 配置、OpenD、通知、任务或模型运行器登录；
8. 返回结构化交接报告。

该基线本身就是有效成果。只有工作流确实需要时，才扩展到私有、broker-connected 或 operations 基础设施。

## 常见问题与回答

### 不使用 Claude Code 时，需要把 `CLAUDE.md` 重命名吗？

不需要。必须保留 `CLAUDE.md`，因为它是完整项目契约，而且 Claude Code 依赖该文件名。Codex 使用已提交的 `AGENTS.md`；其他 Agent 可创建小型适配文件，要求其完整读取 `CLAUDE.md`。绝不能同时维护两份复制的契约。

### AI 应该先读哪个文件？

先读当前 Agent 自动发现的文件：Codex 从 `AGENTS.md` 开始，Claude Code 从 `CLAUDE.md` 开始。无论使用什么 Agent，行动前都必须完整读取 `CLAUDE.md`。Setup 任务接着读本文档；私有数据任务继续读 `trade/README.md`；模型运行器任务继续读 `agents/README.md`。

### 这个项目必须使用 Claude Code 吗？

不需要。应用代码、测试、schema 和搭建流程都与 Agent 厂商无关，差异只在自动指令发现方式。领域 Agent 运行器同样是可选项：路由可以把角色指向任一 CLI，两者都不装时离线系统照样工作。

### 用户请求模糊时选择哪种搭建模式？

“离线开发”。它能建立可测试的代码环境，但不会创建私有文件、连接外部系统或调用模型。

### 安装或测试项目必须使用 OpenD 吗？

不需要。离线 suite 在没有 OpenD、broker 凭证和真实账户数据的情况下通过。只有经过批准的 broker-connected workflow 才需要 OpenD，一般 setup 请求中 Agent 不能自动安装它。

### OpenD、market-data service 或模型 CLI 不可用时怎么办？

继续完成安全离线阶段，把可选集成报告为未配置或不可用，并说明失败的准确检查。不能伪造 broker state、用合成值冒充 live data，或削弱测试来掩盖服务缺失。

### Setup 可以用 paper-trading order 测试连接吗？

不可以。Setup 验证必须只读。Paper 和 live environment 都必须显式核实，而且任何一种环境都不自动授权下单、改单或撤单。绝不能通过 SDK 解锁交易。

### 领域 Agent 的研究结果会写卡吗？

不会。Worker 只返回 JSON；只有宿主控制器可以提交，而且必须先经另一运行器独立复核、在 writer 锁内通过政策与版本检查。`succeeded` 回执是研究，`applied` 是唯一的已写卡回执。

### `*.example.*` 是 starter data 或有效策略吗？

不是。它们是合成 schema，只定义数据形状、所有权和 runtime filename。它们不是市场数据、可直接采用的示例投资组合或推荐交易规则，真实用户数据绝不能写进 example 文件。

### 必需的私有值未知时怎么办？

按照 schema 保持 `null`、空值或 unresolved；只有该值阻塞工作流时才向用户询问。绝不能猜测 account identifier、holding、cost、risk limit、authorization 或 strategy preference。

### 私有工作区已经存在时怎么办？

把任务视为 repair 或 migration，而不是 initialization。先读取并验证已有文件，保留用户编写字段和 broker-owned 字段；获得授权的 migration 前先备份重要状态，只修改最少路径。绝不能盲目复制 example 覆盖已有 runtime file。

### 如何确认生成的私有文件不会被提交？

运行 `git check-ignore -v <path>`，再检查 `git status --short --untracked-files=all`。私有 runtime file、`var/` 产物或 `ai_news/` 文件显示为可跟踪文件，都是必须先修复的失败。

### `.env.example` 和 `.mcp.json.example` 怎么用？

它们是公开模板。只有集成需要时才把 `.env.example` 复制为被忽略的 `.env`，并在私有文件中填写本地值。MCP 不是必需的，只有用户批准集成时才创建本地 `.mcp.json`。

### Skipped test 可以接受吗？

只有 skip 属于仓库预期行为时可以，例如可选集成不可用，或需要显式旗标的宿主状态审计。报告中要写明 skip 数量和原因。新增跳过契约、隐私、架构或合成行为测试，不能算成功搭建。

### Task prompt 文件可以被视为已经安装的 schedule 吗？

不可以。`tasks/` 只保存可复用 prompt。注册是独立操作，必须核实 timezone、environment、destination、准确 cadence 和用户授权；每份 prompt 内的日历门决定某次唤醒是否真的运行。

### Bootstrap 成功后，AI 可以 commit 或 push 吗？

只有用户明确要求对应 Git 操作时才可以。测试通过不会自动授权 staging 或 publication。任何另行授权的 commit/push 前，都必须检查 diff 和公开/私有边界。
