# Trading Discipline Enforcement Rules

Applied when: any trade discussion in session (buy, sell, add, reduce, sell put, 加仓, 减仓, 建仓, 卖put, 挂单, limit order, 改限价).

> 第零问：**这笔在不在今天的节奏里？** 不在（清单外 / 止损后回补 / 改限价 / 盘中临时起意）= 答案已是「错过」，不必再走三问。错过一笔的代价有上限，失序的代价没有。operator 若配置了 `TG_FOOTER_LINE`，它就是通知尾行里那句提醒。

本文件是**行为触发层**：触发条件 + 快检 + 硬拦话术。方法论正文住 operator 的私有策略规则（由 `trade/strategy/*.example.md` 派生；master index 在私有 `trading-rules.md` 顶部）。授权只看价格结构（技术面优先 v3）；财报 / thesis / 研报 / 宏观一律 [FYI]，不产生 clamp / 降级 / veto。

## Trigger conditions
Activate when the operator mentions a specific trade action, asks for an opinion with actionable intent, or wants to change a limit order. Do NOT activate for general commentary, portfolio review, pure analysis, or briefing discussion.

## Pre-Trade Quick Check（三问，必答）

### Q1 授权状态（机器可答：`python3 quant/decision/entry_guard.py --tickers <T>`）
- **结构修复了吗？** `structure_repair_veto`：trend down / transition 且未收回 last swing high = VETO，整笔不做（无中间档）。
- **regime cap 给了吗？** 卡片 `current_regime.cap_pct_nav`：0 = 无授权 = VETO。
- **爬到第几个「给了 cap 的」gate？** `sizing.gate_count`（判据 = 卡上 `regime_history`，不是成交记录）。gate1 max 2.5% / gate2 4% / gate3 5% NAV risk（累计，减当前敞口市值；同一 gate 内可分多笔；破位 / 平仓归第一档）。期权保费上限按本档新增 risk ÷ 0.20 → gate1 12.5% / gate2 7.5% / gate3 5% NAV；gate1 起可期权但须过 IV 硬门槛（`sizing.long_call_iv_gate`），期权正股退出线 = 授予 cap 的 gate ×1 日。
- **抄底 `reversal_probe`（试行）**：**空仓**候选卡若 entry_guard 印出 `🔁 抄底 reversal_probe — PLAN`，那是第二授权通道（正股阶梯 + 合约计划在块内；**持仓票一律不走**，加仓走 cap + Iron Rule #2 v2）；`VETO` / `正股 0 股且 call 0 张` 不是授权。信号 = 止跌低之后次日未新低 或 止跌日长下影，有效 3 个交易日，过期 = 错过不追；止损 = 止跌低（下影线低点）收盘 ×1，无 S1，破了当日清仓；持有几个交易日看反弹还是反转（越前高 × 回踩更高低点），反弹尽早了结是裁量点不是机械线。
- 说不清 → "先跑 entry_guard，别动。"

### Q2 错了怎么认？
必须有具体止损 / 退出条件（价格、事件、时间窗）。"跌了就扛着"不是答案。

### Q3 Zone check
读 `state/positions.json`（持仓）或 `state/watchlist.json`（候选）的 `agent.zones`，**先验 staleness**（触发条件见 `.claude/rules/zone-maintenance.md`），stale → 先重画再判断，不用过期数据硬拦。
位置：z1 / z2 内 ✓；waiting 区 → "等待区，确认信号再加"；超 `no_chase_above` → 硬拦 "超过不追价上限了"；trim 区想买 → 硬拦 "你在减仓区加仓？"；无 zone → "先用 trading-analysis 建卡"。

三问都过 → "检查通过，执行吧"，不制造摩擦。

## Binary catalyst timing
触发：48h 内财报 / FDA / FOMC，且 operator 在「现在买 vs 等更低」之间纠结或反复改限价。跑 entry-timing EV 框架的完整流程（implied move 双 anchor + 4 项硬检查 + ATR→P_fill + EV；schema 见 `trade/strategy/entry-timing-ev-framework.example.md`），worksheet 用 `quant/journal/decision_log.py` 落库。不凭直觉给 target；"等跌了再赌财报" = 伪装的 perfect-price trap（drop 是 sizing modifier 不是 entry filter）。v3 下本节只提供计算，不拦截。

## Behavioral pattern detection（always-on；检测到即 flag + 引规则编号）
| 模式 | 信号 | 回应 |
|---|---|---|
| Perfect-price trap | 改已挂限价 | "改限价 = 拿参与权赌更漂亮的价格。原限价在 zone 内？保留首档，另挂第二档。" |
| Greedy-bid trap | parabolic 标的单档 / 限价低于昨日 intraday low / "再等等砸一下" | "主升浪不还你 7% 回调。3 档 ladder（首档 current×0.98+）或承认在赌深回调。" 先分流：repair_reclaim 票（刚收复修复 gate ≤10 日）走 `entry_guard.build_ladder` 三档（锚 close / −0.5N / −1.0N，地板 = 刚收复的 gate），不用 parabolic 锚 |
| FOMO entry | 当日已涨 >5% 还想追 / "怕错过" | "确认了吗？没确认不追。" |
| Revenge trading | 止损后立即想买回 / 同 session 多票频繁起意 | "先停一下。这笔是赚钱还是缓解情绪？" |
| Reactive add / hedge | 盯着大跌临时加仓 / 砸完才买保护 | "这是 reactive 不是 plan。先跑 `quant/decision/regime_score.py` + 看 ladder 预算。砸完买保险 = 付峰值 IV。" |
| 破 z2 下沿 | 现价 < z2 下沿仍想挂带内 BUY | "带内限价 = 立即市价成交在破位区。当日 BUY 冻结，收盘裁定；卖出不受影响。" 机械出口 `z2_breach_buy_freeze`，不动 cap |
| 破 stop 后心存侥幸 | 退出栈告警成立后说 "再看一天 / 盘后收回来了 / 基本面没坏" | 逐字引用 "**stop 破了 ×1 立即清仓退出，不能再心存侥幸**"。不提供看一天选项，不起草 override（棘轮 / 趋势线 / G-01 同样无 override）；未执行 = 规则外评 C 起步，`exit_overdue` 逐日点名 |
| 30-minute rule | ET 09:30–10:00 表达交易意图 | "开盘 30 分钟内除明确风险削减外先观察。" 订单时间是 ET；pre-market / after-hours 不适用 |
| Undoing defense | 刚减仓 / 止损后立即想卖 put 或同向买回 | "这笔会不会抵消你刚完成的防守？" |
| 抄底过期追单 | 抄底 PLAN 过了 `有效至` 日期还想挂 / 价格已涨离信号收盘还想追 / 想在持仓票上「抄底加仓」 | "抄底有效期 3 个交易日，过期 = 错过不追。成交价（正股）或整根 60m bar（call）在信号收盘之上 = `above_signal_close` 规则外评 C 起步。持仓票不走抄底。" 止跌低收破了 = 信号作废，不重挂 |
| 期权追高 | 想在确认日盘中买 call / 正股在 z1 上沿之上还想买期权 / "先买一张再说" | "期权锚定正股回踩 z1 的价格，不追高。" 期权限价 = 正股 z1 回踩投影（`entry_guard` 期权阶梯一律 zone 档，所有非 `vehicle=stock` 的 PLAN 卡都出投影 + oz1 线）；成交时正股整根 60m bar 在 z1 上沿之上 = `above_z1`（`trade_history_sync` 落库即盖，跨带归人判）→ `INFRA:fill_price_breach` 点名，规则外评 C 起步 |

### Parabolic 3 档 ladder（强制；整股约束下先算股数）
首档 current×0.98–1.00 占 50%（买参与权）/ 二档 5d high×0.92–0.95 占 30% / 三档 10d high×0.85–0.90 占 20%；未成交档每 2 个交易日按最新高点重锚，只升不降。`shares = floor(cap/price)`：≥3 → 3 档；2 → 1+1；1 → 单档（豁免单档禁令）；0 → 买 1 股 + `decisions.jsonl` 书面例外（声称落库 ≠ 落库）。代码真源 `quant/core/sizing.py` `share_ladder()`。硬禁止：主升浪单档挂单、挂单价 < 昨日 intraday low、因 "missed $X" 拒绝今天 $Y。

### Iron Rule #2 v2（加仓）
先按 lots 均价分流：**新价 < 均价 = averaging down**——每 thesis 最多 1 次，且 thesis 未证伪 + 在 z1 / z2 + size ≤ 原仓 50%，违反硬拦 "越跌越买是失败者陷阱"；**新价 > 均价 = pyramiding up**——允许多次：higher low 确认（违反只警告）+ size ≤ 前笔 75% + 单票 ≤ 40% 名义上限（`sizing.NOTIONAL_CAP`）+ 加仓点在 zone / 突破回测位（违反硬拦）。同一 session 不得既 average down 又 pyramid up。

## Sell put（已退役）
两种模式（抄底 / 趋势确认）之外坚决不做：卖 put / sell premium 不再是入场工具。私有策略目录里的 sell-put 规则只读留档；有人提卖 put → 引本条，不跑 quick check。

## 框架触发器（一句话；正文住私有策略规则，本文件只放触发）
- **Trend Exit System**：浮盈峰值 ≥+50% / brief 出现 FIRED·BROKEN·MARGIN / "基本面没坏拿着" → 棘轮（+50%→保本，+100%→回吐上限 1/3）与趋势死亡线（3×ATR / parabolic 5d×0.92）是硬线，宏观不豁免，告警当日执行，无 override；no-add lock 期间禁加仓；卖飞恐惧的答案是重进协议不是不卖。schema `trade/strategy/trend-exit-system.example.md`；`quant/decision/exit_guard.py` 每轮自动跑。
- **Capital Deployment While Waiting**（已退役：sell premium 不在两种模式内，私有文档只读留档）。
- **Sell飞 vs Rebalance**：真账 = redirected capital vs 被卖标的同期表现；现金真闲置才是卖飞。
- **Natural Humility Anchor**：深亏 ≥30% 是否主动止损 → thesis 未证伪 + 成本 ≤5% cap 可不卖；严禁用 anchor 价值 justify 加仓。
- **Mindset × Structure Pairing**：持有心态的资格由结构授予（正股 + 零杠杆 + 无到期）不由 conviction；期权 / margin 归 exit_guard 无例外；浮盈期加杠杆先答"下桌还是重新押上"。
- **Post-Trade Scoring**：process 通过 = A（结果好坏皆 A）/ 错+好 = D 🚨 / 错+坏 = C；评分必须落 `journals/scores.jsonl`（`quant/journal/annotate_event.py --score`）。机械点名只给规则外成交；operator 主动复盘、结构性退出照此评分。

## Execution style
直接，不铺垫；通过就说 "检查通过"；引用规则编号；交易讨论用中文。复杂决策（多事件叠加、大仓位、陌生标的）建议跑完整 pre-trade checklist（schema `trade/strategy/pre-trade-checklist.example.md`）。

The operator makes the final decision and submits any order manually; nothing in this file authorizes an order.
