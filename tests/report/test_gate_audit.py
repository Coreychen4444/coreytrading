"""gate 合规审计 —— 六条机械标准各钉一条正例一条反例。

 用户 定版「gate = 状态转移轴」并称其为整个系统的根基。当天全池手工
审计跑出 9 张不合规, 全部是「文档里写着、没有任何东西检查」的形状 ——
本仓反复栽的那一个 (规则不进代码 = 不存在)。

⚠️ 最后一条测试钉的是**不该有的检查**: `trim_hi ≤ gate` 曾在立表当天被我臆断
成标准, 同日全池审计推翻 (12 张卡三种排列都在用)。把它加回来 = 那条测试变红。
"""
import os

import pytest

from quant.report import gate_audit as ga


def _agent(gate=110.0, days=2, vr=1.2, nc=105.0, z1=(95.0, 100.0),
           z2=(90.0, 95.0), trim=(106.0, 112.0), stop=88.0,
           next_stop=105.0, basis="pivot x3 上沿", zdate="2026-08-11",
           with_gate=True, cap=0.4):
    a = {
        "zones": {"accumulation_zone_1": list(z1), "accumulation_zone_2": list(z2),
                  "trim_zone": list(trim), "no_chase_above": nc, "stop_level": stop,
                  "anchored_on": ({"date": zdate} if zdate else {})},
        "current_regime": {"name": "常规参与", "cap_pct_nav": cap},
    }
    if with_gate:
        eg = {"level": gate}
        if days is not None:
            eg["days"] = days
        if vr is not None:
            eg["volume_ratio_min"] = vr
        if basis:
            eg["anchored_on"] = {"date": "2026-08-11", "basis": basis}
        a["next_regime"] = {"name": "R_收复_X", "entry_gate": eg,
                            "cap_pct_nav": 0.4,
                            "zones": {"stop_level": next_stop},
                            "next_gate_preview": {"level": gate * 1.1, "days": 2}}
    return a


def _issues(**kw):
    return ga.audit_card("T", _agent(**kw), atr20=kw.pop("atr20", 4.0))


def test_a_well_formed_card_is_clean():
    assert ga.audit_card("T", _agent(), atr20=4.0) == []


# ── ⑧ 转移轴本身 ────────────────────────────────────────────────────

def test_missing_axis_short_circuits():
    """没有轴, 后面几条无从谈起 —— 只报这一条, 不刷屏。"""
    out = ga.audit_card("T", _agent(with_gate=False), atr20=4.0)
    assert len(out) == 1 and out[0].startswith("⑧")
    assert "只有一轮" in out[0]


def test_cap_zero_gets_the_harsher_wording():
    out = ga.audit_card("T", _agent(with_gate=False, cap=0), atr20=4.0)
    assert "锁死" in out[0] and "等破位" in out[0]


# ── ③ 确认口径 ──────────────────────────────────────────────────────

def test_missing_confirmation_terms():
    assert any(i.startswith("③") for i in _issues(days=None))
    assert any(i.startswith("③") for i in _issues(vr=None))


# ── ④ 自洽 ─────────────────────────────────────────────────────────

def test_gate_below_no_chase_is_inverted():
    (hit,) = [i for i in _issues(gate=104.0, next_stop=99.0) if i.startswith("④")]
    assert "倒挂" in hit


def test_gate_equal_to_no_chase_is_normal_design():
    """NVDA 卡 gate 214 / no_chase 214 = 在追价上限处确认突破, 不是缺陷。"""
    assert not [i for i in _issues(gate=105.0, nc=105.0, trim=(105.0, 112.0),
                                   next_stop=100.0) if i.startswith("④")]


# ── ⑤ 单调 ─────────────────────────────────────────────────────────

def test_stop_above_the_buy_zone():
    """GOOGL  源案例: 期权的退出线被抄到正股卡上当 stop。"""
    (hit,) = [i for i in _issues(stop=97.0) if i.startswith("⑤")]
    assert "买带压在止损线之下" in hit


def test_z1_above_no_chase():
    assert any("z1 上沿" in i for i in _issues(z1=(95.0, 106.0)))


def test_no_chase_inside_the_trim_zone():
    assert any("追进自己的减仓区" in i for i in _issues(nc=108.0))


def test_trim_above_gate_is_NOT_a_finding():
    """🚨 反向钉: `trim_hi ≤ gate` 是  立表当天的臆断, 同日推翻。

    12 张 live 卡三种排列都在用 —— gate 低于 trim (AAOI/MRVL/AMZN/MSFT)、
    等于 trim 上沿 (INTC/MU)、高于 trim (NBIS/NVDA)。两者不在同一时点生效:
    trim 管当前 regime 止盈, gate 一确认整张图连同 trim 一起被替换。
    把这条加回审计 = 本测试变红。
    """
    assert ga.audit_card("T", _agent(gate=110.0, trim=(106.0, 140.0)),
                         atr20=4.0) == []


# ── ⑥ 地板缓冲 ──────────────────────────────────────────────────────

def test_next_floor_glued_to_the_gate():
    (hit,) = [i for i in _issues(gate=110.0, next_stop=109.5) if i.startswith("⑥")]
    assert "whipsaw" in hit


def test_floor_check_skipped_without_atr():
    """算不出 ATR ≠ 不合规 —— 缺数据只能不判 (迁移即误伤, 形状#11)。"""
    assert not [i for i in ga.audit_card("T", _agent(next_stop=109.5), atr20=None)
                if i.startswith("⑥")]


# ── ⑨ 锚留痕 ───────────────────────────────────────────────────────

def test_anchor_basis_and_date_required():
    assert any("anchored_on.basis" in i for i in _issues(basis=None))
    assert any("anchored_on.date" in i for i in _issues(zdate=None))


# ── 辖区 ───────────────────────────────────────────────────────────

def test_scope_excludes_option_closed_unmanaged_and_zoneless():
    doc = {"tickers": {
        "OK":   {"status": "active", "instrument": {"type": "stock"}, "agent": _agent()},
        "OPT":  {"status": "active", "instrument": {"type": "option"}, "agent": _agent()},
        "GONE": {"status": "closed", "instrument": {"type": "stock"}, "agent": _agent()},
        "BASE": {"status": "active",
                 "instrument": {"type": "stock", "role": "commemorative_hold"},
                 "agent": _agent()},
        "STUB": {"status": "watching", "instrument": {"type": "stock"},
                 "agent": {"zones": None}},
    }}
    assert [t for t, _ in ga.auditable_cards(doc)] == ["OK"]


def test_run_returns_only_offenders():
    doc = {"tickers": {
        "GOOD": {"status": "active", "instrument": {"type": "stock"}, "agent": _agent()},
        "BAD":  {"status": "active", "instrument": {"type": "stock"},
                 "agent": _agent(with_gate=False)},
    }}
    out = ga.run(doc, atr_of=lambda t: 4.0, ctx_of=lambda t: (None, None))
    assert [f["ticker"] for f in out] == ["BAD"]


# ── ① gate 跳过 last swing high (advisory) ──────────────────────────

def _adv(gate, lsh, spot, atr20=4.0):
    return ga.advise_card("T", _agent(gate=gate), swing={"last_swing_high": lsh},
                          atr20=atr20, spot=spot)


def test_gate_skipping_an_unreclaimed_swing_high_is_advised():
    """META  源案例: gate 628.5 越过 lsh 601 达 1.21N, 现价 599 仍在其下。

    我的错法是拿继承来的 no_chase 602 当固定值, 再按标准④去它**上面**找簇 ——
    因果倒置, 让一条旧线决定了 gate 能画在哪。
    """
    (hit,) = _adv(gate=628.5, lsh=601.0, spot=599.12, atr20=22.65)
    assert hit.startswith("①") and "1.21N" in hit and "601" in hit


def test_gate_just_above_the_swing_high_is_correct_placement():
    """「收上 lsh」意味着收在其上, 恰好等于会有歧义 —— 置于其上一点是**对的**。

    live 实测: MRVL 0.02N / NBIS 0.06N / MU 0.21N 都是这种正常置法, 不该报。
    """
    assert _adv(gate=223.0, lsh=222.67, spot=212.31, atr20=15.0) == []
    assert _adv(gate=231.0, lsh=229.47, spot=193.23, atr20=23.65) == []


def test_swing_high_already_taken_out_is_not_applicable():
    """现价已在 lsh 之上 = 那条轴已经被收复, 不构成「跳过」。"""
    assert _adv(gate=520.0, lsh=406.0, spot=503.81, atr20=12.0) == []


def test_advisory_skipped_without_context():
    """kline 不足算不出道氏结构 → 不判 (缺数据 ≠ 不合规, 形状#11)。"""
    assert ga.advise_card("T", _agent(), swing=None, atr20=4.0, spot=100.0) == []
    assert _adv(gate=628.5, lsh=601.0, spot=599.12, atr20=None) == []


def test_advisory_needs_a_gate_to_talk_about():
    assert ga.advise_card("T", _agent(with_gate=False),
                          swing={"last_swing_high": 601.0},
                          atr20=22.65, spot=599.12) == []


@pytest.mark.skipif(os.environ.get("TRADINGOPS_CHECK_LIVE_STATE") != "1",
                    reason="set TRADINGOPS_CHECK_LIVE_STATE=1 to scan local account state")
def test_live_book_has_no_hard_violations():
    """真账本硬违规必须为空。advisory 允许非空 —— 它是待复核清单不是判决,
    2026-08-11 收盘时 INTC 一张在列 (gate 109.6 越过 lsh 103.66 达 0.75N)。"""
    import json
    from quant.core.paths import POSITIONS_PATH, WATCHLIST_PATH
    docs = [json.loads(p.read_text(encoding="utf-8"))
            for p in (POSITIONS_PATH, WATCHLIST_PATH)]
    assert [f for f in ga.run(*docs) if f["issues"]] == []
