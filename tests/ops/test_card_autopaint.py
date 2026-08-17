"""Cardless detection and detached card-agent orchestration."""
import subprocess
import time

from quant.core.holdings import cardless_tickers
from quant.ops import card_autopaint as ca


def _watchlist(**tickers):
    return {"tickers": tickers}


def _stub(**overrides):
    body = {
        "instrument": {"type": "stock"},
        "status": "watching",
        "agent": {"zones": None},
    }
    body.update(overrides)
    return body


def test_cardless_selects_empty_cards_but_not_references():
    watchlist = _watchlist(
        AAA=_stub(),
        REF=_stub(instrument={"type": "stock", "role": "volume_reference"}),
        READY=_stub(agent={"zones": {"stop_level": 20.0}}),
    )
    assert cardless_tickers({"tickers": {}}, watchlist) == ["AAA"]


def test_cardless_skips_unmanaged_positions_and_options():
    positions = {"tickers": {
        "KEEP": {
            "instrument": {"type": "stock", "role": "commemorative_hold"},
            "broker": {"qty": 1.0},
            "agent": {"zones": None},
        },
        "AAA270115C001000": {"broker": {"qty": 1.0}, "agent": {"zones": None}},
        "BBB": {"broker": {"qty": 10.0}, "agent": {"zones": None}},
    }}
    assert cardless_tickers(positions, {"tickers": {}}) == ["BBB"]


def test_cardless_requires_live_or_listed_membership():
    positions = {"tickers": {
        "FLAT": {"broker": {"qty": 0.0}, "agent": {"zones": None}},
    }}
    watchlist = _watchlist(
        REMOVED=_stub(moomoo_listed=False),
        PASSED=_stub(status="passed"),
        LIVE=_stub(),
    )
    assert cardless_tickers(positions, watchlist) == ["LIVE"]


def test_cardless_merges_buckets_without_duplicates():
    positions = {"tickers": {
        "AAA": {"broker": {"qty": 3.0}, "agent": {"zones": None}},
        "BBB": {"broker": {"qty": 1.0}, "agent": {}},
    }}
    assert cardless_tickers(positions, _watchlist(AAA=_stub())) == ["AAA", "BBB"]


def test_due_skips_a_running_job(monkeypatch):
    monkeypatch.setattr(ca, "_alive", lambda pid: pid == 4242)
    state = {"jobs": {
        "AAA": {"pid": 4242, "spawned_at_ts": 0},
        "BBB": {"pid": 9, "spawned_at_ts": 0},
    }}
    ready, skipped = ca.due(["AAA", "BBB"], state, now=10 * ca.COOLDOWN_S)
    assert ready == ["BBB"]
    assert skipped[0][0] == "AAA" and "still running" in skipped[0][1]


def test_due_honors_cooldown_then_retries(monkeypatch):
    monkeypatch.setattr(ca, "_alive", lambda pid: False)
    state = {"jobs": {"AAA": {"pid": 1, "spawned_at_ts": 1000.0}}}
    ready, skipped = ca.due(["AAA"], state, now=1000.0 + ca.COOLDOWN_S / 2)
    assert ready == [] and "cooldown" in skipped[0][1]
    assert ca.due(["AAA"], state, now=1000.0 + ca.COOLDOWN_S + 1)[0] == ["AAA"]
    assert ca.due(["AAA"], state, now=1000.0, force=True)[0] == ["AAA"]


def test_spawn_is_detached_and_never_waits(monkeypatch, tmp_path):
    seen = {}

    class FakeProcess:
        pid = 777

        def wait(self, *args, **kwargs):
            raise AssertionError("detached card launch must never wait")

        communicate = wait

    def fake_popen(argv, **kwargs):
        seen["argv"], seen["kwargs"] = argv, kwargs
        return FakeProcess()

    monkeypatch.setattr(ca, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(ca, "_timeout_argv", lambda seconds: ["/usr/bin/timeout", str(seconds)])
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    job = ca.spawn("AAA", "watchlist", "2099-01-15", "/usr/bin/claude")

    assert job["pid"] == 777
    assert seen["kwargs"]["start_new_session"] is True
    assert seen["kwargs"]["stdin"] is subprocess.DEVNULL
    assert "/usr/bin/claude" in seen["argv"]
    assert "AAA" in " ".join(str(item) for item in seen["argv"])
    assert job["timeout_enforced"] is True


def test_timeout_falls_back_to_perl_when_gnu_timeout_is_missing(monkeypatch):
    monkeypatch.setattr(
        ca.shutil, "which", lambda name: "/usr/bin/perl" if name == "perl" else None)
    argv = ca._timeout_argv(1800)
    assert argv[0].endswith("perl")
    assert "alarm shift; exec @ARGV" in argv
    assert "1800" in argv


def test_perl_timeout_fallback_enforces_wall_clock(monkeypatch):
    monkeypatch.setattr(
        ca.shutil, "which", lambda name: "/usr/bin/perl" if name == "perl" else None)
    started = time.monotonic()
    result = subprocess.run(
        [*ca._timeout_argv(1), "/bin/sleep", "30"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    elapsed = time.monotonic() - started

    assert result.returncode != 0
    assert elapsed < 10


def test_run_reports_missing_binary_without_raising(monkeypatch):
    monkeypatch.setattr(ca, "read_json", lambda path: (
        _watchlist(AAA=_stub()) if "watchlist" in str(path) else {"tickers": {}}
    ))
    monkeypatch.setattr(ca, "load_state", lambda: {})
    monkeypatch.setattr(ca, "_claude_bin", lambda: None)
    result = ca.run()
    assert result["cardless"] == ["AAA"] and result["spawned"] == []
    assert result["errors"] and "CLAUDE_BIN" in result["errors"][0]


def test_run_caps_launches_per_round(monkeypatch):
    monkeypatch.setattr(ca, "read_json", lambda path: (
        _watchlist(AAA=_stub(), BBB=_stub(), CCC=_stub())
        if "watchlist" in str(path) else {"tickers": {}}
    ))
    monkeypatch.setattr(ca, "load_state", lambda: {})
    result = ca.run(dry_run=True, max_spawn=2)
    assert [item["ticker"] for item in result["dry_run"]] == ["AAA", "BBB"]
    assert [item["ticker"] for item in result["skipped"]] == ["CCC"]


def test_prompt_is_self_contained_and_scoped():
    prompt = ca.build_prompt("AAA", "watchlist", "2099-01-15")
    assert "AAA" in prompt and "--bucket watchlist" in prompt
    assert "zone-maintenance.md" in prompt and "macro-context-check.md" in prompt
    assert "update_position_zones.py" in prompt
    assert "Modify only the AAA card" in prompt
    assert "Do not place orders" in prompt
    assert "--bucket current" in ca.build_prompt("BBB", "positions", "2099-01-15")
