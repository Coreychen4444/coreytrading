# 未做项的影响范围 —— 先确定爆炸半径，不动手

> **✅  当日进度**（用户「按顺序做吧」）：§13 顺序的 **1–4 步全部完成**。
> T0 五件 + B0-1 词表附录 · B5 量族体检（**结论：整族关掉**）· B3a 宽度观察层 · B0-4 价值区。
> 剩下的是 **5. DoF 预算（要你拍）**与 **6. B2 事件层（期望产出是「不裁决」）**。
> 逐条去向见文末 §14。

**建于 **（用户：「没做的那些 请逐一写出如果做了 会对哪些东西造成影响
先确定影响范围 不着急动手」）。**非裁决稿**，不产生 ledger 行。

**每条都做了消费链核实**（grep live 树，不是从文档推的）。核实不到的写「未核」。

---

## 0. 一页版：按爆炸半径分层

| 层 | 含义 | 项 | 需要什么才能动 |
|---|---|---|---|
| **T0**| **零行为改变**（注释 / 文档 / 无消费者的新字段） | B0-2 · B0-3 · B0-5 · B0-6注释版 · chandelier pin | 什么都不需要，随时做 |
| **T1**| **只改判读层输出**（不进任何授权路径） | B0-4 价值区 · B3a Context 观测 | 一句「做」即可；跑 8 周再谈阈值 |
| **T2**| **只产出数字，不接线**| B5 量族 lift 体检 | 一句「做」即可（同 `indicator_redundancy` 模式，只算相关不算收益） |
| **T3**| **会改变交易行为**| B2 事件层接线 · B4 横截面进授权 · **swing_points 换实现**| 预注册回测 + 判据先于数字 commit + ledger 行 |
| **T4**| **治理约定，不由数据裁**| DoF 预算 | 用户 拍板 |

🚨 **清单上有一条被写成了一件事，其实是两件**：
**B0-6「`swing_points` 的 Lo 方法论债」——加注释是 T0，改实现是 T3 里最大的一项**（见 §6）。

---

## 1. B0-2 · 三个无出处的 K 线检测器加证据等级注 —— **T0**

**改什么**：`core/technical.py` 的 `detect_hammer` / `detect_engulfing_bullish` /
`detect_morning_star` 三个 docstring，加证据等级 + 出处（Marshall-Young-Rose 2006 DJIA /
Marshall-Young-Cahan 2008 东证 / Horton 2009 负面；Caginalp-Laurent 1998 正面）。

**消费链（已核实）**：
```
detect_hammer/engulfing/morning_star  (technical.py:671/673/675, 唯一调用点)
   → detect_bottom_signals().patterns
   → regime_score.q_c_exhaustion (regime_score.py:234)
   → regime_score.synthesize (:339)
```

**🔎 核实出的关键事实（改变优先级）**：`regime_score` **不在 `pipeline.py` 里，
也不被任何定时任务调用**—— 它只在 `.claude/skills/trading-analysis/SKILL.md` 与
`trading-discipline.md`「加仓/异动日先跑 Q-A→Q-D」的**人工路径**上。

⇒ 这三个无出处判别式的实际影响面 = **一个手工调用的建议工具**，
不是 heartbeat / briefing 的自动输出。此前我在 landing plan §5 里写「它们在影响 Q-C
加仓节奏」是对的，但**没说清那条路径是人工触发的**—— 危害比我描述的更低。

**影响**：加 docstring **零行为改变**（测试不会红，输出一个字节不变）。
**风险**：无。**可逆**：`git revert`。
**不做的代价**：下一个读 `detect_bottom_signals` 的人（包括我自己）会以为那三票有出处。

---

## 2. B0-3 · `pull_breadth_history` 命名消歧 —— **T0**

**改什么**：该脚本 docstring 加一行「本文件的 breadth = **票池宽度**，不是市场宽度
（A/D line / % above MA）」。

**消费链**：`quant/research/pull_breadth_history.py` 是**独立入口脚本**，
写 `market/klines_deep/`，**无人 import**。

**影响**：零。**风险**：无。
**不做的代价**：做 B3a（真市场宽度）时，两个「breadth」会在同一个 research 目录里并存
—— 这正是 `RULINGS_LEDGER` §2 那张命名冲突表在管的事（「快车道」一词承载 1 采纳 + 3 否决）。

---

## 3. B0-5 · setup 六要素表格化 —— **T0**

**改什么**：把现有唯一 setup 家族（gate 转移 + zone 回踩）的六要素
（Context / Preconditions / Setup / Trigger / Confirmation / Invalidation）
连同**各自的强制点**写成一张表，放进 landing plan 附录。

**影响**：纯文档，零代码。
**价值**：它是**将来新增 setup 的举证模板**—— 任何新 setup 提案先填满六格，
填不满的不进讨论。这是把「Setup ≠ Trigger」从原则变成一张要填的表。
**不做的代价**：低。这条随时可做，也随时可以不做。

---

## 4. B0-4 · 价值区 VAH / VAL 进 `volume_profile` —— **T1**

**改什么**：`core/technical.volume_profile` 增加输出（或加一个 `value_area()` 纯函数），
给出 ≈70% 成交所在区间的上下沿。**70% 是拍卖理论的定义常数，不是我们调的参数 ⇒ DoF = 0。**

**消费链（已核实）**：
```
technical.volume_profile
   → level_map.make_profile (别名, :58) → top_hvn / find_lvn / build_map / OI 墙
   → chart_read.walls_block (:277)
   → research: interaction_census / poc_* / hvn_gate
```

**影响**：
- **加字段是加法**：现有消费者读 `lo` / `hi` / `turnover` / `share_pct`，新增键不影响它们；
- **但如果改成返回结构不同的对象，会同时打到 level_map（gate 画线）与两个回测**
  —— 所以必须是**新增字段或新增函数**，不许改现有返回形状；
- 首个消费者只有 `chart_read`（判读层）：每条锚标「在价值区内 / 外」，
  给「区内 = 平衡（均值回复语义）/ 区外 = 失衡（趋势语义）」这个坐标系。

**风险**：低，但**有一处要小心**—— 若将来有人拿 VAH/VAL 当 gate 锚，那是 T3
（`hvn_gate`  已裁：gate 锚从 pivot 簇换成 HVN 上沿 = **不采纳，平手保留 incumbent**；
换个名字再来一次是同一件事）。
**可逆**：是。

---

## 5. B5 · 量族 lift 体检（OBV / A-D / CMF / MFI vs RelVR）—— **T2**

**改什么**：新增 `quant/research/volume_family_redundancy.py`，
**只算相关与提升度，不算收益**（同 `indicator_redundancy.py` 的模式与纪律）。

**消费链**：无。research 层旁路，谁也不依赖它。

**影响**：
- **零 live 影响**；产出是一个数字：量族累积指标相对 RelVR 的**有效维数与 lift**；
- 它是 **landing plan 阶段 7D 的唯一前置**—— 决定「量族要不要进第五轴/轴 B」
  这场讨论要不要开。测出高相关 ⇒ **一整族的讨论当场关掉**（这是最省事的结果）；
- ⚠️ 它**不能**回答「有没有增量预测价值」，只能回答「是不是同一个读数」。
  要回答前者必须做 T3 的预注册回测。**别把体检结果当成 alpha 证据**
  —— landing plan §13 的诚实边界已经写了这条。

**风险**：唯一的风险是**看到结果后忍不住调阈值**（§4.5 数据窥探）。
所以它必须像 `indicator_redundancy` 一样，脚本里**没有收益计算**。

---

## 6. 🚨 B0-6 · `swing_points` 的 Lo 方法论债 —— **注释版 T0 / 改实现 T3（全清单最大）**

**背景**：Lo, Mamaysky & Wang (2000) **明确反对**在原始价上直接取极值
（「识别出太多极值，且产出的形态与技术分析师认为可信的不一致」），
他们的工序是先核回归平滑、在平滑序列上找一阶导变号。
本仓 `technical.swing_points(wing=2)` 是 5-bar 分形 = **正是他反对的那种做法**。

### 6a. 注释版（T0）
只在 docstring 登记这条债 + 指向 `core/patterns.py`（判读层已经自己走了核回归）。
**零行为改变，零风险。**

### 6b. 改实现（T3）—— 消费链已核实，这是爆炸半径最大的一项

```
technical.swing_points(wing=2)
 ├→ technical.swing_structure (:411)  → trend ∈ {up,down,transition}
 │    ├→ entry_guard.py:571          ← **结构降级判别式（改档位 = 改授权）**
 │    ├→ core/structure.py:120       ← entry_classification（probe/confirmed 判别式）
 │    ├→ exit_guard.py:617           ← 退出栈的结构标签
 │    ├→ gate_ladder_report.py:345   ← TG 报表
 │    ├→ gate_audit.py:61            ← gate 合规审计
 │    └→ chart_read.py:217/219       ← 判读层（含周线）
 ├→ level_map.py:355  cluster_pivots 的输入 ← **pivot 簇 = gate 画线的主锚**
 ├→ regime_transition.py:169         ← observation regime 的 z1/z2/stop **自动生成**
 └→ research/gate_backtest.py:37     ← 回测（WING=2 常量）
```

**改它会同时移动**：入场档位（probe/confirmed）· 退出栈结构标签 · **gate 画在哪条线上**·
破位后自动生成的 zone 与 stop · 以及**所有以 gate/pivot 为锚的历史回测的可比性**。

**要动必须满足**（缺一不可）：
1. 重跑 `structure_downgrade_backtest`（判据 = 目标桶「对 up 干净可区分」**且**
   「对 down 不可区分」）——landing plan 已写明「改这个判别式前必跑」；
2. 重跑 `gate_backtest2` 家族（pivot 簇是 cluster gate 的定义，
   round-2 给过 cluster「被拒 episode 差 −9~−14pp 显著」的拒绝权结论，换检测器 = 换那个 gate）；
3. `WING=2` 与新参数（带宽）双份 pin 测试；
4. 一行 ledger。

**我的建议**：**只做 6a，不做 6b。**理由三条：
- 判读层已经拿到了核回归的好处（`patterns.py` 自己走这条路），**收益已经落袋**；
- 6b 的收益是「trend 标签更贴 Lo 的定义」，而 `trend` 的现行判别式**是被自己的回测背书过的**
  （`close_below_last_swing_low` 那半正是被数据否掉我的提案后保留的）；
- 它一次动四个授权消费者，是典型的「为了更正确而重构」，
  而本仓已有的教训是这类改动的代价从来不在改动本身。

---

## 7. B3a · Context 观测层（宽度 / 板块）—— **T1（0 自由度起步）**

**改什么**：`chart_read` 的 ① Context 段增加**只播报不判定**的数值：
SMH 成分 % above 50DMA、这本书自己的 % above 50DMA。

**消费链**：只有 chart_read 的输出面。**不进 `q_a_regime`、不进 brief、不设阈值。**

**影响**：
- 起步版 **DoF = 0**（无阈值 = 无判定）；
- 需要一次板块成分拉取（OpenD `get_plate_stock.py`，**已装未用**），
  成分会变 ⇒ 必须落 `as_of` 并在陈旧时播报降级；
- ⚠️ **代理偏差必须写在输出里**：用 `klines_deep` 167 票算的「宽度」**不是指数成分**，
  是我们为去相关自己挑的池。不写口径 = `portfolio_brief` 固定 5% 阈值的同款错误。

**风险**：真正的风险在**第二步**——8 周后给它设阈值那一刻，它就变成 T3
（一条会说「今天大盘宽度坏了」的线，迟早有人拿它改仓位）。
**所以第一步就要说清：这条线永远不进授权，除非走预注册。**

---

## 8. B4 · 横截面 RS（轴 E）—— **T1 已在 / T3 才算数**

**现状（已核实）**：`technical.rs_series` **已实现**，消费者只有
`level_map.build_trend_obs`（[信号5] AVWAP+RS，**FYI-only**）。
它在 `trend_slots` §5 的 SSS 裁决里**被明确保留**（「Phase 1 观察层独立于本裁决照留」）。

**如果只是让 `chart_read` 也显示它**= **T1**，零风险（我现在已经在 ① 段用了
「20 日相对基准收益」，那是同一根轴的另一种读法）。

**如果要让它进授权**（例如「RS 落后 ⇒ 降档」）= **T3**，且要先答一个问题：
SSS 回测里**相对强度就是五槽之一**，那套评分 c1/c3 双败。
所以「RS 有增量」这个主张**已经有一次负面证据在案**——
重开的举证责任比第一次高，必须说清「这次和上次哪里不同」。

---

## 9. B2 · 交互事件层接线 —— **T3（清单上最贵的一项）**

**现状**：`interaction_census.py` 只数事件不算收益；参数表 §12.1 未锁；
FDR 多重比较校正**尚未实现**（landing plan §9 第 9 问）。

**做它的完整代价**：
1. 锁参数（我已给建议值，走 A：一次锁死只跑一组）→ commit；
2. **先实现 FDR / Romano-Wolf**（族大小 4 事件 × 2 side × 3 锚类 × 3 信息环境分层 ≤ 72 格
   —— 不校正的话 72 格里必然有几格 p<0.05，那是保证会出现的假发现）；
3. 判别式从 `research/` 搬进 `decision/`（`test_module_coverage` 里已写着这条要求）；
4. 回测 → 用户 裁决 → 若 support 才建 [FYI] 任务跑 ≥8 周。

**已知的坏消息（先说，别跑完才发现）**：**H1 大概率功效不足**。
Osler (2000) 给的效应量先验是 **+4.6pp**（公布 S/R 位 bounce 60.8% vs 随机位 56.2%），
按二项检验反推**每臂需 ≈1,800 事件**，而 H1 目标格全池只有 **2,445**个原始事件、
Kish 有效 N 更低。⇒ 最可能的结局是**「不裁决」**（`earnings_gate` 先例）。

**⇒ 这一项现在做的期望产出是「一个诚实的不裁决」，不是一个新能力。**
建议排在 B5 与 B3a 之后，或等 +12 个月样本。

---

## 10. DoF 预算 —— **T4（用户 拍，不是数据裁）**

**它不是代码，是一条约定**：每季度新进 live 的自由度 ≤3，超支须退休一个旧的。

**影响面**：所有后续预注册的**准入**，不改任何现有行为。
**诚实标注**：它**没有任何验证**，正当性只来自「多重比较是数学事实」。
按本仓标准属 `[约定]` 不属 `[实证]`。
**不做的代价**：syllabus 的 ~400 个概念没有总量闸门，会一条一条长进来
—— 而这正是 Bajgrowicz-Scaillet 那 7,846 条规则的形状。

---

## 11. ✅ 已核销：`level_map.chandelier` vs `exit_guard` 趋势死亡线

landing plan §13 记的「一处未核」，本轮核了：

| | `level_map.chandelier` | `exit_guard` |
|---|---|---|
| normal | `hwm_close − mult×n`，**mult 默认 3.0**| `highest_close − p["atr_multiple"]×atr`，**`atr_multiple = 3.0`**|
| parabolic | `max(last5 high) × 0.92` | `max(highs[-5:]) × p["parabolic_trail_pct"]`，**0.92**|

**结论：公式与两个常数完全一致，不是缺陷。**差异只在**谁提供 ATR 与 `since` 锚**
（level_map 由调用方传，exit_guard 用布防日与自己的 ATR）—— 这是有意的用途分工
（画线体检 vs live 强制）。

⚠️ **但两份常量没有 pin 测试**—— 与 `G01_GAINS ↔ g01_levels` 同型的已知债务
（那一对  已被 `tests/test_constant_pins.py` 钉住）。
**建议补一条 pin（T0，零行为改变）**：改吊灯倍数时两处必须同改，否则画线体检与 live 强制
会给出两条不同的死亡线 —— 而本仓已经因为「报了不 binding 那条线」出过事。

---

## 12. 不在清单上但要盯的：chart-read 首跑

**04:35 SGT 第一次真跑。**不是工作项，是观察项。

| 会失败的地方 | 预判 | 怎么确认 |
|---|---|---|
| 扫描表超长被 TG 拒收 | 不会（实测 1,466 字符 < 4096，且有测试钉） | TG 收到第一条 |
| **详情拆条推送**| **最可能出问题的一步**—— 「按 ticker 块 ≤2 只票拆条」是我写给未来自己的**指令**，从没真跑过 | 收到 ~6 条详情；只收到扫描表 = 这步没执行 |
| 定时任务权限 | 低风险（`permissionMode: bypassPermissions`，与另外五个任务同配置） | 注册表 `lastRunAt` |
| 冬令时 | **前必须改 `35 5`**| 已进 `tasks/README.md` 与 CLAUDE.md |

---

## 13. 建议的下一步顺序（如果要动）

```
1. T0 五件一起做 (B0-2/B0-3/B0-5/B0-6注释/chandelier pin)   零风险, 一轮做完
2. B5 量族体检 (T2)                                        可能当场关掉一整族讨论
3. B3a Context 观测 0 阈值版 (T1)                          补最大的真缺口, 跑 8 周
4. B0-4 价值区 (T1)                                        判读层坐标系补全
   ── 以上都不需要预注册 ──
5. DoF 预算 (T4)                                           用户 拍
6. B2 事件层 (T3)                                          期望产出是「不裁决」, 想清楚再开
   B0-6b / B4 进授权                                       建议不做 / 举证责任已抬高
```


---

## 14. 执行记录（当日，按 §13 顺序 1→4）

| 项 | 层 | 去向 | 结果 |
|---|---|---|---|
| B0-2 K 线检测器证据注 | T0 | `core/technical.py` 三个 `detect_*` + 上方区块注释 | ✅ 零行为改变；顺带把「消费链是**人工路径**」写进代码（`regime_score` 不在 pipeline） |
| B0-3 breadth 命名消歧 | T0 | `research/pull_breadth_history.py` docstring | ✅ |
| B0-5 setup 举证模板 | T0 | landing plan **附录 B**（现存 setup 六格 + 新提案空表 8 问） | ✅ |
| B0-6a swing_points 债登记 | T0 | `core/technical.swing_points` docstring（含完整消费链与「换实现要跑什么」） | ✅ **只做注释版，不改实现**（§6 的建议） |
| chandelier pin | T0 | `level_map.TRAIL_ATR_MULT/TRAIL_PARABOLIC_PCT` 提名为常量 + `test_constant_pins` | ✅ 变异测试确认会红（3.0→2.5） |
| B0-1 词表映射 | T0 | landing plan **附录 A**（syllabus ~40 个词 → 本仓对应物 / 为什么不新增） | ✅ 顺手做（防新词入侵） |
| **B5 量族体检**| T2 | `research/volume_family_redundancy.py` + landing plan §5.1 | ✅ **整族关掉**—— 它们是价格读数不是量读数（vs RelVR ρ≈0.02，vs 价格 ρ≈0.58–0.74） |
| B3a 宽度观察层 | T1 | `chart_read.market_breadth()` → ① Context 段 | ✅ **0 阈值**；深度池落后 18 天会喊 🚫 |
| B0-4 价值区 | T1 | `core/technical.value_area()` → ② Structure 段 + 附录 C | ✅ 加法不改 `volume_profile` 形状（测试钉） |

**顺带修的两处**（都不在原清单上）：
- `chart_read` 全书详情 **21.6k 字符 > TG 单条 4096**→ 加 `--digest` 扫描表（1.5k，测试钉住上限）；
- 形态状态词「未触发」不自明（用户 当场问）→ 四态换动作词 + **每条线标「距现价多远」**
  （`○双底 差3.1N` 与 `◐楔形 差0.6N` 是完全不同的两件事，旧标签把它们说成同一个词）。

**一个工具链的坑**（值得记）：变异测试后 `cp` 还原源码，**`__pycache__` 里的旧字节码
可能仍被加载**（同尺寸改动尤其容易），导致「文件是对的但测试仍红」。
清 `find quant -name __pycache__ -exec rm -rf {} +` 即可 —— 与 memory 里
「变异测试后禁用 git checkout 撤销」是同一类工具链陷阱。
