"""fill ↔ decisions.jsonl reconciliation.

The rule "超 cap 必须落书面例外" existed since ; the enforcement point did
not. decision_log.py is a hand-run CLI and annotate_event never touched the
ledger, so MU  filled 1 whole share ($925 = 4.1x the $227 probe cap)
against a worksheet that said size=227, and the card claimed "decisions 已 log"
while the exception itself was never written. Claiming ≠ logging.

This test suite pins the gate that makes the omission a failing action.
"""
import json

import pytest

from quant.journal import annotate_event as ae


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    def _apply(rows):
        path = tmp_path / "decisions.jsonl"
        path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
        monkeypatch.setattr(ae, "DECISIONS_PATH", path)
        return path
    return _apply


def _decision(ticker="MU", ts="2026-07-22T17:26:31-04:00", size=227.0, chosen="prehang"):
    row = {"timestamp": ts, "ticker": ticker, "action": "buy", "chosen": chosen}
    if size is not None:
        row["size"] = size
    return row


FILL = {"event_type": "fill", "ticker": "MU", "side": "BUY", "qty": 1.0,
        "price": 925.0, "timestamp": "2026-07-24T10:17:10-04:00"}


@pytest.fixture(autouse=True)
def no_prior_fills(tmp_path, monkeypatch):
    """累计口径要读 events; 这些用例只关心被检的那一笔。"""
    events = tmp_path / "events"
    (events / "2026-07").mkdir(parents=True)
    (events / "2026-07" / "trades.jsonl").write_text("", encoding="utf-8")
    monkeypatch.setattr(ae, "EVENTS_DIR", events)


def test_the_mu_case_is_refused(ledger):
    """方案A worksheet (227) + 方案B 成交 (925) = 4.1x → 拒写。"""
    ledger([_decision()])
    ok, msg = ae.check_decision(FILL, None)

    assert ok is False
    assert "4.1x" in msg
    assert "decision_log.py" in msg and "--size 925" in msg


# ── 累计口径: 一条 ladder 是 N 笔成交对 1 份 worksheet ──────────

def _prior(tmp_path, rows):
    (tmp_path / "events" / "2026-07" / "trades.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows), encoding="utf-8")


def test_ladder_rungs_are_summed_not_compared_one_by_one(tmp_path, ledger):
    """INTC 7/23 的 1@105 + 1@103 = 一条 2 档 ladder 共 $208, 在 $227 内。
    逐笔比总额会让每档都显示 ~50% 而被误判 (2026-07-25 dogfood 当场踩到)。"""
    ledger([_decision(ticker="INTC", size=227.0, ts="2026-07-23T09:00:00-04:00")])
    _prior(tmp_path, [{"event_type": "fill", "ticker": "INTC", "side": "BUY", "qty": 1.0,
                       "price": 105.0, "order_id": "A", "classification": "probe",
                       "timestamp": "2026-07-23T17:49:00-04:00"}])
    second = {"event_type": "fill", "ticker": "INTC", "side": "BUY", "qty": 1.0,
              "price": 103.0, "order_id": "B", "classification": "probe",
              "timestamp": "2026-07-23T18:13:00-04:00"}
    ok, msg = ae.check_decision(second, None)

    assert ok is True
    assert "$208" in msg              # 累计, 不是单笔 $103


def test_underfilled_ladder_is_never_a_violation(tmp_path, ledger):
    """只成交首档 = 部署不足 —— 需要书面例外的是花超了, 不是没花完。"""
    ledger([_decision(size=925.0, ts="2026-07-24T09:00:00-04:00")])
    one_rung = {**FILL, "price": 400.0, "order_id": "X", "classification": "probe"}

    assert ae.check_decision(one_rung, None)[0] is True


def test_cumulative_overrun_is_refused(tmp_path, ledger):
    """前面已经填满预算, 再来一笔就是超支 —— 单看这一笔却是"小单"。"""
    ledger([_decision(ticker="INTC", size=227.0, ts="2026-07-23T09:00:00-04:00")])
    _prior(tmp_path, [{"event_type": "fill", "ticker": "INTC", "side": "BUY", "qty": 2.0,
                       "price": 104.0, "order_id": "A", "classification": "probe",
                       "timestamp": "2026-07-23T17:49:00-04:00"}])
    extra = {"event_type": "fill", "ticker": "INTC", "side": "BUY", "qty": 1.0,
             "price": 100.0, "order_id": "B", "classification": "probe",
             "timestamp": "2026-07-23T18:13:00-04:00"}
    ok, msg = ae.check_decision(extra, None)

    assert ok is False and "$308" in msg


def test_matching_worksheet_passes(ledger):
    ledger([_decision(size=925.0, chosen="whole_share_over_cap_exception",
                      ts="2026-07-24T09:00:00-04:00")])

    assert ae.check_decision(FILL, None)[0] is True


def test_missing_worksheet_is_refused(ledger):
    """NBIS 2026-07-23 就是这一格: events 注解齐全, ledger 空白。"""
    ledger([])
    ok, msg = ae.check_decision(FILL, None)

    assert ok is False and "没有对应的 decisions.jsonl worksheet" in msg


def test_small_drift_within_tolerance_passes(ledger):
    """限价单成交价小幅偏离不该制造摩擦。"""
    ledger([_decision(size=900.0, ts="2026-07-24T09:00:00-04:00")])

    assert ae.check_decision(FILL, None)[0] is True


def test_stale_worksheet_outside_window_does_not_count(ledger):
    ledger([_decision(size=925.0, ts="2026-06-01T09:00:00-04:00")])

    assert ae.check_decision(FILL, None)[0] is False


def test_worksheet_logged_after_the_fill_still_counts(ledger):
    """例外常常是成交后补的 —— 目的是记录存在, 不是强制 pre-commit 时序。"""
    ledger([_decision(size=925.0, ts="2026-07-24T17:03:05-04:00")])

    assert ae.check_decision(FILL, None)[0] is True


def test_backfill_days_later_still_counts(ledger):
    """NBIS 7/23 那笔的 worksheet 是 7/25 补的; 窗口对称才接得住。"""
    ledger([_decision(size=925.0, ts="2026-07-28T01:25:00-04:00")])

    assert ae.check_decision(FILL, None)[0] is True


def test_other_tickers_are_not_borrowed(ledger):
    ledger([_decision(ticker="NBIS", size=925.0, ts="2026-07-24T09:00:00-04:00")])

    assert ae.check_decision(FILL, None)[0] is False


def test_sizeless_worksheet_passes_with_a_note(ledger):
    """有 worksheet 但没记 size → 无法对账, 放行而不是硬拦 (别为缺字段制造死锁)。"""
    ledger([_decision(size=None)])
    ok, msg = ae.check_decision(FILL, None)

    assert ok is True and "无法对账" in msg


def test_decision_ref_accepts_an_existing_row(ledger):
    ledger([_decision(size=227.0)])
    ok, msg = ae.check_decision(FILL, "2026-07-22")

    assert ok is True and "2026-07-22" in msg


def test_decision_ref_rejects_a_bogus_pointer(ledger):
    """引用必须指向真实存在的一行, 否则 --decision-ref 就是个静默旁路。"""
    ledger([_decision(size=227.0)])
    ok, msg = ae.check_decision(FILL, "1999-01-01")

    assert ok is False and "找不到" in msg


def test_option_notional_uses_the_contract_multiplier(ledger):
    ledger([_decision(ticker="AAOI270617C200000", size=3100.0,
                      ts="2026-07-24T09:00:00-04:00")])
    fill = {**FILL, "ticker": "AAOI270617C200000", "qty": 1.0, "price": 31.0}

    assert ae.check_decision(fill, None)[0] is True
