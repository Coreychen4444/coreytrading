# options_exit_overlay — 单腿 long call 退出栈(premium 口径)预注册

**状态**:✅ 已裁决接线(当日:预注册 → 回放 v1 BS → v2 真实合约 K 线 →
synthetic 1967 → 真实 42 Jan27 → 用户 终裁 **S1=−25 / S2=(20,25) / S4=90 / S3 advisory**,
牛市偏置理由保守取紧;接线 `exit_guard.option_loss_overlay` + at-risk 0.25,测试+变异在案)。
预注册机械裁决如实记录:全样本无臂三判据全过(C2 被 DTE<35 彩票仓杀),裁决转切片证据 +
用户 拍板。+12 月复测。原文如下不改。
**立项链**: `leaps-call-template.md` §13.4 立项(exit_guard 全盯 underlying,构造性盲区 = OTM 横盘 + IV 回落 −64% 零告警)→  用户「后续制定期权自己的 zone/stop/trim」→ **用户 定管理口径**:期权 = 系统唯一杠杆、永不行权、zone/stop/trim 直接用权利金画线,亏损侧要一套先于 underlying stop 响的机制。
**活案例**:GOOGL 270115 390C, 入场 $35,4 个交易日 spot −6.2% 时 premium **−41.5%**— underlying stop 与 premium 损失速度差一个量级的实证。

## §1 范围与裁决状态

本轮只测**亏损侧 S1–S4**(棘轮武装前的裸窗口)。以下已由 用户 裁决、**不在本回测裁决域**:

- 盈利侧结构:正股退出栈照搬 + 第 5 线 underlying regime trim;premium 基(保本地板/回吐上限/G-01)同基取 max,underlying 基(趋势死亡线/regime trim)并行 OR()
- 趋势死亡线保持 underlying 口径()
- underlying regime stop(S5)保留,与 S1–S4 为 OR 双防线
- vehicle 资格 = confirmed-only(probe/early_risky 只能正股),纯 clamp 不需回测(「这个必须」)
- strike 入场 = 8/1 定稿维持:Δ0.60–0.80 主带 / Δ<0.40 禁区 / 仅 IV pct<30–40 放宽 Δ0.55–0.65(复确认)
- 悬而未裁(不阻塞本轮):入场 DTE 下限(定稿 ≥270 vs  口述 >150);sizing 层 premium 化三分法(gross→premium 市值 / 冲击预算保持 BS 重估 / at-risk 保费全额,agent 推荐待 用户 确认)

## §2 待测参数(定义精确化)

执行模型统一:**信号 = 收盘读数,成交 = 次日开盘重建价**(不假设触发价可成交)。成本基准 = campaign 加权保费(多次入场随加仓重算;NOK 4 笔入场即此口径)。同日多线触发取最严动作。

| # | 线 | 定义 | 扫描 |
|---|---|---|---|
| S1 | premium 硬 stop | premium ≤ 加权成本 × (1+s1) | s1 ∈ **{−20%, −25%, −30%, −35%, −40%}**(用户 :−35~−40 太高,加紧档) |
| S2 | 时间 stop | 首笔入场起第 t1 个交易日 underlying close < 入场日 close + 1×ATR20@入场 → 减半;第 t2 个 → 清仓 | (t1,t2) ∈ {(15,25), **(20,30)**} |
| S3 | IV×横盘 | IV_t ≤ IV_entry − 8pts 且 underlying close < 入场 close + 1N → 清仓 | 固定(−8pts, +1N);结论仅方向性(见 §5 近似) |
| S4 | DTE 地板 | 剩余 DTE ≤ d → 无条件平仓(重放中 roll 视同平仓) | d ∈ {**90**, 120, 150}(用户 初值 90;**与 §13.2 定稿 120 分叉**,数据裁,本预注册不静默改定稿) |

参数**各自独立扫**(其余线关闭),另加一个「草案组合臂」(S1=−30 + S2=(20,30) + S3 + S4=90)。**不做全网格联合优化**——n=17 上 60 格网格必然过拟合。

## §3 样本(全量列举,无筛选自由度)

事件流 →08 全部期权 round trip 23 合约,域内(单腿 long call)**17 笔**+ GOOGL 进行中:

**亏家**(final ≤ −20%,9 笔):SMR618C20 −91%(−$780,humility anchor 案)· NOK270115C15 −31%(−$2,295,强平案,4 入 1 出 campaign)· INTC270319C200 −57% · AAOI270617C200 −52% · DRAM261218C70 −46% · RKLB260918C120 −35% · AXTI260918C100 −26% · INTC270115C120 −27% · DRAM270115C60 −23%
**赢家**(final ≥ +40%,4 笔):MU260424C475 +143% · NOK260515C11 +100% · MRVL260821C150 +42% · MRVL270115C150 +40%
**平庸**(4 笔):NOK260501C11 +5% · AAOI270115C140 −18%(2 日 roll 腿)· AAOI270617C140 −5%(46 日)· GOOGL 进行中另列

**域外剔除**(理由固定):AXTI260417P45 / INTC260612P100(put)· AAOI260522P103(卖方)· MU260717C500(spread 短腿)· **MU260717C440 +371%(spread 长腿,退出是 spread 整体决策——附表给数不进判据)**· AMZN261218C280(同日买卖 0 持有)。

## §4 判据(先锁,裁决语言固定)

- **C1 亏家拦截**:对 9 笔亏家,overlay(各独立臂)退出 PnL 相对 actual 的改善**中位 ≥ +15pp**,且无单笔恶化 < −5pp。
- **C2 赢家误伤**:对 4 笔赢家,每笔 overlay PnL ≥ actual × **60%**(允许交确认税,不允许杀掉赢家)。S2 对慢赢家(MRVL270115,66 日 +40%)是最大悬念,单独报。
- **C3 组合净**:17 笔合计 overlay PnL ≥ actual 合计,且最差单笔改善 ≥ −10pp。
- **裁决**:三判据全过 → 推荐该参数档接线 exit_guard;C1 或 C3 单独不过 → 参数回调重跑(标注非预注册内);C2 不过 → 该臂直接否(杀赢家的保护不要);全臂 C2 皆不过 → 只接 S4(DTE 地板无参数争议)。
- n=17 且同 underlying 相关(NOK campaign、DRAM 双腿、INTC 双笔、7/16 六笔同日清账),**输出是案例级证据不是统计裁决**——不报 CI 不装显著,判据全部是「全部案例上的形状」。

## §5 已知混淆与近似(先声明)

1. **premium 重建**= BS(underlying close, K, τ, IV_t, r=4.5%),IV_t 由入场/出场 fill 价反解后线性插值, 后有 iv_history ATM30d 日度序列做形状校验(skew/期限结构未建模)。两端由构造精确锚定 fill 价;**中间段 IV spike 被插值平滑 → S3 触发时点系统性失真,S3 结论降级为方向性**。S1 主要由 underlying 路径驱动,重建质量最高。
2. **跳空**:执行按次日开盘,已含;但 premium 开盘重建用 underlying open + 插值 IV,盘中流动性/spread 未建模 → 改善数字一律**偏乐观 2–5pp 量级**,判据阈值已把这个读进去(C1 的 +15pp 不是 +2pp)。
3. **7/16 清账日**:6 笔 actual 退出是账户级 margin 决策不是逐笔规则退出——actual 基线本身不是「规则臂」,本回测问的是「overlay 在场会不会更好」,不是「overlay vs 完美纪律」。
4. **入场合规错位**:17 笔里多数按新入场规则(Δ带/DTE/confirmed-only)根本不会发生。回放测的是**存量 long call 的退出栈压力测试**(偏严:历史仓比新规仓更烂),不是新体系全流程 EV。
5. GOOGL 进行中笔**不进 C1–C3**,单独一节给「overlay 在场则何时何价退出」的 live 对照读数。

## §6 复测条件

live 单腿 long call 完结 campaign ≥10 笔(新入场规则下),或 +12 月。S3 若要升级为可裁决,需先积累合约级日度 IV(iv_logger 已在跑,ATM 代理)。
