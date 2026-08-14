"""The public study example must stay deterministic and synthetic."""

from quant.research.study_example import PairedObservation, decide, summarize


def test_summary_handles_empty_input_explicitly():
    assert summarize([]) == {
        "n": 0,
        "mean_delta": None,
        "median_delta": None,
        "positive_rate": None,
    }


def test_decision_thresholds_come_from_the_locked_protocol():
    rows = [
        PairedObservation("synthetic-a", 1.0, 1.2),
        PairedObservation("synthetic-b", 1.0, 0.9),
    ]
    result = summarize(rows)

    assert decide(result, minimum_sample=3, minimum_mean_delta=0.0) == "inconclusive"
    assert decide(result, minimum_sample=2, minimum_mean_delta=0.01) == "support"
    assert decide(result, minimum_sample=2, minimum_mean_delta=0.10) == "no_support"
