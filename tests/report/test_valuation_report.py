"""Current deterministic FYI prices must replace historical mirrors, including unknowns."""
from copy import deepcopy

import pytest

from quant.report.valuation_report import fyi_map
from quant.valuation.assessment import build
from tests.valuation.test_assessment import data, STAMP


def test_fyi_carries_price_times_distinct_targets_and_conditional_sensitivities():
    line = fyi_map(build(data(), as_of=STAMP))["SYN"]
    for text in ("基准DCF", "参考价 $50.00", "12个月条件价", "EPS", "PE", "-25", "短期反应未知", "零授权"):
        assert text in line
    assert STAMP in line


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
