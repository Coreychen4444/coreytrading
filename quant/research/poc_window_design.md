# POC 窗口与 ②维去留 — 预注册 round-2（起草，**运行前锁定**）

**状态**：✅ **已跑完并裁决→ 摘掉 ②POC 维**。
解耦标签后 poc60 的优势**完全消失**（0.812 → 0.552），证实 round-1 那个数是共用分布的产物；
两个窗口都输给时间基线**和**①price_drift。见 §10。
**前身**：`poc_calibration_design.md`（round-1， 同日跑完）。

## 0. round-1 留下的两件未了事

round-1 的裁决是「不采纳阈值，②POC 维保持 advisory-only」，但它的**否定结果里有信息**：

| 臂 | AUC | vs 时间基线 (0.627) | 判据 |
|---|---|---|---|
| **poc20（= live 现行）**| **0.544**| **输 0.083**（CI90 排除 0） | ①✗ ②✗ |
| poc60 | **0.812**| 赢 0.185 | ①✓②✓③✓ **④✗**（T=0.1N 掉出 [0.5,3.0]） |

于是剩下两个问题，本轮一次答完：

1. **窗口该不该从 20 改成 60？**—— live 参数，改之前要过闸门
2. **②POC 维该不该留？**—— round-1 §5 的否定分支白纸黑字写着：判据 2 不过 ⇒
   「时间基线已足够」⇒ 考虑把 ②维从 drift 装置里简化掉。**二选一，不能两条都挂着**

## 1. round-1 为什么不能直接拿来定案（本轮存在的理由）

poc60 的 AUC 0.812 **不可信地高**，原因写在 round-1 §9b：

> poc60 的 POC 是从**同一份 60 根 profile**里取的 argmax，而 label 也是从那份 profile 的
> HVN 带算出来的 —— **自变量与因变量共用一个分布**。极小的 POC 位移当然预示带子会动。

这既解释了 T=0.1N 为什么掉出区间，也意味着 **0.812 这个数不能拿来支持"改成 60"**。
本轮的核心改动就是**把标签换成一个不共用分布的东西**。

## 2. 新标签：用 pivot 锚的 zone，不用 volume 锚的带（**运行前锁定**）

`regime_transition.derive_observation_regime` 画 zone 的真实机制是 **pivot swing lows**，
不是 volume profile：

```python
below = sorted({round(v,2) for _,v in swing_lows if v < break_level}, reverse=True)
z1_lo = below[0] ...; z2_lo = below[1] ...; stop = below[2] ...
```

所以本轮：

- **自变量**：`poc_shift_n`，`POC_WINDOW ∈ {20, 60}`（同 round-1，沿用 `core/drift.py` 口径）
  —— **volume 来源**
- **因变量 label = 1 ⇔ pivot 锚的 zone 组在 D 日重画会变**：取 `(z1_lo, z2_lo, stop)`
  三个值（`swing_wing=2`，`pivot_lookback` 与 live 同），锚定日一版、D 日一版，
  **任一值移动 ≥ 1 个 bucket（或该档 pivot 消失/新生）→ label = 1**—— **price 来源**

两者**没有共用任何分布**：一个是成交额在价格上的分布，一个是价格的转折点。这才是
「供需重心搬家了，画线会不会跟着变」的干净问法。

## 3. 基线（两条，都要打赢）

| 基线 | 为什么 |
|---|---|
| **时间**：距锚定日的交易日数 | round-1 的那条；drift 装置的立论就是要打赢它 |
| **①价格离锚**`price_drift_n` | **新增**。②若只是①的影子，那它没有独立存在价值 —— 装置可以只留① |

## 4. 样本（与 round-1 一致，便于对照）

`klines_deep` 165 票；单元 `(ticker, anchor, k)`；每票每 **5**个交易日一个 anchor；
`k ∈ {3, 5, 10, 15, 20, 30}`；右截断 anchor+k ≤ 末尾 −5。

⚠️ **`POC_MIN_SESSIONS = 10` 的守卫照旧**：k=3/5 无 POC 读数（round-1 实测占 33%）。
本轮**明确把它当成结论的一部分**报出来，而不是让那两格静默空着——
**②维结构上答不了「锚定 3–5 天后要不要重画」，而那正是重画最常被问的窗口。**

## 5. 判据（**运行前锁定**）

### 5a. 窗口之争（20 vs 60）

采纳 `POC_WINDOW = 60`，当且仅当：

1. `AUC(poc60) − AUC(poc20) ≥ 0.05`，且按 ticker 整块 bootstrap **CI90 下界 > 0**
2. `AUC(poc60) ≥ 0.65`

两条都过 → **改**；否则**维持 20**（不动 live）。

### 5b. ②维去留（更重要）

保留 ②POC 维（用胜出的那个窗口），当且仅当**全部**成立：

1. **打赢时间基线**：`AUC(POC) − AUC(时间) ≥ 0.05`，CI90 下界 > 0
2. **打赢①价格离锚**：`AUC(POC) − AUC(price_drift) ≥ 0.03`，CI90 下界 > 0
   （门槛比时间那条低：①与②本就都在测结构变化，只要求它有**独立**贡献）
3. 存在阈值 T 使 **FPR ≤ 30%**且 **T ∈ [0.5N, 3.0N]**
4. 在该 T 上 **AUC ≥ 0.65**

任一不满足 → **把 ②POC 维从 drift 装置里摘掉**（round-1 §5 的否定分支），
并把 `drift.py` 的 `THRESHOLDS["poc_shift_n"]` 与相应播报一并移除。
**不允许"既不改也不摘"继续挂着**—— 那正是 round-1 明令禁止的状态。

T 的选法（同 round-1，锁定）：满足 FPR ≤30% 的候选里取 Youden's J 最大者，平手取更大的 T。

## 6. 已知混淆

| 混淆 | 处理 |
|---|---|
| round-1 的自变量/因变量共用分布 | **本轮的核心修正**：label 换成 pivot 来源 |
| 同票不同 anchor 自相关 | 每 5 交易日一个 anchor + bootstrap **按 ticker 整块重抽**|
| 新 label 可能天然更难预测（AUC 普跌） | 判据全部是**相对**的（vs 时间 / vs ①），绝对门槛只有 0.65 一条；且三条曲线同 label 同样本，可比 |
| pivot label 对 `swing_wing` 敏感 | 锁 `wing=2`（live 同值），**不扫参数**|
| 多重比较 | 2 个窗口 × 2 个基线，判据预注册，无事后分层 |
| 幸存者偏差 | 同 round-1；本问题（画线一致性）不受影响 |

## 7. 不做什么

不碰其余四维的阈值 / 不把 drift 接成 hard STALE（那要另一轮）/ 不扫 `swing_wing` 或
`pivot_lookback` / 不改 anchor 抽样密度 / 不看中间数字。

## 8. 产出

`quant/research/poc_window.py`（可复跑）+ 本文档追加「结果」章：
两个窗口 × 两条基线的 AUC 表、选定 T 与 FPR、分 k 表、**以及一个明确的动作**
（改窗口 / 维持 / 摘掉②维，三选一）。

## 9. 相关

- `quant/research/poc_calibration_design.md`（round-1 全文与裁决）
- `quant/core/drift.py`（`POC_WINDOW` / `POC_MIN_SESSIONS` / `THRESHOLDS` 的 live 真源）
- `quant/decision/regime_transition.py`（pivot 锚 zone 的真实机制 = 新 label 的来源）

---

## 10. 结果与裁决（，`quant/research/poc_window.py`）

1,134,107 个单元 / 165 票。共同可评分子集 n=755,453（k=3/5 结构上无 POC 读数）。

| 指标 | AUC（共同子集） |
|---|---|
| **poc20（live 现行）**| 0.5975 |
| **poc60**| **0.5524**|
| ①price_drift_n | **0.6992**|
| 时间（距锚天数） | 0.6158 |

### 10a. round-1 的 0.812 被证实是假象

解耦标签（label 换成 pivot 锚的 zone 组，与 volume profile 零共用）之后：

> **poc60 从 AUC 0.812 掉到 0.552，而且反过来输给 poc20。**

round-1 §9b 的怀疑（自变量与因变量共用同一份 60 根 profile）**得到确证**。
这也说明当时**没有**按那个 0.812 去改 live 参数是对的。

### 10b. 5a 窗口之争 → 维持 20（但这条已无意义，见 10c）

`AUC(60) − AUC(20) = −0.0451`，CI90 [−0.049, −0.041] → 60 显著**更差**，不改。

### 10c. 5b 去留 → **摘掉**

| 判据 | 读数 | 结果 |
|---|---|---|
| 打赢时间基线（≥0.05） | Δ **−0.0183**，CI90 [−0.026, −0.009] | ✗ 显著**输**给数天数 |
| 打赢 ①price_drift（≥0.03） | Δ **−0.1017**，CI90 [−0.113, −0.089] | ✗ 大幅输 |
| 阈值 T=1.9N，FPR 29.5% | 在区间内 | ✓ |
| AUC ≥ 0.65 | 0.5975 | ✗ |

**四条过一条。②POC 维在两个窗口下都打不赢任何一个基线。**

尤其致命的是第二条：**①价格离锚（AUC 0.699）本身就比 POC 好得多**。也就是说
②所提供的信息，①已经提供了而且更好——它不是一个独立维度，是①的一个更差的影子。

**动作（预注册 §5b 强制三选一）：把 ②POC 维从 drift 装置摘掉**，
一并移除 `drift.THRESHOLDS["poc_shift_n"]`、`_poc()`、`POC_WINDOW`/`POC_MIN_SESSIONS`
与 `breaches()` 里那段播报。

### 10d. 诚实标注

- **标签基准率 90.7%**（pivot zone 组几乎每次重画都会动）。极不平衡，AUC 测的是个难题。
  但三个预测器**共用同一个标签同一个样本**，相对比较仍然有效——而结论正是相对的。
- 高基准率本身也是一条发现：**pivot 锚的 zone 极不稳定**，几乎每次重画都变。
  这对「重画触发」这个更大的问题有含义，但不在本轮范围。
- 分 k 上 poc20 随 k 单调**变强**（0.53→0.63），poc60 平坦（0.52）——与 round-1 方向相反，
  再次印证 round-1 是耦合产物。

### 10e. 附带产出

- `quant/research/poc_window.py` + 15 条测试（含源码守卫：label 不得触碰
  `turnover`/`volume_profile`/`top_hvn`/`_poc`，防耦合复发）
- 报告存档 `tmp/backtest/poc_window_report.md`
- **本轮不动 live**：摘除 ②维是一次代码变更，等 用户 点头后单独做
