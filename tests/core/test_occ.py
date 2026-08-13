from quant.core.occ import OCC_RE, contract_multiplier, is_option_ticker, parse_occ, strip_us_prefix


def test_parse_real_book_tickers():
    p = parse_occ("NOK270115C15000")
    assert p == {"underlying": "NOK", "expiry": "2027-01-15", "side": "C", "strike": 15.0, "multiplier": 100}
    p = parse_occ("DRAM261218C70000")
    assert p["underlying"] == "DRAM" and p["strike"] == 70.0 and p["expiry"] == "2026-12-18"
    p = parse_occ("INTC260612P100000")
    assert p["side"] == "P" and p["strike"] == 100.0
    p = parse_occ("AAOI270617C140000")
    assert p["underlying"] == "AAOI" and p["expiry"] == "2027-06-17"


def test_fractional_strike():
    assert parse_occ("MU260618P450500")["strike"] == 450.5


def test_stock_is_not_option():
    for t in ("MU", "AAOI", "SIVE.ST", "LITE", ""):
        assert not is_option_ticker(t)
        assert parse_occ(t) is None
    assert contract_multiplier("MU") == 1
    assert contract_multiplier("NOK270115C15000") == 100


def test_group1_is_underlying():
    # stock_fetch relies on OCC_RE.match(code).group(1) == underlying
    assert OCC_RE.match("NOK270115C15000").group(1) == "NOK"


def test_strip_us_prefix_only_prefix():
    assert strip_us_prefix("US.AAOI270617C140000") == "AAOI270617C140000"
    assert strip_us_prefix("AAOI") == "AAOI"
    assert strip_us_prefix("BUS.X") == "BUS.X"  # mid-string 'US.' untouched
