"""tests/ 与 quant/ 的镜像 + 覆盖棘轮。

镜像本身只是约定，没人守就会烂。这里做两件事:

  1. **镜像**每个 quant/<层>/ 都有 tests/<层>/ —— 找测试永远只有一个地方可找
  2. **棘轮**没被任何测试 import 的模块必须在 UNTESTED 里显式列出。
              新增模块不写测试 → 红。老模块补了测试却没从名单删 → 也红。
              名单只能变短，不能变长而不被看见。

判定「有没有测试」看的是**测试文件真的 import 了它**，不是文件名对不对得上。
文件名匹配会骗人:  我用前缀匹配估覆盖率时，就把
tests/journal/test_annotate_decision_check.py 判成了「annotate_event 没测试」。

⚠️ 这个名单不是"这些模块不需要测试"的意思，是"现在确实没有"。
   括号里写了为什么，以及哪几条其实是真缺口。
"""
import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
QUANT = ROOT / "quant"
TESTS = ROOT / "tests"

# ── 未被任何测试 import 的模块。key = "<层>/<模块>"，value = 为什么 ──────────
UNTESTED: dict[str, str] = {
    # core —— 剩下的 10 个都有测试
    "core/env": "只是 .env → os.environ 的读取，逻辑在 paths.ENV_PATH",

    # data —— 全都要活的 OpenD / 网络，没做 fake 层
    "data/iv_logger": "需 OpenD 期权链。IV 历史不可重拉，写错=永久污染，**值得补**",

    # sync —— 写 state 的路径，风险最高的一层
    "sync/order_sync": "需 OpenD 在途订单。**真缺口**: 它写 pending_orders",

    # options / risk
    "options/implied_move": "需 OpenD 期权链",
    "risk/chain_layers": "纯函数、无外部依赖 —— **没有理由不测，纯粹是欠的**",

    # decision
    "decision/regime_score": "需 OpenD + VIX",
    "decision/structure_drift": "薄 CLI 壳，度量本体在 core/drift.py (有测试)",

    # journal / report / ops
    "journal/decision_log": "薄 append 壳，写入路径由 core/io_json 测试覆盖",
    "report/perf_report": "读时统计。**真缺口**: process×outcome 矩阵算错不会有人发现",

    # research —— 离线旁路层；待补测试的脚本需保留通用理由
    "research/pull_earnings_history": "需要 OpenD 财报端点的研究取数 CLI",
    "research/earnings_gate_backtest": "离线预注册回测；尚缺独立构造级覆盖",
    "research/repaint_trigger_backtest": "离线预注册回测；尚缺独立构造级覆盖",
    "research/synthetic_longcall_backtest": "离线合成期权回测；尚缺独立构造级覆盖",
    "research/sss_backtest": "离线信号评分回测；尚缺独立构造级覆盖",
    "research/indicator_redundancy": "指标冗余体检，"
                                     "**只算相关不算收益**—— 纯度量脚本; B 部分刻意调真的 "
                                     "detect_bottom_signals 而不复刻实现, 所以它测的就是 live 行为",
    "research/volume_family_redundancy": "量族冗余体检，"
                                         "**只算相关不算收益**—— 纯度量脚本, 无判定逻辑可回归; "
                                         "四个候选指标就地实现(未采纳不进 core), RelVR 部件全 import 既有真源",
    "research/interaction_census": "信号量普查，"
                                   "**只数事件不算收益**—— 无判定逻辑可回归; 若后续接 [FYI] 任务, "
                                   "事件判别式要搬进 decision/ 层并在那里补测试",
    "research/escape_velocity_backtest": "离线预注册回测；world_value/_boot_metric 被 earnings_gate 复用",
    "research/entry_ladder_backtest": "离线入场结构回测；尚缺独立构造级覆盖",
    "research/structure_downgrade_backtest": "**真缺口**: CLAUDE.md 规定「改 entry_guard "
                                             "降级条件前必跑」，判据来源却没测",
}


def _modules() -> set[str]:
    return {f"{p.parent.name}/{p.stem}" for p in QUANT.rglob("*.py") if p.name != "__init__.py"}


def _imported_by_tests() -> set[str]:
    """测试文件真正 import 到的 quant 模块，`<层>/<模块>` 形式。"""
    hit: set[str] = set()
    for t in TESTS.rglob("test_*.py"):
        tree = ast.parse(t.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                parts = node.module.split(".")
                if parts[0] != "quant":
                    continue
                if len(parts) >= 3:                      # from quant.core.sizing import x
                    hit.add(f"{parts[1]}/{parts[2]}")
                elif len(parts) == 2:                    # from quant.sync import state_io
                    hit |= {f"{parts[1]}/{a.name}" for a in node.names}
            elif isinstance(node, ast.Import):           # import quant.core.drift as m
                for a in node.names:
                    parts = a.name.split(".")
                    if parts[0] == "quant" and len(parts) >= 3:
                        hit.add(f"{parts[1]}/{parts[2]}")
    return hit


# ── 镜像 ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("layer", sorted(
    d.name for d in QUANT.iterdir() if d.is_dir() and not d.name.startswith((".", "__"))))
def test_layer_has_a_matching_test_dir(layer):
    """quant/<层>/ ↔ tests/<层>/ —— 找某模块的测试只有一个地方可找。"""
    assert (TESTS / layer).is_dir(), (
        f"quant/{layer}/ 没有对应的 tests/{layer}/。新层建目录时一起建，"
        f"否则它的测试会散落到别处，下一个人找不到。")


def test_no_stray_test_dirs():
    """反向: tests/ 下不该有 quant/ 里不存在的层。"""
    layers = {d.name for d in QUANT.iterdir() if d.is_dir() and not d.name.startswith((".", "__"))}
    stray = sorted({d.name for d in TESTS.iterdir()
                    if d.is_dir() and not d.name.startswith((".", "__"))} - layers)
    assert not stray, f"tests/ 下这些层在 quant/ 里已经不存在了，删掉或改名: {stray}"


# ── 棘轮 ───────────────────────────────────────────────────────────

def test_every_module_is_exercised_or_declared():
    """新模块要么有测试，要么显式承认没有。默认值不能是「静悄悄地没有」。"""
    gaps = sorted(_modules() - _imported_by_tests() - set(UNTESTED))
    assert not gaps, (
        "这些模块没有任何测试 import 到:\n  " + "\n  ".join(gaps) +
        "\n\n补个测试，或者在 tests/test_module_coverage.py 的 UNTESTED 里加一条写明原因。")


def test_untested_list_has_no_stale_entries():
    """棘轮: 补了测试就必须从名单删掉 —— 名单只能变短。

    没这条，名单会变成一张没人看的旧账: 模块早有测试了，条目还留着，
    下一个读的人以为这里是缺口。
    """
    now_tested = sorted(set(UNTESTED) & _imported_by_tests())
    assert not now_tested, (
        "这些模块已经有测试了，从 UNTESTED 删掉:\n  " + "\n  ".join(now_tested))


def test_untested_list_has_no_ghost_entries():
    """名单里不该有已经不存在的模块（改名/删除后忘了同步）。"""
    ghosts = sorted(set(UNTESTED) - _modules())
    assert not ghosts, (
        "UNTESTED 里这些模块在 quant/ 下已不存在:\n  " + "\n  ".join(ghosts))


def test_every_untested_entry_states_a_reason():
    empty = sorted(k for k, v in UNTESTED.items() if len(v.strip()) < 10)
    assert not empty, f"这些豁免没写理由: {empty}"


def test_coverage_does_not_regress():
    """整体覆盖率不得低于已锁定的公开基线。

    这条和上面的棘轮互补: 棘轮防「个别模块偷偷退化」，这条防「一次加十个没测试的模块，
    每个都在 UNTESTED 里合规地写了理由」。
    """
    mods = _modules()
    covered = len(mods & _imported_by_tests())
    assert covered >= 26, (
        f"有测试的模块从 26 掉到了 {covered}/{len(mods)}。"
        f"删测试或删模块都可能触发这条 —— 确认是有意的再改这个数字。")
