"""每日判读的不变量 —— **核心目的的守卫**。

用户：「为了在落地过程中不跑偏 我觉得你需要把核心目的钉起来」。
钉的方式只能是**会失败的东西**：文档里的目的三个月后就退化成装饰
（本仓「强制点只在文档不在代码 = 这条规则不存在」的老账）。

这里钉四件：
  1. 核心目的**逐字**在源码里（改掉它 = 这条红）
  2. 判读层**零副作用**（不写任何 state）
  3. 判读层**不重算授权**（不 import sizing / entry_guard / exit_guard）
  4. 输出的每一条形态都带**尺度 + 证据等级**，且整页带免责行
"""
import datetime as dt
import re
from pathlib import Path

import pytest

from quant.core import patterns as P
from quant.ops import tg_notify as tg
from quant.report import chart_read as CR

SRC_READ = Path(CR.__file__).read_text()
SRC_PAT = Path(P.__file__).read_text()


# ══════════════════════════════════════════════════════════════
# 1. 核心目的钉在源码里
# ══════════════════════════════════════════════════════════════

PURPOSE_FRAGMENTS = [
    "每天收盘后",
    "分析出当前所处结构及形态",
    "潜在的交易机会",
]


@pytest.mark.parametrize("frag", PURPOSE_FRAGMENTS)
def test_core_purpose_verbatim_in_both_modules(frag):
    """核心目的**逐字**写在两个模块顶部。

    为什么两个都要：`patterns` 是判别式真源、`chart_read` 是装配与输出面 ——
    将来改动最可能只碰其中一个，那时另一个的目的声明就是唯一的锚。
    """
    assert frag in SRC_READ, f"chart_read 丢了核心目的片段: {frag}"
    assert frag in SRC_PAT, f"patterns 丢了核心目的片段: {frag}"


def test_pipeline_order_matches_canonical_chain():
    """六步链条按 用户 图里的顺序，不许重排（重排 = 换了一套方法论还叫同一个名字）。"""
    for a, b in zip(["Context", "Structure", "Price Action", "Volume", "Indicators", "Trade"],
                    ["Structure", "Price Action", "Volume", "Indicators", "Trade"]):
        assert SRC_READ.index(a) <= SRC_READ.index(b) or True   # 顺序在 docstring 里
    assert "Context → Structure → Price Action → Volume → Indicators → Trade" in SRC_READ


# ══════════════════════════════════════════════════════════════
# 2. 判读层零副作用（源码棘轮）
# ══════════════════════════════════════════════════════════════

WRITE_CALLS = ["atomic_write_json", "append_jsonl", "write_text", "write_bytes",
               ".dump(", "os.remove", "shutil."]


@pytest.mark.parametrize("call", WRITE_CALLS)
def test_read_layer_writes_nothing(call):
    """判读永远只读。写 state 的判读会变成一个没人知道在改交易状态的模块 ——
    `heartbeat.md` §盘中运行 记的正是这种事（「预览」能改交易状态本身就是设计错误）。"""
    assert call not in SRC_READ, f"判读层出现写操作: {call}"


FORBIDDEN_IMPORTS = ["quant.core.sizing", "quant.decision.entry_guard",
                     "quant.decision.exit_guard", "quant.sync."]


@pytest.mark.parametrize("mod", FORBIDDEN_IMPORTS)
def test_read_layer_never_recomputes_authorization(mod):
    """不 import 授权/仓位模块 —— 一旦 import 就会有人在这里算第二份 cap，
    而「实际能下多少」的单一真源是 `sizing.entry_cap`（四个消费者读同一份）。
    判读层要做的是**指过去**，不是**再算一遍**。"""
    assert mod not in SRC_READ, f"判读层不该依赖 {mod}"


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
        "next_regime": {"name": "R_突破_120", "entry_gate": {"level": 120.0}},
    },
}


@pytest.fixture
def report(monkeypatch):
    bars = _bars(_walk(320))
    monkeypatch.setattr(CR, "load_bars", lambda t, asof=None: (bars, None))
    monkeypatch.setattr(CR, "load_card", lambda t: CARD)
    monkeypatch.setattr(CR, "rel_volume", lambda b, t=None: (1.35, "量比(rel QQQ 1.00) 1.35", False))
    return CR.read("TEST")


def test_report_has_all_six_steps_plus_read_and_opportunity(report):
    for k in ("context", "structure", "price_action", "volume", "indicators",
              "patterns", "read", "posture", "opportunity"):
        assert k in report, k


def test_render_always_carries_the_disclaimer(report):
    """免责行不是礼貌用语，它是**这一页与授权层的边界声明**。"""
    txt = CR.render(report)
    assert "判读，不是授权" in txt
    assert "entry_guard" in txt


def test_every_pattern_line_shows_scale_and_evidence(report, monkeypatch):
    """形态行必须同时打出**窗口**与**证据等级**：
    没有窗口 = 尺度外推被当成 Lo 检验过的口径；
    没有证据等级 = 民间形状（楔形）看起来和头肩一样硬。"""
    fake = dict(report)
    fake["patterns"] = P.detect_multi(_bars(
        [92, 96, 100, 96, 92, 90, 95, 100, 108, 115, 108, 100, 94, 90.5,
         94, 98, 100.3, 97, 94] * 6))
    txt = CR.render(fake)
    for p in fake["patterns"]:
        assert f"{p['window']}日窗" in txt
        assert p["evidence"] in txt
        if not p["lo_calibrated"]:
            assert "尺度外推" in txt


def test_relvr_label_matches_the_displayed_number(monkeypatch):
    """播出去的数与它的档位标签必须自洽 —— 1.1997 显示成 1.2 却标「中力」，
    读者只能看到自相矛盾的组合（本仓「两条线并存」的小型版）。"""
    bars = _bars(_walk(120))
    monkeypatch.setattr(CR, "rel_volume", lambda b, t=None: (1.1997, "n", False))
    v = CR.volume_block("TEST", bars, {"range_n": 1.4})
    assert v["relvr"] == 1.2 and v["effort"] == "高力"


def test_posture_has_three_buckets_and_no_probability(report):
    """三栏「延续 / 震荡 / 反转」是核心目的里那句话的直接兑现；
    **不许出现概率**—— 我们从没校准过这三类的先验（sss_backtest 的学费）。"""
    ps = report["posture"]
    assert set(ps) >= {"continuation", "range", "reversal"}
    txt = CR.render(report)
    assert "不给概率" in txt
    seg = txt.split("后续倾向")[1].split("潜在机会")[0]
    assert not re.search(r"\b(概率|胜率|\d{1,3}%\s*(概率|可能))", seg)


TG_LIMIT = 4096      # Telegram sendMessage 单条上限（tg_notify 只有 sendMessage）


def test_digest_of_the_real_book_fits_one_message_and_a_big_book_still_ships(report):
    """扫描表是**唯一那条推送**（起不再推每票详情）。

    全书 11 只实测 1.7k 字符 = 一条装得下。但"装得下"不能靠运气：票一多 /
    某只票多两个形态就会越界，而越界的后果是**整条 400 拒收，一个字都不到**。
    所以第二半钉的是 —— 超限时 `tg_notify.split_message` 必须能在**票的边界**
    上拆开，且每条都合规。
    """
    assert len(CR.digest([report] * 11)) < TG_LIMIT
    parts = tg.split_message(CR.digest([report] * 20))
    assert len(parts) > 1 and all(tg._tg_len(p) <= TG_LIMIT for p in parts)
    assert all("━━" in p for p in parts), "拆出来的每条都该带完整的票块，不是半只票"


# 用户  明确点名不要的东西：这四类一旦回流，扫描表又会变成
# 「授权层的东西混在判读层里」—— 而这正是判读层零授权效力最容易破的地方。
BANISHED = ("收盘", "涨跌", "zone", "cap", "no_chase", "授权")


def test_digest_carries_only_the_four_things_asked_for(report):
    """力果 / 形态(含阶段) / 后续倾向 / 潜在机会(只说假设) —— 别的都不要。"""
    body = "\n".join(ln for ln in CR.digest([report]).splitlines()
                     if not ln.startswith(("[FYI]", "形态阶段", "倾向三栏", "机会 = ")))
    leaked = [w for w in BANISHED if w in body and w != "收盘"]   # "收盘价"没了但"收盘站上颈线"是确认条件
    assert not leaked, f"扫描表里漏进了不该有的东西: {leaked}"
    assert "力果" in body and "形态" in body and "倾向" in body


def test_digest_says_what_the_continuation_continues(report):
    """用户：「延续的话是延续上涨还是延续下跌还是延续震荡」——
    光写「延续 3」等于让读的人自己回去翻结构。"""
    for trend, cn in (("up", "延续上涨"), ("down", "延续下跌"), ("transition", "延续震荡")):
        r = dict(report)
        r["structure"] = {**report["structure"], "daily": {**report["structure"]["daily"],
                                                           "trend": trend}}
        assert cn in "\n".join(CR.digest_block(r))


def test_digest_hypotheses_come_from_the_same_builder_as_the_detail(report):
    """机会段 = `pattern_hypotheses` 的输出，一字不改 —— 抄第二遍的那天，
    扫描表说的确认线和详情说的就会开始各说各话。"""
    atr = report["structure"].get("atr20")
    hyps = CR.pattern_hypotheses(report["patterns"], atr)
    block = "\n".join(CR.digest_block(report))
    for h in hyps:
        assert h in block
        assert h in CR.opportunity(report["structure"], report["patterns"])["items"]


def test_digest_sorts_by_distance(report):
    """导航的排序 = 先看谁已经破线、再看谁快到线。字母序对导航毫无意义。"""
    def mk(tk, state, confirm, chg):
        r = dict(report, ticker=tk)
        r["price_action"] = {**report["price_action"], "chg_pct": chg, "close": 100.0}
        r["structure"] = {**report["structure"], "atr20": 10.0}
        r["patterns"] = [{"name": "DBOT", "cn": "双底", "direction": "bullish",
                          "state": state, "confirm_if": "c", "invalidate_if": "i",
                          "evidence": "e", "window": 120, "lo_calibrated": False,
                          "confirm_level": confirm, "key_level": confirm}]
        return r
    out = CR.digest([mk("FAR", "shaped", 130.0, 0.1),      # 差 3N
                     mk("NEAR", "shaped", 105.0, 0.1),     # 差 0.5N
                     mk("BROKE", "triggered", 100.0, 0.1)])
    assert out.index("BROKE") < out.index("NEAR") < out.index("FAR")


def test_digest_never_silently_drops_a_ticker(report):
    """形态状态是会长的（Lo 族还有一半没实现）。一个没见过的 state 不能让整张表
    崩掉（那天一条 TG 都不会到），也不能让那只票静默消失。"""
    r = dict(report, ticker="NEWSTATE")
    r["patterns"] = [{"name": "X", "cn": "新形态", "state": "brand_new",
                      "direction": "bullish", "confirm_if": "c", "invalidate_if": "i",
                      "evidence": "e", "window": 120, "lo_calibrated": False}]
    out = CR.digest([r])
    assert "NEWSTATE" in out and "新形态" in out


def _pat(cn, name="DBOT", **kw):
    base = {"name": name, "cn": cn, "direction": "bullish", "state": "triggered",
            "confirm_if": "c", "invalidate_if": "i", "evidence": "e", "window": 120,
            "lo_calibrated": False, "confirm_level": 258.08, "key_level": 258.08,
            "last_close": 267.28, "resolved_side": "up", "bars_since_trigger": 8}
    base.update(kw)
    return base


def test_colinear_patterns_merge_into_one_line():
    """共线的形态合并成一行（用户  裁）—— AMZN 的收敛三角底与双底
    确认线同为 258.08，逐字印两遍在推送里就是噪音。"""
    out = CR.pattern_hypotheses([_pat("收敛三角底", "TBOT"), _pat("双底")], 8.61)
    assert len(out) == 1
    assert "收敛三角底 + 双底（两个形态共线）" in out[0]


def test_patterns_on_different_lines_never_merge():
    """**合并判据是正文逐字相同，不是「线相近」**—— 差一个字就说明这两行
    要说的不是同一句话，合并会吃掉信息。"""
    out = CR.pattern_hypotheses(
        [_pat("双底"), _pat("扩散底", "BBOT", confirm_level=260.0)], 8.61)
    assert len(out) == 2 and "共线" not in "".join(out)


def test_merge_happens_before_truncation():
    """分组在截断之前 —— 合并腾出的位置要能让第三个形态露出来，
    否则「合并」等于把一个形态换成另一个形态的重复。"""
    out = CR.pattern_hypotheses(
        [_pat("收敛三角底", "TBOT"), _pat("双底"),
         _pat("扩散底", "BBOT", confirm_level=260.0)], 8.61)
    assert len(out) == 2 and "扩散底" in out[1]


def test_digest_shows_distance_only_when_not_triggered(report):
    """`●已破线(5.1N)` 会被读成「还差 5.1N」—— 同一个括号两种含义 = 每天误读一次。"""
    base = dict(report)
    p = {"name": "DBOT", "cn": "双底", "direction": "bullish", "state": "triggered",
         "confirm_if": "c", "invalidate_if": "i", "evidence": "e", "window": 120,
         "lo_calibrated": False, "confirm_level": 50.0, "key_level": 50.0}
    base["patterns"] = [p]
    assert "距确认" not in "\n".join(CR.digest_block(base))
    base["patterns"] = [{**p, "state": "shaped"}]
    assert "距确认" in "\n".join(CR.digest_block(base))


def test_digest_shows_the_pattern_stage_in_words():
    """形态阶段要写成字（"已破确认线"），不能只留一个 ● —— 记号是给扫的，
    字是给读的；2026-08-13 起扫描表是唯一那条推送，读的人没有第二处可查。"""
    base = dict(report_stage_fixture())
    txt = "\n".join(CR.digest_block(base))
    assert "○双顶" in txt and "形状已成立" in txt
    assert "●" not in txt.split("形态", 1)[1].split("\n")[0].split("（")[1]   # 记号不重复出现


def report_stage_fixture():
    return {"ticker": "X", "volume": {"effort": "高力", "result": "大果"},
            "structure": {"daily": {"trend": "down"}, "atr20": 10.0},
            "price_action": {"close": 100.0, "chg_pct": -1.0},
            "posture": {"continuation": [], "range": [], "reversal": []},
            "patterns": [{"name": "DTOP", "cn": "双顶", "direction": "bearish",
                          "state": "shaped", "confirm_if": "c", "invalidate_if": "i",
                          "evidence": "e", "window": 120, "lo_calibrated": False,
                          "confirm_level": 95.0, "key_level": 95.0}]}


def test_container_patterns_never_assert_a_side():
    """矩形/三角/扩散/楔形 = **方向未定**，不许套用名字里的 TOP/BOT。

     GOOGL 实测过这个矛盾：「扩散顶若成立 = **下行**方向：
    收盘**突破**384.48 = 向上解决」—— 同一行里自己打自己。
    """
    struct = {"daily": {"trend": "up"}, "weekly": None, "regime": {"cap_pct_nav": 0.4},
              "next_gate": None, "zone_position": "waiting", "walls": {}}
    for name in ("BTOP", "TTOP", "RTOP", "RWEDGE"):
        p = {"name": name, "cn": P.PATTERN_SPECS[name]["cn"],
             "direction": P.PATTERN_SPECS[name]["dir"], "state": "shaped",
             "confirm_if": "收盘突破 X", "invalidate_if": "收盘跌破 Y",
             "evidence": "e", "window": 35, "lo_calibrated": True}
        txt = " ".join(CR.opportunity(struct, [p])["items"])
        assert "方向未定" in txt
        assert "下行方向" not in txt and "上行方向" not in txt


def test_neckline_patterns_do_assert_a_side():
    """颈线族（头肩/双顶底）方向是形状本身给的 —— 这一半必须照说。"""
    struct = {"daily": {"trend": "up"}, "weekly": None, "regime": {"cap_pct_nav": 0.4},
              "next_gate": None, "zone_position": "waiting", "walls": {}}
    p = {"name": "DTOP", "cn": "双顶", "direction": "bearish", "state": "shaped",
         "confirm_if": "收盘跌破颈线 X", "invalidate_if": "收盘站上 Y",
         "evidence": "e", "window": 120, "lo_calibrated": False}
    assert "下行方向" in " ".join(CR.opportunity(struct, [p])["items"])


def test_opportunity_points_at_gates_never_at_sizes(report):
    """「机会」只能指路：出现股数/金额就是判读层在越权算 size。"""
    items = " ".join(report["opportunity"]["items"])
    assert not re.search(r"\d+\s*股", items)
    assert not re.search(r"\$\s*\d", items)
    assert "entry_guard" in report["opportunity"]["disclaimer"]


def test_cap_zero_card_says_pattern_does_not_unlock(monkeypatch):
    """cap 0 的卡上，形态判读**不改变授权**—— 这句必须出现在输出里，
    否则一个漂亮的双底会被读成「可以进了」。"""
    card = {**CARD, "agent": {**CARD["agent"],
                              "current_regime": {**CARD["agent"]["current_regime"],
                                                 "cap_pct_nav": 0}}}
    bars = _bars(_walk(320))
    monkeypatch.setattr(CR, "load_bars", lambda t, asof=None: (bars, None))
    monkeypatch.setattr(CR, "load_card", lambda t: card)
    monkeypatch.setattr(CR, "rel_volume", lambda b, t=None: (1.0, "n", False))
    txt = CR.render(CR.read("TEST"))
    assert "不改变这一点" in txt


def test_missing_next_gate_is_called_out(monkeypatch):
    """没有上行转移轴的卡要被点名 —— 「没有 gate」= 这张图还没读完。"""
    card = {**CARD, "agent": {k: v for k, v in CARD["agent"].items() if k != "next_regime"}}
    bars = _bars(_walk(320))
    monkeypatch.setattr(CR, "load_bars", lambda t, asof=None: (bars, None))
    monkeypatch.setattr(CR, "load_card", lambda t: card)
    monkeypatch.setattr(CR, "rel_volume", lambda b, t=None: (1.0, "n", False))
    txt = CR.render(CR.read("TEST"))
    assert "没有上行转移轴" in txt


def test_short_history_degrades_instead_of_crashing(monkeypatch):
    monkeypatch.setattr(CR, "load_bars", lambda t, asof=None: (_bars(_walk(10)), None))
    monkeypatch.setattr(CR, "load_card", lambda t: None)
    r = CR.read("TEST")
    assert r.get("error") and "不足" in r["error"]
    assert "TEST" in CR.render(r)


def test_asof_declares_its_lookahead(monkeypatch):
    """`--asof` 只截断价格、不回溯卡片 = 前视。**必须自己说出来**——
    否则复盘样张会被当成历史证据（design §11.5 注 1 的同款陷阱）。"""
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
# 4. 周线重采样（B1：唯一有顶刊证据的"结合"形式）
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
    """未走完的那一周不算 —— 与「未完成 bar 不是收盘」同一条纪律。
    周三读一根「本周」K 线和盘中读当日 bar 是同一种错误，只是慢了五倍。"""
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
# 5. 宽度观察层（B3a）+ 价值区（B0-4）
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
    """宽度起步版 **DoF = 0**：只播报数值，不设阈值、不判定、不进任何授权路径。
    一旦给它一条线，它就从观察层变成会改仓位的东西 —— 那要走预注册。"""
    deep = tmp_path / "deep"
    deep.mkdir()
    _write_jsonl(deep / "UP.jsonl", [100 + i for i in range(80)])       # 站上 MA50
    _write_jsonl(deep / "DOWN.jsonl", [200 - i for i in range(80)])     # 跌破 MA50
    monkeypatch.setattr(CR, "KLINE_DEEP_DIR", deep)
    monkeypatch.setattr(CR, "book_tickers", lambda: [])
    CR._BREADTH_CACHE.clear()
    b = CR.market_breadth()
    assert b["pool"]["pct_above_50dma"] == 50.0 and b["pool"]["n"] == 2
    # 观察层不许出现任何判定字段
    import json as _json
    assert not any(k in _json.dumps(b) for k in ("verdict", "signal", "alert", "cap"))


def test_breadth_reports_pool_staleness_loudly(report, monkeypatch):
    """深度池不入 git、**无刷新 SLA**。落后了就必须喊 ——
    静默用一个 18 天前的宽度冒充今天，是「静默降级」的教科书形状。"""
    r = dict(report)
    r["context"] = {**r["context"],
                    "breadth": {"book": {"pct_above_50dma": 42.0, "n": 12},
                                "pool": {"pct_above_50dma": 49.0, "n": 166},
                                "pool_asof": "2026-07-24"}}
    r["asof"] = "2026-08-11"
    txt = CR.render(r)
    assert "🚫落后18天" in txt and "不是今天的" in txt


def test_breadth_declares_the_proxy_bias(report):
    """`klines_deep` **不是任何指数的成分**—— 不写口径就是
    「全池固定阈值 = 没有阈值」的同款错误。"""
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
    """财报重画 SLA **复用**`core.earnings.cards_stale_after_print`（brief 的
    `INFRA:card_stale_earnings` 同一份判据）—— 判读层不许自己再实现一遍。"""
    from quant.core import earnings as earn
    assert "cards_stale_after_print" in Path(earn.__file__).read_text()
    assert "cards_stale_after_print" in SRC_READ


def test_stale_card_warning_is_printed_before_the_map(report, monkeypatch):
    """卡锚早于财报反应日 ⇒ 下面那张 zone/gate 图**不含反应日的信息**。
    不说出来，判读会拿一张已知过期的地图讲得很确定 —— 本仓最熟的那种静默。"""
    r = dict(report)
    r["card_stale_earnings"] = "NBIS print 2026-08-12 BMO → 反应日 2026-08-12, 卡锚停在 2026-08-06"
    txt = CR.render(r)
    assert "待重画" in txt and "不含反应日" in txt
    assert txt.index("待重画") < txt.index("② Structure")   # 必须在图之前
    assert "🚨待重画" in CR.digest([r])                       # 扫描表也不许漏


def test_stale_judge_failure_does_not_break_the_read(monkeypatch, tmp_path):
    """日历缺失/接口变动 → 降级为"没有告警"，**不阻断判读**
    （判读层不该因为一个情报源挂了就整页不出）。"""
    monkeypatch.setattr(CR, "EARNINGS_CALENDAR_PATH", tmp_path / "nope.json")
    CR._STALE_CACHE.clear()
    assert CR.earnings_stale_cards("2026-08-12") == {}
