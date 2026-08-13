# TradingOps

TradingOps 是一套开源、技术面优先的交易运营框架：把 broker 与市场数据转换成确定性的状态、风控检查、进出场计划、报告和需要人工确认的订单方案。

[English](README.md)

> 本项目是研究与运营软件，不构成投资建议。Live pipeline 不会自动下单；所有输出都应人工复核，broker 凭证必须只保存在本机。

让 AI 从 fresh clone 开始搭建时，先读取中文版 [`AI_BOOTSTRAP_CN.md`](AI_BOOTSTRAP_CN.md)（[English](AI_BOOTSTRAP.md)）。完整项目契约继续保留在 [`CLAUDE.md`](CLAUDE.md)；[`AGENTS.md`](AGENTS.md) 只是 Codex 等 Agent 的兼容入口，不能替代前者。

## 项目包含什么

公开仓库与实际系统保持同一套应用目录：

```text
quant/
├── core/       schema、路径、结构、sizing 与账本规则的单一真源
├── data/       外部市场数据采集
├── sync/       broker 真值与本地 state 对账
├── decision/   入场、退出、regime、漂移、价位与 tape 闸门
├── options/    期权选择、IV 检查与保费阶梯
├── risk/       组合与仓位风险分析
├── journal/    结构化决策和成交注解
├── report/     持仓、绩效与 gate-ladder 报告
├── ops/        pipeline 编排与通知
└── research/   离线回测/校准；live 代码禁止依赖

tests/           与代码分层镜像的单元、集成和架构测试
tasks/           可复用的定时任务 prompt
.claude/         项目规则与交易/OpenD skills
trade/           面向 AI 的私有数据搭建契约与合成 example
opend/           已脱敏的 OpenD 配置模板
```

真实持仓、成交、订单、NAV、日志、行情缓存、研究、策略、凭证、账户 ID 和机器专属配置均不公开。整个 `trade/` 只保留 README 契约和合成 example，使 AI 能按原路径快速搭建用户自己的私有工作区。

## 系统边界

- 价格结构决定 `zone / gate / regime / cap` 授权。
- 基本面、新闻、财报和宏观主要是情报输入；明确的事件风控可以调整 sizing。
- 系统生成判定、仓位 cap、整数交易单位阶梯、期权参数、告警和 broker 侧授权清单。
- Canonical pipeline 不提交订单，执行必须人工确认或走独立审批层。
- 执行后的订单和成交可以重新同步到本地 state，形成闭环。

## 快速开始

推荐 Python 3.11 或更高版本：

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
pytest -q
```

需要连接 broker 时再安装可选依赖：

```bash
python -m pip install -e '.[broker]'
cp .env.example .env
```

安装并启动 Moomoo OpenD，只在本机 `.env` 中填写真实值，并阅读 `.claude/rules/tools-environment.md`。严禁提交 `.env`、真实 state、账户导出或私钥。

## 由 AI 初始化私有工作区

先读取 `trade/README.md`：它定义了机器可执行的搭建流程、隐私边界和子目录语义。AI 应继续读取相关子目录的 README，只复制当前工作流所需 example，并移除私有运行文件名中的 `.example`。例如：

```bash
cp trade/us_stock/holding/state/positions.example.json trade/us_stock/holding/state/positions.json
cp trade/us_stock/holding/state/watchlist.example.json trade/us_stock/holding/state/watchlist.json
cp trade/us_stock/market/earnings_calendar.example.json trade/us_stock/market/earnings_calendar.json
```

不带 `.example` 的运行数据由 Git 忽略。Example 只是 schema，不是实时数据或推荐策略；未知的私人事实应留空或向用户确认，不能猜测。Runtime 可以继续在这些目录下生成私有 state、行情、研究和策略文件。

## 测试与研究纪律

`tests/` 镜像 `quant/` 分层，并强制依赖方向、package 深度、模块覆盖、仓库路径、pipeline 顺序和合成交易行为。测试不得读取真实账户 state 或行情缓存。

`quant/research/` 与 live 决策路径隔离。任何历史数据结果都应先用合成或对抗 fixture 验证回测机械件。

## License

MIT。第三方集成保留各自声明与条款；使用内置 Moomoo/OpenD skill 前请先阅读其风险提示。
