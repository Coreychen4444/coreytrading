"""每张有 zones 的活跃正股卡都要有上行状态转移轴。

用户  定版: **gate 在本系统的哲学 = 状态转移轴**, 就是标志性价格位 ——
下降趋势有、上升趋势有、修复阶段也有, **不是破位卡的专属物**。当前 regime 管的是
「现在能不能参与」(cap), next_regime 管的是「下一个状态在哪」, 两件独立的事,
画卡时都要同步给出。

缺了不是理论问题:
  cap > 0   R1 进得去, R2/R3 (ROUND_RISK_FRACS 1.5%/1.0%) 永远拿不到授权 ——
            促升后 derive_card_regime 写 cap 0, 没 gate 就没有升回来的路
  cap == 0  本来就锁在 0, 唯一能触发的转移是**破位**(通往 reclaim_* cap 2%)

而写 next_regime 的两处都在 `regime_transition.apply_transition` 里 —— 机器只在
转移发生时写。建卡不写, 就真的没有人会补。这是「缺字段静默」的又一例。
"""
import pytest

from quant.core.structure import cards_missing_next_gate, has_next_gate


def _card(zones=True, gate=None, cap=0.4, status="active", itype="stock", role=None,
          regime="常规参与"):
    agent = {"zones": {"stop_level": 100.0} if zones else None}
    if regime is not None:
        agent["current_regime"] = {"name": regime, "cap_pct_nav": cap}
    if gate is not None:
        agent["next_regime"] = {"name": "R_突破_X",
                                "entry_gate": {"level": gate, "days": 2}}
    body = {"status": status, "instrument": {"type": itype}, "agent": agent}
    if role:
        body["instrument"]["role"] = role
    return body


def _doc(**t):
    return {"tickers": t}


# ── has_next_gate: 只认 next_regime.entry_gate.level ──────────────────

def test_entry_gate_level_is_the_only_axis():
    assert has_next_gate({"next_regime": {"entry_gate": {"level": 279.0}}})
    assert not has_next_gate({"next_regime": {"entry_gate": {}}})
    assert not has_next_gate({"next_regime": {}})
    assert not has_next_gate({})


def test_reclaim_gates_do_not_count():
    """reclaim_gates 是本 regime 内的早信号, 不切 regime 也不授予下一档 cap ——
    `regime_transition.derive()` 的上行分支根本不读它。"""
    agent = {"zones": {"reclaim_gates": [{"level": 905, "days": 2, "label": "早信号"}]}}
    assert not has_next_gate(agent)


def test_last_regime_repair_gate_does_not_count():
    """last_regime.repair_gate 是转移史 (从哪跌下来的), 不是通往下一档的路。"""
    assert not has_next_gate({"last_regime": {"repair_gate": {"level": 358.5}}})


# ── 扫描: 谁该被点名 ─────────────────────────────────────────────────

def test_clean_regular_card_without_a_gate_is_named():
    """**常规参与不是豁免理由**—— COHR/META  源案例。"""
    (row,) = cards_missing_next_gate(_doc(COHR=_card(gate=None, cap=0.4)))
    assert row.startswith("COHR")
    assert "只有一轮" in row and "40%" in row


def test_cap_zero_card_is_named_with_the_harsher_wording():
    """cap 0 更严重: 没有上行出口, 唯一能触发的转移是破位。GOOGL 源案例。"""
    (row,) = cards_missing_next_gate(
        _doc(GOOGL=_card(gate=None, cap=0, regime="持仓·无加仓授权")))
    assert "锁死" in row and "等破位" in row


def test_a_card_with_a_gate_is_quiet():
    assert cards_missing_next_gate(_doc(AMZN=_card(gate=278.6))) == []


def test_scans_both_docs_and_sorts():
    pos = _doc(GOOGL=_card(gate=None, cap=0), NBIS=_card(gate=231))
    wl = _doc(COHR=_card(gate=None), AMZN=_card(gate=278.6))
    rows = cards_missing_next_gate(pos, wl)
    assert [r.split()[0] for r in rows] == ["GOOGL", "COHR"]


# ── 豁免 ────────────────────────────────────────────────────────────

def test_card_without_zones_is_not_named():
    """无 zones = stub / 无结构, 归 NEEDS_CARD 那条路, 不是本告警的事。"""
    assert cards_missing_next_gate(_doc(SOXX=_card(zones=False, gate=None))) == []


def test_option_card_is_exempt():
    """期权的转移轴在 underlying 上 —— 同 current_regime 的期权豁免。"""
    assert cards_missing_next_gate(
        _doc(GOOGL270115C390000=_card(gate=None, itype="option"))) == []


def test_closed_card_is_exempt():
    assert cards_missing_next_gate(_doc(NOK=_card(gate=None, status="closed"))) == []


def test_structurally_exempt_layer_is_out():
    """base 层整个不在管辖内 (position-tiers.md: 不进任何 task 输出)。"""
    assert cards_missing_next_gate(
        _doc(SPCX=_card(gate=None, role="commemorative_hold"))) == []


@pytest.mark.live_data
def test_live_book_is_scanned_not_asserted_clean():
    """真账本上这条**允许**响 ( 有 3 张缺) —— 这里只钉扫描不炸。"""
    import json
    from quant.core.paths import POSITIONS_PATH, WATCHLIST_PATH
    rows = cards_missing_next_gate(
        json.loads(POSITIONS_PATH.read_text(encoding="utf-8")),
        json.loads(WATCHLIST_PATH.read_text(encoding="utf-8")))
    assert isinstance(rows, list)
    assert all(" — " in r for r in rows)


def test_watchlist_tombstone_is_not_scanned():
    """: 移出自选分组只翻 moomoo_listed, status 仍 watching —— 四个 INFRA 扫描都只看
    status≠closed, 墓碑卡天天被点名。判据收进 holdings.is_scannable_card。"""
    live = _card(gate=None, status="watching")
    tomb = {**_card(gate=None, status="watching"), "moomoo_listed": False}
    out = cards_missing_next_gate(_doc(MU=live, AMKR=tomb))
    assert len(out) == 1 and out[0].startswith("MU")
