"""缺卡 → 后台异步建卡。

守两件事:
  ① **判据**: 谁算缺卡 —— 参考票与结构豁免层不算, 期权不算 (它锚在正股卡上),
     已有 zones 的不算 (判据自己就是终止条件, 不需要"已完成"标记)
  ② **不阻塞**: 起进程这一步绝不 wait/communicate; 起不来只落 errors 不抛
"""
import subprocess
import json
from datetime import datetime, timezone

import pytest

from quant.core.holdings import cardless_tickers
from quant.ops import card_autopaint as ca

REAL_BARS_GAP = ca.bars_gap


@pytest.fixture(autouse=True)
def isolated_controller_state(monkeypatch, tmp_path):
    monkeypatch.setattr(ca, "STATE_PATH", tmp_path / "autopaint.json")
    monkeypatch.setattr(ca, "bars_gap", lambda ticker, now=None: None)  # 默认日线已就绪; 新鲜度门单独测


def _wl(**tickers):
    return {"tickers": tickers}


def _stub(**over):
    body = {"instrument": {"type": "stock"}, "status": "watching",
            "agent": {"zones": None}}
    body.update(over)
    return body


# ── ① 判据 ────────────────────────────────────────────────────────

def test_cardless_picks_up_the_empty_stub_but_not_the_reference_tickers():
    """DDD  进组 → 08-14 仍是空 stub, 正是本机制的源案例。
    SPY/QQQ/SMH/SOXX 是量能参考票, 该有报价**不该**有 zones。"""
    wl = _wl(DDD=_stub(),
             SPY=_stub(instrument={"type": "stock", "role": "volume_reference"}),
             CCC=_stub(agent={"zones": {"stop_level": 200.0}}))
    assert cardless_tickers({"tickers": {}}, wl) == ["DDD"]


def test_cardless_skips_the_unmanaged_role_and_options():
    """纪念仓不设 zone 是**对的** (结构豁免层); 期权卡的结构锚在 underlying
    正股卡上, 那半归 underlying_cards_missing_zones。"""
    pos = {"tickers": {
        "SPCX": {"instrument": {"type": "stock", "role": "commemorative_hold"},
                 "broker": {"qty": 1.0}, "agent": {"zones": None}},
        "GOOGL270115C390000": {"broker": {"qty": 1.0}, "agent": {"zones": None}},
        "BBB": {"broker": {"qty": 10.0}, "agent": {"zones": None}},
    }}
    assert cardless_tickers(pos, {"tickers": {}}) == ["BBB"]


def test_cardless_only_counts_live_positions_and_listed_candidates():
    """positions 侧判据 = broker.qty (平仓残卡不该被反复建卡);
    watchlist 侧 = 还在 moomoo 自选里 (moomoo_listed=false 是已移出的墓碑)。"""
    pos = {"tickers": {"MU": {"broker": {"qty": 0.0}, "agent": {"zones": None}}}}
    wl = _wl(GONE=_stub(moomoo_listed=False), OLD=_stub(status="passed"),
             LIVE=_stub())
    assert cardless_tickers(pos, wl) == ["LIVE"]


def test_cardless_merges_both_buckets_deduped():
    wl = _wl(AAA=_stub())
    pos = {"tickers": {"AAA": {"broker": {"qty": 3.0}, "agent": {"zones": None}},
                       "BBB": {"broker": {"qty": 1.0}, "agent": {}}}}
    assert cardless_tickers(pos, wl) == ["AAA", "BBB"]


# ── ② 去重 / 冷却 ─────────────────────────────────────────────────

def test_due_skips_a_job_still_running(monkeypatch):
    monkeypatch.setattr(ca, "_alive", lambda pid: pid == 4242)
    state = {"jobs": {"DDD": {"pid": 4242, "spawned_at_ts": 0, "log": "x.log"},
                      "AAA": {"pid": 9, "spawned_at_ts": 0}}}
    go, skipped = ca.due(["DDD", "AAA"], state, now=10 * ca.COOLDOWN_S)
    assert go == ["AAA"]
    assert skipped[0][0] == "DDD" and "仍在跑" in skipped[0][1]


def test_due_honours_the_cooldown_then_retries(monkeypatch):
    """冷却 = heartbeat 一轮: 上一次没画成, 下一轮自动重试 (不是永久放弃),
    但同一轮内不会刷出第二个进程。"""
    monkeypatch.setattr(ca, "_alive", lambda pid: False)
    state = {"jobs": {"DDD": {"pid": 1, "spawned_at_ts": 1000.0}}}
    go, skipped = ca.due(["DDD"], state, now=1000.0 + ca.COOLDOWN_S / 2)
    assert go == [] and "冷却" in skipped[0][1]
    go2, _ = ca.due(["DDD"], state, now=1000.0 + ca.COOLDOWN_S + 1)
    assert go2 == ["DDD"]
    go3, _ = ca.due(["DDD"], state, now=1000.0, force=True)
    assert go3 == ["DDD"]


# ── ③ 不阻塞 ──────────────────────────────────────────────────────

def test_spawn_is_detached_and_never_waits(monkeypatch, tmp_path):
    """🚨 硬约束: 不许阻塞 heartbeat。
    起进程必须 detached (start_new_session) 且**从不** wait/communicate。"""
    seen = {}

    class FakeProc:
        pid = 777

        def wait(self, *a, **k):          # 调到就是 bug
            raise AssertionError("spawn 不许 wait —— heartbeat 会被卡住")

        communicate = wait

    def fake_popen(argv, **kw):
        seen["argv"], seen["kw"] = argv, kw
        return FakeProc()

    monkeypatch.setattr(ca, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    job = ca.spawn("DDD", "watchlist", "2026-08-14", {"apply": False})

    assert job["pid"] == 777
    assert seen["kw"]["start_new_session"] is True      # heartbeat 退出后照常活
    assert seen["kw"]["stdin"] is subprocess.DEVNULL    # 无人可交互
    assert "DDD" in " ".join(str(x) for x in seen["argv"])
    # 墙钟兜底不留孤儿, 且 argv[0] 必须是**本机真实存在**的可执行文件 ——
    #  的病根就是裸 "timeout" 在 macOS 上不存在, 旧断言反而钉死了它。
    assert seen["argv"][0] == ca.sys.executable
    assert "agent_runtime.py" in seen["argv"][1]
    assert "--apply" not in seen["argv"]
    assert "bypassPermissions" not in seen["argv"]
    assert job["timeout_owner"] == "agent_runtime" and job["mode"] == "shadow"


def test_timeout_argv_survives_a_mac_without_gnu_timeout(monkeypatch):
    """macOS 无 GNU timeout (实证 FileNotFoundError → 0 字节日志 + state
    不落盘 + 冷却空转三症状同根)。which 找不到 timeout/gtimeout 时必须回落
    perl alarm+exec, 而不是把裸字符串 "timeout" 交给 Popen 去炸。"""
    import shutil as _sh
    monkeypatch.setattr(_sh, "which",
                        lambda name: "/usr/bin/perl" if name == "perl" else None)
    argv = ca._timeout_argv(1800)
    assert argv and argv[0].endswith("perl") and "1800" in argv
    # 语义自证: alarm 穿过 exec, 1 秒后 SIGALRM 终止 sleep
    import subprocess as sp, time as _t
    t0 = _t.time()
    p = sp.run([*ca._timeout_argv(1), "/bin/sleep", "30"])
    assert _t.time() - t0 < 10 and p.returncode != 0


def test_run_reports_bad_routing_instead_of_raising(monkeypatch, tmp_path):
    """claude 找不到 (cron 的 PATH 比登录 shell 窄) → 落 errors, 不抛。
    缺卡清单照常报出 —— 静默变黑是本仓明令禁止的形状。"""
    monkeypatch.setattr(ca, "read_json", lambda p: (
        _wl(DDD=_stub()) if "watchlist" in str(p) else {"tickers": {}}))
    monkeypatch.setattr(ca, "load_state", lambda: {})
    def bad_routes():
        raise ValueError("invalid routing")
    monkeypatch.setattr(ca, "load_routes", bad_routes)
    r = ca.run()
    assert r["cardless"] == ["DDD"] and r["spawned"] == []
    assert r["errors"] and "routing" in r["errors"][0]


def test_run_caps_the_number_of_agents_per_round(monkeypatch):
    """自选一夜清空 ≠ 一次起十个 agent; 被压下的必须报出来 (静默截断 = 本仓惯犯)。"""
    monkeypatch.setattr(ca, "read_json", lambda p: (
        _wl(A=_stub(), B=_stub(), C=_stub()) if "watchlist" in str(p)
        else {"tickers": {}}))
    monkeypatch.setattr(ca, "load_state", lambda: {})
    monkeypatch.setattr(ca, "load_routes", lambda: {"autopaint": {"enabled": True, "apply": True, "review": True}})
    r = ca.run(dry_run=True, max_spawn=2)
    assert [d["ticker"] for d in r["dry_run"]] == ["A", "B"]
    assert [s["ticker"] for s in r["skipped"]] == ["C"]
    assert "本轮上限" in r["skipped"][0]["reason"]


def test_closed_position_tombstone_does_not_steal_watchlist_card_job(monkeypatch):
    """A symbol may return to watchlist while its closed position row remains."""
    positions = {"tickers": {"SYN": {"instrument": {"type": "stock"},
                                     "broker": {"qty": 0}, "agent": {"zones": None}}}}
    watchlist = _wl(SYN=_stub())
    monkeypatch.setattr(ca, "read_json", lambda p: watchlist if "watchlist" in str(p) else positions)
    monkeypatch.setattr(ca, "load_state", lambda: {})
    monkeypatch.setattr(ca, "load_routes", lambda: {"autopaint": {"enabled": True, "apply": False, "review": True}})
    output = ca.run(dry_run=True)
    assert output["cardless"] == ["SYN"]
    assert output["dry_run"] == [{"ticker": "SYN", "bucket": "watchlist"}]


@pytest.fixture
def launch_environment(monkeypatch, tmp_path):
    positions, watchlist = tmp_path / "positions.json", tmp_path / "watchlist.json"
    positions.write_text(json.dumps({"tickers": {}}))
    watchlist.write_text(json.dumps(_wl(SYN=_stub())))
    monkeypatch.setattr(ca, "POSITIONS_PATH", positions)
    monkeypatch.setattr(ca, "WATCHLIST_PATH", watchlist)
    monkeypatch.setattr(ca, "load_routes", lambda: {"autopaint": {"enabled": True, "apply": False, "review": True}})
    monkeypatch.setattr(ca, "_alive", lambda pid: False)
    clock = {"now": 100000.0}
    monkeypatch.setattr(ca.time, "time", lambda: clock["now"])
    return clock


def test_starting_lease_is_durable_before_launch(monkeypatch, launch_environment):
    def launch(ticker, bucket, today, route):
        saved = ca.read_json(ca.STATE_PATH)["jobs"][ticker]
        assert saved["status"] == "starting"
        assert saved["lease_until_ts"] > launch_environment["now"]
        assert saved["launch_id"]
        return {"ticker": ticker, "bucket": bucket, "pid": 999, "log": "fake.log"}

    monkeypatch.setattr(ca, "spawn", launch)
    output = ca.run()
    assert output["spawned"][0]["receipt_persisted"] is True
    assert ca.read_json(ca.STATE_PATH)["jobs"]["SYN"]["status"] == "running"


def test_failed_starting_persistence_never_launches(monkeypatch, launch_environment):
    monkeypatch.setattr(ca, "atomic_write_json", lambda *args: (_ for _ in ()).throw(OSError("disk")))
    monkeypatch.setattr(ca, "spawn", lambda *args: pytest.fail("must persist before spawn"))
    output = ca.run()
    assert not output["spawned"]
    assert "not launched" in output["errors"][0]


def test_lost_launch_receipt_keeps_lease_even_under_force(monkeypatch, launch_environment):
    calls, writes = [], []
    original_write = ca.atomic_write_json

    def write(path, data):
        writes.append(1)
        if len(writes) == 2:
            raise OSError("receipt disk failure")
        return original_write(path, data)

    def launch(ticker, bucket, *args):
        calls.append(ticker)
        return {"ticker": ticker, "bucket": bucket, "pid": 999, "log": "fake.log"}

    monkeypatch.setattr(ca, "atomic_write_json", write)
    monkeypatch.setattr(ca, "spawn", launch)
    output = ca.run()
    assert output["spawned"][0]["receipt_persisted"] is False
    assert ca.read_json(ca.STATE_PATH)["jobs"]["SYN"]["status"] == "starting"
    retry = ca.run(force=True)
    assert not retry["spawned"]
    assert "starting lease" in retry["skipped"][0]["reason"]
    assert calls == ["SYN"]
    launch_environment["now"] += ca.STARTING_LEASE_S + 1
    ca.run(force=True)
    assert calls == ["SYN", "SYN"]


def test_definite_launch_failure_marks_failed_but_unknown_outcome_stays_fenced(monkeypatch, launch_environment):
    def not_started(*args):
        raise ca.SpawnNotStartedError("executable unavailable")

    monkeypatch.setattr(ca, "spawn", not_started)
    output = ca.run()
    saved = ca.read_json(ca.STATE_PATH)["jobs"]["SYN"]
    assert saved["status"] == "failed" and saved["lease_until_ts"] == 0
    assert not output["spawned"]

    def uncertain(*args):
        raise RuntimeError("failure after entering launch")

    monkeypatch.setattr(ca, "spawn", uncertain)
    ca.run(force=True)
    assert ca.read_json(ca.STATE_PATH)["jobs"]["SYN"]["status"] == "starting"
    retry = ca.run(force=True)
    assert not retry["spawned"] and "starting lease" in retry["skipped"][0]["reason"]


def test_next_ticker_does_not_erase_previous_uncertain_starting_lease(monkeypatch, launch_environment):
    ca.WATCHLIST_PATH.write_text(json.dumps(_wl(AAA=_stub(), BBB=_stub())))
    original_write = ca.atomic_write_json
    writes = []

    def write(path, data):
        writes.append(1)
        if len(writes) == 2:
            raise OSError("first receipt persistence failed")
        return original_write(path, data)

    monkeypatch.setattr(ca, "atomic_write_json", write)
    monkeypatch.setattr(ca, "spawn", lambda ticker, bucket, *args: {
        "ticker": ticker, "bucket": bucket, "pid": 999, "log": "fake.log"})
    ca.run(max_spawn=2)
    saved = ca.read_json(ca.STATE_PATH)["jobs"]
    assert saved["AAA"]["status"] == "starting"
    assert saved["BBB"]["status"] == "running"


def test_popen_failure_is_explicitly_known_not_started(monkeypatch, tmp_path):
    monkeypatch.setattr(ca, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(ca.subprocess, "Popen", lambda *args, **kwargs: (_ for _ in ()).throw(FileNotFoundError("missing")))
    with pytest.raises(ca.SpawnNotStartedError):
        ca.spawn("SYN", "watchlist", "2026-09-05", {"apply": False})


def test_prompt_is_self_contained_and_scoped():
    """子 agent 是全新 session (读不到触发它的这轮对话) —— prompt 必须自足,
    且把作用域钉死在这一张卡上。"""
    p = ca.build_prompt("DDD", "watchlist", "2026-08-14")
    assert "DDD" in p and "--bucket watchlist" in p
    assert "zone-maintenance.md" in p and "macro-context-check.md" in p
    assert "update_position_zones.py" in p          # 唯一合法写法
    assert "只碰 DDD 这一张卡" in p
    assert "不下单" in p
    assert ca.build_prompt("MRVL", "positions", "2026-08-14").count("--bucket current")


@pytest.mark.parametrize("bucket,flag", [("positions", "current"), ("watchlist", "watchlist")])
def test_prompt_bucket_maps_to_the_cli_flag(bucket, flag):
    """positions 桶在 update_position_zones 里叫 `current` —— 传错桶 = 写错文件。"""
    assert f"--bucket {flag}" in ca.build_prompt("X", bucket, "2026-08-14")


# ── ④ 日线新鲜度门 ────────────────────────────────────────────────

def test_run_defers_tickers_whose_daily_bars_are_not_current(monkeypatch, launch_environment):
    """典型: heartbeat 派发时新票无日线 → run_card 必然 needs_review。
    不起、不占名额、不记冷却; briefing 拉完 K 线后 repaint 队列接手。"""
    ca.WATCHLIST_PATH.write_text(json.dumps(_wl(NEW=_stub(), OLD=_stub())))
    monkeypatch.setattr(ca, "bars_gap", lambda t, now=None: "本地日线停在 无" if t == "NEW" else None)
    monkeypatch.setattr(ca, "spawn", lambda ticker, bucket, *a: {
        "ticker": ticker, "bucket": bucket, "pid": 999, "log": "fake.log"})
    output = ca.run(max_spawn=1)
    assert [s["ticker"] for s in output["spawned"]] == ["OLD"]
    assert output["skipped"] == [{"ticker": "NEW", "reason": "本地日线停在 无"}]
    assert "NEW" not in ca.read_json(ca.STATE_PATH)["jobs"]


def test_bars_gap_uses_the_capture_discriminant(monkeypatch, tmp_path):
    monkeypatch.setattr(ca, "KLINE_CACHE_DIR", tmp_path)
    now = datetime(2026, 9, 11, 10, 26, tzinfo=timezone.utc)   # ET 06:26: 最新完整会话 = 09-10
    assert "停在 无" in REAL_BARS_GAP("CRWD", now)
    bar = {"open": 100, "high": 101, "low": 99, "close": 100, "volume": 1}
    (tmp_path / "CRWD.jsonl").write_text(json.dumps({"time": "2026-09-09", **bar}) + "\n")
    assert "停在 2026-09-09" in REAL_BARS_GAP("CRWD", now)
    with (tmp_path / "CRWD.jsonl").open("a") as fh:
        fh.write(json.dumps({"time": "2026-09-10", **bar}) + "\n")
    assert REAL_BARS_GAP("CRWD", now) is None
