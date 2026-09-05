"""update_position_zones 保留路径防呆 ()。

起因: 一次 --key-risk/--notes-only 调用后 GOOGL 卡 zones/current/next_regime 全为
null — schema 合法 (stub 形状) 所以静默写入。同参数复现三次全部保留, 现场被
git checkout 销毁, 根因未确认。防呆 = 无 zone 参数的调用中 zones/next_regime
必须逐字节透传, 变异 → abort 不写盘。真凶无论是谁, 破坏从此响亮。
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".claude/skills/trading-analysis/scripts/update_position_zones.py"


@pytest.fixture
def upz(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("upz_guard_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setattr(sys, "argv", ["upz"])
    spec.loader.exec_module(mod)

    card = {
        "instrument": {"type": "stock"}, "broker": None, "status": "watching",
        "agent": {
            "thesis": {"summary": "t", "status": "intact", "conviction": "medium",
                       "last_reviewed": "2026-08-05T00:00:00Z"},
            "zones": {"accumulation_zone_1": [358.5, 368.0], "stop_level": 335.5,
                      "valid_until": "2026-08-17",
                      "anchored_on": {"date": "2026-08-04", "basis": "b"}},
            "action": {"intent": "wait_for_zone", "reason": "r"},
            "next_regime": {"name": "R_x", "entry_gate": {"level": 382.5, "days": 2},
                            "cap_pct_nav": 0.05},
            "current_regime": {"name": "常规参与", "cap_pct_nav": 0.05,
                               "participation": "zone_eligible",
                               "since": "2026-08-04", "source": "card"},
        },
    }
    wl = tmp_path / "watchlist.json"
    wl.write_text(json.dumps({"tickers": {"GOOGL": card}}), encoding="utf-8")
    pos = tmp_path / "positions.json"
    pos.write_text(json.dumps({"tickers": {}}), encoding="utf-8")
    monkeypatch.setattr(mod, "WATCHLIST_PATH", wl)
    monkeypatch.setattr(mod, "POSITIONS_PATH", pos)
    from quant.core import io_json
    monkeypatch.setattr(io_json, "STATE_LOCK_PATH", tmp_path / ".state.lock")
    monkeypatch.setattr(mod, "KLINE_CACHE_DIR", tmp_path / "klines")
    monkeypatch.setattr(mod, "VALUATION_INPUTS_PATH", tmp_path / "inputs.json")
    return mod, wl


def _run(mod, argv):
    sys.argv = ["upz"] + argv
    return mod.main()


def test_preserve_path_passes_untouched(upz, monkeypatch):
    mod, wl = upz
    _run(mod, ["--ticker", "GOOGL", "--bucket", "watchlist",
               "--key-risk", "k", "--conviction", "medium"])
    card = json.loads(wl.read_text())["tickers"]["GOOGL"]
    assert card["agent"]["zones"]["accumulation_zone_1"] == [358.5, 368.0]
    assert card["agent"]["next_regime"]["name"] == "R_x"
    assert card["agent"]["key_risk"] == "k"


def test_zone_wipe_aborts_before_write(upz, monkeypatch):
    """模拟 8/6 的破坏形状: merge 返回 zones=None 的卡 → 必须 abort 且不落盘。"""
    mod, wl = upz
    orig = mod.merge_candidate
    def wiping(existing, args):
        out = orig(existing, args)
        out["agent"]["zones"] = None          # 破坏
        out["agent"].pop("current_regime", None)
        return out
    monkeypatch.setattr(mod, "merge_candidate", wiping)
    before = wl.read_text()

    with pytest.raises(RuntimeError, match="保留路径变异.*zones"):
        _run(mod, ["--ticker", "GOOGL", "--bucket", "watchlist",
                   "--key-risk", "k", "--conviction", "medium"])
    assert wl.read_text() == before, "abort 后文件必须原封不动"


def test_next_regime_wipe_aborts(upz, monkeypatch):
    mod, wl = upz
    orig = mod.merge_candidate
    def wiping(existing, args):
        out = orig(existing, args)
        out["agent"]["next_regime"] = None
        return out
    monkeypatch.setattr(mod, "merge_candidate", wiping)

    with pytest.raises(RuntimeError, match="保留路径变异.*next_regime"):
        _run(mod, ["--ticker", "GOOGL", "--bucket", "watchlist",
                   "--key-risk", "k", "--conviction", "medium"])


def test_metadata_only_call_merges_instead_of_wiping_levels(upz):
    """ 发现的静默破坏: 只传 zone **元数据**(valid-until / reasoning /
    update-trigger / anchored-on) 时, 旧判据让它走整块替换 → z1/z2/trim/no_chase/stop
    全部消失, 且 schema 合法照常落盘。价位判别式修掉这条路。"""
    mod, wl = upz
    _run(mod, ["--ticker", "GOOGL", "--bucket", "watchlist",
               "--valid-until", "2026-08-25", "--update-trigger", "t2",
               "--reasoning-summary", "r2", "--conviction", "medium"])
    z = json.loads(wl.read_text())["tickers"]["GOOGL"]["agent"]["zones"]
    assert z["accumulation_zone_1"] == [358.5, 368.0]   # 价位全须留下
    assert z["stop_level"] == 335.5
    assert z["anchored_on"] == {"date": "2026-08-04", "basis": "b"}
    assert z["valid_until"] == "2026-08-25" and z["update_trigger"] == "t2"


def test_explicit_zone_args_may_replace(upz):
    """传了 zone 参数 = 重画路径, 防呆不拦 (INTC 8/5 重画那种调用要照常工作)。"""
    mod, wl = upz
    _run(mod, ["--ticker", "GOOGL", "--bucket", "watchlist",
               "--zone1", "360", "370", "--stop-level", "340",
               "--anchored-on", "2026-08-06", "--anchor-basis", "b2",
               "--conviction", "medium"])
    card = json.loads(wl.read_text())["tickers"]["GOOGL"]
    assert card["agent"]["zones"]["accumulation_zone_1"] == [360.0, 370.0]


def test_expected_revision_accepts_current_card_and_rejects_replay(upz):
    mod, wl = upz
    before = json.loads(wl.read_text())["tickers"]["GOOGL"]
    args = ["--ticker", "GOOGL", "--bucket", "watchlist", "--key-risk", "reviewed",
            "--expected-revision", mod.canonical_hash(before)]
    assert mod.main(args) == 0
    after = wl.read_bytes()
    assert json.loads(after)["tickers"]["GOOGL"]["agent"]["key_risk"] == "reviewed"
    with pytest.raises(RuntimeError, match="revision conflict"):
        mod.main(args)
    assert wl.read_bytes() == after


def test_expected_revision_is_checked_after_acquiring_lock(upz, monkeypatch):
    """A broker/card writer wins the lock after analysis; its newer card must survive."""
    from contextlib import contextmanager
    mod, wl = upz
    old_card = json.loads(wl.read_text())["tickers"]["GOOGL"]
    real_lock = mod.state_lock
    updated = []

    @contextmanager
    def interleaved_lock():
        with real_lock():
            doc = json.loads(wl.read_text())
            doc["tickers"]["GOOGL"]["agent"]["key_risk"] = "newer independent update"
            wl.write_text(json.dumps(doc))
            updated.append(wl.read_bytes())
            yield

    monkeypatch.setattr(mod, "state_lock", interleaved_lock)
    with pytest.raises(RuntimeError, match="revision conflict"):
        mod.main(["--ticker", "GOOGL", "--bucket", "watchlist", "--key-risk", "stale",
                  "--expected-revision", mod.canonical_hash(old_card)])
    assert wl.read_bytes() == updated[0]


def test_revision_is_per_ticker_not_whole_document(upz):
    mod, wl = upz
    doc = json.loads(wl.read_text())
    revision = mod.canonical_hash(doc["tickers"]["GOOGL"])
    doc["generated_at"] = "2099-01-01T00:00:00Z"
    doc["tickers"]["OTHER"] = json.loads(json.dumps(doc["tickers"]["GOOGL"]))
    doc["tickers"]["OTHER"]["agent"]["key_risk"] = "other update"
    wl.write_text(json.dumps(doc, sort_keys=True))
    assert mod.main(["--ticker", "GOOGL", "--bucket", "watchlist", "--key-risk", "accepted",
                     "--expected-revision", revision]) == 0
    assert json.loads(wl.read_text())["tickers"]["OTHER"] == doc["tickers"]["OTHER"]


def test_missing_card_uses_null_revision(upz):
    mod, wl = upz
    args = ["--ticker", "NEW", "--bucket", "watchlist", "--thesis", "synthetic",
            "--expected-revision", mod.canonical_hash(None)]
    assert mod.main(args) == 0
    before = wl.read_bytes()
    with pytest.raises(RuntimeError, match="revision conflict"):
        mod.main(args)
    assert wl.read_bytes() == before


def test_positions_revision_covers_broker_and_preserves_its_update(upz):
    mod, wl = upz
    card = json.loads(wl.read_text())["tickers"]["GOOGL"]
    card.update(status="active", broker={"qty": 2, "current_price": 360},
                lots={"base": [], "position": []}, position_summary={}, pending_orders=[])
    revision = mod.canonical_hash(card)
    card["broker"]["qty"] = 3
    mod.POSITIONS_PATH.write_text(json.dumps({"tickers": {"GOOGL": card}}))
    before = mod.POSITIONS_PATH.read_bytes()
    with pytest.raises(RuntimeError, match="revision conflict"):
        mod.main(["--ticker", "GOOGL", "--bucket", "current", "--key-risk", "stale",
                  "--expected-revision", revision])
    assert mod.POSITIONS_PATH.read_bytes() == before
    # A fresh proposal may update its own fields while retaining the broker fact.
    assert mod.main(["--ticker", "GOOGL", "--bucket", "current", "--key-risk", "fresh",
                     "--expected-revision", mod.canonical_hash(card)]) == 0
    current = json.loads(mod.POSITIONS_PATH.read_text())["tickers"]["GOOGL"]
    assert current["broker"] == card["broker"]
    assert current["agent"]["key_risk"] == "fresh"
