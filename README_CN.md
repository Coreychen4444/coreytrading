# CoreyTrading

一个小型、确定性、broker 无关的交易决策流水线参考实现。项目关注可解释的状态评分、明确的策略授权、约束式仓位构建，以及经过风险调整的订单意图。

> **核心策略：** 一套由量价与市场结构驱动的技术面优先趋势跟踪框架，以确认信号分阶段建仓、以分层风险约束限制敞口，并默认保留人工审核执行。

[English](README.md)

> 本项目仅用于软件与研究演示，不构成投资建议。默认参数和样例都是说明性质，不能直接视为实盘参数。

## 架构

```text
Data
  ↓
Model
  ↓
Prediction / Score
  ↓
Strategy
  ↓
Signal
  ↓
Portfolio Construction
  ↓
Target Position
  ↓
Risk
  ↓
Adjusted Target Position
  ↓
Order
  ↓
Execution
```

| 阶段 | 公开实现 | 输出 |
|---|---|---|
| Data | 与供应商无关的 `DataSource`；测试使用内存数据源 | `MarketSnapshot` |
| Model | 确定性的趋势、距离、成交量与 regime 特征 | `ModelFeatures` |
| Prediction / Score | 可解释的当前状态评分，而非收益预测 | `Prediction` |
| Strategy | 根据 zone、regime 和评分进行授权 | `GO`、`WAIT` 或 `VETO` |
| Portfolio Construction | 按确认度缩放的增量配置 | `TargetPosition` |
| Risk | 单标的、总暴露、止损风险、可选分轮预算与整数交易单位约束 | `AdjustedTargetPosition` |
| Order | 确定性的限价单意图 | `OrderIntent` |
| Execution | 显式执行适配器边界；仅附带 paper executor | `ExecutionReport` |
| 测试 / 回测验证 | 横切全系统的合成数据单元与集成测试；离线研究在真数据运行前必须先验证机械件 | 发布闸门 |

默认流水线停在订单意图。只有显式传入执行适配器并设置 `execute=True` 才会执行，因此可以把人工审核或外部审批系统保留在回路中。

## 系统边界

- Score 只分类当前技术状态，不声称预测未来收益。
- 组合构建采用约束式方法，不是协方差优化器。
- 可选的非递增分轮风险计划可以限制每次后续加仓；未显式配置时保持关闭。
- Target Position 面向当前决策构建，不维护覆盖全部资产的全局优化目标。
- 项目不包含 broker 凭证、账户适配器、市场数据集、真实持仓或自主下单能力。
- 随附的样例和测试全部使用合成输入。

## 测试与回测验证

`tests/` 是框架的可执行发布闸门，不是附属样例目录：

- `test_model.py` 验证特征/评分行为与评分边界。
- `test_pipeline.py` 验证完整决策链、授权 veto 与显式执行开关。
- `test_risk.py` 使用合成组合状态锁定 binding cap 选择、分轮预算耗尽和整数单位取整。
- 新增 strategy、sizing、risk 或离线 research 机械件时，应先写成确定性的合成场景，并在信任任何历史数据结果前将其固化为回归测试。

公开包有意不附带账户记录、历史行情数据集或绩效声明。历史回测属于离线扩展边界；其时序、统计口径与裁决规则应脱离真实数据单独验证，避免“结果好看”掩盖回测机械件错误。

```text
tests/
├── test_model.py      模型与评分不变量
├── test_pipeline.py   signal 到 order 的端到端行为
└── test_risk.py       组合 cap 与取整不变量
```

## 快速开始

项目无运行时第三方依赖，需要 Python 3.11 或更高版本。

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
python examples/minimal_pipeline.py
```

运行测试：

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

## License

MIT
