# 退出栈画线整合 — moomoo 提醒的 binding 下方线 ()

**裁决**: 用户—— 提醒/画线层把退出栈合成为 binding 线 + 来源备注。
实现 `quant/sync/price_reminder_sync.py`；真源 `state/exit_guards.json`。

## 分组：按方向，不按哲学归属

原提案 "止损侧两者 max / 赢家侧四者 max" 修正为：

| 方向 | 成员 | 合成 | note |
|---|---|---|---|
| 下方硬线 (跌破触发) | 卡片 stop + 棘轮地板 + 趋势死亡线 | **同基三者取 max**| `stop:card` / `stop:lock` / `stop:trail` |
| 上方止盈 (涨入触发) | trim_zone (正股) / G-01 下一档 (期权 lot) | 独立线，不参与 max | `trim` / `g01` |

棘轮地板是**下方**线——混进"赢家侧 max"会被上方的 trim 吃掉，丢的恰是保护线。
数学基础：三条地板全部只升不降 ⇒ max 合成线也只升不降，适合做提醒线。
tie-break：同价并列取静态源优先 (card > lock > trail)。

## 词表 (moomoo note 上限 10 字符)

- `stop:card` (9) / `stop:lock` (9, 🔒 棘轮) / `stop:trail` (10, 📉 死亡线) — emoji 对应
  exit_guard 告警家族。`stop:ratchet` 12 字符超限，故 lock。
- 裸 `stop` =  前旧词，认旧不写旧（同 `cc:`），首轮对账 DEL+ADD 换血迁移。
- 语义分层 ()：trim 只属于止盈区词汇，下方线一律 stop: 族——不写 `trim:type`。

## 单一真源 + 滞后

- 地板/trail **只读 exit_guards.json 已算好的值**(`ratchet.floor` / `trail.trail`)。
  不越价约束、armed_date/seed_close 口径、parabolic 分支、HWM 全在 exit_guard——
  reminder 层重算 = 第二套口径。layering: sync 层不 import decision 层，走
  `core.paths.EXIT_GUARDS_PATH` 文件耦合。
- **结构性滞后 ≤6h**：guards 由 brief 阶段刷新，pipeline 顺序 reminders < brief 被
  test_pipeline 钉死 (brief 必须最后)。陈旧值因只升不降恒 = 偏低 = 报警偏晚，
  触发权威仍是 exit_guard 每轮 heartbeat 的真线。guards 缺失 → 退化 stop:card 并播报；
  as_of > 3 天 → 播报仍用值。

## 期权 basis 分离 (不同基价格不可比，max 只在同基内取)

| 候选 | basis | 挂哪 |
|---|---|---|
| 卡 stop (`stop_level_basis=underlying`, 默认) | 正股价 | 正股 code |
| trail | 恒 underlying 收盘 | 正股 code |
| 棘轮地板 (期权仓) | 期权 mark | 合约 code |
| 卡 stop (`stop_level_basis=option_price`) | 期权 mark | 合约 code |
| G-01 下一档 | 期权 mark | 合约 code (`g01`, PRICE_UP) |

每 underlying 一条下方线（跨该 underlying 的正股+期权仓取同基 max）。
**能力收据**：moomoo 支持期权合约价格提醒—— 实测
US.GOOGL270115C390000 ADD/GET/DEL 全 RET_OK (probe 脚本一次往返，已清理)。

## v1 近似 (有意的简化)

- g01 线用**现 mark 判"已过档"**，不读 guards 的 `g01_crossed` 状态——提醒是预警，
  ONCE_A_DAY 自限噪；lot 级 cost×(1+g) 同 exit_guard 公式，全 lot 取 min = 下一个会响的档。
- `G01_GAINS` 镜像 exit_guard `PARAMS["g01_levels"]` 增益列（sync 不能 import decision，
  常量复制 + 注释指向；改档位两处同改）。

## 验收 (首轮)

- 测试 18 项 (tests/sync/test_price_reminder_sync.py)，全仓 612 绿。
- dry-run 实录：4 条裸 stop → stop:card 同价换血；GOOGL LEAPS 首次有线
  (正股 stop:card 358.5 + 合约 g01 70.0 = lot 35×2)；全部 card binding 正确——
  当时无仓位武装棘轮、trail 均低于卡 stop。
