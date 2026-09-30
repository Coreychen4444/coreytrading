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


def coverage_summary(receipt: dict) -> dict:
    """Count each company's research lane without promoting diagnostic prices."""
    rows = (receipt.get("tickers") or {}).values()
    rows = list(rows)
    operating = sum(r.get("estimability", {}).get("operating_value") == "conditional_research" for r in rows)
    path_unestimable = sum(r.get("estimability", {}).get("operating_value") == "unestimable" for r in rows)
    expired = sum(r.get("estimability", {}).get("operating_value") == "input_expired" for r in rows)
    return {"total": len(rows), "publishable": 0,
            "company_research": sum(r.get("status") == "company_research" for r in rows),
            "computed": sum(r.get("status") == "computed" for r in rows),
            "conditional_operating": operating, "operating_path_unestimable": path_unestimable,
            "expired_inputs": expired, "unestimable": path_unestimable + expired}


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
            parts.append("本计算收据的每股公允价值及12个月目标暂不发布（模型诊断，尚未逐公司验收）")
            research = row.get("company_research")
            if research:
                parts.append(f"公司模型 {research['model_version']}；披露勾稽 {research['observed']['status']}；"
                             f"研究情景 {len(research['scenarios'])} 个")
                estimability = research.get("estimability") or {}
                operating = (research.get("scenarios") or {}).get("base", {}).get("operating_value") or {}
                if (estimability.get("operating_value") == "conditional_research"
                        and operating.get("status") == "research_sensitivity"
                        and finite(operating.get("operating_value"))):
                    parts.append(f"基准条件经营价值 ${operating['operating_value']/1000:,.2f} 十亿美元"
                                 f"（价值日 {operating['date']}；不是普通股每股价值；预测未验收）")
                elif estimability.get("operating_value") == "unestimable":
                    parts.append("长期经营价值不可估：" + str(estimability.get("reason") or "缺少有效路径"))
                else:
                    parts.append("长期经营价值未知：" + str(estimability.get("reason") or "缺少有效路径"))
                equity = (research.get('scenarios') or {}).get('base', {}).get('business_equity') or {}
                gaps = []
                for key, label in (('unresolved_assets', '资产'), ('unresolved_claims', '索偿')):
                    pending = equity.get(key) or []
                    if pending:
                        gaps.append(f'{len(pending)}项{label}')
                if gaps:
                    parts.append('普通股权益桥未闭合：' + '、'.join(gaps) + '待估（明细见估值收据）')
                promised = equity.get('promised_payment_components') or []
                if promised:
                    parts.append(f'{len(promised)}项承诺付款已按情景利率折现（信用及赎回权仍待估）')
                    translated = sum(bool(c.get('translation')) for c in promised)
                    if translated:
                        parts.append(f'{translated}项采用固定汇率假设（汇率与对冲仍待估）')
                for cash_link in equity.get('forecast_liquidity_surplus') or []:
                    if (cash_link.get('status') == 'research_sensitivity'
                            and all(finite(cash_link.get(k)) for k in ('value', 'ending_liquidity', 'operating_reserve'))):
                        cash_date = f"{cash_link['date']}；" if cash_link.get('date') else ''
                        parts.append(f"基准流动资金研究余量 ${cash_link['value']/1000:,.2f} 十亿美元"
                                     f"（{cash_date}预测余额 ${cash_link['ending_liquidity']/1000:,.2f} 十亿美元"
                                     f"减营运储备假设 ${cash_link['operating_reserve']/1000:,.2f} 十亿美元；"
                                     "资金归属与储备充分性未验收）")
                scenario = (research.get('scenarios') or {}).get('base') or {}
                cash_tax = scenario.get('native_cash_tax_basis') or {}
                obligations = scenario.get('native_financing_obligation_cash') or {}
                if obligations.get('status') == 'conditional_research':
                    parts.append('既有融资义务付款已单列，不计新增资本；付款时点、本金与利息及完整融资余额仍待核实')
                if (cash_tax.get('scope') == 'modeled_provision_cash_equivalence'
                        and cash_tax.get('status') == 'conditional_research'):
                    parts.append('选定季度现金税等于模型税费用仅为情景假设；实际缴税、递延税与亏损抵扣未核实')
                content = scenario.get('content_capital') or {}
                if content.get('status') == 'conditional_research':
                    parts.append('内容资产、应付款与现金已联动；账面余额不是内容权利价值，未来摊销与付款仍为假设')
                settlements = scenario.get('ordinary_share_cash_settlements') or {}
                if settlements.get('status') == 'conditional_research':
                    parts.append('普通股增发与回购已分列核对；回购账面等于付款及融资残差均为假设，员工权益仍待估')
                cash_scope = (scenario.get('assumptions') or {}).get('native_operating_cash_scope') or {}
                if cash_scope.get('scope') == 'selected_physical_PPE_DA_addback_only':
                    parts.append('季度现金模型假设非物理成本的现金支出等于费用（付款时点及范围未核实）')
                principal = scenario.get('note_principal_cash') or {}
                if (principal.get('status') == 'conditional_research'
                        and finite(principal.get('selected_principal_paid_total'))):
                    parts.append(f"选定到期本金 ${principal['selected_principal_paid_total']/1000:,.2f} 十亿美元按契约季度计入现金（完整融资路径未核实）")
                contracts = scenario.get('selected_contract_costs') or {}
                if contracts.get('status') == 'conditional_research' and finite(contracts.get('selected_amount')):
                    parts.append(f"新增容量租赁 ${contracts['selected_amount']/1000:,.2f} 十亿美元按费用及付款时点假设计入（合同细节与成本归属未核实）")
                ppe = scenario.get('selected_ppe_runoff') or {}
                if ppe.get('status') == 'conditional_research':
                    parts.append('既有融资租赁资产按有限池假设摊销（自有资产、投产与新增租赁仍未闭合）')
                debt_costs = scenario.get('debt_cost_runoff') or {}
                if debt_costs.get('status') == 'conditional_research':
                    parts.append('债务非现金成本采用有限期假设（实际利率法及完整融资范围未核实）')
                runoff = scenario.get('acquired_intangible_runoff') or {}
                if (runoff.get('status') == 'conditional_research'
                        and (runoff.get('operating_capital_scope') or {}).get('basis_type') == 'deferred_contract_acquisition_costs'):
                    parts.append('既有收购资产采用有限摊销；佣金付款与摊销分别计入一次（新收购资产与资本范围未验收）')
                budget = scenario.get('cash_capital_budget') or {}
                if (budget.get('status') == 'conditional_research'
                        and budget.get('scope') == 'cash_only_partial_capital_population'):
                    annual = budget.get('annual_budgets') or []
                    if annual and finite(annual[0].get('annual_cash_budget')):
                        parts.append(f"基准现金采购研究预算 ${annual[0]['annual_cash_budget']/1000:,.2f} 十亿美元"
                                     f"（{annual[0]['start']}–{annual[0]['end']}；采购时点及后续增长为情景假设，资本范围未验收）")
                cash_rows = [(q.get('cash') or {}) for q in scenario.get('quarters') or []]
                balances = [r['ending_liquidity'] for r in cash_rows if finite(r.get('ending_liquidity'))]
                gap = scenario.get('maximum_funding_gap')
                if finite(gap) and gap > 0:
                    deficit = max(0.0, -min(balances)) if balances else None
                    label = (f"；现金跌破零的最大差额 ${deficit/1000:,.2f} 十亿美元"
                             if deficit is not None else '')
                    parts.append(f"基准距设定现金缓冲线的最大缺口 ${gap/1000:,.2f} 十亿美元{label}"
                                 "（预测期间未验证情景；不代表已确认融资需求）")
            quote = row.get('reference_quote') or {}
            if quote.get('status') == 'observed' and finite(quote.get('price')):
                parts.append(f"参考价 {_price(quote['price'])}（{quote.get('as_of')}）")
            elif finite(base.get("price")):
                parts.append(f"参考价 {_price(base['price'])}（{base.get('price_time')}）")
            if row.get("status") == "computed":
                parts.append("原始DCF、同路径EPS/PE及曲线敏感度仅存审计收据；不是可用估值或独立市场目标")
            elif not research:
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
    """Pure release rendering for frozen contexts and control tests.

    Production uses valuation_release.production_release to load host policy,
    current implementation/input hashes and verified review identities first.
    """
    from quant.valuation.releases import resolve
    selected = resolve(events, artifacts, ticker=ticker, kind=kind, as_of=as_of,
                       current_input_hash=current_input_hash,
                       current_code_hash=current_code_hash, enabled=enabled)
    return released_fyi(selected)


def released_fyi(selected):
    """Render the host-selected publication separately from diagnostic receipts."""
    ticker, kind = selected['ticker'], selected['kind']
    if selected['status'] != 'released':
        return f"📐[FYI] {ticker} 估值暂不发布（{selected['reason']}） · 零授权"
    value = selected['output']
    label = '条件公允价值区间' if kind == 'fair_value' else '12个月市场预测区间'
    text = (f"📐[FYI] {ticker} {label} {_price(value['low'])}–{_price(value['high'])}；"
            f"中心值 {_price(value['point'])}；价值时点 {value['value_at']}；"
            f"普通股每股口径 {value['share_basis']}；"
            f"依据 {value['basis']}；版本 {selected['model_version']}；"
            f"发布 {selected['release_id']}；有效至 {selected['valid_until']}")
    if kind == 'fair_value':
        text += '；条件情景范围，不代表概率覆盖'
    else:
        text += f"；名义覆盖率 {(1-value['alpha'])*100:.0f}%"
    return text + ' · 零授权'
