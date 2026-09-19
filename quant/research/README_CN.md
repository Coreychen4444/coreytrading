# Research 层

[English](README.md)

`quant/research/` 是公开、离线的验证边界，只保存可复用机械件、合成示例和搭建私人研究的说明。这里不能出现某位操作者的数据集、标的、持仓、成交、账户数值、工作笔记、对话、结论、裁决、待办或历史署名。

Live module 严禁 import 本 package。研究可以读取 production function 进行验证，依赖方向不能反转。

## 公开内容

- `calibration.py` 是通用只读校准工具，仅在显式执行时读取被 Git 忽略的私人 decision ledger；
- `study_example.py` 使用合成记录演示确定性的 paired comparison；
- `study.example.md` 是 AI 应复制到私人工作区的预注册模板；
- `*_backtest.py` 和分析模块保留可复用研究机械件；注释只解释 invariant 和 protocol，不记录某位操作者的研究历史；
- `pull_*_history.py` 接收显式私人 universe，或发现 ignored cache，不随源码提供私人标的清单；
- `tests/research/` 使用合成数据验证公开机械件。

这里不保存任何已填写的研究。公开示例只是 schema 和 mechanics，不是交易证据或建议。

## 目录地图

| 分组 | 用途 | 私人边界 |
|---|---|---|
| `gate_*`、`fastlane_*`、`ladder_*`、`repaint_*` | 结构、入场、确认与重画对比 | universe、window、output、ruling 留在 ignored workspace |
| `exit_*`、`longcall_*`、`options_*`、`synthetic_longcall_*` | 退出与期权政策回放 | contract、fill、mark、exclusion、account state 不进 Git |
| `earnings_*`、`risk_*`、`shock_*`、`poc_*` | 事件、风险和校准研究 | provider payload 和已填结果不进 Git |
| `indicator_*`、`volume_*`、`interaction_*`、`zone_*`、`dof_audit.py` | 离线诊断与审计 | runtime observation 和 report 留在 ignored path |
| `pull_*_history.py` | 可选研究取数 | 必须有明确授权和私人 manifest |

使用前先运行模块的 `--help`。Import research module 时不得启动研究、调用 provider 或写文件。

## 私人输入示例

以下文件只能创建在被忽略的研究工作区。占位符必须由用户事实替换，已填写版本不能提交。

深度日线 puller 使用的 universe manifest：

```json
["SYNTH_A", "SYNTH_B"]
```

Long-call 回放 manifest：

```json
[
  {
    "ticker": "SYNTH_A",
    "entry_date": "<YYYY-MM-DD>",
    "option_code": "<PRIVATE_OCC_SYMBOL>"
  }
]
```

退出规则回放 config：

```json
{
  "start_date": "<YYYY-MM-DD>",
  "excluded_instruments": [],
  "bad_klines": []
}
```

HVN incumbent 回归收据（`--baseline`）：

```json
{
  "n_episodes": 0,
  "months": 0,
  "kish_n": 0.0,
  "A_primary_T60": {},
  "cells": {
    "<INCUMBENT_CELL>": [0.0, 0.0, 0.0, 0.0]
  }
}
```

每个 cell 数组依次是 `[fire_rate, diff_mean, ci_low, ci_high]`。只能用锁定 incumbent run 在 ignored workspace 中填写该收据。

Option overlay config：

```json
{
  "exclude_symbols": []
}
```

Entry cap config：

```json
{
  "merge_spreads": []
}
```

Puller 和窗口型回测的日期范围通过 CLI 提供，并同步记录在私人 preregistration。

## AI 搭建流程

1. 创建任何文件前，读取 `AGENTS.md`、本文和 `trade/knowledge/research/README.md`。
2. 把研究问题改写成针对一个 production behavior 的单一、可证伪假设。
3. 将 `study.example.md` 复制到被 Git 忽略的私人工作区，并从私人文件名中移除 `.example`。
4. 向用户确认所有未决选择：population、observation unit、metric、horizon、threshold、missing-data behavior、fallback branch 和 promotion rule。不能从公开示例猜测。
5. 先用 synthetic/adversarial record 构建机械件。读取私人数据前，测试必须覆盖构造、边界、缺失数据和所有 decision branch。
6. 查看最终结果前记录不可变的 protocol receipt。修改已经锁定的选择必须创建新版本，不能静默改写旧研究。
7. 运行获准的私人数据，保留失败和限制；所有 input、report、chart、ruling 和 note 都留在 Git 之外。
8. 研究结论只有通过单独 review 的 production change 和 production-layer test 才能进入 live behavior；研究本身不产生授权。

运行既有机械件前，还要确认源码 literal 没有编码私人 ticker、option contract、account value、sample result 或历史批注。脚本需要这些值时，应新增显式私人输入，不能再加常量。

建议的 ignored layout：

```text
trade/knowledge/research/<study_slug>/
├── preregistration.md
├── data_manifest.json
├── inputs/
├── outputs/
└── ruling.md
```

只创建当前研究需要的文件。缺失的私人事实必须保持缺失。

## 必备研究契约

每项私人研究必须声明：

- hypothesis 与 null；
- observation unit 和纳入/排除规则；
- source、license、freshness、session completeness 与 survivorship limitation；
- 被验证的 production predicate 或 formula；
- primary metric、horizon、threshold、uncertainty method 与 minimum sample rule；
- multiple-comparison policy 和 sensitivity analysis；
- missing-data、error 与 insufficient-evidence branch；
- support、no support、inconclusive 的精确 decision table；
- 已知限制、反证和重跑条件；
- 结果仅供参考，还是可以提出 production proposal。

## 公开/私人判定

文件只有同时满足以下条件才可公开：

- 能跨操作者复用；
- 不含真实账户或操作者专属 observation；
- 示例完全合成；
- 注释解释 invariant behavior，不记录内部历史；
- import 时不会读取私人状态或启动研究；
- 有 synthetic test，或在 coverage ledger 中明确登记。

否则必须留在被忽略的私人工作区。

## 常见问题

### 回测脚本可以提交吗？

只有在它可复用、不含私人 universe/result、默认使用 synthetic/explicit input、import 时不运行且具备合成测试时才可以。一次性研究必须保持私人。

### 既有脚本可以读取被忽略的 canonical data path 吗？

可以，但只能在私人协议锁定后显式执行。Import 必须保持无副作用，生成报告必须写入 ignored output path。

### AI 发现脚本仍有硬编码私人样本时怎么办？

将样本改为必填 CLI 参数或 ignored JSON manifest，增加 synthetic boundary test，并在本文记录 schema。不能把原值留在注释或 fixture 中。

### 预注册和裁决放在哪里？

放在 ignored study directory。公开仓库提供 template 和 method，不公开某位操作者的研究历史。

### 公开示例能使用熟悉的市场标的吗？

应使用中性的 synthetic identifier。真实标的可能暴露私人 universe，也容易被误读成推荐。

### 数据缺失或样本过小时怎么办？

返回明确的 insufficient-evidence。不能在看到结果后降低 threshold 或扩大样本。

### 研究结果如何进入 live code？

必须另行提交经过 review 的实现、production test、文档更新并通过 release gate。Research code 始终保持 offline。
