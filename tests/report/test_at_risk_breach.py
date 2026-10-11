"""单笔 at-risk 天花板 — 持仓侧核查 (risk_unit (b) v1, N1 立项)。

规则: 止损打掉后的损失 ≤ AT_RISK_CAP_FRAC (5%) × NAV。
正股按 binding stop (max(卡 stop, trail), 与 stop_status 同口径);
期权 long 按 保费 mark × OPTION_ATRISK_LOSS_FRAC (= |S1|: 放宽自保费全额时
0.25, S1 收到 −20 后 0.20; 系数与 exit_guard 的 opt_s1_stop 相等由
tests/decision/test_exit_guard.py 钉)。
"""
import pytest

from quant.core import sizing
from quant.report import portfolio_brief as pb

NAV = 20_000.0          # cap = 5% = $1,000


def _stock(qty=100, price=150.0):
    return {"ticker": "A", "qty": qty, "current_price": price}


def test_stock_over_cap_fires_with_trim_math():
    sig = pb.at_risk_breach_signal(_stock(), {"stop_level": 120.0}, None, NAV)
    assert sig is not None and sig["kind"] == "stock"
    assert sig["at_risk_usd"] == pytest.approx(3000.0)       # 100 × (150−120)
    assert sig["cap_usd"] == pytest.approx(1000.0)
    assert sig["trim_units"] == 67                            # 留 int(1000/30)=33 股
    assert sig["stop_basis"] == "card"


def test_trail_binds_when_above_card_stop():
    guard = {"trail": {"trail": 130.0}}
    sig = pb.at_risk_breach_signal(_stock(), {"stop_level": 100.0}, guard, NAV)
    assert sig is not None
    assert sig["stop"] == 130.0 and sig["stop_basis"] == "trail"
    assert sig["at_risk_usd"] == pytest.approx(2000.0)        # 多头取大 = 亏得少那条


def test_under_cap_no_stop_synthetic_and_breached_stop_are_silent():
    # 在顶内
    assert pb.at_risk_breach_signal(_stock(qty=10), {"stop_level": 120.0}, None, NAV) is None
    # 无任何 stop 线 (v1 跳过, 归 zone/schema 层)
    assert pb.at_risk_breach_signal(_stock(), {}, None, NAV) is None
    # synthetic 参考行
    assert pb.at_risk_breach_signal({**_stock(), "synthetic": True},
                                    {"stop_level": 120.0}, None, NAV) is None
    # stop 在价上 = 已在破位区, 归 stop_breach 告警管
    assert pb.at_risk_breach_signal(_stock(price=110.0), {"stop_level": 120.0},
                                    None, NAV) is None
    # NAV 缺失 = 不知道, 不编
    assert pb.at_risk_breach_signal(_stock(), {"stop_level": 120.0}, None, None) is None


def test_option_premium_times_s1_fraction_googl_shape():
    """GOOGL270115C390000 实卡数字 @ |S1| 口径 (0.20 自): $2,047.5 × 0.20 =
    $409.5 = 1.9% NAV → **不超标** — 放宽的效果 (旧保费全额口径下它超 ~1.9×)。"""
    from quant.core.sizing import OPTION_ATRISK_LOSS_FRAC as F
    opt = {"ticker": "GOOGL270115C390000", "qty": 1, "current_price": 20.475}
    assert pb.at_risk_breach_signal(opt, {"stop_level": 358.5}, None, 21_200.0) is None
    # 超标形状: 5 张 → at-risk 5×$409.5=$2,047.5 > cap $1,060; keep=int(1060/409.5)=2 → 减 3
    sig = pb.at_risk_breach_signal({**opt, "qty": 5}, {"stop_level": 358.5}, None, 21_200.0)
    assert sig is not None and sig["kind"] == "option_premium"
    assert sig["at_risk_usd"] == pytest.approx(5 * 2047.5 * F, abs=0.01)
    assert sig["trim_units"] == 3
    assert "S1 premium stop" in sig["stop_basis"]
    # 卡 stop 未标 underlying 基准且无 S5g gate → 不给期权折扣, 用保费名义 (有基准才投影)
    assert sig["stop"] is None


# ── 入场侧第⑤条 (entry_guard.size_caps) ───────────────────────

def test_entry_side_at_risk_is_computed_but_structurally_never_binds():
    """ (用户 #3) 后 ②⑤ 共用同一 stop ⇒ 比恒定 ⇒ ⑤ 入场侧永不 binding。
    旧用例钉的"远 stop 时 ⑤ 接管"随 ② 的 2N 分母一起消失了 —— ② 现在也按真
    stop 缩, 缩得比 ⑤ 更快 (0.025 < 0.05)。⑤ 仍要被算出来 (播报 + 持仓侧同源)。"""
    from quant.decision import entry_guard as eg
    caps = eg.size_caps(20_000.0, 0.5, 100.0, risk_stop=50.0,
                         agent={"current_regime": {"cap_pct_nav": 0.40}})
    assert caps["at_risk_cap"] == pytest.approx(2000.0)      # 5%×20k×100/50
    assert caps["n_unit_cap"] == pytest.approx(1000.0)       # 2.5%×20k×100/50
    assert caps["binding"] == "n_unit" and caps["final"] == pytest.approx(1000.0)
    # 不传 stop = ②⑤ 双双缺席 (不是 0), 由 regime/组合层接手 —— 缺 stop 不该放大 size
    caps3 = eg.size_caps(20_000.0, 0.5, 100.0,
                          agent={"current_regime": {"cap_pct_nav": 0.40}})
    assert caps3["at_risk_cap"] is None and caps3["n_unit_cap"] is None
    assert caps3["binding"] == "regime"


def test_at_risk_nominal_cap_rejects_degenerate_stops():
    f = sizing.at_risk_nominal_cap
    assert f(20_000.0, 100.0, 50.0) == pytest.approx(2000.0)
    assert f(None, 100.0, 50.0) is None                       # 无 NAV 不编数
    assert f(20_000.0, 100.0, None) is None                   # 无 stop 缺席
    assert f(20_000.0, 100.0, 100.0) is None                  # stop ≥ price
    assert f(20_000.0, 100.0, 120.0) is None
    assert f(20_000.0, 100.0, -5.0) is None


# ── 期权: 先触发的那条线 (合成形状) ─────────────
#
# stop 上移到 gate 369.8 后旧口径仍按 mark×20% = $910 报超顶; 实际 gate 先响只亏 ~$370。

OPT = {"ticker": "SYNA270319C380000", "qty": 1, "current_price": 45.5,
       "avg_cost": 38.0, "iv_pct": 44.0, "delta": 0.57}
OPT_NAV = 17_500.0     # cap = $875


@pytest.fixture
def _spot(monkeypatch):
    from quant.options import premium_proj
    monkeypatch.setattr(premium_proj, "dte_of", lambda t, today: 162)
    box = {"spot": 376.5}
    monkeypatch.setattr(pb.book, "price_of", lambda t, *a, **k: box["spot"])
    return box


def test_option_gate_binds_before_s1(_spot):
    card = {"stop_level": 369.8, "stop_level_basis": "underlying"}
    # 旧名义口径 $910 > $875 会响; gate 投影只亏 ~$370 → 静默
    assert pb.at_risk_breach_signal(OPT, card, None, OPT_NAV) is None
    sig = pb.at_risk_breach_signal({**OPT, "qty": 3}, card, None, OPT_NAV)
    assert sig is not None and sig["stop"] == 369.8
    assert 300 < sig["at_risk_usd"] / 3 < 450
    assert "保费投影" in sig["stop_basis"] and "先于 S1 线 30.40" in sig["stop_basis"]
    assert sig["trim_units"] == 1


def test_option_guard_gate_line_counts_without_card_basis(_spot):
    """卡 stop 未标基准 (不同基不比), 但 exit_guard 写回了 S5g gate → 用 gate。"""
    guard = {"opt_gate_stop": {"level": 369.8, "days": 1}}
    sig = pb.at_risk_breach_signal({**OPT, "qty": 3}, {"stop_level": 354.0}, guard, OPT_NAV)
    assert sig is not None and sig["stop"] == 369.8
    # 两条都在 → 取高 (严于 gate 的卡 stop 优先)
    sig2 = pb.at_risk_breach_signal({**OPT, "qty": 3},
                                    {"stop_level": 372.0, "stop_level_basis": "underlying"},
                                    guard, OPT_NAV)
    assert sig2 is None or sig2["stop"] == 372.0


def test_option_s1_binds_when_underlying_line_is_far(_spot):
    """正股线远 (投影保费 < 成本×0.8) → S1 先响, 损失 = mark − S1 线, 不报正股价。"""
    card = {"stop_level": 300.0, "stop_level_basis": "underlying"}
    sig = pb.at_risk_breach_signal(OPT, card, None, OPT_NAV)
    assert sig is not None and sig["stop"] is None
    assert sig["at_risk_usd"] == pytest.approx((45.5 - 30.4) * 100, abs=0.01)
    assert "S1 线 30.40" in sig["stop_basis"]


def test_option_already_through_a_line_is_exit_stack_business(_spot):
    card = {"stop_level": 369.8, "stop_level_basis": "underlying"}
    _spot["spot"] = 368.0
    assert pb.at_risk_breach_signal({**OPT, "qty": 9}, card, None, OPT_NAV) is None
    _spot["spot"] = 376.5
    assert pb.at_risk_breach_signal({**OPT, "qty": 9, "current_price": 30.0},
                                    card, None, OPT_NAV) is None


def test_option_projection_missing_falls_back_to_nominal_and_says_so(_spot):
    _spot["spot"] = None
    card = {"stop_level": 369.8, "stop_level_basis": "underlying"}
    sig = pb.at_risk_breach_signal(OPT, card, None, OPT_NAV)
    assert sig is not None and sig["stop"] is None
    assert sig["at_risk_usd"] == pytest.approx(45.5 * 100 * sizing.OPTION_ATRISK_LOSS_FRAC, abs=0.01)
    assert "投影缺" in sig["stop_basis"]
