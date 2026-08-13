"""watchlist → positions promotion, for tickers that were held before.

Two  regressions, both only reachable on a RE-ENTRY (a ticker whose
card already exists in positions.json from a previous campaign):

1. `merged["agent"] = new_block["agent"]` overwrote the whole agent block, so
   the closed campaign's record was destroyed. MU lost its  全清
   note — exit_guard alerts executed same-day, score A-, realized +4,409 —
   which is one of the few written records on the exit-execution side.

2. `merged.setdefault("status", "active")` cannot flip a stale "closed" back,
   so a re-entered position stayed status="closed". portfolio_brief drops
   closed tickers, so it disappeared from every alert while actually held.

Both are load-bearing now that --apply runs unattended from heartbeat and
overnight-briefing, where nobody reads the diff.
"""
import json

import pytest

from quant.sync import watchlist_migrate as wm


CLOSED_MU_AGENT = {
    "thesis": {"summary": "HBM/memory cycle", "last_reviewed": "2026-06-20T05:30:30Z"},
    "action": {
        "intent": "closed",
        "reason": "CLOSED 2026-07-16 全清 episode. SELL 9 @ 868.01. Realized +4,409.",
    },
    "invalid_if": "MU close < 862",
    "zones": None,
}

NEW_MU_CARD = {
    "instrument": {"type": "stock", "role": "core_theme"},
    "broker": None,
    "status": "watching",
    "agent": {
        "thesis": {"summary": "重进 campaign", "status": "intact"},
        "action": {"intent": "wait_for_zone", "reason": "三档 GTC 925/905/885"},
        "current_regime": {"name": "常规参与", "cap_pct_nav": 0.05,
                           "participation": "zone_eligible",
                           "since": "2026-07-20", "source": "card"},
        "zones": {"accumulation_zone_1": [885.0, 925.0], "stop_level": 800.0},
    },
}


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """Redirect the module's read/write paths at a throwaway pair of docs."""
    def _apply(positions, watchlist):
        pos = tmp_path / "positions.json"
        wl = tmp_path / "watchlist.json"
        pos.write_text(json.dumps({"tickers": positions}), encoding="utf-8")
        wl.write_text(json.dumps({"tickers": watchlist}), encoding="utf-8")
        monkeypatch.setattr(wm, "POSITIONS_PATH", pos)
        monkeypatch.setattr(wm, "WATCHLIST_PATH", wl)
        monkeypatch.setattr(wm, "state_lock", lambda *a, **k: _nullcontext())
        return pos, wl
    return _apply


class _nullcontext:
    def __enter__(self): return None
    def __exit__(self, *exc): return False


def _held(qty=1.0, status="closed", agent=None):
    return {
        "instrument": {"type": "stock"},
        "status": status,
        "broker": {"qty": qty, "current_price": 920.95, "avg_cost": 925.0},
        "agent": agent if agent is not None else dict(CLOSED_MU_AGENT),
        "lots": {"base": [], "position": [{"qty": 1, "avg_cost": 925.0}]},
        "position_summary": {"total_qty": 1},
        "pending_orders": [{"order_id": "X1"}],
    }


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))["tickers"]


# ── prior campaign survives the overwrite ─────────────────────

def test_reentry_archives_prior_campaign(repo):
    pos, _ = repo({"MU": _held()}, {"MU": NEW_MU_CARD})
    wm.run_migration(apply=True)

    agent = _read(pos)["MU"]["agent"]
    assert agent["action"]["intent"] == "hold"          # new card took over
    assert agent["zones"]["stop_level"] == 800.0

    prior = agent["prior_campaigns"]
    assert len(prior) == 1
    assert "Realized +4,409" in prior[0]["reason"]
    assert prior[0]["intent"] == "closed"
    assert prior[0]["invalid_if"] == "MU close < 862"


def test_repeated_promotion_does_not_stack_duplicates(repo):
    """Re-promoting the same unchanged card must not grow the archive."""
    prior = [{"archived_at": "2026-07-01T00:00:00Z", "intent": "closed",
              "reason": "r", "thesis_summary": "t"}]
    agent = {"thesis": {"summary": "t"}, "action": {"intent": "closed", "reason": "r"},
             "prior_campaigns": prior}
    pos, _ = repo({"MU": _held(agent=agent)}, {"MU": NEW_MU_CARD})
    wm.run_migration(apply=True)

    assert len(_read(pos)["MU"]["agent"]["prior_campaigns"]) == 1


def test_empty_prior_agent_archives_nothing(repo):
    pos, _ = repo({"MU": _held(agent={})}, {"MU": NEW_MU_CARD})
    wm.run_migration(apply=True)

    assert "prior_campaigns" not in _read(pos)["MU"]["agent"]


# ── status must follow the broker ─────────────────────────────

def test_reentry_flips_stale_closed_status_to_active(repo):
    pos, _ = repo({"MU": _held(qty=1.0, status="closed")}, {"MU": NEW_MU_CARD})
    wm.run_migration(apply=True)

    assert _read(pos)["MU"]["status"] == "active"


def test_unheld_ticker_keeps_its_status(repo):
    pos, _ = repo({"MU": _held(qty=0.0, status="closed")}, {"MU": NEW_MU_CARD})
    wm.run_migration(apply=True)

    assert _read(pos)["MU"]["status"] == "closed"


# ── existing preservation guarantees still hold ───────────────

def test_broker_lots_and_pending_orders_are_preserved(repo):
    pos, _ = repo({"MU": _held()}, {"MU": NEW_MU_CARD})
    wm.run_migration(apply=True)

    body = _read(pos)["MU"]
    assert body["broker"]["qty"] == 1.0
    assert body["lots"]["position"][0]["avg_cost"] == 925.0
    assert body["position_summary"] == {"total_qty": 1}
    assert body["pending_orders"] == [{"order_id": "X1"}]


def test_promotion_removes_the_watchlist_entry(repo):
    """A promoted ticker leaves the entry-candidate list; the card lives in
    exactly one place afterwards."""
    pos, wl = repo({"MU": _held()}, {"MU": NEW_MU_CARD, "NVDA": NEW_MU_CARD})
    wm.run_migration(apply=True)

    assert list(_read(wl)) == ["NVDA"]
    assert _read(pos)["MU"]["agent"]["zones"]["stop_level"] == 800.0


def test_detect_only_writes_nothing(repo):
    pos, wl = repo({"MU": _held()}, {"MU": NEW_MU_CARD})
    before_pos, before_wl = pos.read_text(), wl.read_text()

    result = wm.run_migration(apply=False)

    assert result["to_promote"] == ["MU"]
    assert result["promoted"] == []
    assert pos.read_text() == before_pos
    assert wl.read_text() == before_wl


def test_promotion_carries_regime_ladder_and_constraints(repo):
    """ INTC 回归: promote 用 allowlist 搬 agent, next_regime 不在名单上
    → B_repair_probe_98 整个 gate→cap 阶梯静默丢失 (「字段在转换中被丢」形状)。
    修法 = 全量携带 + deny-list 剥离 watchlist 专属字段, 而不是白名单加名字。"""
    card = json.loads(json.dumps(NEW_MU_CARD))
    card["agent"]["next_regime"] = {
        "name": "B_repair_probe_930",
        "entry_gate": {"level": 930, "days": 2, "unlocks": "probe"},
        "cap_pct_nav": 0.01,
        "zones": {"accumulation_zone_1": [900, 932], "stop_level": 926.0},
    }
    card["agent"]["zones"]["reclaim_gates"] = [{"level": 905, "days": 2, "label": "早信号"}]
    card["agent"]["action"]["constraints"] = {
        "max_classification": "probe", "from": "2026-08-05", "until": "2026-08-12"}
    card["agent"]["entry_plan"] = {"ladder": "watchlist-only, 不该跟过去"}
    pos, wl = repo({"MU": _held()}, {"MU": card})

    wm.run_migration(apply=True)

    agent = _read(pos)["MU"]["agent"]
    assert agent["next_regime"]["name"] == "B_repair_probe_930"
    assert agent["next_regime"]["entry_gate"]["level"] == 930
    assert agent["zones"]["reclaim_gates"][0]["level"] == 905
    assert agent["action"]["constraints"]["from"] == "2026-08-05"
    assert "entry_plan" not in agent


# ── 促升改写 current_regime (regime 统一) ──────────────────

def test_promotion_rewrites_card_regime_to_no_add(repo):
    """card 源常规参与 (入场授权) 促升后改写为 持仓·无加仓授权 — NBIS 规则
    显式化: 成交 = 入场授权已行使, 加仓授权待 gate 达标转移。"""
    card = json.loads(json.dumps(NEW_MU_CARD))
    card["agent"]["current_regime"] = {
        "name": "常规参与", "cap_pct_nav": 0.05, "participation": "zone_eligible",
        "since": "2026-08-01", "source": "card"}
    pos, _ = repo({"MU": _held()}, {"MU": card})
    wm.run_migration(apply=True)
    cr = _read(pos)["MU"]["agent"]["current_regime"]
    assert cr["name"] == "持仓·无加仓授权" and cr["cap_pct_nav"] == 0
    assert cr["source"] == "card"


# ── 空 stub 不许覆盖有图的卡 (GOOGL, 卡空了 6 天) ────────────
#
# 促升的合并假设是「新卡更好」—— 对分析过的候选成立, 对 watchlist_sync 自动
# 生成的 stub **不成立**: stub 更新, 但更空。触发链:
#   sync    held_bare → GOOGL(正股 qty=0) 不算持仓 → 不移出分组 + 建 stub
#   migrate held_incl_option_underlyings → GOOGL(有 LEAPS) 算持仓 → 促升
#   run_migration: merged["agent"] = stub 的 agent  ← 真卡整张进 prior_campaigns
# `_archive_prior_campaign` 有守卫, 但它防的是「别归档垃圾」, 不防「被垃圾覆盖」。

GOOGL_STUB = {                      # watchlist_sync.make_stub() 的形状, 逐字
    "instrument": {"type": "stock"},
    "broker": None,
    "status": "watching",
    "priority": "unassigned",
    "moomoo_listed": True,
    "agent": {
        "thesis": {"summary": "[stub] moomoo 自选同步新增 2026-08-06 — thesis/zones 待建卡",
                   "status": "needs_card", "conviction": None},
        "zones": None,
        "action": {"intent": "needs_card",
                   "reason": "moomoo watchlist 新增, 无 zone 无 thesis"},
    },
}

GOOGL_REAL_AGENT = {
    "thesis": {"summary": "Q2 beat, capex 上修触发 PE derating",
               "status": "needs_reverification", "conviction": "medium"},
    "action": {"intent": "hold", "reason": "回踩 z1 358.5-368 = 参与资格"},
    "invalid_if": "收盘 < 335.5",
    "current_regime": {"name": "持仓·无加仓授权", "cap_pct_nav": 0,
                       "participation": "gate_only", "since": "2026-08-05",
                       "source": "card"},
    "zones": {"accumulation_zone_1": [358.5, 368.0], "stop_level": 335.5,
              "anchored_on": {"date": "2026-08-05", "basis": "level_map asof 8/5"}},
}


def _synthetic_underlying(agent):
    """期权的 underlying 行: qty=0 + synthetic (stock_fetch 补的行情行)。"""
    return {"instrument": {"type": "stock"}, "status": "active",
            "broker": {"qty": 0.0, "synthetic": True, "current_price": 349.65},
            "agent": agent, "lots": {"base": [], "position": []},
            "position_summary": {}, "pending_orders": []}


def _held_option(qty=1.0):
    return {"instrument": {"type": "option",
                           "option": {"underlying": "GOOGL", "strike": 390.0,
                                      "expiry": "2027-01-15", "side": "C",
                                      "exposure": "long"}},
            "status": "active", "broker": {"qty": qty, "avg_cost": 35.0},
            "agent": {"thesis": {"summary": "Jan27 390C"},
                      "action": {"intent": "hold", "reason": "双线取先到"},
                      "zones": {"stop_level": 351.0, "stop_level_basis": "underlying",
                                "stop_level_days": 2},
                      "linked_underlying": "GOOGL"},
            "lots": {"base": [], "position": []},
            "position_summary": {}, "pending_orders": []}


def test_stub_may_not_erase_a_card_that_has_zones(repo):
    """ 回归: 持有 GOOGL LEAPS + watchlist 有 GOOGL stub → 促升必须被拦,
    正股卡的 zones 一个字不许动。"""
    pos, wl = repo(
        {"GOOGL": _synthetic_underlying(json.loads(json.dumps(GOOGL_REAL_AGENT))),
         "GOOGL270115C390000": _held_option()},
        {"GOOGL": GOOGL_STUB},
)

    result = wm.run_migration(apply=True)

    agent = _read(pos)["GOOGL"]["agent"]
    assert agent["zones"]["stop_level"] == 335.5              # 图还在
    assert agent["zones"]["anchored_on"]["date"] == "2026-08-05"
    assert agent["thesis"]["status"] == "needs_reverification"
    assert "prior_campaigns" not in agent                     # 没被归档 = 没被覆盖
    assert result["promote_blocked"] == ["GOOGL"]
    assert result["promoted"] == []


def test_blocked_candidate_stays_in_the_watchlist(repo):
    """拦下之后候选**不 pop**—— 条件因此每轮都算得出来 (对账不是事件),
    portfolio_brief 才能把它当 INFRA:promote_blocked 报出去。"""
    pos, wl = repo(
        {"GOOGL": _synthetic_underlying(json.loads(json.dumps(GOOGL_REAL_AGENT))),
         "GOOGL270115C390000": _held_option()},
        {"GOOGL": GOOGL_STUB},
)

    wm.run_migration(apply=True)
    assert list(_read(wl)) == ["GOOGL"]

    again = wm.run_migration(apply=True)          # 幂等: 下轮照样拦, 照样报
    assert again["promote_blocked"] == ["GOOGL"]
    assert _read(pos)["GOOGL"]["agent"]["zones"]["stop_level"] == 335.5


def test_a_real_card_still_promotes_alongside_a_blocked_one(repo):
    """守卫只挡空卡。同一轮里有图的候选照常促升 —— 别把整条路堵死。"""
    pos, wl = repo(
        {"GOOGL": _synthetic_underlying(json.loads(json.dumps(GOOGL_REAL_AGENT))),
         "GOOGL270115C390000": _held_option(),
         "MU": _held()},
        {"GOOGL": GOOGL_STUB, "MU": NEW_MU_CARD},
)

    result = wm.run_migration(apply=True)

    assert result["promoted"] == ["MU"]
    assert result["promote_blocked"] == ["GOOGL"]
    assert _read(pos)["MU"]["agent"]["zones"]["stop_level"] == 800.0
    assert _read(pos)["GOOGL"]["agent"]["zones"]["stop_level"] == 335.5
    assert list(_read(wl)) == ["GOOGL"]


def test_empty_positions_card_may_still_be_overwritten(repo):
    """守卫的判据是「existing 有 zones 而 incoming 没有」。positions 那张本来就
    没图时, stub 促升无损 —— 不该顺手把正常路径也堵了。"""
    pos, _ = repo(
        {"GOOGL": _synthetic_underlying({"thesis": {"summary": "x"}, "zones": None}),
         "GOOGL270115C390000": _held_option()},
        {"GOOGL": GOOGL_STUB},
)

    result = wm.run_migration(apply=True)
    assert result["promoted"] == ["GOOGL"]
    assert result["promote_blocked"] == []


def test_blocked_promotions_is_a_pure_function_of_the_two_docs(repo):
    pos_doc = {"tickers": {
        "GOOGL": _synthetic_underlying(json.loads(json.dumps(GOOGL_REAL_AGENT))),
        "GOOGL270115C390000": _held_option()}}
    wl_doc = {"tickers": {"GOOGL": GOOGL_STUB}}
    assert wm.blocked_promotions(pos_doc, wl_doc) == ["GOOGL"]

    # 期权平掉 → GOOGL 不再是持仓 → 根本不会促升 → 无所谓拦不拦
    pos_doc["tickers"]["GOOGL270115C390000"] = _held_option(qty=0.0)
    assert wm.blocked_promotions(pos_doc, wl_doc) == []


def test_detect_only_reports_the_block_without_writing(repo):
    pos, wl = repo(
        {"GOOGL": _synthetic_underlying(json.loads(json.dumps(GOOGL_REAL_AGENT))),
         "GOOGL270115C390000": _held_option()},
        {"GOOGL": GOOGL_STUB},
)
    before = pos.read_text(), wl.read_text()

    result = wm.run_migration(apply=False)

    assert result["promote_blocked"] == ["GOOGL"]
    assert "GOOGL" not in result["to_promote"]
    assert (pos.read_text(), wl.read_text()) == before


def test_promotion_preserves_machine_granted_add_cap(repo):
    """gate 达标转移落的 cap (机器源) 促升时原样携带 — 那本来就是 gate 授权的
    加仓资格 (LITE 形态); 出组交给消费判定, 不在促升时抹掉。"""
    card = json.loads(json.dumps(NEW_MU_CARD))
    card["agent"]["current_regime"] = {
        "name": "B_repair_probe_930", "cap_pct_nav": 0.02,
        "participation": "zone_eligible", "since": "2026-08-04", "source": "auto"}
    pos, _ = repo({"MU": _held()}, {"MU": card})
    wm.run_migration(apply=True)
    cr = _read(pos)["MU"]["agent"]["current_regime"]
    assert cr["name"] == "B_repair_probe_930" and cr["cap_pct_nav"] == 0.02
    assert cr["source"] == "auto"
