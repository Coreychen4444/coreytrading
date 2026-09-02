"""估值层 ( 立) —— 判断层, 零授权效力。

股价第一性原理: P = Σ FCF_t/(1+r)^t (FCFF-DCF) 与 P = EPS × PE (Davis) 两个引擎
按商业模式原型加权; 反向解出现价隐含的增长; 三情景给分布; 每次输出落账本待校准。
不 import sizing / entry_guard / exit_guard, 不写 positions/watchlist。
"""
