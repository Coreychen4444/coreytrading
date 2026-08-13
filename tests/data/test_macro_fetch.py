"""macro_fetch live 快照: prev_close 语义 + ^TNX 双口径 + 渲染行。

 立项案: briefing 的隔夜宏观行抄 WebSearch 旧文, USDJPY 写成
">163 (40 年新高)" 而实时 157.6 — 一周前的叙事冒充实时。修法 = 数字改由
Yahoo chart live 快照代码渲染, 本文件钉住两处实测踩过的坑:

  - chartPreviousClose 是 range 起点前的收盘, 不是昨收 (CL=F 实测拿它算出
    -9.9% 而真实 d/d +0.7%) → prev 必须取序列里最近一个非当日 bar;
  - ^TNX 存在 yield×10 与直接收益率两种口径 (老约定 46.3, 当前源 4.627),
    写死除以 10 会输出 0.46% — 荒谬但不报错, 最危险的那种。
"""
import pytest

from quant.data import macro_fetch as mf


def _inject(monkeypatch, meta, closes):
    monkeypatch.setattr(mf, "_yahoo_chart", lambda sym, timeout=15: (meta, closes))


# live bar 的 UTC 日期必须与 closes 尾 bar 对齐 — prev 的判据就是 "date != live_date"。
from datetime import datetime, timezone
LIVE_TS = int(datetime(2026, 8, 5, 14, 0, tzinfo=timezone.utc).timestamp())


def test_prev_close_is_last_completed_session_not_chart_anchor(monkeypatch):
    """CL=F 实测形状: chartPreviousClose 84.67 (7/31), 真昨收 75.77 (8/4)。"""
    _inject(monkeypatch,
            {"regularMarketPrice": 76.29, "regularMarketTime": LIVE_TS,
             "chartPreviousClose": 84.67},
            [("2026-07-31", 84.67), ("2026-08-03", 80.34),
             ("2026-08-04", 75.77), ("2026-08-05", 76.29)])
    out = mf.fetch_live_macro({"WTI": "CL=F"})

    assert out["WTI"]["prev_close"] == pytest.approx(75.77)
    assert out["WTI"]["chg_pct"] == pytest.approx(0.69, abs=0.02)   # 不是 -9.87


def test_prev_skips_the_live_bar_itself(monkeypatch):
    """USDJPY 形状: 序列尾 bar 就是当日进行中读数, prev 要跳过它。"""
    _inject(monkeypatch,
            {"regularMarketPrice": 157.588, "regularMarketTime": LIVE_TS,
             "chartPreviousClose": 163.3},
            [("2026-07-30", 160.18), ("2026-08-03", 157.53),
             ("2026-08-05", 157.59)])
    out = mf.fetch_live_macro({"USDJPY": "USDJPY=X"})

    assert out["USDJPY"]["prev_close"] == pytest.approx(157.53)
    assert abs(out["USDJPY"]["chg_pct"]) < 1        # 不是 vs 163.3 的 -3.5%


def test_tnx_direct_yield_passes_through(monkeypatch):
    """当前源: ^TNX 直接给收益率 4.627 → 不许再除 10 (0.46% 案)。"""
    _inject(monkeypatch,
            {"regularMarketPrice": 4.627, "regularMarketTime": LIVE_TS},
            [("2026-08-04", 4.63)])
    out = mf.fetch_live_macro({"US10Y": "^TNX"})

    assert out["US10Y"]["price"] == pytest.approx(4.627)


def test_tnx_times_ten_convention_is_scaled(monkeypatch):
    """老约定: ^TNX = yield×10 (46.27) → 除 10。>20 是两口径的判别式。"""
    _inject(monkeypatch,
            {"regularMarketPrice": 46.27, "regularMarketTime": LIVE_TS},
            [("2026-08-04", 46.3)])
    out = mf.fetch_live_macro({"US10Y": "^TNX"})

    assert out["US10Y"]["price"] == pytest.approx(4.627)
    assert out["US10Y"]["prev_close"] == pytest.approx(4.63)


def test_one_symbol_failing_does_not_kill_the_rest(monkeypatch):
    calls = {}
    def chart(sym, timeout=15):
        if sym == "DX-Y.NYB":
            raise OSError("timeout")
        return ({"regularMarketPrice": 157.6, "regularMarketTime": LIVE_TS},
                [("2026-08-04", 157.5)])
    monkeypatch.setattr(mf, "_yahoo_chart", chart)
    out = mf.fetch_live_macro({"USDJPY": "USDJPY=X", "DXY": "DX-Y.NYB"})

    assert "USDJPY" in out and "DXY" not in out


def test_render_line_carries_the_numbers(monkeypatch):
    line = mf.render_live_line({
        "US10Y": {"price": 4.63, "as_of_et": "08-05 07:15 ET"},
        "USDJPY": {"price": 157.6, "chg_pct": 0.04},
        "VIX": {"price": 16.58},
    })
    assert "10Y 4.63%" in line and "USDJPY 157.60" in line and "VIX 16.58" in line


def test_render_line_says_so_when_empty():
    """fetch 全挂时必须说出口 — 静默空行会被旧文填坑 (>163 案的形状)。"""
    assert "失败" in mf.render_live_line({})
