# `tests/research/` — 预注册机械件的合成数据验证

`quant/research/` 是验证层旁路：一次性回测与校准产出统计而不是动作。但这些结果会改变
live authorization、止损语义、快车道阈值和组合预算，所以**回测机械件本身必须被测试守住**。
一个算错的回测会把错误参数送进 live，同时看起来完全正常。

## 预注册纪律

> **实现期间只允许合成数据；真实或授权历史数据在机械件锁定后运行。**

每个测试对应设计预注册中的一条机械语义：序列构造、时点、统计口径或裁决规则。
判据必须先于结果锁定；看到历史结果后不能回头修改判据来迎合数字。

## 自由度审计棘轮

`test_dof_audit.py` 钉住 money-moving layer 的无出处自由度基线。

设计目标是**棘轮，而不是限流**：

- 有裁决或回测依据的参数新增不设置拍脑袋限额；
- 没有出处的自由度只能减少，不能增加；
- 当前数量以测试中的 executable baseline 为准，不能复制到文档中。

## 仍需优先补齐的机械件

`tests/test_module_coverage.py` 的 `UNTESTED` 名单保存准确状态和理由。优先关注：

- `quant/research/exit_rules_backtest.py` — 其结果影响利润棘轮与趋势退出参数；
- `quant/research/structure_downgrade_backtest.py` — 其结果影响 entry-guard 降级条件。

共同点是：它们的输出会改变真实决策参数，但判据来源仍需要更完整的合成数据验证。
