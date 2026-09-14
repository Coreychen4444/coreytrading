"""显式 --decision-ref 的两条规矩, 都是  backfill 时踩出来的。

① 人点名的 worksheet 不受自动匹配窗口限制。此前 ref 只在 ±14 天窗口内搜, 于是
   一条**确实存在**、只是隔得久的 worksheet 得到的答复是"在 decisions.jsonl 里
   找不到" —— 报错在说谎。说谎的报错比没有报错更贵: 人会照着它再补一条重复的
   worksheet, 而 ledger 里多一条假记录是不可回滚的。

② classification 只给 BUY fill。它是**入场档位**标签; 给平仓单盖等于声明"这笔卖出
   属于某一轮 ladder", 没有语义。少了这条守卫, 给 7/23 那笔平仓补 tier 时顺手把
   `classification: probe` 盖到了一个 SELL 上 —— 而同一条规则在
   trade_history_sync 里是写着的。**两处对同一问题给出不同答案 = 副本分叉。**
"""
import pytest

from quant.journal import annotate_event as ae

FILL = {"event_type": "fill", "side": "BUY", "ticker": "INTC", "qty": 20.0,
        "price": 107.0, "timestamp": "2026-07-23T17:11:50-04:00", "order_id": "X"}
FAR_WORKSHEET = {"ticker": "INTC", "timestamp": "2026-08-06T20:15:55-04:00",
                 "action": "buy", "chosen": "impulse_buy_reversed_same_session",
                 "size": 2140.0}


@pytest.fixture
def ledger(monkeypatch):
    rows = [FAR_WORKSHEET]
    monkeypatch.setattr(ae, "iter_jsonl", lambda p: list(rows))
    return rows


def test_explicit_ref_is_found_outside_the_auto_match_window(ledger):
    ok, msg = ae.check_decision(FILL, "2026-08-06T20:15")
    assert ok, msg
    assert "距成交 14 天" in msg and "人已显式指认" in msg


def test_gap_is_reported_not_silently_accepted(ledger):
    """越窗必须**说出来**—— 静默接受就等于窗口从来不存在。"""
    _, msg = ae.check_decision(FILL, "2026-08-06T20:15")
    assert str(ae.DECISION_WINDOW_DAYS) in msg


def test_ref_that_really_does_not_exist_still_fails(ledger):
    ok, msg = ae.check_decision(FILL, "2026-01-01")
    assert not ok
    assert "找不到" in msg


CLOSE = {"event_type": "close", "side": "SELL", "ticker": "INTC", "qty": 20.0,
         "price": 107.5, "timestamp": "2026-07-23T17:13:53-04:00", "order_id": "S1"}


def test_cli_never_derives_auth_for_a_sell(monkeypatch, tmp_path, capsys):
    """② 守卫: auth 是入场授权快照, SELL/close 上不推导 (2026-08-07 SELL 被顺手
    盖上入场标签的病, 分类轴退役后同规则作用于 auth)。--classification 参数已随
    轴移除 (2026-08-13) —— CLI 不再接受它。"""
    (tmp_path / "2026-07").mkdir()
    (tmp_path / "2026-07" / "trades.jsonl").touch()
    monkeypatch.setattr(ae, "EVENTS_DIR", tmp_path)
    monkeypatch.setattr(ae, "iter_jsonl", lambda p: [CLOSE])
    monkeypatch.setattr(ae, "_cards", lambda: {})
    monkeypatch.setattr("sys.argv", ["annotate_event", "--order-id", "S1",
                                     "--classification", "probe"])
    with pytest.raises(SystemExit):        # argparse: 参数已不存在
        ae.main()
    out = capsys.readouterr()
    assert "auth 推导" not in out.out      # SELL 路径没有任何 auth 推导发生


def test_near_ref_reports_no_gap_noise(monkeypatch):
    """窗口内的正常引用不该带越窗提示 —— 告警文案只在真异常时出现。"""
    near = dict(FAR_WORKSHEET, timestamp="2026-07-24T06:32:39-04:00", size=226.67)
    monkeypatch.setattr(ae, "iter_jsonl", lambda p: [near])
    ok, msg = ae.check_decision(FILL, "2026-07-24")
    assert ok and "超出" not in msg


OPT_FILL = {"event_type": "fill", "side": "BUY", "ticker": "NBIS270115C90000", "qty": 1.0,
            "price": 12.0, "timestamp": "2026-08-26T10:05:00-04:00", "order_id": "RP1"}


def test_cli_auth_derivation_sees_reversal_probe_plans_at_the_fill_date(monkeypatch, tmp_path):
    """reversal_probe : 授权住 option_plans.json 不住正股卡 —— 补盖章路径必须
    把选约文件喂给 derive_auth, 且有效期按**成交日**判 (不是今天), 与 trade_history_sync 同口径。"""
    from quant.core import io_json
    import json
    (tmp_path / "2026-08").mkdir()
    (tmp_path / "2026-08" / "trades.jsonl").write_text(json.dumps(OPT_FILL) + "\n")
    plans = {"NBIS": {"entry_class": "reversal_probe", "code": "NBIS270115C90000",
                      "valid_until": "2026-08-28", "cap_frac": 0.09}}
    (tmp_path / "option_plans.json").write_text(json.dumps({"plans": plans}))
    monkeypatch.setattr(io_json, "STATE_LOCK_PATH", tmp_path / ".state.lock")
    monkeypatch.setattr(ae, "EVENTS_DIR", tmp_path)
    monkeypatch.setattr(ae, "OPTION_PLANS_PATH", tmp_path / "option_plans.json")
    monkeypatch.setattr(ae, "_cards", lambda: {})
    seen = {}

    def fake_derive(tkr, cards, today=None, option_plans=None):
        seen.update(tkr=tkr, today=today, option_plans=option_plans)
        return None, "stub"
    monkeypatch.setattr(ae, "derive_auth", fake_derive)
    monkeypatch.setattr("sys.argv", ["annotate_event", "--order-id", "RP1", "--notes", "t"])
    try:
        ae.main()
    except SystemExit:
        pass
    assert seen["tkr"] == "NBIS270115C90000"
    assert seen["today"] == "2026-08-26"                       # 成交日, 不是 et_today()
    assert seen["option_plans"] == plans
    assert ae._option_plans() == plans
    monkeypatch.setattr(ae, "OPTION_PLANS_PATH", tmp_path / "absent.json")
    assert ae._option_plans() == {}
