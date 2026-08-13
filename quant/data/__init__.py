"""data — 把外部世界读进本地: 行情/宏观/社交接入 + 每日账本落库。

    market_data   OpenD 行情封装 (get_kline / get_quote / get_snapshot)
    macro_fetch   免费宏观源 (VIX 等, 零 key)
    x_fetch       X (Twitter) 全文抓取, 供研报因子卡
    kline_cache   日线增量缓存 (盘中丢弃未完成 bar)
    iv_logger     每日 ATM IV 快照 —— IV percentile 的不可重拉数据源
    nav_snapshot  每日 NAV + 基准
    skhy_premium  SKHY ADR 溢价 gate

依赖: core。
"""
