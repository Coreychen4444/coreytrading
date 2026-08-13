# `tests/research/` — 目前是空的（有意的）

`quant/research/` 是验证层旁路：一次性回测与校准，输出是统计而不是动作，
所以整层暂时没有测试。目录留着是为了守住 `quant/<层>/ ↔ tests/<层>/` 的镜像——
要补测试时有唯一确定的落点，不会散到别处。

**但其中两个不是"旁路"，是真缺口**（`tests/test_module_coverage.py` 的 UNTESTED 里也这么标了）：

- `exit_rules_backtest.py` — CLAUDE.md 规定「改退出参数前必跑」。它算出来的数字直接
  决定要不要动利润棘轮 / 趋势死亡线，本身却没有任何测试。
- `structure_downgrade_backtest.py` — CLAUDE.md 规定「改 entry_guard 降级条件前必跑」。
  同上：判据的来源没被守住。

这两个的共同点是**它们的输出会改变真金白银的判定参数**。要补测试，从它们开始。
