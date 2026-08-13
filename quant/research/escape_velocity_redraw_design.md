# escape_velocity_redraw 预注册设计（v1 锁定）

> **状态：已锁定 （用户 批准："现在可以做之前提到的预注册了"）。**
> **锁定后不加格、不调阈值、不改判据；真数据只跑一次；Verdict 运行后追记本文件。**
> 立项 （源头 = NBIS 7/30 单日 +27% gap-and-go，repair 卡识别延迟 2-3 session ≈ 15-20%）。
> 流程同 gate round-2。

## 问题

现行 breakout-stale：`close > trim[1]` **连续 2 日**→ 卡片重画资格。垂直行情中从逃逸到"半仓在场"
需  个 session（第 2 收盘确认 + 重画 + D+1 ladder 首档）。用户 质疑：
系统对 gap-and-go 异常场景的识别延迟是真实成本，应有快车道。

**对赌假设（运行前锁定）**：
- **H-fast**：单日逃逸信号（价格 + 放量双确认）足够可靠 → 当日重画资格，提前 2 个 session 入场的收益 > whipsaw 税
- **H-slow**：单日信号假突破率高 → 2 日确认的 whipsaw 保护 > 延迟成本（现行设计）

## 数据

`klines_deep/` 165 票多年日线（gate round-2 同池，纯价量回测，零新数据依赖）。
Survivorship 声明：存续票池 → 突破后崩掉退市的票缺席 → **偏向高估突破跟随的价值**
（偏差方向对 H-fast 有利——若 FAST 仍不显著，结论更稳；方向与 earnings_gate 相反，各自声明）。

## Episode 定义

- **结构上沿代理**（live trim 是卡片值，历史不可得；预注册机械代理）：
  `level = 前 60 日最高收盘`（不含当日）。语义 = 「压了 60 日的箱体顶」，近似 trim 上沿/供给上沿。
- **触发日 i**：`close[i] > level` 的首日（cooldown 内不重复触发）。
- **逃逸条件（FAST 资格，运行前锁定）**：触发日 `量比 ≥ 2`（20 日均量口径，与 `technical.volume_ratio` 同拍，分母含当日）。
  敏感性变体（只报告）：`close > level + 1×ATR20`（价格逃逸版）；`量比 ≥ 1.5`（宽松版）。
- **配对世界（policy / intent-to-treat，共同起点 i，1 元本金，共同终点 i+20 / i+60）**：
  - **World FAST**：逃逸条件成立 → i+1 收盘买入（当日重画、次日执行，含 1 日现实执行延迟）
  - **World SLOW（现行）**：需 `close > level` 连续 2 日（i+1 确认）→ i+2 收盘买入；
    i+1 跌回 level 下 → 不触发，持现金到终点（intent-to-treat，正是 2 日确认要防的 whipsaw 样本）
  - 两世界差异 100% 来自确认口径（1 日 vs 2 日）
- **主样本 = 逃逸条件成立的触发日**（FAST 有资格的格子）；逃逸条件不成立的触发日为对照池（只报告：
  验证量比确认本身有无判别力——若「无量突破」桶 FAST 明显更差，则快车道必须绑量比，不能只看价格）。
- **止损**：对称 `entry − 2×ATR20@入场`，收盘 ×1 日，次日收盘出场，SLIP 0.2%，single-shot。
- **cooldown**：同票 60 交易日。右截断守卫同 gate2。
- **Whipsaw 直接度量**：FAST 买入后 `i+1 收盘跌回 level 之下` 的占比及该子样本 forward 分布
  （= FAST 买了而 SLOW 空仓的差异格，单独一行呈现）。

## 分桶（预注册）

- **regime**：`regime_of(bars, i)` ∈ {parabolic, strong_up, other}（复用，无新自由度）
- **主格 = 3 regime × 1（逃逸条件成立）= 3 格**。每格：ΔT20 / ΔT60 expectancy（FAST−SLOW）、
  止损率差、p10 差、whipsaw 率，CI90 按月 block bootstrap（seed=20260801 固定，10,000 draws；p10 seed+1）。
  Kish 按月；**任一主格 Kish < 30 → 报「数据不足」不裁决**。

## D1（待 用户 锁定的裁决菜单——机械执行落 JSON）

按 regime 格独立执行：
- **分支 A（快车道转正）**：该格 expectancy(FAST−SLOW) T60 CI90 下界 > 0 **且**止损率差 CI90 上界 < +5pp
  → 该 regime 下「单日收盘 > trim 上沿 + 量比 ≥2」当日即重画资格（zone-maintenance 修订），
  并作为 structure_drift 首个裁决阈值接线（advisory → 可裁决）
- **分支 B（可选提前）**：expectancy CI 含 0 **且**止损率差 CI 上界 < +5pp
  → 快车道为「允许但不强制」：briefing 播报「逃逸信号，可提前重画」，重画仍人工触发（现 advisory 加一行，不改判）
- **分支 C（维持现行）**：expectancy CI90 上界 < 0 **或**止损率差显著恶化
  → 2 日确认保留，结案，NBIS 类延迟定性为「已定价的保险费」

**适用域**：只作用于**上行 breakout-stale 的重画时点**。不触碰下行（breakdown stale）、
不触碰 repair gate 确认口径（gate round-2/2b 已裁决，bfloor_buf 语义动它必跑双回测）、
不触碰入场 ladder 结构（首档 0.98-1.00 × current 不变——本回测的 FAST 入场即其代理）。

## 与既有回测的边界

`gate_backtest2` 的 `fire_scan need∈{1,2}` 测的是**修复路径 gate**（破位后收复）的确认日数；
本回测测的是**上行逃逸**（箱体顶突破）的确认日数——样本池（60 日新高 vs 60 日破位后）方向相反，
结论互不覆盖。60d-high 代理 ≠ 每张卡的真实 trim：测的是确认口径的一般性质，不是复刻个卡。

## 不做什么

不调 level 窗口（60 日）/ 不调量比阈值（敏感性变体只报告）/ 不加格 / 不看中间数字 /
对照池（无量突破）不参与裁决 / whipsaw 子样本不单独裁决（并入止损率与 p10）。

## 输出

`quant/research/escape_velocity_backtest.py` --report → `tmp/backtest/escape_velocity_report.md`
（机器写出，同 house 格式）。裁决写回本文件 Verdict 段 + `zone-maintenance.md`（如触发）+ memory。
复测条件：+12 个月数据。

## 运行前修订（对抗性验收 ，真数据运行之前；非结果驱动，用户 保留否决权）

1. 「任一主格 Kish < 30 → 报数据不足」补两字 →「**该格**报数据不足」（按格独立，不全局 abort，对齐 earnings 设计措辞）；闸门 Kish 用 **t60 承载子样本**（kish_t60），全口径 Kish 并列报告。
2. 终点日止损触发 = 悬置：按终点收盘 mark、**不计止损率**（对齐 gate_backtest2 口径——原实现计入，与 round-2 数字不可横比，二者择一时选 house 惯例）。
3. bootstrap 月宇宙只由该终点承载行构建（对齐 gate2 `_by_month` 惯例）。
4. 数据无效（量比/ATR 不可算）的触发日不消耗 cooldown。
5. `ATR==0.0` 不再被 falsy 判为无止损（stop=entry）。
6. 每格补 ΔT20 CI90（同 seed/draws，零自由度）+ whipsaw 子样本 p10/p50/p90（render 表只展示 T60 主口径，其余在报告 JSON）。
7. D1 未覆盖组合（如 exp 下界>0 但止损差 CI 跨 +5pp）→ `no_call` = **维持现行**（证据不足），与 earnings 设计的兜底条款对齐。

## Verdict（round-1 运行后追记，，机器裁决原文见 tmp/backtest/escape_velocity_report.md）

9,273 episodes（FAST 资格 1,313）/ 371 月 / Kish≈266。D1 机械读数：

| regime | n | Kish60 | ΔT60 (FAST−SLOW) CI90 | 止损率Δ CI90 | verdict |
|---|---|---|---|---|---|
| strong_up | 311 | 151.4 | [−0.82pp, +2.56pp] 含 0 | **[+5.5pp, +13.5pp] 显著恶化**| **C_keep_2day**|
| other | 993 | 210.8 | [−0.04pp, +0.83pp] 含 0 | **[+9.1pp, +13.3pp] 显著恶化**| **C_keep_2day**|
| parabolic | 9 | 9.0 | — | — | insufficient（n=9） |

**结论：H-slow 胜。快车道提前 1 个交易日的期望收益 ≈ 0（CI 含 0），代价是止损率 +9~11pp
显著恶化（CI90 下界超 +5pp 预注册阈值）——2 日确认防掉的 whipsaw 是真实保护，不是多余延迟。
现行 breakout-stale 2 日确认口径维持不变；NBIS  类 gap-and-go 的识别延迟正式定性为
已定价的保险费。**

保留问题：parabolic 格（恰是 NBIS 场景所在 regime）n=9 数据不足未裁决——60 日新高逃逸在
parabolic 定义下本就稀少（parabolic 判定要求距 ATH<10%，逃逸日往往已是新 ATH 使 regime 翻成
parabolic 之前触发）。复测条件：+12 个月数据；parabolic 格单独凑不够就并入下轮 gate 类回测设计。
whipsaw 子样本（FAST 买了 SLOW 空仓，n=218）FAST T60 均值 +2.98%——单看是正的，但被止损率
恶化抵消，与主裁决一致读。
