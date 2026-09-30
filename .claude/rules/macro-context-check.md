# Macro Context Check Rules

Applied when: 分析单日大幅异动、zone update 前、下"技术破位 / 趋势反转"结论前。

## 原则
单日异动先分清**是谁在动**：个股 fundamental 驱动 → 技术分析适用；宏观 / 板块驱动 → 这根 K 线的信息归宏观，不下个股顶底结论；混合 → 分层评估。不查 context 就下"技术破位" = 把宏观冲击误判为个股顶部。

**宏观驱动 ≠ 信号打折**：没有证据支持「宏观驱动 → 反转形态打折、等 2–3 日」；宏观主导度高的时期，机械信号并不因此变差。宏观归因只改变**怎么解释这根 K 线**，不改变机械信号（✓止跌 / gate 转移）的执行与授权。

## 触发
硬性：单日 |change| > 5% 且准备 flag 破位 / 反转 / 顶底；出现 shooting star / hammer / engulfing 且准备下趋势改变结论；zone 判为 Event stale；operator pushback 提到宏观。软性：briefing / weekly 分析异动日；"跌得狠该不该抄底"类判断。

## 信息源（全部本地 / 代码，零自行抓新闻）
1. **tape 定时点**（永远第一步）：60m K 线 `quant/decision/tape_check.py` / `get_kline.py --ktype 60m`，钉住异动发生的时段；任何归因必须与 tape 时点绑得上。
2. **宏观归因**：`python3 quant/journal/macro_lens_log.py --explain <T> --date <D>`（再跑一次 `--explain SPY`）→ 当日涨跌 = 宏观解释（前一日读法链上互不共线的因子 × 40 日多元 β，无前视）+ 残差，给出 **宏观驱动 / 混合 / 非宏观**。当前最强驱动与读法看 `trade/intelligence/macro/current.json`（`--digest`）；未发布 = 该项无输入。
3. **大盘 / 板块**：kline 缓存 QQQ / SPY / SMH / SOXX + NDX / SPX / SOX / VIX（`market/klines/*.jsonl`），判"全板块 vs 个股独立"。
4. **事件叙事**：只读 operator 配置的情报归档（可选的 `trade/intelligence/reports/`，最近两日）；持仓自家财报用 `quant/data/earnings_calendar.py`。归档没有或未配置 = **触发源未知**（合法输出）；不做 WebSearch / 社媒补查，不编造归因。
5. **机械校验**：`quant/decision/catalyst_check.py <T> --date ...`（日期绑定 + tape 否定 + 板块残差）；|单日| > 5% 归因必跑。
6. 风险环境：宏观透镜六盏灯（VIX 分位 / 期限倒挂 / SPY 200 日线 / 信用 / MOVE / 油价冲击）—— 亮灯预告的是波动与回撤深度，不是方向。

## 判断逻辑
- 个股独立：`--explain` 判「非宏观」、大盘 ±0.5% 内、个股 ±10%、有明确公司 catalyst → 技术分析可信。
- 宏观驱动：`--explain` 判「宏观驱动」、大盘同向 ≥1%、板块多票同向 → 这根 K 线不作个股顶底 / 趋势改变结论（形态的信息属于宏观）；机械信号照常执行。
- 机械 / flow 放大（负 gamma、CTA、强平踩踏、IPO 抽血、回购静默、rebalance）：均值回归更快、对 thesis 信息量最低、跌破 30D / 50D ≠ 技术破位；**但不放宽 size**，也不豁免 trend-exit-system 的棘轮与趋势死亡线（"机械踩踏会弹回来"是统计倾向不是担保）。上膛半（共识溢价 / 拥挤）与 dealer gamma 结构属于私有策略笔记的内容，若 operator 维护了对应规则则按其执行。

## 执行约束
下 shooting star / blowoff / 反转结论前必须完成 1–4 项（全部本地读取，零成本）；被 pushback 必须补查并修正；宁可标未知，不做叙事归因。宏观透镜是判读层零授权：不据此改 PLAN / VETO / cap / 股数。Context 只解释异动，不覆盖结构、stop、cap 或 sizing 规则；旧文章里的数字不能替代当前市场观测。
