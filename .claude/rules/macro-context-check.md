# Macro Context Check Rules

Applied when: 分析单日大幅异动、zone update 前、下"技术破位 / 趋势反转"结论前。

## 原则
单日异动的原因决定判断逻辑：个股 fundamental 驱动 → 技术分析适用；宏观 / 板块驱动 → 个股 K 线信号打折（反转形态可能是宏观恐慌回声，等 2–3 日看是回声还是真反转）；混合 → 分层评估。不查 context 就下"技术破位" = 把宏观恐慌误判为个股顶部。

## 触发
硬性：单日 |change| > 5% 且准备 flag 破位 / 反转 / 顶底；出现 shooting star / hammer / engulfing 且准备下趋势改变结论；zone 判为 Event stale；operator pushback 提到宏观。软性：briefing / weekly 分析异动日；"跌得狠该不该抄底"类判断。

## 信息源（情报层收拢：零自行拉取）
1. **tape 定时点**（永远第一步）：60m K 线 `quant/decision/tape_check.py` / `get_kline.py --ktype 60m`，钉住异动发生的时段；任何归因必须与 tape 时点绑得上。
2. **大盘 / 板块**：kline 缓存 QQQ / SPY / SMH / SOXX + NDX / SPX / SOX / VIX（`market/klines/*.jsonl`），判"全板块 vs 个股独立"。
3. **事件叙事**：只读 operator 配置的情报归档（可选的 `ai_news/reports/`，最近两日）；持仓自家财报用 `quant/data/earnings_calendar.py`。归档没有或未配置 = **触发源未知**（合法输出）；不做 WebSearch / 社媒补查，不编造归因。
4. **机械校验**：`quant/decision/catalyst_check.py <T> --date ...`（日期绑定 + tape 否定 + 板块残差）；|单日| > 5% 归因必跑。
5. 恐慌代理：VIX 水平与 Δ、大盘直线下杀无反抽、防御轮动（`regime_score.py`）。

## 判断逻辑
- 个股独立：大盘 ±0.5% 内、个股 ±10%、有明确公司 catalyst → 技术分析可信。
- 板块 / 宏观：大盘同向 ≥1%、板块多票同向、当日有 macro catalyst → 反转形态打折。
- 机械 / flow 放大（负 gamma、CTA、强平踩踏、IPO 抽血、回购静默、rebalance）：均值回归更快、对 thesis 信息量最低、跌破 30D / 50D ≠ 技术破位；**但不放宽 size**，也不豁免 trend-exit-system 的棘轮与趋势死亡线（"机械踩踏会弹回来"是统计倾向不是担保）。上膛半（共识溢价 / 拥挤）与 dealer gamma 结构属于私有策略笔记的内容，若 operator 维护了对应规则则按其执行。

## 执行约束
下 shooting star / blowoff / 反转结论前必须完成 1–3 项（全部本地读取，零成本）；被 pushback 必须补查并修正；宁可标未知，不做叙事归因。Context 只解释异动，不覆盖结构、stop、cap 或 sizing 规则；旧文章里的数字不能替代当前市场观测。
