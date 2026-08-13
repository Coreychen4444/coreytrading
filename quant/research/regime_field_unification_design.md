# current_regime 数据模型统一 — 立项设计

- **立项**: （today_group 白名单三轮语义迭代收敛后，用户 指令）
- **状态**: ✅ **已完成  当日**（设计锁定 → 7 工作项全落地 → 验收通过，
  记录见文末「验收记录」）
- **来源**: 判据一句话「就看当前所处的 regime 有没有给 cap」在数据层不字面成立——
  `current_regime` 只有转移机（regime_transition）写过的卡才有，一半卡的授权语义
  活在读侧推断里（`today_group_sync.regime_grant()` 的隐式分支）。本 repo 的惯性
  病灶正是「语义只活在 prose/推断 = 下一个 bug」（监控静默失效 6+3 形状）。

## 目标

每张**有 zones 的卡**（positions + watchlist）显式携带：

```json
"current_regime": {"name", "cap_pct_nav", "participation", "since", "reason"}
```

使「当前 regime 给没给 cap」在**所有消费者**处 = 一次字段读取。
读侧推断（today_group 隐式分支）删除；「有 zones 无 regime」成为 schema 违规。

## 现状盘点

| 卡片状态 | 现役例 | current_regime | 隐含语义（现由读侧推断） |
|---|---|---|---|
| 转移机写过 | LITE / MRVL / MSFT | ✅ cap 0.02/0.05 | 显式授权 |
| 常规参与候选 | GOOGL / AMZN | ❌ | probe 默认（CAP_FRACS；gate 授权域=修复路径 only，7/30 裁决） |
| 修复路径候选 | INTC / MU / NVDA / AAOI / SKHY | ❌（zones.reclaim_gates 非空） | cap 0，等 gate |
| 持仓无授权 | NBIS / SPCX | ❌ | 加仓 cap 0（NBIS 规则：只有转移落 cap 才授权） |
| stub / 无 zones | QQQ / SMH / SOXX / SPY | ❌ | 无 regime 可言 |

## 核心裁决点（立项当日已审计，非假设）

**Today 白名单侧：零冲突**（用户 点破）——cap 0 不进组，与现行 `regime_grant()`
隐式推断产出完全一致，backfill 对白名单是**纯无操作**。

**entry_guard 侧：副作用存在但收窄为一个渲染决定**（代码审计）：

- `entry_guard.py:495-500`：显式 `regime_cap_frac == 0` → **提前 VETO return**，
  位于 `reclaim_gate_lines`（:504）与 `next_regime_lines` 之前 → 显式 cap 0 卡
  的手册输出 = 一行 VETO（「观察层不授权入场; 解除 = 收复 next_regime 的 gate」），
  **修复阶梯地图不渲染**
- 隐式修复候选（无 current_regime，今日 INTC/MU/NVDA/AAOI 形态）→ 走完整路径：
  reclaim 阶梯行 + zone 判定 → WAIT
- **授权语义两者等价**（都是 0 = 不许买）；真实差异只有 verdict 标签（VETO vs WAIT）
  + 手册有无修复地图

**裁决：方案 a（用户 批）**——backfill 后统一 VETO，但把
reclaim/next_regime 阶梯行**提到 cap-0 return 之前**：VETO = 无授权，阶梯 =
修复地图，两者不矛盾，手册信息量不降级。
随之而来的**裁决内预期 diff**（验收时不算回归）：隐式修复候选
（INTC/MU/NVDA/AAOI）verdict WAIT→VETO、新增 ⛔ regime cap 0 行、阶梯行保留、
brief 排序后移；AMZN/GOOGL 新增 🎚 常规参与 regime 行；Today 白名单成员零变化
（仅标签措辞）。

## 语义规格（定稿 ，本节 = 设计锁定物）

canonical `current_regime`，按卡片状态：

| 状态 | name | cap_pct_nav | participation | since | source |
|---|---|---|---|---|---|
| 常规参与候选（有 zones 无修复梯） | `常规参与` | **CAP_FRACS["confirmed"] 快照**（动态跟 live 值：定稿时 0.05， 冲击预算定版后 0.40） | `zone_eligible` | `zones.anchored_on.date`（缺→`thesis.last_reviewed` 日期→迁移日+警告） | `card` |
| 修复路径候选（zones.reclaim_gates 非空） | `修复观察` | `0` | `gate_only` | 同上 | `card` |
| 转移机产物 | transition 命名 | transition 定 | transition 定 | 转移日 | ~~`transition`~~ **`auto`**（升档）/**`demoted`**（降档观察层）← 实现即如此，原文是幽灵取值（N3 案更正） |
| 促升持仓（watchlist 成交→positions） | `持仓·无加仓授权` | `0` | `gate_only` | 促升日 | ~~`promotion`~~ **`card`**← 促升实测走 derive（watchlist_migrate→structure.py），live 印证 NBIS source=card（N4 案更正） |
| 无 zones（stub / 纪念仓 / 空卡） | **禁止携带 current_regime**（zones↔regime 同生同死，对齐 structure.py gate↔zone 哲学） | | | | |

规格铁则：

1. **min() 语义下"不收紧"= 写分类天花板**：`effective_cap_frac = min(分类 cap,
   regime cap)`，常规参与若写 0.02 会把 confirmed 档新 clamp 到 2%（行为回归 +
   违反 7/30「常规参与不等 gate」）。写分类天花板（=CAP_FRACS["confirmed"]，
   定稿时 0.05、 起 0.40）→ min 后分类 cap 恒 binding，
   行为零变化。probe/confirmed 谁 binding 由分类判定，`reason` 字段写明。
2. **since = 结构锚定日，不是 backfill 执行日**：它是 today_group 的消费锚——
   写执行日会让锚定日与执行日之间的成交漏判"已行使"。
3. **source 字段 = 所有权判别式**：`card` 源 regime 每次建卡/重画由
   update_position_zones **重新推导覆盖**（结构变了 regime 跟着变）；
   `auto`/`demoted` 源只有转移机可写，重画不触碰（原文 `transition`/`promotion`
   是幽灵取值——live 从未写过，促升实测写 `card`；schema 枚举  起收死，N3/N4 案）。
4. **促升改写（NBIS 规则显式化）**：watchlist_migrate 促升时 current_regime
   改写为 `持仓·无加仓授权` cap 0（入场授权已行使），`next_regime` 保留
   （未来 gate 达标 → transition 落加仓 cap → 回 Today）。
5. **schema 强制**：有 zones 无 current_regime = error；有 current_regime 无
   zones = error（同生同死，双向）。backfill 与 schema 升级同一 session 落地，
   不留 warning 过渡窗。

## 工作项（顺序即依赖）

1. **行为审计（不改码）**：entry_guard 对上表五种状态的实际 verdict / caps /
   ladder / 渲染逐一快照（golden test 形式落 `tests/`）。cap-0 分支已当日审计
   （见上节，:495 提前 return），剩余状态照此补齐快照。
2. **语义规格定稿**→ **用户 批准 = 设计锁定**：每种状态的 canonical
   current_regime 值（含裁决点选型 + `since` 是否回填——它是 today_group
   消费锚，回填错日期 = 白名单误吊销）。
3. **写入方覆盖**：`update_position_zones.py`（建卡/重画强制写，缺省报错）、
   `watchlist_sync.make_stub`（stub 显式 `needs_card` 无 regime）、
   `regime_transition`（已有，唯一现役写者）、`watchlist_migrate`（促升保留字段）。
4. **schema 强制**：`core/schema.py` 校验 current_regime 形状；过渡期
   「有 zones 无 regime」= warning，backfill 完成后升 error（棘轮方向单向）。
5. **backfill 迁移**：一次性脚本，**首日即带 `--apply`（缺省 dry-run）**，
   跑完即删（上膛枪教训：无 dry-run 的迁移脚本曾回滚 scores.jsonl）。
6. **消费者收敛**：today_group `regime_grant()` 删隐式分支（纯字段读）；
   其余 current_regime 读者（entry_guard / portfolio_brief / regime_transition
   的 last/next 滚动）行为不变由工作项 1 的 golden test 钉住。
7. **验收**：backfill 前后全卡 entry_guard verdict/caps/ladder 逐字段 diff = 空
   （除工作项 2 裁决中明确要改的行为）；today_group 期望集不变；
   briefing 手册渲染 diff = 仅裁决内变化。

## 验收红线

- 迁移后「有 zones 无 current_regime」= schema error——授权语义不再允许活在推断里。
- entry_guard 行为变化**只能**来自工作项 2 的书面裁决，diff 里出现任何裁决外变化 = 回滚。

## 与现役系统的关系

设计锁定前，today_group 读侧推断继续服役（行为正确，测试钉着，
`2eebee0` 已收敛为单 resolver——本项目完成后删的就是 `regime_grant()` 里
`if bucket == ...` 之后的推断段，显式段保留）。

## 验收记录（当日完成）

- **落地物**：`core/structure.derive_card_regime()`（派生真源）+
  update_position_zones 建卡/重画自动派生（期权卡除外）+ watchlist_migrate 促升
  改写（机器源保留）+ `schema._validate_card_regime` 同生同死强制（active/watching
  正股卡）+ entry_guard 方案 a（阶梯行提前于 cap-0 return）+ today_group
  `regime_grant()` 纯字段读（推断分支删除）。backfill 一次性脚本于 scratchpad
  执行后即弃（未入 repo）。
- **backfill 结果**：watchlist 7 卡派生（AMZN/GOOGL→常规参与 0.05；
  AAOI/INTC/MU/NVDA/SKHY→修复观察 0）+ 3 卡机器源保留（LITE/MRVL/MSFT）；
  positions 1 卡（NBIS→持仓·无加仓授权 0）。since 全部取结构锚定日。
- **entry_guard 前后 diff**：全部落在裁决内 —— 修复候选 VETO 化且阶梯/gate 地图
  完整保留（方案 a）、AMZN/GOOGL 新增 🎚 行、NVDA WAIT→VETO 排序后移；
  无任何 cap/ladder 数值变化。顺带修正 INTC/MU 的隐性不一致：此前
  `action.intent` 判别含混致修复卡渲染「🎫 回踩资格」（8/2 INTC 违规买入当晚
  手册上正是这种含混地图），显式 cap 0 后消失。
- **测试**：529 全绿（+13 新增：derive 6 / schema 4 / entry_guard 方案 a 1 /
  promote 2；5 处夹具按新合同补 regime）。
- **已知边界**：①日粒度消费平局按保守判（AMZN 8/3 纠错买入 vs 同日锚定 →
  判已行使出组，注记显式；重画刷新 anchored_on 即重新武装）。②closed/归档卡
  与期权卡豁免同生同死（历史记录不强制迁移）。③cap-0 VETO 卡不渲染
  资格/结构评估段（无授权 = 无入场评估，地图保留）。
