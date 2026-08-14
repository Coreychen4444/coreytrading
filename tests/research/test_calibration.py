"""Synthetic checks for the reusable calibration mechanics."""

from quant.research.calibration import ev_calibration, kelly_calibration


def test_kelly_calibration_joins_only_closed_later_episode():
    decisions = [
        {"ticker": "SYNTH", "p": 0.72, "timestamp": "a"},
        {"ticker": "SYNTH", "p": 0.74, "timestamp": "z"},
    ]
    episodes = {
        "SYNTH": [
            {"status": "closed", "realized_pnl": 1.0, "closed_at": "m"},
        ]
    }

    result = kelly_calibration(decisions, episodes)

    assert result["70-79%"] == {
        "n": 2,
        "wins": 1,
        "joined": 1,
        "actual_win_rate": 1.0,
    }


def test_ev_calibration_does_not_infer_fill_outcomes():
    result = ev_calibration([
        {"chosen": "wait", "p_fill": 0.6},
        {"chosen": "wait_target"},
        {"chosen": "buy_now"},
    ])

    assert result["decisions_total"] == 3
    assert result["chose_wait"] == 2
    assert result["chose_buy_now"] == 1
    assert result["wait_with_p_fill"] == 1
    assert "does not infer" in result["note"]
