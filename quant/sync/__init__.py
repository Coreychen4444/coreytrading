"""sync — broker 真值 ↔ 本地 state 文件的对账层。

    state_io             state/ 原子读写 (保留 agent block)
    stock_fetch          持仓 + 报价 → positions.json broker block
    order_sync           在途订单 → pending_orders
    trade_history_sync   成交 → events/<YYYY-MM>/trades.jsonl (dedup by order_id)
    watchlist_sync       moomoo 自选分组 ↔ watchlist.json
    watchlist_migrate    watchlist ↔ positions 迁移
    lot_reconcile        未入账的 BUY fill → lots.position + position_summary
    close_reconcile      qty 归零的卡片 → 清 lots + status=closed + exit_info

依赖: core, data。**这一层是唯一写 broker block 的地方。**
"""
