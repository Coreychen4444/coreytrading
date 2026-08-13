"""画卡时的「形态 ↔ tape」交叉验证 (⚠️ 提醒不拦截, META  源案例)。

`derive_card_regime` 的判据是 reclaim_gates 字段有无 —— 手工声明, 不看 tape。
8/11 META 卡 basis 明写「LH+LL(下降)」却没写 reclaim_gates → 派生常规参与 40%。
旧世界运行时降级 (probe 2%) 盖住分叉 (两个 bug 互相抵消); 分类轴退役后天花板
变实额, 卡被运行时 VETO 天天拦。守卫在**画卡当场**把分叉说出来。
"""
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".claude/skills/trading-analysis/scripts/update_position_zones.py"

DOWN_ST = {"trend": "down", "high_label": "LH", "low_label": "LL",
           "last_swing_high": 601.0}
UP_ST = {"trend": "up", "high_label": "HH", "low_label": "HL",
         "last_swing_high": 590.0}


@pytest.fixture
def upz(monkeypatch):
    spec = importlib.util.spec_from_file_location("upz_form_vs_tape_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setattr(sys, "argv", ["upz"])
    spec.loader.exec_module(mod)
    return mod


def _wire(upz, monkeypatch, st):
    monkeypatch.setattr(upz.book, "bars",
                        lambda t, kdir=None: [{"close": 599.12}] * 30)
    from quant.core import technical
    monkeypatch.setattr(technical, "swing_structure", lambda rows, **k: st)


def test_normal_form_on_down_tape_warns(upz, monkeypatch, capsys):
    """META 形态: derive 出常规参与而 tape 是 down 未修复 → 必须当场说出来。"""
    _wire(upz, monkeypatch, DOWN_ST)
    agent = {"zones": {"accumulation_zone_1": [578.0, 588.0]}}
    upz._warn_form_vs_tape("META", agent, "derived_normal")
    out = capsys.readouterr().out
    assert "常规参与" in out and "--reclaim-gates" in out and "down 未修复" in out


def test_clean_tape_stays_quiet(upz, monkeypatch, capsys):
    _wire(upz, monkeypatch, UP_ST)
    upz._warn_form_vs_tape("MSFT", {"zones": {}}, "derived_normal")
    assert capsys.readouterr().out == ""


def test_repair_form_never_warns_even_on_down_tape(upz, monkeypatch, capsys):
    """已是修复观察形态 (derive_act != derived_normal) = 形态与 tape 一致, 静默。"""
    _wire(upz, monkeypatch, DOWN_ST)
    upz._warn_form_vs_tape("META", {"zones": {}}, "derived_repair")
    assert capsys.readouterr().out == ""


def test_guard_failure_never_blocks_the_redraw(upz, monkeypatch, capsys):
    """守卫读不到 K 线/判别式炸 → 静默放行 — 画卡比警告重要。"""
    monkeypatch.setattr(upz.book, "bars",
                        lambda t, kdir=None: (_ for _ in ()).throw(OSError("no cache")))
    upz._warn_form_vs_tape("X", {"zones": {}}, "derived_normal")
    assert capsys.readouterr().out == ""
