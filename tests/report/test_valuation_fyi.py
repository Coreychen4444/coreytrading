"""portfolio_brief.valuation_fyi_map: 卡片 agent.valuation.fyi → 简报顶层 map (只读, 判断层, 不进任何判定)。"""
from quant.report import portfolio_brief as pb


def test_valuation_fyi_map_reads_active_and_watching_stock_cards_only():
    pos = {"tickers": {
        "GOOGL": {"status": "active", "agent": {"valuation": {"fyi": "📐[FYI] 估值 高估 +27% · 零授权"}}},
        "OLD": {"status": "closed", "agent": {"valuation": {"fyi": "x"}}},
        "NVDA270319C220000": {"status": "active", "agent": {"valuation": {"fyi": "y"}}},
        "NOBLOCK": {"status": "active", "agent": {}},
    }}
    wl = {"tickers": {
        "MU": {"status": "watching", "agent": {"valuation": {"fyi": "📐[FYI] 估值 高估 +255% · 零授权"}}},
        "GONE": {"status": "removed", "agent": {"valuation": {"fyi": "z"}}},
        "GOOGL": {"status": "watching", "agent": {"valuation": {"fyi": "dup-from-watchlist"}}},
    }}
    m = pb.valuation_fyi_map(pos, wl)
    assert set(m) == {"GOOGL", "MU"}
    assert m["GOOGL"].startswith("📐[FYI]"), "positions 卡优先, watchlist 同票不覆盖"
    assert pb.valuation_fyi_map(None, None) == {} and pb.valuation_fyi_map({}, {"tickers": {}}) == {}


def test_valuation_fyi_is_not_consumed_by_any_decision_path():
    """判断层零授权效力: brief 里除了这个 map, 没有任何代码路径读 agent.valuation。"""
    src = open(pb.__file__, encoding="utf-8").read()
    body = src.split("def valuation_fyi_map")[1].split("\ndef ")[0]
    assert src.count('"valuation"') == body.count('"valuation"') + src.split("def valuation_fyi_map")[0].count('"valuation"')
    assert 'get("valuation")' not in src.split("def valuation_fyi_map")[0]
    assert 'get("valuation")' not in "\n".join(src.split("def valuation_fyi_map")[1].split("\ndef ")[1:])
