"""跨模块「有意重复」常量的相等钉 (BACKLOG : V6 + §3.5 债务)。

有意重复本身可以 —— 分层/历史原因两处各持一份; **不被测试绑住的有意重复**不行:
复测或调参时改一处漏一处, 两边各自都不报错, 系统安静分叉 (ledger V6 的原话:
「不网格」恰恰意味着那是唯一不会因扫参数而被重新对齐的数)。

本文件是这些死账的锁。改任何一边之前先来这里看债主是谁 —— 测试红了不是让你
删断言, 是让你把另一处也改了。
"""


def test_rel_floor_live_equals_backtest():
    """REL_FLOOR=0.4 — relative_volume_gate_design v1 () 单点预注册值。

    live (`regime_transition`) 与回测 (`relative_volume_backtest`) 各持一份且
    无共享 import (research 旁路不许被依赖, sectors 冻结表的「同源」只覆盖表)。
    +12 月复测改 live 忘回测 (或反过来), 回测会安静验证一个 live 已不再执行的
    口径 (ledger V6)。
    """
    from quant.decision import regime_transition as rt
    from quant.research import relative_volume_backtest as rvb
    assert rt.REL_FLOOR == rvb.REL_FLOOR == 0.4


def test_g01_gains_mirror_exit_guard_levels():
    """G01_GAINS — exit_guard `PARAMS["g01_levels"]` 增益列的有意复制。

    sync 层 (2) 不能 import decision 层 (4), 所以 price_reminder_sync 只能抄
    (exit_line_reminder_design 自陈)。改 G-01 档位必须两处同改 (ledger §3.5-g01);
    漂了的后果是 moomoo 提醒线和 exit_guard 告警在**不同的增益档**响。
    """
    from quant.sync import price_reminder_sync as prs
    from quant.decision import exit_guard as xg
    assert tuple(prs.G01_GAINS) == tuple(g for g, _trim in xg.PARAMS["g01_levels"])


def test_shock_breaker_mult_live_equals_backtest():
    """SHOCK_BREAKER_MULT=1.5 — 熔断线 (shock_budget §2.7/§15c,  定版)。

    live (`sizing`) 与回测 (`shock_budget_backtest.BREAKER_MULT`) 各持一份。
    +12 月复测重验 H4 时若按预注册分支上调 (1.5→1.9), 两处必须同改 ——
    否则回测验证的熔断线不再是 live 在执行的那条。
    """
    from quant.core import sizing
    from quant.research import shock_budget_backtest as sb
    assert sizing.SHOCK_BREAKER_MULT == sb.BREAKER_MULT == 1.5


def test_breakout_stale_days_same_in_guard_and_brief():
    """zone-maintenance「连续 ≥2 交易日」stale 确认口径的两份手搓。

    entry_guard (入场侧 STALE 判定) 与 portfolio_brief (简报 zone_stale 告警)
    各自实现 breakout/breakdown-stale。口径漂了的后果: 一边已判 STALE 拒绝
    授权, 另一边还没响 —— 「两条线并存报了不 binding 那条」的既有失效形状
    (ledger §3.5-breakout)。
    """
    from quant.decision import entry_guard as eg
    from quant.report import portfolio_brief as pb
    assert eg.STALE_CONFIRM_DAYS == pb.STALE_CONFIRM_DAYS == 2
    # Breakdown is immediate; volatility is scaled by 2×ATR14 in both consumers.
    assert eg.STALE_BREAKDOWN_DAYS == pb.STALE_BREAKDOWN_DAYS == 1
    assert eg.STALE_VOL_ATR_MULT == pb.STALE_VOL_ATR_MULT == 2.0


def test_chandelier_trail_constants_match_exit_guard():
    """趋势死亡线的两个数 —— `level_map.chandelier` 与 `exit_guard` 的有意复制。

    用途分开是**有意的**：level_map 是**画线时刻的体检**（`[0.7N,2N]` 体检持仓期跑
    = 劝退止损机），exit_guard 是**持仓期的 live 强制**。但**数必须是同一个**——
    漂了的后果是画卡时看到一条死亡线、live 触发的是另一条，
    即「两条线并存，报了不 binding 那条」的既有失效形状（散文版简报曾引用比卡片 stop
    松 6.2% 的 trail 当风险线）。

     核实：公式与两个常数当时一致，但**没有任何东西拦着它们漂**——
    本 pin 就是那个拦着的东西（与 G01_GAINS↔g01_levels 同型）。
    """
    from quant.decision import exit_guard as xg
    from quant.decision import level_map as lm
    assert lm.TRAIL_ATR_MULT == xg.PARAMS["atr_multiple"] == 3.0
    assert lm.TRAIL_PARABOLIC_PCT == xg.PARAMS["parabolic_trail_pct"] == 0.92
