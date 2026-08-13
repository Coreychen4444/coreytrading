"""单笔 at-risk 天花板 — 持仓侧核查 (risk_unit (b) v1, N1 立项)。

规则: 止损打掉后的损失 ≤ AT_RISK_CAP_FRAC (5%) × NAV。
正股按 binding stop (max(卡 stop, trail), 与 stop_status 同口径);
期权 long 按 保费 mark × OPTION_ATRISK_LOSS_FRAC (0.25 — S1 premium stop 接线后的
loss/unit, 用户  裁决放宽自保费全额; 系数与 exit_guard 的 opt_s1_stop
相等由 tests/decision/test_exit_guard.py 钉)。
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
    """GOOGL270115C390000 实卡数字 @ 0.25 口径: $2,047.5 × 0.25 = $511.9 = 2.4% NAV
    → **不再超标**— 这正是放宽的效果 (旧保费全额口径下它超 ~1.9×)。"""
    opt = {"ticker": "GOOGL270115C390000", "qty": 1, "current_price": 20.475}
    assert pb.at_risk_breach_signal(opt, {"stop_level": 358.5}, None, 21_200.0) is None
    # 超标形状: 5 张 → at-risk 5×$511.9=$2,559 > cap $1,060; keep=int(1060/511.9)=2 → 减 3
    sig = pb.at_risk_breach_signal({**opt, "qty": 5}, {"stop_level": 358.5}, None, 21_200.0)
    assert sig is not None and sig["kind"] == "option_premium"
    assert sig["at_risk_usd"] == pytest.approx(5 * 2047.5 * 0.25, abs=0.01)
    assert sig["trim_units"] == 3
    assert "S1 premium stop" in sig["stop_basis"]
    # 标的 stop 不给期权折扣 — 用保费不用 (标的价−stop)
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
