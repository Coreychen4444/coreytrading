"""pipeline —— 采集/编排层的顺序不变量。

这些测试守的不是"代码没崩", 是**两个定时任务不会再各自漂**。此前流程写在
tasks/*.md 的 prose 里, 已经漂出四处分歧 (顺序错 / 漏 order_sync / NAV 滞后 /
--apply 漏传)。顺序一旦进代码, 就该有断言钉住它。
"""
import json
from pathlib import Path

from quant.ops import pipeline  # noqa: E402


def keys(mode):
    return [s.key for s in pipeline.plan(mode)]


# ── 顺序不变量 ────────────────────────────────────────────────────

def test_brief_consumes_everything_so_it_runs_after_the_collectors():
    """portfolio_brief 是唯一消费者。排在采集中间 = 读半成品。
    briefing 此前正是这样: brief 在 fills / watchlist_migrate 之前跑。"""
    for mode in (pipeline.HEARTBEAT, pipeline.BRIEFING):
        k = keys(mode)
        assert k.index("brief") > k.index("fills"), mode
        assert k.index("brief") > k.index("watchlist_migrate"), mode
        assert k.index("brief") > k.index("positions"), mode


def test_nav_lands_before_brief():
    """预算行的分母。nav 排在 brief 之后 → 预算用昨天的 NAV。"""
    k = keys(pipeline.BRIEFING)
    assert k.index("nav") < k.index("brief")


def test_entry_guard_is_last_in_briefing():
    """entry_guard 消费 brief + 今日 NAV, briefing 里必须垫底。"""
    k = keys(pipeline.BRIEFING)
    assert k[-1] == "entry"


def test_watchlist_sync_before_kline_cache():
    """新 stub 卡当轮就要能拉到 K 线, 否则 entry_guard 判 NEEDS_CARD 白等一天。"""
    k = keys(pipeline.BRIEFING)
    assert k.index("watchlist_sync") < k.index("klines")


def test_heartbeat_is_a_subset_of_briefing():
    """差异化只允许来自 cadence 和收盘依赖 —— heartbeat 不该有 briefing 没有的步骤。"""
    assert set(keys(pipeline.HEARTBEAT)) <= set(keys(pipeline.BRIEFING))


def test_intel_layer_stages_stay_retired():
    """情报层 收拢 ai-news-radar (用户: 本仓只做技术分析+风控):
    pipeline 不得有宏观/新闻采集步 (`klines_macro` 已随 macro_lines 退役)。
    想加回去先读 intel-consolidation directive。"""
    assert all(s.key != "klines_macro" for s in pipeline.STAGES)


def test_push_brief_alerts_pushes_only_when_alerts_exist(monkeypatch):
    """无告警不推 (HEARTBEAT_OK 的安静保持); 有告警推卡片+alerts 原文。
    (宏观观察行 随情报层收拢 ai-news-radar 移除 — 本仓只做技术+风控,
    简报不再携带宏观读数。)"""
    sent = []
    monkeypatch.setattr(pipeline, "notify", lambda msg: sent.append(msg) or True)
    assert pipeline.push_brief_alerts({"alerts": [], "fired_cards": []}) == "no_alerts"
    assert sent == []

    data = {"alerts": ["AAOI 🚨 STOP"], "fired_cards": ["card"]}
    assert pipeline.push_brief_alerts(data) is True
    assert len(sent) == 1
    assert "AAOI 🚨 STOP" in sent[0]
    assert sent[0].index("card") < sent[0].index("AAOI 🚨 STOP"), "卡片在前, alerts 在后"


def test_postclose_is_lean_close_layer_only():
    """The close-dependent chain evaluates exits before applying transitions."""
    kp = keys(pipeline.POSTCLOSE)
    assert kp == ["positions", "klines", "nav", "brief", "regime",
                  "reminders", "today_group"]
    assert set(kp) <= set(keys(pipeline.BRIEFING))


def test_exit_evaluation_runs_before_regime_transition():
    for mode in (pipeline.BRIEFING, pipeline.POSTCLOSE):
        k = keys(mode)
        assert k.index("brief") < k.index("regime"), mode


def test_postclose_refreshes_positions_before_brief():
    k = keys(pipeline.POSTCLOSE)
    assert k.index("positions") < k.index("brief")
    assert k.index("klines") < k.index("brief")
    assert k.index("nav") < k.index("brief")


def test_nav_lands_before_reminders_in_every_mode_it_runs():
    """z1 备注金额的分母 (available_cap(nav))。nav 排在 reminders 之后 = 画线用
    昨天的 NAV、brief 用今天的, 两处口径打架。"""
    for mode in (pipeline.BRIEFING, pipeline.POSTCLOSE):
        k = keys(mode)
        assert k.index("nav") < k.index("reminders"), mode


def test_reminders_reconcile_after_regime_and_after_brief():
    """价格提醒同步的是**转移后**的卡 → briefing 里必须在 regime 之后;
    两个模式都要跑 —— session 内重画的卡最迟 6h 被 heartbeat 接上。

    The brief emits alerts before reminder and group effectors, and reminders
    consume the exit stack produced in the same run."""
    for mode in (pipeline.HEARTBEAT, pipeline.BRIEFING, pipeline.POSTCLOSE):
        k = keys(mode)
        assert k.index("reminders") > k.index("brief"), mode
        assert k.index("today_group") > k.index("brief"), mode
    for mode in (pipeline.BRIEFING, pipeline.POSTCLOSE):
        k = keys(mode)
        assert k.index("reminders") > k.index("regime"), mode


def test_today_group_reconciles_after_regime_in_every_mode():
    """Today = 入场授权白名单, 判据直读卡片态 (gate 达标 → regime cap>0), 所以
    任何轮次对账都正确 (v3  用户: v2 曾锚 GO verdict + 绑死 postclose,
    把执行时点当授权状态 — MRVL/MSFT 有 cap 等回踩, 白名单整晚空着)。
    约束只剩一条: 须在 regime 之后 — 转移刚写的 cap/补画锁当轮就要被渲染。"""
    for mode in (pipeline.HEARTBEAT, pipeline.BRIEFING, pipeline.POSTCLOSE):
        assert "today_group" in keys(mode), mode
    for mode in (pipeline.BRIEFING, pipeline.POSTCLOSE):
        k = keys(mode)
        assert k.index("today_group") > k.index("regime"), mode


def test_both_modes_reconcile_orders():
    """briefing 此前完全没跑 order_sync, 早简报的 pending_orders 是隔夜旧的。"""
    for mode in (pipeline.HEARTBEAT, pipeline.BRIEFING):
        assert "orders" in keys(mode), mode


def test_close_reconcile_runs_after_fills_and_before_brief():
    """收尾要读事件流算 realized_pnl → 必须在 fills 之后; 简报把 lots 未清的死卡
    当持仓 → 必须在 brief 之前。两个模式都要有 —— 平仓不挑
    heartbeat 还是 briefing 那一轮发生。"""
    for mode in (pipeline.HEARTBEAT, pipeline.BRIEFING):
        k = keys(mode)
        assert k.index("reconcile") > k.index("fills"), mode
        assert k.index("reconcile") < k.index("brief"), mode


def test_watchlist_sync_runs_after_close_reconcile():
    """平仓自动加回自选的判据是 close_reconcile 落的
    exit_info.closed_at —— 排在它之前, 平仓那一轮就加不回去, 白等下一轮
    (heartbeat 6h / briefing 一天)。两个模式都要有: 平仓不挑哪一轮发生。"""
    for mode in (pipeline.HEARTBEAT, pipeline.BRIEFING):
        k = keys(mode)
        assert k.index("watchlist_sync") > k.index("reconcile"), mode


def test_lot_reconcile_runs_after_fills_and_before_brief():
    """买入落账要读事件流的 order_id → 必须在 fills 之后; brief 的预算行/集中度
    读 lots → 必须在 brief 之前。两个模式都要有 —— 成交不挑 heartbeat 还是
    briefing 那一轮发生。"""
    for mode in (pipeline.HEARTBEAT, pipeline.BRIEFING):
        k = keys(mode)
        assert k.index("lots") > k.index("fills"), mode
        assert k.index("lots") > k.index("watchlist_migrate"), mode
        assert k.index("lots") < k.index("brief"), mode


def test_autopaint_runs_after_watchlist_steps_and_before_brief():
    """New stubs must be visible before detached card agents are launched."""
    for mode in (pipeline.HEARTBEAT, pipeline.BRIEFING):
        planned = keys(mode)
        assert planned.index("autopaint") > planned.index("watchlist_sync"), mode
        assert planned.index("autopaint") > planned.index("watchlist_migrate"), mode
        assert planned.index("autopaint") < planned.index("brief"), mode


def test_read_only_downgrades_state_writers_to_dry_run(monkeypatch):
    """Every state writer in a read-only pipeline must become a dry run."""
    seen = {}

    def fake_run_stage(s, extra=None, drop=None):
        seen[s.key] = list(extra or [])
        return {"key": s.key, "ok": True, "rc": 0, "secs": 0.0, "stdout": "", "stderr": ""}

    monkeypatch.setattr(pipeline, "opend_alive", lambda *a, **k: True)
    monkeypatch.setattr(pipeline, "et_session_state", lambda: "pre")
    monkeypatch.setattr(pipeline, "run_stage", fake_run_stage)
    pipeline.main(["--mode", "heartbeat", "--read-only", "--json"])

    assert "--dry-run" in seen["reconcile"]
    assert "--dry-run" in seen["lots"]
    assert "--dry-run" in seen["autopaint"]
    # 第四个 (): exit_score 写 scores.jsonl — 预览不落评分
    assert "--dry-run" in seen["exit_score"]


def test_postclose_refuses_mid_session_and_alerts(monkeypatch):
    """冬令时自检: postclose 在盘中被触发 = DST 换季 cron 没改。
    kline 守卫只会让它静默 no-op —— 静默的 no-op 就是"这一步不存在",
    必须变成会响的错误: TG 告警 + 中止 rc=3, 每天响到改为止。"""
    sent, ran = [], []
    monkeypatch.setattr(pipeline, "opend_alive", lambda *a, **k: True)
    monkeypatch.setattr(pipeline, "et_session_state", lambda: "open")
    monkeypatch.setattr(pipeline, "notify", lambda m: sent.append(m) or True)
    monkeypatch.setattr(pipeline, "run_stage",
                        lambda s, extra=None, drop=None: ran.append(s.key))
    rc = pipeline.main(["--mode", "postclose", "--json"])
    assert rc == 3 and ran == []
    assert any("冬令时" in m for m in sent)


def test_migrate_always_carries_apply():
    """--apply 缺失 = 只检测不写。"""
    for mode in (pipeline.HEARTBEAT, pipeline.BRIEFING):
        s = next(x for x in pipeline.plan(mode) if x.key == "watchlist_migrate")
        assert "--apply" in s.argv, mode


def test_close_dependent_stages_never_enter_heartbeat():
    """heartbeat 每 6h 跑, 盘中也跑 —— 收盘依赖的步骤进去就是拿半天数据当收盘。"""
    hb = set(keys(pipeline.HEARTBEAT))
    for k in ("klines", "regime", "iv", "nav", "skhy", "entry", "account"):
        assert k not in hb, k


def test_get_portfolio_always_pins_usd():
    """不传 --currency USD → 服务端按 HKD 换算, total_assets/power 全错。"""
    s = next(x for x in pipeline.STAGES if x.key == "account")
    i = s.argv.index("--currency")
    assert s.argv[i + 1] == "USD"


# ── read-only 语义 ────────────────────────────────────────────────

def test_read_only_strips_log_and_adds_read_only(monkeypatch):
    calls = []

    def fake_run(argv, **kw):
        calls.append(argv)

        class R:
            returncode, stdout, stderr = 0, "", ""
        return R()

    monkeypatch.setattr(pipeline.subprocess, "run", fake_run)
    monkeypatch.setattr(pipeline, "opend_alive", lambda *a, **k: True)
    monkeypatch.setattr(pipeline, "stamp_heartbeat", lambda: (_ for _ in ()).throw(
        AssertionError("read-only 不该写 dead-man 时间戳")))
    pipeline.main(["--mode", "briefing", "--read-only", "--json"])

    brief = next(c for c in calls if "portfolio_brief.py" in " ".join(c))
    entry = next(c for c in calls if "entry_guard.py" in " ".join(c))
    assert "--read-only" in brief
    assert "--log" not in entry, "read-only 不该落 decisions.jsonl"


def test_normal_run_keeps_log_and_no_read_only(monkeypatch):
    calls = []

    def fake_run(argv, **kw):
        calls.append(argv)

        class R:
            returncode, stdout, stderr = 0, "", ""
        return R()

    monkeypatch.setattr(pipeline.subprocess, "run", fake_run)
    monkeypatch.setattr(pipeline, "opend_alive", lambda *a, **k: True)
    monkeypatch.setattr(pipeline, "handle_opend_recovered", lambda send: False)
    stamped = []
    monkeypatch.setattr(pipeline, "stamp_heartbeat", lambda: stamped.append(1))

    pipeline.main(["--mode", "briefing", "--json"])
    entry = next(c for c in calls if "entry_guard.py" in " ".join(c))
    brief = next(c for c in calls if "portfolio_brief.py" in " ".join(c))
    assert "--log" in entry and "--read-only" not in brief
    assert not stamped, "briefing 不能写 heartbeat 的 dead-man 时间戳"

    calls.clear()
    pipeline.main(["--mode", "heartbeat", "--json"])
    assert stamped, "heartbeat 正常跑完必须落 dead-man 时间戳"


def test_event_day_flag_only_reaches_entry_guard(monkeypatch):
    calls = []

    def fake_run(argv, **kw):
        calls.append(argv)

        class R:
            returncode, stdout, stderr = 0, "", ""
        return R()

    monkeypatch.setattr(pipeline.subprocess, "run", fake_run)
    monkeypatch.setattr(pipeline, "opend_alive", lambda *a, **k: True)
    monkeypatch.setattr(pipeline, "handle_opend_recovered", lambda send: False)
    pipeline.main(["--mode", "briefing", "--event-day", "--read-only", "--json"])
    with_flag = [c for c in calls if "--event-day" in c]
    assert len(with_flag) == 1 and "entry_guard.py" in " ".join(with_flag[0])


# ── OpenD 变黑 ────────────────────────────────────────────────────

def test_opend_down_aborts_and_fires_infra_alert(monkeypatch, capsys):
    """监控变黑必须可见。此前这段是 prompt 里的 heredoc, 靠 LLM 记得执行。"""
    monkeypatch.setattr(pipeline, "opend_alive", lambda *a, **k: False)
    fired = {}
    monkeypatch.setattr(pipeline, "handle_opend_down",
                        lambda send: fired.setdefault("send", send) or {"alert_fired": True})
    monkeypatch.setattr(pipeline.subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("OpenD down 时不该跑任何采集步骤")))
    rc = pipeline.main(["--mode", "heartbeat"])
    assert rc == 2
    assert "OpenD unreachable" in capsys.readouterr().out
    assert fired["send"] is True


def test_read_only_never_writes_infra_state(monkeypatch):
    monkeypatch.setattr(pipeline, "opend_alive", lambda *a, **k: False)
    monkeypatch.setattr(pipeline, "handle_opend_down",
                        lambda send: (_ for _ in ()).throw(
                            AssertionError("read-only 不该写 INFRA 冷却状态")))
    assert pipeline.main(["--mode", "heartbeat", "--read-only", "--json"]) == 2


def test_fatal_stage_aborts_the_rest(monkeypatch):
    seen = []

    def fake_run(argv, **kw):
        seen.append(argv)

        class R:
            returncode = 1 if "stock_fetch.py" in " ".join(argv) else 0
            stdout, stderr = "", "boom"
        return R()

    monkeypatch.setattr(pipeline.subprocess, "run", fake_run)
    monkeypatch.setattr(pipeline, "opend_alive", lambda *a, **k: True)
    monkeypatch.setattr(pipeline, "handle_opend_recovered", lambda send: False)
    rc = pipeline.main(["--mode", "briefing", "--read-only", "--json"])
    assert rc == 1
    assert not any("portfolio_brief.py" in " ".join(c) for c in seen), \
        "broker 刷新失败后还跑 brief = 拿旧报价当今天的"


# ── ET 日历日口径 (由 pipeline --read-only 暴露) ─────────
#
# 三个落库脚本用 date.today() = 系统本地 (SGT)。定时任务在 SGT 18:30 跑, 那一刻
# SGT 日 == ET 日, 所以三个月来每次都碰巧对。换个时点跑 (人工预览 / --read-only)
# SGT 已是明天 → date key 比 ET 交易日晚一天, 且幂等检查 `date == today` 同时失效
# → 同一 ET 交易日写两行。IV 序列不可重拉, 污染永久。

def test_no_local_date_in_ledger_writers():
    """凡是写 date key 进 jsonl 账本的脚本, 一律不许用 date.today()。"""
    import re
    root = Path(__file__).resolve().parents[2] / "quant"
    writers = ("data/nav_snapshot.py", "data/iv_logger.py", "data/skhy_premium.py",
               "decision/exit_guard.py", "decision/entry_guard.py", "decision/level_map.py")
    missing = [n for n in writers if not (root / n).exists()]
    assert not missing, f"守卫指向了不存在的文件 (移动后没同步?): {missing}"
    offenders = []
    for name in writers:
        for i, line in enumerate((root / name).read_text().splitlines(), 1):
            if re.search(r"(?<!fromisoformat\()\bdate\.today\(\)", line) \
                    and not line.strip().startswith("#"):
                offenders.append(f"{name}:{i}: {line.strip()}")
    assert not offenders, "用 quant.core.timeutil.et_today() 替代:\n" + "\n".join(offenders)


def test_et_today_is_not_local_date(monkeypatch):
    """守住 et_today 本身: SGT 领先 ET 时两者必须不同。"""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    import quant.core.timeutil as tu
    # ET  13:41 = SGT  01:41 —— 正是暴露 bug 的那个时点
    et = datetime(2026, 7, 28, 13, 41, tzinfo=ZoneInfo("America/New_York"))
    assert et.date().isoformat() == "2026-07-28"
    assert et.astimezone(ZoneInfo("Asia/Singapore")).date().isoformat() == "2026-07-29"
    assert tu.et_today() == datetime.now(tu.ET).date().isoformat()


# ── 未完成 bar 不许当收盘 (ET 13:56 源案例) ──────────────
#
# 盘中跑一次 pipeline: kline_cache 把当日进行中的 bar (C=166.41 = 那一刻现价)
# 落进缓存, regime_transition 拿它当收盘, 判 "收盘 166.41 < 卡片 stop 176",
# **永久**把 NBIS 卡片降一档 + 写 regime_history。那根 bar 当天还剩两小时。
# 此前唯一的保护是"定时任务恰好在 ET 盘前跑" —— 时间巧合, 不是机制。

def test_et_session_state_boundaries():
    from datetime import datetime
    import quant.core.timeutil as tu

    def at(h, m, day=28):          #  是周二
        return tu.et_session_state(datetime(2026, 7, day, h, m, tzinfo=tu.ET))

    assert at(6, 30) == "pre"       # 定时 briefing 跑的时点
    assert at(9, 29) == "pre"
    assert at(9, 30) == "open"
    assert at(13, 56) == "open"     # 事故发生的那一刻
    assert at(15, 59) == "open"
    assert at(16, 0) == "closed"
    assert at(20, 0) == "closed"
    assert at(12, 0, day=26) == "weekend"   #  周日


def test_session_open_only_when_open():
    from datetime import datetime
    import quant.core.timeutil as tu
    assert tu.et_session_open(datetime(2026, 7, 28, 13, 56, tzinfo=tu.ET))
    assert not tu.et_session_open(datetime(2026, 7, 28, 6, 30, tzinfo=tu.ET))
    assert not tu.et_session_open(datetime(2026, 7, 28, 16, 30, tzinfo=tu.ET))


def test_kline_cache_drops_todays_unfinished_bar(monkeypatch, tmp_path):
    """缓存的契约是"已完成的日线"。盘中当日那根必须丢。"""
    from quant.data import kline_cache as kc
    monkeypatch.setattr(kc, "et_today", lambda: "2026-07-28")
    monkeypatch.setattr(kc, "_et_hhmm", lambda: "13:56")   # 盘中 (NBIS 7/28 案的时刻)
    monkeypatch.setattr(kc, "KLINE_CACHE_DIR", tmp_path)
    monkeypatch.setattr(kc, "get_kline", lambda *a, **k: [
        {"time": "2026-07-27 00:00:00", "open": 1, "high": 2, "low": 1, "close": 187.88},
        {"time": "2026-07-28 00:00:00", "open": 1, "high": 2, "low": 1, "close": 166.41},
    ])
    n = kc.update_ticker("NBIS", 365)
    import json as _j
    rows = [_j.loads(x) for x in (tmp_path / "NBIS.jsonl").read_text().splitlines()]
    assert n == 1 and len(rows) == 1
    assert rows[-1]["time"][:10] == "2026-07-27", "当日未完成 bar 混进了缓存"


def test_kline_cache_keeps_todays_bar_after_close(monkeypatch, tmp_path):
    from quant.data import kline_cache as kc
    monkeypatch.setattr(kc, "et_today", lambda: "2026-07-28")
    monkeypatch.setattr(kc, "_et_hhmm", lambda: "16:10")   # postclose 时点, 正股已定型
    monkeypatch.setattr(kc, "KLINE_CACHE_DIR", tmp_path)
    monkeypatch.setattr(kc, "get_kline", lambda *a, **k: [
        {"time": "2026-07-28 00:00:00", "open": 1, "high": 2, "low": 1, "close": 166.41},
    ])
    assert kc.update_ticker("NBIS", 365) == 1


def test_eod_ledgers_skipped_while_session_open(monkeypatch):
    """IV/NAV/溢价 每交易日一行。盘中写 = 把盘中时刻当成这一天的记录。"""
    calls = []

    def fake_run(argv, **kw):
        calls.append(" ".join(argv))

        class R:
            returncode, stdout, stderr = 0, "", ""
        return R()

    monkeypatch.setattr(pipeline.subprocess, "run", fake_run)
    monkeypatch.setattr(pipeline, "opend_alive", lambda *a, **k: True)
    monkeypatch.setattr(pipeline, "et_session_state", lambda: "open")
    pipeline.main(["--mode", "briefing", "--read-only", "--json"])
    joined = " ".join(calls)
    for banned in ("iv_logger.py", "nav_snapshot.py", "skhy_premium.py"):
        assert banned not in joined, f"盘中不该跑 {banned}"
    # 非 eod_only 的照跑 —— kline_cache 自己会丢当日 bar, regime 只读已完成收盘
    assert "kline_cache.py" in joined and "portfolio_brief.py" in joined


def test_eod_ledgers_run_premarket(monkeypatch):
    calls = []

    def fake_run(argv, **kw):
        calls.append(" ".join(argv))

        class R:
            returncode, stdout, stderr = 0, "", ""
        return R()

    monkeypatch.setattr(pipeline.subprocess, "run", fake_run)
    monkeypatch.setattr(pipeline, "opend_alive", lambda *a, **k: True)
    monkeypatch.setattr(pipeline, "et_session_state", lambda: "pre")  # 定时任务时点
    pipeline.main(["--mode", "briefing", "--read-only", "--json"])
    joined = " ".join(calls)
    for needed in ("iv_logger.py", "nav_snapshot.py", "skhy_premium.py"):
        assert needed in joined, f"盘前必须跑 {needed}"


def test_watchlist_sync_runs_both_modes_after_positions():
    """已持仓自动移出分组: 判据 broker.qty≠0, 必须在 positions 刷新
    之后 — 排前面 = 拿旧 qty 判候选资格; 进 heartbeat = 成交后 ≤6h 清理, 不等 18:30。"""
    for mode in (pipeline.HEARTBEAT, pipeline.BRIEFING):
        k = keys(mode)
        assert "watchlist_sync" in k, mode
        assert k.index("watchlist_sync") > k.index("positions"), mode
        assert k.index("watchlist_sync") > k.index("fills"), mode


def test_brief_alerts_push_to_tg_before_effector_stages(monkeypatch):
    """Canonical brief alerts are delivered before external effectors."""
    calls = []

    def fake_run(argv, **kw):
        joined = " ".join(str(x) for x in argv)
        calls.append(joined)

        class R:
            returncode, stderr = 0, ""
            stdout = (json.dumps({"alerts": ["🚫 TST 破 z2 下沿"],
                                  "fired_cards": ["TST | 状态卡原文"]})
                      if "portfolio_brief.py" in joined else "")
        return R()

    monkeypatch.setattr(pipeline.subprocess, "run", fake_run)
    monkeypatch.setattr(pipeline, "opend_alive", lambda *a, **k: True)
    monkeypatch.setattr(pipeline, "et_session_state", lambda: "closed")
    monkeypatch.setattr(pipeline, "stamp_heartbeat", lambda: None)
    monkeypatch.setattr(pipeline, "handle_opend_recovered", lambda send: False)

    pipeline.main(["--mode", "heartbeat", "--json"])
    tg_idx = [i for i, c in enumerate(calls) if "tg_notify.py" in c]
    rem_idx = [i for i, c in enumerate(calls) if "price_reminder_sync.py" in c]
    assert tg_idx and rem_idx, calls
    assert tg_idx[0] < rem_idx[0], "TG 推送必须先于画线"
    assert "状态卡原文" in calls[tg_idx[0]] and "破 z2 下沿" in calls[tg_idx[0]], \
        "推的必须是 fired_cards+alerts 原文"


def test_postclose_pushes_stop_alert_before_regime_rewrites_the_card(monkeypatch):
    """Post-close STOP delivery precedes a transition that replaces the old stop."""
    calls = []

    def fake_run(argv, **kw):
        joined = " ".join(str(x) for x in argv)
        calls.append(joined)

        class R:
            returncode, stderr = 0, ""
            stdout = (json.dumps({"alerts": ["🚨 TST STOP: 收 94 < stop 95"],
                                  "fired_cards": []})
                      if "portfolio_brief.py" in joined else "")
        return R()

    monkeypatch.setattr(pipeline.subprocess, "run", fake_run)
    monkeypatch.setattr(pipeline, "opend_alive", lambda *a, **k: True)
    monkeypatch.setattr(pipeline, "et_session_state", lambda: "closed")  # ET 16:10
    monkeypatch.setattr(pipeline, "handle_opend_recovered", lambda send: False)

    pipeline.main(["--mode", "postclose", "--json"])
    tg = [i for i, c in enumerate(calls) if "tg_notify.py" in c]
    regime = [i for i, c in enumerate(calls) if "regime_transition.py" in c]
    assert tg and regime, calls
    assert tg[0] < regime[0], "STOP 告警必须先于 regime 转移出门"
    assert "STOP" in calls[tg[0]]


def test_brief_alerts_not_pushed_in_read_only(monkeypatch):
    calls = []

    def fake_run(argv, **kw):
        joined = " ".join(str(x) for x in argv)
        calls.append(joined)

        class R:
            returncode, stderr = 0, ""
            stdout = (json.dumps({"alerts": ["x"], "fired_cards": []})
                      if "portfolio_brief.py" in joined else "")
        return R()

    monkeypatch.setattr(pipeline.subprocess, "run", fake_run)
    monkeypatch.setattr(pipeline, "opend_alive", lambda *a, **k: True)
    monkeypatch.setattr(pipeline, "et_session_state", lambda: "closed")
    monkeypatch.setattr(pipeline, "handle_opend_recovered", lambda send: False)

    pipeline.main(["--mode", "heartbeat", "--read-only", "--json"])
    assert not any("tg_notify.py" in c for c in calls), "read-only 不消耗告警通道"


def test_exit_score_after_reconcile_before_watchlist_sync():
    """结构性退出自动评分 (): 判据是 close_reconcile 落的 exit_info →
    必须在 reconcile 之后; watchlist_sync 之前 —— 促升会覆盖 closed 卡,
    覆盖前评完 (同一轮内 exit_info 不会先丢)。postclose 不跑它: 盘后卖出
    (COHR 18:03 / NBIS 20:02 ET) 发生在 postclose (≈16:10 ET) 之后, 次日
    heartbeat/briefing 的 reconcile→exit_score 链才是抓它的那一轮。"""
    for mode in (pipeline.HEARTBEAT, pipeline.BRIEFING):
        k = keys(mode)
        assert k.index("exit_score") > k.index("reconcile"), mode
        assert k.index("exit_score") < k.index("watchlist_sync"), mode
    assert "exit_score" not in keys(pipeline.POSTCLOSE)
