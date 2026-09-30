"""Current deterministic FYI prices must replace historical mirrors, including unknowns."""
from copy import deepcopy

import pytest

from quant.report.valuation_report import fyi_map
from quant.core.agent_contracts import canonical_hash
from quant.valuation.assessment import build
from tests.valuation.test_assessment import data, STAMP


def test_computed_receipt_cannot_publish_unaccepted_valuation():
    line = fyi_map(build(data(), as_of=STAMP))["SYN"]
    for text in ("暂不发布", "参考价 $50.00", "仅存审计收据", "短期反应未知", "零授权"):
        assert text in line
    assert "基准DCF $" not in line and "12个月条件价 $" not in line
    assert STAMP in line


def test_selected_finite_lease_asset_discloses_remaining_operating_scope():
    receipt=build(data(),as_of=STAMP)
    receipt['tickers']['SYN']['company_research']={
        'model_version':'synthetic','observed':{'status':'reconciled'},
        'estimability':{'operating_value':'unestimable','reason':'other physical scope'},
        'scenarios':{'base':{'selected_ppe_runoff':{'status':'conditional_research'},
                             'business_equity':{'status':'unknown'}}}}
    receipt['receipt_hash']=canonical_hash({k:v for k,v in receipt.items() if k!='receipt_hash'})
    line=fyi_map(receipt)['SYN']
    assert '既有融资租赁资产按有限池假设摊销（自有资产、投产与新增租赁仍未闭合）' in line
    assert '暂不发布' in line and '基准DCF $' not in line


def test_finite_capacity_contract_discloses_cost_and_timing_hypotheses():
    receipt=build(data(),as_of=STAMP)
    receipt['tickers']['SYN']['company_research']={
        'model_version':'synthetic','observed':{'status':'reconciled'},
        'estimability':{'operating_value':'unestimable','reason':'other capital remains'},
        'scenarios':{'base':{'selected_contract_costs':{'status':'conditional_research','selected_amount':5800},
                            'business_equity':{'status':'unknown'}}}}
    receipt['receipt_hash']=canonical_hash({k:v for k,v in receipt.items() if k!='receipt_hash'})
    line=fyi_map(receipt)['SYN']
    assert '新增容量租赁 $5.80 十亿美元按费用及付款时点假设计入（合同细节与成本归属未核实）' in line
    assert '暂不发布' in line and '基准DCF $' not in line


@pytest.mark.parametrize("claimed_policy", [None, {"status": "approved", "fair_value_publishable": True}])
def test_old_or_self_approved_receipt_cannot_bypass_current_policy(claimed_policy):
    receipt = build(data(), as_of=STAMP)
    if claimed_policy is None:
        receipt.pop("publication")
    else:
        receipt["publication"] = claimed_policy
    base = receipt["tickers"]["SYN"]["baseline"]
    base["fair_value"] = 123456.78
    base["market_target_12m"]["price"] = 234567.89
    receipt["receipt_hash"] = canonical_hash({k: v for k, v in receipt.items() if k != "receipt_hash"})
    line = fyi_map(receipt)["SYN"]
    assert "暂不发布" in line
    assert "123,456.78" not in line and "234,567.89" not in line


def test_missing_and_tampered_receipts_cannot_display_a_prior_price():
    package = data()
    package["valuation"]["fundamentals"]["SYN"]["quarters"][-1]["lt_debt"] = None
    receipt = build(package, as_of=STAMP)
    line = fyi_map(receipt)["SYN"]
    assert "本轮估值未知" in line and "基准DCF $" not in line
    altered = deepcopy(receipt)
    altered["tickers"]["SYN"]["baseline"] = {"fair_value": 999}
    with pytest.raises(ValueError, match="receipt"):
        fyi_map(altered)


def test_translated_promised_components_disclose_fixed_fx_without_releasing_price():
    receipt = build(data(), as_of=STAMP)
    receipt['tickers']['SYN']['company_research'] = {
        'model_version': 'synthetic', 'observed': {'status': 'reconciled'},
        'scenarios': {'base': {'operating_value': {'status': 'research_sensitivity',
            'operating_value': 1000, 'date': '2027-01-01'},
            'business_equity': {'status': 'unknown', 'unresolved_assets': [],
                'unresolved_claims': ['credit_and_fx'], 'promised_payment_components': [
                    {'economic_id': 'USD'}, {'economic_id': 'EUR',
                        'translation': {'status': 'scenario_assumption'}}]}}}}
    receipt['receipt_hash'] = canonical_hash({k: v for k, v in receipt.items() if k != 'receipt_hash'})
    line = fyi_map(receipt)['SYN']
    assert '2项承诺付款' in line and '1项采用固定汇率假设（汇率与对冲仍待估）' in line
    assert '暂不发布' in line and '12个月条件价 $' not in line


def test_finite_debt_cost_diagnostic_discloses_method_gap_and_withholds_price():
    receipt = build(data(), as_of=STAMP)
    receipt['tickers']['SYN']['company_research'] = {
        'model_version':'synthetic','observed':{'status':'reconciled'},
        'scenarios':{'base':{'debt_cost_runoff':{'status':'conditional_research'},
                            'business_equity':{'status':'unknown'}}}}
    receipt['receipt_hash'] = canonical_hash({k:v for k,v in receipt.items() if k!='receipt_hash'})
    line=fyi_map(receipt)['SYN']
    assert '债务非现金成本采用有限期假设（实际利率法及完整融资范围未核实）' in line
    assert '暂不发布' in line and '12个月条件价 $' not in line


def test_staged_release_consumer_rechecks_revocation_and_expiry():
    from quant.report.valuation_report import release_fyi
    from quant.valuation.releases import append_event
    from tests.valuation.test_releases import fixture, NOW
    events, store = fixture()
    args = dict(ticker='SYN', kind='fair_value', as_of=NOW,
                current_input_hash='a'*64, current_code_hash='b'*64)
    assert '$20.00' not in release_fyi(events, store, **args)
    text = release_fyi(events, store, enabled=True, **args)
    assert '$20.00' in text and '不代表概率覆盖' in text and '零授权' in text
    events = append_event(events, {'event_id': 'revoke', 'action': 'revoke', 'ticker': 'SYN',
                          'kind': 'fair_value', 'at': NOW, 'reason': 'Synthetic withdrawal',
                          'release_id': 'one'}, expected_head=events[-1]['event_hash'])
    text = release_fyi(events, store, enabled=True, **args)
    assert '$20.00' not in text and 'current_release_revoked' in text


def test_company_input_expiry_does_not_hide_valid_current_quote_or_restore_a_target():
    from tests.valuation.test_company_model import packet, STAMP as COMPANY_STAMP
    package = packet()
    package['valid_until'] = COMPANY_STAMP
    observed = {'price': 45, 'as_of': COMPANY_STAMP, 'captured_at': COMPANY_STAMP, 'source': 'synthetic daily close'}
    value = {'selected_tickers': ['SYN'], 'valuation': {'company_models': {'SYN': package}, 'quotes': {'SYN': observed}}}
    receipt = build(value, as_of=COMPANY_STAMP)
    line = fyi_map(receipt)['SYN']
    assert receipt['tickers']['SYN']['baseline'] is None
    assert '参考价 $45.00' in line and 'company input expired' in line and '暂不发布' in line
    value['valuation']['quotes']['SYN']['captured_at'] = '2027-01-01T00:00:00Z'
    assert '参考价 $45.00' not in fyi_map(build(value, as_of=COMPANY_STAMP))['SYN']


@pytest.mark.parametrize('balance, gap, expected', [
    (-80000, 110000, '$80.00'), (10000, 20000, '$0.00')])
@pytest.mark.parametrize('has_budget', [True, False])
def test_partial_capital_diagnostic_distinguishes_cash_deficit_from_buffer(balance, gap, expected, has_budget):
    receipt = build(data(), as_of=STAMP)
    receipt['tickers']['SYN']['company_research'] = {
        'model_version': 'synthetic', 'observed': {'status': 'reconciled'},
        'estimability': {'operating_value': 'unestimable', 'reason': 'partial capital scope'},
        'scenarios': {'base': {
            'cash_capital_budget': {'status': 'conditional_research',
                'scope': 'cash_only_partial_capital_population',
                'annual_budgets': [{'start': '2026-01-01', 'end': '2026-12-31',
                                    'annual_cash_budget': 200000}]},
            'maximum_funding_gap': gap,
            'quarters': [{'cash': {'ending_liquidity': balance}}]}}}
    if not has_budget:
        receipt['tickers']['SYN']['company_research']['scenarios']['base'].pop('cash_capital_budget')
    receipt['receipt_hash'] = canonical_hash({k: v for k, v in receipt.items() if k != 'receipt_hash'})
    line = fyi_map(receipt)['SYN']
    assert ('$200.00 十亿美元' in line) is has_budget
    assert '现金跌破零的最大差额 ' + expected in line
    assert line.count('基准距设定现金缓冲线的最大缺口') == 1
    assert '不代表已确认融资需求' in line
    assert '基准DCF $' not in line and '12个月条件价 $' not in line


def test_ordinary_share_cash_proxy_discloses_unproven_payment_and_claim_scope():
    from tests.valuation.test_company_share_settlements import linked_packet, STAMP as company_stamp
    p = linked_packet()
    receipt = build({'selected_tickers': ['SYN'], 'valuation': {'company_models': {'SYN': p}}}, as_of=company_stamp)
    line = fyi_map(receipt)['SYN']
    assert '普通股增发与回购已分列核对' in line
    assert '回购账面等于付款及融资残差均为假设，员工权益仍待估' in line
    assert '暂不发布' in line


def test_content_capital_discloses_book_value_and_future_cost_limits():
    from tests.valuation.test_company_content_capital import linked_packet, STAMP as company_stamp
    p = linked_packet()
    receipt = build({'selected_tickers': ['SYN'], 'valuation': {'company_models': {'SYN': p}}}, as_of=company_stamp)
    line = fyi_map(receipt)['SYN']
    assert '内容资产、应付款与现金已联动' in line
    assert '账面余额不是内容权利价值，未来摊销与付款仍为假设' in line
    assert '暂不发布' in line


def test_selected_tax_cash_equivalence_is_not_reported_as_verified_payment():
    from tests.valuation.test_company_model import tax_equivalence_packet, STAMP as company_stamp
    p = tax_equivalence_packet()
    receipt = build({'selected_tickers': ['SYN'], 'valuation': {
        'company_models': {'SYN': p}}}, as_of=company_stamp)
    line = fyi_map(receipt)['SYN']
    assert '选定季度现金税等于模型税费用仅为情景假设' in line
    assert '实际缴税、递延税与亏损抵扣未核实' in line
    assert '暂不发布' in line


def test_dated_bridge_surplus_does_not_hide_later_negative_native_cash():
    receipt = build(data(), as_of=STAMP)
    receipt['tickers']['SYN']['company_research'] = {
        'model_version': 'synthetic', 'observed': {'status': 'reconciled'},
        'estimability': {'operating_value': 'unestimable', 'reason': 'unaccepted cash model'},
        'scenarios': {'base': {
            'assumptions': {'native_operating_cash_scope': {'scope': 'selected_physical_PPE_DA_addback_only'}},
            'business_equity': {'forecast_liquidity_surplus': [{
                'status': 'research_sensitivity', 'date': '2026-09-30',
                'value': 98075, 'ending_liquidity': 118075, 'operating_reserve': 20000}]},
            'quarters': [{'cash': {'ending_liquidity': 118075}},
                         {'cash': {'ending_liquidity': -62177}}],
            'maximum_funding_gap': 82177}}}
    receipt['receipt_hash'] = canonical_hash({k: v for k, v in receipt.items() if k != 'receipt_hash'})
    line = fyi_map(receipt)['SYN']
    assert '2026-09-30；预测余额 $118.08' in line
    assert '最大缺口 $82.18' in line and '现金跌破零的最大差额 $62.18' in line
    assert '不代表已确认融资需求' in line and '暂不发布' in line
    assert '现金支出等于费用（付款时点及范围未核实）' in line


def test_missing_native_balance_does_not_become_zero_cash_deficit():
    receipt = build(data(), as_of=STAMP)
    receipt['tickers']['SYN']['company_research'] = {
        'model_version': 'synthetic', 'observed': {'status': 'reconciled'},
        'estimability': {'operating_value': 'unestimable'},
        'scenarios': {'base': {'maximum_funding_gap': 10000,
                              'quarters': [{'cash': {'ending_liquidity': None}}]}}}
    receipt['receipt_hash'] = canonical_hash({k: v for k, v in receipt.items() if k != 'receipt_hash'})
    line = fyi_map(receipt)['SYN']
    assert '最大缺口 $10.00' in line
    assert '现金跌破零的最大差额' not in line


def test_native_principal_schedule_is_cash_diagnostic_and_never_a_price():
    from tests.valuation.test_company_model import principal_packet,STAMP as COMPANY_STAMP
    p=principal_packet()
    receipt=build({'selected_tickers':['SYN'],'valuation':{'company_models':{'SYN':p}}},as_of=COMPANY_STAMP)
    line=fyi_map(receipt)['SYN']
    assert '选定到期本金 $0.01 十亿美元按契约季度计入现金（完整融资路径未核实）' in line
    assert '暂不发布' in line and receipt['publication']['status']=='withheld'
    assert '基准DCF $' not in line and '12个月条件价 $' not in line


def test_existing_financing_cash_discloses_unverified_book_and_timing():
    receipt = build(data(), as_of=STAMP)
    receipt['tickers']['SYN']['company_research'] = {
        'model_version': 'synthetic', 'observed': {'status': 'reconciled'},
        'estimability': {'operating_value': 'unestimable', 'reason': 'full capital unknown'},
        'scenarios': {'base': {'native_financing_obligation_cash': {
            'status': 'conditional_research', 'cash_paid': 16, 'book_rollforward_verified': False},
            'business_equity': {'status': 'unknown'}}}}
    receipt['receipt_hash'] = canonical_hash({k: v for k, v in receipt.items() if k != 'receipt_hash'})
    line = fyi_map(receipt)['SYN']
    assert '既有融资义务付款已单列，不计新增资本' in line
    assert '付款时点、本金与利息及完整融资余额仍待核实' in line
    assert '暂不发布' in line and '基准DCF $' not in line
