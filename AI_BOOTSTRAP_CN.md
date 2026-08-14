# coreytrading — AI 搭建与基础设施指南

[English](AI_BOOTSTRAP.md)

本文档供编码 Agent 将一个刚 clone 的仓库搭建为经过验证的本地工作区。内容覆盖项目基础设施和私有工作区构建，但不会取代 `CLAUDE.md` 中的行为、架构和交易规则。

安全默认模式是离线环境加合成数据。Broker 连接、真实账户状态、通知、定时任务、外部服务和 live execution 都是相互独立的可选能力，必须按需启用。

## 目标结果

完成搭建必须同时满足以下条件：

- Agent 已加载完整项目契约；
- 仓库使用受支持且隔离的 Python 环境；
- package 与测试依赖均从 `pyproject.toml` 安装；
- 离线测试不依赖 broker 或私有数据即可通过；
- 只创建当前工作流确实需要的私有运行文件；
- secrets 和私有运行数据只保存在本机并被 Git 忽略；
- 每个可选外部集成都已验证，或明确报告为未配置；
- 不会隐式执行订单、通知、任务注册、commit、push 或其他外部副作用。

依赖安装成功不等于搭建完成。Agent 还必须执行本文的验证门槛并输出规定的交接报告。

## Agent 指令文件：不要重命名 `CLAUDE.md`

不同编码 Agent 没有统一的项目指令文件名。把 `CLAUDE.md` 重命名为 `AGENTS.md` 会降低兼容性，而不是提高兼容性。

已核实的发现规则：

| Agent/runtime | 默认项目指令 | 本仓库策略 |
|---|---|---|
| Claude Code | `CLAUDE.md` | 直接完整读取根目录文件，并保留原文件。 |
| OpenAI Codex | `AGENTS.md` | 先使用已提交的 `AGENTS.md` 入口，再完整读取 `CLAUDE.md`。 |
| Gemini CLI | `GEMINI.md` | 配置 context filename，或创建加载 `CLAUDE.md` 的小型适配文件；不能重命名原文件。 |
| GitHub Copilot | 具体支持取决于 Copilot 运行界面，部分界面识别 `AGENTS.md`、`CLAUDE.md` 或两者 | 检查当前生效的指令清单，避免重复或互相冲突。 |
| 未知 Agent | 取决于厂商 | 先查询该 Agent 的官方指令发现规则；需要时创建小型适配文件并指向 `CLAUDE.md`。 |

官方参考：

- [Codex 使用 AGENTS.md 的自定义指令](https://developers.openai.com/codex/guides/agents-md/)
- [Claude Code 项目记忆与 CLAUDE.md](https://code.claude.com/docs/en/memory)
- [Gemini CLI 使用 GEMINI.md 的 context](https://geminicli.com/docs/cli/gemini-md/)
- [GitHub Copilot 自定义指令支持范围](https://docs.github.com/en/copilot/reference/custom-instructions-support)

### 单一真源策略

- `CLAUDE.md` 是完整且权威的项目契约。
- `AGENTS.md` 是轻量兼容入口。它必须要求 Agent 完整读取 `CLAUDE.md`，不能复制一份契约内容。
- Codex 默认只自动发现总计 32 KiB 的项目指令。完整契约有意保留更多细节，因此薄型 `AGENTS.md` 必须触发显式的完整文件读取，不能复制后被截断。
- 工具专属适配文件必须保持精简，不能重新定义架构、公式、隐私规则或交易行为。
- 如果适配文件与 `CLAUDE.md` 冲突，应停止并修复适配文件，不能静默选择其一。
- 除非已确认所有操作系统和 Agent 都能正确处理，否则不要依赖 symlink。

在仅供 Gemini 使用的本地环境中，可以创建未提交的小型适配文件并使用其官方 import 语法：

```markdown
# Project instructions

@./CLAUDE.md
```

提交新的指令文件前，先检查是否有受支持的 Agent 会同时加载多个文件，导致重复 context。

## 必读顺序

写文件或运行会改变状态的命令之前，Agent 必须依次读取：

1. 当前 Agent 自动选择的指令文件；
2. 根目录 `CLAUDE.md` 全文；
3. 本文档；
4. `quant/README.md`，了解 package 分层和依赖方向；
5. 创建私有状态前，读取 `trade/README.md` 及相关后代目录的 README；
6. 当前能力涉及的 `.claude/rules/` 和 `.claude/skills/*/SKILL.md`；
7. 讨论或注册定时任务前，读取 `tasks/README.md`。

不要预先加载所有 skill 或所有私有工作区 example。项目级契约必须先加载，任务专属材料只在相关时读取。

## 阶段 0 — Preflight 与范围确认

先只核实事实，不改变工作区：

1. 确认当前目录是目标 Git 仓库根目录。
2. 检查 `git status --short`，保留所有已有用户改动。
3. 检测操作系统和可用 Python 版本。
4. 根据下表确定搭建模式。
5. 列出所需外部访问，并标明哪些步骤需要用户授权。
6. 报告 blocker；可选服务不可用时，仍继续安全的离线搭建。

| 模式 | 包含 | 默认排除 |
|---|---|---|
| 文档/schema | 读取契约并检查 example | 安装依赖、创建私有文件、网络访问 |
| 离线开发 | Editable package、测试工具、合成测试 | `.env`、OpenD、broker 调用、通知、定时任务 |
| 私有工作区 | 离线开发，加上从 example 选择性创建的私有文件 | 猜测持仓、批量复制 example、外部数据拉取 |
| Broker 连接 | 私有工作区，加上经过批准的 OpenD 和 broker SDK 配置 | Live trading 和 SDK 解锁交易 |
| Operations | 已验证的 broker 连接，加上选定报告或任务 prompt | 自动注册任务、发送消息或执行订单 |

用户没有指定模式时，默认使用“离线开发”。只有缺失事实或授权会实质影响后续可选阶段时才询问用户。

## 阶段 1 — Python 基础设施

### 受支持的 runtime

最低 Python 版本和依赖以 `pyproject.toml` 为单一真源。不要把版本 pin 复制到新的 requirements 文件，也不要静默升级 broker package。

除非用户提供了其他隔离环境，否则使用仓库本地虚拟环境：

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
```

Windows PowerShell 通常使用：

```powershell
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

规则：

- 使用选定解释器的 `python -m pip`，不能使用含义不明的全局 `pip`；
- 可以使用隔离环境时，不要向系统解释器安装依赖；
- 不能为了方便安装而修改依赖版本；
- 已有环境存在时，替换或升级前先检查其状态；
- 下载 package 需要网络权限时，显式申请权限；
- 在交接报告中写明解释器路径和 Python 版本。

仅运行应用可使用 `python -m pip install -e .`。`dev` extra 是标准测试环境；`broker` extra 安装 broker 支持但不安装测试专属 package。准确版本 pin 始终以 `pyproject.toml` 为准。

## 阶段 2 — 离线验证基线

先运行契约和隐私检查，因为它们失败快且不需要外部服务：

```bash
python -m pytest -q \
  tests/test_project_contract.py \
  tests/test_ai_bootstrap.py \
  tests/test_trade_scaffold.py \
  tests/test_public_sanitization.py
```

然后运行完整测试：

```bash
python -m pytest -q
git diff --check
```

离线测试必须使用合成 fixture 和临时路径。任何读取真实 `trade/` 文件的测试，即使本机通过，也属于搭建失败。依赖 broker 的 smoke check 必须与离线 suite 分开。

测试失败时需要区分：

- 搭建前已经存在的失败；
- 环境或依赖失败；
- Agent 新引入的失败；
- 有意不配置的可选集成。

不能通过删除测试、削弱 guard、读取私有数据或安装无关 package 来掩盖失败。

## 阶段 3 — 私有交易工作区

只有用户要求的工作流需要私有状态时，才进入本阶段。

`trade/` 是 schema 与契约目录，不是内置策略或示例投资组合。其公开文件仅允许 `README.md` 和合成 `*.example.*`；生成的运行文件都是私有文件。

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

不要复制全部 example。Research card、journal、market cache、option policy 和 strategy rule 仅为明确需求创建。策略 example 是 schema，不是有效规则或推荐。

所有权必须严格保持：

- broker synchronization 拥有 broker 派生事实；
- 受控 Agent/用户工作流拥有 thesis、zone、已确认 action 和私有规则；
- descendant README 指定的 decision module 拥有生成的 guard/plan 状态；
- append-only event history 通过新增 correction event 修正，不能静默重写。

## 阶段 4 — Secrets 与本地配置

离线开发不需要任何 secret。

只有经过批准的 broker 连接搭建才执行：

```bash
cp .env.example .env
```

仅在本地填写 `.env`。不能把 secret 输出到 terminal、测试日志、chat、patch、报告或文档。`quant/core/env.py` 会确保已有 process environment 优先于 `.env`。

当前模板将配置分为：

- OpenD host、port、encryption mode 和 private-key path；
- 可选 Telegram 通知凭证；
- 可选外部数据凭证。

规则：

- 私钥必须放在仓库外，并使用本地绝对路径；
- 不能向 `opend/OpenD.xml.reference` 写入真实凭证；
- 不能提交已填写的 `.env`、`.mcp.json`、OpenD 配置、broker export 或 key；
- 不要生成看起来可用的假 secret；
- 必填值未知时保持 unresolved，并在报告中说明。

`.mcp.json.example` 有意保持为空。应用不强制依赖 MCP。只有用户批准集成时才创建本地 `.mcp.json`；凭证放在环境变量中，并在启用可修改外部系统的 tool 前写清 trust boundary。

## 阶段 5 — 可选 OpenD 与 broker 基础设施

OpenD 是可选组件。只有用户明确授权 broker 连接时才进入本阶段。

1. 读取 `.claude/rules/tools-environment.md`。
2. 当前 Agent 支持项目 skill 时，安装或升级 OpenD 前读取 `.claude/skills/install-moomoo-opend/SKILL.md`。
3. 阅读 `opend/README.md` 和对应的 OpenAPI legal notice。
4. 使用 provider 当前官方发行版安装或启动 OpenD GUI。
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

通知模块属于可选外部副作用。只有凭证、destination、message scope 和测试消息授权都明确后才配置。成功本地 render 不等于获得发送授权。

### 定时任务

`tasks/` 保存可复用 prompt，不代表已有 active schedule。注册前必须：

1. 读取 `tasks/README.md` 和选定 prompt；
2. 确认 timezone、market calendar、daylight-saving behavior、working directory、environment 和 notification destination；
3. 先用非交易模式手动验证底层命令；
4. 向用户展示准确 schedule 和 prompt；
5. 获得创建或修改任务的授权。

不能自动注册全部任务，也不能推断 prompt file 已安装到用户的 task runner。

### CI

CI 只安装声明的开发依赖并运行离线验证门槛。它不能获得 broker 凭证、访问私有 `trade/` 运行文件、启动 OpenD、注册任务、发送通知或执行订单脚本。CI 配置属于独立仓库改动，必须正常 review。

## 数据与时间就绪检查

运行 operations report 前，需要区分“已安装”和“数据已就绪”。确认：

- 必需私有文件存在并通过 schema validation；
- timestamp 带 offset 或 `Z`，并在应用 session rule 前完成 normalize；
- market-data source、permission、freshness 和 timezone 已知；
- broker state 已针对请求模式完成 reconcile；
- 缺失数据会 fail closed，而不是伪造连续性；
- task timing 已考虑 operator timezone、exchange calendar 和 daylight-saving change。

系统即使完成安装，只要 market data 陈旧或仍为合成数据，就不能视为 operations ready。

## Mutation 与恢复纪律

- 改动前检查现有文件和 Git 状态。
- 保留无关用户改动和私有运行数据。
- Mutable state 优先使用项目定义的 atomic writer 和 shared lock。
- Setup 期间不能使用 destructive Git command 或宽泛递归删除。
- 获得授权的 migration 执行前，先备份重要私有 state file。
- 一次性 migration 默认 dry-run，并要求显式 apply。
- 除非用户要求，否则不能 stage、commit、push、release、notify 或 register task。

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
Notifications/tasks/MCP 状态：
专项检查：
完整离线测试结果：
未解决输入或待授权事项：
建议的下一条安全命令：
```

私有文件只能报告路径，不能粘贴内容。如果搭建停在较早模式，应明确说明，并列出未尝试的可选阶段。

## AI Agent 快速路径

对于不需要 broker 的标准 fresh-clone 请求，正确快速路径是：

1. 读取 `AGENTS.md` 或当前 Agent 的原生入口；
2. 完整读取 `CLAUDE.md`，然后读取本文档和 `quant/README.md`；
3. 检查 Git 状态和 Python 可用性；
4. 创建或复用隔离环境；
5. 从 `pyproject.toml` 安装 `.[dev]`；
6. 先运行契约/隐私专项测试，再运行完整离线 suite；
7. 不创建 `.env`、私有运行文件、MCP 配置、OpenD、通知或任务；
8. 返回结构化交接报告。

该基线本身就是有效成果。只有工作流确实需要时，才扩展到私有或 broker-connected 基础设施。

## 常见问题与回答

### 不使用 Claude Code 时，需要把 `CLAUDE.md` 重命名吗？

不需要。必须保留 `CLAUDE.md`，因为它是完整项目契约，而且 Claude Code 依赖该文件名。Codex 使用已提交的 `AGENTS.md`；其他 Agent 可创建小型适配文件，要求其完整读取 `CLAUDE.md`。绝不能同时维护两份复制的契约。

### AI 应该先读哪个文件？

先读当前 Agent 自动发现的文件：Codex 从 `AGENTS.md` 开始，Claude Code 从 `CLAUDE.md` 开始。但无论使用什么 Agent，实际行动前都必须完整读取 `CLAUDE.md`。Setup 任务接着读 `AI_BOOTSTRAP_CN.md` 或英文版；私有数据任务继续读 `trade/README.md` 及相关后代契约。

### 为什么 `AGENTS.md` 很短，而 `CLAUDE.md` 很详细？

`AGENTS.md` 只是 discovery adapter。详细规则必须留在一个权威文件中，避免漂移。短入口也能为限制自动加载大小的 Agent 节省空间，并明确要求它使用文件读取工具加载完整契约。

### 这个项目必须使用 Claude Code 吗？

不需要。应用代码、测试、schema 和搭建流程都与 Agent 厂商无关，差异只在自动指令发现方式。非 Claude Agent 只要完整加载并遵守项目契约即可。

### AI 启动时应该读取所有 rule 和 skill 吗？

不应该。它必须读取项目契约和本搭建指南，然后只加载当前任务相关的 rule、skill、task prompt 和 descendant README。把所有任务专属资源都塞进 context 会浪费空间，并引入不相关指令。

### 用户请求模糊时选择哪种搭建模式？

选择“离线开发”。它能建立可测试的代码环境，但不会创建私有文件或连接外部系统。只有实际工作流需要时，才进入“私有工作区”“Broker 连接”或“Operations”。

### 安装或测试项目必须使用 OpenD 吗？

不需要。OpenD 是可选组件。离线 suite 必须在没有 OpenD、broker 凭证和真实账户数据的情况下通过。只有经过批准的 broker-connected workflow 才需要 OpenD。

### 一般 setup 请求中，AI 可以自动安装 OpenD 吗？

不可以。OpenD 安装、GUI 启动、登录、配置和网络访问都属于可选 broker 阶段。必须先确认 broker 连接在任务范围内，再遵循相关 rule 和 skill 改动外部基础设施。

### OpenD 或 market-data service 不可用时怎么办？

继续完成安全离线阶段，把可选集成报告为未配置或不可用，并说明失败的准确检查。不能伪造 broker state、用合成值冒充 live data，或削弱离线测试来掩盖服务缺失。

### Setup 可以用 paper-trading order 测试连接吗？

不可以。Setup 验证必须只读。Paper 和 live environment 都必须显式核实，而且任何一种环境都不自动授权下单、改单或撤单。绝不能通过 SDK 解锁交易。

### Bootstrap 时应该创建 `trade/` 下的所有文件吗？

不应该。只创建当前工作流需要的最少私有文件。Portfolio report 可能需要 positions 和 watchlist，但不代表自动需要 research card、journal、option policy、market cache 或 strategy document。

### `*.example.*` 是 starter data 或有效策略吗？

不是。它们是合成 schema，只定义数据形状、所有权和预期 runtime filename。它们不是当前市场数据、可直接采用的示例投资组合或推荐交易规则。

### AI 可以直接把真实用户数据写进 example 文件吗？

不可以。应把 example 复制到私有 runtime filename，只填写私有副本。公开 example 必须始终保持合成状态并可安全提交。

### 必需的私有值未知时怎么办？

按照 schema 保持 `null`、空值或 unresolved；只有该值阻塞工作流时才向用户询问。绝不能猜测 account identifier、holding、cost、risk limit、authorization、strategy preference 或未知数值。

### 私有工作区已经存在时怎么办？

把任务视为 repair 或 migration，而不是 initialization。先读取并验证已有文件，保留用户编写字段和 broker-owned 字段；获得授权的 migration 前先备份重要状态，只修改最少路径。绝不能盲目复制 example 覆盖已有 runtime file。

### 如何确认生成的私有文件不会被提交？

运行 `git check-ignore -v <path>`，再检查 `git status --short --untracked-files=all`。如果私有 runtime file 显示为可跟踪文件，必须先修复，再继续工作。

### 应该直接填写 `.env.example` 吗？

不应该。它只是公开的 key-name 模板。只有集成需要时才复制为被忽略的 `.env`，并在私有文件中填写本地值。绝不能把真实值写入 `.env.example`。

### `.mcp.json` 是必需的吗？

不是。仓库没有强制 MCP server。只有用户批准集成时才创建本地配置；凭证保存在环境变量中，并在启用 tool 前审查其 read/write 权限。

### 依赖安装与系统 Python 冲突时怎么办？

停止使用系统环境，创建或选择隔离虚拟环境，再用该解释器安装 `pyproject.toml` 声明的版本。不能通过静默修改仓库 pin 来解决 broker SDK 或 protobuf 冲突。

### Setup 时可以把所有依赖升级到最新版吗？

不可以。Setup 的目标是复现声明的环境。依赖升级属于独立代码改动，需要 compatibility review 和完整验证门槛。

### Skipped test 可以接受吗？

只有 skip 属于仓库预期行为时可以，例如可选集成不可用。报告中要写明 skip 数量和原因。新增跳过契约、隐私、架构或合成行为测试，不能算成功搭建。

### 离线测试可以使用用户真实 `trade/` 文件提高真实性吗？

不可以。离线测试必须使用合成 fixture 和临时目录。读取真实持仓或 market cache 会带来隐私、可复现性和误修改风险。

### CI 应该配置 OpenD、secrets、通知或定时任务吗？

不应该。CI 只运行离线验证门槛。除非另有经过明确 review 的私有隔离设计，否则 broker 连接和 operations side effect 都必须留在公开 CI 之外。

### Task prompt 文件可以被视为已经安装的 schedule 吗？

不可以。`tasks/` 只保存可复用 prompt。注册是独立操作，必须核实 timezone、market calendar、environment、destination、准确 cadence 和用户授权。

### 两个指令文件冲突时怎么办？

停止并报告冲突。当前任务的显式用户指令在其有效范围内优先；`CLAUDE.md` 是仓库契约；工具适配文件不能重新定义它。应修复陈旧 adapter 或 prose，并补回归测试，不能静默选择。

### AI 如何判断系统已经可以投入 operations？

完成安装还不够。必需私有 schema 必须验证通过，数据来源和 timestamp 必须新鲜且有授权，broker state 必须针对请求模式完成 reconcile，离线测试必须通过，可选连接检查必须有报告，未解决授权必须明确。

### Setup 后的最终回答应该包含什么？

使用本文的交接报告。包含模式、路径、解释器、依赖集合、检查结果、集成状态和 unresolved input。私有文件只写路径，绝不能粘贴凭证、账户数据、持仓、journal 或私有策略内容。

### Bootstrap 成功后，AI 可以 commit 或 push 吗？

只有用户明确要求对应 Git 操作时才可以。测试通过不会自动授权 staging 或 publication。任何另行授权的 commit/push 前，都必须检查 diff 和公开/私有边界。
