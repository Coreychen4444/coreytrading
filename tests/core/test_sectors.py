"""sectors 冻结映射回归 — 改表 = 改 live 量腿判定, 先过 relative_volume 复测。"""
from quant.core import sectors


def test_semi_table_frozen_2026_08_06():
    for t in ("AAOI", "LITE", "AXTI", "INTC", "MU", "NVDA", "SNDK", "TSM"):
        assert t in sectors.SEMI_SMH, t
    for t in ("NBIS", "CRWV", "WDC", "NOK", "AMZN", "MSFT"):
        assert t not in sectors.SEMI_SMH, t
    assert len(sectors.SEMI_SMH) == 24


def test_benchmark_for_maps_and_defaults():
    assert sectors.benchmark_for("INTC") == "SMH"
    assert sectors.benchmark_for("intc") == "SMH"      # 大小写不敏感
    assert sectors.benchmark_for("AMZN") == "QQQ"
    assert sectors.benchmark_for(None) == "QQQ"        # 未知 → 最宽的 tape
