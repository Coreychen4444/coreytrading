# relative_volume_gate 预注册设计（v1 — **全文锁定 **）

> **状态：四个决策点全锁（用户："2 3 按你推荐，4 分别测试，
> 1 = 半导体以 SMH 为基准、其他以 QQQ"）。实现精度钉在下方"运行前修订"段，
> 全部在真数据运行之前记录，非结果驱动。**
> **锁定后不加格、不调阈值、不改判据；真数据只跑一次；Verdict 运行后追记本文件。**
> 立项 （源头 = INTC 8/4-8/5：gate 98 价格腿 2/2（收 100.94 / 101.06），
> 量腿 1.01× / 0.73× 不过 1.2 未转移——而 8/5 QQQ/SMH 同为缩量日。
> 用户 质疑：全场缩量日用绝对量比惩罚个股 = 尺子瞄歪；写死 1.2 非最佳）。

## 范围（先说不测什么）

**天数腿不在本测范围。**「放量 → 放宽天数」已被两个在案裁决覆盖，复测窗口未到：
- `escape_velocity`  裁决 **C**（上行逃逸 单日+量比≥2 快车道：ΔT60 CI 含 0，
  止损率 +9~11pp 显著恶化；**量比≥1.5 变体已作为敏感性跑过**n=2,623 ΔT60 +0.44pp 同机制）
- `gate_backtest2` （修复 gate need∈{1,2} 扫描；×1 日格 = whipsaw 机器）
- 复测条件 = **+12 个月数据 或 live 修复样本 ≥20**，二者均未满足。

本测只动一件事：**修复 gate 确认的量腿用哪把尺**。

## 对赌假设（运行前锁定）

- **H-rel**：conviction 应以当日 tape 为基准——全场缩量日"个股量不背离大盘"不该压授权；
  相对尺保住保险、消除 INTC 型误杀
- **H-abs**（现行）：修复收复需要**绝对**参与度，缩量收复无论 tape 如何都是 whipsaw 高发
- **H-none**（round-2 暗示的第三种可能）：量腿本身只在放行样本上加迟到成本，
  判别力全在簇维度（画线）——若成立，量腿应退休

## 数据

`market/klines_deep/` 165 票（gate round-2 同池）+ **QQQ/SPY/SMH 已在池内含 volume**
（QQQ →，5,037 bars）。零新数据依赖；右端截至 ，
运行前增量刷新到最新（pull 脚本），不刷新则声明右截断。Survivorship 声明同 gate2。

## 定义（锁定项）

- 量比（个股/基准同口径）= `technical.volume_ratio(vols, 20)`（分母含当日，与 live 转移机同拍）
- **RelVR(d) = 量比_个股(d) / max(量比_基准(d), 0.4)**
  分母地板 0.4：防节假日前极端缩量 tape 把 0.5× 的个股量放大成假信号。单点预注册，不网格。
- **基准（用户 锁定）：半导体标的 → SMH，其余 → QQQ**。
  半导体 = 芯片设计/制造/设备/存储/材料/光电半导体器件，映射表冻结（24 票）：
  `AAOI ADI AMAT AMD AMKR ASML AVGO AXTI INTC KLAC LITE LRCX MCHP MRVL MU NVDA
  ON QCOM QRVO SNDK SWKS TER TSM TXN`
  （光模块 AAOI/LITE、衬底 AXTI 归半导体——用户 用法里 SMH 即此 complex 的尺；
  WDC 分拆后纯 HDD 归 QQQ；NBIS/CRWV 数据中心运营商归 QQQ；NOK 通信设备归 QQQ）。
  敏感性（不裁决）：半导体子集用 QQQ 翻转重算 W1/W2，两版并列报告——分叉大小
  即"板块尺 vs 大盘尺"的实证价差
- 阈值沿用 live 的 **1.2**，绝对与相对同数——本测比的是尺，不是阈值

## 四个世界（policy / intent-to-treat，共同起点终点，1 元本金）

修复路径 episode（复用 gate2 抽取机械：破位 → 反弹收复扫描），触发日 i = 首次满足
该世界完整条件之日，入场 = i+1 收盘（镜像 live：postclose 转移、次日执行）：

| world | 价格腿 | 量腿（最新日） |
|---|---|---|
| **W0 现行**| close > gate ×2 | 绝对量比 ≥1.2 |
| **W1 相对**| 同 | RelVR ≥1.2 |
| **W2 取或**| 同 | 绝对 ≥1.2 **或**RelVR ≥1.2 |
| **W3 无量腿**| 同 | 无 |

止损对称 `entry − 2×ATR20@入场` 收盘 ×1 日次日出，SLIP 0.2%，single-shot，
cooldown 60 交易日，终点 i+20 / i+60，终点日止损悬置不计（house 口径）。
未进场 episode 持现金到终点（intent-to-treat——覆盖率差直接滚进 expectancy）。

## 主格与度量

**3 个对比格：W1−W0 / W2−W0 / W3−W0，不分 regime**（保 Kish；regime 拆分只报告）。
每格：ΔT20/ΔT60 expectancy、止损率差、p10 差、覆盖率差（进场 episode 占比）、
迟到差（价格腿首满足日 → 实际进场日均延迟），CI90 月 block bootstrap
（seed=20260806 固定，10,000 draws），Kish 按月，**该格 Kish<30 → 报数据不足不裁决**。

**earnings 分层（锁定 ，用户"分别测试"）**：满足日落 E±1（vendor day-0，
`pull_earnings_history` 已有）的 episode 打 tag，**E±1 层与非 E±1 层各出一套完整数字**
（expectancy/止损率/p10/覆盖率）。**主裁决用全样本**（分层再裁会把 Kish 切死）；
两层若在获胜分支上方向相反 → 写进 Verdict 叙述作为限定条款，不改写主裁决。

## D1 裁决菜单（草案——待 用户 锁定）

- **分支 R（换尺）**：W1（或 W2）− W0 的 T60 expectancy CI90 下界 ≥ 0 **且**
  止损率差 CI90 上界 < **+3pp****且**覆盖率显著增加
  → live 量腿改相对口径：`regime_transition._volume_ok` 接 QQQ 基准（数据层：
  kline_cache 需保证 QQQ 每日在池——已是 volume_reference 票）
- **分支 N（量腿退休）**：W3 对 W0/W1/W2 全部 expectancy 显著更好 **且**
  止损率差 CI90 上界 < +3pp → 量腿从修复 gate 移除。**大手术，触发也须 用户 单独批**
- **分支 K（维持）**：其余 → W0 保持，INTC 8/5 型延迟正式定性为保险费
- 未覆盖组合 → **no_call = 维持现行**（与 earnings/escape 设计兜底条款对齐）

## 与既有回测的边界

escape_velocity = 上行 breakout 逃逸的**天数**腿；gate2 = 修复 gate **有无**与天数；
本测 = 修复 gate **量腿的计量方式**。样本池与自由度互不覆盖，结论互不改写。

## 不做什么

不动天数腿 / 不调 1.2 / 不调 0.4 地板 / 不加格 / 不看中间数字 /
SMH 敏感性与 regime 拆分不参与裁决 / 不因结果回头改 escape_velocity 或 gate2 的裁决。

## 运行前修订（实现精度钉子，，全部在真数据运行之前；非结果驱动）

1. **共同锚 = p（价格腿首满足日）**：episode 内首个"收盘 > gate 连续 2 日"的完成日。
   四个世界共享 p（价格腿全同），唯一自由度 = 量腿等待。终点 = **p+20 / p+60**
   （草案"终点 i+20/i+60"的 i 即此 p，此处钉死消歧）。无 p（120 日窗内价格腿未满足）
   或无头顶簇（cluster gate 不存在）→ episode 不进样本（无决策点可测）
2. **gate = cluster 单一 kind，days=2 固定**（gate2 同式：破位日画一次固定，
   头顶最近 touches≥2 簇上沿）——live 修复 gate 是画线锚定的，本测只动量腿
3. **episode 池 = gate2 主池破位扫描**（60 日最低收盘 + close<MA60，`episodes_of`
   自带 120 日间距 ⇒ 严于草案 cooldown 60，从严执行）；**ETF 不出 episode**
   （QQQ/SPY/SMH/IWM/GDX/KRE/XB*/XL*/XRT 只作基准，个股池 = 149 票）
4. **入场 = 满足日次日收盘**（镜像 live：postclose 转移 → 次日执行）；q+1 超出
   模拟窗（p+60 与数据右端取小）= 未入场 = 现金（ITT）
5. **基准覆盖规则**：[p, 模拟窗末] 内任一日 RelVR 不可算（基准缺该日 / 任一侧量比
   窗不足）→ 该 episode 全世界对称剔除，剔除数进报告。声明：QQQ  起、
   **SMH  起**→ 半导体票 2012 前 episode 缺席；era 覆盖差异并入 Verdict 叙述
6. **earnings tag**= p 日或任一世界量腿满足日落 vendor E（`pub_trading_day_str`，
   day-0 惯例）±1 自然日；无 earnings 数据的票（165−147）tag=False 并计数报告
7. **覆盖率"显著增加"的机械化**= 覆盖率差 CI90 下界 > 0（R 分支第三条件）
8. **R 分支择一**：W1 与 W2 同过 → 取 W1（更简单的尺）；仅 W2 过 → W2
9. 右端数据  截断（增量刷新对可裁决 episode 贡献为零——p+60 终点
   要求使  后的 p 本就不承载 T60；声明不刷新）
10. 止损 = primary 单口径（对称 entry−2×ATR20@入场，收盘×1 日次日出，SLIP 0.2%，
    终点日触发悬置不计——house 口径），single-shot

## 输出

`quant/research/relative_volume_backtest.py --report` → `tmp/backtest/relvol_report.md`
（house 格式机器写出）。裁决追记本文件 Verdict 段 + 接线改动（如触发）+ memory。
复测条件：+12 个月。

## Verdict（round-1 运行后追记 ，机器裁决原文 tmp/backtest/relvol_report.md）

2,874 episodes / Kish 168-170 / drops: bench_gap 1,041（基准起点 QQQ  /
SMH ，早年 episode 按覆盖规则缺席）+ no_p 369 + no_terminal 18 + no_gate 3。

D1 机械读数（T60 全样本，seed 20260806）：

| cmp | Δmean CI90 | Δ止损率 CI90 | Δp10 | Δ覆盖率 CI90 | 判定 |
|---|---|---|---|---|---|
| **W1−W0**| **+0.48pp [+0.16,+0.79]**| +0.95pp [−0.77,**+2.67**] | −0.94pp | +3.7pp [+2.8,+4.6] | **R 三条件全过**|
| W2−W0 | +0.42pp [+0.17,+0.68] | +2.53pp [+1.21,**+3.89**] | −0.96pp | +4.8pp | 止损守卫超 → 否 |
| W3−W0 | +0.58pp [+0.20,+0.97] | +6.37pp [+4.33,+8.46] | −1.81pp | +9.3pp | 大超 → 否 |

W3−W1 +0.10pp CI [−0.21,+0.41] 含 0 → **N（量腿退休）不触发**——量腿的保险价值
获实证（拆掉 = 止损率 +6.4pp），该换的是尺不是腿。

**裁决：R（W1）——修复 gate 量腿换相对尺 RelVR ≥1.2，阈值/天数不动。**

Verdict 叙述（预注册条款外的诚实披露）：
1. **T+20 止损率差 +3.38pp [+1.86,+4.80] 超 3pp**——早窗 whipsaw 税真实存在
   （更早入场的对价），T60 上收敛到 +0.95pp；裁决锚 T60（实现钉子 pre-run 锁定），
   T20 数字如实挂出
2. **Δp10 −0.94pp 显著**——左尾变差是覆盖率 +3.7pp 的对价（多入场=多暴露）；
   预注册守卫选的是止损率 +3pp 而非 p10，该选择在运行前锁定
3. E±1 层 T60 CI 含 0（n=145, Kish 30.5）但与非 E 层同号——分层反向条款不触发
4. 敏感性：半导体子集 SMH 尺 +1.47pp vs QQQ 尺 +1.30pp，CI 重叠——群体层面基准
   选择差异小；但个案会反转（INTC 8/5：SMH 尺 1.54 过 / QQQ 尺 0.94 不过），
   SMH 映射保留
5. 立项案例复核：INTC 8/4-8/5 RelVR(SMH) = 1.49 / 1.54 → 新尺下转移成立
   （规则先锁后算，非为案例定制）；同日 --check 发现 MU（gate 848）同形，一并在案

接线（同日完成）：`quant/core/sectors.py` 冻结映射（live 与回测同表同源）+
`regime_transition.volume_leg`（canonical，detect_transition 授权与 gate_ladder_report
播报同一函数，永不分叉）；基准缺当日 → 绝对量比兜底且 note 说出口（降级不静默）。
复测条件：+12 个月。

## 决策点锁定记录（，归档）

1. 基准 = QQQ 全池统一，SMH 半导体子集敏感性（不裁决）——**已提案待 用户 确认**。
   影响解释已给：分叉发生在板块轮动日（大盘尺 vs 板块尺给不同答案）；板块配对 = 映射表
   自由度 + 新数据 + live 接线成本 + 小 ETF 量失真更大；SMH 敏感性已覆盖本书实际持仓域。
   SMH-QQQ 若在半导体子集显著分叉 → v2 板块映射立项种子
2. 止损率守卫 = **+3pp 锁定**（用户"按你推荐"）
3. **W3 量腿退休留在裁决菜单锁定**，触发也须 用户 单独批（用户"按你推荐"）
4. earnings **E±1 / 非 E±1 分层各出完整数字锁定**，主裁决全样本（用户"分别测试"，
   语义落法见上文 earnings 分层段——分层不改写主裁决，防 Kish 切死）
