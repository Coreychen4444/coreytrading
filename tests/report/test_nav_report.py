"""report/nav_report —— 周复盘 §2 的 NAV 周变口径: 剔除出入金 + 账本覆盖警告 (合成提款案)。"""
import json

import pytest

from quant.report import nav_report as nr
from tests.core.test_nav import FLOW_0831, NAV


def _env(tmp_path, *, flows=(FLOW_0831,), synced_through="2026-09-05"):
    nav = tmp_path / "nav_history.jsonl"
    nav.write_text("\n".join(json.dumps(r) for r in NAV) + "\n")
    fl = tmp_path / "cash_flows.jsonl"
    fl.write_text("".join(json.dumps(r) + "\n" for r in flows))
    side = tmp_path / "cash_flows.sync.json"
    if synced_through:
        side.write_text(json.dumps({"synced_through": synced_through}))
    elif side.exists():
        side.unlink()
    return dict(nav_path=nav, flows_path=fl, sync_path=side)


def test_build_and_render_the_synthetic_case(tmp_path):
    rep = nr.build(days=7, **_env(tmp_path))
    assert rep["ok"] and rep["adjusted_change_usd"] == pytest.approx(400.0) and rep["warnings"] == []
    txt = nr.render(rep)
    assert "2026-08-28 → 2026-09-04" in txt
    assert "| 账面变动 | -$1,100 (-5.50%) |" in txt
    assert "-$1,500" in txt and "Bank Transfer Withdrawals SGD -2,010.00 = USD -1,500.00" in txt
    assert "| 剔除后变动 | +$400 (+2.00%) · TWR +2.25% |" in txt
    assert "| QQQ / SPY 同期 | +0.50% / +0.20% |" in txt
    assert "出入金不是盈亏" in txt and "⚠️" not in txt


def test_warns_when_ledger_does_not_cover_the_window(tmp_path):
    rep = nr.build(days=7, **_env(tmp_path, synced_through="2026-09-01"))
    assert any("只同步到 2026-09-01" in w for w in rep["warnings"])
    rep = nr.build(days=7, **_env(tmp_path, synced_through=None))
    assert any("从未同步" in w for w in rep["warnings"]) and "⚠️" in nr.render(rep)
    rep = nr.build(days=7, **_env(tmp_path, flows=(dict(FLOW_0831, usd_amount=None),)))
    assert any("未折算" in w for w in rep["warnings"])


def test_no_flows_and_too_few_snapshots(tmp_path):
    rep = nr.build(days=7, **_env(tmp_path, flows=()))
    assert "| 外部现金流 | 无 |" in nr.render(rep) and rep["adjusted_change_pct"] == -5.5
    env = _env(tmp_path)
    env["nav_path"].write_text(json.dumps(NAV[0]) + "\n")
    rep = nr.build(days=7, **env)
    assert not rep["ok"] and "不足两行" in nr.render(rep)


def test_cli_json_and_text(tmp_path, monkeypatch, capsys):
    env = _env(tmp_path)
    monkeypatch.setattr(nr, "NAV_HISTORY_PATH", env["nav_path"])
    monkeypatch.setattr(nr, "CASH_FLOWS_PATH", env["flows_path"])
    monkeypatch.setattr(nr, "CASH_FLOWS_SYNC_PATH", env["sync_path"])
    assert nr.main(["--days", "7", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["twr_pct"] == pytest.approx(2.25, abs=0.01)
    assert nr.main(["--start", "2026-09-01", "--end", "2026-09-03"]) == 0
    assert "2026-09-01 → 2026-09-03" in capsys.readouterr().out
