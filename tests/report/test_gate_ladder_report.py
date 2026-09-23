"""gate_ladder_report 渲染语义 — streak 口径复用 entry_guard, 这里测组装与状态标记。"""
import json

import pytest

from quant.core import technical
from quant.decision import regime_transition as rt
from quant.report import gate_ladder_report as glr


@pytest.fixture(autouse=True)
def _no_bench(monkeypatch):
    """量腿基准隔离 (volume_leg  起相对口径): fixture 是真实  日期,
    不隔离会读 live 缓存的 QQQ/SMH 真数据。置 None = 绝对兜底路径。"""
    monkeypatch.setattr(rt, "_bench_ratio", lambda bench, day: None)


def _bars(closes, vol=1000):
    return [{"time": f"2026-07-{i+1:02d} 00:00:00", "open": c, "high": c + 1,
             "low": c - 1, "close": c, "volume": vol} for i, c in enumerate(closes)]


AGENT = {
    "zones": {"accumulation_zone_1": [137.0, 146.0], "stop_level": 124.0,
              "reclaim_gates": [{"level": 156, "days": 2, "label": "止跌信号"}]},
    "next_regime": {"name": "B_repair_probe_172.5",
                    "entry_gate": {"level": 172.5, "days": 2, "unlocks": "probe"},
                    "next_gate_preview": {"level": 187, "days": 2, "unlocks": "confirmed"}},
    "action": {"constraints": {"max_classification": "probe", "until": "2026-08-12",
                               "reason": "财报 binary"}},
}


def test_ladder_of_sorted_and_tagged():
    lad = glr.ladder_of(AGENT)
    assert [g["level"] for g in lad] == [156, 172.5, 187]
    # 语义分层: gate 行只用结构层词汇, unlocks (probe/confirmed) 不再渲染
    assert lad[0]["tag"] == "止跌线"
    assert lad[1]["tag"] == "收复线" and lad[2]["tag"] == "修复线"
    assert not any("probe" in g["tag"] or "confirmed" in g["tag"] for g in lad)


def test_render_states_untouched_counting_and_established():
    # 收盘序列: ...170, 175, 180 → 156 成立 (3 连>156, 需 2), 172.5 计数 2/2 成立, 187 未触及
    bars = _bars([150] * 20 + [170, 175, 180])
    out = "\n".join(glr.render_ticker("NBIS", AGENT, bars))
    assert "✅ 156" in out and "2/2 成立" in out
    assert "✅ 172.5" in out
    assert "▫️ 187" in out and "距 +3.9%" in out
    # 老卡遗留 max_classification → 播报"已退役" (走 sizing.constraint_lines canonical),
    # 不再渲染成活约束 (分类轴退役)
    assert "已退役" in out and "max_classification" in out


def test_constraint_retired_field_never_reads_as_binding():
    """遗留 max_classification 无论窗口状态都不许播成活约束 —— 只播退役警告。"""
    agent = json.loads(json.dumps(AGENT))
    agent["action"]["constraints"]["from"] = "2026-08-05"
    bars = _bars([150] * 20 + [170, 175, 180])          # 快照日 =
    out = "\n".join(glr.render_ticker("NBIS", agent, bars))
    assert "已退役" in out
    assert "🔒 卡片约束" not in out


def test_gate_volume_floor_annotated_on_established_row():
    """卡片 gate 写了 volume_ratio_min: 收盘 streak 过但量腿未达 → ✅ 行必须带
    ⚠️量能闸未过 (regime_transition.volume_leg 同一函数, 兜底路径); 达标 → 不加噪音。"""
    agent = json.loads(json.dumps(AGENT))
    agent["next_regime"]["entry_gate"]["volume_ratio_min"] = 1.2
    bars = _bars([150] * 20 + [170, 175, 180])          # 等量 → 末 bar 量比 1.0 < 1.2
    out = "\n".join(glr.render_ticker("NBIS", agent, bars))
    assert "✅ 172.5" in out and "量能闸未过" in out and "1.00 < 1.2" in out
    bars[-1]["volume"] = 3000                           # 放量 → 闸过, 无标注
    out2 = "\n".join(glr.render_ticker("NBIS", agent, bars))
    assert "✅ 172.5" in out2 and "量能闸未过" not in out2


def test_gate_volume_relative_leg_in_report(monkeypatch):
    """报表与转移机同一量腿: tape 缩量 (基准 0.5) → RelVR 2.0 过闸, 警告消失 —
    报了不 binding 那条 = 叙述层 bug, 用同一函数杜绝。"""
    monkeypatch.setattr(rt, "_bench_ratio", lambda bench, day: 0.5)
    agent = json.loads(json.dumps(AGENT))
    agent["next_regime"]["entry_gate"]["volume_ratio_min"] = 1.2
    bars = _bars([150] * 20 + [170, 175, 180])          # 自身 1.0×, 相对 2.0
    out = "\n".join(glr.render_ticker("NBIS", agent, bars))
    assert "✅ 172.5" in out and "量能闸未过" not in out


def test_uptrend_gates_renamed_breakout():
    """up 趋势确认后 (HH+HL) 收复/修复线自动换名突破线; 止跌线 (修复遗留物) 不改。"""
    closes = [100, 105, 110, 106, 102, 107, 115, 111, 106, 113, 121, 117, 112, 119, 127]
    bars = _bars(closes)
    out = "\n".join(glr.render_ticker("UP", AGENT, bars))
    assert "突破线" in out and "收复线" not in out and "修复线" not in out
    assert "止跌线" in out


def test_render_counting_midway():
    bars = _bars([150] * 20 + [174])          # 172.5 之上仅 1 收盘 → ⏳ 1/2
    out = "\n".join(glr.render_ticker("NBIS", AGENT, bars))
    assert "⏳ 172.5" in out and "| 1/2 |" in out          # 确认列 ( 表格式)


def test_zone1_position_marked():
    bars = _bars([150] * 20 + [140])
    out = "\n".join(glr.render_ticker("NBIS", AGENT, bars))
    assert "📍 zone1" in out and "带内" in out


def test_volume_ratio_completion_day_convention():
    bars = _bars([100] * 25, vol=1000)
    bars[-1]["volume"] = 2000                 # SMA20 含完成日 = 1050 → 1.90×⚡
    line = glr.render_ticker("X", {"zones": {}}, bars)[0]
    assert "1.90×⚡" in line


def test_sector_tag_four_quadrants():
    assert glr.sector_tag(3.0, 2.0) == "放量上涨"
    assert glr.sector_tag(3.0, 0.5) == "缩量反弹⚠️"
    assert glr.sector_tag(-3.0, 2.0) == "放量下跌⚠️"
    assert glr.sector_tag(-3.0, 0.5) == "缩量回调"
    assert glr.sector_tag(0.5, 2.0) == ""       # 涨跌未达阈值不定性
    assert glr.sector_tag(3.0, None) == ""


def test_sector_lines_reads_smh_from_cache(tmp_path):
    bars = _bars([100.0] * 24 + [103.0], vol=1000)
    bars[-1]["volume"] = 500                     # 涨 3% 缩量 → 缩量反弹⚠️
    (tmp_path / "SMH.jsonl").write_text(
        "\n".join(json.dumps(b) for b in bars) + "\n")
    lines = glr.sector_lines(kdir=tmp_path)
    assert len(lines) == 1                       # QQQ 无缓存 → 跳过
    assert "SMH" in lines[0] and "+3.0%" in lines[0] and "缩量反弹⚠️" in lines[0]


def test_build_report_end_to_end(tmp_path):
    (tmp_path / "klines").mkdir()
    (tmp_path / "premium").mkdir()
    (tmp_path / "klines" / "AAA.jsonl").write_text(
        "\n".join(json.dumps(b) for b in _bars([150] * 20 + [174])) + "\n")
    wl = {"tickers": {"AAA": {"moomoo_listed": True, "agent": AGENT},
                      "GONE": {"moomoo_listed": False, "agent": {}}}}
    wlp = tmp_path / "watchlist.json"
    wlp.write_text(json.dumps(wl))
    pos = {"tickers": {}}
    posp = tmp_path / "positions.json"
    posp.write_text(json.dumps(pos))
    text = glr.build_report(wl_path=wlp, kdir=tmp_path / "klines", market_dir=tmp_path,
                            pos_path=posp)
    assert "AAA" in text and "GONE" not in text          # 移出成员不渲染
    assert "截至 2026-07-21" in text                      # asof = 完成 bar 日期
    # 钉的是**语义**不是字面: 报表必须说清"够到线 ≠ 可以下单"。
    #  改词 —— 旧文案 "gate 成立 ≠ 已转移 / watchlist 需人工确认" 是
    #  裁决 A 之前的话 (那之后 watchlist 也自动切), 真正的闸门在补画锁。
    assert "转移 ≠ 已授权" in text and "补画锁" in text
    assert "📌 持仓" not in text                          # 无持仓 → 不渲染空 section


def test_build_report_includes_holdings_section(tmp_path):
    """持仓卡同样带 gate 阶梯 (用户: 只报 watchlist 是覆盖缺口)。
    判据 = broker.qty (holdings 单一真源); 纪念仓 (base 豁免层) 无 gate 不渲染;
    已进持仓的 ticker 不在 watchlist section 重复出现。"""
    (tmp_path / "klines").mkdir()
    (tmp_path / "premium").mkdir()
    for tk in ("HELD", "AAA"):
        (tmp_path / "klines" / f"{tk}.jsonl").write_text(
            "\n".join(json.dumps(b) for b in _bars([150] * 20 + [174])) + "\n")
    pos = {"tickers": {
        "HELD": {"broker": {"qty": 2}, "agent": AGENT},
        "MEMO": {"broker": {"qty": 1}, "instrument": {"role": "commemorative_hold"},
                 "agent": {}},
        "FLAT": {"broker": {"qty": 0}, "agent": AGENT},
    }}
    posp = tmp_path / "positions.json"
    posp.write_text(json.dumps(pos))
    wl = {"tickers": {"AAA": {"moomoo_listed": True, "agent": AGENT},
                      "HELD": {"moomoo_listed": True, "agent": AGENT}}}  # 迁移间隙重复
    wlp = tmp_path / "watchlist.json"
    wlp.write_text(json.dumps(wl))
    text = glr.build_report(wl_path=wlp, kdir=tmp_path / "klines", market_dir=tmp_path,
                            pos_path=posp)
    assert "📌 持仓" in text and "👀 Watchlist" in text
    held_sec = text.split("📌 持仓")[1].split("👀 Watchlist")[0]
    wl_sec = text.split("👀 Watchlist")[1]
    assert "HELD" in held_sec and "⏳ 172.5" in held_sec  # 持仓 gate 阶梯照渲染
    assert "MEMO" not in text and "FLAT" not in text      # 纪念仓/已平仓不渲染
    assert "HELD" not in wl_sec and "AAA" in wl_sec       # 不重复渲染


# ── gate 距离 + ⚡ 快车道 () ────────────────────────────
#
# 立此节的直接原因:  我引 `fastlane_repair_gate_design.md` 的「不采纳」
# 去否定 用户 记得的规则, 漏了**同日**`ladder_entry_design.md` 的「采纳
# fastlane_k=0.5」—— 同一个词「快车道」在两份文档里一个否一个采纳。
# 否掉的是"幅度→D1 全额单点入场", 采纳的是"幅度→D1 分档 ladder"。
# 这些测试把 live 那一条 (采纳的那条) 钉在报表上。

def test_fastlane_only_on_the_transition_gate():
    """⚡ 只给 next_regime.entry_gate —— 止跌线/修复线 detect_transition 根本不扫,
    给它们标 ⚡ 就是编一个不存在的路径。"""
    kinds = {g["level"]: g["kind"] for g in glr.ladder_of(AGENT)}
    assert kinds == {156: "reclaim", 172.5: "entry", 187: "preview"}
    bars = _bars([150] * 20 + [160])          # 156 之上, 172.5/187 之下
    out = "\n".join(glr.render_ticker("NBIS", AGENT, bars))
    entry_line = [l for l in out.splitlines() if "172.5" in l][0]
    assert "⚡" in entry_line
    for lvl in ("156", "187"):
        assert "⚡" not in [l for l in out.splitlines() if lvl in l][0]


def _deep_repair():
    """①修复形状的深序列 (≥180 根, 破位日在 120 日窗内且收盘 < 172.5/187):
    ⓪域判据  接线后, 测 ①②③ 渲染的用例都要垫上它 —— 与 decision 层
    条件隔离同一条纪律 (不垫的话短 fixture 被域先拦, 测的不再是声称的那条)。"""
    return _bars([300.0] * 140 + [80.0] + [90.0 + i * 0.5 for i in range(60)])


def test_untouched_transition_gate_previews_both_prices():
    """▫️ 行给两个价: gate = 开始 ×2 计数; gate+0.5N = 当日转移免确认。"""
    bars = _bars([150] * 20 + [160])
    line = [l for l in glr.render_ticker("NBIS", AGENT, bars, deep=_deep_repair())
            if "▫️ 172.5" in l][0]
    assert "距 +7.8%" in line and "N)" in line     # 双单位
    assert "⚡ ≥" in line and "免 ×2" in line
    # 触发价 = gate + FASTLANE_K×ATR20, 数值来自 regime_transition 不另存一份
    assert glr.FASTLANE_K == 0.5


def test_counting_gate_row_carries_why_fastlane_missed():
    """⏳ 的两行展开 进同一格 (4b 表格式):
    卡在哪一条 + 还剩什么路, 一个字不少。"""
    bars = _bars([150] * 20 + [174])          # 首日站上 172.5, 幅度很小
    out = glr.render_ticker("NBIS", AGENT, bars, deep=_deep_repair())
    row = [l for l in out if "⏳ 172.5" in l][0]
    assert row.startswith("| ⏳ 172.5 | ") and "| 1/2 |" in row
    assert "上方 +0.9%" in row
    assert "⚡快车道未成" in row and "首日" in row
    assert "幅度" in row and "需收 ≥" in row
    assert "首日已消耗" in row and "只剩 ×2" in row


def test_ticker_block_is_bold_head_plus_gate_table(monkeypatch):
    """4b 表格式: 第 0 行粗体头 + 行内标签, gate 行是 4 列管道表,
    表头/对齐行紧跟, 富消息通道渲染成原生表格; 竖线转义不切列。"""
    bars = _bars([150] * 20 + [170, 175, 180])
    out = glr.render_ticker("NBIS", AGENT, bars)
    assert out[0].startswith("**NBIS $180 (+2.9%) 量比") and "🧭" in out[0] and "📍" in out[0]
    i = out.index(glr.LADDER_TABLE_HEADER[0])
    assert out[i + 1] == glr.LADDER_TABLE_HEADER[1]
    rows = [l for l in out[i + 2:] if l.startswith("| ")]
    assert [r.split(" | ")[0] for r in rows] == ["| ✅ 156", "| ✅ 172.5", "| ▫️ 187"]
    assert all(r.count(" | ") == 3 for r in rows)          # 恰好 4 列
    assert glr._cell("a|b\nc") == "a\\|b · c"


def test_fastlane_qualified_says_transition_in_flight():
    """报表若跑在 postclose 之前, 达标的那条是**转移在途**, 不是"没生效"。"""
    bars = _bars([150] * 20 + [200])          # 远超 172.5 + 0.5N
    out = glr.render_ticker("NBIS", AGENT, bars, deep=_deep_repair())
    line = [l for l in out if "⚡快车道达标" in l]
    assert line and "转移在途" in line[0]


def test_fastlane_not_applicable_after_first_day_is_consumed():
    """首日只有一次: 已站上去两天还没凑满的, 不许事后补一张快车票
    (regime_transition._fastlane ① 的报表镜像)。"""
    agent = json.loads(json.dumps(AGENT))
    agent["next_regime"]["entry_gate"]["days"] = 3
    bars = _bars([150] * 20 + [174, 175])     # 连 2 日 > 172.5, need 3
    out = glr.render_ticker("NBIS", agent, bars, deep=_deep_repair())
    line = [l for l in out if "⚡快车道不适用" in l]
    assert line and "首日已过" in line[0]


def test_established_transition_gate_gives_the_risk_read():
    """✅ = 可以挂 ladder 了 → 直接给 stop 与风险 N; 来源 [卡]/[约定] 标出来。"""
    agent = json.loads(json.dumps(AGENT))
    agent["next_regime"]["zones"] = {"stop_level": 165.0}
    bars = _bars([150] * 20 + [174, 176])
    line = [l for l in glr.render_ticker("NBIS", agent, bars) if "✅ 172.5" in l][0]
    assert "stop 165.00[卡]" in line and "风险" in line and "N" in line
    # 卡片没写 → 按 round-2b 约定 gate−0.5N 推, 并标 [约定]
    del agent["next_regime"]["zones"]
    line2 = [l for l in glr.render_ticker("NBIS", agent, bars) if "✅ 172.5" in l][0]
    assert "[约定]" in line2


def test_distance_is_dual_unit_because_percent_alone_is_not_comparable():
    """同一个 % 在不同波动率的票上是完全不同的距离 ——  实测
    MSFT 距 +6.5% = 2.33N, MU 距 +7.1% = 0.82N, 差 2.8 倍。"""
    calm = glr.gate_distance(100.0, 1.0, 110.0)      # ATR = 1% of price
    wild = glr.gate_distance(100.0, 10.0, 110.0)     # ATR = 10% of price
    assert calm == "距 +10.0% (10.00N)"              # 同一个 10%, 10 个波动单位
    assert wild == "距 +10.0% (1.00N)"               # 同一个 10%, 1 个波动单位
    # 两个方向分母不同是有意的: 上方按线归一 (与 📍 trim 行同口径), 下方按现价
    assert glr.gate_distance(110.0, 10.0, 100.0) == "上方 +10.0% (1.00N)"


def test_volume_note_compacted_but_degradation_kept_verbatim():
    """基准出处每行重复只会撑爆手机行 —— 但 abs 兜底是降级读数, 必须原样保留。"""
    assert glr.vol_short("量比(rel SMH 0.52) 4.27", True) == "量腿 4.27✓"
    assert glr.vol_short("量比(rel QQQ 0.74) 0.89", False) == "量腿 0.89✗"
    degraded = glr.vol_short("量比(abs兜底,SMH缺2026-08-07) 1.25", True)
    assert "abs兜底" in degraded and degraded.endswith("✓")


# ── gate 线名四分类换名 (用户 要求) ─────────────────────
#
# 旧规则只有两档 → 9 张 live 卡 8 张标成修复/收复线。新规则直接问破位史。
# ⚠️ 这些测试钉的是**名字**, 不是资格 —— 四分类  裁决全部不采纳。

def _deep(closes, start=0):
    """长到能跑判别式的合成序列 (≥ MIN_BARS)。"""
    from datetime import date, timedelta
    d0 = date(2020, 1, 1)
    return [{"time": f"{d0 + timedelta(days=start + i)} 00:00:00",
             "open": c, "high": c * 1.01, "low": c * 0.99, "close": c,
             "volume": 1_000_000} for i, c in enumerate(closes)]


def _triangle(n_cycles=15):
    """收敛三角: **抬高的低点**(永不创 60 日新低 ⇒ 无破位) + 走低的高点。

    ⚠️ 别用平的横盘造"没破过位": 平盘每个谷底都是 60 日最低收盘且在 MA60 之下,
    `episodes_of` 照样触发 —— 第一版测试就这么写的, 判出来是①修复。
    """
    out = []
    for k in range(n_cycles):
        lo, hi = 100 + 1.8 * k, 140 - 1.2 * k
        out += [lo + (hi - lo) * x / 9 for x in range(10)]
        out += [hi - (hi - lo) * x / 9 for x in range(10)]
    return out


def test_range_top_gate_is_no_longer_called_a_repair_line():
    """核心修正: 没破过位的 gate 不该叫收复/修复线 (MRVL 223 / MU 945 的形状)。"""
    bars = _deep(_triangle())
    agent = {"zones": {}, "next_regime": {"name": "R_x", "entry_gate": {"level": 125.0, "days": 2}}}
    atr = (technical.atr(bars, 20) or [None])[-1]
    trend = technical.swing_structure(bars)["trend"]
    assert glr.gate_path_of(125.0, bars, trend, atr) == "range_top"   # 先钉住 fixture 本身
    out = "\n".join(glr.render_ticker("BOX", agent, bars, deep=bars))
    assert "箱体顶" in out
    assert "收复线" not in out and "修复线" not in out


def test_repair_gate_keeps_the_repair_vocabulary():
    """真破过位的 gate 仍然是修复路径三词 —— 换名不是把修复语义扫掉。

    ⚠️ 崩塌要**突然**: 缓慢下跌时更早的那一天先成为 episode 并吃掉 COOLDOWN(120),
    真正的坑底反而不是 episode, 于是落不进 120 日窗 —— 第一版测试栽在这里。
    """
    closes = [200.0] * 200 + [80.0] + [82.0 + i * 0.4 for i in range(99)]
    bars = _deep(closes)
    agent = {"zones": {}, "next_regime": {"name": "R_x", "entry_gate": {"level": 120.0, "days": 2}}}
    atr = (technical.atr(bars, 20) or [None])[-1]
    trend = technical.swing_structure(bars)["trend"]
    assert glr.gate_path_of(120.0, bars, trend, atr) == "repair"      # 先钉住 fixture 本身
    out = "\n".join(glr.render_ticker("REP", agent, bars, deep=bars))
    assert "收复线" in out or "修复线" in out
    assert "箱体顶" not in out and "新高线" not in out


def test_gate_at_the_all_time_high_is_called_a_new_high_line():
    closes = [50.0 + i * 0.5 for i in range(300)]          # 一路新高, 从不破位
    bars = _deep(closes)
    top = closes[-1]
    agent = {"zones": {}, "next_regime": {"name": "R_x",
                                          "entry_gate": {"level": top + 0.1, "days": 2}}}
    out = "\n".join(glr.render_ticker("ATH", agent, bars, deep=bars))
    assert "新高线" in out


def test_reclaim_gates_are_never_renamed():
    """止跌线按构造就在修复路径上 —— 四分类不碰它。"""
    bars = _deep(_triangle())
    agent = {"zones": {"reclaim_gates": [{"level": 118.0, "days": 2}]},
             "next_regime": {"name": "R_x", "entry_gate": {"level": 125.0, "days": 2}}}
    out = "\n".join(glr.render_ticker("BOX", agent, bars, deep=bars))
    assert "止跌线" in out


def test_short_series_falls_back_to_the_whole_old_rule_and_says_so():
    """序列不够深 → 回落**整条**旧规则 (含 up→突破线), 并把降级说出口。

    ⚠️ 第一版回落只留了 lsh 那半, up 趋势的短序列卡当场掉回"收复线" ——
    拿不到新能力时应当退回原样, 不是退回半个原样。
    """
    closes = [100, 105, 110, 106, 102, 107, 115, 111, 106, 113, 121, 117, 112, 119, 127]
    bars = _bars(closes)
    out = "\n".join(glr.render_ticker("UP", AGENT, bars))
    assert "突破线" in out                       # 旧规则整条都在
    assert "线名回落旧规则" in out               # 且降级说出口, 不静默


def test_deep_cache_gap_is_reported_not_swallowed():
    """深度缓存不入 git 且会过期 —— 两段之间有洞必须报出来。"""
    old = _deep([100.0 + (i % 5) for i in range(200)], start=0)
    new = _deep([120.0 + (i % 5) for i in range(200)], start=900)   # 中间缺 ~700 天
    merged, note = glr.merge_bars_by_date(old, new)
    assert note and "序列断层" in note
    agent = {"zones": {}, "next_regime": {"name": "R_x", "entry_gate": {"level": 130.0, "days": 2}}}
    out = "\n".join(glr.render_ticker("GAP", agent, merged, deep=merged, deep_note=note))
    assert "线名按现有序列判" in out and "序列断层" in out


def test_merge_dedupes_instead_of_concatenating():
    """🚨  实测踩过: 直接拼接, 重叠段的重复 bar 污染 MA60, 分类当场算错。"""
    a = _deep([100.0 + i for i in range(50)])
    b = _deep([100.0 + i for i in range(50)])[30:]          # 与 a 重叠 20 根
    merged, note = glr.merge_bars_by_date(a, b)
    assert len(merged) == 50, f"去重失败, 拼成了 {len(merged)} 根"
    assert note is None
    assert [x["time"] for x in merged] == sorted(x["time"] for x in merged)


def test_fastlane_domain_is_driven_by_class_key_not_display_names():
    """⓪域  起**合法地**进了 ⚡ 判据 (用户 批「接判别式」) —— 但驱动必须是
    结构身份 key (`cls`), **不许是显示名**: 显示名会随趋势/回落规则换词, 拿它当判据
    = 让渲染层反向决定授权 (prose 当状态的老病)。"""
    import inspect
    src = inspect.getsource(glr.fastlane_state)
    assert 'cls != "repair"' in src, "域判据必须读结构身份 key"
    for w in ("箱体顶", "新高线", "突破线", "PATH_TAGS", 'g["tag"]', "g.get(\"tag\")"):
        assert w not in src, f"⚡ 判据里出现显示名相关的 {w} —— 授权不许读显示层"


def test_out_of_domain_gate_shows_no_fastlane_trigger_price():
    """域外 gate (MRVL 223 / MU 945 的形状) **不许再预告「⚡ ≥X 免 ×2」**——
    live _fastlane 不会发车, 预告就是在报一条不存在的路 (R4 那类渲染层分叉)。"""
    deep_box = _deep(_triangle())            # 收敛三角: 已被换名测试证明判③域外
    closes = [b["close"] for b in deep_box]
    # 用真判别式**断言**fixture 是域外 —— 不许 skip: skip 掉的守卫等于没有
    from quant.core.episodes import breakdown_in_window, episodes_of
    eps = episodes_of(closes, technical.sma(closes, 60))
    box_gate = 125.0
    broke, _, _ = breakdown_in_window(eps, closes, len(closes), box_gate)
    assert not broke, "fixture 意外含破位 —— 三角形状被改过?"
    agent = {"zones": {}, "next_regime": {"name": "R_box",
                                          "entry_gate": {"level": box_gate, "days": 2}}}
    bars = _bars([closes[-1] * 0.9] * 20 + [closes[-1] * 0.95])
    out = "\n".join(glr.render_ticker("BOX", agent, bars, deep=deep_box))
    assert "⚡域外" in out or "⚡域不可判" in out
    assert "免 ×" not in out, "域外的 gate 不许预告免 ×2 触发价"


def test_medium_series_still_falls_back_because_the_window_would_be_partial():
    """长到能算 ATR、却短于 MIN_BARS(180) 的序列 **也必须回落**。

    ⚠️ 这条隔离的是 `MIN_BARS` 那道门槛本身: 15 根的短序列 ATR 就算不出来,
    `atr20 is None` 先挡掉了 —— 拿掉 MIN_BARS 守卫**一条都不红**。
    100 根时 ATR/MA60/episodes_of 全算得出, 但 120 日回看窗只覆盖了一部分,
    `broke` 就是个不完整读数。宁可回落, 不可给一个算不满的身份。
    """
    bars = _deep([100.0 + i * 0.3 for i in range(100)])
    atr = (technical.atr(bars, 20) or [None])[-1]
    assert atr is not None, "fixture 要长到 ATR 算得出, 否则这条又测不到 MIN_BARS"
    assert len(bars) < glr.MIN_BARS
    assert glr.gate_path_of(200.0, bars, "up", atr) is None


# ── unresolved demotion marker ───────────────────────────────────

OVERDUE = {"ticker": "TST", "date": "2026-08-19", "old_stop": 95.0,
           "new_stop": 88.0, "reason": "synthetic close 94 < stop 95",
           "qty": 20.0, "opt_tickers": []}


def test_overdue_demotion_renders_the_fault_line():
    bars = _bars([100.0] * 18 + [97.0, 94.0]) + [
        {"time": "2026-08-20 00:00:00", "open": 94, "high": 96, "low": 93,
         "close": 95.0, "volume": 1000}]
    agent = {"zones": {"accumulation_zone_1": [89.0, 93.0], "stop_level": 88.0}}
    out = "\n".join(glr.render_ticker("TST", agent, bars, held=True, overdue=OVERDUE))
    assert "⬇" in out
    assert "95" in out and "88" in out, "断层两半 (旧→新 stop) 都要说出口"
    assert "D+1" in out                       # fixture 里 8/20 bar 已收
    assert "override" in out


def test_no_overdue_no_fault_line():
    bars = _bars([130.0] * 20 + [131.0])
    agent = {"zones": {"accumulation_zone_1": [89.0, 93.0], "stop_level": 88.0}}
    out = "\n".join(glr.render_ticker("TST", agent, bars, held=True))
    assert "⬇" not in out


def test_gate_volume_leg_from_an_earlier_streak_day_is_reported_as_passed(monkeypatch):
    """对齐日 = streak 内任一日: streak 首日放量、完成日平量 →
    报表与转移机同一份: 不再标「量能闸未过」, ✅ 行写明量腿来自哪一日。"""
    monkeypatch.setattr(rt, "_bench_ratio", lambda bench, day: 1.0)
    agent = json.loads(json.dumps(AGENT))
    agent["next_regime"]["entry_gate"]["volume_ratio_min"] = 1.2
    bars = _bars([150] * 20 + [170, 175, 180])          # streak = 175, 180 (>172.5)
    bars[-2]["volume"] = 3000                           # streak 首日放量, 完成日平量
    out = "\n".join(glr.render_ticker("NBIS", agent, bars))
    assert "✅ 172.5" in out and "量能闸未过" not in out
    assert "量腿 ✓" in out and "streak第1日" in out
    assert rt.streak_above(bars, 172.5) == len(glr._gate_streak(bars, 172.5)) == 2


# ── 🔭 远端行 (阶梯顶之后的几条 gate, 与 moomoo 画线对齐) ──────────

def _peaky_bars(peaks, base=100.0, n=120):
    """平底 base 上按 {bar 序号: 高点} 插 swing high (tests/core/test_future_gates 同款)。"""
    import datetime as dt
    out = []
    for i in range(n):
        t = (dt.date(2025, 1, 1) + dt.timedelta(days=i)).isoformat()
        out.append({"time": f"{t} 00:00:00", "open": base, "high": peaks.get(i, base + 1),
                    "low": base - 1, "close": base, "volume": 1000, "turnover": base * 1000})
    return out


FAR_AGENT = {"zones": {"valid_until": "2099-01-01"},
             "next_regime": {"name": "R_next", "entry_gate": {"level": 104.0, "days": 2},
                             "next_gate_preview": {"level": 106.0, "days": 2}}}
FAR_BARS = _peaky_bars({10: 110.0, 20: 110.4, 30: 120.0, 40: 120.5, 60: 130.0})


def test_far_gate_rows_follow_the_card_ladder_and_never_count():
    block = "\n".join(glr.render_ticker("XX", FAR_AGENT, FAR_BARS))
    far = [ln for ln in block.splitlines() if glr.FAR_MARK in ln]
    assert [ln.split("|")[1].strip() for ln in far] == [
        f"{glr.FAR_MARK} 110.4", f"{glr.FAR_MARK} 120.5", f"{glr.FAR_MARK} 130"]
    assert [ln.split("|")[2].strip() for ln in far] == ["远端 +3", "远端 +4", "远端 +5"]
    assert all(ln.split("|")[3].strip() == "—" for ln in far)      # 不是转移轴: 不计数
    assert "簇×2H" in far[0] and "距 +10.4%" in far[0] and "窗高" in far[2]
    assert not any("⚡" in ln or "量腿" in ln for ln in far)          # 无快车道 / 无量腿
    lines = block.splitlines()
    assert lines.index(far[0]) > max(i for i, ln in enumerate(lines) if "106" in ln)   # 排在卡片阶梯之后


def test_far_gate_rows_are_the_same_lines_moomoo_gets():
    """渲染口径 = 判定口径: TG 表的 `远端 +N` 与手机备注 `gate:+N <收据>` 逐条对得上 ——
    同一个 for_card, 两个渲染层不许各数各的。"""
    from quant.sync import price_reminder_sync as prs
    wl = {"tickers": {"XX": {"moomoo_listed": True, "agent": FAR_AGENT}}}
    far_map = prs.future_gate_map({"tickers": {}}, wl, bars_of=lambda t: FAR_BARS)
    lines, _ = prs.desired_lines({"tickers": {}}, wl, "2026-08-05", far_gates=far_map)
    phone = {d["note"].split(" ", 1)[0]: (d["value"], d["note"].split(" ", 1)[1])
             for d in lines if d["kind"] == "fgate" and " " in d["note"]}
    rows = [ln.split("|") for ln in glr.far_gate_rows(FAR_AGENT, FAR_BARS, 100.0, 2.0)]
    report = {"gate:" + r[2].strip().split()[-1]:
              (float(r[1].replace(glr.FAR_MARK, "").strip()), r[4].split(" · ")[-1].strip())
              for r in rows}
    assert report == phone and len(report) == 3


def test_no_axis_or_no_history_renders_no_far_rows():
    assert glr.far_gate_rows({"zones": {}}, FAR_BARS, 100.0, 2.0) == []
    assert glr.far_gate_rows(FAR_AGENT, FAR_BARS[:40], 100.0, 2.0) == []
    # 既有 fixture (31 根以内) 的整块渲染不受影响 —— 没有远端行, 表照旧
    assert glr.FAR_MARK not in "\n".join(glr.render_ticker("XX", AGENT, _bars([150] * 25)))


def test_stale_preview_is_flagged_and_a_same_priced_far_gate_folds_into_its_row():
    """旧阶梯价位 (stale) == 某个簇上沿 —— moomoo 上只有一条
    `gate:+2 <收据>`, 表里也只许一行, 且 stale 必须说出口 (不然读成一道活的 0/2)。"""
    agent = {"zones": {"valid_until": "2099-01-01"},
             "next_regime": {"name": "R_next", "entry_gate": {"level": 104.0, "days": 2},
                             "next_gate_preview": {"level": 110.4, "days": 2, "stale": True}}}
    block = glr.render_ticker("XX", agent, FAR_BARS)
    at = [ln for ln in block if "110.4" in ln]
    assert len(at) == 1                                              # 一个价位一行
    assert "stale" in at[0] and f"{glr.FAR_MARK} 远端 +2 簇×2H" in at[0]
    assert not at[0].lstrip("| ").startswith(glr.FAR_MARK)           # 行主体仍是卡片行
    far = [ln.split("|")[2].strip() for ln in block if ln.startswith(f"| {glr.FAR_MARK}")]
    assert far == ["远端 +3", "远端 +4"]                              # 其余照常独立成行, 编号不断
    # 未 stale 的 preview 行不带任何后缀
    clean = [ln for ln in glr.render_ticker("XX", FAR_AGENT, FAR_BARS) if "| ▫️ 106 " in ln]
    assert clean and "stale" not in clean[0] and glr.FAR_MARK not in clean[0]
