"""Synthetic checks for research modules that accept ignored private inputs."""

from __future__ import annotations

import json

import pytest

from quant.research import entry_caps_backtest as entry_caps
from quant.research import exit_rules_backtest as exit_rules
from quant.research import longcall_historical_replay as longcall
from quant.research import options_exit_overlay_backtest as option_overlay
from quant.research import pull_breadth_history as pull_breadth
from quant.research import pull_gate_history as pull_gate
from quant.research import zone_hit_analysis as zone_hit


def _write_json(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_universe_manifests_are_explicit_deduplicated_inputs(tmp_path):
    manifest = _write_json(tmp_path / "universe.json", ["synth_a", "SYNTH_A", "synth_b"])
    assert pull_gate.load_universe(manifest) == ["SYNTH_A", "SYNTH_B"]
    assert pull_breadth.load_universe(manifest) == ["SYNTH_A", "SYNTH_B"]


@pytest.mark.parametrize("loader", [pull_gate.load_universe, pull_breadth.load_universe])
def test_universe_manifest_rejects_empty_or_malformed_values(tmp_path, loader):
    empty = _write_json(tmp_path / "empty.json", [])
    malformed = _write_json(tmp_path / "malformed.json", ["SYNTH_A", 7])
    with pytest.raises(ValueError):
        loader(empty)
    with pytest.raises(ValueError):
        loader(malformed)


def test_longcall_campaigns_come_only_from_manifest(tmp_path):
    manifest = _write_json(tmp_path / "campaigns.json", [{
        "ticker": "SYNTH_A",
        "entry_date": "2030-01-02",
        "option_code": "SYNTH300101C00100000",
    }])
    assert longcall.load_campaigns(manifest) == [
        ("SYNTH_A", "2030-01-02", "SYNTH300101C00100000")
    ]
    assert longcall._strike("SYNTH300101C00100000") == 100.0


def test_option_overlay_exclusions_come_only_from_private_config(tmp_path):
    path = _write_json(tmp_path / "config.json", {"exclude_symbols": ["SYNTH_OPTION"]})
    assert option_overlay.load_private_config(path) == {
        "exclude_symbols": {"SYNTH_OPTION"}
    }


def test_exit_rules_private_exceptions_have_neutral_defaults(tmp_path):
    assert exit_rules.load_private_config(tmp_path / "missing.json") == {
        "excluded_instruments": [],
        "bad_klines": [],
        "start_date": None,
    }
    path = _write_json(tmp_path / "config.json", {
        "excluded_instruments": ["SYNTH_A"],
        "bad_klines": ["SYNTH_B"],
        "start_date": "2030-01-02",
    })
    assert exit_rules.load_private_config(path)["bad_klines"] == ["SYNTH_B"]


def test_entry_cap_replay_uses_supplied_nav_without_reading_account_state():
    sample = [{
        "ticker": "SYNTH_A",
        "classification": "confirmed",
        "open": "2030-01-02",
        "close": "2030-01-03",
        "return": 0.10,
    }]
    result = entry_caps.replay(sample, {"confirmed": 0.25}, nav0=1_000.0)
    assert result["end"] == pytest.approx(1_025.0)
    assert result["peak_concentration"] == pytest.approx(0.25)


def test_zone_bucket_classification_is_pure_and_synthetic():
    zones = {
        "accumulation_zone_1": [90.0, 95.0],
        "accumulation_zone_2": [80.0, 85.0],
        "trim_zone": [110.0, 115.0],
        "no_chase_above": 120.0,
    }
    assert zone_hit.classify(92.0, zones) == "zone1"
    assert zone_hit.classify(82.0, zones) == "zone2"
    assert zone_hit.classify(121.0, zones) == "above_no_chase"
