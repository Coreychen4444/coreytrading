"""thesis.frame 判别式 (基本面分析参考卖方研报骨架; 存量卡重画时逐张替换)。

三件事钉在这里: 形状 (不夹带评级/目标价、不省略未知) / summary 由 hypothesis 派生 /
替换时预注册的看点不许静默消失。
"""
from copy import deepcopy

import pytest

from quant.core import thesis_frame as tf
from quant.core.schema import validate_positions_doc, validate_watchlist_doc

TODAY = "2026-09-15"


def _frame(**over):
    frame = {
        "as_of": "2026-09-14",
        "hypothesis": "收入由 A 需求与 B 渗透两条腿驱动",
        "drivers": [
            {"name": "A 需求", "mechanism": "A 负载按用量计费", "evidence": "待核实", "kpi": "A 收入占比"},
            {"name": "B 渗透", "mechanism": "B 客户扩容 → 订单", "evidence": "合成出处 2026-09-14",
             "kpi": "新增客户数"},
        ],
        "bear_case": {"claim": "竞品在 A 上追平", "rebuttal": "待核实",
                      "falsifier": "A 收入环比转负"},
        "valuation": "待核实 (估值层无可发布输出)",
        "catalyst": {"event": "FQ3 财报", "date": "2026-11-05", "baseline": "待核实",
                     "watch": ["A 收入占比", "客户集中度"]},
        "sources": ["合成出处 2026-09-14"],
    }
    frame.update(over)
    return frame


def test_valid_frame_and_optional_review():
    assert tf.frame_errors(_frame()) == []
    review = {"event": "FQ2 财报", "date": "2026-08-20",
              "outcomes": [{"watch": "A 收入占比", "result": "待核实 (归档无披露)"}]}
    assert tf.frame_errors(_frame(review=review)) == []


@pytest.mark.parametrize("mutate, needle", [
    (lambda f: f.pop("bear_case"), "missing"),
    (lambda f: f.update(rating="OW"), "unknown keys"),
    (lambda f: f.update(as_of="2026-9-14"), "as_of"),
    (lambda f: f.update(hypothesis="  "), "hypothesis"),
    (lambda f: f.update(hypothesis="x" + tf.NOTES_SEPARATOR + "y"), "[notes]"),
    (lambda f: f.update(drivers=[]), "drivers"),
    (lambda f: f.update(drivers=f["drivers"] * 2), "drivers"),
    (lambda f: f["drivers"][0].pop("kpi"), "drivers[0]"),
    (lambda f: f["drivers"][0].update(kpi=""), "drivers[0].kpi"),
    (lambda f: f["bear_case"].update(falsifier=None), "falsifier"),
    (lambda f: f["catalyst"].update(date="next quarter"), "catalyst.date"),
    (lambda f: f["catalyst"].update(watch=["a", "a"]), "watch"),
    (lambda f: f["catalyst"].update(watch=[str(i) for i in range(6)]), "watch"),
    (lambda f: f["catalyst"].update(watch=[{"k": "v"}]), "watch"),
    (lambda f: f["catalyst"].update(target_price=300), "catalyst"),
    (lambda f: f.update(sources=[]), "sources"),
    (lambda f: f.update(review={"event": "e", "date": "2026-08-20", "outcomes": []}), "outcomes"),
])
def test_shape_errors_are_named(mutate, needle):
    frame = _frame()
    mutate(frame)
    errs = tf.frame_errors(frame)
    assert errs and any(needle in e for e in errs)


def test_unknown_marker_fills_slots_instead_of_omitting_them():
    frame = _frame(valuation=tf.FRAME_UNKNOWN)
    frame["catalyst"].update(date=tf.FRAME_UNKNOWN, baseline=tf.FRAME_UNKNOWN)
    assert tf.frame_errors(frame) == []


def _watchlist(thesis):
    return {"tickers": {"SYN": {"instrument": {"type": "stock"}, "status": "watching",
                                 "agent": {"thesis": thesis, "action": {"intent": "watching"}}}}}


def test_card_summary_derives_from_hypothesis():
    frame = _frame()
    ok = {"summary": frame["hypothesis"], "status": "intact", "frame": frame}
    assert validate_watchlist_doc(_watchlist(ok))[0] == []
    noted = dict(ok, summary=frame["hypothesis"] + tf.NOTES_SEPARATOR + "结构注记")
    assert validate_watchlist_doc(_watchlist(noted))[0] == []
    forked = dict(ok, summary="另写的一段散文")
    assert any("frame.hypothesis" in e for e in validate_watchlist_doc(_watchlist(forked))[0])
    malformed = dict(ok, frame=_frame(drivers=[]))
    assert any("drivers" in e for e in validate_watchlist_doc(_watchlist(malformed))[0])


def test_positions_cards_use_the_same_check():
    card = {"instrument": {"type": "stock"}, "broker": {"qty": 0}, "status": "active",
            "agent": {"thesis": {"summary": "fork", "status": "intact", "frame": _frame()},
                      "action": {"intent": "hold"}},
            "lots": {"base": [], "position": []}, "position_summary": {}, "pending_orders": []}
    errors, _ = validate_positions_doc({"tickers": {"SYN": card}})
    assert any("frame.hypothesis" in e for e in errors)


def test_legacy_prose_card_is_not_checked():
    legacy = {"summary": "旧散文 thesis" + tf.NOTES_SEPARATOR + "结构注记", "status": "intact"}
    assert validate_watchlist_doc(_watchlist(legacy))[0] == []


PRIOR = _frame(catalyst={"event": "FQ2 财报", "date": "2026-09-10", "baseline": "上次指引 待核实",
                         "watch": ["A 收入占比", "客户集中度"]})


def test_without_prior_frame_only_dates_are_checked():
    assert tf.transition_errors(None, _frame(), TODAY) == []
    assert any("as_of" in e for e in tf.transition_errors(None, _frame(as_of="2026-09-16"), TODAY))
    stale = _frame()
    stale["catalyst"]["date"] = "2026-09-14"
    assert any("catalyst.date" in e for e in tf.transition_errors(None, stale, TODAY))


def test_future_or_unknown_prior_catalyst_needs_no_review():
    assert tf.transition_errors(_frame(), _frame(), TODAY) == []
    unknown = _frame()
    unknown["catalyst"]["date"] = tf.FRAME_UNKNOWN
    assert tf.transition_errors(unknown, _frame(), TODAY) == []


def test_reached_event_requires_review_covering_every_prior_watch_item():
    assert any("review" in e for e in tf.transition_errors(PRIOR, _frame(), TODAY))
    wrong_event = _frame(review={"event": "FQ1 财报", "date": "2026-09-10",
                                 "outcomes": [{"watch": "A 收入占比", "result": "x"}]})
    assert any("已到事件日" in e for e in tf.transition_errors(PRIOR, wrong_event, TODAY))
    partial = _frame(review={"event": "FQ2 财报", "date": "2026-09-10",
                             "outcomes": [{"watch": "A 收入占比", "result": "+12%"}]})
    assert any("客户集中度" in e for e in tf.transition_errors(PRIOR, partial, TODAY))
    full = _frame(review={"event": "FQ2 财报", "date": "2026-09-10", "outcomes": [
        {"watch": "A 收入占比", "result": "+12% (合成归档)"},
        {"watch": "客户集中度", "result": "待核实 (归档无披露)"}]})
    assert tf.transition_errors(PRIOR, full, TODAY) == []


def test_event_day_itself_counts_as_reached():
    same_day = deepcopy(PRIOR)
    same_day["catalyst"]["date"] = TODAY
    assert tf.transition_errors(same_day, _frame(), TODAY)
