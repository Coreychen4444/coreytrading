# Repaint Trigger Backtest — 预注册设计（运行前锁定）

**日期**: （用户 批 "可以验证A C足矣"）
**问题**: watchlist 入场卡的重画触发线——A（现行：收盘 > trim 上沿 ×2 连续）vs
C（提案：收盘 ≥ trim 下沿 ×2 连续，即"两连收在 trim 区间内或上方"）。
B（区间内 ×1）已在讨论中否决（trim 锚在供给簇上，首触 = 拒绝概率峰值；escape_velocity 裁决 C 同族），不测。

**范围**: 仅 watchlist 入场卡语义。positions 卡明确排除（trim 止盈纪律红线：进 trim 即重画会让减仓区永久上逃）。

## 代理与采样（无未来函数）

- 池: structure_downgrade_backtest 的 UNIVERSE 23 票，本地 kline 缓存 ≥200 bars 全入。
- trim 代理带 = [0.97, 1.03] × **冻结锚 M**（episode 内静态——真实卡在重画之间是静态的）。
  M = ARMED 时刻的 trailing-20d high。
- 状态机: 收盘 < 0.97M 时 ARM（冻结 M）；每 20 bars 无触发则**原地重锚**（模拟 valid_until
  节奏的周期性重画，不退出 episode）；触发解决 + 观测期结束后，下一 episode 在
  max(c,a)+20 之后的首个带下收盘重新 ARM（不重叠）。
- 触发: C 在日 c = 连续两收 ≥ 0.97M；A 在日 a = 连续两收 > 1.03M（C 恒 ≤ a）。
  A 等待窗 = c 后 15 bars，超窗 = A 未触发（参与缺口）。
- 尾部不足 c+20 bars 的 episode 丢弃。

## 政策模拟（每 episode 一对）

- C-policy: 于 close[c] 进；stop = 0.97M − 0.5×ATR20[c]（收盘 ×1 确认，bfloor_buf 家规语义）；
  否则 close[c+20] 出。
- A-policy: 若 a 存在，于 close[a] 进，stop = 1.03M − 0.5×ATR20[a]，close[a+20] 出；
  A 未触发 → 回报 0（"晚重画 = 没参与"就是政策差异本体，计入不剔除）。

## 指标（锁定，gate round-2 三件套 + 两个描述量）

1. **paired expectancy diff**（C − A，逐 episode 配对），ticker-cluster bootstrap 2000 抽 CI90
2. **止损率**C vs A（点估之差）
3. **p10**C vs A
4. 时效：median(a − c)；A 未触发占比（描述）
5. 白画率：C 触发后 5 bars 内收盘跌回 0.97M 下（描述）

## 判据（锁定，运行前）

**换 C 需三条全部成立**：
(i) expectancy diff CI90 下界 ≥ 0；(ii) 止损率增幅 ≤ +5pp；(iii) p10 劣化 ≤ 1pp。
任一不成立 → 维持 A。样本 <60 episodes 或单票主导（最大票占比 >30%）→ 标注不裁决。

先验（记档防事后合理化）：C 在时效上必赢（构造上 c ≤ a），代价在 (ii)(iii)——
本质是"早 N 天参与"值不值 whipsaw 税，与 gate round-2 保险定价同构。
