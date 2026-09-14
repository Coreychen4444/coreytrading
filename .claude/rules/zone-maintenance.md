# Zone Maintenance Rules

Applied when: 启动交易讨论、heartbeat、overnight briefing、任何涉及持仓 zone 的判断。

Zone 字段在 `state/positions.json` / `state/watchlist.json` 的 `tickers["<T>"].agent.zones`：`accumulation_zone_1/2` / `trim_zone`（`[low, high]`）/ `no_chase_above` / `stop_level`（+ `stop_level_days`；期权 `stop_level_basis`）/ `valid_until` / `timeframe` / `methodology` / `update_trigger` / `reasoning_summary` / `anchored_on` / `anchor_basis`。

## 核心原则
Zone 是市场结构的快照，必须动态维护。stale zone 两类错：假阳性硬拦（旧 trim 拦合理交易）与错过加仓（新 z1 没画）。**禁止用 stale zone 硬拦交易；禁止只凭 K 线不查宏观 context 就改 zone；禁止批量自动刷新所有 zone。**

## Staleness 触发（任一成立即 stale；代码 `core/structure`，entry_guard 与 portfolio_brief 孪生）
| 类 | 判据 |
|---|---|
| 时间 | `valid_until < 今天`；或 `last_updated` > 14 天 |
| Breakout | close > `trim_zone[1]` 连续 ≥2 交易日 |
| Breakdown | close < `accumulation_zone_2[0]` **当日**（破位收盘图即作废；盘中另有 `z2_breach_buy_freeze` 冻结当日 BUY） |
| Volatility | 单日 \|收收变动\| > 2×ATR14 且非宏观事件（`STALE_VOL_ATR_MULT`，per-ticker 尺） |
| 事件 | 持仓自家财报 / 重大公司事件 → **必须重画**；48h 内宏观 level 事件 → review。事件触发的是重画不是授权限制。财报重画 SLA = 反应日收盘后当日全重画（含 thesis），判据 `core.earnings.cards_stale_after_print`，出口 `INFRA:card_stale_earnings`（只报不拦，绝不写 `pending_repaint`） |

## 更新流程
1. 拉日线：`python3 .claude/skills/openapi/scripts/quote/get_kline.py <CODE> --ktype 1d --start ... --end ... --json`（近 1–3 月；moomoo 脚本用隔离环境的解释器）。
2. 价位图：`python3 quant/decision/level_map.py <T> --levels <候选...> [--oi] [--stops]` —— volume profile HVN 墙 + LVN 真空带 + pivot 簇 + 价位交互史 + 期权 OI 墙。zone 边界别画在 LVN 带内；**真空 ≠ 无阻力**（LVN 回答"有没有库存"，带内 ⛔ 拒绝墙回答"有没有卖方"，由近及远另出一行）。
3. 查宏观 context（`.claude/rules/macro-context-check.md`）：异动日先分个股 / 板块 / 宏观驱动。
4. Momentum diagnosis：Q1 6m ≥ +150% 或 YTD ≥ +200%？Q2 20 日内单日 ≥ 10%？Q3 距 ATH < 10%？全 YES → parabolic-mode；Q1+Q2 YES、Q3 NO → post-parabolic（normal 但 z1 上沿 +3%）；否则 normal。parabolic 标的额外看边际买家是否枯竭（trim / hedge timing 信号，非 zone 失效）。
5. 重算：**normal** — z1 = 近期 shallow dip 低点群，z2 = 上一档 consolidation / gap 下沿，trim = ATH × 1.05–1.07，no_chase = ATH × 0.95–0.97；**parabolic** — z1 = 5d high × 0.92–0.95，z2 = 10d high × 0.85–0.90，trim = 5d high × 1.08–1.15，no_chase = 5d high × 1.02，invalid_if = close < 20d high × 0.75。
6. 写回：
   ```
   python3 .claude/skills/trading-analysis/scripts/update_position_zones.py --ticker <T> --bucket {current|watchlist} \
     --zone1 lo hi --zone2 lo hi --trim lo hi --no-chase-above p --stop-level p --invalid-if "..." \
     --timeframe {swing_2w|swing_4w|event_window|post_earnings} --methodology {normal|parabolic_mode_spirit} \
     --valid-until YYYY-MM-DD --anchored-on YYYY-MM-DD --anchor-basis "簇/HVN/触碰证据" \
     --update-trigger "..." --reasoning-summary "..."
   ```
   只动 agent 块；禁止手编 positions.json。parabolic 必传 `--methodology parabolic_mode_spirit`。写本地活状态需要 operator 的明确授权；分析本身是只读的。
   **stop 永远显式传**：卡上有 stop 的重画不传 `--stop-level` = writer 拒绝写盘（「不传」从来不是「保持」，zones 整块替换）；保持 = 传原值 + 原 `--stop-level-days`（同值自动带过 days / 来历）；只传 `--stop-level` = 并入现有 zones 不重画（调 stop 的正确写法）；确要删传 `--clear-stop`（受控路径白名单外）。`agent_submit` 同判 `proposal would remove existing stop`（预览即拒）；brief `INFRA:card_missing_stop` 兜任何路径留下的无 stop 卡。

## Gate = 状态转移轴（每张卡都要有）
- `current_regime.cap_pct_nav` 答"现在能不能参与"；`next_regime.entry_gate` 答"下一个状态在哪"。`常规参与` 卡同样要给 next gate（否则促升后 cap 0 无路升回）。判据 `core/structure.has_next_gate`；`reclaim_gates`（本 regime 内早信号）与 `last_regime.repair_gate`（转移史）都不算转移轴。强制点：画卡 ⚠️ + brief `INFRA:card_missing_next_gate`，都不拦截。
- **gate 是被发现的，不是被指定的**：市场反复在那个价上做过决定的地方（pivot 簇 / binding 供给簇 / HVN-LVN 边界 / 旧 ATH / 破位起点）。它与 regime 标签、趋势方向无关；证据标准不得放宽（数字随结构可上可下，标准不动）。"这张卡没有 gate" = 这张图还没读完。
- **多维确认**：pivot 簇（`level_map` 信号 4）/ VP 节点边界（边缘不是中间）/ 拒绝计数 + 最近拒绝日期 / 道氏 lsh·lsl / MA50·MA200·AVWAP / 历史极值。收敛维度越多越硬；只有 lsh 一维不算。

### 画 gate 检查表
| # | 标准 |
|---|---|
| 1 | 锚 = tape 里的地标且多维收敛；禁整数关口唯一锚、固定偏移、单一维度 |
| 2 | 簇的**上沿** = gate（整条簇翻面）；下沿常是本 regime 的 `no_chase` |
| 3 | 确认口径：收盘 ×N（默认 2）+ **RelVR ≥1.1**（个股量比 / max(基准量比, 0.4)，半导体→SMH 其余→QQQ；真源 `regime_transition.RECLAIM_VOL_RATIO_MIN`，判据在**卡片字段** `volume_ratio_min`）；**量腿对齐日 = streak 内任一日**（连续站上 gate 的那几天里任一日过即算，跌回归零、day-1 的量作废；`regime_transition.volume_leg(streak=)`，转移机与 gate_ladder_report 同读） |
| 4 | 自洽：上行 gate ≥ `no_chase_above`（相等正常，低于 = 倒挂）；修复 gate ≥ stop。`core.structure.gate_coherence` |
| 5 | 单调：stop < z1_lo，z1_hi ≤ no_chase ≤ trim_lo |
| 6 | 下一档地板 = gate − 0.5×ATR20@转移日，且距 gate > 0.25×ATR20 |
| 7 | 站不住的带标 `provisional`（转移当日补画，否则 `pending_repaint` 锁授权） |
| 8 | `next_gate_preview` 必带 |
| 9 | `anchored_on.date` 与 zones 同日 + `anchor_basis` 写清证据 |
| 10 | 真空里不放追价上限：`no_chase` 压在第一堵 ⛔ 拒绝墙之下 |

trim 与 gate 之间没有序关系约束（它们不在同一时点生效）。档位错 ≠ 价位错：先问「这条线属于哪一档」。

## Gate 与 zone 同步（`core/structure.py`，entry_guard 判 STALE）
1. 同生死：zone stale ⇒ gate stale。
2. 同锚：`gate.anchored_on.date == zones.anchored_on.date`（容差 1 日）；不等 = 只重画了一半，出口 `INFRA:gate_anchor_desync`（两桶都扫）。
3. 自洽：上行 gate ≥ no_chase；repair gate ≥ stop。

重画规则：`last_regime.repair_gate` 复核后用 `--repair-gate-anchor-basis` 盖同日锚；`next_regime.entry_gate` 锚由构造给定（传了 `--next-regime` 才改写锚日）。**复核 ≠ 重锚**：只刷新收据（拒绝计数）不动日期。缺 `anchored_on` 是提示级不判 STALE。防 averaging down 的是 cap，从来不是 gate。重画换了 stop 数值时，转移机按旧 stop 预推的 `last_regime`（source=auto）作废，由 writer 清掉。

## 结构漂移（advisory only，不裁决）
`quant/decision/structure_drift.py` 四维：离锚 N / 区间外 % / gate 可达 N / 波动率×。只产出 brief 的「待重画」清单，阈值未校准，不参与交易判定。

## 通信
- 交易讨论：先说 "zone 已 stale（原因），先刷新再判断"，然后执行更新。
- 定时任务：输出 stale 警告 + 建议，不自动改有 zones 的卡（自动建卡只给无 zones 的 stub 卡，`card_autopaint` → 领域 Agent 控制器，跨运行器复核后才写入，只有 `applied` 回执算落卡）。
- 定期：briefing 每日检查 staleness；weekly review 强制 review 所有持仓 zone；heartbeat 单日 >2×ATR14 立即标 volatility stale。
