# CoreyTrading

一个小型、确定性、broker 无关的交易决策流水线参考实现。项目关注可解释的状态评分、明确的策略授权、约束式仓位构建，以及经过风险调整的订单意图。

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
| Risk | 单标的、总暴露、止损风险与整数交易单位约束 | `AdjustedTargetPosition` |
| Order | 确定性的限价单意图 | `OrderIntent` |
| Execution | 显式执行适配器边界；仅附带 paper executor | `ExecutionReport` |

默认流水线停在订单意图。只有显式传入执行适配器并设置 `execute=True` 才会执行，因此可以把人工审核或外部审批系统保留在回路中。

## 系统边界

- Score 只分类当前技术状态，不声称预测未来收益。
- 组合构建采用约束式方法，不是协方差优化器。
- Target Position 面向当前决策构建，不维护覆盖全部资产的全局优化目标。
- 项目不包含 broker 凭证、账户适配器、市场数据集、真实持仓或自主下单能力。
- 随附的样例和测试全部使用合成输入。

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
