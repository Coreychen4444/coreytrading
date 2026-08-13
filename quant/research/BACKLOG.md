# BACKLOG — /09 两日高密度产出后的全量对账（用户 点名建立）

**用法**：下一个 session 从这里开工。完成一项 → 当轮划掉并写去向（commit / 裁决记录）。
每项都带出处（ledger 条目 / design 文档 / commit）——**引用 ledger 条目时 grep 符号，
不抄行号**（行号一天内实测漂移）。本清单建立时 30 条 ledger 分叉刚做完全量复核
（17 确认 / 8 行号漂 / 3 部分错已改 / 2 误报已改记），下列「开着的」都是**复核确认过**的真缺口。

---

## P0 — 用户 裁决点 ✅ 三件全部裁决并落地（当日）

- [x] **R2**✅ **用户 裁决：「直接按最新规则，以前讨论过的不必理会」**→  NBIS
  规则（更晚）覆盖**促升持仓加仓授权域**；07-30「永不」保留辖新开仓/常规 zone 加仓
  （无更晚裁决触及该域）。live 代码不动。去向：`position-tiers.md` §Gate 授权域
  precedence 注 + ledger R2 结案。
- [x] **N1**🔁 **同日二裁翻案 → 立项并当日接线**（第一裁「不立项」当日晚被 用户 推翻：
  「还是要立项的 初步给定单笔止损后需<=5%」）。初值 `AT_RISK_CAP_FRAC = 0.05`
  （单笔止损后损失 ≤5% NAV）；规格 `risk_unit_design.md` §b1（正股 binding stop /
  期权保费全额 loss/unit=1.0 / 不乘 1.48 实现系数）。接线：入场侧 = `size_caps`
  第⑤条（后备，几乎恒被 n_unit 先咬）；持仓侧 = `portfolio_brief.at_risk_breach_signal`
  （真咬点）。**日 1 已知咬点 = GOOGL LEAPS 保费 ≈$2,048 ≈ 9.4% NAV 超 ~1.9×**，
  告警点名处置归 用户。防误引边界保留：15% 冲击预算是 ATR 尺不是 stop 尺。
- [x] **熔断 1.5×**✅ **用户 裁决：触发直接清仓**（核实 = §2.7 原话「立即减仓甚至清仓，
  盘中立即执行」，记忆正确；当时未定版是因为 H4 反事实读出「清仓在卖底」但被 round-1
  杠杆 bug 污染、round-2 未重跑——**裁决知情作出，H4 复验挂 +12 月复测**）。
  去向：`shock_budget_design.md` §15c 定版 / `sizing.SHOCK_BREAKER_MULT=1.5`（与回测
  BREAKER_MULT 由 pins 测试钉）/ `portfolio_brief.shock_breaker_signal`（受管书单日
  损失 ≥22.5% NAV → 火警级告警绕过 24h 冷却，动作行 = 直接清仓盘中立即执行）/
  CLAUDE.md 冲击预算行补熔断。

## P1 — 机械快清 ✅ 全部完成（本轮，13/13；测试 1016 全绿；去向 commit 999febd）

- [x] **V1**✅ 三处全改「`CAP_FRACS["confirmed"]` 动态（今 0.40）」写法：
  `position-tiers.md` regime 统一段 / `regime_field_unification_design.md` 规格表+铁则1 /
  `annotate_event.py` docstring。live 卡已验证无 0.05 残留（AMZN/MSFT 已 0.40），
  `classification_for_cap` 反查本来就是动态的——纯文案锈。
- [x] **V2**✅ `trading-discipline.md` 表格 50/30/20 + 算例改 2% ≈$425 @ NAV $21.2k
  （镜像 position-tiers 同款数字）；`position-tiers.md` 降级表同款 45-50 顺手对齐。
- [x] **V3**✅ `entry_guard.reclaimed_gate_of` 改数 kline bars（新 rows 参数；缺省退
  自然日近似=只收紧）；测试 `test_reclaim_window_counts_trading_days_not_calendar_days`
  构造 14 自然日/10 交易日窗口——两种实现给相反答案，**钉的是单位**。
- [x] **V4**✅ 四处（backlog 三处 + `portfolio_brief` 卡片行）改「T+1 起恢复全额」；
  深挖再修 3 处同款源头（`earnings_window.py` 头注 + design §0/§10e——原文
  「T+1 收盘后恢复」与「T+0 单日」同句自相矛盾，带更正标记不无痕改写）。
- [x] **V6**✅ `tests/test_constant_pins.py::test_rel_floor_live_equals_backtest`。
- [x] **R4**✅ `portfolio_brief.format_regimes` 停读 unlocks（改渲染 next_regime.name，
  与 `gate_ladder_report`  的处理对齐）。
- [x] **R5**✅ `poc_window.final_action` 三分支改已裁决态：drop 分支=复算确认无待办；
  两个 keep 分支=「与  已执行裁决相反，翻案归 用户」。测试同步改钉新语义。
- [x] **N3+N4**✅ `schema.REGIME_SOURCES = {card, auto, demoted}` 枚举校验 + 测试
  （transition/promotion/Card typo 全拦）；design 规格表 transition→auto、促升行
  promotion→card（live 印证 NBIS）；`structure.py`/`position-tiers.md`/`CLAUDE.md`
  的 promotion 注释同步清除。
- [x] **V8/V9**✅ `gate_backtest2_design`/`ladder_entry_design` 状态行划线+指针（后者
  含 A7 结案与适用域注记）；`earnings_window_design` 第二个 §10f 改 **10f-b**+
  「未生效」两处划线指 §10g；`shock_budget_design` §10 未生效句/§10f「不动 live」/
  「下一轮尚未立项」三处划线指 §12–15 与 risk_sector 结案。原文全部保留。
- [x] **§3.5 债务钉 ×2**✅ `tests/test_constant_pins.py`（G01_GAINS↔g01_levels 增益列 +
  STALE_CONFIRM_DAYS——后者先在 entry_guard/portfolio_brief 各提名常量再钉相等）。
- [x] **§F**✅ `shock_budget_wiring_todo.md` §F 标结案（真聚类版当日被否 ARI 0.15，
  分板块永久取消）。
- [x] **CLAUDE.md 冲击预算行**✅ 补「× 财报反应日 T+0 缩仓（min(1, 1.88/X_earn)×0.7，
  T+1 起恢复全额）」。
- [x] **ladder_entry §10e**✅ 补第五消费者 `gate_ladder_report`（04:20 推 TG 的渲染面，
  与 `_repair_domain` 同拍镜像）。

## P2 — 需要小设计的 ✅ 全部完成（本轮，4/4；去向 commit 999febd）

- [x] **A1**✅ **走 (a) 删死字段**（理由：(b) 会把已证明错 7 天的手填日期当 FYI 播出去；
  财报情报层由 earnings_calendar 的 vendor day-0 供给）。三卡已清（MRVL/NBIS 整块删、
  NVDA 留 `vehicle: leaps`；走 `update_position_zones --constraint-* none` 通道，
  保留路径防呆验证 zones 零变异）+ `sizing.constraint_lines` 加**死字段守卫行**
  （带时间窗无 max_classification → ⚠️ 必响）+ 测试。附带修掉一个显示 bug：
  NVDA 的 vehicle 行此前被 from=8/19 拖成「[窗口未开]」。副作用：三卡
  thesis.last_reviewed 被刷（时间 staleness 钟重置 ≤14 天，价格/结构触发不受影响）。
- [x] **A2**✅ `portfolio_brief.earnings_trim_signal`（纯函数）+ build_brief 装配。
  T-1 判定 = 下一工作日为反应日（周五对周一反应日照算 T-1）；比较口径与
  position_budget 同源（class_cap × 明日 shrink vs |qty|×现价×乘数）；budget 推不出
  （cap-0 促升卡如 NBIS）**退名义 40% 基数不漏检**。key 用 `TICKER:earnings_trim`
  而非 backlog 写的 INFRA:*（INFRA 留基础设施 per heartbeat.md，且同日多票财报
  共用一 key 会互吃 24h 冷却）。测试 `tests/report/test_earnings_trim_alert.py`。
  **8/11 人工核对不再需要**：LITE 无持仓（watchlist），真正持仓里 8/12 反应日的是
  NBIS——本告警 T-1 自动点名。
- [x] **A3**✅ 转移机上行分支写 bfloor（`stop = gate − 0.5×ATR20@转移日`、
  `stop_level_days=1`、`stop_derivation` 留痕含被覆盖的画卡值；ATR 算不出保持旧行为
  不编数）+ `update_position_zones.warn_bfloor_gap`（[0.4,0.6]N 容差，warn 不拦，
  期权卡/无 repair_gate 跳过）+ 两处测试。实现已批语义（round-2b），未过
  回测闸门（按项目条款）。
- [x] **§3.5-4**✅ `portfolio_brief.calendar_stale_note`：>2 **工作日**未重建 →
  `INFRA:earnings_calendar_stale`（周五建→周一读=1 工作日，周末不误报；日历整体缺失/
  as_of 不可解析也响）。选告警不选 postclose 重建——重建要打 OpenD 拉财报期，
  postclose 链路不加网络依赖。测试同文件。

## P0-b — 新增裁决点（晚场收尾时留下的，用户「先这样吧」暂挂）

- [ ] **at-risk 口径分叉**（`risk_unit_design.md` §b1）：现 live = **mark 口径**
  （现价−stop）——它会跟 trend-exit「回吐上限 1/3」抢赢家管辖权（+100% 票的 at-risk
  全是浮盈回吐）；候选 = **本金口径**（max(0, 成本−stop)，棘轮保本后本条自动退场，
  与 trend-exit 零冲突，更贴「止损后损失」字面）。当前书两口径结论相同（全新仓 +
  GOOGL 两算法都超），改动零即时影响。agent 建议改本金口径，归 用户 定。

## P3 — 立项候选（要 用户 批预注册才动）

- [ ] **at-risk cap 代价定价回测**（轻量，非裁决门； 晚场候选）：
  fiat cap 先例 =「数字 用户 拍，代价系统量」（110% 量过 0.17pp / 15% 量过打穿率）。
  量两处：①期权保费 >5% NAV 的历史命中与砍掉的尾巴；②高波动大赢家 3N trail 下
  仓位%×3ATR% >5% 的强制减仓反事实（exit_rules_backtest 持仓路径重放，半天级）。
  产出 =「5% 这个偏好历史上花多少 pp」，供校准 5% 或改口径。

- [x] ~~**risk_unit (b)**at-risk 天花板~~ → **同日二裁：立项并当日接线**
  （初值 ≤5% NAV，`risk_unit_design.md` §b1；第一裁「不立项」同日被推翻，两裁都留档）。
- [ ] **options_exit_overlay_backtest**（立项**未跑**）：exit_guard 期权层参数
  vs 现行 underlying-only。前置于 LEAPS §十三参数化。构造性盲区：OTM 横盘+IV 回落可
  零告警 −64%。
- [ ] **vol_baseline_backtest**（立项**未跑**）：量比分母自污染口径（事件段
  抬高 SMA20）。与 relative_volume 正交（那轮换的是分子参照系）。
- [ ] **H1 重校准**（`shock_budget_design.md` §15a）：SHOCK_BUDGET 15% 的打穿率只有目标
  0.02–0.04×，因为校准假设满仓而实际稀疏。要先给「典型占用率」经验值——**等 live
  跑出几个月占用数据再立项**，现在没料。

## P4 — 日期驱动（到点必做，错过有实害）

| 日期 | 事项 |
|---|---|
| **（周一）**| LITE 财报 8/11 AMC → **8/12 反应日 T+0 缩仓 ×0.24 首个 live 样本**。验证：备注 `z1:<1%:100`、brief 缩仓行、8/13 恢复。~~⚠️ A2 减仓半边还没代码——8/11 收盘前人工核一次 LITE 敞口 vs 缩仓 cap~~ → **A2 已修**：`earnings_trim` T-1 告警自动点名，8/11 无需人工（LITE 本无持仓；持仓中 8/12 反应日 = NBIS，同样被覆盖）。验证项保留 |
|  前后 | MRVL 财报（calendar pub_day 8/27）。若 A1 还没修，卡上 from=8/13 的死字段会继续错着——顺手案例 |
| ****| 美股冬令时：`postclose-transition` cron `10 4`→`10 5`、`gate-ladder-report` `20 4`→`20 5`（有内建盘中自检兜底，但别靠它） |
| /08 | +12 月复测窗集中到期：escape_velocity / gate2+2b / earnings_gate（im5-10 桶预计率先够 Kish）/ repaint_trigger / relative_volume / gate_path_taxonomy / ladder_timing / **stop_cost_guard 作为盲测守卫**（含④历史新高与②③家族——那才是代价守卫无污染的检验；量腿窄池 R 功效不足同窗复验） |

## 已闭环对账（这两天完成的，防下一 session 当新发现重查）

冲击预算四数定版接线（40/15/1.88/110，`available_cap` 单一真源）· earnings_window 路 A
接线（T+0 缩仓）· risk_sector 分板块**否**（结案）· RULINGS_LEDGER 建账 + **30 条全量复核**
（V5/§3.5-5 判误报、R1/A7/A4/A5/A6/R3/P0 结案）· gate_path_taxonomy 四类全否 +
`core/episodes.py` 单一真源 · 四词换名上线（突破线/箱体顶/新高线）· ladder_timing 拆出
「早一天」+ stop_cost_guard 代价守卫重判 → **fastlane_k=0.5 留任**· `_fastlane` 接适用域
判别式（非①一律 ×2，R1 彻底结案）· 持仓 z1 回踩线补洞（只给 Today 成员）· 报表 ⚡ 域
镜像（域外不预告触发价）· 五维→四维 stale 字符串清四处 · RelVR 补进 rules +
zone-touch-confirmation 换尺 · CLAUDE.md v3 行补财报缩仓例外 · trading-discipline 补
repair_reclaim 分流段。

## 本清单不覆盖的（诚实边界）

- ledger **§1 总表与 §2 命名冲突表**未做逐条复核（本轮复核范围 = §3 分叉清单 30 条）。
  §2 两天里被高频引用未见错，优先级低；引用时照旧三问（哪份文档/哪层/哪个自由度）。
- `klines_deep` 不入 git 且会过期——线名④（ath）与回测依赖它，`_repair_domain`（只要
  120 日窗）常规缓存即够。深度缓存刷新无 SLA，暂靠 merge 降级播报兜底。
