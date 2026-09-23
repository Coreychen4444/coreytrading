"""每日判读的不变量 —— **核心目的的守卫**。

目的：落地过程中不跑偏，需要把核心目的钉起来。
收窄：仅关注这两种形态已形成或即将形成的标的，未形成的不需要关注。
钉的方式只能是**会失败的东西**：文档里的目的三个月后就退化成装饰。

这里钉五件：
  1. 核心目的**逐字**在源码里（chart_read + setups；改掉它 = 这条红）
  2. 判读层**零副作用**（不写任何 state）
  3. 判读层**不重算授权**（不 import sizing / entry_guard / exit_guard）
  4. 推送 = 两种模式清单：只列已形成 / 即将形成，不列别的形态，不带授权层字段，不静默丢票
  5. 详情页保留六步事实 + 短期道氏视图，形态段只剩两种模式
"""
import datetime as dt
import re
from pathlib import Path

import pytest

from quant.core import setups as S
from quant.ops import tg_notify as tg
from quant.report import chart_read as CR

SRC_READ = Path(CR.__file__).read_text()
SRC_SETUPS = Path(S.__file__).read_text()


# ══════════════════════════════════════════════════════════════
# 1. 核心目的钉在源码里
# ══════════════════════════════════════════════════════════════

PURPOSE_FRAGMENTS = [
    "每天收盘后",
    "分析出当前所处结构及形态",
    "潜在的交易机会",
]
NARROWING_FRAGMENTS = [
    "仅关注这两种形态已形成或即将形成的标的",
    "未形成的不需要关注",
]


@pytest.mark.parametrize("frag", PURPOSE_FRAGMENTS + NARROWING_FRAGMENTS)
def test_core_purpose_verbatim_in_both_modules(frag):
    """核心目的与收窄**逐字**写在两个模块顶部。

    为什么两个都要：`setups` 是判别式真源、`chart_read` 是装配与输出面 ——
    将来改动最可能只碰其中一个，那时另一个的目的声明就是唯一的锚。
    """
    assert frag in SRC_READ, f"chart_read 丢了核心目的片段: {frag}"
    assert frag in SRC_SETUPS, f"setups 丢了核心目的片段: {frag}"


def test_pipeline_order_is_the_documented_chain():
    """六步链条按文档里的顺序，不许重排（重排 = 换了一套方法论还叫同一个名字）。"""
    assert "Context → Structure → Price Action → Volume → Indicators → Trade" in SRC_READ


def test_pattern_zoo_is_retired_from_the_daily_read():
    """Lo 形态族 / 倾向三栏已退出每日判读 —— chart_read 不再 import patterns；
    抄底判读也不再用道氏四步—— 数字口径见 tests/core/test_setups.py。"""
    code = "\n".join(ln for ln in SRC_READ.splitlines() if not ln.lstrip().startswith("#"))
    assert "import patterns" not in code and "patterns." not in code.replace("`core/patterns`", "")
    for gone in ("def posture(", "def opportunity(", "def pattern_hypotheses("):
        assert gone not in SRC_READ, f"{gone} 退役了，不该回流"


# ══════════════════════════════════════════════════════════════
# 2. 判读层零副作用（源码棘轮）
# ══════════════════════════════════════════════════════════════

WRITE_CALLS = ["atomic_write_json", "append_jsonl", "write_text", "write_bytes",
               ".dump(", "os.remove", "shutil."]


@pytest.mark.parametrize("call", WRITE_CALLS)
def test_read_layer_writes_nothing(call):
    """判读永远只读。写 state 的判读会变成一个没人知道在改交易状态的模块。"""
    assert call not in SRC_READ, f"判读层出现写操作: {call}"
    assert call not in SRC_SETUPS, f"判别式出现写操作: {call}"


FORBIDDEN_IMPORTS = ["quant.core.sizing", "quant.decision.entry_guard",
                     "quant.decision.exit_guard", "quant.sync."]


@pytest.mark.parametrize("mod", FORBIDDEN_IMPORTS)
def test_read_layer_never_recomputes_authorization(mod):
    """不 import 授权/仓位模块 —— 一旦 import 就会有人在这里算第二份 cap，
    而「实际能下多少」的单一真源是 `sizing.entry_cap`。判读层要做的是**指过去**，不是**再算一遍**。"""
    assert mod not in SRC_READ, f"判读层不该依赖 {mod}"
    assert mod.replace("quant.", "from .") not in SRC_SETUPS


# ══════════════════════════════════════════════════════════════
# 3. 端到端（合成数据，monkeypatch 掉磁盘与行情）
# ══════════════════════════════════════════════════════════════

def _bars(closes, start="2025-01-01"):
    d0 = dt.date.fromisoformat(start)
    out = []
    for i, c in enumerate(closes):
        out.append({"time": f"{(d0 + dt.timedelta(days=i)).isoformat()} 00:00:00",
                    "open": c, "high": c * 1.02, "low": c * 0.98, "close": c,
                    "volume": 1_000_000, "turnover": c * 1_000_000})
    return out


def _walk(n, seed=11, sigma=1.8):
    import random
    rnd = random.Random(seed)
    p, out = 100.0, []
    for _ in range(n):
        p *= 1 + rnd.gauss(0, sigma / 100)
        out.append(p)
    return out


CARD = {
    "bucket": "watchlist", "status": "active", "qty": 0,
    "agent": {
        "zones": {"accumulation_zone_1": [95, 100], "accumulation_zone_2": [88, 93],
                  "trim_zone": [120, 130], "no_chase_above": 115, "stop_level": 85},
        "current_regime": {"name": "常规参与", "cap_pct_nav": 0.4,
                           "since": "2026-08-01", "source": "card"},
        "next_regime": {"name": "R_突破_120", "entry_gate": {"level": 120.0, "days": 2,
                                                            "volume_ratio_min": 1.1}},
    },
}


@pytest.fixture(autouse=True)
def _no_disk_volume_leg(monkeypatch):
    """量腿 / 快车道读基准 kline 缓存（磁盘）—— 测试里一律替身。"""
    monkeypatch.setattr(CR, "volume_leg", lambda b, m, t=None, streak=1: (True, "量比(rel QQQ 1.00) 1.35"))
    monkeypatch.setattr(CR, "fastlane_state", lambda *a, **k: None)


@pytest.fixture
def report(monkeypatch):
    bars = _bars(_walk(320))
    monkeypatch.setattr(CR, "load_bars", lambda t, asof=None: (bars, None))
    monkeypatch.setattr(CR, "load_card", lambda t: CARD)
    monkeypatch.setattr(CR, "rel_volume", lambda b, t=None: (1.35, "量比(rel QQQ 1.00) 1.35", False))
    return CR.read("TEST")


def _bottom(stage="formed", **over):
    """一张抄底判读（bottom_block 的产物）—— 直接造字典，测的是渲染与排序。"""
    meta = S.STAGES[stage]
    base = {"stage": stage, "cn": meta["cn"], "formed": meta["formed"], "rank": meta["rank"],
            "listed": stage in S.LISTED_STAGES, "low": 194.0, "low_date": "2026-09-01",
            "hammer": False, "shadow": {"lower_pct": 62, "upper_pct": 10, "body_pct": 28, "close_pos": 0.86},
            "low_bar_high": 202.81, "above_low_bar_high": True, "low_bar_vol_ratio": 0.9,
            "held_days": 7, "signal": S.SIGNAL_HELD, "signal_date": "2026-09-02", "bars_since_signal": 6,
            "bounce_high": 254.74, "bounce_high_date": "2026-09-08", "bounce_pct": 0.153,
            "pullback_low": 223.64, "pullback_low_date": "2026-09-11", "higher_low": False,
            "higher_low_date": None, "bars_since_higher_low": None, "retest_frac": 0.52, "deep_retest": False,
            "lh": 228.5, "lh_date": "2026-08-27", "above_lh": False, "undercut_prev": None,
            "dd_at_low": -0.35, "dd_now": -0.25, "close": 224.55,
            "top": 280.83, "top_date": "2026-08-17", "decline_n": 4.12, "atr_at_low": 20.88,
            "dist": {"low": "-13.2%/1.6N", "bounce_high": "+13.4%/1.6N",
                     "pullback_low": "-0.4%/0.0N", "lh": "+1.8%/0.2N"},
            "probe": None, "weekly_cn": "周线级上涨·观察③", "weekly_prior": "大级别回调结束候选",
            "why": None}
    base.update(over)
    base.setdefault("verdict", S.bounce_or_reversal(base))
    return base


def _trend(state="counting", **over):
    meta = S.TREND_STATES[state]
    base = {"state": state, "cn": meta["cn"], "formed": meta["formed"], "rank": meta["rank"],
            "gate": {"level": 107.6, "need": 2, "streak": 1, "tag": "收复线", "label": "x",
                     "vol_min": 1.1, "vol_ok": True, "vol_note": "量比(rel SMH 1.19) 1.18",
                     "dist_pct": 4.5, "dist_n": 0.8}}
    if state == "transitioned":
        base.update(since_bars=2, level=98.0, date="2026-09-09", fastlane=True, reason="⚡")
    base.update(over)
    return base


def _with(report, ticker=None, bottom=None, trend=None):
    r = dict(report)
    if ticker:
        r["ticker"] = ticker
    r["setups"] = {"bottom": bottom if bottom is not None else report["setups"]["bottom"],
                   "trend": trend if trend is not None else report["setups"]["trend"]}
    return r


def test_report_has_all_six_steps_plus_setups(report):
    for k in ("context", "structure", "price_action", "volume", "indicators", "setups"):
        assert k in report, k
    assert set(report["setups"]) == {"bottom", "trend"}
    assert "stage" in report["setups"]["bottom"] and "state" in report["setups"]["trend"]


def test_render_always_carries_the_disclaimer(report):
    txt = CR.render(report)
    assert "以上是判读，不是授权" in txt and "entry_guard" in txt
    assert "⑥ 两种模式" in txt and "零授权效力" in txt


def test_relvr_label_matches_the_displayed_number(monkeypatch):
    """1.1997 显示成 1.2 却标「中力」= 显示与判定两条线。分档必须用播出去的那个数。"""
    bars = _bars(_walk(120))
    monkeypatch.setattr(CR, "rel_volume", lambda b, t=None: (1.1997, "n", False))
    v = CR.volume_block("X", bars, {"range_n": 1.0})
    assert v["relvr"] == 1.2 and v["effort"] == "高力"


def test_setups_section_never_prints_sizes(report):
    """「阶段」只说结构走到哪一步：出现股数/金额就是判读层在越权算 size。"""
    for stage in ("formed", "watch", "dead"):
        txt = "\n".join(CR.setups_lines(_with(report, bottom=_bottom(stage), trend=_trend())))
        assert not re.search(r"\d+\s*股", txt) and "$" not in txt
        assert "cap" not in txt.lower().replace("entry_cap", "").replace("sizing.entry_cap", "")


def test_setups_section_says_formed_or_forming(report):
    for stage, word in (("formed", "已形成"), ("watch", "即将形成")):
        txt = "\n".join(CR.setups_lines(_with(report, bottom=_bottom(stage), trend=_trend())))
        assert f"🔁 抄底 {S.STAGES[stage]['cn']}（{word}）" in txt
        assert "= 止损（下影线低点）" in txt
    txt = "\n".join(CR.setups_lines(_with(report, bottom=_bottom("none", listed=False, why="回撤不足"),
                                          trend=_trend("transitioned"))))
    assert "🔁 抄底：不在形态内（回撤不足）" in txt
    assert "🚀 趋势确认 ✓已转移（已形成）" in txt and "站稳 98" in txt and "⚡" in txt


def test_setups_section_spells_out_the_next_bar_to_watch(report):
    """每个阶段都要说「下一根看什么」—— 状态词本身不自明。"""
    watch = "\n".join(CR.setups_lines(_with(report, bottom=_bottom("watch"), trend=_trend())))
    assert "明日不再新低 = ✓止跌（止损 = 今日低 194）" in watch and "不是长下影" in watch
    formed = "\n".join(CR.setups_lines(_with(report, bottom=_bottom("formed"), trend=_trend())))
    assert "衰竭证据 次日未新低（信号日 09-02，6 根前）" in formed and "下影 62%" in formed
    assert "量比 0.9" in formed and "收盘已站上止跌日高 202.81" in formed
    assert "收盘越前高 228.5" in formed and "更高的低点" in formed and "收破止跌低 194 = ✗反弹结束" in formed
    done = "\n".join(CR.setups_lines(_with(report, bottom=_bottom("formed", above_lh=True, higher_low=True),
                                            trend=_trend())))
    assert "两个反转事实都已成立" in done
    dead = "\n".join(CR.setups_lines(_with(report, bottom=_bottom("dead", why="收 188 < 止跌低 194，昨日框架作废"),
                                           trend=_trend())))
    assert "昨日框架作废" in dead
    cut = "\n".join(CR.setups_lines(_with(report, bottom=_bottom("watch", undercut_prev={
        "low": 194.0, "date": "2026-09-01", "cn": "盘中破前止跌低 194（09-01）后收回"}), trend=_trend())))
    assert "⚠️ 盘中破前止跌低 194" in cut and "归执行层裁" in cut


def test_bounce_vs_reversal_is_a_reading_with_the_weekly_prior(report):
    txt = "\n".join(CR.setups_lines(_with(report, bottom=_bottom("formed"), trend=_trend())))
    assert "反弹 vs 反转：反弹（未越前高）" in txt and "周线级上涨·观察③（大级别回调结束候选）" in txt
    txt = "\n".join(CR.setups_lines(_with(report, bottom=_bottom("formed", above_lh=True), trend=_trend())))
    assert "反弹 vs 反转：反转候选（已越前高，回踩未验）" in txt
    watch = "\n".join(CR.setups_lines(_with(report, bottom=_bottom("watch"), trend=_trend())))
    assert "反弹 vs 反转" not in watch                       # 还没止跌, 没有反弹可判


def test_probe_marker_is_fyi_and_points_at_entry_guard(report):
    txt = "\n".join(CR.setups_lines(_with(report, bottom=_bottom("formed", probe="🔁探针R2域外·08-04"), trend=_trend())))
    assert "[FYI] 🔁探针R2域外·08-04" in txt and "entry_guard" in txt


# ══════════════════════════════════════════════════════════════
# 4. 模式 2 判别（trend_setup）—— 卡片 gate 的接近度
# ══════════════════════════════════════════════════════════════

def _gate_bars(closes, start="2026-08-01"):
    return _bars(closes, start)


def test_trend_setup_reads_recent_transition_as_formed():
    bars = _gate_bars([100] * 60 + [104, 105, 106])
    agent = {"regime_history": [{"date": bars[-2]["time"][:10], "direction": "up", "level": 103.0,
                                 "fastlane": True, "reason": "⚡快车道"}],
             "next_regime": {"name": "R_x", "entry_gate": {"level": 110.0, "days": 2}}}
    t = CR.trend_setup("T", agent, bars, 2.0)
    assert t["state"] == "transitioned" and t["formed"] and t["since_bars"] == 1 and t["fastlane"]
    assert t["gate"]["level"] == 110.0 and t["gate"]["streak"] == 0


def test_trend_setup_counting_near_and_far():
    agent = {"regime_history": [], "next_regime": {"entry_gate": {"level": 110.0, "days": 2}}}
    counting = CR.trend_setup("T", agent, _gate_bars([100] * 60 + [111]), 2.0)
    assert counting["state"] == "counting" and counting["gate"]["streak"] == 1 and counting["gate"]["vol_ok"] is True
    standing = CR.trend_setup("T", agent, _gate_bars([100] * 60 + [111, 112, 113]), 2.0)
    assert standing["state"] == "standing" and standing["gate"]["streak"] == 3
    near = CR.trend_setup("T", agent, _gate_bars([100] * 60 + [109]), 2.0)
    assert near["state"] == "near" and near["gate"]["dist_n"] == 0.5
    far = CR.trend_setup("T", agent, _gate_bars([100] * 60 + [100]), 2.0)
    assert far["state"] is None and far["gate"]["level"] == 110.0    # gate 在, 但 5N 外不列


def test_trend_setup_old_transition_is_history():
    bars = _gate_bars([100] * 60 + [104] * 10)
    agent = {"regime_history": [{"date": bars[-8]["time"][:10], "direction": "up", "level": 103.0}],
             "next_regime": {"entry_gate": {"level": 120.0, "days": 2}}}
    assert CR.trend_setup("T", agent, bars, 2.0)["state"] is None


def test_trend_setup_without_card_or_gate_is_none():
    bars = _gate_bars([100] * 30)
    assert CR.trend_setup("T", None, bars, 2.0)["state"] is None
    assert CR.trend_setup("T", {"zones": {}}, bars, 2.0) == {"state": None, "gate": None}


def test_trend_row_flags_a_gate_that_is_still_the_one_just_crossed(report):
    """转移后卡上的 entry_gate 还是刚过的线 = 没重画；不能印成「下一道」。"""
    t = _trend("transitioned", level=98.0, gate={**_trend()["gate"], "level": 98.0})
    row = CR.trend_row(_with(report, ticker="X", trend=t))
    assert "待重画" in row and "下一 98" not in row
    t2 = _trend("transitioned", level=98.0)
    assert "下一 107.6 收复线" in CR.trend_row(_with(report, ticker="X", trend=t2))


# ══════════════════════════════════════════════════════════════
# 5. 推送 = 两种模式清单
# ══════════════════════════════════════════════════════════════

# 明确不要的东西：这四类一旦回流，清单又会变成「授权层的东西混在判读层里」。
BANISHED = ("收盘", "涨跌", "zone", "cap", "no_chase", "授权")


def _table_rows(out: str):
    return [ln for ln in out.splitlines() if ln.startswith("| **")]


def test_digest_lists_only_formed_or_forming(report):
    """未形成的不列：none / decline 的抄底 + 无状态的趋势 → 只出现在「其余」行。"""
    quiet = _with(report, ticker="QUIET", bottom=_bottom("none", listed=False), trend={"state": None, "gate": None})
    hot = _with(report, ticker="HOT", bottom=_bottom("formed"), trend=_trend("counting"))
    out = CR.digest([quiet, hot])
    rows = _table_rows(out)
    assert all("HOT" in r for r in rows) and len(rows) == 2          # 抄底一行 + 趋势一行
    assert "其余 1 票不在两种形态内（不列）：QUIET" in out
    assert "### 🔁 抄底（1）" in out and "### 🚀 趋势确认（1）" in out


def test_digest_rows_carry_no_authorization_fields(report):
    for stage in ("formed", "watch", "dead"):
        r = _with(report, ticker="T", bottom=_bottom(stage), trend=_trend("transitioned"))
        for row in _table_rows(CR.digest([r])):
            leaked = [w for w in BANISHED if w in row]
            assert not leaked, f"清单行里漏进了不该有的东西: {leaked} ← {row}"
            assert not re.search(r"\d+\s*股", row) and "$" not in row


def test_digest_sorts_by_stage_rank(report):
    """已形成在前（✓止跌 → ⏳顶后新低 → ✗），✓ 内最新信号在前；趋势表 ✓已转移 → ✅ → ⏳ → ▫️。"""
    rs = [_with(report, ticker="Bwatch", bottom=_bottom("watch"), trend={"state": None, "gate": None}),
          _with(report, ticker="Bdead", bottom=_bottom("dead"), trend={"state": None, "gate": None}),
          _with(report, ticker="Bold", bottom=_bottom("formed", bars_since_signal=6), trend={"state": None, "gate": None}),
          _with(report, ticker="Bnew", bottom=_bottom("formed", bars_since_signal=0), trend={"state": None, "gate": None})]
    out = CR.digest(rs)
    order = [re.search(r"\*\*(\w+)\*\*", r).group(1) for r in _table_rows(out)]
    assert order == ["Bnew", "Bold", "Bwatch", "Bdead"]
    rs = [_with(report, ticker=f"T{s}", bottom=_bottom("none", listed=False), trend=_trend(s))
          for s in ("near", "transitioned", "counting", "standing")]
    order = [re.search(r"\*\*(\w+)\*\*", r).group(1) for r in _table_rows(CR.digest(rs))]
    assert order == ["Ttransitioned", "Tstanding", "Tcounting", "Tnear"]


def test_digest_never_silently_drops_a_ticker(report):
    """K 线不足的票另起 ⚠️ 行；不在形态内的票进「其余」行 —— 哪种都不能静默消失。"""
    err = {"ticker": "SHORT", "error": "日线不足（10 根）"}
    quiet = _with(report, ticker="QUIET", bottom=_bottom("none", listed=False), trend={"state": None, "gate": None})
    out = CR.digest([err, quiet])
    assert "⚠️ SHORT 日线不足（10 根）" in out and "QUIET" in out


def test_digest_bottom_row_says_signal_stop_bounce_lh_and_verdict(report):
    row = CR.bottom_row(_with(report, ticker="NB", bottom=_bottom("formed", probe="🔁探针R1·09-09")))
    assert row.startswith("| **NB** | ✓止跌 09-02 次日未新低(下影62%) · 收上止跌日高 · 顶跌4.1N | 194 (-13.2%/1.6N) | +15.3% · 高 254.74 · 回踩低 223.64 | 228.5 (+1.8%/0.2N) | — | 反弹（未越前高）")
    assert "周线级上涨·观察③" in row and "🔁探针R1·09-09" in row and row.count(" | ") == 6
    hl = CR.bottom_row(_with(report, ticker="NB", bottom=_bottom("formed", higher_low=True, higher_low_date="2026-09-12")))
    assert "回踩低 223.64↑09-12" in hl and "反转候选（回踩更高低点" in hl
    dead = CR.bottom_row(_with(report, ticker="NB", bottom=_bottom("dead", close=188.0)))
    assert "✗反弹结束 收188<194" in dead
    watch = CR.bottom_row(_with(report, ticker="NB", bottom=_bottom("watch", undercut_prev={
        "low": 194.0, "date": "2026-09-01", "cn": "x"})))
    assert "⏳顶后新低(下影62%) · 顶跌4.1N ⚠️盘中破194收回" in watch and "明日不再新低=止跌" in watch


def test_digest_trend_row_says_state_gate_count_volume_distance(report):
    row = CR.trend_row(_with(report, ticker="IN", trend=_trend("counting")))
    assert row == "| **IN** | ⏳计数中 | 107.6 收复线 | 1/2 | ✓ 1.18 | +4.5%/0.8N |"
    row = CR.trend_row(_with(report, ticker="IN", trend=_trend("transitioned")))
    assert "✓已转移 09-09@98⚡" in row and "下一 107.6 收复线" in row


def test_digest_of_the_real_book_fits_one_message_and_a_big_book_still_ships(report):
    """清单是**唯一那条推送**。富消息单条 32,768 字符 / 500 块；票数堆到必然越界时，
    `tg_rich.split_blocks` 必须在**表行边界**拆开、每条重复表头、行一条不丢。"""
    from quant.ops import tg_rich
    one = tg_rich.compile_blocks(CR.digest([_with(report, bottom=_bottom("formed"), trend=_trend())]))
    assert tg_rich.char_len(one) < tg_rich.RICH_CHAR_BUDGET // 8
    n = 60
    many = [_with(report, ticker=f"T{i:02d}", bottom=_bottom("formed"), trend=_trend()) for i in range(n)]
    parts = tg_rich.split_blocks(tg_rich.compile_blocks(CR.digest(many)), max_blocks=25)
    assert len(parts) > 1
    rows, with_table = 0, 0
    for p in parts:
        tables = [b for b in p if b["type"] == "table"]
        if not tables:
            continue
        with_table += 1
        assert all(t["cells"][0][0].get("is_header") for t in tables), "每条带表的都要带表头"
        rows += sum(len(t["cells"]) - 1 for t in tables)
    assert with_table > 1 and rows == 2 * n, "两张表被拆成多条, 行一条不丢"


def test_digest_header_is_the_legend_not_a_verdict(report):
    out = CR.digest([_with(report, bottom=_bottom("formed"), trend=_trend())], asof="2026-09-11")
    lines = out.splitlines()
    assert lines[0].startswith("## [FYI] 收盘判读 2026-09-11 · 两种模式")
    assert lines[1].startswith("> 🔁 抄底 = ") and lines[2].startswith("> 🚀 趋势确认 = ")
    assert list(CR.BOTTOM_TABLE_HEADER)[0] in out and list(CR.TREND_TABLE_HEADER)[0] in out


def test_short_history_degrades_instead_of_crashing(monkeypatch):
    monkeypatch.setattr(CR, "load_bars", lambda t, asof=None: (_bars(_walk(10)), None))
    monkeypatch.setattr(CR, "load_card", lambda t: None)
    r = CR.read("TEST")
    assert r.get("error") and "不足" in r["error"]
    assert "TEST" in CR.render(r)


def test_asof_declares_its_lookahead(monkeypatch):
    """`--asof` 只截断价格、不回溯卡片 = 前视。**必须自己说出来**。"""
    bars = _bars(_walk(320))
    monkeypatch.setattr(CR, "load_bars", lambda t, asof=None: (bars, None))
    monkeypatch.setattr(CR, "load_card", lambda t: CARD)
    monkeypatch.setattr(CR, "rel_volume", lambda b, t=None: (1.0, "n", False))
    assert CR.read("TEST")["lookahead_warning"] is None
    r = CR.read("TEST", asof="2026-01-01")
    assert r["lookahead_warning"] and "前视" in r["lookahead_warning"]
    assert "前视" in CR.render(r)


def test_merge_gap_is_reported_not_swallowed(report, monkeypatch):
    """序列断层必须播报 —— 静默降级是本仓反复记的失效形状。"""
    r = dict(report)
    r["merge_note"] = "序列断层 2026-01-05 → 2026-03-01"
    assert "序列断层" in CR.render(r)


# ══════════════════════════════════════════════════════════════
# 6. 周线重采样
# ══════════════════════════════════════════════════════════════

def _weekday_bars(closes, start="2026-01-05"):
    """只在工作日落 bar（真实日线的形状：周末没有 bar）。"""
    d = dt.date.fromisoformat(start)
    out = []
    for c in closes:
        while d.weekday() >= 5:
            d += dt.timedelta(days=1)
        out.append({"time": f"{d.isoformat()} 00:00:00", "open": c, "high": c * 1.02,
                    "low": c * 0.98, "close": c, "volume": 1000, "turnover": c * 1000})
        d += dt.timedelta(days=1)
    return out


def test_weekly_resample_drops_the_unfinished_week():
    """未走完的那一周不算 —— 与「未完成 bar 不是收盘」同一条纪律。"""
    from quant.core import technical as ta
    bars = _weekday_bars([100 + i for i in range(7)])      # 一整周 + 2 天
    assert dt.date.fromisoformat("2026-01-05").weekday() == 0   # 前提：起点是周一
    wk = ta.resample_weekly(bars)
    assert len(wk) == 1 and wk[0]["_days"] == 5
    assert len(ta.resample_weekly(bars, drop_partial=False)) == 2


def test_weekly_ohlc_is_exact():
    from quant.core import technical as ta
    bars = _weekday_bars([10, 12, 9, 15, 11])             # 恰好一个交易周
    w0 = ta.resample_weekly(bars, drop_partial=False)[0]
    assert w0["open"] == 10
    assert w0["high"] == pytest.approx(15 * 1.02)
    assert w0["low"] == pytest.approx(9 * 0.98)
    assert w0["close"] == 11 and w0["_days"] == 5
    assert w0["volume"] == 5000


# ══════════════════════════════════════════════════════════════
# 7. 宽度观察层 + 价值区 + 财报 stale
# ══════════════════════════════════════════════════════════════

def _write_jsonl(path, closes, start="2026-01-01"):
    import json as _json
    d = dt.date.fromisoformat(start)
    lines = []
    for c in closes:
        while d.weekday() >= 5:
            d += dt.timedelta(days=1)
        lines.append(_json.dumps({"time": f"{d.isoformat()} 00:00:00", "open": c,
                                  "high": c * 1.01, "low": c * 0.99, "close": c,
                                  "volume": 1000, "turnover": c * 1000}))
        d += dt.timedelta(days=1)
    path.write_text("\n".join(lines) + "\n")


def test_breadth_is_observation_only_no_threshold(tmp_path, monkeypatch):
    """宽度起步版 **DoF = 0**：只播报数值，不设阈值、不判定、不进任何授权路径。"""
    deep = tmp_path / "deep"
    deep.mkdir()
    _write_jsonl(deep / "UP.jsonl", [100 + i for i in range(80)])       # 站上 MA50
    _write_jsonl(deep / "DOWN.jsonl", [200 - i for i in range(80)])     # 跌破 MA50
    monkeypatch.setattr(CR, "KLINE_DEEP_DIR", deep)
    monkeypatch.setattr(CR, "book_tickers", lambda: [])
    CR._BREADTH_CACHE.clear()
    b = CR.market_breadth()
    assert b["pool"]["pct_above_50dma"] == 50.0 and b["pool"]["n"] == 2
    import json as _json
    assert not any(k in _json.dumps(b) for k in ("verdict", "signal", "alert", "cap"))


def test_breadth_reports_pool_staleness_loudly(report, monkeypatch):
    """深度池不入 git、**无刷新 SLA**。落后了就必须喊。"""
    r = dict(report)
    r["context"] = {**r["context"],
                    "breadth": {"book": {"pct_above_50dma": 42.0, "n": 12},
                                "pool": {"pct_above_50dma": 49.0, "n": 166},
                                "pool_asof": "2026-07-24"}}
    r["asof"] = "2026-08-11"
    txt = CR.render(r)
    assert "🚫落后18天" in txt and "不是今天的" in txt


def test_breadth_declares_the_proxy_bias(report):
    assert "不是任何指数的成分" in CR.render(report)


def test_value_area_where_label_matches_the_bounds():
    """区内/区上/区下 = 拍卖理论的坐标系（平衡 vs 失衡）。标签必须与数一致。"""
    from quant.core import technical as ta
    bars = _bars(_walk(120))
    atr = ta.atr(bars, 20)[-1]
    w = CR.walls_block(bars, float(bars[-1]["close"]), atr)
    va = w.get("value_area")
    if va:
        spot = float(bars[-1]["close"])
        inside = va["val"] <= spot <= va["vah"]
        assert ("区内" in va["where"]) == inside


def test_stale_card_warning_uses_the_existing_judge(monkeypatch):
    """财报重画 SLA **复用** `core.earnings.cards_stale_after_print` —— 判读层不许自己再实现一遍。"""
    from quant.core import earnings as earn
    assert "cards_stale_after_print" in Path(earn.__file__).read_text()
    assert "cards_stale_after_print" in SRC_READ


def test_stale_card_warning_is_printed_before_the_map(report, monkeypatch):
    """卡锚早于财报反应日 ⇒ gate 图**不含反应日的信息**。不说出来 = 本仓最熟的那种静默。"""
    r = _with(report, bottom=_bottom("formed"), trend=_trend())
    r["card_stale_earnings"] = "NBIS print 2026-08-12 BMO → 反应日 2026-08-12, 卡锚停在 2026-08-06"
    txt = CR.render(r)
    assert "待重画" in txt and "不含反应日" in txt
    assert txt.index("待重画") < txt.index("② Structure")   # 必须在图之前
    out = CR.digest([r])
    assert all("🚨待重画" in row for row in _table_rows(out))   # 两张表都不许漏


def test_stale_judge_failure_does_not_break_the_read(monkeypatch, tmp_path):
    monkeypatch.setattr(CR, "EARNINGS_CALENDAR_PATH", tmp_path / "nope.json")
    CR._STALE_CACHE.clear()
    assert CR.earnings_stale_cards("2026-08-12") == {}


# ══════════════════════════════════════════════════════════════
# 8. 短期道氏视图（详情页 ② 段）
# ══════════════════════════════════════════════════════════════

def _dv(trend="up", phase=0, **over):
    """一张短期道氏视图（dow_block 的产物）—— 直接造字典，测的是渲染。"""
    from quant.core.dow import STAGE_CN
    up = trend == "up"
    base = {"trend": trend, "phase": phase, "labels": "HH+HL" if up else "LH+LL",
            "stage_cn": STAGE_CN[(trend, phase)],
            "bars_in_phase": 2, "candidate": None, "leg_volume": None, "obs_start": None,
            "last_resolution": None, "recent_resolution": None, "effort": {},
            "retrace": None, "swings": [],
            "lines": ({"imp_level": {"level": 120.0, "pct": 14.0, "n": 2.8},
                       "trigger_ref": {"level": 104.0, "pct": -1.0, "n": 0.2},
                       "prev_ret": {"level": 100.0, "pct": -5.0, "n": 1.0}} if phase == 0 else
                      {"resume_level": {"level": 120.0, "pct": 14.0, "n": 2.8},
                       "obs_level": {"level": 96.0, "pct": -8.6, "n": 1.7},
                       "terminate_level": ({"level": 96.0, "pct": -8.6, "n": 1.7}
                                           if phase >= 2 else None),
                       "cnt_level": ({"level": 112.0, "pct": 6.7, "n": 1.3} if phase >= 2 else None),
                       "prev_ret": {"level": 100.0, "pct": -5.0, "n": 1.0}})}
    base.update(over)
    return base


def _wk(trend="up", phase=0):
    from quant.core.dow import STAGE_CN
    return {"trend": trend, "phase": phase, "stage_cn": STAGE_CN[(trend, phase)],
            "labels": "HH+HL", "watch": {}, "last_resolution": None}


def test_structure_block_carries_the_short_term_dow_view(report):
    dv = report["structure"]["dow"]
    for k in ("trend", "phase", "stage_cn", "labels", "lines", "recent_resolution"):
        assert k in dv, k
    assert "短期道氏" in CR.render(report)


def test_retrace_depth_is_spelled_out():
    """回撤深度要说人话，**动词带方向**：上涨 = 回调跌破前 HL；下跌 = 反弹站上前 LH。"""
    deep = "\n".join(CR.dow_lines(_dv("up", 2, retrace={"pct": 1.2, "broke_prev_ret": True})))
    assert "回调已跌破前 HL 100" in deep and "上一段涨幅整段吐回" in deep
    dn_lines = CR.dow_lines(_dv("down", 2, retrace={"pct": 1.2, "broke_prev_ret": True}))
    note = next(x for x in dn_lines if "前 LH" in x)
    assert "反弹已站上前 LH 100" in note and "上一段跌幅整段收复" in note and "跌破" not in note
    two_thirds = "\n".join(CR.dow_lines(_dv("up", 2, retrace={"pct": 0.8, "broke_prev_ret": False})))
    assert "Rhea" in two_thirds and "回调已达最后一段涨幅的 80%" in two_thirds


def test_dow_block_on_synthetic_example_reports_phase_and_effort(monkeypatch):
    """端到端合成例走到观察②，破坏日带 RelVR 标签（量只做限定词）。"""
    from tests.core.test_dow import RISE, PULLBACK, BOUNCE, _bars as _dbars
    bars = _dbars(RISE + PULLBACK + BOUNCE)
    monkeypatch.setattr(CR, "rel_volume", lambda b, t=None: (1.5, "n", False))
    dv = CR.dow_block("TEST", bars, 2.0)
    assert dv["phase"] == 2 and dv["effort"]["obs1"]["label"] == "高力"
    assert dv["lines"]["terminate_level"]["level"] == 180 and dv["lines"]["resume_level"]["level"] == 200
    assert dv["candidate"]["role"] == "LH" and dv["candidate"]["price"] == 190


def test_dow_block_runs_the_same_machine_on_completed_weeks(report):
    dv = report["structure"]["dow"]
    assert dv.get("weekly") and dv["weekly"]["trend"] in ("up", "down")
    assert "stage_cn" in dv["weekly"]


def test_nested_reading_turns_weekly_state_into_a_prior():
    base = _dv("up", 2)
    assert CR.nested_reading({**base, "weekly": _wk("up", 0)})["key"] == "pullback_prior"
    assert CR.nested_reading({**base, "weekly": _wk("up", 3)})["key"] == "both_obs"
    dn = CR.nested_reading({**base, "weekly": _wk("down", 2)})
    assert dn["key"] == "higher_down" and "大级别" in dn["cn"] and "趋势延续" in dn["cn"]
    quiet = CR.nested_reading({**_dv("up", 0), "weekly": _wk("up", 0)})
    assert quiet["key"] == "quiet" and "先验" not in quiet["cn"]
    mirror = CR.nested_reading({**_dv("down", 2), "weekly": _wk("down", 0)})
    assert mirror["key"] == "bounce_prior" and "%" not in mirror["cn"]


def test_retrace_band_line_is_descriptive_only():
    dv = _dv("up", 2, retrace_at_form={"pct": 0.27, "band": "<1/3",
                                       "band_cn": "轻微回调·不到次级折返"})
    txt = "\n".join(CR.dow_lines(dv))
    assert "回撤带 <1/3" in txt and "lsl 成形时回撤 27%" in txt
    assert "终止率" not in txt and "n=" not in txt


def test_dow_lines_are_crossed_with_walls_within_a_quarter_atr():
    dv = _dv("up", 2, obs={"price": 96.0}, imp={"price": 120.0}, cnt={"price": 112.0},
             ret={"price": 100.0})
    walls = [{"level": 96.3, "kind": "pivot", "detail": "×3 [high+low]"},
             {"level": 110.0, "kind": "HVN", "detail": "20% 成交堆积"}]
    sup = CR.dow_line_support(dv, walls, 2.0)
    assert "lsl" in sup and sup["lsl"]["wall"] == 96.3 and sup["lsl"]["gap_n"] == 0.15
    assert "LH" not in sup
    txt = "\n".join(CR.dow_lines({**dv, "line_support": sup}))
    assert "线上有货" in txt and "lsl 96 ← pivot 96.3" in txt
    bare = "\n".join(CR.dow_lines({**dv, "line_support": {}}))
    assert "真空里的线" in bare


def test_bottom_block_carries_the_weekly_prior(monkeypatch):
    """抄底行的周线级先验读同一台周线机器（dow_block 的 weekly），不另算。"""
    bars = _bars(_walk(320))
    dv = {"weekly": {"trend": "down", "phase": 0}}
    b = CR.bottom_block(bars, 2.0, dv)
    assert b["weekly_cn"] == "周线级下跌·推进中" and b["weekly_prior"] == "反弹先验"
    assert CR.bottom_block(bars, 2.0, None)["weekly_cn"] is None


# ══════════════════════════════════════════════════════════════
# 9. 全书人群 = 活着的卡（防止判读在读已不存在的标的）
# ══════════════════════════════════════════════════════════════

def _book_docs(tmp_path, monkeypatch, positions, watchlist):
    import json as _json
    pos_p, wl_p = tmp_path / "positions.json", tmp_path / "watchlist.json"
    pos_p.write_text(_json.dumps({"tickers": positions}))
    wl_p.write_text(_json.dumps({"tickers": watchlist}))
    monkeypatch.setattr(CR, "POSITIONS_PATH", pos_p)
    monkeypatch.setattr(CR, "WATCHLIST_PATH", wl_p)


def _card(**kw):
    body = {"agent": {"zones": {"accumulation_zone_1": [1.0, 2.0]}}}
    body.update(kw)
    return body


def test_book_excludes_moomoo_tombstones(tmp_path, monkeypatch):
    """移出 moomoo 自选分组的卡 = 墓碑，不进全书。"""
    _book_docs(tmp_path, monkeypatch, {}, {
        "LIVE": _card(status="watching", moomoo_listed=True),
        "TOMB": _card(status="watching", moomoo_listed=False),
    })
    assert CR.book_tickers() == ["LIVE"]


def test_book_excludes_flat_and_unmanaged_position_cards(tmp_path, monkeypatch):
    _book_docs(tmp_path, monkeypatch, {
        "HELD": _card(status="active", broker={"qty": 5}),
        "FLAT": _card(status="active", broker={"qty": 0}),
        "SPCX": _card(status="active", broker={"qty": 9},
                      instrument={"role": "commemorative_hold"}),
    }, {})
    assert CR.book_tickers() == ["HELD"]


def test_book_excludes_reference_tickers(tmp_path, monkeypatch):
    _book_docs(tmp_path, monkeypatch, {}, {
        "REAL": _card(status="watching", moomoo_listed=True),
        "SPY": _card(status="watching", moomoo_listed=True,
                     instrument={"role": "volume_reference"}),
    })
    assert CR.book_tickers() == ["REAL"]


def test_book_never_hand_copies_the_candidate_predicate():
    """判别式必须来自 `core.holdings`，不许在本模块再抄一份。"""
    body = SRC_READ.split("def book_tickers", 1)[1].split("\ndef ", 1)[0]
    code = "\n".join(ln for ln in body.splitlines() if not ln.lstrip().startswith("#"))
    code = re.sub(r'""".*?"""', "", code, flags=re.S)
    assert "moomoo_listed" not in code, "book_tickers 又手抄了一份候选判别式"
    for fn in ("live_tickers", "is_managed", "is_candidate", "is_traded"):
        assert fn in code, f"book_tickers 没用 core.holdings.{fn}"


def test_motto_footer_is_appended_by_the_cli_not_the_report(report):
    """座右铭由 tg_notify 尾行附加，判读输出本身不写第二遍。"""
    out = CR.digest([report])
    assert tg.MOTTO_LINE not in out if hasattr(tg, "MOTTO_LINE") else True


# ── 底部确认概率（判读列, 零授权; 随机游走位置几何）──

def test_confirm_block_only_for_formed_frames_with_a_prior_high():
    bars = _bars(_walk(320))
    ok = CR.confirm_block(_bottom("formed"), bars)
    assert ok["state"] == "open" and 0.0 < ok["p"] < 1.0 and ok["cn"] == f"{ok['p']:.0%}"
    assert CR.confirm_block(_bottom("formed", above_lh=True, close=230.0), bars) == \
        {"state": "above", "p": None, "cn": "已越前高"}
    for b in (_bottom("watch"), _bottom("dead"), _bottom("formed", lh=None), _bottom("formed", lh=190.0)):
        assert CR.confirm_block(b, bars) is None
    near_stop = CR.confirm_block(_bottom("formed", close=196.0), bars)["p"]
    near_lh = CR.confirm_block(_bottom("formed", close=227.0), bars)["p"]
    assert near_stop < ok["p"] < near_lh                       # 位置几何: 离止损越近越低, 离前高越近越高


def test_digest_bottom_row_carries_the_confirmation_column_and_the_legend_says_not_a_ranking(report):
    b = _bottom("formed", confirm_p={"state": "open", "p": 0.42, "cn": "42%"})
    row = CR.bottom_row(_with(report, ticker="NB", bottom=b))
    assert "| 228.5 (+1.8%/0.2N) | 42% | 反弹（未越前高）" in row
    above = CR.bottom_row(_with(report, ticker="NB",
                                bottom=_bottom("formed", confirm_p={"state": "above", "p": None, "cn": "已越前高"})))
    assert "| 已越前高 |" in above
    out = CR.digest([_with(report, bottom=b, trend=_trend())])
    assert "| 确认 |" in out and "不是排序键" in out.splitlines()[1]


def test_confirmation_probability_does_not_reorder_the_bottom_table(report):
    """概率与赔率是同一件几何: 按它排 = 系统性挑赔率差的笔 → 表序只看阶段 / 信号龄 / 票名。"""
    none = {"state": None, "gate": None}
    rs = [_with(report, ticker="ZHI", trend=none,
                bottom=_bottom("formed", bars_since_signal=3, confirm_p={"state": "open", "p": 0.95, "cn": "95%"})),
          _with(report, ticker="ALO", trend=none,
                bottom=_bottom("formed", bars_since_signal=3, confirm_p={"state": "open", "p": 0.05, "cn": "5%"}))]
    order = [re.search(r"\*\*(\w+)\*\*", r).group(1) for r in _table_rows(CR.digest(rs))]
    assert order == ["ALO", "ZHI"]


def test_detail_page_spells_out_what_the_confirmation_probability_means(report):
    b = _bottom("formed", confirm_p={"state": "open", "p": 0.42, "cn": "42%"})
    txt = "\n".join(CR.setups_lines(_with(report, bottom=b, trend=_trend())))
    assert "底部确认：42%（20 根内先收盘越前高 228.5、后才收破止跌低 194 的概率" in txt and "不是排序键" in txt


# ══════════════════════════════════════════════════════════════
# 5. 箱体位置段（位置分布；判别式 core/key_levels）
# ══════════════════════════════════════════════════════════════

from quant.core import key_levels as KL  # noqa: E402


def _box(state, top=None, brk=None):
    return {"state": state, "cn": KL.POSITIONS[state]["cn"], "top": top, "brk": brk}


def test_report_carries_a_box_position(report):
    b = report["box"]
    assert b["state"] in CR.BOX_ORDER and {"state", "cn", "top", "brk"} <= set(b)


def test_bottom_bucket_reads_the_same_listed_flag_as_the_bottom_table():
    """「底部」= 🔁 表同一个 listed（✓ / ⏳），不重判；✗反弹结束 不算底部。平盘 = 没有簇 = 无箱。"""
    flat = _bars([100.0] * 300)
    assert CR.box_block(flat, 2.0, {"listed": True, "stage": "formed"})["state"] == "bottom"
    assert CR.box_block(flat, 2.0, {"listed": True, "stage": "watch"})["state"] == "bottom"
    assert CR.box_block(flat, 2.0, {"listed": True, "stage": "dead"})["state"] == "none"
    assert CR.box_block(flat, 2.0, {"listed": False, "stage": "formed"})["state"] == "none"


def test_box_section_lists_every_ticker_once_grouped_in_order(report):
    rs = [
        {**_with(report, ticker="SYNA"), "box": _box("broken", brk={"level": 142.4, "date": "2026-09-22",
                                                                  "intact": True, "gain_pct": 5.16})},
        {**_with(report, ticker="SYNB"), "box": _box("near_top", top={"level": 288.0, "tests": 2, "dist_pct": 4.1})},
        {**_with(report, ticker="SYNC"), "box": _box("mid", top={"level": 376.0, "tests": 2, "dist_pct": 7.1})},
        {**_with(report, ticker="SYND"), "box": _box("bottom")},
        {**_with(report, ticker="SYNE"), "box": _box("failed", brk={"level": 119.35, "date": "2026-08-11",
                                                                   "intact": False, "gain_pct": 14.0})},
        {**_with(report, ticker="ZZ"), "box": _box("none")},
    ]
    out = CR.digest(rs)
    sec = out[out.index("### 🧭 箱体位置（6）"):].splitlines()
    groups = [ln for ln in sec if ln.startswith("- **")]
    assert [re.match(r"- \*\*(.+?)\*\*", ln).group(1) for ln in groups] == [      # 顺序写死: 单边 → 逼近顶 → 中段 → 底 → 失败 → 无箱
        "⭐已破箱顶·单边", "箱顶下·震荡", "箱体中段·震荡", "底部", "⭐突破失败·回到震荡", "无箱"]
    body = "\n".join(groups)
    for t in ("SYNA", "SYNB", "SYNC", "SYND", "SYNE", "ZZ"):
        assert len(re.findall(rf"(?<![A-Z]){t}(?![A-Z])", body)) == 1, t
    assert "SYNA ⭐142.4 +5.2%·09-22" in body and "SYNE ⭐119.35·08-11" in body      # 失败不报涨幅
    assert "SYNB ⭐288（距 +4.1%）" in body
    legend = sec[1]
    assert legend.startswith("> ⭐ = ") and "样本外" in legend and "不当信号" in legend
    for word in ("cap", "NAV", "股数", "$", "买入", "加仓"):
        assert word not in "\n".join(sec), word


def test_box_section_is_skipped_for_reports_without_it(report):
    r = _with(report, bottom=_bottom("formed"), trend=_trend())
    r.pop("box", None)
    assert "🧭 箱体位置" not in CR.digest([r])
