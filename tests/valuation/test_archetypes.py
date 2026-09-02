"""archetypes.py: 原型表完整 / inputs.json 校验拒绝错键 / resolve 带出处。"""
import json

import pytest

from quant.core.paths import VALUATION_DIR, VALUATION_INPUTS_PATH
from quant.valuation import archetypes as A

EXAMPLE_INPUTS_PATH = VALUATION_DIR / "inputs.example.json"

REQUIRED = {"label", "horizon_years", "terminal_growth", "growth_source", "growth_cap", "growth_floor", "fade",
            "half_life", "margin_method", "margin_fade_years", "target_margin", "ronic_cap", "s2c_default",
            "s2c_floor", "engine_weights", "scenario", "base_confidence"}


def test_every_archetype_declares_every_parameter():
    for name, a in A.ARCHETYPES.items():
        assert REQUIRED <= set(a), f"{name} 缺 {REQUIRED - set(a)}"
        if name != "not_applicable":
            assert set(a["engine_weights"]) == {"fcf_dcf", "street"} and a["engine_weights"]["fcf_dcf"] + a["engine_weights"]["street"] > 0
            assert a["terminal_growth"] < 0.05 and a["horizon_years"] >= 5
            assert a["margin_method"] in ("current", "through_cycle", "target")
            if a["margin_method"] == "target":
                assert a["target_margin"] is not None


def test_manual_region_validates_and_every_ticker_has_rationale():
    """人工区每条都要过校验并写明归类理由。

    私有工作区存在时钉的是真文件; 公开仓里没有 runtime 文件, 钉的是同 schema 的
    合成样例 —— 两种情况下这条不变量都在跑, 不需要跳过。
    """
    path = VALUATION_INPUTS_PATH if VALUATION_INPUTS_PATH.exists() else EXAMPLE_INPUTS_PATH
    doc = A.load_inputs(path)
    assert doc["tickers"], f"人工区不该是空的: {path}"
    for t, e in doc["tickers"].items():
        assert e["archetype"] in A.ARCHETYPES and e["rationale"].strip()


@pytest.mark.parametrize("bad, msg", [
    ({"X": {"archetype": "nope", "rationale": "r"}}, "archetype"),
    ({"X": {"archetype": "platform"}}, "rationale"),
    ({"X": {"archetype": "platform", "rationale": "r", "overrides": {"grwoth_start": 0.1}}}, "overrides"),
    ({"X": {"archetype": "platform", "rationale": "r", "overrides": {"probabilities": {"bear": 0.5, "base": 0.5, "bull": 0.5}}}}, "probabilities"),
    ({"X": {"archetype": "platform", "rationale": "r", "overrides": {"engine_weights": {"fcf_dcf": 0.5, "eps_pe": 0.5}}}}, "engine_weights"),
    ({"X": {"archetype": "platform", "rationale": "r", "news_overlay": [{"date": "2026-09-01", "source": "s", "driver": "eps", "delta": 0.1, "note": "n"}]}}, "driver"),
    ({"X": {"archetype": "platform", "rationale": "r", "news_overlay": [{"date": "2026-09-01", "driver": "wacc", "delta": 0.01}]}}, "news_overlay"),
    ({"x": {"archetype": "platform", "rationale": "r"}}, "大写"),
    ({"X": {"archetype": "platform", "rationale": "r", "extra": 1}}, "不认识的键"),
])
def test_validate_rejects_bad_entries(bad, msg):
    with pytest.raises(ValueError, match=msg):
        A.validate_inputs({"tickers": bad})


def test_load_inputs_missing_file_is_empty(tmp_path):
    assert A.load_inputs(tmp_path / "none.json") == {"tickers": {}}


def test_resolve_merges_defaults_overrides_overlay_with_provenance(tmp_path):
    doc = {"tickers": {"T": {"archetype": "semi_cyclical", "rationale": "周期", "overrides": {"target_margin": 0.2},
                             "news_overlay": [{"date": "2026-09-01", "source": "ai_news/reports/x.md", "driver": "growth_start",
                                               "delta": 0.05, "note": "指引上调"}]}}}
    (tmp_path / "inputs.json").write_text(json.dumps(doc))
    r = A.resolve("T", A.load_inputs(tmp_path / "inputs.json"))
    assert r["archetype"] == "semi_cyclical" and r["label"] and "label" not in r["params"]
    assert r["params"]["horizon_years"] == 10 and r["overrides"] == {"target_margin": 0.2}
    assert len(r["news_overlay"]) == 1
    assert any("override target_margin=0.2" in p for p in r["provenance"])
    assert any("overlay 2026-09-01 growth_start +0.05" in p for p in r["provenance"])
    assert A.ARCHETYPES["semi_cyclical"]["horizon_years"] == 10, "resolve 不许改动原型表本身"
    with pytest.raises(KeyError):
        A.resolve("ZZZ", doc)
