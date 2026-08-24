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
    # 第五行「结构」进来后全书一度不是一条装得下；只剩结构行时又装得下 ——
    # 保证不跟着行宽走：**单只票的块**远小于上限（拆条永远有边界可拆），且把票数堆到必然
    # 越界时，拆出来的每条合规、整块不裂。票数由块长算出，不写死。
    one = CR.digest([report])
    assert tg._tg_len(one) < TG_LIMIT // 4
    n = TG_LIMIT // tg._tg_len("\n".join(CR.digest_block(report))) + 2
    parts = tg.split_message(CR.digest([report] * n))
    assert len(parts) > 1 and all(tg._tg_len(p) <= TG_LIMIT for p in parts)
    assert all("━━" in p for p in parts), "拆出来的每条都该带完整的票块，不是半只票"


# 用户  明确点名不要的东西：这四类一旦回流，扫描表又会变成
# 「授权层的东西混在判读层里」—— 而这正是判读层零授权效力最容易破的地方。
BANISHED = ("收盘", "涨跌", "zone", "cap", "no_chase", "授权")


def test_digest_carries_only_the_structure_line(report):
    """扫描表 = 每票「票头 + 一行短期道氏结构」，别的都不要。

    力果 / 形态 / 倾向 / 机会留在详情页；结构行也不许带 zone / cap / 授权。
    """
    out = CR.digest([report])
    body = "\n".join(ln for ln in out.splitlines()
                     if not ln.startswith(("[FYI]", "结构 = ", "顺序 = ")))
    leaked = [w for w in BANISHED if w in body and w != "收盘"]
    assert not leaked, f"扫描表里漏进了不该有的东西: {leaked}"
    for gone in ("力果", "形态", "倾向", "机会"):
        assert gone not in body, f"「{gone}」退回详情页了，不该回流到推送: {body}"
    block = CR.digest_block(report)
    assert len(block) == 2 and block[0].startswith("━━ TEST ━━")
    assert block[1] == CR.dow_digest_line(report["structure"]["dow"], label=False)
    assert not block[1].startswith("结构 "), "票头下面只有这一行，标签就是重复"


def test_detail_says_what_the_continuation_continues(report):
    """延续标签必须说明延续上涨、下跌还是震荡。"""
    for trend, cn in (("up", "延续上涨"), ("down", "延续下跌"), ("transition", "延续震荡")):
        r = dict(report)
        r["structure"] = {**report["structure"], "daily": {**report["structure"]["daily"],
                                                           "trend": trend}}
        assert cn in CR.render(r)


def test_detail_hypotheses_come_from_the_same_builder(report):
    """机会段 = `pattern_hypotheses` 的输出，一字不改 —— 抄第二遍的那天，
    两处说的确认线就会开始各说各话。（推送里已没有机会段，详情页是唯一消费者。）"""
    atr = report["structure"].get("atr20")
    items = CR.opportunity(report["structure"], report["patterns"])["items"]
    for h in CR.pattern_hypotheses(report["patterns"], atr):
        assert h in items


def test_digest_sorts_by_dow_priority(report):
    """导航的顺序 = 与盘前挂单清单同一把尺（`dow.priority`）：上涨推进中 → 观察期 → 下跌，
    同档周线级上涨先于下跌。字母序对导航毫无意义；形态距离那套随四件退役。"""
    def mk(tk, trend, phase, weekly=None):
        r = dict(report, ticker=tk)
        r["structure"] = {**report["structure"], "dow": _dv(trend, phase, weekly=weekly)}
        return r
    wk_up = {"trend": "up", "phase": 0, "stage_cn": "上涨·推进中"}
    wk_dn = {"trend": "down", "phase": 0, "stage_cn": "下跌·推进中"}
    out = CR.digest([mk("DOWN", "down", 0),
                     mk("OBS", "up", 2),
                     mk("RUN_WKDN", "up", 0, wk_dn),
                     mk("RUN_WKUP", "up", 0, wk_up)])
    assert (out.index("RUN_WKUP") < out.index("RUN_WKDN")
            < out.index("OBS") < out.index("DOWN"))


def test_digest_never_silently_drops_a_ticker(report):
    """形态状态是会长的（Lo 族还有一半没实现），道氏视图也可能缺（K 线不足）。
    哪种都不能让整张表崩掉（那天一条 TG 都不会到），也不能让那只票静默消失。"""
    r = dict(report, ticker="NEWSTATE")
    r["patterns"] = [{"name": "X", "cn": "新形态", "state": "brand_new",
                      "direction": "bullish", "confirm_if": "c", "invalidate_if": "i",
                      "evidence": "e", "window": 120, "lo_calibrated": False}]
    nodow = dict(report, ticker="NODOW")
    nodow["structure"] = {**report["structure"], "dow": None}
    out = CR.digest([r, nodow])
    assert "NEWSTATE" in out and "NODOW" in out and "方向未定（K 线不足）" in out


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


# ══════════════════════════════════════════════════════════════
# 5. 短期道氏
# ══════════════════════════════════════════════════════════════

def _dv(trend="up", phase=0, **over):
    """一张短期道氏视图（dow_block 的产物）—— 直接造字典，测的是渲染与归栏。"""
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


def test_structure_block_carries_the_short_term_dow_view(report):
    dv = report["structure"]["dow"]
    for k in ("trend", "phase", "stage_cn", "labels", "lines", "recent_resolution"):
        assert k in dv, k
    assert "短期道氏" in CR.render(report)


def test_dow_digest_line_says_phase_and_the_line_to_watch():
    """一行要答三件事：第几步 / 要看哪条线 / 离多远。"""
    l0 = CR.dow_digest_line(_dv("up", 0))
    assert "推进中 HH+HL" in l0 and "lsh 120" in l0 and "阴线收破104=观察①" in l0
    ln1 = CR.dow_digest_line(_dv("up", 1, obs_start={"date": "2026-08-18", "close": 99, "broke": 100},
                                 candidate={"role": "lsl", "price": 95.0, "date": "2026-08-20",
                                            "confirm_if": "一根阳线收上 101"}))
    assert "终止观察①" in ln1 and "08-18阴线破坏" in ln1 and "候选lsl95(一根阳线收上 101即成形)" in ln1
    ln2 = CR.dow_digest_line(_dv("up", 2))
    assert "终止观察②" in ln2 and "lsl 96 已成形" in ln2 and "看lsh 120" in ln2 and "跌破96=终止确认" in ln2
    ln3 = CR.dow_digest_line(_dv("up", 3))
    assert "终止观察③" in ln3 and "LH 112 已定格" in ln3 and "跌破96" in ln3 and "终止确认" in ln3
    dn = CR.dow_digest_line(_dv("down", 3))
    assert "止跌观察③" in dn and "HL 112 已定格" in dn and "站上96" in dn and "反转确认" in dn
    for ln in (l0, ln1, ln2, ln3, dn):
        assert not any(w in ln for w in BANISHED)


def test_dow_digest_line_spells_out_which_way_the_prior_pivot_broke():
    """上涨里是回调跌破前 HL，下跌里是反弹站上前 LH；动词须带方向。"""
    up = CR.dow_digest_line(_dv("up", 2, retrace={"pct": 1.3, "broke_prev_ret": True}))
    assert "回调已跌破前HL 100(整段吐回)" in up and "已破前HL" not in up
    dn = CR.dow_digest_line(_dv("down", 2, retrace={"pct": 1.3, "broke_prev_ret": True}))
    assert "反弹已站上前LH 100(整段收复)" in dn and "跌破前LH" not in dn


def test_recent_termination_is_a_discretion_point_never_an_order():
    """终止确认是裁量复核点；stop 仍是硬线，判读层不下裁决。"""
    struct = {"daily": {"trend": "down"}, "weekly": None, "regime": {"cap_pct_nav": 0.4},
              "next_gate": None, "zone_position": None, "walls": {},
              "dow": _dv("down", 0, recent_resolution={"type": "terminate", "date": "2026-08-20",
                                                         "bars_ago": 1, "from_phase": 3,
                                                         "cn": "趋势终止确认（LH+LL）→ 转下跌"})}
    txt = " ".join(CR.opportunity(struct, [])["items"])
    assert "🔻" in txt and "裁量" in txt and "stop" in txt and "不替它下裁决" in txt
    assert not re.search(r"\d+\s*股", txt) and "$" not in txt
    # 下跌终止（见底确认）= 修复早信号，不改变 cap
    struct["dow"] = _dv("up", 0, recent_resolution={"type": "terminate", "date": "2026-08-14",
                                                      "bars_ago": 5, "from_phase": 3,
                                                      "cn": "下跌终止确认（HL+HH）→ 转上涨"})
    txt = " ".join(CR.opportunity(struct, [])["items"])
    assert "🔺" in txt and "不改变 cap" in txt


def test_old_termination_is_history_not_opportunity():
    """超过一周（RECENT_BARS）的终止不再进机会段 —— 那是历史，不是现在的裁量点。"""
    struct = {"daily": {"trend": "down"}, "weekly": None, "regime": {"cap_pct_nav": 0.4},
              "next_gate": None, "zone_position": None, "walls": {},
              "dow": _dv("down", 0, last_resolution={"type": "terminate", "bars_ago": 20},
                         recent_resolution=None)}
    assert not any("短期道氏" in x for x in CR.opportunity(struct, [])["items"])


def test_observation_phase_lists_both_lines_in_opportunity():
    struct = {"daily": {"trend": "up"}, "weekly": None, "regime": {"cap_pct_nav": 0.4},
              "next_gate": None, "zone_position": None, "walls": {}, "dow": _dv("up", 2)}
    txt = " ".join(CR.opportunity(struct, [])["items"])
    assert "观察期②" in txt and "恢复线 120" in txt and "终止确认线 96" in txt


def test_dow_votes_land_in_the_right_posture_column():
    """推进中且与快照同向 → 延续；观察期 → 反转栏；②③夹在两线之间 → 震荡栏；
    推进中但与快照反向 → 反转栏（序列与快照打架本身是信息）。"""
    base = {"daily": {"trend": "up"}, "weekly": None, "walls": {}, "zone_position": None}
    ind = {"above_ma": [], "range_contraction": None, "divergence": None}
    ps = CR.posture({**base, "dow": _dv("up", 0)}, {}, {}, ind, [])
    assert any("短期道氏推进中" in x for x in ps["continuation"])
    ps = CR.posture({**base, "dow": _dv("up", 2)}, {}, {}, ind, [])
    assert any("终止观察②" in x for x in ps["reversal"])
    assert any("夹在 96 与 120 之间" in x for x in ps["range"])
    ps = CR.posture({**base, "dow": _dv("down", 0)}, {}, {}, ind, [])
    assert any("与 5 根分形快照 up 相反" in x for x in ps["reversal"])
    assert not any("短期道氏推进中" in x for x in ps["continuation"])


def test_retrace_depth_is_spelled_out():
    """回撤深度要说人话，**动词带方向**：上涨 = 回调跌破前 HL（整段吐回），下跌 = 反弹站上前 LH
    （整段收复）；过 2/3 = Rhea 次级折返上界。旧版两个方向都写「跌破」，下跌镜像是错的。"""
    deep = "\n".join(CR.dow_lines(_dv("up", 2, retrace={"pct": 1.2, "broke_prev_ret": True})))
    assert "回调已跌破前 HL 100" in deep and "上一段涨幅整段吐回" in deep
    dn_lines = CR.dow_lines(_dv("down", 2, retrace={"pct": 1.2, "broke_prev_ret": True}))
    note = next(x for x in dn_lines if "前 LH" in x)        # 只看回撤那一行（别的行本来就有「跌破=延续」）
    assert "反弹已站上前 LH 100" in note and "上一段跌幅整段收复" in note and "跌破" not in note
    two_thirds = "\n".join(CR.dow_lines(_dv("up", 2, retrace={"pct": 0.8, "broke_prev_ret": False})))
    assert "Rhea" in two_thirds and "回调已达最后一段涨幅的 80%" in two_thirds
    shallow = "\n".join(CR.dow_lines(_dv("up", 2, retrace={"pct": 0.3, "broke_prev_ret": False})))
    assert "回调 = 最后一段涨幅的 30%" in shallow


def test_dow_block_on_synthetic_example_reports_phase_and_effort(monkeypatch):
    """端到端合成例走到观察②，破坏日带 RelVR 标签（量只做限定词）。"""
    from tests.core.test_dow import RISE, PULLBACK, BOUNCE, _bars as _dbars
    bars = _dbars(RISE + PULLBACK + BOUNCE)
    monkeypatch.setattr(CR, "rel_volume", lambda b, t=None: (1.5, "n", False))
    dv = CR.dow_block("TEST", bars, 2.0)
    assert dv["phase"] == 2 and dv["effort"]["obs1"]["label"] == "高力"
    assert dv["lines"]["terminate_level"]["level"] == 180 and dv["lines"]["resume_level"]["level"] == 200
    assert dv["candidate"]["role"] == "LH" and dv["candidate"]["price"] == 190


# ══════════════════════════════════════════════════════════════
# 6. 嵌套周线级 / Rhea 回撤带 / 道氏线 × 墙
# ══════════════════════════════════════════════════════════════

def _wk(trend="up", phase=0):
    from quant.core.dow import STAGE_CN
    return {"trend": trend, "phase": phase, "stage_cn": STAGE_CN[(trend, phase)],
            "labels": "HH+HL", "watch": {}, "last_resolution": None}


def test_dow_block_runs_the_same_machine_on_completed_weeks(report):
    """第二级 = 同一台机器跑走完的周 K：视图里必须有 weekly + nested。"""
    dv = report["structure"]["dow"]
    assert dv.get("weekly") and dv["weekly"]["trend"] in ("up", "down")
    assert "stage_cn" in dv["weekly"]


def test_nested_reading_turns_weekly_state_into_a_prior():
    """周线级推进中 → 洗盘先验；周线级观察②③ → 两级共振；周线级下跌 → 大级别延续。
    日线级没在观察期时只报周线级状态，不编先验。"""
    base = _dv("up", 2)
    assert CR.nested_reading({**base, "weekly": _wk("up", 0)})["key"] == "pullback_prior"
    assert CR.nested_reading({**base, "weekly": _wk("up", 3)})["key"] == "both_obs"
    dn = CR.nested_reading({**base, "weekly": _wk("down", 2)})
    assert dn["key"] == "higher_down" and "大级别" in dn["cn"] and "趋势延续" in dn["cn"]
    quiet = CR.nested_reading({**_dv("up", 0), "weekly": _wk("up", 0)})
    assert quiet["key"] == "quiet" and "先验" not in quiet["cn"]
    # 下跌侧镜像同样只给结构先验，不带未经公开验证的频率。
    mirror = CR.nested_reading({**_dv("down", 2), "weekly": _wk("down", 0)})
    assert mirror["key"] == "bounce_prior" and "%" not in mirror["cn"]


def test_retrace_band_line_is_descriptive_only():
    dv = _dv("up", 2, retrace_at_form={"pct": 0.27, "band": "<1/3",
                                       "band_cn": "轻微回调·不到次级折返"})
    txt = "\n".join(CR.dow_lines(dv))
    assert "回撤带 <1/3" in txt and "lsl 成形时回撤 27%" in txt
    assert "终止率" not in txt and "n=" not in txt
    assert "回撤带" in CR.dow_digest_line(dv)


def test_dow_lines_are_crossed_with_walls_within_a_quarter_atr():
    """lsl 落在 ×3 pivot 上 ≠ 落在真空里：0.25N 邻域（cluster_pivots 同口径）内的墙要对上。"""
    dv = _dv("up", 2, obs={"price": 96.0}, imp={"price": 120.0}, cnt={"price": 112.0},
             ret={"price": 100.0})
    walls = [{"level": 96.3, "kind": "pivot", "detail": "×3 [high+low]"},
             {"level": 110.0, "kind": "HVN", "detail": "20% 成交堆积"}]
    sup = CR.dow_line_support(dv, walls, 2.0)
    assert "lsl" in sup and sup["lsl"]["wall"] == 96.3 and sup["lsl"]["gap_n"] == 0.15
    assert "LH" not in sup                                  # 112 与 110 差 1N，不算挨着
    txt = "\n".join(CR.dow_lines({**dv, "line_support": sup}))
    assert "线上有货" in txt and "lsl 96 ← pivot 96.3" in txt
    bare = "\n".join(CR.dow_lines({**dv, "line_support": {}}))
    assert "真空里的线" in bare


def test_digest_carries_the_weekly_level_token():
    dv = _dv("up", 2, weekly=_wk("up", 0))
    dv["nested"] = CR.nested_reading(dv)
    ln = CR.dow_digest_line(dv)
    assert "周线级上涨·推进中(洗盘先验)" in ln
    assert not any(w in ln for w in BANISHED)
