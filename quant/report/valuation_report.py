"""Render the current frozen valuation receipt, without state writes; 零授权效力."""
from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from quant.core.agent_contracts import canonical_hash
from quant.core.valuation_contracts import finite
from quant.valuation.model import publication_policy


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
        # Apply today's publication boundary even to older signed receipts.
        # A hash proves integrity, not acceptance of the underlying methodology.
        policy = publication_policy()
        if policy["status"] == "withheld":
            parts.append("模型待重建：公允价值及12个月目标暂不发布（方法未通过验收）")
            research = row.get("company_research")
            if research:
                parts.append(f"公司模型 {research['model_version']}；披露勾稽 {research['observed']['status']}；"
                             f"研究情景 {len(research['scenarios'])} 个；仅供经营、盈利与资金缺口诊断")
            if finite(base.get("price")):
                parts.append(f"参考价 {_price(base['price'])}（{base.get('price_time')}）")
            if row.get("status") == "computed":
                parts.append("原始DCF、同路径EPS/PE及曲线敏感度仅存审计收据；不是可用估值或独立市场目标")
            else:
                parts.append("本轮估值未知：" + "; ".join(row.get("missing") or ["缺少有效计算"]))
            short = row.get("short_term") or {}
            parts.append("短期研究尚未验证，详见独立收据" if short.get("status") == "research_estimate"
                         else "短期反应未知（事件样本或查询不足）")
            parts.append("零授权")
            output[ticker] = " · ".join(parts)
            continue
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


def release_fyi(events, artifacts, *, ticker, kind, as_of, current_input_hash,
                current_code_hash, enabled=False):
    """Staged release consumer; revalidates live revocations on every rendering.

    The production daily workflow still uses fyi_map and its global hold. A
    trusted rollout controller must own enablement and registry loading before
    this separate consumer can become the daily publication path.
    """
    from quant.valuation.releases import resolve
    selected = resolve(events, artifacts, ticker=ticker, kind=kind, as_of=as_of,
                       current_input_hash=current_input_hash,
                       current_code_hash=current_code_hash, enabled=enabled)
    if selected['status'] != 'released':
        return f"📐[FYI] {ticker} 估值暂不发布（{selected['reason']}） · 零授权"
    value = selected['output']
    label = '条件公允价值区间' if kind == 'fair_value' else '12个月市场预测区间'
    text = (f"📐[FYI] {ticker} {label} {_price(value['low'])}–{_price(value['high'])}；"
            f"中心值 {_price(value['point'])}；价值时点 {value['value_at']}；"
            f"依据 {value['basis']}；版本 {selected['model_version']}；"
            f"发布 {selected['release_id']}；有效至 {selected['valid_until']}")
    if kind == 'fair_value':
        text += '；条件情景范围，不代表概率覆盖'
    else:
        text += f"；名义覆盖率 {(1-value['alpha'])*100:.0f}%"
    return text + ' · 零授权'
