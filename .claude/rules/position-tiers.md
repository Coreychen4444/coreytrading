# Position Tiers & Sizing Rules

Applied when: 讨论仓位、加仓、减仓、止损、止盈、portfolio review、heartbeat、overnight briefing。

## 两层
| 层 | 含义 | 管理 |
|---|---|---|
| `base` | 结构豁免层——**不是"长期看好"，是 exit_guard 不管它**。判据 `instrument.role ∈ UNMANAGED_ROLES`（`quant/core/holdings.py`，该字符串只允许出现在那一个文件） | 不设 zone / stop，不进棘轮 / 趋势线 / regime，不进任何 task 输出（唯一过滤点 = `portfolio_brief.load_rows_from_state`）。钱照算。不加仓。"我很看好"不是进入理由 |
| `position` | 所有受交易纪律管的仓位（由早期的 core / trading 两层合并而来，旧值只读留在 `tier_legacy`） | zone 信号 + exit_guard：z1 / z2 考虑加仓（受 entry_guard + cap）、trim 考虑减仓、破 stop 按 invalid_if、棘轮 / 趋势死亡线 / G-01 机械触发无例外。事件驱动短线仍记 position，lot 必填 catalyst / target_exit / stop_loss / time_stop |

只追踪 USD 绝对盈亏，每层独立：`tier_pnl = (price − tier_avg_cost) × qty`。输出格式 `TICKER position: Q股 @ $COST → PnL: ±$X`（空层不显示）。

## 整股约束
不做碎股，最小 1 股。`shares = floor(cap/price)`：≥3 → 3 档 (50/30/20)；2 → 1+1；1 → 单档（豁免 greedy-bid 单档禁令）；0 → 买 1 股 + **强制** `decisions.jsonl` 书面例外。真源 `quant/core/sizing.py` `share_ladder()`；持仓加仓阶梯 = `portfolio_brief.position_ladder`（价位同 `entry_guard.build_ladder`）。

## 授权与 sizing（真源 `quant/core/sizing.py`）
- **regime cap 二值 {0, NOTIONAL_CAP 40%}**（`current_regime.cap_pct_nav`）：0 = VETO（结构未修复 `structure_repair_veto` / 降级 / 持仓锁）；给了 = 满额参与资格。cap 是**名义天花板不是额度**。
- **实际 cap = `entry_cap()` 六臂取小**：
  ```
  min( regime cap − 当前敞口 (book.exposure_of),
       gate 预算 = GATE_RISK_CAPS[档] × NAV ÷ stop距离 − 当前敞口,   # 2.5% / 4.0% / 5.0% 累计
       at-risk  = AT_RISK_CAP_FRAC 5% × NAV ÷ stop距离,
       剩余冲击预算 ÷ (1.88 × ATR20/价),                            # SHOCK_BUDGET 15% NAV
       GROSS_CAP 110% NAV − 已用 gross,
       购买力余额 book.power_room() )                                # 不用 cash 字段
  ```
  stop 距离 = 真 stop（卡 stop / 棘轮地板 / 趋势死亡线取 max，`sizing.binding_stop`）；2N 只兜底不进 max。
- **档位 = `gate_count(card)`** = 卡上 `regime_history` 里**给了 cap 的** up 转移条数；down 转移 / 平仓归第一档（平仓日读 `exit_info.closed_at` 与 `readd_from_position.closed_at` 两处）。同一 gate 内可分多笔，余额 = 上限 − 敞口市值。「轮」/ r1 r2 r3 已退役。
- **期权**：保费上限按本档**新增** risk ÷ |S1| 0.20 → gate1 12.5% / gate2 7.5% / gate3 5% NAV（不跟累计，两侧口径不同是知情选择）；`OPTION_MIN_GATE = 1`，须过 `long_call_iv_gate`（percentile <40 / ATM IV <50% 兜底 / 无数据不放行）；正股退出线 = 授予 cap 的 gate ×1 日（`granting_gate_level`）；保费与名义是两种钱，换算 `notional_to_premium_cap`，禁降 delta 装 cap。NAV 不够一张 → 0 张 = 正股或 veto。
- **四个消费者读同一份**：`entry_guard.size_caps` / `today_group_sync.grant_actual_cap` / `price_reminder_sync` z1 备注金额 / `portfolio_brief.position_budget`。binding 必须播报。
- 组合占用 = `quant/core/book.py` `load_usage()`，期权按 `broker.delta` 名义。已知局限：这套东西的实质是「单票 40% + 组合波动率目标 + gross 110%」，不是精细 per-position 风险模型。
- 财报反应日 T+0：名义 cap × min(1, 1.88/X_earn) × 0.7（`quant/core/earnings.py`），T+1 恢复。
- 熔断：受管书单日损失 ≥ 22.5% NAV → 清仓，盘中立即执行。
- 估值层的公允价意见只是信息：不抬 cap、不造入场、不替代退出栈。

## 卡片字段
- `agent.action.constraints`：`touch_protocol`（A_take_first / B_passive_only / C_60m_first_bar）、`vehicle`（stock / leaps / spread / stock_first）、`reason` / `from` / `until`（过期明确报出）。只有结构化字段会驱动 entry_guard；写在 prose 里的约束代码读不到。写法 `update_position_zones.py --touch-protocol ... --vehicle ...`，不手编 JSON。
- `agent.current_regime`：`cap_pct_nav` / `participation`（zone_eligible / gate_only）/ `name` / `since` / `reason` / `source`（card = 建卡派生 `derive_card_regime`；auto / demoted = 转移机，重画不触碰）。每张有 zones 的正股活跃卡必有（schema 强制）。持仓票不走 entry_guard，状态卡的 ⛔ cap 0 行是该约束到手册的唯一通道。
- `agent.next_regime.entry_gate`：下一个状态在哪。每张卡都要有（`常规参与` 也不豁免）；缺 = 促升后 cap 0 无路升回。判据 `core/structure.has_next_gate`，出口 `INFRA:card_missing_next_gate`。

## 语义分层（词汇不互借）
仓位层：cap / z1 / z2 / trim / no_chase / stop。结构层（gate 行）：止跌线 / 收复线 / 修复线（修复路径）、突破线 / 箱体顶 / 新高线（非修复路径）——线名 = `core/episodes.gate_path` 四分类（判据 = 120 日窗内破位史，卡片名不是判据），**不携带授权含义**。⚡ 快车道只适用①修复域，非①一律 ×2。

## Gate 授权域
gate→cap 升级只在修复路径与促升持仓的加仓授权（cap 由 `next_regime` gate 达标转移授予，防 averaging down 的是 cap 不是 gate）；新开仓与常规 zone 加仓不引入"等 gate"（回测：gate 是保险不是 alpha，保费 ≈1pp）。转移后 regime 地板 = 刚收复的 gate − 0.5×ATR20@转移日（`bfloor_buf`，×1 日收盘）；地板贴 gate <0.25N = whipsaw 语义。改 gate 确认口径 / stop 语义前必跑 `quant/research/exit_rules_backtest.py` + `quant/research/gate_backtest2.py`；改降级判别式前必跑 `quant/research/structure_downgrade_backtest.py`（判据：目标桶对 up 干净可区分且对 down 不可区分）。判据在读结果之前预注册。

## 数据结构（`state/positions.json` v2）
`lots = {"base": [...], "position": [...]}`（空层 `[]`），`position_summary` 含 base / position / total 的 qty 与 cost。

## 卖出 / 买入落账（对账式，幂等，漏跑一轮下轮补上）
- 卖出：operator 下单 → `trade_history_sync` 落 SELL → `stock_fetch` 刷 qty → `close_reconcile` 全平收尾（清 lots、标 closed、落 `exit_info` 含 realized_pnl 与 `closed_lots`）。部分平仓归人工。券商合并均价不作 PnL 参考。
- 买入：`lot_reconcile` 自动落增量 BUY（tier 恒 position，stop / target 派生自卡片 zones，order_id 去重）；部分卖出 / 数量对不上 / 期权只报不写 → `INFRA:lot_drift`。
- lots 不是注解：规则内外都要与 broker 数量对齐。

## Concentration Override Protocol
单票 > 40% 名义上限且是 deliberate override 时，5 条必备：书面多信号 justification / 硬 trip-wires（价格·事件·time stop）/ time-bound revisit / no-add lock / journal 显式记录 "concentration override"。红线：单票 > 50% 无条件禁止；同时只允许 1 个 active override；LEVEL5 叠加须先 trim。组合 gross 110% 不走 override。
