"""异动告警: 阈值用标的自身的 ATR, dedup key 分方向。

两处都是  定的 (用户), 各修一个真实漏报:

1. 固定 5% 在不同标的上不是同一个事件。实测 32 票: SMU 68.4% 的交易日都 >5%
   (纯噪音), 而 QQQ/SPY 在 274 根里一次都没到过 5%, AAPL 0.5% —— 低波动持仓
   等于无人监控, 高波动持仓等于警报常亮。换成 1xATR14 后全池收进 7.9%-32.1%。

2. dedup key 不分方向: SPCX  白天 +9.43% 合法触发并 latch, 当晚自家
   财报 -7.5% 时同一个 key 仍"持续成立" → should_emit 返回 False, 夜盘那记跳空
   一声没有。那晚真正让它逃过去的, 反而是 change_pct 用错基准算出的假 +1.01%
   (把 latch 清掉了) —— 两个 bug 互相抵消, 不是任何一个被修好了。
"""
import json
import time

import pytest

from quant.report import portfolio_brief as pb


def _bars(close: float, atr_frac: float, n: int = 40):
    """每根 H/L 对称张开 close*atr_frac → ATR 恒等于 close*atr_frac。
    (TR = max(H-L, |H-prevC|, |L-prevC|) = H-L, 因为 close 不动。)"""
    half = close * atr_frac / 2
    return [{"time": f"2026-06-{i % 28 + 1:02d} 00:00:00", "open": close,
             "high": close + half, "low": close - half, "close": close}
            for i in range(n)]


def _position(qty=2.0, price=100.0, change_pct=0.0):
    return {
        "instrument": {"type": "stock", "name": "t"},
        "status": "active",
        "broker": {"qty": qty, "current_price": price, "change_pct": change_pct},
        "agent": {"thesis": {"summary": "t"}, "action": {"intent": "hold"},
                  "zones": {}},
    }


@pytest.fixture
def brief(tmp_path, monkeypatch):
    """Build a brief off a throwaway positions.json + OHLC kline cache.

    load_rows_from_state binds its path as a default arg, so it must be passed
    explicitly — monkeypatching the module global only reaches the readers that
    look it up at call time (load_zone_maps_from_state).
    """
    def _run(tickers, klines=None):
        path = tmp_path / "positions.json"
        path.write_text(json.dumps({"tickers": tickers}), encoding="utf-8")
        monkeypatch.setattr(pb, "STATE_POSITIONS_PATH", path)
        # 隔离 watchlist —— wl_zone_touch (入场侧补盲) 读真 watchlist
        # 的报价快照, 以前碰巧无候选在带内;  COHR -11.3% 砸进 z2 后
        # 泄漏必现 (与下面 exit_guard 同一类洞: 真数据一动, 单测跟着市场红绿)
        wl = tmp_path / "watchlist.json"
        wl.write_text(json.dumps({"tickers": {}}), encoding="utf-8")
        monkeypatch.setattr(pb, "STATE_WATCHLIST_PATH", wl)
        monkeypatch.setattr(pb, "lot_drift", lambda *a, **k: [])
        # 隔离财报日历 — build_brief 读真 market/earnings_calendar.json; 本模块的
        # `assert out["alerts"] == []` 是**全局空**断言, 日历一缺失/一过期就会多出
        # 一条 INFRA:earnings_calendar_stale, 与异动阈值毫无关系 (同上面两条:
        # 真数据一动, 单测跟着市场红绿)。 五世界扫盘抓出。
        monkeypatch.setattr(pb.earnings, "load_calendar",
                            lambda *a, **k: {"as_of": pb.et_today(), "tickers": {}})
        # 隔离 exit_guard — build_brief 内部 import 并读真实 positions/exit_guards,
        # 以前碰巧真书上无告警触发; opt overlay 接线后真 GOOGL S1 常触发, 泄漏必现
        from quant.decision import exit_guard as _eg
        monkeypatch.setattr(_eg, "run_checks", lambda **k: {
            "summary_lines": [], "account": None, "guards": {},
            "one_shot": [], "persistent": []})

        events = tmp_path / "events"
        events.mkdir(exist_ok=True)
        monkeypatch.setattr(pb, "EVENTS_DIR", events)
        nav = tmp_path / "nav_history.jsonl"
        nav.write_text("", encoding="utf-8")
        monkeypatch.setattr(pb, "NAV_HISTORY_PATH", nav)

        kdir = tmp_path / "klines"
        kdir.mkdir(exist_ok=True)
        for ticker, bars in (klines or {}).items():
            (kdir / f"{ticker}.jsonl").write_text(
                "\n".join(json.dumps(b) for b in bars), encoding="utf-8")
        monkeypatch.setattr(pb, "KLINE_CACHE_DIR", kdir)

        rows, col = pb.load_rows_from_state(path)
        return pb.build_brief(rows, col, mutate_state=False)
    return _run


# ── 阈值 = 标的自身的 ATR ──────────────────────────────────────

def test_threshold_is_the_tickers_own_atr(brief, tmp_path, monkeypatch):
    brief({}, {"NBIS": _bars(200.0, 0.08)})
    thr, basis = pb.move_threshold("NBIS")

    assert thr == pytest.approx(8.0, abs=0.01)      # 1N of an 8%-ATR name
    assert "ATR14" in basis


def test_threshold_falls_back_out_loud_without_enough_bars(brief):
    brief({}, {"NBIS": _bars(200.0, 0.08, n=5)})
    thr, basis = pb.move_threshold("NBIS")

    assert thr == pb.MOVE_PCT_FALLBACK
    assert "兜底" in basis          # 静默降级 = 又一个事后才发现的失效


def test_threshold_falls_back_when_bars_have_no_high_low(brief):
    """close-only 缓存算不出 true range; 拿 high=0 硬算会得到 ≈ 股价的假 ATR。"""
    bars = [{"time": f"2026-06-{i + 1:02d}", "close": 100.0} for i in range(40)]
    brief({}, {"NBIS": bars})

    assert pb.move_threshold("NBIS")[0] == pb.MOVE_PCT_FALLBACK


def test_low_vol_holding_is_no_longer_invisible(brief):
    """ATR 1% 的票跌 3% 是大事, 旧的固定 5% 永远不响 (AAPL 实测 0.5% 的日子)。"""
    out = brief({"MSFT": _position(price=100.0, change_pct=-3.0)},
                {"MSFT": _bars(100.0, 0.01)})

    assert any("MSFT" in a for a in out["alerts"])
    assert any(t["type"] == "adverse_move_down" for t in out["triggers"])


def test_high_vol_holding_stops_crying_wolf(brief):
    """ATR 10% 的票动 6% 是日常, 旧规则每次都响 (SMU 三天两响)。"""
    out = brief({"SMU": _position(price=100.0, change_pct=-6.0)},
                {"SMU": _bars(100.0, 0.10)})

    assert out["alerts"] == []
    assert out["triggers"] == []


def test_alert_carries_the_threshold_that_fired_it(brief):
    """尺变成 per-ticker 之后, 不播报阈值就没人能判断为什么这条响了那条没响。"""
    out = brief({"MSFT": _position(price=100.0, change_pct=-3.0)},
                {"MSFT": _bars(100.0, 0.01)})

    assert "[阈值 1.0% = 1N ATR14]" in out["alerts"][0]
    assert out["triggers"][0]["threshold_pct"] == pytest.approx(1.0, abs=0.01)


def test_option_keeps_the_fixed_band(brief):
    """期权链没有 kline 缓存 → 没有 ATR 可用, 固定 ±20% 保留。"""
    assert pb.OPTION_MOVE_PCT == 20.0
    thr, basis = pb.move_threshold("NBIS270115C200000")

    assert thr == pb.MOVE_PCT_FALLBACK      # 期权路径不走 move_threshold, 但即便
    assert "兜底" in basis                   # 误走也不会得到一个假 ATR


# ── dedup key 分方向 ──────────────────────────────────────────

def test_reversal_alerts_again_instead_of_being_deduped(brief):
    """SPCX 2026-08-04 的形状: 白天涨停式上涨 latch 住, 当晚财报砸下来。"""
    now = int(time.time())
    up = pb.alert_key("SPCX", pb.move_trigger_type(+9.43))
    state = {"alerts": {up: {"active": True, "lastAlertTs": now}}}

    # 同向再来一次 → 24h 内不重复 (原行为, 保留)
    assert pb.should_emit(state, "SPCX", pb.move_trigger_type(+9.5), now + 3600) is False
    # 反向 → 是新事件, 必须发得出去 (旧代码两者共用 "adverse_move" 一个 key)
    assert pb.should_emit(state, "SPCX", pb.move_trigger_type(-7.46), now + 3600) is True


def test_direction_lands_in_the_key(brief):
    assert pb.move_trigger_type(7.0) == "adverse_move_up"
    assert pb.move_trigger_type(-7.0) == "adverse_move_down"
    assert pb.alert_key("SPCX", pb.move_trigger_type(-7.0)) == "SPCX:adverse_move_down"


def test_headlines_covers_every_direction(brief):
    """HEADLINES 漏一个 type → fired_cards 整个空掉, 而 heartbeat 推的就是它。
    改名/加方向时这条会红。"""
    for pct in (1.0, -1.0):
        assert pb.HEADLINES.get(pb.move_trigger_type(pct))


def test_up_move_still_produces_a_fired_card(brief):
    out = brief({"MSFT": _position(price=100.0, change_pct=+3.0)},
                {"MSFT": _bars(100.0, 0.01)})

    assert out["triggers"][0]["type"] == "adverse_move_up"
    assert out["fired_cards"] and "⚠️" in out["fired_cards"][0]


# ── 大盘补充: 从死代码变成会触发的检查 ────────────────────────

def _daily(closes):
    return [{"time": f"2026-08-{i + 1:02d} 00:00:00", "open": c, "high": c,
             "low": c, "close": c} for i, c in enumerate(closes)]


def test_index_risk_fires_on_a_2pct_drop(brief):
    brief({}, {"QQQ": _daily([700.0, 680.0])})      # -2.86%
    hits = pb.index_risk()

    assert [h["ticker"] for h in hits] == ["QQQ"]
    assert hits[0]["pct"] == pytest.approx(-2.857, abs=0.01)
    assert hits[0]["date"] == "2026-08-02"


def test_index_risk_silent_within_threshold(brief):
    brief({}, {"QQQ": _daily([700.0, 690.0]),       # -1.43%
               "SPY": _daily([757.0, 780.0])})      # up
    assert pb.index_risk() == []


def test_index_risk_reaches_the_alert_list(brief):
    """2026-08-05 之前这条检查在持仓循环里, 而 QQQ 不是持仓 —— 规则文档写了
    整整半年的"大盘补充", 代码里没有任何路径能触发它。"""
    out = brief({"MSFT": _position(price=100.0, change_pct=0.0)},
                {"MSFT": _bars(100.0, 0.05), "SPY": _daily([757.0, 730.0])})

    assert any("SPY market risk" in a for a in out["alerts"])
    assert any(t["type"] == "index_below_minus_2" for t in out["triggers"])


def test_index_tickers_survive_pruning(brief):
    """QQQ/SPY 不在持仓里 → 忘了放进 universe 的话, prune_dead_keys 每轮都会
    删掉它们的 latch, 大盘一直跌就一直重发, 24h 去重形同虚设。"""
    out = brief({"MSFT": _position(price=100.0, change_pct=0.0)},
                {"MSFT": _bars(100.0, 0.05), "SPY": _daily([757.0, 730.0])})
    assert out["triggers"]                       # 先确认真的触发了

    state = {"alerts": {"SPY:index_below_minus_2": {"active": True, "lastAlertTs": 1}}}
    pb.prune_dead_keys(state, {"MSFT"} | set(pb.INDEX_TICKERS))

    assert "SPY:index_below_minus_2" in state["alerts"]


def test_renamed_trigger_keys_are_pruned(brief):
    """老的 "TICKER:adverse_move" 按 ticker 判永远清不掉 (ticker 还在持仓里)。"""
    state = {"alerts": {"NBIS:adverse_move": {"active": True, "lastAlertTs": 1},
                        "NBIS:adverse_move_down": {"active": False, "lastAlertTs": 2},
                        "INFRA:lot_drift": {"active": False, "lastAlertTs": 3}}}
    removed = pb.prune_dead_keys(state, {"NBIS"})

    assert removed == 1
    assert set(state["alerts"]) == {"NBIS:adverse_move_down", "INFRA:lot_drift"}
