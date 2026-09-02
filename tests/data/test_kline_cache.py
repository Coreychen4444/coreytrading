"""kline_cache: 当日 bar 定型时刻守卫。

缓存契约 = 只含**已完成**日线 (下游 regime_transition / exit_guard / entry_guard
gate 计数 / zone staleness 都把 rows[-1] 读作昨收)。守卫演化史, 两次都是真事故:

  v1 (): et_session_open() —  ET 13:56 盘中手工跑把 NBIS
     当日进行中 bar 落库, regime_transition 拿它当收盘永久降档。
  v2 (): 定型时刻 cutoff — v1 只盖 09:30-16:00 常规时段, 而 ^VIX 从
     03:15 ET 就播值: 8/5 06:31 ET (盘前) briefing 轮把 VIX 当日读数 16.35 当日线
     落库。NDX/SPX/SOX 没中招只因 Yahoo 盘前不播股指当日 row —— 运气不是机制,
     与同日修的夜盘基准案同形状 (守卫只盖一个窗口, 失效存在于更宽的窗口)。
"""
import json

import pytest

from quant.data import kline_cache as kc


@pytest.fixture
def env(tmp_path, monkeypatch):
    """隔离缓存目录 + 注入 bar 源与 ET 时刻。"""
    def _apply(ticker, bars, now_hhmm, cached=()):
        kdir = tmp_path / "klines"
        kdir.mkdir(exist_ok=True)
        path = kdir / f"{ticker}.jsonl"
        if cached:
            path.write_text("\n".join(json.dumps(b) for b in cached) + "\n",
                            encoding="utf-8")
        monkeypatch.setattr(kc, "KLINE_CACHE_DIR", kdir)
        monkeypatch.setattr(kc, "_et_hhmm", lambda: now_hhmm)
        monkeypatch.setattr(kc, "_yahoo_daily", lambda sym, start, **kw: bars)
        monkeypatch.setattr(kc, "get_kline",
                            lambda t, ktype, start, end: bars)
        return path
    return _apply


def _bar(day, close):
    return {"time": f"{day} 00:00:00", "open": close, "high": close,
            "low": close, "close": close, "volume": 0, "turnover": 0.0}


def _dates(path):
    return [json.loads(l)["time"][:10] for l in path.read_text().splitlines() if l.strip()]


TODAY = kc.et_today()


def test_premarket_drops_vix_partial_bar(env):
    """8/5 06:31 ET 回归: 盘前跑, VIX 的当日进行中读数不得入库。"""
    path = env("VIX", [_bar("2026-08-04", 15.73), _bar(TODAY, 16.35)], "06:31")
    n = kc.update_ticker("VIX", 365)

    assert n == 1
    assert _dates(path) == ["2026-08-04"]


def test_premarket_drops_stock_partial_bar_too(env):
    """v1 守卫在盘前是关的 (et_session_open()=False) — 新判据对正股同样生效。"""
    path = env("NBIS", [_bar(TODAY, 227.0)], "06:31")
    assert kc.update_ticker("NBIS", 365) == 0
    assert not path.exists() or _dates(path) == []


def test_midsession_still_drops(env):
    """NBIS  ET 13:56 回归 (v1 的原始案例) 在 v2 下必须仍然成立。"""
    path = env("NBIS", [_bar(TODAY, 166.41)], "13:56")
    assert kc.update_ticker("NBIS", 365) == 0


def test_postclose_keeps_stock_bar(env):
    """postclose 16:10 ET 依赖当日正股收盘 — cutoff 16:05 必须已放行。"""
    path = env("INTC", [_bar(TODAY, 100.94)], "16:10")
    assert kc.update_ticker("INTC", 365) == 1
    assert _dates(path) == [TODAY]


def test_postclose_still_drops_vix_before_settle(env):
    """VIX 16:15 ET 才结算 — 16:10 那轮它的当日 bar 还是进行时。"""
    path = env("VIX", [_bar(TODAY, 16.1)], "16:10")
    assert kc.update_ticker("VIX", 365) == 0


def test_vix_bar_admitted_after_settle(env):
    path = env("VIX", [_bar(TODAY, 16.1)], "16:25")
    assert kc.update_ticker("VIX", 365) == 1
    assert _dates(path) == [TODAY]


def test_historical_bars_pass_at_any_hour(env):
    """次晨补昨日 bar (briefing 轮的正常路径) 不受定型时刻影响。"""
    path = env("VIX", [_bar("2026-08-04", 15.73)], "06:31",
               cached=[_bar("2026-08-03", 15.86)])
    assert kc.update_ticker("VIX", 365) == 1
    assert _dates(path) == ["2026-08-03", "2026-08-04"]


# ── 情报层收拢退役棘轮 (, 用户: 本仓只做技术分析+风控) ──


def test_macro_observation_tickers_stay_retired():
    """宏观三票 (TNX/DXY/BTC, 加) 随情报层收拢 ai-news-radar
    整体退役 — 宏观读数不再由本仓采集, UTC 日历守卫 YH_UTC_DATED 随 BTC 一并删除。
    回到 INDEX_YH = 有人在把宏观情报职能加回本仓, 先读
    intel-consolidation directive (strategy/proposals/) 再动。"""
    assert not {"TNX", "DXY", "BTC"} & set(kc.INDEX_YH)
    assert not hasattr(kc, "YH_UTC_DATED")
    assert "DXY" not in kc.YH_CUTOFF


# ── default_universe: 活仓期权合约进刷新集 () ─────────────

def test_default_universe_includes_live_option_contract(tmp_path, monkeypatch):
    """期权卡按 premium 口径画线后, gate 阶梯表读合约自己的日线。只折
    underlying = 合约缓存永不刷新 → 首日"无缓存", 手拉后静默用旧 close
    (GOOGL270115C390000 源案例)。死合约 (qty 0) 不拉 —— 到期退市只收 API 错。"""
    import json
    from quant.data import kline_cache as kc
    pos = tmp_path / "p.json"
    wl = tmp_path / "w.json"
    pos.write_text(json.dumps({"tickers": {
        "GOOGL270115C390000": {"broker": {"qty": 1}},   # 活仓合约 → 进
        "NOK270115C15000": {"broker": {"qty": 0}},      # 平仓合约 → 只折 underlying
        "AAOI": {"broker": {"qty": 4}},
    }}))
    wl.write_text(json.dumps({"tickers": {"COHR": {"broker": {"qty": 0}}}}))
    monkeypatch.setattr(kc, "POSITIONS_PATH", pos)
    monkeypatch.setattr(kc, "WATCHLIST_PATH", wl)
    u = kc.default_universe()
    assert "GOOGL270115C390000" in u, "活仓合约没进刷新集 — 报表将静默用旧 close"
    assert "NOK270115C15000" not in u, "死合约进了刷新集 — 每轮白收一条 API 错"
    assert {"GOOGL", "NOK", "AAOI", "COHR"} <= set(u)
