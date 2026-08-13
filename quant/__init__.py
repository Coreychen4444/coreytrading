"""quant — trading operations package root.

分层 (依赖只能向下, 见 quant/README.md):

    ops        编排 / 通知 / 运维        pipeline · tg_notify
    report     报告层                    portfolio_brief · perf_report
    journal    账本写入层                annotate_event · decision_log
    decision   决策层 (闸门 / regime)    entry_guard · exit_guard · regime_* · level_map ...
    options    期权执行参数              option_plan · implied_move · strike_compare
    risk       仓位 / 集中度             kelly_size · chain_layers
    sync       broker 真值 ↔ state       stock_fetch · *_sync · watchlist_*
    data       外部数据接入 + 每日账本    market_data · kline_cache · iv_logger ...
    core       纯共享库 (单一真源)        paths · io_json · sizing · structure · technical ...

    research   验证层 (回测 / 校准, 旁路)

每个模块既可 import 也可直接 `python3 quant/<层>/<模块>.py` 跑 —— 后者靠模块顶部
那行 `sys.path.insert(0, ...parents[2])` bootstrap。
"""
