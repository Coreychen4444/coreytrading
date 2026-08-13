"""options — 期权 vehicle 的执行参数与定价工具。

    option_plan     IV 闸门 → delta 带 + 流动性硬门槛 → 限价/张数/标的止损
    implied_move    binary catalyst 的 implied move 双 anchor
    strike_compare  call strike 收益量化 (crossover + IV 调整)

依赖: core, data。
"""
