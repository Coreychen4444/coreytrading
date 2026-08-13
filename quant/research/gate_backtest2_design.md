# Gate 回测第二轮 — 预注册设计稿

**状态：~~草案，待 用户 裁决 D1–D3。批准并锁定 D1 之前不运行、不看任何结果数字。~~**
✅ **已裁决并跑完（当日）**：round-2 结果见下方 §Verdict（D1 分支 3 =
授权域收窄至修复路径，已进 live）；round-2b（bfloor_buf = gate−0.5N ×1 日，用户 批）
见 §Round-2b Verdict。~~原状态行~~划线保留不删（状态回写 ，ledger V8）。
（Bayesian 铁律 #5：pre-commit 在数据之前。看完结果再定标准 = 事后合理化。）

起草 。Round-1 = `gate_backtest.py` @ commit db043f7，
本轮针对其三个未解决 caveat：①Kish N≈40 不显著 ②未建模止损 ③只测深度破位。

---

## 假说（本轮要裁决什么）

- **H1（主）**：建模止损后，gate 入场相对"破位日直接买"，通过降低止损触发率 /
  whipsaw 成本改善 expectancy 与左尾 —— 即 **gate 的价值在风险侧，不在收益择时侧**。
  这是 round-1 没测、也是三态 regime 架构（gate→cap 升级）真正依赖的那半。
- **H2（维度判别力）**：画线维度中只有少数带"拒绝权"。判别力定义 =
  触发率显著 < 100%（有的 episode 永远不过 gate），且 not-fired 组的反事实
  前向收益显著更差（gate 拒掉的确实是差的）。
- **H0**：建模止损后 round-1 结论依旧 —— gate 是延迟器，cap 升级机制降级（见 D1 分支 2）。

## 两个世界（paired，同一组 episodes）

| | World A（基线） | World B（gate） |
|---|---|---|
| 入场 | 破位日收盘 | gate 确认日收盘（i→j 持现金） |
| 止损 | 同一规则（D2） | 同一规则（D2）+ B-floor 变体 |
| 终点 | T+60 / T+120 自破位日起 | 同左（苹果对苹果，含现金期与止损后现金期） |

**止损口径（D2，主 + 两变体； 用户 质询后定稿）**：
- **主（对称）**：入场价 − 2×ATR20@入场，收盘 ×1 日触发，次日收盘出场，滑点 0.2%。
  **血统**：= exit_rules_backtest L12/L50 的官方结构位代理（"合规结构 stop ≤ 2N，
  取最宽 = 触发最晚"），确认单日 = live 卡现行口径（用户 定）。
  **对称的理由 = 归因唯一性**：两世界同止损规则，差异 100% 归入场时机（gate 效应），
  不混入止损规则差异。真结构位是手画的、历史不可重建，机械重画 = 掺第二个未验证变量。
- **变体 B-floor（系统语义直译，全量必测）**：World B 止损 = gate level 本身
  （"上一档 gate = 下一档 regime 的地板"）。回答的是另一个问题：live 那套
  gate=地板语义整体表现如何。
- **变体 S-structure**：两世界止损 = episode 内最低收盘（结构位机械近似）。
- 敏感性：3×ATR20（趋势死亡线口径）。
- **预注册稳健性裁决**：D1 三分支以主口径读数为准；若 B-floor 变体与主口径
  方向矛盾（一胜一败）→ 自动落 D1 分支 3（混合）处理。禁止事后挑对结论有利的口径。

**重进口径（D3）**：
- **主：single-shot**—— 止损后持现金到终点。止损假说（触发率/whipsaw 成本/左尾）
  不需要重进建模就能直接读数，自由度最少。
- **次（预注册，仅 World B）：machine-replay**—— 止损后等下一次同 gate 确认再进，
  最多重进 2 次 = regime 状态机的真实行为。报告 campaign 级 expectancy。

## Episodes

- **主池（可比性锚）**：round-1 同口径 —— 60 日最低收盘 且 close < MA60，
  cooldown 120 交易日，观察窗 120 日，右侧截断丢弃。
- **副池（caveat ③，浅回调）**：close < MA20 且 close > MA60 且 MA60 20 日斜率 > 0，
  cooldown 40。gate = 收复 MA20 / 前 swing high。**只做方向性报告，不参与 D1 裁决**
  （预期与 entry_ladder 结论同向：趋势中不奖励等待；若反向则立项第三轮）。

## Gate 格（12 主格，全部预注册，禁止事后加格/选格）

kinds × days {1,2} × vol {off, on}：
- **swing**：道氏 swing high 收复（round-1 同款，technical.swing_structure 同口径，无前视）
- **ma60**：收盘站上 MA60（round-1 同款）
- **cluster（新）**：level_map 同式 pivot 聚类 —— 头顶最近 touches≥2 簇的上沿，
  tol 0.35×ATR14，只用 ≤j 的数据。**这格让本轮测的是真方法论（binding 供给簇），
  不再只是 swing/MA 代理。**
- vol on = 确认日量比 ≥1.2（20 日均量），即卡上现行的 volume_ratio_min。
- days=3 只做敏感性附表（round-1 显示 days 越多越差，不再占主格）。

round-1 六格是本设计的子集（swing/ma60 × days，vol off）→ **阶段 0 先在新数据上
无止损复刻 round-1**，分离"数据变了"与"规则变了"两个效应，然后才加止损（阶段 1）。

## 指标（每格 paired vs World A）

止损触发率 / expectancy（mean，止损实现后）/ 中位 / p10 / p90 / 半凯利 /
whipsaw 成本（止损亏损合计 per episode）/ trap rate / delay / lateness /
**not-fired 组反事实收益**（H2 判别力）。
显著性：episode 级配对差，**按月聚类 block bootstrap**（与 Kish 修正同源），
10k 次，90% CI。分 cohort 报告：1995-2004（dot-com 阴跌熊，仅 Yahoo 票）/
2005-2009（GFC）/ 2010-2015 / 2016-2019 / 2020-2026。
聚簇口径：主口径按月（保守）；月×行业细口径只作附注，不参与裁决（防自我美化）。

## 数据（用户 已批准扩容）

- **深化**：原 56 票 OpenD 重拉  → （同名票不耗新 quota），
  补 turnover 字段（cluster HVN 加权用）。`pull_gate_history.py` 已改。
- **广度**：+~105 票，11 个行业分层（金融/能源/医疗/工业/消费/软件互联网/材料/
  公用地产/旅游航空/中概/ETF），源 = **Yahoo v8 chart 公开端点，research-only 例外**，
  `pull_breadth_history.py`，来源记录 `klines_deep/_sources.json`。
  （首选 Stooq 已挂 JS 反爬墙——绕 bot 检测是红线，弃用。）
  数据纪律主条款不变：实时决策真源仍是 OpenD。
- **目标 Kish N ≥ 150**（round-1 ≈40 的 ~4×）。拉完后先重算 Kish N 再谈结论；
  若 < 100，先报数据不足。
- **Survivorship 偏差方向声明**：两源都只含存续票 → 退市灾难尾缺失 →
  **偏向低估 gate+止损的价值**。gate 若仍无优势 → 结论对"灾难保护"维度不适用；
  gate 若有优势 → 结论加强（顶着不利偏差赢的）。

## D1 预承诺菜单（运行前锁定；"最好格"只能是预注册格，禁止事后挑）

| 结果 | 动作 |
|---|---|
| **1. B 胜**：某预注册格 expectancy 胜出，或 expectancy 持平且（止损率显著更低 或 p10 显著更好），CI 排除 0 | gate→cap 升级机制**保留**；确认参数（days/vol/kind）校准到获胜格；输掉的确认口径从卡上退役 |
| **2. B 全败**：全部 12 格在收益侧与风险侧均无优势 | gate **卸下 size 授权职能**：保留状态机/stop 棘轮/播报职能，cap 档位改绑分类证据（classification），entry_gate 不再解锁 cap |
| **3. 混合**：风险侧改善但 expectancy 劣化，或格间互相矛盾 | gate 授权**只保留修复路径**（破位后重进场景），新开仓不等 gate；数据再增长后复测 |

## 不做什么

- 不在旧 40 事件池上调任何参数；不加第 4 个画线维度（每一维上卡前必须单独 ablation）；
- 不用盘中数据；不建模期权 vehicle；副池不参与裁决；
- 运行前不看任何中间数字（阶段 0 复刻结果也算结果，一并等 D1 锁定后跑）。

## 待 用户 裁决

- **D1**：上表三分支照单批准 / 修改（关键：分支 2 的降级去向你认不认）
- **D2**：主止损口径 = 对称 2N ×1 日收盘？（推荐）B-floor 与 S-structure 作变体
- **D3**：主口径 single-shot + 次口径 machine-replay（仅 B，≤2 次重进）？（推荐）

批准后：本文件内容进 `gate_backtest2.py` docstring 作为正式预注册，随实现一起 commit，然后运行。

---

## Verdict（运行后追记）

D1/D2/D3 于运行前锁定；对抗审查修正 8 条（见脚本 docstring"审查修正记录"，其中含
本文件两处运行前 pin：S-structure 改 swing-low 版〔原文对 World-A 前视退化〕、cluster
固定于破位日〔卡片语义〕、月×行业附注撤销）。真数据一跑；因 JSON 序列化崩溃重跑一次，
**仅修编码器零逻辑改动，两次控制台输出逐位 diff 一致**。

`d1_flags` 机械读数：全部 12 主格 branch1=✗（expectancy CI90 全负而非 flat）、
stop↓=True、p10↑=True、bfloor 矛盾=False → **预注册分支 3 自动触发**
（风险侧显著改善 + expectancy 显著劣化）。

**执行**：gate→cap 授权只保留修复路径，新开仓不等 gate（已写入
`.claude/rules/position-tiers.md` §Gate 授权域 + memory trading_lessons）。
复测条件：+12 个月数据或 live 修复路径样本 ≥20 笔。
全量数字与 JSON：`tmp/backtest/gate2_report.md`。

---

# Round-2b：修复路径止损语义扫描（立项，运行前锁定）

**问题**：分支 3 保留了修复路径的 gate 授权；round-2 顺带发现 live 止损语义
（stop = 刚收复的 gate，×1 日确认）expectancy 全场最差、×1 日格止损率反而升高。
修复路径既然保留，它的止损语义参数应该是什么？

**人群与设计**：round-2 同 episodes、同引擎；**只比 World-B 变体之间**（A-vs-B 已裁决，
不重开）。配对人群 = 该入场格 fired（窗口内触发）的 episodes——同一入场格内所有止损
变体共享同一入场日，纯配对。

**入场格（主，固定为 live 标准口径）**：kind ∈ {cluster, swing} × 2 日 + 量比 1.2。
ma60×2+vol 与 ×1 日格作附表不裁决。

**止损变体（每入场格 × 5，全部收盘口径、触发次日收盘出场、滑点 0.2%、终点悬置同 round-2）**：
1. `bfloor×1` —— incumbent = live 现行（stop = gate，跌破 1 收盘出场）
2. `bfloor×2` —— 跌回 gate 之下**连续 2 收盘**才出场
3. `bfloor−0.5N×1` —— stop = gate − 0.5×ATR20@入场（半个 N 的缓冲垫）
4. `2N×1` —— round-2 primary 对照（stop = 入场价 − 2×ATR20）
5. `structure×1` —— 入场价下方最近已确认 swing low（回退 2N）

每变体同时报 single-shot 与 machine-replay（≤2 重进 = 修复路径真实行为）。

**指标**：policy expectancy / 止损率 / whipsaw / p10 / p90；候选 vs incumbent 的配对差
按月聚类 bootstrap CI90（round-2 同机器同种子族）；p10 配对差同法（1,000 draws）。

**选择规则（锁定）**：替换 incumbent 当且仅当候选 vs `bfloor×1` 满足
**expectancy 配对差 CI90 下界 > 0**且 **p10 配对差 CI90 下界 > −1pp**（保险目的 =
左尾，不许为 expectancy 显著牺牲左尾）。多个合格 → expectancy 点估计最高者；
平手 → 语义更简单者。**无合格 → live 语义维持**，记录"最差 expectancy 变体但
无满足左尾约束的替代"的诚实结论。single-shot 与 replay 两口径矛盾 → 以 replay 为准
（修复路径真实行为），矛盾本身写进结论。

**exit_rules_backtest 适用性声明**：其回放人群（2026 实盘 142 事件）不含修复路径
regime-stop 再入场，本参数在该回测中不 binding——本轮以 gate_backtest2b 为裁决证据，
免跑理由在此登记（人群不适用，非跳过纪律）。

**live 落地边界**：若有胜出者，改动点 = repair/entry gate 的 stop 语义与确认天数
（regime_transition 检测 + 卡片字段）。**改 live 行为另行请 用户 批准**——本轮只产出
证据与建议，不动生产代码。

## Round-2b Verdict（运行后追记）

审查（单 lane）零 BLOCKER、3 MINOR 运行前修复。一跑出数（`tmp/backtest/gate2b_report.md`）。

按锁定规则的机械结果（replay_T60 主读数）：
- **cluster_x2_vol**：qualify = {bfloor_buf, two_n}，winner = two_n（点估计并列，
  简单序取 2N）；single 口径矛盾（two_n 的 CI 在 single 含 0）。
- **swing_x2_vol**：qualify = {bfloor_x2, bfloor_buf}，winner = bfloor_buf
  （two_n 在此格 p10 守卫失败 [-1.1,-0.6]）；single 口径矛盾。
- structure 变体全场被支配（expectancy 显著更差 + 止损率 53-88%），出局。
- 幅度诚实：改善 +0.1~+0.2pp/episode @T60、+0.3~+0.4pp @T120，显著但小——
  是把保费从 ≈1pp 削掉 10-20%，不是免费保险。

**跨格合成（⚠️ 此步未预注册，公开声明）**：两主格机械胜者不同（two_n / bfloor_buf）；
**唯一在两个主格同时合格的变体 = `bfloor_buf`（stop = gate − 0.5×ATR20，×1 日）**。
live 建议取 bfloor_buf：① 双格合格（two_n 在 swing 格牺牲左尾超守卫）；② 语义上保留
"stop = regime 地板锚在 gate"的架构（two_n 会把 stop 与 regime 结构解耦，同等测得收益下
概念改动更大）。buffer 大小 0.5N 是单点预注册值，不做网格细调（该效应量下调参 = 拟合噪音）。
