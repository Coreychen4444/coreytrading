"""事件剧本形状守卫：分支互斥穷尽、第二根不是镜像、线位带出处、编译结果过发布 schema。"""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from quant.core import macro_playbook as mp
from quant.ops import macro_state

EXAMPLE = Path("trade/intelligence/examples/event-playbook.example.json")
STATE_EXAMPLE = Path("trade/intelligence/examples/macro-state.example.json")


def raw():
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def build(mutate=None):
    data = raw()
    if mutate:
        mutate(data)
    return mp.from_dict(data)


def errors(mutate=None):
    return mp.validate(build(mutate))


def test_example_is_valid_and_roundtrips():
    pb = build()
    assert mp.validate(pb) == []
    assert mp.from_dict(mp.to_dict(pb)) == pb


def test_unknown_or_missing_fields_are_rejected_not_defaulted():
    with pytest.raises(ValueError, match="unknown fields"):
        build(lambda d: d.__setitem__("hunch", "x"))
    with pytest.raises(ValueError, match="verdict: required"):
        build(lambda d: d.pop("verdict"))


def test_verdict_must_fit_one_line():
    errs = errors(lambda d: d.__setitem__("verdict", "长" * (mp.VERDICT_MAX_CHARS + 1)))
    assert any("verdict" in e and "一句话" in e for e in errs)


def test_preferred_and_predicted_paths_must_be_distinct_statements():
    errs = errors(lambda d: d.__setitem__("preferred_path", d["predicted_path"]))
    assert any("preferred_path" in e for e in errs)


@pytest.mark.parametrize("edit,kind", [
    (lambda s: s[1].__setitem__("lower", 0.25), "gap"),
    (lambda s: s[1].__setitem__("lower", 0.15), "overlap"),
])
def test_numeric_branches_must_tile_the_line(edit, kind):
    errs = errors(lambda d: edit(d["scenarios"]))
    assert any(kind in e for e in errs)


def test_first_and_last_branches_must_be_open():
    errs = errors(lambda d: d["scenarios"][0].__setitem__("lower", 0.0))
    assert any("open (-inf)" in e for e in errs)
    errs = errors(lambda d: d["scenarios"][-1].__setitem__("upper", 1.0))
    assert any("open (+inf)" in e for e in errs)


def test_branch_for_uses_inclusive_lower_exclusive_upper():
    pb = build()
    assert mp.branch_for(pb, 0.2) == "COOL"
    assert mp.branch_for(pb, 0.2001) == "MIDDLE"
    assert mp.branch_for(pb, 0.2999) == "MIDDLE"
    assert mp.branch_for(pb, 0.3) == "HOT"
    assert mp.branch_for(pb, -0.4) == "COOL"
    assert mp.branch_for(pb, None) is None


def test_second_leg_may_not_mirror_first_leg():
    def mirror(d):
        s = d["scenarios"][0]
        s["second_leg"]["expected"] = s["first_leg"]["expected"]
    assert any("镜像" in e or "mirrors" in e for e in errors(mirror))

    def same_window(d):
        s = d["scenarios"][0]
        s["second_leg"]["window"] = s["first_leg"]["window"]
    assert any("share a window" in e for e in errors(same_window))


def test_single_policy_path_is_a_prediction_not_a_comparison():
    errs = errors(lambda d: d["scenarios"][0].__setitem__("policy_paths", ["hold"]))
    assert any("single path" in e for e in errs)
    errs = errors(lambda d: d["scenarios"][0].__setitem__("policy_paths", ["hike_forever", "hold"]))
    assert any("hike_forever" in e for e in errs)


def test_flip_must_point_at_another_branch_or_residual():
    assert any("flip_to" in e for e in errors(lambda d: d["scenarios"][0].__setitem__("flip_to", "NOPE")))
    assert any("itself" in e for e in errors(lambda d: d["scenarios"][0].__setitem__("flip_to", "COOL")))
    assert errors(lambda d: d["scenarios"][0].__setitem__("flip_to", "residual")) == []


def test_numbers_in_prose_need_a_sourced_level():
    def bare_number(d):
        d["scenarios"][2]["second_leg"]["accepted_if"] = "10Y 守住 4.95% 下方"
    errs = errors(bare_number)
    assert any("4.95%" in e and "no Level/Baseline" in e for e in errs)
    # 加上带出处与复核日的线位后通过
    def with_level(d):
        bare_number(d)
        d["shared_levels"].append({"instrument": "UST10Y", "value": 4.95, "unit": "%", "meaning": "示例",
                                   "source": "示例", "as_of": "2099-01-13", "review_by": "2099-01-20"})
    assert errors(with_level) == []
    # 分支边界与冻结基准里的数字不需要另立线位
    assert errors(lambda d: d["scenarios"][1].__setitem__("flip_if", "核心到 0.3% 或总项 0.4% 就滑")) == []


def test_level_needs_source_and_review_after_as_of():
    def bad(d):
        d["shared_levels"][0]["review_by"] = "2099-01-01"
        d["shared_levels"][1]["source"] = ""
    errs = errors(bad)
    assert any("review_by: earlier than as_of" in e for e in errs)
    assert any("shared_levels[1].source" in e for e in errs)


def test_probabilities_all_or_none_and_bracket_100():
    errs = errors(lambda d: d["scenarios"][0].__setitem__("probability", [30, 40]))
    assert any("all ranges or all null" in e for e in errs)

    def all_ranges(d):
        for s in d["scenarios"]:
            s["probability"] = [10, 20]
            s["probability_basis"] = "研究者判断，未校准"
    assert any("bracket 100" in e for e in errors(all_ranges))

    def null_without_basis(d):
        d["scenarios"][0]["probability_basis"] = ""
    assert any("probability_basis" in e for e in errors(null_without_basis))


def test_joint_condition_needs_two_parts_and_known_rule():
    errs = errors(lambda d: d["joint_conditions"][0].__setitem__("parts", ["只有一件事"]))
    assert any(">= 2 parts" in e for e in errs)
    errs = errors(lambda d: d["joint_conditions"][0].__setitem__("rule", "most"))
    assert any("rule" in e for e in errs)


def test_scenario_metric_must_be_a_frozen_baseline():
    errs = errors(lambda d: [s.__setitem__("metric", "vibes") for s in d["scenarios"]])
    assert any("not a frozen baseline" in e for e in errs)


def test_compiled_scenarios_pass_the_publication_schema():
    from quant.core.paths import MACRO_SCHEMA_PATH
    if not MACRO_SCHEMA_PATH.exists():
        pytest.skip("optional intelligence workspace schema is not part of this repository")
    pb = build()
    doc = json.loads(STATE_EXAMPLE.read_text(encoding="utf-8"))
    doc["next_event"]["scenarios"] = mp.to_schema_scenarios(pb)
    assert macro_state.validate_document(doc) == []
    compiled = {s["id"]: s for s in doc["next_event"]["scenarios"]}
    assert compiled["MIDDLE"]["confirmation_conditions"][0]["description"] == pb.scenarios[1].second_leg.accepted_if
    assert compiled["MIDDLE"]["invalidation_conditions"][0]["description"] == pb.scenarios[1].second_leg.rejected_if
    assert "4.83%" in compiled["MIDDLE"]["second_order"][0]["rationale"]
    assert compiled["HOT"]["probability_range"] is None
