"""research — 验证层: 回测与校准。旁路, 不参与实时决策。

    calibration                 EV / Kelly 后验 vs 实际
    zone_timeline               从 git 历史挖 zone 演化
    zone_hit_analysis           BUY fill 落点 vs zone + 前瞻收益
    pull_gate_history           回测用多年深度日线
    entry_ladder_backtest       3 档 ladder vs 替代方案
    entry_caps_backtest         分类 cap 敏感性
    exit_rules_backtest         退出栈反事实 —— 改退出参数前必跑
    gate_backtest               破位后什么信号说明可以回来
    structure_downgrade_backtest 降级判别式该多宽 —— 改判别式前必跑
"""
