# 冲击预算框架接线 — 待办清单（记录 → **同日全部收口**）

框架已于  接进 live（commit `f951159`）：`CAP_FRACS["confirmed"]` 5% → 40%、
`size_caps` 四条取小、`_book_usage` 整轮共用、播报带组合占用行。

**第二轮（用户 三点补充）后，A–D 全部完成，E/F 是研究缺口不是接线缺口。**
用户的三点：①「probe/confirmed 的 2%/40% 是默认值，实际 cap 要靠 NAV 15% 回撤幅度
与 gross 110% 算出余额」②「实际 cap 要同步到 moomoo 画线的备注里」③「所有涉及到的
地方进行对齐」。

---

## ✅ 0. 单一真源：`sizing.available_cap()`（用户 点 ①③）

```
实际 cap = min(分类/regime cap × 财报缩仓,          ← CAP_FRACS × current_regime × T+0 系数
                剩余冲击预算 / (1.88 × ATR20/价),      ← SHOCK_BUDGET 15% NAV
                110% NAV − 已用 gross)               ← GROSS_CAP
入场侧再叠 N-unit（0.75%NAV / 2N × 首档价），持仓侧与备注层没有它。
```

**`CAP_FRACS` 从此是名义天花板，不是额度。**三个消费者读同一个函数：

| 消费者 | 位置 | 用途 |
|---|---|---|
| `entry_guard.size_caps` | 入场计划 | GO 的 size 上限 + ladder 股数 |
| `portfolio_brief.position_budget` | 持仓卡预算行 | 本轮还剩多少 / 还买得起几股 |
| `price_reminder_sync.z1_budgets` | **moomoo z1 备注**| 手机响铃看到的金额（点 ②） |

组合占用（已用预算 / 已用 gross）统一走 **`core/book.load_usage()`**；NAV 统一走
`core/book.latest_nav()`。此前 NAV / K 线 / 占用在这三个模块里各存一份实现。

`binding` ∈ {classification, shock_budget, gross} **必须播报**—— 一张写 40% 的卡
今天只给 34.9%，不写出来没人能解释。实测（NAV $21,237，gross 已用 75.1%）：
MSFT 名义 40% → 实际 34.9% = $7,418，备注 `z1:35%:7k`。

## ✅ A. 文档与代码对齐（原「优先级最高，语义漂移」）

| 位置 | 改成 |
|---|---|
| `.claude/rules/position-tiers.md` §整股约束 | probe 1% → **2%**（v3 早就改了，文档一直没跟） |
| 同上 §分类 cap 语义 | cap_confirmed 5% → **40%**；新增「⚠️ 只是名义天花板」整节 |
| 同上 §Concentration Override | 25% → **40%**；并写明 gross 110% 是硬顶**不走 override**|
| `.claude/rules/trading-discipline.md` §Iron Rule #2 B-3 | 25% → 40% + 组合层两条硬顶 |
| `CLAUDE.md` | `core/` 段补 `available_cap` / `book.py`；新增「冲击预算框架」条目 |

## ✅ B. 财报缩仓（`earnings_window` 路 A）已接线

**前置（前视日历） 查证解除**：OpenD 的
`get_financials_earnings_price_history` 返回的**最后一期就是未来那一期**
（`close_price` 为空 + `pub_trading_day_str` > today），还附
`predict_vola_ratio_newest` = 期权隐含波动幅度。原来以为只有历史存档，是没查。

- `quant/data/earnings_calendar.py`（新，pipeline briefing 步，排在 brief/entry 之前）
  → `market/earnings_calendar.json`：`next_reaction_day` + `x_earn`(C 收缩臂) +
  `implied_move_pct`。**反应日 ≠ 公布日**：AMC(pub_type=2) → 下一交易日。
- `quant/core/earnings.py`（新）：`event_state()` / `shrink_ratio()` / `event_line()`。
  T+0 单日生效，T+1 起恢复全额；日历缺失或过期 **必须播报**（静默不缩仓是这条规则
  最可能的失效形态，而 40% 名义仓在财报日是 13–21% NAV 一天）。
- 三个消费者同时生效（cap 只收紧永不放大）。首个 live 样本：LITE 反应日 ，
  X_earn 5.56N/19 事件 → ×0.24，备注从 `z1:2%:400` 变 `z1:<1%:100`，8/13 自动恢复。
- X_normal 取 **live 的 `SHOCK_X`(1.88)**而不是设计稿写的 1.94 —— 判据是「财报日
  占用不高于它自己的普通日占用」，普通日那一项用的就是 SHOCK_X。两处不同步会让
  缩仓比例悄悄偏松 3%。

## ✅ C. broker 缺的两个字段

- **delta → 落库**：`stock_fetch` 写 `broker.delta/gamma/theta/vega/iv_pct`
  （OpenD 快照的**真 greeks**，不是 BS 反解）。entry_guard 的 BS 反解已删 ——
  反解要 IV、要 T、要合约参数，每个 caller 实现一遍就是每个 caller 一个答案。
  实测 GOOGL 270115C390 delta 0.4055（与旧反解 0.4056 一致）。
  > ⚠️ 踩到一个坑：`safe_get(row, 'option_valid') is True` 对 numpy.bool_ 恒 False，
  > 整段 greeks 被静默跳过。真值判断不能用 `is True`。
- **atr20 → 不落库，改为单一实现**：`core/book.atr20_of()` 读 kline 缓存，
  positions 与 **watchlist**（broker=null，没有 broker block 可写）共用同一份。
  往 broker 里再抄一份 ATR 会制造第二个真源 —— 正是本清单在抱怨的那件事。

## ✅ D. `portfolio_brief` 已接组合占用

头部加 `🧮 组合占用` 行（与 entry_guard **同一段渲染**`sizing.usage_lines`）；
预算行的 cap 走 `available_cap`，组合层 binding 与财报缩仓都在卡上点名。

## ✅ D+. 顺手发现并修掉的单位错误（**不在原清单里**）

期权上「保费」和「标的名义」是两种钱，而 8/8 首轮接线把它们直接取小了：

- 分类 cap = 你**花出去**多少（保费）
- n_unit / 冲击预算 / gross = **标的 delta 名义**敞口多少

`option_plan.build()` 拿 `caps["final"]` 当保费预算花。实测 NVDA：名义上限 $7,400
被当保费花 → 1 张 $6,287 = 名义 $17,220 = **2.3× 上限**，方向是**放松约束**——
正是 `shock_budget_design` §1.2「名义 cap 在期权上完全失效」那个洞的镜像。

修法：`sizing.notional_to_premium_cap()` = `notional × premium /(delta × spot)`；
`size_caps` 输出 `deployment_cap` / `notional_cap` 两个数分开传；拿不到 delta 就
只报分类 cap 并写明「组合层未接」，**不给一个单位错的数**。

## ⏳ E. 两条仍未解决的回测缺口（`shock_budget_design.md` §15a）

**这两条是研究缺口，不是接线缺口**—— 接线不会让它们消失，所以留在这里：

- **H1 控制力不过**：打穿只有目标的 0.02–0.04×，因为校准假设满仓 5 票而实际入场稀疏。
  要修得先给「典型占用率」一个经验值，再回头定 `SHOCK_BUDGET`。
- **H2 选择性不过**：40% cap 一旦 binding，预算在单笔层面空转。
  这也是 per-ticker X 能删掉的原因，但意味着**这套东西不是精细的 per-position 风险模型**。

## ✅ F. 风险板块表（已结案 ：真聚类版也被否，分板块永久取消）

判据 4 用现成的 `SEMI_SMH`（量比基准表）当代理分组，sep = 0.066 < 0.15 → 分板块已取消。
**只证伪了「用现成行业标签分组」，没证伪「任何分组」。**真按相关性聚类那版见
`quant/research/risk_sector_design.md`（立项）。
→ **立项当日跑完，被否**：相关性真聚类与行业标签分组 ARI 0.15（聚出来的不是板块），
判据不过 → **分板块永久取消**，SHOCK_BUDGET 保持池级单常数。本节结案，
不再是待办（状态回写 ，BACKLOG §F stale 案）。

---

## 回归覆盖（本轮新增）

| 测试 | 钉住 |
|---|---|
| `tests/core/test_sizing.py` | `available_cap` 三条 binding 各自成立 + NAV 缺失 ≠ 无限制 + 缺 ATR 不吞掉 gross + `notional_to_premium_cap` 单位桥 + 两个数据缺口分开播报 |
| `tests/core/test_book.py` | ATR 暖机不够返回 None（不给凑合的数）+ 期权走 `broker.delta` + 落不到 delta 按 1.0 并报出来 |
| `tests/core/test_earnings.py` | 只缩 T+0 单日 + 只收紧 + **日历缺失必须说出口**+ 过期照用但报警 |
| `tests/data/test_earnings_calendar.py` | C 收缩臂 + 先验 <8 回退池级 + 平仓残卡/量能参考票不查 |
| `tests/sync/test_price_reminder_sync.py` | 备注 = 实际 cap（空书 40% vs gross 挤压后 35%）+ 降级先降精度再丢金额 + `<1%` 记号 |
| `tests/sync/test_today_group_sync.py` | 授权 label 带实际 cap + 余额吃光时「进组≠下得了单」 |
| `tests/report/test_portfolio_brief_cards.py` | 预算行被组合层挤压 + 期权不混保费与名义 |
| `tests/decision/test_entry_guard.py` | deployment/notional 分开 + 三条组合层数逐字来自 `available_cap` |
