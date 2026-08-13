"""core — 纯共享库: 无副作用、被所有层 import 的单一真源实现。

    paths      仓库路径 (唯一允许推导 repo root 的地方) + openapi skill shim
    env        .env → os.environ (import for side effect)
    io_json    原子 JSON 读写 + 跨进程 state lock
    timeutil   两条时间约定 (ET 交易日历 / UTC 时间戳)
    num / occ  broker 数值归一 / OCC 期权代码解析
    schema     state 文件与 trade event 的零依赖结构校验
    ledger     trade-event 账本: 校验写入 + FIFO episode 回放
    sizing     分类 cap / 卡片约束 / 整股档位 —— size 的单一真源
    structure  gate 与 zone 的时效性与自洽 (同生同死同锚)
    drift      结构漂移四维度量 (②POC 已摘除)
    technical  技术指标纯数学 (唯一带 CLI 的 core 模块, 仅作自检)

这一层**不许**向上依赖 (data / sync / decision ...)。
"""
