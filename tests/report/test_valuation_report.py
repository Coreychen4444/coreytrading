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
