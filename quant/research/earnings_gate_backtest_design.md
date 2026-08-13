# earnings_gate_backtest 预注册设计（v1 锁定）

> **状态：已锁定 （用户 批准："现在可以做之前提到的预注册了"）。**
> **锁定后不加桶、不移边界、不调判据；真数据只跑一次；Verdict 运行后追记本文件。**
> 立项 （源头 = INTC 夜盘 FOMO 复盘讨论）。流程同 gate round-2：
> 设计稿锁定 → 内容进脚本 docstring → 真数据只跑一次 → Verdict 追记本文件。

## 问题

现行规则：财报 binary 前 `max_classification: probe` 一刀切（卡片 constraint，如 NBIS 8/12 / LITE 8/11 / NVDA 8/26）。
用户 质疑：趋势成立时 print 反应由板块 regime 主导，clamp 过度迷恋财报事件本身。

**对赌假设（运行前锁定）**：
- **H-proposal**：趋势成立 + 低 implied move → pre-print 建仓无显著惩罚 → 低 IM 桶应解除 clamp
- **H-System**：高 implied move → pre-print 建仓左尾显著肥（gap 穿越 stop 机器）→ 高 IM 桶 clamp 保留

两者可同真 → 规则从一刀切改为 `clamp = f(implied_move)`。

## 数据（audit  实测）

- **财报事件**：`get_financials_earnings_price_history.py`（20 期/票，`pub_time_str` 精确判 AMC/BMO，
  每期 -15..+14 交易日复权 OHLC，vendor `predict_vola_ratio_newest` = 期权隐含 implied move，~2021/2022 起有值）
  + `get_financials_earnings_price_move.py --period-count 50`（44-46 期回溯 2012-2015，±5 日 OHLC）。
  两端点**不占**300 只/30 天 K 线 quota，仅受 ~10 req/30s 频率限制。
- **趋势 context**：`klines_deep/` 165 票多年日线（gate round-2 同池），算 regime / ATR / MA。
- 本地 `iv_history/` 最深 31 行，**不用**（IV percentile 不可算，非阻塞——implied move 由端点自带）。
- 已知缺陷：端点有缺期（MU 案例）→ 按 pub 日期去重对齐，不假设期数连续；
  survivorship：universe 只含存续票，财报暴雷退市者缺席 → **偏向低估 pre-print 风险**
  （偏差方向对 H-proposal 有利；若仍测出左尾肥，结论更稳。运行前书面声明，同 gate2 惯例）。

## Episode 定义

- **Universe**：klines_deep 165 票 ∩ earnings 端点有 implied move 数据（预期 ~150 票 × 2021-2026 ~16-20 期）。
- **事件日 E**：财报发布对应的 print 反应日（`pub_time_str` ≥16:00 ET = AMC → E = 次交易日；<09:30 = BMO → E = 当日；盘中罕见 → 当日，标记）。
- **资格过滤（趋势成立的机械定义，复用无新自由度）**：E-1 日 `regime_of(bars, E-1)` ∈ {parabolic, strong_up}
  （`entry_ladder_backtest.py:regime_of`，zone-maintenance momentum diagnosis 的代码复刻）。
  `other` 不入主样本——趋势不成立时现行规则本就不给 confirmed，无争议。
- **配对世界（policy / intent-to-treat，共同起点 E-1，1 元本金）**：
  - **World PRE**：E-1 收盘买入，承担 print → 共同终点 E+20 / E+60 收盘
  - **World POST**：E-1 至 E+1 持现金，E+1 收盘买入 → 共同终点
  - 差异 100% 来自「是否承担 print 方差 + 是否吃到 E-1→E+1 的 print 反应」
- **副轴（只报告不裁决）**：PRE 变体 = E-5 收盘买入（含 pre-print drift 的版本）。
- **止损**：对称 `entry − 2×ATR20@入场`，收盘 ×1 日触发，次日收盘出场，SLIP 0.2%（gate2 primary 血统）。
  **gap-through 口径**：print 跳空直接穿 stop → 次日收盘出场，不按 stop 价成交——这正是被测机制，gap 不救。
  敏感性：3N。重进：single-shot（止损后持现金到终点）。
- **同票 cooldown**：同一 ticker 相邻 episode ≥ 40 交易日（季度财报天然间隔 ~63 日，此条只防数据缺期造成的重叠）。
  右截断：E + 60 超出数据末端 → 该终点剔除不进 n。

## 分桶（预注册，运行后不移边界）

- **implied move 三桶**（`predict_vola_ratio_newest`）：`<5%` / `5-10%` / `>10%`（round-number 预注册边界）
- **regime 两桶**：strong_up / parabolic
- **主格 = 3 × 2 = 6 格**。每格：ΔT20 / ΔT60 expectancy（PRE−POST）、止损率差、p10 差，
  CI90 = 按月 block bootstrap（seed=20260731 固定，10,000 draws，月索引矩阵全指标共享；p10 差 seed+1, 1,000 draws）。
  Kish 有效 N 按月分桶（财报集中在季报月，聚类严重，Kish 预期显著小于原始 N）——**任一主格 Kish < 30 → 该格报「数据不足」不裁决**。
- Cohort 分层（2021-22 / 2023-24 / 2025-26）只报点估计 + n。

## D 项（待 用户 锁定的裁决菜单——结果→动作，机械执行落 JSON）

**D1 分桶裁决（按 IM 桶独立执行）**：
- **分支 A（解除）**：该桶 expectancy(PRE−POST) T60 CI90 下界 ≥ 0 **且**p10 差 CI90 下界 > −1pp
  → 该 IM 桶解除 pre-print clamp（价格纪律 no_chase/zone/ladder 照旧，clamp 只管分类天花板）
- **分支 B（保留，保险语义成立）**：expectancy CI 含 0 **且**p10 差 CI90 上界 < 0（左尾显著恶化）
  → 该桶保留 probe clamp，定性从「先验」升级为「实测保险」
- **分支 C（保留，均值也输）**：expectancy CI90 上界 < 0 → 保留 clamp；是否加强（如 veto）另行立项，本轮不自动扩展
- 以上都不满足（CI 含 0 且 p10 不显著）→ 维持现行（证据不足不改规则）

**D2 size 折算声明**：本回测测单位暴露差；现行规则的真实对照是「PRE probe + POST 升 confirmed」vs「PRE confirmed」，
size 只线性缩放收益差、不改变符号与显著性 → 裁决基于单位暴露，落地时按 cap 框架折算。

**D3 适用域**：结论只作用于**卡片财报 constraint 的分级**（`max_classification` 是否随 IM 桶自动化），
不触碰宏观事件 gate（FOMC/CPI —— 无 per-event implied move 数据，域外）。

**D4 天数轴已由 fiat 裁决（用户 定，先于本回测）**：clamp 窗口 = print 日 − 7 自然日起
（`constraints.from` 字段，`constraint_window_state()` 强制，live 已改：NVDA/NBIS/LITE/MRVL 四卡落 from）。
理由 = 事件影响在时间上是局部的，窗口外仓位结构规则单独管（用户：「本质还是仓位结构」）。
**本回测不再测天数轴**（副轴 E-5 保留仅为量化 pre-print drift 的量级，不裁决窗口长度）；
只裁决 IM 轴 = 窗口**内**的 clamp 是否按 implied move 分级。若未来要重开天数轴，另行立项。

## 不做什么

不加桶 / 不移桶边界 / 不挑 regime 定义 / 不看中间数字 / 不按 ticker 挑样本 /
副轴（E-5）与 cohort 不参与裁决 / 2012-2020 无 implied move 期不进主样本（可做副池只报告）。

## 输出

`quant/research/earnings_gate_backtest.py` --report → `tmp/backtest/earnings_gate_report.md`
（机器写出：render 表格 + 折叠 full JSON + D1 机械 flag 段）。裁决写回本文件 Verdict 段 +
`.claude/rules/`（如触发规则变更）+ memory。复测条件：+12 个月数据。

## 运行前修订（对抗性验收 ，真数据运行之前；非结果驱动，用户 保留否决权）

1. **（blocker）E 日判定数据源修正**：「pub_time_str 精确判 AMC/BMO」被真数据证伪——~6% 期是
   `00:00:00` 哨兵值（集中 2021-22），~20% 期时间戳为盘中怪值。改为 **vendor day-0
   （`schedule_delta==0` 行的 `trading_day_str`）为反应日首选真源**（在干净 AMC/BMO 期上与
   heuristic 100%/99.9% 一致），退而 `pub_type`（实测 1=BMO、2=AMC），再退干净时间戳；
   全不可判 → 剔除并计数。E 日语义不变（仍 = print 反应日），属数据实现修正非合同变更。
2. 设计稿预注册的 **3N 止损敏感性**补实现（只报告不裁决，与 PRE5 同地位）。
3. D1 桶的 n / Kish / bootstrap 输入全部收窄到 **T60 承载子样本**（右截断条款的正确执行；
   否则数据末端 ~2 个月 t20-only 的财报季高峰月会虚增有效 N）。
4. 每主格（6 格）补 ΔT60 / ΔT20 CI90（设计稿分桶节承诺，同 seed 零自由度增量）。
5. **（pin）D1 分支优先级**：A 与 B 同真（exp 下界 ≥0 且 p10 差 CI ⊂ (−1pp, 0)）时 **A 优先**——
   −1pp 是 materiality 阈值，统计显著但幅度 <1pp 的左尾恶化不构成保留 clamp 的理由。
   Kish<30 闸门定义在 **D1 桶级**（kish_t60 口径）；成员格 <30 → verdict 附 `thin_cells`
   标记（可见、供人工复核），不整桶 abort。
6. **BMO newest-IM caveat**：BMO 期的 `predict_vola_ratio_newest` 可能含 E-1 收盘之后
   （E 日盘前）的更新，vendor 无 E-1 时点快照、无法修数据 → 报告强制展示 session 构成，
   解读 IM 分桶时计入。

## Verdict（round-1 运行后追记，，机器裁决原文见 tmp/backtest/earnings_gate_report.md）

443 episodes（vendor_day0 覆盖 100%，剔除 4 期 unknown）/ session AMC 201 · BMO 164 · intraday 78。
D1 机械读数（kish_t60 闸门 30）：

| IM 桶 | n60 | Kish60 | ΔT60 (PRE−POST) CI90 | p10Δ CI90 | verdict |
|---|---|---|---|---|---|
| im<5 | 127 | 15.1 | [−0.66pp, +1.09pp] | [−1.61pp, +0.77pp] | **insufficient**|
| im5-10 | 229 | 27.3 | [+0.90pp, +3.83pp] | [−1.27pp, +1.14pp] | **insufficient**|
| im>10 | 56 | 14.3 | [+6.68pp, +27.9pp] | [−5.05pp, +5.01pp] | **insufficient**|

**结论：三桶全部数据不足，不裁决，现行规则维持**（clamp 窗口 ≤1 周的 fiat 不变，窗口内 probe
clamp 不变）。财报按季报月聚类，把 371 个日历月压成 Kish 14-27——预注册的 Kish≥30 闸门正是
为这种「原始 n 看着够、独立信息量不够」的形状设的，闸门按合同生效。

**方向性观察（只报告，按预注册不得作为裁决依据）**：PRE−POST 点估计全桶为正（趋势成立时
pre-print 建仓均值上*跑赢*等 print 后——方向支持 H-proposal），p10 差全桶 CI 含 0（左尾未见
显著恶化），3N 敏感性同向。但 im>10 桶 +11.6pp（strong_up）由 56 个 episode / Kish 14 承载，
且 BMO 期 newest-IM 时点 caveat 在场——这些数字的正确用途是设计 round-2，不是改规则。

复测条件：+12 个月数据（im5-10 桶 Kish 27.3 最接近门槛，预计率先过 30）。
