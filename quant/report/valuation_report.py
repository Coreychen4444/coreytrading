"""Render the current frozen valuation receipt, without state writes; 零授权效力."""
from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from quant.core.agent_contracts import canonical_hash
from quant.core.valuation_contracts import finite


def _price(value):
    return f"${value:,.2f}" if finite(value) else "未知"


def fyi_map(receipt: dict) -> dict[str, str]:
    """A missing current result must not silently fall back to an older target."""
    if (not isinstance(receipt, dict) or receipt.get("authority") != "analysis_only"
            or receipt.get("receipt_hash") != canonical_hash({k: v for k, v in receipt.items() if k != "receipt_hash"})):
        raise ValueError("valid frozen valuation receipt required")
    output = {}
    for ticker, row in (receipt.get("tickers") or {}).items():
        parts = [f"📐[FYI] 估值 {receipt.get('model_version')} · 计算 {receipt.get('as_of')}"]
        base = row.get("baseline") or {}
        if row.get("status") == "computed" and finite(base.get("fair_value")):
            parts += [f"基准DCF {_price(base['fair_value'])}",
                      f"参考价 {_price(base.get('price'))}（{base.get('price_time')}）"]
            target = base.get("market_target_12m") or {}
            parts.append(f"12个月条件价 {_price(target.get('price'))}（EPS {_price(target.get('eps'))} × PE "
                         + (f"{target['pe']:.2f}" if finite(target.get("pe")) else "未知") + "）")
            for case in (row.get("macro_scenarios") or []) + (row.get("sensitivities") or []):
                label = (case.get("scenario") or {}).get("label") or case.get("scenario_id") or "宏观情景"
                value = case.get("fair_value") if case.get("status") == "computed" else None
                parts.append(f"条件[{label}] DCF {_price(value)}"
                             + ("（传导未完整量化）" if case.get("status") == "partial" else ""))
        else:
            parts.append("本轮估值未知：" + "; ".join(row.get("missing") or ["缺少有效计算"]))
        short = row.get("short_term") or {}
        if short.get("status") == "research_estimate":
            for horizon, window in (short.get("windows") or {}).items():
                if window.get("status") == "estimated":
                    parts.append(f"{horizon}交易日研究条件价 {_price(window.get('price'))}"
                                 f"，区间 {_price(window.get('low'))}–{_price(window.get('high'))}（准确率未验证）")
        else:
            parts.append("短期反应未知（事件样本或查询不足）")
        parts.append("零授权")
        output[ticker] = " · ".join(parts)
    return output
