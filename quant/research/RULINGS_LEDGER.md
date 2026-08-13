# 裁决账本 (RULINGS LEDGER)

**建于 。**起因是一次真实的误引：我拿 `fastlane_repair_gate_design.md` 的
「**不采纳**」去否定 用户 记得的一条规则，漏了**同一天**`ladder_entry_design.md` 的
「**采纳，k = 0.5**」——而后者接线进 live 的参数名**就叫**`fastlane_k`。
同一个词「快车道」，两份文档一个否一个采纳，代码里存在的是被采纳的那个。
**grep 到符号推不出裁决方向，读到状态行推不出适用域。**这份账本就是防这个。

覆盖：`quant/research/*_design.md` **19/19 份全部**（/09 加 `gate_path_taxonomy` + `ladder_timing` + `stop_cost_guard`）（另两份 .md 非裁决稿：
`shock_budget_wiring_todo.md` = 接线清单、`volume_profile_accuracy.md` = 方法论梳理）。
每份都做了 live 符号核实；核实不到的写「未核实」，不猜。

## 0. 状态词表（分叉清单用）

| 状态 | 含义 | 危险度 |
|---|---|---|
| `REJECTED_BUT_LIVE` | 被否掉的东西在 live 里跑，或被禁的文案还在输出 | 🔴🔴 最高 |
| `ADOPTED_BUT_ABSENT` | 裁决采纳了，但 live 没有强制点（或只有告警/播报，或只活在代码不在 rules） | 🔴 高 |
| `VALUE_MISMATCH` | 同一个数/单位/文案，文档与代码不同 | ⚠️ 中 |
| `NOT_IN_CODE` | 文档点名的符号/取值在 live 不存在（幽灵取值、幽灵卡片） | ⚠️ 中 |
| `CONFIRMED_LIVE` | 逐符号核实一致（含「不该在的确实没在」） | ✅ |

⚠️ **`CONFIRMED_LIVE` ≠ 无债务**。有 7 条核实一致但带已知债务，单列在 §3.5。

---

## 1. 总表

> 「live 强制点」写的是**会改变行为的那一处**。只播报 / 只告警的写「(advisory)」——
> 它不是强制点，这个区分在本仓已经出过事（`bfloor_buf`、`structure_drift`）。

### 1a. 已采纳 / 部分采纳（这些在管 live 的行为）

| design | 裁决 | 采纳了什么（带值） | 否掉了什么 | live 强制点 | 核实 |
|---|---|---|---|---|---|
| `exit_line_reminder_design.md`<br> | **采纳**（用户，当日实现+验收） | 退出栈合成 binding 线：下方硬线 = **max(卡 stop, 棘轮地板, 趋势死亡线)**，同基取 max；上方止盈 trim / g01 独立不进 max。note 词表 `stop:card`/`stop:lock`/`stop:trail`/`trim`/`g01`（moomoo 上限 10 字符）。tie-break `card > lock > trail`。地板/trail **只读**`exit_guards.json` 不重算。期权 basis 分离（卡 stop+trail 挂正股 code，棘轮地板/g01 挂合约 code） | ①原提案「赢家侧四者 max」——棘轮地板混进去会被 trim 吃掉，改按**方向**分组不按哲学归属；②下方线禁写 `trim:type`；③`stop:ratchet`（12 字符）超限改 `lock`；④裸 `stop` 认旧不写旧 | `quant/sync/price_reminder_sync.py`（:88 KINDS / :90 STOP_SRC_PRI / :176 exit_floor_map / :194 _stop_pick / :218 desired_lines / :339 plan_ops）；真源 `state/exit_guards.json` | ✅ 行号逐条命中 |
| `regime_field_unification_design.md`<br> | **采纳**（方案 a，用户，当日完成） | 每张有 zones 的正股活跃卡显式携带 `current_regime{name,cap_pct_nav,participation,since,reason,source}`；zones↔regime **同生同死**（schema 双向 error，无 warning 过渡窗）；`source` = 所有权判别式（card 源重画覆盖，机器源不触碰）；`since` = **结构锚定日**不是执行日；cap-0 卡 **VETO 但保留修复阶梯渲染**| ①「授权语义活在读侧 `if bucket ==` 推断里」——删推断改纯字段读；②「常规参与写 probe 0.02」——min 语义下写天花板才不收紧；③「有 zones 无 regime」从 warning 升 error | `quant/core/structure.py:58 derive_card_regime`；`schema.py:111 _validate_card_regime`（:196/:258）；`today_group_sync.py:84 regime_grant`；`entry_guard.py:547-562`（阶梯行在 cap-0 VETO return **之前**） | ✅ 结构一致<br>⚠️ 3 处漂移见 V1/N3/N4 |
| `relative_volume_gate_design.md`<br>08-06 | **采纳**（分支 R，W1；当日跑完当日接线） | 修复 gate 量腿换相对尺：**RelVR = 个股量比 / max(基准量比, 0.4) ≥ 1.2**；`REL_FLOOR = 0.4`（单点预注册不网格）；基准 **半导体→SMH / 其余→QQQ**（24 票冻结表）；阈值 1.2 与天数腿**不动**；基准缺当日→绝对量比兜底且 note 必须说出口。读数 ΔT60 **+0.48pp**CI90 [+0.16,+0.79] / 覆盖 +3.7pp / 止损率 +0.95pp（守卫 +3pp 内） | ①W2「取或」：止损率 +2.53pp **上界 +3.89pp 超守卫**；②W3「量腿退休」= 分支 N **不触发**——拆掉止损率 **+6.4pp**，且 W3−W1 +0.10pp CI 含 0 →「该换的是尺不是腿」；③天数腿明确不在本测范围 | `quant/core/sectors.py:19 SEMI_SMH` + `:26 benchmark_for`；`regime_transition.py:108 volume_leg`（:91 REL_FLOOR，:130 rel 计算，:129 降级 note）；双消费者 `regime_transition:224/:255` + `gate_ladder_report.py:44/:203/:318` | ✅<br>⚠️ 行号已漂移（claim 写 :96/:90/:40/:196）<br>⚠️ V6 双份常量 / A6 rules 零记载 |
| `ladder_entry_design.md`<br>08-07 | **部分采纳**：k=0.5 采纳 / k=1.0 不采纳 | **幅度门槛 k = 0.5**（D1 = 首个 close>gate 日，要求 `close > gate + 0.5×ATR20`）；**repair_reclaim 三档 ladder 50/30/20**，锚 = `close / close−0.5N / close−1.0N`，**每档地板 = 刚收复的 gate**；限价有效期 10 日；stop 沿用 `bfloor_buf` 不动。读数 n=1,611 Kish 197：风险 N(C−B1) **−0.390N**[−0.424,−0.356]、R(C−B1) **+0.293**[0.159,0.438]、R(C−B2) **+0.484**、部署 86%，五判据全过。风险单位 1.46N→1.07N = **−27%**| ①**k=1.0 格 FAIL**（判据②：R(C−B1) −0.000 CI [−0.143,+0.132]）；②增益**不在最关心那一档**——0.5–1.0N 带显著为正，**>1.0N 带 R 平局**，「ladder 的价值在刚过线的普通突破上，不在跑得很凶的强突破上」；③**不是快车道的翻案**（否的是全额提前，批的是分档提前） | `regime_transition.py:72 fastlane_k=0.5` + `:238 _fastlane()` 三条件（首日/幅度/量腿）；`entry_guard.py:101 repair_reclaim_offsets=(0,0.5,1.0)` + `:316 reclaimed_gate_of` + `:340-352 build_ladder(repair_reclaim)`；权重 `entry_guard.py:93 ladder_weights=(0.50,0.30,0.20)` | ✅ 全部命中<br>⚠️ 声明里的「权重接线缺口」是**误报**（已接，见 §3.5）<br>⚠️ V3 日期单位 / A4 rules 零记载 |
| `gate_backtest2_design.md`<br>07-30（round-2 + 2b） | **部分采纳**：round-2 触发预注册分支 3；round-2b 采纳 `bfloor_buf` | ①**gate 授权域收窄到修复路径**；gate 定位重定义为「**保险不是 alpha，保费 ≈1pp mean/episode**」；②**`bfloor_buf` = stop 为 `gate − 0.5×ATR20@转移日`，收盘 ×1 日确认**——唯一在 cluster/swing 两主格同时合格者（+0.1~0.2pp/ep @T60，把保费削 10-20%，**不是免费**）。规模 165 票/4,670 ep/Kish≈194；风险侧止损率 −8~−30pp、p10 +0.3~+4.6pp 全显著 | ①**分支 1（gate = alpha）**：12 主格 expectancy CI90 **全负**，ΔT60 −0.8~−1.4pp；②**新开仓与常规加仓等 gate**；③`structure×1` 全场被支配；④**live 旧 incumbent `bfloor×1`（stop=gate 本体）实测全场最差 = whipsaw 机器**；⑤`two_n` swing 格 p10 守卫失败 | 规则层 `.claude/rules/position-tiers.md §Gate 授权域`（**prose 不是代码**）；代码只有 `entry_guard.py:211 floor_gate_gap_lines`（**advisory only 不改判**，FLOOR_GAP_ATR=0.25） | ⚠️ **A3：`bfloor_buf` 无写入强制点**<br>🔴 **R2：加仓等 gate 已在 live**<br>⚠️ V8 状态行 stale |
| `earnings_window_design.md`<br> | **部分采纳**：原四臂判据①**全败**→ 用户  批「路 A」变体并接 live | 财报**反应日 T+0**仓位上限 = 名义 × 缩仓比例 × **m 0.7**；估计臂 **C 收缩**：`X_earn = λ·单票p98 + (1−λ)·池级p90`，`λ=n/(n+10)`；先验 <8 回退池级 **3.00N**；`缩仓比例 = min(1, X_normal/X_earn)`，**X_normal 取 live `SHOCK_X=1.88` 不是文档正文的 1.94**；窗口 **T+0 单日**；AMC→下一交易日 / 未知按 AMC；日历陈旧 7 天。首个 live 样本 LITE （x_earn 5.563N/19 事件 → `z1:2%:400`→`z1:<1%:100`） | ①**「财报前一周缩仓」被数据否**（T−5~T−1 中位 0.41–0.44N = 基线 0.99–1.05×，>2N 占比 0.9–1.5%，「找不到依据」）；②**四臂原形态全败**（超限率须落 [1%,4%]：D 18.52 / A 9.97 / B 7.93 / C 8.37%）——但「有效但不够」，三臂全显著砍半，保费比 ≈1.00；③**路 B（全年降到 15%）被否**；④B 臂点估最好但选 C（对少样本更稳）；⑤m=0.4 缩过头 / m=0.8 不合格 | `core/earnings.py`（:37-41 常量组 / :44 shrink_ratio / :59 event_state）；`sizing.py:198-199 event_shrink` **只收紧**；四消费者 `entry_guard:520/522/670`、`portfolio_brief:898/901/1072`、`today_group_sync:136-138`、`price_reminder_sync:380` | ✅ 常量零分叉<br>🔴🔴 **未入 git**（见 §3.0）<br>🔴 A2 减仓半边缺失 / A5 rules 零记载 / V4 恢复文案 |
| `shock_budget_design.md`<br> | **部分采纳**：框架「当前形态」两轮均**不采纳**（H1/H2 全不过），拆块后**四个数定版并进 live**| `w = min(40% NAV, 剩余预算/(1.88×ATR/价), 110%−已用 gross, 分类/regime cap)`。**`NOTIONAL_CAP = 0.40`**（用户，自评风险偏好高；业界 5–25% 之上）/ **`SHOCK_BUDGET = 0.15`**/ **`SHOCK_X = 1.88`**（池级常数）/ **`GROSS_CAP = 1.10`**（代价 −0.17pp）。`CAP_FRACS[confirmed] 0.05→0.40`，**probe 保持 2%**。**§13 SIMPLE 采纳**（per-ticker vs 池级逐笔差 **中位 0.0% / p90 0.0%**）。期权口径 = IV 不变 BS 重估（单腿 long call ⇒ vega 恒正 ⇒ 永远保守） | ①**分板块 10% 永久取消**（sep 0.0652 < 0.15）；②**per-ticker `X_i` 删除**（⚠️只成立于 40% 上限之下）；③**§2 原始形态否**：B_greedy 单笔 354–458% NAV，「冲击预算控制的是单日会亏多少，**根本不控制借了多少**」；④不另设「单笔预算份额」旋钮；⑤**H1 控制力不过**（打穿率仅目标 0.02–0.05×，「不是安全，是校准与现实脱节」）；⑥**H2 选择性不过**（ρ −1.00→+0.06；−1.00 是构造的不是发现）；⑦熔断 1.5× **未定版**；⑧🚨 **破例条款**：任何卖方腿出现 → IV 不变低估 67%，**必须**补 IV 上冲项 | `core/sizing.py:37-38 CAP_FRACS` / `:64-67` 四常数 / `:72 shock_occupancy` / `:156 gross_room` / `:163 budget_cap` / **`:167 available_cap()` = 实际 cap 唯一真源**；`core/book.py:56/62/71/80`；三消费者 entry_guard:403+466 / portfolio_brief:899+1160 / price_reminder_sync:378 | ✅ 四个数逐字一致、零分叉<br>⚠️ V9 §10f「不动 live」未标废<br>⚠️ V1/V2 rules 层未跟 |

| `options_exit_overlay_design.md`<br> | **采纳（用户  终裁当日接线）**——合成大样本 A1/A2 ✗ 后按预注册回退条款走三证据体重裁（17 笔实盘回放 + 合成 1967 campaigns + 真实 Jan27 42 笔重放），S1 取紧 = 牛市偏置知情保守 | 单腿 long call **亏损侧退出栈**（棘轮武装前裸窗口，GOOGL spot −6.2% 时 premium −41.5% 活案）：**S1 premium 硬 stop −25%**（mark ≤ 加权成本×0.75 → 清仓，收复 +2% 重武装）/ **S2 时间 stop (20,25)**（d20 underlying 未收上入场 close+1N → 减半〔单张不可拆→降级提示〕，d25 → 清仓）/ **S4 DTE 地板 90**（≤90 无条件 close-or-roll，roll = 新 campaign 重过 IV+sizing）/ S3 IV×横盘 **advisory**（−8pts 且未 +1N，AAOI200C 样本 2 不裁）/ S5 underlying stop 保留 = OR 双防线。盈利侧 = **premium 管理口径**（棘轮/G-01 premium 基同基取 max + underlying 基〔趋势死亡线/regime trim〕并行 OR）。**vehicle 资格闸 confirmed-only**（entry_guard 前置）。联动：at-risk 期权系数 保费全额 → **保费×0.25**（锚 \|S1\|，pins 测试钉相等） | ①立项草案 S1=−40% → 数据裁 **−25 紧档**（真实 42 笔重放 whipsaw 91-100%、AAOI +399% 被 S d4 砍成 −31%——读数被  起牛市窗「什么都涨回来」污染不可外推，紧档 mean 代价 −20.4pp 同被污染 → 保守取紧，**+12 月复测**）；②S3 升硬线否（真实 IV 救活 AAOI200C +46.6pp 但样本 2）；③合成上 overlay 判据 **A1 ✗**（Δp10 +4.2 < +5pp）/ **A2 ✗**（Δmean −2.7pp，CI90 [−4.46,−1.27] = 净保险费）——**接线依据是三证据体不是合成判据**，回退条款预注册在案 | `exit_guard.PARAMS` `opt_s1_stop=-0.25` / `opt_s2_days=(20,25)` / `opt_s4_dte_floor=90` / `opt_s3_iv_drop=8.0`(advisory) + `option_loss_overlay()`（heartbeat 每 6h 巡逻，告警原文推 TG）；`sizing.OPTION_ATRISK_LOSS_FRAC=0.25`；canonical `leaps-call-template.md` §13.4 | ✅  补记（**当日漏记本表**——次日 AMZN 案 grep ledger 查无此裁，把已裁的 DTE 分叉误标「待裁」，「引裁决前先查 ledger」机制的实证反例） |
| `synthetic_longcall_design.md`<br> | **按预注册判据裁两参数**（B1 否 150 / S4 数据分档；A 组全 ✗ 触发回退条款，归 overlay 行） | **入场 DTE 维持 270**：B1 判据先锁（150 可用需 Δmean ≥−2pp **且**Δp10 ≥−3pp，「用户 偏好生效」写进判据）→ 150 臂 Δmean +0.3pp 过、**Δp10 −5.2pp 超守卫**→ ✗ 不可用。用户  裁的是「**150，去回测**」= 条件式裁决，条件未过，§13.2 的 270 起步/Jan+2 对齐维持。机制：入场 150 − S4 地板 90 = 仅 60 天跑道，150 臂 S4 触发率 6%、p10 −40.1% vs 270 臂 −34.8%；365 臂 p10 −32.3% 全场最优 = Jan+2 对齐档的数据背书。**S4=90 弱优于 120**（mean +14.1% vs +13.1%、p10 持平 −11.3%）→ §13.2  初稿退出地板 120 被取代（已同步） | ①**DTE 150 入场下限**（scope 口语「一般>150」的操作化尝试，被尾部守卫否；scope memory 已标结案）；②Δ0.45 轻度 OTM 信息性臂不裁（mean +16.9% 更高但中位 −14.8%、S1 触发 23% = 「 口述轻度 OTM」那条路的代价已量化，Δ0.60-0.80 主带维持）；③S3 合成永不可裁（IV 无独立路径，声明的框架边界） | `entry_guard.py` LEAPS vehicle `target_dte=270, min_dte=270`（`option_plan.pick_expiry` = 下限之上最近周期。**后续修订**：用户 移除 §13.2「最近 Jan 周期」执行限制——旧 `dte=540` 即该限制的代码近似，同批废；270 下限首次真实 binding）；`exit_guard.PARAMS["opt_s4_dte_floor"]=90`；报告 `tmp/backtest/synthetic_longcall_report.md` | ✅  补记（同上） |
| `risk_allocation_design.md`<br> | **采纳（fiat，三点锁定当日接线）**——按 N1 定式「裁决不需要·定价值得跑」：风险偏好参数不设 verdict 门，价签已附（§4）。⚠️ **两处被后裁推翻**（见 `classification_removal_design.md` 行）：probe flat 0.75% 随分类轴退役（该臂在 cap 2% 下恒不 binding，从未做过功）；期权 R1-only **整个反转**为 R2 起严禁 R1 | **Campaign 风险预算递减分配**：T = **5% NAV 顶格**（= `AT_RISK_CAP_FRAC` 同一个数，`test_round_risk_schedule_locked_values` 钉相等）× 三轮 **50/30/20**→ confirmed campaign 每轮 risk **2...0% NAV**，三轮封顶后 0（T 用尽）；~~probe flat 0.75% 零改动~~；~~**期权 R1-only**（保费上限 = 2.5%/0.25 = **10% NAV**，R2+ 一律正股 delta 名义）~~；轮号 = **保守过计口径**（不同入场日数+1，过计→预算偏小）；价签 = 实证止损乘数 **−1.40R mean**（71,093 止损样本，2N 入场锚定族）→ 名义 5% 打满 ≈ 实证 7% | ①+kATR 机械加仓间距（锁点 3 否，价格确认轴 = A 结构 gate/NBIS 制）；②open risk 实证折算口径（用户 选**名义**，与推荐相反，知情——1.48N 证据在案 +12 月复核）；③板块层硬风险上限（不立）；④首轮 = f(SignalScore)（SSS 已死，不复活）；⑤SSS 衰减减仓（减仓权归退出栈） | `core/sizing.py` `ROUND_RISK_FRACS`/`round_risk_frac`/`round_risk_notional_cap`（唯一换算点）/`campaign_round_no`；消费者两个：`entry_guard.size_caps` ②（恒 R1——watchlist 域=新 campaign）+ `today_group_sync.grant_actual_cap`（加仓侧有 lots → 轮预算封顶，binding=`roundN_risk`）。副作用：⑤ at-risk 后备边界 13.3N→**4N**（R1，2N stop 处恰 2×，测试钉）。记载三处：position-tiers.md 新节 / CLAUDE.md sizing 行 / trend-exit-system §2b 修订注 | ✅ 1086 全绿 + 变异  红（schedule 值/轮号+1）|
| `classification_removal_design.md`<br>****| **采纳（用户 三裁当日接线）**——指令 1「移除 probe 仓位概念 只要给出 regime 给出 cap 就以轮 r1 r2 r3 进行参与」+ 指令 2「gate 后入场基本浮盈从未触发止损…不再有 2% 的限制…第一次给 cap 就允许以 r1 入场…结构降级反转立马 cap 0」+ 指令 3「期权要到 r2 才能进…严禁在 r1 入场」。动机验证在案：两周 live 样本  campaign 浮盈、0 止损、收盘口径 7 票仅 INTC 浅回 gate 下 2 日（≤0.5%，stop 未触）；+ gate round-2 判别力证据（止损率 −8~−30pp）；+ probe 档历史净 **−$2,570 / −26%/$**| **分类轴 probe/confirmed/early_risky 整体退役**：授权量 = `current_regime.cap_pct_nav` 唯一来源、**二值 {0, NOTIONAL_CAP}**（live 12 卡全书二值不变量测试钉）；修复 gate 收复转移落地 **NOTIONAL_CAP 满额**（旧 2% 死）；结构降级判别式内核逐字保留、输出改 **VETO**（`structure.structure_repair_veto`，`structure_downgrade_backtest` 证据续用——transition≈down 更支持 VETO 而非 2% 打折）；`ROUND_RISK_FRACS` 单 tuple (2.5,1.5,1.0)%；**期权 R2 起**（`OPTION_MIN_ROUND=2` 硬闸，保费上限随轮 = 轮risk÷0.25：R2 6% / R3 4% NAV）；盖章 classification → **auth {cap_pct_nav, round_no}**（历史值只读，`AUTH_TAG_SINCE=`）；D7 存量迁移 6 张 reclaim 卡 0.02→0.40（AAOI/INTC/MRVL/LITE/MU/NVDA）；today_group 无 K 线→不 grant（fail-closed）；`entry_cap` 无 regime→cap 0 | **推翻的既有裁决（知情点名）**： risk_allocation 的 probe flat 半句 + 期权 R1-only（整个反转）；「probe 保持 2%（证据问题）」； C1「probe 仅对修复阶段」延伸至终点。**未动**：T=5%、三轮 50/30/20、轮号保守过计、`AT_RISK_CAP_FRAC`、冲击预算/gross/购买力臂、转移确认口径（escape ×2 / fastlane k=0.5）。**标价在案**（§3b）：单笔 at-risk 0.1–0.3% → 1.8–2.5% NAV（15–30×），修复票 R1 名义 8–21×；同板块联合止损 ~9.5% NAV 实证无专门臂（已知留档）。复核触发器：任一修复票 R1 满额被 stop 打掉 → 当周复盘 gate 证据进 ledger；+3 月反事实定价（R1 满额 vs 2%，含熊市段） | `core/sizing.py`（CAP_FRACS/CLASS_ORDER/clamp/derive_classification 删；`derive_auth`/`option_premium_cap_frac`/`OPTION_MIN_ROUND` 立）；`core/structure.py` `structure_repair_veto` + `derive_card_regime` 满额；`regime_transition.py:489` NOTIONAL_CAP + unlocks 死字段停写；entry_guard/today_group/price_reminder/portfolio_brief/gate_ladder/trade_history_sync/annotate_event/schema/perf_report 全链；binding key `classification`→`regime`；live 冒烟：AMZN 轮预算 binding $5,707、LITE 财报缩仓 ×0.24 照咬、**NVDA/META 结构 VETO 当场拦截**（接线首日逮到两张结构不干净的 40% 卡） | ✅ 1332 全绿 + 变异  红（schedule 值/VETO 判别式）+ live 二值不变量 |
| **gate 预算**（用户 直裁，无 design 稿；本行即 canonical）<br>****| **采纳（fiat，当日两次接线）**——同 N1 定式「裁决不需要·定价值得跑」。用户 逐字 ①「gate1 给 r1 gate2 给 r1剩余和r2 即每轮gate解锁的是前面轮的余额总和➕当前轮的预算」→ ②「就是没有所谓的 r1 r2 r3 概念了 就是 gate1 max 2.5% gate2 max 4% gate3 max5%。gate1 里还可以分开加 比如 gate1 的 cap 给了 8k 那我第一笔加了 4000 那我还能在当前 gate 的 zone 再继续加 但第二次加的时候余额会显示只剩 4000」→ ③「不是任何一个 gate 都算次数 只有给 cap 的 gate 才开始算」 | **T = 5% NAV 是 campaign 的总风险预算，gate 按 50/30/20 分批解锁，消耗它的是敞口而不是轮次的流逝**；「轮」整个退役。`GATE_RISK_CAPS = (2.5, 4.0, 5.0)%` **累计上限**（provenance 仍是 T×50/30/20，测试钉）；可用 = 本档上限 − **当前敞口市值**（用户 二选一时选市值，与  ① 臂同理由；风险空间上也正确：A ≤ 预算/stop距离 − M）⇒ ①② **减同一个 `book.exposure_of`**，同一 gate 内可分多笔加。**档位 = `sizing.gate_count(card)` = 参与流内「给了 cap 的」up 转移条数**（下限 1 = 常规参与卡）；`apply_transition` 从此把授予的 cap 盖进 history 条目（上行落哪个 regime 由 `next_regime` 决定，cap 可以是 0 = 中间站）；**参与流被 down 转移 / 平仓打断即归第一档**；第 4 道 gate 停在 T 不归零 | **期权保费上限不跟累计**（用户 同日裁）：按**本档新增**risk ÷ 0.25 = gate2 6% / gate3 4% NAV，`OPTION_MIN_GATE=2`。**代价明示**：正股/名义侧累计、期权保费侧按增量 = 两侧口径不同，知情。退役符号（源码棘轮）：`ROUND_RISK_FRACS`/`round_risk_frac`/`round_no_from_lots_since`/`campaign_round_no`/`lots_deployed_since`/`unlocked_risk_*` —— 两代「从成交记录猜过没过 gate」的近似 | `core/sizing.py`（`GATE_RISK_CAPS`/`gate_risk_cap_frac`/`gate_step_frac`/`gate_risk_notional_cap`/`gate_count`/`_participation_anchor`；`entry_cap(gate_no=)` ② 臂减 `exposure`）；`regime_transition.apply_transition` 盖 history cap；`entry_guard.size_caps(gate_no=)` —— **入场侧写死第一档已删**（旧理由「watchlist = 新 campaign」把「没开过仓」错读成「没爬过 gate」）；`today_group.binding_label` →「gateN 预算上限」；`auth.round_no` → **`auth.gate_no`**（旧键只读留存于 2 条历史事件）；perf_report by_gate；schema 双认。**连带**：`lot_gap` 台账守卫由**出组降为播报**—— 扣减改读 `broker.qty×现价` 后，「lots 落后 = 已投低估 = fail-open」的理由不成立（`lot_gap>0` 恰恰意味着 broker 已反映该笔成交）。⚠️ 修的两个真洞：**AAOI**被 lots 判据误判 gate2（那笔是**零授权**成交）→ 正确 gate1 $4,118；**LITE**连过 716→860 两道线却一股没买、旧规则整档丢失 → gate2 $6,330。平仓日**两个桶记在不同字段**（`exit_info.closed_at` vs `readd_from_position.closed_at`），判别式两个都读 —— 只读前者时 AAOI 又会被数成 gate2 | ✅ 1393 全绿 + 变异  红（cap 判据 / 参与流锚点 / 平仓双字段）|

### 1b. 不采纳（这些**不该**在 live）

| design | 裁决 | 采纳了什么 | 否掉了什么（精确范围） | live 强制点 | 核实 |
|---|---|---|---|---|---|
| `escape_velocity_redraw_design.md`<br>07-31 | **不采纳**（`C_keep_2day`；parabolic 格 n=9 未裁决） | 零。现行 breakout-stale「close > trim[1] 连续 2 日」原样维持。唯一产出 = 把 NBIS 07-30 类 gap-and-go 的识别延迟**定性为已定价的保险费**| 只否**上行箱体顶突破的时间腿**：level = 前 60 日最高收盘，FAST 资格 = **量比 ≥ 2**（绝对，20 日均量）。9,273 ep（FAST 资格 1,313）：strong_up ΔT60 [−0.82,+2.56] 含 0 / 止损率Δ **[+5.5,+13.5]pp**；other ΔT60 [−0.04,+0.83] 含 0 / 止损率Δ **[+9.1,+13.3]pp**。**不触碰**下行 breakdown stale / repair gate 确认口径 / 入场 ladder；**幅度型触发从未在此被测**| 维持的 A 口径在 `entry_guard.py:307-310` 与 `portfolio_brief.py:1123-1128`（**两处各手搓一份**）。分支 A 承诺的两处接线（zone-maintenance 重画口径 / structure_drift 阈值）**均未发生**| ✅ 判 C 的应有结果<br>🔴 **R1：live `_fastlane` 无适用域判别式，突破 gate 也走**<br>⚠️「2 日」是 `closes[-2:]` 切片，**无具名常量**|
| `fastlane_repair_gate_design.md`<br>08-07 | **不采纳**（×2 确认保留） | 无。§9f「不动 live：`bfloor_buf`、ladder、确认口径一行未改」 | 否【修复 gate 上用幅度确认替代第二日时间确认、**全额**提前一天入场】。4,390 ep / Kish≈195：**判据 1（ΔR CI90 下界 >0）四格全败**——k=1.0 主口径 +0.068 [−0.004,+0.159]、k=1.5 +0.038 [−0.009,+0.119]、_rescan 同。机制证伪：达标子集 n=497 每股风险只差 **0.12N**，ΔR 中位 **+0.002**，**最极端 5 例贡献 94%**。对照臂 fast_nomag 止损率 44%→**68%**。**没有**否掉分档提前，**没有**否掉 fastlane 用于转移/画 zone。原文警告「**D1 收盘全额买入这条路径至今没有任何证据支持**」 | 无（研究层 `fastlane_backtest.py` + 14 测试）。**但 live 有同名符号**`regime_transition.PARAMS["fastlane_k"]=0.5` —— 血统是 ladder_entry，不是本轮 | ✅ 入场层无对应物<br>🔴 R1 适用域靠散文分隔，代码无字段表达 |
| `hvn_gate_design.md`<br>08-07 | **不采纳**（**平手保留 incumbent**，与「判据失败」不是同一种否定） | live 零采纳，**cluster 保持 incumbent**。知识产出：round-2 说「画线维度判别力真实存在」，本轮证明**那份判别力对具体画法不敏感**——起作用的是「头顶有没有一条供给线」，不是用哪种方法找到它 | 否【gate 画法从 pivot 簇换成 HVN 上沿】。裁决格 `_x2`：**四判据全过**（回退 3.9%，cluster ΔT60 −0.9 止损 44% vs hvn −1.2 止损 37%）**但配对 h2h 判死**——Δ拒绝权四格 CI90 **全含 0**、Δexpectancy 四格 **全排除 0 且为负**。早停筛查已预测死因：HVN 高于 cluster **84.2%**、「这是**把线画高一档**的全套签名」。**没有**否掉 volume profile / HVN / LVN 本身（level_map 仍 live） | 无。核实的是「不该在的没在」：`grep -rn hvn` 在 decision/core/sync/report 只命中 `level_map.py`（:62 top_hvn / :329 / :373），entry_guard / regime_transition / structure **零命中**| ✅ 21 测试实收一致 |
| `poc_calibration_design.md`<br>08-07（round-1） | **不采纳**（阈值 T 不采纳，②维保持 advisory-only） | 无。唯一「必须行动的发现」= live `POC_WINDOW=20` 设错了，但 §9d 白纸黑字标「**不是本轮裁决，需另立**」——不是采纳物 | 两臂**不同原因**：(a) **poc20（live 现行）**AUC **0.544**vs 时间基线 0.627，Δ −0.083；k=20/30 **AUC<0.5 = 比随机还差**。(b) **poc60**AUC 0.812 判据①②③✓ 但 **T=0.1N 掉出预注册 [0.5N,3.0N]**→ ④✗。§9b 自陈原因 = **度量与标签共用一个分布**。结构性限制：`POC_MIN_SESSIONS=10` ⇒ **k=3/5 完全无读数**（33% 单元），②维**结构上无法回答「锚定 3–5 天后要不要重画」**| 无（§9e「drift.py 一行未改」，当时正确）。次日 round-2 把整维摘了 → 三个符号**现已全不存在**| ✅ `POC_WINDOW`/`POC_MIN_SESSIONS`/`_poc(` 在 live 树零命中<br>🔴 R5：`poc_window.py:265` ACTION 文案仍指向已删符号 |
| `poc_window_design.md`<br>08-07（round-2） | **不采纳（双重）**：维持窗口 20 + **摘掉 ②POC 维**| 采纳的是一个**移除动作**不是参数。新 label = pivot 锚 zone 组（与 volume profile **零共用分布**），5b 四判据**过一条**（③T=1.9N FPR 29.5%✓；①−0.0183 ✗ ②−0.1017 ✗ ④0.5975<0.65 ✗）→ 摘掉。AUC 表：poc20 **0.5975**/ poc60 **0.5524**/ ①price_drift **0.6992**/ 时间基线 **0.6158**| ①改窗 20→60 否（60 显著更差 Δ −0.0451）；②**②维本身否**——「不是一个独立维度，是①的一个更差的影子」；③**round-1 的 0.812 正式证伪**（0.812→0.552，且反过来输给 poc20）。**没有**否掉其余四维 / volume profile 在 level_map 的用途 / 也没把 drift 接成 hard STALE | 已落地：`core/drift.py:26-31` 摘除声明、THRESHOLDS 六键无 `poc_shift_n`。§10e「本轮不动 live」**已过期**| ✅ 实现层已摘<br>🔴 **R3：两处「五维/POC 迁移」字符串仍在跑**（`pipeline.py:147` 进简报输出、`core/__init__.py:12`）<br>⚠️ V7 测试条数 15 vs 实收 13 |
| `repaint_trigger_design.md`<br>08-05 | **不采纳**（提案 C 否，维持现行 A）—— ⚠️ **裁决根本没写进这份文档**| 无新参数。维持 A =「收盘 > trim 上沿 连续 ×2」。裁决只存在于文档之外三处：commit `017cef1` / memory `project_backtest_2026q3.md` / `tests/test_module_coverage.py:62` 注释 | 否**一条具体放宽线**：C =「收盘 ≥ trim **下沿**×2」。三判据全不过：paired diff +3.28pp 但 CI90 [−0.16,+6.64] 含负 / 止损率 **+7.5pp**（限 ≤5）/ p10 **−2.18pp**（限 ≤1）。中位时效只早 **1 bar**，**白画率 40%**，点估几乎全来自 22%「A 永不触发」episode。**范围仅 watchlist 入场卡**（positions 卡从来不在范围内，不是「被否」）；**B（区间内 ×1）讨论中就否了，根本没测**| `entry_guard.py:307-310` + `portfolio_brief.py:1123-1128`（两份手搓，见 §3.5） | ✅ |
| `stop_cost_guard_design.md`<br>08-08/09 | **采纳（守卫规格）+ 重判 support**—— U=止损代价 / C=每笔已入场 / T=B_harm_veto 三点全部先锁后跑（`00de7f2`/`b0ce609`）；重判结论 = **建议 `fastlane_k=0.5` 留任，A7 结案**| **原则（可复用）：守卫的量纲必须跟着止损模型走**—— 入场锚定（escape 的对称 2N）下两臂单位按构造相同 ⟹ 次数≡代价，+5pp 合法；gate 锚定（`bfloor_buf`）下单位随入场时点变 ⟹ 必须直接量代价。家系 +5pp→+8pp(手工找补)→v2(直接量)。定义 = Σ止损档 w×(px入−px出·(1−SLIP))/ATR20[D1]，逐笔实现值负值不夹，配对=判据①同谓词。**今后 gate 锚定预注册守卫一律用本规格；+12 个月复测（含④）以本守卫盲测**。读数：三格代价差 −0.040~−0.079N **CI 上界全<0（A/B 同判，T 未起裁决作用）**；附带发现 = 每次止损实现损失 ≈1.48N/次 > 名义 1.07N（跳空穿越是真钱，进 risk_unit (b) 档案） | 否掉的是【止损**次数**守卫在 gate 锚定模型下的适用性】——两轮 7/7 格「①✓+⑤✗」零分离 = ①奖励的与⑤惩罚的是同一个动作。**没有**否掉 escape 的 +5pp（在它自己的入场锚定模型里就是代价守卫）；**没有**重开②③④（+12 个月盲测才是它们的下一次机会） | 无 live 强制点（研究层守卫规格；`gpt.measure` 加 `stop_cost` 新键，两轮旧输出 diff 逐字不变）。18 测试 + 10 变异会红；**T 锁定前代码拒跑**（退出码 2）是预注册纪律首次进代码 | ✅<br>⚠️ **事后守卫的原罪已申报**（§2：聚合已见、逐笔 CI 未见、零 Schelling 点）——引用本裁决必须连着 §2 一起引 |
| `ladder_timing_design.md`<br>08-08 | **未获支持（三格全 FAIL，都只栽⑤）**→ **08-09 经 `stop_cost_guard` 重判翻为 support**（⑤的量纲被判定错配，见下一行；§9 读数原样保留——变的是守卫不是数据） | 零参数进 live。知识产出三条：①**「早一天」的真实形状是一笔交易，不是没用**—— 更紧的风险单位（−0.16N）+ **更好的 R（+0.226 CI90 [+0.069,+0.391]，显著）**+ 更高绝对收益，**换 +10.6pp 止损率**；它是被**守卫**否掉的，不是被收益否掉的。②**构造效应实测 ≈0**（ANCHOR 与 ROLL 同裁决 ⟹ §5 触发 `hold`，`gate_path_taxonomy` 的②③④**维持并加固**）。③**§3e 恒等式经验证实**：ANCHOR 产出 1,477 条**全部 = ①repair**| 否【live `fastlane_k=0.5` 在拆开自由度后独立成立】。1,477 ep（ANCHOR，1995+，Kish 205）/ 2,102（ROLL）/ 530（+量腿）：**三格都只栽在⑤止损率差**（+8.9~+10.6pp，CI 全远离守卫），其余五条主格全过。**没有**否掉 ladder 分档（`ladder_entry` §10a 同时点 A +0.283 vs C +0.480 仍站得住）；**没有**否掉 `bfloor_buf` / ladder 权重 / gate 画法 / k=0.5 | 无（研究层 `ladder_timing_backtest.py` + 26 测试，8 处变异确认会红；机械件全部复用 `gate_path_taxonomy`，测试强制不许重写）。**判据先于数字 commit `8d49f84`**| ✅<br>🔴 **A7 升级：否决它的那条守卫，口径与阈值都未经校准**（见 §3.2 A7） |
| `gate_path_taxonomy_design.md`<br> | **不采纳（四类全否）**：②③④均不采纳，`_fastlane` 在该三类维持 ×2；①为对照臂**不裁决**| 零参数进 live。**唯一落地件是一次搬家**：`episodes_of` → `quant/core/episodes.py`（决策点 E：回测与 live 必须 import 同一份；`gate_backtest` 保留同名 re-export，四份回测调用点不变）。知识产出 = ①风险N 与⑤止损率是同一件事的两面：入场贴近 `gate−0.5N` ⇒ 风险单位小 ⇒ 更易被打掉（round-2b「stop 贴 gate = whipsaw 机器」的入场侧同构） | 否【按 gate 结构身份给快车道分级授权】。4,417 ep / 149 票 / →，Kish 70.6–180.5 四类全部 ≥30（**④首次可裁决**，escape 那轮 n=9）：①风险N 四类**全部显著降**（−0.12~−0.18N）、③④判据全过，但**⑤止损率差四类全部越守卫**（+5.9/+8.3/+9.7/+11.9pp，CI 全排除 0）。②R 只有④过（+1.245，下界 +0.019 **脆**），③**显著为负**−0.672 [−1.402,−0.075]。**没有**否掉四分类判别式本身（它机械可算、②R 有可见排序 ③<②<①<④），只否掉「据此分级授权」；**没有**触碰 `fastlane_k=0.5`、ladder 权重、`bfloor_buf`、gate 画法 ~~授权侧无强制点~~ → **起有了**：`regime_transition._repair_domain` 把判别式接成 `_fastlane` 的⓪条件（非①一律 ×2，R1 彻底结案）。研究层 43 测试 21 变异；`core/episodes.py`（判别式真源，+12 测试）。**另有显示层**：`gate_ladder_report.py:151 PATH_TAGS` + `:155 gate_path_of` + `:170 retag` —— gate 行线名改由 `gate_path` 判定（突破线/箱体顶/新高线），用户  单独批准；⚡ 判据与 `_fastlane` **一个字没改**| ✅<br>🔴 **§12c：①对照臂数值重现了 `ladder_entry` C 臂却判据不过**—— live `fastlane_k=0.5` 的对照混淆了「执行结构」与「时点」两个自由度，见 A7 |
| `risk_sector_design.md`<br>08-08 | **不采纳**（分板块在数据上不成立，合上这个问题） | 无参数。三条**否定性落地**：①`shock_budget` 只留总额 15%，分板块那级**永久取消**（「不是暂缓」）；②**不新建**`core/risk_sectors.py`；③`core/sectors.py` 保持原样 | 否【用任何按共动性聚类得到的分组做风险板块限额】。159 票 / 117 冲击日：**判据①分离度过了**（k=6 sep 0.166 / k=8 0.178 / k=10 **0.233**，CI 排除 0）——比第一轮强，第一轮确实低估了；**卡死在判据③稳定性**：ARI 最高 **0.15**（<0.30），k≤5 **负数**——「拟合的是噪音」。**没有**做 beta 剥离（局限，不改方向）；**没有**触碰 `core/sectors.py` | 无。核实：`quant/core/risk_sectors.py` 不存在；`sizing.py` 风控链只有 `used_budget`/`used_gross` 两个标量、无分组 | ✅ **本批唯一「文档说不接线、代码确实没接」完全对上的一份」 |
| `trend_slots_design.md` §5<br>（SSS） | **不接线（FAIL：判据 c1/c3 双败）**——五点锁定（用户）+ 判据先于数字入 git（`448f5d9`），当日跑完当日裁决。按 用户 预立原则收尾：「若 0.85 不比 0.65 高 ⇒ 评分无信息含量，**这个结论本身反而非常重要**」 | 零参数进授权/sizing（「锁前零接线」兑现为「裁后也不接线」）。**Phase 1 观察层（AVWAP+RS，level_map [信号5]）独立于本裁决照留**——它从来不是 contingent。知识产出：①高 SSS 带**轻微反向**：meanR 0.387→0.300 递减、止损率 55.2%→61.1% 递增、T60 5.3%→3.7%——「买 salience 峰值」的 H-crowding 担忧直接可见于数据，与 gate 家系「贴强入场 ⇒ 止损率恶化」同构；②H-shadow 实证：confirmed 子集内 Δ(top−mid) +0.010 CI90 [−0.078,+0.103] 含 0——连续分在二值切分之上**零增量**| 否【SSS（锁定权重 0.30/0.25/0.20/0.15/0.10 + §5 五槽机械代理）→ f(S) 连续乘子挂 `n_unit`】。123,576 样本 / 163 票 / 2010+ / 步长 5：c1 败（参与三带 meanR 0.317/0.335/0.300 非单调；Δ(top−mid) −0.016 CI90 [−0.097,+0.073] 含 0）、c2 过（61.1% ≤ 59.5%+5pp）、c3 败。**没有**否掉：二值 probe/confirmed 现制（不动）／价格确认轴 A（NBIS 制，血统另有裁决）／AVWAP+RS 观察层／权重其他配置（预注册单点不网格——负结果对**这一**配置成立）。§3b open-risk 回收：前提（SSS 有信息）死 → 按 §6 park，重开须另立预注册 | 无（研究层 `sss_backtest.py`；`sizing.n_unit` 零改动） | ✅ 判据先锁 `448f5d9`，执行器零新自由度 |

### 1c. 未裁决 / 未立项（引用时不能当结论用）

| design | 裁决 | 采纳了什么 | 否掉了什么 | live 强制点 | 核实 |
|---|---|---|---|---|---|
| `earnings_gate_backtest_design.md`<br> | **未裁决**（三桶全部 Kish<30 数据不足，现行规则维持） | 回测**零采纳**。文档内唯一 live 生效的是**先于回测的 fiat**（D4，用户）：财报 clamp 窗口 = **print 日 − 7 自然日**，字段 `constraints.from` | **没有任何东西被否掉**——是「不裁决」不是「不采纳」。443 ep：im<5 Kish 15.1 [−0.66,+1.09] / im Kish 27.3 [+0.90,+3.83] / im>10 Kish 14.3 [+6.68,+27.9]，三格全 insufficient。⚠️ 被闸门挡住的是「解除低 IM 桶 clamp」，**不是**否掉 H-proposal：点估计**全桶为正、方向支持它**，但「按预注册不得作为裁决依据……正确用途是设计 round-2」。宏观 gate（FOMC/CPI）划为域外。复测：+12 个月（im 桶预计率先过 30） | `sizing.py:317 constraint_window_state()`（:338/:350/:411 消费）—— **但对 live 卡全部空转**：clamp 只在有 `max_classification` 时发生（:344 早退），而 v3 已把财报 clamp 从六张卡全部移除 | 🔴 **A1：符号在、强制点空转**<br>⚠️ **N2：点名四卡只落三卡（LITE 无 constraints）**|
| `risk_unit_design.md`<br>08-06/07 | **未裁决**（未立项执行）；(a)(c) 已被别处否掉/取代，核心 **(b) 仍悬空**| 无，**零参数进 live**。三候选：(a) stop 跟随入场 / **(b) at-risk 天花板 `size = min(名义 cap, at-risk cap / stop 距离)`**← 倾向 / (c) 入场距离限制 | ①**「单笔 ≤0.5% NAV」这个数被否**（当前书里一条都不会触发，要咬住 MRVL 得收到 ~0.25-0.3%）——**否的是数值不是路线**；②(a) 有反向证据（round-2b 实测 `bfloor` = whipsaw 机器）；③**文档外**：`shock_budget §0` 声明**取代 (a)(c)**、`ladder_entry §9` 让 (a) 二次死亡。**(b) 至今未被任何裁决覆盖，也未接线**。已实测的问题量化仍成立：同一 $429 名义上限产生 at-risk **$13–$66 = 5 倍跨度**；放大几乎全来自确认第二天（MRVL D2 +1.22N / NVDA +0.95 / LITE +0.89 / MSFT +0.33） | 无 | ⚠️ **N1：`available_cap()` 只有三项（分类/预算/gross），无 stop 距离项**<br>🔴 ladder_entry 已把 5.5% 失败成本**外包给 (b)**，而 (b) 不存在 |

---

## 2. ⚠️ 命名冲突表

> 读法总则：**符号名、状态行、分支字母都推不出裁决方向。**引用任何一个词之前先问三件事：
> **哪份文档 / 哪一层（授权 vs sizing vs 提醒）/ 哪个自由度。**

### 2a. 🔴🔴 高危（已经害过人，或会直接改变交易动作）

| 词 | 在 A 文档指 | 在 B 文档指 | 消歧读法 |
|---|---|---|---|
| **快车道 / fastlane**<br>（本仓已弃用此词，新稿叫「ladder 的起步日」） | `fastlane_repair_gate`（08-07）= 修复 gate 上**幅度替代时间、全额提前一天入场**→ **不采纳**（判据 1 四格全败）<br>`escape_velocity`（07-31）= 箱体顶突破**量比≥2 提前一天**→ **不采纳**（`C_keep_2day`，代码常量却叫 `A_fastlane`）<br>`gate_path_taxonomy`（08-08）= **按 gate 结构身份分级授权快车道**→ **四类全否**（②③④维持 ×2；①对照臂不裁决） | `ladder_entry`（08-07）= **分档提前**→ **采纳，k=0.5，已进 live**| **live 里存在的 fastlane 是转移层的**（`regime_transition.PARAMS["fastlane_k"]=0.5`，只授权**转移 + 画 zone**）。入场层的 fastlane 是被否的。`grep fastlane` 推不出方向。**「D1 收盘全额买入」至今零证据支持**；**「ladder 分档提前」的证据也只有那次混淆了执行结构与时点的对照**（A7）。一个词现在承载 **1 采纳 + 3 否决**|
| **k**（幅度门槛） | `fastlane_repair_gate` k ∈ {1.0, 1.5} → **两个都否**<br>`ladder_entry` k=1.0 → **否**（判据②） | `ladder_entry` k=0.5 → **采纳**| **k=1.0 是两份文档共同的失败格**。live 的 0.5 只有 ladder_entry 的证据。另外三个不同域的 k：`risk_sector` 聚类簇数 2–10、`risk_unit (a)` stop 倍数（未定值） |
| **0.5N**（三条，同一次交易里同时存在） | `fastlane_k` = gate **上方**0.5×ATR20（转移触发门槛） | `bfloor_buf` = gate **下方**0.5×ATR20（stop 地板，采纳）<br>`repair_reclaim_offsets` = D1 close **下方**0.5N（第二档限价） | 口头说「0.5N」必然歧义。必须带**基准 + 方向**：「gate 上方」/「gate 下方」/「close 下方」 |
| **财报 / earnings gate·窗口**| `earnings_gate_backtest`（授权层）=「能不能进」的 `max_classification` clamp → **未裁决**，且 v3（08-04）已把强制字段从六张卡全抽掉 | `earnings_window`（sizing 层）=「那一天能有多大」的 T+0 缩仓 → **采纳并接 live**| 引用必须带层名。「财报不裁决」≠「财报缩仓也没生效」。`core/earnings.py` docstring 专门写了这条边界（「这不违反 v3」）。**⚠️ CLAUDE.md 现在仍写「cap = 纯价格结构」，与 live 冲突，见 A5**|
| **量比 / 1.2**| `escape_velocity` FAST 资格 = **绝对量比 ≥ 2**（20 日均量）→ **随主裁决被否**<br>`gate_ladder_report` 的 ⚡ 标记 = **个股绝对量比 ≥1.2**，纯 tape 读数**不参与授权**| `relative_volume` = **RelVR ≥ 1.2**（个股量比 / max(SMH或QQQ量比, 0.4)）→ **采纳并接线**| 同一份 TG 报表里同一个数字 1.2 有两个身份。⚡ **不等于**量腿过了。ladder_entry 快车道里「量腿照旧」指的是 **RelVR 那条**|
| **板块 / sectors**| `risk_sector`「风险板块」=「哪些票会一起跌」→ **否**（ARI 0.15） | `core/sectors.py SEMI_SMH`「量比基准表」=「用哪个指数当分母」→ **采纳并接线**（relative_volume R） | 两份 doc 都显式划界，但 `shock_budget` 第一轮判据 4 **恰恰就是拿 SEMI_SMH 当代理跑的**——这个混用正是 risk_sector round-2 存在的理由 |
| **cap**（至少四义） | `CAP_FRACS` = **名义天花板不是额度**/ `NOTIONAL_CAP` 40% = 单票名义 / 集中度 cap（原 25%→40%） | **`available_cap()` = 实际额度的唯一真源**（三条取小后再乘财报缩仓） | 单说「cap」无意义。看到卡上 40% 而手机备注 35%，差额来自组合层 binding，不是数据错误 |
| **confirmed**| 入场**分类**词（仓位层 probe/confirmed 档位） | **单票名义上限 40%**（`sizing.py:38` 注释直接写 `= NOTIONAL_CAP`） | 两义共用同一个数。这违反 「一个词只属于一层」——改任一个的语义会连带改另一个 |
| **缩仓比例**| 文档 §10e 公式 = `min(1, X_normal/X_earn)`，m=0.7 在**外面再乘**| live `core.earnings.shrink_ratio()` **返回值已含 EVENT_MULT=0.7**| **同名不同值，差 0.7 倍**。§10d 表的「NVDA 85% / AAOI 26%」是不含 m 的口径，live 对应值是 **60% / 18%**|

### 2b. ⚠️ 中危（会读错结论，但不直接改动作）

| 词 | 冲突 | 消歧读法 |
|---|---|---|
| **分支/臂字母 A / B / C**| `escape_velocity` C = **维持现行**（否掉快车道）｜`earnings_window` C 臂 = **被采纳的收缩估计量**｜`earnings_gate` C = 保留 clamp｜`repaint_trigger` C = **被否的提案臂**｜`ladder_entry` C 臂 = **被采纳的 ladder**｜`regime_field` 方案 a = 采纳的渲染裁决｜`relative_volume` 分支 R/N/K｜`gate_backtest2` D1 分支 3 | 字母跨文档**不共享含义**。`repaint_trigger:6` 一句里就撞了：「B…（escape_velocity 裁决 C 同族）」——那个 C 与本文档的 C 语义相反 |
| **T**| `poc_*` T = **阈值**（T=1.9N / 0.1N） | `relative_volume`/`gate2`/`fastlane`/`ladder_entry` T20/T60 = **终点视界（交易日）**|
| **D1 / D2**| `fastlane`/`ladder_entry` = **交易日**（D1 = 首个 close>gate 那天） | `gate_backtest2` D1/D2/D3 = **待裁决的决策问题编号**（「分支 3」= D1 的第三分支） |
| **起算点**| `ladder_entry` 终点 = **D1+60**（自决策点） | `gate_backtest2` = **T+60**（自破位日） | 跨文档比数字前必须先对齐起算点 |
| **HVN**| `hvn_gate` = gate 锚 → **不采纳**| `ladder_entry` B2 基线臂 = 挂单价代理（后输给 C）；`level_map` HVN/LVN = **live 分析工具**| 否的只是 **gate 锚这一个用途**|
| **label**（POC 两轮） | round-1 = **HVN 带边缘移动**（volume 源），poc60 AUC **0.812**、基准率 59.4% | round-2 = **pivot 锚 zone 组移动**（price 源），同一 poc60 只有 **0.552**、基准率 90.7%，且 poc60 vs poc20 胜负**反转**| 引用「poc60 的 AUC」不带 label 版本就是错的。**任何仍拿 0.812 支持「改成 60」的引用都是错的**|
| **60**| `poc_window` 否掉 POC_WINDOW=60（**漂移度量窗口**） | **60 根 profile 是 level_map / volume_profile 的现行采纳口径**| 「60 被否」≠「60 根 profile 被否」——卡片结构就是用 60 根画的 |
| **摘除 / 退休**| `poc_window` ②POC 维**摘掉**（已执行） | `relative_volume` W3「量腿退休」**未触发，量腿保留**（拆掉止损率 +6.4pp） | 8/6 与 8/7 相邻两天，两个「拿掉一个维度」结论相反 |
| **「不采纳」三种强度**| `fastlane` = **判据失败**（没证明有用）｜`hvn_gate` = **平手保留 incumbent**（没有更好）｜`ladder_entry` k=1.0 = **判据①过②失败**（风险降了 R 没赢） | 三种不可互换引用 |
| **拒绝权**| round-2 给 cluster **正面**结论（vs 不设 gate，−9~−14pp 显著） | `hvn_gate` 给 HVN **平手**（vs cluster 的配对差） | **判据全过 ≠ 采纳**——「四判据只给各自的 CI，不给差的 CI」。这是 hvn_gate 最容易被误引的一句 |
| **保费**| `gate2` = **expectancy 折损 ≈1pp/episode**| `earnings_window` 保费比 ≤100% = **比率**| 同词不同量纲。且 gate2 的「保险不是 alpha」只对**主池（深度破位）**成立，副池预注册即排除 |
| **止损率差 +5pp**| `escape_velocity` 预注册判据参数（入场锚定 2×ATR20 止损） | `fastlane` 换了止损模型（gate 锚定 `bfloor_buf`），并明写「escape_velocity 的止损模型把要测的机制消掉了」 | 同名指标**不可横比**|
| **2N**| `gate2` 主止损口径 = 入场价 − 2×ATR20（对称，归因唯一性用） | `position-tiers`/trend-exit 的 2N = 趋势死亡线家族 |
| **X**| `shock_budget` `SHOCK_X = 1.88` = 单日跌幅分位倍数（**已进 live**） | `risk_unit (c)` X = 入场距离倍数（未定值）；`earnings_window` X_earn = 财报日跌幅分位 |
| **at-risk vs occupancy**| `risk_unit` at-risk = `size × (entry − stop)`（**stop 距离**口径，未接线） | `shock_budget` occupancy = `敞口 × 1.88 × ATR/价`（**单日跳空**口径，已接线） | **别因为「冲击预算已接线」就认为风险单位缺口关闭了**|
| **预算**（三义） | 冲击预算 15% = 组合**单日损失**上限 | 「本轮 ladder 预算」= 分类 cap（brief 用语）；gross 110% = **敞口**上限 |
| **source**| `current_regime.source` ∈ {card, auto, demoted, promotion} | `broker.source` ∈ {moomoo, ibkr_stockholm} | 同一份 JSON 两个 source，含义完全无关 |
| **since**| card 源 = **结构锚定日**（Today 白名单消费锚） | 机器源 = **转移日**（`regime_transition.py:358`） | 区分依据是 `source`。铁则 2 只规定了 card 源那一半 |
| **cap 0**（两种不授权） | 「修复观察」cap 0 = 等 gate | 「持仓·无加仓授权」cap 0 = 入场授权已行使 | entry_guard 输出都是 VETO，**解除条件完全不同**，只靠 reason 字段区分 |
| **stop:lock 的 lock**| 这里 lock = **棘轮地板**（纯粹因为 `stop:ratchet` 12 字符超限） | 另有 no-add lock（Concentration Override）、Today 组「锁/过期 = 吊销」 | 手机上看到 `stop:lock` **不表示任何锁仓/禁加仓语义**|
| **trail**| 退出栈 = **趋势死亡线**（3×ATR / parabolic 5d×0.92） | entry/zone 侧 parabolic 锚 = **trailing 5d/10d high**| 历史事故：散文版简报「引用了比卡片 stop 松 6.2% 的 trail 当风险线」 |
| **binding**| 提醒层 = **合成后实际会先响的那条线**| sizing/today_group = **哪个 cap 在收紧**| CLAUDE.md 同一段里两义并存 |
| **trim**| `exit_line_reminder` 严格限定为**上方止盈线**（下方线禁写 `trim:type`） | `repaint_trigger`/`escape_velocity` 用 trim[1] 作**结构上沿代理**；positions 卡与 watchlist 卡还有**相反规定**|
| **基准 vs 基线**| `relative_volume`「基准」= QQQ/SMH 量比**分母**| poc 两份「基线」= 预测力对照（时间 / ①price_drift）；`gate2`「World A（基线）」= 对照世界 | 中文近音近形，极易串 |
| **世界命名法**| W0–W3（relative_volume）｜World A/B（gate2）｜FAST/SLOW（escape_velocity, fastlane）｜A/B1/B2/C（ladder_entry） | 每份一套，跨文档说「W1」「A 世界」互不通用 |
| **白画率 vs whipsaw 率**| `repaint_trigger` 白画率 = C 触发后 5 bars 内跌回带下（40%） | fastlane/ladder 的 whipsaw 率是同类不同定义 | 勿互相引用数值 |
| **①price_drift「打赢基线」**| AUC 0.6992 全场最强 | 但 `core/drift.py` 整模块仍 **advisory only 不裁决**| 「打赢基线」在本仓不等于任何授权含义 |
| **g01**| `exit_guard` 的 G-01 家族档位 | `price_reminder_sync.G01_GAINS` 是**复制**的常量（不能 import decision 层） | 有意的重复真源，**改档位两处同改**（见 §3.5） |

---

## 3. 分叉清单

**25 条**（不含 §3.0 的 P0 与 §3.5 的 7 条已知债务）。按危险度排。

> ✅ **全量复核**（30 条 = 23 分叉 + 7 债务；12 个核实/复核 agent，逐 claim 对 live）：
> **17 确认 / 8 仅行号漂移 / 3 部分错（R2·R3·A6，已就地改）/ 2 条账本自己错（V5·§3.5-5，已改记误报）**。
> 五条实质修正全部经对抗复核维持。两种错误机理都值得记住：**引用截断**（V5 把同一括号里的
> 修正语截掉了）与**映射失误**（§3.5-5 读到了点名 z1 备注的原文却没认出它就是 price_reminder）。
> ⚠️ **行号以写账 commit `75bea17` 为锚，一天内已实测漂移**（08-08/09 大改）——引用条目时
> **grep 符号，不抄行号**；本轮只改正「写账当时就错」的两处（:838→:837、:55→:54），
> 漂移的不逐条追（追了明天又漂）。

### 3.0 ~~🔴🔴🔴 P0~~ ✅ **已解除（复核）**

**7 个文件已全部入 git**，commit `2a407f6`「冲击预算第二轮接线: 实际 cap 单一真源 + 财报缩仓」。
逐个 `git ls-files --error-unmatch` 复核过，无一遗漏。下方原文保留作案底。

> ⚠️ **读账本的人注意**：这条在写下当天就已经过期了。**引用本文件任何一条之前先复核**——
>  另有 R1 一条，agent 标 `REJECTED_BUT_LIVE`，实际性质是「采纳域越界」。
> 账本是索引不是真值；真值在代码里。

**~~`earnings_window` + `shock_budget` 第二轮接线的核心文件全部未入 git，只活在工作区。~~**

```
?? quant/core/book.py            ← 被 entry_guard / portfolio_brief / today_group_sync / price_reminder_sync 四处 import
?? quant/core/earnings.py
?? quant/data/earnings_calendar.py
?? trade/us_stock/market/earnings_calendar.json
?? tests/core/test_book.py  ?? tests/core/test_earnings.py  ?? tests/data/test_earnings_calendar.py
```

消费侧全是 ` M `（未提交修改）。最后一次相关 commit 只到 `f951159`。
**一次 `git clean -fd` 或换 checkout → pipeline 的 brief 步（fatal=True）直接 ImportError 全线变黑，
且 git 历史里没有任何东西可以恢复。**memory 里「保留现场 / 手工步骤 = 这一步不存在」的教训在这里以最纯粹的形态复现。

### 3.1 🔴🔴 REJECTED_BUT_LIVE（5 条）

| # | 分叉 | 证据 | 它会让哪个决策出错 |
|---|---|---|---|
| **R1**✅✅ **彻底结案 **：适用域判别式**已接进 live**（用户 批「接判别式」，前提「跑完回测再动」满足）。`regime_transition._repair_domain` 前置于 `_fastlane`：非①修复或判不了 → 拒发快车票维持 ×2；判据只有 `broke` 一道 = `core/episodes.breakdown_in_window`（**与回测同一份**，决策点 E 当初就是为这一天立的）；序列 = 深度+常规按日期去重合并，断层/不足 180 根 = 保守拒。报表同拍镜像（`fastlane_state` 读 `cls` key 非显示名）——live 实测 MRVL 223/MU 945/AMZN 278.6 显「⚡域外 — ×2 恒定」，6 张①卡照旧。7 处变异确认会红。<br>（中间态：四类全否 ⇒「不划域」暂时无害；A7 的证据债经 ladder_timing + stop_cost_guard 清偿后，①有据②③被否的**不对称**裁决使划域成为正确动作） | ~~**`_fastlane` 没有适用域判别式，突破 gate 也走快车道**~~（域已裁：三类都不该走；`_fastlane` 至今 0 次真实触发） | `regime_transition.py:232` 挂在 `next_regime.entry_gate` 分支上，对 gate **类别不做任何判别**。但它的证据基座只有修复 episodes（`ladder_entry_backtest` import 自 `gate_backtest2`，主池 = 60 日最低收盘且 close<MA60 的**深度破位**）。live 已有非修复 gate 在等：watchlist **MSFT**`R_突破_473 → R_突破_500 gate=500.0`、**AMZN**`常规参与 → R_突破_279 gate=278.6` | MSFT 500 / AMZN 278.6 这类**突破线**上，首日收盘超 gate+0.5N 且量腿过 → 当日机械转移 + 写新 `since` → **Today 白名单当日重新授权**+ zones/stop 整套上移，全部依据一个只在破位重进样本上验过的 k。而 `escape_velocity` 否掉的恰是「上行箱体顶突破的快车道」。命名坑已被 `gate_ladder_report.py:51-57` 注释钉住，**唯独适用域没人钉**|
| **R2**✅ **已裁决结案 （用户：「直接按最新规则，以前讨论过的不必理会」）**—— NBIS 规则（更晚）覆盖**促升持仓加仓授权域**；「永不」保留辖新开仓/常规 zone 加仓（无更晚裁决触及该域）。live 代码不动，`position-tiers.md` §Gate 授权域已补 precedence 注。〔原复核定性存档：〕不是「被否之物在 live」——是**两条已采纳裁决冲突**（round-2 分支3「加仓永不等 gate」 vs **NBIS 规则**（regime_field_unification 方案 a，用户 批，design :29/:65 显式写明），后者更晚），**无覆盖标注**。与 R1 同族的状态词误标。~~解法 = **提请 用户 裁决谁覆盖谁**~~（已裁） | **「持仓加仓等 gate」在 live，与 round-2 分支 3 冲突且无 precedence 标注**| `structure.py:82-87` positions 分支写 `{"name":"持仓·无加仓授权","cap_pct_nav":0,"participation":"gate_only","reason":"入场授权已行使; 加仓授权待 gate 达标转移 (NBIS 规则)"}`。live 实例 NBIS：一张**未破位**的持仓卡，加仓额度 0→40% 完全由 gate 231 解锁。而 `.claude/rules/position-tiers.md:198` 写的是「**新开仓与常规加仓永不引入『等 gate』条件**」 | round-2 机械读数是 12 主格 expectancy 全负（−0.8~−1.4pp），保留 gate 只因它在**修复路径**上是保险。把 gate→cap 升级搬到常规加仓 = 分支 3 明确排除的那一半，按回测就是**纯付保费**。两条规则（一条在代码、一条在 rules）都在 live，**没有任何地方标注谁覆盖谁**|
| **R3**✅ **已修 **（四处 stale 字符串全清：`pipeline.py:147`→四维、`core/__init__.py:12`→四维、**`tasks/overnight-briefing.md:88`**（复核新发现——它才是真正每日进简报链的那处，还把已证伪的②维标成「核心维」；原条目引的 pipeline desc 只在 `--dry-run` 打印，传播路径 claim 不成立）+ live 副本 cp 同步） | ~~「五维 / POC 迁移」描述字符串仍在跑~~ | `quant/ops/pipeline.py:147`「结构漂移观测 (**五维**: 离锚/**POC 迁移**/区间外/gate 可达/波动率)」——**这条文本进 pipeline 输出 → 进简报**；`quant/core/__init__.py:12`「drift 结构漂移**五维**度量」。实现侧已是四维（`drift.py:26-31` 摘除声明，THRESHOLDS 无 `poc_shift_n`） | 简报里读到「五维含 POC 迁移」会以为供需重心那一维还在观测。更糟是反向：下次想「校准 POC 阈值」的人会从 pipeline 描述出发去找一个不存在的字段，或**照着描述把它加回来**|
| **R4**| **`gate.unlocks` 仍在简报渲染**（起禁） | `.claude/rules/position-tiers.md:191`「`unlocks` 经全仓验证为 display-only，**起渲染层不再读它**」。但 `portfolio_brief.py:1040` 仍读：`f"{gate.get('unlocks') or nxt.get('name') or '?'}{cap_txt}"`。`gate_ladder_report.py:125` 确实已停读 = **两个渲染层不同步**。live 数据 AAOI/INTC/NBIS/MU 的 entry_gate 都带 `unlocks=confirmed` | 简报用旧标签命名 gate 解锁到哪一档，而授权真源是 `next_regime.cap_pct_nav`（现在全是 0.4）。「confirmed」在 08-01 语义分层后已被禁止出现在 gate 行——**它正从这一行漏出来，且是 fired_cards 原文推送那条链**|
| **R5**| **`poc_window.py:265` ACTION 文案指向已删符号**| `"ACTION: **保留 ②POC 维, 且 POC_WINDOW 20 → 60**"` 仍是可触发分支，而 ②维已摘、`POC_WINDOW` 不再存在。同族 stale：`tests/research/test_poc_calibration.py:8/:132` 注释把守卫归给 `drift.py`（测试本身断言 research 模块，不会红） | 复测时重跑若走到该分支，机器会打印一条「改 live 参数」的行动项指向已删除符号——**而这台机器的输出正是被当作裁决收据引用的**|

### 3.2 🔴 ADOPTED_BUT_ABSENT（7 条）

| # | 分叉 | 证据 | 它会让哪个决策出错 |
|---|---|---|---|
| **A1**| **财报 clamp 强制点对 live 卡全部空转**| `sizing.py:317 constraint_window_state()` 存在，但 clamp 只在有 `max_classification` 时发生（`:344` 早退），播报也只在 max_classification/touch_protocol/vehicle 三者之一存在时才产行（`:404-433`）。live 卡：MRVL `{reason, until:, from:}`、NBIS `{reason, until:, from:}` —— **两张都无 max_classification**（v3 08-04 已从六张卡全部移除） | 读 positions.json 看到 `from`/`until`/财报 reason，会以为财报 clamp 已武装；实际 entry_guard 与 gate_ladder_report 对 MRVL/NBIS **一个字都不显示、一分钱都不收紧**。附带：MRVL `from=8/13` 与 earnings_calendar 的 pub_day 8/27（print−7 = 8/20）差 7 天，**因为整条约束不生效，没人会发现它没跟着官宣日更新**|
| **A2**| **财报缩仓只实现了「那天不许再加」，没实现「那天之前要减到线下」**| 唯一强制点 `sizing.py:198-199 event_shrink` 只压这一笔还能下多少。全仓 grep 无任何「已有敞口超过缩仓后 cap → 减仓」告警：`portfolio_brief` 的 10 处 alerts 无一与 earnings 有关；`event_shrink` 在 brief 里唯一出口是预算行尾巴（纯文本）。裁决原话是「**反应日前一交易日收盘前减完**」 | LITE 8/12 反应日缩到 ×0.24：若 8/11 收盘时持仓已在名义 cap 上，8/12 系统只会把预算行显示成 `余 $0`，**没有任何一条 alert 会响**。规则里真正管风险的那半（把 13–21% NAV 的单日敞口压下去）**没有代码**|
| **A3**| **`bfloor_buf`（stop = gate − 0.5N）无写入强制点**| 只有两个触点：`entry_guard.py:211 floor_gate_gap_lines()` docstring 自陈「**advisory only, 不改判**」，阈值 `FLOOR_GAP_ATR = 0.25`；`gate_ladder_report.py:60 BFLOOR_BUF = 0.5` 只在卡片没写 next_stop 时**推算显示**。真正写 stop 的两条路径都不管这条约定（`regime_transition.py:326-329` 原样搬卡片手画值；`update_position_zones.py` grep 不到任何 0.5N/gate 校验）。live 六张卡目前**恰好**合规（0.47–0.57N），靠画卡的人记得 | 一次重画把 stop 画到 gate 上（= `bfloor×1`，round-2b 实测**全场最差**、止损率反而高于不等 gate 的 whipsaw 机器），系统只多一行 ⚠️——**不 VETO、不拦 schema、不改 STALE**。而且 0.25N 告警线比 0.5N 约定**松一半**，画在 0.3N 的 stop 连告警都不响 |
| **A4**✅ **已补 **（`trading-discipline.md` parabolic ladder 节前加分流段：repair_reclaim 锚 = close/close−0.5N/close−1.0N、地板 = 刚收复 gate，与 5d/10d 锚两套价位互不适用） | ~~**`repair_reclaim` ladder mode 在 rules 与 CLAUDE.md 零记载**~~ | grep `repair_reclaim｜修复.*ladder｜收复.*三档` → **零命中**。而 `trading-discipline.md:112-116` 把**唯一**的档位锚点写成 parabolic 那套（`current×0.98-1.00 / 5d高×0.92-0.95 / 10d高×0.85-0.90`），§入场前 5 自检第 3 条硬性要求「首档是不是在 current×0.98+」。live 的 repair_reclaim 锚是 `close / close−0.5N / close−1.0N，地板 = 刚收复的 gate` | rules 是交易讨论的**行为触发层**。修复 gate 刚收复的票，代码给 N 锚三档、文档给 5d/10d 高点锚三档，**两套价位可以差好几个 ATR**。按 trading-discipline 复核会把 live 的合法二三档判成「greedy-bid trap」，或反过来照文档手挂一组代码根本不认的价位 |
| **A5**✅ **已补 **（CLAUDE.md v3 那行补「唯一 sizing 层例外 = 财报反应日 T+0 缩仓」及边界声明指针——按该行复核 live 不再会把 8/12 LITE 的 ×0.24 判成 v3 违规） | ~~**财报 sizing 规则在 rules/CLAUDE.md 零记载**~~ | grep 财报/earnings 的全部命中都是 v3 或更早的表述，无一提到反应日缩仓。CLAUDE.md:50 仍写「入场授权（gate/regime/zone/**cap**/档位）= 纯价格结构；财报…一律降为情报层 [FYI]」；CLAUDE.md:71 冲击预算条目列了 40%/15%/1.88/110%，**没有财报缩仓这一项**。免责边界只写在 `core/earnings.py:16-18` docstring 与 design §9 | CLAUDE.md 是每 session 加载的最高优先级文件，它现在明确说 cap = 纯价格结构。8/12 LITE 的 cap 被乘 0.24 时，**任何按 CLAUDE.md 复核的人（包括下一轮的我）都会把它判成「v3 违规」，并可能去「修」掉一条刚裁决通过的规则**|
| **A6**✅ **已补 **（`zone-maintenance.md` 量比句补 RelVR 相对口径；**复核新发现并同步修掉**：strategy 层 `zone-touch-confirmation.md:110` 的量比基准表**还在正面教绝对口径**——比零记载更主动的误导源，已换 RelVR。复核另纠一句：原条目「被否掉的 escape_velocity 在 rules 里有条目」不成立，写账当时 rules 对 escape/快车道零命中） | ~~**RelVR 在 rules/CLAUDE.md/tasks 零记载**~~ | grep `RelVR｜relative_volume｜SMH 为基准｜benchmark_for` → **零命中**。规则层唯一提到量腿的是 `zone-maintenance.md:133`「证据标准 … 触碰次数 + **量比**+ N 日站稳，任何一项被悄悄放宽 = 作弊」，完全没说这个「量比」自 08-06 起已是相对口径。对比：被**否掉**的 escape_velocity 和已定版的冲击预算都在 rules 里有条目 | 读 rules 画卡的人（和未来的 agent）会按绝对量比理解 `volume_ratio_min=1.2`，于是在「证据标准不得放宽」这条防作弊线上**用错尺**。个股 1.5× 但大盘 1.4× 的日子，绝对口径说过了、live 说没过（RelVR 1.07）。设计稿已实证个案会反转（INTC 8/5：SMH 尺 1.54 过 / QQQ 尺 0.94 不过） |

| **A7**✅ **已结案 **（`stop_cost_guard` 重判：⑤换代价守卫后 ANCHOR/ROLL 双双 support —— 「早一天」六判据全过，代价差 **−0.072N [−0.099,−0.045]**显著更省，连没选的 A 严格版也过。`fastlane_k=0.5` 的证据基座补齐 = ladder_entry 的分档半边 + ladder_timing 重判的时点半边。残留两条边界：最贴 live 的量腿格判据② R 含 0（机制级支持，窄池功效不足）；+12 个月本守卫盲测）| ~~**live `fastlane_k=0.5` 的采纳对照混淆了两个自由度，「早一天」从未被单独验过**~~ | `ladder_entry` 比的是 **ladder@D1 vs 单点@D2**（C vs B1/B2）—— 执行结构与时点绑在一起。它引 `fastlane_repair_gate`「D1 全额 ≈ D2 全额是平局」当前提，但**那一轮测的是全额提前，不是 ladder 提前**。`gate_path_taxonomy` §12c 把执行结构固定成 ladder、只放时点：D2 臂 R 由 **+0.194（单点）→ +0.320（ladder）**，R(F−S) 的 CI 随即含 0（①：+0.401 [−0.087,+1.106]）；且止损率差 **+9.65pp**。⚠️ **+11pp 这个数在 `ladder_entry` 自己那张表上就印着**（C 67% vs B1 56%），只是五条判据里没有一条管它 | `regime_transition.py:72 fastlane_k=0.5` + `:238 _fastlane()` 正在 live 授权「首日幅度达标即转移 + 写新 since ⇒ Today 白名单当日重授权」。按拆开后的读数，这条快路买到的是**更紧的风险单位（−0.17N）+ 更高的止损率（+9.7pp）**，R 上不显著。<br>**不是 `ladder_entry` 的翻案**（两份预注册、判据不同；①在本轮是对照臂不裁决），**是够格立项的新证据**。<br>✅ **已立项并跑完（同日，`ladder_timing_design.md`）——结论要改写一半**：<br>· ✅ **成立**：对照确实混淆了两个自由度；止损率代价真实且更大（三格 +8.9~+10.6pp）<br>· ❌ **我说错的那半**：「拆开后 R 上没有优势」**不成立**—— 在 `ladder_entry` 自己的池（量腿 off，1995+，n=1,477）上 **R(FAST−SLOW) = +0.226 CI90 [+0.069,+0.391] 显著为正**，ROLL 独立复现 +0.221。原话那个「CI 含 0」只成立于**带量腿**的池，是总体依赖的<br>🔴 **债务转移**：现在卡住 `_fastlane` 的是**判据⑤本身**，而它有两个未校准处 ——（a）**ITT 口径**：SLOW 未入场记 `stopped=False` 而 FAST 必定入场；按 `止损率÷入场率` 换成每笔已入场口径，三格条件差是 **+3.2 / +3.0 / +2.9pp，全在 +5pp 守卫内**（点估计，无 CI，未预注册故不作裁决）；（b）**+5pp 搬自 `escape_velocity`**，而那是**入场锚定 2N**止损下的参数，本轮是 **gate 锚定 `bfloor_buf`**—— §2b 自己写着「同名指标不可横比」。<br>**处置（用户 定）**：先别撤也别宣布有据；守卫预注册已立（`stop_cost_guard_design.md`，U=止损代价 / C=每笔已入场 已锁，T 待锁，代码在 T 锁定前拒跑）。⚠️ 此前写「原样可重判、不需重跑」在 U=代价下不成立——逐笔代价从未存过，需对同一确定性样本做一次**新计算**（同 collector、无新数据、无新抽样） |

### 3.3 ⚠️ VALUE_MISMATCH（9 条）

| # | 分叉 | 文档说 | live 是 | 它会让哪个决策出错 |
|---|---|---|---|---|
| **V1**| 「常规参与」regime cap | `.claude/rules/position-tiers.md:175`「写**分类天花板 0.05**」；`regime_field_unification_design.md:62`「快照（今 0.05）」；`annotate_event.py:16`「反查 CAP_FRACS (0.05→confirmed)」 | `structure.py:94` **动态读**`CAP_FRACS["confirmed"]` = **0.40**（`sizing.py:38`，08-08 前是 0.05）。live 卡实测 AMZN `cap_pct_nav = 0.4` | **差 8 倍。**按文档手算「这张干净候选卡今天能给多少」会低估一个数量级；或把一笔 40% 授权的成交误判成「超 cap 需书面例外」。反向更糟：若有人照文档把 `structure.py` 改回硬编码 0.05，会在无人察觉下把所有 card 源常规参与卡从 40% clamp 到 5% ——**正是规格铁则 1 警告过的那个方向**|
| **V2**| probe cap 与 ladder 权重 | `.claude/rules/trading-discipline.md:134`「probe cap = **1% NAV ≈ $227**@ NAV $22.6k」+ 整股算例（INTC@92→2 股 / MU@925→0 股 = 4.1× cap）；`:114/:127` ladder 首档写区间 `45-50%` | `sizing.py:39 "probe": 0.020`（v3 08-04 起 2%），NAV 实测 ≈$21.2k → **≈$425**；ladder 权重是精确的 `(0.50, 0.30, 0.20)`。对照组 `position-tiers.md:60/:70/:366` **已更新**到 2%/40% —— 说明 `wiring_todo §A`「文档与代码对齐」只对齐了一半 | trading-discipline 是**交易讨论强制触发**层。按 $227 算 `floor(cap/price)` 会在 $100–$425 价位带系统性少算一半股数，把能切 3–4 档的票判成「切不开→单档豁免」，**正好是 greedy-bid 禁令要防的形状**；书面例外的倍数也全错 |
| **V3**| `repair_reclaim_days = 10` 的单位 | `entry_guard.py:102` 注释写「限价挂单有效期 (**交易日**)」，回测采纳物也是 10 个交易日 | 实现是**自然日**：`:320` docstring 与 `:334` `(d1 - d0).days`（`date` 相减 = 日历天）。测试只钉数字不钉单位（`test_repair_reclaim_ladder.py:135`） | 10 交易日 ≈ 14 日历日 ⇒ live 窗口实际只有 **~7 个交易日，比采纳参数短约 30%**。表现静默：转移后第 8–10 个交易日回踩到二三档时，mode 已悄悄退回 normal/parabolic，ladder 锚从「close−{0.5,1.0}N，地板=gate」变成 z1/z2 锚 —— **同一结构事件两套挂单价，没有任何一行输出会说「窗口过期了」**|
| **V4**| 财报缩仓何时恢复 | 两条会被 用户 读到的文案都写「**T+1 收盘后恢复**」（`earnings.py:102`、`portfolio_brief.py:1073-1074`） | 实现是 **T+0 单日**：`earnings.py:86-88` 只在 `day == today` 时 active，ET 日历跨到 T+1 即 `shrink=1.0` —— cap 在 **T+1 零点**就全额恢复（design §0 锁定的也是 T+0 单日） | 播报说 T+1 收盘后恢复、系统 T+1 开盘前就放回全额。用户 若按文案在 T+1 继续按缩仓口径控 size，会与系统给的 ladder/z1 金额**差 1/0.24 ≈ 4 倍**。**文案与实现必须改一个，现在两个同时在 live**|
| **V5**❌ **复核判误报 **（对抗复核维持）：分叉**从未存在**——账本引 CLAUDE.md 时**把引用截断在修正语之前**，被截掉的同一括号里明写「 起这个数 = `sizing.available_cap` 算出来的实际 cap——名义分数再过冲击预算余额与 gross 余额」，且文档与代码是同一 commit `2a407f6` 原子落地（早账本 37 秒）。「手机见 35% 会被当数据错误去修」的危险场景不成立。保留作**引用截断**的机理样本 | ~~z1 备注里那个百分数~~ | CLAUDE.md「z1 备注带 **比例+起步预算**`z1:2%:450`（天花板型显 probe 起步、机器源显 regime cap）」= **名义**分数 | `price_reminder_sync.py:107-108`「⚠️ `frac` 从  起是**实际**cap 分数（已过冲击预算与 gross 余额）」；`:118-121` 还加了 `<1%` 记号处理财报缩仓后的非整数 | 手机响铃看到 `z1:35%:7k`，按 CLAUDE.md 去卡片上找那个 35% —— 找不到（卡上是 40%）。差额来自组合层 binding，而 CLAUDE.md 那段**没有这个概念**，会被当成数据错误去「修」 |
| **V6**| `REL_FLOOR = 0.4` | 设计稿声明它是「单点预注册，不网格」 | **两份独立常量，无共享 import**：`regime_transition.py:91` 与 `relative_volume_backtest.py:57`。回测只从 `core.sectors` 拿冻结表，分母地板与阈值都是自己那份（`sectors.py` docstring 承诺的「live 与回测同表同源」**只覆盖表**） | 复测（+12 个月）时改 live 的 0.4 而忘了回测那份（或反过来），回测会安静地验证一个 live 已不再执行的口径，**两边都不报错**。「不网格」恰恰意味着它是唯一不会因扫参数而被重新对齐的数 |
| **V7**| 测试条数 | `exit_line_reminder`「18 项 / 全仓 **612**绿」；CLAUDE.md:39「pytest **396**项」；`poc_window`「15 条」 | 现跑：全仓 **881**collected；`test_price_reminder_sync.py` **22**项；`test_poc_window.py` **13**项 | **三方全过期**（396 / 612 / 881 互不相同）。别用任何一处总数判断「测试有没有被删掉」；要判断只能现跑 `--collect-only`。守卫本身健在（`test_poc_window.py:51` 源码守卫、`:108` 三选一强制、`:115` 常量钉死） |
| **V8**| 设计稿状态行 stale | `gate_backtest2_design.md:3`「状态：草案，待 用户 裁决 D1–D3。批准并锁定 D1 之前**不运行、不看任何结果数字**」；`ladder_entry_design.md:3`「⏳ 待 用户 锁定。锁定前不跑」 | 两份都已跑完、已裁决、（ladder_entry）已接线。gate2 报告 115KB @ 03:59、gate2b 60KB @ 17:41，`entry_guard.py:212` docstring 已写「round-2b 裁决（用户 批准）」 | 只读顶部会误判为「这份还没跑」，从而重开一个已裁决的问题；或以为 `bfloor_buf` 尚未获批而继续按 `bfloor×1`（**全场最差变体**）画地板 |
| **V9**| 文档内自相矛盾且未标废 | `earnings_window_design.md` **有两个 §10f**（:213「没有可挂靠的地方」与 :229「附带产出」重号）；:214-216 写 live 是 `{confirmed: 0.05}`、「40% 与冲击预算都还没进 live」、:227「已裁决但未生效的设计」、:233「不动 live：entry_guard/sizing 一行未改」。`shock_budget_design.md:363` 同病：「不动 live：`CAP_FRACS` 一行未改」 | 全部被同日追加的 §10g/§15b 推翻，旧段**无任何废止标记**。实测 `sizing.py:38/64-67` 已是 0.40/0.15/1.88/1.10 | 按小节号引用（「见 §10f」）会指到两个不同地方，其中一个说这条规则没生效。这两份是财报缩仓与冲击预算的 canonical，**下一个来核对的人有一半概率读到「未生效」并据此判定 live 行为是 bug**|

### 3.4 ⚠️ NOT_IN_CODE（4 条）

| # | 分叉 | 证据 | 它会让哪个决策出错 |
|---|---|---|---|
| **N1**🔁 **同日二裁翻案 （用户：「还是要立项的 初步给定单笔止损后需<=5%」）→ 立项并当日接线**：`AT_RISK_CAP_FRAC=0.05`（单笔止损后损失 ≤5% NAV），规格 `risk_unit_design.md` §b1（正股 binding stop 口径 / 期权保费全额 / 不乘 1.48 系数）；入场侧 = size_caps 第⑤条（后备），持仓侧 = `at_risk_breach_signal`（真咬点，GOOGL LEAPS 日 1 超 ~1.9× 已知）。〔第一裁存档：〕✅ ~~**结案 （用户：不立项）**~~。边界注记（防误引）：用户 问「不是已经靠 NAV 15% 那条控制了吗」——**严格说没有**：15% 是 ATR 冲击尺（1.88×ATR20，答「全书今天挨打亏多少」），不是 stop 尺（答「这笔止损打掉亏多少」），贴线 bfloor stop 下两尺差  倍，**日后别引 15% 当单笔 stop 控制**。不立项仍成立的理由：①入场侧 N-unit（0.75%NAV/2N）已近似单笔上限；②「用 at-risk cap 管」针对的 fastlane 失败成本已由 stop_cost_guard v2 代价守卫直接量化并 support（−0.072N 显著，A7 结案）——(b) 的主要动机已付讫；③剩余 $13–66 五倍跨度在 probe 规模 = 0.06–0.3% NAV，二阶。**复开条件**：confirmed 档大仓+远 stop 常态化（单笔 at-risk >1.5% NAV 出现）。 | **`risk_unit` (b) at-risk 天花板不存在**| `sizing.py:167-215 available_cap()` 只有三项（分类/regime cap、shock_budget、gross），docstring 自称「这个函数是那个数的唯一真源」，全函数不出现 `entry − stop` 的任何形式。`entry_guard.py:407` 的 n_unit 用的是**假设 2N 止损**的名义单位，不读卡片真实 stop 距离 | ladder_entry  裁决把「D1 幅度达标后 5.5% 次日跌回 gate 下」的风险**外包给 (b)**（`regime_transition.py:71` 注释原文「用 at-risk cap 管，不作否决理由」）——**一条已进 live 且已在放宽转移速度的规则，其风险控制指向一份待跑设计**。且 `sizing.py:56-62` 自承 40% 一旦 binding 冲击预算在单笔层面空转 ⇒ **risk_unit §0 的 $13–$66 五倍 at-risk 跨度在 live 里仍然没有任何东西管着**|
| **N2**| **文档点名「四卡落 from」，LITE 卡根本没有 constraints**| 全量扫描带 constraints 的卡只有 5 张：MRVL / NBIS / NVDA（三者有 from）、AMZN / MSFT（只有 touch_protocol+vehicle）。**LITE 卡存在**（watchlist，`B_repair_probe_716` cap 0.02）**但 `constraints` 整个键不存在**。而 earnings_calendar 里 LITE 是 `next_reaction_day  / pub_type 2 / implied_move ±13.5%` —— **距今 3 天**| 若引用「四卡都有 from」论证覆盖完整，最近财报那张（也是隐含波动最大那张）会被当成已被窗口机制管着。实际它连那两个空转字段都没有。⚠️ 缩仓侧另有独立通路生效（`core/earnings.py` 直接读 calendar，不依赖卡片 constraints）——**别把这两层混为一谈**|
| **N3**| **`source: "transition"` 是幽灵取值**| 设计稿规格表写转移机产物 `source = transition`，live 写的是 **`auto`**（`regime_transition.py:168/:333/:411`）与 **`demoted`**（`:299`）。`schema.py:144` 合法集文案是 `card｜auto｜demoted｜promotion`。**设计稿是唯一用 `transition` 这个名字的地方**| `source` 是所有权判别式（`derive_card_regime`：非 `card` 即 `kept_machine`）。按文档写一个 `source:"transition"` 的卡不会报错也不会被 derive 覆盖 —— 它会**永久卡在「机器源」保护里，重画永远不刷新它的 cap**|
| **N4**| **`source: "promotion"` 同样是幽灵，且 schema 不校验枚举**| 促升路径实测写 `card`（`watchlist_migrate.py:99` → `structure.py:82-86`，live 印证 NBIS `source=card`）。`promotion` 全仓只出现在 `structure.py:50` 注释与 `schema.py:144` 错误文案里。**schema 只查非空不查枚举**（`:143-144`） | 两个后果叠加：①写 `promotion`/`transition`/`Card`/typo 一律通过 schema，而任何非 `card` 值都让重画不再维护这张卡的 regime；②「所有权判别式」这句承诺**在校验层没有强制点，可以被一个 typo 悄悄绕过**|

### 3.5 ✅ CONFIRMED_LIVE 但带已知债务（7 条，不计入 24）

| 项 | 债务 |
|---|---|
| `G01_GAINS` 双真源 | `price_reminder_sync.py:91 (1.0,2.0,3.0)` vs `exit_guard.py:70 g01_levels`。今天一致，**改档位必须两处同改**，没有任何测试或 import 把它们绑住（文档自陈的有意重复） |
| breakout-stale「2 日」双份手搓 | `entry_guard.py:307-310` 与 `portfolio_brief.py:1123-1128` 各写一遍，**无共享函数、无具名常量**（是 `closes[-2:]` 切片）。下次真去改重画触发线（复测条件：+12 月或 ≥20 个真实 episode），改一处漏一处 → 入场侧与简报侧对同一张卡给两个时效结论。**这正是 `CAP_FRACS` 在  合并前的形状**|
| `ladder_weights` 的「接线缺口」是**误报**| 权重已接：`entry_guard.py:93` + 三个调用点 `:714/:770-775/:837`（:838 为写账笔误，复核），`sizing.py:507-565 share_ladder` 按权重做整股最大余数分配，repair_reclaim 与 parabolic 共用同一段渲染。**核对结论应改为「已接线」**—— 这条误报的风险是引导下一个人去补一份第二真源 |
| `earnings` stage 模式集不对称 | `pipeline.py:139` 只在 BRIEFING 重建日历，但消费者跑在 BOTH / (HEARTBEAT,BRIEFING,POSTCLOSE)。有 7 天兜底（`CALENDAR_STALE_DAYS`）。**briefing 连续失败 ≤7 天时，heartbeat/postclose 会继续静默缩仓或静默不缩**—— 而 `earnings.py` docstring 自己写着「静默不缩仓是这条规则最可能的失效形态」。这个 7 天窗口是那条失效形态的唯一入口，文档没记 |
| ~~`earnings` 第四个消费者未声明~~ ❌ **复核判误报 **（对抗复核维持）：moomoo z1 备注**就是**design 实现表点名的三消费者之一（`2a407f6:251`，还是其 live 样本的展示主角）——审计者读到了点名 z1 备注的原文却没把它映射到 price_reminder。**映射失误**的机理样本 | **真正的残余观察**：z1 备注路径横跨两个模块——`today_group_sync.grant_actual_cap`（**earnings.event_state 的实际调用宿主**，:136-138，另有 :246 自己的 load_calendar）计算、price_reminder 渲染——design 只写到「z1 备注」粒度没点名宿主。**改 earnings 接口最容易忘的是 today_group_sync，不是 price_reminder**。§1 行的四消费者清单是正确版本 |
| `gate_ladder_report` 是第五个未登记的 fastlane 触点 | `:58 FASTLANE_K = RT_PARAMS["fastlane_k"]`（读 regime_transition 不存第二份）、`:193/:210/:292/:324`。作用域正确（只对 `kind=="entry"` 标 ⚡，文案「当日转移在途」不是入场指令），命名坑已写在 `:51-57` 注释里。**但它每天 04:20 SGT 把 ⚡ 推进 Telegram —— 这是「快车道」唯一每日出现在 用户 眼前的地方**，一次文案松动（「转移在途」→「可入场」）就会把零证据路径变成日常提示 |
| `bfloor_buf = 0.5` 的家安在渲染层 | 这条止损语义约定只活在 `gate_ladder_report.py:60`（报表模块）与 `ladder_entry_backtest.py:54`，**core 层没有**。是下一次分叉的现成入口（配合 A3 一起读） |

---

## 4. 维护约定

1. **新回测裁决后必须在此加一行。**顺序：先在 §1 的对应分组加行（裁决 / 采纳带值 / 否掉的精确范围 / live 强制点 / 核实），再检查 §2 有没有新撞词，最后把非 CONFIRMED_LIVE 的落点加进 §3。**不加行的裁决等于没裁决**—— 这是本仓「手工步骤 + 无人看的警告 = 这一步不存在」的同一条教训。
2. **「live 强制点」一栏只写会改变行为的那一处。**advisory / 播报 / 显示兜底一律标 `(advisory)`。`bfloor_buf` 与 `structure_drift` 都是因为这个区分没写清楚而被误当成已生效。
3. **裁决落地后回写设计稿状态行。**现存 V8/V9 四处 stale 状态行与两个 §10f 就是没做这件事的存量。
4. **接线后当轮 commit。**§3.0 的 7 个未入 git 文件是这条约定第一次被违反的记录。
5. **新词进仓前先 grep 全仓。**若已被占用，要么换词，要么在两处都加一句「本词在 X 指 Y」的注释（`gate_ladder_report.py:51-57` 是正确示范）。`fastlane` 一词现在承载 1 采纳 + 2 否决，就是没做这一步的代价。
6. **引用一条裁决时的三问**：哪份文档？哪一层（授权 / sizing / 提醒 / 观测）？哪个自由度（时点 / 幅度 / 结构 / 尺）？——三个都答得出来才引用。答不出来去读 §2。

---

## 5.  cap 口径对齐（用户 会话裁决，非回测）

起因：用户「cap 在系统中的种类…我发现不太符合要求」。全仓盘点出 7 族 21 处 cap，
实测三处对同一张卡给三个数（AMZN：入场计划 $8,120 / 手机备注 $421 / 告警文案 40%）。

| # | 裁决 | 采纳值 | live 强制点 | 作废了什么 |
|---|---|---|---|---|
| **C1**| **档位由结构判别式定，probe 仅对修复阶段**—— 上升结构不等 gate 直接 R1 | — | `core/structure.entry_classification`（entry_guard / today_group / price_reminder / brief 四处共读） | 8/6「天花板型 → probe 起步」clamp（`_class_ceiling` 降级为播报措辞）；entry_guard 硬编码文案「z1 触碰 = probe 参与资格」 |
| **C2**| available cap 无论画线还是 entry guard 都以同一个数显示 | 六条臂 | `core/sizing.entry_cap` | `grant_actual_cap` 的半套实现（②只在有 lots 时叠、⑤完全没有） |
| **C3**| **轮预算与 at-risk 的分母 = 真 stop 距离，stop 多者取 max**| 2N 降为兜底 | `sizing.binding_stop` + `sizing.risk_notional_cap` | `round_risk_notional_cap` 的固定 `stop_n_mult=2.0` 分母；entry_guard 旧 `min(结构位, 2N)` 取更远口径 |
| **C4**| 分类 cap → **累计语义**（减当前敞口），每轮多大归轮预算 | `book.exposure_of` | `sizing.available_cap(exposure=)` | position-tiers §「每轮独立重置、不减已部署」 |
| **C5**| 接购买力为第⑥臂 | `book.power_room()` = power 快照 − 当日已成交 BUY | `sizing.available_cap(power_room=)` | — （`cash` 字段标为不可用：读出 0.0/负数） |
| **C6**| v3 §4②「confirmed 5→7→8% 里程碑」标 SUPERSEDED，机制改挂 `ROUND_RISK_FRACS` 的 T + `CAP_FRACS["probe"]` | 两个都挂 | 文档 (advisory) | 8/8 起 `CAP_FRACS["confirmed"]=NOTIONAL_CAP=0.40`，那三个数升的是不存在的东西 |
| **C7**| IV 60-80%「期权 size 减半」**从文案删除**| — | 无（删除即强制点） | 一条只活在 `iv_gate_line` 文本里、从未进任何 cap 的规则 |
| **C8**| `cap_multiple > 1.25×` 成为告警 | `CAP_MULTIPLE_ALERT` | `portfolio_brief` (alert) | 「算出来但没有任何通道」 |

**已知后果（用户 明确接受，不是遗漏）**
- C4×轮预算：低波动票 R1 就吃掉整个 40%，campaign 事实上只剩一轮 ——「低波动票就是一轮打满」。
- C3 附带：②⑤ 共用同一 stop ⇒ 比值恒为 `round_risk_frac/AT_RISK_CAP_FRAC` ⇒
  **⑤ 在入场侧可证明永不 binding**，「stop >4N 才轮到 ⑤」的交叉点随 2N 分母一起消失。
  ⑤ 的真咬点只剩持仓侧（`at_risk_breach_signal`，棘轮抬升会推高 stop）。
- C3 数值影响（AMZN 8/11）：卡 stop 246.5 距 9.2%，2N 距 6.5% → ② 由 $8,120 降到 **$5,712**（−30%）。

**未接线 / 待办**
- `binding_stop` 在 today_group 侧只喂卡片 stop；棘轮地板与 trail 住 `exit_guards.json` 未接
  （它们只会更高 = 风险距离更小 = cap 更大，所以少喂 = 保守，可安全等接线）。
- ~~C4 的扣减口径分叉（市值 vs 成本）~~ **已统一 （C9）**：全系统用**市值**。
  用户 裁决原话「cap 应该重算，我觉得这才是合理的，本身 cap 就是跟随 NAV」——
  `cap = 40% × NAV` 逐日 mark-to-market，减数冻在成本上就是同一个减法两边活在
  不同时间。`position_budget` 改为 `remaining = cap`（cap 已净掉敞口），不再
  二次减 `deployed`；`deployed`/`cap_multiple` 降为展示量，`cap_multiple` 分母
  换成**名义**`class_cap_usd`（净额常为 0，除法会消失，而超顶告警恰恰要在那时响）。
  敞口真源 = `portfolio_brief.exposure_map`（brief 自己的 rows，与播报同一份数）
  / `book.exposure_of`（独立调用）。
  ⚠️ 已知副作用：**亏损会腾出额度**（敞口缩水 → ① 放宽）。缓冲 = ② 轮预算
  （R2 1.5% / R3 1.0% / 之后 0）与 T=5% 顶格照旧压着，所以不会变成「越亏越能加」。
