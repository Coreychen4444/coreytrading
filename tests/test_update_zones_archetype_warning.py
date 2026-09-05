"""画卡写盘后估值层未归原型 → ⚠️ 提醒不拦截。

与 next_gate 缺失同款: 硬拦会让人随手填个原型, 比空着更坏; 空着由 brief `INFRA:card_missing_archetype` 点名。
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".claude/skills/trading-analysis/scripts/update_position_zones.py"


@pytest.fixture
def upz(monkeypatch):
    spec = importlib.util.spec_from_file_location("upz_archetype_warning_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setattr(sys, "argv", ["upz"])
    spec.loader.exec_module(mod)
    return mod


def test_warns_with_the_exact_command_and_stays_silent_when_present(upz, tmp_path, capsys):
    p = tmp_path / "inputs.json"
    p.write_text(json.dumps({"tickers": {"MU": {"archetype": "semi_cyclical", "rationale": "x"}}}))
    assert upz.warn_missing_archetype("AVGO", inputs_path=p) is True
    out = capsys.readouterr().out
    assert "AVGO 卡已写盘但估值层未归原型" in out and "archetypes.py set AVGO --archetype" in out
    assert upz.warn_missing_archetype("mu", inputs_path=p) is False and capsys.readouterr().out == ""
    assert upz.warn_missing_archetype("NVDA270319C220000", inputs_path=p) is False   # 期权卡不查
    assert upz.warn_missing_archetype("AVGO", inputs_path=tmp_path / "none.json") is True   # 文件不存在 = 缺
