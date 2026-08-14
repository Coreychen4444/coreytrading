"""自由度棘轮：会动钱层的无出处旋钮只能减少，不能增加。

具体数量由 `quant/research/dof_audit.py` 现场计算，不在测试说明中保存历史快照。
有依据的参数必须记录来源；无出处参数的新增会让本测试失败。

这和本仓已有的 `tests/test_module_coverage.py` 的 `UNTESTED` 名单棘轮是同一个装置
（「只能变短」），不是新发明。
"""
import pytest

from quant.research import dof_audit as A

#  审计基线。**这个数只允许下调。**
# 上调 = 往会动钱的那一层加了一个没人能说出出处的旋钮 —— 那正是本棘轮要拦的。
MONEY_UNBACKED_MAX = 17


def test_money_layer_unbacked_dof_ratchet():
    r = A.run()
    n = r["money_by_class"].get("unbacked", 0)
    assert n <= MONEY_UNBACKED_MAX, (
        f"会动钱那一层的无出处自由度 {n} > 基线 {MONEY_UNBACKED_MAX}。\n"
        f"要么给新旋钮补出处（进 dof_audit.CLASSIFY，附裁决/回测/canonical 出处），\n"
        f"要么退休一个旧的。当前清单：\n  " + "\n  ".join(r["money_unbacked"]))


def test_ratchet_baseline_is_not_stale_by_a_lot():
    """基线远高于实况 = 棘轮松了（挡不住新增）。差 >3 就该把基线调下来。"""
    r = A.run()
    n = r["money_by_class"].get("unbacked", 0)
    assert MONEY_UNBACKED_MAX - n <= 3, (
        f"实况 {n} 已明显低于基线 {MONEY_UNBACKED_MAX} —— 把 MONEY_UNBACKED_MAX 调到 {n}")


def test_judgment_layer_is_excluded_by_construction():
    """判读层（`chart_read`/`patterns`）不进本预算 —— 它由测试钉死改不了交易，
    花的是注意力不是钱（注意力由「一屏内读得完」那条预算管）。"""
    assert A.JUDGMENT_ONLY == {"report/chart_read", "core/patterns"}
    r = A.run()
    assert not any(it["mod"] in A.JUDGMENT_ONLY for it in r["items"])


def test_money_layers_cover_the_authorization_chain():
    """会动钱的那一层必须包含授权链的每一环 —— 漏掉一环，那一环的旋钮就免检了。"""
    must = {"core/sizing", "decision/entry_guard", "decision/exit_guard",
            "decision/regime_transition", "sync/today_group_sync",
            "sync/price_reminder_sync"}
    assert must <= A.MONEY_LAYERS


@pytest.mark.parametrize("key", [
    "core/sizing:NOTIONAL_CAP",          # production contract fiat
    "decision/regime_transition:REL_FLOOR",   # 0.4 relative_volume 裁决
    "decision/exit_guard:opt_s1_stop",   # −25% options_exit_overlay 裁决
    "core/technical:VALUE_AREA_FRAC",    # 70% 拍卖理论定义
])
def test_known_parameters_stay_classified(key):
    """已知出处的参数不许悄悄掉回 `unbacked`（改名/搬家时最容易发生）。"""
    r = A.run()
    hit = [it for it in r["items"] if f"{it['mod']}:{it['name']}" == key]
    assert hit, f"{key} 从审计里消失了 —— 改名或搬家了？"
    assert hit[0]["class"] != "unbacked", key


def test_unclassified_defaults_to_unbacked_not_to_a_free_pass():
    """没人记下出处 = 没有出处。默认必须落进 `unbacked`，不能是一个中性桶 ——
    中性桶会让「忘了写出处」和「本来就没出处」看起来一样无害。"""
    r = A.run()
    assert "unclassified" not in r["by_class"]
