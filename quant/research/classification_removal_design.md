# 分类轴移除设计（probe/confirmed → regime cap 二值 × 轮预算）

**指令 1（用户  逐字）**：「我决定尝试移除系统中的 probe 仓位概念 只要给出 regime
给出 cap 就以轮 r1 r2 r3 进行参与」

**指令 2（同日，动机 + 加码裁决，逐字要点）**：「这两周的交易看下来 在达成解锁 probe 仓的那个
gate 后 以 zone 入场 基本到现在都是处于浮盈 且基本从未触发止损 也很少会回到 gate 以下（包括
那些在 today 的也是如此）所以可以说明 这个原本解锁 probe 的 gate 本身就具有足够高的确定性
有 at risk 控制风险的情况下 我们可以稍微大胆一点 以轮预算方式直接入场 **不再有 2% 的限制**……
第一次给 cap 就允许以 r1 入场 这样可以在风控和 alpha 间取得一个平衡 容易更早抓住主升浪……
结构降级反转立马 cap 0 即可」

**一句话**：cap 二值化 —— 要么 0（VETO），要么给（= 满额名义天花板 `NOTIONAL_CAP` 0.40）；
给了就按轮预算 R1/R2/R3（2.5/1.5/1.0% NAV risk）递减入场。**修复 gate 收复后不再有 2%
中间档**—— 这不是词汇收敛，是有意的 size 放大（量化标价见 §3b），风控交给 at-risk /
轮递减 / 组合五臂。probe / confirmed / early_risky 分类轴整体退役。

**状态**：✅ **当日接线完成**。D1/D3 = 指令 2 拍；D2 = 指令 3 拍（期权 R2 起）；
D4/D5/D6/D7 按推荐执行。1332 测试全绿 + 变异 2/2 红（schedule 值 / VETO 判别式）+
live 二值不变量（`test_live_current_regime_caps_are_binary`）。存量 6 卡已迁移
（AAOI/INTC/MRVL/LITE/MU/NVDA 0.02→0.40）。接线首日冒烟：AMZN 轮预算 binding
$5,707、LITE 财报缩仓 ×0.24 照咬、**NVDA/META 被结构 VETO 当场拦截**。
ledger 已有行（含对 08-10 两条的知情推翻标注）。

---

## 1. 现状审计 — 分类轴承载的 7 个职责（全仓 grep）

代码面 18 模块 / 测试面 19 文件 / 文档面 15 文件涉及。会动钱的职责：

| # | 职责 | 真源 | 消费者 |
|---|---|---|---|
| J1 | 分类天花板（第①臂的 base） | `sizing.CAP_FRACS` {confirmed 0.40, probe 0.02, early_risky 0} | `effective_cap_frac`（与 regime cap 取小）→ `entry_cap` |
| J2 | 轮预算 schedule（第②臂） | `sizing.ROUND_RISK_FRACS` {confirmed (2.5,1.5,1.0)%, probe flat 0.75%} | `round_risk_frac` → `entry_cap` |
| J3 | 档位判别式（结构降级） | `structure.entry_classification`：trend ∈ {down, transition} 且未修复 → probe | entry_guard / today_group / price_reminder / brief 四处共读（08-11 收敛） |
| J4 | 修复转移落地 cap | `regime_transition.py:489` `cap_pct_nav = CAP_FRACS["probe"]` + `unlocks: "probe"`（display-only 死字段） | reclaim_* regime 卡 |
| J5 | 卡片天花板 clamp | `sizing.clamp_classification` + `max_classification` 字段 | **live 活例 0 张**（全卡扫描：constraints 只剩 vehicle/touch_protocol 3 张） |
| J6 | 期权资格闸 | `entry_guard.py:763` `classification != "confirmed"` → 拒（用户 「这个必须」） | entry_guard 期权分支 |
| J7 | 落库盖章 + 审计分桶 | `sizing.derive_classification`（cap 反查 → BUY fill 事件）+ `schema.CLASSIFICATIONS` 枚举 | trade_history_sync / annotate_event / perf_report `by_classification` / brief `INFRA:fill_untagged` |

**Live 卡分布**：cap 0.02 修复票 6 张（AAOI/INTC/MRVL 持仓 + LITE/MU/NVDA 候选，
auto 源 `B_repair_probe_*`）；cap 0.40 常规 4 张（AMZN/COHR/META/MSFT）；cap 0 持仓锁 2 张
（GOOGL/NBIS）。所有 `next_regime.cap_pct_nav` = 0.40。

## 2. 目标模型（指令 2 定版）

```
授权量   = current_regime.cap_pct_nav ∈ {0, NOTIONAL_CAP}（二值；唯一来源，分类表死）
           0    = VETO（结构未修复 / 降级 / 持仓锁）
           0.40 = 参与资格 + 名义天花板（gate 收复转移与常规候选同权 —— 「第一次给 cap」）
节奏     = 轮预算 R1/R2/R3 = 2.5/1.5/1.0% NAV risk（单一 schedule，三轮后 0 = T 用尽）
主尺     = 轮预算（risk-based：名义 = risk% × NAV ÷ stop 距离）—— cap 从主约束退为天花板
入场侧   = 恒 R1（watchlist 域 = 新 campaign，不变）
加仓侧   = campaign_round_no 封顶（today_group grant，保守过计口径不变）
降级     = 结构反转（trend ∈ {down, transition} 未修复）→ 立马 cap 0（用户 指令 2 原话），
           无 2% 打折中间态；转移机破位降档照旧写 cap 0 观察层
六臂取小 = ①regime cap−敞口 ②轮预算 ③at-risk ④冲击预算 ⑤gross ⑥购买力（结构不变）
```

与原稿 v1 的差异：v1 假设修复 regime 落地 cap 2% 保留（词死数活、行为零变化）。指令 2
明确推翻 —— **2% 这个数本身作废**，修复转移落地直接 `NOTIONAL_CAP`。

## 3. 证据与标价

### 3a. 动机验证（用户 叙述 vs 数据， 核）

样本 = 7/31–8/6 gate 收复后 zone 入场的四笔 campaign（8/2–8/3 夜盘 early_risky 四连是
超授权 + 当日纠错，不入样本；GOOGL LEAPS 8/5 above_no_chase 是规则外反例）：

| 票 | 入场 | gate | stop | 现价(8/12快照) | 浮盈 | 止损触发 | 收复后收盘回 gate 下 |
|---|---|---|---|---|---|---|---|
| AAOI | 2@125 + 2@123 | 120.5 | 112.35 | 138.67 | +$59 | 0 | 0 天 |
| INTC | 2@98.7 + 2@97.8 | 98.0 | 93.9 | 101.15 | +$12 | 0 | **2 天**（8/10 收 97.52 / 8/11 收 97.71，浅破 ≤0.5%，8/12 收回；stop 全程未触） |
| MRVL | 2@213 | 189.0 | 179.8 | 223.27 | +$21 | 0 | 0 天 |
| NBIS | 1@189 | 175.1 | 175.1 | 247.11 | +$58 | 0 | 0 天 |
| LITE/MU/NVDA（Today 候选未成交） | — | 716/848/204 | — | — | — | — | 收盘 0 天（MU 盘中破 3 日） |

**结论：叙述成立**—— 4/4 浮盈、0 止损、收盘口径 7 票中仅 INTC 浅回 2 天。方向与 gate
round-2 回测一致（gate 择时止损率 −8~−30pp 显著改善、cluster gate 被拒组差 −9~−14pp =
画线维度判别力真实）。**诚实声明**：n=4 campaign / ~1.5 周 / 单一牛市段；INTC 的 2 天浅破
说明 whipsaw 形状真实存在（这次没打到 stop 是因为 bfloor_buf 地板离 gate 有 0.5N —— round-2b
选这个语义正是为此），规则的防线是 **stop 不是 gate**，回 gate 下本身不亏钱。

### 3b. 「稍微大胆一点」的精确标价（新旧 R1 对照，NAV ≈ $21k）

| 票 | stop 距离 | 旧 R1（probe cap 2% binding） | 新 R1（轮预算 2.5% risk 主尺） | binding | 名义倍数 | 单笔 at-risk（名义） |
|---|---|---|---|---|---|---|
| AAOI @125 | 10.1% | ~$497（4 股） | ~$5,200（≈42 股，24.8% NAV） | 轮预算 | ~10× | $50 → **$525 = 2.5% NAV**|
| INTC @98.25 | 4.4% | ~$393（4 股） | **$8,400（40% cap 压住，≈85 股）**| ①cap | ~21× | $17 → **$370 = 1.76% NAV**|
| MRVL @213 | 15.6% | ~$426（2 股） | ~$3,400（≈16 股，16% NAV） | 轮预算 | ~8× | $66 → **$525 = 2.5% NAV**|

- 单笔 at-risk：0.1–0.3% NAV → **1.8–2.5% NAV**（实证 ×1.40 ≈ 2.5–3.5%）。三轮全上 = T 5%
  名义 / 7% 实证 —— 全部在 08-10 已定版的框架数内，本设计没有加大任何顶，只是让轮预算
  从「被 2% 压死的死字母」变成真 binding 尺。
- **组合相关性提醒（无专门臂）**：修复票高度同板块（半导体/光模块）。三票 R1 同时在场 ≈
  81% NAV 名义（购买力 ~85% 恰好容纳），同日联合止损 ≈ 6.8% NAV 名义 / ~9.5% 实证。
  冲击预算 15% 是 **ATR 尺不是 stop 尺**（N1 边界注记），别拿它当联合止损兜底；真兜底 =
  熔断 22.5%。这不是新洞（现状同样没有联合 stop 臂），但量级放大后值得知道。
- 期权 R1-only 保费上限照旧 = R1 2.5% ÷ 0.25 = 10% NAV。

## 4. 与既有裁决的关系

| 裁决 | 处置 |
|---|---|
| 08-10 risk_allocation「probe flat 0.75% 零改动」 | **推翻 probe 半**（该臂在 cap 2% 下恒不 binding，从未做过功）；confirmed 半 (2.5/1.5/1.0)、T=5%、期权 R1-only、轮号保守过计 —— 原样保留升格为唯一 schedule |
| 08-08「probe 保持 2%（证据问题不是风险问题）」 | **彻底推翻**（指令 2「不再有 2% 的限制」）—— 两周 live 样本 + gate round-2 判别力证据：gate 多维确认本身就是证据关，过关不再打折。2% 这个数从系统消失 |
| 08-11 C1「probe 仅对修复阶段」 | 延伸至终点：修复阶段也直接 R1，probe 词与数一并退役 |
| 08-10 期权 confirmed-only「这个必须」 | 闸的判据换承载（D2）：结构未修复 = cap 0 = 整笔 VETO（期权自然进不来），GOOGL −41.5% 案在新世界仍被拦（above_no_chase + 结构未修复双拦）。**行为变化**：修复收复票（旧 probe 域）现在 cap 0.40 = 期权资格开放，vehicle 仍由卡片 constraints 控 |
| 07-30 gate round-2 D1 分支 3「gate 授权域 = 修复路径 only」 | 不冲突：本设计改的是「转移后给多少」，不是「谁要等 gate」（新开仓照旧不等 gate） |
| escape_velocity C（2 日确认）/ fastlane k=0.5 | 不动 —— 转移**时点**口径与本设计正交 |
| structure_downgrade_backtest 前置规矩 | 判别式内核（trend/repaired）不动，消费方式降档→VETO（指令 2「结构降级反转立马 cap 0」）。回测证据（transition ≈ down）更支持 VETO 而非 2% 打折 |
| zone-maintenance「tier↔证据：confirmed 要求结构性修复级证据」 | 语义保留换词：「满额 cap 的授予要求 gate 多维确认」—— gate 检查表 10 条不变，证据标准不放宽 |
| 08-01 语义分层 + ledger §89 confirmed 双义 | 顺手治愈：confirmed 词退役，0.40 只剩 NOTIONAL_CAP 一个名字 |

## 5. 设计决策记录

- **D1 结构降级 → VETO / cap 0**：✅ **用户 已拍**（指令 2）。判别式内核保留
  （`structure_repair_veto`），输出从降档改 VETO；today_group「无 K 线→probe」回落改
  「无 K 线→不 grant + 播报」。
- **D2 期权资格闸**：✅ **用户  三裁（逐字）**：「期权要到 r2 才能进 目前由于
  期权样本较少 需要更高高确定性 因此严禁在 r1 入场」。—— **08-10「期权 R1-only」整个反转**：
  旧 = R1 用期权、R2+ 一律正股；新 = **R1 严禁期权**（正股先验证 campaign），R2 起可期权。
  判据 = `cap > 0` **且**`round_no ≥ OPTION_MIN_ROUND (=2)` + 卡片 vehicle。保费上限随轮走
  （同公式 轮 risk ÷ S1 loss 0.25）：R2 = 1.5%/0.25 = **6% NAV**、R3 = 4% NAV；旧 10% NAV
  是 R1 2.5% 的产物，随 R1 禁令退役。watchlist 域恒 R1 ⇒ entry_guard 的期权执行计划整体
  移到加仓侧（vehicle=leaps/spread 卡在 R1 出正股计划 + 播报「期权 R2 起」）。硬 veto 非
  advisory。GOOGL −41.5% 防线升级为三重：cap>0 + 轮号≥2 + 结构腿。
- **D3 修复转移落地 cap**：~~RECLAIM_CAP_FRAC=0.02~~ **作废**（指令 2）——
  `regime_transition.py:489` 直接写 `NOTIONAL_CAP`；删 `unlocks` 死字段写入。
- **D4 落库盖章**：BUY fill 停写 `classification`，改写 `auth: {cap_pct_nav, round_no}`；
  历史值只读保留（tier_legacy 先例）；`schema.CLASSIFICATIONS` 冻结为 legacy 校验；
  `INFRA:fill_untagged` 改盯 tier + auth；perf_report 保留读历史 + 新增 by_round 分桶。
- **D5 early_risky 一并退役**：判定源只剩 max_classification 手工标注且活例 0；防线由
  D1 VETO + no_chase/zone 位置闸承载。历史事件值只读。
- **D6 max_classification 机制删除**：活例 0，空转确认。未来需要卡级收紧时另立数值字段。
- **D7 存量迁移（待 用户 确认）**：6 张 cap 0.02 卡按新语义应为 0.40（gate 已收复、转移
  已发生）。推荐**接线时一次性迁移**（auto 源 cap==0.02 → NOTIONAL_CAP，播报），否则
  live 上新旧规则并存 = 同概念双判别式的惯犯形状。迁移后轮号照 campaign_round_no 保守
  口径接着数：AAOI 入场日 2 个 → 下一轮 R3 1.0%；INTC/MRVL 各 1 个 → R2 1.5%；
  LITE/MU/NVDA 未入场 → R1 2.5%（LITE 卡 stale_earnings 告警在响，重画前 entry_guard
  的 STALE/NEEDS_CARD 拦截照常生效，迁移不豁免）。
  ⚠️ 老 campaign 的 R1 是 2% 时代打的小额但轮号已消耗 —— 不追溯重数（追溯 = 放宽）。

## 6. 接线清单（依赖序）

**代码**：
1. `core/sizing.py`：删 CAP_FRACS/CLASS_ORDER/clamp_classification/derive_classification/
   classification_for_cap；`ROUND_RISK_FRACS = (0.025, 0.015, 0.010)` 单 tuple；
   `round_risk_frac(round_no)` 去分类参；删 `effective_cap_frac`（第①臂直读
   `regime_cap_frac`，**None → 0 fail-closed**——schema 同生同死保证活跃正股卡必有 regime）；
   `entry_cap` 签名去 classification。
2. `core/structure.py`：`entry_classification` → `structure_repair_veto(agent, st, close) ->
   (ok, reason)`（内核 trend/repaired 逐字不动）；`derive_card_regime` 文案去分类词。
3. `decision/regime_transition.py`：`:489` cap 写 `NOTIONAL_CAP`；删 unlocks 写入。
4. `decision/entry_guard.py`：PARAMS 三 cap_* 删；降级段改 VETO；期权闸 = cap>0 + R1-only；
   播报改 cap% + R 轮号。
5. `sync/today_group_sync.py` / `sync/price_reminder_sync.py` / `report/portfolio_brief.py` /
   `report/gate_ladder_report.py`：分类读点全换 regime cap + 轮号；z1 备注格式不变
   （本来就是 entry_cap 实际值）。
6. `sync/trade_history_sync.py` + `journal/annotate_event.py` + `core/schema.py` +
   `report/perf_report.py`：D4 口径。
7. 存量迁移（D7）：一次性脚本或 `derive_card_regime` 对账式升档（auto 源 0.02→0.40），播报。
8. `research/dof_audit.py`：分类轴条目改记退役（预计 −6~−8 会动钱条目）。

**测试**（19 文件改断言 + 新增）：
① D7 迁移后 6 张卡 entry_cap 输出符合 §3b 表（数字钉死）
② fail-closed（无 regime → cap 0）
③ D1 VETO（transition 未修复 + cap 0.40 卡 → VETO 非降档）
④ 期权闸（cap 0 → 期权拒；R2 → 期权拒 R1-only）
⑤ 轮预算 schedule 锁值（2.5/1.5/1.0，第 4 轮 0.0）
变异测试至少 2 处红（schedule 值 / VETO 判别式）。

**文档**：CLAUDE.md（sizing 行、注解判据段）、position-tiers.md（分类 cap 语义节重写 +
「每轮 ladder 预算不是持仓总额」节按二值世界改）、trading-discipline.md（Q1 三问改
「结构修复了吗 / cap 给了吗 / 第几轮」；probe cap $425 算例全清）、zone-maintenance.md、
tasks/*.md、RULINGS_LEDGER 新条目。历史文本保留不删（v3 惯例）。

**不做**：不迁移历史事件 classification 值；不大爆炸改旧 regime 名（`B_repair_probe_*`
随重画淘汰）；不动 tier 轴；不动 AT_RISK/T=5%/冲击预算/gross/购买力五臂的任何数字。

## 7. 风险与复核

- **主风险 = 假收复的单笔代价放大 15–30×**（$17–66 → $370–525 名义/笔）。缓释：bfloor_buf
  地板（gate−0.5N）+ at-risk 顶 + 轮递减。INTC 8/10-11 浅破 gate 不破 stop = 该缓释的
  live 首个正样本。
- **样本声明**：动机样本 n=4 campaign / 1.5 周 / 牛市段。按 N1 定式「裁决不需要回测、
  定价值得跑」：+3 个月后跑一版「修复收复后 R1 满额 vs 2%」反事实定价（gate2 家系池），
  含熊市段再核一次止损率。**复核触发器**：任一修复票 R1 满额后被 stop 打掉 → 当周把
  该笔的 gate 多维证据复盘进 ledger（gate 确定性主张的第一个反例值得单独看）。
- **组合相关性**：同板块修复票联合止损 ~9.5% NAV 实证无专门臂（见 §3b）——已知留档，
  不在本设计内立新臂。
- 回滚：D1 输出改回数值收紧一处改动；D3 回滚 = cap 落地改回常数一处改动。
