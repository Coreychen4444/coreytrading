"""data — 把外部世界读进本地: 行情/宏观接入 + 每日账本落库。

    market_data   OpenD 行情封装 (get_kline / get_quote / get_snapshot)
    macro_series  跨资产宏观日线 (Yahoo / FRED, 尾部覆盖缓存) —— 宏观透镜与估值 rf 的唯一取数口
    kline_cache   日线增量缓存 (盘中丢弃未完成 bar)
    iv_logger     每日 ATM IV 快照 —— IV percentile 的不可重拉数据源
    nav_snapshot  每日 NAV + 基准
    earnings_calendar  前视财报日历 (离线消费: 缩仓比例 + 重画 SLA)
    skhy_premium  跨市场 ADR 溢价 gate

依赖: core。
"""
