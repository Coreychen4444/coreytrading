"""risk — 仓位大小与组合集中度。

    kelly_size    分数凯利单档上限
    chain_layers  AI 链同层冗余探测 (同层 stacking, 不是总暴露)

size 的**规则真源**在 core/sizing.py; 这一层是使用它的计算器。
"""
