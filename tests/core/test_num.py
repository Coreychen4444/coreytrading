from quant.core.num import coerce_float


def test_broker_string_formats():
    assert coerce_float("1,234.5") == 1234.5
    assert coerce_float("12.3%") == 12.3
    assert coerce_float("--") is None
    assert coerce_float("") is None
    assert coerce_float(None) is None
    assert coerce_float("N/A") is None


def test_zero_is_not_missing():
    assert coerce_float("0") == 0.0
    assert coerce_float(0) == 0.0


def test_default_override():
    assert coerce_float("--", default=0.0) == 0.0


def test_nan_and_bool():
    assert coerce_float(float("nan")) is None
