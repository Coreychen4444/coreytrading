"""portfolio_brief.valuation_fyi_map: 最近一次估值日更的研究级目标 → 简报顶层 map (只读, 判断层, 不进任何判定)。
卡片镜像 agent.valuation (旧估值栈, 方法已判不可用) 不再读。"""
from quant.report import portfolio_brief as pb

RESEARCH = {"as_of": "2026-09-29T00:04:49+00:00", "tickers": {
    "AAA": {"status": "ok", "price": 100.0, "target_12m": {"center": 110.0}, "upside_center": 0.10,
              "flags": ["cash_earnings_divergence"], "implied": {"status": "ok", "implied_growth": 0.13}},
    "BBB": {"status": "unavailable", "reason": "forward_eps_not_positive", "implied": {}},
    "OLD": {"status": "ok", "price": 1.0, "target_12m": {"center": 1.0}, "upside_center": 0.0, "flags": []},
}}


def test_valuation_fyi_map_reads_research_targets_for_active_and_watching_cards_only():
    pos = {"tickers": {
        "AAA": {"status": "active", "agent": {"valuation": {"fyi": "📐[FYI] 估值 低估 -33% · 零授权"}}},
        "OLD": {"status": "closed"},
        "AAA270319C220000": {"status": "active"},
        "NOROW": {"status": "active"},
    }}
    wl = {"tickers": {"BBB": {"status": "watching"}, "AAA": {"status": "watching"}}}
    m = pb.valuation_fyi_map(pos, wl, research=RESEARCH)
    assert set(m) == {"AAA", "BBB"}
    assert "研究级12月 中枢 $110" in m["AAA"] and "低估" not in m["AAA"], "卡片旧镜像不再出现"
    assert "FCF<净利一半" in m["AAA"] and "13%" in m["AAA"] and "2026-09-29" in m["AAA"]
    assert "不出数" in m["BBB"]


def test_missing_research_means_no_line_not_old_mirror():
    pos = {"tickers": {"AAA": {"status": "active", "agent": {"valuation": {"fyi": "旧"}}}}}
    assert pb.valuation_fyi_map(pos, None, research={}) == {}
    assert pb.valuation_fyi_map(None, None, research=RESEARCH) == {}


def test_valuation_fyi_is_not_consumed_by_any_decision_path():
    """判断层零授权效力: brief 里除了这个 map, 没有任何代码路径读估值。"""
    src = open(pb.__file__, encoding="utf-8").read()
    before, rest = src.split("def valuation_fyi_map")
    after = "\n".join(rest.split("\ndef ")[1:])
    assert 'get("valuation")' not in before + after
    assert "research_target" not in before + after
