# Campaign 风险预算递减分配 (risk_allocation) — 设计定版

**立项 **（trend_slots 结案余波：SSS 死了，但 用户 详案的「总风险预算 +
递减路径」两层结构**不依赖打分**，独立成立。用户：「我赞同」B+C 合体 → 当日三点锁定）。
**状态（终）：已接线**（并行 session 的 PLAN 改造落地后当轮完成）：
`sizing.ROUND_RISK_FRACS` + `round_risk_frac` + `round_risk_notional_cap`（唯一换算点）
+ `campaign_round_no`（保守过计口径）；消费者 = entry_guard ②（恒 R1）+
today_group `grant_actual_cap`（加仓侧轮预算封顶）+ 期权 `ar_prem` 轮口径。
1086 测试全绿 + 变异 2/2 红；记载 = position-tiers.md 新节 / CLAUDE.md / §2b 修订注；
ledger §1a 已加行。副作用声明：⑤ at-risk 后备边界 13.3N → **4N**（confirmed R1）。

> ⚠️ **的后裁把本稿的三处改写了，读本稿前先读这一段**
> （canonical = ledger §1a 的 `classification_removal_design` 行 + **`gate 预算`**行）：
> 1. ~~probe flat 0.75%~~ 随分类轴退役（该臂在 cap 2% 下恒不 binding）。
> 2. ~~期权 R1-only（保费 10% NAV）~~ **整个反转**→ 期权 gate2 起、第一档严禁，
>    保费上限 gate2 6% / gate3 4% NAV（= **本档新增**risk ÷ 0.25，**不跟累计**）。
> 3. **「轮」整个退役**。本稿通篇的「每轮 risk 2.5/1.5/1.0%」现在只是**分批比例**；
>    live 用的是 `GATE_RISK_CAPS` = **累计上限 2.5 / 4.0 / 5.0%**，档位 =
>    `sizing.gate_count(card)`（卡上爬过几个**给了 cap 的**gate，破位/平仓归零），
>    可用 = 本档上限 − **当前敞口市值**。用户 逐字：「没有所谓的 r1 r2 r3 概念了 ——
>    就是 gate1 max 2.5% gate2 max 4% gate3 max 5%」+「不是任何一个 gate 都算次数，
>    只有给 cap 的 gate 才开始算」。换算点更名 `round_risk_notional_cap` →
>    `gate_risk_notional_cap`；`round_no_from_lots_since` / `campaign_round_no` /
>    `lots_deployed_since`（从成交记录猜档位的两代近似）**已删**。
>    §4 的价签（名义 −1.0R ≈ 实证 −1.40R，T 名义 5% ≈ 实证 7%）**不变**——
>    改的是「什么时候能动这笔钱、怎么数档」，不是 T 本身。

---

## §1 锁定值（用户，三问三答）

| # | 锁点 | 值 |
|---|---|---|
| 1 | campaign 总风险预算 T | **5% NAV 顶格**（=at-risk 天花板 `AT_RISK_CAP_FRAC`，两处同一个数不留 buffer；**名义口径折算必须随行播报：名义 5% ≈ 实证 7.0%**，见 §4 乘数） |
| 2 | 分配形状 | **三轮 50/30/20**→ 每轮 risk = **2.5% / 1.5% / 1.0% NAV**|
| 3 | probe campaign | **维持 flat 0.75% 单轮**（probe 路径零改动） |

## §2 机制（改的是「每轮多大」，不碰「何时有轮」）

- **适用域：confirmed campaign only**。campaign/episode 语义沿用现有（exit_guard
  `campaign_entry` / lots 归属）；轮计数 = campaign 内第几次 confirmed 部署。
- **每轮股数 = floor(round_risk_usd / (2×ATR20@当轮))**—— 与现行 n_unit 同一条公式，
  只是 risk 分数从 flat 0.75% 换成 schedule {2.5, 1.5, 1.0}%。
- **轮触发不变 = A 轴**（zone 资格 / gate 转移 / NBIS 促升制）。本设计不引入任何
  +kATR 机械间距（08-10 锁点 3 已否）。
- **外层全部照咬**：`available_cap`（分类/冲击预算/gross）、NOTIONAL_CAP 40%、
  at-risk 5%（=T，同一个数，语义闭合）、财报 T+0 ×0.7、每轮内 ladder 整股降级表。
- **#2B 递减律自动满足**：1.5/2.5 = 60% ≤75% ✓，1.0/1.5 = 67% ≤75% ✓。
  averaging-down 严限（#2A）不变——schedule 只管 pyramid-up 路径。

### §2b 期权条款（用户  观察收编：「期权永远在第一轮」= feature）

- **期权 vehicle 只有资格消费 R1 预算**：R1 保费上限 = 2.5%NAV / 0.25 =
  **10% NAV 保费**（$2,124 @ NAV $21,237；换算基于 S1 硬 stop −25% 的
  `OPTION_ATRISK_LOSS_FRAC=0.25`）。R2/R3 整张 floor 到 0 = 物理上进不来。
- **R2+ 加仓一律正股**（delta 名义计入）——与三条既有法条同构：LEAPS 等趋势确定
  （R1=证据最满时刻）/ LEAPS 不进 stock pyramid 链（§11）/ 浮盈期不叠期权杠杆
  （NOK -$2,295 学费；趋势后段 IV 已抬，加正股不加 vega 恰好是对的）。
- 0 张 → fallback 阶梯 spread→正股（现行）；**禁降 delta 装 cap**（现行规则照抄）。
- 附注：本 schedule 让期权可行域比 flat 时代**大 3.3×**（旧单轮 0.75% → 保费 $637）。

## §3 行为变化（价签，两个典型 + 期权）

NAV $21,237，R1/R2/R3 = $531 / $319 / $212：

| | COHR（8.7%/日，2N=$66.3） | MSFT（3.0%/日，2N=$30.1） |
|---|---|---|
| R1 | 8 股 $3,033（14.3%） | **16 股 $8,000（37.7%）— notional 40% 当轮咬住**|
| R2 | 4 股 $1,517 | 0 股（headroom $495 装不下）|
| R3 | 3 股 $1,137 | — |
| 到位 | **3 轮 26.8% NAV**，at-risk 名义 4.7% | **1 轮实质完成**，at-risk 仅 2.3% |
| 对照 flat 0.75% | 原需 ~7-11 轮 | 原需 ~3.4 轮 |

- **低波动票 = 大幅前置**（一轮到位，binding 从风险单位切到名义 cap）；
  **高波动票 = 3 轮走完**，at-risk 逼近但不破 T（整股 floor 留出天然 headroom）。
- **单轮热度 3.3×**：R1 一次止损烧名义 2.5%（实证 ≈3.5%，见 §4）。

## §4 定价（N1 定式「裁决不需要·定价值得跑」——本档给算术价签 + 实证乘数）

- **实证止损乘数（新读数，）**：复用 sss_backtest §5 基础设施
  （166 票池 / 2010+ / entry−2N ×1 日确认 / 0.5% 滑点），71,093 个止损样本：
  **mean −1.40R / median −1.32R / p90 −2.07R**。跳空穿越是真钱——与 ledger 在案的
  gate 锚定族 1.48N 同向、同量级，本族略低（入场锚定 stop 距离恒 2N 不随时点变）。
  复现：`from quant.research import sss_backtest` → `scan_ticker` 全池 →
  stopped 样本 R 分布（勿改 §5 锁定参数）。
- **T 的真实含义**：名义 5% 全部部署且全部被扫 → 期望实现损失 ≈ **7.0% NAV**
  （尾部按 p90 更差）。用户 知情选择顶格（与 § 名义口径锁定一致，两处不留双账）。
- **full campaign simulator（结构轮触发的历史仿真）不在 v1 范围**——A 轴触发依赖
  手画 gate，机械化仿真是独立课题。复看条件：+12 月，或 live confirmed campaigns ≥20
  个后拿真实 heat 分布对照本节算术价签。

## §5 接线计划（DEFERRED，两个前置）

1. **等并行 session 的 entry_guard PLAN 改造落地**（同文件冲突， 在途）。
2. 接线点：`entry_guard.PARAMS["nunit_risk_frac"]` → per-round schedule；轮态读
   campaign lots/episode；`sizing` 换算复用（`notional_to_premium_cap` 期权侧照旧）；
   z1 备注金额（price_reminder）与 Today 组 grant 随 `available_cap` 链自动跟上，
   但**轮预算数字必须进 entry_guard 渲染**（binding 必须播报——写 40% 的卡今天给
   2.5% 轮，不写出来没人能解释）。
3. 接线当轮：ledger §1a 加行 + tests（轮计数 / 期权 R1-only / #2B 相容性 / 整股）+
   变异守卫；`.claude/rules/position-tiers.md` 补一段（规则不进代码=不存在，但规则
   只进代码不进 rules = A4/A5 型记载缺失，两边都要）。
