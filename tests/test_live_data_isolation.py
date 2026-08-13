"""单测不许悄悄读 live 数据（财报日历 / K 线缓存）—— 源码级棘轮。

**源案例 **: `test_leftover_smaller_than_one_share_is_exhausted` 拿 NBIS
当样本调 `position_budget`, 而那个函数的 `calendar=None` 默认值会去读 live 的
`market/earnings_calendar.json`。NBIS 的真实财报反应日恰好是当天 (8/12 BMO) →
T+0 缩仓 ×0.7 把 probe cap 压到低于当前敞口 → `remaining_usd` 归 0, 断言当场红。
**测试没写错, 它只是不知道自己在读 live。**

同日六世界扫盘 (真日历 / 今天没人发 / 全员发×两档缩仓 / 日历整个缺失 / 日历过期
30 天) 又抓出 4 条同类: `test_today_group_sync` 三条 + `test_portfolio_brief_moves`
一条 —— 它们今天绿, 只是因为用到的 ticker 那天不在真日历上。**这类雷靠跑一遍
测试是发现不了的**(变异测试也不行: 摘掉隔离 fixture 今天照样全绿), 只有换掉
底下那份数据才看得见。所以这条规则要在**源码层**钉, 不在运行时钉。

规则: 任何 tests/ 下的文件, 只要调了会读 live 日历的入口, 就必须在同一文件里
钉住 `load_calendar`(或显式传 `calendar=`)。允许豁免, 但要写进 EXEMPT 并说明
为什么 —— 和 `test_module_coverage.UNTESTED` 一样, 这份名单只能变短。
"""
import ast
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
TESTS = REPO / "tests"

# 这些入口的 `calendar` 参数默认 None → 落到 `earnings.load_calendar()` 读真文件。
# 加新入口时把它加进来 (判据: 函数体里有 event_state/load_calendar 且 calendar 可选)。
LIVE_CALENDAR_ENTRYPOINTS = (
    "position_budget(",
    "grant_actual_cap(",
    "annotate_actual_caps(",
    "build_brief(",
)

# 钉住的两种合法写法: 换掉 load_calendar, 或每个调用点显式传 calendar=。
PINS = ("load_calendar", "calendar=")

# key = repo 相对路径, value = 为什么这条真的需要读 live。空着是目标状态。
EXEMPT: dict[str, str] = {}


def _offenders() -> list[str]:
    out = []
    for p in sorted(TESTS.rglob("test_*.py")):
        rel = p.relative_to(REPO).as_posix()
        if rel in EXEMPT or p.name == pathlib.Path(__file__).name:
            continue
        src = p.read_text(encoding="utf-8")
        if any(e in src for e in LIVE_CALENDAR_ENTRYPOINTS) and not any(k in src for k in PINS):
            out.append(rel)
    return out


def test_no_test_file_reads_the_live_earnings_calendar():
    bad = _offenders()
    assert not bad, (
        "这些测试调了会读 live 财报日历的入口, 却没钉住它 —— 断言会跟着财报季红绿:\n  "
        + "\n  ".join(bad)
        + "\n\n修法: 在 fixture 里 monkeypatch `pb.earnings.load_calendar` 返回\n"
          '  {"as_of": pb.et_today(), "tickers": {}}, 或每个调用点显式传 calendar=。\n'
          "真的需要读 live 就写进本文件的 EXEMPT 并说明理由。")


def test_exempt_list_has_no_dead_entries():
    """名单只能变短: 文件没了 / 已经钉住了 → 把它从 EXEMPT 划掉。"""
    stale = [k for k in EXEMPT
             if not (REPO / k).exists()
             or any(pin in (REPO / k).read_text(encoding="utf-8") for pin in PINS)]
    assert not stale, f"EXEMPT 里这些已经不需要豁免了, 删掉: {stale}"


# ── live K 线缓存 ────────────────────────────────────────────────────────
#
# **源案例 **: 分类轴退役接线让这几个入口内部多走一步
# `structure_veto_of` → `book.bars(ticker)` → `market/klines/<T>.jsonl`。
# 那份缓存 **gitignored（本机可重拉）且每天变**—— 于是
# `test_z1_budget_is_the_actual_cap_not_the_card_percentage` 当场红:
# 它问的是组合层余额算术 (gross 余 35% → 备注写 35%)，答案却变成了
# "MSFT 今天的真实结构说了算"(trend=transition, close 492.43 < lsh 513.73 → VETO → 0%:0)。
# 与上面日历那条**同一形状、差一天**: 08-12 立了日历的闸, 08-13 又开了 K 线这道口。
#
# 与日历那条的差别: 这里按**调用点**判, 不按文件判。同一份
# `test_price_reminder_sync.py` 里三条用例注入了 `veto_of`、两条没注入 ——
# 文件级检查会被前三条喂饱, 正好放过真正出事的那两条。
LIVE_KLINE_ENTRYPOINTS = {
    # 入口名: (能把 live K 线换掉的关键字, 起同样作用的位置参数下标 | None)
    "grant_actual_cap": ({"veto_of", "bars_of"}, None),
    "annotate_actual_caps": ({"veto_of"}, None),
    "z1_budgets": ({"veto_of"}, None),
    "structure_veto_of": ({"bars_of"}, 2),      # tgs.structure_veto_of(t, card, bars_of)
}

# key = "相对路径:函数名", value = 为什么这条真的要读 live K 线。空着是目标状态。
KLINE_EXEMPT: dict[str, str] = {}


def _called_name(node: ast.Call):
    f = node.func
    return f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)


def _kline_offenders() -> list[str]:
    out = []
    for p in sorted(TESTS.rglob("test_*.py")):
        rel = p.relative_to(REPO).as_posix()
        tree = ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            spec = LIVE_KLINE_ENTRYPOINTS.get(_called_name(node) or "")
            if not spec:
                continue
            kwargs, pos_idx = spec
            if kwargs & {k.arg for k in node.keywords if k.arg}:
                continue
            if pos_idx is not None and len(node.args) > pos_idx:
                continue
            if f"{rel}:{_called_name(node)}" in KLINE_EXEMPT:
                continue
            out.append(f"{rel}:{node.lineno} → {_called_name(node)}(...)")
    return out


def test_no_test_call_site_reads_the_live_kline_cache():
    bad = _kline_offenders()
    assert not bad, (
        "这些调用点会落到 book.bars() 读本机 K 线缓存 —— 断言跟着真实行情红绿:\n  "
        + "\n  ".join(bad)
        + "\n\n修法: 这次调用里传 veto_of=（结构维钉死）或 bars_of=（喂自造 bar 序列）。\n"
          "真的要读 live 就写进 KLINE_EXEMPT 并说明理由。")


def test_kline_exempt_list_has_no_dead_entries():
    """同 EXEMPT: 名单只能变短。文件没了 / 调用点已经注入了 → 划掉。"""
    live = {o.split(" → ")[1].rstrip("(...)"): o for o in _kline_offenders()}
    stale = [k for k in KLINE_EXEMPT
             if not (REPO / k.split(":")[0]).exists() or k.split(":")[1] not in live]
    assert not stale, f"KLINE_EXEMPT 里这些已经不需要豁免了, 删掉: {stale}"


@pytest.mark.parametrize("entry", sorted(LIVE_KLINE_ENTRYPOINTS))
def test_the_kline_entrypoints_still_exist(entry):
    """入口改名 → 检查对象不存在 = 永远没有 offender (形状#17: 不会触发的检查更危险)。"""
    src = "\n".join((REPO / f).read_text(encoding="utf-8")
                    for f in ("quant/sync/today_group_sync.py",
                              "quant/sync/price_reminder_sync.py"))
    assert f"def {entry}(" in src, f"{entry} 已改名/删除 — 同步更新 LIVE_KLINE_ENTRYPOINTS"


@pytest.mark.parametrize("entry", LIVE_CALENDAR_ENTRYPOINTS)
def test_the_entrypoints_still_exist(entry):
    """入口改名 → 这条规则会静默失效 (检查的对象不存在了 = 永远没有 offender)。

     形状#17: **一个不会触发的具体检查, 比没有这条检查更危险。**
    """
    name = entry.rstrip("(")
    src = "\n".join((REPO / f).read_text(encoding="utf-8")
                    for f in ("quant/report/portfolio_brief.py",
                              "quant/sync/today_group_sync.py"))
    assert f"def {name}(" in src, f"{name} 已改名/删除 — 同步更新 LIVE_CALENDAR_ENTRYPOINTS"
