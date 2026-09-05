"""估值层的**默认范围** = 持仓 ∪ 候选 (`core/holdings.coverage_universe`), 不是 inputs.json 全表。

源案例: `valuate.py --all` 与 `calibrate.py reconcile` 曾按 inputs.json **全表**跑, 周复盘 §7 因此列出了
已移出自选分组的墓碑卡与纪念仓 (「不进任何 task 输出」)。inputs.json 是人工区: 删条目会丢 archetype / overrides / news_overlay
(那是资产, 票回来还要用), 所以范围不能由它定义 —— 它只回答「这票**怎么**估」; 「估**哪些**」归持仓 /
候选判据, 与 `ops/ainews_universe` 同一个真源。
范围内没归原型的票由 valuate 报错不跑 (= 人工区待补), 不是静默跳过。

🚨 零授权效力: 只读两个 state 文件取**名字**, 不写、不 import sizing/entry_guard/exit_guard;
输出没有价格 / 仓位 / cap。
"""
from __future__ import annotations

from quant.core.holdings import coverage_universe
from quant.sync import state_io


def coverage_tickers() -> list[str]:
    """持仓 (期权映射回正股, 纪念仓/参考票不进) ∪ 候选 (status=watching 且 moomoo_listed≠false), 排序。"""
    return coverage_universe(state_io.read_positions(allow_missing=True),
                             state_io.read_watchlist(allow_missing=True))
