# 技术交易框架落地方案 —— Technical Engine Landing Plan v1.0

**状态**：📚 syllabus 全文学习完成 + 落地方案起草。**未立项、未预注册、未接线。**
**文档性质**：**非裁决稿**（同 `shock_budget_wiring_todo.md` / `volume_profile_accuracy.md`）——
它不产生 `RULINGS_LEDGER` 行，只产生**待裁决点**（§12）与**零自由度动作清单**（§7 B0）。
**输入**：`quant/research/technical_trading_framework_learning_plan.md`（用户  给的正式 syllabus，19 阶段 / 10 原则 / 6 大阶段）。
**关系**：本文取代 `price_action_framework_design.md` §1 的**自拟学习计划**（那份文档自己写了「将被 用户的正式计划取代或合并」）；
其 §2–§13 的**证据与结论全部保留并被本文引用**——它们是证据，不是计划。

---

## 0.0 🎯 核心目的（用户  逐字定版 —— 一切落地围绕它，不围绕"更全面"）

> 「每天收盘后 你根据 k 线 交易量 指标等，分析出当前所处结构及形态，
>   例如这可能是底部 这可能见顶 这会继续延续 这后续会震荡
>   这会形成双顶 楔形 头肩形态，然后进而分析出潜在的交易机会」
>
> 链条：`Context → Structure → Price Action → Volume → Indicators → Trade`

**这段话是本文件、`quant/core/patterns.py`、`quant/report/chart_read.py` 存在的唯一理由。**
任何一次改动先问：**它让「每天收盘后看懂这只票现在处在什么结构里」这件事做得更好了吗？**
—— 不是「它是否更严谨 / 更全面 / 更像个系统」。

**钉在三处**（用户：「为了在落地过程中不跑偏 你需要把核心目的钉起来」）：

| 位置 | 形式 | 谁会让它失败 |
|---|---|---|
| `quant/core/patterns.py` 顶部 | 逐字 docstring | `tests/core/test_patterns.py` |
| `quant/report/chart_read.py` 顶部 | 逐字 docstring | `tests/report/test_chart_read.py::test_core_purpose_verbatim_in_both_modules` |
| 本文件 §0.0 | 逐字引文 | 人读 |

**判读层 ≠ 授权层**（同一份测试钉住）：判读产出「假设 + 确认 + 证伪 + 先验强度」，
决策人是 用户；授权产出 PLAN/VETO/cap/stop，决策人是代码。
判读层**不写任何 state、不 import sizing/entry_guard/exit_guard、不产出股数或金额**。
这与 v3「授权纯结构机器管，判断权归 用户，agent 供弹药」是同一条分工。

### 已落地（当日）

| 件 | 位置 | 说明 |
|---|---|---|
| 形态判别式真源 | `quant/core/patterns.py` | Lo(2000) 工序：核回归平滑 → 极值 → 10 形态不等式；每个带**已发表 KS p 值**；楔形标 `民间·无正式检验` |
| 每日判读装配与渲染 | `quant/report/chart_read.py` | 六步 + 形态 + **后续倾向三栏**+ 潜在机会；全书 1.3 秒 |
| 周线（B1） | `technical.resample_weekly` | 跨尺度是唯一有顶刊证据的"结合"（Han-Zhou-Zhu 2016） |
| 波动收缩（B6） | `technical.range_contraction` | VCP/squeeze/三角 的共同可测量核心 —— **形态名降解成读数**|
| RelVR 计算真源提取 | `regime_transition.rel_volume` | 判读层与授权层共读一份，防「同概念双判别式」 |
| 定时任务稿 | `tasks/chart-read.md` | **未启用**，等 用户 点头 |
| 测试 | `tests/core/test_patterns.py` (24) + `tests/report/test_chart_read.py` (27) | 全仓 1264 passed |

**与本文其余章节的关系**：§1–§13 是**研究/授权轨**（回测、预注册、DoF 预算），
§0.0 这条是**判读轨**。两轨共用四轴与既有真源，但**证据标准不同**（见上表），
不要把其中一轨的规矩套到另一轨上 —— 那正是  差点发生的一次跑偏：
「landing plan 说 ❌ 不做双顶/楔形/头肩」管的是**授权判别式**，不是**每日判读**。

---

## 0. 一页版（新 session 与 用户 从这里读起）

### 0.1 三句话结论

1. **syllabus 的终点（第十/十一阶段：Signal Score → Confidence → Position Size）在本仓已经被自己的数据否掉了。**
   `sss_backtest`（裁 FAIL，123,576 样本 / 163 票）不仅没证明评分有用，还测到**高分带轻微反向**
   （meanR 0.387→0.300 递减、止损率 55.2%→61.1% 递增）。所以本方案的第一条硬约束是：
   **不重建评分→仓位管道**。这不是拒绝 syllabus，这是执行它自己的原则 10（Every Signal Must Be Tested）。
2. **把 syllabus 的九节主链对到本仓，七节是强的，两节是弱的**（§3）——
   弱的恰好是 `Market Context` 与 `Signal Score`，而后者是有意不做的。
   **所以真正的缺口只有一个：Context / Regime 的上游那一半**（大盘宽度、板块、跨周期、横截面）。
   落地重心应压在 syllabus 的**阶段 1 / 3 / 4 / 14**，**不是**它篇幅最大的阶段 7（指标）。
3. **syllabus 有约 400 个概念，照单实现 = 重演 Sullivan-Timmermann-White 的 7,846 条规则**
   （Bajgrowicz-Scaillet 2012：FDR 校正后**无一条存活**）。所以落地的第一动作是**减法**，
   并需要一个新的治理机制：**自由度预算（DoF budget）**（§2）——它与冲击预算同构，
   管的不是钱，是**统计有效性**。

### 0.2 本方案的骨架

```
减法 (§1 已否的终点 / §2 DoF 预算)
  → 映射 (§3 syllabus 主链 ↔ 本仓真源: 哪七节已强、哪两节缺)
  → 组织原则 (§4 五轴取代 syllabus 的 A-G 指标分类)
  → 逐段落地 (§5 十九阶段, 每段: 要什么/现状/已裁不重开/缺口/动作/验证)
  → 可得性 (§6 数据审计: 已装未用的端点 / 拿不到的 / 代理的偏差)
  → 队列 (§7 B0 零自由度 → B1 周线 → B2 事件层 → B3 Context → B4 横截面 → B5 量族体检)
  → 解卡 (§8 v0.2 §12.1 五个"拍的"参数的建议锁定值)
  → 验证规格 (§9 预注册模板 12 问 = 本仓惯例 ∪ syllabus 第十七阶段)
  → 输出面 (§10) / 失败模式 (§11) / 裁决点 (§12) / 诚实边界 (§13)
```

### 0.3 待 用户 裁决（详见 §12，此处只列题目）

| # | 题目 | 不定的后果 |
|---|---|---|
| **Q1**| **自由度预算机制**批不批（每季度上限 / 超支须退休旧自由度） | syllabus 的 400 概念没有闸门，会一条一条长进来 |
| **Q2**| v0.2 §12.1 的 **5 个「拍的」参数**走 A（一次锁死）还是 B（先做纯信号量敏感性） | 交互事件回测开不了跑（卡了一轮了） |
| **Q3**| `debounce_days` **10 还是 20**| 它同时是样本量与报表密度旋钮，事后再动 = 事后改 N |
| **Q4**| **指标四条规矩**（声明轴 / 同轴同源一票 / 压成共识不计票 / 不做独立触发器）采纳否 | 决定 syllabus 阶段 7 的 80+ 指标怎么被挡在门外 |
| **Q5**| **B0 六项零自由度动作**现在做否（纯记账/注释/定义常数，零行为改变） | 都是「不做也不会错，但下一个人会重踩」的债 |
| **Q6**| **周线**（阶段 1）是否作为**分层变量**挂进事件回测，而不是单独立项 | 这是唯一有顶刊证据的"结合"形式，也是最省自由度的做法 |
| **Q7**| 阶段 14「Market Regime → Strategy Selection」**明确不做**，确认 | 不写死它，将来会有人按 syllabus 去建策略切换器 = 乘法自由度 |
| **Q8**| syllabus 第十五阶段与 **v3 技术面优先**的分叉，确认以 **v3**为准 | syllabus 说「基本面定买什么、技术面定何时买」，v3 说「授权纯结构」——两者不同 |

### 0.4 明确不做（写下来防止下一轮悄悄长回来）

- ❌ **Technical Signal Score → Position Size**（§1，已 FAIL，重开须新预注册 + 新证据）
- ❌ **Market Regime → Strategy Selection**（§5 阶段 14，乘法自由度）
- ❌ 多极值形态族（HS / 三角 / 旗形 / cup&handle / VCP / IPO base…）作为**形态名**（§5 阶段 4）
- ❌ 引入 SMC/ICT 词汇（BOS / CHoCH / MSS / order block / FVG / liquidity sweep）——
  四类事件已覆盖其全部机械内容，新词 = 新自由度 + 违反语义分层
- ❌ 趋势线 / 通道（画法自由度无限：两点定线，选哪两点没有判据）
- ❌ 新 regime 词汇（`position-tiers.md` §语义分层）
- ❌ 均值回归 setup 家族（我们是纯趋势/结构系统，`mindset-structure-pairing` 的结构授权原则）
- ❌ 任何「在看到前向收益之后调阈值」的动作（`price_action_framework_design` §4.5 + §8.0）

---

## 1. 第一个结论：syllabus 的终点在本仓已被数据否掉

### 1.1 syllabus 要建的东西

第十阶段要求把信号拆成维度并合成：

```
Signal = Context + Structure + Price Action + Volume + Momentum + Volatility
       → Technical Signal Score → Signal Quality
第十一阶段: Signal Strength → Position Size ; Confidence Score → Target Exposure
```

### 1.2 本仓已经跑过这件事，判据先于数字，结论是 FAIL

`quant/research/sss_backtest.py` / `trend_slots_design.md` §5，用户 五点锁定 + 判据先入 git（`448f5d9`），
 当日跑完当日裁决（`RULINGS_LEDGER` §1b）：

| 项 | 读数 |
|---|---|
| 样本 | **123,576**/ 163 票 / 2010+ / 步长 5 |
| 构造 | 锁定权重 0.30/0.25/0.20/0.15/0.10 五槽机械代理 → `f(S)` 连续乘子挂 `n_unit`（预注册**单点不网格**） |
| c1 单调性 | **败**。参与三带 meanR **0.317 / 0.335 / 0.300 非单调**；Δ(top−mid) −0.016 CI90 [−0.097, +0.073] 含 0 |
| c2 止损率守卫 | 过（61.1% ≤ 59.5%+5pp） |
| c3 | **败**|
| 高分带的形状 | meanR **0.387→0.300 递减**、止损率 **55.2%→61.1% 递增**、T60 **5.3%→3.7%**|
| 二值之上的增量 | confirmed 子集内 Δ(top−mid) **+0.010**CI90 [−0.078, +0.103] **含 0**= 连续分在 probe/confirmed 二值切分之上**零增量**|

**两条机制解释都不是事后编的**（预立在案）：
- **H-crowding**：高分 = 买 salience 峰值，与 gate 家系「贴强入场 ⇒ 止损率恶化」同构
  （`gate_path_taxonomy`：入场贴 `gate−0.5N` ⇒ 风险单位小 ⇒ 更易被打掉，四类止损率 +5.9~+11.9pp 全越守卫）；
- **H-shadow**：连续分只是二值切分的影子。

### 1.3 落地推论（三条，全是硬的）

1. **不重建 score→size 管道。**syllabus 第十/十一阶段的这条主链在本仓是**已证伪的形状**。
   要重开，须走新预注册（不同权重配置的负结果**只对那一个配置成立**——但重开的举证责任在提案方，
   且必须先说清「这次和上次哪里不同」）。
2. **但 syllabus 第十一阶段有一条对的、且我们已经实现得更细的区分**：
   `Confidence → Target Exposure`（目标敞口）与 `Pyramiding → Path to Target`（到达路径）**是两件事**。
   本仓对应物：目标 = `NOTIONAL_CAP 40%`（跨轮累计语义），路径 = `ROUND_RISK_FRACS` 三轮 **2.5/1.5/1.0% NAV**
   （T=5% × 50/30/20）。**这一段我们比 syllabus 走得更远**，不需要从它这里学。
3. **引擎的输出只有三个合法出口**（其余都是违规）：

| 出口 | 内容 | 约束 |
|---|---|---|
| ① **[FYI] 观察层**| 单独 postclose 任务，抬头固定「观察层，零授权效力」 | 不进 gate ladder report、不进 brief 授权段、不进 Today 组、不画提醒线（用户  已定 A1/A2） |
| ② **二值档位的输入候选**| 只能通过 `core/structure.entry_classification` 这一个判别式 | 要改判别式 = 改授权，必须过 `structure_downgrade_backtest` 的判据（目标桶「对 up 干净可区分」**且**「对 down 不可区分」） |
| ③ **回测的事件记录**| `interaction_census` 的 jsonl → 预注册回测的样本 | 事件定义锁定后不许因结果回改 |

---

## 2. 第二个结论：需要一个自由度预算（提案，Q1）

### 2.1 为什么

syllabus 列了约 400 个概念，其中阶段 7 一节就有 80+ 个指标。**照单实现的终局是已知的**：

- Sullivan, Timmermann & White (*JF* 1999)：把 BLL 的 26 条规则扩到 **7,846 条**全域，
  用 White Reality Check 校正数据窥探，随后 **10 年样本外盈利性很低**；
- Bajgrowicz & Scaillet (*JFE* 2012)：同一 7,846 条、DJIA 1897-2011、FDR 校正后 **没有任何一条仍然显著**。

本仓已经内建了三道防线（判据先于数字 commit / Kish N / cluster bootstrap），
但它们是**逐条**防线——**没有任何机制在管「一共放进来多少条」**。
syllabus 是第一个会把总量推爆的输入。

### 2.2 先量基线，再谈额度（用户  纠正）

> 「不应该随便按感觉拍板 你应该去审查下系统现在已经在用的有多少
>   如果已经很多 那就收紧 如果相对较少 那就放宽」

我第一版把额度「每季度 ≤3」**直接拍了出来**—— 那正是本文档自己禁止的做法。
去量了：`quant/research/dof_audit.py`（可重跑，AST 扫 live 树）。

```
扫描候选 202 项 − 非自由度 57 项(容差/排序/编码/API/格式/利率入参) = **真实自由度 145**

其中 🚨 会动钱那一层 (授权/仓位/退出/画线) = **71 个**：
     24  有裁决/回测在案      (shock_budget · relative_volume · ladder_entry · options_overlay …)
     17  用户 明确拍的偏好    (40% / 15% / 110% / 5% / 1.5× / 轮预算 / 棘轮 +50%+100% …)
      8  口径约定/教科书标准    (ATR20 算钱·ATR14 读盘 / RSI14 / MACD 12-26-9 …)
      5  运维陈旧度阈值
     17  **无出处的选择**← 真正的债
```

**基线推翻了我的提案。**→ 08-12 **两周内**，光是**有裁决背书**的新增
就有 ~22 个（shock_budget 4 · earnings_window 4 · options_exit_overlay 4 ·
ladder_entry 4 · risk_allocation 3 · synthetic_longcall / N1 / 熔断 各 1 …）。
**「每季度 ≤3」会挡掉过去两周 90% 的工作 —— 拍的数错了一个数量级。**

### 2.2b 修正后的机制：棘轮，不是限流

| | 原提案（已作废） | 修正版 |
|---|---|---|
| 管什么 | 所有新增自由度 | **只管「无出处」那一类**|
| 形状 | 每季度限流 ≤3 | **棘轮：只能减不能增**|
| 有裁决的新增 | 占额度 | **不设上限**—— 代价已由预注册 + 守卫 + FDR 付过；一次回测带出 4 个同源的数（`opt_s1/s2/s4/s3`）本来就该算**一次**决定 |
| 加一个无出处旋钮 | 扣额度 | **先退休或补齐一个**|
| 强制点 | 无（只是约定） | `tests/research/test_dof_audit.py`，基线 **17**，变异测试确认加一个假旋钮会红 |

**这不是新发明**—— 它和 `tests/test_module_coverage.py` 的 `UNTESTED` 名单棘轮
（「只能变短」）是同一个装置。本仓已经证明过这种形状能活下来。

**「无出处」的默认判定**：不在 `dof_audit.CLASSIFY` 里的一律计入 ——
「没人记下它的出处」在操作上就等于「没有出处」。想脱离这一类，
去补那一行**并附出处**（裁决 / 回测 / canonical 文档）。

**判读层不进本预算**：`chart_read` / `patterns` 由测试钉死改不了交易，
它花的是注意力不是钱 —— 那由「一屏内读得完」那条注意力预算管。

**当前 17 个债在哪**（`dof_audit.py --json` 可查全量）：
`exit_guard` 的 margin 守卫与 `new_leg_pct` · `regime_transition` 的
`pivot_lookback`/`swing_wing`/`air_pocket_atr` · `option_plan` 的流动性门槛
（`max_spread_pct`/`min_open_interest`/`fill_days`）· `entry_guard` 的
`stop_n_mult`/`normal_t1_disc` · `sizing.share_ladder(multiplier=)`。
—— 这些不是"错的"，是**没人记下为什么是这个数**。补出处比改数值便宜得多。

### 2.3 它与冲击预算的同构

| | 冲击预算 | 自由度预算 |
|---|---|---|
| 管什么 | 组合单日会亏多少（15% NAV） | 系统里有多少条未经充分校正的规则 |
| 机制 | 总量守恒，逐笔从余额里扣 | 总量守恒，逐条从季度额度里扣 |
| 为什么不逐条判 | 「这一笔看起来还行」× N 笔 = 爆 | 「这条规则 p<0.05」× N 条 = 假发现 |
| 已知边界 | H1 控制力不过（校准与现实脱节） | **本机制未经任何验证，它是治理约定不是实证结论**|

⚠️ **诚实标注**：DoF 预算是**我提的治理机制，没有出处、没有回测**。
它的正当性只来自「多重比较问题是数学事实」这一条，**不来自任何证明它有效的实验**。
按本仓标准，它属于 `[约定]` 而不是 `[实证]`——所以它归 用户 拍，不归数据裁。

---

## 3. 映射：syllabus 主链 ↔ 本仓已有的引擎

syllabus 总目标链：`Context → Structure → Setup → Trigger → Confirmation → Signal → Position → Risk → Exit`

| 链节 | 本仓真源（grep 符号，不抄行号） | 强度 | 说明 |
|---|---|---|---|
| **Context**| `regime_score.q_a_regime`（peers / defensives / VIX 三态轮动）、`macro_fetch.fetch_vix`（FRED）、`portfolio_brief.index_risk`（QQQ/SPY 已完成日线 `INDEX_DROP_PCT=-2.0`）、`.claude/rules/macro-context-check.md` | ⚠️ **弱**| **无宽度、无板块趋势、无跨周期、无横截面**。`q_a_regime` 的 peers/defensives 是**手工传参**不是常驻观测 |
| **Regime**| `current_regime{name,cap_pct_nav,participation,since,source}`（schema 同生同死强制）、`regime_transition`、`episodes.gate_path` 四分类 | ✅ 强 | 但它是**授权语义**的 regime，不是 syllabus 的「市场状态」 |
| **Structure**| `technical.swing_structure`（trend up/down/transition）、`core/structure.py`（gate↔zone 同生同死同锚 / `gate_coherence` / `has_next_gate`）、`episodes.py`（破位史 / 四分类）、`level_map`（VP/HVN/LVN/pivot 簇/OI 墙） | ✅ 强 | 全系统最成熟的一层 |
| **Setup**| 卡片 `agent.zones` + `entry_guard` 的 PLAN/VETO/STALE/NEEDS_CARD + 三档 ladder | ✅ 强 | **只有一个 setup 家族**（gate 转移 + zone 回踩），且是有意的 |
| **Trigger**| zone 触碰（`wl_zone_touch` / 提醒线 z1）、收盘越 gate | ✅ 强 | syllabus 原则 4「Setup ≠ Trigger」我们已实现：PLAN 恒带 ladder 限价，不是市价 |
| **Confirmation**| 收盘 ×N 日 + `regime_transition.volume_leg` 的 **RelVR ≥1.2**（个股量比 / max(基准,0.4)，半导体→SMH 其余→QQQ） | ✅ 强 |  裁决口径 |
| **Signal（score）**| **无独立评分层**| ⛔ **有意缺**| §1 |
| **Position**| `sizing.entry_cap` 六臂（分类/regime − 敞口 · 轮预算 · at-risk · 冲击预算余额 · gross 余额 · 购买力余额）+ `share_ladder` 整股档位 | ✅ 强 | 比 syllabus 第十一阶段细得多 |
| **Risk**| `AT_RISK_CAP_FRAC 5%`、`SHOCK_BUDGET 15%`、`NOTIONAL_CAP 40%`、`GROSS_CAP 110%`、熔断 1.5×、`binding_stop`（卡 stop / 棘轮地板 / 趋势死亡线取 max） | ✅ 强 | |
| **Exit**| `exit_guard`：利润棘轮（+50% 保本 / +100% 回吐上限 1/3）、趋势死亡线（布防后最高收盘 − 3×ATR20；parabolic = 5d high × 0.92）、G-01 档位、期权 overlay S1/S2/S4 | ✅ 强 | 本仓最完整的一层 |

### 3.1 这张表的三条读法

1. **我们已经有一个 Technical Engine 了。**它是 **gate/regime 中心**的，不是 score 中心的。
   syllabus 读起来像「从零建一个技术模块」，但对本仓来说，它 90% 的内容是**已建成物的另一种命名**。
2. **syllabus 真正补的只有三件**：
   ① **Context / Regime 的上游那一半**（宽度 / 板块 / 跨周期 / 横截面）——真缺口；
   ② **事件语法层**（`price_action_framework_design` 的四类交互事件）——已起草，卡在参数锁定；
   ③ **指标的轴纪律**（把 80+ 指标挡在门外的机械判据）——已有实测数字（§4），缺的是规矩本身（Q4）。
3. **落地重心因此是 syllabus 的阶段 1 / 3 / 4 / 14，不是阶段 7。**
   这与直觉相反：阶段 7 篇幅最大、看起来最"技术"，但它对本仓的边际信息量最低（§4）。

---

## 4. 组织原则：五轴取代 syllabus 的 A–G 指标分类

### 4.1 为什么换

syllabus 第七阶段按**信息来源**分类（Trend / Momentum / Volatility / Volume / Breadth / RS / Sentiment），
比"乱堆指标"好，但仍然按**算什么**分。四/五轴按**回答什么问题**分——
而「回答同一个问题」正是冗余的定义。本仓有实测数字支持这个换法（下表）。

### 4.2 轴表

| 轴 | 它回答什么 | 文献钉子（读取层级见 design §10） | 本仓实现 | 冗余状态 |
|---|---|---|---|---|
| **A 位置**| 这条线上有没有「货」 | Kavajecz & Odders-White 2004（S/R = 限价簿 depth 峰，技术规则「定位了已经在簿上的 depth」）+ Osler 2002 订单簇 + 拍卖理论 `[原典]` | `technical.volume_profile`（区间三角铺开）、`level_map.top_hvn / find_lvn / cluster_pivots / lvn_rejection_walls / fetch_oi_walls`、卡片线、`technical.anchored_vwap` | ✅ 最独立的一轴，也是地基 |
| **B 力**| 用了多少量 | Blume-Easley-O'Hara 1994（量携带价格没有的「信号精度」）+ GKM01（异常量按**自身历史**定义） | `regime_transition.volume_leg` 的 **RelVR**| ✅ 已裁决口径 |
| **B′ 果**| 换到多少价 | Wyckoff 法则 3 `[原典]`（effort vs result） | `range/ATR20`、`close_pos`、`tape_check` 的 60m 同口径 | ⚠️ **RSI / 布林 %B / MA 偏离 / 20 日区间位置 实测 ρ≈0.89–0.95 = 同一票**|
| **C 前置状态**| 从什么状态来 | Llorente 等 2002（信息环境定延续/反转）+ Lee-Swaminathan 2000 + 本仓 `gate_path` 回测 | `swing_structure`、`episodes.gate_path` 四分类、`current_regime`、MA60 | ⚠️ 缺**跨周期**（周线）与**波动率状态**|
| **D 后续确认**| 被证实还是被推翻 | Wyckoff spring/UT 的确认与证伪条件 `[原典]` + Lo 等 2000 的检测滞后 `d` | 收盘 ×N 日、RelVR 量腿、`bfloor_buf` | ✅ |
| **E 横截面**（**候选，新**） | 相对于别人如何 | syllabus 第十八阶段自己把 Cross-sectional 单列；Neely 等 2014 的「跨族互补」 | `technical.rs_series` + `level_map.build_trend_obs`（[信号5] AVWAP+RS，**FYI-only**） | ✅ **我们 12 个连续读数里没有任何一个是横截面的**——它是结构上最可能正交的一族 |

### 4.3 支持换法的实测（`quant/research/indicator_redundancy.py`，只算相关不算收益）

```
A. 12 个连续读数 / 60 票 / 55,732 行 (逐票标准化后池化)
   PC1 解释 53.1% | PC1-3 78.8% | 到 80% 需 4 个主成分
   **有效维数 (participation ratio) = 3.01 / 12**
   最相关的对: boll_%B ~ 20日区间位置 0.95 | RSI14 ~ MA20偏离 0.91
                RSI14 ~ 20日区间位置 0.90 | boll_%B ~ MA20偏离 0.90 | RSI14 ~ boll_%B 0.89

B. detect_bottom_signals 的「共振投票」/ 11,800 个逐日快照 (调真函数不复刻)
   提升度: rsi_div ~ macd_div **×7.25**(共现 642 次) | bb_lower_touch ~ ma60_extreme ×4.01
   score=A 最常见组合 74× = macd_div + near_ma60 + rsi_div  ← 三票里两票是同一件事
```

⚠️ **方法学注记（也是一个坑）**：二值信号的 φ 相关被基础率压住，会**高估独立性**——
第一版报的「有效票数 9.12/10」就是这么来的。**稀疏二值上不要只看相关系数，要看提升度。**

### 4.4 「结合使用」的三种形态（用户 「指标也是要结合使用的」的严格版）

| 形态 | 证据 | 对我们的含义 |
|---|---|---|
| **同族压成共识**| Neely, Rapach, Tu & Zhou, *Management Science* 2014：14 个技术指标 PCA **K=1**、载荷近似均匀，「第一主成分像一个 consensus 指标」；PC-TECH R² 0.84% | **组合出来的是「一个」因子，不是 14 份证据**。共识 = **平均**，不是**计票**|
| **跨族互补**| 同篇：技术 × 宏观**互补**（技术抓周期顶附近溢价下降，宏观抓周期底附近上升） | 增量来自**跨轴**，不来自同轴堆叠 |
| **跨时间尺度**| Han, Zhou & Zhu, *JFE* 2016：把短/中/长期均线信息合成**一个**trend factor，夏普比是三个单趋势因子的**两倍以上**；Zhu & Zhou, *JFE* 2009 | **同一个东西在不同尺度上的读数，是可以组合的真信息**；同尺度换算法（RSI vs %B vs MA 偏离）**不是**|

> 对照 Zakamulin (2017)：各类均线与动量指标**都能写成过去收益的线性组合**，差别只在权重函数形状。
> ⇒ 换一个「指标」，很多时候只是换一组权重，**不是换一个信息源**。
>
> **这三条合起来解释了本方案的优先级**：跨尺度（阶段 1 周线）与跨族（阶段 3/4 Context、轴 E 横截面）
> 有顶刊证据；同尺度加指标（阶段 7 的 80 个）没有。

---

## 5. 十九阶段逐段落地

> 每段格式：**要什么 → 现状（真源）→ 已裁决不重开 → 缺口 → 动作 → 验证**。
> 「已裁决不重开」一栏是本节最重要的部分：它防止 syllabus 把已经付过学费的问题重新打开。

---

### I. Understand Market

#### 阶段 1 · 多时间周期分析

- **要什么**：Monthly→5min 全谱、Top-Down、HTF/LTF、Timeframe Alignment / Conflict。
- **现状**：日线为主（`kline_cache` 13 个月 live / `klines_deep` 167 票深度）；60m 只有 `tape_check`
  （climax_fade / drive_hold / absorption / steady_trend / mixed 五态，**情报层不参与授权**）；
  **周线完全没有**；月线没有；15m/5m 不做（我们不做日内）。
- **已裁决**：`tape_check` 的定位（FYI）是既定的；日线口径「未完成 bar 不是收盘」是硬约束
  （`heartbeat.md` §盘中运行，NBIS 7/28 源案例）。
- **缺口**：**周线是本 syllabus 里性价比最高的一个缺口**——它是唯一一种有顶刊证据的"结合"
  （Han-Zhou-Zhu 2016 跨 horizon 合成，夏普翻倍），且**零新数据**：日线→周线是精确重采样。
- **动作（B1）**：`core/technical.py` 加 `resample_weekly(bars)`（自然周，周五收盘为准；
  不足一周的尾部**丢弃**不算半根 = 与「未完成 bar 不是收盘」同一条纪律）。
  用途**只有两个**：① `swing_structure` 的周线读数进**轴 C**（前置状态分层变量）；
  ② 周线 ATR 作为 zone 宽度的 sanity 提示（**不是新 stop**，不进 `binding_stop`）。
- **验证**：**不单独立项**。作为交互事件回测（§7 B2）的**分层变量**跑
  ——「周线 trend=up 的事件」与「周线 trend≠up 的事件」是否可区分。
  这样它的自由度成本是 **1**（周线定义），而不是「一整套 MTF 系统」。
- **禁止**：不引入 4H / 30m / 15m / 5m；不做「三周期共振」计票（那是 §4.4 明令的计票错误）。

#### 阶段 2 · 市场趋势状态

- **要什么**：Bull/Bear/Correction/Rally/Trend/Range/Consolidation/Expansion/Contraction/
  Accumulation/Distribution/Reaccumulation/Redistribution/Risk-On/Risk-Off（15 个词）。
- **现状**：本仓有三套**已经在用**的状态词，且**分层严格**：
  `current_regime.name`（授权语义）/ `trend ∈ {up,down,transition}`（结构语义）/
  `gate_path` 四分类（结构身份：①修复 ②突破 ③箱体顶 ④新高）。
- **已裁决 🚨**：`position-tiers.md` §语义分层——**一个词只属于一层，仓位层与结构层词汇永不互借**
  （定版，起因是 "confirmed" 打三份工连烧四轮对话）。
- **缺口**：无。syllabus 这 15 个词**没有一个**带来新的可观测量。
- **动作（B0-1，零自由度）**：做一张**词表映射附录**（syllabus 词 → 本仓词 / 真源 / 若无对应则标「不引入」），
  放进本文档 §附录 A。目的是**防新词入侵**：下次有人说「我们需要一个 redistribution 状态」时，
  这张表直接回答「它映射到 `gate_path`③ + trend=transition，不新增」。
- **验证**：不需要（零自由度）。

#### 阶段 3 · 大盘 Context

- **要什么**：SPY/QQQ/IWM/DIA/VIX + Market Breadth（A/D、New High/Low、% Above MA、
  等权指数、Leadership、Sector Rotation、Relative Strength）。
- **现状**：QQQ/SPY 在 `portfolio_brief.index_risk`（阈值 `INDEX_DROP_PCT = -2.0`，24h 边沿冷却，
  数据源 = kline 缓存**已完成日线**）；VIX 走 `macro_fetch.fetch_vix`（FRED 免费源）；
  `q_a_regime` 有 peers / defensives 的**手工传参**三态轮动（INTRA-MARKET ROTATION / BROAD EXIT / MIXED）。
  **宽度指标一个都没有。**
- **⚠️ 命名冲突登记（新，按 ledger §2 惯例）**：
  `quant/research/pull_breadth_history.py` **不是市场宽度**，它是**回测池的行业扩容**
  （Yahoo v8 日线拉 ~105 票进 `klines_deep`，用户  批的 research-only 数据源例外）。
  名字里的 "breadth" 指的是**票池的宽度**，不是市场宽度。
  ⇒ **B0-3**：在该脚本 docstring 加一行消歧；本文档登记。
- **已裁决**：`index_risk` 这条检查曾经是**死代码**半年（写在持仓循环里，而 QQQ/SPY 不是持仓，
  永远进不了那个循环， 修）——**「一条写得很具体的死检查比没有这条检查更危险」**。
  任何新的 Context 检查**必须端到端验证会响**。
- **缺口**：宽度、板块趋势、leadership。
- **数据可得性（关键）**：
  - `% above 50DMA` 可以用 `klines_deep` 167 票算——但**那不是指数成分**，是我们为了去相关自己挑的池，
    所以它是**有偏代理**，必须播报口径（「全池固定阈值 = 没有阈值」的同款教训）；
  - **真板块宽度可得**：OpenD 的 `get_plate_list.py` / `get_plate_stock.py` / `get_owner_plate.py`
    能拉板块成分 ⇒ 可以算 **SMH 成分的 % above 50DMA**，这比全市场宽度**更贴我们这本书**；
  - A/D line、New High/Low 需要全市场成分，**代价高、收益不明**——不做。
- **动作（B3a）**：**先做 0 自由度版本**——只在 [FYI] 输出里播报数值
  （SMH 成分 % above 50DMA、我们这本书自己的 % above 50DMA、QQQ/SPY/VIX 现值），
  **不设阈值、不判定、不进任何授权**。跑 ≥8 周后再谈阈值（届时计 1 DoF 并走入账条件）。
- **验证**：观察层无需裁决；设阈值时才需要。

#### 阶段 4 · Sector / Industry Context

- **要什么**：板块趋势、轮动、Leading/Lagging、Stock vs Sector、Stock vs Index、
  建立 `Market → Sector → Industry → Stock`。
- **现状**：`core/sectors.py` 的 `SEMI_SMH` 冻结表（24 票）+ `benchmark_for`——
  **只用于 RelVR 的分母**（半导体→SMH，其余→QQQ）。
- **🚨 已裁决（边界要引准）**：`risk_sector_design.md`  **不采纳**——
  否掉的是【用**按共动性聚类**得到的分组做风险板块限额】（判据③稳定性卡死：ARI 最高 0.15，k≤5 为负，
  「拟合的是噪音」），并明确**不新建**`core/risk_sectors.py`、`core/sectors.py` 保持原样。
  **没有否掉**「用**固定**板块 ETF 做基准/相对强度参照」——后者正是 `relative_volume` 分支 R
  **采纳并接线**的东西。引用这条裁决时必须带这个边界，否则会把已采纳的东西当成被否的。
- **缺口**：`SEMI_SMH` 只覆盖 24 票且只服务一个用途；板块**趋势**与**相对强度**没有。
- **动作（B4 前置）**：把冻结表扩到全书（手工 ticker→板块 ETF 映射，**冻结、不聚类**），
  服务两个用途：① RelVR 分母（已有）② 轴 E 横截面 RS 的分母（新）。
  **DoF = 0**（映射是事实不是参数），但它是**手工步骤 = 无主步骤风险**（`data_discipline` 形状）
  ⇒ 守卫必须存在：新票无映射 → 退 QQQ **且播报降级**（现有 `benchmark_for` 已是这个行为，已核实）。
- **验证**：映射表本身不需要验证；轴 E 的用法需要（见阶段 7F / §7 B4）。

#### 阶段 14 · Market Regime → Strategy Selection

> （提前到这里，因为它属于 "Understand Market" 组，且是本 syllabus **最危险的一章**）

- **要什么**：Trending / Range Bound / High Vol / Low Vol / Risk On / Risk Off 分类，
  然后 `Market Regime → Strategy Selection`，「避免把同一套技术信号用于所有市场」。
- **现状**：本仓的 regime **只用于授权额度**（`cap_pct_nav` / `participation`），
  **不用于选策略**。我们只有一个策略族（趋势 / 结构）。
- **🚨 落地裁决建议（Q7）：明确不做。**理由三条：
  1. **乘法自由度**：R 个 regime × S 个策略 = R×S 个待验证格，且每格的样本是全样本的 1/R。
     以本仓的样本规模（gate 家系 Kish 70–200），切三份就基本全进「不裁决」；
  2. **本仓已有反例**：`gate_path_taxonomy` 正是「按结构身份分级授权」的尝试——
     **四类全否**（②R 显著为负、⑤止损率四类全部越守卫）。**按状态分级给不同待遇，我们试过，失败了**；
  3. 真正需要「按 regime 变行为」的地方我们已经用**额度**表达了（cap 0 / 分类天花板 / 轮预算），
     那是标量收紧，不是策略切换——**标量收紧不引入新的成功/失败模式，策略切换引入**。
- **保留**：syllabus 这一章唯一该留下的是**它的警告**（不要把同一套信号无条件套用）。
  我们执行这个警告的方式是**分层报告**（§9 的预注册模板要求按信息环境 / 结构身份分层报，
  不是建切换器）。

---

### II. Read Market

#### 第二阶段（市场结构）1–5：趋势结构 / Structure Change / S-R / 供需 / 趋势线

- **要什么**：HH/HL/LH/LL、swing、impulse/correction；BOS/CHoCH/MSS/Failed Break/Reclaim/
  Swing Failure/Liquidity Sweep/Stop Run；各类 S/R；Supply/Demand Zone / Order Block / FVG /
  Price Acceptance / Rejection；趋势线 / 通道。
- **现状**：
  - 趋势结构 = `technical.swing_points(wing=2)` + `swing_structure` → `trend ∈ {up,down,transition}`；
  - Structure Change = `episodes.py`（`episodes_of` 破位定义、`gate_path` 四分类、`breakdown_in_window`）
    + `regime_transition`；
  - S/R = `level_map` 全家（HVN/LVN/pivot 簇/OI 墙/交互史）+ 卡片线；
  - Acceptance/Rejection = `lvn_rejection_walls` + 四类事件 E1/E2/E3/E4。
- **已裁决（三条，都不重开）**：
  1. `structure_downgrade_backtest` ：`trend ∈ {down, transition}` 降级；
     **`close_below_last_swing_low` 不降级**——该桶 p10 **−5.97%**优于 up 干净的 **−12.95%**、
     胜率 75% 全场最高（那是上升趋势中的回调，不是破位）。**改判别式前必跑该回测。**
  2. **FVG 不进**： 评估结论「FVG = 无量数据的 LVN 几何代理，我们用真分布直接找」。
  3. `hvn_gate`  **不采纳（平手保留 incumbent）**：gate 锚从 pivot 簇换成 HVN 上沿，
     四判据全过**但配对 h2h 判死**——「起作用的是**头顶有没有一条供给线**，不是用哪种方法找到它」。
- **🚨 本轮发现的一个真缺口（方法论级）**：
  Lo, Mamaysky & Wang (2000) 明确反对**在原始价上直接取极值**——
  「识别出太多极值，而且产出的形态与技术分析师认为可信的形态不一致」；他们的工序是
  **先核回归平滑、在平滑序列上找一阶导变号、再回到原始价的 `[t−1,t+1]` 取真实高低点**。
  **我们的 `swing_points(wing=2)` 正是他反对的那种做法。**
  - 影响面：`trend` → `structure_downgrade` 降级判别式 → 入场档位；`cluster_pivots` 的输入；
    交互事件的 pivot 锚。
  - **处理：不做局部手术。**改 `swing_points` 会改 `trend`，而 `trend` 驱动授权。
    要动就**整体重跑**`structure_downgrade_backtest`（同 Q-C 与 K 线检测器的处理原则）。
  - 现在做的只有**登记**（B0-6）：在 `swing_points` docstring 写明这条已知方法论债 + 出处。
- **不做**：SMC/ICT 词汇（BOS/CHoCH/MSS/liquidity sweep/stop run）——
  它们的机械内容**已被四类事件完全覆盖**（BOS = E2；CHoCH = trend 状态变化；
  liquidity sweep / stop run = E3 + Osler 止损级联机制）。引入新词 = 新自由度 + 违反语义分层。
- **不做**：趋势线 / 通道——两点定线，「选哪两点」没有判据 = 画法自由度无限。
  唯一机械可算的是**回归通道**（最小二乘 ± k×σ），列为**最低优先级候选**，本轮不开。

#### 第三阶段（Price Action）1–6：K线 / 形态 / 突破 / 回踩 / 拒绝接受 / 动量

- **要什么**：OHLC 结构、18 种 K 线形态、突破族、回踩族、拒绝/接受族、动量族。
- **已裁决 / 已学结论（design §5，本轮不重开）**：
  - 正式检验**以负面为主**：Marshall, Young & Rose (*JBF* 2006, DJIA 成分 1992-2002，
    **随机生成 OHLC 四价**的扩展 bootstrap 作零分布) → K 线策略对 DJIA 股票**没有价值**；
    Marshall, Young & Cahan (2008, 东证前 100) → 无正异常收益；Horton (2009) 负面。
    正面只有 Caginalp & Laurent (1998, S&P500 三日反转形态) 与部分新兴市场。
  - Bulkowski 的 shooting star（用户 例②）：**「Bearish reversal 59%」，他自己标注 "near random"，
    overall rank 55/103**，并写着「这根 K 线看起来比它的表现好」。
  - ⇒ **单 bar 形状不作为独立触发器**；它只能作为「在某条线上被拒绝」这一**事件**的观测特征。
- **现状的存量问题（design §6.8，仍未处理）**：
  `technical.detect_hammer` / `detect_engulfing_bullish` / `detect_morning_star`（**三个无出处的判别式**）
  → `detect_bottom_signals().patterns` → `regime_score.q_c_exhaustion` → **Q-C 是加仓节奏三旋钮之一**。
  它们现在的位置是「共振计分里的一票」，不是独立触发器——**危害有限但不是零**。
- **动作（B0-2，零行为改变）**：三个 `detect_*` 的 docstring 加**证据等级 + 出处**
  （`[实证-负面为主]` + 上面三篇 + 一条正面）。**不删、不改行为**——
  删掉会改 Q-C 的行为，而 **Q-C 从未被单独回测过**。要动就整体回测 Q-C。
- **突破 / 回踩 / 拒绝接受 / 动量四族**：全部落进四类事件 + 轴 B 的 2×2，**不新增形态名**。
  syllabus 的 `Undercut and Rally` / `Shakeout` = **E3（陷阱，下方 = Wyckoff Spring）**；
  `Failed Breakout / Fakeout` = **E2 的否定 = E3（上方 = Upthrust）**；
  `Breakout Retest` = **E4（易位）**。这正是四类事件族「由构造封死」的价值：
  syllabus 列的十几个突破/回踩术语，全部是四个格子的别名。
- **Exhaustion / Climactic / Parabolic**：已有——parabolic 走 `zone-maintenance.md` 第 4 步
  momentum diagnosis 三问（6m ≥+150% / 20d 内单日 ≥+10% / 距 ATH <10%）+ parabolic anchor；
  climax 走 `tape_check` 的 `climax_fade`。**不新增。**

#### 第四阶段 · Chart Patterns（形态族）

- **要什么**：延续族（旗形/三角/矩形/杯柄/平台/Darvas/VCP/三周收紧）、反转族（双顶底/三顶底/头肩/
  圆弧/岛形/V）、楔形、缺口族、成长股 base 族（IPO base / cup base / 双底 base / 上升 base…）
  + pivot / buy point / early entry / shakeout / handle / base depth / base duration。
- **🚨 明确不做（design §7.4，本文重申并给出唯一可接受路线）**。理由：
  1. Lo 等的形态是 **35 日窗口的 5 个连续极值几何**，我们的决策单位是 gate；
  2. 引入它们会新增 **≥6 个容差参数**（HS 的 1.5% / RTOP 的 0.75% / DTOP 的 22 日间隔…），
     在数据窥探约束下不划算；
  3. 成长股 base 族（O'Neil / Minervini 体系）我**没有找到同行评议检验**——Bulkowski 是自证式的，
     ⇒ `[民间]`，登记不采用。
- **但 syllabus 原则 3「Structure > Pattern Name」指了一条正确的路**：
  **把形态名降解成可测量。**最有价值的一个例子：
  **VCP（波动收缩形态）的核心可观测量 = 波动收缩**，那是机械可算的（阶段 6 的 range contraction / NR7）。
  ⇒ 我们不实现 "VCP"，我们实现 `range_contraction` 读数并把它放进**轴 C**（阶段 6 的动作）。
  同理：cup&handle 的核心 = 「回撤深度 + 时长 + 右侧缩量」，前两个是 `episodes_of` 已有的量。
- **唯一可接受的重开路线**（写下来，防止将来有人偷偷做半套）：
  必须整套照 Lo 的工序——① 核回归平滑后取极值（不是原始价）② 带宽**事前**锁定（他们用 0.3×h*，
  并在脚注说明是分析前定的 = 2000 年的一次预注册）③ 窗口 `l=35` + 检测滞后 `d=3` 写进定义
  ④ 检验的是**信息含量**（decile-Q / KS）不是盈利 ⑤ **全族报告 + FDR**。
  ——单独立项、单独预注册、单独一行 ledger。

#### 第五阶段 · Volume / Price

- **要什么**：基础量（RVOL/spike/dry-up/dollar volume/turnover）、四象限量价、
  Volume Structure（VP/VRVP/FRVP/POC/VAH/VAL/HVN/LVN）、VWAP 体系（含各类 AVWAP）。
- **现状**：
  - 相对量 = **RelVR**（已裁决口径，）；
  - 四象限 = 轴 B 的 2×2（力大/小 × 果大/小），design §6.2；
  - VP = `technical.volume_profile`（**成交额按 bar 真实区间三角铺开**，不是压成 TP 一个点——
    旧法对 5m 真值 TVD 21.5%→7.2%、POC 7/10→10/10，梳理见 `volume_profile_accuracy.md`）
    + `top_hvn` / `find_lvn` / `lvn_rejection_walls`；
  - AVWAP = `technical.anchored_vwap`，消费者只有 `level_map.build_trend_obs`（[信号5]）。
- **已裁决**：
  - 「放量不涨 = 见顶」**在文献层面没有出处**（design §2.4 四篇指向四个方向：
    CGW93 高量下跌反转 / LMSW02 取决于信息环境 / GKM01 异常高量后 1 个月**偏涨**/ LS00 高换手赢家长期反转）
    ⇒ 只能写成**条件式**并单独回测（H1）；
  - Lo 等测过「把形态**再条件化于成交量趋势**」→ 增量与减量两条条件分布的差异**对几乎所有形态都不显著**
    （唯一例外 TBOT）。⚠️ 他们的量是**跨年换手趋势**，不是 bar 级相对量 ⇒ **这条不反驳 RelVR**，
    但反驳「用长期量趋势给形态背书」。**区别必须写明，否则就是拿一篇论文的否定结论去背书另一个东西。**
  - 🚨 **LVN ≠ 无阻力**（用户 纠正，AAOI 140 源案例）：LVN 答「有没有**库存**」（决定回踩停哪），
    rejection 答「有没有**卖方**」（决定上攻卡哪），**反复的快速拒绝正是真空的成因**⇒ 两者必然同真。
    `level_map.lvn_rejection_walls` 已交叉这两个信号，且**按由近及远排不按强度排**
    （下跌趋势里越高越旧的线计数天然越多）。
- **缺口（一个，低成本高价值）**：**价值区 VAH / VAL 没有实现**。
  我们有 POC / HVN / LVN，但没有拍卖理论的「≈70% 成交所在区间」边界——
  而「区内 = 平衡（均值回复语义）/ 区外 = 失衡（趋势语义）」是**轴 A 的坐标系**，不是一个信号。
- **动作（B0-4）**：`volume_profile` 输出加 `vah` / `val`（70% 是**拍卖理论的定义常数**，
  不是我们调的参数 ⇒ **DoF = 0**）。用途：交互事件记录里给每条锚标「在价值区内/外」，
  作为回测的**分层变量**。
- **不做**：OBV / A-D / CMF / MFI 等**量族累积指标**——见阶段 7D（需先做 lift 体检 B5）。

#### 第六阶段 · Volatility

- **要什么**：HV/RV/ATR/TR/ADR/BB Width/波动扩张收缩/波动率 regime；
  Squeeze/NR4/NR7/Inside Day/Wide Range Day；期权侧 IV/IV Rank/IV Percentile/期限结构/skew/expected move。
- **现状**：`technical.atr`（**ATR20 = 算钱 / ATR14 = 读盘**，分工是既定口径）、`true_range`、
  `bollinger`；期权侧有 `iv_logger`（每日 ATM IV 落库，**不可重拉必须入 git**）+ IV percentile +
  `option_plan` 的 IV 闸门表（canonical 在 `options-strategy-framework` §六）+ `implied_move`。
- **判断**：syllabus 这一节列的价格波动指标**几乎全是 ATR 的单调变换或子集**
  （冗余体检已证 boll_%B ~ 20 日区间位置 ρ=0.95）。**真正新的只有一个东西：波动率的时间结构**
  ——收缩→扩张的**转折**（squeeze / NR7 / VCP 的可测量核心）。
- **动作**：只加**一个**读数 `range_contraction`（近 N 日 ATR / 前 N 日 ATR，或 NR7 计数），
  进**轴 C**。**DoF = 1**（窗口 N）。
  **不加**BB Width / Keltner / Donchian / Chaikin Volatility / ADR（同轴同源 = 一票）。
- **期权侧缺口**：IV 期限结构与 skew 未落库（`get_option_volatility.py` 可拉）。
  价值只对期权 vehicle 存在，且 IV percentile 闸门已在管 ⇒ **候选，不优先**。
- **验证**：`range_contraction` 同 B1，作为事件回测的**分层变量**，不单独立项。

---

### III. Quantify Market

#### 阶段 7 · Technical Indicators（A–G，80+ 个）—— 本方案对它做减法

> **这一节是 syllabus 篇幅最大、对本仓边际信息量最低的一节。**处理方式 = 逐族点名 + 轴归位 + 一票制。

| 族 | syllabus 列了 | 裁决 | 依据 | 本仓代表 |
|---|---|---|---|---|
| **A Trend**| SMA/EMA/WMA/HMA/VWMA/DEMA/TEMA/KAMA/ALMA/McGinley/Ribbon/Crossover/MACD/ADX/DMI/Aroon/SAR/Supertrend/Ichimoku/Vortex/TRIX/Mass Index（22） | **一票**| Zakamulin 2017：均线与动量类规则**都是过去收益的线性组合**，差别只在权重函数形状（他扫了 **300 种**形状） | `sma/ema`（MA20/60/200）+ `swing_structure`（**结构版趋势，与均线不同源**） |
| | | ⚠️ **ADX/DMI 单列为候选**| 它测的是趋势**强度**不是方向——可能不与 A 族同源 | 未实现；进候选须先测 lift vs `range/ATR` |
| | | ❌ Ichimoku/Supertrend/SAR/Vortex/TRIX/Mass Index/Aroon/KAMA/ALMA/McGinley **不进**| 无增量信息、只增自由度 | — |
| **B Momentum**| RSI/Stochastic/StochRSI/ROC/Momentum/CCI/Williams %R/UO/AO/PPO/TSI/CMO/Fisher/Coppock（14） | **一票**，且这一票的内容是「**今天收盘离最近的均值有多远**」 | 本仓实测：RSI14 ~ MA20偏离 **0.91**/ ~ 20日区间位置 **0.90**/ ~ boll_%B **0.89**| `rsi`（已在 `detect_bottom_signals`）或 `ma_deviation`，**二选一**|
| **C Volatility**| ATR/BB/BB Width/Keltner/Donchian/StdDev/HV/Chaikin Vol（8） | **一票**| 同阶段 6 | `atr`（20/14 分工） |
| **D Volume**| OBV/A-D/CMF/MFI/Volume Osc/PVO/Force/EOM/NVI/PVI/VWAP/AVWAP（12） | ~~⚠️ 唯一可能有正交增量的一族~~ → **体检完毕：整族关掉**（见 §5.1） | 它们**不是量读数，是价格读数**：与 RelVR \|ρ\|≈0.02–0.05，与价格读数 \|ρ\|≈0.58–0.74 | `volume_ratio`/RelVR 维持为轴 B 唯一读数；**VWAP/AVWAP 属轴 A（位置）不属轴 B**← 这个归位非显然但重要 |

#### 5.1 量族体检结果（`quant/research/volume_family_redundancy.py`，只算相关不算收益）

60 票 / **269,088 行**/ `klines_deep` 池 / 逐票标准化后池化：

```
Q1 量族内部有效维数 (OBV/AD/CMF/MFI 四个)   = 1.73 / 4
Q2 量族 + RelVR 五个一起                    = 2.43 / 5     ← RelVR 确实**加了约一维**
   七个一起 (含 mom20 / ma20_dev 两个价读数)  = 2.47 / 7     ← 价读数几乎不再加维

Q3 每个量族指标离谁更近:
   obv_slope  |ρ| vs RelVR 0.019   vs 价格读数(max) 0.742   → **更像价格**
   ad_slope   |ρ| vs RelVR 0.018   vs 价格读数(max) 0.585   → **更像价格**
   cmf20      |ρ| vs RelVR 0.018   vs 价格读数(max) 0.585   → **更像价格**
   mfi14      |ρ| vs RelVR 0.050   vs 价格读数(max) 0.728   → **更像价格**
```

**三条结论**：

1. **这一族不在轴 B（力），在轴 B′（果）。**OBV 与 A/D 都是**带符号累积**，
   其斜率在构造上就与价格方向纠缠 —— 实测证实了这一点。
   ⇒ 「加一个量能指标」实际上是**又加了一票价格**，而轴 B′ 已经有
   RSI/%B/MA偏离/区间位置（ρ≈0.9）挤在那里。**阶段 7D 整族关掉。**
2. **RelVR 反而被这次体检背书了**：1.73 → 2.43 说明它与这一族**近乎正交**
   —— 我们现有的量腿不是这一族的冗余，它是真的在测另一件事（当日相对量）。
3. 🚩 **`ad_slope ~ cmf20 = +1.000` 是构造恒等式不是发现**：
   `ad_slope = Σ(MFM·v)/(n·avg_v)` 而 `n·avg_v = Σv` ⇒ 它**恒等于**`CMF(n)`。
   syllabus 的 D 族清单里，两个不同名字的指标是同一条公式 ——
   Zakamulin「换指标常常只是换权重不是换信息源」的一个当场实例。

⚠️ **边界**：本体检测的是**相关性**，不是**增量预测价值**。
高相关 ⇒ 可以关掉讨论；低相关 **⇏**有 alpha。要证明后者必须走预注册回测。
| **E Breadth**| A/D Line、A/D Ratio、McClellan、TRIN、New High-New Low、% Above 20/50/200DMA、Zweig Thrust（10） | **数据是瓶颈不是算法**| 见阶段 3 | 无。板块宽度代理可做（B3a） |
| **F Relative Strength**| RS、RS Line、Comparative RS、RS Rating、Mansfield RS、Stock/SPY、Stock/QQQ、Stock/Sector（8） | **轴 E 候选（唯一结构性正交的一族）**| syllabus 第十八阶段自己把 Cross-sectional 单列；我们 12 个连续读数**没有一个是横截面的**| `technical.rs_series` 已实现，消费者只有 `level_map.build_trend_obs`（FYI） |
| **G Sentiment/Positioning**| VIX/Put-Call/AAII/Fear&Greed/Short Interest/Days to Cover + Dealer Gamma/GEX/Call-Put Wall/Zero Gamma/OI/期权量 | 混合 | | VIX ✅（`macro_fetch`）；Short Interest **端点已装未用**（`get_short_interest.py` / `get_daily_short_volume.py`）；OI 墙 ✅（`level_map --oi`）；GEX ❌ 无数据源，canonical `dealer-gamma-positioning.md` 已写明只有 OI 粗代理且**不放宽 size**|

**结论：80+ 个指标 → 5 个轴 / 至多 7 票。**增加的候选只有三个（ADX、量族累积、横截面 RS），
且每个都要先过 lift 体检（B5）才谈回测。

#### 阶段 8 · Indicator Interpretation

- **要什么**：趋势/动量/量确认、各类背离、超买超卖、交叉、斜率、扩张收缩、指标 regime、
  **Indicator Dependency / Avoid Double Counting**。
- **本仓在这一阶段领先于 syllabus**——因为我们有数字而不是原则：
  - 背离：`detect_rsi_divergence` 与 `detect_macd_divergence` **共现提升 ×7.25**
    ⇒ 它们不是两份证据，是「价格新低但动量没新低」这一件事的两种算法；
  - 超买超卖 = 「离均值多远」的另一个名字（ρ≈0.9）；
  - 交叉 / 斜率 / 扩张收缩 = 同一序列的导数读数。
- **动作（Q4）：采纳指标四条规矩**（design §13.5 提案）：
  1. **每个指标必须声明它服务哪一根轴**（A/B/B′/C/D/E）。说不出来的不进。
  2. **同一根轴上的同源指标 = 一票。**增量只能来自**跨轴**。
  3. 轴内确实要用多个时，**先报有效维数与 lift，再按"共识"压成一个数**（平均 / PC1），**不许计票**。
  4. **指标不做独立触发器。**
- **对 Q-C 的具体建议（不改代码）**：`q_c_exhaustion` 直接吃 `detect_bottom_signals` 的 A/B/C，
  而 A 级最常见组合的三票里有两票是同一件事。建议 ①`rsi_div`+`macd_div` 合并为「动量背离」一票
  ②`bb_lower_touch`/`ma60_extreme`/`near_ma60` 同属「离均值多远」族取其一
  ③计票改**跨族计票**（量族 / 动量族 / 位置族 / K 线族，要求 ≥2 个**不同族**）。
  ⚠️ **不做局部手术**——Q-C 从未被单独回测过，改计票就是改它的行为。**要动就整体回测 Q-C。**

---

### IV. Build Signal

#### 阶段 9 · Technical Setup

- **要什么**：四类 Setup（趋势延续 / 趋势反转 / 动量 / 均值回归）× 六要素
  （Context / Preconditions / Setup / Trigger / Confirmation / Invalidation）。
- **现状**：我们**只有一个 setup 家族**，但六要素齐全且都有强制点：

| 要素 | 本仓实现 | 强制点 |
|---|---|---|
| Context | `current_regime`（name/cap/participation/since/source） | schema 同生同死 |
| Preconditions | `cap_pct_nav > 0`、结构未破、zones 未过期、无 `pending_repaint`、在 Today 组 | `entry_guard` VETO / `today_group_sync` |
| Setup | 卡片 zones（z1/z2/trim/no_chase/stop）+ `next_regime.entry_gate` | `has_next_gate` + `gate_coherence` + 单调性 |
| Trigger | zone 触碰（提醒线 z1 / `wl_zone_touch`）或 收盘越 gate | `price_reminder_sync` / `regime_transition` |
| Confirmation | 收盘 ×N 日 + **RelVR ≥1.2**| `volume_leg` |
| Invalidation | `stop_level` + `invalid_if` + `binding_stop` 三线取 max | `exit_guard` |

- **syllabus 原则 4「Setup ≠ Trigger」我们已实现**：`entry_guard`  移除 GO/WAIT，
  PLAN 恒带 ladder + 股数 + 止损单行，「挂的是 zone 限价单不是市价单」，市价位置降为 📍 信息行。
- **缺口**：**均值回归 setup 完全没有，且是故意的**——`mindset-structure-pairing.md`：
  持有心态的资格由**结构**授予，不由 conviction 授予；我们是趋势/结构系统。
- **动作（B0-5，零自由度）**：把上表**显式写进本文档**（附录 B），作为**将来新增 setup 的模板与举证标准**
  ——任何新 setup 提案必须先填满这六格，填不满的不进讨论。

#### 阶段 10 · Signal Architecture

→ **已在 §1 裁决**。此处只留一条转写：
syllabus 的 `Signal = Context + Structure + PA + Volume + Momentum + Volatility` 这个**加法**是错的形状；
正确的形状是**条件式**：`Context/Structure 给资格 → Price×Line 交互给事件 → Volume/Volatility 给分层`。
——这也正是 design §13.5c 登记的那个缺口（「趋势过滤 + 振荡器触发」这一实务最常见配方
**没有可引用的同行评议检验**，搜索返回的是内容农场）⇒ 它在本框架里是**一个真问题，不是一句已知结论**。

---

### V. Trade Signal

#### 阶段 11 · Signal → Position

- **现状（比 syllabus 细）**：`sizing.entry_cap()` 六臂取小 = 「这一笔实际能下多少」的**单一真源**：
  ① 分类/regime cap − 当前敞口（累计语义）② 轮预算（`ROUND_RISK_FRACS` 2.5/1.5/1.0%，按真 stop 距离）
  ③ at-risk ≤5% NAV ④ 剩余冲击预算 /(1.88×ATR20/价) ⑤ 110% NAV − 已用 gross ⑥ 购买力余额。
  四个消费者读同一份（入场计划 / Today grant / 手机 z1 备注金额 / brief 预算行）。
  整股档位走 `share_ladder`（≥3→3 档 / 2→2 档 / 1→单档豁免 greedy-bid 禁令 / 0→书面例外）。
- **syllabus 补的**：`R-Multiple` —— 我们有（回测统一用 `fwd_n = 前向收益 / ATR20@事件日` 风险 N 口径，
  与 `gate_path_taxonomy` 同族可横比）。
- **动作**：无。**这一阶段 syllabus 学不到东西，反向倒是可以：**
  syllabus 缺「多臂取小 + binding 播报」这个概念，而那是本仓踩出来的（一张写 40% 的卡今天只给 34%，
  不写出来就没人能解释）。

#### 阶段 12 · Technical Risk Management

- **现状**：stop 三家族已在 `level_map --stops`（`turtle_n` 2N / `chandelier` 吊灯 / `stop_sanity` 结构体检）；
  live 强制线 = `binding_stop`（卡 stop / 棘轮地板 / 趋势死亡线取 **max**）；
  转移后地板 = `bfloor_buf`（`gate − 0.5×ATR20@转移日`，收盘 ×1 日，round-2b 唯一双主格合格变体）。
- **已裁决**：
  - `bfloor`（stop = gate 本体 ×1 日）**是全场最差变体 = whipsaw 机器**；
  - stop 距任一 gate < 0.25×ATR20 → 播报「地板贴 gate = whipsaw 语义」（advisory 不改判）；
  - `[0.7N, 2N]` 体检**只在画线时刻跑**，持仓期跑 = 劝退止损机。
- **⚠️ 本轮核实出的一处债**：`level_map.chandelier` 与 `exit_guard` 的趋势死亡线
  （布防后最高收盘 − 3×ATR20 / parabolic 5d×0.92）**是同族的两处独立实现**，用途不同
  （画线体检 vs live 强制）。用途分开是**有意的**，但**两处参数是否一致我没有核**。
  ⇒ 登记为待核（不是缺陷，是未验证）。
- **syllabus 的 Invalidation 清单**（Structure Failure / Failed Breakout / Support Loss /
  **Relative Strength Breakdown**/ **Momentum Failure**/ **Volume Failure**）：
  前三个我们有；**后三个不进**——无出处，且会与 `exit_guard` 抢管辖权
  （`trend-exit-system` 的教训：退出权归机械线，不接受叙事豁免）。

#### 阶段 13 · Exit / Take Profit

- **现状（本仓最完整的一层）**：利润棘轮（+50% → 保本地板；+100% → 回吐上限 1/3）、
  趋势死亡线（3×ATR / parabolic 5d×0.92）、G-01 档位、期权亏损侧 overlay
  （S1 premium −25% / S2 时间 stop (20,25) / S4 DTE 地板 90 / S3 advisory / S5 underlying stop = OR 双防线）、
  重进协议、**宏观不豁免**。
- **syllabus 补的三个，逐个裁**：
  - `Measured Move`（Wyckoff P&F 计数定目标）→ `[原典]` 无独立检验 ⇒ **不进**；
  - `Chandelier Exit` → **已有实现但未接 exit_guard**（见上，两处独立实现，用途不同）；
  - `Momentum Divergence` 作为退出触发 → 与阶段 8 同理（背离两票实为一票）+ 无出处 ⇒ **不进**；
  - `Time-Based Exit` → **已有**（期权 S2）。正股侧无时间 stop，且是有意的（趋势系统）。
- **动作**：无新增。**趋势衰竭**一节（blow-off / parabolic extension / exhaustion gap）
  已由 parabolic 体系 + `tape_check.climax_fade` + 棘轮覆盖。

---

### VI. Validate Edge

#### 阶段 16 · Technical Analysis Quantification

- **要什么**：把「强势突破」这类自然语言变成 `Rule-Based Definition`。
- **这是本仓的核心能力**。对照表（syllabus 点名的 12 项检测器）：

| syllabus 项 | 本仓状态 | 真源 |
|---|---|---|
| Swing Point Detection | ✅（⚠️ 带 Lo 2000 的方法论债，见第二阶段） | `technical.swing_points` |
| Trend Detection | ✅ | `technical.swing_structure` + `episodes.gate_path` |
| Support/Resistance Detection | ✅ | `level_map.cluster_pivots` / `top_hvn` / `find_lvn` |
| Breakout Detection | ✅ | `regime_transition`（收盘 ×N 日 + RelVR + `fastlane_k=0.5`） |
| Volume Expansion Detection | ✅ | `volume_leg` RelVR |
| Pullback Detection | ✅ | zone 带 + `wl_zone_touch` |
| Relative Strength Calculation | ⚠️ 有实现无消费者 | `technical.rs_series` |
| Momentum Detection | ⚠️ 有实现，冗余未压缩 | `rsi` / `macd` |
| Volatility Regime Detection | ❌ **缺**| — （阶段 6 的 `range_contraction`） |
| Divergence Detection | ⚠️ 两个检测器实为一票 | `detect_rsi_divergence` / `detect_macd_divergence` |
| Pattern Detection | ⛔ 有意不做 | — |
| Signal Scoring | ⛔ **已 FAIL 不重建**| `sss_backtest` |
- **一条要写进 canon 的观察**：本仓「强势突破」的机械定义现在是
  `收盘 > gate + 0.5×ATR20`（幅度）**且**`RelVR ≥ 1.2`（量）**且**收盘 ×N 日（时间）**且**①修复域内
  ——**四腿**，每一腿都有一次裁决在案。这就是 syllabus 第十六阶段要的东西的完成态样例。

#### 阶段 17 · Backtesting & Validation

- **本仓已有（比 syllabus 列的更严）**：
  判据先于数字 **commit**（`ladder_timing` 的 `8d49f84` / `sss` 的 `448f5d9` / `stop_cost_guard` 的
  `00de7f2`+`b0ce609`，后者**锁定前代码拒跑，退出码 2**）；Kish 有效样本；**ticker × 日期双向 cluster bootstrap**；
  守卫（止损率 / p10 / 样本量）；**守卫的量纲必须跟着止损模型走**（`stop_cost_guard` 结案原则）；
  预注册回退条款；变异测试确认守卫会红；一行 ledger。
- **syllabus 点名而我们**缺**的四项**：
  1. **多重比较校正**（FDR / Romano-Wolf step-down）——`price_action_framework_design` §8.4 c4
     **要求了但从未实现**。这是交互事件回测（族大小 4 事件 × 2 side × 3 锚类 × 3 信息环境分层 ≤ 72 格）
     **开跑前的必要件**；
  2. **Walk-Forward / OOS 划分约定**——本仓目前是全样本 + 复测窗（+12 个月），
     没有 in-sample/out-of-sample 的显式切分惯例；
  3. **交易成本与滑点**——只有 `stop_cost_guard` 的 `SLIP` 一处，没有统一约定；
  4. **Monte Carlo / 参数敏感性**——敏感性我们做（`interaction_census` §11.2b 四旋钮），
     Monte Carlo 没做过。
- **动作**：把这四项写进 §9 的**统一预注册模板**，其中 (1) 是 B2 的**阻塞前置**。

#### 阶段 18 · Feature Redundancy / Signal Independence

- **要什么**：Feature/Signal Correlation、多重共线性、信息重叠、冗余指标、正交信号、增量预测价值；
  并按 Price-derived / Volume-derived / Volatility-derived / Structure-derived / Cross-sectional 五类划分。
- **本仓状态：已做且有数字**（§4.3）。syllabus 的五分类与我们的五轴**几乎一一对应**——
  唯一差别是我们把 Structure-derived 拆成了「位置（A）」与「前置状态（C）」，
  而它们在冗余上确实是两回事（位置是当下的线，前置是历史的路径）。
- **动作**：`indicator_redundancy.py` 已在 `UNTESTED` 名单登记（「只算相关不算收益 —— 纯度量脚本」）。
  新增 **B5**：**量族 lift 体检**——OBV / A-D / CMF / MFI vs RelVR 的有效维数与提升度，
  **只算相关不算收益**（同一模式，零风险、零自由度）。这是决定阶段 7D 要不要进的**唯一**前置。

#### 阶段 19 · 形成完整 Technical Engine

**syllabus 的目标架构 vs 本方案落地后的实际架构：**

```
syllabus:   Context → Regime → Structure → Setup → Trigger → Vol/Price 确认
            → Mom/Vol 确认 → Technical Signal Score → Confidence
            → Position Sizing → Pyramiding → Risk → Exit
                                  ↑
                        这两格已被本仓数据否掉 (§1)

本方案:     ┌─ 轴 A 位置 (VP/HVN/LVN/pivot/卡片线/AVWAP/价值区)
            ├─ 轴 B 力  (RelVR)          ┐
   日线 ────┼─ 轴 B′果 (range_n/close_pos)┼→ 交互事件 E1/E2/E3/E4 (T 候选 → T+k 确认)
   +周线 ──┼─ 轴 C 前置 (trend/gate_path/current_regime/周线/range_contraction)
            └─ 轴 D 确认 (收盘×N + RelVR)  ┘        │
                    ↑                                │
            轴 E 横截面 (RS/板块) ── 候选 ───────────┤
                                                     ▼
                            ┌────────────────────────────────────┐
                            │  三个出口 (§1.3), 没有第四个        │
                            ├────────────────────────────────────┤
                            │ ① [FYI] postclose 任务  零授权效力  │
                            │ ② entry_classification 二值判别式   │
                            │    (改它 = 改授权, 须过降级回测)    │
                            │ ③ 预注册回测的事件样本              │
                            └────────────────────────────────────┘

授权/仓位/风险/退出 = 既有引擎, 本框架**不接管**:
   entry_cap 六臂 → ROUND_RISK_FRACS 三轮 → binding_stop → exit_guard 退出栈
```

**一句话**：syllabus 想建的是一个**打分器**；本仓能承受的是一个**事件记录器 + 分层器**。
两者的差别不是野心，是**样本量**（本书 12 票、Kish 常在 70–200）。

---

### 阶段 15 · Technical + Fundamental Integration（单列，因为它与 canon 有分叉）

- **syllabus 说**：`Fundamental → What to Buy`；`Technical → When to Buy`；
  形成 `Fundamental Conviction × Technical Confirmation`（并自陈「不要绝对化」）。
- **本仓 canon（v3 技术面优先，用户  directive）说**：
  **入场授权（gate/regime/zone/cap/档位）= 纯价格结构**；财报 · thesis · 研报 · 宏观消息
  **一律降为情报层 [FYI]**，判断权归 用户。唯一 sizing 层例外 = 财报**反应日 T+0**缩仓
  （`core/earnings.py`，改的是「那天能多大」不是「能不能进」）。
- **🚨 这是分叉，不是重合**：v3 比 syllabus **更极端**——它连 "what to buy" 的**授权**也归结构
  （基本面只作为 用户的判断输入，不进机器授权链）。
- **动作（Q8）**：确认**以 v3 为准**，并在本文档登记这条分叉。
  理由是 v3 是本仓的**已批裁决**且晚于任何相反表述；
  syllabus 是学习材料，**学习材料不覆盖裁决**（同 ledger 的 precedence 原则：以更晚的已批裁决为准）。

---

## 6. 数据可得性审计

| syllabus 要的 | 数据源 | 现状 | 代价 / 风险 |
|---|---|---|---|
| 多周期 K 线 | OpenD `get_kline.py --ktype`（1m…月线）；日线可精确重采样成周线 | 日线 ✅ / 60m ✅（tape_check）/ 周线 ❌ | **重采样零成本零风险**|
| 板块成分与趋势 | `get_plate_list.py` / `get_plate_stock.py` / `get_owner_plate.py` | **已装未用**| 需要一次成分拉取；成分会变（漂移风险，须落 as_of） |
| 资金流 | `get_capital_flow.py` / `get_capital_distribution.py` | **已装未用**| 主力/散户拆分是 LMSW02「谁在放量」的**直接代理**（比价差/市值更近）——但**无学术检验**，且口径不透明 ⇒ `[民间]`，只能当分层观察 |
| 盘口 depth | `get_orderbook.py` / `get_broker_queue.py` | **已装未用**| 这是 Kavajecz & Odders-White 测的**真东西**（我们平时只有 depth 的影子）——但**只有快照无历史**，不能回测，只能 live 观察 |
| 空头数据 | `get_short_interest.py` / `get_daily_short_volume.py` | **已装未用**| 低频（双月）；`days to cover` 可算 |
| 期权 IV / 期限结构 / skew / OI | `get_option_volatility.py` / `get_option_chain.py` / `iv_logger` | ATM IV ✅ 每日落库；期限结构/skew ❌ | IV 序列**不可重拉必须入 git**|
| VIX / 宏观 | FRED（`macro_fetch`） | ✅ | |
| % Above 50DMA（宽度代理） | `klines_deep` 167 票自算 / 或板块成分自算 | ❌ | ⚠️ **klines_deep 不是指数成分**（是去相关挑的池）⇒ **有偏代理，必须播报口径**；且**不入 git 会过期**（BACKLOG 已登记无 SLA） |
| A/D Line、New High/Low、McClellan、TRIN | 需全市场成分 | ❌ | 代价高、收益不明 ⇒ **不做**|
| Put/Call Ratio、AAII、Fear&Greed | CBOE / AAII / CNN | ❌ | 无稳定免费源；**不做**|
| Dealer Gamma / GEX / Zero Gamma | SpotGamma 类 | ❌ | canonical 已写明：只有 **OI 粗代理**（看不到 dealer 方向），**别当精确点位，不放宽 size**|
| 换手率 / 流通股本（LMSW02 分层） | `get_stock_info.py` / `get_valuation_detail.py` | 未核 | 分层可退用**市值**代理（design §2.4.1 已如此计划） |

> **数据纪律不变**：价格/K 线的实时真源 = **OpenD**；Yahoo 只进 `klines_deep` 回测池，
> **永不进实时决策 / live 缓存 / 卡片**（用户  批的 research-only 例外）。

---

## 7. 建设队列（按 收益 / 自由度 比排序，每一步都可以停）

| # | 项 | 产出 | DoF | 前置 | 停止条件 |
|---|---|---|---|---|---|
| **B0-1**| syllabus 词表 → 本仓词表映射（附录 A） | 防新词入侵 | 0 | — | — |
| **B0-2**| 三个 K 线检测器 docstring 加证据等级 + 出处 | 让下一个人读得到 | 0 | — | — |
| **B0-3**| `pull_breadth_history.py` 命名消歧一行 | 防误读为市场宽度 | 0 | — | — |
| **B0-4**| `volume_profile` 输出加 `vah`/`val`（70% = 定义常数） | 轴 A 的坐标系补全 | 0 | — | — |
| **B0-5**| 现有唯一 setup 的六要素表格化（附录 B） | 新 setup 的举证模板 | 0 | — | — |
| **B0-6**| `swing_points` docstring 登记 Lo 2000 方法论债 | 已知债不失传 | 0 | — | — |
| **B1**| 周线重采样 → 轴 C 分层变量 | 唯一有顶刊证据的"结合" | 1 | B0 | 若事件回测分层无可区分性 → 停在观察层 |
| **B2**| 交互事件层 v0.3（承接 v0.2；补 `anchor_dims` + `last_rejection_date`；锁参数；**先实现 FDR**） | 本框架主体 | 见 §8 | **Q2/Q3 裁决 + FDR 实现**| 回测 FAIL → 不接线（同 `sss_backtest` 处理） |
| **B3a**| Context 观测层（板块宽度 / 书内宽度 / VIX，**只播报不设阈值**） | 补最大的真缺口 | 0 | B0-3 | 8 周后若读数与决策零关联 → 撤 |
| **B4**| 轴 E 横截面 RS（`rs_series` + 冻结板块表） | 结构上最可能正交的一族 | 1–2 | B5 通过 + 预注册 | lift 体检显示与已有读数 ρ 高 → 不开 |
| **B5**| 量族 lift 体检（OBV/A-D/CMF/MFI vs RelVR，**只算相关不算收益**） | 决定阶段 7D 要不要进 | 0 | — | — |
| **B6**| `range_contraction` 读数 → 轴 C | VCP/squeeze 的可测量核心 | 1 | B2 分层框架就位 | 同 B1 |

**顺序理由**：B0 全是零自由度（可立即做，不占预算）；B5 也是零自由度且能**关掉**一整族的讨论；
B1/B6 是分层变量（挂在 B2 的回测里，不单独立项 = 省族大小）；B3a 起步 0 DoF；
B2 是唯一的大件，且它有一个**硬阻塞**：FDR 校正没实现之前不该开跑（族大小已经到 72 格）。

---

## 8. 解卡：v0.2 §12.1 五个「拍的」参数的建议

> `price_action_framework_design` §12.1 的表卡了一轮。这里给**具体建议值 + 理由**，
> 让 用户 只需要回答「批 / 改哪一个」，而不是从头拍五个数。**建议走 A（一次锁死只跑一组）**——
> 理由是 §4.5 的 7,846 条先例：允许"先做敏感性再选一档"本身就是一次挑选，
> 而「事件率最平稳」不是一个与收益无关的中性判据。

| 参数 | v0.2 状态 | **本方案建议**| 理由 |
|---|---|---|---|
| `effort_lo`（RelVR 低力） | 拍的 0.8 | **0.8 锁死**| 与已裁的 `effort_hi=1.2` 对称（1/1.2≈0.83）。**理由是对称性不是最优性**——写下来，别假装它有证据 |
| `result_hi / lo`（range/ATR20） | 拍的 1.0 / 0.6 | **1.0 / 0.6 锁死**| 1.0N = 定义上的「平均日波幅」，是**尺度的自然分界**不是选出来的数；0.6 是 1.0 的补 |
| `close_weak / strong` | 拍的 0.35 / 0.65 | **改为 0.40 / 0.70，与 `tape_check` 对齐**| **一个词只属于一层**的同款理由：同一个「收盘位」概念在 60m 与日线上用两套阈值，
迟早出现「tape 说强收、事件说不强收」的双线并存（形状 #8）。对齐的成本是零，不对齐的成本是将来一次归因事故 |
| `k_confirm` | 半拍 1 | **1 锁死**| 本仓「收盘 ×N 日」的最小值；Lo 用 d=3 是**形态**的检测滞后不是确认。**回测须在 k=2 报一次稳健性（报告用不是调参用）**|
| `debounce_days` | **拍的 10（最需要拍板）**| **10 锁死**（Q3） | 它是**主旋钮**（3→65.1 / 5→50.9 / 10→37.8 / 20→28.9 事件/票/年，近似反比）。
选 10 的理由是注意力预算，**不是**统计理由；选 20 会把样本量砍 24%，而 H1 **已经功效不足**（见下） |
| 锚合并半径 | 半拍 0.5N | **0.5N 锁死**| 比 `tol`(0.25N) 宽一档；实测把卡片线与 pivot 簇正确并成一条 |
| `flip_lookback`（E4） | 拍的 20 | **20 锁死**| 纯选的。20 ≈ 一个月交易日，与 `profile_bars=60`/`pivot_bars=250` 的量级阶梯一致 |
| **锚名额分配**（D5） | 未定 | **card 恒进 + pivot ≤3 + profile ≤2**| 实测不分配时 pivot 占 **79.1%**、profile 仅 8.9%，**卡片线可能被挤掉——那是最不该丢的一类**。分配后 profile 升到 13.1% |
| **`anchor_dims` / `last_rejection_date`**（D6） | 未做 | **做**（「多维确认」canon 的要求） | 「一条线之所以是地标，是因为多个互相独立的读数落在同一个数上」+「陈旧的拒绝 ≠ 现在的卖方」。**不算这两个字段，事件记录就没法验证 canon 的核心主张**|

### 8.1 功效不足是已知的，处理方式必须在跑之前定（D4）

Osler (2000) 给了效应量先验：**公布 S/R 位 bounce 60.8% vs 随机位 56.2% = +4.6pp**
（16 个「机构 × 货币」组合**全部**同向，除 3 个外都在 5% 显著）。
按二项检验 p≈0.58、Δ=4.6pp、α=0.05、power=80%：**每臂需 ≈1,800 事件**。
而 H1 目标格全池只有 **2,445**个原始事件，**Kish 有效 N 会明显更低**。

**建议（三选一里选第三+第一的组合）**：
1. **接受「不裁决」是合法结论**（`earnings_gate` 先例：三桶全 Kish<30 → 不裁决，现行规则维持）；
2. **同时**把窗口从 10 年拉到 20 年（`klines_deep` 已能到 2005，deep 池够）——这是**唯一不增加族大小**的功效改善手段；
3. ❌ **不**放宽目标格来凑样本（那会增加族大小，与 c4 的多重比较校正互相抵消）。

---

## 9. 统一预注册模板（本仓惯例 ∪ syllabus 第十七阶段）

任何新的 `*_design.md` 在跑第一个数字之前，必须回答这 12 问（前 8 条是本仓现行做法，后 4 条是本 syllabus 补的）：

| # | 问题 | 本仓先例 |
|---|---|---|
| 1 | 假设是什么？**它的对照臂是什么、只隔离哪一个自由度？**| Chang-Osler：形态「赚钱但被更简单规则支配」；ledger **A7**：对照臂混淆两个自由度 → 结论无法归因 |
| 2 | 样本池、单位、时间窗？ | 事件 ≠ ticker-day；`klines_deep` 而非 live 32 票 |
| 3 | 有效样本怎么算？**Kish N ≥30 否则不裁决**| `earnings_gate` |
| 4 | bootstrap 怎么聚类？**ticker × 日期双向**| `gate_backtest2` |
| 5 | 主度量与**量纲**？（风险 N 口径可横比） | `gate_path_taxonomy` |
| 6 | **守卫**是什么？**守卫的量纲跟着止损模型走**| `stop_cost_guard` 结案原则 |
| 7 | 判据先于数字 **commit**了吗？ | `8d49f84` / `448f5d9` / `00de7f2`（**锁定前拒跑，退出码 2**） |
| 8 | 回退条款 / 「不裁决」的处理写了吗？ | `options_exit_overlay` 预注册回退条款 |
| **9**| **族有多大？多重比较怎么校正？**（FDR q=0.10 或 Romano-Wolf；**报告全族不许只报显著的**） | ⚠️ **本仓尚无实现**← B2 的阻塞前置 |
| **10**| **样本内/样本外怎么切？**（或声明「全样本 + 复测窗」并说明代价） | ⚠️ 本仓惯例是全样本 + 12 月复测窗，**没有显式 OOS**|
| **11**| **交易成本与滑点**怎么建模？ | 只有 `stop_cost_guard` 的 `SLIP` 一处 |
| **12**| **功效分析**做了吗？（效应量先验从哪来、每臂需要多少样本） | ⚠️ 本轮 Osler +4.6pp 是本仓**第一次**有效应量先验 |

---

## 10. 输出面

- **出口只有 §1.3 的三个。**新增的 [FYI] postclose 任务（建议 SGT 04:35，排在 `gate-ladder-report` 04:20 之后），
  抬头固定 `[FYI] 交互事件 — 观察层，零授权效力`。
- **明确不进**：gate ladder report（那张表的价值在于「只报事实不发授权」这条线很干净）、
  brief 的授权段、Today 组、提醒线画线。
- **播报纪律**：会驱动动作的文本必须**代码渲染 + 原文推送**，不许散文改写
  （`heartbeat` 推 `fired_cards` 原文的同款规矩——散文版把「预算已用尽的 zone」播成加仓信号，
  并引用了比卡片 stop 松 6.2% 的 trail 当风险线）。
- **密度**：live 预览实测 **3.63 事件/日**（12 张卡 × 20 交易日），超 §11.1 定的 ≤2/日。
  **不为了达标改定义**（改定义 = 拿显示需求去改样本量）；按票分块后是 3.42 块/日，仍是一屏内的东西。

---

## 11. 失败模式登记（本仓存量教训 → 本项目的具体形状）

| 存量形状 | 本项目最可能怎么犯 |
|---|---|
| **规则不进代码 = 不存在**| 四轴纪律 / 一票制写在本文档里，但事件记录没有 `axis` 字段 ⇒ 下一个人照样堆指标 |
| **没有出处的规则进了代码 = 更糟**（看起来有依据） | 把 syllabus 的某个形态名直接实现，因为「syllabus 里写了」——**syllabus 是学习地图不是证据**|
| **不会触发的检查比没有检查更危险**| Context 观测层加了阈值但数据链断（`index_risk` 死代码半年的同款）⇒ **加告警后必须端到端验证会响**|
| **两条线并存，报了不 binding 那条**| `close_pos` 在 60m（0.40/0.70）与日线（0.35/0.65）用两套阈值 ⇒ §8 建议对齐 |
| **同概念双判别式**| 「触碰」在 census 里是 `high ≥ L`，若 live 实现写成 `high ≥ L − tol` = v0 的原始错误复活（那次事件率 298/票/年） |
| **自变量与因变量共用一份数据**| 锚（profile/pivot）必须用**事件日之前**的 bar（`poc_window` round-1 的 AUC 0.812 就是这么假的） |
| **盘中未完成 bar 当收盘**| 交互**候选**可以盘中算，**事件**只能收盘后落；T+k 确认同样只用完成 bar |
| **幂等守卫吃掉更准的写入**| 若 [FYI] 任务与 heartbeat 共用状态文件，边沿去重会让两者互吃冷却（`nav_snapshot` first-write-wins 的同款） |
| **裁决 vs live 分叉**| 引用「gate = 保险不是 alpha」「四类全否」「SSS FAIL」时**先 grep live 参数名 + 查 ledger**，不抄行号 |
| **手工步骤 = 这一步实际上不存在**| 板块冻结表的维护（新票无映射）⇒ 必须有降级播报，不能靠人记得去加 |

---

## 12. 待 用户 裁决（编号版）

| # | 题目 | 选项 | 我的建议 | 不定的后果 |
|---|---|---|---|---|
| **Q1**| 自由度预算机制（§2） | 批 / 改额度 / 不设 | **批，季度 ≤3**（它是约定不是实证，归你拍） | syllabus 的 400 概念没有闸门 |
| **Q2**| v0.2 五个「拍的」参数（§8） | **A**一次锁死只跑一组 / **B**先做纯信号量敏感性 | **A**，锁定值见 §8 表 | 事件回测开不了跑 |
| **Q3**| `debounce_days` | 10 / 20 | **10**（20 会砍 24% 样本，而 H1 已功效不足） | 事后再动 = 事后改 N |
| **Q4**| 指标四条规矩（§5 阶段 8） | 采纳 / 改 | **采纳**| 阶段 7 的 80+ 指标没有机械门槛 |
| **Q5**| B0 六项零自由度动作（§7） | 现在做 / 等 | **现在做**（零行为改变，纯记账/注释/定义常数） | 都是「下一个人会重踩」的债 |
| **Q6**| 周线作为**分层变量**挂进事件回测（不单独立项） | 是 / 单独立项 / 不做 | **是**（省族大小，且这是唯一有顶刊证据的"结合"） | 要么漏掉最有价值的一维，要么多付一个族 |
| **Q7**| 阶段 14「regime → strategy selection」明确不做 | 确认 / 保留可能 | **确认不做**（`gate_path_taxonomy` 四类全否是同一形状的先例） | 将来有人按 syllabus 建切换器 |
| **Q8**| syllabus 阶段 15 vs v3 的分叉 | 以 v3 为准 / 讨论 | **以 v3 为准**（学习材料不覆盖裁决） | 下一个人以为 syllabus 松动了 v3 |

---

## 13. 诚实边界（这份文档没做到的）

1. **本轮没有读新文献。**§4/§5 引用的 29 条出处与读取层级全部来自
   `price_action_framework_design` §10 的上一轮阅读（其中 6 条读了全文，其余是摘要/二手综述）。
   **本轮做的是把 syllabus 全文逐段过一遍并对到本仓真源**，不是新的文献工作。
2. **本轮没有跑任何数字。**§1 / §4.3 / §8 的所有读数都是已有回测与体检的**引用**，不是新测量。
3. **DoF 预算（§2）没有任何验证**，它是治理约定 `[约定]`，不是实证结论。
4. **一处未核**：`level_map.chandelier` 与 `exit_guard` 趋势死亡线的参数是否一致（§5 阶段 12）。
5. **一处已知债只登记未处理**：`swing_points(wing=2)` 在原始价上取极值 = Lo 2000 明确反对的做法（§5 第二阶段）。
   它影响 `trend` → 授权，**要动就整体重跑 `structure_downgrade_backtest`**。
6. **syllabus 的部分条目我给的是「不做」而不是「测过不行」**—— 两者不同。
   凡本文写「不做」的，理由都是**自由度成本 / 无出处 / 已有覆盖**，不是「已证伪」。
   将来若有人拿出证据，重开的门是开着的（走 §9 的 12 问）。
7. **阶段 7 的族级裁决（"一票制"）是基于本仓 60 票 / 55,732 行的冗余体检**，
   它测的是**相关性**不是**增量预测价值**——严格说「同轴同源一票」是一个**保守的近似**，
   不是「它们一定没有增量」。要证明某个指标有增量，正确做法是 lift 体检（B5）+ 预注册回测。

---

# 附录 A · syllabus 词表 → 本仓词表映射（**防新词入侵**，B0-1，）

syllabus 有 ~400 个术语。**没有映射表 = 每次讨论都可能长出一个新词**，
而本仓  已经为「一个词打三份工」（confirmed）烧过四轮对话。

**读法**：左列出现在讨论里时，**用中列的既有词**回答；右列写明为什么不新增。

| syllabus 词 | 本仓对应物（真源） | 说明 |
|---|---|---|
| Bull / Bear / Correction / Rally | `technical.swing_structure` 的 `trend ∈ {up, down, transition}` | 我们只有三态，且它被 `structure_downgrade_backtest` 背书过 |
| Trend / Range / Consolidation | 同上 + `technical.range_contraction`（波动收缩比） | 「震荡」不是一个 regime 名，是**波动收缩 + trend=transition**两个读数 |
| Expansion / Contraction | `range_contraction`（>1.15 扩张 / <0.85 收缩） | 数值，不是标签 |
| Accumulation / Distribution | Wyckoff `[原典]` 词汇 → **四类交互事件 E1–E4 的序列**（design §6.7） | **序列层本轮明确不做**（参数空间爆炸点）；不引入为 regime 名 |
| Reaccumulation / Redistribution | 同上 | 同上 |
| Risk-On / Risk-Off | `regime_score.q_a_regime` 的三态轮动（INTRA-MARKET ROTATION / BROAD EXIT / MIXED） | 已有实现，别新造 |
| BOS (Break of Structure) | **E2 穿越-被接受**| SMC 词汇，机械内容已被四类事件覆盖 |
| CHoCH / MSS | `trend` 的状态变化（up→transition→down） | 同上 |
| Liquidity Sweep / Stop Run | **E3 穿越-被拒（陷阱）**+ Osler 止损级联机制（design §2.1） | 同上 |
| Swing Failure Pattern | **E3**| 同上 |
| Failed Breakout / Fakeout | **E2 的否定 = E3**| 镜像关系由构造给出，不需要额外形态 |
| Reclaim | **E4 触碰-被接受（易位）**/ gate 收复 | |
| Breakout Retest / Support Retest | **E4**| |
| Undercut and Rally / Shakeout | **E3（下方 = Wyckoff Spring）**| |
| Order Block / Supply-Demand Zone | `level_map` 的 HVN 带 / pivot 簇 | 「有 depth 证据的线」的既有实现 |
| Fair Value Gap (FVG) | `level_map.find_lvn` 的 LVN 带 | **已裁**：FVG = 无量数据的 LVN 几何代理，我们用真分布直接找 |
| Price Acceptance / Rejection | **E2 / E1**；拒绝计数 = `level_map.level_interactions` | ⚠️ LVN（有没有库存）与 rejection（有没有卖方）**是两个维度**，必然可同真 |
| Value Area / VAH / VAL | `technical.value_area()`（附录 C， 新增） | 70% 是拍卖理论定义常数，DoF = 0 |
| POC / HVN / LVN | `technical.volume_profile` + `level_map.top_hvn` / `find_lvn` | |
| RVOL（相对成交量） | **RelVR**= 个股量比 / max(基准量比, 0.4)，基准 半导体→SMH 其余→QQQ |  裁决口径，**不是绝对量比**|
| ATR / Volatility Regime | `technical.atr`（**ATR20 算钱 / ATR14 读盘**，口径分工不可混） | |
| Squeeze / NR4 / NR7 / BB Width / VCP | `technical.range_contraction` **一个读数**| 「把形态名降解成可测量」——同轴同源一票 |
| Higher Timeframe / MTF | `technical.resample_weekly`（日→周，自然周，丢未走完那周） | 只做日+周，**不引入 4H/15m/5m**|
| Relative Strength（非 RSI） | `technical.rs_series` / `chart_read` 的 20 日相对基准收益 | 轴 E，**FYI-only**|
| RSI / Stochastic / CCI / Williams %R / %B / MA 偏离 | **一票**：「离最近的均值有多远」 | 实测 ρ≈0.89–0.95（`indicator_redundancy`） |
| MACD 背离 / RSI 背离 | **一票**：「动量背离」 | 实测共现提升 ×7.25 |
| Chandelier Exit | `exit_guard` 趋势死亡线 / `level_map.chandelier`（同公式同常数，pin 测试钉） | |
| Measured Move | **不引入**| Wyckoff P&F 计数 `[原典]`，无独立检验 |
| Signal Score / Confidence Score | **不引入**| `sss_backtest` 已 FAIL（§1） |
| Market Regime → Strategy Selection | **不引入**| 乘法自由度（§5 阶段 14） |
| Trendline / Channel | **不引入**| 两点定线，「选哪两点」无判据 |

**用法**：有人（包括我）在讨论里用了左列的词而中列已有对应物 → **换成中列的词**。
真要新增一个词，先答：它对应哪个**可观测量**？谁读它？改了谁的行为？（= §9 的 12 问）

---

# 附录 B · Setup 举证模板（B0-5，）

syllabus 第九阶段要求每个 Setup 都有六要素。本仓**只有一个 setup 家族**，
但它六格齐全**且每格都有代码强制点**—— 这是模板，不是描述。

## B.1 现存唯一 setup：gate 转移 + zone 回踩

| 要素 | 内容 | **强制点（改这格 = 改代码）**|
|---|---|---|
| Context | `current_regime{name, cap_pct_nav, participation, since, source}` | `schema` 同生同死（有 zones 必有 regime，双向 error） |
| Preconditions | cap > 0 · 结构未破 · zones 未过期 · 无 `pending_repaint` · 在 Today 组 | `entry_guard` VETO / `today_group_sync` |
| Setup | 卡片 zones（z1/z2/trim/no_chase/stop）+ `next_regime.entry_gate` | `has_next_gate` + `gate_coherence` + 单调性检查 |
| Trigger | zone 触碰（提醒线 z1 / `wl_zone_touch`）或 收盘越 gate | `price_reminder_sync` / `regime_transition` |
| Confirmation | 收盘 ×N 日 **且**RelVR ≥ 1.2 | `regime_transition.volume_leg` |
| Invalidation | `stop_level` + `invalid_if` + `binding_stop`（卡 stop / 棘轮地板 / 趋势死亡线取 max） | `exit_guard` |

## B.2 新 setup 提案必须填满的空表

**填不满的不进讨论。**每格的举证标准：

| 要素 | 必须回答 | 不合格的样子 |
|---|---|---|
| Context | 它在**哪个 regime**下有效？其余 regime 下呢？ | 「趋势市场里好用」（没有可判定的 regime 字段） |
| Preconditions | **谁没有资格**用这个 setup？判据是哪个字段？ | 「质量好的票」 |
| Setup | 价位从哪来？**anchor_basis 是什么**（多维收敛几维）？ | 整数关口 / 固定偏移 / 单一维度 |
| Trigger | 触发是**收盘**还是盘中？盘中的话，未完成 bar 怎么处理？ | 「突破就买」 |
| Confirmation | 确认口径（×N 日 / 量腿）？**与既有口径一致吗**？ | 悄悄放宽任一项 |
| Invalidation | **什么观测能推翻它**？stop 在哪？谁强制？ | 「跌了就扛着」 |
| 增量 | 它**打赢**哪条更简单的既有规则？（Chang-Osler：形态可盈利但被更简单规则支配） | 「回测赚钱」（没有对照臂） |
| 自由度 | 引入几个可调参数？族多大？多重比较怎么校正？ | 「就一个阈值」 |

⚠️ 最后两行是本仓踩出来的：**对照臂只隔离一个自由度**（ledger A7）+
**族由构造封顶**（7,846 条规则先例）。没有这两行的提案，回测结论无法归因。

---

# 附录 C · 价值区 VAH / VAL（B0-4， 落地）

**真源 `core/technical.value_area(profile, frac=VALUE_AREA_FRAC)`**，
`VALUE_AREA_FRAC = 0.70` 是**拍卖理论的定义常数**（Steidlmayer / Market Profile），
**不是我们调的参数 ⇒ DoF = 0**（改它 = 离开那套词汇表，那时它才成为一个自由度）。

**为什么要它**：我们已有 POC / HVN / LVN，但缺拍卖理论的**坐标系**——
「区内 = 平衡（均值回复语义）/ 区外 = 失衡（趋势语义）」。
一根 bar 的含义取决于它相对价值区的位置；这是位置轴（轴 A）的底座，**不是一个信号**。

| 项 | 值 |
|---|---|
| 算法 | 从 POC 出发，每步并入上下相邻桶里**成交额大的那一侧**，直到累计 ≥70%（Market Profile 标准做法） |
| 输入 | `volume_profile` 的输出（60 根，三角铺开口径） |
| 输出 | `{poc_lo, poc_hi, val, vah, covered_pct, n_buckets}` |
| 消费者 | 目前只有 `chart_read.walls_block` → ② Structure 段的「价值区 X–Y　现价**区内/区上/区下**」 |
| 认识论 | `[原典]` —— Market Photo/Profile 的**统计检验我没有找到**（找到的都是教学材料）。**采用它的坐标，不采用它的胜率。**|

**加法不改形状**：`volume_profile` 有 5 个消费者（`level_map` 的 gate 画线 / `chart_read` /
三个回测），所以这是**新函数**而不是改它的返回结构 —— 测试
`test_value_area_does_not_change_volume_profile_shape` 钉住这一点。

🚫 **不许拿 VAH/VAL 当 gate 锚**：`hvn_gate`  已裁
「gate 锚从 pivot 簇换成 HVN 上沿 = **不采纳，平手保留 incumbent**」——
换个名字再来一次是同一件事，要做就重开那份预注册。
