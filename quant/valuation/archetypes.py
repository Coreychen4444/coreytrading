#!/usr/bin/env python3
"""archetypes.py — 商业模式原型 → 估值参数缺省; 人工区 inputs.json 的读取与校验。

为什么存在 (用户): 「根据商业模式、估值久期、资产负债表、经营周期、增量新闻
大致算出股价」—— 前四项里只有商业模式是**人给的**, 其余三项都能从三表/市场数据算出来
(久期是模型的输出不是输入; 资产负债表走权益桥; 经营周期走利润率归一化)。所以人工区只留
两样: ① 每票归哪个原型 (+ 为什么), ② 明确知道比缺省更准的覆盖值与增量新闻 overlay。
其余一律由原型缺省 + 数据推导, 缺省值本身写在这里, 改了有 git 记录。

原型 (`ARCHETYPES`) 的每个参数都是**声明**, 不是拟合: 数字来自估值常识 (Damodaran/McKinsey
的口径), 未经本仓校准 —— `valuations.jsonl` 攒够样本后才有资格调。

人工区文件 = `trade/us_stock/valuation/inputs.json`:
    {"tickers": {"GOOGL": {"archetype": "platform", "rationale": "...",
                           "overrides": {...}, "news_overlay": [...]}}}
  overrides 只认 `OVERRIDE_KEYS`; news_overlay 每条 = {date, source, driver, delta, note[, valid_until]}
  = 用户 **手写**的增量新闻 (reliability=manual, 缺 valid_until 即不过期), driver 只认
  `OVERLAY_DRIVERS` (= EPS_DRIVERS ∪ PE_DRIVERS, 与雷达写的 overlays.jsonl 同一词表, 见 overlays.py)。
  读不认识的键 = 报错, 不静默忽略 —— 写错键名等于没写, 比没写更坏。
  ⚠️ 雷达自动的新闻→驱动走 `ai_news/valuation/overlays.jsonl`, 不走本文件。

🚨 零授权效力: 本层不被 sizing/entry_guard/exit_guard import, 不写 positions/watchlist。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 直接 `python3 quant/<层>/<模块>.py` 时让 `quant.*` 可 import

import copy
import json

from quant.core.io_json import read_json  # noqa: E402
from quant.core.paths import VALUATION_INPUTS_PATH  # noqa: E402

# ── 原型缺省 ────────────────────────────────────────────────────────
# horizon_years     显式预测期 N (终值之后按 terminal_growth 永续)
# growth_source     起始增速 g0 的取法: blend = 均(TTM yoy, 3y CAGR); long_run = 5y/10y CAGR 中位
#                   (周期票不用峰值增速); ttm = TTM yoy (年轻公司只有这一个数)
# growth_cap/floor  g0 夹逼区间 (声明, 防极值把模型炸成天文数字)
# fade              g 从 g0 到 gT 的路径: linear / exp (半衰期 half_life 年)
# margin_method     营业利润率路径: current = 维持 TTM; through_cycle = 向历史中位回归
#                   (周期票: 顶部 PE 最低是陷阱); target = 向 target_margin 爬升 (亏损成长股)
# margin_fade_years 利润率到达终点的年数
# revenue_method    收入基数: actual = TTM 收入; trend = 向 10 年对数趋势线回归 (周期票: 峰值收入 × 中周期
#                   利润率仍偏乐观 —— MU 2026 FQ3 TTM 收入是趋势线的 1.6×; 增速也改用趋势斜率, 首尾点 CAGR
#                   在谷→峰区间有端点偏差)。 第二轮加, 回测**未采纳**为缺省 (设计 §10.4: 4 季前向增速
#                   覆盖 31%→24%、|偏差中位| 3.3%→4.0% 双双变差 —— AI 上行周期里向趋势回归 = 更悲观), 保留为覆盖项
# revenue_fade_years 收入水平回归趋势线的年数 (与利润率回归同拍)
# ronic_cap         终值期新增资本回报上限 (RONIC_T = clamp(历史 ROIC 中位, WACC, cap));
#                   None = WACC+2pp (无护城河假设)
# s2c_default       销售/投入资本比缺省 (历史算不出时用; 再投资 = Δ收入 / s2c)
# s2c_floor         销售/资本比地板: 扩产期 (产能先于收入) 的当前值不是稳态, 低于地板取地板
# engine_weights    v3: fcf_dcf (本仓 DCF = 自己的判断) 与 street (卖方一致目标价按 r_e 折现 12 个月 = 机构口径) 的加权;
#                   无目标价 (n<3) 时 street 自动 0 权。旧 EPS×PE 引擎降为交叉显示不进权重
# scenario          熊/牛情景相对基准的偏移: g0 ∓, 终点利润率 ∓, WACC ±, (周期票) 利润率回归年数 ∓
#                   ⚠️ 回测校准 (calibrate backtest, 4 季前向, 设计 §10.4): 原 ±5pp 只盖住周期票 31% /
#                   平台 53% 的实现增速; 按「±1.5Δ 内 ≈75%」定宽 → 周期 g±20/m±10, 平台 g±10/m±6, 软件 g±15/m±8
# base_confidence   原型先验置信 (2 高 / 1 中 / 0 低), 与数据质量旗标合成最终置信
# ── v2 ( 夜, 用户: 「NBIS 才 19 MU 才 232 严重滞后」; 参照 Damodaran + 开源 DCF 通行做法) ──
# industry_beta     Damodaran 行业 levered β (半导体 1.52 / 软件 1.28–1.69 / 计算机 1.35)。v3 起**只用行业 β**
#                   (Damodaran 自下而上 β 的主张: 回归 β 噪声大), 个股 5 年月度回归只作交叉显示; 周期原型跑道改 10 年指数
#                   衰减 (半衰期 3 年)、年 2 起封顶 30%、RONIC 上限 20% —— AI 半导体 ROIC (剔商誉) 20–40%
# margin_window_q   through_cycle 中位的窗口 (季): 周期票取近 5 年 (20 季) —— 行业整合后中周期利润率高于 10 年中位
# growth_cap_after  一致前瞻锚定后**第 2 年起**的增速上限 (第 1 年 = 一致前瞻收入的跳变, 不设上限)
#                   terminal_growth 统一 3.5% (Damodaran: 稳态增速 ≤ rf 4.75%; 开源模型通行 3% 名义 GDP; 取中)
ARCHETYPES: dict[str, dict] = {
    "platform": {
        "label": "平台/净现金高盈利 (广告·云·软硬一体)",
        "horizon_years": 10, "terminal_growth": 0.035,
        "growth_source": "blend", "growth_cap": 0.30, "growth_floor": 0.0,
        "fade": "linear", "half_life": None,
        "margin_method": "current", "margin_fade_years": 1, "target_margin": None,
        "revenue_method": "actual", "revenue_fade_years": 1,
        "industry_beta": 1.30, "margin_window_q": 40, "growth_cap_after": 0.25,
        "ronic_cap": 0.25, "s2c_default": 2.5, "s2c_floor": None,
        "engine_weights": {"fcf_dcf": 0.5, "street": 0.5},
        "scenario": {"growth": 0.10, "margin": 0.06, "wacc": 0.005},
        "base_confidence": 2,
    },
    "semi_cyclical": {
        "label": "半导体/光模块 周期 (利润率随 capex 周期大幅摆动)",
        "horizon_years": 10, "terminal_growth": 0.035,
        "growth_source": "long_run", "growth_cap": 0.25, "growth_floor": 0.0,
        "fade": "exp", "half_life": 3.0,
        "margin_method": "through_cycle", "margin_fade_years": 5, "target_margin": None,
        "revenue_method": "actual", "revenue_fade_years": 3,
        "industry_beta": 1.52, "margin_window_q": 20, "growth_cap_after": 0.30,
        "ronic_cap": 0.20, "s2c_default": 1.5, "s2c_floor": None,
        "engine_weights": {"fcf_dcf": 0.5, "street": 0.5},
        "scenario": {"growth": 0.20, "margin": 0.10, "wacc": 0.005, "margin_fade_years": 2},
        "base_confidence": 1,
    },
    "ai_infra_capex": {
        "label": "AI 基础设施 高 capex (当前亏损, 价值几乎全在终值)",
        "horizon_years": 10, "terminal_growth": 0.035,
        "growth_source": "ttm", "growth_cap": 0.60, "growth_floor": 0.10,
        "fade": "exp", "half_life": 2.0,
        "margin_method": "target", "margin_fade_years": 5, "target_margin": 0.25,
        "revenue_method": "actual", "revenue_fade_years": 1,
        "industry_beta": 1.69, "margin_window_q": 40, "growth_cap_after": 0.60,
        "ronic_cap": None, "s2c_default": 0.8, "s2c_floor": 0.8,
        "engine_weights": {"fcf_dcf": 0.5, "street": 0.5},
        "scenario": {"growth": 0.15, "margin": 0.10, "wacc": 0.010},
        "base_confidence": 0,
    },
    "software_growth": {
        "label": "高倍数软件 (高增速 + 利润率爬坡)",
        "horizon_years": 10, "terminal_growth": 0.035,
        "growth_source": "ttm", "growth_cap": 0.50, "growth_floor": 0.05,
        "fade": "exp", "half_life": 3.0,
        "margin_method": "target", "margin_fade_years": 5, "target_margin": 0.40,
        "revenue_method": "actual", "revenue_fade_years": 1,
        "industry_beta": 1.45, "margin_window_q": 40, "growth_cap_after": 0.40,
        "ronic_cap": 0.20, "s2c_default": 2.0, "s2c_floor": 1.5,
        "engine_weights": {"fcf_dcf": 0.5, "street": 0.5},
        "scenario": {"growth": 0.15, "margin": 0.08, "wacc": 0.005},
        "base_confidence": 1,
    },
    "not_applicable": {
        "label": "不适用 (非经营性公司 / 结构豁免持仓 / 资产净值型)",
        "horizon_years": None, "terminal_growth": None,
        "growth_source": None, "growth_cap": None, "growth_floor": None,
        "fade": None, "half_life": None,
        "margin_method": None, "margin_fade_years": None, "target_margin": None,
        "revenue_method": None, "revenue_fade_years": None,
        "industry_beta": None, "margin_window_q": None, "growth_cap_after": None,
        "ronic_cap": None, "s2c_default": None, "s2c_floor": None,
        "engine_weights": {"fcf_dcf": 0.0, "street": 0.0},
        "scenario": None,
        "base_confidence": None,
    },
}

# 人工可覆盖的参数 (键名即模型参数名)
OVERRIDE_KEYS = frozenset({
    "growth_start", "terminal_growth", "horizon_years", "growth_cap", "growth_floor",
    "target_margin", "margin_fade_years", "sales_to_capital", "ronic_terminal",
    "history_start", "beta", "wacc", "probabilities", "engine_weights", "tax_rate",
    "revenue_method", "revenue_fade_years", "trend_min_r2", "growth_cap_after", "use_consensus",
    "industry_beta", "margin_window_q",
})
# 新闻→估值驱动词表 (overlays.py 契约与 model.apply_overlays_* 共用; 语义见 model.py 顶部)
EPS_DRIVERS = frozenset({"revenue_base", "growth_start", "target_margin", "margin_start",
                         "share_change_rate", "net_interest_after_tax", "tax_rate"})
PE_DRIVERS = frozenset({"erp", "rf", "wacc", "terminal_growth", "pe_multiplier", "scenario_tilt"})
OVERLAY_DRIVERS = EPS_DRIVERS | PE_DRIVERS
DEFAULT_PROBABILITIES = {"bear": 0.25, "base": 0.50, "bull": 0.25}


def _validate_ticker_entry(ticker: str, e: dict) -> None:
    if not isinstance(e, dict):
        raise ValueError(f"{ticker}: 条目必须是对象")
    arch = e.get("archetype")
    if arch not in ARCHETYPES:
        raise ValueError(f"{ticker}: archetype {arch!r} 不在 {sorted(ARCHETYPES)}")
    if not str(e.get("rationale") or "").strip():
        raise ValueError(f"{ticker}: 缺 rationale —— 原型归属是判断, 判断要留痕")
    unknown = set(e) - {"archetype", "rationale", "overrides", "news_overlay", "notes"}
    if unknown:
        raise ValueError(f"{ticker}: 不认识的键 {sorted(unknown)}")
    ov = e.get("overrides") or {}
    bad = set(ov) - OVERRIDE_KEYS
    if bad:
        raise ValueError(f"{ticker}: overrides 里不认识的键 {sorted(bad)}; 只认 {sorted(OVERRIDE_KEYS)}")
    if "probabilities" in ov:
        p = ov["probabilities"]
        if set(p) != {"bear", "base", "bull"} or abs(sum(p.values()) - 1.0) > 1e-6:
            raise ValueError(f"{ticker}: probabilities 必须含 bear/base/bull 且和为 1")
    if "revenue_method" in ov and ov["revenue_method"] not in ("actual", "trend"):
        raise ValueError(f"{ticker}: revenue_method 只认 actual|trend")
    if "engine_weights" in ov:
        w = ov["engine_weights"]
        if set(w) != {"fcf_dcf", "street"} or any(v < 0 for v in w.values()) or sum(w.values()) <= 0:
            raise ValueError(f"{ticker}: engine_weights 必须含 fcf_dcf/street 且非负")
    for i, o in enumerate(e.get("news_overlay") or []):
        need = {"date", "source", "driver", "delta", "note"}
        if not isinstance(o, dict) or not (need <= set(o)) or not (set(o) <= need | {"valid_until"}):
            raise ValueError(f"{ticker}: news_overlay[{i}] 必须有键 {sorted(need)} (可选 valid_until)")
        if o["driver"] not in OVERLAY_DRIVERS:
            raise ValueError(f"{ticker}: news_overlay[{i}].driver {o['driver']!r} 不在 {sorted(OVERLAY_DRIVERS)}")
        if not isinstance(o["delta"], (int, float)):
            raise ValueError(f"{ticker}: news_overlay[{i}].delta 必须是数")


def validate_inputs(doc: dict) -> None:
    if not isinstance(doc, dict) or not isinstance(doc.get("tickers"), dict):
        raise ValueError("inputs.json 顶层必须有 tickers 对象")
    for t, e in doc["tickers"].items():
        if t != t.upper():
            raise ValueError(f"ticker 键必须大写: {t}")
        _validate_ticker_entry(t, e)


def load_inputs(path: Path | None = None) -> dict:
    p = Path(path or VALUATION_INPUTS_PATH)
    if not p.exists():
        return {"tickers": {}}
    doc = read_json(p)
    validate_inputs(doc)
    return doc


def resolve(ticker: str, inputs: dict) -> dict:
    """原型缺省 + 覆盖 + overlay → 一份带出处的参数表。

    返回 {"archetype", "label", "params", "overrides", "news_overlay", "provenance": [...]}
    —— provenance 逐条写「哪个参数从哪来」, 进账本与渲染。"""
    entry = (inputs.get("tickers") or {}).get(ticker.upper())
    if entry is None:
        raise KeyError(f"{ticker}: inputs.json 没有归原型 —— 先在人工区写 archetype + rationale")
    arch = entry["archetype"]
    params = copy.deepcopy(ARCHETYPES[arch])
    prov = [f"archetype={arch} ({entry.get('rationale')})"]
    overrides = dict(entry.get("overrides") or {})
    for k, v in overrides.items():
        prov.append(f"override {k}={v}")
    overlay = list(entry.get("news_overlay") or [])
    for o in overlay:
        prov.append(f"overlay {o['date']} {o['driver']} {o['delta']:+g} ← {o['source']}: {o['note']}")
    return {
        "archetype": arch, "label": params.pop("label"), "params": params,
        "overrides": overrides, "news_overlay": overlay, "provenance": prov,
        "notes": entry.get("notes"),
    }


def main(argv: list[str] | None = None) -> int:
    doc = load_inputs()
    print(json.dumps({t: e["archetype"] for t, e in doc["tickers"].items()}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
