"""有价位带却没有 stop 的正股卡 = 一张不会降级的卡 (INFRA:card_missing_stop)。

不变量: 有买带的活跃正股卡必须有数值 zones.stop_level。`regime_transition.derive` 下行分支是
`if stop is not None:` —— 没有 stop 就整段不评估: 收盘破位不降级, cap 与
Today 授权一直挂着; entry_guard 的预设止损退到 2N 兜底 (不是卡的线)。
invalid_if 的文字仍写着那条线, 代码读不到 = 规则不进代码 = 不存在。

写入端 (writer / agent_submit) 已拒绝「擦除现有 stop」; 本扫描兜任何路径留下的无 stop 卡。
"""
import pytest

from quant.core.structure import cards_missing_stop, has_stop


def _card(z1=True, stop=None, cap=0.4, status="active", itype="stock", role=None,
          regime="常规参与", zones=True):
    z = None
    if zones:
        z = {"trim_zone": [150.0, 155.0], "no_chase_above": 140.0}
        if z1:
            z["accumulation_zone_1"] = [120.0, 125.0]
        if stop is not None:
            z["stop_level"] = stop
    agent = {"zones": z}
    if regime is not None:
        agent["current_regime"] = {"name": regime, "cap_pct_nav": cap}
    body = {"status": status, "instrument": {"type": itype}, "agent": agent}
    if role:
        body["instrument"]["role"] = role
    return body


def _doc(**t):
    return {"tickers": t}


# ── has_stop: 只认 zones.stop_level 数值 ─────────────────────────────

def test_only_a_numeric_zones_stop_counts():
    assert has_stop({"zones": {"stop_level": 95.12}})
    assert has_stop({"zones": {"stop_level": "95.12"}})      # coerce_float 同消费者口径
    assert not has_stop({"zones": {"stop_level": None}})
    assert not has_stop({"zones": {}})
    assert not has_stop({})


def test_invalid_if_prose_does_not_count():
    """invalid_if 写着「收盘 < 95.12 x1」, 代码读的是 zones.stop_level —— 规则不进代码 = 不存在。"""
    assert not has_stop({"invalid_if": "收盘 < 95.12 x1 → 本图作废", "zones": {"accumulation_zone_1": [98.5, 101.6]}})


# ── 扫描: 谁该被点名 ─────────────────────────────────────────────────

def test_promoted_card_without_stop_is_named_with_the_authority_wording():
    """促升卡 cap 40% 挂着, bfloor 被擦 → 破位不降级。"""
    (row,) = cards_missing_stop(_doc(AAA=_card(stop=None, cap=0.4, regime="R_收复_100")))
    assert row.startswith("AAA") and "40%" in row
    assert "破位不降级" in row and "Today" in row


def test_cap_zero_card_without_stop_is_named_with_the_floor_wording():
    """降级卡: 降级世界 cap 0, 但没有地板 = 再破位也不会作废重画。"""
    (row,) = cards_missing_stop(_doc(BBB=_card(stop=None, cap=0, regime="demoted_at_2026-01-05")))
    assert "无地板" in row and "cap 0%" in row


def test_card_with_a_stop_is_quiet():
    assert cards_missing_stop(_doc(AAA=_card(stop=95.12))) == []


def test_scans_both_docs_and_sorts():
    pos = _doc(NVDA=_card(stop=None, cap=0), MU=_card(stop=100.0))
    wl = _doc(CCC=_card(stop=None), AAA=_card(stop=95.12))
    rows = cards_missing_stop(pos, wl)
    assert [r.split()[0] for r in rows] == ["NVDA", "CCC"]


# ── 豁免 ────────────────────────────────────────────────────────────

def test_card_without_accumulation_zones_is_not_named():
    """只剩 trim / no_chase 或元数据的卡不在本告警范围 —— 没有买带就没有「破位不降级」这件事。"""
    assert cards_missing_stop(_doc(DDD=_card(z1=False, stop=None))) == []


def test_card_without_zones_is_not_named():
    """无 zones = stub, 归 NEEDS_CARD 那条路。"""
    assert cards_missing_stop(_doc(SOXX=_card(zones=False))) == []


def test_option_card_is_exempt():
    """期权的退出线归 exit_guard 期权 overlay, 转移机本来不管它。"""
    assert cards_missing_stop(_doc(CCC270115C390000=_card(stop=None, itype="option"))) == []


def test_closed_card_is_exempt():
    assert cards_missing_stop(_doc(NOK=_card(stop=None, status="closed"))) == []


def test_structurally_exempt_layer_is_out():
    assert cards_missing_stop(_doc(SPCX=_card(stop=None, role="commemorative_hold"))) == []


def test_watchlist_tombstone_is_not_scanned():
    live = _card(stop=None, status="watching")
    tomb = {**_card(stop=None, status="watching"), "moomoo_listed": False}
    out = cards_missing_stop(_doc(MU=live, AMKR=tomb))
    assert len(out) == 1 and out[0].startswith("MU")


@pytest.mark.live_data
def test_live_book_is_scanned_not_asserted_clean():
    import json
    from quant.core.paths import POSITIONS_PATH, WATCHLIST_PATH
    rows = cards_missing_stop(json.loads(POSITIONS_PATH.read_text(encoding="utf-8")),
                              json.loads(WATCHLIST_PATH.read_text(encoding="utf-8")))
    assert isinstance(rows, list)
    assert all(" — " in r for r in rows)
