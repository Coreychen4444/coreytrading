"""decision — 决策层: 机械闸门, 不是叙事。

    entry_guard        入场闸门 + 每日执行计划 (PLAN 变动才落 decisions.jsonl)
    exit_guard         退出栈监控 (利润棘轮 / 趋势死亡线 / G-01 / margin)
    regime_transition  gate 触发即机械转移卡片 regime
    regime_score       不确定性执行三旋钮量化
    structure_drift    结构漂移观测 (advisory only, 当前不裁决)
    level_map          四信号价位图 (volume profile / pivot / 交互史 / OI)
    catalyst_check     异动归因机械校验

依赖: core, data, sync, options, risk。
"""
